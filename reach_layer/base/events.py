"""
reach_layer/base/events.py

Lightweight event dataclasses for SSE events received from Agent Core.

These mirror Agent Core's StreamEvent types but are independently defined
to keep reach_layer decoupled from agent_core's internal models. The base
class parses SSE JSON into these objects so channel implementations can
render them without coupling to Agent Core internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass
class SignalEvent:
    """Pipeline stage signal from Agent Core."""

    stage: str
    status: str
    type: str = "signal"
    turn_id: str = ""


@dataclass
class SentenceEvent:
    """Streamed sentence from Agent Core's LLM response."""

    text: str
    sentence_index: int = 0
    type: str = "sentence"
    turn_id: str = ""


@dataclass
class DoneEvent:
    """Turn completion signal from Agent Core."""

    turn_status: str = "completed"
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0
    turn_id: str = ""
    session_ended: bool = False
    type: str = "done"


@dataclass
class ConsentEvent:
    """Trust Layer consent decision streamed back to Reach Layer.

    Emitted by Agent Core after ``/consent/verify`` succeeds for a tracked
    purpose. Reach Layer adapters use it to gate channel-side behaviour
    (e.g. start the call recorder when purpose=recording is granted).
    """

    purpose: str
    granted: bool
    consent_granted_ts: float = 0.0
    turn_id: str = ""
    type: str = "consent"


# Union type matching Agent Core's StreamEvent
StreamEvent = Union[SignalEvent, SentenceEvent, DoneEvent, ConsentEvent]
