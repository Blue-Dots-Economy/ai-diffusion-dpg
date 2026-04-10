"""
reach_layer/server.py

FastAPI web server for the DPG Reach Layer block.
Serves the single-page chat UI at GET / and proxies turn requests to Agent Core.
Also proxies GET /user-history/{user_id} to the Memory Layer for session restore.

This module is the entry point for the web channel adapter. The CLI adapter
remains available via main.py for local development.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from config_loader import load_config
from src.web_reach import WebReachLayer
from src.base import TurnResult

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# OpenTelemetry instrumentation guard — module-level flag set once at startup
_HTTPX_INSTRUMENTED = False


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Payload sent by the browser for each chat turn."""
    session_id: str
    user_id: Optional[str] = None
    message: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(web_reach: WebReachLayer, config: dict) -> FastAPI:
    """Create and return the FastAPI application.

    Args:
        web_reach: Initialised WebReachLayer instance used to validate and
            format each turn.
        config: Full merged config dict. Reads agent_core_client and
            memory_layer_client sections.

    Returns:
        Configured FastAPI application.

    Raises:
        ValueError: If web_reach is None.
    """
    if web_reach is None:
        raise ValueError("web_reach must not be None")

    ac_cfg = config.get("agent_core_client", {})
    ac_endpoint = ac_cfg.get("endpoint", "http://localhost:8000/process_turn")
    ac_timeout = float(ac_cfg.get("timeout_s", 30.0))

    ml_cfg = config.get("memory_layer_client", {})
    ml_endpoint = ml_cfg.get("endpoint", "http://localhost:8002")
    ml_timeout = float(ml_cfg.get("timeout_s", 10.0))

    app = FastAPI(title="Reach Layer — Web Channel Adapter")
    FastAPIInstrumentor.instrument_app(app)

    # Paths to the React production build
    _dist = Path(__file__).parent / "web" / "dist"
    _assets = _dist / "assets"

    # Mount /assets — serves JS/CSS bundles built by Vite
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    # Shared HTTP clients — created once at startup to enable connection pooling.
    ac_client = httpx.Client(timeout=ac_timeout)
    ml_client = httpx.Client(timeout=ml_timeout)

    @app.on_event("shutdown")
    def _close_clients() -> None:
        """Close shared HTTP clients on shutdown."""
        ac_client.close()
        ml_client.close()

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        """Return service health status."""
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # GET /app-config — expose UI branding config to the browser
    # ------------------------------------------------------------------

    @app.get("/app-config")
    def app_config() -> dict[str, Any]:
        """Return UI branding and copy strings from the merged config.

        The browser fetches this at boot so all domain-specific text
        (app name, avatars, placeholder copy) comes from config rather
        than being hardcoded in the HTML.

        Returns:
            Dict of UI config keys from the merged dpg/domain YAML.
        """
        return config.get("ui", {})

    # ------------------------------------------------------------------
    # GET / — serve the chat UI
    # ------------------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        """Serve the React SPA entry point."""
        return FileResponse(str(_dist / "index.html"), media_type="text/html")

    # ------------------------------------------------------------------
    # POST /chat — proxy turn to Agent Core
    # ------------------------------------------------------------------

    @app.post("/chat")
    def chat(req: ChatRequest) -> dict[str, Any]:
        """Forward a chat turn to Agent Core and return the response.

        Validates the request, builds a TurnInput, calls Agent Core, and
        returns the formatted result.

        Args:
            req: Chat request with session_id, optional user_id, and message.

        Returns:
            Dict with response_text, was_escalated, session_id, latency_ms.
            Returns a safe error response on any failure.
        """
        start = time.time()
        with otel_trace.get_tracer(__name__).start_as_current_span("reach.inbound") as span:
            span.set_attribute("session_id", req.session_id or "")
            span.set_attribute("dpg.channel", "web")
            try:
                turn = web_reach.build_turn_input(req.session_id, req.user_id, req.message)
            except ValueError as e:
                span.record_exception(e)
                logger.warning(
                    "reach_server.chat_invalid",
                    extra={
                        "operation": "reach_server.chat",
                        "status": "failure",
                        "error": str(e),
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return {"response_text": f"[Invalid request: {e}]", "was_escalated": False,
                        "session_id": req.session_id or "", "latency_ms": 0}

            payload: dict = {
                "session_id": turn.session_id,
                "user_message": turn.user_message,
                "channel": turn.channel,
            }
            if turn.user_id:
                payload["user_id"] = turn.user_id

            # Retry once on timeout (exponential backoff: 1 s delay before retry).
            _last_timeout: httpx.TimeoutException | None = None
            data: dict = {}
            for _attempt in range(2):
                try:
                    response = ac_client.post(ac_endpoint, json=payload, timeout=ac_timeout)
                    response.raise_for_status()
                    data = response.json()
                    _last_timeout = None
                    break
                except httpx.TimeoutException as _te:
                    _last_timeout = _te
                    if _attempt == 0:
                        time.sleep(1.0)
                except httpx.ConnectError as e:
                    span.record_exception(e)
                    logger.error(
                        "reach_server.chat_connect_error",
                        extra={
                            "operation": "reach_server.chat",
                            "status": "failure",
                            "error": "Agent Core connection refused",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return {"response_text": "[Could not reach Agent Core. Is the backend running?]",
                            "was_escalated": False, "session_id": turn.session_id, "latency_ms": 0}
                except Exception as e:
                    span.record_exception(e)
                    logger.error(
                        "reach_server.chat_error",
                        extra={
                            "operation": "reach_server.chat",
                            "status": "failure",
                            "error": str(e),
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return {"response_text": f"[Unexpected error: {type(e).__name__}]",
                            "was_escalated": False, "session_id": turn.session_id, "latency_ms": 0}

            if _last_timeout is not None:
                span.record_exception(_last_timeout)
                logger.error(
                    "reach_server.chat_timeout",
                    extra={
                        "operation": "reach_server.chat",
                        "status": "failure",
                        "error": "Agent Core timeout after retry",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return {"response_text": "[Agent Core did not respond in time. Please try again.]",
                        "was_escalated": False, "session_id": turn.session_id, "latency_ms": 0}

            result = TurnResult(
                session_id=turn.session_id,
                response_text=data.get("response_text", ""),
                was_escalated=data.get("was_escalated", False),
                was_tool_used=data.get("was_tool_used", False),
                model_used=data.get("model_used", ""),
                latency_ms=int((time.time() - start) * 1000),
            )
            formatted = web_reach.format_result(result)
            logger.info(
                "reach_server.chat_success",
                extra={
                    "operation": "reach_server.chat",
                    "status": "success",
                    "session_id": turn.session_id,
                    "latency_ms": formatted["latency_ms"],
                },
            )
            return formatted

    # ------------------------------------------------------------------
    # GET /user-history/{user_id} — proxy to Memory Layer
    # ------------------------------------------------------------------

    @app.get("/user-history/{user_id}")
    def user_history(user_id: str) -> dict[str, Any]:
        """Fetch the active session and its chat history for a returning user.

        Proxies to Memory Layer GET /users/{user_id}/active-history.
        Returns safe defaults on any failure so the browser can proceed
        with a fresh session.

        Args:
            user_id: The user identifier to look up.

        Returns:
            Dict with session_id (str | None) and turns (list).
        """
        start = time.time()
        user_id = user_id.strip()
        if not user_id:
            return {"session_id": None, "turns": []}
        try:
            response = ml_client.get(
                f"{ml_endpoint}/users/{user_id}/active-history",
                timeout=ml_timeout,
            )
            response.raise_for_status()
            data = response.json()
            logger.info(
                "reach_server.user_history_success",
                extra={
                    "operation": "reach_server.user_history",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return data
        except Exception as e:
            logger.error(
                "reach_server.user_history_error",
                extra={
                    "operation": "reach_server.user_history",
                    "status": "failure",
                    "error": str(e),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"session_id": None, "turns": []}

    return app


# ---------------------------------------------------------------------------
# Application instance (used by uvicorn: server:app)
# ---------------------------------------------------------------------------

def _resolve_domain_path() -> str:
    """Resolve domain config path from CONFIG_FOLDER or fall back to config/domain.yaml."""
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        resolved = Path(config_folder) / "reach_layer.yaml"
        if resolved.exists():
            return str(resolved)
    return "config/domain.yaml"


_config = load_config("config/dpg.yaml", _resolve_domain_path())

# ---------------------------------------------------------------------------
# OpenTelemetry initialisation
# ---------------------------------------------------------------------------
try:
    from dpg_telemetry import init_otel
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    init_otel(service_name="reach_layer", config=_config)

    if not _HTTPX_INSTRUMENTED:
        try:
            HTTPXClientInstrumentor().instrument()
            _HTTPX_INSTRUMENTED = True
        except Exception as e:
            logger.warning(
                "reach_server.httpx_instrumentation_failed",
                extra={
                    "operation": "server.httpx_instrumentation",
                    "status": "failure",
                    "error": str(e),
                },
            )
except Exception as _otel_err:
    logger.warning(
        "reach_server.otel_init_skipped",
        extra={
            "operation": "server.otel_init",
            "status": "skipped",
            "error": str(_otel_err),
        },
    )

_web_reach = WebReachLayer(_config)
app = create_app(_web_reach, _config)
