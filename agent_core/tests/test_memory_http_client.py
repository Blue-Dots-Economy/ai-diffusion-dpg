"""
agent_core/tests/test_memory_http_client.py

Unit tests for MemoryLayerHttpClient.

All HTTP calls are mocked via httpx. No real network connections made.

Covers:
- Normal execution: all 5 methods call correct endpoints and return correct types
- Edge cases: empty inputs, non-list response for sessions, None journey in bundle
- Failure scenarios: TimeoutException, HTTPStatusError, generic Exception all return safe defaults
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch
import httpx

from src.http_clients.memory_layer import MemoryLayerHttpClient
from src.models import ContextBundle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "memory_client": {
        "endpoint": "http://memory-layer:8002",
        "timeout_ms": 2000,
    }
}


@pytest.fixture
def client():
    return MemoryLayerHttpClient(CONFIG)


def _mock_response(json_data: dict | list, status_code: int = 200) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _mock_http_error(status_code: int) -> httpx.HTTPStatusError:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    return httpx.HTTPStatusError("error", request=MagicMock(), response=mock_resp)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        MemoryLayerHttpClient(None)


def test_default_endpoint_used_when_not_configured():
    c = MemoryLayerHttpClient({})
    assert c._endpoint == "http://localhost:8002"


def test_configured_endpoint_is_used(client):
    assert client._endpoint == "http://memory-layer:8002"


def test_configured_timeout_is_converted_to_seconds(client):
    assert client._timeout_s == 2.0


# ---------------------------------------------------------------------------
# context_bundle — normal execution
# ---------------------------------------------------------------------------

def test_context_bundle_returns_context_bundle_object(client):
    mock_resp = _mock_response({
        "session": {"current_node": "greeting"},
        "profile": {"language": "hindi"},
        "journey": None,
    })
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp):
        result = client.context_bundle("sess-1", "user-1")

    assert isinstance(result, ContextBundle)
    assert result.session == {"current_node": "greeting"}
    assert result.profile == {"language": "hindi"}
    assert result.journey is None


def test_context_bundle_posts_to_correct_endpoint(client):
    mock_resp = _mock_response({"session": {}, "profile": {}, "journey": None})
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp) as mock_post:
        client.context_bundle("sess-1", "user-1")

    mock_post.assert_called_once_with(
        "http://memory-layer:8002/context_bundle",
        json={"session_id": "sess-1", "user_id": "user-1", "adopt": True},
        timeout=2.0,
    )


def test_context_bundle_with_journey_data(client):
    mock_resp = _mock_response({
        "session": {},
        "profile": {},
        "journey": {"journey_id": "prev-sess", "end_reason": "completed", "outcomes": []},
    })
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp):
        result = client.context_bundle("sess-2", "user-1")

    assert result.journey is not None
    assert result.journey["journey_id"] == "prev-sess"


# ---------------------------------------------------------------------------
# context_bundle — edge cases
# ---------------------------------------------------------------------------

def test_context_bundle_empty_session_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="session_id must not be empty"):
        c.context_bundle("", "user-1")


def test_context_bundle_empty_user_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="user_id must not be empty"):
        c.context_bundle("sess-1", "")


def test_context_bundle_null_session_in_response_defaults_to_empty(client):
    mock_resp = _mock_response({"session": None, "profile": None, "journey": None})
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp):
        result = client.context_bundle("sess-1", "user-1")
    assert result.session == {}
    assert result.profile == {}


# ---------------------------------------------------------------------------
# context_bundle — failure scenarios
# ---------------------------------------------------------------------------

def test_context_bundle_timeout_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=httpx.TimeoutException("timeout")):
        result = client.context_bundle("sess-1", "user-1")
    assert result == ContextBundle.empty()


def test_context_bundle_http_error_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=_mock_http_error(500)):
        result = client.context_bundle("sess-1", "user-1")
    assert result == ContextBundle.empty()


def test_context_bundle_connection_error_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=ConnectionError("connection refused")):
        result = client.context_bundle("sess-1", "user-1")
    assert result == ContextBundle.empty()


# ---------------------------------------------------------------------------
# write — normal execution
# ---------------------------------------------------------------------------

def test_write_posts_to_correct_endpoint(client):
    mock_resp = _mock_response({"status": "ok"})
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp) as mock_post:
        client.write("sess-1", "user-1", "session", "current_node", "jobs")

    mock_post.assert_called_once_with(
        "http://memory-layer:8002/write",
        json={
            "session_id": "sess-1",
            "user_id": "user-1",
            "scope": "session",
            "key": "current_node",
            "value": "jobs",
        },
        timeout=2.0,
    )


def test_write_with_dict_value(client):
    mock_resp = _mock_response({"status": "ok"})
    signal_val = {"type": "objection", "turn": "3", "raw": "nahi chahiye"}
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp) as mock_post:
        client.write("sess-1", "user-1", "signal", "signal", signal_val)

    call_json = mock_post.call_args[1]["json"]
    assert call_json["value"] == signal_val


# ---------------------------------------------------------------------------
# write — edge cases
# ---------------------------------------------------------------------------

def test_write_empty_session_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="session_id must not be empty"):
        c.write("", "user-1", "session", "key", "val")


def test_write_empty_user_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="user_id must not be empty"):
        c.write("sess-1", "", "session", "key", "val")


# ---------------------------------------------------------------------------
# write — failure scenarios
# ---------------------------------------------------------------------------

def test_write_timeout_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=httpx.TimeoutException("timeout")):
        client.write("sess-1", "user-1", "session", "key", "val")  # must not raise


def test_write_http_error_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=_mock_http_error(503)):
        client.write("sess-1", "user-1", "session", "key", "val")  # must not raise


def test_write_connection_error_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=ConnectionError("refused")):
        client.write("sess-1", "user-1", "session", "key", "val")  # must not raise


# ---------------------------------------------------------------------------
# flush_session — normal execution
# ---------------------------------------------------------------------------

def test_flush_session_posts_to_correct_endpoint(client):
    mock_resp = _mock_response({"status": "ok"})
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp) as mock_post:
        client.flush_session("sess-1", "user-1", "completed")

    mock_post.assert_called_once_with(
        "http://memory-layer:8002/flush_session",
        json={"session_id": "sess-1", "user_id": "user-1", "end_reason": "completed"},
        timeout=2.0,
    )


def test_flush_session_uses_unknown_for_empty_end_reason(client):
    mock_resp = _mock_response({"status": "ok"})
    with patch("src.http_clients.memory_layer.httpx.post", return_value=mock_resp) as mock_post:
        client.flush_session("sess-1", "user-1", "")

    call_json = mock_post.call_args[1]["json"]
    assert call_json["end_reason"] == "unknown"


# ---------------------------------------------------------------------------
# flush_session — edge cases
# ---------------------------------------------------------------------------

def test_flush_session_empty_session_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="session_id must not be empty"):
        c.flush_session("", "user-1", "done")


def test_flush_session_empty_user_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="user_id must not be empty"):
        c.flush_session("sess-1", "", "done")


# ---------------------------------------------------------------------------
# flush_session — failure scenarios
# ---------------------------------------------------------------------------

def test_flush_session_timeout_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=httpx.TimeoutException("timeout")):
        client.flush_session("sess-1", "user-1", "done")  # must not raise


def test_flush_session_connection_error_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.post",
               side_effect=ConnectionError("refused")):
        client.flush_session("sess-1", "user-1", "done")  # must not raise


# ---------------------------------------------------------------------------
# get_active_sessions — normal execution
# ---------------------------------------------------------------------------

def test_get_active_sessions_returns_list(client):
    sessions = [
        {"session_id": "sess-1", "last_accessed": "2024-01-01T11:00:00Z"},
        {"session_id": "sess-2", "last_accessed": "2024-01-01T10:00:00Z"},
    ]
    mock_resp = _mock_response(sessions)
    with patch("src.http_clients.memory_layer.httpx.get", return_value=mock_resp):
        result = client.get_active_sessions("user-1")

    assert len(result) == 2
    assert result[0]["session_id"] == "sess-1"


def test_get_active_sessions_calls_correct_url(client):
    mock_resp = _mock_response([])
    with patch("src.http_clients.memory_layer.httpx.get", return_value=mock_resp) as mock_get:
        client.get_active_sessions("user-1")

    mock_get.assert_called_once_with(
        "http://memory-layer:8002/sessions/user-1",
        timeout=2.0,
    )


# ---------------------------------------------------------------------------
# get_active_sessions — edge cases
# ---------------------------------------------------------------------------

def test_get_active_sessions_empty_user_id_returns_empty():
    c = MemoryLayerHttpClient(CONFIG)
    result = c.get_active_sessions("")
    assert result == []


def test_get_active_sessions_non_list_response_returns_empty(client):
    mock_resp = _mock_response({"unexpected": "dict"})
    with patch("src.http_clients.memory_layer.httpx.get", return_value=mock_resp):
        result = client.get_active_sessions("user-1")
    assert result == []


# ---------------------------------------------------------------------------
# get_active_sessions — failure scenarios
# ---------------------------------------------------------------------------

def test_get_active_sessions_timeout_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.get",
               side_effect=httpx.TimeoutException("timeout")):
        result = client.get_active_sessions("user-1")
    assert result == []


def test_get_active_sessions_http_error_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.get",
               side_effect=_mock_http_error(503)):
        result = client.get_active_sessions("user-1")
    assert result == []


def test_get_active_sessions_connection_error_returns_empty(client):
    with patch("src.http_clients.memory_layer.httpx.get",
               side_effect=ConnectionError("refused")):
        result = client.get_active_sessions("user-1")
    assert result == []


# ---------------------------------------------------------------------------
# delete_user — normal execution
# ---------------------------------------------------------------------------

def test_delete_user_calls_correct_url(client):
    mock_resp = _mock_response({"status": "ok"})
    with patch("src.http_clients.memory_layer.httpx.delete", return_value=mock_resp) as mock_del:
        client.delete_user("user-1")

    mock_del.assert_called_once_with(
        "http://memory-layer:8002/user/user-1",
        timeout=2.0,
    )


# ---------------------------------------------------------------------------
# delete_user — edge cases
# ---------------------------------------------------------------------------

def test_delete_user_empty_user_id_raises():
    c = MemoryLayerHttpClient(CONFIG)
    with pytest.raises(ValueError, match="user_id must not be empty"):
        c.delete_user("")


# ---------------------------------------------------------------------------
# delete_user — failure scenarios
# ---------------------------------------------------------------------------

def test_delete_user_timeout_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.delete",
               side_effect=httpx.TimeoutException("timeout")):
        client.delete_user("user-1")  # must not raise


def test_delete_user_http_error_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.delete",
               side_effect=_mock_http_error(500)):
        client.delete_user("user-1")  # must not raise


def test_delete_user_connection_error_does_not_raise(client):
    with patch("src.http_clients.memory_layer.httpx.delete",
               side_effect=ConnectionError("refused")):
        client.delete_user("user-1")  # must not raise
