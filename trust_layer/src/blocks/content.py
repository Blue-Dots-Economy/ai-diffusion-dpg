"""
trust_layer/src/blocks/content.py

ContentBlock — phrase-based input and output safety checks.

Reads blocked_phrases, escalation_topics, and blocked_phrases (output) from
trust.input_rules and trust.output_rules config sections. All matching is
case-insensitive substring search.

active_risks is accepted but not acted upon in this implementation — it is
passed through for future semantic matching upgrades.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class ContentBlock:
    """
    Phrase-match input/output content checker.

    Args:
        config: Full config dict containing a "trust" section.
    """

    def __init__(self, config: dict) -> None:
        start = time.time()
        trust_cfg = (config or {}).get("trust", {})
        input_cfg = trust_cfg.get("input_rules", {})
        output_cfg = trust_cfg.get("output_rules", {})

        self._blocked_input: list[str] = [
            p.lower() for p in input_cfg.get("blocked_phrases", []) if p
        ]
        self._escalation_topics: list[str] = [
            t.lower() for t in input_cfg.get("escalation_topics", []) if t
        ]
        self._blocked_output: list[str] = [
            p.lower() for p in output_cfg.get("blocked_phrases", []) if p
        ]

        logger.info(
            "content_block.init",
            extra={
                "operation": "content_block.init",
                "status": "success",
                "blocked_input_count": len(self._blocked_input),
                "escalation_count": len(self._escalation_topics),
                "blocked_output_count": len(self._blocked_output),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    def check_input(
        self,
        session_id: str,
        user_message: str | None,
        active_risks: list[str] | None = None,
    ) -> dict:
        """
        Check user input against blocked phrases and escalation topics.

        Args:
            session_id: Current session identifier.
            user_message: Raw user input.
            active_risks: Risk signals from NLU (accepted, not yet acted upon).

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None).
            action is "allow", "block", or "escalate".
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not user_message:
            return _result(passed=True, action="allow")

        lower = user_message.lower()

        for phrase in self._blocked_input:
            if phrase in lower:
                logger.warning(
                    "content_block.input_blocked",
                    extra={
                        "operation": "content_block.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_phrase:{phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_phrase:{phrase}")

        for topic in self._escalation_topics:
            if topic in lower:
                logger.warning(
                    "content_block.input_escalated",
                    extra={
                        "operation": "content_block.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"escalation_topic:{topic}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="escalate", reason=f"escalation_topic:{topic}")

        logger.info(
            "content_block.input_allowed",
            extra={
                "operation": "content_block.check_input",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")

    def check_output(self, session_id: str, llm_response: str | None) -> dict:
        """
        Check LLM output against blocked phrases.

        Args:
            session_id: Current session identifier.
            llm_response: LLM-generated response text.

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not llm_response:
            return _result(passed=True, action="allow")

        lower = llm_response.lower()

        for phrase in self._blocked_output:
            if phrase in lower:
                logger.warning(
                    "content_block.output_blocked",
                    extra={
                        "operation": "content_block.check_output",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_output_phrase:{phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_output_phrase:{phrase}")

        logger.info(
            "content_block.output_allowed",
            extra={
                "operation": "content_block.check_output",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")


def _result(passed: bool, action: str, reason: str | None = None) -> dict:
    """Build a standard check result dict."""
    return {"passed": passed, "action": action, "reason": reason}
