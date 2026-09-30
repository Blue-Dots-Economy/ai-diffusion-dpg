"""reach_layer/bridge/src/server.py

FastAPI surface for the Reach Layer bridge channel: an OpenAI-compatible
``/v1/chat/completions`` endpoint backed by Agent Core.

Errors use the OpenAI error envelope with real HTTP status codes (spec
section 12). There is deliberately no authentication (spec section 6): the
deployment restricts access to internal cluster traffic, and that
restriction is the only control.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.agent_core_client import AgentCoreClient, AgentCoreError
from src.openai_models import build_completion, build_error, new_completion_id
from src.translate import (
    SSE_DONE,
    RequestError,
    StreamTranslator,
    is_tool_result_followup,
    offered_hangup_tool,
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
            (default ``60.0``), ``hangup_tool_name`` (default ``""``, which
            disables hanging up), ``tool_status_phrases`` (default ``{}``).

    Returns:
        A configured FastAPI app serving ``POST /v1/chat/completions`` and
        ``GET /health``.
    """
    app = FastAPI(title="Reach Layer Bridge", version="0.1.0")

    channel = config.get("channel", "bridge")
    terminal_word = config.get("terminal_word", "")
    hangup_tool_name = config.get("hangup_tool_name", "")
    tool_status_phrases = dict(config.get("tool_status_phrases") or {})
    client = AgentCoreClient(
        config["agent_core_url"], timeout_s=config.get("timeout_s", 60.0)
    )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        """Release the Agent Core connection pool on app shutdown."""
        await client.aclose()

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Return the OpenAI error envelope for any exception a route missed.

        Without this handler, an unhandled exception falls through to
        Starlette's default ``text/plain`` "Internal Server Error" body,
        which is not valid JSON — an OpenAI SDK client parsing the response
        gets a decode failure on top of the original fault. The exception
        text is never included in the response or the log message: request
        bodies on this endpoint carry ``metadata.caller_phone`` (PII), and an
        unanticipated exception's ``str()`` is exactly the kind of place
        that could leak it.

        Args:
            request: The request being served when the exception escaped.
            exc: The unhandled exception.

        Returns:
            A 500 JSON response using the OpenAI error envelope.
        """
        logger.error(
            "bridge.unhandled_exception",
            extra={"operation": "server.chat_completions", "status": "failure",
                   "error": type(exc).__name__},
        )
        return _error_response(500, "An internal error occurred.", "api_error")

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

        if is_tool_result_followup(body):
            # The client is handing back the result of the hangup call. The
            # conversation is over; answer with nothing rather than replay
            # the caller's goodbye to Agent Core as a new turn.
            logger.info(
                "bridge.tool_result_followup",
                extra={"operation": "server.chat_completions", "status": "skipped"},
            )
            model = str(body.get("model") or "")
            if body.get("stream"):
                return StreamingResponse(
                    _empty_stream(model),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            return JSONResponse(content=build_completion(
                new_completion_id(), int(time.time()), model, ""))

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
            hangup_tool = offered_hangup_tool(body, hangup_tool_name)
            return StreamingResponse(
                _stream(client, turn, model, terminal_word, include_usage,
                        hangup_tool, tool_status_phrases),
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


async def _empty_stream(model: str) -> AsyncIterator[str]:
    """Render a well-formed stream that says nothing.

    Args:
        model: The client's requested model, echoed on every chunk.

    Yields:
        The role chunk, a ``stop`` chunk, and the ``[DONE]`` sentinel.
    """
    translator = StreamTranslator(model)
    yield sse(translator.opening())
    for chunk in translator.finish({"session_ended": False}, include_usage=False):
        yield sse(chunk)
    yield SSE_DONE


async def _stream(client: AgentCoreClient, turn: dict, model: str,
                   terminal_word: str, include_usage: bool,
                   hangup_tool: str | None = None,
                   tool_status_phrases: dict[str, str] | None = None,
                   ) -> AsyncIterator[str]:
    """Render one Agent Core event stream as OpenAI SSE chunks.

    Closing this stream (client disconnect) closes the upstream
    ``/stream_turn`` body; Agent Core's TurnAssembler then stops the turn at
    its next safe point and keeps what it did for the next turn (spec
    2026-09-29 §4.2). The bridge sends no cancel.

    Args:
        client: The Agent Core client used to stream the turn.
        turn: The translated turn request to forward to Agent Core.
        model: The client's requested model, echoed on every chunk.
        terminal_word: Closing word spoken when Agent Core ends the
            session. Empty disables it.
        include_usage: True when the client requested a trailing usage
            chunk via ``stream_options.include_usage``.
        hangup_tool: Client-offered tool to call when the turn ends the
            session, or None to just stop.
        tool_status_phrases: Tool name to the line spoken while it runs.

    Yields:
        SSE-framed ``chat.completion.chunk`` payloads, ending in the
        ``data: [DONE]`` sentinel on a normal finish.
    """
    translator = StreamTranslator(model, terminal_word, hangup_tool,
                                  tool_status_phrases)
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
                logger.info(
                    "bridge.stream_complete",
                    extra={"operation": "server.stream", "status": "success",
                           "latency_ms": int((time.time() - start) * 1000)},
                )
                return
            elif kind == "signal" and event.get("stage") == "tool_start":
                # The one signal worth voicing: the caller is otherwise silent
                # through the tool round trip. Every other signal is pipeline
                # progress with no place in the OpenAI contract, and dropped.
                status = translator.tool_status(event.get("tools"))
                if status is not None:
                    yield sse(status)

        # The event stream ended without a terminal `done` event — Agent
        # Core closed the SSE body cleanly with no AgentCoreError raised.
        # The client still gets a well-formed close rather than a silent
        # truncation.
        logger.warning(
            "bridge.stream_ended_without_done",
            extra={"operation": "server.stream", "status": "failure",
                   "latency_ms": int((time.time() - start) * 1000)},
        )
        for chunk in translator.finish({"session_ended": False},
                                        include_usage=include_usage):
            yield sse(chunk)
        yield SSE_DONE
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
        for chunk in translator.finish({"session_ended": False},
                                        include_usage=include_usage):
            yield sse(chunk)
        yield SSE_DONE
