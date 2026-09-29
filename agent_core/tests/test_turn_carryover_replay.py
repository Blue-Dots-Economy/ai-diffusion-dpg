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
    agent._nlu_processor = type("P", (), {"process": lambda s, *a, **k: NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9)})()
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
        return turn.record.segments

    job = "मुझे डिलीवरी बॉय का जॉब चाहिए"
    hello = "हेलो कोई है"
    assert await run([job, hello, hello]) == [job, hello, hello]
    long = await run([job] + [hello] * 6)
    assert len(long) == 3 and long == [hello] * 3
