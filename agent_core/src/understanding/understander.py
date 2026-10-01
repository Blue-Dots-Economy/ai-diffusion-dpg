# agent_core/src/understanding/understander.py
"""
agent_core/src/understanding/understander.py

TurnUnderstander: the dialogue_act replacement for NLUProcessor.process()
(NLU dialogue-acts spec §5–§6, §9, §10). Pure apart from the LLM call; the
orchestrator applies ``TurnUnderstanding.writes`` and ``signals``.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models import NLUResult
from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import DialogueActNLU, DialogueActNLUBase
from src.understanding.frame import FrameBuilder, FrameBuilderBase, offered_rows
from src.understanding.models import DialogueActResult, TurnUnderstanding
from src.understanding.pending import PendingResolver, PendingResolverBase
from src.understanding.postprocess import (accept_slots, derive_intent, gate_passes, next_off_track,
                                           normalise_slots, resolve_reference)
from src.understanding.slot_writer import plan_writes, stored_off_track_count

logger = logging.getLogger(__name__)
_counters: dict[str, Any] = {}


def _counter(name: str, description: str):
    """Lazily create an OTel counter; a no-op meter when OTel is not configured."""
    if name not in _counters:
        from opentelemetry import metrics
        _counters[name] = metrics.get_meter("agent_core.understanding").create_counter(name, description=description)
    return _counters[name]


@dataclass(frozen=True)
class TurnContext:
    """Inputs for one understanding pass.

    Attributes:
        subagent_id: Subagent the caller is in (before routing).
        state: Merged routing state (``AgentCore._routing_state``).
        session: Raw session state (provenance, extras, counters).
        segments: This turn's utterances; earlier ones were interrupted.
        recent: ``recent_turns`` entries, oldest first.
        tool_cache: This turn's ``TurnToolCache``, or None.
    """

    subagent_id: str
    state: dict
    session: dict
    segments: list[str]
    recent: list[dict] = field(default_factory=list)
    tool_cache: Any | None = None


class TurnUnderstanderBase(ABC):
    """Interface for turn understanding."""

    @abstractmethod
    def understand(self, ctx: TurnContext) -> TurnUnderstanding:
        """Understand one caller turn. Never raises."""


class TurnUnderstander(TurnUnderstanderBase):
    """Composes pending resolution, frame, NLU call and post-processing.

    Args:
        cfg: Parsed dialogue-act config.
        workflow: Loaded workflow (pending declarations).
        nlu: The NLU call.
        frame: Frame renderer (default FrameBuilder).
        resolver: Pending resolver (default PendingResolver(workflow)).
    """

    def __init__(self, cfg: DialogueActConfig, workflow: Any, nlu: DialogueActNLUBase,
                 frame: FrameBuilderBase | None = None, resolver: PendingResolverBase | None = None) -> None:
        self._cfg = cfg
        self._nlu = nlu
        self._frame = frame or FrameBuilder()
        self._resolver = resolver or PendingResolver(workflow)

    @property
    def config(self) -> DialogueActConfig:
        """The parsed dialogue-act config."""
        return self._cfg

    @classmethod
    def from_config(cls, config: dict, workflow: Any, chat_provider: Any) -> "TurnUnderstander | None":
        """Build from the merged config; None in intent mode.

        Args:
            config: Merged agent_core config.
            workflow: Loaded workflow.
            chat_provider: Dedicated NLU provider (orchestrator builds it, §9.1).

        Returns:
            A TurnUnderstander, or None unless ``mode == "dialogue_act"``.
        """
        cfg = DialogueActConfig.from_config(config)
        if cfg is None:
            return None
        return cls(cfg, workflow, DialogueActNLU(cfg, chat_provider))

    def understand(self, ctx: TurnContext) -> TurnUnderstanding:
        """Understand one caller turn (spec §6). Never raises.

        Args:
            ctx: Turn inputs.

        Returns:
            The understanding; on any failure, a fallback with intent ``any_input``.
        """
        start = time.time()
        pending = None
        message = ""
        try:
            pending = self._resolver.resolve(ctx.subagent_id, ctx.state)
            rows: list[dict] = []
            if pending is not None and pending.options_from is not None and ctx.tool_cache is not None:
                rows = offered_rows(ctx.tool_cache.latest_entry(pending.options_from.tool))
            known = [(label, ctx.state.get(key)) for label, key in self._cfg.known_state_keys()]
            recent = ctx.recent[-self._cfg.history_turns:] if self._cfg.history_turns > 0 else []
            message = self._frame.build(step=ctx.subagent_id, pending=pending, rows=rows, known=known,
                                        recent=recent, segments=ctx.segments)
            dialogue, reason, _ = self._nlu.classify(message)
            if reason:
                return self._finish(self._fallback(dialogue, pending, reason), ctx, message, start)
            u = self._post(dialogue, pending, rows, ctx)
            return self._finish(u, ctx, message, start)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.error("nlu.understanding_error", extra={
                "operation": "turn_understander.understand", "status": "failure",
                "error": type(e).__name__, "latency_ms": int((time.time() - start) * 1000)})
            return self._finish(self._fallback(DialogueActResult.fallback(), pending, "exception"),
                                ctx, message, start)

    @staticmethod
    def _fallback(dialogue: DialogueActResult, pending: Any, reason: str) -> TurnUnderstanding:
        return TurnUnderstanding(
            nlu_result=NLUResult(intent="any_input", entities={}, sentiment="neutral", confidence=0.0),
            dialogue=dialogue, pending_id=getattr(pending, "id", None), fallback_reason=reason)

    def _post(self, dialogue: DialogueActResult, pending: Any, rows: list[dict],
              ctx: TurnContext) -> TurnUnderstanding:
        cfg = self._cfg
        pid = getattr(pending, "id", None)
        slots, rejected = normalise_slots(dialogue.slots, cfg)
        accepted, not_pending = accept_slots(slots, cfg, pid, dialogue.acts)
        resolved, unresolved = resolve_reference(dialogue.option, pending, rows)
        gate_ok = gate_passes(cfg, pid, ctx.state)
        intent, rule = derive_intent(dialogue, pid, cfg, gate_ok=gate_ok, resolved=resolved is not None)
        count, tripped = next_off_track(stored_off_track_count(ctx.session), dialogue.relation,
                                        cfg, is_fallback=False)
        if tripped and not (rule is not None and rule.gated):
            intent = cfg.off_track_intent
            count = 0   # recovery owns the next turn; start counting afresh
        writes, updates = plan_writes(accepted=accepted, cfg=cfg, state=ctx.state, session=ctx.session,
                                      pending=pending, resolved=resolved, extras=dialogue.extras,
                                      off_track_count=count)
        entities = {cfg.state_key(k): v for k, v in accepted.items()}
        if resolved is not None and getattr(pending, "resolves_to", None):
            entities[pending.resolves_to] = resolved.id
        gate_blocked = (not gate_ok) and any(
            r.gated and set(r.acts) <= set(dialogue.acts) for r in cfg.act_intents)
        u = TurnUnderstanding(
            nlu_result=NLUResult(intent=intent, entities=entities, sentiment="neutral", confidence=1.0),
            dialogue=dialogue, pending_id=pid, resolved=resolved, unresolved=unresolved,
            accepted_slots=accepted, updates=updates, rejected_slots=rejected + not_pending,
            writes=writes, signals=list(dialogue.signals), gate_blocked=gate_blocked,
            off_track_tripped=tripped and intent == cfg.off_track_intent)
        return u

    def _finish(self, u: TurnUnderstanding, ctx: TurnContext, message: str, start: float) -> TurnUnderstanding:
        """Stamp latency and emit telemetry; telemetry failures never change ``u`` or raise."""
        u.latency_ms = int((time.time() - start) * 1000)
        try:
            self._emit(u, ctx, message)
        except Exception as e:  # noqa: BLE001 — telemetry must never break a turn
            logger.warning("nlu.telemetry_error", extra={"operation": "turn_understander.telemetry",
                                                          "status": "failure", "error": type(e).__name__})
        return u

    def _emit(self, u: TurnUnderstanding, ctx: TurnContext, message: str) -> None:
        """Log the PII-free summary, bump OTel counters and (opt-in) capture the eval case."""
        d = u.dialogue
        extra = {
            "operation": "turn_understander.understand",
            "status": "fallback" if u.fallback_reason else "success",
            "latency_ms": u.latency_ms,
            "subagent_id": ctx.subagent_id,
            "pending_id": u.pending_id,
            "acts": list(d.acts) if d else [],
            "relation": d.relation if d else None,
            "topic": d.topic if d else None,
            "derived_intent": u.nlu_result.intent,
            "resolved": u.resolved is not None,
            "slot_keys_written": sorted({w.key for w in u.writes}),
            "slots_rejected": [f"{r.slot}:{r.reason}" for r in u.rejected_slots],
            "fallback_reason": u.fallback_reason,
        }
        logger.info("nlu.understanding", extra=extra)
        _counter("agent_core.nlu.turns_total", "Dialogue-act NLU turns by pending, first act, relation, intent").add(
            1, {"pending": u.pending_id or "none", "act": (d.acts[0] if d else "none"),
                "relation": (d.relation if d else "none"), "intent": u.nlu_result.intent,
                "fallback": u.fallback_reason or "none"})
        events = _counter("agent_core.nlu.events_total", "Dialogue-act NLU events")
        if u.gate_blocked:
            events.add(1, {"event": "termination_gate_blocked"})
        if u.off_track_tripped:
            events.add(1, {"event": "off_track_route"})
        if u.unresolved is not None:
            events.add(1, {"event": f"resolver_miss:{u.unresolved.reason}"})
        for r in u.rejected_slots:
            events.add(1, {"event": f"slot_rejected:{r.reason}"})
        if self._cfg.log_raw_response:
            logger.info("nlu.eval_case", extra={
                "operation": "turn_understander.eval_capture", "status": "success",
                "case": json.dumps({"step": ctx.subagent_id, "pending": u.pending_id, "frame": message,
                                    "output": d.__dict__ if d else None}, ensure_ascii=False, default=str)})
