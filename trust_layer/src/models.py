"""
trust_layer/src/models.py

Pydantic request and response models for all Trust Layer endpoints.

This module defines all request and response schemas used by the Trust Layer,
which is the safety gate for the DPG framework. These models enforce type
safety, validation, and clear contracts between Agent Core and the Trust Layer.
"""

from typing import Literal
from pydantic import BaseModel, Field


class InputCheckRequest(BaseModel):
    """Request body for POST /check/input.

    Represents a user message or system input that must be checked for
    safety violations before LLM processing.

    Attributes:
        session_id: Unique identifier for the conversation session.
        message: The raw user message or input text to be checked.
        active_risks: Optional list of risk categories to check (e.g., 'false_certainty').
    """

    session_id: str
    message: str
    active_risks: list[str] | None = None


class OutputCheckRequest(BaseModel):
    """Request body for POST /check/output.

    Represents an LLM-generated response that must be checked for safety
    violations before delivery to the user.

    Attributes:
        session_id: Unique identifier for the conversation session.
        response: The LLM-generated response text to be checked.
    """

    session_id: str
    response: str


class ConsentCheckRequest(BaseModel):
    """Request body for POST /check/consent (connector-level).

    Checks if a specific connector (write/identity action) has user consent.

    Attributes:
        session_id: Unique identifier for the conversation session.
        connector_name: The name of the connector requiring consent check.
    """

    session_id: str
    connector_name: str


class TrustCheckResponse(BaseModel):
    """Response for /check/input and /check/output.

    Indicates whether a check passed and what action should be taken.

    Attributes:
        passed: True if the check passed, False if it failed.
        action: Action to take: 'allow', 'block', or 'escalate'.
        reason: Optional explanation of why the check passed or failed.
    """

    passed: bool
    action: Literal["allow", "block", "escalate"]
    reason: str | None = None


class ConsentResponse(BaseModel):
    """Response for POST /check/consent.

    Indicates whether the user has granted consent for a specific connector.

    Attributes:
        granted: True if consent is granted, False otherwise.
    """

    granted: bool


class AssembleConstraintsRequest(BaseModel):
    """Request body for POST /assemble_constraints.

    Assembles guardrail constraints based on workflow context, user segment,
    and active risk categories.

    Attributes:
        session_id: Unique identifier for the conversation session.
        workflow_step: Current step in the workflow (e.g., 'ready', 'input_check').
        active_risks: List of active risk categories to constrain against.
        user_segment: Optional user segment or role (e.g., 'labour', 'vulnerable').
    """

    session_id: str
    workflow_step: str
    active_risks: list[str]
    user_segment: str | None = None


class GuardrailConstraints(BaseModel):
    """Response for POST /assemble_constraints.

    Contains all guardrail constraints to be enforced during the turn.

    Attributes:
        prompt_constraints: List of constraints to inject into the LLM prompt.
        required_disclosures: List of required disclosure messages.
        action_gates: Dict of action names to permission bools (write/identity gates).
        refusal_templates: Dict of refusal template names to template text.
    """

    prompt_constraints: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    action_gates: dict[str, bool] = Field(default_factory=dict)
    refusal_templates: dict[str, str] = Field(default_factory=dict)


class ConsentVerifyRequest(BaseModel):
    """Request body for POST /consent/verify.

    Verifies that a user message contains sufficient consent signal for a
    write or identity action.

    Attributes:
        session_id: Unique identifier for the conversation session.
        user_message: The user's message that may contain consent signal.
    """

    session_id: str
    user_message: str


class ConsentVerifyResponse(BaseModel):
    """Response for POST /consent/verify.

    Indicates whether the user message contains sufficient consent.

    Attributes:
        granted: True if consent is verified, False otherwise.
    """

    granted: bool


class HiTLEscalateRequest(BaseModel):
    """Request body for POST /escalate.

    Escalates a turn to a human-in-the-loop queue for review and intervention.

    Attributes:
        session_id: Unique identifier for the conversation session.
        escalation_reason: Reason for escalation (e.g., 'consent_needed').
        user_message: The user's message that triggered escalation.
        workflow_step: The workflow step at which escalation occurred.
    """

    session_id: str
    escalation_reason: str
    user_message: str
    workflow_step: str


class HiTLEscalateResponse(BaseModel):
    """Response for POST /escalate.

    Confirms escalation and provides a holding message and ticket ID.

    Attributes:
        queued: True if the escalation was successfully queued.
        ticket_id: Unique identifier for the escalation ticket.
        holding_message: Message to send to the user while the ticket is pending.
    """

    queued: bool
    ticket_id: str
    holding_message: str


class StatusResponse(BaseModel):
    """Response for GET /health.

    Health check response indicating service status.

    Attributes:
        status: Service status (e.g., 'healthy', 'unhealthy').
    """

    status: str
