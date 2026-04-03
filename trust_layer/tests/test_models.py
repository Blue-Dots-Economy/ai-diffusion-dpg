"""Test suite for Pydantic models in trust_layer.src.models."""

from trust_layer.src.models import (
    InputCheckRequest,
    AssembleConstraintsRequest,
    GuardrailConstraints,
    ConsentVerifyRequest,
    ConsentVerifyResponse,
    HiTLEscalateRequest,
    HiTLEscalateResponse,
)


def test_input_check_request_with_risks():
    """Test InputCheckRequest with active_risks specified."""
    r = InputCheckRequest(session_id="s1", message="hello", active_risks=["false_certainty"])
    assert r.active_risks == ["false_certainty"]


def test_input_check_request_no_risks():
    """Test InputCheckRequest with default active_risks."""
    r = InputCheckRequest(session_id="s1", message="hello")
    assert r.active_risks is None


def test_assemble_constraints_request():
    """Test AssembleConstraintsRequest with all fields."""
    r = AssembleConstraintsRequest(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty"],
        user_segment="labour",
    )
    assert r.user_segment == "labour"


def test_guardrail_constraints_defaults():
    """Test GuardrailConstraints with default values."""
    g = GuardrailConstraints()
    assert g.prompt_constraints == []
    assert g.required_disclosures == []
    assert g.action_gates == {}
    assert g.refusal_templates == {}


def test_consent_verify_response():
    """Test ConsentVerifyResponse with granted=True."""
    r = ConsentVerifyResponse(granted=True)
    assert r.granted is True


def test_hitl_escalate_response():
    """Test HiTLEscalateResponse with all fields."""
    r = HiTLEscalateResponse(queued=True, ticket_id="TKT-001", holding_message="Wait...")
    assert r.queued is True
