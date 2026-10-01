"""Stream-path wiring of dialogue_act mode."""
from unittest.mock import MagicMock

import pytest

from src.models import ContextBundle, NLUResult, TurnRecord
from src.understanding.history import RECENT_TURNS_KEY
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

pytestmark = pytest.mark.asyncio


def _understanding(intent="any_input", writes=(), signals=()):
    return TurnUnderstanding(
        nlu_result=NLUResult(intent=intent, entities={}, sentiment="neutral", confidence=1.0),
        dialogue=DialogueActResult(acts=("provide_info",), relation="answers_pending"),
        pending_id="age", writes=list(writes), signals=list(signals))


def _agent(understanding):
    agent = _make_agent_core()
    agent._understander = MagicMock()
    agent._understander.understand.return_value = understanding
    agent._dialogue_cfg = MagicMock(history_turns=2, signal_types={})
    agent._nlu_processor = MagicMock()

    async def mock_stream(*args, **kwargs):
        yield "ठीक है। "

    agent._llm.stream = mock_stream
    return agent


async def test_dialogue_act_mode_replaces_nlu_and_applies_writes():
    u = _understanding(writes=[StateWrite("session", "age", 25), StateWrite("session", "slot_provenance", ["age"])])
    agent = _agent(u)
    await _collect_events(agent, _make_turn_input())
    agent._nlu_processor.process.assert_not_called()
    ctx = agent._understander.understand.call_args.args[0]
    assert ctx.tool_cache is not None                       # cache built before NLU
    written = {(c.args[2], c.args[3]): c.args[4] for c in agent._async_memory.write.await_args_list}
    assert written[("session", "age")] == 25 and written[("session", "slot_provenance")] == ["age"]


async def test_caller_turn_reaches_the_prompt():
    agent = _agent(_understanding())
    await _collect_events(agent, _make_turn_input())
    kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert kwargs["caller_turn"].startswith("acts: provide_info")


async def test_recent_turn_appended_at_end_of_turn():
    agent = _agent(_understanding())
    await _collect_events(agent, _make_turn_input())
    # Step 11 schedules this write with create_task (like current_question), so
    # it is recorded as a call but may not have been awaited when the stream ends.
    writes = [c for c in agent._async_memory.write.call_args_list if c.args[3] == RECENT_TURNS_KEY]
    assert writes and writes[-1].args[4][-1]["bot"].startswith("ठीक है")


async def test_signals_written_as_signal_nodes():
    agent = _agent(_understanding(signals=["pay_disappointment"]))
    await _collect_events(agent, _make_turn_input())
    sig = [c for c in agent._async_memory.write.await_args_list if c.args[2] == "signal"]
    assert sig and sig[0].args[4]["type"] == "pay_disappointment"


async def test_intent_mode_untouched():
    agent = _make_agent_core()
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = NLUResult(intent="greeting", entities={}, sentiment="neutral",
                                                           confidence=0.9)

    async def mock_stream(*args, **kwargs):
        yield "Hi. "

    agent._llm.stream = mock_stream
    await _collect_events(agent, _make_turn_input())
    agent._nlu_processor.process.assert_called_once()
    assert agent._manager_agent.build_system_prompt.call_args.kwargs.get("caller_turn", "") == ""
    assert not any(c.args[3] == RECENT_TURNS_KEY for c in agent._async_memory.write.call_args_list)


async def test_interrupted_turn_persists_what_was_spoken():
    agent = _agent(_understanding())
    agent._async_memory.context_bundle.return_value = ContextBundle(session={RECENT_TURNS_KEY: []}, profile={})
    record = TurnRecord()
    record.spoken = ["बेंगलुरु में तीन नौकरियां हैं।"]
    record.segments = ["वेल्डर"]
    await agent._persist_interrupted("s1", "u1", record, exchanges=[], carry=None)
    by_key = {c.args[3]: c.args[4] for c in agent._async_memory.write.await_args_list}
    assert by_key["current_question"] == "बेंगलुरु में तीन नौकरियां हैं।"
    assert by_key[RECENT_TURNS_KEY][-1] == {"caller": "वेल्डर", "bot": "बेंगलुरु में तीन नौकरियां हैं।",
                                           "interrupted": True}


async def test_interrupted_turn_with_nothing_spoken_writes_no_question():
    agent = _agent(_understanding())
    record = TurnRecord()
    await agent._persist_interrupted("s1", "u1", record, exchanges=[], carry=None)
    assert not any(c.args[3] == "current_question" for c in agent._async_memory.write.await_args_list)
