"""RecordingSourceBase — ABC for audio capture mechanisms.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.recordings.manager_base import RecordingPayload


class RecordingSourceBase(ABC):
    """Abstract base class for audio capture sources.

    Concrete implementations provide either in-pipeline tap capture
    (PipelineRecordingSource) or Vobiz-side server recording (VobizRecordingSource).
    """

    @abstractmethod
    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        """Start capturing audio for a call.

        Args:
            call_sid: Telephony platform call SID.
            vobiz_call_id: Internal Vobiz call identifier (may be empty string for
                pipeline-only sources).
        """

    @abstractmethod
    async def end(self) -> RecordingPayload:
        """Stop capturing and return the audio payload.

        Returns:
            RecordingPayload with either bytes_data (pipeline) or fetch_url (vobiz).
        """

    @property
    @abstractmethod
    def pipeline_processors(self) -> list:
        """Pipecat processors to splice into the call pipeline.

        Returns:
            List of FrameProcessor instances; empty list for non-pipeline sources.
        """
