"""Tests for router.on_config_update — applies chat answers with mirror validation.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §7.
"""
import pytest

from dev_kit.agent.router import on_config_update


def _empty_accumulator() -> dict[str, dict]:
    return {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}


# ---------------------------------------------------------------------------
# Valid write
# ---------------------------------------------------------------------------

def test_valid_write_updates_accumulator_and_field_status():
    """A valid value is persisted and field_status is marked answered."""
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    result = on_config_update(
        path="agent_core.conversation.blocked_message",
        value="I cannot help with that.",
        accumulator=accumulator,
        field_status=field_status,
    )

    assert result == {
        "ok": True,
        "path": "agent_core.conversation.blocked_message",
        "value": "I cannot help with that.",
    }
    assert accumulator["agent_core"]["conversation"]["blocked_message"] == "I cannot help with that."
    assert field_status["agent_core.conversation.blocked_message"] == "answered"


# ---------------------------------------------------------------------------
# Unknown path
# ---------------------------------------------------------------------------

def test_unknown_path_raises_value_error():
    """Paths not in AGGREGATED_FIELD_RULES raise ValueError; accumulator untouched."""
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with pytest.raises(ValueError, match="unknown path"):
        on_config_update(
            path="agent_core.nonexistent.field",
            value="something",
            accumulator=accumulator,
            field_status=field_status,
        )

    assert accumulator["agent_core"] == {}
    assert field_status == {}


# ---------------------------------------------------------------------------
# Non-chat category
# ---------------------------------------------------------------------------

def test_predetermined_path_raises_value_error():
    """Paths with category != 'chat' raise ValueError; accumulator untouched."""
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    # agent_core.agent.ask_for_consent is predetermined (set: needs_consent)
    with pytest.raises(ValueError, match="not a chat field"):
        on_config_update(
            path="agent_core.agent.ask_for_consent",
            value=True,
            accumulator=accumulator,
            field_status=field_status,
        )

    assert "agent" not in accumulator["agent_core"]
    assert field_status == {}


# ---------------------------------------------------------------------------
# Validation failure — accumulator reverted
# ---------------------------------------------------------------------------

def test_validation_failure_reverts_accumulator_and_leaves_field_status_unchanged():
    """An invalid value is rejected; accumulator is reverted to its pre-write state."""
    accumulator = _empty_accumulator()
    # Pre-seed a valid value so we can verify it's restored after revert.
    accumulator["agent_core"]["conversation"] = {"blocked_message": "original"}
    field_status: dict[str, str] = {"agent_core.conversation.blocked_message": "pending"}

    # blocked_message is str with min_length=1; an empty string violates the constraint.
    with pytest.raises(ValueError):
        on_config_update(
            path="agent_core.conversation.blocked_message",
            value="",
            accumulator=accumulator,
            field_status=field_status,
        )

    # Accumulator must be reverted to the pre-write state.
    assert accumulator["agent_core"]["conversation"]["blocked_message"] == "original"
    # field_status must NOT have been updated (revert includes status).
    assert field_status["agent_core.conversation.blocked_message"] == "pending"
