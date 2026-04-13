"""
telephony_adapter/src/pipecat_services/stt_base.py

STTServiceBase — DPG abstract interface for speech-to-text services.

Pipecat-independent. Concrete implementations may inherit from both this
class and a Pipecat STT base, keeping Pipecat as an implementation detail.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class STTServiceBase(ABC):
    """Abstract interface for all DPG speech-to-text service implementations.

    Defines the minimal contract for transcribing a single utterance from raw
    audio bytes to text. Concrete classes are free to use any underlying
    framework (Pipecat, raw HTTP, WebSocket) as an implementation detail.
    """

    @abstractmethod
    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete utterance to text.

        Args:
            audio: Complete WAV file bytes (PCM16, mono) for one utterance,
                assembled by the caller after VAD detects end-of-speech.

        Returns:
            Transcribed text string, or None if the audio is silent,
            unintelligible, or below the service's confidence threshold.

        Raises:
            STTError: If transcription fails after all retries.
        """
