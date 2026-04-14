"""
Tests for POST /stream_turn SSE endpoint.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.models import DoneEvent, SentenceEvent, SignalEvent
from src.servers.orchestration_server import create_orchestration_app


def _make_mock_agent_core():
    """Create a mock AgentCore with stream_turn support."""
    agent = MagicMock()
    agent.process_turn = MagicMock()
    return agent


class TestStreamTurnEndpoint:

    def test_stream_turn_returns_event_stream(self):
        """POST /stream_turn returns text/event-stream content type."""
        agent = _make_mock_agent_core()

        async def mock_stream_turn(turn_input):
            yield SignalEvent(stage="memory_read", status="start")
            yield SignalEvent(stage="memory_read", status="complete")
            yield SentenceEvent(text="Hello.", sentence_index=0)
            yield DoneEvent(turn_id="t-1", latency_ms=100)

        agent.stream_turn = mock_stream_turn

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.post(
            "/stream_turn",
            json={"session_id": "s1", "user_message": "Hi"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

    def test_stream_turn_events_are_valid_sse(self):
        """Each line in the stream is valid SSE format."""
        agent = _make_mock_agent_core()

        async def mock_stream_turn(turn_input):
            yield SignalEvent(stage="trust_input", status="start")
            yield SentenceEvent(text="Hi there.", sentence_index=0)
            yield DoneEvent(turn_id="t-1")

        agent.stream_turn = mock_stream_turn

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.post(
            "/stream_turn",
            json={"session_id": "s1", "user_message": "Hi"},
        )

        lines = response.text.strip().split("\n\n")
        assert len(lines) == 3

        for line in lines:
            assert line.startswith("data: ")
            payload = json.loads(line[len("data: "):])
            assert "type" in payload

    def test_stream_turn_done_event_is_last(self):
        """DoneEvent is always the last event."""
        agent = _make_mock_agent_core()

        async def mock_stream_turn(turn_input):
            yield SignalEvent(stage="memory_read", status="start")
            yield SentenceEvent(text="Hello.", sentence_index=0)
            yield DoneEvent(turn_id="t-1", turn_status="completed")

        agent.stream_turn = mock_stream_turn

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.post(
            "/stream_turn",
            json={"session_id": "s1", "user_message": "Hi"},
        )

        lines = response.text.strip().split("\n\n")
        last_payload = json.loads(lines[-1][len("data: "):])
        assert last_payload["type"] == "done"

    def test_stream_turn_exception_emits_abandoned_done(self):
        """Exception during streaming emits DoneEvent with abandoned status."""
        agent = _make_mock_agent_core()

        async def mock_stream_turn(turn_input):
            yield SignalEvent(stage="memory_read", status="start")
            raise RuntimeError("boom")

        agent.stream_turn = mock_stream_turn

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.post(
            "/stream_turn",
            json={"session_id": "s1", "user_message": "Hi"},
        )

        lines = response.text.strip().split("\n\n")
        last_payload = json.loads(lines[-1][len("data: "):])
        assert last_payload["type"] == "done"
        assert last_payload["turn_status"] == "abandoned"

    def test_existing_process_turn_unchanged(self):
        """POST /process_turn still works as before."""
        from src.models import TurnResult
        agent = _make_mock_agent_core()
        agent.process_turn.return_value = TurnResult(
            session_id="s1", turn_id="t1", response_text="Hi",
            was_escalated=False, was_tool_used=False, model_used="m1", latency_ms=50,
        )
        agent.stream_turn = AsyncMock()

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.post(
            "/process_turn",
            json={"session_id": "s1", "user_message": "Hello"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response_text"] == "Hi"

    def test_health_endpoint_unchanged(self):
        """GET /health still returns ok."""
        agent = _make_mock_agent_core()
        agent.stream_turn = AsyncMock()

        app = create_orchestration_app(agent)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
