"""Cooperative interruption and predecessor drain (Agent Core block, TurnAssembler).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.4
"""

import asyncio

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn_assembler import TurnAssembler, TurnStatus


def _cfg(**interruption):
    return {"reach_layer": {"turn_assembler": {
        "silence_trigger": {"silence_ms": 10},
        "max_wait_ceiling": {"max_wait_ms": 5000},
        "interruption": interruption,
    }}, "channels": {}}


class _SlowAgent:
    """stream_turn that emits a stage, then sleeps in a 'tool', then sentences.

    Records whether it ran to its abort check (``stopped``), and never yields
    content after abort — mimicking the orchestrator's cooperative checks.
    """

    def __init__(self, tool_s=0.2):
        self.tool_s = tool_s
        self.calls = []
        self.stopped = []

    async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
        # The real stream_turn wrapper tracks record.last_stage; mimic it.
        self.calls.append(turn_input.user_message)
        if record is not None:
            record.last_stage = "tool_start"
        yield SignalEvent(stage="tool_start", status="start")
        await asyncio.sleep(self.tool_s)             # a dispatched tool call
        if record is not None:
            record.last_stage = "tool_end"
        yield SignalEvent(stage="tool_end", status="complete")
        if abort_event is not None and abort_event.is_set():
            self.stopped.append(turn_input.user_message)
            return
        yield SentenceEvent(text="answer", sentence_index=0)
        yield DoneEvent(turn_id=turn_id, turn_status="completed")


def _seg(text):
    return SegmentInput(text=text, channel="voice", user_id="u1")


async def _wait_status(turn, status, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if turn.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"turn stayed {turn.status}")


class TestCooperativeInterrupt:

    async def test_barge_in_does_not_cancel_the_running_task(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await asyncio.sleep(0.05)                   # inside the 'tool'
        await ta.add_segment("s1", _seg("second"))
        assert first.status == TurnStatus.INTERRUPTED
        assert first.abort_event.is_set()
        assert not first.invocation_task.cancelled()
        await asyncio.wait_for(first.invocation_task, 1)
        assert agent.stopped == ["first"]           # reached its safe point

    async def test_sealed_queue_carries_stage_and_no_content(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await asyncio.sleep(0.05)
        await ta.add_segment("s1", _seg("second"))
        await asyncio.wait_for(first.invocation_task, 1)
        events = [e async for e in first.iter_events()]
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert done[0].turn_status == "interrupted"
        assert done[0].interrupted_at_stage == "tool_start"
        assert not any(isinstance(e, SentenceEvent) for e in events)

    async def test_successor_waits_for_predecessor(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        await _wait_status(second, TurnStatus.COMPLETED, timeout=3)
        # the successor's stream_turn started only after the predecessor stopped
        assert agent.calls == ["first", "second"]
        assert first.invocation_task.done()

    async def test_drain_timeout_proceeds_without_cancelling(self, caplog):
        agent = _SlowAgent(tool_s=0.5)
        ta = TurnAssembler(agent_core=agent, config=_cfg(drain_max_ms=50))
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        await _wait_status(second, TurnStatus.INVOKED, timeout=1)
        await asyncio.sleep(0.1)
        assert not first.invocation_task.done()      # still draining, not cancelled
        assert any("turn_assembler.drain_timeout" in r.message for r in caplog.records)
        await asyncio.wait_for(first.invocation_task, 2)

    async def test_replace_policy_disables_carryover(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg(on_new_input="replace"))
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        assert first.record.write_carryover is False

    async def test_cancel_is_cooperative(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.cancel("s1")
        assert first.status == TurnStatus.INTERRUPTED
        assert not first.invocation_task.cancelled()
        await asyncio.wait_for(first.invocation_task, 1)
        assert agent.stopped == ["first"]

    async def test_invoke_passes_record(self):
        seen = {}

        class _Agent:
            async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
                seen["record"] = record
                yield DoneEvent(turn_id=turn_id, turn_status="completed")

        ta = TurnAssembler(agent_core=_Agent(), config=_cfg())
        await ta.add_segment("s1", _seg("hi"))
        turn = ta._sessions["s1"].current_turn
        await _wait_status(turn, TurnStatus.COMPLETED)
        assert seen["record"] is turn.record
