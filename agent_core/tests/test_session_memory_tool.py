"""Tests for the ``session_memory`` internal tool route (Agent Core).

The route lets the LLM report a fact it just heard straight into session
state. It is the replacement for NLU entity extraction on the two fields the
opening gate routes on, for domains that run with
``preprocessing.nlu_processor.enabled: false``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Route declaration
# ---------------------------------------------------------------------------


def _registry_with_session_tool() -> ToolRegistry:
    gateway = MagicMock()
    gateway.list_available_tools.return_value = []
    return ToolRegistry(config={
        "connectors": {
            "internal": [{
                "name": "record_opening_facts",
                "route": "session_memory",
                "description": "Record consent and age.",
                "input_schema": {"type": "object", "properties": {}},
            }],
        },
    }, gateway=gateway)


def test_session_memory_route_is_registered():
    assert _registry_with_session_tool().get_route("record_opening_facts") == "session_memory"


def test_unrouted_tool_has_no_route():
    assert _registry_with_session_tool().get_route("save_profile") is None


# ---------------------------------------------------------------------------
# _write_session_facts
# ---------------------------------------------------------------------------


class _Orch:
    """Minimal stand-in exposing only the helper under test."""

    from src.orchestrator import AgentCore
    _write_session_facts = AgentCore._write_session_facts

    def __init__(self):
        self._async_memory = SimpleNamespace(write=AsyncMock())


def _bundle():
    return SimpleNamespace(session={}, profile={})


@pytest.mark.asyncio
async def test_writes_reported_facts_to_session_scope():
    orch, bundle = _Orch(), _bundle()

    written = await orch._write_session_facts(
        "sess-1", "user-1", {"consent_response": "granted", "age": 24}, bundle,
    )

    assert sorted(written) == ["age", "consent_response"]
    assert bundle.session == {"consent_response": "granted", "age": 24}
    orch._async_memory.write.assert_any_await("sess-1", "user-1", "session", "age", 24)


@pytest.mark.asyncio
async def test_omitted_fields_never_clobber_what_is_on_record():
    """The model sends only what it heard; a null must not erase a known age."""
    orch, bundle = _Orch(), _bundle()
    bundle.session["age"] = 24

    written = await orch._write_session_facts(
        "sess-1", "user-1", {"consent_response": "granted", "age": None}, bundle,
    )

    assert written == ["consent_response"]
    assert bundle.session["age"] == 24


@pytest.mark.asyncio
async def test_empty_payload_writes_nothing():
    orch, bundle = _Orch(), _bundle()

    assert await orch._write_session_facts("sess-1", "user-1", {}, bundle) == []
    orch._async_memory.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_falsy_but_meaningful_values_are_dropped_not_written():
    """Age 0 is the sentinel that previously reached the upstream and tripped
    U18_NOT_ALLOWED. Empty-ish values are skipped rather than persisted."""
    orch, bundle = _Orch(), _bundle()

    assert await orch._write_session_facts("s", "u", {"age": "", "trade": []}, bundle) == []
    assert bundle.session == {}
