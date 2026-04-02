"""
reach_layer/src/web_reach.py

WebReachLayer — web channel adapter for the DPG Reach Layer block.
Implements the ReachLayerBase interface for HTTP/browser interactions.

Unlike the CLI adapter, this class does not manage its own I/O loop. Instead,
the FastAPI server calls build_turn_input() per request and format_result()
to serialise the Agent Core response for JSON delivery to the browser.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.base import ReachLayerBase, TurnInput, TurnResult

logger = logging.getLogger(__name__)


class WebReachLayer(ReachLayerBase):
    """Web channel adapter that bridges the FastAPI server and Agent Core.

    Stateless per-request: the FastAPI server instantiates this once at startup
    and calls build_turn_input() / format_result() for every HTTP request.

    Args:
        config: Full merged config dict. Reads reach_layer.web section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        web_cfg = config.get("reach_layer", {}).get("web", {})
        self._title: str = web_cfg.get("title", "DPG Chat")

        logger.info(
            "web_reach.init",
            extra={
                "operation": "web_reach.init",
                "status": "success",
                "channel": "web",
                "title": self._title,
            },
        )

    # ------------------------------------------------------------------
    # ReachLayerBase interface
    # ------------------------------------------------------------------

    def receive(self) -> TurnInput:
        """Not applicable for the web adapter.

        WebReachLayer is request-driven, not polling-driven. The FastAPI
        server calls build_turn_input() per HTTP request instead of this
        method.

        Raises:
            NotImplementedError: Always. Use build_turn_input() instead.
        """
        raise NotImplementedError(
            "WebReachLayer is request-driven. Use build_turn_input() instead of receive()."
        )

    def deliver(self, result: TurnResult) -> None:
        """Not applicable for the web adapter.

        WebReachLayer delivers responses via format_result() and the HTTP
        response cycle, not via this push-style method.

        Raises:
            NotImplementedError: Always. Use format_result() instead.
        """
        raise NotImplementedError(
            "WebReachLayer is request-driven. Use format_result() instead of deliver()."
        )

    # ------------------------------------------------------------------
    # Web-specific helpers called by server.py
    # ------------------------------------------------------------------

    def build_turn_input(
        self,
        session_id: str,
        user_id: Optional[str],
        message: str,
    ) -> TurnInput:
        """Build a TurnInput from an HTTP request's parsed fields.

        Args:
            session_id: UUID session identifier from the browser.
            user_id: Optional user identifier entered in the setup form.
            message: The raw user message text.

        Returns:
            TurnInput ready to send to Agent Core.

        Raises:
            ValueError: If session_id or message is empty.
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not message or not message.strip():
            raise ValueError("message must not be empty")

        return TurnInput(
            session_id=session_id.strip(),
            user_message=message.strip(),
            channel="web",
            timestamp_ms=int(time.time() * 1000),
            user_id=user_id.strip() if user_id else None,
        )

    def format_result(self, result: TurnResult) -> dict[str, Any]:
        """Serialise a TurnResult to a JSON-safe dict for the browser.

        Args:
            result: Agent Core response to serialise.

        Returns:
            Dict with response_text, was_escalated, session_id, and latency_ms.
            Returns a safe fallback dict if result is None.
        """
        if result is None:
            logger.warning(
                "web_reach.format_result_skipped",
                extra={
                    "operation": "web_reach.format_result",
                    "status": "skipped",
                    "reason": "result is None",
                },
            )
            return {
                "response_text": "",
                "was_escalated": False,
                "session_id": "",
                "latency_ms": 0,
            }

        return {
            "response_text": result.response_text or "",
            "was_escalated": result.was_escalated,
            "was_tool_used": result.was_tool_used,
            "session_id": result.session_id,
            "latency_ms": result.latency_ms,
        }
