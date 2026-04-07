"""
trust_layer/src/guardrails.py

BasicTrustLayer — PoC stub for the Trust Layer DPG.

Implements the TrustLayerBase interface using simple phrase-based rules loaded
from YAML config. No ML model, no embedding similarity — pure string matching.

Design:
- All rules are loaded at construction time from config. Zero runtime config re-reads.
- check_input(): blocked_phrases → "block", escalation_topics → "escalate", else → "allow"
- check_output(): blocked_output_phrases → "block", else → "allow"
- check_consent(): PoC stub always returns True — no consent flow implemented.
- All phrase matching is case-insensitive substring search.
- Empty / None message is always allowed (not a content violation).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class BasicTrustLayer:
    """
    Phrase-based trust layer stub.

    Reads all rules from config at construction time. Rule sections:
        trust.input_rules.blocked_phrases      — list of strings
        trust.input_rules.escalation_topics    — list of strings
        trust.output_rules.blocked_phrases     — list of strings

    Args:
        config: Full config dict containing a "trust" section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        trust_cfg = config.get("trust", {})
        input_cfg = trust_cfg.get("input_rules", {})
        output_cfg = trust_cfg.get("output_rules", {})

        self._blocked_phrases: list[str] = [
            p.lower() for p in input_cfg.get("blocked_phrases", []) if p
        ]
        self._escalation_topics: list[str] = [
            t.lower() for t in input_cfg.get("escalation_topics", []) if t
        ]
        self._blocked_output_phrases: list[str] = [
            p.lower() for p in output_cfg.get("blocked_phrases", []) if p
        ]

        logger.info(
            "trust_layer.init",
            extra={
                "operation": "guardrails.init",
                "status": "success",
                "blocked_phrases_count": len(self._blocked_phrases),
                "escalation_topics_count": len(self._escalation_topics),
                "blocked_output_phrases_count": len(self._blocked_output_phrases),
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors TrustLayerBase
    # ------------------------------------------------------------------

    def check_input(self, session_id: str, user_message: str, active_risks: list | None = None) -> dict:
        """
        Evaluate raw user input against content rules and topic firewall.

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None)
            action is one of: "allow", "block", "escalate"
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        # Empty or None input is not a content violation
        if not user_message:
            return _result(passed=True, action="allow")

        lower_msg = user_message.lower()

        # Blocked phrases take priority over escalation
        for phrase in self._blocked_phrases:
            if phrase in lower_msg:
                logger.warning(
                    "trust_layer.input_blocked",
                    extra={
                        "operation": "guardrails.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_phrase: {phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_phrase: {phrase}")

        # Escalation topics — route to human agent
        for topic in self._escalation_topics:
            if topic in lower_msg:
                logger.warning(
                    "trust_layer.input_escalated",
                    extra={
                        "operation": "guardrails.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"escalation_topic: {topic}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="escalate", reason=f"escalation_topic: {topic}")

        logger.info(
            "trust_layer.input_allowed",
            extra={
                "operation": "guardrails.check_input",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")

    def check_output(self, session_id: str, llm_response: str) -> dict:
        """
        Evaluate LLM-generated response against output safety rules.

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None)
            action is "block" if a blocked phrase is found, otherwise "allow".
            Orchestrator replaces blocked output with output_blocked_message from AC config.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not llm_response:
            return _result(passed=True, action="allow")

        lower_resp = llm_response.lower()

        for phrase in self._blocked_output_phrases:
            if phrase in lower_resp:
                logger.warning(
                    "trust_layer.output_blocked",
                    extra={
                        "operation": "guardrails.check_output",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_output_phrase: {phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_output_phrase: {phrase}")

        logger.info(
            "trust_layer.output_allowed",
            extra={
                "operation": "guardrails.check_output",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")

    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """
        Verify that confirmed user consent exists for a write or identity connector.

        PoC stub: always returns True.
        In production, this checks a consent record in the session state or a
        dedicated consent store keyed by (session_id, connector_name).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        logger.info(
            "trust_layer.consent_check",
            extra={
                "operation": "guardrails.check_consent",
                "status": "success",
                "session_id": session_id,
                "connector_name": connector_name,
                "note": "PoC stub — always returns True",
            },
        )
        return True


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _result(passed: bool, action: str, reason: str | None = None) -> dict:
    """Build a TrustCheckResult-compatible dict."""
    return {"passed": passed, "action": action, "reason": reason}
