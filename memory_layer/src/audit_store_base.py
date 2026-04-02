"""
memory_layer/src/audit_store_base.py

AuditStoreBase — abstract interface for the audit persistence layer within the
DPG Memory Layer block. Concrete implementations (SQLite, PostgreSQL, etc.) must
inherit from this class and implement every method so they can be swapped without
changing MemoryLayer or its tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class AuditStoreBase(ABC):
    """Abstract base class for session and turn audit persistence.

    Defines the contract that all audit store backends must satisfy.
    MemoryLayer depends only on this interface, never on a concrete implementation.
    """

    @abstractmethod
    def record_session_event(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: Optional[str] = None,
        consent_given: Optional[str] = None,
    ) -> None:
        """Record a session lifecycle event.

        Args:
            session_id:    Session identifier.
            user_id:       User identifier.
            action:        'start', 'end', or 'escalate'.
            reason:        Optional reason for the action.
            consent_given: DPDP consent state — 'true', 'false', or None (pending).
        """

    @abstractmethod
    def record_turn_history(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_msg: str,
        system_msg: str,
        subagent_id: str = "",
        intent: str = "",
        model: str = "",
        latency_ms: int = 0,
        metadata: Optional[dict] = None,
    ) -> None:
        """Record a single conversation turn.

        Args:
            session_id:  Session identifier.
            user_id:     User identifier.
            turn_id:     Unique identifier for this turn.
            user_msg:    The user's message text.
            system_msg:  The agent's response text.
            subagent_id: Identifier of the subagent that handled the turn.
            intent:      NLU-detected intent label.
            model:       LLM model used for this turn.
            latency_ms:  End-to-end latency for this turn in milliseconds.
            metadata:    Optional dict of additional turn metadata.
        """

    @abstractmethod
    def update_consent(self, session_id: str, consent_given: str) -> None:
        """Persist the user's consent decision for an active session.

        Args:
            session_id:    Session identifier.
            consent_given: 'true' if user accepted storage, 'false' if declined.
        """

    @abstractmethod
    def get_history(self, session_id: str) -> list[dict]:
        """Retrieve full turn history for a session, sorted by timestamp.

        Args:
            session_id: Session identifier.

        Returns:
            List of turn dicts sorted ascending by timestamp.
            Returns an empty list if the session has no recorded turns.
        """
