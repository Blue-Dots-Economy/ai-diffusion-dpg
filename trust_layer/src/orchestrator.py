"""
trust_layer/src/orchestrator.py

TrustLayer — orchestrator wiring all four sub-blocks.

Replaces BasicTrustLayer as the primary implementation.
All config is parsed at construction time. Zero runtime config reads.
"""

from __future__ import annotations

import logging

from blocks.content import ContentBlock
from blocks.guardrails import GuardrailsBlock
from blocks.consent import ConsentBlock
from blocks.hitl import HiTLBlock
from consent_store import ConsentStore

logger = logging.getLogger(__name__)


class TrustLayer:
    """Orchestrates ContentBlock, GuardrailsBlock, ConsentBlock, and HiTLBlock.

    Args:
        config: Full config dict containing a "trust" section.
    """

    def __init__(self, config: dict) -> None:
        """Construct TrustLayer with all sub-blocks.

        Args:
            config: Full config dict containing a "trust" section.

        Raises:
            ValueError: If config is None.
        """
        if config is None:
            raise ValueError("config must not be None")
        self._content = ContentBlock(config)
        self._guardrails = GuardrailsBlock(config)
        self._consent = ConsentBlock(config)
        self._hitl = HiTLBlock(config)
        trust_cfg = (config or {}).get("trust", {})
        db_path = trust_cfg.get("consent_store", {}).get("db_path", "/tmp/dpg_consent.db")
        self._consent_store = ConsentStore(db_path)

        logger.info(
            "trust_layer.init",
            extra={"operation": "trust_layer.init", "status": "success"},
        )

    def check_input(self, session_id: str, user_message: str, active_risks: list[str] | None = None) -> dict:
        """Delegate to ContentBlock.check_input.

        Args:
            session_id: Unique identifier for the conversation session.
            user_message: The raw user message to be checked.
            active_risks: Optional list of active risk categories.

        Returns:
            Dict with keys: passed, action, reason.
        """
        return self._content.check_input(session_id, user_message, active_risks)

    def check_output(self, session_id: str, llm_response: str) -> dict:
        """Delegate to ContentBlock.check_output.

        Args:
            session_id: Unique identifier for the conversation session.
            llm_response: The LLM-generated response text to be checked.

        Returns:
            Dict with keys: passed, action, reason.
        """
        return self._content.check_output(session_id, llm_response)

    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """Connector-level consent check backed by the SQLite consent store.

        Args:
            session_id: Unique identifier for the conversation session.
            connector_name: The name of the connector requiring consent check.

        Returns:
            True if a consent record exists for this session, False otherwise.

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        granted = self._consent_store.has_consent(session_id)
        logger.info(
            "trust_layer.check_consent",
            extra={
                "operation": "trust_layer.check_consent",
                "status": "success",
                "session_id": session_id,
                "connector_name": connector_name,
                "granted": granted,
            },
        )
        return granted

    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: str | None,
    ) -> dict:
        """Delegate to GuardrailsBlock.assemble_constraints.

        Args:
            session_id: Unique identifier for the conversation session.
            workflow_step: Current step in the workflow.
            active_risks: List of active risk categories.
            user_segment: Optional user segment or role.

        Returns:
            Dict with guardrail constraints.
        """
        return self._guardrails.assemble_constraints(
            session_id, workflow_step, active_risks, user_segment
        )

    def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Delegate to ConsentBlock.verify_consent and persist consent if granted.

        Args:
            session_id: Unique identifier for the conversation session.
            user_message: The user's message that may contain consent signal.

        Returns:
            True if consent is verified.
        """
        granted = self._consent.verify_consent(session_id, user_message)
        if granted:
            self._consent_store.record_consent(session_id)
        return granted

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Delegate to HiTLBlock.escalate.

        Args:
            session_id: Unique identifier for the conversation session.
            escalation_reason: Reason for escalation.
            user_message: The user's message that triggered escalation.
            workflow_step: The workflow step at which escalation occurred.

        Returns:
            Dict with queued, ticket_id, and holding_message.
        """
        return self._hitl.escalate(session_id, escalation_reason, user_message, workflow_step)
