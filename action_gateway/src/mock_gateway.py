"""
action_gateway/src/mock_gateway.py

MockActionGateway — PoC stub for the Action Gateway DPG.

Implements the ActionGatewayBase interface. Makes HTTP calls to the mock ONEST
server (mock_server.py, port 9999) when the LLM requests a tool call.

Architecture:
    LLM → tool_use block → Agent Core → MockActionGateway.execute()
        → HTTP POST → mock_server /onest/market_lookup
        → ToolResult → Agent Core → second LLM call

Tool definitions:
    list_available_tools() returns Anthropic-formatted tool schema for
    onest_market_lookup. The ToolRegistry in Agent Core calls this once at startup
    and serves the cached list on every LLM call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definition — Anthropic tool-use format
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "onest_market_lookup",
        "description": (
            "Search ONEST live job market data by trade and location. "
            "Returns salary range, market signal (growth trend), and top employers "
            "currently hiring for the given trade in the specified area."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trade": {
                    "type": "string",
                    "description": "Trade/skill to search for. E.g. electrician, welder, fitter.",
                },
                "location": {
                    "type": "string",
                    "description": "City or district name. E.g. Hubli, Dharwad, Belgaum.",
                },
                "distance_km": {
                    "type": "integer",
                    "description": "Search radius in km from the specified location. Default 50.",
                },
            },
            "required": ["trade"],
        },
    },
    {
        "name": "onest_apply",
        "description": (
            "Submit a job application via ONEST on behalf of the user. "
            "Call this only after the user has explicitly confirmed they want to apply "
            "to a specific employer for a specific role. "
            "Returns a reference number and confirmation message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trade": {
                    "type": "string",
                    "description": "The trade/role the user is applying for.",
                },
                "employer": {
                    "type": "string",
                    "description": "Name of the employer to apply to.",
                },
                "location": {
                    "type": "string",
                    "description": "City or district of the job.",
                },
                "applicant_name": {
                    "type": "string",
                    "description": "Name of the applicant (from user profile).",
                },
            },
            "required": ["trade", "employer"],
        },
    },
]


class MockActionGateway:
    """
    Action Gateway stub. Implements ActionGatewayBase contract.

    Calls the mock ONEST server via HTTP for onest_market_lookup tool calls.
    All other tool names return a structured error — no silent swallowing.

    Args:
        config: Full config dict. Reads action_gateway.connectors for endpoint URLs
                and timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        gateway_cfg = config.get("action_gateway", {})
        connectors_cfg = gateway_cfg.get("connectors", {})
        onest_cfg = connectors_cfg.get("onest_market_lookup", {})
        apply_cfg = connectors_cfg.get("onest_apply", {})

        self._onest_endpoint: str = onest_cfg.get(
            "endpoint", "http://localhost:9999/onest/market_lookup"
        )
        self._apply_endpoint: str = apply_cfg.get(
            "endpoint", "http://localhost:9999/onest/apply"
        )
        self._timeout_s: float = onest_cfg.get("timeout_ms", 5000) / 1000

        logger.info(
            "action_gateway.init",
            extra={
                "operation": "mock_gateway.init",
                "status": "success",
                "onest_endpoint": self._onest_endpoint,
                "timeout_s": self._timeout_s,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors ActionGatewayBase
    # ------------------------------------------------------------------

    def list_available_tools(self) -> list[dict]:
        """
        Return Anthropic-formatted tool definitions for all available connectors.
        Called once at startup by ToolRegistry — result is cached.
        """
        return list(_TOOL_DEFINITIONS)

    def execute(self, tool_call: dict, session_id: str) -> dict:
        """
        Execute a single tool call against the appropriate connector.

        Args:
            tool_call: dict with keys: tool_name, tool_use_id, input_params
            session_id: used for logging and per-session constraints

        Returns:
            dict with keys: tool_use_id, tool_name, result, success, error
            Always returns a result — never raises.
        """
        if tool_call is None:
            raise ValueError("tool_call must not be None")
        if session_id is None:
            raise ValueError("session_id must not be None")

        tool_name = tool_call.get("tool_name", "")
        tool_use_id = tool_call.get("tool_use_id", "")
        input_params = tool_call.get("input_params", {})

        if tool_name == "onest_market_lookup":
            return self._call_onest(tool_use_id, tool_name, input_params, session_id)

        if tool_name == "onest_apply":
            return self._call_apply(tool_use_id, tool_name, input_params, session_id)

        # Unknown tool — structured error, not an exception
        logger.warning(
            "action_gateway.unknown_tool",
            extra={
                "operation": "mock_gateway.execute",
                "status": "failure",
                "session_id": session_id,
                "tool_name": tool_name,
                "error": f"unknown_tool: {tool_name}",
            },
        )
        return _tool_result(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            result={},
            success=False,
            error=f"unknown_tool: {tool_name}",
        )

    # ------------------------------------------------------------------
    # Private: connector implementations
    # ------------------------------------------------------------------

    def _call_onest(
        self,
        tool_use_id: str,
        tool_name: str,
        input_params: dict[str, Any],
        session_id: str,
    ) -> dict:
        """HTTP POST to the mock ONEST server."""
        start = time.time()

        try:
            response = httpx.post(
                self._onest_endpoint,
                json={
                    "trade": input_params.get("trade", ""),
                    "location": input_params.get("location", ""),
                    "distance_km": input_params.get("distance_km", 50),
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            logger.info(
                "action_gateway.onest_success",
                extra={
                    "operation": "mock_gateway._call_onest",
                    "status": "success",
                    "session_id": session_id,
                    "trade": input_params.get("trade"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result=data,
                success=True,
            )

        except httpx.TimeoutException as e:
            logger.error(
                "action_gateway.onest_timeout",
                extra={
                    "operation": "mock_gateway._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error="onest_lookup_timeout",
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "action_gateway.onest_http_error",
                extra={
                    "operation": "mock_gateway._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"onest_http_error: {e.response.status_code}",
            )

        except Exception as e:
            logger.error(
                "action_gateway.onest_unexpected_error",
                extra={
                    "operation": "mock_gateway._call_onest",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"onest_error: {type(e).__name__}",
            )

    def _call_apply(
        self,
        tool_use_id: str,
        tool_name: str,
        input_params: dict[str, Any],
        session_id: str,
    ) -> dict:
        """HTTP POST to the mock ONEST apply endpoint."""
        start = time.time()

        try:
            response = httpx.post(
                self._apply_endpoint,
                json={
                    "trade":          input_params.get("trade", ""),
                    "employer":       input_params.get("employer", ""),
                    "location":       input_params.get("location", ""),
                    "applicant_name": input_params.get("applicant_name", ""),
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            logger.info(
                "action_gateway.apply_success",
                extra={
                    "operation": "mock_gateway._call_apply",
                    "status": "success",
                    "session_id": session_id,
                    "trade": input_params.get("trade"),
                    "employer": input_params.get("employer"),
                    "reference": data.get("reference_number"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result=data,
                success=True,
            )

        except httpx.TimeoutException as e:
            logger.error(
                "action_gateway.apply_timeout",
                extra={
                    "operation": "mock_gateway._call_apply",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error="apply_timeout",
            )

        except Exception as e:
            logger.error(
                "action_gateway.apply_error",
                extra={
                    "operation": "mock_gateway._call_apply",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result={},
                success=False,
                error=f"apply_error: {type(e).__name__}",
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _tool_result(
    tool_use_id: str,
    tool_name: str,
    result: dict,
    success: bool,
    error: str | None = None,
) -> dict:
    """Build a ToolResult-compatible dict."""
    return {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "result": result,
        "success": success,
        "error": error,
    }
