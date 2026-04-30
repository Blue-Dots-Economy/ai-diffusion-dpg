"""AnthropicChatProvider — only file in agent_core that imports `anthropic`.

Translates neutral chat_provider types to/from Anthropic SDK shapes.
Lifts the retry/backoff/timeout/OTel scaffolding from the legacy
agent_core/src/llm_wrapper/claude_wrapper.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderConfigError,
)
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    TextBlock,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


# Minimum size below which we skip cache_control. Anthropic ignores
# cache markers on prompts shorter than ~1024 tokens; ~4 chars/token is
# a conservative English estimate. Lifted unchanged from legacy
# claude_wrapper.py:113.
_CACHE_MIN_CHARS = 3000

# Default response-token ceiling when ChatRequest.max_tokens is missing
# (it's not, since the model has a default of 4096, but we keep the
# constant so the provider can override consistently if needed).
_DEFAULT_MAX_TOKENS = 4096


class AnthropicChatProvider(ChatProviderBase):
    """Anthropic implementation of ChatProviderBase.

    Reads runtime config from a dict; nothing hardcoded.

    Required keys:
        primary_model    (str) Claude model id
        timeout_ms       (int) per-request timeout in ms
        retry_attempts   (int) attempts before giving up (min 1)

    Optional keys (defaults shown):
        retry_backoff_seconds  list[float]  [0, 0.5, 1.0]
        features.prompt_cache  bool         True  (capability default)
        features.streaming     bool         True
        features.image_input   bool         True
    """

    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )

    def __init__(self, config: dict) -> None:
        """Initialise the provider from a config dict.

        Args:
            config: Runtime configuration dict with required and optional keys.

        Raises:
            ProviderConfigError: If required keys are missing or invalid.
        """
        if not config:
            raise ProviderConfigError(
                "AnthropicChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid Claude model id, or set CONFIG_FOLDER in .env.local "
                "to point at your domain configs folder."
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

        # Effective per-deployment features (AND of capability and config).
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
        self._client = anthropic.Anthropic()
        self._async_client = anthropic.AsyncAnthropic()

    # ------------------------------------------------------------------
    # Public ChatProviderBase methods (filled in subsequent tasks)
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a synchronous LLM call.

        Args:
            request: The chat request to send to the model.

        Returns:
            ChatResponse with the model's reply.

        Raises:
            NotImplementedError: Until implemented in Task 11.
        """
        raise NotImplementedError("Implemented in Task 11")

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream an LLM response token by token.

        Args:
            request: The chat request to send to the model.
            abort_event: Optional event to signal early termination.

        Yields:
            str tokens as they arrive from the model.

        Raises:
            NotImplementedError: Until implemented in Task 12.
        """
        raise NotImplementedError("Implemented in Task 12")
        if False:  # pragma: no cover
            yield ""

    def get_active_model(self) -> str:
        """Return the currently active model identifier.

        Returns:
            The model id string in use for this provider instance.
        """
        return self._active_model

    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> Anthropic SDK shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into Anthropic SDK kwargs.

        The dict returned here is passed directly to
        anthropic.Anthropic.messages.create() (or .stream()).

        Caching: a TextBlock with cache_hint set produces a
        cache_control marker only when (a) features.prompt_cache is on
        and (b) the text exceeds _CACHE_MIN_CHARS — Anthropic ignores
        markers on shorter prompts, and emitting them just bloats the
        request.

        Output format: emulated by appending a synthetic
        respond_with_json tool with the supplied schema and forcing
        tool_choice to it. _from_wire reverses this on the response side.
        """
        wire: dict[str, Any] = {
            "model": self._active_model,
            "max_tokens": request.max_tokens,
            "messages": [self._message_to_wire(m) for m in request.messages],
            "timeout": self._timeout_s,
        }

        if request.system is not None:
            wire["system"] = [
                self._system_block_to_wire(b) for b in request.system.blocks
            ]

        # Tools — combine declared tools with synthetic respond_with_json
        # if output_format is set.
        tools = list(request.tools)
        forced_tool_name: str | None = None
        if request.output_format is not None:
            tools.append(
                ToolDefinition(
                    name="respond_with_json",
                    description="Return the response as JSON conforming to the schema.",
                    input_schema=request.output_format.schema,
                )
            )
            forced_tool_name = "respond_with_json"

        if tools and request.tool_choice != "none":
            wire["tools"] = [self._tool_to_wire(t) for t in tools]

        # tool_choice mapping
        choice = forced_tool_name or request.tool_choice
        if choice == "auto":
            if "tools" in wire:
                wire["tool_choice"] = {"type": "auto"}
        elif choice == "any":
            wire["tool_choice"] = {"type": "any"}
        elif choice == "none":
            # Already handled above by skipping wire["tools"].
            pass
        else:
            # Named tool (either user-forced or synthetic respond_with_json)
            wire["tool_choice"] = {"type": "tool", "name": choice}

        return wire

    @staticmethod
    def _tool_to_wire(t: ToolDefinition) -> dict[str, Any]:
        """Translate a ToolDefinition to the Anthropic SDK tool dict.

        Args:
            t: The neutral ToolDefinition to translate.

        Returns:
            Dict with name, description, and input_schema keys.
        """
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }

    def _system_block_to_wire(self, block: TextBlock) -> dict[str, Any]:
        """Translate a system TextBlock to the Anthropic SDK system block dict.

        Args:
            block: The TextBlock from SystemPrompt.blocks.

        Returns:
            Dict with type, text, and optionally cache_control keys.
        """
        out: dict[str, Any] = {"type": "text", "text": block.text}
        if (
            block.cache_hint
            and self._features["prompt_cache"]
            and len(block.text) >= _CACHE_MIN_CHARS
        ):
            out["cache_control"] = {"type": "ephemeral"}
        return out

    def _message_to_wire(self, msg: Message) -> dict[str, Any]:
        """Translate a neutral Message to the Anthropic SDK message dict.

        Args:
            msg: The neutral Message to translate.

        Returns:
            Dict with role and content keys.
        """
        return {
            "role": msg.role,
            "content": [self._content_block_to_wire(b) for b in msg.content],
        }

    def _content_block_to_wire(self, block) -> dict[str, Any]:
        """Translate a single content block to the Anthropic SDK shape.

        Args:
            block: One of TextBlock, ImageBlock, ToolUseBlock, or ToolResultBlock.

        Returns:
            Dict in the Anthropic SDK content block format.

        Raises:
            AssertionError: If an unknown block type is encountered.
        """
        # block is one of TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock
        if block.type == "text":
            out: dict[str, Any] = {"type": "text", "text": block.text}
            if (
                block.cache_hint
                and self._features["prompt_cache"]
                and len(block.text) >= _CACHE_MIN_CHARS
            ):
                out["cache_control"] = {"type": "ephemeral"}
            return out

        if block.type == "image":
            src = block.source
            if src.kind == "url":
                return {
                    "type": "image",
                    "source": {"type": "url", "url": src.url},
                }
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": src.media_type,
                    "data": src.data,
                },
            }

        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.tool_use_id,
                "name": block.tool_name,
                "input": block.input,
            }

        if block.type == "tool_result":
            content: Any
            if isinstance(block.content, str):
                content = block.content
            else:
                content = [{"type": "text", "text": tb.text} for tb in block.content]
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content,
                "is_error": block.is_error,
            }

        raise AssertionError(f"unknown block type {block.type!r}")
