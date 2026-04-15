"""Tests for RayaTTSService — Pipecat TTSService backed by Raya SSE streaming endpoint."""
import base64
import json
import struct
import pytest
import respx
import httpx
import numpy as np

from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame


def _make_sse_body(f32le_chunks: list[bytes]) -> bytes:
    """Build an SSE body with chunk events followed by a done event."""
    lines = []
    for chunk in f32le_chunks:
        data = {
            "type": "chunk",
            "status_code": 206,
            "done": False,
            "data": base64.b64encode(chunk).decode(),
            "step_time": 0.05,
        }
        lines.append(f"event: chunk\ndata: {json.dumps(data)}\n\n")
    lines.append("event: done\ndata: {}\n\n")
    return "".join(lines).encode()


def _f32le_from_samples(samples: list[float]) -> bytes:
    """Convert a list of floats to F32LE bytes."""
    return struct.pack(f"<{len(samples)}f", *samples)


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "raya": {
                "api_key": "test-key",
                "tts_base_url": "https://hub.getraya.app/v1",
                "voice_id": "voice_001",
                "language": "hi",
                "tts_speed": 1.0,
            }
        }
    }


@pytest.mark.asyncio
async def test_run_tts_yields_tts_audio_raw_frames(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    f32_chunk = _f32le_from_samples([0.1, -0.2, 0.3, -0.1])

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = []
        async for frame in svc.run_tts("नमस्ते", context_id="ctx1"):
            frames.append(frame)

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    assert len(audio_frames) >= 1
    # Check it emitted PCM16 (2 bytes per sample × 4 samples = 8 bytes)
    total_bytes = sum(len(f.audio) for f in audio_frames)
    assert total_bytes == len(f32_chunk) // 2  # F32 4 bytes → PCM16 2 bytes per sample


@pytest.mark.asyncio
async def test_run_tts_pcm16_conversion_is_correct(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    # Known F32LE input: 1.0 → max int16 (32767), -1.0 → min int16 (-32767)
    f32_chunk = _f32le_from_samples([1.0, -1.0, 0.0])

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = [f async for f in svc.run_tts("test", context_id="ctx1")]

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    pcm16 = b"".join(f.audio for f in audio_frames)
    samples = np.frombuffer(pcm16, dtype=np.int16)
    assert samples[0] == 32767    # 1.0 clipped
    assert samples[1] == -32767   # -1.0 clipped
    assert samples[2] == 0        # 0.0


@pytest.mark.asyncio
async def test_run_tts_audio_frames_have_correct_sample_rate(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    f32_chunk = _f32le_from_samples([0.5] * 8)

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = [f async for f in svc.run_tts("hi", context_id="ctx1")]

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    for f in audio_frames:
        assert f.sample_rate == 8000
        assert f.num_channels == 1


@pytest.mark.asyncio
async def test_run_tts_sends_correct_payload(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    with respx.mock:
        route = respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([_f32le_from_samples([0.0])]))
        )
        svc = RayaTTSService(config)
        async for _ in svc.run_tts("hello", context_id="ctx1"):
            pass

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "hello"
    assert body["voice_id"] == "voice_001"
    assert body["language"] == "hi"
    assert body["sample_rate"] == 8000
    assert route.calls[0].request.headers["X-API-Key"] == "test-key"


@pytest.mark.asyncio
@respx.mock
async def test_run_tts_yields_error_frame_on_http_error(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
        return_value=httpx.Response(500, text="error")
    )
    tts = RayaTTSService(config)
    frames = []
    async for frame in tts.run_tts("hello", "ctx-1"):
        frames.append(frame)
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


def test_missing_api_key_raises():
    from src.pipecat_services.raya_tts import RayaTTSService
    with pytest.raises(ValueError, match="api_key"):
        RayaTTSService({})


# ---------------------------------------------------------------------------
# Task 4: TTSServiceBase inheritance + synthesize() tests
# ---------------------------------------------------------------------------
from src.pipecat_services.tts_base import TTSServiceBase


def _make_f32le_chunk(n_samples: int = 160) -> bytes:
    samples = np.zeros(n_samples, dtype=np.float32)
    return samples.tobytes()


def _sse_line(chunk_b64: str, done: bool = False) -> str:
    if done:
        return 'data: {"done": true}\n\n'
    payload = json.dumps({"type": "chunk", "status_code": 206, "done": False, "data": chunk_b64})
    return f"data: {payload}\n\n"


def test_raya_tts_is_tts_service_base(config):
    from src.pipecat_services.raya_tts import RayaTTSService
    tts = RayaTTSService(config)
    assert isinstance(tts, TTSServiceBase)


@pytest.mark.asyncio
@respx.mock
async def test_synthesize_yields_pcm16_chunks(config):
    from src.pipecat_services.raya_tts import RayaTTSService
    chunk = _make_f32le_chunk(160)
    chunk_b64 = base64.b64encode(chunk).decode()
    sse_body = _sse_line(chunk_b64) + _sse_line("", done=True)
    respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
        return_value=httpx.Response(200, text=sse_body)
    )
    tts = RayaTTSService(config)
    chunks = [c async for c in tts.synthesize("नमस्ते")]
    assert len(chunks) == 1
    assert len(chunks[0]) == 160 * 2  # float32 → int16: same sample count, half bytes


@pytest.mark.asyncio
@respx.mock
async def test_synthesize_yields_nothing_on_http_error(config):
    from src.pipecat_services.raya_tts import RayaTTSService
    respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
        return_value=httpx.Response(500, text="error")
    )
    tts = RayaTTSService(config)
    chunks = [c async for c in tts.synthesize("hi")]
    assert chunks == []


@pytest.mark.asyncio
async def test_synthesize_retries_once_on_connect_error(config):
    """TTS must retry once on connection error before giving up."""
    from src.pipecat_services.raya_tts import RayaTTSService
    from unittest.mock import patch, AsyncMock

    f32_chunk = _make_f32le_chunk(160)
    chunk_b64 = base64.b64encode(f32_chunk).decode()
    sse_body = _sse_line(chunk_b64) + _sse_line("", done=True)

    with patch("src.pipecat_services.raya_tts.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with respx.mock:
            route = respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
                side_effect=[
                    httpx.ConnectError("refused"),
                    httpx.Response(200, text=sse_body),
                ]
            )
            tts = RayaTTSService(config)
            chunks = [c async for c in tts.synthesize("hello")]

    assert len(route.calls) == 2
    mock_sleep.assert_called_once()
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_synthesize_yields_nothing_on_connection_error(config):
    from src.pipecat_services.raya_tts import RayaTTSService
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient.stream", return_value=mock_cm):
        tts = RayaTTSService(config)
        chunks = [c async for c in tts.synthesize("hi")]
    assert chunks == []
