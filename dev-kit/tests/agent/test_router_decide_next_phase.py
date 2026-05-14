"""Tests for router.decide_next_phase."""
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import decide_next_phase


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


def test_stays_when_current_incomplete():
    state = _intake()
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "pending"}
    nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)
    assert nxt == "language"


def test_advances_when_current_complete():
    state = _intake()
    # All language-phase chat fields are answered.
    # The router walks PHASES from "language" forward.
    field_status = {}  # empty = no pending fields anywhere
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
