"""
agent_core/interfaces/memory_layer.py

Contract that Agent Core requires from the Memory Layer DPG.
Five methods — the only API Agent Core ever calls.
Agent Core never touches Redis or Neo4j directly.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.models import ContextBundle


class MemoryLayerBase(ABC):

    @abstractmethod
    def context_bundle(self, session_id: str, user_id: str, adopt: bool = True) -> ContextBundle:
        """
        Called at the START of every turn.

        First call for a new session_id:
          - Checks Neo4j for user_id (returning user?)
          - Creates Journey node in Neo4j
          - Initialises Redis hash with default session state
          - Loads prior journey summary if returning user

        All subsequent calls (existing session_id in Redis):
          - Reads Redis hash (hot path — no Neo4j init)
          - Reads UserProfile from Neo4j (declared fields + UserAttribute nodes)
          - Returns ContextBundle

        Returns ContextBundle.empty() on any failure. Never raises.
        """

    @abstractmethod
    def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """
        Called AFTER every turn (async daemon thread — must not block response).

        scope="session"
          → Redis HSET on session:{session_id} + reset TTL
          → Also updates last_accessed in user:{user_id} hash + resets user key TTL

        scope="persistent"
          → Neo4j MERGE on UserProfile node
          → If key in declared_fields: SET property directly on UserProfile
          → If key not in declared_fields: MERGE UserAttribute child node

        scope="signal"
          → Neo4j CREATE Signal node under ContextGraph

        scope="journey_event"
          → Neo4j CREATE/MERGE journey child node under current Journey

        Never raises.
        """

    @abstractmethod
    def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """
        Called when a session ends:
          - termination_intent detected by NLU
          - SIGTERM received
          - Escalation to HITL

        Operations (in order):
          1. Read full Redis hash for session
          2. Execute merge_on_session_end rules from config
             (promotes session fields to Journey node properties)
          3. Close Journey node: SET ended_at, end_reason
          4. If consent == "false": DETACH DELETE the User node (DPDP)
          5. redis.delete("session:{session_id}")
          6. redis.hdel("user:{user_id}", session_id)
          7. If no fields remain in user:{user_id}: redis.delete("user:{user_id}")

        Never raises.
        """

    @abstractmethod
    def get_active_sessions(self, user_id: str) -> list[dict]:
        """
        Called when a user reconnects, before any session is started.

        Reads user:{user_id} hash from Redis.
        For each session_id field:
          - Checks if session:{session_id} still exists (TTL not expired)
          - If expired: removes that field from user:{user_id} (lazy cleanup)
        Returns list of alive sessions sorted by last_accessed descending.

        Return format:
          [
            {"session_id": "<id>", "last_accessed": "2026-03-26T15:30:00Z"},
            ...
          ]

        Returns [] if no active sessions or user key not found. Never raises.
        """

    @abstractmethod
    def delete_user(self, user_id: str) -> None:
        """
        DPDP right-to-erasure.

        MATCH (u:User {user_id: $user_id}) DETACH DELETE u
        Removes User + all subnodes (UserProfile, JourneyHistory, ContextGraph)
        and all their descendants.
        Also deletes user:{user_id} key from Redis.

        Never raises.
        """

    @abstractmethod
    def record_audit_session(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: str = None,
        consent_given: str = None,
    ) -> None:
        """
        Record a session lifecycle event (start, end, escalate) in the audit store.

        Args:
            session_id:    Session identifier.
            user_id:       User identifier.
            action:        'start', 'end', or 'escalate'.
            reason:        Optional reason for the action.
            consent_given: DPDP consent state — 'true', 'false', or None (pending).
        """

    @abstractmethod
    def record_audit_turn(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_message: str,
        system_message: str,
        metadata: dict = None
    ) -> None:
        """
        Record a single conversation turn in the audit store.
        """

    @abstractmethod
    def get_chat_history(self, session_id: str) -> list[dict]:
        """
        Retrieve full chat history for a session, sorted by timestamp.

        Args:
            session_id: Session identifier.

        Returns:
            List of turn dicts sorted by timestamp ascending. Returns [] on failure.
        """
