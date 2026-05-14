"""Tests for action_gateway FIELD_RULES content (per catalogue §7.4)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.action_gateway import FIELD_RULES


# Catalogue §7.4: the full set of domain-half paths under action_gateway.
# FIELD_RULES tracks the whole tools list as one chat field.
# Per-entry editing (tools[id=X].*) is handled by the add_tool tool (Phase 6).
EXPECTED_PATHS = {
    "tools",
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
