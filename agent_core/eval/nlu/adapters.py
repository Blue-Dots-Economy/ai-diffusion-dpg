# agent_core/eval/nlu/adapters.py
"""Run one EvalCase through either NLU mode and return a comparable Prediction."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from eval.nlu.cases import EvalCase
from eval.nlu.offline import StaticToolCache


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
    from src.understanding.understander import TurnContext  # lazy: absent in pre-dialogue-act targets

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


def predict_intent(case: EvalCase, nlu: Any, workflow: Any, entity_map: dict, routed: set[str],
                   resolves_to: str | None) -> Prediction:
    """Run a case through today's NLUProcessor, the way the stream path calls it.

    Intents no routing rule uses (e.g. profile_answer) count as ``any_input``,
    so both modes are scored on the intents routing actually sees.

    Args:
        case: The case.
        nlu: NLUProcessor.
        workflow: Loaded intent-mode workflow (for per-subagent intent lists).
        entity_map: entity_to_profile_field.
        routed: Intents used by routing rules.
        resolves_to: State key for the selected option (compared to ``expect.option_id``).

    Returns:
        Prediction (terminate mirrors the short-circuit: termination_intent at ≥0.7).
    """
    start = time.time()
    result = nlu.process(
        normalised_input=" ".join(case.caller_now),
        current_question=(case.recent[-1].get("bot", "") if case.recent else ""),
        current_subagent_id=case.step,
        allowed_intents=workflow.nlu_intent_set.get(case.step, []),
        existing_profile_keys=[k for k, v in case.state.items() if v not in (None, "", 0)],
        previous_user_state=None,
    )
    slots = {entity_map.get(k, k): v for k, v in (result.entities or {}).items()}
    intent = result.intent if result.intent in routed else "any_input"
    return Prediction(intent=intent, terminate=result.intent == "termination_intent" and result.confidence >= 0.7,
                      slots=slots, option_id=str(slots.get(resolves_to)) if resolves_to and slots.get(resolves_to) else None,
                      acts=(), relation=None, topic=None, pending=None,
                      latency_ms=int((time.time() - start) * 1000), fallback=None)
