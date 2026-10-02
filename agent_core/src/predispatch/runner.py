"""
agent_core/src/predispatch/runner.py

Runs the selected pre-dispatch through the shared guard and the path's own
execute callable, under a budget (Spec E §5.2-5.5). Decides inject /
remove-tool by outcome and tool kind. Never raises. Belongs to the Agent Core
DPG block.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from src.models import ToolCall, ToolResult
from src.predispatch.rules import Selection

logger = logging.getLogger(__name__)
PREDISPATCH_ID = "predispatch-1"


@dataclass(frozen=True)
class PredispatchResult:
    """What the turn should do with the pre-dispatch.

    Attributes:
        outcome: Metric outcome, or None when no rule applied.
        tool: Tool name, or None.
        tool_call: The ToolCall made (or attempted), or None.
        tool_result: Result to inject (when ``inject``), or None.
        inject: Add the synthetic tool exchange to the messages.
        remove_tool: Drop ``tool`` from this turn's main-LLM tool list.
        ms: Wall time spent.
    """

    outcome: str | None
    tool: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    inject: bool = False
    remove_tool: bool = False
    ms: int = 0


def _failure(tc: ToolCall, reason: str) -> ToolResult:
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=False,
                      result_text=f"Error: {reason}", error=reason)


def _decide(sel: Selection, tc: ToolCall, kind: str, result: ToolResult | None, start: float) -> PredispatchResult:
    ms = int((time.time() - start) * 1000)
    if kind == "refuse":
        return PredispatchResult("refused_guard", sel.tool, tc, None, False, False, ms)
    if kind == "hit":
        return PredispatchResult("cache_hit", sel.tool, tc, result, True, True, ms)
    if result is not None and result.success:
        return PredispatchResult("fired", sel.tool, tc, result, True, True, ms)
    outcome = "timeout" if kind == "timeout" else "failed"
    if sel.is_write:
        return PredispatchResult(outcome, sel.tool, tc, result or _failure(tc, outcome), True, True, ms)
    return PredispatchResult(outcome, sel.tool, tc, None, False, False, ms)


def _call(sel: Selection) -> ToolCall:
    return ToolCall(tool_name=sel.tool, tool_use_id=PREDISPATCH_ID, input_params=dict(sel.args))


async def run_async(sel: Selection, *, guard: Callable[[ToolCall], Awaitable], execute: Callable[[ToolCall], Awaitable[ToolResult]] | None,
                    timeout_s: float) -> PredispatchResult:
    """Stream path: guard, then execute under ``timeout_s``.

    Args:
        sel: Task 2 selection.
        guard: Async callable returning a GuardVerdict.
        execute: Async live execute (gateway + shape + map + after_call + persist); unused unless the guard says go.
        timeout_s: Pre-dispatch budget.

    Returns:
        PredispatchResult (never raises).
    """
    if sel.tool is None:
        return PredispatchResult(sel.outcome)
    start = time.time()
    tc = _call(sel)
    try:
        verdict = await guard(tc)
        if verdict.kind != "go":
            return _decide(sel, tc, verdict.kind, verdict.result, start)
        try:
            result = await asyncio.wait_for(execute(tc), timeout=timeout_s)
        except asyncio.TimeoutError:
            return _decide(sel, tc, "timeout", None, start)
        return _decide(sel, tc, "go", result, start)
    except Exception as e:  # noqa: BLE001 — never raise into the turn
        logger.warning("predispatch.error", extra={"operation": "predispatch.run", "status": "failure",
                                                   "tool": sel.tool, "error": type(e).__name__})
        return PredispatchResult("error", sel.tool, tc, None, False, False, int((time.time() - start) * 1000))


def run_sync(sel: Selection, *, guard: Callable[[ToolCall], object], execute: Callable[[ToolCall], ToolResult] | None) -> PredispatchResult:
    """Sync path: same decisions; budget is the gateway's own per-tool timeout (plan ruling 3)."""
    if sel.tool is None:
        return PredispatchResult(sel.outcome)
    start = time.time()
    tc = _call(sel)
    try:
        verdict = guard(tc)
        if verdict.kind != "go":
            return _decide(sel, tc, verdict.kind, verdict.result, start)
        return _decide(sel, tc, "go", execute(tc), start)
    except Exception as e:  # noqa: BLE001
        logger.warning("predispatch.error", extra={"operation": "predispatch.run", "status": "failure",
                                                   "tool": sel.tool, "error": type(e).__name__})
        return PredispatchResult("error", sel.tool, tc, None, False, False, int((time.time() - start) * 1000))
