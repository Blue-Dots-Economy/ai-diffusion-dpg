"""Async wrapper over the OpenAI Realtime WebSocket.

Handles connection, session.update, audio append, event iteration, and
clean close. Does not interpret events — just yields them to callers.

OpenAI Realtime API reference:
  https://developers.openai.com/api/docs/guides/realtime
"""
from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator

import websockets

logger = logging.getLogger(__name__)


OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


class RealtimeClient:
    """Per-call connection to the OpenAI Realtime WebSocket.

    Usage:
        client = RealtimeClient(api_key="sk-...", model="gpt-realtime-mini")
        await client.connect()
        await client.send_session_update(instructions="...", voice="alloy")
        async for event in client.events():
            ...
        await client.aclose()
    """

    def __init__(self, api_key: str, model: str = "gpt-realtime-mini") -> None:
        """Initialize a RealtimeClient.

        Args:
            api_key: OpenAI API key for authentication.
            model: OpenAI Realtime model name (default: gpt-realtime-mini).
        """
        self._api_key = api_key
        self._model = model
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def connect(self) -> None:
        """Open the WebSocket. Raises if connection fails."""
        url = f"{OPENAI_REALTIME_URL}?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(url, additional_headers=headers)
        logger.info("openai_realtime.connected", extra={"operation": "openai_realtime.connect", "status": "success", "model": self._model})

    async def send_session_update(
        self,
        *,
        instructions: str,
        voice: str = "alloy",
        language_code: str = "hi",
        vad_silence_ms: int = 600,
    ) -> None:
        """Send the session.update event to configure the session.

        Args:
            instructions: System instructions for the assistant.
            voice: Voice name (e.g., "alloy", "echo", "shimmer").
            language_code: Language code for input transcription (e.g., "hi", "en").
            vad_silence_ms: Silence duration in ms before turn ends (VAD).
        """
        if self._ws is None:
            raise RuntimeError("connect() must be called before send_session_update")
        config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": instructions,
                "voice": voice,
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "input_audio_transcription": {
                    "model": "gpt-4o-transcribe",
                    "language": language_code,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "silence_duration_ms": vad_silence_ms,
                },
            },
        }
        await self._ws.send(json.dumps(config))

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Append a chunk of caller audio to the input buffer.

        Args:
            audio_bytes: Raw audio bytes in g711_ulaw format.
        """
        if self._ws is None:
            raise RuntimeError("connect() must be called before send_audio")
        if not audio_bytes:
            return
        event = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio_bytes).decode("ascii"),
        }
        await self._ws.send(json.dumps(event))

    async def events(self) -> AsyncIterator[dict]:
        """Yield parsed JSON events from OpenAI until the connection closes.

        Yields:
            dict: Parsed JSON event from the OpenAI Realtime API.

        Raises:
            RuntimeError: If connect() was not called before events().
        """
        if self._ws is None:
            raise RuntimeError("connect() must be called before events")
        try:
            async for raw in self._ws:
                try:
                    yield json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("openai_realtime.malformed_event",
                                   extra={"operation": "openai_realtime.events", "status": "failure", "error": "malformed_json", "raw": raw[:200]})
        except websockets.ConnectionClosed:
            logger.info("openai_realtime.disconnected", extra={"operation": "openai_realtime.events", "status": "success"})

    async def aclose(self) -> None:
        """Close the WebSocket if open.

        Handles errors gracefully; logs warnings if close fails.
        """
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.warning("openai_realtime.close_error",
                               extra={"operation": "openai_realtime.aclose", "status": "failure", "error": str(exc)})
            self._ws = None
