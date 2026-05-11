"""RecordingTapProcessor — passive Pipecat processor that writes a single WAV.

Adapted from reach_layer/voice/src/pipecat_services/recording_tap.py to
handle the case where caller and bot audio arrive at different sample
rates (e.g. OpenAI Realtime emits at 24 kHz while Pipecat's pipeline
runs at 16 kHz). On each audio frame we resample to the configured
target rate via numpy linear interpolation before writing, so the output
is a single WAV file that plays both directions at correct speed.

Production's tap can use a fixed sample rate without resampling because
its pipeline is internally uniform (TTS provider runs at the pipeline
rate). We can't get that with OpenAI Realtime, so we resample at the
recording write step only — the pipeline itself is untouched.
"""
from __future__ import annotations

import io
import logging
import time
import wave
from typing import IO, Optional

import numpy as np

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


# When OutputAudioRawFrame arrives, treat the bot as "actively speaking" for
# this many milliseconds. Caller (InputAudioRawFrame) frames received while
# the bot is active are dropped from the recording — otherwise the caller's
# continuous silent audio (Vobiz streams ~50 fps regardless of whether the
# user is speaking) gets concatenated AFTER the bot's audio in the mono WAV
# and you hear ~10 seconds of blank trailing each bot reply.
_BOT_ACTIVE_WINDOW_MS = 300


def _resample_pcm16(audio_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample PCM16 mono audio via linear interpolation.

    Quality is fine for telephony-band speech (sub-4 kHz signal content) — the
    interpolation artefacts sit above the speech band and aren't perceptible
    by ear. Not appropriate for music or wideband fidelity work. For our
    verification recording it's the right trade — single dep (numpy, already
    a transitive Pipecat dep), tiny code, no listenable artefacts.

    Args:
        audio_bytes: Raw PCM16 mono samples.
        src_rate: Source sample rate in Hz.
        dst_rate: Target sample rate in Hz.

    Returns:
        Resampled PCM16 mono samples as bytes.
    """
    if src_rate == dst_rate or not audio_bytes:
        return audio_bytes
    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    if len(samples) == 0:
        return audio_bytes
    n_out = int(round(len(samples) * dst_rate / src_rate))
    if n_out <= 0:
        return b""
    indices = np.linspace(0, len(samples) - 1, n_out)
    resampled = np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)
    return resampled.tobytes()


class RecordingTapProcessor(FrameProcessor):
    """Captures inbound and outbound audio frames into a single WAV buffer.

    Frames may arrive at different sample rates (caller audio at the pipeline
    rate, bot audio at OpenAI Realtime's native 24 kHz). Each frame's audio
    bytes are resampled to ``target_sample_rate`` before writing, so the
    output WAV is uniform and plays both directions at correct speed.

    Inactive by default; call activate() to begin capturing, close() to
    finalise the WAV header and stop further writes.
    """

    def __init__(self, target_sample_rate: int, sink: Optional[IO[bytes]] = None) -> None:
        """Initialise the tap processor.

        Args:
            target_sample_rate: PCM sample rate (Hz) the output WAV will be
                encoded at. All incoming frames are resampled to this rate
                before being written.
            sink: Writable binary stream for WAV output; defaults to an
                in-memory BytesIO.
        """
        super().__init__()
        self._target_sample_rate = int(target_sample_rate)
        self._sink: IO[bytes] = sink if sink is not None else io.BytesIO()
        self._wav: Optional[wave.Wave_write] = None
        self._active: bool = False
        self._closed: bool = False
        # Wall-clock (monotonic ms) until which the bot is considered actively
        # speaking. Used to suppress caller frames during bot speech so the
        # mono WAV doesn't concatenate parallel streams into double-length.
        self._bot_active_until_ms: float = 0.0

    def activate(self) -> None:
        """Start capturing audio frames into the WAV sink. Idempotent."""
        if self._closed:
            return
        if self._wav is None:
            self._wav = wave.open(self._sink, "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)  # 16-bit PCM
            self._wav.setframerate(self._target_sample_rate)
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
        """Return the accumulated WAV bytes from the in-memory sink."""
        if hasattr(self._sink, "getvalue"):
            return self._sink.getvalue()  # type: ignore[no-any-return]
        return b""

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        """Intercept audio frames, resample to the target rate, write to WAV.

        Args:
            frame: Any Pipecat frame; only InputAudioRawFrame and
                OutputAudioRawFrame are captured; all frames forwarded unchanged.
            direction: Pipeline direction (upstream / downstream); passed through.
        """
        # FrameProcessor.process_frame() handles StartFrame / EndFrame /
        # CancelFrame bookkeeping. Without this super call every subsequent
        # frame is rejected with "Trying to process X but StartFrame not
        # received yet" and downstream push_frame() is dropped.
        await super().process_frame(frame, direction)
        if self._active and self._wav is not None and not self._closed:
            now_ms = time.monotonic() * 1000.0
            should_write = False
            if isinstance(frame, OutputAudioRawFrame):
                # Bot audio always writes; extend the "bot active" window so
                # caller frames received in the next _BOT_ACTIVE_WINDOW_MS
                # are skipped.
                should_write = True
                self._bot_active_until_ms = now_ms + _BOT_ACTIVE_WINDOW_MS
            elif isinstance(frame, InputAudioRawFrame):
                # Caller audio writes only when bot isn't currently speaking.
                # Otherwise the WAV gets caller-silence-in-parallel-with-bot
                # appended, doubling the playback length and producing the
                # "blank after each turn" artefact.
                should_write = now_ms >= self._bot_active_until_ms

            if should_write:
                try:
                    audio = _resample_pcm16(
                        frame.audio,
                        int(frame.sample_rate),
                        self._target_sample_rate,
                    )
                    self._wav.writeframes(audio)
                except Exception as exc:
                    logger.warning(
                        "recording_tap_write_failed",
                        extra={
                            "operation": "recording_tap.write",
                            "status": "failure",
                            "frame_rate": getattr(frame, "sample_rate", None),
                            "target_rate": self._target_sample_rate,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
        await self.push_frame(frame, direction)
