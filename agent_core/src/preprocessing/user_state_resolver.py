"""
agent_core/src/preprocessing/user_state_resolver.py

Pure helper that resolves the post-NLU user-state payload for the turn,
given the classification from NLU and the previous payload from session state.

Called by the orchestrator after NLU returns. No I/O. No logging.
The orchestrator is responsible for persisting the payload, emitting
observability events, and passing the guidance text to ManagerAgent.

Belongs to the DPG Agent Core block.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.models import UserStateClassification


def resolve_user_state(
    *,
    classification: UserStateClassification | None,
    previous: dict | None,
    config: dict,
    now: datetime,
) -> tuple[Optional[dict], bool]:
    """Derive the new session payload for user_state.

    Args:
        classification: NLU output. None when the model is disabled OR when
                        NLU failed (in which case we keep previous state).
        previous:       The payload from session state at turn start, if any.
        config:         Full agent_core config dict.
        now:            Current UTC timestamp (injected for testability).

    Returns:
        Tuple of (new_payload, transitioned).
        new_payload is None only when the user-state model is disabled.
        transitioned is True only on an actual id change from previous to new.
    """
    usm = (config or {}).get("conversation", {}).get("user_state_model", {}) or {}
    if not usm.get("enabled", False):
        return None, False

    default_state = usm.get("default_state", "")

    if classification is None:
        if previous is None:
            return _initial_payload(default_state, 0.0, now), False
        return _sticky_payload(previous), False

    new_id = classification.id
    new_conf = classification.confidence

    if previous is None:
        return _initial_payload(new_id or default_state, new_conf, now), False

    previous_id = previous.get("id", "")
    if new_id == previous_id or not new_id:
        return _sticky_payload(previous, new_conf), False

    return {
        "id": new_id,
        "confidence": new_conf,
        "updated_at": now.isoformat(),
        "previous_id": previous_id,
        "turn_count": 1,
    }, True


def _initial_payload(state_id: str, confidence: float, now: datetime) -> dict:
    """Build the payload used on the very first turn of a session.

    Args:
        state_id: The initial state ID.
        confidence: The confidence score for the state.
        now: Current UTC timestamp.

    Returns:
        Dict with initial state payload.
    """
    return {
        "id": state_id,
        "confidence": confidence,
        "updated_at": now.isoformat(),
        "previous_id": None,
        "turn_count": 1,
    }


def _sticky_payload(previous: dict, new_confidence: float | None = None) -> dict:
    """Retain previous state id and timestamps; bump turn_count.

    Args:
        previous: The previous state payload dict.
        new_confidence: Optional updated confidence score. If None, retains
                        the previous confidence.

    Returns:
        Dict with state retained from previous, turn_count incremented.
    """
    return {
        "id": previous.get("id", ""),
        "confidence": (
            new_confidence if new_confidence is not None
            else float(previous.get("confidence", 0.0))
        ),
        "updated_at": previous.get("updated_at", ""),
        "previous_id": previous.get("previous_id"),
        "turn_count": int(previous.get("turn_count", 0)) + 1,
    }
