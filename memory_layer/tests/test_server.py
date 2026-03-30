"""
memory_layer/tests/test_server.py

Unit tests for the Memory Layer FastAPI server (src/server.py).
Uses FastAPI TestClient — no real HTTP calls or real memory stores.

Covers:
- Normal execution: all 6 endpoints return correct responses for valid inputs
- Edge cases: empty session_id/user_id, missing fields, empty user sessions
- Failure scenarios: memory layer exceptions are absorbed, safe defaults returned
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_memory():
    return MagicMock()


@pytest.fixture
def client(mock_memory):
    app = create_app(mock_memory)
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_app validation
# ---------------------------------------------------------------------------

def test_create_app_none_raises():
    with pytest.raises(ValueError, match="memory must not be None"):
        create_app(None)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /context_bundle — normal execution
# ---------------------------------------------------------------------------

def test_context_bundle_returns_bundle(client, mock_memory):
    mock_memory.context_bundle.return_value = {
        "session": {"current_node": "greeting", "trade": ""},
        "profile": {"language": "hindi"},
        "journey": None,
    }
    response = client.post("/context_bundle", json={"session_id": "sess-1", "user_id": "user-1"})
    assert response.status_code == 200
    data = response.json()
    assert data["session"]["current_node"] == "greeting"
    assert data["profile"]["language"] == "hindi"
    assert data["journey"] is None


def test_context_bundle_calls_memory_with_correct_args(client, mock_memory):
    mock_memory.context_bundle.return_value = {"session": {}, "profile": {}, "journey": None}
    client.post("/context_bundle", json={"session_id": "sess-1", "user_id": "user-1"})
    mock_memory.context_bundle.assert_called_once_with("sess-1", "user-1")


# ---------------------------------------------------------------------------
# POST /context_bundle — edge cases
# ---------------------------------------------------------------------------

def test_context_bundle_empty_session_id_returns_empty_bundle(client, mock_memory):
    response = client.post("/context_bundle", json={"session_id": "", "user_id": "user-1"})
    assert response.status_code == 200
    data = response.json()
    assert data == {"session": {}, "profile": {}, "journey": None}
    mock_memory.context_bundle.assert_not_called()


def test_context_bundle_empty_user_id_returns_empty_bundle(client, mock_memory):
    response = client.post("/context_bundle", json={"session_id": "sess-1", "user_id": ""})
    assert response.status_code == 200
    data = response.json()
    assert data == {"session": {}, "profile": {}, "journey": None}
    mock_memory.context_bundle.assert_not_called()


# ---------------------------------------------------------------------------
# POST /context_bundle — failure scenarios
# ---------------------------------------------------------------------------

def test_context_bundle_memory_exception_returns_empty_bundle(client, mock_memory):
    mock_memory.context_bundle.side_effect = RuntimeError("neo4j down")
    response = client.post("/context_bundle", json={"session_id": "sess-1", "user_id": "user-1"})
    assert response.status_code == 200
    data = response.json()
    assert data == {"session": {}, "profile": {}, "journey": None}


# ---------------------------------------------------------------------------
# POST /write — normal execution
# ---------------------------------------------------------------------------

def test_write_returns_ok_status(client, mock_memory):
    response = client.post("/write", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "scope": "session",
        "key": "current_node",
        "value": "jobs",
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_write_calls_memory_with_correct_args(client, mock_memory):
    client.post("/write", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "scope": "session",
        "key": "trade",
        "value": "electrician",
    })
    mock_memory.write.assert_called_once_with("sess-1", "user-1", "session", "trade", "electrician")


def test_write_with_dict_value(client, mock_memory):
    response = client.post("/write", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "scope": "signal",
        "key": "signal",
        "value": {"type": "objection", "turn": "3", "raw": "nahi chahiye"},
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /write — edge cases
# ---------------------------------------------------------------------------

def test_write_empty_session_id_returns_ok_without_calling_memory(client, mock_memory):
    response = client.post("/write", json={
        "session_id": "",
        "user_id": "user-1",
        "scope": "session",
        "key": "trade",
        "value": "welder",
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mock_memory.write.assert_not_called()


def test_write_empty_key_returns_ok_without_calling_memory(client, mock_memory):
    response = client.post("/write", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "scope": "session",
        "key": "",
        "value": "welder",
    })
    assert response.status_code == 200
    mock_memory.write.assert_not_called()


# ---------------------------------------------------------------------------
# POST /write — failure scenarios
# ---------------------------------------------------------------------------

def test_write_memory_exception_still_returns_ok(client, mock_memory):
    mock_memory.write.side_effect = RuntimeError("redis down")
    response = client.post("/write", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "scope": "session",
        "key": "trade",
        "value": "welder",
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /flush_session — normal execution
# ---------------------------------------------------------------------------

def test_flush_session_returns_ok(client, mock_memory):
    response = client.post("/flush_session", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "end_reason": "completed",
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_flush_session_calls_memory_with_correct_args(client, mock_memory):
    client.post("/flush_session", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "end_reason": "timeout",
    })
    mock_memory.flush_session.assert_called_once_with("sess-1", "user-1", "timeout")


# ---------------------------------------------------------------------------
# POST /flush_session — edge cases
# ---------------------------------------------------------------------------

def test_flush_session_empty_session_id_returns_ok_without_call(client, mock_memory):
    response = client.post("/flush_session", json={
        "session_id": "",
        "user_id": "user-1",
        "end_reason": "completed",
    })
    assert response.status_code == 200
    mock_memory.flush_session.assert_not_called()


# ---------------------------------------------------------------------------
# POST /flush_session — failure scenarios
# ---------------------------------------------------------------------------

def test_flush_session_memory_exception_still_returns_ok(client, mock_memory):
    mock_memory.flush_session.side_effect = RuntimeError("neo4j down")
    response = client.post("/flush_session", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "end_reason": "error",
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /sessions/{user_id} — normal execution
# ---------------------------------------------------------------------------

def test_get_active_sessions_returns_session_list(client, mock_memory):
    mock_memory.get_active_sessions.return_value = [
        {"session_id": "sess-1", "last_accessed": "2024-01-01T11:00:00Z"},
        {"session_id": "sess-2", "last_accessed": "2024-01-01T10:00:00Z"},
    ]
    response = client.get("/sessions/user-1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["session_id"] == "sess-1"


def test_get_active_sessions_no_sessions_returns_empty_list(client, mock_memory):
    mock_memory.get_active_sessions.return_value = []
    response = client.get("/sessions/user-1")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /sessions/{user_id} — failure scenarios
# ---------------------------------------------------------------------------

def test_get_active_sessions_memory_exception_returns_empty(client, mock_memory):
    mock_memory.get_active_sessions.side_effect = RuntimeError("redis down")
    response = client.get("/sessions/user-1")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# DELETE /user/{user_id} — normal execution
# ---------------------------------------------------------------------------

def test_delete_user_returns_ok(client, mock_memory):
    response = client.delete("/user/user-1")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_user_calls_memory_with_correct_args(client, mock_memory):
    client.delete("/user/user-1")
    mock_memory.delete_user.assert_called_once_with("user-1")


# ---------------------------------------------------------------------------
# DELETE /user/{user_id} — failure scenarios
# ---------------------------------------------------------------------------

def test_delete_user_memory_exception_still_returns_ok(client, mock_memory):
    mock_memory.delete_user.side_effect = RuntimeError("neo4j down")
    response = client.delete("/user/user-1")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
