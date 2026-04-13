"""
telephony_adapter/src/vad/vad_base.py

VADAnalyzerBase — DPG abstract interface for voice activity detection.

Operator-agnostic: any VAD implementation (Silero, WebRTC, cloud) works
with any telephony operator. Concrete implementations return a Pipecat
VADAnalyzer instance configured from the domain YAML.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VADAnalyzerBase(ABC):
    """Abstract interface for all DPG voice activity detection implementations.

    Defines a factory method that produces a configured Pipecat VADAnalyzer
    from the domain config. Keeping the factory pattern here ensures all
    VAD parameters are config-driven and none are hardcoded in bot.py.
    """

    @abstractmethod
    def create_analyzer(self, config: dict) -> Any:
        """Instantiate and return a configured Pipecat VADAnalyzer.

        Args:
            config: Full merged config dict. Reads telephony_adapter.vad section.
                Must not be None.

        Returns:
            Configured Pipecat VADAnalyzer ready to pass to VADProcessor.

        Raises:
            ValueError: If config is None.
        """
