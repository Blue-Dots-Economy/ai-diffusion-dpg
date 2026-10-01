"""
agent_core/models.py

Shared dataclasses used as the data contract between all components of Agent Core
and the interfaces it calls. Every other file in agent_core/ imports from here.
No business logic. No imports from within agent_core/.
"""

from __future__ import annotations

import asyncio
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
    caller_agent_id: Optional[str] = None  # unique identifier of calling agent (GH-338)
    fresh: bool = False             # True when caller wants a clean "New chat" — disables session adoption
    locale: Optional[str] = None
    metadata: Optional[dict] = None


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
    channel: Optional[str] = None
    timestamp_ms: int = 0
    caller_agent_id: Optional[str] = None  # unique identifier of calling agent (GH-338)
    locale: Optional[str] = None
    metadata: Optional[dict] = None
    fresh: bool = False  # request adapter: caller wants a clean session (no adoption)


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

        tool_results: Unexpired tool-result entries from Memory Layer (spec §6).
    """

    session: dict
    profile: dict
    journey: dict | None = None
    tool_results: list[dict] = field(default_factory=list)

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
    Output of the dialogue-act understanding step run in Agent Core.
    Produced before the Knowledge Engine call and passed as parameters to KE's retrieve().
    """

    intent: str                              # classified intent label from config intents list
    entities: dict[str, Any]                 # extracted entity key→value pairs
    confidence: float                        # 0.0–1.0; below threshold triggers early exit
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
    # Values the connector's `session_mapping` lifted out of the response, to
    # be written to session state. Routing reads session ∪ profile, so without
    # this a workflow cannot gate on anything a tool returned — the gap behind
    # consent_response, profile_setup_done and the participant fetch alike.
    session_values: dict[str, Any] = field(default_factory=dict)
    # True when the Action Gateway applied the connector's projection to the result.
    projected: bool = False


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
    error_type: Optional[str]         = None
    error_message: Optional[str]      = None


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
    turn_id: str = ""
    # tool_start only: names of the tools about to run, in call order. Lets a
    # channel tell the caller what is happening ("looking up jobs") during the
    # tool round trip. Empty on every other stage.
    tools: list[str] = field(default_factory=list)

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
    turn_id: str = ""

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
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    interrupted_at_stage: Optional[str] = None  # last SignalEvent stage of an interrupted turn

    def to_sse(self) -> str:
        """Serialise to SSE data line."""
        return f"data: {json.dumps(asdict(self))}\n\n"


@dataclass
class TurnRecord:
    """Mutable per-turn ledger shared by the TurnAssembler and ``stream_turn``.

    The TurnAssembler creates one per Turn and reads it after an interruption.
    ``stream_turn`` fills it as the turn runs, so what an interrupted turn did is
    known without waiting for its end-of-turn memory write.

    Attributes:
        captured_exchanges: Tool rounds completed this turn (#193 shape).
        prior_exchanges: ``recent_tool_exchanges`` as read at turn start; the
            interrupted-turn persist falls back to it only if its re-read fails.
        max_items: The ``recent_tool_exchanges`` cap in force.
        segments: User utterances this turn answers, after folding.
        fold_ran: True once the carry-over fold has run for this turn.
        last_stage: Stage of the last SignalEvent emitted.
        write_carryover: False when policy says an interruption must not carry
            the utterances forward (``on_new_input: replace``).
        persist_task: Background task persisting an interrupted turn, if any.
        spoken: Sentences emitted to the caller so far (for interrupted-turn
            persistence).
    """

    captured_exchanges: list[dict] = field(default_factory=list)
    prior_exchanges: list[dict] = field(default_factory=list)
    max_items: int = 0
    segments: list[str] = field(default_factory=list)
    fold_ran: bool = False
    last_stage: str = ""
    write_carryover: bool = True
    persist_task: Optional["asyncio.Task"] = None
    spoken: list[str] = field(default_factory=list)


StreamEvent = Union[SignalEvent, SentenceEvent, DoneEvent]
