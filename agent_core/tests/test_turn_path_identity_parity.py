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
