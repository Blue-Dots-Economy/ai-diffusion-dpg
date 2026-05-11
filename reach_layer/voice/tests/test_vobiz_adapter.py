"""Tests for VobizAdapter — concrete TelephonyAdapterBase implementation."""
import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.vobiz_adapter import VobizAdapter
from src.base import TelephonyAdapterBase


@pytest.fixture
def config():
    return {
        "reach_layer": {"channels": {"voice": {
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
        }}}
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
         patch("src.vobiz_adapter.UserTurnProcessor"), \
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


@pytest.mark.asyncio
async def test_close_call_logs_vendor_signal_and_outcome_no_active_ws(config, caplog):
    adapter = VobizAdapter(config)
    with caplog.at_level(logging.INFO, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    invoked = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call")
    assert invoked.reason == "session_end"
    assert invoked.vendor_signal == "vobiz_rest_delete"
    skipped = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_no_active_ws")
    assert skipped.outcome == "skipped"


@pytest.mark.asyncio
async def test_close_call_with_active_ws_logs_success(config, caplog):
    adapter = VobizAdapter(config)
    fake_ws = MagicMock()
    fake_ws.close = AsyncMock()
    adapter._active_websocket = fake_ws
    with caplog.at_level(logging.INFO, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    fake_ws.close.assert_awaited_once()
    rec = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_complete")
    assert rec.outcome == "ws_closed"
    assert rec.vendor_signal == "vobiz_rest_delete"
    assert isinstance(rec.latency_ms, int)


@pytest.mark.asyncio
async def test_close_call_with_active_ws_close_raises_logs_failure(config, caplog):
    adapter = VobizAdapter(config)
    fake_ws = MagicMock()
    fake_ws.close = AsyncMock(side_effect=RuntimeError("ws boom"))
    adapter._active_websocket = fake_ws
    with caplog.at_level(logging.ERROR, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    rec = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_failed")
    assert rec.outcome == "failure"
    assert "RuntimeError" in rec.error


# ---------------------------------------------------------------------------
# Recording wiring tests (Tasks 12 + 13)
# ---------------------------------------------------------------------------

def _voice_cfg(source: str = "disabled", salt: str = "") -> dict:
    """Return a minimal voice config dict for recording tests."""
    return {
        "reach_layer": {
            "channels": {
                "voice": {
                    "vobiz": {
                        "auth_id": "A",
                        "auth_token": "T",
                        "sample_rate": 8000,
                    },
                    "vad": {
                        "start_secs": 0.2,
                        "stop_secs": 0.6,
                        "min_volume": 0.6,
                    },
                    "raya": {"endpoint": "http://raya:9090"},
                    "agent_core": {
                        "base_url": "http://agent:8000",
                        "submit_path": "/process_turn",
                        "events_path": "/sessions/{session_id}/events",
                        "cancel_path": "/sessions/{session_id}/cancel",
                        "request_timeout_s": 30,
                    },
                    "recording": {
                        "source": source,
                        "consent_purpose": "recording",
                        "webhook_timeout_s": 5.0,
                        "fetch_timeout_s": 5.0,
                        "min_duration_ms": 10,
                        "caller_id_hash_salt": salt,
                        "store": {
                            "backend": "local",
                            "local": {"base_path": "/tmp/x"},
                            "s3": {
                                "bucket": "",
                                "prefix": "rec/",
                                "region": "ap-south-1",
                                "kms_key_id": "",
                            },
                        },
                    },
                }
            }
        }
    }


def test_vobiz_adapter_exposes_null_manager_when_disabled():
    """recording_manager must return a non-None RecordingManagerBase in idle state."""
    from src.recordings.manager_base import RecordingManagerBase

    a = VobizAdapter(_voice_cfg())
    assert isinstance(a.recording_manager, RecordingManagerBase)
    assert a.recording_manager.state == "idle"


@pytest.mark.asyncio
async def test_consent_event_triggers_manager_start():
    """_on_consent_event must call start() when purpose matches and granted=True."""
    from reach_layer_base import ConsentEvent

    a = VobizAdapter(_voice_cfg(source="pipeline", salt="s" * 32))
    a._recording_manager.start = AsyncMock()
    evt = ConsentEvent(purpose="recording", granted=True, consent_granted_ts=1.0, turn_id="t-1")
    await a._on_consent_event(evt)
    a._recording_manager.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_consent_event_for_other_purpose_ignored():
    """_on_consent_event must not call start() when purpose does not match."""
    from reach_layer_base import ConsentEvent

    a = VobizAdapter(_voice_cfg(source="pipeline", salt="s" * 32))
    a._recording_manager.start = AsyncMock()
    evt = ConsentEvent(purpose="data_share", granted=True, consent_granted_ts=1.0)
    await a._on_consent_event(evt)
    a._recording_manager.start.assert_not_called()


@pytest.mark.asyncio
async def test_consent_event_denied_ignored():
    """_on_consent_event must not call start() when granted=False."""
    from reach_layer_base import ConsentEvent

    a = VobizAdapter(_voice_cfg(source="pipeline", salt="s" * 32))
    a._recording_manager.start = AsyncMock()
    evt = ConsentEvent(purpose="recording", granted=False)
    await a._on_consent_event(evt)
    a._recording_manager.start.assert_not_called()
