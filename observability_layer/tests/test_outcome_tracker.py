"""Tests for OutcomeTracker — lifecycle state machine and OTel metric emitter."""
from unittest.mock import MagicMock
import pytest

from schema.config import (
    ObservabilityConfig,
    InstrumentType,
    LifecycleState,
    MetricDefinition,
    OutcomesConfig,
)
from outcome_tracker import OutcomeTracker


def _make_config(lifecycle=None, metrics=None):
    cfg = ObservabilityConfig()
    if lifecycle:
        cfg.outcomes.lifecycle = lifecycle
    if metrics:
        cfg.outcomes.metrics = metrics
    return cfg


def _make_event(tool_calls=None, intent="market_truth", session_id="s1"):
    return {
        "tool_calls": tool_calls or [],
        "intent": intent,
        "session_id": session_id,
        "trace_id": "abc123",
    }


def test_process_increments_counter_on_matching_tool():
    counter = MagicMock()
    meter = MagicMock()
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()
    meter.create_histogram.return_value = MagicMock()

    config = _make_config(
        lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
        metrics=[MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps")],
    )
    tracker = OutcomeTracker(config, meter)

    event = _make_event(tool_calls=[{"tool_name": "onest_apply", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)

    counter.add.assert_called_once()
    assert counter.add.call_args[0][0] == 1


def test_process_no_increment_on_non_matching_tool():
    counter = MagicMock()
    meter = MagicMock()
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()

    config = _make_config(
        lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
        metrics=[MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps")],
    )
    tracker = OutcomeTracker(config, meter)

    event = _make_event(tool_calls=[{"tool_name": "other_tool", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)

    counter.add.assert_not_called()


def test_process_with_none_event_does_not_raise():
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    config = _make_config()
    tracker = OutcomeTracker(config, meter)
    tracker.process(None)


def test_process_with_empty_tool_calls_does_not_raise():
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    config = _make_config()
    tracker = OutcomeTracker(config, meter)
    tracker.process(_make_event(tool_calls=[]))


def test_process_exception_does_not_propagate():
    meter = MagicMock()
    counter = MagicMock()
    counter.add.side_effect = RuntimeError("otel failure")
    meter.create_counter.return_value = counter
    meter.create_gauge.return_value = MagicMock()

    config = _make_config(
        lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
        metrics=[MetricDefinition(name="placement.applications", instrument=InstrumentType.counter, description="apps")],
    )
    tracker = OutcomeTracker(config, meter)
    event = _make_event(tool_calls=[{"tool_name": "onest_apply", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)  # must not raise


def test_no_metrics_config_process_is_noop():
    meter = MagicMock()
    config = _make_config(lifecycle=[], metrics=[])
    tracker = OutcomeTracker(config, meter)
    tracker.process(_make_event(tool_calls=[{"tool_name": "any_tool", "tool_use_id": "t1", "input_params": {}}]))
    meter.create_counter.assert_not_called()


def test_init_with_none_config_raises():
    meter = MagicMock()
    with pytest.raises(ValueError, match="config"):
        OutcomeTracker(None, meter)


def test_init_with_none_meter_raises():
    config = _make_config()
    with pytest.raises(ValueError, match="meter"):
        OutcomeTracker(config, None)
