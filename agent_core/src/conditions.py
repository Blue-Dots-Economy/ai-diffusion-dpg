"""
agent_core/src/conditions.py

Routing-condition evaluation shared by workflow routing (orchestrator) and the
understanding package (pending questions, termination gate). One evaluator so
the two can never disagree about what a condition means.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any, Iterable


def evaluate_condition(condition: Any, state: dict) -> bool:
    """Evaluate one routing condition against a state dict.

    A dotted field ``parent.child`` reads ``state[parent][child]``, defaulting
    to 0 when the parent is missing or not a dict (the
    ``subagent_entry_count.<id>`` convention).

    Args:
        condition: Object with ``field``, ``operator`` (eq | not_eq | in | lt | gt)
            and ``value`` attributes.
        state: Merged session/profile state.

    Returns:
        True if the condition holds. Unknown operators and non-numeric
        comparisons return False.
    """
    field = condition.field
    if "." in field:
        parent, child = field.split(".", 1)
        container = (state or {}).get(parent, {})
        value = container.get(child, 0) if isinstance(container, dict) else 0
    else:
        value = (state or {}).get(field)

    op = condition.operator
    cond_val = condition.value
    if op == "eq":
        return value == cond_val
    if op == "not_eq":
        return value != cond_val
    if op == "in":
        return value in (cond_val if isinstance(cond_val, list) else [cond_val])
    if op in ("lt", "gt"):
        try:
            left, right = float(value or 0), float(cond_val)
        except (TypeError, ValueError):
            return False
        return left < right if op == "lt" else left > right
    return False


def all_conditions(conditions: Iterable[Any], state: dict) -> bool:
    """Return True when every condition holds (True for an empty iterable).

    Args:
        conditions: Conditions as accepted by :func:`evaluate_condition`.
        state: Merged session/profile state.

    Returns:
        True if all conditions hold.
    """
    return all(evaluate_condition(c, state) for c in conditions)
