"""
agent_core/src/http_clients/async_observability_layer.py

Async HTTP client for the Observability Layer service (port 8004).
Mirror of ObservabilityLayerHttpClient using httpx.AsyncClient.
Used exclusively by stream_turn(); the sync client is unchanged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.interfaces.async_.observability_layer import AsyncObservabilityLayerBase
from src.models import TurnEvent

logger = logging.getLogger(__name__)


class AsyncObservabilityLayerHttpClient(AsyncObservabilityLayerBase):
    """Async HTTP client that calls the Observability Layer service.

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
        self._client = httpx.AsyncClient(timeout=self._timeout_s)

        logger.info(
            "async_learning_http_client.init",
            extra={
                "operation": "async_learning_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        await self._client.aclose()

    async def emit_turn(self, event: TurnEvent) -> None:
        """POST /emit/turn — async version. Never raises."""
        if event is None:
            logger.warning(
                "async_learning_http_client.emit_turn_skipped",
                extra={
                    "operation": "async_learning_http_client.emit_turn",
                    "status": "skipped",
                    "reason": "event is None",
                },
            )
            return

        start = time.time()
        try:
            payload = _serialise_turn_event(event)
            response = await self._client.post(
                f"{self._endpoint}/emit/turn",
                json=payload,
            )
            response.raise_for_status()
            logger.info(
                "async_learning_http_client.emit_turn",
                extra={
                    "operation": "async_learning_http_client.emit_turn",
                    "status": "success",
                    "session_id": payload.get("session_id", ""),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "async_learning_http_client.emit_turn_error",
                extra={
                    "operation": "async_learning_http_client.emit_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    async def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """POST /emit/signal — async version. Never raises."""
        if signal_type is None:
            logger.warning(
                "async_learning_http_client.emit_signal_skipped",
                extra={
                    "operation": "async_learning_http_client.emit_signal",
                    "status": "skipped",
                    "reason": "signal_type is None",
                },
            )
            return

        start = time.time()
        try:
            response = await self._client.post(
                f"{self._endpoint}/emit/signal",
                json={"signal_type": signal_type, "data": data or {}},
            )
            response.raise_for_status()
            logger.info(
                "async_learning_http_client.emit_signal",
                extra={
                    "operation": "async_learning_http_client.emit_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "async_learning_http_client.emit_signal_error",
                extra={
                    "operation": "async_learning_http_client.emit_signal",
                    "status": "failure",
                    "signal_type": signal_type,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _serialise_turn_event(event: Any) -> dict:
    """Convert a TurnEvent dataclass to a flat JSON-serialisable dict."""
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    def _serialise_trust_result(tr: Any) -> dict:
        if tr is None:
            return {"passed": True, "action": "allow", "reason": None}
        if isinstance(tr, dict):
            return {"passed": tr.get("passed", True), "action": tr.get("action", "allow"), "reason": tr.get("reason")}
        return {"passed": getattr(tr, "passed", True), "action": getattr(tr, "action", "allow"), "reason": getattr(tr, "reason", None)}

    def _serialise_tool_calls(tcs: Any) -> list:
        if not tcs:
            return []
        result = []
        for tc in tcs:
            if isinstance(tc, dict):
                result.append({"tool_name": tc.get("tool_name", ""), "tool_use_id": tc.get("tool_use_id", ""), "input_params": tc.get("input_params", {})})
            else:
                result.append({"tool_name": getattr(tc, "tool_name", ""), "tool_use_id": getattr(tc, "tool_use_id", ""), "input_params": getattr(tc, "input_params", {})})
        return result

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
