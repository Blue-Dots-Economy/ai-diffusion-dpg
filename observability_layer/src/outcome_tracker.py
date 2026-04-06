"""
OutcomeTracker — maps TurnEvent tool calls to domain lifecycle state
transitions and increments the corresponding OTel metric instruments.

Runs in the Observability Layer's emit_turn path. Must never raise.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

import logging
from typing import Any

from schema.config import InstrumentType, ObservabilityConfig

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Maps incoming TurnEvent tool calls to OTel metric increments.

    At construction OTel metric instruments are created from the domain config.
    At runtime, ``process()`` evaluates each tool call in the event against the
    lifecycle trigger rules and increments the matching counters.

    Args:
        config: Validated ObservabilityConfig containing lifecycle and metrics.
        meter: OTel Meter instance used to create and update instruments.
    """

    def __init__(self, config: ObservabilityConfig, meter: Any) -> None:
        if config is None:
            raise ValueError("config must not be None")
        if meter is None:
            raise ValueError("meter must not be None")

        self._lifecycle = config.outcomes.lifecycle
        self._counters: dict = {}
        self._gauges: dict = {}
        self._histograms: dict = {}
        # Counters that carry a "state" attribute — used for per-state increments.
        # Populated during __init__ from MetricDefinition.attributes.
        self._state_counters: dict = {}

        for m in config.outcomes.metrics:
            try:
                if m.instrument == InstrumentType.counter:
                    instrument = meter.create_counter(
                        name=m.name, description=m.description, unit=m.unit,
                    )
                    self._counters[m.name] = instrument
                    if "state" in m.attributes:
                        self._state_counters[m.name] = instrument
                elif m.instrument == InstrumentType.gauge:
                    self._gauges[m.name] = meter.create_gauge(
                        name=m.name, description=m.description, unit=m.unit,
                    )
                elif m.instrument == InstrumentType.histogram:
                    self._histograms[m.name] = meter.create_histogram(
                        name=m.name, description=m.description, unit=m.unit,
                    )
            except Exception as e:
                logger.error(
                    "outcome_tracker.instrument_create_error",
                    extra={
                        "operation": "outcome_tracker.init",
                        "status": "failure",
                        "metric_name": m.name,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    def process(self, event: Any) -> None:
        """Evaluate tool calls in a TurnEvent against lifecycle trigger rules.

        Increments OTel counters for matching tool calls. Accepts a TurnEvent
        dataclass or plain dict. Silently ignores None. Never raises.

        Args:
            event: TurnEvent dataclass or dict with tool_calls, intent, session_id.
        """
        if event is None:
            return

        try:
            def _get(key: str, default: Any = None) -> Any:
                if isinstance(event, dict):
                    return event.get(key, default)
                return getattr(event, key, default)

            tool_calls = _get("tool_calls", []) or []
            intent = _get("intent", "")
            session_id = _get("session_id", "")

            for tc in tool_calls:
                tool_name = (
                    tc.get("tool_name", "") if isinstance(tc, dict)
                    else getattr(tc, "tool_name", "")
                )
                self._evaluate_tool_call(tool_name, intent, session_id)

        except Exception as e:
            logger.error(
                "outcome_tracker.process_error",
                extra={
                    "operation": "outcome_tracker.process",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def _evaluate_tool_call(self, tool_name: str, intent: str, session_id: str) -> None:
        """Check tool_name against lifecycle rules and increment matching metrics.

        Args:
            tool_name: Name of the tool that was called.
            intent: NLU intent from the current turn.
            session_id: Current session identifier.
        """
        for state_def in self._lifecycle:
            if state_def.trigger_tool and state_def.trigger_tool == tool_name:
                # NOTE: trigger_condition evaluation is not yet implemented.
                # Currently any invocation of trigger_tool triggers the state transition.
                # Future: evaluate condition against tool call result dict.
                attrs = {"intent": intent, "state": state_def.state, "session_id": session_id}
                # Only increment counters that declare "state" as an attribute —
                # these are per-state counters. Fall back to all counters if none
                # are tagged with "state" (e.g. a config with no attribute annotations).
                target_counters = self._state_counters if self._state_counters else self._counters
                try:
                    for counter in target_counters.values():
                        counter.add(1, attrs)
                except Exception as e:
                    logger.error(
                        "outcome_tracker.increment_error",
                        extra={
                            "operation": "outcome_tracker._evaluate_tool_call",
                            "status": "failure",
                            "tool_name": tool_name,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                break
