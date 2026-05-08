"""
reach_layer/base/reach_layer_base.py

ReachLayerBase — async abstract base class for all Reach Layer channel adapters.

All channel implementations (CLI, Web, Voice) inherit from this class or one of
its specialisations (TextChannelBase, VoiceChannelBase).

The base class provides concrete HTTP methods for communicating with Agent Core:
- submit_input() routes to session or direct endpoint based on assembly_mode
- subscribe_events() opens SSE subscription for session-based channels
- cancel_turn() interrupts the active turn for session-based channels

Channel-specific lifecycle (on_session_start, on_session_end) is abstract.

Design decisions not in the spec:

1. Concrete HTTP methods: submit_input(), subscribe_events(), and cancel_turn()
   are concrete (not abstract as the spec suggests) because all channels use the
   same HTTP calls to Agent Core. Only the channel lifecycle differs. This avoids
   every channel re-implementing identical HTTP logic.

2. Agent Core base URL: Derived from the existing agent_core_client.endpoint config
   by stripping the path component. No new config key needed.

3. Event dataclasses: reach_layer defines its own lightweight event types (in
   events.py) that mirror Agent Core's SSE event format. This keeps the packages
   decoupled — reach_layer never imports from agent_core.

4. SSE parsing: _parse_sse_event() handles the data: <json> format that Agent Core
   emits. It converts JSON payloads into typed event objects based on the "type" field.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from .events import ConsentEvent, DoneEvent, SentenceEvent, SignalEvent, StreamEvent

logger = logging.getLogger(__name__)


class ReachLayerBase(ABC):
    """Async abstract base for all Reach Layer channel adapters.

    Provides concrete HTTP methods for Agent Core communication and
    abstract lifecycle hooks for channel-specific behaviour.

    Args:
        config: Full merged reach_layer config dict.
        channel_name: Channel identifier (e.g. "cli", "web", "voice").
    """

    def __init__(self, config: dict, channel_name: str) -> None:
        """Initialise the base with config and channel identity.

        Args:
            config: Full merged reach_layer config dict. Must contain
                    agent_core_client section with endpoint and timeout_s.
            channel_name: Channel identifier (e.g. "cli", "web", "voice").

        Raises:
            ValueError: If config is None or channel_name is empty.
        """
        if config is None:
            raise ValueError("config must not be None")
        if not channel_name:
            raise ValueError("channel_name must not be empty")

        self._config = config
        self._channel_name = channel_name

        # Parse Agent Core base URL from endpoint config.
        # Config has agent_core_client.endpoint = "http://agent_core:8000/process_turn"
        # We strip the path to get the base URL for all endpoints.
        ac_config = config.get("agent_core_client", {})
        endpoint = ac_config.get("endpoint", "http://localhost:8000/process_turn")
        parsed = urlparse(endpoint)
        self._agent_core_base = f"{parsed.scheme}://{parsed.netloc}"
        self._timeout_s = ac_config.get("timeout_s", 30.0)

        # Read assembly_mode from reach_layer.channels.<channel_name>.assembly_mode
        channels_config = config.get("reach_layer", {}).get("channels", {})
        channel_config = channels_config.get(channel_name, {})
        self._assembly_mode = channel_config.get("assembly_mode", "direct")

        # HTTP client — created lazily on first use
        self._http_client: Optional[httpx.AsyncClient] = None

        logger.info(
            "reach_layer_base.init",
            extra={
                "operation": "reach_layer_base.init",
                "status": "success",
                "channel": channel_name,
                "assembly_mode": self._assembly_mode,
                "agent_core_base": self._agent_core_base,
            },
        )

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx.AsyncClient.

        Uses read=None so SSE connections never time out waiting for events
        between turns. Connect/write/pool timeouts are still enforced.
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._timeout_s,
                    read=None,
                    write=self._timeout_s,
                    pool=self._timeout_s,
                )
            )
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client. Call on shutdown."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Concrete Agent Core communication methods
    # ------------------------------------------------------------------

    async def submit_input(
        self,
        session_id: str,
        text: str,
        user_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Submit text to Agent Core. Routes based on assembly_mode.

        session mode: POST /sessions/{id}/input -> 202. Response via SSE.
        direct mode:  POST /process_turn -> sync TurnResult JSON.

        Design decision: This is a concrete method (not abstract) because all
        channels use the same HTTP calls. Only the input source differs.

        Args:
            session_id: Unique session identifier.
            text: User input text.
            user_id: Optional user identifier.

        Returns:
            In direct mode: TurnResult dict from Agent Core.
            In session mode: None (response delivered via subscribe_events).

        Raises:
            httpx.HTTPStatusError: On non-2xx response from Agent Core.
            httpx.TimeoutException: If Agent Core does not respond in time.
        """
        client = await self._get_client()
        start = time.time()

        if self._assembly_mode == "session":
            url = f"{self._agent_core_base}/sessions/{session_id}/input"
            payload = {
                "text": text,
                "channel": self._channel_name,
                "user_id": user_id or "",
            }
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info(
                    "reach_layer.submit_input",
                    extra={
                        "operation": "reach_layer.submit_input",
                        "status": "success",
                        "session_id": session_id,
                        "assembly_mode": "session",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return None
            except Exception as e:
                logger.error(
                    "reach_layer.submit_input_error",
                    extra={
                        "operation": "reach_layer.submit_input",
                        "status": "failure",
                        "session_id": session_id,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                raise

        else:  # direct mode
            url = f"{self._agent_core_base}/process_turn"
            payload = {
                "session_id": session_id,
                "user_message": text,
                "channel": self._channel_name,
                "user_id": user_id or "",
            }
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()
                logger.info(
                    "reach_layer.submit_input",
                    extra={
                        "operation": "reach_layer.submit_input",
                        "status": "success",
                        "session_id": session_id,
                        "assembly_mode": "direct",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return result
            except Exception as e:
                logger.error(
                    "reach_layer.submit_input_error",
                    extra={
                        "operation": "reach_layer.submit_input",
                        "status": "failure",
                        "session_id": session_id,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                raise

    async def subscribe_events(
        self, session_id: str, user_id: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Open SSE subscription to Agent Core for session-based channels.

        Connects to GET /sessions/{id}/events and yields parsed StreamEvent
        objects until DoneEvent is received.

        Only used when assembly_mode is "session". Direct-mode channels
        should not call this method.

        Args:
            session_id: Unique session identifier.
            user_id: Optional user identifier. When provided, appended as a
                ``?user_id=`` query param so Agent Core can proactively emit
                the entry subagent's opening_phrase on the first connect for
                a brand-new session (GH-149). Omit on reconnects where the
                session's opening_phrase has already been delivered — the
                server-side flag gate makes the call idempotent either way.

        Yields:
            StreamEvent instances (SignalEvent, SentenceEvent, DoneEvent).
        """
        if self._assembly_mode != "session":
            logger.warning(
                "reach_layer.subscribe_events_skipped",
                extra={
                    "operation": "reach_layer.subscribe_events",
                    "status": "skipped",
                    "reason": f"assembly_mode is '{self._assembly_mode}', not 'session'",
                    "session_id": session_id,
                },
            )
            return

        client = await self._get_client()
        url = f"{self._agent_core_base}/sessions/{session_id}/events"
        params = []
        if user_id:
            params.append(f"user_id={quote(user_id, safe='')}")
        # Include channel so Agent Core creates the session buffer with the
        # correct channel identity from the first turn (per-channel
        # system_prompt_suffix / tts_rules / turn_assembler timing).
        if self._channel_name:
            params.append(f"channel={quote(self._channel_name, safe='')}")
        if params:
            url = f"{url}?{'&'.join(params)}"

        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_text, buffer = buffer.split("\n\n", 1)
                        event = self._parse_sse_event(event_text)
                        if event:
                            yield event
                            # Do not close on DoneEvent — the server keeps the SSE
                            # connection open across turns (design decision #4 in
                            # TurnAssembler). Stay connected so subsequent turns
                            # are delivered on the same stream.
        except Exception as e:
            logger.error(
                "reach_layer.subscribe_events_error",
                extra={
                    "operation": "reach_layer.subscribe_events",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            # Yield a DoneEvent so callers don't hang
            yield DoneEvent(turn_status="abandoned")

    async def cancel_turn(self, session_id: str) -> bool:
        """Interrupt the active turn for session-based channels.

        Sends DELETE /sessions/{id}/active_turn to Agent Core.
        Only meaningful when assembly_mode is "session".

        Args:
            session_id: Unique session identifier.

        Returns:
            True if cancellation was accepted, False otherwise.
        """
        if self._assembly_mode != "session":
            return False

        client = await self._get_client()
        start = time.time()

        try:
            url = f"{self._agent_core_base}/sessions/{session_id}/active_turn"
            resp = await client.delete(url)
            success = resp.status_code == 200
            logger.info(
                "reach_layer.cancel_turn",
                extra={
                    "operation": "reach_layer.cancel_turn",
                    "status": "success" if success else "failure",
                    "session_id": session_id,
                    "http_status": resp.status_code,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return success
        except Exception as e:
            logger.error(
                "reach_layer.cancel_turn_error",
                extra={
                    "operation": "reach_layer.cancel_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return False

    # ------------------------------------------------------------------
    # SSE event parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sse_event(event_text: str) -> Optional[StreamEvent]:
        """Parse a single SSE event (data: <json>) into a StreamEvent.

        Args:
            event_text: Raw SSE event text (one or more "data: ..." lines).

        Returns:
            Parsed StreamEvent, or None if parsing fails.
        """
        data_lines = []
        for line in event_text.strip().split("\n"):
            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif line.startswith("data:"):
                data_lines.append(line[5:])

        if not data_lines:
            return None

        try:
            payload = json.loads("".join(data_lines))
        except json.JSONDecodeError:
            return None

        event_type = payload.get("type", "")

        if event_type == "signal":
            return SignalEvent(
                stage=payload.get("stage", ""),
                status=payload.get("status", ""),
                turn_id=payload.get("turn_id", ""),
            )
        elif event_type == "sentence":
            return SentenceEvent(
                text=payload.get("text", ""),
                sentence_index=payload.get("sentence_index", 0),
                turn_id=payload.get("turn_id", ""),
            )
        elif event_type == "done":
            return DoneEvent(
                turn_status=payload.get("turn_status", "completed"),
                was_escalated=payload.get("was_escalated", False),
                was_tool_used=payload.get("was_tool_used", False),
                model_used=payload.get("model_used", ""),
                latency_ms=payload.get("latency_ms", 0),
                turn_id=payload.get("turn_id", ""),
                session_ended=payload.get("session_ended", False),
            )
        elif event_type == "consent":
            return ConsentEvent(
                purpose=str(payload.get("purpose", "")),
                granted=bool(payload.get("granted", False)),
                consent_granted_ts=float(payload.get("consent_granted_ts", 0.0)),
                turn_id=str(payload.get("turn_id", "")),
            )
        else:
            return None

    # ------------------------------------------------------------------
    # Abstract lifecycle methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """Called when a new session begins. Sets up channel-specific state.

        Args:
            session_id: Unique session identifier.
            user_id: User identifier for this session.
        """

    @abstractmethod
    async def on_session_end(self, session_id: str) -> None:
        """Called when a session ends. Tears down channel-specific state.

        Args:
            session_id: Unique session identifier.
        """

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def channel_name(self) -> str:
        """Return the channel identifier."""
        return self._channel_name

    @property
    def assembly_mode(self) -> str:
        """Return the assembly mode (session or direct)."""
        return self._assembly_mode
