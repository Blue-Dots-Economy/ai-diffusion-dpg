"""
telephony_adapter/server.py

FastAPI application for the Telephony Adapter DPG service.

Endpoints:
  POST /answer              — Vobiz webhook on call answered; returns XML with WebSocket URL.
  WebSocket /ws/{call_sid}  — Bidirectional audio stream per call.
  POST /campaign            — Trigger outbound call (also callable by Action Gateway).
  POST /recording-finished  — Vobiz webhook: recording stopped.
  POST /recording-ready     — Vobiz webhook: recording MP3 ready.
  GET  /health              — Liveness probe.

Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import json
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from config_loader import load_config
from src.campaign_manager import CampaignManager
from src.telephony_adapter import VobizTelephonyAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OTel — imported at module level so tests can patch server.init_otel
# ---------------------------------------------------------------------------
try:
    from dpg_telemetry import init_otel
except ImportError:
    def init_otel(service_name: str, config: dict) -> None:  # type: ignore[misc]
        """No-op fallback when dpg_telemetry is not installed."""


# ---------------------------------------------------------------------------
# Module-level singletons (set by create_app)
# ---------------------------------------------------------------------------
_adapter: VobizTelephonyAdapter | None = None
_campaign_manager: CampaignManager | None = None


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
    global _adapter, _campaign_manager

    if config is None:
        dpg_path = os.getenv("DPG_CONFIG_PATH", "config/telephony.yaml")
        domain_path = os.getenv("DOMAIN_CONFIG_PATH", "../dev-kit/configs/kkb/telephony_adapter.yaml")
        config = load_config(dpg_path, domain_path)

    init_otel("telephony_adapter", config)

    _adapter = VobizTelephonyAdapter(config)
    _campaign_manager = CampaignManager(config)

    public_url: str = config.get("telephony_adapter", {}).get("public_url", "")
    if not public_url:
        raise ValueError("telephony_adapter.public_url is required in config")
    # Convert http(s) scheme to ws(s) for the Stream URL
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")

    app = FastAPI(
        title="Telephony Adapter",
        description="DPG Reach Layer telephony channel adapter — Vobiz + Raya + Agent Core.",
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        """Handle Vobiz call-answered webhook; return XML with WebSocket stream URL.

        Vobiz POSTs form fields: CallSid, From, To, etc.
        Returns XML instructing Vobiz to open a WebSocket to /ws/{call_sid}.
        """
        form = await request.form()
        call_sid = form.get("CallSid", "unknown")
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream url="{ws_url}/ws/{call_sid}" bidirectional="true"/>\n'
            "</Response>"
        )
        return Response(content=xml, media_type="application/xml")

    @app.websocket("/ws/{call_sid}")
    async def websocket_endpoint(websocket: WebSocket, call_sid: str) -> None:
        """Bidirectional audio stream for an active call.

        Vobiz connects here after receiving the XML from /answer.
        The first message is the "start" event with call metadata.
        """
        if _adapter is None:
            raise RuntimeError("App not initialised via create_app()")
        await websocket.accept()
        caller_id = "unknown"
        first_msg = ""

        try:
            first_msg = await websocket.receive_text()
            data = json.loads(first_msg)
            if data.get("event") == "start":
                caller_id = (
                    data.get("start", {})
                    .get("customParameters", {})
                    .get("caller_id", "unknown")
                )
        except Exception as e:
            logger.warning(
                "server.ws_start_parse_error",
                extra={
                    "operation": "server.websocket_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.websocket_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )

        class _PrefixedWebSocket:
            """Wraps a WebSocket to prepend one already-consumed message."""

            def __init__(self, ws: WebSocket, prefixed: str) -> None:
                self._ws = ws
                self._prefix = prefixed
                self._sent = False

            async def __aiter__(self):
                """Yield the prefetched message then iterate remaining messages."""
                if not self._sent:
                    self._sent = True
                    yield self._prefix
                async for msg in self._ws.iter_text():
                    yield msg

            async def send(self, data: str) -> None:
                """Send a text message over the WebSocket."""
                await self._ws.send_text(data)

            async def close(self) -> None:
                """Close the underlying WebSocket."""
                await self._ws.close()

        wrapped = _PrefixedWebSocket(websocket, first_msg)
        _call_start = time.time()
        try:
            await _adapter.handle_call(call_sid, caller_id, wrapped)
            logger.info(
                "server.ws_call_completed",
                extra={
                    "operation": "server.websocket_endpoint",
                    "status": "success",
                    "call_sid": call_sid,
                    "latency_ms": int((time.time() - _call_start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "server.ws_call_error",
                extra={
                    "operation": "server.websocket_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "latency_ms": int((time.time() - _call_start) * 1000),
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    @app.post("/campaign")
    async def campaign(body: CampaignRequest) -> dict:
        """Trigger an outbound call to the given number.

        Called directly by operators or by Action Gateway as a connector tool
        (telephony_channel_switch) when Agent Core decides to switch channels.

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
