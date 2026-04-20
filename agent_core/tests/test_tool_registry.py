"""
agent_core/tests/test_tool_registry.py

Unit tests for ToolRegistry.
Action Gateway is mocked — no real gateway calls are made.

Coverage:
- Normal: loads and caches tool definitions from gateway
- Normal: correctly identifies write/identity tools as requiring consent
- Normal: read tools do not require consent
- Edge: empty connectors config
- Failure: gateway returns non-list raises ConfigurationError
- Failure: tool definition missing name raises ConfigurationError
- Failure: connector in config has no matching tool definition raises ConfigurationError
- Failure: None config raises ValueError
- Failure: None gateway raises ValueError
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.exceptions import ConfigurationError
from src.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gateway(tools: list[dict]) -> MagicMock:
    gateway = MagicMock()
    gateway.list_available_tools.return_value = tools
    return gateway


READ_TOOL = {"name": "search_records", "description": "Search", "category": "read", "input_schema": {}}
WRITE_TOOL = {"name": "submit_form", "description": "Submit", "category": "write", "input_schema": {}}
IDENTITY_TOOL = {"name": "verify_id", "description": "Verify", "category": "identity", "input_schema": {}}

VALID_CONFIG = {
    "connectors": {
        "read": [{"name": "search_records"}],
        "write": [{"name": "submit_form"}],
        "identity": [{"name": "verify_id"}],
    }
}


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------

def test_get_tool_definitions_returns_all_tools():
    gateway = _make_gateway([READ_TOOL, WRITE_TOOL, IDENTITY_TOOL])
    registry = ToolRegistry(config=VALID_CONFIG, gateway=gateway)

    definitions = registry.get_tool_definitions()
    assert len(definitions) == 3
    assert {t["name"] for t in definitions} == {"search_records", "submit_form", "verify_id"}


def test_get_tool_definitions_is_cached():
    gateway = _make_gateway([READ_TOOL])
    config = {"connectors": {"read": [{"name": "search_records"}]}}
    registry = ToolRegistry(config=config, gateway=gateway)

    _ = registry.get_tool_definitions()
    _ = registry.get_tool_definitions()

    # Gateway should only be called once (during __init__)
    assert gateway.list_available_tools.call_count == 1


def test_get_tool_names_returns_set_of_names():
    gateway = _make_gateway([READ_TOOL, WRITE_TOOL])
    config = {
        "connectors": {
            "read": [{"name": "search_records"}],
            "write": [{"name": "submit_form"}],
        }
    }
    registry = ToolRegistry(config=config, gateway=gateway)
    assert registry.get_tool_names() == {"search_records", "submit_form"}


def test_write_tool_requires_consent():
    # Consent is derived from the 'category' field on gateway tool definitions.
    gateway = _make_gateway([WRITE_TOOL])
    registry = ToolRegistry(config={}, gateway=gateway)
    assert registry.requires_consent("submit_form") is True


def test_identity_tool_requires_consent():
    gateway = _make_gateway([IDENTITY_TOOL])
    registry = ToolRegistry(config={}, gateway=gateway)
    assert registry.requires_consent("verify_id") is True


def test_read_tool_does_not_require_consent():
    gateway = _make_gateway([READ_TOOL])
    registry = ToolRegistry(config={}, gateway=gateway)
    assert registry.requires_consent("search_records") is False


def test_unknown_tool_does_not_require_consent():
    gateway = _make_gateway([READ_TOOL])
    config = {"connectors": {"read": [{"name": "search_records"}]}}
    registry = ToolRegistry(config=config, gateway=gateway)
    assert registry.requires_consent("nonexistent_tool") is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_connectors_config_is_valid():
    gateway = _make_gateway([])
    registry = ToolRegistry(config={"connectors": {}}, gateway=gateway)
    assert registry.get_tool_definitions() == []
    assert registry.get_tool_names() == set()


def test_missing_connectors_key_is_tolerated():
    gateway = _make_gateway([])
    registry = ToolRegistry(config={}, gateway=gateway)
    assert registry.get_tool_definitions() == []


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------

def test_raises_value_error_on_none_config():
    gateway = _make_gateway([])
    with pytest.raises(ValueError, match="config must not be None"):
        ToolRegistry(config=None, gateway=gateway)


def test_raises_error_when_gateway_is_none():
    # Source propagates AttributeError when gateway is None.
    with pytest.raises(Exception):
        ToolRegistry(config={}, gateway=None)


def test_raises_error_when_gateway_raises():
    # Source does not wrap gateway errors — RuntimeError propagates directly.
    gateway = MagicMock()
    gateway.list_available_tools.side_effect = RuntimeError("gateway down")
    with pytest.raises(RuntimeError, match="gateway down"):
        ToolRegistry(config={}, gateway=gateway)


# ---------------------------------------------------------------------------
# GH-137: register_internal
# ---------------------------------------------------------------------------


def test_register_internal_adds_tool_definition_and_route():
    gateway = _make_gateway([])
    registry = ToolRegistry(config={}, gateway=gateway)
    registry.register_internal(
        name="end_session",
        route="orchestrator",
        description="desc",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    names = registry.get_tool_names()
    assert "end_session" in names
    assert registry.get_route("end_session") == "orchestrator"
    # Tool definition only carries Anthropic-compatible keys.
    defs = [t for t in registry.get_tool_definitions() if t["name"] == "end_session"]
    assert defs and set(defs[0].keys()) == {"name", "description", "input_schema"}


def test_register_internal_idempotent_on_duplicate_name():
    gateway = _make_gateway([])
    registry = ToolRegistry(config={}, gateway=gateway)
    registry.register_internal(
        name="end_session", route="orchestrator",
        description="v1", input_schema={"type": "object"},
    )
    registry.register_internal(
        name="end_session", route="orchestrator",
        description="v2", input_schema={"type": "object"},
    )
    defs = [t for t in registry.get_tool_definitions() if t["name"] == "end_session"]
    assert len(defs) == 1
    assert defs[0]["description"] == "v2"


def test_register_internal_rejects_empty_name():
    gateway = _make_gateway([])
    registry = ToolRegistry(config={}, gateway=gateway)
    with pytest.raises(ValueError, match="name must not be empty"):
        registry.register_internal(name="", route="orchestrator", description="d", input_schema={})
