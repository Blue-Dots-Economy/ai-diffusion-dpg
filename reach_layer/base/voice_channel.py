"""
reach_layer/base/voice_channel.py

VoiceChannelBase — abstract base for voice/telephony channel adapters.

Extends ReachLayerBase with voice-specific methods for barge-in handling
and VAD event processing. All voice channels (Vobiz, SIP, etc.) extend
this class instead of ReachLayerBase directly.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass

from .reach_layer_base import ReachLayerBase


@dataclass
class VADEvent:
    """Voice Activity Detection event from the audio pipeline.

    Attributes:
        event_type: One of "speech_start", "speech_end", "silence_detected".
        session_id: Session this event belongs to.
        timestamp_ms: Timestamp of the event in milliseconds.
        duration_ms: Duration of the speech/silence segment in ms (if applicable).
    """

    event_type: str
    session_id: str
    timestamp_ms: int = 0
    duration_ms: int = 0


class VoiceChannelBase(ReachLayerBase):
    """Abstract base for voice/telephony channels.

    Voice channels process audio streams with VAD, STT, and TTS pipelines.
    This base adds voice-specific methods for barge-in handling and VAD event
    processing on top of the ReachLayerBase HTTP communication.

    Subclasses must implement handle_barge_in(), on_vad_event(), and the
    lifecycle methods from ReachLayerBase.
    """

    @abstractmethod
    async def handle_barge_in(self, session_id: str) -> None:
        """Interrupt TTS playback and cancel the active turn on barge-in.

        Implementations must:
            1. Stop TTS audio queue
            2. Call cancel_turn() to interrupt Agent Core
            3. Prepare to accept new input segments

        Args:
            session_id: Session where barge-in occurred.
        """

    @abstractmethod
    async def on_vad_event(self, session_id: str, event: VADEvent) -> None:
        """Handle a Voice Activity Detection signal.

        Args:
            session_id: Session this event belongs to.
            event: VAD event with type, timestamp, and optional duration.
        """
