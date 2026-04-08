"""
telephony_adapter/src/base.py

TelephonyAdapterBase — abstract interface for the telephony channel adapter.
All concrete adapter implementations inherit from this class.
Belongs to the Reach Layer channel family in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class TelephonyError(Exception):
    """Raised when a telephony adapter operation fails unrecoverably."""


@dataclass
class TelephonyTurnInput:
    """Normalised inbound turn from a telephone call.

    Extends the Reach Layer TurnInput concept with telephony-specific metadata.
    """

    session_id: str
    call_sid: str
    caller_id: str
    user_message: str
    channel: str
    timestamp_ms: int
    user_id: Optional[str] = None


@dataclass
class TelephonyTurnResult:
    """Normalised outbound response for a telephone call turn."""

    session_id: str
    call_sid: str
    response_text: str
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0


class TelephonyAdapterBase(ABC):
    """Abstract base class for telephony channel adapters.

    Defines the lifecycle interface every concrete adapter must implement:
    pipeline setup, turn processing, and teardown.
    """

    @abstractmethod
    async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
        """Handle the full lifecycle of an inbound call over a WebSocket.

        Args:
            call_sid: Unique identifier for this call from the telephony provider.
            caller_id: Caller's phone number (opaque — never log directly).
            websocket: Active WebSocket connection from the telephony provider.

        Raises:
            TelephonyError: If the pipeline cannot be established.
        """

    @abstractmethod
    async def teardown(self, call_sid: str) -> None:
        """Clean up resources for a completed or dropped call.

        Args:
            call_sid: The call SID whose resources should be released.
        """
