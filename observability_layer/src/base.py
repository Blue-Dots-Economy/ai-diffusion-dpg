"""
Abstract base class for the Observability Layer DPG block.

All implementations must honour the contract: never block, never raise.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ObservabilityLayerBase(ABC):
    """Abstract base class for the Observability Layer.

    Implementations must be non-blocking. Any I/O must happen in a
    background thread or queue inside the implementation — never in these calls.
    """

    @abstractmethod
    def emit_turn(self, event: Any) -> None:
        """Process a completed turn event for observability.

        Accepts a TurnEvent dataclass or a plain dict with equivalent fields.
        Must never block or raise.

        Args:
            event: TurnEvent dataclass or dict. None is silently ignored.
        """

    @abstractmethod
    def emit_signal(self, signal_type: str, data: dict) -> None:
        """Process a discrete signal event (e.g. drop_off, mismatch).

        Must never block or raise.

        Args:
            signal_type: Label for the signal. None is silently ignored.
            data: Arbitrary key-value context dict.
        """
