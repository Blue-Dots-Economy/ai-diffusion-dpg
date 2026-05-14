"""Tests for build_skeleton: walks FIELD_RULES, produces domain accumulator + field_status."""
import pytest

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.skeleton import build_skeleton


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="A pilot project", project_name="kkb",
    )
    base.update(overrides)
    return IntakeState(**base)


def test_skeleton_kb_off_omits_kb_connector():
    state = _intake(has_kb=False)
    accumulator, _ = build_skeleton(state)
    internal = accumulator["agent_core"].get("connectors", {}).get("internal", [])
    assert all(c.get("name") != "knowledge_retrieval" for c in internal)


def test_skeleton_kb_on_seeds_knowledge_retrieval():
    state = _intake(has_kb=True)
    accumulator, _ = build_skeleton(state)
    internal = accumulator["agent_core"]["connectors"]["internal"]
    kr = next((c for c in internal if c.get("name") == "knowledge_retrieval"), None)
    assert kr is not None
    assert kr["route"] == "knowledge_engine"


def test_skeleton_companion_sets_dignity_questions():
    state = _intake(is_companion_style=True)
    accumulator, _ = build_skeleton(state)
    questions = accumulator["trust_layer"].get("dignity_check", {}).get("questions", [])
    assert len(questions) == 5


def test_skeleton_companion_off_omits_dignity_questions():
    """When equal to dpg default (empty list), skeleton should suppress write."""
    state = _intake(is_companion_style=False)
    accumulator, _ = build_skeleton(state)
    # dignity_check.questions should NOT be written when value equals the dpg default ([])
    questions = accumulator["trust_layer"].get("dignity_check", {}).get("questions")
    assert questions is None


def test_skeleton_field_status_marks_chat_pending():
    state = _intake()
    _, field_status = build_skeleton(state)
    # `agent_core.preprocessing.nlu_processor.intents` is always-asked chat → pending
    assert field_status["agent_core.preprocessing.nlu_processor.intents"] == "pending"


def test_skeleton_field_status_marks_inapplicable_when_gated_off():
    state = _intake(has_kb=False)
    _, field_status = build_skeleton(state)
    # KE chat fields are not_applicable when has_kb=false
    kf = "knowledge_engine.knowledge.blocks.static_knowledge_base.default_doc_type"
    assert field_status[kf] == "not_applicable"
