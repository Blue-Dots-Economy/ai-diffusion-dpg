"""
agent_core/tests/test_models.py

Unit tests for dataclasses in src.models.

Coverage:
- TurnEvent: trace_id field exists with default None
- TurnEvent: trace_id field can be set and retrieved
"""

from __future__ import annotations

import json

import pytest

from src.models import DoneEvent, NLUResult, SentenceEvent, SignalEvent, TurnEvent, TrustCheckResult, UserStateClassification


def test_turn_event_has_trace_id_field():
    """Test that TurnEvent accepts trace_id field and stores it correctly."""
    event = TurnEvent(
        session_id="s1",
        turn_id="t1",
        trace_id="abc123def456",
        response_text="hello",
        tool_calls=[],
        trust_input_result=TrustCheckResult(passed=True, action="allow"),
        trust_output_result=TrustCheckResult(passed=True, action="allow"),
        model_used="claude-haiku",
        intent="market_truth",
        input_tokens=10,
        output_tokens=5,
        latency_ms=800,
        timestamp_ms=1700000000000,
    )
    assert event.trace_id == "abc123def456"


def test_turn_event_trace_id_defaults_to_none():
    """Test that TurnEvent.trace_id defaults to None when not provided."""
    event = TurnEvent(
        session_id="s1",
        turn_id="t1",
        response_text="hello",
        tool_calls=[],
        trust_input_result=TrustCheckResult(passed=True, action="allow"),
        trust_output_result=TrustCheckResult(passed=True, action="allow"),
        model_used="claude-haiku",
        intent="market_truth",
        input_tokens=10,
        output_tokens=5,
        latency_ms=800,
        timestamp_ms=1700000000000,
    )
    assert event.trace_id is None


def test_user_state_classification_defaults():
    """Test that UserStateClassification accepts id and confidence fields."""
    usc = UserStateClassification(id="fog", confidence=0.82)
    assert usc.id == "fog"
    assert usc.confidence == 0.82


def test_nlu_result_user_state_default_is_none():
    """Test that NLUResult.user_state defaults to None when not provided."""
    result = NLUResult(intent="greeting", entities={}, sentiment="neutral", confidence=0.9)
    assert result.user_state is None


def test_nlu_result_accepts_user_state():
    """Test that NLUResult accepts and stores a user_state field."""
    usc = UserStateClassification(id="orientation", confidence=0.7)
    result = NLUResult(
        intent="greeting", entities={}, sentiment="neutral",
        confidence=0.9, user_state=usc,
    )
    assert result.user_state is usc
    assert result.user_state.id == "orientation"


def test_turn_result_session_ended_default_false():
    """Test that TurnResult.session_ended defaults to False when not provided."""
    from src.models import TurnResult
    result = TurnResult(session_id="s1", turn_id="t1", response_text="hi")
    assert result.session_ended is False


def test_turn_result_session_ended_accepts_true():
    """Test that TurnResult accepts and stores session_ended=True."""
    from src.models import TurnResult
    result = TurnResult(session_id="s1", turn_id="t1", response_text="bye", session_ended=True)
    assert result.session_ended is True


def test_done_event_session_ended_default_false():
    """Test that DoneEvent.session_ended defaults to False when not provided."""
    from src.models import DoneEvent
    evt = DoneEvent()
    assert evt.session_ended is False


def test_done_event_session_ended_accepts_true():
    """Test that DoneEvent accepts and stores session_ended=True."""
    from src.models import DoneEvent
    evt = DoneEvent(session_ended=True)
    assert evt.session_ended is True


# Tests for StreamEvent classes after adding turn_id field (#224)
def test_signal_event_has_turn_id_default_empty():
    ev = SignalEvent(stage="memory_read", status="start")
    assert ev.turn_id == ""


def test_sentence_event_carries_turn_id():
    ev = SentenceEvent(text="hello", sentence_index=0, turn_id="abc-123")
    assert ev.turn_id == "abc-123"
    assert "abc-123" in ev.to_sse()


def test_done_event_serialises_turn_id_in_sse():
    ev = DoneEvent(turn_status="completed", turn_id="t-1")
    payload = ev.to_sse()
    assert json.loads(payload.removeprefix("data: ").rstrip())["turn_id"] == "t-1"


def test_signal_event_to_sse_includes_turn_id_field():
    ev = SignalEvent(stage="trust_input", status="complete", turn_id="t-2")
    parsed = json.loads(ev.to_sse().removeprefix("data: ").rstrip())
    assert parsed["turn_id"] == "t-2"
