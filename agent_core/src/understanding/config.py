"""
agent_core/src/understanding/config.py

DialogueActConfig: the dialogue_act NLU config parsed once at startup
(configuration-discipline rule). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.workflow_loader import RoutingCondition


@dataclass(frozen=True)
class SlotSpec:
    """One configured slot (spec §7.1)."""

    name: str
    type: str = "string"
    values: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    normalise: str | None = None
    accept_when_pending: tuple[str, ...] = ()
    description: str = ""
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActIntentRule:
    """One row of the act → intent table (spec §6.4)."""

    intent: str
    acts: tuple[str, ...] = ()
    pending: str | None = None
    relation: str | None = None
    topic: str | None = None
    gated: bool = False


@dataclass(frozen=True)
class GateItem:
    """Termination-gate entry: a pending id, or a routing condition."""

    pending: str | None = None
    condition: RoutingCondition | None = None


@dataclass(frozen=True)
class DialogueActConfig:
    """Parsed ``preprocessing.nlu_processor`` for dialogue_act mode.

    ``user_states`` / ``user_state_default`` / ``user_state_threshold`` come
    from ``conversation.user_state_model`` (empty, ``""`` and 0.4 when the
    model is disabled) and ``nlu_processor.user_state_confidence_threshold``.
    """

    slots: dict[str, SlotSpec]
    known_fields: tuple[str, ...]
    topics: tuple[str, ...]
    signals: tuple[str, ...]
    examples: tuple[dict, ...]
    act_intents: tuple[ActIntentRule, ...]
    gate: tuple[GateItem, ...]
    off_track_threshold: int
    off_track_intent: str
    history_turns: int
    timeout_ms: int
    retry_attempts: int
    entity_map: dict[str, str]
    entity_scope: str
    signal_types: dict[str, str]
    log_raw_response: bool = False
    user_states: tuple[dict, ...] = ()
    user_state_default: str = ""
    user_state_threshold: float = 0.4

    @classmethod
    def from_config(cls, config: dict | None, *, require_mode: bool = True) -> "DialogueActConfig | None":
        """Parse the merged config; None unless ``mode == "dialogue_act"``.

        Args:
            config: Full merged agent_core config (already schema-validated).
            require_mode: When False, skip the mode check and always parse
                (the orchestrator's single understanding path). Transitional;
                removed with the ``mode`` key.

        Returns:
            The parsed config, or None in intent mode when ``require_mode``.
        """
        nlu: dict[str, Any] = ((config or {}).get("preprocessing") or {}).get("nlu_processor") or {}
        if require_mode and nlu.get("mode") != "dialogue_act":
            return None
        slots = {
            name: SlotSpec(
                name=name, type=s.get("type", "string"), values=tuple(s.get("values") or ()),
                min=s.get("min"), max=s.get("max"), normalise=s.get("normalise"),
                accept_when_pending=tuple(s.get("accept_when_pending") or ()),
                description=s.get("description", ""), examples=tuple(s.get("examples") or ()),
            )
            for name, s in (nlu.get("slots") or {}).items()
        }
        rows = tuple(
            ActIntentRule(intent=r["intent"], acts=tuple(r.get("acts") or ()), pending=r.get("pending"),
                          relation=r.get("relation"), topic=r.get("topic"), gated=bool(r.get("gated", False)))
            for r in (nlu.get("act_intents") or [])
        )
        gate = tuple(
            GateItem(pending=g["pending"]) if g.get("pending") else GateItem(
                condition=RoutingCondition(field=g["field"], operator=str(getattr(g["operator"], "value", g["operator"])),
                                           value=g.get("value")))
            for g in ((nlu.get("termination_gate") or {}).get("any_of") or [])
        )
        off = nlu.get("off_track") or {}
        usm = ((config or {}).get("conversation") or {}).get("user_state_model") or {}
        usm_on = bool(usm.get("enabled"))
        return cls(
            slots=slots,
            known_fields=tuple(nlu.get("known_fields") or ()),
            topics=tuple(nlu.get("topics") or ()),
            signals=tuple(nlu.get("signals") or ()),
            examples=tuple(nlu.get("examples") or ()),
            act_intents=rows,
            gate=gate,
            off_track_threshold=int(off.get("threshold", 3)),
            off_track_intent=str(off.get("intent", "off_track")),
            history_turns=int(nlu.get("history_turns", 2)),
            timeout_ms=int(nlu.get("timeout_ms", 2500)),
            retry_attempts=int(nlu.get("retry_attempts", 2)),
            entity_map=dict((config or {}).get("entity_to_profile_field") or {}),
            entity_scope=str(((config or {}).get("entity_persistence") or {}).get("scope", "persistent")),
            signal_types=dict(nlu.get("signal_intents") or {}),
            log_raw_response=bool(nlu.get("log_raw_response", False)),
            user_states=tuple(dict(s) for s in usm.get("states") or []) if usm_on else (),
            user_state_default=str(usm.get("default_state", "")) if usm_on else "",
            user_state_threshold=float(nlu.get("user_state_confidence_threshold", 0.4)) if usm_on else 0.4,
        )

    def state_key(self, slot: str) -> str:
        """State key a slot is written to (via ``entity_to_profile_field``)."""
        return self.entity_map.get(slot, slot)

    def known_state_keys(self) -> tuple[tuple[str, str], ...]:
        """(label, state key) for each known field; non-slot names are raw state keys."""
        return tuple((f, self.state_key(f) if f in self.slots else f) for f in self.known_fields)
