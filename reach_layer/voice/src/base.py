"""
reach_layer/voice/src/base.py

TelephonyAdapterBase — voice channel base class for telephony providers.
Extends ``reach_layer_base.VoiceChannelBase`` so voice adapters inherit the
shared Agent Core HTTP methods (submit_input, subscribe_events, cancel_turn)
plus voice-specific lifecycle hooks (handle_barge_in, on_vad_event).

Historical note: this used to be a standalone class in ``telephony_adapter/``
with its own parallel hierarchy (handle_call / teardown). It now plugs into
the unified Reach Layer base class tree. handle_call / teardown are kept as
additional abstract methods because the pipecat-based pipeline needs them,
but every concrete subclass must also implement the VoiceChannelBase hooks.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from reach_layer_base import VoiceChannelBase
from src.recordings.manager_base import RecordingManagerBase


class TelephonyError(Exception):
    """Raised when a telephony adapter operation fails unrecoverably."""


class STTError(Exception):
    """Raised when speech-to-text transcription fails after retries."""


class TTSError(Exception):
    """Raised when text-to-speech synthesis fails."""


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


class TelephonyAdapterBase(VoiceChannelBase):
    """Voice channel adapter base for telephony providers.

    Defines telephony-pipeline-specific lifecycle in addition to the hooks
    inherited from VoiceChannelBase. Concrete adapters (e.g. VobizAdapter)
    must implement both sets.
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

    @property
    @abstractmethod
    def recording_manager(self) -> RecordingManagerBase:
        """The RecordingManagerBase instance owning this call's recording.

        Adapters that do not support recording must return a NullRecordingManager —
        never None — so callers can dispatch unconditionally.
        """
