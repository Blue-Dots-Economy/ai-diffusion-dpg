"""Unit tests for the per-session lifecycle object (#224)."""
import asyncio

import pytest

from src.models import SegmentInput
from src.session import Session
from src.turn import Turn, TurnStatus


@pytest.mark.asyncio
async def test_session_starts_empty():
    s = Session(session_id="s1", user_id=None, channel="cli")
    assert s.current_turn is None
    assert s.ended is False
    assert not s.turn_changed.is_set()


@pytest.mark.asyncio
async def test_replace_turn_installs_fresh_turn_and_signals():
    s = Session(session_id="s1", user_id="u1", channel="voice")
    new_turn = await s.replace_turn(seed_segments=[])
    assert s.current_turn is new_turn
    assert new_turn.session_id == "s1"
    assert new_turn.user_id == "u1"
    assert new_turn.channel == "voice"
    assert new_turn.status == TurnStatus.WAITING
    assert new_turn.epoch == 1
    assert s.turn_changed.is_set()


@pytest.mark.asyncio
async def test_replace_turn_bumps_epoch_monotonically():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    # Mark t1 terminal so replace_turn precondition holds.
    t1.status = TurnStatus.COMPLETED
    t2 = await s.replace_turn()
    t2.status = TurnStatus.INTERRUPTED
    t3 = await s.replace_turn()
    assert (t1.epoch, t2.epoch, t3.epoch) == (1, 2, 3)


@pytest.mark.asyncio
async def test_replace_turn_carries_seed_segments():
    s = Session(session_id="s1", user_id=None, channel="cli")
    seeds = [SegmentInput(text="seed", user_id=None, channel="cli")]
    t = await s.replace_turn(seed_segments=seeds)
    assert t.segments == seeds
    # Segments list must be a fresh list, not the same reference.
    t.segments.append(SegmentInput(text="x", user_id=None, channel="cli"))
    assert len(seeds) == 1


@pytest.mark.asyncio
async def test_replace_turn_rejects_active_prior_turn():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    # t1 is WAITING (active), so replace_turn must refuse.
    with pytest.raises(RuntimeError):
        await s.replace_turn()


@pytest.mark.asyncio
async def test_turn_changed_event_clearable_for_resignal():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    assert s.turn_changed.is_set()
    s.turn_changed.clear()
    t1.status = TurnStatus.COMPLETED
    await s.replace_turn()
    assert s.turn_changed.is_set()
