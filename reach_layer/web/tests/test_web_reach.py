"""
Tests for WebReachLayer — the web channel adapter.

Covers:
  - Constructor validation and config parsing
  - Lifecycle hooks (no-op logging)
  - run_loop placeholder
  - build_turn_input validation + normalisation
  - format_result empty / populated / partial responses
"""

from __future__ import annotations

import pytest

from src.web_reach import WebReachLayer


def _config(assembly_mode: str = "direct") -> dict:
    return {
        "agent_core_client": {
            "endpoint": "http://localhost:8000/process_turn",
            "timeout_s": 30.0,
        },
        "reach_layer": {
            "channels": {
                "web": {
                    "assembly_mode": assembly_mode,
                    "title": "Test Chat",
                }
            }
        },
    }


class TestConstructor:
    def test_raises_if_config_is_none(self) -> None:
        with pytest.raises(ValueError):
            WebReachLayer(None)

    def test_reads_title(self) -> None:
        layer = WebReachLayer(_config())
        assert layer._title == "Test Chat"

    def test_default_title_when_missing(self) -> None:
        layer = WebReachLayer({"agent_core_client": {}})
        assert layer._title == "DPG Chat"

    def test_channel_name(self) -> None:
        assert WebReachLayer(_config()).channel_name == "web"

    def test_assembly_mode_direct(self) -> None:
        assert WebReachLayer(_config("direct")).assembly_mode == "direct"

    def test_assembly_mode_session(self) -> None:
        assert WebReachLayer(_config("session")).assembly_mode == "session"


class TestLifecycleHooks:
    async def test_on_session_start_noop(self) -> None:
        layer = WebReachLayer(_config())
        await layer.on_session_start("s1", "u1")

    async def test_on_session_end_noop(self) -> None:
        layer = WebReachLayer(_config())
        await layer.on_session_end("s1")

    async def test_run_loop_noop(self) -> None:
        layer = WebReachLayer(_config())
        await layer.run_loop()


class TestBuildTurnInput:
    def test_builds_valid_input(self) -> None:
        layer = WebReachLayer(_config())
        t = layer.build_turn_input("sess-1", "user-1", "hello world")
        assert t["session_id"] == "sess-1"
        assert t["user_message"] == "hello world"
        assert t["user_id"] == "user-1"
        assert t["channel"] == "web"
        assert t["timestamp_ms"] > 0

    def test_strips_whitespace(self) -> None:
        layer = WebReachLayer(_config())
        t = layer.build_turn_input(" s1 ", " u1 ", "  hey  ")
        assert t["session_id"] == "s1"
        assert t["user_id"] == "u1"
        assert t["user_message"] == "hey"

    def test_user_id_optional(self) -> None:
        layer = WebReachLayer(_config())
        t = layer.build_turn_input("s1", None, "hello")
        assert t["user_id"] is None

    def test_empty_session_id_raises(self) -> None:
        layer = WebReachLayer(_config())
        with pytest.raises(ValueError):
            layer.build_turn_input("", "u1", "hello")

    def test_empty_message_raises(self) -> None:
        layer = WebReachLayer(_config())
        with pytest.raises(ValueError):
            layer.build_turn_input("s1", "u1", "")

    def test_whitespace_message_raises(self) -> None:
        layer = WebReachLayer(_config())
        with pytest.raises(ValueError):
            layer.build_turn_input("s1", "u1", "   ")


class TestFormatResult:
    def test_empty_data(self) -> None:
        layer = WebReachLayer(_config())
        out = layer.format_result("s1", None, 123)
        assert out == {
            "response_text": "",
            "was_escalated": False,
            "was_tool_used": False,
            "session_id": "s1",
            "latency_ms": 123,
        }

    def test_populated_data(self) -> None:
        layer = WebReachLayer(_config())
        data = {
            "response_text": "Hi there.",
            "was_escalated": True,
            "was_tool_used": True,
        }
        out = layer.format_result("s1", data, 200)
        assert out["response_text"] == "Hi there."
        assert out["was_escalated"] is True
        assert out["was_tool_used"] is True
        assert out["session_id"] == "s1"
        assert out["latency_ms"] == 200

    def test_partial_data_safely_defaults(self) -> None:
        layer = WebReachLayer(_config())
        out = layer.format_result("s1", {"response_text": "ok"}, 100)
        assert out["was_escalated"] is False
        assert out["was_tool_used"] is False
