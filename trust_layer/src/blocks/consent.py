"""
trust_layer/src/blocks/consent.py

ConsentBlock — DPDP Act consent phrase evaluation.

Stateless: evaluates the user's message against consent_phrases and
decline_phrases from config. Returns True if a consent phrase is found,
False for decline or unclear responses. Agent Core owns all flag management
and Memory Layer writes.

Config section: trust.consent
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class ConsentBlock:
    """
    Evaluates whether a user message grants or declines consent.

    Args:
        config: Full config dict containing trust.consent section.
    """

    def __init__(self, config: dict) -> None:
        start = time.time()
        consent_cfg = (config or {}).get("trust", {}).get("consent", {})
        self._consent_phrases: list[str] = [
            p.lower() for p in consent_cfg.get("consent_phrases", []) if p
        ]
        self._decline_phrases: list[str] = [
            p.lower() for p in consent_cfg.get("decline_phrases", []) if p
        ]

        logger.info(
            "consent_block.init",
            extra={
                "operation": "consent_block.init",
                "status": "success",
                "consent_phrase_count": len(self._consent_phrases),
                "decline_phrase_count": len(self._decline_phrases),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    def verify_consent(self, session_id: str, user_message: str | None) -> bool:
        """
        Evaluate user message against configured consent and decline phrases.

        Args:
            session_id: Current session identifier.
            user_message: User's response to the consent prompt.

        Returns:
            True if a consent phrase is found; False for decline or unclear.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not user_message:
            logger.info(
                "consent_block.verify",
                extra={
                    "operation": "consent_block.verify_consent",
                    "status": "success",
                    "session_id": session_id,
                    "granted": False,
                    "reason": "empty_message",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return False

        lower = user_message.lower()

        for phrase in self._consent_phrases:
            if phrase in lower:
                logger.info(
                    "consent_block.verify",
                    extra={
                        "operation": "consent_block.verify_consent",
                        "status": "success",
                        "session_id": session_id,
                        "granted": True,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return True

        logger.info(
            "consent_block.verify",
            extra={
                "operation": "consent_block.verify_consent",
                "status": "success",
                "session_id": session_id,
                "granted": False,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return False
