"""
reach_layer/voice/src/pipecat_services/latency_observer.py

LatencyObserverProcessor — captures per-turn latency markers for the
production voice pipeline and writes one JSONL row per turn.

Mirrors experiments/realtime_voice_test/latency_observer.py (which targets
the gpt-realtime-mini voice-native stack) so per-turn JSONL fields are
directly comparable across the two stacks. Production additionally emits
per-stage breakdowns (STT, agent/LLM, TTS first audio) because the
pipeline has discrete stages we can timestamp individually.

Spliced near the end of the pipeline (just before the recording manager
processors) so all upstream frames pass through it on their way to
transport.output().

Belongs to the Reach Layer / Voice channel in the DPG framework — added
on the experiment/realtime-voice-test branch for the production-side
half of the gpt-realtime-mini vs production-stack comparison.
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
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


def _ms() -> float:
    """Return current monotonic time in milliseconds."""
    return time.monotonic() * 1000.0


class LatencyObserverProcessor(FrameProcessor):
    """Capture per-turn latency markers and write one JSONL row per turn.

    Frame timeline observed (production pipeline):

        UserStartedSpeakingFrame   → t_user_start_ms
        UserStoppedSpeakingFrame   → t_user_stop_ms
        TranscriptionFrame (first) → t_stt_done_ms      (Raya STT finished)
        TTSSpeakFrame (first)      → t_agent_done_ms    (Agent Core + LLM finished)
        TTSAudioRawFrame (first)   → t_bot_start_ms     (Raya TTS first audio)
        TTSAudioRawFrame (subsq.)  → accumulate tpot
        BotStoppedSpeakingFrame    → t_bot_stop_ms      → finalise, write JSONL

    Derived:
        ttft_ms                 = t_bot_start − t_user_stop   (HEADLINE — same as experiment)
        total_response_ms       = t_bot_stop  − t_user_stop
        silence_to_ttft_ms      = t_bot_start − t_user_start
        bot_speaking_ms         = t_bot_stop  − t_bot_start
        user_speech_duration_ms = t_user_stop − t_user_start
        tpot_ms                 = mean inter-chunk gap across TTSAudioRawFrames
        stt_ms                  = t_stt_done   − t_user_stop  (production-only)
        agent_ms                = t_agent_done − t_stt_done   (production-only)
        tts_first_audio_ms      = t_bot_start  − t_agent_done (production-only)

    All frames are pushed downstream unchanged.

    Args:
        call_sid: Vobiz call identifier (goes into every JSONL row).
        out_path: Path to the per-call JSONL file.
        model: LLM model name (recorded for context).
        language: Configured language hint (recorded for context).
    """

    def __init__(
        self,
        *,
        call_sid: str,
        out_path: Path,
        model: str,
        language: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._call_sid = call_sid
        self._out_path = out_path
        self._model = model
        self._language = language

        self._turn_idx = 0
        self._cur: dict[str, Any] = {}
        self._chunk_times: list[float] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Observe frame, update latency state, push frame downstream unchanged."""
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            # Only start a new turn when _cur is empty (mirrors the experiment
            # observer's guard against VAD multi-fire mid-turn).
            if not self._cur:
                self._turn_idx += 1
                self._cur = {
                    "call_sid": self._call_sid,
                    "turn": self._turn_idx,
                    "stack": "production",
                    "model": self._model,
                    "language": self._language,
                    "t_user_start_ms": _ms(),
                }
                self._chunk_times = []

        elif isinstance(frame, UserStoppedSpeakingFrame) and self._cur:
            # Lock once the bot starts speaking (defensive guard from the
            # experiment observer — late VAD fires can otherwise corrupt ttft).
            if "t_bot_start_ms" not in self._cur:
                self._cur["t_user_stop_ms"] = _ms()

        elif isinstance(frame, TranscriptionFrame) and self._cur:
            # Raya STT finished. Capture the first transcription per turn.
            if "t_stt_done_ms" not in self._cur:
                self._cur["t_stt_done_ms"] = _ms()
                # Best-effort transcript capture for context (NOT used for any
                # latency math). text attribute exists on TranscriptionFrame.
                self._cur["transcript_in"] = getattr(frame, "text", "") or ""

        elif isinstance(frame, TTSSpeakFrame) and self._cur:
            # Agent Core / LLM produced text ready for TTS. First-frame timing
            # marks the end of the agent-LLM stage. In session-mode pipelines
            # this fires per-sentence; we only record the first.
            if "t_agent_done_ms" not in self._cur:
                self._cur["t_agent_done_ms"] = _ms()
            # Accumulate bot reply text (concatenate sentence frames)
            speak_text = getattr(frame, "text", "") or ""
            if speak_text:
                self._cur["transcript_out"] = (
                    self._cur.get("transcript_out", "") + speak_text
                )

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
        """Compute derived metrics and append one JSONL row."""
        c = self._cur

        t_us = c.get("t_user_start_ms")
        t_uo = c.get("t_user_stop_ms")
        t_st = c.get("t_stt_done_ms")
        t_ag = c.get("t_agent_done_ms")
        t_bs = c.get("t_bot_start_ms")
        t_bo = c.get("t_bot_stop_ms")

        # Comparable metrics (match experiment schema)
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

        # Production-only per-stage breakdown
        if t_uo is not None and t_st is not None:
            c["stt_ms"] = round(t_st - t_uo, 1)
        if t_st is not None and t_ag is not None:
            c["agent_ms"] = round(t_ag - t_st, 1)
        if t_ag is not None and t_bs is not None:
            c["tts_first_audio_ms"] = round(t_bs - t_ag, 1)

        # Write the row
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
                    "stt_ms": c.get("stt_ms"),
                    "agent_ms": c.get("agent_ms"),
                    "tts_first_audio_ms": c.get("tts_first_audio_ms"),
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
