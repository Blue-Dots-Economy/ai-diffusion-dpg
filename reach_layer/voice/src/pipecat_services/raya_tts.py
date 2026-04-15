"""
telephony_adapter/src/pipecat_services/raya_tts.py

RayaTTSService — Pipecat TTSService backed by the Raya SSE streaming TTS API.

Inherits TTSServiceBase (DPG contract) and Pipecat's TTSService.
The DPG synthesis logic lives in synthesize(); run_tts() is a thin Pipecat
bridge that calls synthesize() and wraps each chunk in a TTSAudioRawFrame.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
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

from src.pipecat_services.tts_base import TTSServiceBase

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 8000
_NUM_CHANNELS = 1


class RayaTTSService(TTSServiceBase, TTSService):
    """Synthesises speech via the Raya SSE streaming TTS endpoint.

    Inherits TTSServiceBase for the DPG interface contract and Pipecat's
    TTSService for pipeline integration. The synthesis logic lives in
    synthesize(); run_tts() delegates to it and wraps results in Pipecat frames.

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
        self._tts_model = raya_cfg.get("tts_model", "standard")
        self._tts_timeout = float(raya_cfg.get("tts_timeout_s", 30.0))
        TTSService.__init__(
            self,
            sample_rate=_SAMPLE_RATE,
            settings=TTSSettings(model=None, voice=self._voice_id, language=self._language),
        )

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesise text to PCM16 chunks via the Raya SSE streaming TTS endpoint.

        Streams F32LE PCM chunks from Raya SSE and converts each to PCM16.

        Args:
            text: The text to synthesise.

        Yields:
            Raw PCM16 bytes at 8000 Hz mono. Yields nothing on HTTP or
            connection errors (logs and returns cleanly).
        """
        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "model": self._tts_model,
            "language": self._language,
            "speed": self._speed,
            "sample_rate": _SAMPLE_RATE,
        }
        headers = {"X-API-Key": self._api_key}
        total_bytes = 0

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._tts_timeout) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            body_bytes = await response.aread()
                            error_body = body_bytes.decode(errors="replace")[:400]
                            logger.error(
                                "raya_tts.http_error",
                                extra={
                                    "operation": "raya_tts.synthesize",
                                    "status": "failure",
                                    "error": f"HTTP {response.status_code}: {error_body}",
                                    "latency_ms": int((time.time() - start) * 1000),
                                },
                            )
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
                                try:
                                    f32le_bytes = base64.b64decode(chunk_data["data"])
                                    pcm16_bytes = _f32le_to_pcm16(f32le_bytes)
                                except (ValueError, Exception) as exc:
                                    logger.warning(
                                        "raya_tts.chunk_decode_error",
                                        extra={
                                            "operation": "raya_tts.synthesize",
                                            "status": "failure",
                                            "error": f"{type(exc).__name__}: {exc}",
                                        },
                                    )
                                    continue
                                total_bytes += len(pcm16_bytes)
                                yield pcm16_bytes

                # Streaming completed successfully — don't retry
                break

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                latency_ms = int((time.time() - start) * 1000)
                if attempt == 0:
                    logger.warning(
                        "raya_tts.retrying",
                        extra={
                            "operation": "raya_tts.synthesize",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": latency_ms,
                        },
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.error(
                        "raya_tts.connection_error",
                        extra={
                            "operation": "raya_tts.synthesize",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": latency_ms,
                        },
                    )
                    return

        if total_bytes > 0:
            logger.info(
                "raya_tts.synthesized",
                extra={
                    "operation": "raya_tts.synthesize",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                    "audio_bytes": total_bytes,
                },
            )

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Pipecat hook: synthesise text and yield TTSAudioRawFrame objects.

        Delegates to synthesize(). Each PCM16 chunk becomes one TTSAudioRawFrame.
        Yields ErrorFrame if synthesis produced no audio, so the pipeline can react.

        Args:
            text: The text to synthesise.
            context_id: Pipecat context ID for this TTS turn.

        Yields:
            TTSAudioRawFrame per PCM16 chunk on success.
            ErrorFrame if synthesis failed (no audio produced).
        """
        yielded_any = False
        async for pcm16_bytes in self.synthesize(text):
            yielded_any = True
            yield TTSAudioRawFrame(
                audio=pcm16_bytes,
                sample_rate=_SAMPLE_RATE,
                num_channels=_NUM_CHANNELS,
                context_id=context_id,
            )
        if not yielded_any:
            yield ErrorFrame(error="Raya TTS produced no audio")


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
