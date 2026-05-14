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
from dev_kit.agent.intake_state import IntakeState
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
    if old_value == new_value:
        return {"ok": True, "noop": True}

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
            if applies and rule.rule:
                value = eval_rule(rule.rule, state)
                fw_default = get_framework_default(full_path)
                if value is not _SKIP and value is not None and value != fw_default:
                    set_path(accumulator[block], relative_path, value)
                else:
                    clear_path(accumulator[block], relative_path)
            else:
                clear_path(accumulator[block], relative_path)

        elif rule.category == "chat":
            if not applies:
                clear_path(accumulator[block], relative_path)
                field_status[full_path] = "not_applicable"
            else:
                # If the field was not_applicable and we have a default,
                # seed the default and mark it needs_re_asking.
                if (
                    rule.default is not None
                    and field_status.get(full_path) == "not_applicable"
                ):
                    set_path(accumulator[block], relative_path, rule.default)
                field_status[full_path] = "needs_re_asking"
                earliest_phase = _earlier_phase(earliest_phase, rule.phase)

        elif rule.category == "derived":
            # Flag for renderer recompute; derived-stale tracking is Phase 9 work.
            pass

    logger.debug(
        "on_intake_update",
        extra={
            "operation": "router.on_intake_update",
            "status": "success",
            "field": field,
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

    A phase with no applicable chat fields is trivially complete.

    Args:
        phase: Phase name to check.
        state: Current IntakeState (used for applies_if evaluation).
        field_status: Current per-path statuses.

    Returns:
        True if all applicable chat fields in this phase are answered; False
        if any applicable field is pending, needs_re_asking, or not yet in
        field_status.
    """
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat" or rule.phase != phase:
            continue
        if not eval_expr(rule.applies_if, state):
            continue
        # Fields absent from field_status were never initialised — treat as
        # answered (they've been handled or do not require wizard input here).
        status = field_status.get(full_path, "answered")
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
    return None


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
        return invalidated

    if _is_phase_complete(current_phase, state, field_status):
        nxt = _next_relevant_phase(current_phase, state)
        return nxt if nxt else current_phase

    return current_phase


def on_config_update(
    path: str,
    value: Any,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Apply a user's chat answer to the accumulator with mirror validation.

    Steps:
    1. Split ``path`` into block and relative_path.
    2. Look up the FieldRule. Raise ValueError if absent or not a chat field.
    3. Write ``value`` via ``set_path``.
    4. Run ``validate_partial`` against the mirror schema. On failure, revert
       the write via ``clear_path`` and raise ValueError.
    5. Mark ``field_status[path] = "answered"``.
    6. Return ``{"ok": True, "path": path, "value": value}``.

    Persistence (saving accumulator/field_status to disk) is the caller's
    responsibility — this function only mutates the in-memory dicts.

    Args:
        path: Full dotted path including block prefix, e.g.
            ``"agent_core.conversation.blocked_message"``.
        value: The user-provided value (raw Python type).
        accumulator: Per-block YAML dicts — mutated in-place on success,
            reverted on validation failure.
        field_status: Field status registry — mutated in-place on success
            (set to ``"answered"``), left unchanged on failure.

    Returns:
        ``{"ok": True, "path": path, "value": value}`` on success.

    Raises:
        ValueError: If ``path`` is not in AGGREGATED_FIELD_RULES.
        ValueError: If the rule's category is not ``"chat"``.
        ValueError: If ``validate_partial`` reports constraint violations
            (accumulator is reverted before raising).
    """
    # Lazy import to avoid circular import risk; validation module is heavy.
    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415

    rule = AGGREGATED_FIELD_RULES.get(path)
    if rule is None:
        raise ValueError(f"unknown path: {path!r}")

    if rule.category != "chat":
        raise ValueError(
            f"path {path!r} is not a chat field (category={rule.category!r}); "
            "only chat fields are user-writeable via the wizard"
        )

    block, relative_path = path.split(".", 1)

    # Capture pre-write state so we can revert on validation failure.
    import copy
    pre_write_snapshot = copy.deepcopy(accumulator[block])

    set_path(accumulator[block], relative_path, value)

    errors = validate_partial(block, accumulator[block])
    if errors:
        # Revert — restore block to snapshot.
        accumulator[block] = pre_write_snapshot
        raise ValueError(
            f"Validation failed for {path!r}: {'; '.join(errors)}"
        )

    field_status[path] = "answered"

    logger.debug(
        "on_config_update",
        extra={
            "operation": "router.on_config_update",
            "status": "success",
            "path": path,
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
