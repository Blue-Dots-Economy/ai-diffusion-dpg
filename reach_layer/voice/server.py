"""
reach_layer/voice/server.py

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

from opentelemetry import trace as otel_trace

import src.bot as bot
from reach_layer_base import load_reach_config
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
        # Resolve dpg.yaml / domain.yaml: prefer env overrides → local
        # checkout under ../config/ → container cwd ./config/.
        from pathlib import Path as _P
        _voice_dir = _P(__file__).resolve().parent
        _local_reach_config = _voice_dir.parent / "config"

        dpg_env = os.getenv("DPG_CONFIG_PATH")
        if dpg_env:
            dpg_path = dpg_env
        elif (_local_reach_config / "dpg.yaml").exists():
            dpg_path = str(_local_reach_config / "dpg.yaml")
        else:
            dpg_path = "config/dpg.yaml"

        domain_env = os.getenv("DOMAIN_CONFIG_PATH")
        if domain_env:
            domain_path = domain_env
        elif (_local_reach_config / "domain.yaml").exists():
            domain_path = str(_local_reach_config / "domain.yaml")
        else:
            domain_path = "config/domain.yaml"

        config = load_reach_config(
            channel_name="voice",
            dpg_path=dpg_path,
            domain_path=domain_path,
        )

    _config = config
    init_otel("reach_layer.voice", config)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass  # Observability must not prevent startup
    _campaign_manager = CampaignManager(config)

    public_url: str = config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("public_url", "").rstrip("/")
    if not public_url:
        raise ValueError("reach_layer.channels.voice.public_url is required in config")
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
    # Shared registry: vobiz_call_id → asyncio.Future[str]
    # Populated by VobizRecordingSource.begin(); resolved by /recording-ready webhook.
    app.state.recording_url_registry = {}
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass  # Observability must not prevent startup

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
        # Pick the right field based on call direction:
        #   - inbound: From = the user calling us (correct)
        #   - outbound (campaign): From = VOBIZ_FROM_NUMBER (us); To = the
        #     user we're dialling. caller_id must be the user, never us.
        #
        # Two complementary signals — either is sufficient to detect outbound:
        #   1. Direction field (Plivo convention: "outbound-api" / "inbound").
        #   2. Comparing From against VOBIZ_FROM_NUMBER: if From matches our
        #      configured outbound caller ID, it's necessarily outbound — they
        #      could only have dialled FROM us.
        # Falling back to From is the safe default when neither signal fires
        # (matches the historical behaviour for inbound calls).
        direction = str(form.get("Direction") or "").lower()
        form_from = str(form.get("From") or "")
        form_to = str(form.get("To") or "")
        our_number = (os.environ.get("VOBIZ_FROM_NUMBER") or "").strip().lstrip("+")
        from_norm = form_from.strip().lstrip("+")
        is_outbound = (
            direction.startswith("outbound")
            or (our_number != "" and from_norm == our_number)
        )
        raw = form_to if is_outbound else form_from
        # Normalise to bare 10-digit local number for upstream lookups. Upstreams
        # like Blue Dots' fetch_profile expect the local form without the +91
        # country code; Vobiz passes E.164 (+91…). Strip the leading + and the
        # 91 prefix when present.
        caller_id = raw.lstrip("+")
        if caller_id.startswith("91") and len(caller_id) == 12:
            caller_id = caller_id[2:]
        _caller_id_map[call_sid] = caller_id
        # Evict oldest entries when the map exceeds its cap (orphaned calls)
        while len(_caller_id_map) > _CALLER_ID_MAP_MAX:
            _caller_id_map.popitem(last=False)
        stream_url = f"{ws_url}/ws/{call_sid}"
        logger.info(
            "server.answer",
            extra={
                "operation": "server.answer",
                "status": "success",
                "call_sid": call_sid,
                "caller_id": caller_id,
                "stream_url": stream_url,
                "form_keys": list(form.keys()),
            },
        )
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
        logger.info("server.answer_xml: %s", xml)
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
        with otel_trace.get_tracer(__name__).start_as_current_span("reach.inbound") as span:
            span.set_attribute("session_id", call_sid)
            span.set_attribute("dpg.channel", "voice")
            span.set_attribute("dpg.assembly_mode", "session")
            try:
                await bot.run_bot(websocket, call_sid, caller_id, _config)
            except Exception as exc:
                span.record_exception(exc)
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

    async def _parse_recording_webhook(request: Request) -> tuple[str, str, dict]:
        """Extract (call_id, recording_url, raw_fields) from a Vobiz webhook.

        Vobiz inherits Plivo's recording-callback shape — fields are POSTed as
        application/x-www-form-urlencoded with PascalCase names (CallUUID,
        RecordUrl, RecordingID, RecordingDuration, …). The earlier strict JSON
        model (`callSid` / `recordingUrl`) returned 422 for the real payload.
        This helper accepts either form or JSON and aliases the common variants.
        """
        content_type = (request.headers.get("content-type") or "").lower()
        raw: dict = {}
        try:
            if "application/json" in content_type:
                raw = await request.json()
            else:
                form = await request.form()
                raw = dict(form)
        except Exception as exc:
            logger.warning(
                "server.recording_webhook_parse_failed",
                extra={"operation": "server._parse_recording_webhook",
                       "status": "failure",
                       "error": f"{type(exc).__name__}: {exc}"},
            )
            raw = {}
        call_id = (
            raw.get("CallUUID") or raw.get("CallSid") or raw.get("callSid")
            or raw.get("call_uuid") or raw.get("call_sid") or ""
        )
        recording_url = (
            raw.get("RecordUrl") or raw.get("RecordingUrl")
            or raw.get("recordingUrl") or raw.get("record_url")
            or raw.get("recording_url") or ""
        )
        return str(call_id), str(recording_url), raw

    @app.post("/recording-finished")
    async def recording_finished(request: Request) -> dict:
        """Handle Vobiz webhook when recording has stopped.

        Body is parsed flexibly (form or JSON) — Vobiz sends Plivo-style form
        fields. We log the resolved fields plus the raw field-name set so any
        unrecognised vendor variant is visible.
        """
        call_id, recording_url, raw = await _parse_recording_webhook(request)
        logger.info(
            "server.recording_finished",
            extra={
                "operation": "server.recording_finished",
                "status": "success",
                "call_sid": call_id,
                "recording_url": recording_url,
                "raw_field_keys": sorted(raw.keys()),
            },
        )
        return {"status": "ok"}

    @app.post("/recording-ready")
    async def recording_ready(request: Request) -> dict:
        """Handle Vobiz webhook when recording MP3 is ready.

        Resolves the asyncio.Future registered by VobizRecordingSource.begin()
        so that VobizRecordingSource.end() can proceed to fetch the MP3.
        Accepts either form or JSON payload; aliases CallUUID/CallSid/callSid
        and RecordUrl/RecordingUrl/recordingUrl variants.
        """
        call_id, recording_url, raw = await _parse_recording_webhook(request)
        fut = app.state.recording_url_registry.pop(call_id, None) if call_id else None
        if fut is not None and not fut.done():
            fut.set_result(recording_url)
        logger.info(
            "server.recording_ready",
            extra={
                "operation": "server.recording_ready",
                "status": "success",
                "call_sid": call_id,
                "recording_url": recording_url,
                "had_future": fut is not None,
                "raw_field_keys": sorted(raw.keys()),
            },
        )
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    import uvicorn

    _app = create_app()
    port = int(os.getenv("PORT", "8006"))
    uvicorn.run(_app, host="0.0.0.0", port=port)
