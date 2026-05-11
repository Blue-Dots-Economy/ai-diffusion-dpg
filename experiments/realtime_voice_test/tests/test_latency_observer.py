"""Tests for LatencyObserverProcessor — per-turn JSONL writing.

The observer is a Pipecat FrameProcessor. Tests feed it synthetic frames
in the order Pipecat would emit them and verify the JSONL output.
"""
import asyncio
import json
from pathlib import Path

import pytest

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from latency_observer import LatencyObserverProcessor


def make_observer(tmp_path: Path) -> LatencyObserverProcessor:
    return LatencyObserverProcessor(
        call_sid="test-call-123",
        out_path=tmp_path / "turns.jsonl",
        model="gpt-realtime-mini",
        voice="alloy",
        language="hi",
    )


def fake_chunk(n_bytes: int = 100) -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=b"\x00" * n_bytes, sample_rate=16000, num_channels=1)


@pytest.mark.asyncio
async def test_one_turn_writes_one_row(tmp_path):
    """A standard speech_started → ... → bot_stopped sequence writes one row."""
    obs = make_observer(tmp_path)
    await obs.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.01)
    await obs.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.01)
    for _ in range(3):
        await obs.process_frame(fake_chunk(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.005)
    await obs.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    out = tmp_path / "turns.jsonl"
    assert out.exists()
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["call_sid"] == "test-call-123"
    assert row["turn"] == 1
    assert row["model"] == "gpt-realtime-mini"
    assert row["voice"] == "alloy"
    assert row["language"] == "hi"
    assert row["ttft_ms"] >= 0
    assert row["total_response_ms"] >= 0
    assert row["bot_speaking_ms"] >= 0
    assert row["user_speech_duration_ms"] >= 0


@pytest.mark.asyncio
async def test_two_turns_write_two_rows(tmp_path):
    """Two consecutive turns each write a row, with incrementing turn index."""
    obs = make_observer(tmp_path)
    # Turn 1
    for f in [UserStartedSpeakingFrame(), UserStoppedSpeakingFrame(),
              fake_chunk(), BotStoppedSpeakingFrame()]:
        await obs.process_frame(f, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.005)
    # Turn 2
    for f in [UserStartedSpeakingFrame(), UserStoppedSpeakingFrame(),
              fake_chunk(), BotStoppedSpeakingFrame()]:
        await obs.process_frame(f, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.005)

    out = tmp_path / "turns.jsonl"
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["turn"] == 1
    assert json.loads(lines[1])["turn"] == 2


@pytest.mark.asyncio
async def test_tpot_computed_when_multiple_chunks(tmp_path):
    """tpot_ms is the mean inter-chunk gap across all TTSAudioRawFrames."""
    obs = make_observer(tmp_path)
    await obs.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await obs.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    for _ in range(5):
        await obs.process_frame(fake_chunk(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.01)
    await obs.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    row = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[0])
    assert row["tpot_ms"] is not None
    assert row["tpot_ms"] > 0


@pytest.mark.asyncio
async def test_vad_multi_fire_within_one_turn(tmp_path):
    """A second UserStartedSpeakingFrame mid-turn is ignored.

    Pipecat's VAD can fire UserStartedSpeakingFrame multiple times within
    one real conversational turn (the user pauses briefly mid-sentence).
    The observer must NOT overwrite the in-flight turn — doing so produces
    bot_start < user_stop (negative ttft_ms) which was the bug seen on
    the first real test call. Only one row should be written, with the
    first t_user_start_ms preserved.
    """
    obs = make_observer(tmp_path)
    await obs.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    first_user_start = obs._cur["t_user_start_ms"]
    await asyncio.sleep(0.02)
    # VAD re-fires mid-turn (user paused briefly, then resumed)
    await obs.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert obs._cur["t_user_start_ms"] == first_user_start  # unchanged
    assert obs._turn_idx == 1                                # turn counter unchanged
    # Rest of the turn unfolds normally
    await obs.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await obs.process_frame(fake_chunk(), FrameDirection.DOWNSTREAM)
    await obs.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    lines = [l for l in (tmp_path / "turns.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["turn"] == 1
    # ttft must be non-negative (the original bug produced negative values)
    assert row["ttft_ms"] >= 0
    assert row["t_user_start_ms"] == first_user_start


@pytest.mark.asyncio
async def test_late_user_stop_after_bot_start_is_ignored(tmp_path):
    """A UserStoppedSpeakingFrame arriving after the first TTSAudioRawFrame is ignored.

    Two VADs run in parallel: OpenAI's server VAD (triggers the bot reply
    when it thinks the user is done) and Pipecat's local Silero VAD (drives
    UserStoppedSpeakingFrame). For long utterances with mid-clause pauses
    the OpenAI side can fire first, the bot can start replying, and a
    delayed local UserStoppedSpeakingFrame can arrive later. Without this
    guard, that late frame would overwrite t_user_stop_ms with a later
    value and produce a negative ttft_ms.
    """
    obs = make_observer(tmp_path)
    await obs.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.005)
    # Early user_stop (e.g., from OpenAI server VAD perception)
    await obs.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    early_user_stop = obs._cur["t_user_stop_ms"]
    await asyncio.sleep(0.005)
    # Bot starts replying
    await obs.process_frame(fake_chunk(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.02)
    # Late UserStoppedSpeakingFrame arrives (Silero finally noticed silence)
    await obs.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert obs._cur["t_user_stop_ms"] == early_user_stop  # NOT overwritten
    await obs.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    row = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[0])
    assert row["ttft_ms"] >= 0  # the bug produced negative values
    assert row["t_user_stop_ms"] == early_user_stop


@pytest.mark.asyncio
async def test_observer_passes_frames_through(tmp_path):
    """The observer is passive — it must call push_frame on every frame."""
    obs = make_observer(tmp_path)
    seen = []

    async def fake_push_frame(frame, direction):
        seen.append(type(frame).__name__)

    obs.push_frame = fake_push_frame  # monkeypatch

    frames = [UserStartedSpeakingFrame(), UserStoppedSpeakingFrame(),
              fake_chunk(), BotStoppedSpeakingFrame()]
    for f in frames:
        await obs.process_frame(f, FrameDirection.DOWNSTREAM)

    assert len(seen) == len(frames)
