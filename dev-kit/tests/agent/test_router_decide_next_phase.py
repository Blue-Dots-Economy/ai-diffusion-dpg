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


def test_stay_log_lists_pending_field_paths(caplog) -> None:
    """When decide_next_phase keeps us in the same phase, the log must say
    WHY by listing the still-pending chat-field paths.

    GoGuide regression: a user finished the language phase by their
    reading of the chat, but the wizard kept asking "what next?". With
    no log explaining which fields were still pending, the root cause
    was invisible. The fix adds a `status=stay, reason=phase_incomplete,
    pending_fields=[...]` log on the stay path.
    """
    import logging

    state = _intake()
    # Only ONE chat field marked pending; everything else missing from
    # field_status — both should appear in the pending list.
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "pending"}

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)

    assert nxt == "language"

    stay_records = [
        rec for rec in caplog.records
        if getattr(rec, "operation", None) == "router.decide_next_phase"
        and getattr(rec, "status", None) == "stay"
        and getattr(rec, "reason", None) == "phase_incomplete"
    ]
    assert stay_records, (
        "Expected a router.decide_next_phase 'stay' log with "
        "reason=phase_incomplete; got none. Without this log, 'why is the "
        "phase not advancing?' is invisible in production."
    )
    rec = stay_records[-1]
    pending_fields = getattr(rec, "pending_fields", None)
    assert isinstance(pending_fields, list) and pending_fields
    assert any(
        "agent_core.preprocessing.nlu_processor.intents" in path
        for path in pending_fields
    )
    pending_count = getattr(rec, "pending_field_count", None)
    assert isinstance(pending_count, int) and pending_count >= 1


def test_knowledge_phase_blocked_until_azure_decision_recorded():
    """Knowledge phase stays open even when every chat field is answered,
    until the LLM captures the Azure-Blob decision via update_intake.

    Tour Pal regression: the LLM happily skipped the post-KB Azure
    question and the wizard advanced to memory without the deploy form
    ever learning whether Azure credentials were needed. The router
    now gates knowledge-phase completion on ``state.azure_blob_decided``.
    """
    state = _intake(has_kb=True)
    # Every chat field in the knowledge phase that applies is answered.
    field_status = _answered_field_status_for_phase("knowledge", state)

    # azure_blob_decided defaults to False → phase NOT complete → stay.
    assert decide_next_phase("knowledge", state, accumulator={}, field_status=field_status) == "knowledge"

    # Simulate the LLM recording the Azure answer via update_intake. We
    # set the flag directly here (the router.on_intake_update path is
    # covered by test_router_on_intake_update.py).
    state.azure_blob_decided = True
    assert decide_next_phase("knowledge", state, accumulator={}, field_status=field_status) != "knowledge"


def test_knowledge_phase_advances_without_azure_when_no_kb():
    """When has_kb=False, the Azure question is irrelevant — knowledge
    phase advancement does NOT gate on azure_blob_decided.
    """
    state = _intake(has_kb=False)
    field_status = _answered_field_status_for_phase("knowledge", state)
    # azure_blob_decided=False (default) — but has_kb=False, so the gate
    # is bypassed and the phase advances normally.
    nxt = decide_next_phase("knowledge", state, accumulator={}, field_status=field_status)
    assert nxt != "knowledge"


def test_stay_log_surfaces_azure_blob_decision_blocker(caplog) -> None:
    """When the knowledge phase stays open ONLY because the Azure
    decision is missing, the stay log must surface it as a pending
    field so future debugging is not a guessing game.
    """
    import logging

    state = _intake(has_kb=True)
    field_status = _answered_field_status_for_phase("knowledge", state)

    with caplog.at_level(logging.INFO, logger="dev_kit.agent.router"):
        nxt = decide_next_phase("knowledge", state, accumulator={}, field_status=field_status)

    assert nxt == "knowledge"
    stay_records = [
        rec for rec in caplog.records
        if getattr(rec, "operation", None) == "router.decide_next_phase"
        and getattr(rec, "status", None) == "stay"
    ]
    assert stay_records, "expected at least one stay log record"
    pending = getattr(stay_records[-1], "pending_fields", []) or []
    assert any("uses_azure_blob" in p for p in pending), (
        f"expected Azure decision in pending list; got {pending!r}"
    )


def test_on_intake_update_sets_azure_blob_decided():
    """router.on_intake_update must flip azure_blob_decided to True
    whenever the LLM records the user's answer — both True and False.
    """
    from dev_kit.agent.router import on_intake_update

    # Case 1: user said yes (True).
    state = _intake(has_kb=True)
    assert state.azure_blob_decided is False
    on_intake_update("uses_azure_blob", True, state, accumulator={"agent_core": {}, "knowledge_engine": {}}, field_status={})
    assert state.uses_azure_blob is True
    assert state.azure_blob_decided is True

    # Case 2: user said no (False). The "value change" path is a noop
    # (False → False default), but the decision-flag flip must still
    # happen.
    state = _intake(has_kb=True)
    on_intake_update("uses_azure_blob", False, state, accumulator={"agent_core": {}, "knowledge_engine": {}}, field_status={})
    assert state.uses_azure_blob is False
    assert state.azure_blob_decided is True
