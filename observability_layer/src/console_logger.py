"""
ConsoleLogger — PoC stub for the Observability Layer DPG.

Implements the ObservabilityLayerBase interface. Writes all observability events to the
Python logging system (structured JSON-compatible key-value pairs via `extra`).

In production this is replaced by an async pipeline that writes to an audit database,
runs quality eval models, and stores feedback signals for training.

Design:
- emit_turn(): logs all TurnEvent fields at INFO level. Never raises.
- emit_signal(): logs signal_type + data at INFO level. Never raises.
- Both methods are safe to call from daemon threads (no shared mutable state).
- Internal errors are caught and logged — never propagated to the caller.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from base import ObservabilityLayerBase

logger = logging.getLogger(__name__)


class ConsoleLogger(ObservabilityLayerBase):
    """
    Console-logging Observability Layer stub.

    Accepts any dict-shaped event (to avoid a hard dependency on agent_core.models)
    and logs it as a structured entry. Attributes are accessed via getattr with
    a fallback to dict .get() so both dataclasses and plain dicts are supported.

    Args:
        config: Full config dict. Reads observability_layer section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        ll_cfg = config.get("observability_layer", {})
        self._log_level: str = ll_cfg.get("log_level", "INFO").upper()

        logger.info(
            "observability_layer.init",
            extra={
                "operation": "console_logger.init",
                "status": "success",
                "log_level": self._log_level,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors ObservabilityLayerBase
    # ------------------------------------------------------------------

    def emit_turn(self, event: Any) -> None:
        """
        Log turn-level observability data.

        Accepts a TurnEvent dataclass or any object/dict with the same fields.
        Never raises — all exceptions are caught and logged internally.
        """
        try:
            if event is None:
                logger.warning(
                    "observability_layer.emit_turn_skipped",
                    extra={
                        "operation": "console_logger.emit_turn",
                        "status": "skipped",
                        "reason": "event is None",
                    },
                )
                return

            start = time.time()

            # Support both dataclass attribute access and plain dict access
            def _get(key: str, default: Any = None) -> Any:
                if isinstance(event, dict):
                    return event.get(key, default)
                return getattr(event, key, default)

            trust_in = _get("trust_input_result")
            trust_out = _get("trust_output_result")
            tool_calls = _get("tool_calls", []) or []
            response_text = _get("response_text", "")
            session_id = _get("session_id", "")
            model_used = _get("model_used", "")
            input_tokens = _get("input_tokens", 0)
            output_tokens = _get("output_tokens", 0)
            latency_ms = _get("latency_ms", 0)

            # Format tool calls for display
            tool_calls_str = "none"
            if tool_calls:
                tool_calls_str = ", ".join(
                    f"{tc.get('tool_name', '?')}({tc.get('input_params', {})})"
                    if isinstance(tc, dict)
                    else f"{getattr(tc, 'tool_name', '?')}({getattr(tc, 'input_params', {})})"
                    for tc in tool_calls
                )

            # Truncate long response text for readability
            response_preview = (response_text[:300] + "...") if len(response_text) > 300 else response_text

            logger.info(
                "\n"
                "┌─────────────────────────────────────────────────────────────────\n"
                "│  LEARNING LAYER — TURN AUDIT\n"
                "│  session_id   : %s\n"
                "│  model        : %s\n"
                "│  tokens       : %d in / %d out\n"
                "│  latency      : %d ms\n"
                "│  trust input  : %s\n"
                "│  trust output : %s\n"
                "│  tool calls   : %s\n"
                "│  response     : %s\n"
                "└─────────────────────────────────────────────────────────────────",
                session_id,
                model_used,
                input_tokens,
                output_tokens,
                latency_ms,
                _trust_action(trust_in),
                _trust_action(trust_out),
                tool_calls_str,
                response_preview,
            )

            logger.info(
                "observability_layer.turn_event",
                extra={
                    "operation": "console_logger.emit_turn",
                    "status": "success",
                    "session_id": session_id,
                    "model_used": model_used,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                    "timestamp_ms": _get("timestamp_ms", 0),
                    "tool_calls_count": len(tool_calls),
                    "trust_input_action": _trust_action(trust_in),
                    "trust_output_action": _trust_action(trust_out),
                    "emit_latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            # Observability Layer must never crash the caller
            logger.error(
                "observability_layer.emit_turn_error",
                extra={
                    "operation": "console_logger.emit_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """
        Log a discrete signal event.

        Args:
            signal_type: Label for the signal (e.g. "drop_off", "mismatch",
                         "low_confidence", "escalation_triggered").
            data:        Arbitrary key-value context for the signal.

        Never raises — all exceptions are caught and logged internally.
        """
        try:
            if signal_type is None:
                logger.warning(
                    "observability_layer.emit_signal_skipped",
                    extra={
                        "operation": "console_logger.emit_signal",
                        "status": "skipped",
                        "reason": "signal_type is None",
                    },
                )
                return

            logger.info(
                "observability_layer.signal_event",
                extra={
                    "operation": "console_logger.emit_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "data": data or {},
                },
            )

        except Exception as e:
            logger.error(
                "observability_layer.emit_signal_error",
                extra={
                    "operation": "console_logger.emit_signal",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _trust_action(trust_result: Any) -> str:
    """Extract action string from a TrustCheckResult dataclass or dict."""
    if trust_result is None:
        return "unknown"
    if isinstance(trust_result, dict):
        return trust_result.get("action", "unknown")
    return getattr(trust_result, "action", "unknown")
