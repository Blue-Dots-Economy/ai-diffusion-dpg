"""RecordingTapProcessor — passive Pipecat processor that writes audio to WAV.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import io
import logging
import wave
from typing import IO, Optional

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class RecordingTapProcessor(FrameProcessor):
    """Captures inbound and outbound audio frames into a WAV buffer when active.

    Inactive by default; call activate() to begin capturing, close() to finalise
    the WAV header and stop further writes.
    """

    def __init__(self, sample_rate: int, sink: Optional[IO[bytes]] = None) -> None:
        """Initialise the tap processor.

        Args:
            sample_rate: PCM sample rate in Hz (e.g. 8000, 16000).
            sink: Writable binary stream for WAV output; defaults to an in-memory
                BytesIO if not provided.
        """
        super().__init__()
        self._sample_rate = int(sample_rate)
        self._sink: IO[bytes] = sink if sink is not None else io.BytesIO()
        self._wav: Optional[wave.Wave_write] = None
        self._active: bool = False
        self._closed: bool = False

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Start capturing audio frames into the WAV sink.

        Idempotent: safe to call multiple times. No-op if already closed.
        """
        if self._closed:
            return
        if self._wav is None:
            self._wav = wave.open(self._sink, "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)  # 16-bit PCM
            self._wav.setframerate(self._sample_rate)
        self._active = True

    def deactivate(self) -> None:
        """Pause capturing without finalising the WAV header."""
        self._active = False

    def close(self) -> None:
        """Finalise the WAV header and permanently stop capturing.

        Safe to call multiple times; subsequent audio frames are silently dropped.
        """
        self.deactivate()
        if self._wav is not None and not self._closed:
            try:
                self._wav.close()
            except Exception as exc:
                logger.warning(
                    "recording_tap_close_failed",
                    extra={
                        "operation": "recording_tap.close",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        self._closed = True

    @property
    def buffer_value(self) -> bytes:
        """Return the accumulated WAV bytes from the in-memory sink.

        Returns:
            Bytes of the WAV data, or empty bytes if the sink has no getvalue().
        """
        if hasattr(self._sink, "getvalue"):
            return self._sink.getvalue()  # type: ignore[no-any-return]
        return b""

    # ------------------------------------------------------------------
    # FrameProcessor implementation
    # ------------------------------------------------------------------

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        """Intercept audio frames and write PCM data to the WAV sink when active.

        Args:
            frame: Any Pipecat frame; only InputAudioRawFrame and OutputAudioRawFrame
                are captured; all frames are forwarded unchanged.
            direction: Pipeline direction (upstream / downstream); passed through.
        """
        # FrameProcessor.process_frame() handles StartFrame / EndFrame /
        # CancelFrame bookkeeping and toggles the _started flag. Without
        # this super call every subsequent frame is rejected with
        # "Trying to process X but StartFrame not received yet" and
        # downstream push_frame() is dropped.
        await super().process_frame(frame, direction)
        if self._active and self._wav is not None and not self._closed:
            if isinstance(frame, (InputAudioRawFrame, OutputAudioRawFrame)):
                try:
                    self._wav.writeframes(frame.audio)
                except Exception as exc:
                    logger.warning(
                        "recording_tap_write_failed",
                        extra={
                            "operation": "recording_tap.write",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
        await self.push_frame(frame, direction)
