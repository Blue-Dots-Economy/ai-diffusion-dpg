"""Tests for trust_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.trust_layer import (
    ConsentConfig,
    DignityCheckSection,
    GuardrailConfig,
    HitlConfig,
    InputRulesConfig,
    ObservabilitySection,
    OutputRulesConfig,
    PolicyPackConfig,
    TrustSection,
)


# -- ConsentConfig -----------------------------------------------------------

def test_consent_config_defaults():
    c = ConsentConfig()
    assert c.consent_phrases == []
    assert c.decline_phrases == []


def test_consent_config_extra_forbidden():
    with pytest.raises(ValidationError):
        ConsentConfig(consent_phrases=["yes"], unknown="x")


# -- HitlConfig --------------------------------------------------------------

def test_hitl_config_holding_message_required():
    with pytest.raises(ValidationError):
        HitlConfig(holding_message="")


def test_hitl_default_queue_backend_is_log():
    h = HitlConfig(holding_message="Please wait")
    assert h.queue_backend.value == "log"


def test_hitl_queue_backend_rejects_memory():
    """'memory' is not a valid backend — runtime crashes on it."""
    with pytest.raises(ValidationError):
        HitlConfig(holding_message="hi", queue_backend="memory")


def test_hitl_queue_backend_accepts_valid():
    for b in ("log", "redis", "webhook"):
        HitlConfig(holding_message="hi", queue_backend=b)


# -- InputRulesConfig --------------------------------------------------------

def test_input_rules_blocked_input_message_required():
    with pytest.raises(ValidationError):
        InputRulesConfig(blocked_input_message="")


def test_input_rules_minimal():
    i = InputRulesConfig(blocked_input_message="blocked")
    assert i.blocked_phrases == []
    assert i.escalation_topics == []


# -- OutputRulesConfig -------------------------------------------------------

def test_output_rules_output_blocked_message_required():
    with pytest.raises(ValidationError):
        OutputRulesConfig(output_blocked_message="")


# -- GuardrailConfig ---------------------------------------------------------

def test_guardrail_config_defaults():
    g = GuardrailConfig()
    assert g.severity.value == "warning"
    assert g.failure_mode.value == "constrain"
    assert g.prompt_constraints == []
    assert g.required_disclosures == []
    assert g.refusal_template is None


def test_guardrail_severity_enum():
    GuardrailConfig(severity="blocker")
    GuardrailConfig(severity="warning")
    with pytest.raises(ValidationError):
        GuardrailConfig(severity="critical")


def test_guardrail_failure_mode_enum():
    GuardrailConfig(failure_mode="block")
    GuardrailConfig(failure_mode="constrain")
    with pytest.raises(ValidationError):
        GuardrailConfig(failure_mode="punish")


def test_guardrail_full_construction():
    g = GuardrailConfig(
        severity="blocker",
        failure_mode="block",
        prompt_constraints=["Never give medical advice."],
        required_disclosures=["I am an AI assistant."],
        refusal_template="I can't help with that. Please contact a professional.",
    )
    assert g.refusal_template.startswith("I can't help")


def test_guardrail_extra_forbidden():
    with pytest.raises(ValidationError):
        GuardrailConfig(unknown_field="x")


def test_guardrail_refusal_template_rejects_empty_string():
    """Empty refusal_template would be useless when failure_mode='block' fires."""
    with pytest.raises(ValidationError):
        GuardrailConfig(refusal_template="")


def test_guardrail_refusal_template_none_allowed():
    """None means 'no fixed refusal template' — valid."""
    g = GuardrailConfig(refusal_template=None)
    assert g.refusal_template is None


# -- PolicyPackConfig --------------------------------------------------------

def test_policy_pack_empty_guardrails_allowed():
    p = PolicyPackConfig()
    assert p.guardrails == {}


def test_policy_pack_with_guardrails():
    p = PolicyPackConfig(
        guardrails={
            "medical_advice": GuardrailConfig(severity="blocker", failure_mode="block"),
        }
    )
    assert "medical_advice" in p.guardrails


def test_policy_pack_extra_forbidden():
    with pytest.raises(ValidationError):
        PolicyPackConfig(guardrails={}, unknown="x")


# -- TrustSection ------------------------------------------------------------

def _minimal_trust_section_kwargs():
    return dict(
        hitl=HitlConfig(holding_message="hi"),
        input_rules=InputRulesConfig(blocked_input_message="b"),
        output_rules=OutputRulesConfig(output_blocked_message="o"),
    )


def test_trust_section_minimal():
    t = TrustSection(**_minimal_trust_section_kwargs())
    assert t.policy_pack == ""
    assert t.policy_packs == {}


def test_trust_section_with_policy_pack():
    t = TrustSection(
        **_minimal_trust_section_kwargs(),
        policy_pack="kkb_advisory",
        policy_packs={
            "kkb_advisory": PolicyPackConfig(guardrails={
                "medical": GuardrailConfig(severity="blocker"),
            }),
        },
    )
    assert t.policy_pack == "kkb_advisory"


def test_trust_section_policy_pack_must_be_declared():
    """If policy_pack is set, it must reference a key in policy_packs."""
    with pytest.raises(ValidationError, match="not declared"):
        TrustSection(
            **_minimal_trust_section_kwargs(),
            policy_pack="ghost_pack",
            policy_packs={},
        )


def test_trust_section_empty_policy_pack_skips_validation():
    """policy_pack='' is valid even with empty policy_packs (no active pack)."""
    t = TrustSection(
        **_minimal_trust_section_kwargs(),
        policy_pack="",
        policy_packs={},
    )
    assert t.policy_pack == ""


def test_trust_section_extra_forbidden():
    with pytest.raises(ValidationError):
        TrustSection(**_minimal_trust_section_kwargs(), unknown="x")


# -- DignityCheckSection -----------------------------------------------------

def test_dignity_check_disabled_default():
    """When disabled, questions can be empty — no enforcement."""
    d = DignityCheckSection(enabled=False, questions=[])
    assert d.enabled is False


def test_dignity_check_enabled_requires_questions():
    with pytest.raises(ValidationError, match="questions"):
        DignityCheckSection(enabled=True, questions=[])


def test_dignity_check_questions_must_be_strings():
    """Critical: questions must be plain strings, not dicts."""
    with pytest.raises(ValidationError):
        DignityCheckSection(
            enabled=True,
            questions=["valid one", {"category": "hate", "severity": "high"}],
        )


def test_dignity_check_empty_string_question_rejected():
    with pytest.raises(ValidationError):
        DignityCheckSection(enabled=True, questions=["valid", ""])


def test_dignity_check_fail_action_enum():
    DignityCheckSection(enabled=False, fail_action="rewrite")
    DignityCheckSection(enabled=False, fail_action="flag")
    DignityCheckSection(enabled=False, fail_action="skip")
    with pytest.raises(ValidationError):
        DignityCheckSection(enabled=False, fail_action="bogus")


def test_dignity_check_full_valid():
    d = DignityCheckSection(
        enabled=True,
        questions=[
            "Does this blame the user?",
            "Does it over-promise?",
            "Does it push urgency?",
            "Does it reduce their agency?",
            "Does it sound like a script?",
        ],
        fail_action="rewrite",
    )
    assert len(d.questions) == 5


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_section_domain_must_be_non_empty():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="")


def test_observability_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", typo="y")
