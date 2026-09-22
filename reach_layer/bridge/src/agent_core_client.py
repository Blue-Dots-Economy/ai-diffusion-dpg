"""reach_layer/bridge/src/agent_core_client.py

HTTP client for Agent Core's turn API.

Three calls are used:

- ``POST /process_turn``  blocking, for ``stream: false``
- ``POST /stream_turn``   SSE, for ``stream: true``
- ``DELETE /sessions/{id}/active_turn``  barge-in cancel (spec section 8)

Agent Core signals turn failures with HTTP 200 and an error ``DoneEvent``
rather than an HTTP status, so callers must inspect the terminal event. This
client raises only for transport-level problems.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class AgentCoreError(RuntimeError):
    """A transport-level failure talking to Agent Core.

    Attributes:
        kind: ``timeout`` | ``connect`` | ``http`` | ``protocol``.
    """

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class AgentCoreClient:
    """Async client for Agent Core's turn endpoints."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        """Initialise the client.

        Args:
            base_url: Agent Core root, e.g. ``http://agent_core:8000``.
            timeout_s: Explicit per-request timeout. Turns measured at 4-6s,
                so this must comfortably exceed that.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        """Release the connection pool."""
        await self._http.aclose()

    async def process_turn(self, payload: dict) -> dict[str, Any]:
        """Execute one blocking turn.

        Args:
            payload: The turn request body to forward to Agent Core.

        Returns:
            The parsed JSON response body.

        Raises:
            AgentCoreError: On timeout, connection failure, any other
                transport-level failure (e.g. ``ReadError``,
                ``RemoteProtocolError``, ``WriteError``), non-2xx response,
                or an undecodable response body.
        """
        start = time.time()
        try:
            response = await self._http.post(
                f"{self._base_url}/process_turn", json=payload
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise AgentCoreError(f"Agent Core timed out: {exc}", "timeout") from exc
        except httpx.ConnectError as exc:
            raise AgentCoreError(f"Agent Core unreachable: {exc}", "connect") from exc
        except httpx.HTTPStatusError as exc:
            raise AgentCoreError(
                f"Agent Core returned {exc.response.status_code}", "http"
            ) from exc
        except ValueError as exc:
            raise AgentCoreError(f"Agent Core sent invalid JSON: {exc}", "protocol") from exc
        except httpx.RequestError as exc:
            # httpx's transport-error tree is wider than TimeoutException/ConnectError
            # (e.g. ReadError, WriteError, RemoteProtocolError — the last is not exotic
            # here: turns take 4-6s, and a server closing mid-response produces exactly
            # this). Left uncaught, any of these leaks as a raw httpx exception and
            # becomes a 500 instead of a clean 502. Must stay last so it doesn't shadow
            # the precise .kind values above.
            raise AgentCoreError(
                f"Agent Core request failed ({type(exc).__name__}): {exc}", "connect"
            ) from exc

        logger.info(
            "bridge.process_turn",
            extra={
                "operation": "agent_core_client.process_turn",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return data

    async def stream_turn(self, payload: dict) -> AsyncIterator[dict[str, Any]]:
        """Execute one streaming turn, yielding each decoded event.

        Malformed ``data:`` lines are logged and skipped rather than aborting
        a live call. Blank lines and SSE comments are ignored. The stream has
        no ``[DONE]`` sentinel — it simply ends after the terminal event.

        Args:
            payload: The turn request body to forward to Agent Core.

        Yields:
            Each decoded SSE event as a dict, in arrival order.

        Raises:
            AgentCoreError: On timeout, connection failure, any other
                transport-level failure (e.g. ``ReadError``,
                ``RemoteProtocolError``, ``WriteError``) or non-2xx response.
        """
        start = time.time()
        try:
            async with self._http.stream(
                "POST", f"{self._base_url}/stream_turn", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    try:
                        yield json.loads(raw)
                    except ValueError:
                        logger.warning(
                            "bridge.stream_event_undecodable",
                            extra={
                                "operation": "agent_core_client.stream_turn",
                                "status": "skipped",
                            },
                        )
        except httpx.TimeoutException as exc:
            raise AgentCoreError(f"Agent Core timed out: {exc}", "timeout") from exc
        except httpx.ConnectError as exc:
            raise AgentCoreError(f"Agent Core unreachable: {exc}", "connect") from exc
        except httpx.HTTPStatusError as exc:
            raise AgentCoreError(
                f"Agent Core returned {exc.response.status_code}", "http"
            ) from exc
        except httpx.RequestError as exc:
            # See the matching fallback in process_turn: httpx's transport-error tree
            # is wider than TimeoutException/ConnectError, and a leak here becomes a
            # 500 instead of a clean 502.
            raise AgentCoreError(
                f"Agent Core request failed ({type(exc).__name__}): {exc}", "connect"
            ) from exc

        logger.info(
            "bridge.stream_turn",
            extra={
                "operation": "agent_core_client.stream_turn",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    async def cancel_turn(self, session_id: str) -> None:
        """Interrupt the active turn for a session (barge-in, spec section 8).

        This is the caller-hung-up path: Agent Core runs a turn to
        completion whether or not anyone is still listening, so for a turn
        that calls a write tool (e.g. ``save_profile``, ``apply_job``),
        skipping this means committing a side effect for a sentence the
        caller never finished. The protection is deliberately partial —
        Agent Core aborts at the next stage boundary but lets any
        already-in-flight tool call or trust check run to completion to
        preserve external-side-effect safety, so this cannot undo a write
        already issued.

        Best-effort and never raises: this runs when the client has already
        disconnected, and a failure here must not mask that original
        disconnect.

        Args:
            session_id: The session whose active turn should be cancelled.
                Never logged — it is the caller's phone number.
        """
        try:
            await self._http.delete(
                f"{self._base_url}/sessions/{session_id}/active_turn"
            )
            logger.info(
                "bridge.turn_cancelled",
                extra={
                    "operation": "agent_core_client.cancel_turn",
                    "status": "success",
                },
            )
        except Exception as exc:  # noqa: BLE001 — best effort by design
            logger.warning(
                "bridge.turn_cancel_failed",
                extra={
                    "operation": "agent_core_client.cancel_turn",
                    "status": "failure",
                    "error": str(exc),
                },
            )
