"""Tests for TelephonyOperatorBase — DPG abstract telephony operator interface."""
import pytest
from src.operators.operator_base import TelephonyOperatorBase


def test_cannot_instantiate_operator_base():
    with pytest.raises(TypeError):
        TelephonyOperatorBase()


def test_concrete_operator_must_implement_all_methods():
    class PartialOperator(TelephonyOperatorBase):
        async def parse_handshake(self, websocket):
            return ("sid", "cid")
        # missing create_transport and webhook_response_xml

    with pytest.raises(TypeError):
        PartialOperator()


def test_concrete_operator_with_all_methods_instantiates():
    class FullOperator(TelephonyOperatorBase):
        async def parse_handshake(self, websocket):
            return ("sid", "cid")

        def create_transport(self, websocket, stream_id, call_id):
            return object()

        def webhook_response_xml(self, websocket_url):
            return "<Response/>"

    op = FullOperator()
    assert op is not None
