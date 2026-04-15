"""
telephony_adapter/server.py

FastAPI application for the Telephony Adapter DPG service.

Endpoints:
  POST /answer              — Vobiz webhook on call answered; returns XML with WebSocket URL.
  WebSocket /ws/{call_sid}  — Bidirectional audio stream per call.
  POST /campaign            — Trigger outbound call.
  POST /recording-finished  — Vobiz webhook: recording stopped.
  POST /recording-ready     — Vobiz webhook: recording MP3 ready.
  GET  /health              — Liveness probe.

Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import os

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

import src.bot as bot
from config_loader import load_config
from src.campaign_manager import CampaignManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OTel
# ---------------------------------------------------------------------------
try:
    from dpg_telemetry import init_otel
except ImportError:
    def init_otel(service_name: str, config: dict) -> None:  # type: ignore[misc]
        """No-op fallback when dpg_telemetry is not installed."""


# ---------------------------------------------------------------------------
# Module-level singletons (set by create_app)
# ---------------------------------------------------------------------------
_campaign_manager: CampaignManager | None = None
_config: dict | None = None


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class CampaignRequest(BaseModel):
    """Request body for POST /campaign."""

    to_number: str


class RecordingWebhook(BaseModel):
    """Vobiz recording webhook payload."""

    callSid: str = ""
    recordingUrl: str = ""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config: dict | None = None) -> FastAPI:
    """Construct the FastAPI application and wire up all singletons.

    Args:
        config: Optional pre-loaded config dict. Loads from YAML files if None.

    Returns:
        Configured FastAPI application.
    """
    global _campaign_manager, _config

    if config is None:
        dpg_path = os.getenv("DPG_CONFIG_PATH", "config/telephony.yaml")
        domain_path = os.getenv(
            "DOMAIN_CONFIG_PATH", "../dev-kit/configs/kkb/telephony_adapter.yaml"
        )
        config = load_config(dpg_path, domain_path)

    _config = config
    init_otel("telephony_adapter", config)
    _campaign_manager = CampaignManager(config)

    public_url: str = config.get("telephony_adapter", {}).get("public_url", "")
    if not public_url:
        raise ValueError("telephony_adapter.public_url is required in config")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")

    # Maps call_sid → caller_id (E.164) so the WebSocket endpoint can retrieve it.
    # Populated by /answer; consumed and cleared by /ws/{call_sid}.
    # Capped at 1000 entries to prevent unbounded growth from orphaned calls
    # (Vobiz called /answer but WebSocket never connected).
    from collections import OrderedDict
    _caller_id_map: OrderedDict[str, str] = OrderedDict()
    _CALLER_ID_MAP_MAX = 1000

    # Operator singleton for XML generation — created once, stateless.
    from src.operators.vobiz_operator import VobizOperator as _VobizOperator
    try:
        _operator = _VobizOperator(config)
    except ValueError as exc:
        logger.warning(
            "server.operator_init_failed",
            extra={
                "operation": "server.create_operator",
                "status": "failure",
                "error": str(exc),
            },
        )
        _operator = None

    app = FastAPI(
        title="Telephony Adapter",
        description="DPG Reach Layer telephony channel adapter — Vobiz + Raya + Agent Core.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        """Handle Vobiz call-answered webhook; return XML with WebSocket stream URL.

        Vobiz POSTs form fields: CallSid (or CallUUID), From, To, etc.
        Returns XML instructing Vobiz to open a bidirectional WebSocket to
        /ws/{call_sid}.
        """
        form = await request.form()
        call_sid = str(form.get("CallUUID") or form.get("CallSid") or "unknown")
        caller_id = str(form.get("From") or "")
        _caller_id_map[call_sid] = caller_id
        # Evict oldest entries when the map exceeds its cap (orphaned calls)
        while len(_caller_id_map) > _CALLER_ID_MAP_MAX:
            _caller_id_map.popitem(last=False)
        stream_url = f"{ws_url}/ws/{call_sid}"
        if _operator is not None:
            xml = _operator.webhook_response_xml(stream_url)
        else:
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f'  <Stream bidirectional="true" keepCallAlive="true"'
                f' contentType="audio/x-mulaw;rate=8000">'
                f"{stream_url}</Stream>\n"
                "</Response>"
            )
        return Response(content=xml, media_type="application/xml")

    @app.websocket("/ws/{call_sid}")
    async def websocket_endpoint(websocket: WebSocket, call_sid: str) -> None:
        """Bidirectional audio stream for an active call.

        Accepts the WebSocket then hands it to bot.run_bot which owns the full
        Pipecat pipeline lifecycle: parses the Vobiz handshake, runs the
        VAD → STT → Agent Core → TTS pipeline, and closes on call end.
        """
        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.websocket_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await websocket.accept()
        caller_id = _caller_id_map.pop(call_sid, "")
        try:
            await bot.run_bot(websocket, call_sid, caller_id, _config)
        except Exception as exc:
            logger.error(
                "server.ws_error",
                extra={
                    "operation": "server.websocket_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    @app.post("/campaign")
    async def campaign(body: CampaignRequest) -> dict:
        """Trigger an outbound call to the given number.

        Args:
            body: Contains the destination phone number.

        Returns:
            Dict with callSid of the initiated call.

        Raises:
            HTTPException 400: If to_number is empty.
        """
        if _campaign_manager is None:
            raise RuntimeError("App not initialised via create_app()")
        if not body.to_number or not body.to_number.strip():
            raise HTTPException(status_code=400, detail="to_number must not be empty")
        result = await _campaign_manager.initiate_call(to_number=body.to_number)
        return result

    @app.post("/recording-finished")
    async def recording_finished(body: RecordingWebhook) -> dict:
        """Handle Vobiz webhook when recording has stopped.

        Args:
            body: Webhook payload containing callSid and recordingUrl.

        Returns:
            Dict with status ok.
        """
        logger.info(
            "server.recording_finished",
            extra={
                "operation": "server.recording_finished",
                "status": "success",
                "call_sid": body.callSid,
            },
        )
        return {"status": "ok"}

    @app.post("/recording-ready")
    async def recording_ready(body: RecordingWebhook) -> dict:
        """Handle Vobiz webhook when recording MP3 is ready.

        Args:
            body: Webhook payload containing callSid and recordingUrl.

        Returns:
            Dict with status ok.
        """
        logger.info(
            "server.recording_ready",
            extra={
                "operation": "server.recording_ready",
                "status": "success",
                "call_sid": body.callSid,
            },
        )
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    import uvicorn

    _app = create_app()
    port = int(os.getenv("PORT", "8006"))
    uvicorn.run(_app, host="0.0.0.0", port=port)
