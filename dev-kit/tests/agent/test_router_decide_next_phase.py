"""Tests for router.decide_next_phase."""
from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import decide_next_phase
from dev_kit.agent.skeleton import eval_expr


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="", project_name="p",
    )
    base.update(overrides)
    return IntakeState(**base)


def _answered_field_status_for_phase(phase: str, state: IntakeState) -> dict[str, str]:
    """Build a field_status dict with all applicable chat fields for a phase marked 'answered'."""
    return {
        path: "answered"
        for path, rule in AGGREGATED_FIELD_RULES.items()
        if rule.category == "chat" and rule.phase == phase and eval_expr(rule.applies_if, state)
    }


def test_stays_when_current_incomplete():
    state = _intake()
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "pending"}
    nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)
    assert nxt == "language"


def test_advances_when_current_complete():
    state = _intake()
    # All language-phase chat fields explicitly answered.
    # The router walks PHASES from "language" forward.
    field_status = _answered_field_status_for_phase("language", state)
    nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)
    # Should advance to the next relevant phase ("memory" — knowledge is skipped because has_kb=false)
    assert nxt == "memory"


def test_backtracks_when_earlier_phase_invalidated():
    state = _intake()
    field_status = {
        "agent_core.preprocessing.nlu_processor.intents": "needs_re_asking",
    }
    nxt = decide_next_phase("workflow", state, accumulator={}, field_status=field_status)
    assert nxt == "language"


def test_skips_irrelevant_phase():
    """user_state phase is_relevant only when is_companion_style=true."""
    state = _intake(is_companion_style=False)
    nxt = decide_next_phase("memory", state, accumulator={}, field_status={})
    # user_state should be skipped → next relevant is trust
    assert nxt == "trust"


def test_tier_phase_not_complete_when_state_completed_false():
    """With state.completed=False, tier is NOT complete; wizard stays on tier."""
    state = _intake(completed=False)
    nxt = decide_next_phase("tier", state, accumulator={}, field_status={})
    assert nxt == "tier"


def test_tier_phase_complete_when_state_completed_true():
    """With state.completed=True, tier IS complete; wizard advances to language."""
    state = _intake(completed=True)
    nxt = decide_next_phase("tier", state, accumulator={}, field_status={})
    assert nxt == "language"


def test_language_phase_not_complete_when_field_status_empty_and_no_skeleton():
    """With no skeleton run (empty field_status), language phase has pending fields.

    The tightened default of 'pending' for missing fields means an empty
    field_status no longer causes the language phase to be vacuously complete.
    """
    state = _intake()
    # No field_status entries — skeleton hasn't run yet.
    nxt = decide_next_phase("language", state, accumulator={}, field_status={})
    # Language phase has chat fields that are absent from field_status → not complete.
    assert nxt == "language"
