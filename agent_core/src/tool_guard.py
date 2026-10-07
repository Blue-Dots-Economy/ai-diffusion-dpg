"""
agent_core/src/tool_guard.py
One guard + cache decision for every tool-execution site (Spec E §5.1-5.2):
consent -> pending question -> per-turn cap -> grounding -> cache. Live
execution, shaping,
mapping and persistence stay at each site. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from src.manager_agent import over_call_cap, refusal_result, ungrounded_params
from src.models import ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardVerdict:
    """Decision for one tool call.

    Attributes:
        kind: "refuse" (result is the refusal), "hit" (result is the cached
            result), or "go" (execute live).
        result: The ToolResult for refuse/hit; None for go.
    """

    kind: Literal["refuse", "hit", "go"]
    result: ToolResult | None = None


_CONSENT_REASON = "consent_required: the caller has not given consent for this action"


def _cap_reason(tool_name: str) -> str:
    return (
        f"Refused: {tool_name} has already run this turn and its "
        f"effect cannot be undone. One per turn. If the caller meant a "
        f"different one, ask which, and call it on the next turn."
    )


def _unasked_reason(tool_name: str, required: str, pending: str | None) -> str:
    return (
        f"Refused: {tool_name} may only run as the answer to the "
        f"'{required}' question, and that question is not open "
        f"(pending={pending or 'none'}). Ask it, wait for the caller's "
        f"answer, and call this tool on the turn that answers it."
    )


def _ungrounded_reason(bad: set[str]) -> str:
    return (
        f"Refused: {', '.join(sorted(bad))} did not come from "
        f"any tool result in this conversation, so the value was "
        f"invented. Do not guess an identifier. Re-read the most "
        f"recent tool result, copy the exact value for the item the "
        f"user chose, and call this tool again."
    )


def check_tool_call(
    tc: Any,
    *,
    cap: int | None,
    used: int,
    grounded_spec: dict | None,
    messages: list,
    stored_results: dict,
    session_grounded: dict | None,
    consent_ok: bool | None,
    cache_lookup: Callable[[Any], ToolResult | None],
    requires_pending: str | None = None,
    pending_id: str | None = None,
    ungrounded_error: str = "REFUSED",
) -> GuardVerdict:
    """Apply the guards in order and decide refuse / cache-hit / go.

    Args:
        tc: The ToolCall.
        cap: ``max_calls_per_turn`` for this tool, or None.
        used: Calls of this tool already made this turn.
        grounded_spec: ``grounded_params[tool]`` (param -> source tools), or None.
        messages: The turn's messages so far (grounding evidence).
        stored_results: ``tool_cache.stored_results_by_tool()``.
        session_grounded: Param -> session values lifted by ``session_mapping``, or None.
        consent_ok: None when consent is not required; else whether it is granted.
        cache_lookup: ``tool_cache.lookup``.
        requires_pending: ``invocation_rules.requires_pending`` for this tool,
            or None when the tool declares no required question.
        pending_id: The question actually open this turn.
        ungrounded_error: ``error`` code on the grounding refusal (sync keeps
            its historical ``UNGROUNDED_PARAMETER``).

    Returns:
        GuardVerdict.
    """
    if consent_ok is False:
        logger.warning("tool_guard.consent_denied tool=%s", tc.tool_name)
        return GuardVerdict("refuse", ToolResult(
            tool_use_id=tc.tool_use_id, tool_name=tc.tool_name,
            result={}, success=False, error="consent_required",
            result_text=_CONSENT_REASON,
        ))
    if requires_pending and pending_id != requires_pending:
        logger.warning(
            "tool_guard.pending_not_open tool=%s requires=%s pending=%s",
            tc.tool_name, requires_pending, pending_id,
        )
        return GuardVerdict("refuse", refusal_result(
            tc.tool_name, tc.tool_use_id,
            _unasked_reason(tc.tool_name, requires_pending, pending_id)))
    if over_call_cap(cap, used):
        logger.warning("tool_guard.tool_call_cap tool=%s used=%s", tc.tool_name, used)
        return GuardVerdict("refuse", refusal_result(
            tc.tool_name, tc.tool_use_id, _cap_reason(tc.tool_name)))
    if grounded_spec:
        bad = ungrounded_params(
            grounded_spec, tc, messages,
            stored_results=stored_results, session_grounded=session_grounded,
        )
        if bad:
            logger.warning("tool_guard.ungrounded_param tool=%s params=%s",
                           tc.tool_name, sorted(bad))
            res = refusal_result(tc.tool_name, tc.tool_use_id, _ungrounded_reason(bad))
            res.error = ungrounded_error
            return GuardVerdict("refuse", res)
    hit = cache_lookup(tc)
    if hit is not None:
        return GuardVerdict("hit", hit)
    return GuardVerdict("go")


def apply_session_only(tc: Any, spec: dict | None, session: dict | None) -> list[str]:
    """Force ``session_only_params`` to their session value; drop what session lacks.

    The model sees every optional field in the tool schema and will fill
    plausible values for a caller who never gave one. Checking the model's value
    is not enough — the fix is to stop using it. Each listed param is replaced by
    the first non-empty session value among its keys, and removed entirely when
    none has one, so a field that was never collected cannot be sent.

    Args:
        tc: The pending ToolCall; ``input_params`` is mutated in place.
        spec: ``session_only_params`` for this tool (param -> session keys).
        session: The session dict.

    Returns:
        Names of params that were dropped, for logging. Empty when nothing changed.
    """
    if not spec or not isinstance(getattr(tc, "input_params", None), dict):
        return []
    session = session or {}
    dropped: list[str] = []
    for param, keys in spec.items():
        value = _first_session_value(session, keys or [param])
        if value is None:
            if tc.input_params.pop(param, _MISSING) is not _MISSING:
                dropped.append(param)
        else:
            tc.input_params[param] = value
    return dropped


_MISSING = object()


def _first_session_value(session: dict, keys: list) -> Any | None:
    """First non-empty session value among ``keys``, or None when none has one."""
    for key in keys:
        v = session.get(key)
        if v not in (None, "", []):
            return v
    return None
