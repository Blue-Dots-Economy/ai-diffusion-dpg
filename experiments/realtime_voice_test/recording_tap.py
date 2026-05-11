"""RecordingTapProcessor — passive Pipecat processor that writes per-direction WAVs.

Based on reach_layer/voice/src/pipecat_services/recording_tap.py — but adapted
for OpenAI Realtime, where caller audio (16 kHz at the tap) and bot audio
(24 kHz, OpenAI's native rate) arrive at different sample rates. Production's
single-fixed-rate WAV plays the bot voice at the wrong speed.

We solve this by writing TWO separate WAV files, one per direction, each at
the native sample rate of the frames it contains. The pipeline's transport
layer resamples for Vobiz output independently; both copies on disk are
self-described and play correctly in any media player.
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
    """Captures inbound and outbound audio frames into TWO WAV buffers.

    Inactive by default; call activate() to begin capturing, close() to
    finalise both WAV headers and stop further writes. Inbound and outbound
    audio go to separate sinks because their sample rates can differ — each
    WAV is initialised lazily on the first frame of that direction and uses
    `frame.sample_rate` as the WAV's frame rate.
    """

    def __init__(self) -> None:
        """Initialise the tap with two empty BytesIO sinks (one per direction)."""
        super().__init__()
        self._input_sink: IO[bytes] = io.BytesIO()
        self._output_sink: IO[bytes] = io.BytesIO()
        self._input_wav: Optional[wave.Wave_write] = None
        self._output_wav: Optional[wave.Wave_write] = None
        self._active: bool = False
        self._closed: bool = False

    def activate(self) -> None:
        """Start capturing audio frames. Idempotent."""
        if self._closed:
            return
        self._active = True

    def deactivate(self) -> None:
        """Pause capturing without finalising WAV headers."""
        self._active = False

    def close(self) -> None:
        """Finalise both WAV headers and permanently stop capturing."""
        self.deactivate()
        for direction, wav in (("input", self._input_wav), ("output", self._output_wav)):
            if wav is None or self._closed:
                continue
            try:
                wav.close()
            except Exception as exc:
                logger.warning(
                    "recording_tap_close_failed",
                    extra={
                        "operation": "recording_tap.close",
                        "status": "failure",
                        "direction": direction,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        self._closed = True

    @property
    def input_buffer_value(self) -> bytes:
        """WAV bytes for caller (inbound) audio."""
        if hasattr(self._input_sink, "getvalue"):
            return self._input_sink.getvalue()  # type: ignore[no-any-return]
        return b""

    @property
    def output_buffer_value(self) -> bytes:
        """WAV bytes for bot (outbound) audio."""
        if hasattr(self._output_sink, "getvalue"):
            return self._output_sink.getvalue()  # type: ignore[no-any-return]
        return b""

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        """Intercept audio frames and write each direction to its own WAV."""
        # Required: FrameProcessor.process_frame() handles StartFrame/EndFrame
        # bookkeeping. Without this, subsequent frames are rejected.
        await super().process_frame(frame, direction)

        if self._active and not self._closed:
            if isinstance(frame, InputAudioRawFrame):
                self._write(frame, is_input=True)
            elif isinstance(frame, OutputAudioRawFrame):
                self._write(frame, is_input=False)

        await self.push_frame(frame, direction)

    def _write(self, frame, *, is_input: bool) -> None:
        """Lazy-init the correct WAV (using the frame's native sample rate) and write."""
        wav_attr = "_input_wav" if is_input else "_output_wav"
        sink_attr = "_input_sink" if is_input else "_output_sink"
        wav = getattr(self, wav_attr)
        if wav is None:
            sink = getattr(self, sink_attr)
            try:
                wav = wave.open(sink, "wb")
                wav.setnchannels(1)
                wav.setsampwidth(2)  # 16-bit PCM
                wav.setframerate(int(frame.sample_rate))
            except Exception as exc:
                logger.warning(
                    "recording_tap_open_failed",
                    extra={
                        "operation": "recording_tap.open",
                        "status": "failure",
                        "direction": "input" if is_input else "output",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                return
            setattr(self, wav_attr, wav)
            logger.info(
                "recording_tap_opened",
                extra={
                    "operation": "recording_tap.open",
                    "status": "success",
                    "direction": "input" if is_input else "output",
                    "sample_rate": int(frame.sample_rate),
                },
            )
        try:
            wav.writeframes(frame.audio)
        except Exception as exc:
            logger.warning(
                "recording_tap_write_failed",
                extra={
                    "operation": "recording_tap.write",
                    "status": "failure",
                    "direction": "input" if is_input else "output",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
