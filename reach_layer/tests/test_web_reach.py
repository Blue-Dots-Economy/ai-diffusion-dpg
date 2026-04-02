"""
reach_layer/tests/test_web_reach.py

Unit tests for WebReachLayer (src/web_reach.py).

Covers:
- Normal execution: build_turn_input and format_result for valid inputs
- Edge cases:       optional user_id absent, whitespace-only inputs
- Failure scenarios: empty session_id, empty message, None config raises ValueError
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.web_reach import WebReachLayer
from src.base import TurnInput, TurnResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "reach_layer": {
            "web": {"title": "Test Chat"},
        }
    }


@pytest.fixture
def web_reach(config):
    return WebReachLayer(config)


# ---------------------------------------------------------------------------
# __init__ validation
# ---------------------------------------------------------------------------

def test_init_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        WebReachLayer(None)


def test_init_empty_config_uses_defaults():
    wr = WebReachLayer({})
    assert wr._title == "DPG Chat"


def test_init_reads_title_from_config(config):
    wr = WebReachLayer(config)
    assert wr._title == "Test Chat"


# ---------------------------------------------------------------------------
# build_turn_input — normal execution
# ---------------------------------------------------------------------------

def test_build_turn_input_returns_turn_input(web_reach):
    turn = web_reach.build_turn_input("sess-1", "user-1", "hello")
    assert isinstance(turn, TurnInput)


def test_build_turn_input_session_id_set(web_reach):
    turn = web_reach.build_turn_input("sess-1", None, "hello")
    assert turn.session_id == "sess-1"


def test_build_turn_input_message_stripped(web_reach):
    turn = web_reach.build_turn_input("sess-1", None, "  hello  ")
    assert turn.user_message == "hello"


def test_build_turn_input_channel_is_web(web_reach):
    turn = web_reach.build_turn_input("sess-1", "user-1", "hi")
    assert turn.channel == "web"


def test_build_turn_input_user_id_set(web_reach):
    turn = web_reach.build_turn_input("sess-1", "user-1", "hi")
    assert turn.user_id == "user-1"


def test_build_turn_input_user_id_stripped(web_reach):
    turn = web_reach.build_turn_input("sess-1", "  user-1  ", "hi")
    assert turn.user_id == "user-1"


def test_build_turn_input_no_user_id_is_none(web_reach):
    turn = web_reach.build_turn_input("sess-1", None, "hi")
    assert turn.user_id is None


def test_build_turn_input_timestamp_positive(web_reach):
    turn = web_reach.build_turn_input("sess-1", None, "hi")
    assert turn.timestamp_ms > 0


# ---------------------------------------------------------------------------
# build_turn_input — edge cases
# ---------------------------------------------------------------------------

def test_build_turn_input_session_id_whitespace_stripped(web_reach):
    turn = web_reach.build_turn_input("  sess-1  ", None, "hi")
    assert turn.session_id == "sess-1"


# ---------------------------------------------------------------------------
# build_turn_input — failure scenarios
# ---------------------------------------------------------------------------

def test_build_turn_input_empty_session_id_raises(web_reach):
    with pytest.raises(ValueError, match="session_id"):
        web_reach.build_turn_input("", None, "hello")


def test_build_turn_input_whitespace_session_id_raises(web_reach):
    with pytest.raises(ValueError, match="session_id"):
        web_reach.build_turn_input("   ", None, "hello")


def test_build_turn_input_empty_message_raises(web_reach):
    with pytest.raises(ValueError, match="message"):
        web_reach.build_turn_input("sess-1", None, "")


def test_build_turn_input_whitespace_message_raises(web_reach):
    with pytest.raises(ValueError, match="message"):
        web_reach.build_turn_input("sess-1", None, "   ")


# ---------------------------------------------------------------------------
# format_result — normal execution
# ---------------------------------------------------------------------------

def test_format_result_returns_dict(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hello!")
    out = web_reach.format_result(result)
    assert isinstance(out, dict)


def test_format_result_response_text(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hello!")
    assert web_reach.format_result(result)["response_text"] == "Hello!"


def test_format_result_session_id(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hi")
    assert web_reach.format_result(result)["session_id"] == "sess-1"


def test_format_result_was_escalated_false_by_default(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hi")
    assert web_reach.format_result(result)["was_escalated"] is False


def test_format_result_escalated_propagates(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Escalated", was_escalated=True)
    assert web_reach.format_result(result)["was_escalated"] is True


def test_format_result_latency_ms(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hi", latency_ms=250)
    assert web_reach.format_result(result)["latency_ms"] == 250


# ---------------------------------------------------------------------------
# format_result — failure scenarios
# ---------------------------------------------------------------------------

def test_format_result_none_returns_safe_fallback(web_reach):
    out = web_reach.format_result(None)
    assert out["response_text"] == ""
    assert out["was_escalated"] is False
    assert out["session_id"] == ""
    assert out["latency_ms"] == 0


# ---------------------------------------------------------------------------
# receive / deliver — ABC contract satisfied
# ---------------------------------------------------------------------------

def test_receive_returns_turn_input(web_reach):
    turn = web_reach.receive()
    assert isinstance(turn, TurnInput)
    assert turn.channel == "web"


def test_deliver_is_no_op(web_reach):
    result = TurnResult(session_id="sess-1", response_text="Hi")
    web_reach.deliver(result)  # should not raise
