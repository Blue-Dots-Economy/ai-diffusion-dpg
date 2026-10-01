"""
agent_core/tests/test_turn_path_identity_parity.py

Guards the invariant that BOTH turn paths forward the caller's identity to the
Action Gateway.

Agent Core has two paths — sync ``process_turn`` (channels running
assembly_mode ``direct``: web, CLI, MCP) and async ``stream_turn`` (``session``
mode: voice, and web when configured for it). They must behave identically with
respect to identity, because connectors substitute ``{user_id}`` into request
paths and body templates.

They diverged once: ``stream_turn`` passed ``user_id`` to the gateway while
``ManagerAgent._execute_tool`` — the sync path's gateway call — did not. Every
connector templating ``{user_id}`` then received an empty string on the sync
path only. A whole-placeholder resolving to "" is DROPPED from the rendered
body, so the request went out missing a required field and the upstream
rejected it for a reason that looked unrelated to identity.

Nothing caught it: the block's suite passed identically with and without the
fix. These tests exist so the two paths cannot drift apart silently again.
Belongs to the Agent Core block in the DPG framework.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat_provider.types import ToolUseBlock
from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.models import NLUResult, ToolResult

from tests.test_manager_agent import (
    MESSAGES,
    SESSION_ID,
    _make_manager,
    _text_response,
    _tool_call,
    _tool_response,
)
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

USER_ID = "919900000001"


def _forwarded_user_id(execute_mock) -> str | None:
    """Pull user_id out of the gateway call, positional or keyword."""
    args, kwargs = execute_mock.call_args
    if "user_id" in kwargs:
        return kwargs["user_id"]
    return args[2] if len(args) > 2 else None


# ── sync path: run_turn → _execute_tool → Action Gateway ────────────────────

def test_sync_path_forwards_user_id_to_gateway():
    """run_turn must hand user_id to gateway.execute, not drop it."""
    tc = _tool_call()
    initial = _tool_response(tc)
    agent, _llm, _registry, gateway, _trust = _make_manager(
        llm_responses=[initial, _text_response()],
        tool_result=ToolResult(
            tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True
        ),
    )

    agent.run_turn(list(MESSAGES), SESSION_ID, initial, user_id=USER_ID)

    gateway.execute.assert_called_once()
    assert _forwarded_user_id(gateway.execute) == USER_ID


def test_sync_path_defaults_to_empty_when_caller_has_no_identity():
    """Callers that omit user_id keep working; the default is "", not a crash."""
    tc = _tool_call()
    initial = _tool_response(tc)
    agent, _llm, _registry, gateway, _trust = _make_manager(
        llm_responses=[initial, _text_response()],
        tool_result=ToolResult(
            tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True
        ),
    )

    agent.run_turn(list(MESSAGES), SESSION_ID, initial)

    gateway.execute.assert_called_once()
    assert _forwarded_user_id(gateway.execute) == ""


# ── async path: stream_turn → Action Gateway ────────────────────────────────

@pytest.mark.asyncio
async def test_stream_path_forwards_user_id_to_gateway():
    """stream_turn must hand the same identity to the async gateway."""
    agent = _make_agent_core()

    call_count = 0

    async def mock_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield "checking"
            raise ChatToolUseRequested(
                [ToolUseBlock(tool_name="search", tool_use_id="tu_1", input={"q": "x"})]
            )
        else:
            yield "done. "

    agent._llm.stream = mock_stream
    agent._async_gateway.execute = AsyncMock(
        return_value=ToolResult(
            tool_use_id="tu_1", tool_name="search", result={}, success=True, result_text="ok"
        )
    )
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("msg", "english")
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9
    )

    await _collect_events(agent, _make_turn_input(user_id=USER_ID))

    assert agent._async_gateway.execute.await_count >= 1
    assert _forwarded_user_id(agent._async_gateway.execute) == USER_ID


# ── the invariant itself ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_paths_forward_identical_identity():
    """Parity: whatever identity a turn carries reaches the gateway on BOTH paths.

    This is the equivalence the original bug broke — one path forwarded, the
    other silently did not.
    """
    tc = _tool_call()
    initial = _tool_response(tc)
    sync_agent, _l, _r, sync_gateway, _t = _make_manager(
        llm_responses=[initial, _text_response()],
        tool_result=ToolResult(
            tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True
        ),
    )
    sync_agent.run_turn(list(MESSAGES), SESSION_ID, initial, user_id=USER_ID)

    stream_agent = _make_agent_core()
    count = 0

    async def mock_stream(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            yield "checking"
            raise ChatToolUseRequested(
                [ToolUseBlock(tool_name="search", tool_use_id="tu_1", input={})]
            )
        else:
            yield "done. "

    stream_agent._llm.stream = mock_stream
    stream_agent._async_gateway.execute = AsyncMock(
        return_value=ToolResult(
            tool_use_id="tu_1", tool_name="search", result={}, success=True, result_text="ok"
        )
    )
    stream_agent._language_normaliser = MagicMock()
    stream_agent._language_normaliser.normalise.return_value = ("msg", "english")
    stream_agent._nlu_processor = MagicMock()
    stream_agent._nlu_processor.process.return_value = NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9
    )
    await _collect_events(stream_agent, _make_turn_input(user_id=USER_ID))

    assert _forwarded_user_id(sync_gateway.execute) == _forwarded_user_id(
        stream_agent._async_gateway.execute
    ) == USER_ID


# ── tool-result persistence: same outcome on both paths ─────────────────────
#
# The sync side mirrors process_turn's wiring of ManagerAgent.run_turn (the
# same tool_cache / remember_handler it passes) and drains the batch once, as
# process_turn does at the end of the turn. The stream side runs the real
# stream_turn and collects every apply_tool_results batch it sent.

from src.models import ToolCall  # noqa: E402
from src.remember import RememberTool  # noqa: E402
from src.tool_results import ToolResultPolicies, TurnToolCache  # noqa: E402
from src.chat_provider.types import ChatResponse, TokenUsage  # noqa: E402
from tests.test_stream_turn import _TR_CONFIG, _tr_agent, _tr_entry  # noqa: E402

_GROUNDED = {"apply_job": {"job_id": ["fetch_jobs"]}}
_GATEWAY_TEXT = '{"balance": 5}'
_PARITY_CONFIG = {
    **_TR_CONFIG,
    "connectors": {
        **_TR_CONFIG["connectors"],
        "read": [*_TR_CONFIG["connectors"]["read"],
                 {"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600}}],
    },
}


def _jobs_entry():
    """A fresh stored fetch_jobs result listing job J-1."""
    from src.tool_results import args_hash
    import time as _time
    return {"tool": "fetch_jobs", "args_hash": args_hash({}),
            "data": {"jobs": [{"job_id": "J-1"}]}, "fetched_at": _time.time(),
            "expires_at": 9e12, "origin": "turn", "scope": "session"}


# (id, tool_name, input, seeded entries?, expect gateway call, expect batch)
_OUTCOMES = [
    ("hit", "get_balance", {"account": "12345"}, True, False, None),
    ("miss_store", "get_balance", {"account": "999"}, False, True, "put"),
    ("write_invalidation", "save_profile", {"name": "A"}, False, True,
     {"invalidate": ["fetch_profile"], "puts": []}),
    ("remember_write", "remember", {"field": "account", "value": "12345"}, True, False, None),
    ("remember_reject", "remember", {"field": "account", "value": "invented"}, True, False, None),
    ("ungrounded_refusal", "apply_job", {"job_id": "J-invented"}, True, False, None),
]


def _sync_run(calls, entries, caps=None):
    """Run one sync tool round of ``calls`` [(tool, params), ...] via run_turn."""
    tcs = [ToolCall(tool_name=t, tool_use_id=f"tu_{i}", input_params=dict(p))
           for i, (t, p) in enumerate(calls, 1)]
    initial = ChatResponse(
        content=[ToolUseBlock(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name,
                              input=tc.input_params) for tc in tcs],
        stop_reason="tool_use", model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )
    agent, _llm, _reg, gateway, _trust = _make_manager(
        llm_responses=[initial, _text_response()],
        tool_result=ToolResult(tool_use_id="tu_1", tool_name=tcs[0].tool_name, result={},
                               success=True, result_text=_GATEWAY_TEXT, projected=True),
    )
    agent._grounded_params = _GROUNDED
    agent._tool_call_caps = dict(caps or {})
    cache = TurnToolCache(ToolResultPolicies.from_config(_PARITY_CONFIG), list(entries), {})
    remember = RememberTool.from_config(_PARITY_CONFIG)
    memory = MagicMock()
    memory.write_strict.return_value = (True, "")
    _text, _calls, results = agent.run_turn(
        list(MESSAGES), SESSION_ID, initial, user_id=USER_ID,
        tool_cache=cache, remember_name=remember.name,
        remember_handler=lambda _tc, _msgs: remember.handle(
            _tc, _msgs, cache.stored_results_by_tool(),
            lambda scope, key, value: memory.write_strict(SESSION_ID, USER_ID, scope, key, value),
            lambda *_a: None,
        ),
    )
    batches = [cache.drain_batch()] if cache.has_pending() else []
    writes = [c.args[2:] for c in memory.write_strict.call_args_list]
    sent = [c.args[0].input_params for c in gateway.execute.call_args_list]
    return [r.result_text for r in results], batches, sent, writes


async def _stream_run(calls, entries, caps=None):
    """Run the same single tool round through the real stream_turn."""
    agent, _order, requests = _tr_agent(
        [[ToolUseBlock(tool_name=t, tool_use_id=f"tu_{i}", input=dict(p))
          for i, (t, p) in enumerate(calls, 1)]],
        entries=list(entries), gateway_text=_GATEWAY_TEXT, remember=True,
    )
    agent._tool_policies = ToolResultPolicies.from_config(_PARITY_CONFIG)
    agent._remember = RememberTool.from_config(_PARITY_CONFIG)
    agent._manager_agent._grounded_params = _GROUNDED
    agent._manager_agent._tool_call_caps = dict(caps or {})
    await _collect_events(agent, _make_turn_input())
    texts = [b.content for b in requests[1].messages[-1].content if b.type == "tool_result"]
    batches = [c.args[2] for c in agent._async_memory.apply_tool_results.await_args_list]
    writes = [c.args[2:] for c in agent._async_memory.write_strict.await_args_list]
    sent = [c.args[0].input_params for c in agent._async_gateway.execute.await_args_list]
    return texts, batches, sent, writes


def _sync_outcome(tool, params, seeded):
    texts, batches, sent, writes = _sync_run([(tool, params)], [_tr_entry()] if seeded else [])
    return texts, batches, len(sent), writes


async def _stream_outcome(tool, params, seeded):
    texts, batches, sent, writes = await _stream_run(
        [(tool, params)], [_tr_entry()] if seeded else [])
    return texts, batches, len(sent), writes


@pytest.mark.parametrize(
    "tool,params,seeded,expect_gateway,expect_batch",
    [o[1:] for o in _OUTCOMES], ids=[o[0] for o in _OUTCOMES],
)
async def test_both_paths_serve_and_record_tool_results_identically(
    tool, params, seeded, expect_gateway, expect_batch,
):
    """Parity: each tool-result outcome yields the same result text and the
    same Memory Layer batches whether the turn ran sync or streaming."""
    s_texts, s_batches, s_calls, s_writes = _sync_outcome(tool, params, seeded)
    a_texts, a_batches, a_calls, a_writes = await _stream_outcome(tool, params, seeded)

    assert s_texts == a_texts
    assert s_batches == a_batches
    assert s_writes == a_writes
    assert bool(s_calls) == bool(a_calls) == expect_gateway

    # Pin the outcome itself, so both paths cannot agree on the wrong thing.
    if expect_batch is None:
        assert a_batches == []
    elif expect_batch == "put":
        assert len(a_batches) == 1 and a_batches[0]["invalidate"] == []
        assert [p["data"] for p in a_batches[0]["puts"]] == [{"balance": 5}]
    else:
        assert a_batches == [expect_batch]
    outcome_text = {
        "hit": lambda t: t.startswith("(stored result"),
        "miss_store": lambda t: t == _GATEWAY_TEXT,
        "write_invalidation": lambda t: t == _GATEWAY_TEXT,
        "remember_write": lambda t: t == "Saved account.",
        "remember_reject": lambda t: "must be copied exactly" in t,
        "ungrounded_refusal": lambda t: t.startswith("Refused: job_id"),
    }
    case = next(o[0] for o in _OUTCOMES if o[1] == tool and o[2] == params)
    assert outcome_text[case](a_texts[0]), a_texts
    if case == "remember_write":
        assert a_writes == [("session", "account", "12345")]


# ── per-turn caps: counted only for live gateway calls, never for remember ──

_CAPPED = [
    # (id, calls, caps, expected result-text checks, expected gateway inputs)
    ("cached_tool_hit_twice",
     [("get_balance", {"account": "12345"}), ("get_balance", {"account": "12345"})],
     {"get_balance": 1},
     [lambda t: t.startswith("(stored result"), lambda t: t.startswith("(stored result")],
     []),
    ("refused_write_retried_with_valid_id",
     [("apply_job", {"job_id": "J-invented"}), ("apply_job", {"job_id": "J-1"})],
     {"apply_job": 1},
     [lambda t: t.startswith("Refused: job_id"), lambda t: t == _GATEWAY_TEXT],
     [{"job_id": "J-1"}]),
    ("remember_with_cap_configured",
     [("remember", {"field": "account", "value": "12345"}),
      ("remember", {"field": "account", "value": "12345"})],
     {"remember": 1},
     [lambda t: t == "Saved account.", lambda t: t == "Saved account."],
     []),
]


@pytest.mark.parametrize("calls,caps,checks,expect_sent",
                         [c[1:] for c in _CAPPED], ids=[c[0] for c in _CAPPED])
async def test_both_paths_apply_per_turn_caps_identically(calls, caps, checks, expect_sent):
    """Parity: a cap counts live Action Gateway calls only, on both paths.

    Stored-result hits and grounding refusals do not use up a capped tool's
    budget, and ``remember`` is never capped even when a cap names it.
    """
    entries = [_tr_entry(), _jobs_entry()]
    s_texts, s_batches, s_sent, s_writes = _sync_run(calls, entries, caps)
    a_texts, a_batches, a_sent, a_writes = await _stream_run(calls, entries, caps)

    assert s_texts == a_texts
    assert s_batches == a_batches
    assert s_sent == a_sent == expect_sent
    assert s_writes == a_writes
    assert len(a_texts) == len(checks)
    for check, text in zip(checks, a_texts):
        assert check(text), a_texts
    assert not any("has already run this turn" in t for t in a_texts)


# ── session bootstrap: early routing on both paths (spec §5.5, §8) ──────────
#
# A returning caller already accepted terms/privacy and has an age on file.
# The bootstrap's fetch_profile lifts those as session_mapping values; the
# opening subagent's rule 1b (all three True → profile_resolve) then fires on
# the first turn that routes. That is turn 1 on the stream path (the canned
# opening phrase is suppressed there, GH-239) and turn 2 on the sync path,
# whose turn 1 is the canned opening phrase — the bootstrap still runs before
# that gate. A new caller (flags false/absent) or a failed bootstrap leaves
# routing as it is today: opening (the consent flow).

import json as _json  # noqa: E402

from src.models import ContextBundle  # noqa: E402
from src.session_bootstrap import LATCH, SessionBootstrap  # noqa: E402
from src.workflow_loader import AgentWorkflow, RoutingCondition, RoutingRule, SubAgent  # noqa: E402
from tests.test_orchestrator import _make_agent, _turn_input  # noqa: E402

_BOOT_CONFIG = {
    "connectors": {"read": [{"name": "fetch_profile", "cache": {"scope": "session", "ttl_seconds": 1800}}]},
    "session_bootstrap": {"timeout_ms": 1500, "steps": [{"type": "tool", "tool": "fetch_profile"}]},
}
_FLAGS = {"user_terms": True, "user_privacy": True, "has_age": True}
_OPENING_PHRASE = "Welcome to Blue Dots. Shall we begin?"
_NEW_CALLER = {"has_age": False}            # user_terms / user_privacy absent
_GREETING = NLUResult(intent="greeting", entities={}, sentiment="neutral", confidence=0.9)


def _sub(sid, routing=(), is_start=False, opening_phrase=""):
    return SubAgent(id=sid, name=sid, description=sid, is_start=is_start, is_terminal=False,
                    special_handler=None, valid_intents=["greeting"], tools=[],
                    system_prompt=f"{sid} prompt", output_format=None, routing=list(routing),
                    opening_phrase=opening_phrase)


def _opening_workflow():
    rule_1b = RoutingRule(intent="*", next_subagent_id="profile_resolve", conditions=[
        RoutingCondition(field=f, operator="eq", value=True) for f in _FLAGS])
    subs = {"opening": _sub("opening", [rule_1b], is_start=True, opening_phrase=_OPENING_PHRASE),
            "profile_resolve": _sub("profile_resolve")}
    wf = MagicMock(spec=AgentWorkflow)
    wf.start_subagent_id = "opening"
    wf.subagents = subs
    wf.nlu_intent_set = {k: ["greeting"] for k in subs}
    wf.tool_defs = {k: [] for k in subs}
    wf.global_routing = []
    wf.default_fallback_subagent_id = "opening"
    wf.agent_system_prompt = "System prompt"
    return wf


def _bootstrap_result(session_values: dict | None) -> ToolResult:
    """success with ``session_values``; ``None`` means the call failed."""
    ok = session_values is not None
    return ToolResult(tool_use_id="bootstrap-0", tool_name="fetch_profile", result={}, success=ok,
                      result_text=_json.dumps({"items": [{"item_id": "p1"}]}), projected=True,
                      session_values=dict(session_values or {}),
                      error=None if ok else "upstream 500")


def _routed_to(write_calls) -> list:
    return [c.args[4] for c in write_calls if c.args[3] == "current_subagent_id"]


def _sync_bootstrap_route(session_values: dict | None) -> list:
    """Sync turn 1 (canned phrase) then turn 2; returns turn-2 routing writes."""
    agent = _make_agent(session_data={}, workflow=_opening_workflow(), nlu_result=_GREETING)
    agent._tool_policies = ToolResultPolicies.from_config(_BOOT_CONFIG)
    agent._bootstrap = SessionBootstrap.from_config(_BOOT_CONFIG, agent._tool_policies)
    gw = agent._manager_agent._gateway = MagicMock()
    gw.execute.return_value = _bootstrap_result(session_values)

    turn1 = agent.process_turn(_turn_input())
    assert turn1.response_text == _OPENING_PHRASE
    gw.execute.assert_called_once()                      # bootstrap ran before the gate
    agent._manager_agent.run_turn.assert_not_called()

    # Turn 2 reads back what turn 1 persisted: latch, lifted values, phrase latch.
    persisted = {c.args[3]: c.args[4] for c in agent._memory.write.call_args_list}
    assert persisted[LATCH] is True
    agent._memory.write.reset_mock()
    agent._memory.context_bundle.return_value = ContextBundle(
        session=dict(persisted), profile={}, journey=None)
    agent.process_turn(_turn_input())
    gw.execute.assert_called_once()                      # no second bootstrap
    return _routed_to(agent._memory.write.call_args_list)


async def _stream_bootstrap_route(session_values: dict | None) -> list:
    """Stream turn 1; returns its routing writes."""
    agent = _make_agent_core(workflow=_opening_workflow())
    agent._async_memory.context_bundle.return_value = ContextBundle(session={}, profile={})
    agent._tool_policies = ToolResultPolicies.from_config(_BOOT_CONFIG)
    agent._bootstrap = SessionBootstrap.from_config(_BOOT_CONFIG, agent._tool_policies)
    agent._async_gateway.execute = AsyncMock(return_value=_bootstrap_result(session_values))

    async def mock_stream(*args, **kwargs):
        yield "Welcome back. "

    agent._llm.stream = mock_stream
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = _GREETING
    await _collect_events(agent, _make_turn_input())
    assert agent._async_gateway.execute.await_count == 1
    return _routed_to(agent._async_memory.write.await_args_list)


async def test_returning_caller_routes_past_opening_stream_turn1_sync_turn2():
    """Returning caller: stream turn 1 and sync turn 2 (after the canned phrase)
    both route to profile_resolve on the bootstrap-lifted flags."""
    assert await _stream_bootstrap_route(_FLAGS) == ["profile_resolve"]
    assert _sync_bootstrap_route(_FLAGS) == ["profile_resolve"]


async def test_new_caller_stays_in_opening_on_both_paths():
    """New caller: bootstrap succeeds but the flags are false/absent → opening."""
    assert await _stream_bootstrap_route(_NEW_CALLER) == ["opening"]
    assert _sync_bootstrap_route(_NEW_CALLER) == ["opening"]


async def test_bootstrap_failure_leaves_routing_unchanged_on_both_paths():
    """A failed bootstrap (success=False) writes no flags; routing stays in opening."""
    assert await _stream_bootstrap_route(None) == ["opening"]
    assert _sync_bootstrap_route(None) == ["opening"]


# ── dialogue_act mode: both paths apply one understanding identically ───────

from src.understanding.history import RECENT_TURNS_KEY  # noqa: E402
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding  # noqa: E402

_DA_U = TurnUnderstanding(
    nlu_result=NLUResult(intent="any_input", entities={"age": 25}, sentiment="neutral", confidence=1.0),
    dialogue=DialogueActResult(acts=("provide_info",), relation="answers_pending"),
    pending_id="age", writes=[StateWrite("session", "age", 25), StateWrite("session", "slot_provenance", ["age"])])


def _da(agent):
    """Switch an agent into dialogue_act mode with a canned understanding."""
    agent._understander = MagicMock()
    agent._understander.understand.return_value = _DA_U
    agent._dialogue_cfg = MagicMock(history_turns=2, signal_types={})
    agent._nlu_processor = MagicMock()
    return agent


async def test_both_paths_apply_identical_understanding():
    """Parity: dialogue_act mode writes, frames and prompts the same on both paths,
    and both send the raw caller text to NLU (legacy NLU is never called)."""
    # opening_phrase_emitted: past the sync canned-greeting gate, so turn 1 reaches NLU.
    sync_agent = _da(_make_agent(session_data={"current_subagent_id": "market_truth",
                                               "opening_phrase_emitted": True}))
    sync_agent.process_turn(_turn_input("Hello"))
    sync_writes = {(c.args[2], c.args[3]): c.args[4] for c in sync_agent._memory.write.call_args_list}
    sync_ctx = sync_agent._understander.understand.call_args.args[0]
    sync_prompt = sync_agent._manager_agent.build_system_prompt.call_args.kwargs["caller_turn"]

    stream_agent = _da(_make_agent_core())

    async def mock_stream(*args, **kwargs):
        yield "ok. "

    stream_agent._llm.stream = mock_stream
    await _collect_events(stream_agent, _make_turn_input(user_message="Hello"))
    # The recent-turns write is fire-and-forget (create_task), so read call_args_list.
    stream_writes = {(c.args[2], c.args[3]): c.args[4] for c in stream_agent._async_memory.write.call_args_list}
    stream_ctx = stream_agent._understander.understand.call_args.args[0]
    stream_prompt = stream_agent._manager_agent.build_system_prompt.call_args.kwargs["caller_turn"]

    for key in (("session", "age"), ("session", "slot_provenance")):
        assert sync_writes[key] == stream_writes[key]
    assert ("session", RECENT_TURNS_KEY) in sync_writes and ("session", RECENT_TURNS_KEY) in stream_writes
    assert sync_ctx.segments == stream_ctx.segments == ["Hello"]   # raw caller text on both paths
    assert sync_ctx.tool_cache is not None and stream_ctx.tool_cache is not None
    assert sync_prompt == stream_prompt != ""
    sync_agent._nlu_processor.process.assert_not_called()
    stream_agent._nlu_processor.process.assert_not_called()


# ── F3: the last-served tool entry is persisted at end of turn on both paths ──

_SERVED_KEY = "served_tool_results"


def _served_writes(calls):
    return [c.args[4] for c in calls if c.args[2] == "session" and c.args[3] == _SERVED_KEY]


@pytest.mark.asyncio
async def test_served_map_persisted_on_both_paths(monkeypatch):
    """Existing map merged with this turn's served entries, read into TurnContext, written once."""
    monkeypatch.setattr(TurnToolCache, "served", lambda self: {"fetch_jobs": "hb"})
    prior = {"fetch_profile": "hp", "fetch_jobs": "ha"}
    expected = {"fetch_profile": "hp", "fetch_jobs": "hb"}

    sync_agent = _da(_make_agent(session_data={"current_subagent_id": "market_truth",
                                               "opening_phrase_emitted": True, _SERVED_KEY: dict(prior)}))
    sync_agent.process_turn(_turn_input("Hello"))
    assert sync_agent._understander.understand.call_args.args[0].served == prior
    assert _served_writes(sync_agent._memory.write.call_args_list) == [expected]

    stream_agent = _da(_make_agent_core())
    stream_agent._async_memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "start", _SERVED_KEY: dict(prior)}, profile={})

    async def mock_stream(*args, **kwargs):
        yield "ok. "

    stream_agent._llm.stream = mock_stream
    await _collect_events(stream_agent, _make_turn_input(user_message="Hello"))
    assert stream_agent._understander.understand.call_args.args[0].served == prior
    assert _served_writes(stream_agent._async_memory.write.call_args_list) == [expected]


@pytest.mark.asyncio
async def test_served_map_not_written_when_unchanged_or_intent_mode(monkeypatch):
    monkeypatch.setattr(TurnToolCache, "served", lambda self: {"fetch_jobs": "ha"})
    prior = {"fetch_jobs": "ha"}
    sync_agent = _da(_make_agent(session_data={"current_subagent_id": "market_truth",
                                               "opening_phrase_emitted": True, _SERVED_KEY: dict(prior)}))
    sync_agent.process_turn(_turn_input("Hello"))
    assert _served_writes(sync_agent._memory.write.call_args_list) == []

    stream_agent = _da(_make_agent_core())
    stream_agent._async_memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "start", _SERVED_KEY: "corrupt"}, profile={})

    async def mock_stream(*args, **kwargs):
        yield "ok. "

    stream_agent._llm.stream = mock_stream
    await _collect_events(stream_agent, _make_turn_input(user_message="Hello"))
    assert stream_agent._understander.understand.call_args.args[0].served == {}   # dict-guarded
    assert _served_writes(stream_agent._async_memory.write.call_args_list) == [prior]

    intent_agent = _make_agent(session_data={"current_subagent_id": "market_truth",
                                             "opening_phrase_emitted": True})
    intent_agent.process_turn(_turn_input("Hello"))
    assert _served_writes(intent_agent._memory.write.call_args_list) == []


def test_sync_nlu_log_prints_entity_keys_not_values(caplog):
    """M1: the sync [STEP 5] ✓ line logs entity keys only (no PII), like the stream path."""
    import logging
    agent = _make_agent(session_data={"current_subagent_id": "market_truth", "opening_phrase_emitted": True},
                        nlu_result=NLUResult(intent="any_input", entities={"name": "Ramesh Kumar"},
                                             sentiment="neutral", confidence=0.9))
    with caplog.at_level(logging.INFO, logger="src.orchestrator"):
        agent.process_turn(_turn_input("मेरा नाम Ramesh Kumar है"))
    lines = [r.getMessage() for r in caplog.records if "[STEP 5] NLU Processor  ✓" in r.getMessage()]
    assert lines and "name" in lines[0]
    assert "Ramesh" not in lines[0]
