"""Tests for knowledge_engine FIELD_RULES content (per catalogue §7.5)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.knowledge_engine import FIELD_RULES


# Catalogue §7.5: the full set of domain-half paths under knowledge_engine.
# knowledge.blocks.multimodal_input_handler.* stays framework_default_only
# per locked decision #7 — not listed here.
EXPECTED_PATHS = {
    "knowledge.blocks.glossary.enabled",
    "knowledge.blocks.glossary.mappings",
    "knowledge.blocks.static_knowledge_base.enabled",
    "knowledge.blocks.static_knowledge_base.collection_name",
    "knowledge.blocks.static_knowledge_base.default_doc_type",
    "knowledge.blocks.static_knowledge_base.top_k",
    "knowledge.blocks.static_knowledge_base.similarity_threshold",
    "knowledge.blocks.static_knowledge_base.embedding_provider",
    "knowledge.blocks.static_knowledge_base.intent_filters",
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
