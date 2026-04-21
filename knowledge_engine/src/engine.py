"""
knowledge_engine/src/engine.py

KnowledgeEngine — the orchestrator for the 3 retrieval blocks.

Implements KnowledgeEngineBase. Called by Agent Core after Language Normalisation
and NLU Processor have already run (Agent Core steps 3-4). KE receives the NLU
results as parameters and returns raw knowledge chunks.

Prompt assembly (system prompt + messages) is Agent Core's responsibility,
handled by ManagerAgent.build_system_prompt() and build_messages().

Block execution order (fixed — cannot be changed by config):
    [1] Glossary             — normalises entity values using domain vocabulary
    [2] Static Knowledge Base — RAG retrieval using intent + entities
    [3] Multimodal Input Handler — processes attached images/PDFs (if present)

Design:
- retrieve() is the only public entry point for Knowledge Engine.
- All 3 blocks are instantiated at startup; disabled blocks are skipped at process time.
- retrieve() never raises — all errors are absorbed by blocks.
- llm is optional: Glossary and Static KB do not use it. Multimodal may use it
  for image description when enabled. Pass None if multimodal is disabled.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.base import (
    KnowledgeEngineBase,
    KnowledgeBlock,
    KEContext,
    LLMWrapperBase,
)
from src.models import SessionState  # used internally by retrieve() to build KEContext
from src.blocks.glossary import GlossaryBlock
from src.blocks.static_knowledge_base import StaticKnowledgeBaseBlock
from src.blocks.multimodal_input_handler import MultimodalInputHandlerBlock

logger = logging.getLogger(__name__)

# Fixed execution order — block name maps to class.
# Language Normalisation and NLU Processor have been moved to Agent Core.
_BLOCK_REGISTRY: list[tuple[str, type[KnowledgeBlock]]] = [
    ("glossary", GlossaryBlock),
    ("static_knowledge_base", StaticKnowledgeBaseBlock),
    ("multimodal_input_handler", MultimodalInputHandlerBlock),
]


class KnowledgeEngine(KnowledgeEngineBase):
    """
    Stateless Knowledge Engine orchestrator.

    Instantiated once at service startup. Blocks are also instantiated once and
    reused across all sessions — they hold no session-scoped state.

    Args:
        config: Full config dict from config/config.yaml.
        llm:    Optional LLMWrapperBase instance. Only needed when multimodal block
                is enabled. Pass None (or omit) if multimodal is disabled.
    """

    def __init__(self, config: dict, llm: Optional[LLMWrapperBase] = None) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._config = config
        self._llm = llm
        self._blocks: list[KnowledgeBlock] = self._init_blocks()
        self._warmup_blocks()

        logger.info(
            "knowledge_engine.init",
            extra={
                "operation": "knowledge_engine.init",
                "status": "success",
                "blocks_loaded": len(self._blocks),
            },
        )

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
    ) -> list:
        """
        Run Glossary, Static KB, and Multimodal blocks then return chunks only.

        Prompt assembly (system prompt + messages) is Agent Core's responsibility.
        Blocks run identically to assemble_prompt() — only the return value differs.

        Returns:
            list[RetrievalChunk]: chunks from static KB + always-include sources.
            Empty list if user_message is empty or no chunks found.
            Never raises.
        """
        from src.models import RetrievalChunk as KERetrievalChunk

        if not user_message:
            return []

        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        # Build a minimal SessionState from the session + profile dicts
        # so existing blocks (Glossary, StaticKB) still work unchanged.
        session_state = SessionState(
            session_id=session_id,
            history=[],   # no history in new design
            confirmed_entities=dict(entities) if entities else {},
            workflow_step=session.get("current_node") or session.get("workflow_step"),
            user_profile=dict(profile) if profile else {},
        )

        context = KEContext(
            session_id=session_id,
            raw_input=user_message,
            normalised_input=normalised_input or user_message,
            detected_language=detected_language,
            intent=intent,
            entities=dict(entities) if entities else {},
            sentiment=sentiment,
            confidence=confidence,
            retrieval_chunks=[],
            always_include_chunks=[],
            session_state=session_state,
        )

        for block in self._blocks:
            try:
                context = block.process(context, self._llm, self._config)
            except Exception as e:
                logger.error(
                    "knowledge_engine.block_failure",
                    extra={
                        "operation": "knowledge_engine.retrieve",
                        "status": "failure",
                        "session_id": session_id,
                        "block": type(block).__name__,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

        # Convert raw chunk dicts to RetrievalChunk objects
        chunks: list[KERetrievalChunk] = []
        for c in context.always_include_chunks:
            chunks.append(KERetrievalChunk(
                text=c.get("text", ""),
                doc_type=c.get("doc_type", ""),
                source=c.get("source", ""),
                always_include=True,
            ))
        for c in context.retrieval_chunks:
            chunks.append(KERetrievalChunk(
                text=c.get("text", ""),
                doc_type=c.get("doc_type", ""),
                source=c.get("source", ""),
                always_include=False,
            ))

        logger.info(
            "knowledge_engine.retrieve",
            extra={
                "operation": "knowledge_engine.retrieve",
                "status": "success",
                "session_id": session_id,
                "intent": context.intent,
                "chunk_count": len(chunks),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return chunks

    def get_static_kb_block(self):
        """Return the StaticKnowledgeBaseBlock instance, or None if not present.

        Returns:
            The StaticKnowledgeBaseBlock if it exists in the engine's block list,
            otherwise None.
        """
        from src.blocks.static_knowledge_base import StaticKnowledgeBaseBlock
        for block in self._blocks:
            if isinstance(block, StaticKnowledgeBaseBlock):
                return block
        return None

    # ------------------------------------------------------------------
    # Private: block initialisation
    # ------------------------------------------------------------------

    def _init_blocks(self) -> list[KnowledgeBlock]:
        """
        Instantiate all 3 blocks.
        All blocks are always instantiated — disabled blocks are skipped
        inside their own process() method based on config.
        """
        return [cls() for _, cls in _BLOCK_REGISTRY]

    def _warmup_blocks(self) -> None:
        """Pre-warm blocks that have expensive cold-start initialisation (e.g. embedding model load)."""
        block_cfg = (
            self._config.get("knowledge", {})
            .get("blocks", {})
            .get("static_knowledge_base", {})
        )
        for block in self._blocks:
            if hasattr(block, "warmup"):
                block.warmup(block_cfg)

