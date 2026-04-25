"""Tests for VobizOperator — concrete Vobiz telephony operator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.operators.vobiz_operator import VobizOperator
from src.operators.operator_base import TelephonyOperatorBase


@pytest.fixture
def config():
    return {
        "reach_layer": {"channels": {"voice": {
            "vobiz": {
                "auth_id": "test-auth-id",
                "auth_token": "test-auth-token",
                "sample_rate": 8000,
            }
        }}}
    }


def test_vobiz_operator_is_operator_base():
    assert issubclass(VobizOperator, TelephonyOperatorBase)


def test_vobiz_operator_raises_on_missing_auth_id():
    with pytest.raises(ValueError, match="auth_id"):
        VobizOperator({})


def test_vobiz_operator_raises_on_missing_auth_token():
    with pytest.raises(ValueError, match="auth_token"):
        VobizOperator({"reach_layer": {"channels": {"voice": {"vobiz": {"auth_id": "x"}}}}})


@pytest.mark.asyncio
async def test_parse_handshake_returns_stream_and_call_ids(config):
    mock_ws = MagicMock()
    mock_call_data = {"stream_id": "stream-123", "call_id": "call-456"}

    with patch(
        "src.operators.vobiz_operator.parse_telephony_websocket",
        new=AsyncMock(return_value=("plivo", mock_call_data)),
    ):
        op = VobizOperator(config)
        stream_id, call_id = await op.parse_handshake(mock_ws)

    assert stream_id == "stream-123"
    assert call_id == "call-456"


@pytest.mark.asyncio
async def test_parse_handshake_falls_back_to_empty_strings_on_missing_keys(config):
    with patch(
        "src.operators.vobiz_operator.parse_telephony_websocket",
        new=AsyncMock(return_value=("plivo", {})),
    ):
        op = VobizOperator(config)
        stream_id, call_id = await op.parse_handshake(MagicMock())

    assert stream_id == ""
    assert call_id == ""


def test_create_transport_returns_fastapi_websocket_transport(config):
    mock_ws = MagicMock()
    mock_transport = MagicMock()

    with patch("src.operators.vobiz_operator.VobizFrameSerializer"), \
         patch("src.operators.vobiz_operator.FastAPIWebsocketParams"), \
         patch("src.operators.vobiz_operator.FastAPIWebsocketTransport", return_value=mock_transport):
        op = VobizOperator(config)
        result = op.create_transport(mock_ws, "stream-1", "call-1")

    assert result is mock_transport


def test_webhook_response_xml_contains_url(config):
    op = VobizOperator(config)
    xml = op.webhook_response_xml("wss://example.com/ws/abc")
    assert "wss://example.com/ws/abc" in xml
    assert "<Stream" in xml
    assert 'bidirectional="true"' in xml


def test_webhook_response_xml_is_valid_xml(config):
    import xml.etree.ElementTree as ET
    op = VobizOperator(config)
    xml = op.webhook_response_xml("wss://example.com/ws/abc")
    root = ET.fromstring(xml)  # raises if invalid
    assert root.tag == "Response"


def test_webhook_response_xml_embeds_sample_rate(config):
    """The contentType attribute must include the configured sample rate."""
    op = VobizOperator(config)
    xml = op.webhook_response_xml("wss://example.com/ws/abc")
    assert "rate=8000" in xml


import logging
from pipecat.frames.frames import EndFrame, CancelFrame
from src.operators.vobiz_operator import LoggingVobizFrameSerializer


def _make_serializer(call_id: str = "call-456"):
    return LoggingVobizFrameSerializer(
        stream_id="stream-123",
        call_id=call_id,
        auth_id="aid",
        auth_token="tok",
    )


class _FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.delete_calls = []

    def delete(self, url, headers=None):
        self.delete_calls.append({"url": url, "headers": headers})
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _patch_aiohttp(response: _FakeResponse):
    fake = _FakeSession(response)
    return patch("aiohttp.ClientSession", return_value=fake), fake


@pytest.mark.asyncio
async def test_logging_serializer_success_outcome(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    assert len(fake.delete_calls) == 1
    call = fake.delete_calls[0]
    assert call["url"] == "https://api.vobiz.ai/api/v1/Account/aid/Call/call-456/"
    assert call["headers"] == {"X-Auth-ID": "aid", "X-Auth-Token": "tok"}
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "success"
    assert rec.call_id == "call-456"
    assert isinstance(rec.latency_ms, int)


@pytest.mark.asyncio
async def test_logging_serializer_already_terminated_404(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(404))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "already_terminated"
    assert rec.status == "success"


@pytest.mark.asyncio
async def test_logging_serializer_missing_call_id(caplog):
    serializer = _make_serializer(call_id=None)
    with caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "skipped_missing_credentials"
    assert rec.status == "failure"


@pytest.mark.asyncio
async def test_logging_serializer_cancel_frame_triggers_hangup():
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm:
        await serializer.serialize(CancelFrame())
    assert len(fake.delete_calls) == 1


@pytest.mark.asyncio
async def test_logging_serializer_idempotent_on_double_end_frame():
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm:
        await serializer.serialize(EndFrame())
        await serializer.serialize(EndFrame())
    assert len(fake.delete_calls) == 1


@pytest.mark.asyncio
async def test_logging_serializer_5xx_outcome_failure(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(500, text="boom"))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "failure"
    assert rec.status == "failure"


def test_create_transport_uses_logging_serializer(config):
    op = VobizOperator(config)
    mock_ws = MagicMock()
    with patch("src.operators.vobiz_operator.FastAPIWebsocketTransport") as mock_transport_cls:
        op.create_transport(mock_ws, "stream-x", "call-y")
    kwargs = mock_transport_cls.call_args.kwargs
    serializer = kwargs["params"].serializer
    assert isinstance(serializer, LoggingVobizFrameSerializer)
    assert serializer._params.auto_hang_up is True
