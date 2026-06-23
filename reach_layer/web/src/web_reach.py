"""
reach_layer/web/src/web_reach.py

WebReachLayer — web channel adapter extending TextChannelBase.

Runs in ``assembly_mode: direct`` — the browser posts complete utterances to
POST /chat. This adapter's ``submit_input()`` (inherited from ReachLayerBase)
routes each turn to Agent Core's synchronous POST /process_turn endpoint and
returns the TurnResult JSON to the caller. The browser never holds an SSE
subscription, so ``subscribe_events()`` and ``cancel_turn()`` are unused in
direct mode.

The ``server.py`` FastAPI module owns the HTTP lifecycle. It calls
``build_turn_input()`` to validate and normalise each inbound request and
``format_result()`` to serialise the response. ``run_loop()`` is a
placeholder; the real "loop" is the FastAPI/uvicorn request-response cycle.

Design decisions not in the spec:

1. run_loop() is a no-op (logs and returns). The web channel's lifecycle is
   request-driven rather than stdin-driven, so the TextChannelBase loop
   abstraction does not apply. We inherit the contract but satisfy it with a
   log-and-exit: the FastAPI server owns the real loop.

2. build_turn_input() returns a plain dict rather than constructing a full
   TurnInput object — the legacy TurnInput dataclass lived on the old sync
   base class (reach_layer/src/base.py) and does not exist on the new
   ReachLayerBase. The server.py call-site only needs the primitive fields
   (session_id, user_id, message, channel) to POST to Agent Core, so a dict
   is the minimum viable carrier and keeps the module self-contained.

3. Helpers stay on the adapter (not hoisted to server.py) so domain teams
   that replace the server module can still reuse validation/formatting.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from reach_layer_base import TextChannelBase

logger = logging.getLogger(__name__)


class WebReachLayer(TextChannelBase):
    """Web channel adapter backed by FastAPI.

    Stateless per request. ``server.py`` instantiates one instance at startup
    and calls ``build_turn_input()`` / ``format_result()`` on every POST /chat.

    Args:
        config: Full merged reach_layer config dict.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config, channel_name="web")
        web_cfg = (
            config.get("reach_layer", {}).get("channels", {}).get("web", {})
            if config
            else {}
        )
        self._title: str = web_cfg.get("title", "DPG Chat")

        logger.info(
            "web_reach.init",
            extra={
                "operation": "web_reach.init",
                "status": "success",
                "channel": "web",
                "assembly_mode": self.assembly_mode,
                "title": self._title,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks required by ReachLayerBase
    # ------------------------------------------------------------------

    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """No per-session setup needed; sessions are HTTP-request-scoped."""
        logger.info(
            "web_reach.session_start",
            extra={
                "operation": "web_reach.on_session_start",
                "status": "success",
                "session_id": session_id,
                "user_id": user_id or "anonymous",
            },
        )

    async def on_session_end(self, session_id: str) -> None:
        """No per-session teardown. HTTP client is reused across requests."""
        logger.info(
            "web_reach.session_end",
            extra={
                "operation": "web_reach.on_session_end",
                "status": "success",
                "session_id": session_id,
            },
        )

    # ------------------------------------------------------------------
    # TextChannelBase.run_loop — not meaningful for a request/response web channel
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Placeholder. The FastAPI request cycle in ``server.py`` is the real loop.

        The web adapter's lifecycle is request-driven: uvicorn owns the event
        loop; FastAPI routes invoke the adapter's helpers (build_turn_input /
        format_result) and inherited submit_input() per request. There is no
        long-running polling loop to enter here.
        """
        logger.info(
            "web_reach.run_loop_noop",
            extra={
                "operation": "web_reach.run_loop",
                "status": "skipped",
                "reason": "web channel is request-driven; server.py owns the loop",
            },
        )

    # ------------------------------------------------------------------
    # Web-specific helpers consumed by server.py
    # ------------------------------------------------------------------

    def build_turn_input(
        self,
        session_id: str,
        user_id: Optional[str],
        message: str,
    ) -> dict[str, Any]:
        """Validate and normalise the inbound browser request.

        Args:
            session_id: UUID session identifier from the browser.
            user_id: Optional user identifier (from auth cookie or form).
            message: Raw user message text.

        Returns:
            Dict with session_id, user_message, user_id, channel, timestamp_ms.

        Raises:
            ValueError: If session_id or message is empty after stripping.
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not message or not message.strip():
            raise ValueError("message must not be empty")

        return {
            "session_id": session_id.strip(),
            "user_message": message.strip(),
            "user_id": user_id.strip() if user_id else None,
            "channel": "web",
            "timestamp_ms": int(time.time() * 1000),
        }

    def format_result(
        self,
        session_id: str,
        data: Optional[dict],
        latency_ms: int,
    ) -> dict[str, Any]:
        """Serialise an Agent Core JSON response for the browser.

        Args:
            session_id: Session identifier to include even on failure paths.
            data: Raw JSON dict returned by Agent Core, or None on failure.
            latency_ms: Measured end-to-end latency for this turn.

        Returns:
            Dict with response_text, was_escalated, was_tool_used, session_id,
            latency_ms. Always safe to JSON-encode.
        """
        if not data:
            logger.warning(
                "web_reach.format_result_empty",
                extra={
                    "operation": "web_reach.format_result",
                    "status": "skipped",
                    "reason": "data is None or empty",
                    "session_id": session_id,
                },
            )
            return {
                "response_text": "",
                "was_escalated": False,
                "was_tool_used": False,
                "session_id": session_id,
                "latency_ms": latency_ms,
                "error_type": "empty_response",
                "error_message": "We're having trouble connecting to the AI service right now. Please try again shortly.",
            }

        error_type = data.get("error_type")
        error_message = data.get("error_message")
        response_text = data.get("response_text", "") or ""

        if not response_text and not error_type:
            error_type = "empty_response"
            error_message = "We're having trouble connecting to the AI service right now. Please try again shortly."

        return {
            "response_text": response_text,
            "was_escalated": bool(data.get("was_escalated", False)),
            "was_tool_used": bool(data.get("was_tool_used", False)),
            "session_id": session_id,
            "latency_ms": latency_ms,
            "error_type": error_type,
            "error_message": error_message,
        }
