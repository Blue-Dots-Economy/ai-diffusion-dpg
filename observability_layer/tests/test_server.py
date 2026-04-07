"""
observability_layer/tests/test_server.py

Unit tests for the Observability Layer FastAPI server (src/server.py).
Uses FastAPI TestClient — no real HTTP calls made.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from server import create_app
from schema.config import ObservabilityConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TRUST_ALLOW = {"passed": True, "action": "allow", "reason": None}

_VALID_TURN_PAYLOAD = {
    "session_id": "sess-obs-1",
    "turn_id": "t1",
    "trace_id": "abc",
    "response_text": "Hubli mein electrician kaam milta hai.",
    "tool_calls": [],
    "trust_input_result": _TRUST_ALLOW,
    "trust_output_result": _TRUST_ALLOW,
    "model_used": "claude-haiku-4-5-20251001",
    "input_tokens": 120,
    "output_tokens": 80,
    "latency_ms": 450,
    "timestamp_ms": 1700000000000,
}


def _make_layer():
    with patch("otel_observability_layer.get_tracer", return_value=MagicMock()), \
         patch("otel_observability_layer.get_meter", return_value=MagicMock()):
        from otel_observability_layer import OtelObservabilityLayer
        return OtelObservabilityLayer({})


def _make_obs_config():
    return ObservabilityConfig.from_config({})


@pytest.fixture
def observability():
    return _make_layer()


@pytest.fixture
def obs_config():
    return _make_obs_config()


@pytest.fixture
def client(observability, obs_config):
    app = create_app(observability, obs_config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_app validation
# ---------------------------------------------------------------------------

def test_create_app_none_observability_raises():
    with pytest.raises(ValueError, match="observability must not be None"):
        create_app(None, _make_obs_config())


def test_create_app_none_obs_config_raises():
    with pytest.raises(ValueError, match="obs_config must not be None"):
        create_app(_make_layer(), None)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /validate-config
# ---------------------------------------------------------------------------

def test_validate_config_returns_domain(client):
    response = client.get("/validate-config")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "domain" in body


# ---------------------------------------------------------------------------
# POST /emit/turn
# ---------------------------------------------------------------------------

def test_emit_turn_returns_ok(client):
    response = client.post("/emit/turn", json=_VALID_TURN_PAYLOAD)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_emit_turn_with_tool_calls_returns_ok(client):
    payload = dict(_VALID_TURN_PAYLOAD)
    payload["tool_calls"] = [
        {"tool_name": "onest_market_lookup", "tool_use_id": "tu_1", "input_params": {"location": "Hubli"}}
    ]
    response = client.post("/emit/turn", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_emit_turn_empty_tool_calls_returns_ok(client):
    payload = dict(_VALID_TURN_PAYLOAD)
    payload["tool_calls"] = []
    response = client.post("/emit/turn", json=payload)
    assert response.status_code == 200


def test_emit_turn_calls_observability_emit_turn(observability, obs_config):
    observability.emit_turn = MagicMock()
    app = create_app(observability, obs_config)
    tc = TestClient(app)
    tc.post("/emit/turn", json=_VALID_TURN_PAYLOAD)
    observability.emit_turn.assert_called_once()


def test_emit_turn_exception_still_returns_ok(obs_config):
    mock_obs = MagicMock()
    mock_obs.emit_turn.side_effect = RuntimeError("emit error")
    app = create_app(mock_obs, obs_config)
    tc = TestClient(app)
    response = tc.post("/emit/turn", json=_VALID_TURN_PAYLOAD)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_emit_turn_trust_block_action_returns_ok(client):
    payload = dict(_VALID_TURN_PAYLOAD)
    payload["trust_input_result"] = {"passed": False, "action": "block", "reason": "harmful"}
    response = client.post("/emit/turn", json=payload)
    assert response.status_code == 200


def test_emit_turn_zero_tokens_returns_ok(client):
    payload = dict(_VALID_TURN_PAYLOAD)
    payload["input_tokens"] = 0
    payload["output_tokens"] = 0
    response = client.post("/emit/turn", json=payload)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /emit/signal
# ---------------------------------------------------------------------------

def test_emit_signal_returns_ok(client):
    response = client.post("/emit/signal", json={"signal_type": "low_confidence", "data": {"confidence": 0.3}})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_emit_signal_empty_data_returns_ok(client):
    response = client.post("/emit/signal", json={"signal_type": "drop_off", "data": {}})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_emit_signal_calls_observability_emit_signal(observability, obs_config):
    observability.emit_signal = MagicMock()
    app = create_app(observability, obs_config)
    tc = TestClient(app)
    tc.post("/emit/signal", json={"signal_type": "escalation_triggered", "data": {"reason": "topic"}})
    observability.emit_signal.assert_called_once_with("escalation_triggered", {"reason": "topic"})


def test_emit_signal_exception_still_returns_ok(obs_config):
    mock_obs = MagicMock()
    mock_obs.emit_signal.side_effect = RuntimeError("signal error")
    app = create_app(mock_obs, obs_config)
    tc = TestClient(app)
    response = tc.post("/emit/signal", json={"signal_type": "test", "data": {}})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_emit_signal_various_signal_types_return_ok(client):
    for sig in ["drop_off", "mismatch", "low_confidence", "escalation_triggered", "feedback"]:
        response = client.post("/emit/signal", json={"signal_type": sig, "data": {}})
        assert response.status_code == 200
