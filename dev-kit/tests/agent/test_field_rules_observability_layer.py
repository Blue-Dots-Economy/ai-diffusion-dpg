"""Tests for observability_layer FIELD_RULES content (per catalogue §7.7)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.observability_layer import FIELD_RULES


# Catalogue §7.7: the full set of domain-half paths under observability_layer.
EXPECTED_PATHS = {
    "observability.outcomes.lifecycle",
    "observability.outcomes.metrics",
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
