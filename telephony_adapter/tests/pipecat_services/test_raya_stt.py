"""Tests for RayaSTTService — Raya HTTP multipart STT via Pipecat SegmentedSTTService."""
import io
import wave
import base64
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TranscriptionFrame, ErrorFrame


def _make_wav(pcm_bytes: bytes = b"\x00\x01" * 800, sample_rate: int = 8000) -> bytes:
    """Build a minimal WAV file wrapping pcm_bytes."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sample_rate)
    w.writeframes(pcm_bytes)
    w.close()
    buf.seek(0)
    return buf.read()


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "raya": {
                "api_key": "test-key",
                "stt_wss_url": "wss://hub.getraya.app/transcribe",
                "language": "hi",
            }
        }
    }


@pytest.mark.asyncio
async def test_run_stt_returns_transcription_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "नमस्ते", "status": "success"})
        )
        svc = RayaSTTService(config)
        frames = []
        async for frame in svc.run_stt(wav_audio):
            frames.append(frame)

    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "नमस्ते"


@pytest.mark.asyncio
async def test_run_stt_sends_correct_fields(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        route = respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "hello", "status": "success"})
        )
        svc = RayaSTTService(config)
        async for _ in svc.run_stt(wav_audio):
            pass

    request = route.calls[0].request
    assert request.headers["X-API-Key"] == "test-key"
    # multipart body should contain 'file' and 'language' fields
    body = request.content.decode(errors="replace")
    assert "hi" in body  # language field value


@pytest.mark.asyncio
async def test_run_stt_http_error_yields_error_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(500, json={"error": "internal"})
        )
        svc = RayaSTTService(config)
        frames = []
        async for frame in svc.run_stt(wav_audio):
            frames.append(frame)

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


@pytest.mark.asyncio
async def test_run_stt_empty_transcript_yields_nothing(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "  ", "status": "success"})
        )
        svc = RayaSTTService(config)
        frames = [f async for f in svc.run_stt(wav_audio)]

    assert frames == []


@pytest.mark.asyncio
async def test_run_stt_connect_error_yields_error_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with patch("src.pipecat_services.raya_stt.asyncio.sleep", new_callable=AsyncMock):
        with respx.mock:
            respx.post("https://hub.getraya.app/transcribe").mock(
                side_effect=httpx.ConnectError("refused")
            )
            svc = RayaSTTService(config)
            frames = [f async for f in svc.run_stt(wav_audio)]

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


def test_missing_api_key_raises():
    from src.pipecat_services.raya_stt import RayaSTTService
    with pytest.raises(ValueError, match="api_key"):
        RayaSTTService({})
