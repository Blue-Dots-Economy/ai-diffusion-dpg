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


__all__ = ["on_intake_update", "PHASE_ORDER"]
