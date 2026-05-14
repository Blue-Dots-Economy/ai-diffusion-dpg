"""Tests for FieldRule dataclass shape."""
import pytest

from dev_kit.agent.field_rules import FieldRule, AGGREGATED_FIELD_RULES, register_block_rules


def test_fieldrule_predetermined_minimal():
    rule = FieldRule(category="predetermined", rule="set: is_companion_style")
    assert rule.category == "predetermined"
    assert rule.rule == "set: is_companion_style"
    assert rule.deploy_overridable is False
    assert rule.invalidated_by == []


def test_fieldrule_chat_with_deploy_override():
    rule = FieldRule(
        category="chat",
        phase="language",
        default="anthropic",
        description="LLM provider",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    )
    assert rule.category == "chat"
    assert rule.deploy_overridable is True
    assert rule.phase == "language"


def test_fieldrule_invalid_category_rejected():
    with pytest.raises(ValueError):
        FieldRule(category="invalid_category")


def test_fieldrule_frozen():
    rule = FieldRule(category="chat", phase="trust")
    with pytest.raises((TypeError, AttributeError)):
        rule.category = "deploy"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# register_block_rules tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_aggregate():
    """Ensure tests start with an empty AGGREGATED_FIELD_RULES.

    Note: AGGREGATED_FIELD_RULES is module-level mutable state. Per-block
    modules call register_block_rules() at import time, so anything that
    imports the per-block modules would pre-populate it. This fixture
    snapshots and restores around each test to keep tests isolated.
    """
    snapshot = dict(AGGREGATED_FIELD_RULES)
    AGGREGATED_FIELD_RULES.clear()
    yield
    AGGREGATED_FIELD_RULES.clear()
    AGGREGATED_FIELD_RULES.update(snapshot)


def test_register_block_rules_prefixes_paths():
    rule = FieldRule(category="chat", phase="language")
    register_block_rules("agent_core", {"agent.timeout_ms": rule})
    assert "agent_core.agent.timeout_ms" in AGGREGATED_FIELD_RULES
    assert AGGREGATED_FIELD_RULES["agent_core.agent.timeout_ms"] is rule


def test_register_block_rules_is_idempotent():
    rule_v1 = FieldRule(category="chat", phase="language", default="v1")
    rule_v2 = FieldRule(category="chat", phase="language", default="v2")
    register_block_rules("agent_core", {"agent.timeout_ms": rule_v1})
    register_block_rules("agent_core", {"agent.timeout_ms": rule_v2})
    # Replaces, doesn't duplicate.
    matching = [k for k in AGGREGATED_FIELD_RULES if k == "agent_core.agent.timeout_ms"]
    assert len(matching) == 1
    assert AGGREGATED_FIELD_RULES["agent_core.agent.timeout_ms"].default == "v2"


def test_register_block_rules_two_blocks_coexist():
    rule = FieldRule(category="chat", phase="language")
    register_block_rules("agent_core", {"foo": rule})
    register_block_rules("trust_layer", {"foo": rule})
    assert "agent_core.foo" in AGGREGATED_FIELD_RULES
    assert "trust_layer.foo" in AGGREGATED_FIELD_RULES


def test_register_block_rules_rejects_dotted_block_name():
    rule = FieldRule(category="chat", phase="language")
    with pytest.raises(ValueError, match="simple identifier"):
        register_block_rules("reach_layer.web", {"foo": rule})


def test_register_block_rules_rejects_empty_block_name():
    rule = FieldRule(category="chat", phase="language")
    with pytest.raises(ValueError, match="simple identifier"):
        register_block_rules("", {"foo": rule})


def test_register_block_rules_rejects_non_fieldrule_value():
    with pytest.raises(TypeError, match="FieldRule"):
        register_block_rules("agent_core", {"foo": {"category": "chat"}})  # dict, not FieldRule


# ---------------------------------------------------------------------------
# FIELD_RULES_PHASES_VALID tests
# ---------------------------------------------------------------------------

def test_valid_phases_has_eleven_entries():
    from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
    assert len(FIELD_RULES_PHASES_VALID) == 11
    expected = {
        "tier", "language", "knowledge", "memory", "user_state", "trust",
        "tools", "workflow", "observability", "reach", "review",
    }
    assert FIELD_RULES_PHASES_VALID == expected
