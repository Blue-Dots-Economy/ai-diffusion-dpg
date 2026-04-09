"""
telephony_adapter/src/pipecat_services/raya_stt.py

RayaSTTService — Pipecat SegmentedSTTService backed by the Raya HTTP STT API.

Pipecat's SegmentedSTTService base class buffers AudioRawFrames between
VADUserStartedSpeakingFrame and VADUserStoppedSpeakingFrame events, wraps
the buffer into a WAV file, then calls run_stt(audio) with the complete WAV
bytes.  This service POSTs those bytes as multipart/form-data to the Raya
HTTP transcription endpoint and yields a TranscriptionFrame on success.
On any error it yields an ErrorFrame so the pipeline can continue.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

import httpx

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService

logger = logging.getLogger(__name__)

_RAYA_STT_URL = "https://hub.getraya.app/transcribe"


class RayaSTTService(SegmentedSTTService):
    """Transcribes one VAD-segmented utterance per call via the Raya HTTP STT API.

    Each call to run_stt receives a complete WAV file (PCM16, 8 kHz, mono)
    assembled by the SegmentedSTTService base class.  The WAV is sent as the
    ``file`` field of a multipart/form-data POST to Raya.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If api_key is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        self._api_key = api_key
        self._language = raya_cfg.get("stt_language") or raya_cfg.get("language", "hi")
        self._timeout = float(raya_cfg.get("stt_timeout_s", 30.0))
        super().__init__(
            sample_rate=8000,
            settings=STTSettings(model=None, language=self._language),
        )

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe one utterance via Raya HTTP multipart STT.

        Args:
            audio: Complete WAV file bytes (PCM16, 8 kHz, mono) for the utterance.

        Yields:
            TranscriptionFrame on success, ErrorFrame on HTTP or connection failure.
            Yields nothing if the transcript is empty or blank.
        """
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _RAYA_STT_URL,
                    headers={"X-API-Key": self._api_key},
                    files={"file": ("utterance.wav", audio, "audio/wav")},
                    data={"language": self._language},
                )

            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                error_body = response.text[:400]
                logger.error(
                    f"raya_stt.http_error HTTP {response.status_code}: {error_body}",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "failure",
                        "latency_ms": latency_ms,
                    },
                )
                yield ErrorFrame(error=f"Raya STT HTTP {response.status_code}")
                return

            data = response.json()
            transcript = data.get("transcript", "").strip()

            if not transcript:
                logger.info(
                    "raya_stt.empty_transcript",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "skipped",
                        "latency_ms": latency_ms,
                    },
                )
                return

            logger.info(
                "raya_stt.transcribed",
                extra={
                    "operation": "raya_stt.run_stt",
                    "status": "success",
                    "latency_ms": latency_ms,
                    "audio_bytes": len(audio),
                    "transcript_len": len(transcript),
                },
            )
            yield TranscriptionFrame(text=transcript, user_id="", timestamp="")

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "raya_stt.connection_error_retrying",
                extra={
                    "operation": "raya_stt.run_stt",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            await asyncio.sleep(0.5)
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        _RAYA_STT_URL,
                        headers={"X-API-Key": self._api_key},
                        files={"file": ("utterance.wav", audio, "audio/wav")},
                        data={"language": self._language},
                    )
                latency_ms = int((time.time() - start) * 1000)
                if response.status_code != 200:
                    error_body = response.text[:400]
                    logger.error(
                        f"raya_stt.http_error HTTP {response.status_code}: {error_body}",
                        extra={
                            "operation": "raya_stt.run_stt",
                            "status": "failure",
                            "latency_ms": latency_ms,
                        },
                    )
                    yield ErrorFrame(error=f"Raya STT HTTP {response.status_code}")
                    return
                data = response.json()
                transcript = data.get("transcript", "").strip()
                if not transcript:
                    logger.info(
                        "raya_stt.empty_transcript",
                        extra={
                            "operation": "raya_stt.run_stt",
                            "status": "skipped",
                            "latency_ms": latency_ms,
                        },
                    )
                    return
                logger.info(
                    "raya_stt.transcribed",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "success",
                        "latency_ms": latency_ms,
                        "audio_bytes": len(audio),
                        "transcript_len": len(transcript),
                    },
                )
                yield TranscriptionFrame(text=transcript, user_id="", timestamp="")
            except (httpx.ConnectError, httpx.TimeoutException) as retry_exc:
                latency_ms = int((time.time() - start) * 1000)
                logger.error(
                    "raya_stt.connection_error",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "failure",
                        "error": f"{type(retry_exc).__name__}: {retry_exc}",
                        "latency_ms": latency_ms,
                    },
                )
                yield ErrorFrame(error=f"Raya STT connection error: {retry_exc}")
