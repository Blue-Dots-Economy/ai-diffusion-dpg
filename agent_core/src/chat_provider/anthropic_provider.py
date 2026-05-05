"""AnthropicChatProvider — only file in agent_core that imports `anthropic`.

Translates neutral chat_provider types to/from Anthropic SDK shapes.
Lifts the retry/backoff/timeout/OTel scaffolding from the legacy
agent_core/src/llm_wrapper/claude_wrapper.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import anthropic
from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderConfigError,
    UnsupportedFeatureError,
)
from src.chat_provider.metrics import record_call_metrics
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    OutputFormat,
    TextBlock,
    TextBlock as _TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
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


class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed.

    Caught inside AnthropicChatProvider.call() / .stream() to transition
    into the error-response path. Also imported by the legacy adapter
    (llm_wrapper/claude_wrapper.py) so it can detect retry exhaustion
    and trigger its own fallback logic. This coupling is intentional and
    scoped — the adapter is deleted in PR5 (#292).
    """


def _safe_int(value) -> int:
    """Coerce a possibly-missing usage field to int.

    Mirrors the behaviour of llm_wrapper.claude_wrapper._safe_int — keeps
    MagicMock and None values from poisoning metric streams.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


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
        """Execute a single Anthropic call with retries on transient failures.

        Args:
            request: The chat request to send to the model.

        Returns:
            ChatResponse with the model's reply, or an error response if all
            retries are exhausted or a non-retryable error occurs.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If request uses features disabled in config.
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_features(request)
        self._validate_request(request, is_stream=False)

        try:
            return self._call_with_retry(request)
        except _RetryableExhausted:
            return ChatResponse(
                content=[],
                stop_reason="error",
                model_used=self._active_model,
                usage=TokenUsage(),
            )

    def _validate_features(self, request: ChatRequest) -> None:
        """Raise UnsupportedFeatureError if the request uses a disabled deployment feature.

        Args:
            request: The chat request to validate.

        Raises:
            UnsupportedFeatureError: If a feature required by the request is
                disabled in this provider's effective feature config.
        """
        if request.system is not None and not self._features["prompt_cache"]:
            for block in request.system.blocks:
                if block.cache_hint:
                    raise UnsupportedFeatureError(
                        "prompt_cache is disabled in this deployment; "
                        "remove cache_hint from system blocks."
                    )

    def _call_with_retry(self, request: ChatRequest) -> ChatResponse:
        """Execute the Anthropic API call with retry/backoff and OTel spans.

        Args:
            request: The chat request to execute.

        Returns:
            ChatResponse on success.

        Raises:
            _RetryableExhausted: When all retry attempts on transient errors
                are consumed.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[
                min(attempt, len(self._backoff_seconds) - 1)
            ]
            if delay > 0:
                time.sleep(delay)

            start = time.time()
            tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs = self._to_wire(request)
                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "anthropic")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")
                    raw = self._client.messages.create(**kwargs)
                    response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)
                    span.set_attribute(
                        "gen_ai.usage.input_tokens", response.usage.input_tokens or 0
                    )
                    span.set_attribute(
                        "gen_ai.usage.output_tokens", response.usage.output_tokens or 0
                    )
                    span.set_attribute(
                        "gen_ai.usage.cache_read_input_tokens",
                        response.usage.cache_read_tokens or 0,
                    )
                    span.set_attribute(
                        "gen_ai.usage.cache_creation_input_tokens",
                        response.usage.cache_creation_tokens or 0,
                    )

                record_call_metrics(
                    model=self._active_model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                    provider_system="anthropic",
                )
                logger.info(
                    "chat_provider.anthropic.call",
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_read_input_tokens": response.usage.cache_read_tokens,
                        "cache_creation_input_tokens": response.usage.cache_creation_tokens,
                    },
                )
                return response

            except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "chat_provider.anthropic.retryable_error",
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

            except anthropic.APIError as e:
                # Surface the API error in the log MESSAGE itself — not just
                # in `extra` — so a default logging.basicConfig deployment
                # shows what went wrong without needing a structured-extras
                # formatter.
                _body = (
                    getattr(getattr(e, "response", None), "text", None)
                    or getattr(e, "message", None)
                    or str(e)
                )
                logger.error(
                    "chat_provider.anthropic.api_error: %s — %s",
                    type(e).__name__,
                    _body,
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ChatResponse(
                    content=[],
                    stop_reason="error",
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

            except Exception as e:
                logger.error(
                    "chat_provider.anthropic.unexpected_error",
                    extra={
                        "operation": "chat_provider.anthropic.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ChatResponse(
                    content=[],
                    stop_reason="error",
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

        logger.error(
            "chat_provider.anthropic.exhausted",
            extra={
                "operation": "chat_provider.anthropic.call",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(
            f"All {self._max_attempts} retry attempts exhausted for model "
            f"{self._active_model}"
        )

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text tokens from Anthropic.

        Same retry contract as call(); on exhausted retries the
        generator returns silently. Raises ToolUseRequested if the model
        emits any tool_use blocks (caller executes tools and resumes).

        Args:
            request: The chat request to send to the model.
            abort_event: Optional event to signal early termination.

        Yields:
            str tokens as they arrive from the model.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If output_format is set (stream does
                not support structured output).
            ToolUseRequested: If the model emits tool_use blocks in the
                final message.
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=True)

        try:
            async for token in self._stream_with_retry(request, abort_event):
                yield token
        except _RetryableExhausted:
            return

    async def _stream_with_retry(
        self,
        request: ChatRequest,
        abort_event: "asyncio.Event | None",
    ) -> AsyncGenerator[str, None]:
        """Execute the streaming Anthropic API call with retry/backoff and OTel spans.

        Args:
            request: The chat request to stream.
            abort_event: Optional event to abort iteration early.

        Yields:
            str tokens as they arrive from the model.

        Raises:
            _RetryableExhausted: When all retry attempts on transient errors are consumed.
            ToolUseRequested: When the model emits tool_use blocks in the final message.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[
                min(attempt, len(self._backoff_seconds) - 1)
            ]
            if delay > 0:
                await asyncio.sleep(delay)

            start = time.time()
            tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs = self._to_wire(request)
                tool_calls: list[ToolUseBlock] = []
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0
                cache_creation_tokens = 0

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "anthropic")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    async with self._async_client.messages.stream(**kwargs) as stream:
                        async for event in stream:
                            if abort_event is not None and abort_event.is_set():
                                return
                            if hasattr(event, "type") and event.type == "content_block_delta":
                                if hasattr(event.delta, "text"):
                                    yield event.delta.text

                        final_message = await stream.get_final_message()
                        stop_reason = final_message.stop_reason
                        input_tokens = _safe_int(getattr(final_message.usage, "input_tokens", 0))
                        output_tokens = _safe_int(getattr(final_message.usage, "output_tokens", 0))
                        cache_read_tokens = _safe_int(
                            getattr(final_message.usage, "cache_read_input_tokens", 0)
                        )
                        cache_creation_tokens = _safe_int(
                            getattr(final_message.usage, "cache_creation_input_tokens", 0)
                        )
                        for block in final_message.content:
                            if block.type == "tool_use":
                                tool_calls.append(
                                    ToolUseBlock(
                                        tool_use_id=block.id,
                                        tool_name=block.name,
                                        input=block.input,
                                    )
                                )

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    span.set_attribute("gen_ai.usage.cache_read_input_tokens", cache_read_tokens)
                    span.set_attribute("gen_ai.usage.cache_creation_input_tokens", cache_creation_tokens)

                latency_ms = int((time.time() - start) * 1000)
                synth_usage = TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                )
                synth_resp = ChatResponse(
                    content=[],
                    stop_reason=stop_reason or "end_turn",
                    model_used=self._active_model,
                    usage=synth_usage,
                )
                record_call_metrics(
                    model=self._active_model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=synth_resp,
                    provider_system="anthropic",
                )
                logger.info(
                    "chat_provider.anthropic.stream",
                    extra={
                        "operation": "chat_provider.anthropic.stream",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_input_tokens": cache_read_tokens,
                        "cache_creation_input_tokens": cache_creation_tokens,
                        "stop_reason": stop_reason,
                    },
                )

                if stop_reason == "tool_use" and tool_calls:
                    from src.chat_provider.base import ToolUseRequested
                    raise ToolUseRequested(tool_calls)

                return

            except _RetryableExhausted:
                raise

            except Exception as e:
                from src.chat_provider.base import ToolUseRequested
                if isinstance(e, ToolUseRequested):
                    raise
                if isinstance(e, (anthropic.APITimeoutError, anthropic.RateLimitError)):
                    last_error = e
                    logger.warning(
                        "chat_provider.anthropic.stream_retryable_error",
                        extra={
                            "operation": "chat_provider.anthropic.stream",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    continue
                # Non-retryable
                logger.error(
                    "chat_provider.anthropic.stream_error",
                    extra={
                        "operation": "chat_provider.anthropic.stream",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return

        logger.error(
            "chat_provider.anthropic.stream_exhausted",
            extra={
                "operation": "chat_provider.anthropic.stream",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model "
            f"{self._active_model}"
        )

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

    def _from_wire(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an Anthropic Message into a neutral ChatResponse.

        When output_format was set on the request, the response was
        forced into a respond_with_json tool call. We unwrap that here:
        parsed_output is set to the tool input, content is replaced with
        a single TextBlock carrying the JSON string, and stop_reason is
        normalised to "end_turn" so the caller sees a clean response
        rather than tool_use semantics.
        """
        content_blocks: list = []
        synthetic_input: dict | None = None
        for block in raw.content:
            if block.type == "text":
                content_blocks.append(_TextBlock(text=block.text))
            elif block.type == "tool_use":
                if (
                    output_format is not None
                    and block.name == "respond_with_json"
                ):
                    synthetic_input = block.input  # already a dict
                else:
                    content_blocks.append(
                        ToolUseBlock(
                            tool_use_id=block.id,
                            tool_name=block.name,
                            input=block.input,
                        )
                    )

        usage = raw.usage
        token_usage = TokenUsage(
            input_tokens=_safe_int(getattr(usage, "input_tokens", 0)),
            output_tokens=_safe_int(getattr(usage, "output_tokens", 0)),
            cache_read_tokens=_safe_int(getattr(usage, "cache_read_input_tokens", 0)),
            cache_creation_tokens=_safe_int(getattr(usage, "cache_creation_input_tokens", 0)),
        )

        if output_format is not None and synthetic_input is not None:
            return ChatResponse(
                content=[_TextBlock(text=json.dumps(synthetic_input))],
                parsed_output=synthetic_input,
                stop_reason="end_turn",
                model_used=self._active_model,
                usage=token_usage,
            )

        # Standard path
        stop_reason = raw.stop_reason or "end_turn"
        return ChatResponse(
            content=content_blocks,
            parsed_output=None,
            stop_reason=stop_reason,
            model_used=self._active_model,
            usage=token_usage,
        )
