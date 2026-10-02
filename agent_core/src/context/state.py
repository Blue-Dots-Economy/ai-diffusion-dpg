"""
agent_core/src/context/state.py
Renders the main LLM's <state> and <recent> blocks from session data
(Spec D §6.2–6.3). Pure; built in code, never by a model. Belongs to the
Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any

from src.understanding.frame import option_label

_SKIP_KEYS = {"attributes", "user_id"}


def is_collected(
    value: object, field: str = "", zero_seeds: frozenset[str] = frozenset()
) -> bool:
    """Whether a profile value counts as something the caller has told us.

    The previous check listed the empty sentinels explicitly — ``None``,
    ``""``, ``[]``, ``"[]"`` — which covers every string field, since those
    default to ``""``. It does not cover ``age``, the one integer field,
    whose unset default is ``0``.

    A zero therefore rendered under "Already collected — do NOT ask for any
    of these fields again", so the agent never asked the caller's age and
    sent ``age=0`` to the profile API, which rejects it as under-18.

    The string ``"0"`` is the same seed for the int fields in ``zero_seeds``
    (a copy that skipped Memory Layer's int coercion still reads ``"0"``). For
    any other field it is a real value, e.g. ``experience_years`` of zero.

    Args:
        value: A profile field value.
        field: The field's name.
        zero_seeds: Fields for which ``"0"`` is the unset seed.

    Returns:
        True when the value should be shown to the LLM as already collected.
    """
    if isinstance(value, bool):
        return value
    if value in (None, "", "[]"):
        return False
    if value == "0" and field in zero_seeds:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _collected_line(collected: dict, zero_seeds: frozenset[str] = frozenset()) -> str:
    items = [
        f"{k}={v}" for k, v in collected.items()
        if k not in _SKIP_KEYS and is_collected(v, k, zero_seeds)
    ]
    for attr in collected.get("attributes") or []:
        if isinstance(attr, dict) and attr.get("key") and attr.get("value"):
            items.append(f"{attr['key']}={attr['value']}")
    return " · ".join(items)


def render_state(
    *, phase: str, pending: Any, collected: dict, offered: list[dict], status: dict,
    zero_seeds: frozenset[str] = frozenset(),
) -> str:
    """Render <state>: phase, open question, collected values, offered options in read order, status.

    Args:
        phase: Subagent id the prompt is built for.
        pending: Resolved PendingQuestion for that subagent, or None.
        collected: Profile context dict (``_build_profile_context`` output).
        offered: Rows on offer, in stored (= resolver) order; [] when none.
        status: ``agent.state_fields`` key → session value.
        zero_seeds: Int fields whose string ``"0"`` is the unset seed (#436 D1);
            such a value is not listed as collected.

    Returns:
        Block text; lines with nothing to say are omitted.
    """
    lines = [f"phase: {phase}"] if phase else []
    if pending is not None:
        expects = f" — {pending.expects}" if getattr(pending, "expects", "") else ""
        lines.append(f"waiting for: {pending.id}{expects}")
    c = _collected_line(collected or {}, zero_seeds)
    if c:
        lines.append(f"collected (do not ask again): {c}")
    of = getattr(pending, "options_from", None) if pending is not None else None
    if of is not None and offered:
        labels = [f"{i}. {option_label(r, tuple(of.fields))}" for i, r in enumerate(offered, start=1)]
        lines.append("offered (read in this order): " + "; ".join(labels))
    s = " · ".join(f"{k}={v}" for k, v in (status or {}).items() if v is not None)
    if s:
        lines.append(f"status: {s}")
    return "\n".join(lines)


def render_recent(recent_turns: object, n: int) -> str:
    """Render <recent>: the last ``n`` exchanges verbatim; interrupted replies marked.

    Args:
        recent_turns: Session ``recent_turns`` list of {caller, bot, interrupted}.
        n: Exchanges to show; 0 renders nothing.

    Returns:
        Block text, or "".
    """
    if n <= 0 or not isinstance(recent_turns, list):
        return ""
    lines: list[str] = []
    for e in [e for e in recent_turns if isinstance(e, dict)][-n:]:
        if e.get("caller"):
            lines.append(f"caller: {e['caller']}")
        if e.get("bot"):
            label = "bot (caller heard only)" if e.get("interrupted") else "bot"
            lines.append(f"{label}: {e['bot']}")
    return "\n".join(lines)
