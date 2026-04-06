"""Tests for OtelObservabilityLayer."""
from unittest.mock import MagicMock, patch
import pytest


def _make_event(tool_calls=None):
    return {
        "session_id": "s1",
        "turn_id": "t1",
        "response_text": "hello",
        "tool_calls": tool_calls or [],
        "trust_input_result": {"passed": True, "action": "allow", "reason": None},
        "trust_output_result": {"passed": True, "action": "allow", "reason": None},
        "model_used": "claude-haiku",
        "intent": "market_truth",
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 800,
        "timestamp_ms": 1700000000000,
        "trace_id": "abc123",
    }


def test_emit_turn_with_valid_event_does_not_raise():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({"observability": {}})
        layer.emit_turn(_make_event())


def test_emit_turn_none_is_silently_ignored():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_turn(None)  # must not raise


def test_emit_signal_with_valid_type_does_not_raise():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_signal("drop_off", {"stage": "profile_building"})


def test_emit_signal_none_type_is_silently_ignored():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        layer = OtelObservabilityLayer({})
        layer.emit_signal(None, {})  # must not raise


def test_init_with_none_config_raises_value_error():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        with pytest.raises(ValueError, match="config"):
            OtelObservabilityLayer(None)


def test_inherits_from_base():
    from base import ObservabilityLayerBase
    from otel_observability_layer import OtelObservabilityLayer
    assert issubclass(OtelObservabilityLayer, ObservabilityLayerBase)
