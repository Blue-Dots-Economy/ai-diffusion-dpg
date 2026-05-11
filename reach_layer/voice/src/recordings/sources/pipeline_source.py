"""PipelineRecordingSource — Pipecat-tap based audio capture.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import logging
import time

from src.pipecat_services.recording_tap import RecordingTapProcessor
from src.recordings.manager_base import RecordingPayload
from src.recordings.sources.source_base import RecordingSourceBase

logger = logging.getLogger(__name__)


class PipelineRecordingSource(RecordingSourceBase):
    """Captures audio via a RecordingTapProcessor spliced into the call pipeline.

    The tap is inactive at construction time; begin() activates it and end()
    finalises the WAV and returns the in-memory payload.
    """

    def __init__(self, sample_rate: int) -> None:
        """Initialise the pipeline recording source.

        Args:
            sample_rate: PCM sample rate in Hz (e.g. 8000, 16000).
        """
        self._processor = RecordingTapProcessor(sample_rate=sample_rate)
        self._sample_rate = sample_rate
        self._started_ts: float = 0.0

    @property
    def pipeline_processors(self) -> list:
        """Return the single tap processor to inject into the Pipecat pipeline.

        Returns:
            List containing the RecordingTapProcessor instance.
        """
        return [self._processor]

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        """Activate the tap processor and begin audio capture.

        Args:
            call_sid: Telephony platform call SID.
            vobiz_call_id: Internal Vobiz call identifier (unused for pipeline source).
        """
        self._started_ts = time.time()
        self._processor.activate()
        logger.info(
            "pipeline_source_begin",
            extra={
                "operation": "pipeline_source.begin",
                "status": "success",
                "call_sid": call_sid,
                "sample_rate": self._sample_rate,
            },
        )

    async def end(self) -> RecordingPayload:
        """Finalise the WAV and return the in-memory audio payload.

        Returns:
            RecordingPayload with bytes_data containing the WAV-encoded audio.
        """
        self._processor.close()
        duration_ms = int((time.time() - self._started_ts) * 1000)
        logger.info(
            "pipeline_source_end",
            extra={
                "operation": "pipeline_source.end",
                "status": "success",
                "latency_ms": duration_ms,
            },
        )
        return RecordingPayload(bytes_data=self._processor.buffer_value)
