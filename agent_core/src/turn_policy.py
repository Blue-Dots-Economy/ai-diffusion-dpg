"""agent_core/turn_policy.py

Turn-lifecycle policy for streaming turns (Agent Core block).

Resolves the ``interruption`` / ``fold`` / ``carryover`` sections of
``turn_assembler`` config into one immutable ``TurnPolicy`` per channel. Used by
both the TurnAssembler (when to stop a turn) and ``stream_turn`` (what an
interrupted turn leaves for its successor), so the two can never disagree about
a default.

Resolution order, merged per sub-section: built-in defaults, then
``reach_layer.turn_assembler``, then ``channels.<name>.turn_assembler``.
Invalid values fall back to the default with a warning. Boot-time schema
validation rejects them first; this is the runtime backstop.

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

ON_NEW_INPUT_ABORT_AND_FOLD = "abort_and_fold"
ON_NEW_INPUT_REPLACE = "replace"
ON_DISCONNECT_ABORT = "abort"
ON_DISCONNECT_CONTINUE = "continue"

_ON_NEW_INPUT_VALUES = (ON_NEW_INPUT_ABORT_AND_FOLD, ON_NEW_INPUT_REPLACE)
_ON_DISCONNECT_VALUES = (ON_DISCONNECT_ABORT, ON_DISCONNECT_CONTINUE)
_DEFAULT_SESSION_IDLE_TTL_MS = 1_800_000


@dataclass(frozen=True)
class TurnPolicy:
    """Resolved interruption, fold and carry-over settings for one channel.

    Attributes:
        on_new_input: ``abort_and_fold`` or ``replace`` — what a new input does
            to a turn still in flight.
        on_disconnect: ``abort`` or ``continue`` — what a request-scoped
            client closing its connection does to its turn.
        drain_max_ms: How long a successor waits for its predecessor to reach
            a safe point and persist.
        fold_max_segments: Newest utterances kept when folding; 0 disables.
        carryover_max_age_ms: Carry-over older than this is discarded.
        undelivered_note: Appended to replayed tool results the user has not
            heard. Empty disables the annotation.
    """

    on_new_input: str = ON_NEW_INPUT_ABORT_AND_FOLD
    on_disconnect: str = ON_DISCONNECT_ABORT
    drain_max_ms: int = 3000
    fold_max_segments: int = 3
    carryover_max_age_ms: int = 60000
    undelivered_note: str = ""


def _section(block: Any, name: str) -> dict:
    """Return ``block[name]`` when both are dicts, else an empty dict."""
    if not isinstance(block, dict):
        return {}
    value = block.get(name)
    return value if isinstance(value, dict) else {}


def _merged(config: dict, channel: Optional[str], name: str) -> dict:
    """Merge one sub-section: reach_layer default, then channel override."""
    default_ta = _section(_section(config, "reach_layer"), "turn_assembler")
    merged = dict(_section(default_ta, name))
    if channel:
        channel_block = _section(_section(config, "channels"), channel)
        merged.update(_section(_section(channel_block, "turn_assembler"), name))
    return merged


def _choice(value: Any, allowed: tuple[str, ...], default: str, field: str) -> str:
    """Return ``value`` if allowed, else ``default`` with a warning."""
    if value is None:
        return default
    if value in allowed:
        return value
    logger.warning(
        "turn_policy.invalid_value",
        extra={"operation": "turn_policy.resolve", "status": "failure",
               "field": field, "error": f"not one of {allowed}"},
    )
    return default


def _non_negative_int(value: Any, default: int, field: str) -> int:
    """Return ``value`` if it is a non-negative int, else ``default``."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    logger.warning(
        "turn_policy.invalid_value",
        extra={"operation": "turn_policy.resolve", "status": "failure",
               "field": field, "error": "expected a non-negative integer"},
    )
    return default


def resolve_turn_policy(config: dict, channel: Optional[str]) -> TurnPolicy:
    """Resolve the turn-lifecycle policy for ``channel``.

    Args:
        config: Full merged agent_core config dict.
        channel: Channel name, or None when unknown (defaults apply).

    Returns:
        The resolved, immutable TurnPolicy.
    """
    config = config if isinstance(config, dict) else {}
    base = TurnPolicy()
    interruption = _merged(config, channel, "interruption")
    fold = _merged(config, channel, "fold")
    carryover = _merged(config, channel, "carryover")
    note = carryover.get("undelivered_note", base.undelivered_note)
    return TurnPolicy(
        on_new_input=_choice(interruption.get("on_new_input"), _ON_NEW_INPUT_VALUES,
                             base.on_new_input, "interruption.on_new_input"),
        on_disconnect=_choice(interruption.get("on_disconnect"), _ON_DISCONNECT_VALUES,
                              base.on_disconnect, "interruption.on_disconnect"),
        drain_max_ms=_non_negative_int(interruption.get("drain_max_ms"),
                                       base.drain_max_ms, "interruption.drain_max_ms"),
        fold_max_segments=_non_negative_int(fold.get("max_segments"),
                                            base.fold_max_segments, "fold.max_segments"),
        carryover_max_age_ms=_non_negative_int(carryover.get("max_age_ms"),
                                               base.carryover_max_age_ms,
                                               "carryover.max_age_ms"),
        undelivered_note=note if isinstance(note, str) else base.undelivered_note,
    )


def resolve_session_idle_ttl_ms(config: dict) -> int:
    """Return ``reach_layer.turn_assembler.session_idle_ttl_ms`` or its default.

    Args:
        config: Full merged agent_core config dict.

    Returns:
        Idle time in ms after which an idle in-process session is evicted.
    """
    config = config if isinstance(config, dict) else {}
    default_ta = _section(_section(config, "reach_layer"), "turn_assembler")
    return _non_negative_int(default_ta.get("session_idle_ttl_ms"),
                             _DEFAULT_SESSION_IDLE_TTL_MS, "session_idle_ttl_ms")
