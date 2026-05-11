"""Tests for recording_tap.py — single-WAV tap with on-the-fly resampling.

The Pipecat-frame side of the processor is exercised in the real call
end-to-end (no clean way to mock InputAudioRawFrame / OutputAudioRawFrame
construction across Pipecat versions). What we DO test here is the part
that's most likely to silently misbehave: the numpy resampler.
"""
import numpy as np

from recording_tap import _resample_pcm16, RecordingTapProcessor


def test_resample_identity_when_rates_match():
    """If source and target rates are equal, bytes pass through unchanged."""
    audio = (np.arange(1000, dtype=np.int16) * 32).tobytes()
    assert _resample_pcm16(audio, 16000, 16000) == audio


def test_resample_empty_is_empty():
    """Empty input produces empty output without crashing."""
    assert _resample_pcm16(b"", 24000, 16000) == b""


def test_resample_24k_to_16k_produces_two_thirds_samples():
    """24 kHz → 16 kHz should yield ~2/3 the original sample count."""
    samples_in = 1500
    audio = (np.arange(samples_in, dtype=np.int16) * 8).tobytes()
    out = _resample_pcm16(audio, 24000, 16000)
    out_samples = np.frombuffer(out, dtype=np.int16)
    expected = int(round(samples_in * 16000 / 24000))
    assert len(out_samples) == expected


def test_resample_8k_to_16k_produces_double_samples():
    """8 kHz → 16 kHz should yield ~2× the original sample count."""
    samples_in = 500
    audio = (np.arange(samples_in, dtype=np.int16) * 16).tobytes()
    out = _resample_pcm16(audio, 8000, 16000)
    out_samples = np.frombuffer(out, dtype=np.int16)
    expected = int(round(samples_in * 16000 / 8000))
    assert len(out_samples) == expected


def test_resample_preserves_a_linear_ramp_in_value():
    """A linear ramp resampled to half rate should still be (approximately) a linear ramp.

    Catches the obvious "garbage out" case where resampling produces noise instead
    of an interpolated signal. We allow some tolerance because linear interp +
    int16 truncation introduces small numeric error.
    """
    src_samples = np.linspace(-30000, 30000, num=1000, dtype=np.int16)
    audio = src_samples.tobytes()
    out = _resample_pcm16(audio, 24000, 12000)
    out_samples = np.frombuffer(out, dtype=np.int16)
    assert len(out_samples) == 500
    # First and last samples should be approximately preserved
    assert abs(int(out_samples[0]) - int(src_samples[0])) < 50
    assert abs(int(out_samples[-1]) - int(src_samples[-1])) < 50
    # Mid-point should be approximately the input mid-point
    assert abs(int(out_samples[250]) - 0) < 200


def test_tap_construction_and_lifecycle():
    """activate() then close() writes a valid 44-byte WAV header."""
    tap = RecordingTapProcessor(target_sample_rate=16000)
    tap.activate()
    tap.close()
    # Standard WAV header for PCM16 mono is exactly 44 bytes when no audio frames.
    assert len(tap.buffer_value) == 44
    # close() is idempotent
    tap.close()
    assert len(tap.buffer_value) == 44
