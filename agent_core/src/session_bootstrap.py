"""
agent_core/src/session_bootstrap.py

Session bootstrap (session-bootstrap spec §5): config-declared `tool` steps run
deterministically on the first turn of a session, before routing. Results go
through the same paths as a live call — session_mapping values into session
state, the projected result into the tool-result store (origin "bootstrap").

Pure unit: all I/O is injected, so the sync and stream paths share one logic.
Never raises into the turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.models import ToolCall, ToolResult
from src.tool_results import ToolResultPolicies, TurnToolCache

logger = logging.getLogger(__name__)

LATCH = "bootstrap_done"

_counter = None


def _outcome_counter():
    global _counter
    if _counter is None:
        from opentelemetry import metrics
        _counter = metrics.get_meter("agent_core.session_bootstrap").create_counter(
            "agent_core.session_bootstrap.outcomes_total",
            description="Session bootstrap step outcomes, tagged by tool and outcome.",
        )
    return _counter


def _record(tool: str, outcome: str, latency_ms: int) -> None:
    logger.info("session_bootstrap", extra={
        "operation": "session_bootstrap.step", "status": "success" if outcome == "ok" else outcome,
        "tool": tool, "outcome": outcome, "latency_ms": latency_ms})
    try:
        _outcome_counter().add(1, {"tool": tool, "outcome": outcome})
    except Exception:  # metrics must never break a turn
        pass


@dataclass(frozen=True)
class BootstrapStep:
    """One `tool` step."""

    tool: str
    args: dict
    requires_consent: bool = False


@dataclass(frozen=True)
class BootstrapReport:
    """Outcome per tool and total latency, for logging."""

    outcomes: dict
    latency_ms: int


class SessionBootstrap:
    """Runs the configured steps once per session.

    Args:
        steps: Steps in config order.
        timeout_ms: Budget for the whole bootstrap.
        policies: Tool-result cache policies (Spec A).
    """

    def __init__(self, steps: list[BootstrapStep], timeout_ms: int, policies: ToolResultPolicies) -> None:
        self._steps = steps
        self._timeout_s = timeout_ms / 1000
        self._policies = policies

    @classmethod
    def from_config(cls, config: dict | None, policies: ToolResultPolicies) -> "SessionBootstrap | None":
        """Build from the merged (schema-validated) agent_core config; None when not configured."""
        sb = (config or {}).get("session_bootstrap")
        if not isinstance(sb, dict) or not sb.get("steps"):
            return None
        steps = [BootstrapStep(tool=str(s["tool"]), args=dict(s.get("args") or {}),
                               requires_consent=bool(s.get("requires_consent", False)))
                 for s in sb["steps"]]
        return cls(steps, int(sb.get("timeout_ms", 1500)), policies)

    def needed(self, bundle) -> bool:
        """True when this session has not been bootstrapped yet."""
        return not bool((getattr(bundle, "session", None) or {}).get(LATCH))

    def _call(self, i: int, step: BootstrapStep) -> ToolCall:
        return ToolCall(tool_name=step.tool, tool_use_id=f"bootstrap-{i}", input_params=dict(step.args))

    def _apply(self, bundle, cache: TurnToolCache, call: ToolCall, result: ToolResult,
               writes: list) -> str:
        """Fold one successful result into this turn's state; returns the outcome."""
        if not result.success:
            return "failed"
        for key, value in (result.session_values or {}).items():
            bundle.session[key] = value
            writes.append((key, value))
        entry = cache.after_call(call, result, origin="bootstrap")
        if entry is not None:
            bundle.tool_results.append(entry)
        return "ok"

    def _finish(self, start: float, outcomes: dict) -> BootstrapReport:
        latency = int((time.monotonic() - start) * 1000)
        mark = "✓" if all(o == "ok" for o in outcomes.values()) else "✗"
        logger.info("  [STEP 1b] Session bootstrap  %s  tools=%s  outcomes=%s  latency=%dms",
                    mark, [s.tool for s in self._steps], outcomes, latency)
        logger.info("session_bootstrap.turn", extra={
            "operation": "session_bootstrap.run", "status": "success", "latency_ms": latency})
        return BootstrapReport(outcomes=outcomes, latency_ms=latency)

    def run_sync(self, bundle, *, execute: Callable[[ToolCall], ToolResult],
                 check_consent: Callable[[str], bool], write_session: Callable[[str, Any], None],
                 apply_tool_results: Callable[[dict], None]) -> BootstrapReport:
        """Run steps in order on the sync path. Never raises."""
        start = time.monotonic()
        outcomes: dict = {}
        bundle.session[LATCH] = True
        try:
            write_session(LATCH, True)
        except Exception as e:
            logger.error("session_bootstrap.latch_error", extra={
                "operation": "session_bootstrap.run", "status": "failure", "error": type(e).__name__})
        cache = TurnToolCache(self._policies, [], bundle.session)
        writes: list = []
        deadline = start + self._timeout_s
        for i, step in enumerate(self._steps):
            t0 = time.monotonic()
            try:
                if t0 >= deadline:
                    outcomes[step.tool] = "timeout"
                elif step.requires_consent and not check_consent(step.tool):
                    outcomes[step.tool] = "skipped_consent"
                else:
                    call = self._call(i, step)
                    result = execute(call)
                    if time.monotonic() > deadline:
                        outcomes[step.tool] = "timeout"      # late: discarded, never written
                    else:
                        outcomes[step.tool] = self._apply(bundle, cache, call, result, writes)
            except Exception as e:
                logger.error("session_bootstrap.step_error", extra={
                    "operation": "session_bootstrap.step", "status": "failure",
                    "tool": step.tool, "error": type(e).__name__})
                outcomes[step.tool] = "failed"
            _record(step.tool, outcomes[step.tool], int((time.monotonic() - t0) * 1000))
        self._persist_sync(cache, writes, write_session, apply_tool_results)
        return self._finish(start, outcomes)

    def _persist_sync(self, cache, writes, write_session, apply_tool_results) -> None:
        try:
            for key, value in writes:
                write_session(key, value)
            if cache.has_pending():
                apply_tool_results(cache.drain_batch())
        except Exception as e:
            logger.error("session_bootstrap.persist_error", extra={
                "operation": "session_bootstrap.persist", "status": "failure", "error": type(e).__name__})

    async def run_async(self, bundle, *, execute: Callable[[ToolCall], Awaitable[ToolResult]],
                        check_consent: Callable[[str], Awaitable[bool]],
                        write_session: Callable[[str, Any], Awaitable[None]],
                        apply_tool_results: Callable[[dict], Awaitable[None]]) -> BootstrapReport:
        """Run steps concurrently on the stream path within the budget. Never raises."""
        start = time.monotonic()
        outcomes: dict = {}
        bundle.session[LATCH] = True
        try:
            await write_session(LATCH, True)
        except Exception as e:
            logger.error("session_bootstrap.latch_error", extra={
                "operation": "session_bootstrap.run", "status": "failure", "error": type(e).__name__})
        cache = TurnToolCache(self._policies, [], bundle.session)
        writes: list = []

        async def one(i: int, step: BootstrapStep):
            if step.requires_consent and not await check_consent(step.tool):
                return i, step, None, "skipped_consent"
            call = self._call(i, step)
            return i, step, (call, await execute(call)), None

        tasks = [asyncio.ensure_future(one(i, s)) for i, s in enumerate(self._steps)]
        try:
            done, pending = await asyncio.wait(tasks, timeout=self._timeout_s)
        except Exception as e:
            logger.error("session_bootstrap.wait_error", extra={
                "operation": "session_bootstrap.run", "status": "failure", "error": type(e).__name__})
            done, pending = set(), set(tasks)
        pending_ids = {id(t) for t in pending}
        for t in pending:
            t.cancel()
        if pending:                                       # reap cancelled tasks; no leaked exceptions
            await asyncio.gather(*pending, return_exceptions=True)
        finished: dict = {}
        for t in done:
            try:
                i, step, payload, early = t.result()
                finished[i] = (step, payload, early)
            except Exception as e:
                logger.error("session_bootstrap.step_error", extra={
                    "operation": "session_bootstrap.step", "status": "failure", "error": type(e).__name__})
        for i, step in enumerate(self._steps):           # apply in config order
            if i not in finished:
                outcome = "timeout" if id(tasks[i]) in pending_ids else "failed"
            else:
                _, payload, early = finished[i]
                if early:
                    outcome = early
                else:
                    call, result = payload
                    try:
                        outcome = self._apply(bundle, cache, call, result, writes)
                    except Exception:
                        outcome = "failed"
            outcomes[step.tool] = outcome
            _record(step.tool, outcome, int((time.monotonic() - start) * 1000))
        try:
            for key, value in writes:
                await write_session(key, value)
            if cache.has_pending():
                await apply_tool_results(cache.drain_batch())
        except Exception as e:
            logger.error("session_bootstrap.persist_error", extra={
                "operation": "session_bootstrap.persist", "status": "failure", "error": type(e).__name__})
        return self._finish(start, outcomes)
