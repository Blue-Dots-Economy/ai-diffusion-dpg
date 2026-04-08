# telephony_adapter/tests/test_telephony_adapter.py
import asyncio
import json
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.telephony_adapter import VobizTelephonyAdapter
from src.base import TelephonyError
from src.vobiz_serializer import VobizCallMetadata
from src.agent_core_service import AgentCoreTurnResult


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {"auth_id": "MA1", "auth_token": "t", "api_base": "https://api.vobiz.ai/api/v1", "from_number": "+91"},
            "raya": {"api_key": "k", "stt_wss_url": "wss://stt.example", "tts_base_url": "https://tts.example", "language": "hi", "voice_id": "v1", "tts_speed": 1.0},
            "agent_core": {"base_url": "http://agent_core:8000", "timeout_ms": 5000, "fallback_phrase": "sorry"},
            "public_url": "https://example.app",
        },
        "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
    }


def _make_ws(messages: list[str]):
    """Create an async mock WebSocket that yields given messages then closes."""

    async def _aiter():
        for msg in messages:
            yield msg

    class _FakeWS:
        def __aiter__(self):
            return _aiter()

        send = AsyncMock()
        close = AsyncMock()

    ws = _FakeWS()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _start_msg(call_sid="CA1", caller_id="+91999"):
    return json.dumps({
        "event": "start",
        "start": {
            "callSid": call_sid,
            "streamSid": "SS1",
            "customParameters": {"caller_id": caller_id},
        },
        "streamSid": "SS1",
    })


def _media_msg(audio=b"\x00" * 20):
    return json.dumps({
        "event": "media",
        "media": {"payload": base64.b64encode(audio).decode(), "track": "inbound"},
        "streamSid": "SS1",
    })


def _stop_msg():
    return json.dumps({"event": "stop", "streamSid": "SS1"})


@pytest.mark.asyncio
async def test_handle_call_full_turn(config):
    """Inbound call: start → audio × N → stop → STT called → AC called → TTS sent."""
    ws = _make_ws([
        _start_msg(),
        _media_msg(),
        _media_msg(),
        _stop_msg(),
    ])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        mock_stt = AsyncMock()
        mock_stt.transcribe = AsyncMock(return_value="नमस्ते")
        MockSTT.return_value = mock_stt

        mock_ac = AsyncMock()
        mock_ac.process_turn = AsyncMock(return_value=AgentCoreTurnResult(
            session_id="s1", response_text="hello", was_escalated=False,
            was_tool_used=False, model_used="", latency_ms=100,
        ))
        MockAC.return_value = mock_ac

        mock_tts = AsyncMock()
        mock_tts.synthesize = AsyncMock(return_value=b"\x01\x02\x03")
        MockTTS.return_value = mock_tts

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    mock_stt.transcribe.assert_called_once()
    mock_ac.process_turn.assert_called_once()
    assert mock_ac.process_turn.call_args.kwargs["user_message"] == "नमस्ते"
    mock_tts.synthesize.assert_called_once_with("hello")
    ws.send.assert_called()


@pytest.mark.asyncio
async def test_handle_call_escalation_closes_call(config):
    ws = _make_ws([_start_msg(), _media_msg(), _stop_msg()])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        MockSTT.return_value.transcribe = AsyncMock(return_value="help")
        MockAC.return_value.process_turn = AsyncMock(return_value=AgentCoreTurnResult(
            session_id="s1", response_text="transferring", was_escalated=True,
            was_tool_used=False, model_used="", latency_ms=0,
        ))
        MockTTS.return_value.synthesize = AsyncMock(return_value=b"\x00")

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_teardown_removes_call_sid(config):
    adapter = VobizTelephonyAdapter(config)
    adapter._active_calls["CA1"] = {"session_id": "s1"}
    await adapter.teardown("CA1")
    assert "CA1" not in adapter._active_calls


@pytest.mark.asyncio
async def test_handle_call_empty_transcript_skips_ac(config):
    """Empty STT result skips Agent Core — no LLM call for silence."""
    ws = _make_ws([_start_msg(), _media_msg(), _stop_msg()])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        MockSTT.return_value.transcribe = AsyncMock(return_value="")
        MockAC.return_value.process_turn = AsyncMock()
        MockTTS.return_value.synthesize = AsyncMock(return_value=b"")

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    MockAC.return_value.process_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_call_empty_buffer_on_stop_skips_stt(config):
    """stop event with no buffered media must not call STT or Agent Core."""
    ws = _make_ws([_start_msg(), _stop_msg()])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        MockSTT.return_value.transcribe = AsyncMock()
        MockAC.return_value.process_turn = AsyncMock()
        MockTTS.return_value.synthesize = AsyncMock()

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA_empty", "+91999", ws)

    MockSTT.return_value.transcribe.assert_not_called()
    MockAC.return_value.process_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_call_teardown_called_on_exception(config):
    """teardown must be called even when the WebSocket raises mid-loop."""
    class _ErrorWS:
        async def __aiter__(self):
            raise OSError("connection reset")
            yield  # make it an async generator

        async def send(self, data):
            pass

        async def close(self):
            pass

    with patch("src.telephony_adapter.RayaSTTService"), \
         patch("src.telephony_adapter.AgentCoreLLMService"), \
         patch("src.telephony_adapter.RayaTTSService"):

        adapter = VobizTelephonyAdapter(config)
        adapter._active_calls["CA_err"] = {"session_id": "s-err"}
        await adapter.handle_call("CA_err", "+91999", _ErrorWS())

    assert "CA_err" not in adapter._active_calls
