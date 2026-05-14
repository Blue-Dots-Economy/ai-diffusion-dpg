"""Tests for dev_kit.agent.phase_prompts._helpers."""
from __future__ import annotations

import pytest

from dev_kit.agent.phase_prompts._helpers import _path_of, _rule_of, _render_fields
from dev_kit.agent.field_rules import FieldRule


# ---------------------------------------------------------------------------
# _path_of
# ---------------------------------------------------------------------------

def test_path_of_with_path_attr():
    """Object with .path attribute returns that attribute."""
    class _Obj:
        path = "agent_core.agent.primary_model"

    assert _path_of(_Obj()) == "agent_core.agent.primary_model"


def test_path_of_with_tuple():
    """(path, rule) tuple returns the first element."""
    rule = FieldRule(category="chat", phase="knowledge", description="test")
    assert _path_of(("foo.bar", rule)) == "foo.bar"


def test_path_of_fallback():
    """A plain string (no .path, not a 2-tuple) falls back to str(item)."""
    assert _path_of("plain_string") == "plain_string"


# ---------------------------------------------------------------------------
# _rule_of
# ---------------------------------------------------------------------------

def test_rule_of_with_field_rule():
    """A bare FieldRule instance (has .category) is returned as-is."""
    rule = FieldRule(category="chat", phase="knowledge", description="desc")
    assert _rule_of(rule) is rule


def test_rule_of_with_tuple():
    """(path, rule) tuple returns the second element."""
    rule = FieldRule(category="chat", phase="tools", description="a tool")
    result = _rule_of(("action_gateway.connector", rule))
    assert result is rule


# ---------------------------------------------------------------------------
# _render_fields
# ---------------------------------------------------------------------------

def test_render_fields_empty_list():
    """Empty list returns a non-empty sentinel string (not a crash)."""
    result = _render_fields([])
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_fields_with_one_field():
    """A single FieldRule with a description appears in the output."""
    rule = FieldRule(category="chat", phase="memory", description="Session TTL in minutes")
    rule_with_path = type("_R", (), {"path": "memory_layer.state.session.ttl_minutes",
                                     "category": "chat",
                                     "description": "Session TTL in minutes",
                                     "default": None,
                                     "applies_if": None})()
    result = _render_fields([rule_with_path])
    assert "Session TTL in minutes" in result


def test_render_fields_with_tuples():
    """(path, FieldRule) tuples render both the path and the description."""
    rule = FieldRule(category="chat", phase="workflow", description="Workflow identifier")
    result = _render_fields([("foo.bar", rule)])
    assert "foo.bar" in result
    assert "Workflow identifier" in result
