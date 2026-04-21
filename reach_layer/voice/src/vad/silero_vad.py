"""
telephony_adapter/src/vad/silero_vad.py

SileroVADWrapper — config-driven factory for Pipecat's SileroVADAnalyzer.

All VAD parameters (stop_secs, min_volume, confidence, start_secs,
smoothing_factor) are read from telephony_adapter.vad config. None are
hardcoded. Defaults match values tuned for 8 kHz telephony audio.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from src.vad.vad_base import VADAnalyzerBase

logger = logging.getLogger(__name__)


class SileroVADWrapper(VADAnalyzerBase):
    """Creates a SileroVADAnalyzer configured from the domain YAML.

    Reads all parameters from telephony_adapter.vad in the config dict.
    Falls back to telephony-tuned defaults if keys are absent.
    """

    def create_analyzer(self, config: dict) -> SileroVADAnalyzer:
        """Instantiate SileroVADAnalyzer with config-driven parameters.

        Args:
            config: Full merged config dict. Reads telephony_adapter.vad section.

        Returns:
            Configured SileroVADAnalyzer instance.
        """
        if config is None:
            raise ValueError("config must be a dict, got None")
        vad_cfg = config.get("telephony_adapter", {}).get("vad", {})
        # Hardcoded fallbacks align with dev-kit/dpg/reach_layer.yaml defaults
        # (GH-152 follow-up). Older, looser values caused noise-triggered VAD
        # false positives that flushed TTS via the UserTurnProcessor →
        # InterruptionFrame path.
        stop_secs = float(vad_cfg.get("stop_secs", 0.4))
        min_volume = float(vad_cfg.get("min_volume", 0.7))
        confidence = float(vad_cfg.get("confidence", 0.75))
        start_secs = float(vad_cfg.get("start_secs", 0.25))
        smoothing_factor = float(vad_cfg.get("smoothing_factor", 0.1))

        analyzer = SileroVADAnalyzer(
            params=VADParams(
                stop_secs=stop_secs,
                min_volume=min_volume,
                confidence=confidence,
                start_secs=start_secs,
            )
        )
        # smoothing_factor is not a VADParams constructor argument in Pipecat;
        # it must be set directly on the analyzer instance after construction.
        # Wrapped in try/except so a Pipecat upgrade that removes the attribute
        # degrades gracefully (logs a warning) rather than crashing at startup.
        try:
            analyzer._smoothing_factor = smoothing_factor
        except AttributeError:
            logger.warning(
                "silero_vad.smoothing_factor_unsupported",
                extra={
                    "operation": "silero_vad.create_analyzer",
                    "status": "skipped",
                    "error": "Pipecat SileroVADAnalyzer no longer exposes _smoothing_factor",
                },
            )

        logger.info(
            "silero_vad.created",
            extra={
                "operation": "silero_vad.create_analyzer",
                "status": "success",
                "stop_secs": stop_secs,
                "min_volume": min_volume,
                "confidence": confidence,
            },
        )
        return analyzer
