"""
telephony_adapter/src/bot.py

run_bot — per-call Pipecat pipeline factory for the Telephony Adapter.

Called once per inbound WebSocket connection from Vobiz.  Parses the Vobiz
telephony handshake to extract stream_id and call_id, builds the pipeline:

  FastAPIWebsocketTransport (VobizFrameSerializer)
    → VADProcessor (SileroVADAnalyzer)
    → RayaSTTService
    → AgentCoreLLMProcessor
    → RayaTTSService
    → FastAPIWebsocketTransport output

Sends a TTS greeting immediately on client connect.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService

logger = logging.getLogger(__name__)


async def run_bot(websocket: WebSocket, call_sid: str, config: dict) -> None:
    """Build and run the Pipecat pipeline for one Vobiz call.

    Parses the Vobiz WebSocket handshake (reads the two start messages that
    Vobiz sends before audio) to extract stream_id and call_id needed by
    VobizFrameSerializer.  Then assembles the full pipeline and runs it until
    the call ends or is escalated.

    Args:
        websocket: FastAPI WebSocket that has already been accepted by the caller.
        call_sid: Call SID from the URL path — used as the Agent Core user_id.
        config: Full merged config dict.
    """
    vobiz_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
    ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})

    # Parse the Vobiz telephony handshake. Vobiz is Plivo-compatible so
    # parse_telephony_websocket returns transport_type="plivo" and
    # call_data with stream_id / call_id from the start message.
    transport_type, call_data = await parse_telephony_websocket(websocket)
    stream_id = call_data.get("stream_id") or ""
    call_id = call_data.get("call_id") or call_sid

    logger.info(
        "bot.call_started",
        extra={
            "operation": "bot.run_bot",
            "status": "success",
            "call_sid": call_sid,
            "stream_id": stream_id,
            "transport_type": transport_type,
        },
    )

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=vobiz_cfg.get("auth_id", ""),
        auth_token=vobiz_cfg.get("auth_token", ""),
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=8000,
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

    session_id = str(uuid.uuid4())
    stt = RayaSTTService(config)
    agent = AgentCoreLLMProcessor(config, call_sid=call_sid, session_id=session_id)
    tts = RayaTTSService(config)

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            stt,
            agent,
            tts,
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        greeting = ac_cfg.get("greeting", "Hello, how can I help you today?")
        logger.info(
            "bot.greeting",
            extra={
                "operation": "bot.on_client_connected",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await task.queue_frame(TTSSpeakFrame(text=greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(
            "bot.call_ended",
            extra={
                "operation": "bot.on_client_disconnected",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
