# agent_core/eval/nlu/adapters.py
"""Run one EvalCase through the TurnUnderstander and return a Prediction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval.nlu.cases import EvalCase
from eval.nlu.offline import StaticToolCache
from src.understanding.understander import TurnContext


@dataclass(frozen=True)
class Prediction:
    """What the understander concluded for one case (one repeat)."""

    intent: str
    terminate: bool
    slots: dict
    option_id: str | None
    acts: tuple[str, ...]
    relation: str | None
    topic: str | None
    pending: str | None
    latency_ms: int
    fallback: str | None


def predict_dialogue_act(case: EvalCase, understander: Any) -> Prediction:
    """Run a case through TurnUnderstander.

    Args:
        case: The case.
        understander: A TurnUnderstander (real provider in the CLI).

    Returns:
        Prediction (terminate = derived termination_intent; gate already applied).
    """
    cache = StaticToolCache({case.offered_tool: case.offered}) if case.offered_tool else None
    u = understander.understand(TurnContext(
        subagent_id=case.step, state=dict(case.state), session=dict(case.session or case.state),
        segments=list(case.caller_now), recent=list(case.recent), tool_cache=cache))
    d = u.dialogue
    return Prediction(intent=u.nlu_result.intent, terminate=u.nlu_result.intent == "termination_intent",
                      slots=dict(u.nlu_result.entities), option_id=u.resolved.id if u.resolved else None,
                      acts=tuple(d.acts) if d else (), relation=d.relation if d else None,
                      topic=d.topic if d else None, pending=u.pending_id, latency_ms=u.latency_ms,
                      fallback=u.fallback_reason)
