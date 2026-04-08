# telephony_adapter/tests/test_raya_stt_service.py
import asyncio
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.raya_stt_service import RayaSTTService


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
async def test_transcribe_returns_transcript(config):
    audio = b"\x00" * 100
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(return_value=json.dumps({
        "transcript": "नमस्ते",
        "status": "success",
    }))

    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = RayaSTTService(config)
        result = await svc.transcribe(audio)

    assert result == "नमस्ते"
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert "audio_base64" in sent
    assert sent["language"] == "hi"
    decoded = base64.b64decode(sent["audio_base64"])
    assert decoded == audio


@pytest.mark.asyncio
async def test_transcribe_empty_audio_returns_empty_string(config):
    svc = RayaSTTService(config)
    result = await svc.transcribe(b"")
    assert result == ""


@pytest.mark.asyncio
async def test_transcribe_ws_failure_retries_once_then_raises(config):
    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.side_effect = OSError("connection refused")
        svc = RayaSTTService(config)
        with pytest.raises(Exception, match="STT"):
            await svc.transcribe(b"\x00" * 100)
    assert mock_connect.call_count == 2


@pytest.mark.asyncio
async def test_transcribe_error_response_raises(config):
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(return_value=json.dumps({
        "error": "bad request",
        "status": "error",
    }))

    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = RayaSTTService(config)
        with pytest.raises(Exception, match="STT transcription failed"):
            await svc.transcribe(b"\x00" * 100)


@pytest.mark.asyncio
async def test_transcribe_missing_config_raises():
    with pytest.raises(ValueError, match="api_key"):
        RayaSTTService({})
