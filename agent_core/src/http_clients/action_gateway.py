"""
agent_core/src/http_clients/action_gateway.py

Generic HTTP client for the Action Gateway DPG. 
Implements a single-endpoint tool execution pattern to maintain domain-agnosticism.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.interfaces.action_gateway import ActionGatewayBase
from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


def _build_tool_definitions(config: dict) -> list[dict]:
    """
    Build Anthropic-formatted tool definitions from the connectors config.
    Normally this would fetch from the Gateway, but for the PoC we derive 
    from domain.yaml to ensure local consistency.
    """
    definitions: list[dict] = []
    connectors = config.get("connectors", {})
    
    # Iterate through external connector types only (read, write, identity).
    # Internal connectors (e.g. knowledge_retrieval) are loaded separately by
    # ToolRegistry._load_internal_tools() and routed to the Knowledge Engine,
    # not through the Action Gateway /execute endpoint.
    for connector_type in ("read", "write", "identity"):
        for connector in connectors.get(connector_type, []) or []:
            name = connector.get("name")
            description = connector.get("description", "")
            input_schema = connector.get("input_schema")
            
            if not name or not input_schema:
                logger.warning(
                    "action_gateway_client.skip_connector",
                    extra={"connector_name": name or "(unnamed)"}
                )
                continue
                
            definitions.append({
                "name": name,
                "description": description,
                "input_schema": input_schema,
            })
            
    return definitions


class ActionGatewayHttpClient(ActionGatewayBase):
    """
    Domain-agnostic HTTP client that routes all tool calls to a generic 
    Action Gateway execution endpoint.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        client_cfg = config.get("action_gateway_client", {})
        self._endpoint: str = client_cfg.get(
            "endpoint", "http://localhost:9999/execute"
        )
        self._timeout_s: float = client_cfg.get("timeout_ms", 5000) / 1000
        
        # Pre-compute definitions from config (derived from domain.yaml)
        self._tool_definitions: list[dict] = _build_tool_definitions(config)

        logger.info(
            "action_gateway_http_client.init",
            extra={
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
                "tools_loaded": [t["name"] for t in self._tool_definitions]
            },
        )

    def list_available_tools(self) -> list[dict]:
        """Return the pre-computed tool definitions."""
        return list(self._tool_definitions)

    def execute(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """
        Execute a single tool call via the generic Action Gateway router.
        """
        if tool_call is None:
            raise ValueError("tool_call must not be None")

        payload = {
            "tool_name": tool_call.tool_name,
            "tool_use_id": tool_call.tool_use_id,
            "input_params": tool_call.input_params,
            "session_id": session_id,
        }

        try:
            logger.info(
                "action_gateway.execute_request",
                extra={
                    "tool_name": tool_call.tool_name,
                    "session_id": session_id,
                },
            )

            with httpx.Client(timeout=self._timeout_s) as client:
                res = client.post(self._endpoint, json=payload)
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
                "action_gateway.execution_failed",
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
