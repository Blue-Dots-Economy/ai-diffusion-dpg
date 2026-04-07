"""
agent_core/src/observability_http_client.py

HTTP client for the Observability Layer service at port 8004.
Implements the same interface as ObservabilityLayerBase.

Called from daemon threads (async post-turn). Must never raise.
Must never block the response path.

Config reads from:
  learning_client.endpoint   (default "http://localhost:8004")
  learning_client.timeout_ms (default 2000)
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.interfaces.observability_layer import ObservabilityLayerBase
from src.models import TurnEvent, TrustCheckResult, ToolCall

logger = logging.getLogger(__name__)


class ObservabilityLayerHttpClient(ObservabilityLayerBase):
    """
    HTTP client that calls the Observability Layer service.

    Implements the ObservabilityLayerBase interface contract so it can be swapped
    with any other implementation without changing the orchestrator.

    Args:
        config: Full config dict. Reads learning_client.endpoint and
                learning_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        client_cfg = config.get("learning_client", {})
        self._endpoint: str = client_cfg.get("endpoint", "http://localhost:8004")
        self._timeout_s: float = client_cfg.get("timeout_ms", 2000) / 1000

        logger.info(
            "learning_http_client.init",
            extra={
                "operation": "learning_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors ObservabilityLayerBase
    # ------------------------------------------------------------------

    def emit_turn(self, event: TurnEvent) -> None:
        """
        POST /emit/turn — serialise TurnEvent and send to Observability Layer service.
        Logs errors and continues. Never raises.
        """
        if event is None:
            logger.warning(
                "learning_http_client.emit_turn_skipped",
                extra={
                    "operation": "learning_http_client.emit_turn",
                    "status": "skipped",
                    "reason": "event is None",
                },
            )
            return

        start = time.time()

        try:
            payload = _serialise_turn_event(event)
            response = httpx.post(
                f"{self._endpoint}/emit/turn",
                json=payload,
                timeout=self._timeout_s,
            )
            response.raise_for_status()

            logger.info(
                "learning_http_client.emit_turn",
                extra={
                    "operation": "learning_http_client.emit_turn",
                    "status": "success",
                    "session_id": payload.get("session_id", ""),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.TimeoutException as e:
            logger.error(
                "learning_http_client.emit_turn_timeout",
                extra={
                    "operation": "learning_http_client.emit_turn",
                    "status": "failure",
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "learning_http_client.emit_turn_http_error",
                extra={
                    "operation": "learning_http_client.emit_turn",
                    "status": "failure",
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "learning_http_client.emit_turn_error",
                extra={
                    "operation": "learning_http_client.emit_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """
        POST /emit/signal — send a discrete signal event.
        Logs errors and continues. Never raises.
        """
        if signal_type is None:
            logger.warning(
                "learning_http_client.emit_signal_skipped",
                extra={
                    "operation": "learning_http_client.emit_signal",
                    "status": "skipped",
                    "reason": "signal_type is None",
                },
            )
            return

        start = time.time()

        try:
            response = httpx.post(
                f"{self._endpoint}/emit/signal",
                json={"signal_type": signal_type, "data": data or {}},
                timeout=self._timeout_s,
            )
            response.raise_for_status()

            logger.info(
                "learning_http_client.emit_signal",
                extra={
                    "operation": "learning_http_client.emit_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.TimeoutException as e:
            logger.error(
                "learning_http_client.emit_signal_timeout",
                extra={
                    "operation": "learning_http_client.emit_signal",
                    "status": "failure",
                    "signal_type": signal_type,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "learning_http_client.emit_signal_http_error",
                extra={
                    "operation": "learning_http_client.emit_signal",
                    "status": "failure",
                    "signal_type": signal_type,
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "learning_http_client.emit_signal_error",
                extra={
                    "operation": "learning_http_client.emit_signal",
                    "status": "failure",
                    "signal_type": signal_type,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _serialise_trust_result(trust_result: Any) -> dict:
    """Convert a TrustCheckResult dataclass or dict to a JSON-serialisable dict."""
    if trust_result is None:
        return {"passed": True, "action": "allow", "reason": None}
    if isinstance(trust_result, dict):
        return {
            "passed": trust_result.get("passed", True),
            "action": trust_result.get("action", "allow"),
            "reason": trust_result.get("reason"),
        }
    # Dataclass
    return {
        "passed": getattr(trust_result, "passed", True),
        "action": getattr(trust_result, "action", "allow"),
        "reason": getattr(trust_result, "reason", None),
    }


def _serialise_tool_calls(tool_calls: Any) -> list:
    """Convert a list of ToolCall dataclasses or dicts to JSON-serialisable form."""
    if not tool_calls:
        return []
    result = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            result.append({
                "tool_name": tc.get("tool_name", ""),
                "tool_use_id": tc.get("tool_use_id", ""),
                "input_params": tc.get("input_params", {}),
            })
        else:
            result.append({
                "tool_name": getattr(tc, "tool_name", ""),
                "tool_use_id": getattr(tc, "tool_use_id", ""),
                "input_params": getattr(tc, "input_params", {}),
            })
    return result


def _serialise_turn_event(event: Any) -> dict:
    """Convert a TurnEvent dataclass or dict to a flat JSON-serialisable dict."""
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    return {
        "session_id": _get("session_id", ""),
        "turn_id": _get("turn_id", ""),
        "trace_id": _get("trace_id") or "",
        "response_text": _get("response_text", ""),
        "tool_calls": _serialise_tool_calls(_get("tool_calls", [])),
        "trust_input_result": _serialise_trust_result(_get("trust_input_result")),
        "trust_output_result": _serialise_trust_result(_get("trust_output_result")),
        "model_used": _get("model_used", ""),
        "input_tokens": _get("input_tokens", 0),
        "output_tokens": _get("output_tokens", 0),
        "latency_ms": _get("latency_ms", 0),
        "timestamp_ms": _get("timestamp_ms", 0),
    }
