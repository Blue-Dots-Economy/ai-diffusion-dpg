"""Tests for VobizAdapter — concrete TelephonyAdapterBase implementation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.vobiz_adapter import VobizAdapter
from src.base import TelephonyAdapterBase


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {
                "api_key": "raya-key",
                "tts_base_url": "https://hub.getraya.app/v1",
                "language": "hi",
                "voice_id": "voice_001",
            },
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "greeting": "नमस्ते",
                "fallback_phrase": "माफ़ करें",
            },
        }
    }


def test_vobiz_adapter_is_telephony_adapter_base():
    assert issubclass(VobizAdapter, TelephonyAdapterBase)


def test_vobiz_adapter_raises_on_none_config():
    with pytest.raises(ValueError):
        VobizAdapter(None)


@pytest.mark.asyncio
async def test_teardown_does_not_raise(config):
    adapter = VobizAdapter(config)
    await adapter.teardown("call-123")


@pytest.mark.asyncio
async def test_handle_call_uses_caller_id_as_user_id(config):
    """user_id passed to AgentCoreLLMProcessor must equal caller_id."""
    captured_user_id = {}

    class MockAgentCoreLLM:
        def __init__(self, cfg, *, call_sid, session_id, user_id, channel=None,
                     channel_config=None, telephony=None):
            captured_user_id["user_id"] = user_id
            captured_user_id["channel"] = channel

        async def process_frame(self, frame, direction):
            pass

    mock_ws = MagicMock()
    mock_transport = MagicMock()
    mock_transport.input = MagicMock(return_value=MagicMock())
    mock_transport.output = MagicMock(return_value=MagicMock())
    mock_transport.event_handler = MagicMock(return_value=lambda f: f)
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock()

    with patch("src.vobiz_adapter.VobizOperator") as MockOp, \
         patch("src.vobiz_adapter.SileroVADWrapper") as MockVAD, \
         patch("src.vobiz_adapter.RayaSTTService"), \
         patch("src.vobiz_adapter.AgentCoreLLMProcessor", MockAgentCoreLLM), \
         patch("src.vobiz_adapter.RayaTTSService"), \
         patch("src.vobiz_adapter.VADProcessor"), \
         patch("src.vobiz_adapter.Pipeline"), \
         patch("src.vobiz_adapter.PipelineTask"), \
         patch("src.vobiz_adapter.PipelineRunner", return_value=mock_runner):

        MockOp.return_value.parse_handshake = AsyncMock(return_value=("sid", "cid"))
        MockOp.return_value.create_transport = MagicMock(return_value=mock_transport)
        MockVAD.return_value.create_analyzer = MagicMock(return_value=MagicMock())

        adapter = VobizAdapter(config)
        await adapter.handle_call("call-123", "+919876543210", mock_ws)

    assert captured_user_id["user_id"] == "+919876543210"


@pytest.mark.asyncio
async def test_handle_call_raises_telephony_error_on_handshake_failure(config):
    """handle_call must wrap parse_handshake exceptions as TelephonyError."""
    from src.base import TelephonyError

    mock_ws = MagicMock()

    with patch("src.vobiz_adapter.VobizOperator") as MockOp, \
         patch("src.vobiz_adapter.SileroVADWrapper"):
        MockOp.return_value.parse_handshake = AsyncMock(
            side_effect=RuntimeError("bad frame")
        )
        adapter = VobizAdapter(config)
        with pytest.raises(TelephonyError) as exc_info:
            await adapter.handle_call("call-123", "+91999", mock_ws)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
