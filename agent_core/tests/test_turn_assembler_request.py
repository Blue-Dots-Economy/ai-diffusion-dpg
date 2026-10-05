"""Request-scoped adapter and idle eviction (Agent Core block, TurnAssembler).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.1-4.2, §4.7
"""

import asyncio

import pytest

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn_assembler import TurnAssembler, TurnAssemblerBase, TurnStatus

from tests.test_turn_assembler_interrupt import _SlowAgent, _cfg, _wait_status


def _req(text, channel="bridge"):
    return SegmentInput(text=text, channel=channel, user_id="u1")


class TestSubmitAttach:

    async def test_submit_invokes_immediately_and_attach_streams(self):
        agent = _SlowAgent(tool_s=0.01)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        assert turn.status == TurnStatus.INVOKED             # no silence timer
        events = [e async for e in ta.attach(turn)]
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        assert any(isinstance(e, SentenceEvent) for e in events)

    async def test_second_submit_interrupts_first_and_waits(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        first = await ta.submit("s1", _req("I want work nearby"))
        await asyncio.sleep(0.05)
        second = await ta.submit("s1", _req("hello?"))
        assert first.status == TurnStatus.INTERRUPTED
        events = [e async for e in ta.attach(second)]
        assert events[-1].turn_status == "completed"
        assert agent.calls == ["I want work nearby", "hello?"]
        assert agent.stopped == ["I want work nearby"]

    async def test_submit_rejects_empty(self):
        ta = TurnAssembler(agent_core=_SlowAgent(), config=_cfg())
        with pytest.raises(ValueError):
            await ta.submit("", _req("hi"))
        with pytest.raises(ValueError):
            await ta.submit("s1", _req("  "))

    async def test_submit_carries_fresh(self):
        seen = {}

        class _Agent:
            async def stream_turn(self, ti, *, abort_event=None, turn_id="", record=None):
                seen["fresh"] = ti.fresh
                yield DoneEvent(turn_id=turn_id, turn_status="completed")

        ta = TurnAssembler(agent_core=_Agent(), config=_cfg())
        turn = await ta.submit("s1", SegmentInput(text="hi", channel="bridge", fresh=True))
        [e async for e in ta.attach(turn)]
        assert seen["fresh"] is True


class TestDetach:

    async def test_detach_aborts_by_default(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        await asyncio.sleep(0.05)
        ta.detach(turn, "disconnect")                       # sync call
        assert turn.status == TurnStatus.INTERRUPTED
        await asyncio.wait_for(turn.invocation_task, 1)
        assert agent.stopped == ["hi"]

    async def test_detach_continue_lets_turn_finish(self):
        agent = _SlowAgent(tool_s=0.05)
        ta = TurnAssembler(agent_core=agent, config=_cfg(on_disconnect="continue"))
        turn = await ta.submit("s1", _req("hi"))
        ta.detach(turn, "disconnect")
        await asyncio.wait_for(turn.invocation_task, 1)
        assert turn.status == TurnStatus.COMPLETED
        assert agent.stopped == []

    async def test_detach_after_completion_is_noop(self):
        agent = _SlowAgent(tool_s=0.01)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        [e async for e in ta.attach(turn)]
        ta.detach(turn, "disconnect")
        assert turn.status == TurnStatus.COMPLETED

    async def test_detach_of_superseded_turn_is_noop(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        first = await ta.submit("s1", _req("a"))
        second = await ta.submit("s1", _req("b"))
        ta.detach(first, "disconnect")                      # already interrupted
        assert second.status == TurnStatus.INVOKED


class TestEviction:

    async def test_idle_session_evicted_on_next_lookup(self):
        now = [1000.0]
        cfg = _cfg()
        cfg["reach_layer"]["turn_assembler"]["session_idle_ttl_ms"] = 1000
        ta = TurnAssembler(agent_core=_SlowAgent(tool_s=0.01), config=cfg, clock=lambda: now[0])
        turn = await ta.submit("s1", _req("hi"))
        [e async for e in ta.attach(turn)]
        now[0] += 5.0
        await ta.submit("s2", _req("hi"))
        assert "s1" not in ta._sessions and "s2" in ta._sessions

    async def test_busy_or_subscribed_session_not_evicted(self):
        now = [1000.0]
        cfg = _cfg()
        cfg["reach_layer"]["turn_assembler"]["session_idle_ttl_ms"] = 1000
        ta = TurnAssembler(agent_core=_SlowAgent(tool_s=10), config=cfg, clock=lambda: now[0])
        await ta.submit("busy", _req("hi"))                 # stays INVOKED
        sub = ta._get_or_create_session("sub")
        sub.subscribers = 1
        now[0] += 5.0
        ta._get_or_create_session("other")
        assert "busy" in ta._sessions and "sub" in ta._sessions
        ta._sessions["busy"].current_turn.invocation_task.cancel()   # test cleanup only


def test_base_declares_request_methods():
    for name in ("submit", "attach", "detach"):
        assert name in TurnAssemblerBase.__abstractmethods__
