"""
agent_core/src/understanding/precedence.py

Provenance-based precedence (NLU dialogue-acts spec §6.5): a value the
SlotWriter wrote this session beats the stored profile; every other session
copy keeps the profile-first rule that guards against seeded defaults
(e.g. a session ``age`` of 0 outranking a real profile 25).

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

PROVENANCE_KEY = "slot_provenance"
_SEEDS = (None, "", [], {}, 0, "0")


def nlu_owned_values(session: dict | None) -> dict:
    """Session values for keys the SlotWriter wrote, excluding seeded defaults.

    Args:
        session: Session state (``bundle.session``).

    Returns:
        key → value to layer over the profile. Empty when no provenance is recorded.
    """
    session = session or {}
    keys = session.get(PROVENANCE_KEY)
    if not isinstance(keys, list):
        return {}
    return {k: session[k] for k in keys if isinstance(k, str) and session.get(k) not in _SEEDS}
