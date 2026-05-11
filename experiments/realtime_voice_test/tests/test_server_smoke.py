"""Smoke tests for server.py: HTTP endpoints only.

The WebSocket /ws/{call_sid} endpoint requires real Vobiz + OpenAI
connections, so it's exercised by manual end-to-end calls (Task 9).
"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Build the FastAPI app with required env vars stubbed."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-stub")
    monkeypatch.setenv("PUBLIC_URL", "https://test.ngrok-free.app")
    from server import create_app
    return TestClient(create_app())


def test_health_returns_ok(client):
    """GET /health returns 200 with {'status': 'ok'}."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_answer_returns_stream_xml(client):
    """POST /answer returns Vobiz Stream XML with the WS URL."""
    r = client.post(
        "/answer",
        data={"CallUUID": "call-test-123", "From": "+919999999999"},
    )
    assert r.status_code == 200
    body = r.text
    assert "<Response>" in body
    assert "<Stream" in body
    assert "wss://test.ngrok-free.app/ws/call-test-123" in body


def test_answer_with_callsid_field(client):
    """POST /answer accepts CallSid (capital-S) as alternative to CallUUID."""
    r = client.post("/answer", data={"CallSid": "alt-id", "From": "+919"})
    assert r.status_code == 200
    assert "wss://test.ngrok-free.app/ws/alt-id" in r.text


def test_answer_missing_callid_defaults_to_unknown(client):
    """If neither CallUUID nor CallSid is sent, server falls back to 'unknown'."""
    r = client.post("/answer", data={"From": "+919"})
    assert r.status_code == 200
    assert "wss://test.ngrok-free.app/ws/unknown" in r.text
