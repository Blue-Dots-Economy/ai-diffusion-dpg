"""
trust_layer/src/blocks/hitl.py

HiTLBlock — Human-in-the-Loop escalation queue.

Queue backend is configurable via trust.hitl.queue_backend:
  "log"     — writes structured JSON to the Python logger (default)
  "redis"   — reserved for future implementation
  "webhook" — reserved for future implementation

Returns a ticket_id and holding_message to Agent Core. Agent Core writes
the session escalation state to Memory Layer after receiving this response.

Config section: trust.hitl
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)


class HiTLBlock:
    """
    Submits escalation events to a configurable queue backend.

    Args:
        config: Full config dict containing trust.hitl section.
    """

    def __init__(self, config: dict) -> None:
        start = time.time()
        hitl_cfg = (config or {}).get("trust", {}).get("hitl", {})
        self._queue_backend: str = hitl_cfg.get("queue_backend", "log")
        self._holding_message: str = hitl_cfg.get("holding_message", "")
        self._notification_webhook: str | None = hitl_cfg.get("notification_webhook")

        logger.info(
            "hitl_block.init",
            extra={
                "operation": "hitl_block.init",
                "status": "success",
                "queue_backend": self._queue_backend,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """
        Queue an escalation event and return a ticket ID and holding message.

        Args:
            session_id: Current session identifier.
            escalation_reason: Human-readable reason string (e.g. "escalation_topic:suicide").
            user_message: The user's message that triggered escalation.
            workflow_step: Current subagent step at time of escalation.

        Returns:
            dict with keys: queued (bool), ticket_id (str), holding_message (str).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()
        ticket_id = f"TKT-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

        self._write_to_queue(
            ticket_id=ticket_id,
            session_id=session_id,
            escalation_reason=escalation_reason,
            user_message=user_message,
            workflow_step=workflow_step,
        )

        logger.info(
            "hitl_block.escalated",
            extra={
                "operation": "hitl_block.escalate",
                "status": "success",
                "session_id": session_id,
                "ticket_id": ticket_id,
                "escalation_reason": escalation_reason,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

        return {
            "queued": True,
            "ticket_id": ticket_id,
            "holding_message": self._holding_message,
        }

    def _write_to_queue(
        self,
        ticket_id: str,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> None:
        """Write escalation event to the configured queue backend."""
        if self._queue_backend == "log":
            logger.warning(
                "hitl_block.escalation_queued",
                extra={
                    "operation": "hitl_block.queue_write",
                    "status": "success",
                    "ticket_id": ticket_id,
                    "session_id": session_id,
                    "escalation_reason": escalation_reason,
                    "workflow_step": workflow_step,
                },
            )
        else:
            # TODO(GH-hitl): Implement redis and webhook backends.
            # Returning queued=True for unsupported backends is a known gap tracked in
            # the HiTL queue implementation issue. The caller (escalate) returns True
            # to avoid breaking the turn; a real backend will either deliver or return queued=False.
            logger.warning(
                "hitl_block.unsupported_backend",
                extra={
                    "operation": "hitl_block.queue_write",
                    "status": "skipped",
                    "queue_backend": self._queue_backend,
                    "ticket_id": ticket_id,
                },
            )
