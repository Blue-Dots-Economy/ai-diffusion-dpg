"""Tests for the new tools-phase tool handlers."""
import json
import pytest
from unittest.mock import patch, MagicMock

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler, _parse_sse_json


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
    """add_mcp_tool should register an MCP server entry in action_gateway.tools."""
    result = handler.dispatch("add_mcp_tool", {
        "id": "obsrv_query",
        "category": "read",
        "description": "Query Obsrv data",
        "mcp_server_url": "https://mcp.example.com",
        "transport": "streamable_http",
    })
    assert "obsrv_query" in result
    ag = acc.get_block("action_gateway")
    mcp_tools = [t for t in ag["tools"] if t["type"] == "mcp"]
    assert len(mcp_tools) == 1
    assert mcp_tools[0]["transport"] == "streamable_http"
    # MCP adapters must NOT create agent_core connector entries
    connectors = acc.get_block("agent_core").get("connectors", {}).get("read", [])
    assert len(connectors) == 0


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


# ---- _parse_sse_json ----

def test_parse_sse_json_extracts_data_line():
    """_parse_sse_json returns the JSON payload from the first data: line."""
    sse = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"result\":{\"tools\":[]},\"id\":1}\n\n"
    result = _parse_sse_json(sse)
    assert result == {"jsonrpc": "2.0", "result": {"tools": []}, "id": 1}


def test_parse_sse_json_ignores_non_data_lines():
    """_parse_sse_json skips event: and comment lines."""
    sse = ": comment\nevent: message\ndata: {\"ok\":true}\n"
    result = _parse_sse_json(sse)
    assert result == {"ok": True}


def test_parse_sse_json_returns_none_for_no_data_line():
    """_parse_sse_json returns None when no data: line is found."""
    assert _parse_sse_json("event: message\n") is None
    assert _parse_sse_json("") is None


def test_parse_sse_json_returns_none_for_invalid_json():
    """_parse_sse_json returns None when the data: payload is not valid JSON."""
    assert _parse_sse_json("data: not-json\n") is None


# ---- discover_mcp_tools (transport auto-detection) ----

def _make_response(text: str, content_type: str = "application/json"):
    """Build a minimal mock httpx.Response."""
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()

    def _json():
        import json as _json_mod
        return _json_mod.loads(text)

    resp.json = _json
    return resp


def _mcp_tools_payload():
    return {
        "jsonrpc": "2.0",
        "result": {
            "tools": [
                {
                    "name": "searchDocumentation",
                    "description": "Search docs",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
                {
                    "name": "getPage",
                    "description": "Get a page by URL",
                    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
                },
            ]
        },
        "id": 1,
    }


@patch("httpx.post")
def test_discover_mcp_tools_plain_json(mock_post, handler):
    """discover_mcp_tools works with a plain JSON-RPC response."""
    payload = _mcp_tools_payload()
    mock_post.return_value = _make_response(json.dumps(payload))

    result = handler.dispatch("discover_mcp_tools", {"mcp_server_url": "https://mcp.example.com"})
    tools = json.loads(result)
    assert len(tools) == 2
    assert tools[0]["name"] == "searchDocumentation"
    assert tools[1]["name"] == "getPage"


@patch("httpx.post")
def test_discover_mcp_tools_sse_transport(mock_post, handler):
    """discover_mcp_tools falls back to SSE parsing when response.json() fails."""
    payload = _mcp_tools_payload()
    sse_body = f"event: message\ndata: {json.dumps(payload)}\n\n"

    resp = MagicMock()
    resp.text = sse_body
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("not json")  # simulate SSE content-type failure
    mock_post.return_value = resp

    result = handler.dispatch("discover_mcp_tools", {"mcp_server_url": "https://mcp.example.com"})
    tools = json.loads(result)
    assert len(tools) == 2
    assert tools[0]["name"] == "searchDocumentation"


@patch("httpx.post")
def test_discover_mcp_tools_sends_accept_header(mock_post, handler):
    """discover_mcp_tools sends Accept: application/json, text/event-stream."""
    payload = _mcp_tools_payload()
    mock_post.return_value = _make_response(json.dumps(payload))

    handler.dispatch("discover_mcp_tools", {"mcp_server_url": "https://mcp.example.com"})

    _, kwargs = mock_post.call_args
    headers = kwargs.get("headers", {})
    assert "text/event-stream" in headers.get("Accept", "")


@patch("httpx.post")
def test_discover_mcp_tools_http_error_returns_error_string(mock_post, handler):
    """discover_mcp_tools returns an ERROR string on HTTP failure."""
    import httpx
    mock_post.side_effect = httpx.HTTPError("connection refused")

    result = handler.dispatch("discover_mcp_tools", {"mcp_server_url": "https://mcp.example.com"})
    assert result.startswith("ERROR")


@patch("httpx.post")
def test_discover_mcp_tools_unrecognised_format_returns_error(mock_post, handler):
    """discover_mcp_tools returns ERROR when neither JSON nor SSE can be parsed."""
    resp = MagicMock()
    resp.text = "totally unexpected format"
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("not json")
    mock_post.return_value = resp

    result = handler.dispatch("discover_mcp_tools", {"mcp_server_url": "https://mcp.example.com"})
    assert result.startswith("ERROR")


# ---------------------------------------------------------------------------
# ToolHandler._handle_declare_azure_storage
# ---------------------------------------------------------------------------

class TestDeclareAzureStorageTool:
    def _make_handler(self):
        """Create a ToolHandler with empty accumulator and minimal state."""
        from dev_kit.agent.accumulator import ConfigAccumulator
        from dev_kit.agent.tools import ToolHandler
        acc = ConfigAccumulator()
        state = {"phase": "knowledge", "phase_changed": None, "rollback_to": None, "project_meta": {}}
        return ToolHandler(acc, state), acc

    def test_sets_azure_needed_on_accumulator(self):
        """Calling declare_azure_storage flags azure as needed in the accumulator."""
        handler, acc = self._make_handler()
        result = handler._handle_declare_azure_storage({})
        assert acc.is_azure_needed() is True
        assert "Azure Blob Storage noted" in result or "Deployment Inputs" in result

    def test_no_credentials_in_state(self):
        """declare_azure_storage must NOT store any credential values in state."""
        handler, acc = self._make_handler()
        handler._handle_declare_azure_storage({})
        assert "azure_storage" not in handler._state

    def test_ignores_extra_input(self):
        """declare_azure_storage must ignore any unexpected input parameters."""
        handler, acc = self._make_handler()
        # Should not raise even if extra params are passed
        result = handler._handle_declare_azure_storage({"unexpected": "value"})
        assert acc.is_azure_needed() is True
        assert isinstance(result, str)
