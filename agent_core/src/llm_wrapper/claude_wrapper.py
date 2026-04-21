"""
agent_core/llm_wrapper/claude_wrapper.py

Concrete LLM wrapper for the Anthropic Claude API.
THIS IS THE ONLY FILE IN THE ENTIRE CODEBASE THAT IMPORTS OR CALLS anthropic SDK.

Responsibilities:
- Executes LLM calls with an explicit timeout on every request.
- Retries transient failures (rate limits, timeouts) with exponential backoff.
- Switches to the fallback model after primary model exhaustion.
- Emits a structured log entry for every call attempt.
- Never raises — all failures are returned as LLMResponse(stop_reason="error").
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Optional

import anthropic
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace

from src.exceptions import LLMCallError, LLMFallbackError, ToolUseRequested
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OTel metrics — created lazily once the provider is installed (tests override).
# GH-151: exposes per-call latency, token counts, and cache hit rate so Grafana
# dashboards can track the prompt-caching win without log aggregation.
# ---------------------------------------------------------------------------
_METRICS_INITIALIZED = False
_LLM_LATENCY_HIST = None
_LLM_INPUT_TOKENS_HIST = None
_LLM_OUTPUT_TOKENS_HIST = None
_LLM_CACHE_READ_HIST = None
_LLM_CALL_COUNTER = None
_LLM_CACHE_HIT_COUNTER = None


def _get_metrics() -> dict:
    """Initialise (once) and return the LLM-wrapper metrics instruments.

    Re-resolves on first use rather than at import time so that dpg_telemetry
    has an opportunity to install a real MeterProvider during app startup.
    Returns a dict of named instruments; missing entries are None if metrics
    are disabled, which every caller must be able to handle.
    """
    global _METRICS_INITIALIZED, _LLM_LATENCY_HIST, _LLM_INPUT_TOKENS_HIST
    global _LLM_OUTPUT_TOKENS_HIST, _LLM_CACHE_READ_HIST, _LLM_CALL_COUNTER
    global _LLM_CACHE_HIT_COUNTER
    if _METRICS_INITIALIZED:
        return {
            "latency_ms": _LLM_LATENCY_HIST,
            "input_tokens": _LLM_INPUT_TOKENS_HIST,
            "output_tokens": _LLM_OUTPUT_TOKENS_HIST,
            "cache_read_tokens": _LLM_CACHE_READ_HIST,
            "calls": _LLM_CALL_COUNTER,
            "cache_hits": _LLM_CACHE_HIT_COUNTER,
        }
    meter = otel_metrics.get_meter(__name__)
    _LLM_LATENCY_HIST = meter.create_histogram(
        "agent_core.llm.call.duration_ms",
        unit="ms",
        description="Wall-clock latency of a single LLM call, tagged by call_kind (sync|stream) and model.",
    )
    _LLM_INPUT_TOKENS_HIST = meter.create_histogram(
        "agent_core.llm.call.input_tokens",
        unit="tokens",
        description="Input token count per LLM call.",
    )
    _LLM_OUTPUT_TOKENS_HIST = meter.create_histogram(
        "agent_core.llm.call.output_tokens",
        unit="tokens",
        description="Output token count per LLM call.",
    )
    _LLM_CACHE_READ_HIST = meter.create_histogram(
        "agent_core.llm.call.cache_read_tokens",
        unit="tokens",
        description="Tokens served from the Anthropic prompt cache on this call.",
    )
    _LLM_CALL_COUNTER = meter.create_counter(
        "agent_core.llm.calls_total",
        description="Total LLM calls, tagged by model, call_kind, and status.",
    )
    _LLM_CACHE_HIT_COUNTER = meter.create_counter(
        "agent_core.llm.cache_events_total",
        description="Prompt-cache events — tag event=hit|create|miss so the hit ratio can be derived.",
    )
    _METRICS_INITIALIZED = True
    return {
        "latency_ms": _LLM_LATENCY_HIST,
        "input_tokens": _LLM_INPUT_TOKENS_HIST,
        "output_tokens": _LLM_OUTPUT_TOKENS_HIST,
        "cache_read_tokens": _LLM_CACHE_READ_HIST,
        "calls": _LLM_CALL_COUNTER,
        "cache_hits": _LLM_CACHE_HIT_COUNTER,
    }


# Minimum size below which we skip `cache_control` — Anthropic silently
# ignores cache markers on prompts shorter than ~1024 tokens anyway, and
# estimating tokens from char count avoids a tokeniser dependency here.
# ~4 chars/token is a conservative English estimate; KKB Hindi/Devanagari is
# denser so the real threshold is easily met by NLU and subagent prompts.
_CACHE_MIN_CHARS = 3000


def _safe_int(value) -> int:
    """Return value as int, defaulting to 0 on non-numeric (e.g. None, Mock).

    Rejects arbitrary objects that happen to define ``__int__`` (notably
    MagicMock) so tests that don't bother to set cache-usage fields can't
    accidentally propagate sentinel values into metrics.
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


class _RetryableExhausted(Exception):
    """Internal sentinel: all retry attempts on transient errors were consumed.
    Raised only from _call_with_retry; caught only in call() to trigger fallback.
    Never surfaces outside ClaudeLLMWrapper.
    """


class ClaudeLLMWrapper(LLMWrapperBase):
    """
    Anthropic Claude implementation of LLMWrapperBase.

    Reads all runtime values from the injected config dict — nothing hardcoded.
    Expected config keys:
        primary_model   (str)   Claude model ID for primary calls
        fallback_model  (str)   Claude model ID used after primary exhaustion
        timeout_ms      (int)   Per-request timeout in milliseconds
        retry_attempts  (int)   Max attempts before switching to fallback (min 1)
    """

    def __init__(self, config: dict) -> None:
        if not config:
            raise ValueError("ClaudeLLMWrapper requires a non-empty config dict")

        primary_model = config.get("primary_model", "")
        fallback_model = config.get("fallback_model", "")
        if not primary_model:
            raise ValueError(
                "agent.primary_model is not set. Ensure your domain config has a valid "
                "Claude model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )
        if not fallback_model:
            raise ValueError(
                "agent.fallback_model is not set. Ensure your domain config has a valid "
                "Claude model ID, or set CONFIG_FOLDER in .env.local to point to your "
                "domain configs folder."
            )

        self._primary_model: str = primary_model
        self._fallback_model: str = fallback_model
        self._timeout_s: float = config["timeout_ms"] / 1000
        self._max_attempts: int = max(1, config["retry_attempts"])
        self._backoff_seconds: list[float] = config.get("retry_backoff_seconds", [0, 0.5, 1.0])

        self._active_model: str = self._primary_model
        self._client = anthropic.Anthropic()
        self._async_client = anthropic.AsyncAnthropic()

    # ------------------------------------------------------------------
    # Prompt caching (GH-151 #1)
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_system_for_caching(system: Optional[str]):
        """Wrap a system prompt string as an Anthropic cache-control block.

        Anthropic prompt caching requires the system prompt to be sent as a
        list of content blocks with ``cache_control: {"type": "ephemeral"}``
        on the portion to cache. When the caller has already built such a
        list (future extension), it is returned unchanged. Strings shorter
        than ``_CACHE_MIN_CHARS`` are returned as-is because Anthropic ignores
        cache markers below its internal minimum — avoiding the list form
        keeps the request slightly smaller.

        Returns the original value when caching is not applicable, so this is
        safe to call on every request.
        """
        if not system:
            return system
        if not isinstance(system, str):
            return system  # already structured; trust the caller
        if len(system) < _CACHE_MIN_CHARS:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        model_override: Optional[str] = None,
        output_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Execute an LLM call with automatic retries and fallback model switching.

        Args:
            messages: List of message dicts with role and content.
            tools: List of tool definitions the LLM can call.
            system: System prompt text.
            model_override: Optional model ID to override the active model.
            output_format: Optional structured output format dict for the response.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            ValueError: If messages is empty.
        """
        if not messages:
            raise ValueError("messages must not be empty")

        model = model_override or self._active_model

        try:
            return self._call_with_retry(model, messages, tools, system, output_format)
        except _RetryableExhausted:
            if model != self._primary_model:
                # Already on fallback — nothing left to try
                return LLMResponse(content=None, stop_reason="error")
            logger.warning(
                "llm_wrapper.fallback_triggered",
                extra={"operation": "llm_wrapper.call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                return self._call_with_retry(self._fallback_model, messages, tools, system, output_format)
            except _RetryableExhausted:
                return LLMResponse(content=None, stop_reason="error")

    def get_active_model(self) -> str:
        return self._active_model

    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        model_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream text tokens from the Anthropic API.

        Same retry + fallback logic as call(). Yields raw text tokens.
        Raises ToolUseRequested if the LLM returns a tool_use stop reason.

        Args:
            messages: Conversation messages in Anthropic format.
            tools: Tool definitions. None or empty for no tools.
            system: System prompt string.
            model_override: Optional model ID override.

        Yields:
            str: Individual text tokens.

        Raises:
            ToolUseRequested: If the LLM requests tool use.
        """
        if not messages:
            raise ValueError("messages must not be empty")

        model = model_override or self._active_model

        try:
            async for token in self._stream_with_retry(model, messages, tools, system):
                yield token
        except _RetryableExhausted:
            if model != self._primary_model:
                return
            logger.warning(
                "llm_wrapper.stream_fallback_triggered",
                extra={"operation": "llm_wrapper.stream_call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                async for token in self._stream_with_retry(self._fallback_model, messages, tools, system):
                    yield token
            except _RetryableExhausted:
                return

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _stream_with_retry(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
    ) -> AsyncGenerator[str, None]:
        """Internal retry loop for streaming with exponential backoff.

        Args:
            model: Model ID to use.
            messages: Conversation messages.
            tools: Tool definitions.
            system: System prompt.

        Yields:
            str: Text tokens from the stream.

        Raises:
            _RetryableExhausted: If all retry attempts are exhausted.
            ToolUseRequested: If the LLM requests tool use.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
            if delay > 0:
                await asyncio.sleep(delay)

            start = time.time()
            _tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": 4096,
                    "messages": messages,
                }
                if system:
                    kwargs["system"] = self._wrap_system_for_caching(system)
                if tools:
                    kwargs["tools"] = tools

                tool_calls: list[ToolCall] = []
                stop_reason: str | None = None
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0
                cache_creation_tokens = 0

                # GH-151 #4: wrap the streaming call in an llm.call span so
                # trace analysis sees the same parent span as the sync path.
                # Previously the stream request showed up only as an
                # orphaned raw POST to api.anthropic.com.
                with _tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.model", model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "stream")

                    async with self._async_client.messages.stream(
                        **kwargs, timeout=self._timeout_s
                    ) as stream:
                        async for event in stream:
                            if hasattr(event, "type"):
                                if event.type == "content_block_delta":
                                    if hasattr(event.delta, "text"):
                                        yield event.delta.text

                        # After the stream closes, get the final message for metadata
                        final_message = await stream.get_final_message()
                        stop_reason = final_message.stop_reason
                        input_tokens = final_message.usage.input_tokens
                        output_tokens = final_message.usage.output_tokens
                        cache_read_tokens = _safe_int(
                            getattr(final_message.usage, "cache_read_input_tokens", 0)
                        )
                        cache_creation_tokens = _safe_int(
                            getattr(final_message.usage, "cache_creation_input_tokens", 0)
                        )

                        # Collect tool_use blocks from the final message
                        for block in final_message.content:
                            if block.type == "tool_use":
                                tool_calls.append(
                                    ToolCall(
                                        tool_name=block.name,
                                        tool_use_id=block.id,
                                        input_params=block.input,
                                    )
                                )

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    span.set_attribute("gen_ai.usage.cache_read_input_tokens", cache_read_tokens)
                    span.set_attribute(
                        "gen_ai.usage.cache_creation_input_tokens", cache_creation_tokens
                    )

                latency_ms = int((time.time() - start) * 1000)
                self._record_call_metrics(
                    model=model,
                    call_kind="stream",
                    status="success",
                    latency_ms=latency_ms,
                    response=LLMResponse(
                        content=None,
                        stop_reason=stop_reason or "end_turn",
                        model_used=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_input_tokens=cache_read_tokens,
                        cache_creation_input_tokens=cache_creation_tokens,
                    ),
                )
                logger.info(
                    "llm_wrapper.stream_call",
                    extra={
                        "operation": "llm_wrapper.stream_call",
                        "status": "success",
                        "model": model,
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
                    raise ToolUseRequested(tool_calls)

                return  # Stream complete

            except ToolUseRequested:
                raise  # Propagate immediately — not retryable

            except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "llm_wrapper.stream_retryable_error",
                    extra={
                        "operation": "llm_wrapper.stream_call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

            except anthropic.APIError as e:
                logger.error(
                    "llm_wrapper.stream_api_error",
                    extra={
                        "operation": "llm_wrapper.stream_call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return  # Non-retryable API error

            except Exception as e:
                logger.error(
                    "llm_wrapper.stream_unexpected_error",
                    extra={
                        "operation": "llm_wrapper.stream_call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return  # Non-retryable

        logger.error(
            "llm_wrapper.stream_exhausted",
            extra={
                "operation": "llm_wrapper.stream_call",
                "status": "failure",
                "model": model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(f"All {self._max_attempts} stream retry attempts exhausted for model {model}")

    def _call_with_retry(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        system: str,
        output_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Internal retry loop for a single LLM call with exponential backoff.

        Args:
            model: Model ID to use for this call.
            messages: List of message dicts with role and content.
            tools: List of tool definitions the LLM can call.
            system: System prompt text.
            output_format: Optional structured output format dict for the response.

        Returns:
            LLMResponse with parsed content, tool calls, and metadata.

        Raises:
            _RetryableExhausted: If all retry attempts are exhausted on transient errors.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self._max_attempts):
            delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
            if delay > 0:
                time.sleep(delay)

            start = time.time()
            _tracer = otel_trace.get_tracer(__name__)
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": 4096,
                    "system": self._wrap_system_for_caching(system),
                    "messages": messages,
                    "timeout": self._timeout_s,
                }
                if tools:
                    kwargs["tools"] = tools
                if output_format:
                    kwargs["response_format"] = output_format

                with _tracer.start_as_current_span("llm.call") as span:
                    span.set_attribute("gen_ai.model", model)
                    span.set_attribute("llm.attempt", attempt + 1)
                    span.set_attribute("llm.call_kind", "sync")
                    raw = self._client.messages.create(**kwargs)
                    response = self._parse_response(raw, model)
                    latency_ms = int((time.time() - start) * 1000)
                    span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)
                    span.set_attribute(
                        "gen_ai.usage.cache_read_input_tokens",
                        response.cache_read_input_tokens,
                    )
                    span.set_attribute(
                        "gen_ai.usage.cache_creation_input_tokens",
                        response.cache_creation_input_tokens,
                    )

                self._record_call_metrics(
                    model=model,
                    call_kind="sync",
                    status="success",
                    latency_ms=latency_ms,
                    response=response,
                )
                logger.info(
                    "llm_wrapper.call",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "success",
                        "model": model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cache_read_input_tokens": response.cache_read_input_tokens,
                        "cache_creation_input_tokens": response.cache_creation_input_tokens,
                    },
                )
                return response

            except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_error = e
                logger.warning(
                    "llm_wrapper.retryable_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

            except anthropic.APIError as e:
                logger.error(
                    "llm_wrapper.api_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return LLMResponse(content=None, stop_reason="error")

            except Exception as e:
                # Catches SDK errors raised before the HTTP request fires —
                # e.g. TypeError from missing API key during header validation.
                # These are non-retryable configuration errors.
                logger.error(
                    "llm_wrapper.unexpected_error",
                    extra={
                        "operation": "llm_wrapper.call",
                        "status": "failure",
                        "model": model,
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return LLMResponse(content=None, stop_reason="error")

        logger.error(
            "llm_wrapper.exhausted",
            extra={
                "operation": "llm_wrapper.call",
                "status": "failure",
                "model": model,
                "attempts": self._max_attempts,
                "error": str(last_error),
            },
        )
        raise _RetryableExhausted(f"All {self._max_attempts} retry attempts exhausted for model {model}")

    def _switch_to_fallback(self) -> None:
        self._active_model = self._fallback_model

    @staticmethod
    def _record_call_metrics(
        *,
        model: str,
        call_kind: str,
        status: str,
        latency_ms: int,
        response: Optional[LLMResponse] = None,
    ) -> None:
        """Emit LLM-call metrics via OTel (GH-151 observability).

        Safe to call on both success and failure paths. When a MeterProvider
        is not installed (unit tests, local dev without dpg_telemetry),
        instrument creation still succeeds against the no-op default provider
        and the writes are silently discarded — keeping this call fail-safe.

        Args:
            model: Model ID used for the call.
            call_kind: "sync" or "stream".
            status: "success" | "failure" | "retry".
            latency_ms: Wall-clock duration of the attempt.
            response: Parsed response (optional; None on failure).
        """
        try:
            m = _get_metrics()
            attrs = {"model": model, "call_kind": call_kind, "status": status}
            if m["latency_ms"] is not None:
                m["latency_ms"].record(latency_ms, attrs)
            if m["calls"] is not None:
                m["calls"].add(1, attrs)
            if response is not None:
                if m["input_tokens"] is not None:
                    m["input_tokens"].record(response.input_tokens, attrs)
                if m["output_tokens"] is not None:
                    m["output_tokens"].record(response.output_tokens, attrs)
                if m["cache_read_tokens"] is not None:
                    m["cache_read_tokens"].record(response.cache_read_input_tokens, attrs)
                if m["cache_hits"] is not None:
                    if response.cache_read_input_tokens > 0:
                        m["cache_hits"].add(1, {**attrs, "event": "hit"})
                    elif response.cache_creation_input_tokens > 0:
                        m["cache_hits"].add(1, {**attrs, "event": "create"})
                    else:
                        m["cache_hits"].add(1, {**attrs, "event": "miss"})
        except Exception:  # noqa: BLE001
            # Metrics must never fail an LLM call; swallow instrumentation errors.
            pass

    def _parse_response(self, raw: anthropic.types.Message, model: str) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        text_content: Optional[str] = None

        for block in raw.content:
            if block.type == "text":
                text_content = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        tool_name=block.name,
                        tool_use_id=block.id,
                        input_params=block.input,
                    )
                )

        usage = raw.usage
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            stop_reason=raw.stop_reason or "end_turn",
            model_used=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=_safe_int(
                getattr(usage, "cache_read_input_tokens", 0)
            ),
            cache_creation_input_tokens=_safe_int(
                getattr(usage, "cache_creation_input_tokens", 0)
            ),
        )
