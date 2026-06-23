"""OllamaChatProvider — only file in agent_core that imports `ollama`.

Translates neutral chat_provider types to/from Ollama Chat API shapes.
Ollama exposes a Chat Completions API compatible with OpenAI's interface,
so wire translation closely mirrors openai_provider.py. The main differences:
- Ollama endpoint is configured via base_url (required config).
- Supports fewer capabilities (no prompt caching, no structured output,
  typically no image input for most models).
- Streaming response format is newline-delimited JSON (identical to OpenAI).

Dual-mode operation:
- **Local mode** (default): Uses the native ``ollama`` SDK with
  ``ollama.Client(host=base_url)``. No API key needed.
- **Cloud mode** (``OLLAMA_API_KEY`` env var set): Uses the ``openai``
  SDK pointed at the Ollama-hosted OpenAI-compatible endpoint
  (``openai.OpenAI(base_url=..., api_key=...)``). This mirrors the
  OpenAI provider pattern where the SDK reads the API key automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderAPIError,
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
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
    ImageBlock,
)

logger = logging.getLogger(__name__)

# Minimum size below which we skip cache_control markers.
# Ollama doesn't support prompt caching, but we keep this for consistency.
_CACHE_MIN_CHARS = 3000

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

    Caught only inside OllamaChatProvider.call() / .stream() to
    transition into the error-response path. Never escapes.
    """

    def __init__(self, message: str, error_type: str, error_message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.error_message = error_message


class OllamaChatProvider(ChatProviderBase):
    """Ollama Chat API implementation of ChatProviderBase.

    Ollama runs locally (or on a remote server) and exposes a Chat
    Completions API compatible with OpenAI's interface. Configuration
    is minimal: base_url (endpoint) and model name.

    Two operating modes:

    **Local mode** (no ``OLLAMA_API_KEY``): Uses the native ``ollama``
    Python SDK. ``base_url`` points at the local Ollama server
    (default ``http://localhost:11434``).

    **Cloud mode** (``OLLAMA_API_KEY`` env var set): Uses the ``openai``
    Python SDK pointed at the Ollama-hosted OpenAI-compatible endpoint.
    Mirrors the OpenAI provider pattern where the SDK reads the API key
    and the endpoint is configured via ``base_url``.

    Required config keys:
        primary_model    (str) e.g. "llama2", "neural-chat"
        base_url         (str) e.g. "http://localhost:11434"
        timeout_ms       (int)
        retry_attempts   (int) min 1

    Optional:
        retry_backoff_seconds  list[float]  [0, 0.5, 1.0]
        features.streaming     bool         True

    Environment variables:
        OLLAMA_API_KEY   (str) When set, switches to cloud mode using the
                         openai SDK with API key auth.
    """

    capabilities = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_image_input=True,   # Some Ollama models support vision
        supports_audio_input=False,
        supports_structured_output=False,
        supports_force_tool_choice=False,
    )

    def __init__(self, config: dict) -> None:
        """Initialise the Ollama provider from a config dict.

        Detects operating mode from the environment:
        - If ``OLLAMA_API_KEY`` is set → cloud mode (openai SDK).
        - Otherwise → local mode (ollama SDK).

        Args:
            config: Provider configuration dict. Must contain primary_model,
                base_url, timeout_ms, and retry_attempts.

        Raises:
            ProviderConfigError: If any required config key is missing or invalid.
        """
        if not config:
            raise ProviderConfigError(
                "OllamaChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid Ollama model name (e.g. llama2, neural-chat)."
            )

        base_url = os.environ.get("OLLAMA_ENDPOINT", "")
        if not base_url:
            base_url = config.get("base_url", "")
        if not base_url:
            base_url = "http://localhost:11434"

        if "timeout_ms" not in config:
            raise ProviderConfigError("agent.timeout_ms is required")
        if "retry_attempts" not in config:
            raise ProviderConfigError("agent.retry_attempts is required")

        self._primary_model: str = primary_model
        self._base_url: str = base_url.rstrip("/")  # Remove trailing slash
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

        # API key resolution — mirrors the OpenAI provider pattern.
        # When OLLAMA_API_KEY is set, switch to cloud mode using the openai
        # SDK pointed at the Ollama-hosted OpenAI-compatible endpoint.
        api_key = os.environ.get("OLLAMA_API_KEY", "")

        if api_key:
            # Cloud mode: use the openai SDK with API key auth.
            self._cloud_mode = True
            try:
                import openai
                self._openai_client = openai.OpenAI(
                    base_url=self._base_url,
                    api_key=api_key,
                    timeout=self._timeout_s,
                )
                self._openai_async_client = openai.AsyncOpenAI(
                    base_url=self._base_url,
                    api_key=api_key,
                    timeout=self._timeout_s,
                )
            except ImportError:
                raise ProviderConfigError(
                    "openai package is not installed. Cloud mode Ollama "
                    "requires the openai SDK. "
                    "Install it with: pip install openai"
                )
            # Placeholders so attribute access never fails.
            self._client = None  # type: ignore[assignment]
            self._async_client = None  # type: ignore[assignment]
            logger.info(
                "chat_provider.ollama.cloud_mode",
                extra={
                    "operation": "chat_provider.ollama.init",
                    "status": "success",
                    "base_url": self._base_url,
                    "model": self._primary_model,
                },
            )
        else:
            # Local mode: use the native ollama SDK.
            self._cloud_mode = False
            self._openai_client = None  # type: ignore[assignment]
            self._openai_async_client = None  # type: ignore[assignment]
            try:
                import ollama
                self._client = ollama.Client(host=self._base_url, timeout=self._timeout_s)
                self._async_client = ollama.AsyncClient(host=self._base_url, timeout=self._timeout_s)
            except ImportError:
                raise ProviderConfigError(
                    "ollama package is not installed. "
                    "Install it with: pip install ollama"
                )


    # ------------------------------------------------------------------
    # Public ChatProviderBase methods
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a single Ollama call with retries on transient failures.

        Args:
            request: The chat request containing messages, tools, and config.

        Returns:
            ChatResponse with content blocks and token usage, or an error
            response if all retry attempts are exhausted or a non-retryable
            error occurs.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If the request uses capabilities not
                supported by Ollama (e.g. prompt caching, structured output).
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=False)

        try:
            return self._call_with_retry(request)
        except _RetryableExhausted as e:
            return ChatResponse(
                content=[],
                stop_reason="error",
                error_type=e.error_type,
                error_message=e.error_message,
                model_used=self._active_model,
                usage=TokenUsage(),
            )

    def _call_with_retry(self, request: ChatRequest) -> ChatResponse:
        """Execute the Ollama API call with retry logic for transient errors.

        Args:
            request: The chat request to execute.

        Returns:
            ChatResponse on success.

        Raises:
            _RetryableExhausted: If all retry attempts are consumed by
                transient errors (connection errors, timeouts).
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
                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "ollama")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")

                    if self._cloud_mode:
                        kwargs = self._to_wire_openai(request)
                        raw = self._openai_client.chat.completions.create(**kwargs)
                        response = self._from_wire_openai(raw, output_format=request.output_format)
                    else:
                        kwargs = self._to_wire(request)
                        raw = self._client.chat(**kwargs)
                        response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)

                    span.set_attribute(
                        "gen_ai.usage.input_tokens", response.usage.input_tokens or 0
                    )
                    span.set_attribute(
                        "gen_ai.usage.output_tokens", response.usage.output_tokens or 0
                    )

                record_call_metrics(
                    model=self._active_model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                    provider_system="ollama",
                )
                logger.info(
                    "chat_provider.ollama.call",
                    extra={
                        "operation": "chat_provider.ollama.call",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                )
                return response

            except Exception as e:
                # Classify error type
                error_name = type(e).__name__
                is_retryable = self._is_retryable_error(e)

                if is_retryable:
                    last_error = e
                    logger.warning(
                        "chat_provider.ollama.retryable_error",
                        extra={
                            "operation": "chat_provider.ollama.call",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "error_type": error_name,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    continue

                # Non-retryable
                logger.error(
                    "chat_provider.ollama.api_error: %s",
                    error_name,
                    extra={
                        "operation": "chat_provider.ollama.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "error_type": error_name,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return ChatResponse(
                    content=[],
                    stop_reason="error",
                    error_type="api_error",
                    error_message="We're having trouble connecting to the AI service right now. Please try again shortly.",
                    model_used=self._active_model,
                    usage=TokenUsage(),
                )

        logger.error(
            "chat_provider.ollama.exhausted",
            extra={
                "operation": "chat_provider.ollama.call",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        err_str = str(last_error).lower()
        if "timeout" in err_str or "deadline" in err_str:
            error_type = "timeout"
        elif "429" in err_str or "rate" in err_str:
            error_type = "rate_limit"
        else:
            error_type = "timeout"
        error_message = str(last_error) if last_error else "All retry attempts exhausted"
        raise _RetryableExhausted(
            f"All {self._max_attempts} retry attempts exhausted for model {self._active_model}",
            error_type=error_type,
            error_message=error_message,
        )

    async def stream(
        self,
        request: ChatRequest,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text tokens from Ollama Chat API.

        Same retry contract as call(). Yields text deltas as they
        arrive. After the stream closes, if any tool_calls were emitted,
        raises ToolUseRequested with the accumulated calls.

        Args:
            request: The chat request containing messages, tools, and config.
            abort_event: Optional asyncio Event; when set, streaming stops early.

        Yields:
            Text token strings as they arrive from the API.

        Raises:
            ValueError: If request.messages is empty.
            UnsupportedFeatureError: If request uses output_format (not
                compatible with streaming) or other unsupported features.
            ToolUseRequested: After the stream closes, if the model emitted
                tool calls.
        """
        if not request.messages:
            raise ValueError("messages must not be empty")
        self._validate_request(request, is_stream=True)

        try:
            async for token in self._stream_with_retry(request, abort_event):
                yield token
        except _RetryableExhausted as e:
            raise ProviderAPIError(
                f"All {self._max_attempts} stream retry attempts exhausted: {e.error_message}",
                error_type=e.error_type,
                error_message=e.error_message,
            ) from e

    async def _stream_with_retry(
        self,
        request: ChatRequest,
        abort_event: "asyncio.Event | None",
    ) -> AsyncGenerator[str, None]:
        """Inner retry loop for stream().

        Args:
            request: The chat request to stream.
            abort_event: Optional event to abort mid-stream.

        Yields:
            Text token strings.

        Raises:
            _RetryableExhausted: If all attempts are consumed by transient errors.
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
                input_tokens = 0
                output_tokens = 0
                stop_reason: str | None = None
                tool_call_buf: dict[int, dict] = {}

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "ollama")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    if self._cloud_mode:
                        kwargs = self._to_wire_openai(request)
                        kwargs["stream"] = True
                        kwargs["stream_options"] = {"include_usage": True}
                        stream_obj = await self._openai_async_client.chat.completions.create(**kwargs)
                        async for chunk in stream_obj:
                            if abort_event is not None and abort_event.is_set():
                                return

                            choice = chunk.choices[0] if chunk.choices else None
                            if choice is not None:
                                delta = choice.delta
                                if delta is not None:
                                    if delta.content:
                                        yield delta.content
                                    if delta.tool_calls:
                                        for tc_delta in delta.tool_calls:
                                            idx = tc_delta.index
                                            slot = tool_call_buf.setdefault(
                                                idx, {"id": None, "name": None, "args": ""}
                                            )
                                            if tc_delta.id is not None:
                                                slot["id"] = tc_delta.id
                                            fn = tc_delta.function
                                            if fn is not None and fn.name:
                                                slot["name"] = fn.name
                                            if fn is not None and fn.arguments:
                                                slot["args"] += fn.arguments
                                if choice.finish_reason is not None:
                                    stop_reason = choice.finish_reason

                            if getattr(chunk, "usage", None) is not None:
                                input_tokens = _safe_int(getattr(chunk.usage, "prompt_tokens", 0))
                                output_tokens = _safe_int(getattr(chunk.usage, "completion_tokens", 0))
                    else:
                        kwargs = self._to_wire(request)
                        kwargs["stream"] = True
                        async for chunk in await self._async_client.chat(**kwargs):
                            if abort_event is not None and abort_event.is_set():
                                return

                            if getattr(chunk.message, "content", None):
                                yield chunk.message.content

                            if getattr(chunk, "done_reason", None):
                                stop_reason = chunk.done_reason

                            # Extract token usage from final chunk
                            if getattr(chunk, "done", False):
                                input_tokens = getattr(chunk, "prompt_eval_count", 0) or 0
                                output_tokens = getattr(chunk, "eval_count", 0) or 0

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

                mapped_stop_reason = "end_turn"
                if stop_reason == "length":
                    mapped_stop_reason = "max_tokens"
                elif stop_reason == "tool_calls":
                    mapped_stop_reason = "tool_use"
                elif stop_reason in ("stop", "", "stop_sequence"):
                    mapped_stop_reason = "end_turn"
                elif stop_reason == "error":
                    mapped_stop_reason = "error"

                latency_ms = int((time.time() - start) * 1000)
                synth_resp = ChatResponse(
                    content=[],
                    stop_reason=mapped_stop_reason,
                    model_used=self._active_model,
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=None,
                        cache_creation_tokens=None,
                    ),
                )
                record_call_metrics(
                    model=self._active_model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=synth_resp,
                    provider_system="ollama",
                )

                # In cloud mode, handle tool calls from OpenAI-format stream.
                if self._cloud_mode and stop_reason == "tool_calls" and tool_call_buf:
                    from src.chat_provider.base import ToolUseRequested
                    tool_calls: list[ToolUseBlock] = []
                    for idx in sorted(tool_call_buf.keys()):
                        slot = tool_call_buf[idx]
                        try:
                            parsed = json.loads(slot["args"]) if slot["args"] else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        tool_calls.append(
                            ToolUseBlock(
                                tool_use_id=slot["id"] or f"call_{idx}",
                                tool_name=slot["name"] or "",
                                input=parsed,
                            )
                        )
                    raise ToolUseRequested(tool_calls)

                logger.info(
                    "chat_provider.ollama.stream",
                    extra={
                        "operation": "chat_provider.ollama.stream",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "stop_reason": stop_reason,
                    },
                )
                return

            except Exception as e:
                is_retryable = self._is_retryable_error(e)
                if is_retryable:
                    last_error = e
                    logger.warning(
                        "chat_provider.ollama.stream_retryable_error",
                        extra={
                            "operation": "chat_provider.ollama.stream",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    continue

                logger.error(
                    "chat_provider.ollama.stream_error: %s",
                    type(e).__name__,
                    extra={
                        "operation": "chat_provider.ollama.stream",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                raise ProviderAPIError("We're having trouble connecting to the AI service right now. Please try again shortly.") from e

        logger.error(
            "chat_provider.ollama.stream_exhausted",
            extra={
                "operation": "chat_provider.ollama.stream",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        err_str = str(last_error).lower()
        if "timeout" in err_str or "deadline" in err_str:
            error_type = "timeout"
        elif "429" in err_str or "rate" in err_str:
            error_type = "rate_limit"
        else:
            error_type = "timeout"
        error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model {self._active_model}",
            error_type=error_type,
            error_message=error_message,
        )

    def get_active_model(self) -> str:
        """Return the model identifier currently active for API calls.

        Returns:
            The active model string (primary model for Ollama).
        """
        return self._active_model

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------

    @staticmethod
    def _is_retryable_error(e: Exception) -> bool:
        """Classify whether an error is transient (retryable) or permanent.

        Args:
            e: The exception to classify.

        Returns:
            True if the error is transient (connection error, timeout, etc.)
            False if the error is permanent (model not found, validation error, etc.)
        """
        error_name = type(e).__name__

        # Connection/timeout errors
        if error_name in (
            "ConnectionError",
            "TimeoutError",
            "RemoteProtocolError",
            "ConnectError",
        ):
            return True

        # HTTP status code errors (transient: 429 rate limit, 5xx server errors)
        if hasattr(e, "status_code"):
            status = getattr(e, "status_code", None)
            if status in (429, 500, 502, 503, 504):
                return True

        return False

    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> Ollama Chat API shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into Ollama Chat API kwargs.

        Ollama's Chat API is compatible with OpenAI's Chat Completions API,
        so translation closely mirrors openai_provider._to_wire().

        Args:
            request: The neutral chat request to translate.

        Returns:
            A dict of kwargs ready to pass to ollama.Client.chat().
        """
        wire_messages: list[dict[str, Any]] = []

        # System prompt → first message.
        if request.system is not None:
            system_blocks = []
            for b in request.system.blocks:
                block_dict: dict[str, Any] = {"type": "text", "text": b.text}
                if (
                    b.cache_hint
                    and self._features["prompt_cache"]
                    and len(b.text) >= _CACHE_MIN_CHARS
                ):
                    block_dict["cache_control"] = {"type": "ephemeral"}
                system_blocks.append(block_dict)
            
            # Flatten system blocks into a single system message
            joined = "\n\n".join(b.text for b in request.system.blocks)
            wire_messages.append({"role": "system", "content": joined})

        # Conversation messages.
        for msg in request.messages:
            wire_messages.extend(self._message_to_wire(msg, is_openai=False))

        wire: dict[str, Any] = {
            "model": self._active_model,
            "messages": wire_messages,
            "options": {
                "num_predict": request.max_tokens,
            },
        }

        # Tools (if supported by model).
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
            # Map tool_choice to Ollama format
            choice = forced_tool_name or request.tool_choice
            if choice == "auto":
                wire["tool_choice"] = "auto"
            elif choice == "any":
                wire["tool_choice"] = "required"
            elif choice not in ("none",):
                # Named tool
                wire["tool_choice"] = {"type": "function", "name": choice}

        return wire

    @staticmethod
    def _tool_to_wire(t: ToolDefinition) -> dict[str, Any]:
        """Translate a neutral ToolDefinition to an Ollama function tool dict.

        Args:
            t: The tool definition to translate.

        Returns:
            An Ollama-shaped tool dict.
        """
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    def _message_to_wire(self, msg: Message, is_openai: bool = False) -> list[dict[str, Any]]:
        """Translate one neutral Message into one or more Ollama messages.

        Returns a list because a single user-role Message containing
        ToolResultBlocks expands into multiple role="tool" messages.

        Args:
            msg: The neutral Message to translate.
            is_openai: If True, uses the OpenAI wire format.

        Returns:
            A list of Ollama message dicts.
        """
        # Separate tool_results — each becomes its own role="tool" message.
        tool_results = [b for b in msg.content if b.type == "tool_result"]
        non_tool_results = [b for b in msg.content if b.type != "tool_result"]

        out: list[dict[str, Any]] = []

        if non_tool_results:
            out.append(self._build_primary_message(msg.role, non_tool_results, is_openai=is_openai))

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

    def _build_primary_message(self, role: str, blocks: list, is_openai: bool = False) -> dict[str, Any]:
        """Build one Ollama message from a list of content blocks (sans tool_results).

        Args:
            role: The message role (e.g. "user", "assistant").
            blocks: Content blocks excluding ToolResultBlocks.
            is_openai: If True, uses the OpenAI wire format.

        Returns:
            A single Ollama message dict.
        """
        text_blocks = [b for b in blocks if b.type == "text"]
        image_blocks = [b for b in blocks if b.type == "image"]
        tool_use_blocks = [b for b in blocks if b.type == "tool_use"]

        msg: dict[str, Any] = {"role": role}

        if is_openai:
            # Content: string if only TextBlocks, else parts array for mixed content
            if image_blocks and self._features["image_input"]:
                # Mixed content: use parts array
                parts: list[dict[str, Any]] = []
                for tb in text_blocks:
                    part: dict[str, Any] = {"type": "text", "text": tb.text}
                    if (
                        tb.cache_hint
                        and self._features["prompt_cache"]
                        and len(tb.text) >= _CACHE_MIN_CHARS
                    ):
                        part["cache_control"] = {"type": "ephemeral"}
                    parts.append(part)
                for ib in image_blocks:
                    parts.append({"type": "image_url", "image_url": self._image_url(ib)})
                msg["content"] = parts
            elif text_blocks:
                # Text only
                if len(text_blocks) > 1:
                    # Multiple text blocks: concatenate them
                    msg["content"] = "\n\n".join(tb.text for tb in text_blocks)
                else:
                    msg["content"] = text_blocks[0].text
            else:
                msg["content"] = None if tool_use_blocks else ""
        else:
            # Native Ollama path
            text_content = ""
            if text_blocks:
                if len(text_blocks) > 1:
                    text_content = "\n\n".join(tb.text for tb in text_blocks)
                else:
                    text_content = text_blocks[0].text
            else:
                text_content = None if tool_use_blocks else ""
            msg["content"] = text_content

            if image_blocks and self._features["image_input"]:
                images = []
                for ib in image_blocks:
                    if ib.source.kind == "base64":
                        images.append(ib.source.data)
                if images:
                    msg["images"] = images

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
    def _image_url(block) -> dict[str, str]:
        """Build an Ollama image_url dict from a neutral ImageBlock.

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
        """Translate an Ollama chat response into a neutral ChatResponse.

        Args:
            raw: The raw Ollama chat response object.
            output_format: If provided, parse message content as JSON for
                structured output; mark stop_reason="error" if parsing fails.

        Returns:
            A ChatResponse with content blocks, token usage, and stop_reason.
        """
        content_blocks: list = []

        # Extract message content
        msg = getattr(raw, "message", None)
        msg_content = getattr(msg, "content", "") if msg else ""
        if msg_content:
            content_blocks.append(TextBlock(text=msg_content))

        # Extract tool calls if present
        tool_calls = getattr(msg, "tool_calls", None) if msg else None
        if tool_calls:
            for tc in tool_calls:
                func = getattr(tc, "function", None)
                parsed_input = {}
                tool_name = ""
                if func:
                    tool_name = getattr(func, "name", "")
                    arguments = getattr(func, "arguments", {})
                    if isinstance(arguments, dict):
                        parsed_input = arguments
                content_blocks.append(
                    ToolUseBlock(
                        tool_use_id=getattr(tc, "id", "") or "unknown",
                        tool_name=tool_name,
                        input=parsed_input,
                    )
                )

        # Map stop reason
        stop_reason = getattr(raw, "done_reason", "end_turn") or "end_turn"
        if stop_reason == "length":
            stop_reason = "max_tokens"
        elif stop_reason in ("stop", "", "tool_calls"):
            stop_reason = "end_turn"

        # Extract token usage
        input_tokens = _safe_int(getattr(raw, "prompt_eval_count", 0))
        output_tokens = _safe_int(getattr(raw, "eval_count", 0))

        # Parse structured output if requested
        parsed_output = None
        if output_format is not None and msg_content:
            try:
                parsed_output = json.loads(msg_content)
            except (json.JSONDecodeError, TypeError):
                # JSON parse error marks the response as error
                stop_reason = "error"
                parsed_output = None

        return ChatResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            model_used=self._active_model,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=None,
                cache_creation_tokens=None,
            ),
            parsed_output=parsed_output,
        )

    # ------------------------------------------------------------------
    # Cloud-mode wire translation (OpenAI-compatible format)
    # ------------------------------------------------------------------

    def _to_wire_openai(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a neutral ChatRequest into OpenAI Chat Completions kwargs.

        Used in cloud mode where the Ollama endpoint exposes an
        OpenAI-compatible API. Mirrors openai_provider._to_wire().

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
            wire_messages.extend(self._message_to_wire(msg, is_openai=True))

        wire: dict[str, Any] = {
            "model": self._active_model,
            "max_completion_tokens": request.max_tokens,
            "messages": wire_messages,
            "timeout": self._timeout_s,
        }

        # Tools.
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
            choice = forced_tool_name or request.tool_choice
            if choice == "auto":
                wire["tool_choice"] = "auto"
            elif choice == "any":
                wire["tool_choice"] = "required"
            elif choice not in ("none",):
                wire["tool_choice"] = {"type": "function", "function": {"name": choice}}

        return wire

    def _from_wire_openai(self, raw, output_format: OutputFormat | None) -> ChatResponse:
        """Translate an OpenAI ChatCompletion into a neutral ChatResponse.

        Used in cloud mode. Mirrors openai_provider._from_wire().

        Args:
            raw: The raw OpenAI ChatCompletion response object.
            output_format: If provided, attempt to parse content as JSON.

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

        # Map stop reason.
        finish = choice.finish_reason
        error_type: str | None = None
        error_message: str | None = None

        if finish == "tool_calls":
            stop_reason = "tool_use"
        elif finish == "stop":
            stop_reason = "end_turn"
        elif finish == "length":
            stop_reason = "max_tokens"
        elif finish == "content_filter":
            stop_reason = "error"
            error_type = "safety_blocked"
            error_message = "We're experiencing a temporary issue with the AI service. Please try again."
        else:
            stop_reason = "end_turn"

        # Token usage.
        usage = getattr(raw, "usage", None)
        input_tokens = _safe_int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        output_tokens = _safe_int(getattr(usage, "completion_tokens", 0)) if usage else 0

        # Structured output.
        parsed_output = None
        if output_format is not None and msg.content:
            try:
                parsed_output = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                stop_reason = "error"
                parsed_output = None

        return ChatResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            error_type=error_type,
            error_message=error_message,
            model_used=self._active_model,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=None,
                cache_creation_tokens=None,
            ),
            parsed_output=parsed_output,
        )

