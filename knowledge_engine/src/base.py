"""
knowledge_engine/src/base.py

Base classes and the shared context object for all Knowledge Engine components:

- LLMWrapperBase   — ABC for the LLM client injected into KE (HttpLLMWrapper)
- KEContext        — shared data object passed through all 3 processing blocks
- KnowledgeBlock   — ABC every block must inherit and implement
- KnowledgeEngineBase — ABC the KnowledgeEngine orchestrator must inherit

These are the only types that cross module boundaries inside Knowledge Engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from src.models import LLMResponse, SessionState


# ---------------------------------------------------------------------------
# LLM wrapper interface
# ---------------------------------------------------------------------------


class LLMWrapperBase(ABC):
    """
    Abstract interface for the LLM client used by Knowledge Engine blocks.

    Currently used only by MultimodalInputHandler (src/blocks/multimodal_input_handler.py)
    for image and PDF description via LLM vision. That block is currently DISABLED
    (knowledge.blocks.multimodal_input_handler.enabled: false in config), so llm=None
    is passed to KnowledgeEngine at startup. This interface will be active when
    multimodal is enabled.

    Concrete implementation: HttpLLMWrapper (knowledge_engine/src/llm_proxy_client.py).
    It forwards calls to Agent Core's POST /internal/llm/call endpoint so that
    KE never holds an Anthropic API key.

    Glossary and StaticKnowledgeBaseBlock receive llm as a parameter (required by the
    KnowledgeBlock contract) but do not call it.
    """

    @abstractmethod
    def call(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        system: str = "",
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        """
        Execute a single LLM call.

        Args:
            messages:       Conversation messages in Anthropic format.
            tools:          Tool definitions. Pass empty list if no tools needed.
            system:         System prompt string.
            model_override: Override the active model for this call if needed.

        Returns:
            LLMResponse — always. Never raises.
            On failure: LLMResponse(content=None, stop_reason="error").
        """

    @abstractmethod
    def get_active_model(self) -> str:
        """Return the name of the currently active model."""


# ---------------------------------------------------------------------------
# Shared context object
# ---------------------------------------------------------------------------


@dataclass
class KEContext:
    """
    Shared data object passed through all 3 Knowledge Engine blocks in sequence.
    Created at the start of retrieve() with defaults, then enriched by each block.

    Language Normalisation and NLU run in Agent Core before this call.
    Their results arrive as parameters and are pre-loaded into the context.

    Immutable fields (set once, never changed):
        session_id      — identifies the session
        raw_input       — original user_message; blocks must never modify this
        session_state   — full session context from Memory Layer; read-only

    Fields pre-populated from Agent Core NLU results:
        normalised_input    — cleaned text from Language Normalisation
        detected_language   — "hindi" | "kannada" | "english" | "hinglish"
        intent              — classified intent label from NLU Processor
        entities            — extracted entities dict; Block 1 (Glossary) normalises values
        sentiment           — sentiment class from NLU Processor
        confidence          — NLU confidence score 0.0–1.0

    Mutable fields (enriched by blocks in order):
        retrieval_chunks    — Block 2 (Static KB): top-k relevant chunks
        always_include_chunks — Block 2: always-present chunks (bypass similarity filter)
    """

    session_id: str
    raw_input: str
    normalised_input: str
    detected_language: str
    intent: str
    entities: dict[str, Any]
    sentiment: str
    confidence: float
    retrieval_chunks: list[dict]
    always_include_chunks: list[dict]
    session_state: SessionState


# ---------------------------------------------------------------------------
# Block base class
# ---------------------------------------------------------------------------


class KnowledgeBlock(ABC):
    """
    Abstract base class for all 3 Knowledge Engine processing blocks.

    Every block receives the current KEContext, enriches it, and returns it.
    Blocks are stateless — all configuration is read from the config dict passed
    at process() time. The same block instance handles all sessions.

    Contract:
    - process() must never raise. On failure, log the error and return context unchanged.
    - process() must return KEContext — the same instance modified in-place is fine.
    - process() must not modify context.raw_input or context.session_state.
    """

    @abstractmethod
    def process(
        self,
        context: KEContext,
        llm: LLMWrapperBase,
        config: dict,
    ) -> KEContext:
        """
        Enrich the context with this block's output.

        Args:
            context: The shared KEContext object for this turn.
            llm:     The LLM client (HttpLLMWrapper) injected by the engine.
                     Blocks that don't need LLM (e.g. Glossary) ignore this parameter.
            config:  The full config dict from config/config.yaml.
                     Each block reads its own section: config["knowledge"]["blocks"][<name>].

        Returns:
            KEContext — enriched. Must never return None.
        """


# ---------------------------------------------------------------------------
# Knowledge Engine interface
# ---------------------------------------------------------------------------


class KnowledgeEngineBase(ABC):
    """
    Public contract for the Knowledge Engine.

    This mirrors agent_core/src/interfaces/knowledge_engine.py exactly.
    Agent Core depends on this interface; KnowledgeEngine must implement it.

    KE's sole responsibility is RAG retrieval — returning knowledge chunks.
    Prompt assembly (system prompt + messages) is Agent Core's responsibility,
    handled by ManagerAgent.build_system_prompt() and build_messages().

    Language Normalisation and NLU Processor run in Agent Core before this call.
    KE receives their results as parameters and runs only Glossary, Static KB, Multimodal.
    """

    @abstractmethod
    def retrieve(
        self,
        session_id: str,
        user_message: str,
        profile: dict,
        session: dict,
        intent: str = "unknown",
        entities: Optional[dict] = None,
        sentiment: str = "neutral",
        confidence: float = 0.0,
        normalised_input: str = "",
        detected_language: str = "",
    ) -> list:
        """
        Run RAG retrieval and return knowledge chunks.

        Returns list of RetrievalChunk (from src.models). Prompt assembly is
        Agent Core's responsibility. Never raises — returns [] on any failure.
        """

    @abstractmethod
    def get_static_kb_block(self) -> Optional[Any]:
        """Return the StaticKnowledgeBaseBlock instance, or None if not present."""
