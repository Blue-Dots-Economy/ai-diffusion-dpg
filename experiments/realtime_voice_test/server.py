"""FastAPI server: Vobiz webhook + WebSocket endpoint.

Endpoints:
  GET  /health              — liveness probe
  POST /answer              — Vobiz call-answered webhook, returns Stream XML
  WS   /ws/{call_sid}       — bidirectional audio bridge to OpenAI Realtime

Config is loaded from environment variables (see README).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response

import bridge
from prompts import get_prompt

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"


def _read_config() -> dict:
    """Read settings from environment, validating required ones."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var is required")
    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        raise RuntimeError("PUBLIC_URL env var is required")
    return {
        "api_key": api_key,
        "public_url": public_url,
        "ws_url": public_url.replace("https://", "wss://").replace("http://", "ws://"),
        "prompt_name": os.getenv("PROMPT_NAME", "SHORT_HINDI"),
        "model": os.getenv("MODEL", "gpt-realtime-mini"),
        "voice": os.getenv("VOICE", "alloy"),
        "vad_silence_ms": int(os.getenv("VAD_SILENCE_MS", "600")),
    }


def create_app() -> FastAPI:
    """Construct the FastAPI app. Reads env once at startup."""
    cfg = _read_config()
    # Resolve the prompt now so a bad PROMPT_NAME crashes at startup, not mid-call.
    instructions = get_prompt(cfg["prompt_name"])
    logger.info(
        "server.startup",
        extra={
            "operation": "server.create_app",
            "status": "success",
            "public_url": cfg["public_url"],
            "model": cfg["model"],
            "voice": cfg["voice"],
            "prompt_name": cfg["prompt_name"],
            "vad_silence_ms": cfg["vad_silence_ms"],
        },
    )

    app = FastAPI(title="Realtime Voice Test", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        form = await request.form()
        call_sid = str(form.get("CallUUID") or form.get("CallSid") or "unknown")
        stream_url = f"{cfg['ws_url']}/ws/{call_sid}"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream bidirectional="true" keepCallAlive="true"'
            f' contentType="audio/x-mulaw;rate=8000">{stream_url}</Stream>\n'
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
            await bridge.run(
                websocket,
                call_sid=call_sid,
                api_key=cfg["api_key"],
                model=cfg["model"],
                voice=cfg["voice"],
                prompt_name=cfg["prompt_name"],
                instructions=instructions,
                vad_silence_ms=cfg["vad_silence_ms"],
                results_dir=RESULTS_DIR,
            )
        except Exception as exc:
            logger.error(
                "server.ws_error",
                extra={
                    "operation": "server.ws_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    return app


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8007"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
