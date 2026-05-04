"""Provider-agnostic OTel instruments shared by every ChatProviderBase implementation.

Lifted from agent_core/src/llm_wrapper/claude_wrapper.py (GH-151).
Instrument names are unchanged so existing Grafana dashboards keep
working.
"""

from __future__ import annotations

from typing import Optional

from opentelemetry import metrics as otel_metrics

from src.chat_provider.types import ChatResponse


_METRICS_INITIALIZED = False
_LLM_LATENCY_HIST = None
_LLM_INPUT_TOKENS_HIST = None
_LLM_OUTPUT_TOKENS_HIST = None
_LLM_CACHE_READ_HIST = None
_LLM_CALL_COUNTER = None
_LLM_CACHE_HIT_COUNTER = None


def get_metrics() -> dict:
    """Initialise (once) and return the chat-provider metrics instruments.

    Re-resolves on first use rather than at import time so that
    dpg_telemetry has an opportunity to install a real MeterProvider
    during app startup. Missing entries are None if metrics are
    disabled, which every caller must be able to handle.
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
        description="Tokens served from the prompt cache on this call (provider-specific).",
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


def record_call_metrics(
    *,
    model: str,
    call_kind: str,
    status: str,
    latency_ms: int,
    response: Optional[ChatResponse] = None,
    provider_system: str | None = None,
) -> None:
    """Emit chat-provider call metrics via OTel.

    Safe on both success and failure paths. When a MeterProvider is not
    installed, instrument creation succeeds against the no-op default
    provider and writes are silently discarded.

    Args:
        model: model id used for the call.
        call_kind: "sync" or "stream".
        status: "success" | "failure" | "retry".
        latency_ms: wall-clock duration of the attempt.
        response: parsed ChatResponse on success; None on failure.
        provider_system: "anthropic" | "openai" — added in PR1 so
            dashboards can split per provider. Optional for backward
            compatibility with the adapter shim.
    """
    try:
        m = get_metrics()
        attrs: dict[str, str] = {
            "model": model,
            "call_kind": call_kind,
            "status": status,
        }
        if provider_system:
            attrs["gen_ai.system"] = provider_system

        if m["latency_ms"] is not None:
            m["latency_ms"].record(latency_ms, attrs)
        if m["calls"] is not None:
            m["calls"].add(1, attrs)

        if response is not None:
            usage = response.usage
            if usage.input_tokens is not None and m["input_tokens"] is not None:
                m["input_tokens"].record(usage.input_tokens, attrs)
            if usage.output_tokens is not None and m["output_tokens"] is not None:
                m["output_tokens"].record(usage.output_tokens, attrs)
            if usage.cache_read_tokens is not None and m["cache_read_tokens"] is not None:
                m["cache_read_tokens"].record(usage.cache_read_tokens, attrs)
            if m["cache_hits"] is not None:
                read = usage.cache_read_tokens or 0
                created = usage.cache_creation_tokens or 0
                if read > 0:
                    m["cache_hits"].add(1, {**attrs, "event": "hit"})
                elif created > 0:
                    m["cache_hits"].add(1, {**attrs, "event": "create"})
                else:
                    m["cache_hits"].add(1, {**attrs, "event": "miss"})
    except Exception:  # noqa: BLE001
        # Metrics must never fail an LLM call; swallow instrumentation errors.
        pass
