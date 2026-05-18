"""Router — handles intake updates and decides phase transitions.

Contains three mutation handlers used by the phase driver:
- on_intake_update: cascades an intake-field change through FIELD_RULES
- decide_next_phase: selects the next wizard phase at end-of-turn
- on_config_update: applies a user chat answer to the accumulator with mirror validation

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §7.
"""
from __future__ import annotations

import logging
from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FIELD_RULES_PHASES_VALID, FieldRule
from dev_kit.agent.intake_state import BINARY_INTAKE_FIELDS, IntakeState
from dev_kit.agent.path_ops import clear_path, get_path, set_path
from dev_kit.agent.skeleton import _SKIP, eval_expr, eval_rule, get_framework_default

logger = logging.getLogger(__name__)

# Canonical phase order — mirrors the PHASES list in the design doc (§5).
PHASE_ORDER = (
    "tier", "language", "knowledge", "memory", "user_state",
    "trust", "tools", "workflow", "observability", "reach", "review",
)


def _earlier_phase(a: str | None, b: str | None) -> str | None:
    """Return the earlier of two phase names according to PHASE_ORDER.

    Args:
        a: First phase name, or None.
        b: Second phase name, or None.

    Returns:
        The phase that comes first in PHASE_ORDER, or the non-None argument
        if one of them is None. Returns None if both are None.
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if PHASE_ORDER.index(a) <= PHASE_ORDER.index(b) else b


def on_intake_update(
    field: str,
    new_value: Any,
    state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Apply an intake field change and cascade through FIELD_RULES.

    Mutates ``state`` in-place (including calling ``state.touch()``), then
    re-evaluates every FIELD_RULE that lists ``field`` in its ``invalidated_by``
    list:

    - ``predetermined`` rules: re-runs the ``rule`` expression and updates or
      clears the accumulator path.
    - ``chat`` rules: marks status ``"needs_re_asking"`` (or ``"not_applicable"``
      if ``applies_if`` is now false) and clears the accumulator path.
    - ``derived`` rules: noted but no accumulator action taken (renderer recomputes).

    Args:
        field: Name of the IntakeState field being changed (e.g., ``"has_kb"``).
        new_value: The new value to assign to ``state.<field>``.
        state: IntakeState instance — mutated in-place.
        accumulator: Per-block YAML dicts — mutated in-place.
        field_status: Field status registry keyed by full dotted path — mutated
            in-place.

    Returns:
        A dict with the following keys:

        - ``ok`` (bool): Always True.
        - ``noop`` (bool): Present and True when ``old_value == new_value``.
        - ``field`` (str): The field name that changed.
        - ``old_value``: The previous value of the field.
        - ``new_value``: The new value of the field.
        - ``affected_count`` (int): Number of FIELD_RULES entries affected.
        - ``earliest_affected_phase`` (str | None): The earliest phase name
          containing an affected chat field, or None if no chat fields were
          affected.

    Raises:
        AttributeError: If ``field`` is not a valid attribute of ``IntakeState``.
    """
    old_value = getattr(state, field)

    # Record the user's explicit answer to a tier binary field BEFORE the noop
    # check below. `app.py` seeds all 7 binary flags to False at project
    # creation; without this ordering, an explicit "no" answer (False → False)
    # would short-circuit as a noop and the flag would never be marked seen.
    # The result was tier never completing for projects with any "no" answers,
    # and the LLM then hallucinating phase transitions in its prose. Recording
    # the answer here decouples "user gave an answer" from "value changed".
    if field in BINARY_INTAKE_FIELDS and field not in state.binary_flags_seen:
        state.binary_flags_seen.append(field)
        if not state.completed and BINARY_INTAKE_FIELDS.issubset(set(state.binary_flags_seen)):
            state.completed = True
            state.touch()
            logger.info(
                "router.intake_completed",
                extra={
                    "operation": "router.intake_completed",
                    "status": "success",
                    "binary_flags_seen": list(state.binary_flags_seen),
                },
            )

    # When the LLM records the user's answer to the Azure-Blob question,
    # flip `azure_blob_decided=True` so `_is_phase_complete("knowledge")`
    # can release the phase. The boolean `uses_azure_blob` alone is
    # ambiguous (False = default OR False = answered no), so we track the
    # decision flag separately. See the comment on the field in
    # intake_state.py for the full rationale.
    if field == "uses_azure_blob" and not state.azure_blob_decided:
        state.azure_blob_decided = True
        state.touch()
        logger.info(
            "router.azure_blob_decided",
            extra={
                "operation": "router.azure_blob_decided",
                "status": "success",
                "uses_azure_blob": new_value,
            },
        )

    if old_value == new_value:
        return {
            "ok": True,
            "noop": True,
            "field": field,
            "new_value": new_value,
            "binary_flag_recorded": field in BINARY_INTAKE_FIELDS,
        }

    setattr(state, field, new_value)
    state.touch()

    affected: list[tuple[str, FieldRule]] = [
        (full_path, rule)
        for full_path, rule in AGGREGATED_FIELD_RULES.items()
        if field in rule.invalidated_by
    ]

    earliest_phase: str | None = None
    for full_path, rule in affected:
        block, relative_path = full_path.split(".", 1)
        applies = eval_expr(rule.applies_if, state)

        if rule.category == "predetermined":
            old_predetermined = get_path(accumulator[block], relative_path)
            if applies and rule.rule:
                value = eval_rule(rule.rule, state)
                fw_default = get_framework_default(full_path)
                if value is not _SKIP and value is not None and value != fw_default:
                    set_path(accumulator[block], relative_path, value)
                else:
                    clear_path(accumulator[block], relative_path)
                    value = None
            else:
                clear_path(accumulator[block], relative_path)
                value = None
            # Point 4: log each predetermined field recomputed
            new_predetermined = get_path(accumulator[block], relative_path)
            logger.info(
                "router.predetermined_recomputed",
                extra={
                    "operation": "router.predetermined_recomputed",
                    "status": "success",
                    "path": full_path,
                    "triggered_by": field,
                    "old_value": old_predetermined,
                    "new_value": new_predetermined,
                },
            )

        elif rule.category == "chat":
            if not applies:
                clear_path(accumulator[block], relative_path)
                field_status[full_path] = "not_applicable"
            else:
                # `needs_re_asking` only makes sense for fields the user has
                # actually answered (or had seeded as `not_applicable` and is
                # now becoming applicable). For fields that are `pending` or
                # have never been entered into field_status, leave them
                # alone — there is no answer to invalidate, and marking 43
                # pristine fields as "needs re-asking" before the chat has
                # started misleads both the LLM (which thinks it must
                # re-ask) and the UI counters.
                current = field_status.get(full_path)
                if current == "answered":
                    field_status[full_path] = "needs_re_asking"
                    earliest_phase = _earlier_phase(earliest_phase, rule.phase)
                    logger.info(
                        "router.field_marked_needs_re_asking",
                        extra={
                            "operation": "router.field_marked_needs_re_asking",
                            "status": "success",
                            "path": full_path,
                            "triggered_by": field,
                            "reason": "answered_invalidated",
                            "previous_status": "answered",
                        },
                    )
                elif current == "not_applicable":
                    # Field just transitioned from not_applicable →
                    # applicable. Treat it like a fresh skeleton field
                    # rather than blindly marking `needs_re_asking`:
                    #   - non-None default → write it + mark `answered`
                    #     (the framework default IS the answer, same as
                    #     `skeleton.build_skeleton`)
                    #   - auto_answer → mark `answered`
                    #   - otherwise → `pending` (LLM will propose when
                    #     it gets to that phase)
                    # The earlier blanket `needs_re_asking` created false
                    # work: e.g. `memory_layer.state.persistent.merge_on_session_end`
                    # (default=[]) was forced into a confirmation loop
                    # the moment `needs_persistent_user_data` flipped on,
                    # blocking the memory phase from advancing.
                    new_status: str
                    if rule.default is not None:
                        set_path(accumulator[block], relative_path, rule.default)
                        new_status = "answered"
                    elif getattr(rule, "auto_answer", False):
                        new_status = "answered"
                    else:
                        new_status = "pending"
                    field_status[full_path] = new_status
                    logger.info(
                        "router.field_status_reset_from_not_applicable",
                        extra={
                            "operation": "router.field_status_reset",
                            "status": "success",
                            "path": full_path,
                            "triggered_by": field,
                            "reason": "not_applicable_became_applicable",
                            "previous_status": "not_applicable",
                            "new_status": new_status,
                        },
                    )
                # else (`pending` / missing): no action — skeleton will fill
                # in the baseline at tier completion; the field has nothing
                # to be re-asked about because it has never been answered.

        elif rule.category == "derived":
            # Flag for renderer recompute; derived-stale tracking is Phase 9 work.
            pass

    # Point 1: summary log — promote DEBUG → INFO, add old_value/new_value
    logger.info(
        "router.on_intake_update",
        extra={
            "operation": "router.on_intake_update",
            "status": "success",
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "affected_count": len(affected),
            "earliest_affected_phase": earliest_phase,
        },
    )

    return {
        "ok": True,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "affected_count": len(affected),
        "earliest_affected_phase": earliest_phase,
    }


# Per-phase relevance predicates. A phase is irrelevant when no chat fields in
# it could ever apply for the current IntakeState — skip it in phase navigation.
# "memory" is always relevant (every deployment has at least a session).
PHASE_RELEVANCE: dict[str, Any] = {
    "tier": lambda s: True,
    "language": lambda s: True,
    "knowledge": lambda s: s.has_kb,
    "memory": lambda s: True,
    "user_state": lambda s: s.is_companion_style,
    "trust": lambda s: True,
    "tools": lambda s: s.has_external_tools,
    "workflow": lambda s: True,
    "observability": lambda s: True,
    "reach": lambda s: True,
    "review": lambda s: True,
}


def _phase_for_path(path: str) -> str | None:
    """Look up the phase name for a full dotted path via AGGREGATED_FIELD_RULES.

    Args:
        path: Full dotted path, e.g. ``"agent_core.preprocessing.nlu_processor.intents"``.

    Returns:
        The phase name string, or None if the path is not in AGGREGATED_FIELD_RULES
        or the rule has no phase.
    """
    rule = AGGREGATED_FIELD_RULES.get(path)
    return rule.phase if rule else None


def _earliest_phase_with_needs_re_asking(field_status: dict[str, str]) -> str | None:
    """Scan field_status for the earliest needs_re_asking phase.

    Args:
        field_status: Dict of full path → status.

    Returns:
        The earliest phase name that has at least one ``needs_re_asking`` field,
        or None if no such field exists.
    """
    earliest: str | None = None
    for path, status in field_status.items():
        if status != "needs_re_asking":
            continue
        phase = _phase_for_path(path)
        if phase is None:
            continue
        earliest = _earlier_phase(earliest, phase)
    return earliest


def _is_phase_complete(
    phase: str,
    state: IntakeState,
    field_status: dict[str, str],
) -> bool:
    """Return True when every applicable chat field in ``phase`` is answered.

    Special case: the tier (intake) phase has no chat fields — its
    completeness is gated on ``state.completed``, which flips True once all
    7 BINARY_INTAKE_FIELDS have been captured via update_intake.

    Args:
        phase: Phase name to check.
        state: Current IntakeState (used for applies_if evaluation).
        field_status: Current per-path statuses.

    Returns:
        True if all applicable chat fields in this phase are answered; False
        if any applicable field is pending, needs_re_asking, or not yet in
        field_status.
    """
    if phase == "tier":
        return state.completed
    # Knowledge phase has an out-of-band intake question (Azure Blob Storage)
    # that isn't part of FIELD_RULES — the LLM must ask it AFTER the KB chat
    # fields are answered, then call `update_intake(field="uses_azure_blob",
    # ...)`. Without this gate the LLM has no router-side reason to ask the
    # question and skips it, leaving the deploy form unable to decide whether
    # to surface Azure credential inputs.
    if phase == "knowledge" and state.has_kb and not state.azure_blob_decided:
        return False
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat" or rule.phase != phase:
            continue
        if not eval_expr(rule.applies_if, state):
            continue
        # Default to "pending" — a field absent from field_status was never
        # initialised (build_skeleton hasn't run yet), so the phase is NOT
        # complete. (Old default of "answered" caused premature advancement
        # for every phase pre-skeleton.)
        status = field_status.get(full_path, "pending")
        if status != "answered":
            return False
    return True


def _next_relevant_phase(current: str, state: IntakeState) -> str | None:
    """Walk PHASE_ORDER forward from ``current``, returning the first relevant phase.

    Args:
        current: The current phase name.
        state: IntakeState for relevance evaluation.

    Returns:
        The name of the next relevant phase, or None if no further relevant
        phase exists (wizard is complete).
    """
    idx = PHASE_ORDER.index(current)
    for nxt in PHASE_ORDER[idx + 1:]:
        if PHASE_RELEVANCE[nxt](state):
            return nxt
        # Point 7: log each skipped phase with the reason it was irrelevant
        _reason = _phase_skip_reason(nxt, state)
        logger.info(
            "router.phase_skipped",
            extra={
                "operation": "router.phase_skipped",
                "status": "skipped",
                "skipped_phase": nxt,
                "reason": _reason,
            },
        )
    return None


def _phase_skip_reason(phase: str, state: IntakeState) -> str:
    """Return a human-readable reason why ``phase`` is not relevant for ``state``.

    Args:
        phase: Phase name to explain.
        state: Current IntakeState.

    Returns:
        A short reason string (e.g. ``"has_kb=false"``).
    """
    _SKIP_REASONS: dict[str, str] = {
        "knowledge": "has_kb=false",
        "user_state": "is_companion_style=false",
        "tools": "has_external_tools=false",
    }
    return _SKIP_REASONS.get(phase, f"phase={phase}_not_relevant")


def decide_next_phase(
    current_phase: str,
    state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> str:
    """Decide which phase the wizard should be in for the next turn.

    Rules applied in order:

    1. **Backtrack**: if any field has ``needs_re_asking`` in an earlier phase
       than ``current_phase``, return that earlier phase.
    2. **Advance**: if ``current_phase`` is complete (all applicable chat fields
       answered), return the next relevant phase.  If no further relevant phase
       exists, stay on ``current_phase`` (wizard complete).
    3. **Stay**: the current phase is not yet complete; return ``current_phase``.

    Args:
        current_phase: The phase the wizard is currently in.
        state: Current IntakeState (used for applies_if and relevance evaluation).
        accumulator: Per-block YAML dicts (read-only here; not mutated).
        field_status: Per-field status dict (read-only here; not mutated).

    Returns:
        The phase name for the next turn.

    Raises:
        ValueError: If ``current_phase`` is not in PHASE_ORDER.
    """
    if current_phase not in PHASE_ORDER:
        raise ValueError(
            f"Unknown phase {current_phase!r}; must be one of {PHASE_ORDER}"
        )

    invalidated = _earliest_phase_with_needs_re_asking(field_status)
    if invalidated and PHASE_ORDER.index(invalidated) < PHASE_ORDER.index(current_phase):
        # Point 6: backtrack transition
        logger.warning(
            "router.decide_next_phase",
            extra={
                "operation": "router.decide_next_phase",
                "status": "backtrack",
                "from_phase": current_phase,
                "to_phase": invalidated,
                "reason": "invalidated",
                "triggered_by": None,
            },
        )
        return invalidated

    if _is_phase_complete(current_phase, state, field_status):
        nxt = _next_relevant_phase(current_phase, state)
        result = nxt if nxt else current_phase
        if result != current_phase:
            # Point 5: forward transition
            logger.info(
                "router.decide_next_phase",
                extra={
                    "operation": "router.decide_next_phase",
                    "status": "advance",
                    "from_phase": current_phase,
                    "to_phase": result,
                    "reason": "phase_complete",
                },
            )
        else:
            # Phase is complete but no further relevant phase exists — the
            # wizard has reached the terminal phase (review).
            logger.info(
                "router.decide_next_phase",
                extra={
                    "operation": "router.decide_next_phase",
                    "status": "stay",
                    "from_phase": current_phase,
                    "to_phase": current_phase,
                    "reason": "wizard_complete",
                },
            )
        return result

    # Phase is NOT complete. Surface which fields are still blocking the
    # transition so the user and the developer can see WHY the router
    # decided to stay (this was the GoGuide regression: "phase transition
    # is not happening" was invisible without this log).
    pending = _pending_field_paths_for_phase(current_phase, state, field_status)
    logger.info(
        "router.decide_next_phase",
        extra={
            "operation": "router.decide_next_phase",
            "status": "stay",
            "from_phase": current_phase,
            "to_phase": current_phase,
            "reason": "phase_incomplete",
            "pending_field_count": len(pending),
            "pending_fields": pending[:25],  # cap log payload at 25 paths
        },
    )
    return current_phase


def _pending_field_paths_for_phase(
    phase: str, state: IntakeState, field_status: dict[str, str]
) -> list[str]:
    """Return the list of applicable chat-field paths in ``phase`` that are not
    yet ``answered``.

    Mirrors the iteration in ``_is_phase_complete`` but collects the paths
    instead of returning the first failure. Used for the structured "stay"
    log emitted by ``decide_next_phase`` so it is obvious WHICH fields are
    blocking advancement.
    """
    if phase == "tier":
        # Tier has no chat fields; completeness gates on state.completed,
        # which the caller already evaluated.
        return [] if state.completed else ["intake_state.binary_flags_seen (< 7 captured)"]
    pending: list[str] = []
    # Mirror the out-of-band gate in `_is_phase_complete` so the "stay"
    # log surfaces the Azure decision as a blocker.
    if phase == "knowledge" and state.has_kb and not state.azure_blob_decided:
        pending.append("intake_state.uses_azure_blob [not_yet_asked]")
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat" or rule.phase != phase:
            continue
        if not eval_expr(rule.applies_if, state):
            continue
        status = field_status.get(full_path, "pending")
        if status != "answered":
            pending.append(f"{full_path} [{status}]")
    return pending


def on_config_update(
    path: str,
    value: Any,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Apply a user's chat answer to the accumulator with mirror validation.

    **Validation-before-write guarantee** (mirrors the OLD ConfigAccumulator
    pattern from main; preserved verbatim so wrong values never reach the
    rendered YAML):

    1. Look up the FieldRule. Raise ``ValueError`` if the path is unknown
       or the rule's category is not ``"chat"``.
    2. Build a deep-copied **candidate** of ``accumulator[block]``.
    3. Apply ``set_path(candidate, relative_path, value)`` to the candidate.
    4. Validate the candidate via ``validate_partial(block, candidate)``.
    5. If validation reports errors, raise ``ValueError`` — ``accumulator``
       is **never** mutated, so no bad value can leak into the per-turn
       YAML render at the end of ``phase_driver.run_turn``.
    6. Only on success: commit the candidate (``accumulator[block] = candidate``)
       and mark ``field_status[path] = "answered"``.

    This is intentionally a candidate-copy commit rather than a
    mutate-then-revert. With mutate-then-revert, an unexpected exception
    raised mid-validation (e.g. a bug in a Pydantic validator) would leave
    the live accumulator in a partial state; with candidate-copy commit,
    the live accumulator is touched exactly once, after validation
    succeeds.

    Persistence (saving accumulator/field_status to disk) is the caller's
    responsibility — this function only mutates the in-memory dicts.

    Args:
        path: Full dotted path including block prefix, e.g.
            ``"agent_core.conversation.blocked_message"``.
        value: The user-provided value (raw Python type).
        accumulator: Per-block YAML dicts — mutated in-place ONLY when
            validation passes.
        field_status: Field status registry — mutated in-place on success
            (set to ``"answered"``), left unchanged on failure.

    Returns:
        ``{"ok": True, "path": path, "value": value}`` on success.

    Raises:
        ValueError: If ``path`` is not in AGGREGATED_FIELD_RULES.
        ValueError: If the rule's category is not ``"chat"``.
        ValueError: If ``validate_partial`` reports constraint violations
            (accumulator is left untouched before raising).
    """
    # Lazy import to avoid circular import risk; validation module is heavy.
    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415
    import copy  # noqa: PLC0415

    rule = AGGREGATED_FIELD_RULES.get(path)
    if rule is None:
        raise ValueError(f"unknown path: {path!r}")

    if rule.category != "chat":
        raise ValueError(
            f"path {path!r} is not a chat field (category={rule.category!r}); "
            "only chat fields are user-writeable via the wizard"
        )

    block, relative_path = path.split(".", 1)

    # Candidate-copy commit pattern: build the would-be merged result on a
    # deep copy first, validate it, and only swap in if validation passes.
    # The live accumulator is never mutated until the commit line below.
    candidate = copy.deepcopy(accumulator.get(block, {}))
    set_path(candidate, relative_path, value)

    errors = validate_partial(block, candidate)
    if errors:
        _section_on_fail = relative_path.split(".")[0] if "." in relative_path else relative_path
        logger.warning(
            "router.on_config_update",
            extra={
                "operation": "router.on_config_update",
                "status": "failure",
                "block": block,
                "section": _section_on_fail,
                "paths_written": [],
                "validation_errors": errors,
            },
        )
        raise ValueError(
            f"Validation failed for {path!r}: {'; '.join(errors)}"
        )

    # Commit the validated candidate. After this line both `accumulator`
    # and `field_status` reflect the successful write.
    accumulator[block] = candidate
    field_status[path] = "answered"

    # Point 2: log config field updated — promote DEBUG → INFO, add required fields
    _block_part, _section_part = block, relative_path.split(".")[0] if "." in relative_path else relative_path
    logger.info(
        "router.on_config_update",
        extra={
            "operation": "router.on_config_update",
            "status": "success",
            "block": block,
            "section": _section_part,
            "paths_written": [path],
            "validation_errors": [],
        },
    )

    return {"ok": True, "path": path, "value": value}


__all__ = [
    "on_intake_update",
    "decide_next_phase",
    "on_config_update",
    "PHASE_ORDER",
    "PHASE_RELEVANCE",
]
