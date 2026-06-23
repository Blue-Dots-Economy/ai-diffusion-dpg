"""
reach_layer/web/server.py

FastAPI web server for the DPG Reach Layer — web channel.
Serves the single-page chat UI at GET / and proxies turn requests to Agent Core.
Also proxies GET /user-history/{user_id} to the Memory Layer for session restore.

Direct-mode channel — each POST /chat triggers a synchronous POST /process_turn
against Agent Core and returns the JSON response to the browser. No SSE path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from reach_layer_base import DoneEvent, SentenceEvent, load_reach_config
from src.auth import (
    AuthError,
    Reason,
    issue_session_token,
    verify_api_key as _verify_api_key,
    verify_google_id_token,
    verify_session_token,
)
from src.web_reach import WebReachLayer

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
    fresh: bool = False  # True on first turn of a "New chat" — disables session adoption


class GoogleAuthRequest(BaseModel):
    """Payload posted by the SPA after Google Identity Services returns an ID token."""
    credential: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

async def _chat_direct_mode(
    ac_client: httpx.Client,
    ac_endpoint: str,
    ac_timeout: float,
    session_id: str,
    turn: dict,
    fresh: bool,
    web_reach: WebReachLayer,
    span: Any,
    start: float,
) -> dict[str, Any]:
    """Direct assembly-mode path: POST /process_turn once, return TurnResult.

    Uses a sync httpx client for compatibility with the existing retry and
    error-handling shape; the outer endpoint is async, so the blocking call
    runs in a worker thread.
    """
    payload: dict = {
        "session_id": session_id,
        "user_message": turn["user_message"],
        "channel": turn["channel"],
        "fresh": bool(fresh),
    }
    if turn["user_id"]:
        payload["user_id"] = turn["user_id"]

    def _blocking_call() -> tuple[Optional[dict], Optional[Exception]]:
        """Run the two-attempt POST inside a worker thread."""
        _last_timeout: httpx.TimeoutException | None = None
        for _attempt in range(2):
            try:
                response = ac_client.post(ac_endpoint, json=payload, timeout=ac_timeout)
                response.raise_for_status()
                return response.json(), None
            except httpx.TimeoutException as _te:
                _last_timeout = _te
                if _attempt == 0:
                    time.sleep(1.0)
            except httpx.ConnectError as e:
                return None, e
            except Exception as e:
                return None, e
        return None, _last_timeout

    data, err = await asyncio.to_thread(_blocking_call)

    if err is not None:
        span.record_exception(err)
        if isinstance(err, httpx.TimeoutException):
            logger.error(
                "reach_server.chat_timeout",
                extra={
                    "operation": "reach_server.chat",
                    "status": "failure",
                    "error": "Agent Core timeout after retry",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {
                "response_text": "We're having trouble connecting to the AI service right now. Please try again shortly.",
                "was_escalated": False,
                "session_id": session_id,
                "latency_ms": 0,
                "error_type": "timeout",
                "error_message": "We're having trouble connecting to the AI service right now. Please try again shortly.",
            }
        if isinstance(err, httpx.ConnectError):
            logger.error(
                "reach_server.chat_connect_error",
                extra={
                    "operation": "reach_server.chat",
                    "status": "failure",
                    "error": "Agent Core connection refused",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {
                "response_text": "We're having trouble connecting to the AI service right now. Please try again shortly.",
                "was_escalated": False,
                "session_id": session_id,
                "latency_ms": 0,
                "error_type": "backend_unreachable",
                "error_message": "We're having trouble connecting to the AI service right now. Please try again shortly.",
            }
        logger.error(
            "reach_server.chat_error",
            extra={
                "operation": "reach_server.chat",
                "status": "failure",
                "error": str(err),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {
            "response_text": "We're experiencing a temporary issue with the AI service. Please try again.",
            "was_escalated": False,
            "session_id": session_id,
            "latency_ms": 0,
            "error_type": "internal_server_error",
            "error_message": "We're experiencing a temporary issue with the AI service. Please try again.",
        }

    latency_ms = int((time.time() - start) * 1000)
    formatted = web_reach.format_result(session_id, data, latency_ms)
    logger.info(
        "reach_server.chat_success",
        extra={
            "operation": "reach_server.chat",
            "status": "success",
            "assembly_mode": "direct",
            "session_id": session_id,
            "latency_ms": formatted["latency_ms"],
        },
    )
    return formatted


async def _chat_session_mode(
    web_reach: WebReachLayer,
    session_id: str,
    user_id: Optional[str],
    message: str,
    span: Any,
    start: float,
) -> dict[str, Any]:
    """Session assembly-mode path: submit input, consume SSE, aggregate sentences.

    The browser API is unchanged: sentences arriving on the SSE stream are
    concatenated into ``response_text`` and the aggregated dict is returned
    when the stream's DoneEvent arrives. This keeps ``/chat`` a single-shot
    JSON endpoint while still exercising the TurnAssembler / stream_turn path
    under the hood.
    """
    try:
        await web_reach.submit_input(session_id, message, user_id)
    except Exception as e:
        span.record_exception(e)
        logger.error(
            "reach_server.chat_session_submit_error",
            extra={
                "operation": "reach_server.chat",
                "status": "failure",
                "assembly_mode": "session",
                "error": f"{type(e).__name__}: {e}",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {
            "response_text": "We're experiencing a temporary issue with the AI service. Please try again.",
            "was_escalated": False,
            "session_id": session_id,
            "latency_ms": 0,
            "error_type": "internal_server_error",
            "error_message": "We're experiencing a temporary issue with the AI service. Please try again.",
        }

    parts: list[str] = []
    was_escalated = False
    was_tool_used = False
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    try:
        # Web intentionally does NOT pass user_id here — the /chat endpoint is
        # request/response per user message, so the SSE only opens AFTER the user
        # sends their first message. Passing user_id would trigger proactive
        # opening_phrase emission, which would arrive interleaved with the LLM's
        # reply to the first message. Web users receive opening_phrase via the
        # orchestrator's first-turn gate (orchestrator.py) as the response_text
        # of the first /chat call — identical to today's behavior. (GH-149)
        async for event in web_reach.subscribe_events(session_id):
            if isinstance(event, SentenceEvent):
                parts.append(event.text)
            elif isinstance(event, DoneEvent):
                was_escalated = event.was_escalated
                was_tool_used = event.was_tool_used
                error_type = getattr(event, "error_type", None)
                error_message = getattr(event, "error_message", None)
                break
    except Exception as e:
        span.record_exception(e)
        logger.error(
            "reach_server.chat_session_stream_error",
            extra={
                "operation": "reach_server.chat",
                "status": "failure",
                "assembly_mode": "session",
                "error": f"{type(e).__name__}: {e}",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {
            "response_text": "We're having trouble connecting to the AI service right now. Please try again shortly.",
            "was_escalated": False,
            "session_id": session_id,
            "latency_ms": 0,
            "error_type": "stream_error",
            "error_message": "We're having trouble connecting to the AI service right now. Please try again shortly.",
        }

    latency_ms = int((time.time() - start) * 1000)
    formatted = {
        "response_text": "".join(parts).strip(),
        "was_escalated": was_escalated,
        "was_tool_used": was_tool_used,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "error_type": error_type,
        "error_message": error_message,
    }
    logger.info(
        "reach_server.chat_success",
        extra={
            "operation": "reach_server.chat",
            "status": "success",
            "assembly_mode": "session",
            "session_id": session_id,
            "latency_ms": latency_ms,
            "sentence_count": len(parts),
        },
    )
    return formatted


def _register_ingest_routes(app: FastAPI, config: dict) -> None:
    """Register GET /health and the three /ingest/* proxy routes onto app.

    Called by both create_routing_only_app and create_app so ingest behaviour
    is identical in both modes. Env vars are read at request time so that
    test fixtures can set them after app creation.

    Args:
        app: FastAPI instance to register routes on.
        config: Full merged config dict (used for ke_internal_url fallback).
    """
    _ke_internal_url_fallback = config.get("ke_internal_url", "")

    @app.get("/health")
    def health() -> dict:
        """Return service health status."""
        return {"status": "ok"}

    @app.post("/ingest/upload")
    async def ingest_upload(
        request: Request,
    ):
        """Stream multipart upload from dev-kit to KE without buffering.

        Validates dev-kit API key, then streams the full multipart body to KE.
        Streaming avoids accumulating up to 150 MB (5 files × 30 MB) in memory.
        """
        devkit_key = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
        reach_to_ke_key = os.environ.get("REACH_TO_KE_API_KEY", "")
        ke_url = os.environ.get("KE_INTERNAL_URL") or _ke_internal_url_fallback

        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, devkit_key)

        if not ke_url:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{ke_url}/upload",
                    content=request.stream(),
                    headers={
                        "Content-Type": request.headers.get("Content-Type", ""),
                        "X-API-Key": reach_to_ke_key,
                    },
                )
            logger.info(
                "reach.ingest_upload",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "success",
                    "ke_status": response.status_code,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.ingest_upload_ke_unreachable",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.ingest_upload_timeout",
                extra={
                    "operation": "reach.ingest_upload",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e

    @app.get("/ingest/job/{job_id}")
    async def ingest_job_status(
        job_id: str,
        request: Request,
    ):
        """Proxy job status poll from dev-kit to KE.

        Validates dev-kit API key, then forwards the GET to KE's job status endpoint.
        """
        devkit_key = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
        reach_to_ke_key = os.environ.get("REACH_TO_KE_API_KEY", "")
        ke_url = os.environ.get("KE_INTERNAL_URL") or _ke_internal_url_fallback

        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, devkit_key)

        if not ke_url:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{ke_url}/upload/job/{job_id}",
                    headers={"X-API-Key": reach_to_ke_key},
                )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.ingest_job_status_ke_unreachable",
                extra={
                    "operation": "reach.ingest_job_status",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.ingest_job_status_timeout",
                extra={
                    "operation": "reach.ingest_job_status",
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e

    @app.get("/ingest/jobs")
    async def list_ingest_jobs(
        request: Request,
        limit: int = 100,
    ):
        """Proxy ingestion history list from dev-kit to KE.

        Validates dev-kit API key, then forwards the GET to KE's job list endpoint.

        Args:
            request: Incoming request (used to read X-API-Key header).
            limit: Maximum number of records to return.

        Returns:
            Proxied JSON response from KE.

        Raises:
            HTTPException: 401 if API key invalid; 503/504 if KE unreachable.
        """
        devkit_key = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
        reach_to_ke_key = os.environ.get("REACH_TO_KE_API_KEY", "")
        ke_url = os.environ.get("KE_INTERNAL_URL") or _ke_internal_url_fallback

        x_api_key = request.headers.get("X-API-Key")
        _verify_api_key(x_api_key, devkit_key)

        if not ke_url:
            raise HTTPException(503, "KE_INTERNAL_URL is not configured")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{ke_url}/upload/jobs",
                    params={"limit": limit},
                    headers={"X-API-Key": reach_to_ke_key},
                )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.ConnectError as e:
            logger.error(
                "reach.list_ingest_jobs_ke_unreachable",
                extra={"operation": "reach.list_ingest_jobs", "status": "failure", "error": str(e)},
            )
            raise HTTPException(503, "Knowledge Engine is unreachable") from e
        except httpx.TimeoutException as e:
            logger.error(
                "reach.list_ingest_jobs_timeout",
                extra={"operation": "reach.list_ingest_jobs", "status": "failure", "error": str(e)},
            )
            raise HTTPException(504, "Knowledge Engine timed out") from e


def create_routing_only_app(config: dict) -> FastAPI:
    """Create a minimal FastAPI app for voice-only deployments.

    Registers only GET /health and the three /ingest/* proxy routes.
    No WebReachLayer, no HTTP clients, no auth validation, no React SPA.

    Args:
        config: Full merged config dict (used by _register_ingest_routes).

    Returns:
        Configured minimal FastAPI application.
    """
    app = FastAPI(title="Reach Layer — Web Channel Adapter (routing-only)")
    FastAPIInstrumentor.instrument_app(app)
    _register_ingest_routes(app, config)
    return app


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

    # Sidebar sessions — framework-level cap on how many recent sessions we
    # return. The list is labelled by `last_accessed` in the browser, so no
    # per-domain vocabulary is needed.
    reach_cfg = config.get("reach_layer", {}) or {}
    sessions_cfg = reach_cfg.get("sessions", {}) or {}
    sessions_limit = int(sessions_cfg.get("limit", 25))

    auth_cfg = config.get("auth", {}) or {}
    auth_enabled = bool(auth_cfg.get("enabled", False))
    google_client_id = auth_cfg.get("google_client_id") or os.getenv("GOOGLE_CLIENT_ID", "")
    cookie_name = auth_cfg.get("session_cookie_name", "reach_session")
    session_ttl_s = int(auth_cfg.get("session_ttl_s", 86400))
    cookie_secure = bool(auth_cfg.get("cookie_secure", True))
    cookie_samesite = auth_cfg.get("cookie_samesite", "lax")
    session_secret = os.getenv("REACH_SESSION_SECRET", "")
    if auth_enabled and not session_secret:
        # Fail loud at startup — never silently boot a broken auth config.
        raise RuntimeError(
            "auth.enabled is true but REACH_SESSION_SECRET env var is not set"
        )
    if auth_enabled and not google_client_id:
        raise RuntimeError(
            "auth.enabled is true but auth.google_client_id / GOOGLE_CLIENT_ID is not set"
        )

    def _require_session(request: Request) -> dict:
        """Read session cookie and return claims dict, or raise 401."""
        token = request.cookies.get(cookie_name, "")
        try:
            claims = verify_session_token(token, session_secret)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail={"reason": exc.reason.value})
        return {
            "user_id": claims.user_id,
            "display_name": claims.display_name,
            "email": claims.email,
            "picture": claims.picture,
        }

    app = FastAPI(title="Reach Layer — Web Channel Adapter")
    FastAPIInstrumentor.instrument_app(app)

    # Paths to the React production build (this file lives in reach_layer/web/,
    # so the Vite output dir is simply ./dist relative to this module).
    _dist = Path(__file__).parent / "dist"
    _assets = _dist / "assets"

    # Mount /assets — serves JS/CSS bundles built by Vite
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    # Shared HTTP clients — created once at startup to enable connection pooling.
    # Instrument each client so W3C traceparent headers propagate to downstream services.
    ac_client = httpx.Client(timeout=ac_timeout)
    ml_client = httpx.Client(timeout=ml_timeout)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor.instrument_client(ac_client)
        HTTPXClientInstrumentor.instrument_client(ml_client)
    except Exception:
        pass  # Observability must not prevent startup

    @app.on_event("shutdown")
    def _close_clients() -> None:
        """Close shared HTTP clients on shutdown."""
        ac_client.close()
        ml_client.close()

    # ------------------------------------------------------------------
    # GET /app-config — expose UI branding config to the browser
    # ------------------------------------------------------------------

    @app.get("/app-config")
    def app_config() -> dict[str, Any]:
        """Return UI branding/copy plus public auth settings to the browser.

        The browser fetches this at boot so all domain-specific text
        (app name, avatars, placeholder copy) comes from config rather
        than being hardcoded in the HTML. Adds an `auth` block exposing
        only public values (enabled flag + Google OAuth client_id).

        Returns:
            Dict of UI config keys merged with a public auth block.
        """
        ui = dict(config.get("ui", {}))
        ui["auth"] = {
            "enabled": auth_enabled,
            "google_client_id": google_client_id if auth_enabled else "",
        }
        return ui

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    @app.post("/auth/google")
    def auth_google(req: GoogleAuthRequest, response: Response) -> dict[str, Any]:
        """Exchange a Google ID token for an HttpOnly session cookie.

        Verifies the Google ID token via google-auth, then issues an
        HS256 session JWT and sets it as a Secure HttpOnly cookie.

        Args:
            req: Body containing the GIS credential string.
            response: FastAPI response used to set the cookie.

        Returns:
            Identity payload {user_id, display_name, email, picture}.
        """
        if not auth_enabled:
            raise HTTPException(status_code=404, detail="auth disabled")
        start = time.time()
        try:
            identity = verify_google_id_token(req.credential, google_client_id)
        except AuthError as exc:
            logger.warning(
                "reach_server.auth_google_failure",
                extra={
                    "operation": "reach_server.auth_google",
                    "status": "failure",
                    "reason": exc.reason.value,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            status = 401 if exc.reason is not Reason.MISSING else 400
            raise HTTPException(status_code=status, detail={"reason": exc.reason.value})

        token = issue_session_token(
            user_id=identity.user_id,
            display_name=identity.name,
            ttl_s=session_ttl_s,
            secret=session_secret,
            email=identity.email,
            picture=identity.picture,
        )
        response.set_cookie(
            key=cookie_name,
            value=token,
            max_age=session_ttl_s,
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
            path="/",
        )
        logger.info(
            "reach_server.auth_google_success",
            extra={
                "operation": "reach_server.auth_google",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {
            "user_id": identity.user_id,
            "display_name": identity.name,
            "email": identity.email,
            "picture": identity.picture,
        }

    @app.get("/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        """Return the current session identity, or 401 if no valid cookie."""
        if not auth_enabled:
            raise HTTPException(status_code=404, detail="auth disabled")
        return _require_session(request)

    @app.post("/auth/logout")
    def auth_logout(response: Response) -> dict[str, Any]:
        """Clear the session cookie. Always 200 regardless of prior state."""
        response.delete_cookie(
            key=cookie_name,
            path="/",
            secure=cookie_secure,
            samesite=cookie_samesite,
        )
        return {"ok": True}

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
    async def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
        """Forward a chat turn to Agent Core and return the response.

        Routing is driven by ``web_reach.assembly_mode``:
          - ``direct``  → POST /process_turn (synchronous). Default.
          - ``session`` → POST /sessions/{id}/input + consume SSE events,
                          aggregating sentences into a single response body.

        Args:
            req: Chat request with session_id, optional user_id, and message.

        Returns:
            Dict with response_text, was_escalated, session_id, latency_ms.
            Returns a safe error response on any failure.
        """
        start = time.time()
        # When auth is enabled, override any client-supplied user_id with the
        # one bound to the session cookie. Cookie is the source of truth.
        effective_user_id = req.user_id
        if auth_enabled:
            session = _require_session(request)
            effective_user_id = session["user_id"]
        with otel_trace.get_tracer(__name__).start_as_current_span("reach.inbound") as span:
            span.set_attribute("session_id", req.session_id or "")
            span.set_attribute("dpg.channel", "web")
            span.set_attribute("dpg.assembly_mode", web_reach.assembly_mode)
            try:
                turn = web_reach.build_turn_input(req.session_id, effective_user_id, req.message)
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

            session_id = turn["session_id"]

            if web_reach.assembly_mode == "session":
                return await _chat_session_mode(
                    web_reach, session_id, turn["user_id"], turn["user_message"], span, start,
                )
            return await _chat_direct_mode(
                ac_client, ac_endpoint, ac_timeout,
                session_id, turn, bool(req.fresh), web_reach, span, start,
            )

    # ------------------------------------------------------------------
    # GET /user-history/{user_id} — proxy to Memory Layer
    # ------------------------------------------------------------------

    @app.get("/user-history/{user_id}")
    def user_history(user_id: str, request: Request) -> dict[str, Any]:
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
        if auth_enabled:
            session = _require_session(request)
            # Cookie identity is authoritative; ignore the path param value.
            user_id = session["user_id"]
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

    # ------------------------------------------------------------------
    # GET /sessions — list the current user's sessions for the sidebar
    # ------------------------------------------------------------------

    def _resolve_user_id(request: Request, fallback: Optional[str]) -> Optional[str]:
        """Return cookie-bound user_id when auth enabled; otherwise the fallback."""
        if auth_enabled:
            session = _require_session(request)
            return session["user_id"]
        return (fallback or "").strip() or None

    @app.get("/sessions")
    def list_sessions(request: Request, user_id: Optional[str] = None) -> dict[str, Any]:
        """List the current user's recent sessions (up to 25, most recent first).

        When auth is enabled, the cookie identity is authoritative and the
        ``user_id`` query parameter is ignored. When auth is disabled, the
        ``user_id`` query parameter must be provided so the dev/demo channel
        can still scope sessions.
        """
        start = time.time()
        effective = _resolve_user_id(request, user_id)
        if not effective:
            return {"sessions": []}
        try:
            response = ml_client.get(
                f"{ml_endpoint}/sessions/{effective}",
                timeout=ml_timeout,
            )
            response.raise_for_status()
            raw_sessions = response.json() if isinstance(response.json(), list) else []
        except Exception as e:
            logger.error(
                "reach_server.list_sessions_error",
                extra={
                    "operation": "reach_server.list_sessions",
                    "status": "failure",
                    "error": str(e),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"sessions": []}

        capped = raw_sessions[:sessions_limit]
        sessions: list[dict[str, Any]] = []
        for entry in capped:
            sid = entry.get("session_id")
            if not sid:
                continue
            sessions.append({
                "session_id": sid,
                "last_accessed": entry.get("last_accessed"),
            })

        logger.info(
            "reach_server.list_sessions_success",
            extra={
                "operation": "reach_server.list_sessions",
                "status": "success",
                "session_count": len(sessions),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {"sessions": sessions}

    @app.get("/sessions/{session_id}/history")
    def session_history(session_id: str, request: Request, user_id: Optional[str] = None) -> dict[str, Any]:
        """Return the chat history for a single session owned by the caller.

        Authorises by verifying the session belongs to the cookie-bound user
        (or to the supplied ``user_id`` when auth is disabled).
        """
        start = time.time()
        sid = session_id.strip()
        effective = _resolve_user_id(request, user_id)
        if not sid or not effective:
            return {"session_id": sid, "turns": []}

        # Authorisation check — verify session belongs to caller.
        try:
            sessions_resp = ml_client.get(
                f"{ml_endpoint}/sessions/{effective}",
                timeout=ml_timeout,
            )
            sessions_resp.raise_for_status()
            owned = sessions_resp.json() if isinstance(sessions_resp.json(), list) else []
            owned_ids = {s.get("session_id") for s in owned if isinstance(s, dict)}
        except Exception as e:
            logger.error(
                "reach_server.session_history_owner_error",
                extra={
                    "operation": "reach_server.session_history",
                    "status": "failure",
                    "error": str(e),
                },
            )
            owned_ids = set()
        if sid not in owned_ids:
            raise HTTPException(status_code=404, detail="session not found")

        try:
            hist_resp = ml_client.get(
                f"{ml_endpoint}/audit/sessions/{sid}/history",
                timeout=ml_timeout,
            )
            hist_resp.raise_for_status()
            turns = hist_resp.json() or []
        except Exception as e:
            logger.error(
                "reach_server.session_history_error",
                extra={
                    "operation": "reach_server.session_history",
                    "status": "failure",
                    "error": str(e),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"session_id": sid, "turns": []}

        logger.info(
            "reach_server.session_history_success",
            extra={
                "operation": "reach_server.session_history",
                "status": "success",
                "session_id": sid,
                "turn_count": len(turns) if isinstance(turns, list) else 0,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {"session_id": sid, "turns": turns if isinstance(turns, list) else []}

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str, request: Request, user_id: Optional[str] = None) -> dict[str, Any]:
        """Delete a single session (Redis state + SQLite audit) for the caller."""
        start = time.time()
        sid = session_id.strip()
        effective = _resolve_user_id(request, user_id)
        if not sid or not effective:
            raise HTTPException(status_code=400, detail="missing session_id or user_id")

        # Authorisation check.
        try:
            sessions_resp = ml_client.get(
                f"{ml_endpoint}/sessions/{effective}",
                timeout=ml_timeout,
            )
            sessions_resp.raise_for_status()
            owned = sessions_resp.json() if isinstance(sessions_resp.json(), list) else []
            owned_ids = {s.get("session_id") for s in owned if isinstance(s, dict)}
        except Exception:
            owned_ids = set()
        if sid not in owned_ids:
            raise HTTPException(status_code=404, detail="session not found")

        try:
            del_resp = ml_client.delete(
                f"{ml_endpoint}/sessions/{sid}",
                params={"user_id": effective},
                timeout=ml_timeout,
            )
            del_resp.raise_for_status()
            body = del_resp.json() if del_resp.text else {}
            if isinstance(body, dict) and body.get("status") == "error":
                raise ValueError("Memory Layer returned status=error")
        except Exception as e:
            logger.error(
                "reach_server.delete_session_error",
                extra={
                    "operation": "reach_server.delete_session",
                    "status": "failure",
                    "session_id": sid,
                    "error": str(e),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            raise HTTPException(status_code=502, detail="memory layer error")

        logger.info(
            "reach_server.delete_session_success",
            extra={
                "operation": "reach_server.delete_session",
                "status": "success",
                "session_id": sid,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return {"ok": True, "session_id": sid}

    _register_ingest_routes(app, config)

    return app


# ---------------------------------------------------------------------------
# Application instance (used by uvicorn: server:app)
# ---------------------------------------------------------------------------

# Resolve DPG defaults / domain overrides, preferring the checked-in
# unified config at ../config/{dpg,domain}.yaml (local dev) and falling
# back to ./config/* relative to cwd (container runtime where the
# Dockerfile COPYs reach_layer/config/ to /app/config/).
_WEB_DIR = Path(__file__).resolve().parent
_LOCAL_REACH_CONFIG_DIR = _WEB_DIR.parent / "config"


def _resolve_dpg_path() -> str:
    """Pick the first existing dpg.yaml: local checkout → container cwd."""
    local = _LOCAL_REACH_CONFIG_DIR / "dpg.yaml"
    if local.exists():
        return str(local)
    return "config/dpg.yaml"


def _resolve_domain_path() -> str:
    """Resolve domain config path from CONFIG_FOLDER or fall back to config/domain.yaml."""
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        resolved = Path(config_folder) / "reach_layer.yaml"
        if resolved.exists():
            return str(resolved)
    local = _LOCAL_REACH_CONFIG_DIR / "domain.yaml"
    if local.exists():
        return str(local)
    return "config/domain.yaml"


_config = load_reach_config(
    channel_name="web",
    dpg_path=_resolve_dpg_path(),
    domain_path=_resolve_domain_path(),
)

# ---------------------------------------------------------------------------
# OpenTelemetry initialisation
# ---------------------------------------------------------------------------
try:
    from dpg_telemetry import init_otel
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    init_otel(service_name="reach_layer.web", config=_config)

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

WEB_MODE = os.getenv("REACH_LAYER_WEB_MODE", "full")
if WEB_MODE == "routing_only":
    logger.info(
        "Starting reach_layer_web in routing_only mode — web UI disabled",
        extra={"operation": "server.startup", "status": "success", "mode": "routing_only"},
    )
    app = create_routing_only_app(_config)
else:
    logger.info(
        "Starting reach_layer_web in full mode",
        extra={"operation": "server.startup", "status": "success", "mode": "full"},
    )
    _web_reach = WebReachLayer(_config)
    app = create_app(_web_reach, _config)
