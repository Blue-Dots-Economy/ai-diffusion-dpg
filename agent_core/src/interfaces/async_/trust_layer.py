"""
agent_core/interfaces/async_trust_layer.py

Async contract that Agent Core's stream_turn() requires from the Trust Layer DPG.
Mirror of TrustLayerBase with all methods as async def.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.models import TrustCheckResult


class AsyncTrustLayerBase(ABC):

    @abstractmethod
    async def check_input(self, session_id: str, user_message: str, active_risks: Optional[list[str]] = None) -> TrustCheckResult:
        """Async version of TrustLayerBase.check_input(). See sync interface for full docs."""

    @abstractmethod
    async def check_output(self, session_id: str, llm_response: str) -> TrustCheckResult:
        """Async version of TrustLayerBase.check_output(). See sync interface for full docs."""

    @abstractmethod
    async def check_consent(self, session_id: str, connector_name: str) -> bool:
        """Async version of TrustLayerBase.check_consent(). See sync interface for full docs."""

    @abstractmethod
    async def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: Optional[str],
    ) -> dict:
        """Async version of TrustLayerBase.assemble_constraints(). See sync interface for full docs."""

    @abstractmethod
    async def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Async version of TrustLayerBase.verify_consent(). See sync interface for full docs."""

    @abstractmethod
    async def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Async version of TrustLayerBase.escalate(). See sync interface for full docs."""
