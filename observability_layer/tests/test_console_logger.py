"""
observability_layer/tests/test_console_logger.py

Tests for ConsoleLogger.

Verifies that emit_turn() and emit_signal() log correctly and never raise
regardless of input quality.
"""

from __future__ import annotations

import logging
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Any, Optional

from src.console_logger import ConsoleLogger


# ---------------------------------------------------------------------------
# Minimal TurnEvent-like dataclass for testing
# ---------------------------------------------------------------------------

@dataclass
class _FakeTrustResult:
    passed: bool
    action: str
    reason: Optional[str] = None


@dataclass
class _FakeTurnEvent:
    session_id: str
    response_text: str
    tool_calls: list = field(default_factory=list)
    trust_input_result: Any = None
    trust_output_result: Any = None
    model_used: str = "claude-test"
    input_tokens: int = 100
    output_tokens: int = 50
    latency_ms: int = 200
    timestamp_ms: int = 1700000000000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict = {"observability_layer": {"log_level": "INFO"}}


def _make_logger() -> ConsoleLogger:
    return ConsoleLogger(_BASE_CONFIG)


def _make_event(**kwargs) -> _FakeTurnEvent:
    defaults = {
        "session_id": "session-1",
        "response_text": "Here is your answer",
        "trust_input_result": _FakeTrustResult(passed=True, action="allow"),
        "trust_output_result": _FakeTrustResult(passed=True, action="allow"),
    }
    defaults.update(kwargs)
    return _FakeTurnEvent(**defaults)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_raises_if_config_is_none(self) -> None:
        with pytest.raises(ValueError, match="config must not be None"):
            ConsoleLogger(None)

    def test_uses_default_log_level_if_not_configured(self) -> None:
        ll = ConsoleLogger({})
        assert ll._log_level == "INFO"

    def test_reads_log_level_from_config(self) -> None:
        ll = ConsoleLogger({"observability_layer": {"log_level": "DEBUG"}})
        assert ll._log_level == "DEBUG"


# ---------------------------------------------------------------------------
# emit_turn — normal cases
# ---------------------------------------------------------------------------

class TestEmitTurnNormal:
    def test_emit_turn_does_not_raise_for_valid_event(self) -> None:
        ll = _make_logger()
        event = _make_event()
        ll.emit_turn(event)  # Must not raise

    def test_emit_turn_logs_at_info_level(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.INFO, logger="src.console_logger"):
            ll.emit_turn(_make_event())
        assert any("turn_event" in r.message for r in caplog.records)

    def test_emit_turn_logs_session_id(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.INFO, logger="src.console_logger"):
            ll.emit_turn(_make_event(session_id="my-session"))
        log_extras = [r.__dict__ for r in caplog.records]
        assert any(d.get("session_id") == "my-session" for d in log_extras)

    def test_emit_turn_accepts_dict_event(self) -> None:
        ll = _make_logger()
        event_dict = {
            "session_id": "s1",
            "response_text": "ok",
            "tool_calls": [],
            "trust_input_result": {"action": "allow", "passed": True},
            "trust_output_result": {"action": "allow", "passed": True},
            "model_used": "claude-test",
            "input_tokens": 10,
            "output_tokens": 5,
            "latency_ms": 100,
            "timestamp_ms": 1700000000000,
        }
        ll.emit_turn(event_dict)  # Must not raise

    def test_emit_turn_logs_tool_calls_count(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        tool_call = MagicMock()
        with caplog.at_level(logging.INFO, logger="src.console_logger"):
            ll.emit_turn(_make_event(tool_calls=[tool_call, tool_call]))
        extras = [r.__dict__ for r in caplog.records]
        assert any(d.get("tool_calls_count") == 2 for d in extras)


# ---------------------------------------------------------------------------
# emit_turn — edge cases
# ---------------------------------------------------------------------------

class TestEmitTurnEdgeCases:
    def test_emit_turn_none_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_turn(None)  # Must not raise

    def test_emit_turn_none_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.WARNING, logger="src.console_logger"):
            ll.emit_turn(None)
        assert any("skipped" in r.message for r in caplog.records)

    def test_emit_turn_missing_fields_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_turn({})  # Minimal dict, no fields

    def test_emit_turn_with_none_trust_results_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_turn(_make_event(trust_input_result=None, trust_output_result=None))

    def test_emit_turn_exception_in_event_does_not_propagate(self) -> None:
        ll = _make_logger()
        bad_event = MagicMock()
        bad_event.session_id = "s"
        bad_event.model_used = "m"
        bad_event.input_tokens = 0
        bad_event.output_tokens = 0
        bad_event.latency_ms = 0
        bad_event.timestamp_ms = 0
        bad_event.tool_calls = None  # None instead of list — triggers len() error
        bad_event.trust_input_result = None
        bad_event.trust_output_result = None
        # Must not raise even if internal processing fails
        ll.emit_turn(bad_event)


# ---------------------------------------------------------------------------
# emit_signal — normal cases
# ---------------------------------------------------------------------------

class TestEmitSignalNormal:
    def test_emit_signal_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_signal("drop_off", {"session_id": "s1", "step": "intent"})

    def test_emit_signal_logs_at_info_level(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.INFO, logger="src.console_logger"):
            ll.emit_signal("low_confidence", {"score": 0.3})
        assert any("signal_event" in r.message for r in caplog.records)

    def test_emit_signal_logs_signal_type(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.INFO, logger="src.console_logger"):
            ll.emit_signal("escalation_triggered", {})
        extras = [r.__dict__ for r in caplog.records]
        assert any(d.get("signal_type") == "escalation_triggered" for d in extras)

    def test_emit_signal_empty_data_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_signal("mismatch", {})

    def test_emit_signal_none_data_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_signal("mismatch", None)


# ---------------------------------------------------------------------------
# emit_signal — edge cases
# ---------------------------------------------------------------------------

class TestEmitSignalEdgeCases:
    def test_emit_signal_none_type_does_not_raise(self) -> None:
        ll = _make_logger()
        ll.emit_signal(None, {"key": "val"})  # Must not raise

    def test_emit_signal_none_type_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        ll = _make_logger()
        with caplog.at_level(logging.WARNING, logger="src.console_logger"):
            ll.emit_signal(None, {})
        assert any("skipped" in r.message for r in caplog.records)
