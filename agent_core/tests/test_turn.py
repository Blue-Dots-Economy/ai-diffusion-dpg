"""Unit tests for the per-turn lifecycle object (#224)."""
import asyncio

import pytest

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn import Turn, TurnStatus


@pytest.mark.asyncio
async def test_turn_default_status_is_waiting():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    assert t.status == TurnStatus.WAITING
    assert t.segments == []
    assert not t.abort_event.is_set()
    assert t.invocation_task is None
    assert t._context_fetched is False
    assert t.context_bundle is None


@pytest.mark.asyncio
async def test_iter_events_yields_until_done():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    await t.event_queue.put(SignalEvent(stage="memory_read", status="start"))
    await t.event_queue.put(SentenceEvent(text="hi", sentence_index=0))
    await t.event_queue.put(DoneEvent(turn_status="completed"))
    # Anything enqueued *after* Done must not be yielded by iter_events.
    await t.event_queue.put(SentenceEvent(text="leak", sentence_index=1))

    collected = []
    async for ev in t.iter_events():
        collected.append(ev)
    assert len(collected) == 3
    assert isinstance(collected[-1], DoneEvent)
    # The leaked event remains in the queue but iter_events has exited.
    assert t.event_queue.qsize() == 1


@pytest.mark.asyncio
async def test_seed_segments_are_preserved():
    seg = SegmentInput(text="hello", user_id=None, channel="cli", timestamp_ms=0)
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0, segments=[seg])
    assert t.segments == [seg]


@pytest.mark.asyncio
async def test_abort_event_settable_independently():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    assert not t.abort_event.is_set()
    t.abort_event.set()
    assert t.abort_event.is_set()
