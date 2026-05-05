"""Domain schemas for trust_layer block.

Sections written by the LLM during the trust phase, plus the policy_pack /
policy_packs system used by trust_layer.guardrails.assemble_constraints to
inject prompt_constraints + required_disclosures into the LLM system prompt
every turn. Without policy_packs populated, no safety constraints reach the
LLM — silent safety degradation.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import (
    TrustQueueBackend, DignityFailAction,
    GuardrailSeverity, GuardrailFailureMode,
)


class ConsentConfig(BaseModel):
    """DPDP Act consent phrases used by Trust Layer's consent block."""
    model_config = ConfigDict(extra="forbid")
    consent_phrases: list[str] = Field(default_factory=list)
    decline_phrases: list[str] = Field(default_factory=list)


class HitlConfig(BaseModel):
    """Human-in-the-loop handoff settings.

    queue_backend excludes 'memory' — runtime crashes on it.
    Valid backends: log (dev), redis (prod queue), webhook (external HITL).
    notification_webhook is read but not yet dispatched (GH-36).
    """
    model_config = ConfigDict(extra="forbid")
    holding_message: str = Field(..., min_length=1)
    queue_backend: TrustQueueBackend = TrustQueueBackend.log
    notification_webhook: Optional[str] = None


class InputRulesConfig(BaseModel):
    """Input safety rules — blocked phrases + escalation topics."""
    model_config = ConfigDict(extra="forbid")
    blocked_phrases: list[str] = Field(default_factory=list)
    blocked_input_message: str = Field(..., min_length=1)
    escalation_topics: list[str] = Field(default_factory=list)


class OutputRulesConfig(BaseModel):
    """Output safety rules — phrases the LLM output must not contain."""
    model_config = ConfigDict(extra="forbid")
    blocked_phrases: list[str] = Field(default_factory=list)
    output_blocked_message: str = Field(..., min_length=1)


class GuardrailConfig(BaseModel):
    """One named guardrail.

    prompt_constraints + required_disclosures are actively injected into the
    LLM system prompt every turn via trust_layer.guardrails.assemble_constraints.
    severity + failure_mode are validated but not yet runtime-enforced (GH-170).
    refusal_template is the fixed text shown when failure_mode='block' fires.
    """
    model_config = ConfigDict(extra="forbid")
    severity: GuardrailSeverity = GuardrailSeverity.warning
    failure_mode: GuardrailFailureMode = GuardrailFailureMode.constrain
    prompt_constraints: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    refusal_template: Optional[str] = Field(default=None, min_length=1)


class PolicyPackConfig(BaseModel):
    """A named policy pack — collection of guardrails keyed by risk name."""
    model_config = ConfigDict(extra="forbid")
    guardrails: dict[str, GuardrailConfig] = Field(default_factory=dict)


class TrustSection(BaseModel):
    """trust_layer.trust — content rules + consent + HITL + policy packs."""
    model_config = ConfigDict(extra="forbid")
    consent: ConsentConfig = Field(default_factory=ConsentConfig)
    hitl: HitlConfig
    input_rules: InputRulesConfig
    output_rules: OutputRulesConfig
    # Active policy pack name + dict of pack definitions. The active pack's
    # guardrails are injected into the LLM system prompt every turn. Without
    # these, no safety constraints reach the LLM — silent safety degradation.
    policy_pack: str = ""
    policy_packs: dict[str, PolicyPackConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def policy_pack_must_be_declared(self) -> "TrustSection":
        if self.policy_pack and self.policy_pack not in self.policy_packs:
            raise ValueError(
                f"policy_pack {self.policy_pack!r} is not declared in policy_packs. "
                f"Declared packs: {sorted(self.policy_packs.keys())}"
            )
        return self


class DignityCheckSection(BaseModel):
    """trust_layer.dignity_check — Conversational agents only.

    When enabled, the LLM self-checks each response against the questions list
    (e.g., "Does this blame the user?"). Empty questions list with enabled=True
    is a no-op disguised as a check — disallowed.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    questions: list[str] = Field(default_factory=list)
    fail_action: DignityFailAction = DignityFailAction.rewrite

    @model_validator(mode="after")
    def enabled_requires_string_questions(self) -> "DignityCheckSection":
        if not self.enabled:
            return self
        if not self.questions:
            raise ValueError(
                "dignity_check.enabled=True requires non-empty questions list. "
                "An empty list means the check always passes (no protection)."
            )
        for i, q in enumerate(self.questions):
            if not isinstance(q, str) or not q.strip():
                raise ValueError(
                    f"dignity_check.questions[{i}] must be a non-empty plain string, "
                    f"got {type(q).__name__}: {q!r}. Do not pass dicts or empty values."
                )
        return self


class ObservabilitySection(BaseModel):
    """trust_layer.observability — domain identifier."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
