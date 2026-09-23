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

# Strong references for fire-and-forget cancel tasks scheduled from _stream.
# asyncio.create_task() only keeps a *weak* reference internally (see the
# "Important" warning in the asyncio docs and the RUF006 lint rule) — if
# nothing else holds the task, the event loop is free to garbage-collect it
# mid-flight, and the cancel silently never reaches Agent Core. Retaining a
# reference here until the task itself reports completion closes that gap.
_pending_cancel_tasks: set[asyncio.Task] = set()


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


def _schedule_cancel(client: AgentCoreClient, session_id: str) -> None:
    """Fire-and-forget an Agent Core turn cancel from inside a `finally`.

    Never awaited: see the module-level note in :func:`_stream` for why an
    ``await`` here is unsafe on the disconnect path. The task is kept in
    :data:`_pending_cancel_tasks` — a bare ``asyncio.create_task`` call
    without retaining the result is only weakly referenced by the event
    loop and can be garbage-collected before it runs, which would silently
    defeat the whole mechanism.

    Args:
        client: The Agent Core client whose ``cancel_turn`` should run.
        session_id: The caller's phone number — passed through only, never
            logged.
    """
    try:
        task = asyncio.create_task(client.cancel_turn(session_id))
    except RuntimeError as exc:
        # No running event loop to schedule onto — e.g. the generator is
        # being finalised (GeneratorExit) during interpreter/loop shutdown.
        # This is best-effort by design (see AgentCoreClient.cancel_turn),
        # so log and move on rather than letting this mask the original
        # GeneratorExit/exception already propagating.
        logger.warning(
            "bridge.turn_cancel_not_scheduled",
            extra={"operation": "server.stream", "status": "failure",
                   "error": str(exc)},
        )
        return
    _pending_cancel_tasks.add(task)
    task.add_done_callback(_pending_cancel_tasks.discard)


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

    Instead, completion is tracked with a local flag. The turn is provably
    complete the moment the terminal ``done`` event is *read* off the
    stream — not when this function finishes emitting the chunks derived
    from it. An OpenAI SSE client is free to close its connection the
    instant it reads ``[DONE]``, and that close races the still-suspended
    ``yield SSE_DONE`` in this generator: if the flag were set *after* that
    yield, a client that disconnects right on schedule would still find
    ``finished`` False when ``GeneratorExit`` arrives, and the ``finally``
    below would fire a stray cancel against a turn that already completed
    — landing, at worst, on the caller's *next* turn. So the flag is set as
    soon as the terminal ``done`` event is *read*, before any further
    chunks are yielded. An ``AgentCoreError`` does NOT set the flag: unlike
    a ``done`` event, it is not evidence the upstream turn stopped (see the
    ``except AgentCoreError`` block below), so the cancel must still fire
    for it.

    The generator body runs inside ``try/finally``; if the flag is still
    unset when the ``finally`` runs — disconnect, ``aclose()``, an
    unexpected exception, or a stream that ends with no terminal event at
    all — the cancel is scheduled via :func:`_schedule_cancel`
    (``asyncio.create_task``, retained, NOT awaited). Do not "fix" this
    into an ``await`` — that reintroduces the ``RuntimeError`` above on the
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
                # The turn is complete as of this event being read, whether
                # or not the client sticks around for the chunks below —
                # set the flag before emitting anything further.
                finished = True
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
            # signal events (pipeline progress) are dropped — not part of
            # the OpenAI contract.

        # The event stream ended without a terminal `done` event — Agent
        # Core closed the SSE body cleanly with no AgentCoreError raised.
        # The turn did not genuinely finish (no DoneEvent was ever seen), so
        # `finished` stays False and the `finally` below still cancels it.
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
        #
        # Deliberately do NOT set `finished = True` here. An AgentCoreError
        # means *this bridge's view* of the turn ended abnormally — it is
        # not evidence the upstream Agent Core turn stopped. For `timeout`
        # and `protocol` (and `http`, if Agent Core ever returns non-2xx
        # mid-turn) the turn can still be running server-side and can still
        # commit a `save_profile` or `apply_job` write after this generator
        # gives up on it — exactly what the cancel in `finally` exists to
        # prevent. For `connect` there is no reachable turn to cancel, but
        # `cancel_turn` is documented best-effort and never raises, so
        # firing it anyway is a harmless no-op rather than a special case
        # worth branching on here.
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
    finally:
        if not finished:
            # Fire-and-forget by design: see the docstring above. Do not
            # await this — GeneratorExit is propagating on the disconnect
            # path and awaiting here raises RuntimeError. cancel_turn()
            # never raises, so an un-awaited task is safe.
            _schedule_cancel(client, session_id)
