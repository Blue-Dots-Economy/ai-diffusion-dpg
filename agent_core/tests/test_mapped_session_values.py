"""Values a connector lifted from a response reach session state.

Routing reads session ∪ profile. A tool response otherwise reaches only the
LLM, so without this a workflow cannot branch on anything a tool returned —
the gap that produced consent_response, profile_setup_done and every flag on
the participant fetch as three separate bugs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models import ToolResult
from src.orchestrator import AgentCore


class _Orch:
    """Minimal stand-in exposing only the method under test."""

    _write_mapped_session_values = AgentCore._write_mapped_session_values

    def __init__(self):
        self._async_memory = SimpleNamespace(write=AsyncMock())


def _bundle():
    return SimpleNamespace(session={}, profile={})


def _result(values):
    return ToolResult(tool_use_id="1", tool_name="fetch_profile", result={},
                      success=True, session_values=values)


@pytest.mark.asyncio
async def test_values_are_written_at_session_scope():
    orch, bundle = _Orch(), _bundle()

    await orch._write_mapped_session_values(
        "sess-1", "user-1", _result({"has_age": True, "user_terms": True}), bundle)

    orch._async_memory.write.assert_any_await(
        "sess-1", "user-1", "session", "has_age", True)
    assert bundle.session == {"has_age": True, "user_terms": True}


@pytest.mark.asyncio
async def test_false_is_written_not_skipped():
    """A false consent flag is the whole point — it must not be dropped."""
    orch, bundle = _Orch(), _bundle()

    await orch._write_mapped_session_values(
        "s", "u", _result({"user_terms": False}), bundle)

    assert bundle.session["user_terms"] is False
    orch._async_memory.write.assert_any_await("s", "u", "session", "user_terms", False)


@pytest.mark.asyncio
async def test_a_result_without_mapped_values_writes_nothing():
    orch, bundle = _Orch(), _bundle()

    await orch._write_mapped_session_values("s", "u", _result({}), bundle)

    orch._async_memory.write.assert_not_awaited()
    assert bundle.session == {}


@pytest.mark.asyncio
async def test_a_tool_result_lacking_the_field_is_tolerated():
    """Older gateways, and internal tools, return results without it."""
    orch, bundle = _Orch(), _bundle()
    legacy = SimpleNamespace(tool_name="x")

    await orch._write_mapped_session_values("s", "u", legacy, bundle)

    orch._async_memory.write.assert_not_awaited()
