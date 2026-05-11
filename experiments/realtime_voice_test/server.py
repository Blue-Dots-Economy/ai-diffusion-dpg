"""FastAPI server for the realtime voice test.

Endpoints:
  GET  /health              — liveness probe.
  POST /answer              — Vobiz call-answered webhook; returns Stream XML.
  WS   /ws/{call_sid}       — Vobiz audio WS; runs the Pipecat pipeline.

Config is loaded from environment variables (see README).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from pipecat.pipeline.runner import PipelineRunner
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from pipeline import build_pipeline_task

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
VOBIZ_SAMPLE_RATE = 8000


def _read_config() -> dict:
    """Read settings from env at startup. Raises on missing required vars."""
    cfg = {}
    cfg["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    if not cfg["api_key"]:
        raise RuntimeError("OPENAI_API_KEY env var is required")
    cfg["vobiz_auth_id"] = os.environ.get("VOBIZ_AUTH_ID", "")
    if not cfg["vobiz_auth_id"]:
        raise RuntimeError("VOBIZ_AUTH_ID env var is required")
    cfg["vobiz_auth_token"] = os.environ.get("VOBIZ_AUTH_TOKEN", "")
    if not cfg["vobiz_auth_token"]:
        raise RuntimeError("VOBIZ_AUTH_TOKEN env var is required")
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        raise RuntimeError("PUBLIC_URL env var is required")
    cfg["public_url"] = public_url
    cfg["ws_url"] = public_url.replace("https://", "wss://").replace("http://", "ws://")
    cfg["model"] = os.environ.get("MODEL", "gpt-realtime-mini")
    cfg["voice"] = os.environ.get("VOICE", "alloy")
    cfg["language"] = os.environ.get("LANGUAGE", "hi")
    cfg["vad_silence_ms"] = int(os.environ.get("VAD_SILENCE_MS", "600"))
    return cfg


def create_app() -> FastAPI:
    """Construct the FastAPI app. Reads env once at startup; misconfig fails fast."""
    cfg = _read_config()
    logger.info(
        "server.startup",
        extra={
            "operation": "server.create_app",
            "status": "success",
            "public_url": cfg["public_url"],
            "model": cfg["model"],
            "voice": cfg["voice"],
            "language": cfg["language"],
            "vad_silence_ms": cfg["vad_silence_ms"],
        },
    )

    app = FastAPI(title="Realtime Voice Test (Pipecat)", version="0.2.0")

    @app.get("/health")
    def health() -> dict:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        """Vobiz call-answered webhook; return Stream XML with our WS URL."""
        form = await request.form()
        call_sid = str(form.get("CallUUID") or form.get("CallSid") or "unknown")
        stream_url = f"{cfg['ws_url']}/ws/{call_sid}"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream bidirectional="true" keepCallAlive="true"'
            f' contentType="audio/x-mulaw;rate={VOBIZ_SAMPLE_RATE}">{stream_url}</Stream>\n'
            "</Response>"
        )
        logger.info(
            "server.answer",
            extra={
                "operation": "server.answer",
                "status": "success",
                "call_sid": call_sid,
                "stream_url": stream_url,
            },
        )
        return Response(content=xml, media_type="application/xml")

    @app.websocket("/ws/{call_sid}")
    async def ws_endpoint(websocket: WebSocket, call_sid: str) -> None:
        """Vobiz audio WS; parse handshake, build transport + pipeline, run."""
        await websocket.accept()
        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.ws_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )

        try:
            _transport_type, call_data = await parse_telephony_websocket(websocket)
        except Exception as exc:
            logger.error(
                "server.handshake_failed",
                extra={
                    "operation": "server.ws_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            await websocket.close()
            return

        stream_id = call_data.get("stream_id") or call_sid
        vobiz_call_id = call_data.get("call_id") or call_sid

        serializer = VobizFrameSerializer(
            stream_id=stream_id,
            call_id=vobiz_call_id,
            auth_id=cfg["vobiz_auth_id"],
            auth_token=cfg["vobiz_auth_token"],
            params=VobizFrameSerializer.InputParams(
                vobiz_sample_rate=VOBIZ_SAMPLE_RATE,
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

        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = RESULTS_DIR / f"{ts}_{call_sid}" / "turns.jsonl"

        task = build_pipeline_task(
            transport=transport,
            call_sid=call_sid,
            out_path=out_path,
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg["voice"],
            language=cfg["language"],
            vad_silence_ms=cfg["vad_silence_ms"],
        )

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(transport, client):
            logger.info(
                "server.ws_disconnected",
                extra={
                    "operation": "server.ws_endpoint",
                    "status": "success",
                    "call_sid": call_sid,
                },
            )
            await task.cancel()

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        except Exception as exc:
            logger.error(
                "server.pipeline_error",
                extra={
                    "operation": "server.ws_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        finally:
            logger.info(
                "server.session_ended",
                extra={
                    "operation": "server.ws_endpoint",
                    "status": "success",
                    "call_sid": call_sid,
                },
            )

    return app


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8007"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
