"""
agent_core/src/http_clients/async_action_gateway.py

Async HTTP client for the Action Gateway DPG.
Mirror of ActionGatewayHttpClient using httpx.AsyncClient.
Used exclusively by stream_turn(); the sync client is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.interfaces.async_.action_gateway import AsyncActionGatewayBase
from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


def _build_tool_definitions(config: dict) -> list[dict]:
    """Build Anthropic-formatted tool definitions from the connectors config."""
    definitions: list[dict] = []
    connectors = config.get("connectors", {})

    for connector_type in ("read", "write", "identity"):
        for connector in connectors.get(connector_type, []) or []:
            name = connector.get("name")
            description = connector.get("description", "")
            input_schema = connector.get("input_schema")

            if not name or not input_schema:
                continue

            definitions.append({
                "name": name,
                "description": description,
                "input_schema": input_schema,
            })

    return definitions


class AsyncActionGatewayHttpClient(AsyncActionGatewayBase):
    """Async HTTP client that routes tool calls to the Action Gateway.

    Args:
        config: Full config dict. Reads action_gateway_client.endpoint and
                action_gateway_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        client_cfg = config.get("action_gateway_client", {})
        base_url: str = client_cfg.get("endpoint", "http://localhost:9999")
        self._endpoint: str = f"{base_url.rstrip('/')}/execute"
        self._timeout_s: float = client_cfg.get("timeout_ms", 5000) / 1000
        self._client = httpx.AsyncClient(timeout=self._timeout_s)
        self._tool_definitions: list[dict] = _build_tool_definitions(config)

        logger.info(
            "async_action_gateway_http_client.init",
            extra={
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
                "tools_loaded": [t["name"] for t in self._tool_definitions],
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        await self._client.aclose()

    async def list_available_tools(self) -> list[dict]:
        """Return pre-computed tool definitions."""
        return list(self._tool_definitions)

    async def execute(
        self,
        tool_call: ToolCall,
        session_id: str,
        user_id: str = "",
    ) -> ToolResult:
        """Execute a single tool call via the Action Gateway.

        Always returns ToolResult — never raises.

        Args:
            tool_call: Tool_use block from the LLM.
            session_id: Session identifier.
            user_id: Stable user ID (E.164 for voice). Forwarded to Action
                Gateway so path-templated tools like ``get_profile`` can
                substitute ``{user_id}`` without the LLM having to pass it.
        """
        if tool_call is None:
            raise ValueError("tool_call must not be None")

        payload = {
            "tool_name": tool_call.tool_name,
            "tool_use_id": tool_call.tool_use_id,
            "input_params": tool_call.input_params,
            "session_id": session_id,
            "user_id": user_id,
        }

        try:
            logger.info(
                "async_action_gateway.execute_request",
                extra={
                    "tool_name": tool_call.tool_name,
                    "session_id": session_id,
                },
            )

            res = await self._client.post(self._endpoint, json=payload)
            res.raise_for_status()
            data = res.json()

            return ToolResult(
                tool_use_id=data["tool_use_id"],
                tool_name=tool_call.tool_name,
                result=data.get("result", {}),
                success=data.get("success", True),
                result_text=data.get("result_text", ""),
                error=data.get("error"),
            )

        except Exception as e:
            logger.error(
                "async_action_gateway.execution_failed",
                extra={
                    "tool_name": tool_call.tool_name,
                    "error": str(e),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                result_text="",
                error=f"Action Gateway error: {str(e)}",
            )
