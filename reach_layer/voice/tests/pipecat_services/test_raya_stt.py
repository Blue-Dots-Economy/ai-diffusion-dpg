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
        "reach_layer": {"channels": {"voice": {
            "raya": {
                "api_key": "test-key",
                "stt_wss_url": "https://hub.getraya.app/transcribe",
                "language": "hi",
            }
        }}}
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


@pytest.mark.asyncio
async def test_run_stt_retries_once_on_connect_error(config):
    """Retry path must fire exactly once before giving up on ConnectError."""
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with patch("src.pipecat_services.raya_stt.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with respx.mock:
            route = respx.post("https://hub.getraya.app/transcribe").mock(
                side_effect=[
                    httpx.ConnectError("refused"),
                    httpx.Response(200, json={"transcript": "hello"}),
                ]
            )
            svc = RayaSTTService(config)
            frames = [f async for f in svc.run_stt(wav_audio)]

    # Should have called the URL twice (1 failure + 1 success)
    assert len(route.calls) == 2
    mock_sleep.assert_called_once()
    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "hello"


def test_missing_api_key_raises():
    from src.pipecat_services.raya_stt import RayaSTTService
    with pytest.raises(ValueError, match="api_key"):
        RayaSTTService({})


# ── New tests for STTServiceBase inheritance and transcribe() ──────────────────

import pytest
import respx
import httpx
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.stt_base import STTServiceBase


def test_raya_stt_is_stt_service_base(config):
    stt = RayaSTTService(config)
    assert isinstance(stt, STTServiceBase)


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_text_on_success(config):
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(200, json={"transcript": "नमस्ते"})
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result == "नमस्ते"


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_empty_string_on_empty_transcript(config):
    """Empty transcript (silent utterance) returns "" not None to distinguish from errors."""
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(200, json={"transcript": "  "})
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result == ""


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_none_on_http_error(config):
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_returns_none_on_timeout(config):
    import httpx
    from unittest.mock import patch, AsyncMock

    async def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=raise_timeout)):
        stt = RayaSTTService(config)
        result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result is None
