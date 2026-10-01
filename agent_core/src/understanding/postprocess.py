"""
agent_core/src/understanding/postprocess.py

Deterministic post-processing of a DialogueActResult (NLU dialogue-acts spec
§6.1–§6.4, §6.6, §6.7): normalise and accept slots, resolve the option
reference, gate termination, derive the routing intent, count off-track turns.
Pure functions. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import re
from typing import Any

from src.conditions import evaluate_condition
from src.understanding.config import ActIntentRule, DialogueActConfig, SlotSpec
from src.understanding.frame import option_label
from src.understanding.models import DialogueActResult, ResolvedReference, SlotRejection, UnresolvedReference
from src.workflow_loader import PendingQuestion

_DIGITS = re.compile(r"^\s*\d+\s*$")


def _normalise_one(spec: SlotSpec, value: Any) -> tuple[Any, str | None]:
    """Normalise one slot value.

    Args:
        spec: The slot's configuration.
        value: Raw non-None value from the model.

    Returns:
        ``(value, None)`` on success or ``(None, reason)`` on rejection.
    """
    if spec.type == "int":
        if isinstance(value, bool):
            return None, "not_integer"
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and _DIGITS.match(value):
            number = int(value.strip())
        else:
            return None, "not_integer"
        if (spec.min is not None and number < spec.min) or (spec.max is not None and number > spec.max):
            return None, "out_of_range"
        return number, None
    if spec.type == "enum":
        return (value, None) if value in spec.values else (None, "not_in_enum")
    text = str(value).strip()
    if not text:
        return None, "empty"
    if spec.normalise == "title":
        text = text.title()
    elif spec.normalise == "lower":
        text = text.lower()
    return text, None


def normalise_slots(raw: dict, cfg: DialogueActConfig) -> tuple[dict, list[SlotRejection]]:
    """Validate and normalise slot values per config (spec §6.1).

    Args:
        raw: Slot name → raw value (None means "not said").
        cfg: Parsed dialogue-act config.

    Returns:
        (normalised slot name → value, rejections). None values and unknown
        slot names are skipped without a rejection.
    """
    ok: dict = {}
    rejected: list[SlotRejection] = []
    for name, value in (raw or {}).items():
        spec = cfg.slots.get(name)
        if spec is None or value is None:
            continue
        normalised, reason = _normalise_one(spec, value)
        if reason:
            rejected.append(SlotRejection(slot=name, value=value, reason=f"normalise:{reason}"))
        else:
            ok[name] = normalised
    return ok, rejected


def accept_slots(slots: dict, cfg: DialogueActConfig, pending_id: str | None,
                 acts: tuple[str, ...]) -> tuple[dict, list[SlotRejection]]:
    """Keep pending-scoped slots only while their question is pending (spec §6.2).

    Args:
        slots: Normalised slots.
        cfg: Parsed config.
        pending_id: Resolved pending id, or None.
        acts: The turn's acts; ``correct`` lifts the restriction.

    Returns:
        (accepted slots, ``not_pending`` rejections).
    """
    ok: dict = {}
    rejected: list[SlotRejection] = []
    for name, value in slots.items():
        scope = cfg.slots[name].accept_when_pending
        if scope and pending_id not in scope and "correct" not in acts:
            rejected.append(SlotRejection(slot=name, value=value, reason="not_pending"))
        else:
            ok[name] = value
    return ok, rejected


def resolve_reference(option: int | None, pending: PendingQuestion | None,
                      rows: list[dict]) -> tuple[ResolvedReference | None, UnresolvedReference | None]:
    """Map an offered-option number to its row id (spec §6.3).

    Args:
        option: 1-based option number from NLU, or None.
        pending: Resolved pending question; only one with ``options_from`` resolves.
        rows: The rows rendered as ``offered`` this turn (same entry, same order).

    Returns:
        (resolved, None), (None, unresolved), or (None, None) when there is
        nothing to resolve.
    """
    if option is None or pending is None or pending.options_from is None:
        return None, None
    if not rows:
        return None, UnresolvedReference(option=option, offered=0, reason="no_options")
    if not 1 <= option <= len(rows):
        return None, UnresolvedReference(option=option, offered=len(rows), reason="out_of_range")
    row = rows[option - 1]
    of = pending.options_from
    row_id = row.get(of.id_field)
    if row_id in (None, ""):
        return None, UnresolvedReference(option=option, offered=len(rows), reason="missing_id")
    return ResolvedReference(option=option, id=str(row_id), label=option_label(row, of.fields),
                             id_field=of.id_field), None


_OFF_TRACK_RELATIONS = ("unrelated", "unclear")


def gate_passes(cfg: DialogueActConfig, pending_id: str | None, state: dict) -> bool:
    """True when any termination-gate item holds (spec §6.7).

    Args:
        cfg: Parsed config.
        pending_id: Resolved pending id, or None.
        state: Merged routing state.

    Returns:
        True if a gated row may fire this turn.
    """
    for item in cfg.gate:
        if item.pending is not None:
            if pending_id == item.pending:
                return True
        elif item.condition is not None and evaluate_condition(item.condition, state or {}):
            return True
    return False


def derive_intent(dialogue: DialogueActResult, pending_id: str | None, cfg: DialogueActConfig, *,
                  gate_ok: bool, resolved: bool) -> tuple[str, ActIntentRule | None]:
    """First matching act_intents row → routing intent (spec §6.4).

    A row matches when the turn's acts contain all of the row's acts, and the
    row's pending / relation / topic (each optional) equal the turn's. A row
    containing ``select`` also needs a resolved reference; a gated row needs
    ``gate_ok``. Unmatched turns are ``any_input``.

    Args:
        dialogue: Validated NLU result.
        pending_id: Resolved pending id, or None.
        cfg: Parsed config.
        gate_ok: Result of :func:`gate_passes`.
        resolved: Whether the option reference resolved.

    Returns:
        (intent, matched rule or None).
    """
    acts = set(dialogue.acts)
    for rule in cfg.act_intents:
        if rule.acts and not set(rule.acts) <= acts:
            continue
        if rule.pending is not None and rule.pending != pending_id:
            continue
        if rule.relation is not None and rule.relation != dialogue.relation:
            continue
        if rule.topic is not None and rule.topic != dialogue.topic:
            continue
        if "select" in rule.acts and not resolved:
            continue
        if rule.gated and not gate_ok:
            continue
        return rule.intent, rule
    return "any_input", None


def next_off_track(prev: int, relation: str, cfg: DialogueActConfig, *,
                   is_fallback: bool) -> tuple[int, bool]:
    """Advance the consecutive off-track counter (spec §6.6).

    Args:
        prev: Current ``off_track_count``.
        relation: This turn's relation.
        cfg: Parsed config.
        is_fallback: True when NLU failed (a system failure never counts).

    Returns:
        (new count, tripped) — tripped when the count reaches the threshold.
    """
    if is_fallback:
        return prev, False
    if relation == "answers_pending":
        return 0, False
    if relation in _OFF_TRACK_RELATIONS:
        count = prev + 1
        return count, count >= cfg.off_track_threshold
    return prev, False
