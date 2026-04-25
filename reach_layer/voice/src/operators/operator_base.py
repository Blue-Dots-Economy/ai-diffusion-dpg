"""
reach_layer/voice/src/operators/operator_base.py

TelephonyOperatorBase — DPG abstract interface for telephony operator adapters.

Each telephony operator (Vobiz, Twilio, Telnyx) has its own WebSocket
message format and webhook XML schema. This base class defines the three
methods every operator must implement: handshake parsing, transport creation,
and webhook XML generation. The serializer is bundled with the operator
because they share the same wire protocol — they are never swapped
independently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TelephonyOperatorBase(ABC):
    """Abstract interface for telephony operator adapters.

    Bundles frame serializer creation with operator identity because the
    serializer encodes the operator's wire protocol and is never swapped
    without also swapping the operator.
    """

    @abstractmethod
    async def parse_handshake(self, websocket) -> tuple[str, str]:
        """Parse provider-specific WebSocket handshake messages.

        Most telephony providers send one or two JSON messages immediately
        after the WebSocket is accepted, before audio begins. These carry
        stream and call identifiers needed to configure the serializer.

        Args:
            websocket: Active WebSocket connection from the telephony provider.

        Returns:
            Tuple of (stream_id, call_id). Either may be empty string if
            the provider does not supply it.
        """

    @abstractmethod
    def create_transport(self, websocket, stream_id: str, call_id: str) -> "Any":
        """Build the Pipecat transport with the provider's frame serializer.

        The serializer is constructed here because it encodes the same wire
        protocol as the operator and must match it exactly.

        Args:
            websocket: Active WebSocket connection.
            stream_id: Stream identifier from parse_handshake.
            call_id: Call identifier from parse_handshake.

        Returns:
            Configured FastAPIWebsocketTransport ready for pipeline use.
        """

    @abstractmethod
    def webhook_response_xml(self, websocket_url: str) -> str:
        """Return the XML response body for the telephony provider's /answer webhook.

        When the provider signals an inbound call via HTTP POST, the server
        must respond with XML instructing it where to open the WebSocket.

        Args:
            websocket_url: Full WebSocket URL the provider should connect to,
                e.g. wss://example.com/ws/{call_sid}.

        Returns:
            Provider-specific XML string. Must be valid XML.
        """
