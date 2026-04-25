"""Tests for VADObserverProcessor and run_voice_heartbeat (GH-238)."""
from __future__ import annotations

import asyncio
import logging

import pytest
from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from src.pipecat_services.vad_observer import (
    VADObserverProcessor,
    run_voice_heartbeat,
)


def _make_observer() -> VADObserverProcessor:
    return VADObserverProcessor(session_id="sess-1", call_sid="call-1")


async def _run(processor: VADObserverProcessor, frame, captured: list) -> None:
    async def capture(f, direction=None):
        captured.append((f, direction))

    processor.push_frame = capture
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)


# ---------------------------------------------------------------------------
# Frame logging + state tracking
# ---------------------------------------------------------------------------

async def test_vad_speech_start_logs_and_records_timestamp(caplog):
    observer = _make_observer()
    captured = []

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, VADUserStartedSpeakingFrame(), captured)

    assert any(r.message == "vad_observer.speech_start" for r in caplog.records)
    assert observer.last_vad_speech_start_at is not None
    assert len(captured) == 1


async def test_vad_speech_stop_includes_duration_when_start_seen(caplog):
    observer = _make_observer()
    captured = []

    await _run(observer, VADUserStartedSpeakingFrame(), captured)
    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, VADUserStoppedSpeakingFrame(), captured)

    stop = next(r for r in caplog.records if r.message == "vad_observer.speech_stop")
    assert getattr(stop, "duration_ms") is not None
    assert observer.last_vad_speech_stop_at is not None


async def test_vad_speech_stop_duration_none_without_prior_start(caplog):
    observer = _make_observer()
    captured = []

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, VADUserStoppedSpeakingFrame(), captured)

    stop = next(r for r in caplog.records if r.message == "vad_observer.speech_stop")
    assert getattr(stop, "duration_ms") is None


async def test_user_turn_start_and_stop_tracked(caplog):
    observer = _make_observer()
    captured = []

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, UserStartedSpeakingFrame(), captured)
        await _run(observer, UserStoppedSpeakingFrame(), captured)

    msgs = [r.message for r in caplog.records]
    assert "vad_observer.user_turn_start" in msgs
    assert "vad_observer.user_turn_stop" in msgs
    assert observer.last_user_turn_start_at is not None
    assert observer.last_user_turn_stop_at is not None


async def test_transcription_logged_with_length(caplog):
    observer = _make_observer()
    captured = []

    frame = TranscriptionFrame(text="हलो", user_id="", timestamp="")
    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, frame, captured)

    rec = next(r for r in caplog.records if r.message == "vad_observer.transcript")
    assert getattr(rec, "transcript_len") == len("हलो")
    assert observer.last_transcript_at is not None


async def test_interruption_logged_and_tracked(caplog):
    observer = _make_observer()
    captured = []

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, InterruptionFrame(), captured)

    assert any(r.message == "vad_observer.interruption" for r in caplog.records)
    assert observer.last_interruption_at is not None


async def test_unrelated_frames_pass_through_silently(caplog):
    observer = _make_observer()
    captured = []

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        await _run(observer, EndFrame(), captured)

    # No vad_observer.* messages for an unrelated frame
    msgs = [r.message for r in caplog.records if r.message.startswith("vad_observer.")]
    assert msgs == []
    # Frame still propagated
    assert len(captured) == 1
    assert isinstance(captured[0][0], EndFrame)
    assert captured[0][1] == FrameDirection.DOWNSTREAM


async def test_every_tracked_frame_is_forwarded_unchanged():
    observer = _make_observer()
    captured = []

    # InterruptionFrame forwarding is exercised in test_interruption_logged_and_tracked;
    # it requires Pipecat's TaskManager which isn't wired in plain unit tests.
    frames = [
        VADUserStartedSpeakingFrame(),
        VADUserStoppedSpeakingFrame(),
        UserStartedSpeakingFrame(),
        UserStoppedSpeakingFrame(),
        TranscriptionFrame(text="hi", user_id="", timestamp=""),
    ]
    for f in frames:
        await _run(observer, f, captured)

    assert [type(c[0]) for c in captured] == [type(f) for f in frames]
    assert all(d == FrameDirection.DOWNSTREAM for _, d in captured)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

async def test_heartbeat_emits_periodically_with_deltas(caplog):
    observer = _make_observer()
    # Pretend a VAD speech_start happened so the delta is non-None.
    captured: list = []
    await _run(observer, VADUserStartedSpeakingFrame(), captured)

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        task = asyncio.create_task(
            run_voice_heartbeat(
                observer,
                session_id="sess-1",
                call_sid="call-1",
                interval_s=0.05,
            )
        )
        await asyncio.sleep(0.13)  # expect ~2 ticks
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    ticks = [r for r in caplog.records if r.message == "voice.heartbeat"]
    assert len(ticks) >= 2
    # Delta is populated (we recorded a speech_start above).
    assert getattr(ticks[0], "seconds_since_vad_speech_start") is not None
    # Untouched fields stay None.
    assert getattr(ticks[0], "seconds_since_transcript") is None


async def test_heartbeat_logs_cancel_on_stop(caplog):
    observer = _make_observer()

    with caplog.at_level(logging.INFO, logger="src.pipecat_services.vad_observer"):
        task = asyncio.create_task(
            run_voice_heartbeat(
                observer,
                session_id="sess-1",
                call_sid="call-1",
                interval_s=0.05,
            )
        )
        await asyncio.sleep(0.06)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert any(r.message == "voice.heartbeat_cancelled" for r in caplog.records)


async def test_heartbeat_rejects_non_positive_interval():
    observer = _make_observer()
    with pytest.raises(ValueError):
        await run_voice_heartbeat(
            observer, session_id="s", call_sid="c", interval_s=0
        )
    with pytest.raises(ValueError):
        await run_voice_heartbeat(
            observer, session_id="s", call_sid="c", interval_s=-1.0
        )
