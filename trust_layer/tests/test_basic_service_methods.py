"""
trust_layer/tests/test_basic_service_methods.py

Regression (#438): main.py wires BasicTrustLayer into create_app, so every route
server.py exposes must be backed by a method on BasicTrustLayer — not on
orchestrator.TrustLayer, which the deployment never serves.

Before this fix, /assemble_constraints returned HTTP 200 with EMPTY constraints
(the except branch's "fail-safe" default, so the endpoint looked healthy while
silently dropping every guardrail) and /consent/verify always returned
granted=False. escalate had the identical gap, fixed in #437.

These tests build the app the way main.py does. Asserting through
orchestrator.TrustLayer would pass against the broken code.
"""

from fastapi.testclient import TestClient

from server import create_app
from src.guardrails import BasicTrustLayer

CONSTRAINT = "MUST NOT guarantee or imply certainty about job outcomes"
DISCLOSURE = "Hiring decisions rest with the employer"


def _cfg() -> dict:
    return {
        "trust": {
            "policy_pack": "test_pack",
            "policy_packs": {
                "test_pack": {
                    "guardrails": {
                        "false_certainty": {
                            "severity": "blocker",
                            "failure_mode": "block",
                            "prompt_constraints": [CONSTRAINT],
                            "required_disclosures": [DISCLOSURE],
                            "refusal_template": "no guarantees",
                        }
                    }
                }
            },
            "consent": {
                "consent_phrases": ["yes", "haan", "हाँ"],
                "decline_phrases": ["no", "nahi", "नहीं"],
            },
            "consent_store": {"db_path": ":memory:"},
        }
    }


def _client() -> TestClient:
    return TestClient(create_app(BasicTrustLayer(_cfg())))


# ---- the methods exist on the object main.py actually serves ----------------

def test_basic_trust_layer_has_every_method_server_calls():
    trust = BasicTrustLayer(_cfg())
    for name in ("check_input", "check_output", "check_consent",
                 "assemble_constraints", "verify_consent", "escalate"):
        assert callable(getattr(trust, name, None)), f"BasicTrustLayer is missing {name}()"


# ---- /assemble_constraints --------------------------------------------------

def test_assemble_constraints_returns_the_configured_guardrail():
    resp = _client().post("/assemble_constraints", json={
        "session_id": "s1", "workflow_step": "job_match",
        "active_risks": ["false_certainty"], "user_segment": None,
    })
    assert resp.status_code == 200
    body = resp.json()
    # The pre-fix bug returned 200 with everything empty, so a status-only
    # assertion would have passed against the broken code.
    assert body["prompt_constraints"] == [CONSTRAINT], body
    assert body["required_disclosures"] == [DISCLOSURE], body


def test_assemble_constraints_empty_for_unknown_risk():
    resp = _client().post("/assemble_constraints", json={
        "session_id": "s1", "workflow_step": "job_match",
        "active_risks": ["no_such_risk"], "user_segment": None,
    })
    assert resp.status_code == 200
    assert resp.json()["prompt_constraints"] == []


def test_assemble_constraints_empty_when_no_risks_active():
    resp = _client().post("/assemble_constraints", json={
        "session_id": "s1", "workflow_step": "opening",
        "active_risks": [], "user_segment": None,
    })
    assert resp.status_code == 200
    assert resp.json()["prompt_constraints"] == []


# ---- /consent/verify --------------------------------------------------------

def test_verify_consent_grants_on_a_consent_phrase():
    resp = _client().post("/consent/verify", json={"session_id": "s1", "user_message": "haan"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is True


def test_verify_consent_grants_on_a_devanagari_consent_phrase():
    resp = _client().post("/consent/verify", json={"session_id": "s1", "user_message": "हाँ"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is True


def test_verify_consent_declines_and_stays_fail_closed_on_empty():
    client = _client()
    assert client.post("/consent/verify",
                       json={"session_id": "s1", "user_message": "nahi"}).json()["granted"] is False
    assert client.post("/consent/verify",
                       json={"session_id": "s2", "user_message": ""}).json()["granted"] is False


def test_verify_consent_persists_so_a_later_check_can_read_it():
    """Verifying without recording would leave the store empty for every later check."""
    trust = BasicTrustLayer(_cfg())
    assert trust.verify_consent("s-persist", "yes") is True
    assert trust._consent_store.has_consent("s-persist") is True
    assert trust._consent_store.has_consent("s-never-asked") is False
