"""
agent_core/src/understanding/models.py

Result types of the dialogue-act understanding pipeline (NLU dialogue-acts
spec §4, §5.3, §8). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from src.models import NLUResult

ACTS: tuple[str, ...] = (
    "affirm", "deny", "acknowledge", "provide_info", "correct", "select",
    "ask", "request_change", "repeat", "hold", "close", "other",
)
RELATIONS: tuple[str, ...] = (
    "answers_pending", "answers_other", "new_topic", "unrelated", "unclear",
)
MAX_ACTS = 3


@dataclass(frozen=True)
class DialogueActResult:
    """Validated NLU output in dialogue_act mode.

    Attributes:
        acts: 1–3 acts, in the order the caller performed them.
        relation: How the turn relates to the pending question.
        topic: Topic for ask / request_change, else None.
        slots: Every configured slot name → raw value or None.
        option: Offered-option number the caller referred to, or None.
        spoken_reference: The caller's words for that reference, or None.
        signals: Configured signal names the turn carries.
        extras: Ad-hoc (key, value) details; never read by routing.
        user_state_id: Classified caller state id, or None.
        user_state_confidence: Its confidence, or None.
    """

    acts: tuple[str, ...]
    relation: str
    topic: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    option: int | None = None
    spoken_reference: str | None = None
    signals: tuple[str, ...] = ()
    extras: tuple[tuple[str, str], ...] = ()
    user_state_id: str | None = None
    user_state_confidence: float | None = None

    @classmethod
    def fallback(cls) -> "DialogueActResult":
        """Result used when the NLU call fails (spec §9.2)."""
        return cls(acts=("other",), relation="unclear")

    @classmethod
    def from_parsed(cls, parsed: Any, *, slot_names: Iterable[str], topics: Iterable[str],
                    signals: Iterable[str], user_state_ids: Iterable[str] = ()) -> "DialogueActResult":
        """Validate a provider's parsed JSON into a result.

        Tolerant where a wrong value is harmless (unknown topic → None,
        unknown signal dropped, >3 acts truncated, unknown slot keys ignored);
        strict where routing depends on it (acts and relation enums).

        Args:
            parsed: ``ChatResponse.parsed_output``.
            slot_names: Configured slot names.
            topics: Configured topics.
            signals: Configured signal names.
            user_state_ids: Configured user-state ids; an unknown id or a
                non-numeric confidence leaves the user-state fields None.

        Returns:
            The validated result.

        Raises:
            ValueError: If the object, acts, relation or slots are malformed.
        """
        if not isinstance(parsed, dict):
            raise ValueError("parsed output must be an object")
        acts = parsed.get("acts")
        if not isinstance(acts, list) or not acts or any(a not in ACTS for a in acts):
            raise ValueError(f"invalid acts: {acts!r}")
        relation = parsed.get("relation")
        if relation not in RELATIONS:
            raise ValueError(f"invalid relation: {relation!r}")
        raw_slots = parsed.get("slots", {})
        if raw_slots is None:
            raw_slots = {}
        if not isinstance(raw_slots, dict):
            raise ValueError("slots must be an object")
        topic = parsed.get("topic")
        topic = topic if isinstance(topic, str) and topic in set(topics) else None
        ref = parsed.get("reference") if isinstance(parsed.get("reference"), dict) else {}
        option = ref.get("option")
        option = option if isinstance(option, int) and not isinstance(option, bool) else None
        spoken = ref.get("spoken") if isinstance(ref.get("spoken"), str) else None
        allowed_signals = set(signals)
        sig = tuple(s for s in (parsed.get("signals") or []) if s in allowed_signals)
        extras = tuple((str(e["key"]), str(e["value"])) for e in (parsed.get("extras") or [])
                       if isinstance(e, dict) and "key" in e and "value" in e)
        us = parsed.get("user_state")
        us_id: str | None = None
        us_conf: float | None = None
        if isinstance(us, dict) and us.get("id") in set(user_state_ids):
            c = us.get("confidence")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                us_id, us_conf = us["id"], float(c)
        return cls(
            acts=tuple(acts[:MAX_ACTS]), relation=relation, topic=topic,
            slots={n: raw_slots.get(n) for n in slot_names},
            option=option, spoken_reference=spoken, signals=sig, extras=extras,
            user_state_id=us_id, user_state_confidence=us_conf,
        )


@dataclass(frozen=True)
class ResolvedReference:
    """An offered option the caller chose, mapped to its id (spec §6.3)."""

    option: int
    id: str
    label: str
    id_field: str


@dataclass(frozen=True)
class UnresolvedReference:
    """An option reference that could not be mapped. reason: no_options | out_of_range | missing_id."""

    option: int
    offered: int
    reason: str


@dataclass(frozen=True)
class SlotRejection:
    """A slot value that was dropped, and why (``normalise:<rule>`` or ``not_pending``)."""

    slot: str
    value: Any
    reason: str


@dataclass(frozen=True)
class SlotUpdate:
    """A state key whose existing non-empty value changed this turn."""

    key: str
    old: Any
    new: Any


@dataclass(frozen=True)
class StateWrite:
    """One Memory Layer write the orchestrator must apply (scope, key, value)."""

    scope: str
    key: str
    value: Any


@dataclass
class TurnUnderstanding:
    """Everything the turn learned from the caller (spec §8).

    ``nlu_result`` is what routing consumes, in both modes. ``dialogue`` is
    None in intent mode. ``writes`` and ``signals`` are applied by the
    orchestrator.
    """

    nlu_result: NLUResult
    dialogue: DialogueActResult | None = None
    pending_id: str | None = None
    resolved: ResolvedReference | None = None
    unresolved: UnresolvedReference | None = None
    accepted_slots: dict[str, Any] = field(default_factory=dict)
    updates: list[SlotUpdate] = field(default_factory=list)
    rejected_slots: list[SlotRejection] = field(default_factory=list)
    writes: list[StateWrite] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    latency_ms: int = 0
    gate_blocked: bool = False
    off_track_tripped: bool = False
