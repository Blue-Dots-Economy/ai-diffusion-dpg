"""
agent_core/src/action_gateway_http_client.py

HTTP client for the Action Gateway service at port 9999.
Self-contained in agent_core — does not import from action_gateway package.

Implements the ActionGatewayBase interface contract:
  list_available_tools() -> list[dict]
  execute(tool_call: ToolCall, session_id: str) -> ToolResult

Config reads from:
  action_gateway_client.endpoint   (default "http://localhost:9999/onest/market_lookup")
  action_gateway_client.timeout_ms (default 5000)

Tool definitions are built from config at startup:
  connectors.read / connectors.write / connectors.identity
  Each connector entry must have: name, description, input_schema.
  No tool schema is hardcoded here — all comes from domain.yaml.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.interfaces.action_gateway import ActionGatewayBase
from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_tool_definitions(config: dict) -> list[dict]:
    """
    Build Anthropic-formatted tool definitions from the connectors config.

    Reads connectors.read, connectors.write, and connectors.identity.
    Each connector entry must have: name, description, input_schema.
    Entries missing name or input_schema are logged and skipped.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    definitions: list[dict] = []
    connectors = config.get("connectors", {})
    for connector_type in ("read", "write", "identity"):
        for connector in connectors.get(connector_type, []) or []:
            name = connector.get("name")
            description = connector.get("description", "")
            input_schema = connector.get("input_schema")
            if not name or not input_schema:
                _log.warning(
                    "action_gateway_http_client.skip_connector",
                    extra={
                        "operation": "_build_tool_definitions",
                        "status": "skipped",
                        "connector_name": name or "(unnamed)",
                        "reason": "missing name or input_schema",
                    },
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
    HTTP client that calls the Action Gateway service (ONEST mock at port 9999).

    Implements the ActionGatewayBase interface contract. Operates on ToolCall /
    ToolResult dataclasses — no dict conversion needed by the caller.

    Args:
        config: Full config dict. Reads action_gateway_client.endpoint and
                action_gateway_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        client_cfg = config.get("action_gateway_client", {})
        self._endpoint: str = client_cfg.get(
            "endpoint", "http://localhost:9999/onest/market_lookup"
        )
        self._apply_endpoint: str = client_cfg.get(
            "apply_endpoint", "http://localhost:9999/onest/apply"
        )
        self._timeout_s: float = client_cfg.get("timeout_ms", 5000) / 1000
        self._tool_definitions: list[dict] = _build_tool_definitions(config)

        logger.info(
            "action_gateway_http_client.init",
            extra={
                "operation": "action_gateway_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors ActionGatewayBase
    # ------------------------------------------------------------------

    def list_available_tools(self) -> list[dict]:
        """
        Return Anthropic-formatted tool definitions for all available connectors.
        Built from config at startup. Called once by ToolRegistry — result is cached.
        """
        return list(self._tool_definitions)

    def execute(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """
        Execute a single tool call against the appropriate connector.

        Routes onest_market_lookup to the ONEST mock server at the configured
        endpoint. Unknown tool names return a structured error ToolResult.

        Args:
            tool_call: ToolCall dataclass with tool_name, tool_use_id, input_params.
            session_id: Used for logging and per-session constraints.

        Returns:
            ToolResult dataclass. Always returns a result — never raises.
        """
        if tool_call is None:
            raise ValueError("tool_call must not be None")
        if session_id is None:
            raise ValueError("session_id must not be None")

        if tool_call.tool_name == "onest_market_lookup":
            return self._call_onest(tool_call, session_id)

        if tool_call.tool_name == "onest_apply":
            return self._call_apply(tool_call, session_id)

        # Unknown tool — structured error, not an exception
        logger.warning(
            "action_gateway_http_client.unknown_tool",
            extra={
                "operation": "action_gateway_http_client.execute",
                "status": "failure",
                "session_id": session_id,
                "tool_name": tool_call.tool_name,
                "error": f"unknown_tool: {tool_call.tool_name}",
            },
        )
        return ToolResult(
            tool_use_id=tool_call.tool_use_id,
            tool_name=tool_call.tool_name,
            result={},
            success=False,
            error=f"unknown_tool: {tool_call.tool_name}",
        )

    # ------------------------------------------------------------------
    # Private: connector implementations
    # ------------------------------------------------------------------

    def _call_onest(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """HTTP POST to the ONEST mock server."""
        start = time.time()
        params = tool_call.input_params or {}

        try:
            response = httpx.post(
                self._endpoint,
                json={
                    "trade": params.get("trade", ""),
                    "location": params.get("location", ""),
                    "distance_km": params.get("distance_km", 50),
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            logger.info(
                "action_gateway_http_client.onest_success",
                extra={
                    "operation": "action_gateway_http_client._call_onest",
                    "status": "success",
                    "session_id": session_id,
                    "trade": params.get("trade"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result=data,
                success=True,
            )

        except httpx.TimeoutException as e:
            logger.error(
                "action_gateway_http_client.onest_timeout",
                extra={
                    "operation": "action_gateway_http_client._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error="onest_lookup_timeout",
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "action_gateway_http_client.onest_http_error",
                extra={
                    "operation": "action_gateway_http_client._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"onest_http_error: {e.response.status_code}",
            )

        except Exception as e:
            logger.error(
                "action_gateway_http_client.onest_unexpected_error",
                extra={
                    "operation": "action_gateway_http_client._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"onest_error: {type(e).__name__}",
            )

    def _call_apply(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """HTTP POST to the ONEST mock server for job application."""
        start = time.time()
        params = tool_call.input_params or {}

        try:
            response = httpx.post(
                self._apply_endpoint,
                json={
                    "trade": params.get("trade", ""),
                    "employer": params.get("employer", ""),
                    "location": params.get("location", ""),
                    "applicant_name": params.get("applicant_name", ""),
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            logger.info(
                "action_gateway_http_client.apply_success",
                extra={
                    "operation": "action_gateway_http_client._call_apply",
                    "status": "success",
                    "session_id": session_id,
                    "employer": params.get("employer"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result=data,
                success=True,
            )

        except httpx.TimeoutException as e:
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error="onest_apply_timeout",
            )
        except httpx.HTTPStatusError as e:
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"onest_apply_http_error: {e.response.status_code}",
            )
        except Exception as e:
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=f"onest_apply_error: {type(e).__name__}",
            )
