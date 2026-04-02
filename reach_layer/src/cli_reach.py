"""
reach_layer/src/cli_reach.py

CLIReachLayer — PoC stub for the Reach Layer DPG.

Implements the ReachLayerBase interface using stdin/stdout (CLI REPL).
In production this is replaced by a channel adapter (WhatsApp, Web, VOIP).

Design:
- receive(): prints a prompt, reads one line from stdin, returns a TurnInput.
- deliver(): prints the agent response prefixed with "Agent: ".
- session_id is generated once at construction and reused for the whole CLI session.
- Raises EOFError (propagated to caller) when stdin is closed — signals clean shutdown.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid

from src.base import ReachLayerBase, TurnInput, TurnResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI Reach Layer
# ---------------------------------------------------------------------------

class CLIReachLayer(ReachLayerBase):
    """
    CLI-based Reach Layer stub.

    Reads user messages from stdin and prints agent responses to stdout.
    One session per process invocation — session_id is fixed at construction.

    Args:
        config: Full config dict. Reads reach_layer.cli section.
        session_id: Optional override. If None, a UUID is generated.
    """

    def __init__(
        self,
        config: dict,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if config is None:
            raise ValueError("config must not be None")

        cli_cfg = config.get("reach_layer", {}).get("cli", {})
        self._prompt: str = cli_cfg.get("prompt", "You: ")
        self._agent_prefix: str = cli_cfg.get("agent_prefix", "Agent: ")
        self._session_id: str = session_id or str(uuid.uuid4())
        self._user_id: str | None = user_id

        logger.info(
            "reach_layer.init",
            extra={
                "operation": "cli_reach.init",
                "status": "success",
                "session_id": self._session_id,
                "channel": "cli",
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors ReachLayerBase
    # ------------------------------------------------------------------

    def receive(self) -> TurnInput:
        """
        Block until a user message is available on stdin.

        Returns a TurnInput with channel="cli".
        Raises EOFError if stdin is closed (Ctrl-D / pipe end) — caller should catch to exit.
        """
        try:
            sys.stdout.write(self._prompt)
            sys.stdout.flush()
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            raise EOFError("keyboard interrupt — session ended")

        if not line:
            # Empty read means stdin is closed
            raise EOFError("stdin closed — session ended")

        user_message = line.rstrip("\n").strip()
        timestamp_ms = int(time.time() * 1000)

        logger.info(
            "reach_layer.receive",
            extra={
                "operation": "cli_reach.receive",
                "status": "success",
                "session_id": self._session_id,
                "message_length": len(user_message),
            },
        )

        return TurnInput(
            session_id=self._session_id,
            user_message=user_message,
            channel="cli",
            timestamp_ms=timestamp_ms,
            user_id=self._user_id,
        )

    def deliver(self, result: TurnResult) -> None:
        """
        Print the agent response to stdout.

        Handles was_escalated flag by adding an escalation notice.
        Never raises — delivery failure is logged but does not crash the loop.
        """
        if result is None:
            logger.warning(
                "reach_layer.deliver_skipped",
                extra={
                    "operation": "cli_reach.deliver",
                    "status": "skipped",
                    "reason": "result is None",
                },
            )
            return

        try:
            response_text = result.response_text or ""

            if result.was_escalated:
                sys.stdout.write(
                    f"{self._agent_prefix}[ESCALATED TO HUMAN AGENT]\n"
                )

            sys.stdout.write(f"{self._agent_prefix}{response_text}\n")
            sys.stdout.flush()

            logger.info(
                "reach_layer.deliver",
                extra={
                    "operation": "cli_reach.deliver",
                    "status": "success",
                    "session_id": result.session_id,
                    "response_length": len(response_text),
                    "was_escalated": result.was_escalated,
                    "latency_ms": result.latency_ms,
                },
            )

        except Exception as e:
            logger.error(
                "reach_layer.deliver_error",
                extra={
                    "operation": "cli_reach.deliver",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    @property
    def session_id(self) -> str:
        """Return the session ID for this CLI session."""
        return self._session_id

    @property
    def user_id(self) -> str | None:
        """Return the user ID for this CLI session (phone number or None)."""
        return self._user_id
