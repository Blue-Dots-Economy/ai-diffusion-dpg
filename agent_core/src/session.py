"""Per-session lifecycle object for the TurnAssembler (#224).

Belongs to the Agent Core block. Long-lived across many turns.
Holds the current Turn pointer and a fan-out signal that subscribers
use to learn when a new Turn becomes current.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .models import SegmentInput
from .turn import Turn, TurnStatus


_TERMINAL_STATUSES = (
    TurnStatus.COMPLETED,
    TurnStatus.INTERRUPTED,
    TurnStatus.ABANDONED,
)


@dataclass
class Session:
    """Long-lived per-session state.

    Owns identity (session_id, user_id, channel), the current Turn pointer,
    a per-session lock (for atomic turn rollover), and a turn_changed Event
    (single-subscriber fan-out signal — switch to asyncio.Condition if
    multi-subscriber lands).
    """

    session_id: str
    user_id: Optional[str]
    channel: str
    current_turn: Optional[Turn] = None
    ended: bool = False
    turn_changed: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _epoch_counter: int = 0

    async def replace_turn(
        self, *, seed_segments: list[SegmentInput] | None = None
    ) -> Turn:
        """Install a fresh Turn as ``current_turn`` and return it.

        Precondition: prior ``current_turn`` (if any) is in a terminal state
        (COMPLETED / INTERRUPTED / ABANDONED). Caller is responsible for
        cancelling an active turn before calling ``replace_turn``.

        Args:
            seed_segments: Optional initial segments for the new Turn.
                Copied into a fresh list so mutation does not leak.

        Returns:
            The newly created Turn.

        Raises:
            RuntimeError: If prior current_turn is still WAITING or INVOKED.
        """
        if self.current_turn is not None and self.current_turn.status not in _TERMINAL_STATUSES:
            raise RuntimeError(
                f"replace_turn called while prior turn is "
                f"{self.current_turn.status.value}; cancel first"
            )
        self._epoch_counter += 1
        turn = Turn(
            turn_id=str(uuid.uuid4()),
            epoch=self._epoch_counter,
            session_id=self.session_id,
            channel=self.channel,
            user_id=self.user_id,
            started_at_ms=int(time.time() * 1000),
            segments=list(seed_segments) if seed_segments else [],
        )
        self.current_turn = turn
        self.turn_changed.set()
        return turn
