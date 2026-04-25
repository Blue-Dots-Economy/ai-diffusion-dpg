"""
reach_layer/voice/src/pipecat_services/vad_observer.py

VADObserverProcessor — passive structured-log tap for the voice pipeline.

Logs each VAD / user-turn / transcription / interruption frame as it passes
through, and exposes the most-recent timestamps so a sibling heartbeat task
can detect a stalled pipeline (GH-238). Never modifies frames; never blocks
their forward propagation.
Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class VADObserverProcessor(FrameProcessor):
    """Logs VAD / user-turn / transcription / interruption frames passively.

    Insert anywhere downstream of VADProcessor, UserTurnProcessor, and STT
    so all three frame families are visible. The processor never mutates or
    drops frames — every frame is forwarded unchanged in its original
    direction immediately after the log call.

    Maintains monotonic-clock timestamps of the most recent occurrence of
    each tracked frame family so a sibling heartbeat task can compute
    seconds-since-last-X without subscribing to frames itself.

    Args:
        session_id: Stable session identifier included in every log entry
            for cross-component correlation.
        call_sid: Telephony call identifier included in every log entry.
    """

    def __init__(self, *, session_id: str, call_sid: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._call_sid = call_sid
        self._last_vad_speech_start_at: Optional[float] = None
        self._last_vad_speech_stop_at: Optional[float] = None
        self._last_user_turn_start_at: Optional[float] = None
        self._last_user_turn_stop_at: Optional[float] = None
        self._last_transcript_at: Optional[float] = None
        self._last_interruption_at: Optional[float] = None

    @property
    def last_vad_speech_start_at(self) -> Optional[float]:
        return self._last_vad_speech_start_at

    @property
    def last_vad_speech_stop_at(self) -> Optional[float]:
        return self._last_vad_speech_stop_at

    @property
    def last_user_turn_start_at(self) -> Optional[float]:
        return self._last_user_turn_start_at

    @property
    def last_user_turn_stop_at(self) -> Optional[float]:
        return self._last_user_turn_stop_at

    @property
    def last_transcript_at(self) -> Optional[float]:
        return self._last_transcript_at

    @property
    def last_interruption_at(self) -> Optional[float]:
        return self._last_interruption_at

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Log tracked frames; forward every frame unchanged.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        now = time.monotonic()

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._last_vad_speech_start_at = now
            logger.info(
                "vad_observer.speech_start",
                extra={
                    "operation": "vad_observer.speech_start",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                },
            )
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            duration_ms: Optional[int] = None
            if self._last_vad_speech_start_at is not None:
                duration_ms = int((now - self._last_vad_speech_start_at) * 1000)
            self._last_vad_speech_stop_at = now
            logger.info(
                "vad_observer.speech_stop",
                extra={
                    "operation": "vad_observer.speech_stop",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                    "duration_ms": duration_ms,
                },
            )
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._last_user_turn_start_at = now
            logger.info(
                "vad_observer.user_turn_start",
                extra={
                    "operation": "vad_observer.user_turn_start",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                },
            )
        elif isinstance(frame, UserStoppedSpeakingFrame):
            duration_ms = None
            if self._last_user_turn_start_at is not None:
                duration_ms = int((now - self._last_user_turn_start_at) * 1000)
            self._last_user_turn_stop_at = now
            logger.info(
                "vad_observer.user_turn_stop",
                extra={
                    "operation": "vad_observer.user_turn_stop",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                    "duration_ms": duration_ms,
                },
            )
        elif isinstance(frame, TranscriptionFrame):
            self._last_transcript_at = now
            logger.info(
                "vad_observer.transcript",
                extra={
                    "operation": "vad_observer.transcript",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                    "transcript_len": len(getattr(frame, "text", "") or ""),
                },
            )
        elif isinstance(frame, InterruptionFrame):
            self._last_interruption_at = now
            logger.info(
                "vad_observer.interruption",
                extra={
                    "operation": "vad_observer.interruption",
                    "status": "success",
                    "session_id": self._session_id,
                    "call_sid": self._call_sid,
                },
            )

        await self.push_frame(frame, direction)


async def run_voice_heartbeat(
    observer: VADObserverProcessor,
    *,
    session_id: str,
    call_sid: str,
    interval_s: float,
) -> None:
    """Periodically log seconds-since-last activity until cancelled (GH-238).

    Reads the observer's most-recent timestamps and emits a single structured
    log entry per tick so a stalled pipeline shows up as a steadily rising
    ``seconds_since_*`` field with no new ``vad_observer.*`` events between
    ticks. Designed to be launched with ``asyncio.create_task`` and cancelled
    on call disconnect.

    Args:
        observer: The VADObserverProcessor wired into the active pipeline.
        session_id: Session identifier included in every heartbeat entry.
        call_sid: Telephony call identifier included in every heartbeat entry.
        interval_s: Seconds between heartbeats. Must be positive.

    Raises:
        ValueError: If ``interval_s`` is not positive.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    try:
        while True:
            await asyncio.sleep(interval_s)
            now = time.monotonic()

            def _delta(ts: Optional[float]) -> Optional[float]:
                return None if ts is None else round(now - ts, 3)

            logger.info(
                "voice.heartbeat",
                extra={
                    "operation": "voice.heartbeat",
                    "status": "success",
                    "session_id": session_id,
                    "call_sid": call_sid,
                    "seconds_since_vad_speech_start": _delta(
                        observer.last_vad_speech_start_at
                    ),
                    "seconds_since_vad_speech_stop": _delta(
                        observer.last_vad_speech_stop_at
                    ),
                    "seconds_since_user_turn_start": _delta(
                        observer.last_user_turn_start_at
                    ),
                    "seconds_since_user_turn_stop": _delta(
                        observer.last_user_turn_stop_at
                    ),
                    "seconds_since_transcript": _delta(observer.last_transcript_at),
                    "seconds_since_interruption": _delta(
                        observer.last_interruption_at
                    ),
                },
            )
    except asyncio.CancelledError:
        logger.info(
            "voice.heartbeat_cancelled",
            extra={
                "operation": "voice.heartbeat",
                "status": "skipped",
                "reason": "cancelled",
                "session_id": session_id,
                "call_sid": call_sid,
            },
        )
        raise
