"""
agent_core/src/understanding/slot_writer.py

Builds the list of state writes a turn's understanding implies (NLU
dialogue-acts spec §6.3, §6.5, §6.6). Pure: the orchestrator applies the
writes through its existing Memory Layer helpers on both paths.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from src.understanding.config import DialogueActConfig
from src.understanding.models import ResolvedReference, SlotUpdate, StateWrite
from src.understanding.precedence import PROVENANCE_KEY
from src.workflow_loader import PendingQuestion

_SEEDS = (None, "", [], {}, 0, "0")


def plan_writes(*, accepted: dict, cfg: DialogueActConfig, state: dict, session: dict,
                pending: PendingQuestion | None, resolved: ResolvedReference | None,
                extras: tuple[tuple[str, str], ...], off_track_count: int
                ) -> tuple[list[StateWrite], list[SlotUpdate]]:
    """Plan the turn's writes.

    Args:
        accepted: Accepted slot name → normalised value (never None).
        cfg: Parsed config (state keys, entity scope).
        state: Merged routing state, for change detection.
        session: Raw session state, for provenance / extras / counter.
        pending: Resolved pending question, or None.
        resolved: Resolved option reference, or None.
        extras: Ad-hoc (key, value) pairs.
        off_track_count: New off-track counter value.

    Returns:
        (writes, updates) — updates list only changes over a non-seed value.
        Provenance is recorded only for session-scope writes; a persistent
        write lands in the profile, which already wins profile-first.
    """
    writes: list[StateWrite] = []
    updates: list[SlotUpdate] = []
    provenance = [k for k in (session.get(PROVENANCE_KEY) or []) if isinstance(k, str)]
    added = False
    for slot, value in accepted.items():
        key = cfg.state_key(slot)
        old = state.get(key)
        if old == value:
            continue
        writes.append(StateWrite(cfg.entity_scope, key, value))
        if old not in _SEEDS:
            updates.append(SlotUpdate(key, old, value))
        if cfg.entity_scope == "session" and key not in provenance:
            provenance.append(key)
            added = True
    if added:
        writes.append(StateWrite("session", PROVENANCE_KEY, provenance))
    if resolved is not None and pending is not None and pending.resolves_to:
        writes.append(StateWrite("session", pending.resolves_to, resolved.id))
    if extras:
        merged = dict(session.get("nlu_extras") or {})
        merged.update(dict(extras))
        writes.append(StateWrite("session", "nlu_extras", merged))
    if off_track_count != int(session.get("off_track_count") or 0):
        writes.append(StateWrite("session", "off_track_count", off_track_count))
    return writes, updates
