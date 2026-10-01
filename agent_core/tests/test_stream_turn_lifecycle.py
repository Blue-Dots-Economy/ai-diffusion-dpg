"""Lifecycle tests for AgentCore.stream_turn: record, capture-first, persistence, fold.

Belongs to the Agent Core block. Spec:
docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.5-4.6
"""

import asyncio

import pytest

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolUseBlock
from src.models import DoneEvent, NLUResult, SignalEvent, ToolResult, TurnRecord

from tests.fakes import fake_understander
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
    agent._understander = fake_understander(NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9))
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


import time as _time

from src.chat_provider.types import ToolResultBlock


def _carry(segments, age_ms=0):
    return {"segments": segments, "stopped_at_stage": "nlu", "turn_id": "t0",
            "written_at_ms": int(_time.time() * 1000) - age_ms}


class TestFold:

    async def test_carryover_folded_into_user_message_and_cleared(self):
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["I want work in Ghaziabad"])
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(
            _make_turn_input(user_message="hello, anyone there?"), record=record)]
        assert isinstance(events[-1], DoneEvent)
        assert record.fold_ran is True
        assert record.segments == ["I want work in Ghaziabad", "hello, anyone there?"]
        # cleared (None written) before anything else
        assert _writes(agent, "turn_carryover")[0] is None
        # the model's user turn contains both utterances
        built = agent._manager_agent.build_messages.call_args
        assert "I want work in Ghaziabad" in str(built) and "hello, anyone there?" in str(built)

    async def test_no_carryover_leaves_message_unchanged(self):
        agent = _tool_agent(rounds=0)
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"), record=record)]
        assert record.segments == ["hi"] and record.fold_ran is True
        assert _writes(agent, "turn_carryover") == []

    async def test_stale_carryover_is_discarded_and_cleared(self):
        """Review focus 2: a callback must not fold the previous call's goodbye."""
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["thank you, bye"], age_ms=10 * 60 * 1000)
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"), record=record)]
        assert record.segments == ["hi"]
        assert _writes(agent, "turn_carryover")[0] is None

    @pytest.mark.parametrize("raw", ["oops", ["a"], {"segments": "a"},
                                     {"segments": [1, None, " "], "written_at_ms": 0}])
    async def test_malformed_carryover_is_ignored(self, raw):
        """Review focus 4."""
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = raw
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"),
                                                     record=record)]
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        assert record.segments == ["hi"]

    @pytest.mark.parametrize("written", [None, "123", 1.5, True])
    def test_missing_or_non_int_timestamp_is_malformed_not_stale(self, written, caplog):
        """Final review 5: a bad ``written_at_ms`` is reported as malformed."""
        agent = _tool_agent(rounds=0)
        raw = {"segments": ["a"]}
        if written is not None:
            raw["written_at_ms"] = written
        caplog.set_level("INFO")
        assert agent._valid_carryover_segments(raw, agent._turn_policy("bridge")) == []
        reasons = [getattr(r, "reason", None) for r in caplog.records
                   if r.getMessage() == "orchestrator.carryover_discarded"]
        assert reasons == ["malformed"]

    def test_future_timestamp_within_skew_tolerance_is_accepted(self):
        """Final review 5: a slightly-future timestamp (clock skew) counts as age 0."""
        agent = _tool_agent(rounds=0)
        raw = _carry(["a"], age_ms=-4000)
        assert agent._valid_carryover_segments(raw, agent._turn_policy("bridge")) == ["a"]

    def test_future_timestamp_beyond_skew_tolerance_is_discarded(self, caplog):
        agent = _tool_agent(rounds=0)
        caplog.set_level("INFO")
        raw = _carry(["a"], age_ms=-60_000)
        assert agent._valid_carryover_segments(raw, agent._turn_policy("bridge")) == []
        reasons = [getattr(r, "reason", None) for r in caplog.records
                   if r.getMessage() == "orchestrator.carryover_discarded"]
        assert reasons == ["malformed"]

    def test_old_timestamp_is_stale(self, caplog):
        agent = _tool_agent(rounds=0)
        caplog.set_level("INFO")
        raw = _carry(["a"], age_ms=10 * 60 * 1000)
        assert agent._valid_carryover_segments(raw, agent._turn_policy("bridge")) == []
        reasons = [getattr(r, "reason", None) for r in caplog.records
                   if r.getMessage() == "orchestrator.carryover_discarded"]
        assert reasons == ["stale"]

    async def test_fold_cap(self):
        agent = _tool_agent(rounds=0)
        agent._config.setdefault("reach_layer", {})["turn_assembler"] = {"fold": {"max_segments": 2}}
        agent._turn_policies.clear()
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["a", "b", "c"])
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="d"), record=record)]
        assert record.segments == ["c", "d"]

    async def test_max_segments_zero_disables_fold(self):
        agent = _tool_agent(rounds=0)
        agent._config.setdefault("reach_layer", {})["turn_assembler"] = {"fold": {"max_segments": 0}}
        agent._turn_policies.clear()
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = _carry(["a"])
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="d"), record=record)]
        assert record.segments == ["d"]


class TestUndeliveredReplay:

    def test_note_appended_only_to_undelivered(self):
        agent = _make_agent_core()
        ex = lambda i, d: {"tool_uses": [{"type": "tool_use", "id": i, "name": "t", "input": {}}],
                           "tool_results": [{"type": "tool_result", "tool_use_id": i,
                                             "content": "R"}], **d}
        msgs = agent._build_tool_exchange_messages(
            [ex("a", {}), ex("b", {"delivered": False})], undelivered_note="NOTE")
        results = [b for m in msgs if m.role == "user" for b in m.content
                   if isinstance(b, ToolResultBlock)]
        assert results[0].content == "R"
        assert results[1].content == "R\nNOTE"

    def test_no_note_leaves_content(self):
        agent = _make_agent_core()
        msgs = agent._build_tool_exchange_messages([{
            "tool_uses": [{"type": "tool_use", "id": "a", "name": "t", "input": {}}],
            "tool_results": [{"type": "tool_result", "tool_use_id": "a", "content": "R"}],
            "delivered": False}])
        results = [b for m in msgs if m.role == "user" for b in m.content
                   if isinstance(b, ToolResultBlock)]
        assert results[0].content == "R"

    async def test_completed_turn_clears_delivered_flags_without_new_rounds(self):
        agent = _tool_agent(rounds=0)
        prior = [{"tool_uses": [{"type": "tool_use", "id": "p", "name": "t", "input": {}}],
                  "tool_results": [{"type": "tool_result", "tool_use_id": "p", "content": "c"}],
                  "delivered": False}]
        agent._async_memory.context_bundle.return_value.session["recent_tool_exchanges"] = prior
        [e async for e in agent.stream_turn(_make_turn_input())]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stored = _writes(agent, "recent_tool_exchanges")[-1]
        assert "delivered" not in stored[0]
