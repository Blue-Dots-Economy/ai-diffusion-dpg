"""Smoke tests for chat_provider.metrics — instruments are lazy and idempotent."""

from src.chat_provider.metrics import get_metrics, record_call_metrics
from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage


def test_get_metrics_returns_named_instruments():
    m = get_metrics()
    expected_keys = {
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "calls",
        "cache_hits",
    }
    assert expected_keys.issubset(m.keys())


def test_get_metrics_idempotent():
    a = get_metrics()
    b = get_metrics()
    assert a is b or all(a[k] is b[k] for k in a)


def test_record_call_metrics_does_not_raise_on_minimal_response():
    response = ChatResponse(
        content=[TextBlock(text="hi")],
        stop_reason="end_turn",
        model_used="claude-test",
        usage=TokenUsage(input_tokens=1, output_tokens=1, cache_read_tokens=0,
                         cache_creation_tokens=0),
    )
    record_call_metrics(
        model="claude-test",
        call_kind="sync",
        status="success",
        latency_ms=42,
        response=response,
    )


def test_record_call_metrics_handles_none_token_fields():
    response = ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-test",
        usage=TokenUsage(),
    )
    record_call_metrics(
        model="claude-test", call_kind="sync", status="failure",
        latency_ms=10, response=response,
    )
