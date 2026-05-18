"""Tests for router.on_intake_update — the FIELD_RULES cascade."""
from dataclasses import replace

from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS, IntakeState
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


def _empty_accumulator() -> dict:
    return {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}


def test_all_seven_binary_flags_flip_completed_true():
    """Calling update_intake for all 7 binary flags sets state.completed = True."""
    state = _intake()
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    for flag in BINARY_INTAKE_FIELDS:
        assert state.completed is False, f"should not be complete before all 7 flags; just set {flag}"
        on_intake_update(
            field=flag, new_value=True,
            state=state, accumulator=accumulator, field_status=field_status,
        )

    assert state.completed is True
    assert set(state.binary_flags_seen) == BINARY_INTAKE_FIELDS


def test_non_binary_field_does_not_add_to_binary_flags_seen():
    """Updating a non-binary field (project_name) does not modify binary_flags_seen."""
    state = _intake()
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    on_intake_update(
        field="project_name", new_value="My Project",
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert state.binary_flags_seen == []
    assert state.completed is False


def test_repeated_calls_to_same_flag_do_not_duplicate_binary_flags_seen():
    """Calling update_intake multiple times for the same flag only records it once."""
    state = _intake()
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )
    # Second call: has_kb is already True → noop, won't append again.
    on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert state.binary_flags_seen.count("has_kb") == 1


def test_explicit_false_answer_records_binary_flag_seen():
    """Production seeds all 7 binary flags to False. An explicit "no" answer
    (False → False) must still mark the flag as seen — otherwise tier never
    completes for projects with any "no" answers and the LLM ends up
    hallucinating phase transitions in its prose.

    Reproduces the GoGuide bug observed on 2026-05-15: the user answered "no"
    to needs_persistent_user_data and is_companion_style, but binary_flags_seen
    only had 5 entries (the 5 "yes" answers), so completed stayed False.
    """
    # Production initial state: all 7 flags False, none seen yet.
    state = _intake()
    assert state.binary_flags_seen == []
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    # User says "no" to is_companion_style. Value didn't change, but the
    # answer must be recorded.
    result = on_intake_update(
        field="is_companion_style", new_value=False,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert result["noop"] is True
    assert result["binary_flag_recorded"] is True
    assert "is_companion_style" in state.binary_flags_seen


def test_all_seven_false_answers_complete_tier():
    """Sending update_intake(field, False) for every flag completes tier.

    Production scenario: a fully transactional bot with no KB, no tools, no
    consent, no escalation, no companion-style — the user answers "no" to
    every binary flag.
    """
    state = _intake()
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    for flag in BINARY_INTAKE_FIELDS:
        on_intake_update(
            field=flag, new_value=False,
            state=state, accumulator=accumulator, field_status=field_status,
        )

    assert state.completed is True
    assert set(state.binary_flags_seen) == BINARY_INTAKE_FIELDS


def test_cascade_does_not_promote_pending_field_to_needs_re_asking():
    """When intake flips invalidate a chat field that was never answered
    (status is `pending` or missing from field_status entirely), the
    cascade must NOT mark it `needs_re_asking`. Re-ask only makes sense
    for fields the user has already answered.

    Tour Pal regression: after the seven tier turns, 43 chat fields had
    status `needs_re_asking` even though none had been touched — the
    cascade was unconditionally writing `needs_re_asking` for every
    invalidated chat path. The UI showed "43 needs re-asking" before the
    chat had even reached language phase, and the LLM thought it needed
    to re-confirm fields that had no prior answer.

    Fix: only the `answered → needs_re_asking` and
    `not_applicable → needs_re_asking` transitions emit the
    `needs_re_asking` status. Pending/missing entries are left alone.
    """
    state = _intake(has_kb=False)
    # nlu_processor.intents (chat, invalidated_by has_kb) starts with NO
    # entry in field_status — never answered.
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    # The cascade visited this chat field but must not have written
    # needs_re_asking. The field has no entry (will be picked up by
    # skeleton later) or is `pending`.
    status = field_status.get("agent_core.preprocessing.nlu_processor.intents")
    assert status != "needs_re_asking", (
        f"Pending/missing chat field was incorrectly promoted to "
        f"needs_re_asking; got status={status!r}"
    )


def test_cascade_promotes_answered_to_needs_re_asking():
    """The legitimate case still works: a field the user has already
    answered, when invalidated by an intake flag flip, is correctly
    marked `needs_re_asking` so the wizard backtracks.
    """
    state = _intake(has_kb=False)
    accumulator = _empty_accumulator()
    field_status = {
        "agent_core.preprocessing.nlu_processor.intents": "answered",
    }

    on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert (
        field_status["agent_core.preprocessing.nlu_processor.intents"]
        == "needs_re_asking"
    )


def test_completed_does_not_flip_until_all_seven_seen():
    """Completing 6 of the 7 binary flags must NOT set state.completed = True."""
    state = _intake()
    accumulator = _empty_accumulator()
    field_status: dict[str, str] = {}

    flags = list(BINARY_INTAKE_FIELDS)
    for flag in flags[:-1]:  # all but the last
        on_intake_update(
            field=flag, new_value=True,
            state=state, accumulator=accumulator, field_status=field_status,
        )

    assert state.completed is False
    assert len(state.binary_flags_seen) == 6
