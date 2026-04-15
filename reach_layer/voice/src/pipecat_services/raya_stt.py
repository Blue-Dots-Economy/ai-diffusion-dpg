"""
telephony_adapter/src/pipecat_services/raya_stt.py

RayaSTTService — Pipecat SegmentedSTTService backed by the Raya HTTP STT API.

Inherits STTServiceBase (DPG contract) and Pipecat's SegmentedSTTService.
The DPG transcription logic lives in transcribe(); run_stt() is a thin
Pipecat bridge that calls transcribe() and wraps the result in a
TranscriptionFrame.
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

from src.pipecat_services.stt_base import STTServiceBase

logger = logging.getLogger(__name__)

class RayaSTTService(STTServiceBase, SegmentedSTTService):
    """Transcribes one VAD-segmented utterance per call via the Raya HTTP STT API.

    Inherits STTServiceBase for the DPG interface contract and Pipecat's
    SegmentedSTTService for pipeline integration. The transcription logic
    lives in transcribe(); run_stt() delegates to it.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If api_key or stt_url is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        stt_url = raya_cfg.get("stt_wss_url") or raya_cfg.get("stt_url", "")
        if not stt_url:
            raise ValueError("telephony_adapter.raya.stt_wss_url is required")
        self._api_key = api_key
        self._stt_url = stt_url
        self._language = raya_cfg.get("stt_language") or raya_cfg.get("language", "hi")
        self._timeout = float(raya_cfg.get("stt_timeout_s", 30.0))
        SegmentedSTTService.__init__(
            self,
            sample_rate=8000,
            settings=STTSettings(model=None, language=self._language),
        )

    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete utterance via Raya HTTP multipart STT.

        Sends the WAV bytes as multipart/form-data. Retries once on
        connection or timeout errors with a 500ms backoff.

        Args:
            audio: Complete WAV file bytes (PCM16, 8 kHz, mono).

        Returns:
            Transcribed text on success.
            Empty string if Raya returned no speech (genuinely silent utterance).
            None on service error (HTTP error, timeout, or malformed response).
        """
        start = time.time()
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._stt_url,
                        headers={"X-API-Key": self._api_key},
                        files={"file": ("utterance.wav", audio, "audio/wav")},
                        data={"language": self._language},
                    )
                latency_ms = int((time.time() - start) * 1000)
                if response.status_code != 200:
                    error_body = response.text[:400]
                    logger.error(
                        "raya_stt.http_error",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"HTTP {response.status_code}: {error_body}",
                            "latency_ms": latency_ms,
                        },
                    )
                    return None
                try:
                    transcript = response.json().get("transcript", "").strip()
                except ValueError as exc:
                    logger.error(
                        "raya_stt.json_parse_error",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"JSONDecodeError: {exc}",
                            "latency_ms": latency_ms,
                        },
                    )
                    return None
                if not transcript:
                    logger.info(
                        "raya_stt.empty_transcript",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "skipped",
                            "latency_ms": latency_ms,
                        },
                    )
                    return ""  # silent utterance — distinct from service error (None)
                logger.info(
                    "raya_stt.transcribed",
                    extra={
                        "operation": "raya_stt.transcribe",
                        "status": "success",
                        "latency_ms": latency_ms,
                        "audio_bytes": len(audio),
                        "transcript_len": len(transcript),
                    },
                )
                return transcript
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt == 0:
                    logger.warning(
                        "raya_stt.retrying",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.error(
                        "raya_stt.connection_error",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return None
        return None

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Pipecat hook: transcribe audio and yield TranscriptionFrame or ErrorFrame.

        Delegates to transcribe(). Yields TranscriptionFrame on success,
        ErrorFrame if transcription fails so the pipeline can react.

        Args:
            audio: Complete WAV file bytes assembled by SegmentedSTTService.

        Yields:
            TranscriptionFrame on success.
            ErrorFrame if transcription failed (service error, timeout, or bad response).
            Nothing if the utterance was genuinely silent/empty.
        """
        transcript = await self.transcribe(audio)
        if transcript:
            yield TranscriptionFrame(text=transcript, user_id="", timestamp="")
        elif transcript is None:
            # None means a service error occurred (empty string means silent audio).
            yield ErrorFrame(error="Raya STT returned no transcript")
