"""
telephony_adapter/src/raya_tts_service.py

RayaTTSService — converts text to audio via the Raya/Bakbak SSE streaming TTS API.

Calls POST /v1/text-to-speech/stream and accumulates base64 PCM F32LE chunks
from Server-Sent Events until the "done" event, then returns concatenated audio bytes.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class RayaTTSService:
    """Synthesises speech audio from text via the Raya SSE streaming TTS endpoint.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If required config keys (api_key, tts_base_url) are missing.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        self._api_key = api_key
        tts_base_url = raya_cfg.get("tts_base_url", "")
        if not tts_base_url:
            raise ValueError("telephony_adapter.raya.tts_base_url is required")
        self._base_url = tts_base_url
        self._voice_id = raya_cfg.get("voice_id", "voice_001")
        self._language = raya_cfg.get("language", "hi")
        self._speed = float(raya_cfg.get("tts_speed", 1.0))
        self._tts_timeout = float(raya_cfg.get("tts_timeout_s", 30.0))

    async def synthesize(self, text: str) -> bytes:
        """Convert text to audio bytes via the Raya SSE streaming TTS API.

        Streams chunks from the SSE response and concatenates them into a
        single audio buffer. Returns immediately on empty text.

        Args:
            text: The text to synthesise. Empty string returns b"" immediately.

        Returns:
            Concatenated raw audio bytes (PCM F32LE) from all SSE chunks.

        Raises:
            Exception: If the HTTP request fails or Raya returns a non-200 status.
        """
        if not text or not text.strip():
            return b""

        from opentelemetry import trace as _otel_trace
        tracer = _otel_trace.get_tracer("telephony_adapter")
        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "language": self._language,
            "speed": self._speed,
        }
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

        with tracer.start_as_current_span("telephony.tts") as span:
            span.set_attribute("voice_id", self._voice_id)
            span.set_attribute("language", self._language)
            try:
                async with httpx.AsyncClient(timeout=self._tts_timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    span.set_attribute("status", "failure")
                    raise Exception(f"TTS synthesis failed: HTTP {response.status_code} — {response.text[:200]}")

                audio_chunks: list[bytes] = []
                for line in response.text.splitlines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw or raw == "{}":
                        continue
                    try:
                        chunk_data = json.loads(raw)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "raya_tts.chunk_parse_error",
                            extra={
                                "operation": "raya_tts_service.synthesize",
                                "status": "failure",
                                "error": f"JSONDecodeError: {e}",
                            },
                        )
                        continue
                    if chunk_data.get("type") == "chunk" and "data" in chunk_data:
                        audio_chunks.append(base64.b64decode(chunk_data["data"]))

                result = b"".join(audio_chunks)
                latency = int((time.time() - start) * 1000)
                span.set_attribute("status", "success")
                span.set_attribute("latency_ms", latency)
                span.set_attribute("audio_bytes", len(result))
                logger.info(
                    "raya_tts.synthesize",
                    extra={
                        "operation": "raya_tts_service.synthesize",
                        "status": "success",
                        "latency_ms": latency,
                        "audio_bytes": len(result),
                    },
                )
                return result

            except Exception as e:
                if "TTS synthesis failed" in str(e):
                    raise
                latency = int((time.time() - start) * 1000)
                span.set_attribute("status", "failure")
                logger.error(
                    "raya_tts.error",
                    extra={
                        "operation": "raya_tts_service.synthesize",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": latency,
                    },
                )
                raise Exception(f"TTS synthesis failed: {e}") from e
