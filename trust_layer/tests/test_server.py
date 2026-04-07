"""
trust_layer/tests/test_server.py

Unit tests for the Trust Layer FastAPI server (src/server.py).
Uses FastAPI TestClient — no real HTTP calls made.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from server import create_app
from orchestrator import TrustLayer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "trust": {
        "policy_pack": "",
        "input_rules": {
            "blocked_phrases": ["kill", "bomb"],
            "escalation_topics": ["suicide", "self harm"],
        },
        "output_rules": {
            "blocked_phrases": ["confidential"],
        },
        "policy_packs": {},
        "consent": {"consent_phrases": [], "decline_phrases": []},
        "hitl": {"queue_backend": "log", "holding_message": "", "notification_webhook": None},
    }
}


@pytest.fixture
def trust():
    return TrustLayer(MINIMAL_CONFIG)


@pytest.fixture
def client(trust):
    app = create_app(trust)
    return TestClient(app)


# ---------------------------------------------------------------------------
# create_app validation
# ---------------------------------------------------------------------------

def test_create_app_none_raises():
    with pytest.raises(ValueError, match="trust must not be None"):
        create_app(None)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /check/input
# ---------------------------------------------------------------------------

def test_check_input_clean_message_returns_allow(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": "kaam chahiye Hubli mein"})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is True
    assert data["action"] == "allow"


def test_check_input_blocked_phrase_returns_block(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": "I will kill you"})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is False
    assert data["action"] == "block"
    assert data["reason"] is not None


def test_check_input_escalation_topic_returns_escalate(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": "I am thinking about suicide"})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is False
    assert data["action"] == "escalate"


def test_check_input_empty_message_returns_allow(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": ""})
    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_check_input_exception_fails_closed():
    mock_trust = MagicMock()
    mock_trust.check_input.side_effect = RuntimeError("trust error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    response = tc.post("/check/input", json={"session_id": "s1", "message": "hello"})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is False
    assert data["action"] == "block"


def test_check_input_response_has_required_keys(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": "test"})
    data = response.json()
    assert "passed" in data
    assert "action" in data


# ---------------------------------------------------------------------------
# POST /check/output
# ---------------------------------------------------------------------------

def test_check_output_clean_response_returns_allow(client):
    response = client.post("/check/output", json={"session_id": "s1", "response": "Electrician jobs in Hubli."})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is True
    assert data["action"] == "allow"


def test_check_output_blocked_phrase_returns_block(client):
    response = client.post("/check/output", json={"session_id": "s1", "response": "This is confidential data."})
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is False
    assert data["action"] == "block"


def test_check_output_empty_response_returns_allow(client):
    response = client.post("/check/output", json={"session_id": "s1", "response": ""})
    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_check_output_exception_fails_closed():
    mock_trust = MagicMock()
    mock_trust.check_output.side_effect = RuntimeError("output error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    response = tc.post("/check/output", json={"session_id": "s1", "response": "hello"})
    assert response.status_code == 200
    assert response.json()["passed"] is False
    assert response.json()["action"] == "block"


def test_check_output_reason_none_for_clean_response(client):
    response = client.post("/check/output", json={"session_id": "s2", "response": "Good answer."})
    assert response.json().get("reason") is None


# ---------------------------------------------------------------------------
# POST /check/consent
# ---------------------------------------------------------------------------

def test_check_consent_returns_granted(client):
    # No consent recorded for this session yet — check_consent is backed by
    # the SQLite consent store and returns False until verify_consent is called.
    response = client.post("/check/consent", json={"session_id": "s1", "connector_name": "job_apply"})
    assert response.status_code == 200
    assert response.json()["granted"] is False


def test_check_consent_exception_fails_closed():
    mock_trust = MagicMock()
    mock_trust.check_consent.side_effect = RuntimeError("consent error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    response = tc.post("/check/consent", json={"session_id": "s1", "connector_name": "job_apply"})
    assert response.status_code == 200
    assert response.json()["granted"] is False


def test_check_consent_response_has_granted_key(client):
    response = client.post("/check/consent", json={"session_id": "s1", "connector_name": "any_connector"})
    assert "granted" in response.json()


# ---------------------------------------------------------------------------
# Blocked phrase is case-insensitive
# ---------------------------------------------------------------------------

def test_check_input_block_is_case_insensitive(client):
    response = client.post("/check/input", json={"session_id": "s1", "message": "BOMB THREAT"})
    assert response.json()["action"] == "block"


def test_check_output_block_is_case_insensitive(client):
    response = client.post("/check/output", json={"session_id": "s1", "response": "CONFIDENTIAL info here"})
    assert response.json()["action"] == "block"


# ---------------------------------------------------------------------------
# New endpoints: /assemble_constraints, /consent/verify, /escalate
# ---------------------------------------------------------------------------

FULL_CONFIG = {
    "trust": {
        "policy_pack": "kkb_advisory_jobs",
        "input_rules": {
            "blocked_phrases": ["bomb"],
            "escalation_topics": ["suicide"],
            "blocked_input_message": "Cannot help.",
        },
        "output_rules": {
            "blocked_phrases": ["guaranteed placement"],
            "output_blocked_message": "Bad output.",
        },
        "policy_packs": {
            "kkb_advisory_jobs": {
                "risks": ["false_certainty"],
                "guardrails": {
                    "false_certainty": {
                        "id": "GR-001",
                        "severity": "blocker",
                        "failure_mode": "block",
                        "prompt_constraints": ["MUST NOT guarantee outcomes"],
                        "required_disclosures": ["Hiring decisions rest with employer"],
                        "refusal_template": "Main guarantee nahi de sakta.",
                    }
                },
            }
        },
        "consent": {
            "consent_phrases": ["haan", "yes"],
            "decline_phrases": ["nahi", "no"],
        },
        "hitl": {
            "queue_backend": "log",
            "holding_message": "Advisor ko connect kar rahe hain.",
            "notification_webhook": None,
        },
    }
}


def test_assemble_constraints_known_risk():
    from orchestrator import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/assemble_constraints", json={
        "session_id": "s1",
        "workflow_step": "ready",
        "active_risks": ["false_certainty"],
        "user_segment": None,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "MUST NOT guarantee outcomes" in data["prompt_constraints"]
    assert data["refusal_templates"]["false_certainty"] == "Main guarantee nahi de sakta."


def test_assemble_constraints_empty_risks():
    from orchestrator import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/assemble_constraints", json={
        "session_id": "s1",
        "workflow_step": "ready",
        "active_risks": [],
        "user_segment": None,
    })
    assert resp.status_code == 200
    assert resp.json()["prompt_constraints"] == []


def test_consent_verify_granted():
    from orchestrator import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/consent/verify", json={"session_id": "s1", "user_message": "haan"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is True


def test_consent_verify_denied():
    from orchestrator import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/consent/verify", json={"session_id": "s1", "user_message": "nahi"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is False


def test_escalate_returns_ticket():
    from orchestrator import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/escalate", json={
        "session_id": "s1",
        "escalation_reason": "escalation_topic:suicide",
        "user_message": "pareshaan hoon",
        "workflow_step": "ready",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] is True
    assert data["holding_message"] == "Advisor ko connect kar rahe hain."
    assert data["ticket_id"].startswith("TKT-")


# ── New endpoint error-path (fail-closed) tests ─────────────────────────────

def test_assemble_constraints_exception_returns_empty():
    from unittest.mock import MagicMock
    mock_trust = MagicMock()
    mock_trust.assemble_constraints.side_effect = RuntimeError("error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    resp = tc.post("/assemble_constraints", json={
        "session_id": "s1", "workflow_step": "ready", "active_risks": [], "user_segment": None
    })
    assert resp.status_code == 200
    assert resp.json()["prompt_constraints"] == []


def test_consent_verify_exception_returns_false():
    from unittest.mock import MagicMock
    mock_trust = MagicMock()
    mock_trust.verify_consent.side_effect = RuntimeError("error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    resp = tc.post("/consent/verify", json={"session_id": "s1", "user_message": "haan"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is False  # fail-closed


def test_escalate_exception_returns_not_queued():
    from unittest.mock import MagicMock
    mock_trust = MagicMock()
    mock_trust.escalate.side_effect = RuntimeError("error")
    app = create_app(mock_trust)
    tc = TestClient(app)
    resp = tc.post("/escalate", json={
        "session_id": "s1", "escalation_reason": "r", "user_message": "m", "workflow_step": "ready"
    })
    assert resp.status_code == 200
    assert resp.json()["queued"] is False  # fail-closed


# ---------------------------------------------------------------------------
# OTel span instrumentation tests
# ---------------------------------------------------------------------------

def test_check_input_emits_trust_span():
    """POST /check/input must produce a trust.input_check span."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace
    from dpg_telemetry import _reset_for_testing

    _reset_for_testing()

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    tl = TrustLayer(MINIMAL_CONFIG)
    tc = TestClient(create_app(tl))
    response = tc.post("/check/input", json={"session_id": "s1", "message": "hello"})
    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "trust.input_check" in span_names
    input_check_span = next(s for s in spans if s.name == "trust.input_check")
    assert input_check_span.attributes.get("session_id") == "s1"
    assert input_check_span.attributes.get("trust.action") is not None


def test_check_output_emits_trust_span():
    """POST /check/output must produce a trust.output_check span."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace
    from dpg_telemetry import _reset_for_testing

    _reset_for_testing()

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    tl = TrustLayer(MINIMAL_CONFIG)
    tc = TestClient(create_app(tl))
    response = tc.post("/check/output", json={"session_id": "s1", "response": "Good answer."})
    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "trust.output_check" in span_names
    output_check_span = next(s for s in spans if s.name == "trust.output_check")
    assert output_check_span.attributes.get("session_id") == "s1"
    assert output_check_span.attributes.get("trust.action") is not None
