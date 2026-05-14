"""Tests for derived_fields.apply_derived_fields.

Verifies that the derived-field pass correctly evaluates every
``category="derived"`` compute expression from AGGREGATED_FIELD_RULES
and writes the result into the per-block accumulator dict.

Blocks under test: agent_core, trust_layer, knowledge_engine, memory_layer,
action_gateway, reach_layer, observability_layer.
"""
from __future__ import annotations

import copy

import pytest

from dev_kit.agent.derived_fields import apply_derived_fields, slug
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.skeleton import BLOCKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intake(**overrides) -> IntakeState:
    """Return a minimal IntakeState with sensible defaults, accepting overrides."""
    base = dict(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="A pilot project",
        project_name="My Project Name",
    )
    base.update(overrides)
    return IntakeState(**base)


def _fresh_accumulator() -> dict[str, dict]:
    """Return an empty per-block accumulator, one empty dict per block."""
    return {block: {} for block in BLOCKS}


# ---------------------------------------------------------------------------
# Test 1: observability.domain set in every block that has a derived rule for it
# ---------------------------------------------------------------------------

def test_observability_domain_set_for_every_block():
    """observability.domain is written as the slug of project_name for all blocks."""
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name")
    apply_derived_fields(accumulator, intake)

    expected_domain = "my_project_name"

    # These blocks all have a derived rule writing observability.domain.
    blocks_with_obs_domain = [
        "agent_core",
        "trust_layer",
        "knowledge_engine",
        "action_gateway",
        "memory_layer",
        "observability_layer",
    ]
    for block in blocks_with_obs_domain:
        actual = accumulator[block].get("observability", {}).get("domain")
        assert actual == expected_domain, (
            f"{block}.observability.domain expected {expected_domain!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: agent_workflow.workflow_id
# ---------------------------------------------------------------------------

def test_workflow_id_set():
    """agent_core.agent_workflow.workflow_id is set to f'{slug}_workflow'."""
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name")
    apply_derived_fields(accumulator, intake)

    workflow_id = accumulator["agent_core"].get("agent_workflow", {}).get("workflow_id")
    assert workflow_id == "my_project_name_workflow"


# ---------------------------------------------------------------------------
# Test 3: reach_layer web UI storage keys
# ---------------------------------------------------------------------------

def test_reach_layer_storage_keys_set():
    """reach_layer.channels.web.ui.storage_key and theme_storage_key are slug-derived."""
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name", selected_channels=["web"])
    apply_derived_fields(accumulator, intake)

    expected_slug = "my_project_name"
    web_ui = (
        accumulator["reach_layer"]
        .get("channels", {})
        .get("web", {})
        .get("ui", {})
    )
    storage_key = web_ui.get("storage_key")
    theme_storage_key = web_ui.get("theme_storage_key")

    assert storage_key == f"{expected_slug}_user_id", (
        f"storage_key expected '{expected_slug}_user_id', got {storage_key!r}"
    )
    assert theme_storage_key == f"{expected_slug}_theme", (
        f"theme_storage_key expected '{expected_slug}_theme', got {theme_storage_key!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: applies_if gating
# ---------------------------------------------------------------------------

def test_applies_if_gating():
    """A derived rule with applies_if=False must NOT write the path.

    reach_layer.channels.web.ui.storage_key has no applies_if (always applies),
    but we can test a hypothetical gating scenario by checking that the
    reach_layer.common.observability.domain is still written (no applies_if)
    while we verify the general mechanism via a rule that would be skipped.

    The reach_layer.channels.web.ui.storage_key and theme_storage_key derived
    rules have no applies_if, so they always write.  To test gating we use
    a scenario where project_name is fine but we manually inspect that
    rules with applies_if guard correctly.

    For a more direct test: reach_layer.common.observability.domain has no
    applies_if, but the *other* derived rules in reach_layer (storage_key)
    also have no applies_if either.  We verify the logic path by checking
    that an accumulator with a missing block key causes the rule to be
    skipped gracefully (not raise).
    """
    # Remove the reach_layer key from the accumulator to simulate a block
    # not present — apply_derived_fields should skip it silently.
    accumulator = {block: {} for block in BLOCKS if block != "reach_layer"}
    intake = _intake(project_name="My Project Name")
    # Should not raise even though reach_layer is absent.
    apply_derived_fields(accumulator, intake)

    # reach_layer is absent so no KeyError and agent_core is still written.
    assert "my_project_name" == accumulator["agent_core"]["observability"]["domain"]
    assert "reach_layer" not in accumulator


def test_applies_if_gating_skips_false_rule():
    """A derived rule with applies_if evaluating to False is NOT written.

    We test this by building a custom rule with applies_if and patching
    AGGREGATED_FIELD_RULES temporarily.  Since real derived rules don't use
    applies_if today, we test the code path directly.
    """
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule

    # Snapshot and restore AGGREGATED_FIELD_RULES after the test.
    snapshot = dict(AGGREGATED_FIELD_RULES)
    try:
        # Insert a test-only derived rule that is gated by has_kb=True.
        AGGREGATED_FIELD_RULES["agent_core._test_gated_field"] = FieldRule(
            category="derived",
            compute='"gated_value"',
            applies_if="has_kb",
        )

        # has_kb=False → applies_if is False → rule should NOT write.
        accumulator = _fresh_accumulator()
        intake = _intake(has_kb=False)
        apply_derived_fields(accumulator, intake)
        assert "_test_gated_field" not in accumulator["agent_core"]

        # has_kb=True → applies_if is True → rule SHOULD write.
        accumulator2 = _fresh_accumulator()
        intake2 = _intake(has_kb=True)
        apply_derived_fields(accumulator2, intake2)
        assert accumulator2["agent_core"]["_test_gated_field"] == "gated_value"

    finally:
        # Restore original state.
        AGGREGATED_FIELD_RULES.clear()
        AGGREGATED_FIELD_RULES.update(snapshot)


# ---------------------------------------------------------------------------
# Test 5: slug function handles special characters
# ---------------------------------------------------------------------------

def test_slug_function_handles_special_chars():
    """slug() normalises hyphens, spaces, and special characters to underscores."""
    assert slug("My Test! Project") == "my_test_project"
    assert slug("hello-world") == "hello_world"
    assert slug("hello world") == "hello_world"
    assert slug("  leading-and-trailing  ") == "leading_and_trailing"
    assert slug("UPPERCASE") == "uppercase"
    assert slug("multiple---dashes") == "multiple_dashes"
    assert slug("") == ""


def test_slug_end_to_end_with_apply():
    """apply_derived_fields uses the same slug logic as the slug() function."""
    project_name = "KKB Finance -- 2025!"
    expected_slug = slug(project_name)
    assert expected_slug == "kkb_finance_2025"

    accumulator = _fresh_accumulator()
    intake = _intake(project_name=project_name)
    apply_derived_fields(accumulator, intake)

    assert accumulator["agent_core"]["observability"]["domain"] == expected_slug
    assert accumulator["agent_core"]["agent_workflow"]["workflow_id"] == f"{expected_slug}_workflow"


# ---------------------------------------------------------------------------
# Test 6: idempotent — calling twice produces same result
# ---------------------------------------------------------------------------

def test_idempotent():
    """Calling apply_derived_fields twice produces the same accumulator state."""
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name")

    apply_derived_fields(accumulator, intake)
    first_pass = copy.deepcopy(accumulator)

    apply_derived_fields(accumulator, intake)
    second_pass = copy.deepcopy(accumulator)

    assert first_pass == second_pass, "apply_derived_fields is not idempotent"


# ---------------------------------------------------------------------------
# Test 7: does NOT overwrite existing chat or predetermined fields
# ---------------------------------------------------------------------------

def test_does_not_overwrite_existing_chat_fields():
    """apply_derived_fields only writes to paths owned by derived rules.

    A chat field (e.g. agent.primary_model) already present in the accumulator
    must not be overwritten by apply_derived_fields.
    """
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name")

    # Pre-populate a chat field.
    accumulator["agent_core"]["agent"] = {"primary_model": "claude-sonnet-4-5"}

    apply_derived_fields(accumulator, intake)

    # Chat field must be untouched.
    assert accumulator["agent_core"]["agent"]["primary_model"] == "claude-sonnet-4-5"
    # Derived field must also be written.
    assert accumulator["agent_core"]["observability"]["domain"] == "my_project_name"


# ---------------------------------------------------------------------------
# Test 8: reach_layer.common.observability.domain (nested common path)
# ---------------------------------------------------------------------------

def test_reach_layer_common_observability_domain():
    """reach_layer.common.observability.domain is written at the 'common' sub-path."""
    accumulator = _fresh_accumulator()
    intake = _intake(project_name="My Project Name")
    apply_derived_fields(accumulator, intake)

    common_domain = (
        accumulator["reach_layer"]
        .get("common", {})
        .get("observability", {})
        .get("domain")
    )
    assert common_domain == "my_project_name"


# ---------------------------------------------------------------------------
# Test 9: empty project_name produces empty slug gracefully
# ---------------------------------------------------------------------------

def test_empty_project_name_produces_empty_slug():
    """An empty project_name yields an empty slug without raising."""
    # IntakeState requires project_name to be a string but does not forbid empty.
    accumulator = _fresh_accumulator()
    # Use a non-empty project_name for intake construction (required), then
    # test the slug function in isolation for the empty case.
    assert slug("") == ""

    # In practice the wizard always captures project_name from the form, so
    # an empty value is a UI bug.  apply_derived_fields must still not crash.
    intake = _intake(project_name="x")
    # Manually override after construction to test the truly empty case.
    object.__setattr__(intake, "project_name", "")
    apply_derived_fields(accumulator, intake)
    # observability.domain should be empty string, not raise.
    domain = accumulator["agent_core"].get("observability", {}).get("domain")
    assert domain == ""
