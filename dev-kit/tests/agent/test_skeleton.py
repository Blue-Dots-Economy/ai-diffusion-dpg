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


def test_skeleton_marks_chat_fields_with_default_as_answered():
    """Chat fields whose FieldRule has a non-None default are pre-filled with
    that default AND marked `answered` — the framework default IS the answer
    and the user has nothing to add.

    GoGuide regression: previously `build_skeleton` always set chat
    fields to `pending`, even when it had just written a useful default
    (`language_normalisation.enabled=True`,
    `nlu_processor.user_state_confidence_threshold=0.4`, etc.). The
    router then refused to advance the language phase because seven such
    fields stayed at `pending` forever — the LLM had no question to
    ask about them, the user had no answer to give, and the phase was
    deadlocked.
    """
    state = _intake()
    accumulator, field_status = build_skeleton(state)

    # `language_normalisation.enabled` has FieldRule.default=True. After
    # the fix it must be both written into the accumulator AND marked
    # answered (not pending).
    path = "agent_core.preprocessing.language_normalisation.enabled"
    assert field_status[path] == "answered", (
        f"Expected `answered` after skeleton wrote the default; "
        f"got {field_status[path]!r}"
    )
    # And the default value (`True`) lands in the accumulator.
    accum = accumulator["agent_core"]
    assert (
        accum["preprocessing"]["language_normalisation"]["enabled"]
        is True
    )


def test_skeleton_keeps_chat_fields_without_default_as_pending():
    """Chat fields whose FieldRule has `default=None` and need user input
    stay `pending` after skeleton — the LLM still has to propose them.
    """
    state = _intake()
    _, field_status = build_skeleton(state)

    # `conversation.blocked_message` has no default (the schema's
    # min_length=1 means an empty string would fail). It must stay
    # pending so the LLM proposes a domain-appropriate value.
    assert (
        field_status["agent_core.conversation.blocked_message"]
        == "pending"
    )


def test_skeleton_clears_the_seven_goguide_pending_fields():
    """The seven language-phase chat fields that blocked GoGuide's phase
    advancement must NOT all be `pending` after skeleton. The five with
    non-None defaults must be `answered`; the two without defaults stay
    `pending` and the language prompt instructs the LLM to write them.
    """
    state = _intake(
        has_kb=True, has_external_tools=True, is_multi_turn=True,
        needs_persistent_user_data=False, is_companion_style=True,
        needs_consent=False, has_hitl=True,
        selected_channels=["web", "voice"],
        default_language="english",
        supported_languages=["english", "telugu", "kannada", "hinglish", "tamil"],
        domain_description="A tour guide bot.",
        project_name="go guide",
        completed=True,
    )
    _, field_status = build_skeleton(state)

    # Defaulted fields — must be 'answered' now.
    defaulted = [
        "agent_core.preprocessing.language_normalisation.enabled",
        "agent_core.preprocessing.language_normalisation.provider",
        "agent_core.preprocessing.language_normalisation.model",
        "agent_core.preprocessing.nlu_processor.provider",
        "agent_core.preprocessing.nlu_processor.model",
    ]
    for path in defaulted:
        assert field_status[path] == "answered", (
            f"{path} should be 'answered' after skeleton "
            f"(FieldRule has a non-None default); got {field_status[path]!r}"
        )

    # Non-defaulted fields — stay 'pending' so the LLM writes a value.
    pending = [
        "agent_core.conversation.unsupported_language_message",
    ]
    for path in pending:
        assert field_status[path] == "pending", (
            f"{path} should stay 'pending' until the LLM writes it; "
            f"got {field_status[path]!r}"
        )


def test_skeleton_writes_collection_name_using_project_slug():
    """The predetermined `collection_name` rule references `project_slug`,
    which must be available in `eval_rule`'s namespace.

    Tour Pal regression: `eval_rule` was only exposing IntakeState fields
    plus a small set of constants. The collection_name rule
    (`set: f"{project_slug}_knowledge" if has_kb else None`) silently
    evaluated to _SKIP because `project_slug` was undefined, so the KB
    `collection_name` field was never written. At deploy time the
    runtime would fall back to its default `dpg_knowledge` — wrong for
    every project.
    """
    state = _intake(has_kb=True, project_name="Tour Pal")
    accumulator, _ = build_skeleton(state)
    kb = (
        accumulator["knowledge_engine"]
        .get("knowledge", {})
        .get("blocks", {})
        .get("static_knowledge_base", {})
    )
    # `derived_fields.slug("Tour Pal")` produces `tour_pal` (underscore
    # separator — distinct from `app.py:_slugify` which uses hyphens for
    # the project directory). The rule appends "_knowledge".
    assert kb.get("collection_name") == "tour_pal_knowledge", (
        f"expected `tour_pal_knowledge`, got {kb.get('collection_name')!r}"
    )
