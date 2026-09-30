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
from tests.test_stream_turn import _TR_CONFIG, _tr_agent, _tr_entry  # noqa: E402

_GROUNDED = {"apply_job": {"job_id": ["fetch_jobs"]}}
_GATEWAY_TEXT = '{"balance": 5}'

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


def _sync_outcome(tool, params, seeded):
    tc = ToolCall(tool_name=tool, tool_use_id="tu_1", input_params=dict(params))
    initial = _tool_response(tc)
    agent, _llm, _reg, gateway, _trust = _make_manager(
        llm_responses=[initial, _text_response()],
        tool_result=ToolResult(tool_use_id="tu_1", tool_name=tool, result={}, success=True,
                               result_text=_GATEWAY_TEXT, projected=True),
    )
    agent._grounded_params = _GROUNDED
    cache = TurnToolCache(ToolResultPolicies.from_config(_TR_CONFIG),
                          [_tr_entry()] if seeded else [], {})
    remember = RememberTool.from_config(_TR_CONFIG)
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
    return [r.result_text for r in results], batches, gateway.execute.call_count, writes


async def _stream_outcome(tool, params, seeded):
    agent, _order, requests = _tr_agent(
        [[ToolUseBlock(tool_name=tool, tool_use_id="tu_1", input=dict(params))]],
        entries=[_tr_entry()] if seeded else [], gateway_text=_GATEWAY_TEXT, remember=True,
    )
    agent._manager_agent._grounded_params = _GROUNDED
    agent._manager_agent._tool_call_caps = {}
    await _collect_events(agent, _make_turn_input())
    texts = [b.content for b in requests[1].messages[-1].content if b.type == "tool_result"]
    batches = [c.args[2] for c in agent._async_memory.apply_tool_results.await_args_list]
    writes = [c.args[2:] for c in agent._async_memory.write_strict.await_args_list]
    return texts, batches, agent._async_gateway.execute.await_count, writes


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
