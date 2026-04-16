"""MCP (Model Context Protocol) adapter for the Action Gateway block.

This module provides McpAdapter, which connects to a single MCP server over SSE
transport, discovers available tools at startup, and executes tool calls on
behalf of Agent Core. External access to MCP servers is entirely managed here —
Agent Core never communicates with MCP servers directly.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TRANSPORT = "sse"
_DEFAULT_MAX_SIZE_CHARS = 4000


class McpAdapter(ToolAdapter):
    """Adapter that connects to a single MCP server and exposes its tools.

    One instance is created per MCP server configured in the domain YAML.
    Tool discovery happens during async initialisation so that __init__ never
    performs I/O. All discovered tools are namespaced to avoid collisions when
    multiple MCP servers are registered.

    Attributes:
        config: Raw adapter config dict loaded from domain YAML at startup.
    """

    def __init__(self, config: dict) -> None:
        """Store configuration without connecting to the MCP server.

        Connection and tool discovery are deferred to the async initialize()
        method. Calling execute() or get_tool_definitions() before initialize()
        will return empty / failed results gracefully.

        Args:
            config: Adapter-level config dict. Must contain 'server_url' and
                'category'. Optional fields: 'transport' (default 'sse'),
                'namespace' (default config['id']), 'response.max_size_chars'
                (default 4000).

        Raises:
            ValueError: If config is None (enforced by base class).
        """
        super().__init__(config)
        self._server_url: str = config.get("server_url", "")
        self._transport: str = config.get("transport", _DEFAULT_TRANSPORT)
        self._namespace: str = config.get("namespace", config.get("id", ""))
        self._category: str = config.get("category", "read")
        self._max_size_chars: int = (
            config.get("response", {}).get("max_size_chars", _DEFAULT_MAX_SIZE_CHARS)
        )
        self._tool_definitions: list[ToolDefinition] = []
        self._connected: bool = False

        logger.debug(
            "mcp_adapter_init",
            extra={
                "operation": "McpAdapter.__init__",
                "status": "success",
                "adapter_id": config.get("id"),
                "server_url": self._server_url,
                "namespace": self._namespace,
            },
        )

    # ------------------------------------------------------------------
    # Async lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Connect to the MCP server and discover available tools.

        Calls the server's tools/list endpoint and converts each discovered
        tool into a namespaced ToolDefinition. Must be awaited before
        execute() is called. Safe to call multiple times — subsequent calls
        replace the previously discovered tool list.
        """
        start = time.time()
        try:
            mcp_tools = await self._connect_and_discover()
            self._tool_definitions = [
                self._to_tool_definition(t) for t in (mcp_tools or [])
            ]
            self._connected = True
            latency_ms = int((time.time() - start) * 1000)
            logger.info(
                "mcp_adapter_initialize",
                extra={
                    "operation": "McpAdapter.initialize",
                    "status": "success",
                    "adapter_id": self.config.get("id"),
                    "tools_discovered": len(self._tool_definitions),
                    "latency_ms": latency_ms,
                },
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "mcp_adapter_initialize_failed",
                extra={
                    "operation": "McpAdapter.initialize",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "adapter_id": self.config.get("id"),
                    "latency_ms": latency_ms,
                },
            )
            self._connected = False
            raise

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return the cached list of tools discovered from the MCP server.

        Returns an empty list if initialize() has not been called yet or
        if the server reported no tools.

        Returns:
            List of ToolDefinition objects with namespaced names.
        """
        return list(self._tool_definitions)

    async def execute(
        self, tool_name: str, params: dict, session_id: str
    ) -> ToolResult:
        """Execute a tool call on the MCP server and return a normalised result.

        Strips the namespace prefix from tool_name before forwarding to the
        MCP server. Returns a failed ToolResult for unknown tools or any
        exception raised during the call. Never raises.

        Args:
            tool_name: Namespaced tool name (e.g. 'my_mcp.search').
            params: Parameter dict from the LLM's tool_use input block.
            session_id: Session identifier for log correlation; may be empty.

        Returns:
            ToolResult with success=True and populated result on success,
            or success=False with a structured error string on failure.
        """
        # Validate tool is known
        known_names = {d.name for d in self._tool_definitions}
        if tool_name not in known_names:
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result={},
                success=False,
                error=f"unknown_tool: {tool_name}",
            )

        # Strip namespace prefix to get the raw MCP tool name
        mcp_tool_name = tool_name
        prefix = f"{self._namespace}."
        if tool_name.startswith(prefix):
            mcp_tool_name = tool_name[len(prefix):]

        start = time.time()
        try:
            call_result = await self._call_tool(mcp_tool_name, params)
            latency_ms = int((time.time() - start) * 1000)

            # Extract text from first content item
            raw_text: str = ""
            if call_result.content:
                raw_text = call_result.content[0].text

            # Truncate to max_size_chars
            raw_text = raw_text[: self._max_size_chars]

            # Try to parse JSON; fall back to wrapping in dict
            try:
                result_dict: dict = json.loads(raw_text)
                if not isinstance(result_dict, dict):
                    result_dict = {"value": result_dict}
            except (json.JSONDecodeError, ValueError):
                result_dict = {"text": raw_text}

            logger.info(
                "mcp_adapter_execute",
                extra={
                    "operation": "McpAdapter.execute",
                    "status": "success",
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result=result_dict,
                success=True,
                result_text=raw_text,
            )

        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.time() - start) * 1000)
            error_msg = f"mcp_error: {type(exc).__name__}: {exc}"
            self._connected = False
            logger.error(
                "mcp_adapter_execute_failed",
                extra={
                    "operation": "McpAdapter.execute",
                    "status": "failure",
                    "error": error_msg,
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            )
            return ToolResult(
                tool_use_id="",
                tool_name=tool_name,
                result={},
                success=False,
                error=error_msg,
            )

    def health_check(self) -> bool:
        """Return True if the MCP server connection is active.

        Reflects the internal connection state; does not issue a live probe.

        Returns:
            True if initialize() completed successfully and no subsequent
            call has failed; False otherwise.
        """
        return self._connected

    # ------------------------------------------------------------------
    # Internal helpers (not part of public interface)
    # ------------------------------------------------------------------

    async def _connect_and_discover(self) -> list[Any]:
        """Connect to the MCP server and return the list of discovered tools.

        Opens an SSE connection, initialises a ClientSession, and calls
        tools/list. This method is a seam for testing — it is patched in
        unit tests so no real network connection is made.

        Returns:
            List of MCP Tool objects with .name, .description, .inputSchema.

        Raises:
            Exception: Propagates any connection or protocol error so that
                initialize() can handle it.
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self._server_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return result.tools or []

    async def _call_tool(self, mcp_tool_name: str, params: dict) -> Any:
        """Call a single tool on the MCP server and return the raw result.

        Opens an SSE connection, initialises a ClientSession, and calls
        tools/call. This method is a seam for testing — it is patched in
        unit tests so no real network connection is made.

        Args:
            mcp_tool_name: Raw (non-namespaced) tool name as known to the server.
            params: Parameter dict to pass to the tool.

        Returns:
            MCP CallToolResult object with .content and .isError fields.

        Raises:
            Exception: Propagates any connection or tool execution error.
        """
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self._server_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(mcp_tool_name, params)

    def _to_tool_definition(self, mcp_tool: Any) -> ToolDefinition:
        """Convert a raw MCP Tool object into a namespaced ToolDefinition.

        Args:
            mcp_tool: MCP Tool object with .name, .description, .inputSchema.

        Returns:
            ToolDefinition with name prefixed by the adapter namespace and
            category inherited from the adapter config.
        """
        input_schema = mcp_tool.inputSchema
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}

        return ToolDefinition(
            name=f"{self._namespace}.{mcp_tool.name}",
            description=mcp_tool.description or f"Tool {mcp_tool.name}",
            input_schema=input_schema,
            category=self._category,
        )
