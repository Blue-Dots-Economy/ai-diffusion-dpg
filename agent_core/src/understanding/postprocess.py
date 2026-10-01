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

from src.understanding.config import DialogueActConfig, SlotSpec
from src.understanding.frame import option_label
from src.understanding.models import ResolvedReference, SlotRejection, UnresolvedReference
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
