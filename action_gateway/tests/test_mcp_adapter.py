"""Tests for the McpAdapter in the Action Gateway block.

Covers normal execution, edge cases, and failure scenarios for MCP server
tool discovery and execution. All MCP SDK calls are mocked — no real server
connections are made.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.mcp import McpAdapter
from src.models import ToolDefinition, ToolResult


def _mock_mcp_tool(name: str, description: str, schema: dict) -> MagicMock:
    """Return a mock MCP Tool object with the given attributes."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = schema
    return tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_tool_config():
    """MCP adapter config for a test SSE server."""
    return {
        "id": "test_mcp",
        "type": "mcp",
        "category": "read",
        "description": "Test MCP server",
        "server_url": "https://mcp.test.example/sse",
        "transport": "sse",
        "namespace": "test_mcp",
    }


@pytest.fixture
def mock_tools():
    """Two fake MCP tool objects."""
    return [
        _mock_mcp_tool(
            "search",
            "Search for items",
            {"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        _mock_mcp_tool(
            "get_item",
            "Fetch a single item",
            {"type": "object", "properties": {"id": {"type": "string"}}},
        ),
    ]


# ---------------------------------------------------------------------------
# TestMcpAdapterInit
# ---------------------------------------------------------------------------


class TestMcpAdapterInit:
    """Tests for McpAdapter initialisation and tool discovery via initialize()."""

    @pytest.mark.asyncio
    async def test_discovers_tools_from_server(self, mcp_tool_config, mock_tools):
        """initialize() populates _tool_definitions from discovered MCP tools."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        definitions = adapter.get_tool_definitions()
        assert len(definitions) == 2
        assert all(isinstance(d, ToolDefinition) for d in definitions)

    @pytest.mark.asyncio
    async def test_namespace_prefixed_on_tool_names(self, mcp_tool_config, mock_tools):
        """Tool names are prefixed with namespace so they are globally unique."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        names = [d.name for d in adapter.get_tool_definitions()]
        assert "test_mcp.search" in names
        assert "test_mcp.get_item" in names

    @pytest.mark.asyncio
    async def test_category_from_config(self, mcp_tool_config, mock_tools):
        """All discovered tools inherit the category from config."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        for defn in adapter.get_tool_definitions():
            assert defn.category == "read"

    @pytest.mark.asyncio
    async def test_no_tools_discovered_returns_empty_list(self, mcp_tool_config):
        """When MCP server reports no tools, get_tool_definitions returns []."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=[]
        ):
            await adapter.initialize()

        assert adapter.get_tool_definitions() == []

    def test_init_does_not_connect(self, mcp_tool_config):
        """__init__ must not connect — _connected starts as False."""
        adapter = McpAdapter(mcp_tool_config)
        assert adapter.health_check() is False

    def test_default_namespace_is_adapter_id(self):
        """Namespace defaults to config['id'] when not specified."""
        config = {
            "id": "my_mcp",
            "type": "mcp",
            "category": "read",
            "description": "desc",
            "server_url": "https://example.com/sse",
        }
        adapter = McpAdapter(config)
        assert adapter._namespace == "my_mcp"

    def test_default_transport_is_sse(self):
        """Transport defaults to 'sse' when not specified in config."""
        config = {
            "id": "my_mcp",
            "type": "mcp",
            "category": "read",
            "description": "desc",
            "server_url": "https://example.com/sse",
        }
        adapter = McpAdapter(config)
        assert adapter._transport == "sse"


# ---------------------------------------------------------------------------
# TestMcpAdapterExecute
# ---------------------------------------------------------------------------


class TestMcpAdapterExecute:
    """Tests for McpAdapter.execute()."""

    @pytest.mark.asyncio
    async def test_strips_namespace_for_mcp_call(self, mcp_tool_config, mock_tools):
        """execute() strips the namespace prefix before calling the MCP tool."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        mock_call_result = MagicMock()
        mock_call_result.content = [MagicMock(text='{"results": []}')]
        mock_call_result.isError = False

        with patch.object(
            adapter, "_call_tool", new_callable=AsyncMock, return_value=mock_call_result
        ) as mock_call:
            result = await adapter.execute("test_mcp.search", {"query": "hello"}, "session-1")

        mock_call.assert_called_once_with("search", {"query": "hello"})
        assert result.success is True
        assert result.tool_name == "test_mcp.search"

    @pytest.mark.asyncio
    async def test_mcp_error_returns_failure(self, mcp_tool_config, mock_tools):
        """execute() returns success=False when _call_tool raises an exception."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        with patch.object(
            adapter,
            "_call_tool",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ):
            result = await adapter.execute("test_mcp.search", {}, "session-1")

        assert result.success is False
        assert "mcp_error" in result.error
        assert "RuntimeError" in result.error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_failure(self, mcp_tool_config, mock_tools):
        """execute() returns success=False for a tool name not in _tool_definitions."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        result = await adapter.execute("test_mcp.nonexistent", {}, "session-1")

        assert result.success is False
        assert "unknown_tool" in result.error
        assert "test_mcp.nonexistent" in result.error

    @pytest.mark.asyncio
    async def test_execute_parses_json_result(self, mcp_tool_config, mock_tools):
        """execute() parses JSON text content into a dict on success."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        mock_call_result = MagicMock()
        mock_call_result.content = [MagicMock(text='{"key": "value"}')]
        mock_call_result.isError = False

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock, return_value=mock_call_result):
            result = await adapter.execute("test_mcp.search", {"query": "test"}, "s1")

        assert result.success is True
        assert result.result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_execute_handles_non_json_text(self, mcp_tool_config, mock_tools):
        """execute() wraps non-JSON text content in a dict with 'text' key."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        mock_call_result = MagicMock()
        mock_call_result.content = [MagicMock(text="plain text response")]
        mock_call_result.isError = False

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock, return_value=mock_call_result):
            result = await adapter.execute("test_mcp.search", {}, "s1")

        assert result.success is True
        assert result.result == {"text": "plain text response"}

    @pytest.mark.asyncio
    async def test_mcp_error_sets_connected_false(self, mcp_tool_config, mock_tools):
        """execute() sets _connected=False when _call_tool raises."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools
        ):
            await adapter.initialize()

        assert adapter.health_check() is True

        with patch.object(
            adapter,
            "_call_tool",
            new_callable=AsyncMock,
            side_effect=ConnectionError("lost"),
        ):
            await adapter.execute("test_mcp.search", {}, "s1")

        assert adapter.health_check() is False


# ---------------------------------------------------------------------------
# TestMcpAdapterHealthCheck
# ---------------------------------------------------------------------------


class TestMcpAdapterHealthCheck:
    """Tests for McpAdapter.health_check()."""

    @pytest.mark.asyncio
    async def test_returns_true_when_connected(self, mcp_tool_config):
        """health_check() returns True after successful initialize()."""
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(
            adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=[]
        ):
            await adapter.initialize()

        assert adapter.health_check() is True

    def test_returns_false_when_disconnected(self, mcp_tool_config):
        """health_check() returns False before initialize() is called."""
        adapter = McpAdapter(mcp_tool_config)
        assert adapter.health_check() is False


# ---------------------------------------------------------------------------
# TestMcpAdapterOtel
# ---------------------------------------------------------------------------


class TestMcpAdapterOtel:
    """Tests for OTel span instrumentation in McpAdapter."""

    @pytest.fixture
    def mock_tools(self):
        return [
            _mock_mcp_tool(
                "search",
                "Search",
                {"type": "object", "properties": {"query": {"type": "string"}}},
            )
        ]

    @pytest.mark.asyncio
    async def test_execute_emits_mcp_tool_call_span(self, otel_setup, mcp_tool_config, mock_tools):
        """execute() must produce an action.mcp.tool_call child span."""
        exporter, _ = otel_setup
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools):
            await adapter.initialize()

        mock_result = MagicMock()
        mock_result.content = [MagicMock(text='{"answer": 1}')]

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock, return_value=mock_result):
            await adapter.execute("test_mcp.search", {"query": "hello"}, "sess-otel-mcp-1")

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        assert "action.mcp.tool_call" in span_names

        mcp_span = next(s for s in spans if s.name == "action.mcp.tool_call")
        assert mcp_span.attributes.get("mcp.server_url") == "https://mcp.test.example/sse"
        assert mcp_span.attributes.get("mcp.tool_name") == "search"

    @pytest.mark.asyncio
    async def test_mcp_error_records_exception_on_span(self, otel_setup, mcp_tool_config, mock_tools):
        """execute() failure must record the exception on the action.mcp.tool_call span."""
        exporter, _ = otel_setup
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools):
            await adapter.initialize()

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock, side_effect=RuntimeError("lost")):
            result = await adapter.execute("test_mcp.search", {}, "sess-otel-mcp-2")

        assert result.success is False
        spans = exporter.get_finished_spans()
        mcp_span = next((s for s in spans if s.name == "action.mcp.tool_call"), None)
        assert mcp_span is not None
        assert len(mcp_span.events) > 0

    @pytest.mark.asyncio
    async def test_response_size_metric_recorded(self, otel_setup, mcp_tool_config, mock_tools):
        """execute() must record action.response.size_bytes on success."""
        _, reader = otel_setup
        adapter = McpAdapter(mcp_tool_config)
        with patch.object(adapter, "_connect_and_discover", new_callable=AsyncMock, return_value=mock_tools):
            await adapter.initialize()

        mock_result = MagicMock()
        mock_result.content = [MagicMock(text='{"data": "value"}')]

        with patch.object(adapter, "_call_tool", new_callable=AsyncMock, return_value=mock_result):
            await adapter.execute("test_mcp.search", {}, "sess-otel-mcp-3")

        metrics_data = reader.get_metrics_data()
        metric_names = {
            m.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "action.response.size_bytes" in metric_names
