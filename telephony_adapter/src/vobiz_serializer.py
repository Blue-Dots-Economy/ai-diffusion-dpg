"""
telephony_adapter/src/vobiz_serializer.py

VobizFrameSerializer — decodes and encodes the Vobiz WebSocket message envelope.

Vobiz streams audio over a WebSocket using JSON messages:
  - "start": call metadata (call SID, stream SID, caller ID)
  - "media": base64-encoded µ-law 8000 Hz audio chunk (inbound or outbound)
  - "stop": call ended

Outbound audio is sent back as {"event": "media", "media": {"payload": "<base64>"}}.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VobizCallMetadata:
    """Metadata extracted from a Vobiz "start" WebSocket event."""

    call_sid: str
    stream_sid: str
    caller_id: str


class VobizFrameSerializer:
    """Encode and decode Vobiz WebSocket JSON envelope messages.

    Stateless — one instance may be shared across calls.
    """

    def parse_start(self, raw: str) -> VobizCallMetadata:
        """Parse a Vobiz "start" event and return call metadata.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            VobizCallMetadata with call_sid, stream_sid, and caller_id.
            caller_id defaults to "unknown" if not present in customParameters.

        Raises:
            ValueError: If the message is not valid JSON or missing required fields.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in start event: {e}") from e

        start = data.get("start", {})
        call_sid = start.get("callSid") or data.get("callSid", "")
        stream_sid = start.get("streamSid") or data.get("streamSid", "")
        caller_id = start.get("customParameters", {}).get("caller_id", "unknown")

        if not call_sid:
            raise ValueError(f"Missing callSid in start event: {raw!r}")

        return VobizCallMetadata(
            call_sid=call_sid,
            stream_sid=stream_sid,
            caller_id=caller_id,
        )

    def parse_media(self, raw: str) -> bytes:
        """Decode audio bytes from a Vobiz "media" event.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            Decoded audio bytes (µ-law 8000 Hz).

        Raises:
            ValueError: If the base64 payload is missing or malformed.
        """
        try:
            data = json.loads(raw)
            payload = data.get("media", {}).get("payload", "")
        except (json.JSONDecodeError, AttributeError) as e:
            raise ValueError(f"Invalid media event: {e}") from e

        if not payload:
            return b""

        try:
            return base64.b64decode(payload)
        except (ValueError, binascii.Error) as e:
            raise ValueError(f"Invalid base64 in media payload: {e}") from e

    def build_media_message(self, stream_sid: str, audio: bytes) -> str:
        """Build a Vobiz "media" outbound message from raw audio bytes.

        Args:
            stream_sid: The stream SID for this call.
            audio: Raw audio bytes to send back to Vobiz.

        Returns:
            JSON string ready to send over the Vobiz WebSocket.
        """
        return json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode()},
        })

    def is_stop_event(self, raw: str) -> bool:
        """Return True if the message is a Vobiz "stop" event.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            True if event == "stop", False otherwise (including on parse error).
        """
        try:
            return json.loads(raw).get("event") == "stop"
        except (json.JSONDecodeError, AttributeError):
            return False
