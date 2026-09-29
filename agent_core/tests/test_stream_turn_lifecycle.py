"""Lifecycle tests for AgentCore.stream_turn: record, capture-first, persistence, fold.

Belongs to the Agent Core block. Spec:
docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.5-4.6
"""

import asyncio

import pytest

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolUseBlock
from src.models import DoneEvent, NLUResult, SignalEvent, ToolResult, TurnRecord

from tests.test_stream_turn import _make_agent_core, _make_turn_input


def _tool_agent(rounds: int = 1):
    """AgentCore whose LLM requests ``rounds`` tool rounds, then answers."""
    agent = _make_agent_core()
    calls = {"n": 0}

    async def mock_stream(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= rounds:
            yield "Checking. "
            raise ChatToolUseRequested([ToolUseBlock(
                tool_name=f"tool_{calls['n']}",
                tool_use_id=f"tu_{calls['n']}",
                input={"q": calls["n"]},
            )])
        yield "Done. "

    agent._llm.stream = mock_stream
    agent._async_gateway.execute.side_effect = lambda tc, *a, **k: ToolResult(
        tool_use_id=tc.tool_use_id, tool_name=tc.tool_name,
        result={"ok": True}, success=True, result_text=f"result-{tc.tool_use_id}",
    )
    agent._language_normaliser = type("N", (), {"normalise": lambda self, *a, **k: ("msg", "english")})()
    agent._nlu_processor = type("P", (), {"process": lambda self, *a, **k: NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9)})()
    agent._tool_registry.get_route.return_value = None
    return agent


async def _run(agent, record, abort_after_tool_end: int | None = None):
    """Consume stream_turn; set abort once the Nth tool_end is seen."""
    abort = asyncio.Event()
    events, tool_ends = [], 0
    async for ev in agent.stream_turn(_make_turn_input(), abort_event=abort, record=record):
        events.append(ev)
        if isinstance(ev, SignalEvent) and ev.stage == "tool_end":
            tool_ends += 1
            if abort_after_tool_end is not None and tool_ends == abort_after_tool_end:
                abort.set()
    return events


class TestRecordAndCapture:

    async def test_completed_tool_turn_captures_each_round_once(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        events = await _run(agent, record)
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        names = [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges]
        assert names == ["tool_1", "tool_2"]          # no double capture
        assert record.last_stage == "memory_write"

    async def test_abort_after_first_round_still_captures_it(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        events = await _run(agent, record, abort_after_tool_end=1)
        assert not any(isinstance(e, DoneEvent) for e in events)
        assert [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges] == ["tool_1"]
        assert record.last_stage == "tool_end"

    async def test_abort_after_nested_round_still_captures_it(self):
        agent = _tool_agent(rounds=3)
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=2)
        names = [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges]
        assert names == ["tool_1", "tool_2"]

    async def test_prior_exchanges_and_cap_recorded(self):
        agent = _tool_agent(rounds=1)
        prior = [{"tool_uses": [{"type": "tool_use", "id": "p", "name": "old", "input": {}}],
                  "tool_results": [{"type": "tool_result", "tool_use_id": "p", "content": "c"}]}]
        agent._async_memory.context_bundle.return_value.session["recent_tool_exchanges"] = prior
        record = TurnRecord()
        await _run(agent, record)
        assert record.prior_exchanges == prior
        assert record.max_items > 0

    async def test_stream_turn_without_record_still_works(self):
        agent = _tool_agent(rounds=1)
        events = [e async for e in agent.stream_turn(_make_turn_input())]
        assert isinstance(events[-1], DoneEvent)


def _writes(agent, key):
    return [c.args[4] for c in agent._async_memory.write.await_args_list if c.args[3] == key]


class TestInterruptedPersist:

    async def test_interrupt_persists_exchanges_undelivered_and_carryover(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=1)
        assert record.persist_task is not None
        await record.persist_task

        rte = _writes(agent, "recent_tool_exchanges")[-1]
        assert [ex["tool_uses"][0]["name"] for ex in rte] == ["tool_1"]
        assert rte[0]["delivered"] is False

        carry = _writes(agent, "turn_carryover")[-1]
        assert carry["segments"] == ["Hello"]
        assert carry["stopped_at_stage"] == "tool_end"
        assert isinstance(carry["written_at_ms"], int)
        # the question the turn never delivered is not recorded
        assert _writes(agent, "current_question") == []

    async def test_completed_turn_schedules_no_persist(self):
        agent = _tool_agent(rounds=1)
        record = TurnRecord()
        await _run(agent, record)
        assert record.persist_task is None
        assert _writes(agent, "turn_carryover") in ([], [None])

    async def test_write_carryover_false_persists_exchanges_only(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord(write_carryover=False)
        await _run(agent, record, abort_after_tool_end=1)
        await record.persist_task
        assert _writes(agent, "recent_tool_exchanges")
        assert [v for v in _writes(agent, "turn_carryover") if v is not None] == []

    async def test_interrupt_before_any_tool_writes_carryover_only(self):
        agent = _tool_agent(rounds=0)
        record = TurnRecord()
        abort = asyncio.Event()
        async for ev in agent.stream_turn(_make_turn_input(), abort_event=abort, record=record):
            if isinstance(ev, SignalEvent) and ev.stage == "nlu":
                abort.set()
        await record.persist_task
        assert _writes(agent, "recent_tool_exchanges") == []
        assert _writes(agent, "turn_carryover")[-1]["segments"] == ["Hello"]

    async def test_interrupt_before_fold_appends_to_existing_carryover(self):
        """Review focus 3: an abort during step 1 must not overwrite older carry-over."""
        agent = _tool_agent(rounds=0)
        import time as _t
        existing = {"segments": ["I want work in Ghaziabad"], "stopped_at_stage": "nlu",
                    "turn_id": "old", "written_at_ms": int(_t.time() * 1000)}
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = existing
        record = TurnRecord()
        abort = asyncio.Event()
        abort.set()                              # aborted before step 1 completes
        async for _ in agent.stream_turn(_make_turn_input(user_message="hello?"),
                                         abort_event=abort, record=record):
            pass
        assert record.fold_ran is False
        await record.persist_task
        carry = _writes(agent, "turn_carryover")[-1]
        assert carry["segments"] == ["I want work in Ghaziabad", "hello?"]

    async def test_persist_failure_is_logged_not_raised(self, caplog):
        agent = _tool_agent(rounds=2)
        agent._async_memory.write.side_effect = RuntimeError("down")
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=1)
        await record.persist_task                 # must not raise
        assert any("orchestrator.interrupted_persist" in r.message for r in caplog.records)

    async def test_error_turn_is_treated_as_not_completed(self):
        agent = _tool_agent(rounds=0)
        async def boom(*a, **k):
            raise RuntimeError("llm down")
            yield  # pragma: no cover
        agent._llm.stream = boom
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(_make_turn_input(), record=record)]
        assert events[-1].turn_status == "abandoned"
        await record.persist_task
        assert _writes(agent, "turn_carryover")[-1]["segments"] == ["Hello"]
