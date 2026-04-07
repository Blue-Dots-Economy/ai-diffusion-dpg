"""
reach_layer/tests/test_server.py

Unit tests for the Reach Layer FastAPI server (server.py).
Uses FastAPI TestClient — no real HTTP calls; Agent Core and Memory Layer
are mocked via respx.

Covers:
- Normal execution: /health, /chat success, /user-history success
- Edge cases:       empty user_id in /user-history, missing/invalid chat fields
- Failure scenarios: Agent Core timeout, connect error, Memory Layer error
"""

from __future__ import annotations

import pytest
import respx
import httpx
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from server import create_app
from src.web_reach import WebReachLayer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "agent_core_client": {
            "endpoint": "http://agent-core-test/process_turn",
            "timeout_s": 5.0,
        },
        "memory_layer_client": {
            "endpoint": "http://memory-layer-test",
            "timeout_s": 5.0,
        },
        "reach_layer": {"web": {"title": "Test Chat"}},
    }


@pytest.fixture
def web_reach(config):
    return WebReachLayer(config)


@pytest.fixture
def client(web_reach, config):
    app = create_app(web_reach, config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_app validation
# ---------------------------------------------------------------------------

def test_create_app_none_web_reach_raises(config):
    with pytest.raises(ValueError, match="web_reach must not be None"):
        create_app(None, config)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET / — serves HTML
# ---------------------------------------------------------------------------

def test_index_returns_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# POST /chat — normal execution
# ---------------------------------------------------------------------------

@respx.mock
def test_chat_returns_response_text(client):
    respx.post("http://agent-core-test/process_turn").mock(
        return_value=httpx.Response(200, json={"response_text": "Hello!", "was_escalated": False})
    )
    response = client.post("/chat", json={
        "session_id": "sess-1",
        "user_id": "user-1",
        "message": "hi there",
    })
    assert response.status_code == 200
    assert response.json()["response_text"] == "Hello!"


@respx.mock
def test_chat_passes_session_id_to_agent_core(client):
    route = respx.post("http://agent-core-test/process_turn").mock(
        return_value=httpx.Response(200, json={"response_text": "ok"})
    )
    client.post("/chat", json={
        "session_id": "sess-abc",
        "user_id": "user-1",
        "message": "test",
    })
    called_body = route.calls[0].request
    import json
    body = json.loads(called_body.content)
    assert body["session_id"] == "sess-abc"


@respx.mock
def test_chat_was_escalated_propagates(client):
    respx.post("http://agent-core-test/process_turn").mock(
        return_value=httpx.Response(200, json={"response_text": "Escalated", "was_escalated": True})
    )
    response = client.post("/chat", json={"session_id": "s1", "message": "bad input"})
    assert response.json()["was_escalated"] is True


# ---------------------------------------------------------------------------
# POST /chat — edge cases
# ---------------------------------------------------------------------------

def test_chat_empty_session_id_returns_error_message(client):
    response = client.post("/chat", json={"session_id": "", "message": "hi"})
    assert response.status_code == 200
    data = response.json()
    assert "Invalid request" in data["response_text"]


def test_chat_empty_message_returns_error_message(client):
    response = client.post("/chat", json={"session_id": "sess-1", "message": ""})
    assert response.status_code == 200
    data = response.json()
    assert "Invalid request" in data["response_text"]


# ---------------------------------------------------------------------------
# POST /chat — failure scenarios
# ---------------------------------------------------------------------------

@respx.mock
def test_chat_agent_core_timeout_returns_safe_message(client):
    respx.post("http://agent-core-test/process_turn").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    response = client.post("/chat", json={"session_id": "sess-1", "message": "hi"})
    assert response.status_code == 200
    assert "did not respond" in response.json()["response_text"].lower()


@respx.mock
def test_chat_agent_core_connect_error_returns_safe_message(client):
    respx.post("http://agent-core-test/process_turn").mock(
        side_effect=httpx.ConnectError("refused")
    )
    response = client.post("/chat", json={"session_id": "sess-1", "message": "hi"})
    assert response.status_code == 200
    assert "reach agent core" in response.json()["response_text"].lower()


@respx.mock
def test_chat_agent_core_500_returns_safe_message(client):
    respx.post("http://agent-core-test/process_turn").mock(
        return_value=httpx.Response(500)
    )
    response = client.post("/chat", json={"session_id": "sess-1", "message": "hi"})
    assert response.status_code == 200
    assert response.json()["was_escalated"] is False


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — normal execution
# ---------------------------------------------------------------------------

@respx.mock
def test_user_history_returns_session_and_turns(client):
    respx.get("http://memory-layer-test/users/user-1/active-history").mock(
        return_value=httpx.Response(200, json={
            "session_id": "sess-abc",
            "turns": [{"user_message": "hello", "system_message": "hi"}],
        })
    )
    response = client.get("/user-history/user-1")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-abc"
    assert len(data["turns"]) == 1


@respx.mock
def test_user_history_no_session_returns_null(client):
    respx.get("http://memory-layer-test/users/new-user/active-history").mock(
        return_value=httpx.Response(200, json={"session_id": None, "turns": []})
    )
    response = client.get("/user-history/new-user")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — edge cases
# ---------------------------------------------------------------------------

def test_user_history_whitespace_user_id_returns_null(client):
    response = client.get("/user-history/%20%20%20")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /user-history/{user_id} — failure scenarios
# ---------------------------------------------------------------------------

@respx.mock
def test_user_history_memory_layer_error_returns_null(client):
    respx.get("http://memory-layer-test/users/user-1/active-history").mock(
        side_effect=httpx.ConnectError("refused")
    )
    response = client.get("/user-history/user-1")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


@respx.mock
def test_user_history_memory_layer_timeout_returns_null(client):
    respx.get("http://memory-layer-test/users/user-1/active-history").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    response = client.get("/user-history/user-1")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is None
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# GET /app-config — normal execution
# ---------------------------------------------------------------------------

def test_app_config_returns_200(client):
    response = client.get("/app-config")
    assert response.status_code == 200


def test_app_config_returns_ui_section_from_config():
    config_with_ui = {
        "agent_core_client": {"endpoint": "http://ac/process_turn", "timeout_s": 5.0},
        "memory_layer_client": {"endpoint": "http://ml", "timeout_s": 5.0},
        "reach_layer": {"web": {"title": "Test"}},
        "ui": {
            "app_name": "Test App",
            "app_icon": "🧪",
            "storage_key": "test_user",
        },
    }
    wr = WebReachLayer(config_with_ui)
    app = create_app(wr, config_with_ui)
    test_client = TestClient(app)
    response = test_client.get("/app-config")
    assert response.status_code == 200
    data = response.json()
    assert data["app_name"] == "Test App"
    assert data["app_icon"] == "🧪"
    assert data["storage_key"] == "test_user"


# ---------------------------------------------------------------------------
# GET /app-config — edge cases
# ---------------------------------------------------------------------------

def test_app_config_returns_empty_dict_when_no_ui_section(client):
    # The default test fixture config has no ui: key → should return {}
    response = client.get("/app-config")
    assert response.json() == {}


def test_app_config_response_is_json(client):
    response = client.get("/app-config")
    assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# OTel span — reach.inbound
# ---------------------------------------------------------------------------

@respx.mock
def test_handle_message_emits_reach_span(web_reach, config):
    """The /chat endpoint must emit a reach.inbound span."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace
    from unittest.mock import patch

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Patch get_tracer_provider so the handler uses our test provider directly.
    with patch("opentelemetry.trace.get_tracer", lambda name, **_kw: provider.get_tracer(name)):
        respx.post("http://agent-core-test/process_turn").mock(
            return_value=httpx.Response(200, json={"response_text": "Hello!", "was_escalated": False})
        )

        app = create_app(web_reach, config)
        test_client = TestClient(app)
        test_client.post("/chat", json={
            "session_id": "sess-otel",
            "user_id": "user-otel",
            "message": "ping",
        })

    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "reach.inbound" in span_names

    reach_span = next(s for s in spans if s.name == "reach.inbound")
    assert reach_span.attributes.get("session_id") == "sess-otel"
    assert reach_span.attributes.get("dpg.channel") == "web"
