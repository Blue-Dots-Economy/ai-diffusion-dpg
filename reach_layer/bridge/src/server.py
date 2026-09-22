"""reach_layer/bridge/src/server.py

FastAPI surface for the Reach Layer bridge channel: an OpenAI-compatible
``/v1/chat/completions`` endpoint backed by Agent Core.

Errors use the OpenAI error envelope with real HTTP status codes (spec
section 12). There is deliberately no authentication (spec section 6): the
deployment restricts access to internal cluster traffic, and that
restriction is the only control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.agent_core_client import AgentCoreClient, AgentCoreError
from src.openai_models import build_error
from src.translate import (
    SSE_DONE,
    RequestError,
    StreamTranslator,
    sse,
    to_completion,
    to_turn_request,
)

logger = logging.getLogger(__name__)


def _error_response(status: int, message: str, err_type: str,
                     param: str | None = None) -> JSONResponse:
    """Build a JSON response carrying the OpenAI error envelope.

    Args:
        status: HTTP status code to return.
        message: Human-readable error description.
        err_type: OpenAI error type, e.g. ``"invalid_request_error"``.
        param: Name of the offending request parameter, if applicable.

    Returns:
        A ``JSONResponse`` with the error envelope as its body.
    """
    return JSONResponse(status_code=status,
                         content=build_error(message, err_type, param))


def create_app(config: dict) -> FastAPI:
    """Build the bridge FastAPI application.

    Args:
        config: Requires ``agent_core_url``. Optional ``channel`` (default
            ``"bridge"``), ``terminal_word`` (default ``""``), ``timeout_s``
            (default ``60.0``).

    Returns:
        A configured FastAPI app serving ``POST /v1/chat/completions`` and
        ``GET /health``.
    """
    app = FastAPI(title="Reach Layer Bridge", version="0.1.0")

    channel = config.get("channel", "bridge")
    terminal_word = config.get("terminal_word", "")
    client = AgentCoreClient(
        config["agent_core_url"], timeout_s=config.get("timeout_s", 60.0)
    )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        """Release the Agent Core connection pool on app shutdown."""
        await client.aclose()

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report liveness for readiness probes.

        Returns:
            A static ``{"status": "ok"}`` body.
        """
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """Serve one OpenAI-compatible chat-completion request.

        Dispatches to Agent Core's blocking ``/process_turn`` when
        ``stream`` is falsy (the OpenAI default) or its streaming
        ``/stream_turn`` when ``stream: true`` is set.

        Args:
            request: The raw FastAPI request; the body is parsed here so a
                non-JSON body can be reported as a 400 rather than a 500.

        Returns:
            A ``chat.completion`` JSON body, a ``text/event-stream`` of
            ``chat.completion.chunk`` events, or an OpenAI error envelope.
        """
        start = time.time()
        try:
            body: dict[str, Any] = await request.json()
        except Exception as exc:
            logger.warning(
                "bridge.request_body_invalid",
                extra={"operation": "server.chat_completions",
                       "status": "failure", "error": str(exc)},
            )
            return _error_response(400, "Request body must be valid JSON.",
                                    "invalid_request_error")
        if not isinstance(body, dict):
            return _error_response(400, "Request body must be a JSON object.",
                                    "invalid_request_error")

        try:
            turn = to_turn_request(body, channel=channel)
        except RequestError as exc:
            logger.info(
                "bridge.request_rejected",
                extra={"operation": "server.chat_completions",
                       "status": "failure", "error": str(exc)},
            )
            return _error_response(400, str(exc), "invalid_request_error", exc.param)

        model = body["model"]
        session_id = turn["session_id"]

        if body.get("stream"):
            include_usage = bool(
                (body.get("stream_options") or {}).get("include_usage")
            )
            return StreamingResponse(
                _stream(client, turn, model, terminal_word, include_usage, session_id),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            data = await client.process_turn(turn)
        except AgentCoreError as exc:
            logger.error(
                "bridge.turn_failed",
                extra={"operation": "server.chat_completions", "status": "failure",
                       "error": exc.kind,
                       "latency_ms": int((time.time() - start) * 1000)},
            )
            return _error_response(502, "The AI service is unavailable.", "api_error")

        if data.get("error_type"):
            logger.error(
                "bridge.turn_error",
                extra={"operation": "server.chat_completions", "status": "failure",
                       "error": str(data.get("error_type")),
                       "latency_ms": int((time.time() - start) * 1000)},
            )
            return _error_response(502, "The AI service could not complete the turn.",
                                    "api_error")

        logger.info(
            "bridge.turn_complete",
            extra={"operation": "server.chat_completions", "status": "success",
                   "latency_ms": int((time.time() - start) * 1000)},
        )
        return JSONResponse(content=to_completion(data, model))

    return app


async def _stream(client: AgentCoreClient, turn: dict, model: str,
                   terminal_word: str, include_usage: bool,
                   session_id: str) -> AsyncIterator[str]:
    """Render one Agent Core event stream as OpenAI SSE chunks.

    On client disconnect the Agent Core turn is cancelled (spec section 8):
    Agent Core otherwise runs the turn to completion regardless of whether
    anyone is listening, which for a turn that writes a profile or submits
    an application means acting on a sentence the caller interrupted and
    never finished.

    Disconnect detection deliberately does **not** use
    ``except Exception: await client.cancel_turn(...)``. When Starlette
    closes this generator early (client disconnect, or any other early
    ``aclose()``), it raises ``GeneratorExit`` at the suspended ``yield`` —
    and ``GeneratorExit``, like ``asyncio.CancelledError``, derives from
    ``BaseException``, not ``Exception``, so an ``except Exception`` clause
    never sees it; the cancel would silently never fire. Separately, ``await``
    is not permitted while a ``GeneratorExit`` is propagating through an
    async generator — attempting it raises ``RuntimeError: async generator
    ignored GeneratorExit``. So the cancel cannot be awaited inline in a
    handler for that exception either way.

    Instead, completion is tracked with a local flag set only on the
    success path (immediately after ``SSE_DONE`` is yielded). The generator
    body runs inside ``try/finally``; if the flag is still unset when the
    ``finally`` runs — whatever the reason: disconnect, ``aclose()``, an
    unexpected exception — the cancel is scheduled with
    ``asyncio.create_task`` and NOT awaited. Do not "fix" this into an
    ``await`` — that reintroduces the ``RuntimeError`` above on the
    disconnect path. ``AgentCoreClient.cancel_turn`` is documented as
    best-effort and never raises, so firing it without awaiting is safe:
    there is no exception to lose track of.

    Args:
        client: The Agent Core client used to stream the turn and, if
            needed, cancel it.
        turn: The translated turn request to forward to Agent Core.
        model: The client's requested model, echoed on every chunk.
        terminal_word: Closing word spoken when Agent Core ends the
            session. Empty disables it.
        include_usage: True when the client requested a trailing usage
            chunk via ``stream_options.include_usage``.
        session_id: The caller's phone number — used only to address the
            cancel call, never logged.

    Yields:
        SSE-framed ``chat.completion.chunk`` payloads, ending in the
        ``data: [DONE]`` sentinel on a normal finish.
    """
    translator = StreamTranslator(model, terminal_word)
    finished = False
    start = time.time()
    try:
        yield sse(translator.opening())
        async for event in client.stream_turn(turn):
            kind = event.get("type")
            if kind == "sentence":
                yield sse(translator.sentence(event.get("text", "")))
            elif kind == "done":
                if event.get("error_type"):
                    logger.error(
                        "bridge.stream_turn_error",
                        extra={"operation": "server.stream", "status": "failure",
                               "error": str(event.get("error_type"))},
                    )
                for chunk in translator.finish(event, include_usage=include_usage):
                    yield sse(chunk)
                yield SSE_DONE
                finished = True
                logger.info(
                    "bridge.stream_complete",
                    extra={"operation": "server.stream", "status": "success",
                           "latency_ms": int((time.time() - start) * 1000)},
                )
                return
            # signal events (pipeline progress) are dropped — not part of
            # the OpenAI contract.
    except AgentCoreError as exc:
        # Headers are already sent, so no HTTP status code is available at
        # this point. Close the stream cleanly with a terminal chunk and
        # [DONE] rather than truncating it mid-event.
        logger.error(
            "bridge.stream_failed",
            extra={"operation": "server.stream", "status": "failure",
                   "error": exc.kind,
                   "latency_ms": int((time.time() - start) * 1000)},
        )
        yield sse(translator.finish({"session_ended": False},
                                     include_usage=False)[-1])
        yield SSE_DONE
        finished = True
    finally:
        if not finished:
            # Fire-and-forget by design: see the docstring above. Do not
            # await this — GeneratorExit is propagating on the disconnect
            # path and awaiting here raises RuntimeError. cancel_turn()
            # never raises, so an un-awaited task is safe.
            asyncio.create_task(client.cancel_turn(session_id))
