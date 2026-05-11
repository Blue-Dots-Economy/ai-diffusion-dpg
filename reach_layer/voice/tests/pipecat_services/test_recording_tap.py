"""Tests for RecordingTapProcessor — frame interception and WAV write."""
from __future__ import annotations

import io
import wave

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

from src.pipecat_services.recording_tap import RecordingTapProcessor


async def _started(proc: RecordingTapProcessor) -> None:
    """Mark the processor as started without a real pipeline.

    Pipecat's FrameProcessor.process_frame() refuses to handle audio
    frames until a StartFrame has flipped its _started flag. In a live
    pipeline the runner pushes the StartFrame and wires up a TaskManager.
    For unit tests we skip that machinery and poke the flag directly —
    the recording behaviour under test is independent of it.
    """
    proc._started = True  # noqa: SLF001 — intentional test shortcut


@pytest.mark.asyncio
async def test_processor_inactive_by_default_no_writes():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    await _started(proc)
    f = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1)
    await proc.process_frame(f, direction=None)
    assert buf.getvalue() == b""


@pytest.mark.asyncio
async def test_active_writes_input_and_output_frames():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    await _started(proc)
    proc.activate()
    in_f = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1)
    out_f = OutputAudioRawFrame(audio=b"\x02\x03" * 80, sample_rate=8000, num_channels=1)
    await proc.process_frame(in_f, direction=None)
    await proc.process_frame(out_f, direction=None)
    proc.close()
    buf.seek(0)
    with wave.open(buf, "rb") as w:
        assert w.getframerate() == 8000
        assert w.getnchannels() == 1
        assert w.getnframes() > 0


@pytest.mark.asyncio
async def test_inactive_after_close_drops_frames():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    await _started(proc)
    proc.activate()
    proc.close()
    f = InputAudioRawFrame(audio=b"\x05" * 160, sample_rate=8000, num_channels=1)
    await proc.process_frame(f, direction=None)
    buf.seek(0)
    with wave.open(buf, "rb") as w:
        assert w.getframerate() == 8000
