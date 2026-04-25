"""Per-turn lifecycle object for the TurnAssembler (#224).

Belongs to the Agent Core block. Owns turn-scoped state (segments, queue,
abort signal, invocation task) so that cancellation is a structural property:
a cancelled Turn is dead and its queue is sealed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

from .models import ContextBundle, DoneEvent, SegmentInput, StreamEvent


class TurnStatus(str, Enum):
    """State machine for a single turn's lifecycle.

    Transitions:
        WAITING → INVOKED      (policy triggered, invocation task created)
        WAITING → ABANDONED    (cancel() while waiting)
        INVOKED → COMPLETED    (DoneEvent emitted naturally)
        INVOKED → INTERRUPTED  (cancel() while LLM call in flight)
    """

    WAITING = "waiting"
    INVOKED = "invoked"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"


@dataclass
class Turn:
    """One conversational turn within a Session.

    Each Turn owns its own event queue and abort signal. When cancelled,
    the queue is sealed with a terminal DoneEvent and the Turn becomes dead;
    a successor Turn gets a fresh queue.
    """

    turn_id: str
    epoch: int
    session_id: str
    channel: str
    user_id: Optional[str]
    started_at_ms: int
    segments: list[SegmentInput] = field(default_factory=list)
    status: TurnStatus = TurnStatus.WAITING
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    invocation_task: Optional[asyncio.Task] = None
    silence_task: Optional[asyncio.Task] = None
    ceiling_task: Optional[asyncio.Task] = None

    # Context cache — fetched once on first add_segment() so the semantic gate
    # has NLU context (current_question, current_subagent_id) without re-reading
    # Memory Layer on every segment.
    _context_fetched: bool = False
    context_bundle: Optional[ContextBundle] = None

    async def iter_events(self) -> AsyncIterator[StreamEvent]:
        """Drain the event queue until DoneEvent is yielded, then exit.

        Yields:
            Each StreamEvent in queue order. Terminates after DoneEvent.
            Any events enqueued after DoneEvent remain in the queue and
            are not yielded.
        """
        while True:
            ev = await self.event_queue.get()
            yield ev
            if isinstance(ev, DoneEvent):
                return
