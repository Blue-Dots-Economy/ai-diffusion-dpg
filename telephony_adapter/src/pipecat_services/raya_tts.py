"""
telephony_adapter/src/pipecat_services/raya_tts.py

RayaTTSService — Pipecat TTSService backed by the Raya SSE streaming TTS API.

Receives text from upstream (via TTSSpeakFrame processed by the base class),
POSTs to the Raya /text-to-speech/stream endpoint, reads Server-Sent Events
containing base64 PCM F32LE chunks, converts each chunk from F32LE to PCM16,
and yields TTSAudioRawFrame objects at 8 kHz.

The VobizFrameSerializer downstream encodes PCM16 8 kHz → µ-law 8 kHz before
sending to Vobiz, so no additional format conversion is needed here.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import AsyncGenerator

import httpx
import numpy as np

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 8000
_NUM_CHANNELS = 1


class RayaTTSService(TTSService):
    """Synthesises speech via the Raya SSE streaming TTS endpoint.

    Each call to run_tts streams PCM F32LE audio chunks from Raya, converts
    them to PCM16 at 8 kHz, and yields TTSAudioRawFrame objects for the
    Pipecat pipeline.  The VobizFrameSerializer then encodes PCM16 → µ-law.

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
        tts_base_url = raya_cfg.get("tts_base_url", "")
        if not tts_base_url:
            raise ValueError("telephony_adapter.raya.tts_base_url is required")
        self._api_key = api_key
        self._base_url = tts_base_url.rstrip("/")
        self._voice_id = raya_cfg.get("voice_id", "voice_001")
        self._language = raya_cfg.get("tts_language") or raya_cfg.get("language", "hi")
        self._speed = float(raya_cfg.get("tts_speed", 1.0))
        self._tts_timeout = float(raya_cfg.get("tts_timeout_s", 30.0))
        super().__init__(
            sample_rate=_SAMPLE_RATE,
            settings=TTSSettings(model=None, voice=self._voice_id, language=self._language),
        )

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesise text to PCM16 audio via the Raya SSE streaming TTS endpoint.

        Streams F32LE PCM chunks from Raya SSE, converts each to PCM16, and
        yields TTSAudioRawFrame objects.  On HTTP error yields ErrorFrame.

        Args:
            text: The text to synthesise.
            context_id: Pipecat context ID for this TTS turn (passed to TTSAudioRawFrame).

        Yields:
            TTSAudioRawFrame per SSE chunk, or ErrorFrame on failure.
        """
        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "model": "standard",
            "language": self._language,
            "speed": self._speed,
            "sample_rate": _SAMPLE_RATE,
        }
        headers = {"X-API-Key": self._api_key}

        total_bytes = 0
        try:
            async with httpx.AsyncClient(timeout=self._tts_timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        latency_ms = int((time.time() - start) * 1000)
                        error_body = body.decode(errors="replace")[:400]
                        logger.error(
                            f"raya_tts.http_error HTTP {response.status_code}: {error_body}",
                            extra={
                                "operation": "raya_tts.run_tts",
                                "status": "failure",
                                "latency_ms": latency_ms,
                            },
                        )
                        yield ErrorFrame(error=f"Raya TTS HTTP {response.status_code}")
                        return

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if not raw or raw == "{}":
                            continue
                        try:
                            chunk_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if chunk_data.get("type") == "chunk" and "data" in chunk_data:
                            f32le_bytes = base64.b64decode(chunk_data["data"])
                            pcm16_bytes = _f32le_to_pcm16(f32le_bytes)
                            total_bytes += len(pcm16_bytes)
                            yield TTSAudioRawFrame(
                                audio=pcm16_bytes,
                                sample_rate=_SAMPLE_RATE,
                                num_channels=_NUM_CHANNELS,
                                context_id=context_id,
                            )

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "raya_tts.connection_error",
                extra={
                    "operation": "raya_tts.run_tts",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                },
            )
            yield ErrorFrame(error=f"Raya TTS connection error: {exc}")
            return

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "raya_tts.synthesized",
            extra={
                "operation": "raya_tts.run_tts",
                "status": "success",
                "latency_ms": latency_ms,
                "audio_bytes": total_bytes,
            },
        )


def _f32le_to_pcm16(f32le_bytes: bytes) -> bytes:
    """Convert raw PCM F32LE bytes to PCM16 bytes.

    Args:
        f32le_bytes: Raw bytes containing 32-bit float samples (little-endian).

    Returns:
        Raw bytes containing 16-bit signed integer samples, same count.
    """
    samples_f32 = np.frombuffer(f32le_bytes, dtype=np.float32)
    samples_i16 = (samples_f32 * 32767.0).clip(-32767, 32767).astype(np.int16)
    return samples_i16.tobytes()
