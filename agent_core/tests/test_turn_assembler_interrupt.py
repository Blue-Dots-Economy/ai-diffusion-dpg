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
        self._active = 0              # stream_turn generators not yet exited
        self.active_at_start = []     # _active seen as each call began

    async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
        # The real stream_turn wrapper tracks record.last_stage; mimic it.
        self.calls.append(turn_input.user_message)
        self.active_at_start.append(self._active)
        self._active += 1
        try:
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
        finally:
            self._active -= 1


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
        # the successor's stream_turn started only after the predecessor's
        # generator had exited (no overlap), not merely after it was aborted
        assert agent.calls == ["first", "second"]
        assert agent.active_at_start == [0, 0]
        assert agent.stopped == ["first"]
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


class _PersistingAgent(_SlowAgent):
    """_SlowAgent whose aborted turns leave a slow ``record.persist_task``.

    Mimics ``stream_turn``'s ``finally`` scheduling the interrupted-turn
    persist. Records the loop time each call starts and each persist ends.
    """

    def __init__(self, tool_s=0.05, persist_s=0.3):
        super().__init__(tool_s=tool_s)
        self.persist_s = persist_s
        self.started_at = []
        self.persisted_at = []

    async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
        loop = asyncio.get_running_loop()
        self.started_at.append(loop.time())
        try:
            async for event in super().stream_turn(
                turn_input, abort_event=abort_event, turn_id=turn_id, record=record,
            ):
                yield event
        finally:
            if abort_event is not None and abort_event.is_set() and record is not None:
                async def _persist():
                    await asyncio.sleep(self.persist_s)
                    self.persisted_at.append(loop.time())
                record.persist_task = loop.create_task(_persist())


class TestDrainingPredecessor:

    async def test_submit_waits_for_a_predecessor_still_persisting(self):
        """Final review 2: an interrupted turn whose invocation is done but whose
        persist task is not is still draining; the successor waits for it."""
        agent = _PersistingAgent(tool_s=0.05, persist_s=0.3)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        seg = SegmentInput(text="first", channel="bridge", user_id="u1")
        first = await ta.submit("s1", seg)
        await asyncio.sleep(0.01)
        ta.detach(first, "disconnect")
        await asyncio.wait_for(first.invocation_task, 1)
        assert first.record.persist_task is not None and not first.record.persist_task.done()
        second = await ta.submit("s1", SegmentInput(text="second", channel="bridge", user_id="u1"))
        await asyncio.wait_for(second.invocation_task, 2)
        assert len(agent.persisted_at) == 1
        assert agent.started_at[1] >= agent.persisted_at[0]

    async def test_add_segment_after_cancel_waits_for_the_draining_turn(self):
        """Final review 3: after cancel (DELETE active_turn), a new segment must
        still wait for the interrupted turn that has not drained yet."""
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.cancel("s1")
        assert first.status == TurnStatus.INTERRUPTED
        assert not first.invocation_task.done()
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        assert second is not first
        await _wait_status(second, TurnStatus.COMPLETED, timeout=3)
        assert agent.calls == ["first", "second"]
        assert agent.active_at_start == [0, 0]      # no overlap with the draining turn

    async def test_add_segment_after_cancel_waits_for_the_persist_task(self):
        """Final review 3: the persist leg counts as draining on the session path."""
        agent = _PersistingAgent(tool_s=0.05, persist_s=0.3)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.cancel("s1")
        await asyncio.wait_for(first.invocation_task, 1)
        assert not first.record.persist_task.done()
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        await _wait_status(second, TurnStatus.COMPLETED, timeout=3)
        assert agent.started_at[1] >= agent.persisted_at[0]

    async def test_one_drain_budget_covers_invocation_and_persist(self, caplog):
        """Final review 2: _await_predecessor spends a single drain_max_ms budget
        across both waits, then gives up without cancelling either task."""
        from types import SimpleNamespace

        from src.models import TurnRecord

        record = TurnRecord()
        persist_started = asyncio.Event()

        async def _invocation():
            await asyncio.sleep(0.15)
            record.persist_task = asyncio.get_running_loop().create_task(asyncio.sleep(0.6))
            persist_started.set()

        pred = SimpleNamespace(
            invocation_task=asyncio.get_running_loop().create_task(_invocation()),
            record=record, session_id="s1", turn_id="t1",
        )
        ta = TurnAssembler(agent_core=_SlowAgent(), config=_cfg())
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await ta._await_predecessor(pred, 300)
        elapsed = loop.time() - t0
        assert persist_started.is_set()             # the persist leg was reached
        assert 0.28 <= elapsed < 0.45               # one 300 ms budget, not 300 + 300
        assert not record.persist_task.done() and not record.persist_task.cancelled()
        assert any("turn_assembler.drain_timeout" in r.message for r in caplog.records)
        await asyncio.wait_for(record.persist_task, 2)
