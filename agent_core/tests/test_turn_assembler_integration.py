"""Integration test for the cancel mechanism (#224 acceptance #4).

Replays the T4-T7 cluster from the 2026-04-24 voice UX triage:
multiple cancel events fire during a sequence of utterance segments,
and only the final turn runs to completion. With the new lifecycle,
exactly one DoneEvent(turn_status='completed') should reach the consumer;
sentences from cancelled turns must not appear after their respective
DoneEvent(interrupted) markers.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn_assembler import TurnAssembler, TurnStatus


def _make_config_for_integration(silence_ms=50, max_wait_ms=500):
    """Standard fast config for integration tests."""
    return {
        "reach_layer": {
            "turn_assembler": {
                "semantic_gate": {"enabled": False, "confidence_threshold": 0.75},
                "silence_trigger": {"silence_ms": silence_ms},
                "max_wait_ceiling": {"max_wait_ms": max_wait_ms},
            },
        },
        "channels": {},
    }


def _make_seg(text, channel="cli", user_id="u1", timestamp_ms=None):
    """Build a SegmentInput with optional auto-timestamping."""
    if timestamp_ms is None:
        timestamp_ms = int(asyncio.get_event_loop().time() * 1000)
    return SegmentInput(text=text, channel=channel, user_id=user_id, timestamp_ms=timestamp_ms)


def _make_slow_streaming_agent_core(per_chunk_delay_s=0.005):
    """Mock agent_core whose stream_turn yields 5 sentences slowly so cancel can interrupt.

    Yields:
      - SignalEvent(stage='memory_read', status='start')
      - 5x SentenceEvent with configurable delay between each
      - DoneEvent(turn_status='completed')

    Respects abort_event: if set, stops streaming early.
    """
    agent = MagicMock()

    async def stream(turn_input, *, abort_event=None, turn_id=""):
        yield SignalEvent(stage="memory_read", status="start", turn_id=turn_id)
        for i in range(5):
            await asyncio.sleep(per_chunk_delay_s)
            if abort_event is not None and abort_event.is_set():
                return
            yield SentenceEvent(
                text=f"sent{i}",
                sentence_index=i,
                turn_id=turn_id,
            )
        yield DoneEvent(turn_status="completed", turn_id=turn_id)

    agent.stream_turn = stream
    return agent


@pytest.mark.asyncio
async def test_t4_t7_replay_yields_exactly_one_completed_done():
    """Replay: 3 cancels + 1 successful turn → 3 Done(interrupted) + 1 Done(completed),
    no stale sentences after each cancel boundary.

    Simulates the voice triage pattern where VAD pauses 4 times within one user
    intent, triggering silence-based LLM invocations. The first three are
    cancelled mid-stream; the fourth runs to completion. Validates:
      1. Exactly one DoneEvent(turn_status='completed') reaches the consumer.
      2. Exactly three DoneEvent(turn_status='interrupted') are emitted.
      3. No SentenceEvent from a cancelled turn appears after its DoneEvent.
    """
    agent = _make_slow_streaming_agent_core(per_chunk_delay_s=0.050)
    ta = TurnAssembler(
        agent_core=agent,
        config=_make_config_for_integration(silence_ms=20, max_wait_ms=500),
    )

    received = []
    finished = asyncio.Event()

    async def consume():
        """Subscribe to session 's1' and collect all events until a completed DoneEvent."""
        async for ev in ta.subscribe("s1"):
            received.append(ev)
            if isinstance(ev, DoneEvent) and ev.turn_status == "completed":
                finished.set()
                break

    consumer = asyncio.create_task(consume())

    # Simulate VAD pausing 4 times inside one user intent. Each pause triggers
    # a silence_trigger that invokes the LLM. We cancel each in-flight turn
    # before letting it complete — except the last.
    for i, text in enumerate(["hello", "who is", "the prime", "minister of india"]):
        await ta.add_segment("s1", _make_seg(text=text))
        # Wait for the silence trigger to fire (silence_ms=20) and for the turn
        # to enter INVOKED. Then let two sentences arrive before we cancel.
        await asyncio.sleep(0.05)
        if i < 3:  # cancel the first three turns
            await ta.cancel("s1")
            # Wait briefly for the cancel's DoneEvent(interrupted) to land.
            await asyncio.sleep(0.005)
        # Mark the (now interrupted/completed) turn as terminal so the next
        # add_segment installs a fresh Turn. cancel() already sets INTERRUPTED;
        # for the final turn, we let it run to completion via the wait below.

    # Wait for the final DoneEvent(completed) with a timeout.
    await asyncio.wait_for(finished.wait(), timeout=2.0)
    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass

    # --- Assertion 1: Exactly one DoneEvent(turn_status='completed') ---
    completed_dones = [
        e for e in received
        if isinstance(e, DoneEvent) and e.turn_status == "completed"
    ]
    interrupted_dones = [
        e for e in received
        if isinstance(e, DoneEvent) and e.turn_status == "interrupted"
    ]
    assert len(completed_dones) == 1, (
        f"expected exactly 1 completed Done; got {len(completed_dones)}"
    )
    assert len(interrupted_dones) == 3, (
        f"expected 3 interrupted Dones; got {len(interrupted_dones)}"
    )

    # --- Assertion 2: No stale sentences after cancel boundary ---
    # Every SentenceEvent with a given turn_id must appear BEFORE that turn's
    # terminal DoneEvent in the received sequence. Equivalently: no SentenceEvent
    # appears after a DoneEvent for the SAME turn_id.
    last_done_index_per_turn: dict[str, int] = {}
    for idx, ev in enumerate(received):
        if isinstance(ev, DoneEvent):
            last_done_index_per_turn[ev.turn_id] = idx

    for idx, ev in enumerate(received):
        if isinstance(ev, SentenceEvent) and ev.turn_id in last_done_index_per_turn:
            assert idx < last_done_index_per_turn[ev.turn_id], (
                f"SentenceEvent at idx {idx} (turn_id={ev.turn_id}, text={ev.text}) "
                f"appears AFTER its turn's DoneEvent at idx "
                f"{last_done_index_per_turn[ev.turn_id]}"
            )

    # --- Assertion 3: Final completed turn produced all 5 sentences ---
    final_turn_id = completed_dones[0].turn_id
    final_sentences = [
        e for e in received
        if isinstance(e, SentenceEvent) and e.turn_id == final_turn_id
    ]
    assert len(final_sentences) == 5, (
        f"final turn should yield 5 sentences; got {len(final_sentences)}"
    )
