"""
agent_core/interfaces/async_memory_layer.py

Async contract that Agent Core's stream_turn() requires from the Memory Layer DPG.
Mirror of MemoryLayerBase with all methods as async def.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.models import ContextBundle


class AsyncMemoryLayerBase(ABC):

    @abstractmethod
    async def context_bundle(self, session_id: str, user_id: str, adopt: bool = True) -> ContextBundle:
        """Async version of MemoryLayerBase.context_bundle(). See sync interface for full docs."""

    @abstractmethod
    async def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """Async version of MemoryLayerBase.write(). See sync interface for full docs."""

    @abstractmethod
    async def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """Async version of MemoryLayerBase.flush_session(). See sync interface for full docs."""

    @abstractmethod
    async def get_active_sessions(self, user_id: str) -> list[dict]:
        """Async version of MemoryLayerBase.get_active_sessions(). See sync interface for full docs."""

    @abstractmethod
    async def delete_user(self, user_id: str) -> None:
        """Async version of MemoryLayerBase.delete_user(). See sync interface for full docs."""

    @abstractmethod
    async def record_audit_session(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: str = None,
        consent_given: str = None,
    ) -> None:
        """Async version of MemoryLayerBase.record_audit_session(). See sync interface for full docs."""

    @abstractmethod
    async def record_audit_turn(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_message: str,
        system_message: str,
        metadata: dict = None
    ) -> None:
        """Async version of MemoryLayerBase.record_audit_turn(). See sync interface for full docs."""

    @abstractmethod
    async def get_chat_history(self, session_id: str) -> list[dict]:
        """Async version of MemoryLayerBase.get_chat_history(). See sync interface for full docs."""
