"""LatencyObserverProcessor — passive Pipecat FrameProcessor that captures
per-turn latency markers and writes one JSONL row per turn.

The observer sits in the pipeline (typically just before transport.output)
and watches frames go past:

  UserStartedSpeakingFrame  → t_user_start_ms
  UserStoppedSpeakingFrame  → t_user_stop_ms
  TTSAudioRawFrame (first)  → t_bot_start_ms  (drives ttft_ms)
  TTSAudioRawFrame (rest)   → accumulate inter-chunk gaps for tpot_ms
  BotStoppedSpeakingFrame   → t_bot_stop_ms, finalise turn, write JSONL

All frames are pushed downstream unchanged.

TODO (open question, resolved on first call): capture token usage from
OpenAI's response.done event. Hook depends on what Pipecat surfaces.
Until verified, token-usage fields are written as 0 and cost_usd as 0.0.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pricing import compute_turn_cost

logger = logging.getLogger(__name__)


def _ms() -> float:
    """Return current monotonic time in milliseconds."""
    return time.monotonic() * 1000.0


class LatencyObserverProcessor(FrameProcessor):
    """Passive Pipecat processor that captures per-turn metrics and writes JSONL.

    Sits in the Pipecat pipeline and observes the standard speaking-lifecycle
    frames (UserStartedSpeakingFrame → UserStoppedSpeakingFrame →
    TTSAudioRawFrame(s) → BotStoppedSpeakingFrame) to derive latency metrics.
    One JSONL row is appended to ``out_path`` at the end of each turn.
    Frames are always forwarded downstream unchanged.

    Args:
        call_sid: Vobiz call identifier (goes into every JSONL row).
        out_path: Path to the per-call JSONL file.
        model: OpenAI model name (recorded for context).
        voice: OpenAI voice name (recorded for context).
        language: Configured input transcription language (recorded for context).
    """

    def __init__(
        self,
        *,
        call_sid: str,
        out_path: Path,
        model: str,
        voice: str,
        language: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._call_sid = call_sid
        self._out_path = out_path
        self._model = model
        self._voice = voice
        self._language = language

        self._turn_idx = 0
        self._cur: dict[str, Any] = {}
        self._chunk_times: list[float] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Observe frame, update latency state, and push frame downstream unchanged.

        Intercepts the four lifecycle frame types to record timestamps; all
        other frames are passed through without side-effects. When
        BotStoppedSpeakingFrame arrives the accumulated turn state is
        finalised and written to the JSONL file.

        Args:
            frame: The Pipecat frame to process.
            direction: The direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            # Pipecat's VAD can fire UserStartedSpeakingFrame multiple times
            # within one real conversational turn (the user pauses briefly
            # mid-sentence). Only start a new turn when _cur is empty —
            # i.e., the previous turn's BotStoppedSpeakingFrame has arrived.
            # Otherwise we'd overwrite an in-flight turn's timestamps and
            # produce negative ttft_ms readings.
            if not self._cur:
                self._turn_idx += 1
                self._cur = {
                    "call_sid": self._call_sid,
                    "turn": self._turn_idx,
                    "model": self._model,
                    "voice": self._voice,
                    "language": self._language,
                    "t_user_start_ms": _ms(),
                }
                self._chunk_times = []

        elif isinstance(frame, UserStoppedSpeakingFrame) and self._cur:
            # Lock t_user_stop_ms once the bot has started replying.
            # A late UserStoppedSpeakingFrame from local Silero VAD
            # (which can lag OpenAI's server VAD on long utterances)
            # would otherwise overwrite with a later timestamp and
            # produce negative ttft_ms readings.
            if "t_bot_start_ms" not in self._cur:
                self._cur["t_user_stop_ms"] = _ms()

        elif isinstance(frame, TTSAudioRawFrame) and self._cur:
            now = _ms()
            self._chunk_times.append(now)
            if "t_bot_start_ms" not in self._cur:
                self._cur["t_bot_start_ms"] = now

        elif isinstance(frame, BotStoppedSpeakingFrame) and self._cur:
            self._cur["t_bot_stop_ms"] = _ms()
            self._finalise_and_write()
            self._cur = {}
            self._chunk_times = []

        await self.push_frame(frame, direction)

    def _finalise_and_write(self) -> None:
        """Compute derived latency metrics and append one JSONL row to out_path.

        Reads accumulated timestamps from ``self._cur`` and ``self._chunk_times``,
        derives all latency fields (ttft_ms, tpot_ms, etc.), fills token-usage
        placeholders, computes cost, and writes the row. Errors are logged but
        not re-raised so a write failure never kills the pipeline.
        """
        c = self._cur

        t_us = c.get("t_user_start_ms")
        t_uo = c.get("t_user_stop_ms")
        t_bs = c.get("t_bot_start_ms")
        t_bo = c.get("t_bot_stop_ms")

        if t_uo is not None and t_bs is not None:
            c["ttft_ms"] = round(t_bs - t_uo, 1)
        if t_us is not None and t_bs is not None:
            c["silence_to_ttft_ms"] = round(t_bs - t_us, 1)
        if t_uo is not None and t_bo is not None:
            c["total_response_ms"] = round(t_bo - t_uo, 1)
        if t_bs is not None and t_bo is not None:
            c["bot_speaking_ms"] = round(t_bo - t_bs, 1)
        if t_us is not None and t_uo is not None:
            c["user_speech_duration_ms"] = round(t_uo - t_us, 1)

        if len(self._chunk_times) >= 2:
            gaps = [
                self._chunk_times[i] - self._chunk_times[i - 1]
                for i in range(1, len(self._chunk_times))
            ]
            c["tpot_ms"] = round(sum(gaps) / len(gaps), 1)
        else:
            c["tpot_ms"] = None

        for field in (
            "input_text_tokens", "input_audio_tokens", "input_cached_tokens",
            "output_text_tokens", "output_audio_tokens",
        ):
            c.setdefault(field, 0)
        c["cost_usd"] = round(compute_turn_cost(c), 8)

        try:
            self._out_path.parent.mkdir(parents=True, exist_ok=True)
            with self._out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
                f.flush()
            logger.info(
                "latency_observer.turn_written",
                extra={
                    "operation": "latency_observer.write",
                    "status": "success",
                    "call_sid": self._call_sid,
                    "turn": c["turn"],
                    "ttft_ms": c.get("ttft_ms"),
                },
            )
        except Exception as exc:
            logger.error(
                "latency_observer.write_failed",
                extra={
                    "operation": "latency_observer.write",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
