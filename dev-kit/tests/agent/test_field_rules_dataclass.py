"""Tests for FieldRule dataclass shape."""
import pytest

from dev_kit.agent.field_rules import FieldRule


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
