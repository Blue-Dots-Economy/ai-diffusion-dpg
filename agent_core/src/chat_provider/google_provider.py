"""GoogleChatProvider — only file in agent_core that imports ``google-genai``.

Translates neutral chat_provider types to/from Google Gemini
SDK shapes.  We use ``google-genai`` as it is the recommended SDK.

Gemini supports structured output, tools, streaming, and image input.
Prompt caching is implemented via the ``CachedContent`` resource — the
system prompt (when it carries a ``cache_hint`` and exceeds the minimum
token threshold) is uploaded as a cache and referenced via
``cached_content=`` in the generate call.

Belongs to the Agent Core DPG block.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

from google import genai
from google.genai import types
from opentelemetry import trace as otel_trace

from src.chat_provider.base import (
    Capabilities,
    ChatProviderBase,
    ProviderAPIError,
    ProviderConfigError,
    UnsupportedFeatureError,
    ToolUseRequested,
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

# Minimum character count for system-prompt caching.  Gemini's context
# caching requires >=32 768 tokens; ~4 chars/token → ~131 072 chars.
# We use a conservative threshold slightly below that.
_CACHE_MIN_CHARS = 120_000

# Default TTL for cached content (1 hour).
_CACHE_TTL = "3600s"

# Keywords in error messages that indicate a transient (retryable) failure.
_TRANSIENT_KEYWORDS = frozenset((
    "429", "rate", "timeout", "503", "service unavailable",
    "deadline", "temporarily", "resource_exhausted",
    "internal", "unavailable",
))


def _safe_int(value: Any) -> int:
    """Coerce a possibly-missing usage field to int.

    Args:
        value: The raw usage field from the API response.

    Returns:
        Integer value, or 0 if the input is None or an unexpected type.
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


def _is_transient_error(exc: Exception) -> bool:
    """Determine whether an exception represents a transient failure.

    Checks the string representation of the exception against known
    transient-error keywords (rate limits, timeouts, 503s).

    Args:
        exc: The exception to classify.

    Returns:
        True if the error should be retried; False for fatal errors.
    """
    error_str = str(exc).lower()
    return any(kw in error_str for kw in _TRANSIENT_KEYWORDS)


class _RetryableExhausted(Exception):
    """Internal: all retry attempts on transient errors were consumed."""

    def __init__(self, message: str, error_type: str, error_message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.error_message = error_message


class GoogleChatProvider(ChatProviderBase):
    """Google Gemini implementation of ChatProviderBase.

    Reads runtime config from a dict; nothing hardcoded.

    Required keys:
        primary_model    (str) Gemini model id
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
            ProviderConfigError: If required keys are missing or invalid, or
                if no Google/Gemini API key is available.
        """
        if not config:
            raise ProviderConfigError(
                "GoogleChatProvider requires a non-empty config dict"
            )

        primary_model = config.get("primary_model", "")
        if not primary_model:
            raise ProviderConfigError(
                "agent.primary_model is not set. Ensure your domain config has "
                "a valid Google/Gemini model id (e.g. gemini-3.5-flash)."
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

        # Explicitly read the API key — docker-compose sets GOOGLE_API_KEY / GEMINI_API_KEY.
        api_key = (
            config.get("api_key")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        )
        if not api_key:
            raise ProviderConfigError(
                "No Google API key found. Set GOOGLE_API_KEY or GEMINI_API_KEY "
                "in the environment, or pass api_key in the config dict."
            )
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": self._timeout_s},
        )
        self._async_client = genai.Client(
            api_key=api_key,
            http_options={"timeout": self._timeout_s},
        )

        # Cache state for prompt caching (maps content hash → cache name).
        self._cache_name: str | None = None
        self._cache_content_hash: str | None = None

    # ------------------------------------------------------------------
    # Public ChatProviderBase methods
    # ------------------------------------------------------------------

    def call(self, request: ChatRequest) -> ChatResponse:
        """Execute a single Google call with retries on transient failures.

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
        """Execute the Google API call with retry/backoff and OTel spans.

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
                contents, config_kwargs = self._to_wire(request)

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "google")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")

                    raw = self._client.models.generate_content(
                        model=self._active_model,
                        contents=contents,
                        config=types.GenerateContentConfig(**config_kwargs)
                    )
                    response = self._from_wire(raw, output_format=request.output_format)
                    latency_ms = int((time.time() - start) * 1000)

                    span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens or 0)
                    span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens or 0)
                    span.set_attribute("gen_ai.usage.cache_read_input_tokens", response.usage.cache_read_tokens or 0)

                record_call_metrics(
                    model=self._active_model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                    provider_system="google",
                )
                logger.info(
                    "chat_provider.google.call",
                    extra={
                        "operation": "chat_provider.google.call",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_read_tokens": response.usage.cache_read_tokens,
                    },
                )
                return response

            except Exception as e:
                last_error = e
                latency_ms = int((time.time() - start) * 1000)

                if _is_transient_error(e):
                    logger.warning(
                        "chat_provider.google.retryable_error",
                        extra={
                            "operation": "chat_provider.google.call",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "latency_ms": latency_ms,
                        },
                    )
                    continue

                # Non-retryable error — log and return error response.
                logger.error(
                    "chat_provider.google.api_error: %s — %s",
                    type(e).__name__,
                    str(e),
                    extra={
                        "operation": "chat_provider.google.call",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": latency_ms,
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
            "chat_provider.google.exhausted",
            extra={
                "operation": "chat_provider.google.call",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        err_str = str(last_error).lower()
        if "429" in err_str or "rate" in err_str or "resource" in err_str:
            error_type = "rate_limit"
        elif "timeout" in err_str or "deadline" in err_str:
            error_type = "timeout"
        else:
            error_type = "timeout"
        error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."
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
        """Stream raw text tokens from Google.

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
        """Execute the streaming Google API call with retry/backoff and OTel spans.

        Args:
            request: The chat request to stream.
            abort_event: Optional event to abort iteration early.

        Yields:
            str tokens as they arrive from the model.

        Raises:
            _RetryableExhausted: When all retry attempts on transient errors
                are consumed.
            ToolUseRequested: When the model emits tool_use blocks in the
                final message.
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
                contents, config_kwargs = self._to_wire(request)

                tool_calls_buf: list[ToolUseBlock] = []
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0

                with tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.system", "google")
                    span.set_attribute("gen_ai.model", self._active_model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    stream_obj = await self._async_client.aio.models.generate_content_stream(
                        model=self._active_model,
                        contents=contents,
                        config=types.GenerateContentConfig(**config_kwargs)
                    )

                    async for chunk in stream_obj:
                        if abort_event is not None and abort_event.is_set():
                            return

                        if chunk.text:
                            yield chunk.text

                        # Gemini streaming chunks are snapshots, not deltas.
                        # Replace the tool_calls buffer on each chunk that
                        # carries function_calls to avoid duplicates.
                        if chunk.function_calls:
                            tool_calls_buf = [
                                ToolUseBlock(
                                    tool_use_id=fc.id or fc.name,
                                    tool_name=fc.name,
                                    input=dict(fc.args) if fc.args else {},
                                )
                                for fc in chunk.function_calls
                            ]

                        # Accumulate usage if available in the chunk.
                        if chunk.usage_metadata:
                            input_tokens = _safe_int(
                                getattr(chunk.usage_metadata, "prompt_token_count", input_tokens)
                            )
                            output_tokens = _safe_int(
                                getattr(chunk.usage_metadata, "candidates_token_count", output_tokens)
                            )
                            cache_read_tokens = _safe_int(
                                getattr(chunk.usage_metadata, "cached_content_token_count", cache_read_tokens)
                            )

                        if chunk.candidates and chunk.candidates[0].finish_reason:
                            fr = chunk.candidates[0].finish_reason
                            stop_reason = (
                                str(fr.name) if hasattr(fr, "name") else str(fr)
                            )

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

                latency_ms = int((time.time() - start) * 1000)

                # Map Gemini stop reasons to neutral stop_reason values.
                mapped_stop_reason = "end_turn"
                if stop_reason == "MAX_TOKENS":
                    mapped_stop_reason = "max_tokens"
                elif stop_reason in ("SAFETY", "RECITATION"):
                    mapped_stop_reason = "error"
                elif tool_calls_buf:
                    mapped_stop_reason = "tool_use"

                synth_resp = ChatResponse(
                    content=[],
                    stop_reason=mapped_stop_reason,  # type: ignore[arg-type]
                    model_used=self._active_model,
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens,
                    ),
                )

                record_call_metrics(
                    model=self._active_model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=synth_resp,
                    provider_system="google",
                )
                logger.info(
                    "chat_provider.google.stream",
                    extra={
                        "operation": "chat_provider.google.stream",
                        "status": "success",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "stop_reason": stop_reason,
                    },
                )

                if mapped_stop_reason == "error":
                    err_t = "safety_blocked" if stop_reason == "SAFETY" else ("recitation_blocked" if stop_reason == "RECITATION" else "api_error")
                    raise ProviderAPIError(
                        f"Google stream stopped due to {stop_reason or 'safety/recitation block'}",
                        error_type=err_t,
                        error_message="We're experiencing a temporary issue with the AI service. Please try again.",
                    )

                if mapped_stop_reason == "tool_use" and tool_calls_buf:
                    raise ToolUseRequested(tool_calls_buf)

                return

            except _RetryableExhausted:
                raise
            except ToolUseRequested:
                raise
            except ProviderAPIError:
                raise
            except Exception as e:
                last_error = e
                latency_ms = int((time.time() - start) * 1000)

                if _is_transient_error(e):
                    logger.warning(
                        "chat_provider.google.stream_retryable_error",
                        extra={
                            "operation": "chat_provider.google.stream",
                            "status": "failure",
                            "model": self._active_model,
                            "attempt": attempt + 1,
                            "error": str(e),
                            "latency_ms": latency_ms,
                        },
                    )
                    continue

                # Non-retryable error — log and raise.
                logger.error(
                    "chat_provider.google.stream_error: %s — %s",
                    type(e).__name__,
                    str(e),
                    extra={
                        "operation": "chat_provider.google.stream",
                        "status": "failure",
                        "model": self._active_model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": latency_ms,
                    },
                )
                raise ProviderAPIError("We're having trouble connecting to the AI service right now. Please try again shortly.") from e

        logger.error(
            "chat_provider.google.stream_exhausted",
            extra={
                "operation": "chat_provider.google.stream",
                "status": "failure",
                "model": self._active_model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        err_str = str(last_error).lower()
        if "429" in err_str or "rate" in err_str or "resource" in err_str:
            error_type = "rate_limit"
        elif "timeout" in err_str or "deadline" in err_str:
            error_type = "timeout"
        else:
            error_type = "timeout"
        error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."
        raise _RetryableExhausted(
            f"All {self._max_attempts} stream retry attempts exhausted for model {self._active_model}",
            error_type=error_type,
            error_message=error_message,
        )

    def get_active_model(self) -> str:
        """Return the currently active model identifier.

        Returns:
            The model id string in use for this provider instance.
        """
        return self._active_model

    # ------------------------------------------------------------------
    # Wire translation — neutral types <-> Google SDK shapes
    # ------------------------------------------------------------------

    def _to_wire(self, request: ChatRequest) -> tuple[list[types.Content], dict[str, Any]]:
        """Translate a neutral ChatRequest into Google SDK shapes.

        Returns a (contents, config_kwargs) tuple.  ``contents`` is the
         history as ``types.Content`` objects; ``config_kwargs``
        is passed to ``types.GenerateContentConfig``.

        Caching: if ``features.prompt_cache`` is enabled and the system
        prompt carries a ``cache_hint`` and exceeds ``_CACHE_MIN_CHARS``,
        the system text is uploaded as a ``CachedContent`` resource and
        referenced via ``cached_content=`` in the config.  When a cache
        is active, ``system_instruction`` is omitted (it lives inside the
        cache).

        Args:
            request: The neutral ChatRequest to translate.

        Returns:
            Tuple of (contents list, config kwargs dict).
        """
        contents: list[types.Content] = []
        config_kwargs: dict[str, Any] = {}

        # --- System prompt & caching -----------------------------------------
        sys_text: str | None = None
        has_cache_hint = False
        if request.system is not None:
            sys_text = "\n\n".join(b.text for b in request.system.blocks)
            has_cache_hint = any(b.cache_hint for b in request.system.blocks)

        if (
            sys_text
            and has_cache_hint
            and self._features["prompt_cache"]
            and len(sys_text) >= _CACHE_MIN_CHARS
        ):
            # Attempt to use or create a CachedContent resource.
            cache_name = self._get_or_create_cache(sys_text)
            if cache_name:
                config_kwargs["cached_content"] = cache_name
                # When using cached_content, system_instruction must NOT
                # be set — it is already inside the cache.
            else:
                # Caching failed (too few tokens, API error) — fall back.
                config_kwargs["system_instruction"] = sys_text
        elif sys_text:
            config_kwargs["system_instruction"] = sys_text

        # --- Build tool_id → tool_name map -----------------------------------
        tool_id_to_name: dict[str, str] = {}
        for msg in request.messages:
            for block in msg.content:
                if block.type == "tool_use":
                    tool_id_to_name[block.tool_use_id] = block.tool_name

        # --- Messages --------------------------------------------------------
        for msg in request.messages:
            role = "user" if msg.role == "user" else "model"
            parts: list[types.Part] = []

            for block in msg.content:
                if block.type == "text":
                    parts.append(types.Part.from_text(text=block.text))
                elif block.type == "image":
                    if block.source.kind == "base64" and block.source.data and block.source.media_type:
                        b64_bytes = base64.b64decode(block.source.data)
                        parts.append(types.Part.from_bytes(data=b64_bytes, mime_type=block.source.media_type))
                    elif block.source.kind == "url" and block.source.url:
                        parts.append(types.Part.from_uri(uri=block.source.url, mime_type="image/jpeg"))
                elif block.type == "tool_use":
                    parts.append(types.Part.from_function_call(
                        name=block.tool_name,
                        args=block.input
                    ))
                elif block.type == "tool_result":
                    # ToolResultBlock.content: str | list[TextBlock]
                    content_str = (
                        block.content
                        if isinstance(block.content, str)
                        else "\n".join(b.text for b in block.content)
                    )
                    # Gemini function_response requires a dict.
                    try:
                        resp_dict = json.loads(content_str)
                    except json.JSONDecodeError:
                        resp_dict = {"result": content_str}
                    func_name = tool_id_to_name.get(block.tool_use_id, block.tool_use_id)
                    parts.append(types.Part.from_function_response(
                        name=func_name,
                        response=resp_dict
                    ))

            if parts:
                contents.append(types.Content(role=role, parts=parts))

        # --- Max tokens ------------------------------------------------------
        config_kwargs["max_output_tokens"] = request.max_tokens

        # --- Tools -----------------------------------------------------------
        if request.tools and request.tool_choice != "none":
            func_decls = []
            for t in request.tools:
                func_decls.append(types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=t.input_schema
                ))

            tool = types.Tool(function_declarations=func_decls)
            config_kwargs["tools"] = [tool]

            if request.tool_choice == "auto":
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                )
            elif request.tool_choice == "any":
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="ANY")
                )
            else:
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=[request.tool_choice]
                    )
                )

        # --- Output format ---------------------------------------------------
        if request.output_format is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = request.output_format.schema

        return contents, config_kwargs

    def _from_wire(self, raw: types.GenerateContentResponse, output_format: OutputFormat | None) -> ChatResponse:
        """Translate a Google GenerateContentResponse into a neutral ChatResponse.

        Guards against empty ``candidates`` (safety blocks, quota
        exhaustion, auth errors) which would otherwise raise an
        exception when accessing ``.text``.

        Args:
            raw: The raw API response.
            output_format: The output format from the request, if any.

        Returns:
            A neutral ChatResponse.
        """
        content_blocks: list = []
        parsed_output: dict | None = None
        stop_reason = "end_turn"
        error_type: str | None = None
        error_message: str | None = None
 
        # Guard: check candidates exist before accessing content.
        has_candidates = bool(raw.candidates and len(raw.candidates) > 0)
 
        if has_candidates:
            candidate = raw.candidates[0]
 
            # Extract text and function_call parts from the candidate.
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        content_blocks.append(TextBlock(text=part.text))
                    elif hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        content_blocks.append(
                            ToolUseBlock(
                                tool_use_id=getattr(fc, "id", None) or fc.name,
                                tool_name=fc.name,
                                input=dict(fc.args) if fc.args else {}
                            )
                        )
 
            # Parse JSON output if output_format was requested.
            if output_format is not None:
                text_parts = [b.text for b in content_blocks if b.type == "text"]
                if text_parts:
                    try:
                        parsed_output = json.loads("".join(text_parts))
                    except json.JSONDecodeError:
                        parsed_output = None
                        stop_reason = "error"
 
            # Check for tool_use blocks.
            has_tool_use = any(b.type == "tool_use" for b in content_blocks)
            if has_tool_use:
                stop_reason = "tool_use"
 
            # Map candidate finish_reason.
            if candidate.finish_reason:
                fr = candidate.finish_reason
                cand_reason = str(fr.name) if hasattr(fr, "name") else str(fr)
                if cand_reason == "MAX_TOKENS":
                    stop_reason = "max_tokens"
                elif cand_reason in ("SAFETY", "RECITATION"):
                    stop_reason = "error"
                    error_type = "safety_blocked" if cand_reason == "SAFETY" else "recitation_blocked"
                    error_message = f"Google API block: {cand_reason}"
        else:
            # No candidates — likely a safety block or server error.
            logger.warning(
                "chat_provider.google.empty_candidates",
                extra={
                    "operation": "chat_provider.google._from_wire",
                    "status": "failure",
                    "model": self._active_model,
                    "error": "Response has no candidates — possibly safety-blocked or quota-exceeded",
                },
            )
            stop_reason = "error"
            error_type = "api_error"
            error_message = "We're experiencing a temporary issue with the AI service. Please try again."
 
        # --- Usage -----------------------------------------------------------
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
 
        if raw.usage_metadata:
            input_tokens = _safe_int(getattr(raw.usage_metadata, "prompt_token_count", 0))
            output_tokens = _safe_int(getattr(raw.usage_metadata, "candidates_token_count", 0))
            cache_read_tokens = _safe_int(getattr(raw.usage_metadata, "cached_content_token_count", 0))
 
        return ChatResponse(
            content=content_blocks,
            parsed_output=parsed_output,
            stop_reason=stop_reason,  # type: ignore[arg-type]
            error_type=error_type,
            error_message=error_message,
            model_used=self._active_model,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
            ),
            raw=None,
        )

    # ------------------------------------------------------------------
    # Prompt caching helpers
    # ------------------------------------------------------------------

    def _get_or_create_cache(self, sys_text: str) -> str | None:
        """Return an existing cache name or create one for the system text.

        Uses a SHA-256 hash of the system text to detect when the content
        has changed and a new cache is needed.  Falls back gracefully on
        API errors (returns None, caller sends system_instruction inline).

        Args:
            sys_text: The concatenated system prompt text.

        Returns:
            The cache resource name string, or None if caching failed.
        """
        content_hash = hashlib.sha256(sys_text.encode()).hexdigest()

        # Re-use existing cache if content hasn't changed.
        if self._cache_name and self._cache_content_hash == content_hash:
            return self._cache_name

        try:
            cache = self._client.caches.create(
                model=self._active_model,
                config=types.CreateCachedContentConfig(
                    contents=[
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=sys_text)],
                        )
                    ],
                    ttl=_CACHE_TTL,
                ),
            )
            self._cache_name = cache.name
            self._cache_content_hash = content_hash
            logger.info(
                "chat_provider.google.cache_created",
                extra={
                    "operation": "chat_provider.google._get_or_create_cache",
                    "status": "success",
                    "model": self._active_model,
                    "cache_name": cache.name,
                    "content_chars": len(sys_text),
                },
            )
            return cache.name
        except Exception as e:
            # Caching is best-effort; fall back to inline system_instruction.
            logger.warning(
                "chat_provider.google.cache_create_failed",
                extra={
                    "operation": "chat_provider.google._get_or_create_cache",
                    "status": "failure",
                    "model": self._active_model,
                    "error": str(e),
                    "content_chars": len(sys_text),
                },
            )
            self._cache_name = None
            self._cache_content_hash = None
            return None
