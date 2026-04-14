"""
Tests for streaming event dataclasses (SignalEvent, SentenceEvent, DoneEvent)
and ToolUseRequested exception.
"""

import json

from src.exceptions import ToolUseRequested
from src.models import (
    DoneEvent,
    SentenceEvent,
    SignalEvent,
    ToolCall,
)


class TestSignalEvent:

    def test_defaults(self):
        event = SignalEvent()
        assert event.type == "signal"
        assert event.stage == ""
        assert event.status == ""
        assert event.detail == ""

    def test_to_sse(self):
        event = SignalEvent(stage="memory_read", status="start")
        sse = event.to_sse()
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        payload = json.loads(sse[len("data: "):-2])
        assert payload["type"] == "signal"
        assert payload["stage"] == "memory_read"
        assert payload["status"] == "start"

    def test_to_sse_with_detail(self):
        event = SignalEvent(stage="nlu", status="complete", detail="intent=greeting")
        payload = json.loads(event.to_sse()[len("data: "):-2])
        assert payload["detail"] == "intent=greeting"


class TestSentenceEvent:

    def test_defaults(self):
        event = SentenceEvent()
        assert event.type == "sentence"
        assert event.text == ""
        assert event.sentence_index == 0

    def test_to_sse(self):
        event = SentenceEvent(text="Hello, how can I help?", sentence_index=0)
        sse = event.to_sse()
        payload = json.loads(sse[len("data: "):-2])
        assert payload["type"] == "sentence"
        assert payload["text"] == "Hello, how can I help?"
        assert payload["sentence_index"] == 0

    def test_multiple_sentences(self):
        for i in range(3):
            event = SentenceEvent(text=f"Sentence {i}.", sentence_index=i)
            payload = json.loads(event.to_sse()[len("data: "):-2])
            assert payload["sentence_index"] == i


class TestDoneEvent:

    def test_defaults(self):
        event = DoneEvent()
        assert event.type == "done"
        assert event.was_escalated is False
        assert event.was_tool_used is False
        assert event.model_used == ""
        assert event.latency_ms == 0
        assert event.turn_id == ""
        assert event.turn_status == "completed"

    def test_to_sse(self):
        event = DoneEvent(
            was_escalated=True,
            was_tool_used=True,
            model_used="claude-3-haiku",
            latency_ms=1200,
            turn_id="abc-123",
        )
        sse = event.to_sse()
        payload = json.loads(sse[len("data: "):-2])
        assert payload["type"] == "done"
        assert payload["was_escalated"] is True
        assert payload["was_tool_used"] is True
        assert payload["model_used"] == "claude-3-haiku"
        assert payload["latency_ms"] == 1200
        assert payload["turn_id"] == "abc-123"
        assert payload["turn_status"] == "completed"

    def test_abandoned_status(self):
        event = DoneEvent(turn_status="abandoned")
        payload = json.loads(event.to_sse()[len("data: "):-2])
        assert payload["turn_status"] == "abandoned"


class TestToolUseRequested:

    def test_carries_tool_calls(self):
        tc = ToolCall(tool_name="search", tool_use_id="tu_1", input_params={"q": "hello"})
        exc = ToolUseRequested([tc])
        assert len(exc.tool_calls) == 1
        assert exc.tool_calls[0].tool_name == "search"

    def test_message_contains_tool_names(self):
        tc1 = ToolCall(tool_name="search", tool_use_id="tu_1", input_params={})
        tc2 = ToolCall(tool_name="fetch", tool_use_id="tu_2", input_params={})
        exc = ToolUseRequested([tc1, tc2])
        assert "search" in str(exc)
        assert "fetch" in str(exc)

    def test_is_agent_core_error(self):
        from src.exceptions import AgentCoreError
        tc = ToolCall(tool_name="x", tool_use_id="y", input_params={})
        exc = ToolUseRequested([tc])
        assert isinstance(exc, AgentCoreError)
