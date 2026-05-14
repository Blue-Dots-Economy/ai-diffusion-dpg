"""Tests for trust_layer FIELD_RULES content (per catalogue §7.2)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.trust_layer import FIELD_RULES


# Catalogue §7.2: the full set of domain-half paths under trust_layer.
EXPECTED_PATHS = {
    "trust.policy_pack",
    "trust.input_rules.blocked_phrases",
    "trust.input_rules.blocked_input_message",
    "trust.input_rules.escalation_topics",
    "trust.output_rules.blocked_phrases",
    "trust.output_rules.output_blocked_message",
    "trust.policy_packs",
    "trust.consent.consent_phrases",
    "trust.consent.decline_phrases",
    "trust.hitl.holding_message",
    "trust.hitl.queue_backend",
    "trust.hitl.notification_webhook",
    "dignity_check.enabled",
    "dignity_check.questions",
    "observability.domain",
}


def test_all_expected_paths_present():
    actual = set(FIELD_RULES.keys())
    missing = EXPECTED_PATHS - actual
    extra = actual - EXPECTED_PATHS
    assert missing == set(), f"missing rules: {sorted(missing)}"
    if extra:
        pytest.fail(f"unexpected rules not in catalogue: {sorted(extra)}")


def test_predetermined_have_rule_expressions():
    for path, rule in FIELD_RULES.items():
        if rule.category == "predetermined":
            assert rule.rule, f"{path}: predetermined rule must define `rule`"


def test_chat_fields_have_phase():
    for path, rule in FIELD_RULES.items():
        if rule.category == "chat":
            assert rule.phase, f"{path}: chat rule must define `phase`"
            assert rule.phase in FIELD_RULES_PHASES_VALID, (
                f"{path}: phase {rule.phase!r} not in FIELD_RULES_PHASES_VALID"
            )
