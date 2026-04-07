"""
agent_core/interfaces/observability_layer.py

Contract that Agent Core requires from the Observability Layer DPG.
emit_turn() is always called asynchronously — after the response is delivered.
Implementations must be non-blocking. Any blocking I/O must be done in a
background thread or queue inside the implementation, not in this call.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.models import TurnEvent


class ObservabilityLayerBase(ABC):

    @abstractmethod
    def emit_turn(self, event: TurnEvent) -> None:
        """
        Record turn-level observability data for audit, quality evaluation,
        and feedback signal collection.

        Contract:
        - Must not block the caller.
        - Must not raise — swallow and log any internal errors internally.
        - Called from a daemon thread inside orchestrator._post_turn().
        """

    @abstractmethod
    def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """
        Record a discrete signal event (e.g. drop_off, mismatch, feedback).

        Args:
            signal_type: Label for the signal (e.g. "drop_off", "mismatch",
                         "low_confidence", "escalation_triggered").
            data:        Arbitrary key-value context for the signal.

        Contract:
        - Must not block the caller.
        - Must not raise — swallow and log any internal errors internally.
        """
