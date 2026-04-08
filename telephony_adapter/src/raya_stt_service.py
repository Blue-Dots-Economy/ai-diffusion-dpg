"""
telephony_adapter/src/raya_stt_service.py

RayaSTTService — transcribes audio utterances via the Raya/Bakbak WebSocket STT API.

Called by VobizTelephonyAdapter once per utterance (after silence detection).
Sends base64-encoded WAV audio over WSS and returns the transcript string.
Retries once on connection failure with exponential backoff.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import websockets

logger = logging.getLogger(__name__)


class RayaSTTService:
    """Transcribes audio bytes via the Raya WebSocket STT endpoint.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If required config keys (api_key, stt_wss_url) are missing.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        self._api_key = api_key
        wss_url = raya_cfg.get("stt_wss_url", "")
        if not wss_url:
            raise ValueError("telephony_adapter.raya.stt_wss_url is required")
        self._wss_url = wss_url
        self._language = raya_cfg.get("language", "hi")
        self._max_retries = 2

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe raw audio bytes to text via the Raya WebSocket STT API.

        Sends the audio as a single base64-encoded payload per connection.
        Retries once on transient connection failures.

        Args:
            audio: Raw audio bytes (µ-law 8000 Hz from Vobiz). Empty bytes
                   are returned immediately as an empty string.

        Returns:
            Transcript string, or empty string if audio is empty.

        Raises:
            Exception: If transcription fails after retries, or if Raya returns
                       an error status.
        """
        if not audio:
            return ""

        from opentelemetry import trace as _otel_trace
        tracer = _otel_trace.get_tracer("telephony_adapter")
        start = time.time()
        last_error: Exception | None = None

        with tracer.start_as_current_span("telephony.stt") as span:
            span.set_attribute("language", self._language)
            for attempt in range(self._max_retries):
                try:
                    transcript = await self._call_raya_wss(audio)
                    latency = int((time.time() - start) * 1000)
                    span.set_attribute("status", "success")
                    span.set_attribute("latency_ms", latency)
                    logger.info(
                        "raya_stt.transcribe",
                        extra={
                            "operation": "raya_stt_service.transcribe",
                            "status": "success",
                            "latency_ms": latency,
                            "language": self._language,
                        },
                    )
                    return transcript
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "raya_stt.retry",
                        extra={
                            "operation": "raya_stt_service.transcribe",
                            "status": "failure",
                            "attempt": attempt + 1,
                            "error": f"{type(e).__name__}: {e}",
                        },
                    )
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(0.5 * (2 ** attempt))

            latency = int((time.time() - start) * 1000)
            span.set_attribute("status", "failure")
            span.set_attribute("latency_ms", latency)
            logger.error(
                "raya_stt.failed",
                extra={
                    "operation": "raya_stt_service.transcribe",
                    "status": "failure",
                    "latency_ms": latency,
                    "error": str(last_error),
                },
            )
            raise Exception(f"STT transcription failed after {self._max_retries} attempts: {last_error}")

    async def _call_raya_wss(self, audio: bytes) -> str:
        """Open a Raya WebSocket connection, send audio, and return transcript.

        Args:
            audio: Raw audio bytes to transcribe.

        Returns:
            Transcript string from Raya.

        Raises:
            Exception: On connection error or Raya error response.
        """
        headers = {"X-API-Key": self._api_key}
        payload = json.dumps({
            "audio_base64": base64.b64encode(audio).decode(),
            "language": self._language,
        })

        async with websockets.connect(
            self._wss_url,
            additional_headers=headers,
            open_timeout=5,
            close_timeout=5,
        ) as ws:
            await ws.send(payload)
            raw_response = await ws.recv()

        response = json.loads(raw_response)
        if response.get("status") == "error" or "error" in response:
            raise Exception(
                f"STT transcription failed: {response.get('error', response)}"
            )
        return response.get("transcript", "")
