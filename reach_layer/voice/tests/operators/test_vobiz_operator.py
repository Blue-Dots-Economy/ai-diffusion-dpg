"""Tests for VobizOperator — concrete Vobiz telephony operator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.operators.vobiz_operator import VobizOperator
from src.operators.operator_base import TelephonyOperatorBase


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {
                "auth_id": "test-auth-id",
                "auth_token": "test-auth-token",
                "sample_rate": 8000,
            }
        }
    }


def test_vobiz_operator_is_operator_base():
    assert issubclass(VobizOperator, TelephonyOperatorBase)


def test_vobiz_operator_raises_on_missing_auth_id():
    with pytest.raises(ValueError, match="auth_id"):
        VobizOperator({})


def test_vobiz_operator_raises_on_missing_auth_token():
    with pytest.raises(ValueError, match="auth_token"):
        VobizOperator({"telephony_adapter": {"vobiz": {"auth_id": "x"}}})


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
