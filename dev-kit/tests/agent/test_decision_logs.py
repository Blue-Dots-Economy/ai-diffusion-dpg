"""Tests for all 14 structured decision-log points (design §8).

Each test triggers the relevant action and asserts the emitted log record
carries the required ``operation``, ``status``, and additional fields.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from dev_kit.agent.derived_fields import apply_derived_fields
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import decide_next_phase, on_config_update, on_intake_update
from dev_kit.agent.skeleton import build_skeleton

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_intake(**overrides) -> IntakeState:
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
        domain_description="test domain",
        project_name="test-project",
    )
    base.update(overrides)
    return IntakeState(**base)


def _empty_accumulator() -> dict[str, dict]:
    return {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}


def _find_log(records, operation: str) -> list:
    """Return all log records whose ``operation`` extra matches."""
    return [r for r in records if getattr(r, "operation", None) == operation]


# ---------------------------------------------------------------------------
# Point 1: on_intake_update summary log
# ---------------------------------------------------------------------------

def test_point1_on_intake_update_summary_log(caplog) -> None:
    """router.on_intake_update emits INFO with field, old_value, new_value, affected_count."""
    state = _make_intake(has_kb=False)
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        on_intake_update(
            field="has_kb",
            new_value=True,
            state=state,
            accumulator=accumulator,
            field_status=field_status,
        )

    records = _find_log(caplog.records, "router.on_intake_update")
    assert records, "Expected a router.on_intake_update log record"
    rec = records[-1]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "field", None) == "has_kb"
    assert getattr(rec, "old_value", None) is False
    assert getattr(rec, "new_value", None) is True
    assert isinstance(getattr(rec, "affected_count", None), int)


# ---------------------------------------------------------------------------
# Point 2: on_config_update summary log
# ---------------------------------------------------------------------------

def test_point2_on_config_update_success_log(caplog) -> None:
    """router.on_config_update emits INFO with block, section, paths_written, validation_errors."""
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        on_config_update(
            path="agent_core.conversation.blocked_message",
            value="I cannot help with that.",
            accumulator=accumulator,
            field_status=field_status,
        )

    records = _find_log(caplog.records, "router.on_config_update")
    assert records, "Expected a router.on_config_update log record"
    rec = records[-1]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "block", None) == "agent_core"
    assert getattr(rec, "section", None) == "conversation"
    paths_written = getattr(rec, "paths_written", None)
    assert isinstance(paths_written, list) and len(paths_written) >= 1
    assert getattr(rec, "validation_errors", None) == []


def test_point2_on_config_update_failure_log(caplog) -> None:
    """router.on_config_update emits WARNING with validation_errors on failure."""
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with caplog.at_level(logging.WARNING, logger="dev_kit.agent.router"):
        with pytest.raises(ValueError):
            on_config_update(
                path="agent_core.conversation.blocked_message",
                value="",  # violates min_length=1
                accumulator=accumulator,
                field_status=field_status,
            )

    records = _find_log(caplog.records, "router.on_config_update")
    assert records, "Expected a router.on_config_update WARNING record"
    rec = records[-1]
    assert rec.levelno == logging.WARNING
    assert getattr(rec, "status", None) == "failure"
    errors = getattr(rec, "validation_errors", None)
    assert isinstance(errors, list) and len(errors) > 0


# ---------------------------------------------------------------------------
# Point 3: field_marked_needs_re_asking
# ---------------------------------------------------------------------------

def test_point3_field_marked_needs_re_asking_log(caplog) -> None:
    """router.field_marked_needs_re_asking emitted for each chat field invalidated."""
    state = _make_intake(has_kb=False)
    accumulator = _empty_accumulator()
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "answered"}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        on_intake_update(
            field="has_kb",
            new_value=True,
            state=state,
            accumulator=accumulator,
            field_status=field_status,
        )

    records = _find_log(caplog.records, "router.field_marked_needs_re_asking")
    assert records, "Expected at least one router.field_marked_needs_re_asking record"
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "triggered_by", None) == "has_kb"
    assert getattr(rec, "path", None) is not None
    assert getattr(rec, "reason", None) is not None


# ---------------------------------------------------------------------------
# Point 4: predetermined_recomputed
# ---------------------------------------------------------------------------

def test_point4_predetermined_recomputed_log(caplog) -> None:
    """router.predetermined_recomputed emitted for each predetermined field affected."""
    state = _make_intake(is_companion_style=False)
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        on_intake_update(
            field="is_companion_style",
            new_value=True,
            state=state,
            accumulator=accumulator,
            field_status=field_status,
        )

    records = _find_log(caplog.records, "router.predetermined_recomputed")
    assert records, "Expected at least one router.predetermined_recomputed record"
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "triggered_by", None) == "is_companion_style"
    assert "path" in rec.__dict__ or hasattr(rec, "path")


# ---------------------------------------------------------------------------
# Point 5: phase transition forward (advance)
# ---------------------------------------------------------------------------

def test_point5_phase_transition_forward_log(caplog) -> None:
    """router.decide_next_phase emits INFO with reason='phase_complete' on advance."""
    # Tier phase completion is gated on state.completed, not chat fields.
    # Set completed=True so the tier phase is considered complete.
    state = _make_intake(completed=True)
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        result = decide_next_phase("tier", state, accumulator, field_status)

    assert result != "tier", "Expected advance away from 'tier'"

    records = _find_log(caplog.records, "router.decide_next_phase")
    advance_records = [r for r in records if getattr(r, "status", None) == "advance"]
    assert advance_records, "Expected a router.decide_next_phase advance record"
    rec = advance_records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "from_phase", None) == "tier"
    assert getattr(rec, "reason", None) == "phase_complete"


# ---------------------------------------------------------------------------
# Point 6: phase transition backtrack
# ---------------------------------------------------------------------------

def test_point6_phase_transition_backtrack_log(caplog) -> None:
    """router.decide_next_phase emits WARNING with reason='invalidated' on backtrack."""
    state = _make_intake()
    accumulator = _empty_accumulator()
    # Simulate a field in the earlier 'tier' phase needing re-asking while we're in 'trust'
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "needs_re_asking"}

    with caplog.at_level(logging.WARNING, logger="dev_kit.agent.router"):
        result = decide_next_phase("trust", state, accumulator, field_status)

    # The 'nlu_processor.intents' field is in 'language' phase (earlier than 'trust')
    assert result != "trust", "Expected backtrack from 'trust'"

    records = _find_log(caplog.records, "router.decide_next_phase")
    backtrack_records = [r for r in records if getattr(r, "status", None) == "backtrack"]
    assert backtrack_records, "Expected a router.decide_next_phase backtrack record"
    rec = backtrack_records[0]
    assert rec.levelno == logging.WARNING
    assert getattr(rec, "from_phase", None) == "trust"
    assert getattr(rec, "reason", None) == "invalidated"


# ---------------------------------------------------------------------------
# Point 7: phase skipped via is_relevant
# ---------------------------------------------------------------------------

def test_point7_phase_skipped_log(caplog) -> None:
    """router.phase_skipped emitted for each irrelevant phase during advance scan.

    Set up so we advance from 'language' (where knowledge is the next candidate
    but has_kb=False makes it irrelevant → skip log fired).
    """
    # has_kb=False → 'knowledge' is irrelevant, will be skipped when scanning forward
    state = _make_intake(has_kb=False)
    accumulator = _empty_accumulator()
    # Mark all language-phase chat fields as answered so decide_next_phase advances.
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
    field_status: dict[str, str] = {}
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.phase == "language" and rule.category == "chat":
            field_status[path] = "answered"

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        result = decide_next_phase("language", state, accumulator, field_status)

    # Should have skipped 'knowledge' and advanced to 'memory'
    assert result == "memory", f"Expected 'memory', got {result!r}"

    records = _find_log(caplog.records, "router.phase_skipped")
    # 'knowledge' phase should be skipped since has_kb=False
    knowledge_skip = [r for r in records if getattr(r, "skipped_phase", None) == "knowledge"]
    assert knowledge_skip, "Expected router.phase_skipped for 'knowledge'"
    rec = knowledge_skip[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "skipped"
    assert getattr(rec, "reason", None) is not None


# ---------------------------------------------------------------------------
# Point 8: skeleton field written
# ---------------------------------------------------------------------------

def test_point8_skeleton_field_written_log(caplog) -> None:
    """skeleton.field_written emitted for each predetermined or chat-default field written."""
    state = _make_intake(is_companion_style=True)

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.skeleton"):
        build_skeleton(state)

    records = _find_log(caplog.records, "skeleton.field_written")
    assert records, "Expected at least one skeleton.field_written record"
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "path", None) is not None
    assert getattr(rec, "category", None) is not None
    value_kind = getattr(rec, "value_kind", None)
    assert value_kind in ("predetermined_value", "chat_default", "derived")


# ---------------------------------------------------------------------------
# Point 9: skeleton field skipped (equals framework default)
# ---------------------------------------------------------------------------

def test_point9_skeleton_field_skipped_equals_default_log(caplog) -> None:
    """skeleton.field_skipped emitted when predetermined value equals dpg default."""
    # is_companion_style=False → dignity_check.questions = [] (equals framework default)
    state = _make_intake(is_companion_style=False)

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.skeleton"):
        build_skeleton(state)

    records = _find_log(caplog.records, "skeleton.field_skipped")
    assert records, "Expected at least one skeleton.field_skipped record"
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "skipped"
    assert getattr(rec, "path", None) is not None
    assert getattr(rec, "reason", None) == "equals_dpg_default"


# ---------------------------------------------------------------------------
# Point 10: derived field computation
# ---------------------------------------------------------------------------

def test_point10_derived_field_computed_log(caplog) -> None:
    """derived_fields.computed emitted for each derived field written."""
    state = _make_intake(project_name="My Cool Project")
    accumulator = _empty_accumulator()

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.derived_fields"):
        apply_derived_fields(accumulator, state)

    records = _find_log(caplog.records, "derived_fields.computed")
    assert records, "Expected at least one derived_fields.computed record"
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert getattr(rec, "status", None) == "success"
    assert getattr(rec, "path", None) is not None
    assert "computed_value" in rec.__dict__ or hasattr(rec, "computed_value")


# Point 14 (compose_generator.service_decision) was covered by the now-deleted
# compose_generator.py module. The equivalent coverage is provided by the
# deploy preview / deploy runner integration tests in test_deploy_preview_intake.py.
