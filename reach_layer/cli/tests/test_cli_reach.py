"""
Tests for CLIReachLayer — the CLI channel adapter.

Covers:
  - Constructor validation, config defaults
  - Lifecycle hooks (on_session_start, on_session_end)
  - run_loop session mode (submits line, waits for DoneEvent)
  - run_loop direct mode (submits line, prints synchronous result)
  - Event rendering (SentenceEvent, SignalEvent, DoneEvent)
  - EOF/empty-line handling
  - Error handling on submit failure
"""

from __future__ import annotations

import asyncio
import io
import sys
from unittest.mock import AsyncMock, patch

import pytest

from src.cli_reach import CLIReachLayer
from reach_layer_base import DoneEvent, SentenceEvent, SignalEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_config() -> dict:
    return {
        "agent_core_client": {
            "endpoint": "http://localhost:8000/process_turn",
            "timeout_s": 30.0,
        },
        "reach_layer": {
            "channels": {
                "cli": {
                    "assembly_mode": "session",
                    "prompt": "You: ",
                    "agent_prefix": "Agent: ",
                }
            }
        },
    }


def _direct_config() -> dict:
    cfg = _session_config()
    cfg["reach_layer"]["channels"]["cli"]["assembly_mode"] = "direct"
    return cfg


def _make_layer(config: dict | None = None, **kwargs) -> CLIReachLayer:
    return CLIReachLayer(
        config=config or _session_config(),
        session_id=kwargs.pop("session_id", "test-session"),
        user_id=kwargs.pop("user_id", None),
        verbose=kwargs.pop("verbose", False),
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_raises_if_config_is_none(self) -> None:
        with pytest.raises(ValueError, match="config must not be None"):
            CLIReachLayer(None)

    def test_generates_session_id_if_not_provided(self) -> None:
        layer = CLIReachLayer(_session_config())
        assert layer.session_id
        assert len(layer.session_id) > 0

    def test_uses_provided_session_id(self) -> None:
        layer = _make_layer(session_id="abc-123")
        assert layer.session_id == "abc-123"

    def test_defaults_for_missing_cli_config(self) -> None:
        layer = CLIReachLayer({"agent_core_client": {}}, session_id="s1")
        assert layer._prompt == "You: "
        assert layer._agent_prefix == "Agent: "

    def test_reads_prompt_from_config(self) -> None:
        cfg = _session_config()
        cfg["reach_layer"]["channels"]["cli"]["prompt"] = ">>> "
        layer = CLIReachLayer(cfg, session_id="s1")
        assert layer._prompt == ">>> "

    def test_channel_name_is_cli(self) -> None:
        layer = _make_layer()
        assert layer.channel_name == "cli"

    def test_reads_assembly_mode_session(self) -> None:
        layer = _make_layer()
        assert layer.assembly_mode == "session"

    def test_reads_assembly_mode_direct(self) -> None:
        layer = _make_layer(_direct_config())
        assert layer.assembly_mode == "direct"

    def test_user_id_property(self) -> None:
        layer = _make_layer(user_id="+91999")
        assert layer.user_id == "+91999"


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


class TestLifecycleHooks:
    async def test_on_session_start_does_not_raise(self) -> None:
        layer = _make_layer()
        await layer.on_session_start("sess-1", "user-1")

    async def test_on_session_end_closes_client(self) -> None:
        layer = _make_layer()
        layer.close = AsyncMock()
        await layer.on_session_end("sess-1")
        layer.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Event rendering
# ---------------------------------------------------------------------------


class TestRenderEvent:
    def test_renders_sentence_event_first(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer()
        layer._render_event(SentenceEvent(text="Hello there.", sentence_index=0))
        out = capsys.readouterr().out
        assert "Agent: " in out
        assert "Hello there." in out

    def test_renders_sentence_event_subsequent(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer()
        layer._render_event(SentenceEvent(text="Second sentence.", sentence_index=1))
        out = capsys.readouterr().out
        # No agent prefix on subsequent sentences
        assert "Agent: " not in out
        assert "Second sentence." in out

    def test_signal_event_silent_without_verbose(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer()
        layer._render_event(SignalEvent(stage="nlu", status="start"))
        assert capsys.readouterr().out == ""

    def test_signal_event_shown_with_verbose(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer(verbose=True)
        layer._render_event(SignalEvent(stage="nlu", status="start"))
        assert "nlu:start" in capsys.readouterr().out

    def test_done_event_plain(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer()
        layer._render_event(DoneEvent(turn_status="completed"))
        out = capsys.readouterr().out
        assert "\n" in out  # newline terminator only
        assert "escalated" not in out

    def test_done_event_escalated(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer()
        layer._render_event(DoneEvent(was_escalated=True))
        assert "escalated" in capsys.readouterr().out

    def test_done_event_tool_used(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer()
        layer._render_event(DoneEvent(was_tool_used=True))
        assert "tool" in capsys.readouterr().out

    def test_done_event_abandoned(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer()
        layer._render_event(DoneEvent(turn_status="abandoned"))
        assert "abandoned" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Direct-mode result rendering
# ---------------------------------------------------------------------------


class TestRenderDirectResult:
    def test_renders_response(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer(_direct_config())
        layer._render_direct_result({"response_text": "Hi there."}, 0.0)
        out = capsys.readouterr().out
        assert "Agent: Hi there." in out

    def test_none_result_prints_error(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer(_direct_config())
        layer._render_direct_result(None, 0.0)
        assert "no response" in capsys.readouterr().out

    def test_escalated_notice(self, capsys: pytest.CaptureFixture) -> None:
        layer = _make_layer(_direct_config())
        layer._render_direct_result(
            {"response_text": "handing off", "was_escalated": True}, 0.0
        )
        assert "ESCALATED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _read_line
# ---------------------------------------------------------------------------


class TestReadLine:
    def test_read_line_strips(self) -> None:
        layer = _make_layer()
        with patch("sys.stdin", io.StringIO("  hello  \n")):
            assert layer._read_line() == "hello"

    def test_read_line_eof_returns_none(self) -> None:
        layer = _make_layer()
        with patch("sys.stdin", io.StringIO("")):
            assert layer._read_line() is None


# ---------------------------------------------------------------------------
# run_loop — direct mode (simpler to exercise end-to-end)
# ---------------------------------------------------------------------------


class TestRunLoopDirect:
    async def test_submits_each_line_and_prints_result(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer(_direct_config())
        # Mock submit_input to return TurnResult-shaped dicts
        layer.submit_input = AsyncMock(
            side_effect=[
                {"response_text": "Hello!"},
                {"response_text": "Goodbye."},
            ]
        )
        layer.close = AsyncMock()

        inputs = iter(["hi", "bye", None])  # None marks EOF

        def fake_read() -> str | None:
            return next(inputs)

        layer._read_line = fake_read

        await layer.run_loop()

        assert layer.submit_input.await_count == 2
        out = capsys.readouterr().out
        assert "Hello!" in out
        assert "Goodbye." in out

    async def test_empty_line_is_skipped(self) -> None:
        layer = _make_layer(_direct_config())
        layer.submit_input = AsyncMock(return_value={"response_text": "ok"})
        layer.close = AsyncMock()

        inputs = iter(["", "real", None])

        def fake_read() -> str | None:
            return next(inputs)

        layer._read_line = fake_read
        await layer.run_loop()

        assert layer.submit_input.await_count == 1

    async def test_submit_error_is_reported_and_loop_continues(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer(_direct_config())
        layer.submit_input = AsyncMock(
            side_effect=[RuntimeError("network"), {"response_text": "recovered"}]
        )
        layer.close = AsyncMock()

        inputs = iter(["first", "second", None])

        def fake_read() -> str | None:
            return next(inputs)

        layer._read_line = fake_read
        await layer.run_loop()

        out = capsys.readouterr().out
        assert "Error: RuntimeError: network" in out
        assert "recovered" in out


# ---------------------------------------------------------------------------
# run_loop — session mode (waits for DoneEvent)
# ---------------------------------------------------------------------------


class TestRunLoopSession:
    async def test_session_mode_waits_for_done_event(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        layer = _make_layer()  # session mode
        layer.submit_input = AsyncMock(return_value=None)
        layer.close = AsyncMock()

        # subscribe_events yields one sentence then a DoneEvent, loops idle after.
        async def fake_subscribe(_session_id: str, user_id: str | None = None):
            yield SentenceEvent(text="Hi.", sentence_index=0)
            yield DoneEvent(turn_status="completed")

        layer.subscribe_events = fake_subscribe

        inputs = iter(["hello", None])

        def fake_read() -> str | None:
            return next(inputs)

        layer._read_line = fake_read

        await asyncio.wait_for(layer.run_loop(), timeout=2.0)

        layer.submit_input.assert_awaited_once()
        assert "Hi." in capsys.readouterr().out
