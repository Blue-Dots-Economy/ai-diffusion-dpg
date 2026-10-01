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
OFF_TRACK_KEY = "off_track_count"
EXTRAS_KEY = "nlu_extras"


def stored_off_track_count(session: dict) -> int:
    """Read ``off_track_count`` tolerantly (a corrupted value must not fail every turn).

    Args:
        session: Raw session state.

    Returns:
        The stored non-negative int; a digit string is parsed; anything
        else (bool, float, list, dict, other strings) counts as 0.
    """
    raw = session.get(OFF_TRACK_KEY)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(raw, 0)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return 0


def _off_track_is_clean(session: dict) -> bool:
    """True when the stored counter is absent or already a non-negative int."""
    raw = session.get(OFF_TRACK_KEY)
    return raw is None or (isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0)


def stored_extras(session: dict) -> dict:
    """Read ``nlu_extras`` tolerantly: a non-dict value counts as ``{}``."""
    raw = session.get(EXTRAS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


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
        merged = stored_extras(session)
        merged.update(dict(extras))
        writes.append(StateWrite("session", EXTRAS_KEY, merged))
    # A corrupted stored counter is rewritten (healed) even when the value is unchanged.
    if off_track_count != stored_off_track_count(session) or not _off_track_is_clean(session):
        writes.append(StateWrite("session", OFF_TRACK_KEY, off_track_count))
    return writes, updates
