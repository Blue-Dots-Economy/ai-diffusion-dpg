"""Tests for router.on_intake_update — the FIELD_RULES cascade."""
from dataclasses import replace

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import on_intake_update


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="", project_name="proj",
    )
    base.update(overrides)
    return IntakeState(**base)


def test_flip_has_kb_marks_nlu_intents_for_re_ask():
    state = _intake(has_kb=False)
    accumulator = {"agent_core": {"preprocessing": {"nlu_processor": {"intents": ["unknown"]}}},
                   "knowledge_engine": {}, "trust_layer": {}, "memory_layer": {},
                   "action_gateway": {}, "reach_layer": {}, "observability_layer": {}}
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "answered"}

    result = on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert state.has_kb is True
    assert field_status["agent_core.preprocessing.nlu_processor.intents"] == "needs_re_asking"
    assert result["affected_count"] >= 1
    assert result["earliest_affected_phase"] in ("language", "knowledge")


def test_flip_companion_style_recomputes_dignity_enabled():
    state = _intake(is_companion_style=False)
    accumulator = {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}
    field_status: dict[str, str] = {}

    on_intake_update(
        field="is_companion_style", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    # dignity_check.enabled is predetermined `set: is_companion_style`
    assert accumulator["trust_layer"]["dignity_check"]["enabled"] is True
    assert len(accumulator["trust_layer"]["dignity_check"]["questions"]) == 5


def test_noop_when_value_unchanged():
    state = _intake(has_kb=True)
    accumulator: dict = {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}
    field_status: dict[str, str] = {}

    result = on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert result["noop"] is True
