"""
knowledge_engine/src/models.py

Shared dataclasses used across all Knowledge Engine components.
These mirror the equivalent types in agent_core but are defined independently
so Knowledge Engine has no compile-time dependency on agent_core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Session state (mirrors agent_core/src/models.py:SessionState)
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """
    Session context received from Agent Core at the start of every turn.
    Passed through to KEContext unchanged — KE reads it but never writes it.
    The structure mirrors agent_core.src.models.SessionState exactly so that
    Agent Core can pass its SessionState instances without conversion.
    """

    session_id: str
    history: list[dict]                 # [{"role": "user"|"assistant", "content": str}, ...]
    confirmed_entities: dict[str, Any]  # entities already confirmed across prior turns
    workflow_step: Optional[str]        # current workflow step, if any
    user_profile: dict[str, Any]        # persistent user profile data

    @staticmethod
    def empty(session_id: str) -> "SessionState":
        """Return a blank SessionState for brand-new sessions (used in tests)."""
        return SessionState(
            session_id=session_id,
            history=[],
            confirmed_entities={},
            workflow_step=None,
            user_profile={},
        )


# ---------------------------------------------------------------------------
# LLM response (mirrors agent_core/src/models.py:LLMResponse)
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """
    Normalised response from the LLM, returned by HttpLLMWrapper.
    Mirrors agent_core.src.models.LLMResponse so Agent Core and KE share
    compatible structures without a shared package.

    KE blocks only make direct completion calls (no tool_use), so tool_calls
    is always an empty list in KE usage.
    """

    content: Optional[str]
    stop_reason: str = "end_turn"   # "end_turn" | "max_tokens" | "error"
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Retrieval request / response (new interface — replaces AssemblePromptRequest)
# ---------------------------------------------------------------------------


@dataclass
class RetrievalChunk:
    """
    A single chunk of retrieved knowledge.
    Mirrors agent_core.src.models.RetrievalChunk — KE defines its own copy
    to remain dependency-free from agent_core.
    """
    text: str
    doc_type: str = ""
    source: str = ""
    always_include: bool = False


@dataclass
class RetrievalRequest:
    """
    Request body for POST /retrieve from Agent Core.
    Carries everything KE needs: user context + pre-computed NLU results.
    """
    session_id: str
    user_message: str
    profile: dict = field(default_factory=dict)    # UserProfile from ContextBundle
    session: dict = field(default_factory=dict)    # Session state from ContextBundle
    intent: str = "unknown"
    entities: dict = field(default_factory=dict)
    sentiment: str = "neutral"
    confidence: float = 0.0
    normalised_input: str = ""
    detected_language: str = ""


@dataclass
class RetrievalResponse:
    """
    Response body for POST /retrieve.
    Contains retrieval chunks only — prompt assembly is Agent Core's responsibility.
    """
    session_id: str
    chunks: list[RetrievalChunk] = field(default_factory=list)
