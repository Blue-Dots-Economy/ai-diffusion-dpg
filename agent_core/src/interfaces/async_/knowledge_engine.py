"""
agent_core/interfaces/async_knowledge_engine.py

Async contract that Agent Core's stream_turn() requires from the Knowledge Engine DPG.
Mirror of KnowledgeEngineBase with all methods as async def.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from src.models import RetrievalChunk


class AsyncKnowledgeEngineBase(ABC):

    @abstractmethod
    async def retrieve(
        self,
        session_id: str,
        user_message: str,
        profile: dict,
        session: dict,
        intent: str = "unknown",
        entities: Optional[dict[str, Any]] = None,
        sentiment: str = "neutral",
        confidence: float = 0.0,
        normalised_input: str = "",
        detected_language: str = "",
    ) -> list[RetrievalChunk]:
        """Async version of KnowledgeEngineBase.retrieve(). See sync interface for full docs."""
