"""
OtelObservabilityLayer — concrete implementation of ObservabilityLayerBase.

Replaces ConsoleLogger. Processes TurnEvents through the OutcomeTracker
(which maps tool calls to OTel metric increments) and emits structured logs.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from base import ObservabilityLayerBase
from dpg_telemetry import get_meter, get_tracer
from outcome_tracker import OutcomeTracker
from schema.config import ObservabilityConfig

logger = logging.getLogger(__name__)


class OtelObservabilityLayer(ObservabilityLayerBase):
    """Observability Layer implementation backed by OpenTelemetry.

    Processes incoming TurnEvents via OutcomeTracker to increment
    domain-defined OTel metrics. Emits structured logs for all signals.
    Never blocks or raises.

    Args:
        config: Full merged config dict. Reads observability section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._obs_config = ObservabilityConfig.from_config(config)
        self._tracer = get_tracer(__name__)
        self._meter = get_meter(__name__)
        self._outcome_tracker = OutcomeTracker(self._obs_config, self._meter)

        logger.info(
            "observability_layer.init",
            extra={
                "operation": "otel_observability_layer.init",
                "status": "success",
                "domain": self._obs_config.domain,
            },
        )

    def emit_turn(self, event: Any) -> None:
        """Process a completed turn event.

        Passes the event to OutcomeTracker for metric updates and emits
        structured log of turn metadata. Never blocks or raises.

        Args:
            event: TurnEvent dataclass or dict. None is silently ignored.
        """
        if event is None:
            return

        start = time.time()
        try:
            def _get(key: str, default: Any = None) -> Any:
                if isinstance(event, dict):
                    return event.get(key, default)
                return getattr(event, key, default)

            trace_id = _get("trace_id", "")
            session_id = _get("session_id", "")

            if not trace_id:
                logger.warning(
                    "observability_layer.emit_turn_no_trace_id",
                    extra={
                        "operation": "otel_observability_layer.emit_turn",
                        "status": "skipped_span_attachment",
                        "session_id": session_id,
                    },
                )

            self._outcome_tracker.process(event)

            logger.info(
                "observability_layer.turn_event",
                extra={
                    "operation": "otel_observability_layer.emit_turn",
                    "status": "success",
                    "session_id": _get("session_id", ""),
                    "turn_id": _get("turn_id", ""),
                    "model_used": _get("model_used", ""),
                    "input_tokens": _get("input_tokens", 0),
                    "output_tokens": _get("output_tokens", 0),
                    "latency_ms": _get("latency_ms", 0),
                    "emit_latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "observability_layer.emit_turn_error",
                extra={
                    "operation": "otel_observability_layer.emit_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def emit_signal(self, signal_type: str, data: dict) -> None:
        """Process a discrete signal event. Never blocks or raises.

        Args:
            signal_type: Signal label (e.g. "drop_off"). None is silently ignored.
            data: Arbitrary context dict.
        """
        if signal_type is None:
            return

        try:
            logger.info(
                "observability_layer.signal_event",
                extra={
                    "operation": "otel_observability_layer.emit_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "data": data or {},
                },
            )

        except Exception as e:
            logger.error(
                "observability_layer.emit_signal_error",
                extra={
                    "operation": "otel_observability_layer.emit_signal",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
