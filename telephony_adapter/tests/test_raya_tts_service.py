# telephony_adapter/tests/test_raya_tts_service.py
import base64
import json
import pytest
import respx
import httpx
from src.raya_tts_service import RayaTTSService


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


def _sse_body(chunks: list[bytes]) -> bytes:
    lines = []
    for chunk in chunks:
        data = {
            "type": "chunk",
            "status_code": 206,
            "done": False,
            "data": base64.b64encode(chunk).decode(),
        }
        lines.append(f"event: chunk\ndata: {json.dumps(data)}\n\n")
    lines.append("event: done\ndata: {}\n\n")
    return "".join(lines).encode()


@pytest.mark.asyncio
async def test_synthesize_returns_audio_bytes(config):
    chunk1 = b"\x01\x02"
    chunk2 = b"\x03\x04"

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_sse_body([chunk1, chunk2]))
        )
        svc = RayaTTSService(config)
        result = await svc.synthesize("hello")

    assert result == chunk1 + chunk2


@pytest.mark.asyncio
async def test_synthesize_empty_text_returns_empty_bytes(config):
    svc = RayaTTSService(config)
    result = await svc.synthesize("")
    assert result == b""


@pytest.mark.asyncio
async def test_synthesize_http_error_raises(config):
    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        svc = RayaTTSService(config)
        with pytest.raises(Exception, match="TTS synthesis failed"):
            await svc.synthesize("hello")


@pytest.mark.asyncio
async def test_synthesize_missing_config_raises():
    with pytest.raises(ValueError, match="api_key"):
        RayaTTSService({})


@pytest.mark.asyncio
async def test_synthesize_sends_correct_payload(config):
    with respx.mock:
        route = respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_sse_body([b"\x00"]))
        )
        svc = RayaTTSService(config)
        await svc.synthesize("नमस्ते")

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "नमस्ते"
    assert body["voice_id"] == "voice_001"
    assert body["language"] == "hi"
    assert route.calls[0].request.headers["X-API-Key"] == "test-key"
