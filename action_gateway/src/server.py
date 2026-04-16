"""FastAPI server for the Action Gateway block.

This module exposes the HTTP interface used by Agent Core to discover available
tools and execute tool calls. It is a thin routing layer over AdapterRegistry
and delegates all business logic to the registered ToolAdapter instances.

Endpoints:
  GET  /tools    — return all ToolDefinitions in the registry.
  POST /execute  — execute a single tool call by name.
  GET  /health   — return per-adapter health status.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI

from src.models import ExecuteRequest, ExecuteResponse, HealthResponse, ToolsResponse
from src.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)


def create_app(registry: AdapterRegistry) -> FastAPI:
    """Create and return the FastAPI application with the given registry.

    The registry is captured in the closure and is used for all request
    handling. This factory pattern allows tests to inject a mock registry
    without modifying module-level state.

    Args:
        registry: Pre-built AdapterRegistry containing all registered adapters.

    Returns:
        A configured FastAPI application instance.
    """
    app = FastAPI(title="Action Gateway", description="DPG Action Gateway service")

    @app.get("/tools", response_model=ToolsResponse)
    async def get_tools() -> ToolsResponse:
        """Return all tool definitions available in the registry.

        Returns:
            ToolsResponse containing a list of all ToolDefinitions.
        """
        start = time.time()
        definitions = registry.get_all_tool_definitions()
        logger.info(
            "get_tools",
            extra={
                "operation": "server.get_tools",
                "status": "success",
                "tool_count": len(definitions),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return ToolsResponse(tools=definitions)

    @app.post("/execute", response_model=ExecuteResponse)
    async def execute_tool(request: ExecuteRequest) -> ExecuteResponse:
        """Execute a single tool call and return the normalised result.

        Resolves the adapter for the requested tool, delegates execution, and
        maps the ToolResult back to an ExecuteResponse. Unknown tool names are
        returned as a structured error rather than an HTTP error code so that
        Agent Core can handle them in the tool loop.

        Args:
            request: ExecuteRequest carrying tool_name, tool_use_id,
                input_params, and optional session_id.

        Returns:
            ExecuteResponse with success=True on success, or success=False
            with an error string for unknown tools or adapter failures.
        """
        start = time.time()

        try:
            adapter = registry.resolve(request.tool_name)
        except KeyError:
            logger.warning(
                "execute_tool_unknown",
                extra={
                    "operation": "server.execute_tool",
                    "status": "failure",
                    "error": f"unknown_tool: {request.tool_name}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ExecuteResponse(
                tool_use_id=request.tool_use_id,
                tool_name=request.tool_name,
                success=False,
                result={},
                error=f"unknown_tool: {request.tool_name}",
            )

        result = await adapter.execute(
            request.tool_name,
            request.input_params,
            request.session_id,
        )

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "execute_tool",
            extra={
                "operation": "server.execute_tool",
                "status": "success" if result.success else "failure",
                "tool_name": request.tool_name,
                "latency_ms": latency_ms,
            },
        )
        return ExecuteResponse(
            tool_use_id=request.tool_use_id,
            tool_name=result.tool_name,
            success=result.success,
            result=result.result,
            result_text=result.result_text,
            error=result.error,
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return the health status of each unique registered adapter.

        Calls health_check() on each unique adapter instance and assembles an
        adapter_status dict keyed by adapter type and id. The overall status is
        "healthy" if all adapters are healthy, "degraded" otherwise.

        Returns:
            HealthResponse with overall status string and per-adapter booleans.
        """
        start = time.time()
        adapter_status: dict[str, bool] = {}
        seen_adapter_ids: set[int] = set()

        for tool_name, adapter in registry._adapters.items():
            adapter_id = id(adapter)
            if adapter_id in seen_adapter_ids:
                continue
            seen_adapter_ids.add(adapter_id)
            adapter_key = tool_name
            adapter_status[adapter_key] = adapter.health_check()

        overall = "healthy" if all(adapter_status.values()) else "degraded"
        if not adapter_status:
            overall = "healthy"

        logger.info(
            "health_check",
            extra={
                "operation": "server.health",
                "status": overall,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return HealthResponse(status=overall, adapters=adapter_status)

    return app
