"""
Tests for session-based HTTP endpoints (#82):
  POST /sessions/{session_id}/input
  GET  /sessions/{session_id}/events
  DELETE /sessions/{session_id}/active_turn

Also tests backward compatibility: endpoints not registered when turn_assembler is None.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent, TurnResult
from src.servers.orchestration_server import create_orchestration_app
from src.turn_assembler import SessionBuffer, TurnAssembler, TurnStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_agent_core():
    agent = MagicMock()
    agent.process_turn = MagicMock(return_value=TurnResult(
        session_id="s1", turn_id="t1", response_text="Hi",
    ))

    async def mock_stream(ti):
        yield DoneEvent(turn_status="completed")

    agent.stream_turn = mock_stream
    return agent


def _make_mock_assembler():
    """Create a mock TurnAssembler with the expected interface."""
    assembler = MagicMock(spec=TurnAssembler)
    assembler._sessions = {}
    assembler.add_segment = AsyncMock()
    assembler.cancel = AsyncMock()
    assembler.session_end = AsyncMock()
    return assembler


# ---------------------------------------------------------------------------
# Backward compatibility — no turn_assembler
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:

    def test_no_session_endpoints_without_assembler(self):
        """Session endpoints are NOT registered when turn_assembler is None."""
        agent = _make_mock_agent_core()
        app = create_orchestration_app(agent, turn_assembler=None)
        client = TestClient(app)

        resp = client.post("/sessions/s1/input", json={"text": "hi"})
        assert resp.status_code in (404, 405)

    def test_process_turn_still_works(self):
        """POST /process_turn is unaffected by turn_assembler parameter."""
        agent = _make_mock_agent_core()
        app = create_orchestration_app(agent, turn_assembler=None)
        client = TestClient(app)

        resp = client.post("/process_turn", json={
            "session_id": "s1", "user_message": "hello",
        })
        assert resp.status_code == 200

    def test_health_still_works(self):
        agent = _make_mock_agent_core()
        app = create_orchestration_app(agent, turn_assembler=None)
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/input
# ---------------------------------------------------------------------------


class TestSessionInput:

    def test_returns_202(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/sessions/s1/input", json={"text": "hello"})
        assert resp.status_code == 202

    def test_calls_add_segment(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/sessions/s1/input", json={
            "text": "hello", "channel": "voice", "user_id": "u1",
        })
        assert resp.status_code == 202
        assembler.add_segment.assert_called_once()

        call_args = assembler.add_segment.call_args
        assert call_args[0][0] == "s1"
        segment = call_args[0][1]
        assert isinstance(segment, SegmentInput)
        assert segment.text == "hello"
        assert segment.channel == "voice"

    def test_empty_text_returns_422(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/sessions/s1/input", json={"text": ""})
        assert resp.status_code == 422

    def test_whitespace_text_returns_422(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/sessions/s1/input", json={"text": "   "})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/events
# ---------------------------------------------------------------------------


class TestSessionEvents:

    def test_returns_event_stream(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()

        # Mock subscribe to yield one DoneEvent
        async def mock_subscribe(session_id, user_id=None, channel=None):
            yield DoneEvent(turn_status="completed")

        assembler.subscribe = mock_subscribe
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.get("/sessions/s1/events")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    def test_yields_valid_sse(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()

        async def mock_subscribe(session_id, user_id=None, channel=None):
            yield SignalEvent(stage="memory_read", status="start")
            yield SentenceEvent(text="Hello!", sentence_index=0)
            yield DoneEvent(turn_status="completed")

        assembler.subscribe = mock_subscribe
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.get("/sessions/s1/events")
        lines = resp.text.strip().split("\n\n")
        assert len(lines) == 3

        for line in lines:
            assert line.startswith("data: ")
            payload = json.loads(line[len("data: "):])
            assert "type" in payload

    def test_done_event_is_last(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()

        async def mock_subscribe(session_id, user_id=None, channel=None):
            yield SentenceEvent(text="Hi.", sentence_index=0)
            yield DoneEvent(turn_status="completed")

        assembler.subscribe = mock_subscribe
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.get("/sessions/s1/events")
        lines = resp.text.strip().split("\n\n")
        last = json.loads(lines[-1][len("data: "):])
        assert last["type"] == "done"
        assert last["turn_status"] == "completed"


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}/active_turn
# ---------------------------------------------------------------------------


class TestSessionCancel:

    def test_returns_200_for_existing_session(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        assembler._sessions["s1"] = SessionBuffer(session_id="s1")
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.delete("/sessions/s1/active_turn")
        assert resp.status_code == 200
        assembler.cancel.assert_called_once_with("s1")

    def test_returns_404_for_nonexistent_session(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.delete("/sessions/nonexistent/active_turn")
        assert resp.status_code == 404

    def test_cancel_response_body(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        assembler._sessions["s1"] = SessionBuffer(session_id="s1")
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.delete("/sessions/s1/active_turn")
        assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Existing endpoints unchanged with assembler present
# ---------------------------------------------------------------------------


class TestExistingEndpointsWithAssembler:

    def test_process_turn_works_with_assembler(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/process_turn", json={
            "session_id": "s1", "user_message": "hello",
        })
        assert resp.status_code == 200

    def test_stream_turn_works_with_assembler(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/stream_turn", json={
            "session_id": "s1", "user_message": "hello",
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    def test_health_works_with_assembler(self):
        agent = _make_mock_agent_core()
        assembler = _make_mock_assembler()
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
