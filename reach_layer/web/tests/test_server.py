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
    assert "trouble connecting to the ai service" in response.json()["response_text"].lower()


@respx.mock
def test_chat_agent_core_connect_error_returns_safe_message(client):
    respx.post("http://agent-core-test/process_turn").mock(
        side_effect=httpx.ConnectError("refused")
    )
    response = client.post("/chat", json={"session_id": "sess-1", "message": "hi"})
    assert response.status_code == 200
    assert "trouble connecting to the ai service" in response.json()["response_text"].lower()


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

def test_app_config_returns_only_auth_block_when_no_ui_section(client):
    # The default test fixture config has no ui: key. /app-config now
    # always includes the public auth block, so the response should be
    # exactly {"auth": {"enabled": False, "google_client_id": ""}}.
    response = client.get("/app-config")
    assert response.json() == {"auth": {"enabled": False, "google_client_id": ""}}


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


# ---------------------------------------------------------------------------
# Auth endpoints (auth.enabled = true)
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"
SESSION_SECRET = "s" * 48


@pytest.fixture
def auth_config():
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
        "auth": {
            "enabled": True,
            "google_client_id": GOOGLE_CLIENT_ID,
            "session_cookie_name": "reach_session",
            "session_ttl_s": 3600,
            "cookie_secure": False,  # TestClient http://
            "cookie_samesite": "lax",
        },
    }


@pytest.fixture
def auth_client(auth_config, monkeypatch):
    monkeypatch.setenv("REACH_SESSION_SECRET", SESSION_SECRET)
    wr = WebReachLayer(auth_config)
    app = create_app(wr, auth_config)
    return TestClient(app)


def _mock_google_identity():
    """Patch Google ID token verification to return a fixed identity."""
    from src.auth import GoogleIdentity
    return patch(
        "server.verify_google_id_token",
        return_value=GoogleIdentity(
            sub="100",
            email="alice@example.com",
            name="Alice",
            picture="https://example.com/a.png",
        ),
    )


def test_auth_disabled_endpoints_return_404(client):
    """When auth.enabled is false, /auth/google and /auth/me return 404."""
    r = client.post("/auth/google", json={"credential": "x"})
    assert r.status_code == 404
    r = client.get("/auth/me")
    assert r.status_code == 404


def test_create_app_raises_when_auth_enabled_without_secret(auth_config, monkeypatch):
    """Startup must fail loud if REACH_SESSION_SECRET is missing."""
    monkeypatch.delenv("REACH_SESSION_SECRET", raising=False)
    wr = WebReachLayer(auth_config)
    with pytest.raises(RuntimeError, match="REACH_SESSION_SECRET"):
        create_app(wr, auth_config)


def test_create_app_raises_when_auth_enabled_without_client_id(auth_config, monkeypatch):
    """Startup must fail loud if google_client_id is missing."""
    monkeypatch.setenv("REACH_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    auth_config["auth"]["google_client_id"] = ""
    wr = WebReachLayer(auth_config)
    with pytest.raises(RuntimeError, match="google_client_id"):
        create_app(wr, auth_config)


def test_app_config_exposes_public_auth_block(auth_client):
    r = auth_client.get("/app-config")
    assert r.status_code == 200
    data = r.json()
    assert data["auth"]["enabled"] is True
    assert data["auth"]["google_client_id"] == GOOGLE_CLIENT_ID


def test_auth_google_sets_cookie_and_returns_identity(auth_client):
    with _mock_google_identity():
        r = auth_client.post("/auth/google", json={"credential": "valid-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "google:100"
    assert body["display_name"] == "Alice"
    assert body["email"] == "alice@example.com"
    assert "reach_session" in r.cookies


def test_auth_google_invalid_token_returns_401(auth_client):
    with patch(
        "server.verify_google_id_token",
        side_effect=AuthErrorImport(),
    ):
        r = auth_client.post("/auth/google", json={"credential": "bad"})
    assert r.status_code == 401


def AuthErrorImport():
    from src.auth import AuthError, Reason
    return AuthError(Reason.INVALID, "bad")


def test_auth_me_without_cookie_returns_401(auth_client):
    r = auth_client.get("/auth/me")
    assert r.status_code == 401


def test_auth_me_with_cookie_returns_identity(auth_client):
    with _mock_google_identity():
        login = auth_client.post("/auth/google", json={"credential": "valid-token"})
    assert login.status_code == 200
    r = auth_client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["user_id"] == "google:100"


def test_auth_logout_clears_cookie(auth_client):
    with _mock_google_identity():
        auth_client.post("/auth/google", json={"credential": "valid-token"})
    r = auth_client.post("/auth/logout")
    assert r.status_code == 200
    # Subsequent /auth/me must now 401
    auth_client.cookies.clear()
    r2 = auth_client.get("/auth/me")
    assert r2.status_code == 401


# ---------------------------------------------------------------------------
# /chat and /user-history are gated by cookie when auth.enabled
# ---------------------------------------------------------------------------

def test_chat_requires_session_when_auth_enabled(auth_client):
    r = auth_client.post("/chat", json={"session_id": "s1", "message": "hi"})
    assert r.status_code == 401


def test_user_history_requires_session_when_auth_enabled(auth_client):
    r = auth_client.get("/user-history/anyone")
    assert r.status_code == 401


@respx.mock
def test_chat_with_session_overrides_user_id_with_cookie(auth_client):
    """user_id from request body must be ignored; cookie is authoritative."""
    route = respx.post("http://agent-core-test/process_turn").mock(
        return_value=httpx.Response(200, json={"response_text": "ok"})
    )
    with _mock_google_identity():
        auth_client.post("/auth/google", json={"credential": "valid-token"})
    auth_client.post(
        "/chat",
        json={"session_id": "sess-1", "user_id": "attacker", "message": "hi"},
    )
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["user_id"] == "google:100"


@respx.mock
def test_user_history_with_session_uses_cookie_user_id(auth_client):
    route = respx.get("http://memory-layer-test/users/google:100/active-history").mock(
        return_value=httpx.Response(200, json={"session_id": "sX", "turns": []})
    )
    with _mock_google_identity():
        auth_client.post("/auth/google", json={"credential": "valid-token"})
    # Path param "anyone" should be ignored — cookie's google:100 is used.
    r = auth_client.get("/user-history/anyone")
    assert r.status_code == 200
    assert r.json()["session_id"] == "sX"
    assert route.called


# ---------------------------------------------------------------------------
# Fixtures for upload proxy tests — need env vars set
# ---------------------------------------------------------------------------

import os
import respx as _respx_module
import httpx as _httpx


@pytest.fixture
def upload_client(config, web_reach):
    """TestClient with upload proxy env vars set."""
    os.environ["DEVKIT_TO_REACH_API_KEY"] = "devkit-key-test"
    os.environ["REACH_TO_KE_API_KEY"] = "ke-key-test"
    os.environ["KE_INTERNAL_URL"] = "http://ke-test:8001"

    from server import create_app
    test_app = create_app(web_reach, config)
    client = TestClient(test_app)
    yield client

    os.environ.pop("DEVKIT_TO_REACH_API_KEY", None)
    os.environ.pop("REACH_TO_KE_API_KEY", None)
    os.environ.pop("KE_INTERNAL_URL", None)


# ---------------------------------------------------------------------------
# POST /ingest/upload
# ---------------------------------------------------------------------------

class TestIngestUploadProxy:
    @respx.mock
    def test_proxies_to_ke_and_returns_response(self, upload_client):
        ke_response = {"batch_id": "b1", "jobs": [{"filename": "doc.pdf", "job_id": "j1"}]}
        respx.post("http://ke-test:8001/upload").mock(
            return_value=_httpx.Response(200, json=ke_response)
        )

        response = upload_client.post(
            "/ingest/upload",
            content=b"--boundary\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n[]\r\n--boundary--",
            headers={
                "X-API-Key": "devkit-key-test",
                "Content-Type": "multipart/form-data; boundary=boundary",
            },
        )
        assert response.status_code == 200
        assert response.json()["batch_id"] == "b1"

    def test_missing_api_key_returns_401(self, upload_client):
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 401

    def test_wrong_api_key_returns_401(self, upload_client):
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"X-API-Key": "wrong-key", "Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 401

    @respx.mock
    def test_ke_error_propagated(self, upload_client):
        respx.post("http://ke-test:8001/upload").mock(
            return_value=_httpx.Response(429, json={"detail": "Queue full"})
        )
        response = upload_client.post(
            "/ingest/upload",
            content=b"body",
            headers={"X-API-Key": "devkit-key-test", "Content-Type": "multipart/form-data; boundary=b"},
        )
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# GET /ingest/job/{job_id}
# ---------------------------------------------------------------------------

class TestIngestJobProxy:
    @respx.mock
    def test_proxies_job_status_from_ke(self, upload_client):
        job_response = {"job_id": "j1", "status": "ingested", "chunks_added": 42}
        respx.get("http://ke-test:8001/upload/job/j1").mock(
            return_value=_httpx.Response(200, json=job_response)
        )

        response = upload_client.get(
            "/ingest/job/j1",
            headers={"X-API-Key": "devkit-key-test"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ingested"

    def test_missing_api_key_returns_401(self, upload_client):
        response = upload_client.get("/ingest/job/j1")
        assert response.status_code == 401

    @respx.mock
    def test_ke_404_propagated(self, upload_client):
        respx.get("http://ke-test:8001/upload/job/nonexistent").mock(
            return_value=_httpx.Response(404, json={"detail": "Not found"})
        )
        response = upload_client.get(
            "/ingest/job/nonexistent",
            headers={"X-API-Key": "devkit-key-test"},
        )
        assert response.status_code == 404


from server import create_routing_only_app


# ---------------------------------------------------------------------------
# Fixture for routing_only mode
# ---------------------------------------------------------------------------

@pytest.fixture
def client_routing_only(config):
    """TestClient for routing_only mode — no WebReachLayer needed."""
    app = create_routing_only_app(config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# routing_only mode — routes that MUST work
# ---------------------------------------------------------------------------

def test_routing_only_health(client_routing_only):
    res = client_routing_only.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@respx.mock
def test_routing_only_ingest_upload_proxies_to_ke(client_routing_only, monkeypatch):
    monkeypatch.setenv("DEVKIT_TO_REACH_API_KEY", "test-devkit-key")
    monkeypatch.setenv("KE_INTERNAL_URL", "http://ke-test")
    respx.post("http://ke-test/upload").mock(
        return_value=httpx.Response(200, json={"job_id": "j1"})
    )
    res = client_routing_only.post(
        "/ingest/upload",
        headers={"X-API-Key": "test-devkit-key", "Content-Type": "multipart/form-data; boundary=x"},
        content=b"--x\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\ndata\r\n--x--",
    )
    assert res.status_code == 200


@respx.mock
def test_routing_only_ingest_job_proxies_to_ke(client_routing_only, monkeypatch):
    monkeypatch.setenv("DEVKIT_TO_REACH_API_KEY", "test-devkit-key")
    monkeypatch.setenv("KE_INTERNAL_URL", "http://ke-test")
    respx.get("http://ke-test/upload/job/job-123").mock(
        return_value=httpx.Response(200, json={"status": "complete"})
    )
    res = client_routing_only.get(
        "/ingest/job/job-123",
        headers={"X-API-Key": "test-devkit-key"},
    )
    assert res.status_code == 200


@respx.mock
def test_routing_only_ingest_jobs_proxies_to_ke(client_routing_only, monkeypatch):
    monkeypatch.setenv("DEVKIT_TO_REACH_API_KEY", "test-devkit-key")
    monkeypatch.setenv("KE_INTERNAL_URL", "http://ke-test")
    respx.get("http://ke-test/upload/jobs").mock(
        return_value=httpx.Response(200, json=[])
    )
    res = client_routing_only.get(
        "/ingest/jobs",
        headers={"X-API-Key": "test-devkit-key"},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# routing_only mode — web/auth/chat/session routes must NOT exist (404)
# ---------------------------------------------------------------------------

def test_routing_only_root_is_404(client_routing_only):
    assert client_routing_only.get("/").status_code == 404


def test_routing_only_chat_is_404(client_routing_only):
    assert client_routing_only.post("/chat", json={}).status_code == 404


def test_routing_only_app_config_is_404(client_routing_only):
    assert client_routing_only.get("/app-config").status_code == 404


def test_routing_only_auth_google_is_404(client_routing_only):
    assert client_routing_only.post("/auth/google", json={"credential": "x"}).status_code == 404


def test_routing_only_sessions_is_404(client_routing_only):
    assert client_routing_only.get("/sessions").status_code == 404


def test_routing_only_user_history_is_404(client_routing_only):
    assert client_routing_only.get("/user-history/user-1").status_code == 404


# ---------------------------------------------------------------------------
# routing_only mode — no auth env vars required at startup
# ---------------------------------------------------------------------------

def test_routing_only_boots_without_google_client_id(config, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("REACH_SESSION_SECRET", raising=False)
    app = create_routing_only_app(config)   # must not raise
    client = TestClient(app)
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Full mode — existing behaviour preserved (smoke test)
# ---------------------------------------------------------------------------

def test_full_mode_health_still_works(client):
    assert client.get("/health").status_code == 200
