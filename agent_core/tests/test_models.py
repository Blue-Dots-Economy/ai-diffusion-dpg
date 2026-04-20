"""
agent_core/tests/test_models.py

Unit tests for dataclasses in src.models.

Coverage:
- TurnEvent: trace_id field exists with default None
- TurnEvent: trace_id field can be set and retrieved
"""

from __future__ import annotations

import pytest

from src.models import NLUResult, TurnEvent, TrustCheckResult, UserStateClassification


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
