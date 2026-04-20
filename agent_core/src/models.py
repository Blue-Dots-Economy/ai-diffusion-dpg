"""
agent_core/models.py

Shared dataclasses used as the data contract between all components of Agent Core
and the interfaces it calls. Every other file in agent_core/ imports from here.
No business logic. No imports from within agent_core/.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Knowledge retrieval
# ---------------------------------------------------------------------------


@dataclass
class RetrievalChunk:
    """
    A single chunk of retrieved knowledge returned by the Knowledge Engine.
    Used by ManagerAgent.build_messages() to construct the LLM prompt.
    """
    text: str
    doc_type: str = ""
    source: str = ""
    always_include: bool = False


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


@dataclass
class TurnInput:
    """Normalised user message received from the Reach Layer."""

    session_id: str
    user_message: str
    channel: str          # "cli" | "whatsapp" | "web" | "voip"
    timestamp_ms: int
    user_id: Optional[str] = None   # opaque identifier set by Reach Layer (phone, email, etc.)
    fresh: bool = False             # True when caller wants a clean "New chat" — disables session adoption


@dataclass
class SegmentInput:
    """A single text segment submitted to TurnAssembler via POST /sessions/{id}/input.

    Spec gap: The TurnAssembler spec defines add_segment(session_id, text) but
    does not carry metadata needed to construct TurnInput when invoking stream_turn().
    SegmentInput bridges this by carrying channel, user_id, and timestamp alongside
    the text so TurnAssembler can build TurnInput without a second round-trip.
    """

    text: str
    user_id: Optional[str] = None
    channel: str = "cli"
    timestamp_ms: int = 0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class ContextBundle:
    """
    Everything Agent Core needs to know about a user at the start of a turn.
    Returned by memory.context_bundle(session_id, user_id).

    Primary state contract between Memory Layer and Agent Core.

    Fields:
        session: Full Redis hash — current session state.
                 Always contains: user_id, journey_id, is_returning.
                 Plus all domain session fields declared in domain.yaml.

        profile: UserProfile declared fields + all UserAttribute nodes.
                 {
                   "<declared_field>": "<value>",
                   ...,
                   "attributes": [{"key": ..., "value": ..., "raw": ...}]
                 }

        journey: Prior journey summary — only for returning users.
                 {
                   "outcomes": [...],
                   "signals": [...],
                   "end_reason": "...",
                   ...promoted session fields from merge_on_session_end config...
                 }
                 None for new users.
    """

    session: dict
    profile: dict
    journey: dict | None = None

    @staticmethod
    def empty() -> ContextBundle:
        """Return a blank ContextBundle — used on failure paths."""
        return ContextBundle(session={}, profile={}, journey=None)


# ---------------------------------------------------------------------------
# NLU (Language Normalisation + Intent Classification)
# ---------------------------------------------------------------------------


@dataclass
class UserStateClassification:
    """
    Classification output for the user's mental state dimension.

    Populated by NLU Processor when the domain declares conversation.user_state_model.
    None on NLUResult when the model is disabled or absent.
    """

    id: str
    confidence: float


@dataclass
class NLUResult:
    """
    Combined output of Language Normalisation and NLU Processor steps run in Agent Core.
    Produced before the Knowledge Engine call and passed as parameters to KE's retrieve().
    """

    intent: str                              # classified intent label from config intents list
    entities: dict[str, Any]                 # extracted entity key→value pairs
    sentiment: str                           # one of the configured sentiment classes
    confidence: float                        # 0.0–1.0; below threshold triggers early exit
    active_risks: list[str] | None = None    # risk signals from NLU; None if not classified
    user_state: UserStateClassification | None = None   # classified user mental state (GH-139); None when model disabled


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------


@dataclass
class TrustCheckResult:
    """Result returned by TrustLayer.check_input() and check_output()."""

    passed: bool
    action: str                       # "allow" | "block" | "escalate"
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool use
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool call expressed by the LLM in its response."""

    tool_name: str
    tool_use_id: str
    input_params: dict[str, Any]


@dataclass
class ToolResult:
    """Normalised result returned by Action Gateway after executing a tool call."""

    tool_use_id: str
    tool_name: str
    result: dict[str, Any]
    success: bool
    result_text: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """
    Normalised response from the LLM wrapper.
    Never raises — failures are expressed via stop_reason="error".
    """

    content: Optional[str]
    tool_calls: list[ToolCall]        = field(default_factory=list)
    stop_reason: str                  = "end_turn"   # "end_turn" | "tool_use" | "max_tokens" | "error"
    model_used: str                   = ""
    input_tokens: int                 = 0
    output_tokens: int                = 0


# ---------------------------------------------------------------------------
# Turn output
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    """Final result returned to the Reach Layer after a completed turn."""

    session_id: str
    turn_id: str
    response_text: str
    was_escalated: bool               = False
    was_tool_used: bool               = False
    model_used: str                   = ""
    latency_ms: int                   = 0
    session_ended: bool               = False


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@dataclass
class TurnEvent:
    """
    Audit payload emitted to the Observability Layer after every turn.
    Emitted asynchronously — never in the response path.

    NOTE: user_message is intentionally excluded.
    PII is routed only through the Observability Layer's designated audit log path.
    trace_id links outcome metrics to the distributed trace; None if span context unavailable.
    """

    session_id: str
    turn_id: str
    response_text: str
    tool_calls: list[ToolCall]
    trust_input_result: TrustCheckResult
    trust_output_result: TrustCheckResult
    model_used: str
    intent: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp_ms: int
    trace_id: Optional[str] = None
    turn_status: str = "completed"  # "completed" | "interrupted" | "abandoned" — added for #72 TurnAssembler observability


# ---------------------------------------------------------------------------
# Streaming events (SSE)
# ---------------------------------------------------------------------------


@dataclass
class SignalEvent:
    """Pipeline stage notification yielded by stream_turn().

    Emitted before and after each pipeline stage to give callers
    mid-turn visibility. No trust check applied to signal events.
    """

    type: str = "signal"
    stage: str = ""     # memory_read | trust_input | nlu | routing | ke_retrieval | tool_start | tool_end | trust_output | memory_write
    status: str = ""    # "start" | "complete" | "skipped"
    detail: str = ""    # optional human-readable info

    def to_sse(self) -> str:
        """Serialise to SSE data line."""
        return f"data: {json.dumps(asdict(self))}\n\n"


@dataclass
class SentenceEvent:
    """One trust-checked sentence from the LLM response.

    Yielded by stream_turn() after each sentence passes the
    per-sentence trust check.
    """

    type: str = "sentence"
    text: str = ""
    sentence_index: int = 0

    def to_sse(self) -> str:
        """Serialise to SSE data line."""
        return f"data: {json.dumps(asdict(self))}\n\n"


@dataclass
class DoneEvent:
    """Terminal event — always the last event in a stream_turn() sequence.

    Carries aggregated metadata for the completed turn.
    """

    type: str = "done"
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0
    turn_id: str = ""
    turn_status: str = "completed"  # "completed" | "interrupted" | "abandoned"
    session_ended: bool = False

    def to_sse(self) -> str:
        """Serialise to SSE data line."""
        return f"data: {json.dumps(asdict(self))}\n\n"


StreamEvent = Union[SignalEvent, SentenceEvent, DoneEvent]
