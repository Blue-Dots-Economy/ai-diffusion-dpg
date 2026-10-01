"""24 Sep 2026 PoC replays: interrupted streaming turns keep what they held.

Real AgentCore + real TurnAssembler over an in-memory Memory Layer fake.
Belongs to the Agent Core block. Evidence: voicera-cancelled-turns.md §2.
"""

import asyncio
import copy

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolUseBlock
from src.models import ContextBundle, DoneEvent, NLUResult, SegmentInput, ToolResult
from src.turn_assembler import TurnAssembler, TurnStatus

from tests.fakes import fake_understander
from tests.test_stream_turn import _make_agent_core


class FakeMemory:
    """Session-scope key/value store with the AsyncMemoryLayer surface used here."""

    def __init__(self):
        self.session = {"current_subagent_id": "start"}
        self.writes = []

    async def context_bundle(self, session_id, user_id, adopt=True):
        return ContextBundle(session=copy.deepcopy(self.session), profile={})

    async def write(self, session_id, user_id, scope, key, value):
        self.writes.append((key, copy.deepcopy(value)))
        if scope == "session":
            self.session[key] = copy.deepcopy(value)

    def __getattr__(self, name):                      # flush, audit, etc.: no-ops
        async def _noop(*a, **k):
            return None
        return _noop


def _agent(memory, llm_script):
    agent = _make_agent_core(async_memory=memory)
    agent._config["channels"]["bridge"] = {"system_prompt_suffix": ""}
    agent._llm.stream = llm_script
    agent._language_normaliser = type("N", (), {"normalise": lambda s, *a, **k: ("m", "hindi")})()
    agent._understander = fake_understander(NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9))
    agent._tool_registry.get_route.return_value = None
    return agent


def _user_text(agent):
    """Text of the user message the last LLM call was built from."""
    return str(agent._manager_agent.build_messages.call_args)


async def test_checkin_during_first_llm_call_folds_the_lost_utterance():
    """Class A, row 2: 'I want to work in Ghaziabad' cut at 2.8 s by 'hello, anyone?'."""
    memory = FakeMemory()
    started = asyncio.Event()

    async def llm(*args, abort_event=None, **kwargs):
        started.set()
        for _ in range(100):                          # slow first token
            if abort_event is not None and abort_event.is_set():
                return
            await asyncio.sleep(0.01)
        yield "Which trade? "

    agent = _agent(memory, llm)
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    first = await ta.submit("919900112233", SegmentInput(
        text="मैं ग़ाज़ियाबाद में ही काम करना चाहता हूँ", channel="bridge", user_id="919900112233"))
    await asyncio.wait_for(started.wait(), 2)
    ta.detach(first, "disconnect")                    # VoicERA closed the request
    second = await ta.submit("919900112233", SegmentInput(
        text="हेलो कोई है", channel="bridge", user_id="919900112233"))
    events = [e async for e in ta.attach(second)]
    assert isinstance(events[-1], DoneEvent)
    assert "ग़ाज़ियाबाद" in _user_text(agent) and "हेलो कोई है" in _user_text(agent)
    assert memory.session.get("turn_carryover") is None      # consumed


async def test_tool_rounds_before_disconnect_reach_the_next_turn():
    """Row 9: fetch -> apply -> save ran, then the caller hung up / re-spoke."""
    memory = FakeMemory()
    calls = {"n": 0}
    hold = asyncio.Event()

    async def llm(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 3:
            yield ""
            raise ChatToolUseRequested([ToolUseBlock(
                tool_name=f"t{calls['n']}", tool_use_id=f"tu{calls['n']}", input={})])
        await hold.wait()                              # 4th call: never gets to speak
        yield "Applied. "

    agent = _agent(memory, llm)
    agent._async_gateway.execute.side_effect = lambda tc, *a, **k: ToolResult(
        tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True,
        result_text=f"ok-{tc.tool_name}")
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    first = await ta.submit("s", SegmentInput(text="हाँ ठीक है फिर से कोशिश कर लीजिए",
                                              channel="bridge", user_id="s"))
    for _ in range(200):
        if len(first.record.captured_exchanges) == 3:
            break
        await asyncio.sleep(0.01)
    ta.detach(first, "disconnect")
    hold.set()
    await asyncio.wait_for(first.invocation_task, 2)
    await asyncio.wait_for(first.record.persist_task, 2)
    stored = memory.session["recent_tool_exchanges"]
    assert [ex["tool_uses"][0]["name"] for ex in stored] == ["t1", "t2", "t3"]
    assert all(ex["delivered"] is False for ex in stored)


async def test_checkin_cascade_keeps_newest_segments():
    """Review focus 5: the substantive utterance survives within the cap; a long
    cascade stays capped at fold.max_segments (default 3)."""

    async def llm(*args, abort_event=None, **kwargs):
        for _ in range(100):
            if abort_event is not None and abort_event.is_set():
                return
            await asyncio.sleep(0.01)
        yield "ok "

    async def run(texts):
        memory = FakeMemory()
        agent = _agent(memory, llm)
        ta = TurnAssembler(agent_core=agent, config=agent._config)
        turn = None
        for text in texts:
            turn = await ta.submit("s", SegmentInput(text=text, channel="bridge", user_id="s"))
            await asyncio.sleep(0.05)
        events = [e async for e in ta.attach(turn)]
        assert isinstance(events[-1], DoneEvent)
        return turn.record.segments, _user_text(agent)

    job = "मुझे डिलीवरी बॉय का जॉब चाहिए"
    hello = "हेलो कोई है"
    short, short_text = await run([job, hello, hello])
    assert short == [job, hello, hello]
    assert job in short_text                          # the model heard the job
    long, long_text = await run([job] + [hello] * 6)
    assert len(long) == 3 and long == [hello] * 3
    assert job not in long_text and hello in long_text


def _slow_tool_agent(memory, tool_s=0.5, drain_max_ms=None, silence_ms=None):
    """Agent whose first LLM call runs one slow tool, then answers.

    Returns ``(agent, tool_started)``; ``tool_started`` is set once the tool
    is in flight.
    """
    calls = {"n": 0}

    async def llm(*args, abort_event=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            yield ""
            raise ChatToolUseRequested([ToolUseBlock(tool_name="t1", tool_use_id="tu1", input={})])
        yield "ok "

    agent = _agent(memory, llm)
    ta_cfg = agent._config.setdefault("reach_layer", {}).setdefault("turn_assembler", {})
    if drain_max_ms is not None:
        ta_cfg["interruption"] = {"drain_max_ms": drain_max_ms}
    if silence_ms is not None:
        ta_cfg["silence_trigger"] = {"silence_ms": silence_ms}
    tool_started = asyncio.Event()

    async def slow_exec(tc, *a, **k):
        tool_started.set()
        await asyncio.sleep(tool_s)
        return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={},
                          success=True, result_text="ok")

    agent._async_gateway.execute.side_effect = slow_exec
    return agent, tool_started


async def test_turn_interrupted_while_awaiting_predecessor_keeps_its_utterance():
    """Final review 1: A in a slow tool, B waits on A, C arrives before A drains.
    B's utterance must reach C, between A's and C's."""
    memory = FakeMemory()
    agent, tool_started = _slow_tool_agent(memory)
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    await ta.submit("s", SegmentInput(text="AAA substantive", channel="bridge", user_id="s"))
    await asyncio.wait_for(tool_started.wait(), 2)
    await ta.submit("s", SegmentInput(text="BBB second", channel="bridge", user_id="s"))
    await asyncio.sleep(0.05)                        # B is waiting on A
    c = await ta.submit("s", SegmentInput(text="CCC third", channel="bridge", user_id="s"))
    events = [e async for e in ta.attach(c)]
    assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
    assert c.record.segments == ["AAA substantive", "BBB second", "CCC third"]
    text = _user_text(agent)
    assert "AAA substantive" in text and "BBB second" in text and "CCC third" in text


async def test_session_barge_in_onto_waiting_successor_keeps_its_utterance():
    """Final review 1, session path: add_segment barges in on a successor that is
    INVOKED but still waiting for its own predecessor."""
    memory = FakeMemory()
    agent, tool_started = _slow_tool_agent(memory, silence_ms=10)
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    await ta.add_segment("s", SegmentInput(text="AAA substantive", channel="bridge", user_id="s"))
    await asyncio.wait_for(tool_started.wait(), 2)
    await ta.add_segment("s", SegmentInput(text="BBB second", channel="bridge", user_id="s"))
    b = ta._sessions["s"].current_turn
    for _ in range(200):
        if b.status == TurnStatus.INVOKED:
            break
        await asyncio.sleep(0.01)
    assert b.status == TurnStatus.INVOKED            # waiting on A inside _invoke
    await ta.add_segment("s", SegmentInput(text="CCC third", channel="bridge", user_id="s"))
    c = ta._sessions["s"].current_turn
    assert c is not b
    for _ in range(300):
        if c.status == TurnStatus.COMPLETED:
            break
        await asyncio.sleep(0.01)
    assert c.status == TurnStatus.COMPLETED
    assert c.record.segments == ["AAA substantive", "BBB second", "CCC third"]


def _exchange(name, tool_use_id, **extra):
    """One persisted #193 exchange."""
    return dict({
        "tool_uses": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}],
        "tool_results": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}],
    }, **extra)


def _names(memory):
    return [ex["tool_uses"][0]["name"] for ex in memory.session.get("recent_tool_exchanges", [])]


async def test_late_persist_does_not_clobber_successor_exchanges():
    """Final review 4: A persists after its drain timed out and B completed.
    A's write must merge into what B stored, not overwrite it with A's
    start-of-turn snapshot."""
    memory = FakeMemory()
    calls = {"n": 0}

    async def llm(*args, abort_event=None, **kwargs):
        calls["n"] += 1
        n = calls["n"]
        if n in (1, 2):
            yield ""
            raise ChatToolUseRequested([ToolUseBlock(tool_name=f"t{n}", tool_use_id=f"tu{n}", input={})])
        yield "ok "

    agent = _agent(memory, llm)
    agent._config.setdefault("reach_layer", {})["turn_assembler"] = {"interruption": {"drain_max_ms": 50}}
    started = asyncio.Event()

    async def exec_(tc, *a, **k):
        if tc.tool_name == "t1":
            started.set()
            await asyncio.sleep(0.6)
        return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={},
                          success=True, result_text="ok")

    agent._async_gateway.execute.side_effect = exec_
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    a = await ta.submit("s", SegmentInput(text="AAA", channel="bridge", user_id="s"))
    await asyncio.wait_for(started.wait(), 2)
    b = await ta.submit("s", SegmentInput(text="BBB", channel="bridge", user_id="s"))
    events = [e async for e in ta.attach(b)]
    assert events[-1].turn_status == "completed"
    await asyncio.sleep(0.05)
    assert _names(memory) == ["t2"]
    await asyncio.wait_for(a.invocation_task, 2)
    await asyncio.wait_for(a.record.persist_task, 2)
    assert _names(memory) == ["t2", "t1"]
    stored = memory.session["recent_tool_exchanges"]
    assert stored[0].get("delivered") is not False and stored[1]["delivered"] is False


async def test_persist_interrupted_caps_to_max_items():
    """Final review 4: current + captured is capped to record.max_items, newest kept."""
    from src.models import TurnRecord

    memory = FakeMemory()
    memory.session["recent_tool_exchanges"] = [_exchange("p1", "p1"), _exchange("p2", "p2")]
    agent = _agent(memory, None)
    record = TurnRecord(max_items=3, prior_exchanges=[])
    captured = [_exchange("c1", "c1", delivered=False), _exchange("c2", "c2", delivered=False)]
    await agent._persist_interrupted("s", "s", record, captured, None, "bridge")
    assert _names(memory) == ["p2", "c1", "c2"]


async def test_persist_interrupted_dedupes_by_tool_use_id():
    """Final review 4: a captured round already stored (same tool_use id) is not
    duplicated; the stored copy is kept."""
    from src.models import TurnRecord

    memory = FakeMemory()
    memory.session["recent_tool_exchanges"] = [_exchange("t1", "tu1")]
    agent = _agent(memory, None)
    record = TurnRecord(max_items=5)
    captured = [_exchange("t1", "tu1", delivered=False), _exchange("t2", "tu2", delivered=False)]
    await agent._persist_interrupted("s", "s", record, captured, None, "bridge")
    stored = memory.session["recent_tool_exchanges"]
    assert _names(memory) == ["t1", "t2"]
    assert "delivered" not in stored[0] and stored[1]["delivered"] is False


async def test_persist_interrupted_reads_memory_once_for_exchanges_and_carryover():
    """Final review 4: one context_bundle read serves both the exchange merge and
    the not-fold_ran carry-over append."""
    import time as _time

    from src.models import TurnRecord

    memory = FakeMemory()
    memory.session["recent_tool_exchanges"] = [_exchange("p1", "p1")]
    memory.session["turn_carryover"] = {"segments": ["earlier"], "stopped_at_stage": "",
                                        "turn_id": "x", "written_at_ms": int(_time.time() * 1000)}
    reads = {"n": 0}
    real = memory.context_bundle

    async def counting(*a, **k):
        reads["n"] += 1
        return await real(*a, **k)

    memory.context_bundle = counting
    agent = _agent(memory, None)
    record = TurnRecord(max_items=3, fold_ran=False)
    carry = {"segments": ["now"], "stopped_at_stage": "", "turn_id": "y",
             "written_at_ms": int(_time.time() * 1000)}
    await agent._persist_interrupted("s", "s", record,
                                     [_exchange("c1", "c1", delivered=False)], carry, "bridge")
    assert reads["n"] == 1
    assert _names(memory) == ["p1", "c1"]
    assert memory.session["turn_carryover"]["segments"] == ["earlier", "now"]


async def test_persist_interrupted_read_failure_falls_back_to_snapshot():
    """Final review 4: a failed re-read neither raises nor drops the rounds; it
    merges onto the start-of-turn snapshot."""
    from src.models import TurnRecord

    memory = FakeMemory()

    async def boom(*a, **k):
        raise TimeoutError("memory down")

    memory.context_bundle = boom
    agent = _agent(memory, None)
    record = TurnRecord(max_items=3, prior_exchanges=[_exchange("p1", "p1")])
    await agent._persist_interrupted("s", "s", record,
                                     [_exchange("c1", "c1", delivered=False)], None, "bridge")
    assert _names(memory) == ["p1", "c1"]
