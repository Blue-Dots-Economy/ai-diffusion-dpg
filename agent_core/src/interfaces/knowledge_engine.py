"""
agent_core/interfaces/knowledge_engine.py

Contract that Agent Core requires from the Knowledge Engine DPG.

The Knowledge Engine performs RAG retrieval over domain documents and returns
raw chunks. Agent Core (via ManagerAgent) is responsible for assembling the
final system prompt and messages from the retrieved chunks.

Language Normalisation and NLU Processor run in Agent Core (steps 3-4 of
process_turn). KE receives their results as parameters for intent-based
filtering and glossary normalisation.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from src.models import RetrievalChunk


class KnowledgeEngineBase(ABC):

    @abstractmethod
    def retrieve(
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
        """
        Run RAG retrieval for this turn and return knowledge chunks.

        Agent Core has already run Language Normalisation and NLU — their results
        are passed as parameters. KE uses them for Glossary normalisation and
        Static KB intent-based filtering.

        Agent Core (ManagerAgent) assembles the final system prompt and messages
        from the returned chunks using build_system_prompt() and build_messages().

        Args:
            session_id:        Session identifier.
            user_message:      Raw user message text.
            profile:           UserProfile dict from ContextBundle.
            session:           Session state dict from ContextBundle.
            intent:            Classified intent from NLU Processor.
            entities:          Extracted entities dict from NLU Processor.
            sentiment:         Sentiment class from NLU Processor.
            confidence:        NLU confidence score 0.0-1.0.
            normalised_input:  Cleaned text from Language Normaliser.
            detected_language: Language detected by Language Normaliser.

        Returns:
            list[RetrievalChunk]: Retrieved knowledge chunks (may include
            always-include chunks). Empty list if no chunks found.
            Never raises.
        """
