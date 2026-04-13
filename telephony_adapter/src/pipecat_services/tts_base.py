"""
telephony_adapter/src/pipecat_services/tts_base.py

TTSServiceBase — DPG abstract interface for text-to-speech services.

Pipecat-independent. Concrete implementations may inherit from both this
class and a Pipecat TTS base, keeping Pipecat as an implementation detail.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class TTSServiceBase(ABC):
    """Abstract interface for all DPG text-to-speech service implementations.

    Defines the minimal contract for synthesising text to raw PCM16 audio
    chunks. Concrete classes are free to use any underlying framework
    (Pipecat, raw HTTP, SSE) as an implementation detail.
    """

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesise text to PCM16 audio chunks at 8000 Hz.

        Args:
            text: Text to synthesise. Must not be empty.

        Yields:
            Raw PCM16 bytes chunks. Each chunk is a variable-length segment
            of 16-bit signed integer samples at 8000 Hz mono. Yields nothing
            if synthesis fails (logs the error and returns cleanly).
        """
