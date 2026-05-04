"""OpenAIChatProvider — only file in agent_core that imports `openai`.

Translates neutral chat_provider types to/from OpenAI Chat Completions
SDK shapes. Mirrors the structure of anthropic_provider.py: capabilities
declared on the class, init validates required config, _to_wire and
_from_wire handle every translation, retry loops live in private
helpers.

OpenAI does not currently report cache hit/miss information through the
SDK, so TokenUsage.cache_read_tokens / cache_creation_tokens stay None
to preserve the "None means not supported" contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import openai
from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderConfigError,
)
from src.chat_provider.metrics import record_call_metrics
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    ImageBlock,
    Message,
    OutputFormat,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


_DEFAULT_MAX_TOKENS = 4096


def _safe_int(value) -> int:
    """Coerce a possibly-missing usage field to int."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed.

    Caught only inside OpenAIChatProvider.call() / .stream() to
    transition into the error-response path. Never escapes.
    """


class OpenAIChatProvider(ChatProviderBase):
    """OpenAI Chat Completions implementation of ChatProviderBase.

    Required config keys:
        primary_model    (str) e.g. "gpt-4o-2024-08-06"
        timeout_ms       (int)
        retry_attempts   (int) min 1

    Optional:
        retry_backoff_seconds  list[float]  [0, 0.5, 1.0]
        features.prompt_cache  bool         False  (capability default)
        features.streaming     bool         True
        features.image_input   bool         True
    """

    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )

    def __init__(self, config: dict) -> None:
        """Initialise the provider, validate required config, and build SDK clients.

        Args:
            config: Provider configuration dict. Must contain primary_model,
                timeout_ms, and retry_attempts.

        Raises:
            ProviderConfigError: If any required config key is missing or invalid.
        """
        if not config:
            raise ProviderConfigError(
                "OpenAIChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid OpenAI model id (e.g. gpt-4o-2024-08-06)."
            )
        if "timeout_ms" not in config:
            raise ProviderConfigError("agent.timeout_ms is required")
        if "retry_attempts" not in config:
            raise ProviderConfigError("agent.retry_attempts is required")

        self._primary_model: str = primary_model
        self._timeout_s: float = config["timeout_ms"] / 1000
        self._max_attempts: int = max(1, config["retry_attempts"])
        self._backoff_seconds: list[float] = config.get(
            "retry_backoff_seconds", [0, 0.5, 1.0]
        )

        feats = dict(config.get("features") or {})
        self._features: dict[str, bool] = {
            "prompt_cache": bool(feats.get("prompt_cache", self.capabilities.supports_prompt_cache))
                            and self.capabilities.supports_prompt_cache,
            "streaming": bool(feats.get("streaming", self.capabilities.supports_streaming))
                         and self.capabilities.supports_streaming,
            "image_input": bool(feats.get("image_input", self.capabilities.supports_image_input))
                           and self.capabilities.supports_image_input,
        }

        self._active_model: str = self._primary_model
        self._client = openai.OpenAI()
        self._async_client = openai.AsyncOpenAI()

    # ------------------------------------------------------------------
    # Public ChatProviderBase methods (filled in subsequent tasks)
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Send a synchronous chat request to the OpenAI API.

        Args:
            request: The chat request containing messages, tools, and config.

        Returns:
            ChatResponse with content blocks and token usage.

        Raises:
            NotImplementedError: Until Task 7 is implemented.
        """
        raise NotImplementedError("Implemented in Task 7")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat response from the OpenAI API.

        Args:
            request: The chat request containing messages, tools, and config.
            abort_event: Optional asyncio Event; when set, streaming stops early.

        Yields:
            Text chunks as they arrive from the API.

        Raises:
            NotImplementedError: Until Task 8 is implemented.
        """
        raise NotImplementedError("Implemented in Task 8")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        """Return the model identifier currently active for API calls.

        Returns:
            The active model string (primary or fallback after a switch).
        """
        return self._active_model

    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> OpenAI Chat Completions shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into chat.completions.create kwargs.

        Differences from Anthropic translation:
          - System prompt becomes the first message (role="system").
          - Tool results become separate role="tool" messages.
          - Mixed image+text content uses the content-parts array; pure
            text uses a plain string for the content field.
          - response_format is native (no tool-coercion emulation).

        Args:
            request: The neutral chat request to translate.

        Returns:
            A dict of kwargs ready to pass to openai.chat.completions.create.
        """
        wire_messages: list[dict[str, Any]] = []

        # System prompt → first message.
        if request.system is not None:
            joined = "\n\n".join(b.text for b in request.system.blocks)
            wire_messages.append({"role": "system", "content": joined})

        # Conversation messages.
        for msg in request.messages:
            wire_messages.extend(self._message_to_wire(msg))

        wire: dict[str, Any] = {
            "model": self._active_model,
            "max_completion_tokens": request.max_tokens,
            "messages": wire_messages,
            "timeout": self._timeout_s,
        }

        # Tools.
        if request.tools and request.tool_choice != "none":
            wire["tools"] = [self._tool_to_wire(t) for t in request.tools]
            wire["tool_choice"] = self._tool_choice_to_wire(request.tool_choice)

        # Structured output.
        if request.output_format is not None:
            wire["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "out",
                    "schema": request.output_format.schema,
                    "strict": request.output_format.strict,
                },
            }

        return wire

    @staticmethod
    def _tool_to_wire(t: ToolDefinition) -> dict[str, Any]:
        """Translate a neutral ToolDefinition to an OpenAI function tool dict.

        Args:
            t: The tool definition to translate.

        Returns:
            An OpenAI-shaped tool dict with type, function name, description, parameters.
        """
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    @staticmethod
    def _tool_choice_to_wire(choice: str) -> Any:
        """Translate a neutral tool_choice string to the OpenAI wire shape.

        Args:
            choice: One of "auto", "any", or a specific tool name.

        Returns:
            "auto", "required", or {"type": "function", "function": {"name": ...}}.
        """
        if choice == "auto":
            return "auto"
        if choice == "any":
            return "required"
        # Named tool.
        return {"type": "function", "function": {"name": choice}}

    def _message_to_wire(self, msg: Message) -> list[dict[str, Any]]:
        """Translate one neutral Message into one or more OpenAI messages.

        Returns a list because a single user-role Message containing
        ToolResultBlocks expands into multiple role="tool" messages.

        Args:
            msg: The neutral Message to translate.

        Returns:
            A list of OpenAI message dicts.
        """
        # Separate tool_results — each becomes its own role="tool" message.
        tool_results = [b for b in msg.content if b.type == "tool_result"]
        non_tool_results = [b for b in msg.content if b.type != "tool_result"]

        out: list[dict[str, Any]] = []

        if non_tool_results:
            out.append(self._build_primary_message(msg.role, non_tool_results))

        for tr in tool_results:
            content: str
            if isinstance(tr.content, str):
                content = tr.content
            else:
                content = "".join(tb.text for tb in tr.content)
            out.append({
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": content,
            })

        return out

    def _build_primary_message(self, role: str, blocks: list) -> dict[str, Any]:
        """Build one OpenAI message from a list of content blocks (sans tool_results).

        Args:
            role: The message role (e.g. "user", "assistant").
            blocks: Content blocks excluding ToolResultBlocks.

        Returns:
            A single OpenAI message dict.
        """
        text_blocks = [b for b in blocks if b.type == "text"]
        image_blocks = [b for b in blocks if b.type == "image"]
        tool_use_blocks = [b for b in blocks if b.type == "tool_use"]

        msg: dict[str, Any] = {"role": role}

        # Content shape: string if only TextBlocks, else parts array.
        if image_blocks:
            parts: list[dict[str, Any]] = []
            for tb in text_blocks:
                parts.append({"type": "text", "text": tb.text})
            for ib in image_blocks:
                parts.append({"type": "image_url", "image_url": self._image_url(ib)})
            msg["content"] = parts
        elif text_blocks:
            # Concatenate multiple text blocks (rare on OpenAI side).
            msg["content"] = (
                "\n\n".join(tb.text for tb in text_blocks)
                if len(text_blocks) > 1
                else text_blocks[0].text
            )
        else:
            # No text, no images — assistant turn that's just tool_calls.
            msg["content"] = None if tool_use_blocks else ""

        # Assistant tool_calls (prior-turn replays).
        if tool_use_blocks:
            msg["tool_calls"] = [
                {
                    "id": tu.tool_use_id,
                    "type": "function",
                    "function": {
                        "name": tu.tool_name,
                        "arguments": json.dumps(tu.input),
                    },
                }
                for tu in tool_use_blocks
            ]

        return msg

    @staticmethod
    def _image_url(block: ImageBlock) -> dict[str, str]:
        """Build an OpenAI image_url dict from a neutral ImageBlock.

        Args:
            block: The ImageBlock containing source information.

        Returns:
            A dict with a single "url" key, either a direct URL or a data URL.
        """
        src = block.source
        if src.kind == "url":
            return {"url": src.url}
        # base64 → data URL
        return {"url": f"data:{src.media_type};base64,{src.data}"}

    def _from_wire(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an OpenAI ChatCompletion into a neutral ChatResponse.

        Args:
            raw: The raw OpenAI ChatCompletion response object.
            output_format: If provided, attempt to parse msg.content as JSON
                and populate parsed_output; marks stop_reason="error" on failure.

        Returns:
            A ChatResponse with content blocks, token usage, and stop_reason.
        """
        choice = raw.choices[0]
        msg = choice.message

        content_blocks: list = []
        if msg.content:
            content_blocks.append(TextBlock(text=msg.content))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    parsed_input = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    parsed_input = {}
                content_blocks.append(
                    ToolUseBlock(
                        tool_use_id=tc.id,
                        tool_name=tc.function.name,
                        input=parsed_input,
                    )
                )

        usage = TokenUsage(
            input_tokens=_safe_int(getattr(raw.usage, "prompt_tokens", 0)),
            output_tokens=_safe_int(getattr(raw.usage, "completion_tokens", 0)),
            cache_read_tokens=None,
            cache_creation_tokens=None,
        )

        finish_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
            "content_filter": "error",
            "function_call": "tool_use",
        }
        stop_reason = finish_map.get(choice.finish_reason, "end_turn")

        # Structured output unwrap.
        parsed_output: dict | None = None
        if output_format is not None:
            if msg.content:
                try:
                    parsed_output = json.loads(msg.content)
                except (json.JSONDecodeError, TypeError):
                    parsed_output = None
                    stop_reason = "error"
            else:
                stop_reason = "error"

        return ChatResponse(
            content=content_blocks,
            parsed_output=parsed_output,
            stop_reason=stop_reason,
            model_used=self._active_model,
            usage=usage,
        )
