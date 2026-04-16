"""Tests for the new tools-phase tool handlers."""
import json
import pytest
from unittest.mock import patch, MagicMock

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler


@pytest.fixture()
def acc():
    return ConfigAccumulator()


@pytest.fixture()
def state():
    return {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}


@pytest.fixture()
def handler(acc, state):
    return ToolHandler(acc, state)


# ---- add_rest_api_tool ----

def test_add_rest_api_tool_adds_to_accumulator(handler, acc):
    """add_rest_api_tool should append a tool to action_gateway.tools."""
    result = handler.dispatch("add_rest_api_tool", {
        "id": "onest_search",
        "category": "read",
        "description": "Search jobs",
        "base_url": "https://api.example.com",
        "auth_type": "api_key",
        "auth_header": "X-API-KEY",
        "auth_secret_env": "ONEST_KEY",
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/search",
                "params": [
                    {"name": "query", "source": "agent", "type": "string", "required": True, "description": "Search query"}
                ],
            }
        ],
    })
    assert "onest_search" in result
    ag = acc.get_block("action_gateway")
    assert len(ag["tools"]) == 1
    assert ag["tools"][0]["id"] == "onest_search"


def test_add_rest_api_tool_rejects_duplicate(handler, acc):
    """Adding a tool with a duplicate ID returns an error string."""
    params = {
        "id": "dup_tool",
        "category": "read",
        "description": "x",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [],
    }
    handler.dispatch("add_rest_api_tool", params)
    result = handler.dispatch("add_rest_api_tool", params)
    assert "ERROR" in result or "already exists" in result.lower()


def test_add_rest_api_tool_syncs_agent_core_connector(handler, acc):
    """Adding a REST API tool auto-creates a corresponding agent_core connector."""
    handler.dispatch("add_rest_api_tool", {
        "id": "market_lookup",
        "category": "read",
        "description": "Find job listings",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [
            {
                "name": "search",
                "method": "GET",
                "path": "/jobs",
                "params": [{"name": "location", "source": "agent", "type": "string", "required": True, "description": "City or region"}],
            }
        ],
    })
    ac = acc.get_block("agent_core")
    read_connectors = ac.get("connectors", {}).get("read", [])
    assert any(c["name"] == "market_lookup" for c in read_connectors)


def test_add_mcp_tool_adds_to_accumulator(handler, acc):
    """add_mcp_tool should append an MCP tool to action_gateway.tools."""
    result = handler.dispatch("add_mcp_tool", {
        "id": "obsrv_query",
        "category": "read",
        "description": "Query Obsrv data",
        "mcp_server_url": "https://mcp.example.com",
        "tool_name": "query_dataset",
        "input_schema": {"type": "object", "properties": {"dataset": {"type": "string"}}},
    })
    assert "obsrv_query" in result
    ag = acc.get_block("action_gateway")
    mcp_tools = [t for t in ag["tools"] if t["type"] == "mcp"]
    assert len(mcp_tools) == 1


def test_parse_openapi_spec_returns_candidates(handler):
    """parse_openapi_spec should return a JSON list of candidate tool descriptions."""
    spec_json = json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/search": {
                "post": {
                    "summary": "Search",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"q": {"type": "string"}},
                                }
                            }
                        }
                    },
                }
            }
        },
    })
    result = handler.dispatch("parse_openapi_spec", {"spec_json": spec_json})
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["path"] == "/search"


def test_parse_openapi_spec_invalid_json_returns_error(handler):
    """Invalid JSON/YAML in spec_json returns an error string."""
    result = handler.dispatch("parse_openapi_spec", {"spec_json": "not json {{"})
    assert "ERROR" in result or "error" in result.lower()


def test_static_params_excluded_from_connector_schema(handler, acc):
    """Static params should not appear in the agent_core connector input_schema."""
    handler.dispatch("add_rest_api_tool", {
        "id": "search",
        "category": "read",
        "description": "Search",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/search",
                "params": [
                    {"name": "query", "source": "agent", "type": "string", "required": True, "description": "query"},
                    {"name": "limit", "source": "static", "type": "integer", "value": 10},
                ],
            }
        ],
    })
    ac = acc.get_block("agent_core")
    connector = ac["connectors"]["read"][0]
    props = connector["input_schema"]["properties"]
    assert "query" in props
    assert "limit" not in props


def test_write_tool_creates_write_connector(handler, acc):
    """A write-category tool creates a connector under agent_core.connectors.write."""
    handler.dispatch("add_rest_api_tool", {
        "id": "apply_job",
        "category": "write",
        "description": "Submit job application",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [{"name": "apply", "method": "POST", "path": "/apply"}],
    })
    ac = acc.get_block("agent_core")
    write_connectors = ac.get("connectors", {}).get("write", [])
    assert any(c["name"] == "apply_job" for c in write_connectors)


# ---- set_reach_channels ----

def test_set_reach_channels_stores_selection(handler, acc):
    """set_reach_channels should store selected channels in reach_layer config."""
    result = handler.dispatch("set_reach_channels", {"channels": ["web", "cli"]})
    assert "web" in result or "cli" in result
    rl = acc.get_block("reach_layer")
    assert rl.get("_selected_channels") == ["web", "cli"]


def test_set_reach_channels_rejects_unknown(handler):
    """set_reach_channels should reject unknown channel names."""
    result = handler.dispatch("set_reach_channels", {"channels": ["fax", "web"]})
    assert "ERROR" in result


def test_set_reach_channels_requires_at_least_one(handler):
    """set_reach_channels rejects empty list."""
    result = handler.dispatch("set_reach_channels", {"channels": []})
    assert "ERROR" in result
