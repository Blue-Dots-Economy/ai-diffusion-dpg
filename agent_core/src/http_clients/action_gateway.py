"""
agent_core/src/http_clients/action_gateway.py

HTTP client for the Action Gateway DPG block.
Fetches tool definitions from GET /tools at startup and routes tool calls via POST /execute.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.interfaces.action_gateway import ActionGatewayBase
from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


class ActionGatewayHttpClient(ActionGatewayBase):
    """HTTP client for the Action Gateway service.

    Fetches tool definitions from GET /tools at startup.
    Routes tool calls via POST /execute.
    """

    def __init__(self, config: dict) -> None:
        """Initialise the client and fetch tool definitions from the gateway.

        Args:
            config: Merged runtime config dict. Must contain
                ``action_gateway_client.endpoint`` (base URL).

        Raises:
            ValueError: If config is None.
        """
        if config is None:
            raise ValueError("config must not be None")

        gw_config = config.get("action_gateway_client", {})
        self._base_url = gw_config.get("endpoint", "http://action_gateway:9999").rstrip("/")
        self._timeout_ms = gw_config.get("timeout_ms", 5000)
        self._timeout_s = self._timeout_ms / 1000.0
        self._tool_definitions = self._fetch_tool_definitions()

    def _fetch_tool_definitions(self) -> list[dict]:
        """Fetch Anthropic-formatted tool definitions from GET /tools.

        Returns:
            List of tool definition dicts; empty list on failure.
        """
        try:
            resp = httpx.get(f"{self._base_url}/tools", timeout=self._timeout_s)
            resp.raise_for_status()
            data = resp.json()
            tools = data.get("tools", [])
            logger.info(
                "action_gateway_client.fetch_tools",
                extra={
                    "operation": "fetch_tool_definitions",
                    "status": "success",
                    "tools_count": len(tools),
                },
            )
            return tools
        except Exception as e:
            logger.error(
                "action_gateway_client.fetch_tools",
                extra={
                    "operation": "fetch_tool_definitions",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return []

    def list_available_tools(self) -> list[dict]:
        """Return the tool definitions fetched at startup.

        Returns:
            Cached list of Anthropic-formatted tool definition dicts.
        """
        return self._tool_definitions

    def execute(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """Execute a single tool call via POST /execute.

        Args:
            tool_call: The tool call to execute.
            session_id: Current session identifier for routing context.

        Returns:
            ToolResult with success/failure status and result data.

        Raises:
            ValueError: If tool_call is None.
        """
        if tool_call is None:
            raise ValueError("tool_call must not be None")

        try:
            resp = httpx.post(
                f"{self._base_url}/execute",
                json={
                    "tool_name": tool_call.tool_name,
                    "tool_use_id": tool_call.tool_use_id,
                    "input_params": tool_call.input_params,
                    "session_id": session_id,
                },
                timeout=self._timeout_s,
            )
            data = resp.json()
            return ToolResult(
                tool_use_id=data.get("tool_use_id", tool_call.tool_use_id),
                tool_name=data.get("tool_name", tool_call.tool_name),
                result=data.get("result", {}),
                success=data.get("success", False),
                result_text=data.get("result_text", ""),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            logger.error(
                "action_gateway_client.execute",
                extra={
                    "operation": f"execute.{tool_call.tool_name}",
                    "status": "failure",
                    "error": "timeout",
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"gateway_timeout: {tool_call.tool_name}",
            )
        except Exception as e:
            logger.error(
                "action_gateway_client.execute",
                extra={
                    "operation": f"execute.{tool_call.tool_name}",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"gateway_error: {type(e).__name__}: {e}",
            )
