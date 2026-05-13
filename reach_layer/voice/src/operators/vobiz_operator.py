"""
reach_layer/voice/src/operators/vobiz_operator.py

VobizOperator — concrete TelephonyOperatorBase for the Vobiz telephony platform.

Vobiz is Plivo-compatible. Uses Pipecat's VobizFrameSerializer (which extends
PlivoFrameSerializer with Vobiz-specific 16 kHz L16 support) and
parse_telephony_websocket for handshake parsing.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import json
import logging
import time

from pipecat.frames.frames import CancelFrame, EndFrame
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.operators.operator_base import TelephonyOperatorBase

logger = logging.getLogger(__name__)


class LoggingVobizFrameSerializer(VobizFrameSerializer):
    """VobizFrameSerializer that surfaces hangup outcome to structured logs.

    Wraps the upstream serializer's ``_hang_up_call`` so the adapter-side
    log stream records the Vobiz REST DELETE outcome consistently with the
    rest of the framework's ``operation`` / ``status`` / ``latency_ms``
    convention. No behavioural divergence from the upstream serializer.
    """

    async def deserialize(self, data: str | bytes):
        """Skip outbound-track media events so the bot's own TTS audio is never fed into VAD.

        Vobiz reflects the bot's outbound audio back on the same bidirectional WebSocket
        with ``"track": "outbound"``. Without this filter those frames enter the VAD,
        fire an InterruptionFrame, and flush the TTS queue before the caller hears anything.
        """
        try:
            msg = json.loads(data) if isinstance(data, (str, bytes)) else {}
            if msg.get("event") == "media" and msg.get("media", {}).get("track") == "outbound":
                return None
        except (json.JSONDecodeError, AttributeError):
            pass
        return await super().deserialize(data)

    async def _hang_up_call(self):
        """Call upstream hangup and emit a single structured log entry."""
        start = time.time()
        outcome = "failure"
        if not self._call_id or not self._auth_id or not self._auth_token:
            outcome = "skipped_missing_credentials"
        else:
            import aiohttp
            try:
                endpoint = (
                    f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
                    f"/Call/{self._call_id}/"
                )
                headers = {
                    "X-Auth-ID": self._auth_id,
                    "X-Auth-Token": self._auth_token,
                }
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.delete(endpoint, headers=headers) as response:
                        if response.status == 204:
                            outcome = "success"
                        elif response.status == 404:
                            outcome = "already_terminated"
                        else:
                            outcome = "failure"
            except Exception as exc:
                logger.error(
                    "vobiz_serializer.hangup_exception",
                    extra={
                        "operation": "vobiz_serializer.hangup",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_id": self._call_id,
                    },
                )
                outcome = "failure"
        logger.info(
            "vobiz_serializer.hangup",
            extra={
                "operation": "vobiz_serializer.hangup",
                "status": "success" if outcome in ("success", "already_terminated") else "failure",
                "outcome": outcome,
                "latency_ms": int((time.time() - start) * 1000),
                "call_id": self._call_id,
            },
        )


class VobizOperator(TelephonyOperatorBase):
    """Telephony operator adapter for the Vobiz platform.

    Handles the Vobiz WebSocket handshake, creates a FastAPIWebsocketTransport
    with VobizFrameSerializer, and generates the Vobiz/Plivo-compatible XML
    response for the /answer webhook.

    Args:
        config: Full merged config dict. Reads reach_layer.channels.voice.vobiz section.

    Raises:
        ValueError: If auth_id or auth_token is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        vobiz_cfg = config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("vobiz", {})
        auth_id = vobiz_cfg.get("auth_id", "")
        if not auth_id:
            raise ValueError("reach_layer.channels.voice.vobiz.auth_id is required")
        auth_token = vobiz_cfg.get("auth_token", "")
        if not auth_token:
            raise ValueError("reach_layer.channels.voice.vobiz.auth_token is required")
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._sample_rate = int(vobiz_cfg.get("sample_rate", 8000))

    async def parse_handshake(self, websocket) -> tuple[str, str]:
        """Parse the Vobiz WebSocket handshake to extract stream_id and call_id.

        Args:
            websocket: Active WebSocket connection from Vobiz.

        Returns:
            Tuple of (stream_id, call_id). Either is empty string if absent.
        """
        try:
            _transport_type, call_data = await parse_telephony_websocket(websocket)
        except Exception as exc:
            logger.error(
                "vobiz_operator.handshake_failed",
                extra={
                    "operation": "vobiz_operator.parse_handshake",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        stream_id = call_data.get("stream_id") or ""
        call_id = call_data.get("call_id") or ""
        logger.info(
            "vobiz_operator.handshake_parsed",
            extra={
                "operation": "vobiz_operator.parse_handshake",
                "status": "success",
                "stream_id": stream_id,
                "call_id": call_id,
            },
        )
        return stream_id, call_id

    def create_transport(
        self, websocket, stream_id: str, call_id: str
    ) -> FastAPIWebsocketTransport:
        """Build FastAPIWebsocketTransport with LoggingVobizFrameSerializer.

        Args:
            websocket: Active WebSocket connection.
            stream_id: Stream identifier from parse_handshake.
            call_id: Call identifier from parse_handshake.

        Returns:
            Configured FastAPIWebsocketTransport.
        """
        start = time.time()
        serializer = LoggingVobizFrameSerializer(
            stream_id=stream_id,
            call_id=call_id,
            auth_id=self._auth_id,
            auth_token=self._auth_token,
            params=VobizFrameSerializer.InputParams(
                vobiz_sample_rate=self._sample_rate,
                auto_hang_up=True,
            ),
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )
        logger.info(
            "vobiz_operator.transport_created",
            extra={
                "operation": "vobiz_operator.create_transport",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
                "stream_id": stream_id,
                "call_id": call_id,
            },
        )
        return transport

    def webhook_response_xml(self, websocket_url: str) -> str:
        """Return Vobiz/Plivo-compatible XML for the /answer webhook.

        Args:
            websocket_url: Full WebSocket URL for Vobiz to connect to.

        Returns:
            XML string instructing Vobiz to open a bidirectional audio stream.
        """
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream bidirectional="true" keepCallAlive="true"'
            f' contentType="audio/x-mulaw;rate={self._sample_rate}">'
            f"{websocket_url}</Stream>\n"
            "</Response>"
        )
