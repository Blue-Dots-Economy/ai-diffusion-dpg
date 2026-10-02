"""
trust_layer/src/blocks/hitl.py

HiTLBlock — Human-in-the-Loop escalation queue.

Queue backend is configurable via trust.hitl.queue_backend:
  "log"     — implemented (default). Writes structured JSON to the Python
              logger; queued=True but delivered=False, reason="log_only".
  "webhook" — implemented. Signed HTTPS POST of the handoff payload (see
              hitl_webhook.py; env HITL_WEBHOOK_URL / HITL_WEBHOOK_SECRET).
              delivered=True only on a 2xx response.
  "redis"   — not implemented; returns queued=False, delivered=False,
              reason="unsupported_backend".

Returns a ticket_id and holding_message to Agent Core. Agent Core writes
the session escalation state to Memory Layer after receiving this response.
The handoff payload, webhook URL and secret are never logged.

Config section: trust.hitl
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

import httpx

from .hitl_webhook import deliver_webhook, webhook_settings

logger = logging.getLogger(__name__)


class HiTLBlock:
    """
    Submits escalation events to a configurable queue backend.

    Args:
        config: Full config dict containing trust.hitl section.
    """

    def __init__(self, config: dict, http_client: httpx.Client | None = None) -> None:
        start = time.time()
        self._http = http_client
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
        handoff: dict | None = None,
    ) -> dict:
        """
        Queue an escalation event and return a ticket ID and holding message.

        Args:
            session_id: Current session identifier.
            escalation_reason: Human-readable reason string (e.g. "escalation_topic:suicide").
            user_message: The user's message that triggered escalation.
            workflow_step: Current subagent step at time of escalation.
            handoff: Optional handoff payload (never logged).

        Returns:
            dict with keys: queued (bool), delivered (bool), reason (str),
            ticket_id (str), holding_message (str).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()
        ticket_id = f"TKT-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

        queued, delivered, reason = self._deliver(
            ticket_id, session_id, escalation_reason, workflow_step, handoff
        )

        logger.info(
            "hitl_block.escalated",
            extra={
                "operation": "hitl_block.escalate",
                "status": "success",
                "session_id": session_id,
                "ticket_id": ticket_id,
                "escalation_reason": escalation_reason,
                "delivered": delivered,
                "reason": reason,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

        return {
            "queued": queued,
            "delivered": delivered,
            "reason": reason,
            "ticket_id": ticket_id,
            "holding_message": self._holding_message,
        }

    def _deliver(
        self,
        ticket_id: str,
        session_id: str,
        escalation_reason: str,
        workflow_step: str,
        handoff: dict | None,
    ) -> tuple[bool, bool, str]:
        """Write to the configured backend. Returns (queued, delivered, reason)."""
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
            return True, False, "log_only"
        if self._queue_backend == "webhook":
            url, secret, _ = webhook_settings(os.environ)
            if not url or not secret:
                return False, False, "misconfigured"
            body = json.dumps({"ticket_id": ticket_id, **(handoff or {})}, ensure_ascii=False).encode("utf-8")
            delivered, reason = deliver_webhook(body, url=url, secret=secret, client=self._http)
            return True, delivered, reason
        logger.warning(
            "hitl_block.unsupported_backend",
            extra={
                "operation": "hitl_block.queue_write",
                "status": "skipped",
                "ticket_id": ticket_id,
                "backend": self._queue_backend,
            },
        )
        return False, False, "unsupported_backend"
