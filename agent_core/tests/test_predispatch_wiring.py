"""Spec E §5.4-5.5: pre-dispatch after routing on both paths.

A determined tool call (a ``predispatch`` rule on the post-routing subagent)
runs before the main LLM. On success its result is injected as a synthetic
tool_use/tool_result pair and the tool leaves this turn's tool list; on a
read failure the turn follows today's path; a write failure is injected and
the tool removed. Belongs to the Agent Core block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolResultBlock, ToolUseBlock
from src.models import ContextBundle, DoneEvent, NLUResult, ToolResult
from src.predispatch.runner import PREDISPATCH_ID
from src.tool_results import ToolResultPolicies, args_hash
from src.understanding.models import StateWrite
from src.workflow_loader import AgentWorkflow, SubAgent

from tests.fakes import fake_understander
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

JOBS_RULE = {"tool": "fetch_jobs", "unless_fresh": True,
             "args": {"query_text": {"template": "{trade|stored_trade} jobs in {location|stored_location}"}}}
APPLY_RULE = {"tool": "apply_job", "enabled": True, "on_intent": ["apply_now"],
              "args": {"profile_item_id": {"from": "session", "key": "profile_item_id"},
                       "job_item_id": {"from": "session", "key": "selected_job_item_id"}}}

FETCH_DEF = {"name": "fetch_jobs", "description": "Find jobs.",
             "input_schema": {"type": "object", "properties": {"query_text": {"type": "string"}},
                              "required": ["query_text"]}}
APPLY_DEF = {"name": "apply_job", "description": "Apply to a job.",
             "input_schema": {"type": "object",
                              "properties": {"profile_item_id": {"type": "string"},
                                             "job_item_id": {"type": "string"}},
                              "required": ["profile_item_id", "job_item_id"]}}
PROFILE_DEF = {"name": "fetch_profile", "description": "Read the profile.",
               "input_schema": {"type": "object", "properties": {}}}
TOOLS = [FETCH_DEF, APPLY_DEF, PROFILE_DEF]
WRITE_TOOLS = {"apply_job"}

CONFIG = {"connectors": {"read": [{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600}},
                                  {"name": "fetch_profile"}],
                         "write": [{"name": "apply_job"}]}}
JOBS_JSON = json.dumps({"items": [{"item_id": "J-1", "title": "Welder"}]})
APPLY_SESSION = {"profile_item_id": "P-1", "selected_job_item_id": "J-1"}


def _sub(sid, rules=(), is_start=False):
    return SubAgent(id=sid, name=sid, description=sid, is_start=is_start, is_terminal=False,
                    special_handler=None, tools=[t["name"] for t in TOOLS],
                    system_prompt=f"{sid} prompt", output_format=None, routing=[],
                    predispatch=[dict(r) for r in rules])


def _workflow(rules, post_applied=False):
    subs = {"start": _sub("start", rules, is_start=True)}
    if post_applied:
        subs["post_applied"] = _sub("post_applied")
    wf = MagicMock(spec=AgentWorkflow)
    wf.start_subagent_id = "start"
    wf.subagents = subs
    wf.tool_defs = {k: list(TOOLS) for k in subs}
    wf.global_tool_defs = []
    wf.global_routing = []
    wf.default_fallback_subagent_id = "start"
    wf.agent_system_prompt = "System prompt"
    wf.resolve_tools_for.side_effect = lambda sid: list(TOOLS)
    return wf


def _gateway_result(tc, *, apply_ok=True, session_values=None):
    if tc.tool_name == "fetch_jobs":
        return ToolResult(tool_use_id=tc.tool_use_id, tool_name="fetch_jobs", result={}, success=True,
                          result_text=JOBS_JSON, projected=True, session_values=dict(session_values or {}))
    if tc.tool_name == "apply_job":
        return ToolResult(tool_use_id=tc.tool_use_id, tool_name="apply_job", result={}, success=apply_ok,
                          result_text='{"status": "applied"}' if apply_ok else "Error: upstream 500",
                          error=None if apply_ok else "upstream 500")
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True,
                      result_text="{}")


def _wire(agent, *, caps=None, timeout_s=1.5, consent=True):
    """Pre-dispatch inputs __init__ derives from config and the registry (pinned in TestInit)."""
    agent._tool_policies = ToolResultPolicies.from_config(CONFIG)
    agent._tool_schemas = {d["name"]: d["input_schema"] for d in TOOLS}
    agent._write_tools = set(WRITE_TOOLS)
    agent._predispatch_tables = {}
    agent._predispatch_timeout_s = timeout_s
    agent._tool_registry.requires_consent.side_effect = lambda name: name in WRITE_TOOLS
    agent._tool_registry.get_route.return_value = None
    agent._manager_agent._tool_call_caps = dict(caps or {})
    agent._manager_agent._grounded_params = {}
    agent._manager_agent.last_llm_calls = 0
    agent._trust.check_consent.return_value = consent
    return agent


def _jobs_entry(query_text="Welder jobs in Bengaluru"):
    return {"tool": "fetch_jobs", "args_hash": args_hash({"query_text": query_text}),
            "data": {"items": [{"item_id": "J-0"}]}, "fetched_at": time.time(),
            "expires_at": 9e12, "origin": "turn", "scope": "session"}


def _agent(session, rules, *, intent="any_input", writes=(), profile=None, entries=None,
           rounds=(), gateway=None, caps=None, timeout_s=1.5, consent=True, post_applied=False,
           fetch_when_offered=False, manager=None):
    """Stream AgentCore routed into ``start`` (carrying ``rules``) with recorded LLM requests.

    The LLM requests each tool round in ``rounds`` in turn, then answers in text.
    With ``fetch_when_offered`` it behaves like the real model on a job_match
    entry: offered ``fetch_jobs`` and holding no result, it calls the tool first.
    Returns ``(agent, requests)``.
    """
    agent = _make_agent_core(workflow=_workflow(rules, post_applied=post_applied))
    if manager is not None:
        agent._manager_agent = manager
    _wire(agent, caps=caps, timeout_s=timeout_s, consent=consent)
    agent._async_trust.check_consent = AsyncMock(return_value=consent)
    agent._async_memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "start", **session}, profile=dict(profile or {}),
        tool_results=list(entries or []))
    agent._async_memory.apply_tool_results = AsyncMock()
    agent._async_gateway.execute = AsyncMock(
        side_effect=gateway or (lambda tc, *a, **k: _gateway_result(tc)))
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    agent._understander = fake_understander(NLUResult(intent=intent, entities={}, confidence=0.9),
                                            writes=writes)
    requests: list = []

    async def mock_stream(request, *, abort_event=None):
        requests.append(request)
        if (fetch_when_offered and len(requests) == 1 and "fetch_jobs" in _names(request)
                and not _has_pair(request.messages)):
            raise ChatToolUseRequested([ToolUseBlock(
                tool_name="fetch_jobs", tool_use_id="tu_m", input={"query_text": "Welder jobs in Bengaluru"})])
        if len(requests) <= len(rounds):
            raise ChatToolUseRequested(list(rounds[len(requests) - 1]))
        yield "Here you go. "

    agent._llm.stream = mock_stream
    return agent, requests


def _names(request) -> list[str]:
    return [t.name for t in (request.tools or [])]


def _has_pair(messages) -> bool:
    return any(getattr(b, "tool_use_id", None) == PREDISPATCH_ID for m in messages for b in m.content)


def _gateway_tools(execute_mock) -> list[str]:
    return [c.args[0].tool_name for c in execute_mock.await_args_list]


def _complete_extras(caplog, message):
    recs = [r for r in caplog.records if r.getMessage() == message]
    assert len(recs) == 1, [r.getMessage() for r in caplog.records]
    return recs[0]


def _assert_replayed_under_turn_id(exchange):
    """The persisted exchange carries a turn-unique id, so next turn's replay
    never shares PREDISPATCH_ID with that turn's own pre-dispatch."""
    use_id = exchange["tool_uses"][0]["id"]
    assert use_id.startswith(PREDISPATCH_ID + "-") and use_id != PREDISPATCH_ID
    assert exchange["tool_uses"][0]["name"] == "fetch_jobs"
    assert exchange["tool_results"][0]["tool_use_id"] == use_id


def _session_writes(write_calls, key):
    return [c.args[4] for c in write_calls if c.args[2] == "session" and c.args[3] == key]


# ── stream path ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_predispatch_one_llm_call_and_tool_removed(caplog):
    agent, requests = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE],
                             fetch_when_offered=True)
    with patch("src.orchestrator.record_predispatch") as rec, \
            caplog.at_level(logging.INFO, logger="src.orchestrator"):
        events = await _collect_events(agent, _make_turn_input())

    assert len(requests) == 1                                   # one main-LLM call
    assert agent._async_gateway.execute.await_count == 1
    req = requests[0]
    assert "fetch_jobs" not in _names(req)
    assert {"apply_job", "fetch_profile"} <= set(_names(req))   # other tools stay
    msgs = req.messages
    use, result = msgs[-2], msgs[-1]
    assert use.role == "assistant" and isinstance(use.content[0], ToolUseBlock)
    assert use.content[0].tool_use_id == PREDISPATCH_ID and use.content[0].tool_name == "fetch_jobs"
    assert use.content[0].input == {"query_text": "Welder jobs in Bengaluru"}
    assert result.role == "user" and isinstance(result.content[0], ToolResultBlock)
    assert result.content[0].tool_use_id == PREDISPATCH_ID and result.content[0].content == JOBS_JSON
    assert result.content[0].is_error is False
    assert msgs[-3].role == "user" and msgs[-3].content[0].text == "Hello"   # utterance before the pair
    assert agent._async_gateway.execute.await_count == 1
    known = agent._manager_agent.build_system_prompt.call_args.kwargs["known_facts"]
    assert "fetch_jobs" in known and "J-1" in known              # in the cache before the prompt

    writes = agent._async_memory.write.call_args_list
    assert "fetch_jobs" in _session_writes(writes, "served_tool_results")[0]
    _assert_replayed_under_turn_id(_session_writes(writes, "recent_tool_exchanges")[0][-1])
    rec.assert_called_once_with("fetch_jobs", "fired")
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    assert done.turn_status == "completed" and done.was_tool_used is True
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert (r.llm_calls, r.predispatch_tool, r.predispatch_outcome) == (1, "fetch_jobs", "fired")
    assert isinstance(r.predispatch_ms, int)


@pytest.mark.asyncio
async def test_fresh_cache_no_predispatch(caplog):
    agent, requests = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE],
                             entries=[_jobs_entry()])
    with patch("src.orchestrator.record_predispatch") as rec, \
            caplog.at_level(logging.INFO, logger="src.orchestrator"):
        await _collect_events(agent, _make_turn_input())

    agent._async_gateway.execute.assert_not_awaited()
    assert len(requests) == 1 and "fetch_jobs" in _names(requests[0])
    assert not _has_pair(requests[0].messages)
    rec.assert_called_once_with(None, "skipped_fresh")
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert (r.predispatch_tool, r.predispatch_outcome) == (None, "skipped_fresh")


@pytest.mark.asyncio
async def test_read_timeout_falls_back(caplog):
    n = {"calls": 0}

    async def slow_then_fast(tc, *a, **k):
        n["calls"] += 1
        if n["calls"] == 1:
            await asyncio.sleep(0.5)
        return _gateway_result(tc)

    model_call = [ToolUseBlock(tool_name="fetch_jobs", tool_use_id="tu_1",
                               input={"query_text": "Welder jobs in Bengaluru"})]
    agent, requests = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE],
                             gateway=slow_then_fast, timeout_s=0.05, rounds=[model_call])
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        await _collect_events(agent, _make_turn_input())

    assert "fetch_jobs" in _names(requests[0])          # still offered
    assert not _has_pair(requests[0].messages)          # nothing injected
    assert len(requests) == 2                           # today's path: the model calls it
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert (r.llm_calls, r.predispatch_outcome) == (2, "timeout")


@pytest.mark.asyncio
async def test_predispatch_uses_values_written_this_turn():
    # Profile and session still hold the old trade; the caller corrects it this turn.
    agent, requests = _agent(
        {"trade": "Electrician", "location": "Bengaluru"}, [JOBS_RULE],
        profile={"trade": "Electrician"},
        writes=[StateWrite("session", "trade", "Welder"),
                StateWrite("session", "slot_provenance", ["trade"])])
    await _collect_events(agent, _make_turn_input())

    sent = agent._async_gateway.execute.await_args_list[0].args[0]
    assert sent.input_params == {"query_text": "Welder jobs in Bengaluru"}
    assert requests[0].messages[-2].content[0].input == {"query_text": "Welder jobs in Bengaluru"}


@pytest.mark.asyncio
async def test_predispatched_write_is_never_repeated():
    model_apply = [ToolUseBlock(tool_name="apply_job", tool_use_id="tu_1",
                                input={"profile_item_id": "P-1", "job_item_id": "J-1"})]
    agent, requests = _agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now",
                             rounds=[model_apply], caps={"apply_job": 1})
    await _collect_events(agent, _make_turn_input())

    assert _gateway_tools(agent._async_gateway.execute) == ["apply_job"]   # exactly one apply
    assert "apply_job" not in _names(requests[0])
    assert requests[0].messages[-1].content[0].tool_use_id == PREDISPATCH_ID
    refusal = [b.content for b in requests[1].messages[-1].content if b.tool_use_id == "tu_1"][0]
    assert "has already run this turn" in refusal


@pytest.mark.asyncio
async def test_write_timeout_consumes_the_cap():
    async def slow(tc, *a, **k):
        await asyncio.sleep(0.5)
        return _gateway_result(tc)

    model_apply = [ToolUseBlock(tool_name="apply_job", tool_use_id="tu_1",
                                input={"profile_item_id": "P-1", "job_item_id": "J-1"})]
    agent, requests = _agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now", gateway=slow,
                             rounds=[model_apply], caps={"apply_job": 1}, timeout_s=0.05)
    await _collect_events(agent, _make_turn_input())

    assert agent._async_gateway.execute.await_count == 1      # the model's retry was refused
    first = requests[0]
    assert "apply_job" not in _names(first)
    assert first.messages[-1].content[0].is_error is True     # the failure is spoken
    refusal = [b.content for b in requests[1].messages[-1].content if b.tool_use_id == "tu_1"][0]
    assert "has already run this turn" in refusal


@pytest.mark.asyncio
async def test_write_failure_injected_and_tool_removed(caplog):
    agent, requests = _agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now",
                             gateway=lambda tc, *a, **k: _gateway_result(tc, apply_ok=False))
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        await _collect_events(agent, _make_turn_input())

    assert len(requests) == 1 and "apply_job" not in _names(requests[0])
    block = requests[0].messages[-1].content[0]
    assert block.tool_use_id == PREDISPATCH_ID and block.is_error is True
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert r.predispatch_outcome == "failed"


@pytest.mark.asyncio
async def test_consent_refusal_falls_back_without_breaking_the_turn(caplog):
    """Blue Dots records consent outside the Trust Layer: an enabled write is refused and the turn goes on."""
    agent, requests = _agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now", consent=False)
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        events = await _collect_events(agent, _make_turn_input())

    agent._async_gateway.execute.assert_not_awaited()
    assert "apply_job" in _names(requests[0]) and not _has_pair(requests[0].messages)
    assert [e for e in events if isinstance(e, DoneEvent)][0].turn_status == "completed"
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert (r.predispatch_tool, r.predispatch_outcome) == ("apply_job", "refused_guard")


@pytest.mark.asyncio
async def test_internal_error_proceeds_without_predispatch(caplog):
    agent, requests = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    agent._select_predispatch = MagicMock(side_effect=RuntimeError("caller said Welder"))
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        events = await _collect_events(agent, _make_turn_input())

    assert [e for e in events if isinstance(e, DoneEvent)][0].turn_status == "completed"
    assert "fetch_jobs" in _names(requests[0]) and not _has_pair(requests[0].messages)
    agent._async_gateway.execute.assert_not_awaited()
    err = [r for r in caplog.records if r.getMessage() == "orchestrator.predispatch_error"]
    assert err and err[0].error == "RuntimeError" and "Welder" not in str(err[0].__dict__)
    r = _complete_extras(caplog, "orchestrator.stream_turn_complete")
    assert r.predispatch_outcome == "error"


@pytest.mark.asyncio
async def test_predispatch_log_carries_keys_not_values(caplog):
    agent, _requests = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        await _collect_events(agent, _make_turn_input())

    r = _complete_extras(caplog, "orchestrator.predispatch")
    assert (r.tool, r.outcome, r.arg_keys) == ("fetch_jobs", "fired", ["query_text"])
    assert "Welder" not in str(r.__dict__) and "Bengaluru" not in str(r.__dict__)


@pytest.mark.asyncio
async def test_post_applied_hook_fires_for_predispatched_apply_job():
    """The model answers in text only; the pre-dispatched apply still moves the session on."""
    agent, requests = _agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now", post_applied=True)
    await _collect_events(agent, _make_turn_input())

    assert len(requests) == 1
    assert "post_applied" in _session_writes(agent._async_memory.write.await_args_list,
                                             "current_subagent_id")


# ── sync path ──────────────────────────────────────────────────────────────

def _sync_agent(session, rules, *, intent="any_input", consent=True, post_applied=False,
                gateway=None, manager=None, caps=None):
    from tests.test_orchestrator import _make_agent
    agent = _make_agent(session_data={"current_subagent_id": "start", **session},
                        workflow=_workflow(rules, post_applied=post_applied),
                        nlu_result=NLUResult(intent=intent, entities={}, confidence=0.9))
    if manager is not None:
        agent._manager_agent = manager
    _wire(agent, caps=caps, consent=consent)
    gw = agent._manager_agent._gateway = MagicMock()
    gw.execute.side_effect = gateway or (lambda tc, *a, **k: _gateway_result(tc))
    return agent, gw


def _sync_turn(agent):
    from tests.test_orchestrator import _turn_input
    return agent.process_turn(_turn_input())


def test_sync_path_predispatch(caplog):
    agent, gw = _sync_agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    with patch("src.orchestrator.record_predispatch") as rec, \
            caplog.at_level(logging.INFO, logger="src.orchestrator"):
        result = _sync_turn(agent)

    assert agent._llm.call.call_count == 1
    req = agent._llm.call.call_args.args[0]
    assert "fetch_jobs" not in _names(req)
    assert req.messages[-2].content[0].tool_use_id == PREDISPATCH_ID
    assert req.messages[-1].content[0].tool_use_id == PREDISPATCH_ID
    assert req.messages[-1].content[0].content == JOBS_JSON
    assert req.messages[-3].content[0].text == "Hello"
    assert gw.execute.call_count == 1
    kw = agent._manager_agent.run_turn.call_args.kwargs
    assert "fetch_jobs" not in [t["name"] for t in kw["active_tools"]]
    assert kw["turn_tool_counts"] == {"fetch_jobs": 1}
    rec.assert_called_once_with("fetch_jobs", "fired")
    assert result.was_tool_used is True
    writes = agent._memory.write.call_args_list
    assert "fetch_jobs" in _session_writes(writes, "served_tool_results")[0]
    _assert_replayed_under_turn_id(_session_writes(writes, "recent_tool_exchanges")[0][-1])
    r = _complete_extras(caplog, "orchestrator.turn_complete")
    assert (r.llm_calls, r.predispatch_tool, r.predispatch_outcome) == (1, "fetch_jobs", "fired")
    assert isinstance(r.predispatch_ms, int)


def test_sync_session_values_written_before_run_turn():
    agent, _gw = _sync_agent(
        {"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE],
        gateway=lambda tc, *a, **k: _gateway_result(tc, session_values={"jobs_found": 3}))
    _sync_turn(agent)

    assert _session_writes(agent._memory.write.call_args_list, "jobs_found") == [3]
    assert agent._manager_agent.run_turn.call_args.kwargs["session_values"]["jobs_found"] == 3


def test_sync_consent_refusal_falls_back(caplog):
    agent, gw = _sync_agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now", consent=False)
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        _sync_turn(agent)

    gw.execute.assert_not_called()
    req = agent._llm.call.call_args.args[0]
    assert "apply_job" in _names(req) and not _has_pair(req.messages)
    assert _complete_extras(caplog, "orchestrator.turn_complete").predispatch_outcome == "refused_guard"


def test_sync_post_applied_hook_fires_for_predispatched_apply_job():
    agent, gw = _sync_agent(APPLY_SESSION, [APPLY_RULE], intent="apply_now", post_applied=True)
    _sync_turn(agent)

    assert [c.args[0].tool_name for c in gw.execute.call_args_list] == ["apply_job"]
    assert "post_applied" in _session_writes(agent._memory.write.call_args_list, "current_subagent_id")


def test_sync_internal_error_proceeds_without_predispatch(caplog):
    agent, gw = _sync_agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    agent._select_predispatch = MagicMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        result = _sync_turn(agent)

    assert result.response_text
    gw.execute.assert_not_called()
    req = agent._llm.call.call_args.args[0]
    assert "fetch_jobs" in _names(req) and not _has_pair(req.messages)
    assert _complete_extras(caplog, "orchestrator.turn_complete").predispatch_outcome == "error"


def test_sync_without_gateway_does_not_predispatch():
    agent, _gw = _sync_agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    agent._manager_agent._gateway = None
    _sync_turn(agent)
    assert not _has_pair(agent._llm.call.call_args.args[0].messages)


# ── startup ────────────────────────────────────────────────────────────────

def _init_agent(config, defs, rules):
    reg = MagicMock()
    reg.get_tool_definitions.return_value = list(defs)
    return _make_agent_core(config=config, tool_registry=reg, workflow=_workflow(rules))


class TestInit:

    def test_reads_tables_timeout_write_tools_and_schemas(self):
        config = {"agent": {"predispatch_timeout_ms": 800},
                  "predispatch_tables": {"city_canonical": {"Bangalore": "Bengaluru"}},
                  "connectors": {"read": [{"name": "fetch_jobs"}],
                                 "write": [{"name": "apply_job"}],
                                 "identity": [{"name": "save_profile"}]}}
        agent = _init_agent(config, TOOLS, [JOBS_RULE])
        assert agent._predispatch_timeout_s == 0.8
        assert agent._predispatch_tables == {"city_canonical": {"Bangalore": "Bengaluru"}}
        assert agent._write_tools == {"apply_job", "save_profile"}
        assert agent._tool_schemas["fetch_jobs"] == FETCH_DEF["input_schema"]

    def test_default_timeout(self):
        assert _init_agent({}, TOOLS, [])._predispatch_timeout_s == 1.5

    def test_rejects_arg_that_is_not_an_agent_parameter(self):
        bad = {"tool": "fetch_jobs", "args": {"city": {"from": "session", "key": "location"}}}
        with pytest.raises(ValueError, match=r"subagents\[start\]\.predispatch\[0\].*city"):
            _init_agent({}, TOOLS, [bad])

    def test_empty_tool_list_does_not_raise(self):
        bad = {"tool": "fetch_jobs", "args": {"city": {"from": "session", "key": "location"}}}
        assert _init_agent({}, [], [bad])._tool_schemas == {}


def test_sync_llm_calls_counts_follow_up_rounds(caplog):
    agent, _gw = _sync_agent({}, [])
    agent._manager_agent.last_llm_calls = 2
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        _sync_turn(agent)
    r = _complete_extras(caplog, "orchestrator.turn_complete")
    assert (r.llm_calls, r.predispatch_tool, r.predispatch_outcome) == (3, None, None)


def test_initial_response_shape_unchanged_for_text_turn():
    """A turn with no rule offers every tool and adds no pair (today's path)."""
    agent, gw = _sync_agent({}, [])
    _sync_turn(agent)
    req = agent._llm.call.call_args.args[0]
    assert set(_names(req)) >= {"fetch_jobs", "apply_job"} and not _has_pair(req.messages)
    gw.execute.assert_not_called()

