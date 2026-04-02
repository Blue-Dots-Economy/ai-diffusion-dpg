"""
reach_layer/src/base.py

ReachLayerBase — abstract channel adapter interface for the DPG Reach Layer block.
All concrete channel adapters (CLI, web, WhatsApp, VOIP) inherit from this class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TurnInput:
    """Normalised inbound message from any channel."""

    session_id: str
    user_message: str
    channel: str
    timestamp_ms: int
    user_id: Optional[str] = None


@dataclass
class TurnResult:
    """Normalised outbound response from Agent Core."""

    session_id: str
    response_text: str
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0


class ReachLayerBase(ABC):
    """Abstract base class for all Reach Layer channel adapters.

    Defines the two-method interface that every channel adapter must implement:
    receive() to accept inbound messages and deliver() to send outbound responses.
    """

    @abstractmethod
    def receive(self) -> TurnInput:
        """Block until an inbound message is available and return it as a TurnInput.

        Returns:
            TurnInput with session_id, user_message, channel, timestamp_ms, and
            optional user_id populated from the inbound channel.

        Raises:
            EOFError: When the channel signals end of input (e.g. stdin closed).
        """

    @abstractmethod
    def deliver(self, result: TurnResult) -> None:
        """Deliver an Agent Core response back to the user on this channel.

        Args:
            result: TurnResult containing response_text, escalation flag, and
                    session metadata. Must not be None.
        """
