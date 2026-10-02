"""Spec D §5: the guard runs on model text before Trust, on all three stream sites and the sync path."""
from unittest.mock import MagicMock

import pytest

from src.models import SentenceEvent, TrustCheckResult
from tests.test_orchestrator import _make_agent, _turn_input
from tests.test_stream_turn import (
    _collect_events, _make_agent_core, _make_turn_input, _tr_agent,
)

_CONTRACT = {"default_language": "hindi",
             "languages": {"hindi": {"script": "devanagari", "numbers": "words"},
                           "english": {"script": "latin", "numbers": "words"}},
             "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True}}
_WORDS = "सत्ताईस हज़ार छह सौ बीस"


def _set_contract(agent):
    # Replace (not mutate) so module-level shared configs never leak between tests.
    agent._config = {**agent._config, "channels": {
        **agent._config["channels"], "cli": {"system_prompt_suffix": "", "output_contract": _CONTRACT}}}


def _hindi_stream_agent():
    agent = _make_agent_core()
    _set_contract(agent)
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("msg", "hindi")
    return agent


def _agent_with_contract(tokens):
    agent = _hindi_stream_agent()
    checked = []

    async def check_output(session_id, text):
        checked.append(text)
        return TrustCheckResult(passed=True, action="allow")

    agent._async_trust.check_output.side_effect = check_output

    async def stream(*a, **k):
        for t in tokens:
            yield t

    agent._llm.stream = stream
    return agent, checked


def _spoken(events):
    return " ".join(e.text for e in events if isinstance(e, SentenceEvent))


@pytest.mark.asyncio
async def test_guard_rewrites_before_trust_and_in_the_tail():
    agent, checked = _agent_with_contract(["सैलरी 27620 है। ", "**कुल** 5"])   # tail has no terminator
    spoken = _spoken(await _collect_events(agent, _make_turn_input(channel="cli")))
    assert "27620" not in spoken and "5" not in spoken and "**" not in spoken
    assert _WORDS in spoken and "पाँच" in spoken
    assert checked and all(not any(ch.isdigit() for ch in t) for t in checked)


@pytest.mark.asyncio
async def test_guard_language_follows_language_preference():
    agent, _ = _agent_with_contract(["It pays 25000. "])
    agent._async_memory.context_bundle.return_value.session["language_preference"] = "english"
    spoken = _spoken(await _collect_events(agent, _make_turn_input(channel="cli")))
    assert "twenty-five thousand" in spoken


@pytest.mark.asyncio
async def test_no_contract_leaves_text_alone():
    agent = _make_agent_core()

    async def stream(*a, **k):
        yield "सैलरी 27620 है। "

    agent._llm.stream = stream
    assert "27620" in _spoken(await _collect_events(agent, _make_turn_input(channel="cli")))


@pytest.mark.asyncio
async def test_guard_runs_on_the_post_tool_stream_site():
    """The second LLM stream (after a tool round) goes through the guard too."""
    from src.chat_provider.types import ToolUseBlock
    from src.chat_provider.base import ToolUseRequested

    agent = _hindi_stream_agent()
    checked = []

    async def check_output(session_id, text):
        checked.append(text)
        return TrustCheckResult(passed=True, action="allow")

    agent._async_trust.check_output.side_effect = check_output
    calls = {"n": 0}

    async def stream(request, *, abort_event=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield "देखता हूँ। "
            raise ToolUseRequested([ToolUseBlock(tool_name="get_balance", tool_use_id="tu_1",
                                                 input={"account": "1"})])
        yield "सैलरी 27620 है। "

    agent._llm.stream = stream
    from src.models import ToolResult
    from unittest.mock import AsyncMock
    agent._async_gateway.execute = AsyncMock(return_value=ToolResult(
        tool_use_id="tu_1", tool_name="get_balance", result={}, success=True,
        result_text='{"balance": 5}', projected=True))
    spoken = _spoken(await _collect_events(agent, _make_turn_input(channel="cli")))
    assert "27620" not in spoken and _WORDS in spoken
    assert all(not any(ch.isdigit() for ch in t) for t in checked)


def test_sync_guard_runs_before_trust():
    agent = _make_agent(manager_text="सैलरी 27620 है।")
    _set_contract(agent)
    agent._language_normaliser.normalise.return_value = ("Hello", "hindi")
    result = agent.process_turn(_turn_input())
    sent = agent._trust.check_output.call_args.args[1]
    assert _WORDS in sent and not any(ch.isdigit() for ch in sent)
    assert _WORDS in result.response_text and not any(ch.isdigit() for ch in result.response_text)


def test_guard_error_counts_and_passes_through(monkeypatch):
    from src.output.guard import OutputGuard
    import src.chat_provider.metrics as m

    seen = []
    monkeypatch.setattr(m, "record_output_guard_error", lambda: seen.append(1))
    g = OutputGuard(_CONTRACT)
    monkeypatch.setattr(g, "_language_entry", MagicMock(side_effect=RuntimeError("boom")))
    assert g.apply("सैलरी 27620", "hindi").text == "सैलरी 27620"
    assert seen == [1]


@pytest.mark.asyncio
async def test_nested_stream_gateway_site_shapes_each_live_result():
    """Round 2 calls the same tool with different args (not a cache hit): shaped again."""
    from src.chat_provider.types import ToolUseBlock

    agent, _order, _requests = _tr_agent([
        [ToolUseBlock(tool_name="get_balance", tool_use_id="tu_1", input={"account": "999"})],
        [ToolUseBlock(tool_name="get_balance", tool_use_id="tu_2", input={"account": "998"})],
    ])
    agent._result_shaper = MagicMock()
    agent._result_shaper.shape.side_effect = lambda r: r
    await _collect_events(agent, _make_turn_input())
    assert agent._result_shaper.shape.call_count == 2
