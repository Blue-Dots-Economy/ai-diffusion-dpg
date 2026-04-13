"""
telephony_adapter/src/vobiz_adapter.py

VobizAdapter — concrete TelephonyAdapterBase for the Vobiz telephony platform.

Owns the full per-call lifecycle: parse handshake, build Pipecat pipeline,
run until call ends, teardown. Composes VobizOperator, SileroVADWrapper,
RayaSTTService, AgentCoreLLMProcessor, and RayaTTSService.

caller_id (E.164 phone number) is used as user_id so the Memory Layer can
recognise returning callers across sessions.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor

from src.base import TelephonyAdapterBase, TelephonyError
from src.operators.vobiz_operator import VobizOperator
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor
from src.vad.silero_vad import SileroVADWrapper

logger = logging.getLogger(__name__)


class VobizAdapter(TelephonyAdapterBase):
    """Telephony adapter for the Vobiz platform.

    Implements TelephonyAdapterBase by composing operator, VAD, STT, LLM,
    and TTS components into a Pipecat pipeline. One instance is created per
    call and discarded after teardown.

    Args:
        config: Full merged config dict.

    Raises:
        ValueError: If config is None.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        self._config = config
        self._operator = VobizOperator(config)
        self._vad_wrapper = SileroVADWrapper()
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        self._greeting = ac_cfg.get("greeting", "Hello, how can I help you today?")
        self._sample_rate = int(
            config.get("telephony_adapter", {}).get("vobiz", {}).get("sample_rate", 8000)
        )

    async def handle_call(self, call_sid: str, caller_id: str, websocket: WebSocket) -> None:
        """Handle the full lifecycle of one Vobiz inbound call.

        Parses the Vobiz handshake, builds the VAD→STT→Agent→TTS pipeline,
        sends a greeting on connect, and runs until the call ends.

        Args:
            call_sid: Vobiz CallUUID — opaque call identifier.
            caller_id: Caller E.164 phone number, used as user_id for Memory Layer.
            websocket: Accepted WebSocket connection from Vobiz.

        Raises:
            TelephonyError: If the handshake fails.
        """
        try:
            stream_id, call_id = await self._operator.parse_handshake(websocket)
        except Exception as exc:
            raise TelephonyError(f"Handshake failed for {call_sid}: {exc}") from exc

        transport = self._operator.create_transport(
            websocket, stream_id, call_id or call_sid
        )
        vad_analyzer = self._vad_wrapper.create_analyzer(self._config)
        session_id = str(uuid.uuid4())

        stt = RayaSTTService(self._config)
        agent = AgentCoreLLMProcessor(
            self._config,
            call_sid=call_sid,
            session_id=session_id,
            user_id=caller_id,
        )
        tts = RayaTTSService(self._config)
        sanitizer = TTSTextSanitizerProcessor()

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                stt,
                agent,
                sanitizer,
                tts,
                transport.output(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=self._sample_rate,
                audio_out_sample_rate=self._sample_rate,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def _on_connected(transport, client):
            logger.info(
                "vobiz_adapter.call_connected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                    "session_id": session_id,
                },
            )
            try:
                await task.queue_frame(TTSSpeakFrame(text=self._greeting))
            except Exception as exc:
                logger.error(
                    "vobiz_adapter.greeting_failed",
                    extra={
                        "operation": "vobiz_adapter._on_connected",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_sid": call_sid,
                    },
                )

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(transport, client):
            logger.info(
                "vobiz_adapter.call_disconnected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                },
            )
            try:
                await task.cancel()
            except Exception as exc:
                logger.warning(
                    "vobiz_adapter.cancel_failed",
                    extra={
                        "operation": "vobiz_adapter._on_disconnected",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_sid": call_sid,
                    },
                )

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    async def teardown(self, call_sid: str) -> None:
        """Log call completion. Pipecat handles WebSocket resource cleanup.

        Args:
            call_sid: The call SID whose resources should be released.
        """
        logger.info(
            "vobiz_adapter.teardown",
            extra={
                "operation": "vobiz_adapter.teardown",
                "status": "success",
                "call_sid": call_sid,
            },
        )
