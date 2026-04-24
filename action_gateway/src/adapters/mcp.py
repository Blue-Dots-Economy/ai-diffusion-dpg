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

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace

from src.adapters.base import ToolAdapter
from src.models import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


def _get_tracer() -> otel_trace.Tracer:
    """Return the OTel tracer for McpAdapter.

    Resolved lazily so tests can install a TracerProvider before the first call.

    Returns:
        opentelemetry.trace.Tracer for this instrumentation scope.
    """
    return otel_trace.get_tracer(__name__)


def _get_meter() -> otel_metrics.Meter:
    """Return the OTel meter for McpAdapter.

    Resolved lazily so tests can install a MeterProvider before the first call.

    Returns:
        opentelemetry.metrics.Meter for this instrumentation scope.
    """
    return otel_metrics.get_meter(__name__)

_DEFAULT_TRANSPORT = "sse"
_SUPPORTED_TRANSPORTS = {"sse", "streamable_http"}
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
        raw_transport = config.get("transport", _DEFAULT_TRANSPORT)
        if raw_transport not in _SUPPORTED_TRANSPORTS:
            raise ValueError(
                f"Unsupported MCP transport '{raw_transport}' for adapter "
                f"'{config.get('id')}'. Supported: {sorted(_SUPPORTED_TRANSPORTS)}"
            )
        self._transport: str = raw_transport
        self._namespace: str = config.get("namespace", config.get("id", ""))
        self._category: str = config.get("category", "read")
        self._max_size_chars: int = (
            config.get("response", {}).get("max_size_chars", _DEFAULT_MAX_SIZE_CHARS)
        )
        self._tool_definitions: list[ToolDefinition] = []
        self._connected: bool = False

        _m = _get_meter()
        self._response_size_hist = _m.create_histogram(
            "action.response.size_bytes", unit="By", description="Response payload size in bytes before truncation."
        )
        self._truncated_counter = _m.create_counter(
            "action.response.truncated_total", description="Count of responses truncated to max_size_chars."
        )

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
        self,
        tool_name: str,
        params: dict,
        session_id: str,
        user_id: str = "",
    ) -> ToolResult:
        """Execute a tool call on the MCP server and return a normalised result.

        Strips the namespace prefix from tool_name before forwarding to the
        MCP server. Returns a failed ToolResult for unknown tools or any
        exception raised during the call. Never raises.

        Args:
            tool_name: Namespaced tool name (e.g. 'my_mcp.search').
            params: Parameter dict from the LLM's tool_use input block.
            session_id: Session identifier for log correlation; may be empty.
            user_id: Stable user identifier; accepted for interface parity
                with :class:`RestApiAdapter` but not yet consumed by the MCP
                protocol.

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
        prefix = f"{self._namespace}__"
        if tool_name.startswith(prefix):
            mcp_tool_name = tool_name[len(prefix):]

        start = time.time()
        try:
            with _get_tracer().start_as_current_span("action.mcp.tool_call") as mcp_span:
                mcp_span.set_attribute("mcp.server_url", self._server_url)
                mcp_span.set_attribute("mcp.tool_name", mcp_tool_name)
                call_result = await self._call_tool(mcp_tool_name, params)
                latency_ms = int((time.time() - start) * 1000)
                mcp_span.set_attribute("latency_ms", latency_ms)

            # Extract text from first content item
            raw_text: str = ""
            if call_result.content:
                raw_text = call_result.content[0].text

            full_text = raw_text
            # Truncate to max_size_chars
            raw_text = raw_text[: self._max_size_chars]

            # Try to parse JSON; fall back to wrapping in dict
            try:
                result_dict: dict = json.loads(raw_text)
                if not isinstance(result_dict, dict):
                    result_dict = {"value": result_dict}
            except (json.JSONDecodeError, ValueError):
                result_dict = {"text": raw_text}

            self._response_size_hist.record(len(full_text.encode()), {"tool_name": tool_name})
            if len(full_text) > self._max_size_chars:
                self._truncated_counter.add(1, {"tool_name": tool_name})

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

    async def _open_session(self):
        """Context manager that opens the correct transport and yields a ready ClientSession.

        Selects between SSE (old transport) and Streamable HTTP (new transport,
        MCP spec 2025-03-26) based on self._transport. Both branches yield a
        fully-initialised ClientSession with the MCP handshake completed.

        SSE transport:         GET /sse establishes stream; POST /messages sends.
        Streamable HTTP:       POST only; server responds inline or via SSE chunks.
                               GitBook, Notion, and other hosted MCP servers use this.

        Yields:
            An initialised mcp.ClientSession.

        Raises:
            Exception: Propagates any connection or protocol error to the caller.
        """
        from contextlib import asynccontextmanager

        from mcp import ClientSession

        if self._transport == "streamable_http":
            from mcp.client.streamable_http import streamablehttp_client

            @asynccontextmanager
            async def _ctx():
                async with streamablehttp_client(self._server_url) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        yield session

        else:  # sse (default)
            from mcp.client.sse import sse_client

            @asynccontextmanager
            async def _ctx():
                async with sse_client(self._server_url) as (r, w):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        yield session

        return _ctx()

    async def _connect_and_discover(self) -> list[Any]:
        """Connect to the MCP server and return the list of discovered tools.

        Opens a session via the configured transport, calls tools/list, and
        returns the raw MCP Tool list. This method is a seam for testing —
        it is patched in unit tests so no real network connection is made.

        Returns:
            List of MCP Tool objects with .name, .description, .inputSchema.

        Raises:
            Exception: Propagates any connection or protocol error so that
                initialize() can handle it.
        """
        async with await self._open_session() as session:
            result = await session.list_tools()
            return result.tools or []

    async def _call_tool(self, mcp_tool_name: str, params: dict) -> Any:
        """Call a single tool on the MCP server and return the raw result.

        Opens a session via the configured transport, calls tools/call, and
        returns the raw CallToolResult. This method is a seam for testing —
        it is patched in unit tests so no real network connection is made.

        Args:
            mcp_tool_name: Raw (non-namespaced) tool name as known to the server.
            params: Parameter dict to pass to the tool.

        Returns:
            MCP CallToolResult object with .content and .isError fields.

        Raises:
            Exception: Propagates any connection or tool execution error.
        """
        async with await self._open_session() as session:
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
            name=f"{self._namespace}__{mcp_tool.name}",
            description=mcp_tool.description or f"Tool {mcp_tool.name}",
            input_schema=input_schema,
            category=self._category,
        )
