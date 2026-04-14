"""
agent_core/interfaces/async_observability_layer.py

Async contract that Agent Core's stream_turn() requires from the Observability Layer DPG.
Mirror of ObservabilityLayerBase with all methods as async def.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.models import TurnEvent


class AsyncObservabilityLayerBase(ABC):

    @abstractmethod
    async def emit_turn(self, event: TurnEvent) -> None:
        """Async version of ObservabilityLayerBase.emit_turn(). See sync interface for full docs."""

    @abstractmethod
    async def emit_signal(self, signal_type: str, data: dict[str, Any]) -> None:
        """Async version of ObservabilityLayerBase.emit_signal(). See sync interface for full docs."""
