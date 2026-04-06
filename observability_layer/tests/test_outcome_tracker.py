"""Tests for OutcomeTracker — lifecycle state machine and OTel metric emitter."""
from pathlib import Path
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
    outcomes = OutcomesConfig(
        lifecycle=lifecycle or [],
        metrics=metrics or [],
    )
    return ObservabilityConfig(outcomes=outcomes)


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
    call_args = counter.add.call_args
    assert call_args[0][0] == 1  # count
    attrs = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("attributes", {})
    assert attrs.get("state") is not None
    assert attrs.get("intent") is not None
    assert attrs.get("session_id") is not None


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


def test_only_state_tagged_counters_increment_on_lifecycle_match():
    """Only counters with 'state' in their attributes should increment on a lifecycle match."""
    state_counter = MagicMock()
    non_state_counter = MagicMock()
    meter = MagicMock()
    call_order = iter([state_counter, non_state_counter])
    meter.create_counter.side_effect = lambda **_: next(call_order)
    meter.create_gauge.return_value = MagicMock()

    config = _make_config(
        lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
        metrics=[
            MetricDefinition(
                name="placement.applications",
                instrument=InstrumentType.counter,
                description="apps",
                attributes=["intent", "state"],
            ),
            MetricDefinition(
                name="drop_off.by_stage",
                instrument=InstrumentType.counter,
                description="drop off",
                attributes=["stage", "intent"],
            ),
        ],
    )
    tracker = OutcomeTracker(config, meter)

    event = _make_event(tool_calls=[{"tool_name": "onest_apply", "tool_use_id": "t1", "input_params": {}}])
    tracker.process(event)

    state_counter.add.assert_called_once()
    non_state_counter.add.assert_not_called()


def test_process_works_with_turn_event_dataclass():
    """OutcomeTracker must work with real TurnEvent dataclasses, not just dicts."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "agent_core" / "src"))
    try:
        from models import TurnEvent, TrustCheckResult, ToolCall

        counter = MagicMock()
        meter = MagicMock()
        meter.create_counter.return_value = counter
        meter.create_gauge.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        config = _make_config(
            lifecycle=[LifecycleState(state="applied", trigger_tool="onest_apply")],
            metrics=[
                MetricDefinition(
                    name="placement.applications",
                    instrument=InstrumentType.counter,
                    description="apps",
                    attributes=["intent", "state"],
                )
            ],
        )
        tracker = OutcomeTracker(config, meter)

        event = TurnEvent(
            session_id="sess-1",
            turn_id="turn-1",
            response_text="ok",
            tool_calls=[ToolCall(tool_name="onest_apply", tool_use_id="t1", input_params={})],
            trust_input_result=TrustCheckResult(passed=True, action="allow"),
            trust_output_result=TrustCheckResult(passed=True, action="allow"),
            model_used="claude-haiku",
            intent="job_apply",
            input_tokens=10,
            output_tokens=5,
            latency_ms=800,
            timestamp_ms=1700000000000,
        )
        tracker.process(event)  # must not raise
        counter.add.assert_called_once()
    except ImportError:
        pytest.skip("agent_core not available in test path")
