"""
agent_core/src/tool_results.py

Per-turn view of stored tool results (tool-result persistence spec §7).

Agent Core builds one TurnToolCache per turn from ContextBundle.tool_results.
It renders <known_facts>, filters the replay, serves hits before execution,
records live projected results and write-tool invalidations, and hands the
pending changes to Memory Layer in one batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)

FORCE_REFRESH = "force_refresh"

_KNOWN_FACTS_HEADER = (
    "Results of earlier tool calls. Use them instead of calling the tool again.\n"
    "Call the tool only if what you need is missing here, or the caller says it "
    "changed — then pass force_refresh=true."
)
_FORCE_REFRESH_SCHEMA = {
    "type": "boolean",
    "description": "Set true only to bypass the stored result in <known_facts> and fetch live data.",
}

_counter = None


def _outcome_counter():
    """Lazily create the OTel counter; a no-op meter when OTel is not configured."""
    global _counter
    if _counter is None:
        from opentelemetry import metrics
        _counter = metrics.get_meter("agent_core.tool_results").create_counter(
            "agent_core.tool_result.outcomes_total",
            description="Tool-result cache outcomes, tagged by tool and outcome.",
        )
    return _counter


def _log(outcome: str, tool: str, scope: str = "", age_s: Optional[int] = None,
         ttl_s: Optional[int] = None) -> None:
    logger.info("tool_result", extra={
        "operation": "tool_results.turn_cache", "status": "success", "tool": tool,
        "scope": scope, "outcome": outcome, "age_s": age_s, "ttl_s": ttl_s})
    try:
        _outcome_counter().add(1, {"tool": tool, "outcome": outcome})
    except Exception:  # metrics must never break a turn
        pass


@dataclass(frozen=True)
class CachePolicy:
    """Cache rule for one read connector."""

    tool: str
    scope: str
    ttl_seconds: int
    # Top-level keys to store. Applies only to dict payloads; a list or scalar
    # result is stored whole.
    keep: tuple[str, ...] = ()
    vary_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResultPolicies:
    """All cache and invalidation rules, read once from agent_core config."""

    cache: dict[str, CachePolicy] = field(default_factory=dict)
    invalidates: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict | None) -> "ToolResultPolicies":
        """Build from the merged agent_core config dict (already schema-validated)."""
        cache: dict[str, CachePolicy] = {}
        inval: dict[str, tuple[str, ...]] = {}
        for conns in ((config or {}).get("connectors") or {}).values():
            for c in conns or []:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                name = str(c["name"])
                cc = c.get("cache")
                if isinstance(cc, dict):
                    cache[name] = CachePolicy(
                        tool=name, scope=str(cc["scope"]), ttl_seconds=int(cc["ttl_seconds"]),
                        keep=tuple(cc.get("keep") or ()), vary_on=tuple(cc.get("vary_on") or ()),
                    )
                if c.get("invalidates"):
                    inval[name] = tuple(str(t) for t in c["invalidates"])
        return cls(cache=cache, invalidates=inval)


def args_hash(input_params: dict | None, vary_values: dict | None = None) -> str:
    """Stable hash of the LLM args (minus force_refresh) plus vary_on session values."""
    params = {k: v for k, v in (input_params or {}).items() if k != FORCE_REFRESH}
    payload = json.dumps({"a": params, "v": vary_values or {}}, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _ago(seconds: float) -> str:
    return "just now" if seconds < 60 else f"{int(seconds // 60)} min ago"


class TurnToolCache:
    """One turn's view of stored tool results, plus the changes it makes.

    Args:
        policies: Cache and invalidation rules.
        entries: ``ContextBundle.tool_results``; unknown or malformed entries are ignored.
        session: Session state, for ``vary_on`` values.
        now: Clock, injectable for tests.
    """

    def __init__(self, policies: ToolResultPolicies, entries: list[dict] | None,
                 session: dict | None, now: Callable[[], float] = time.time) -> None:
        self._p = policies
        self._session = session or {}
        self._now = now
        self._entries: dict[tuple[str, str], dict] = {}
        t = now()
        for e in entries or []:
            # Normalise and validate each entry; skip if malformed.
            if not isinstance(e, dict):
                continue
            try:
                # Require tool as string in cache.
                tool = e.get("tool")
                if not isinstance(tool, str) or tool not in policies.cache:
                    continue
                # Require data key present.
                if "data" not in e:
                    continue
                # Coerce fetched_at and expires_at to float.
                fetched_at = float(e.get("fetched_at", 0))
                stored_expires_at = float(e.get("expires_at", 0))
                # NaN never compares as expired and inf never expires: reject both.
                if not (math.isfinite(fetched_at) and math.isfinite(stored_expires_at)):
                    continue
                # Cap effective expiry by policy TTL.
                pol = policies.cache[tool]
                effective_expires_at = min(stored_expires_at, fetched_at + pol.ttl_seconds)
                # Skip if already expired.
                if effective_expires_at <= t:
                    continue
                # Build normalised entry with float timestamps.
                args_hash = str(e.get("args_hash", ""))
                normalised = {
                    "tool": tool,
                    "args_hash": args_hash,
                    "data": e["data"],
                    "fetched_at": fetched_at,
                    "expires_at": effective_expires_at,  # Use capped expiry
                    "origin": str(e.get("origin", "turn")),
                    "scope": str(e.get("scope", "user")),
                }
                key = (tool, args_hash)
                # If two entries share a key, keep the one with latest fetched_at.
                if key in self._entries and self._entries[key]["fetched_at"] >= fetched_at:
                    continue
                self._entries[key] = normalised
            except (TypeError, ValueError, KeyError):
                # Skip entry that fails to normalise.
                continue
        self._puts: list[dict] = []
        self._invalidated: list[str] = []
        # Serialised data of entries invalidated this turn. Never served or
        # rendered again, but the model has already seen it, so it still
        # grounds values (e.g. ``remember``) for the rest of the turn.
        self._invalidated_data: dict[str, list[str]] = {}
        # tool → args_hash of the entry last served to the model this turn
        # (cache hit or live store). NLU dialogue-acts spec §5.2.
        self._served: dict[str, str] = {}

    def _hash(self, tool_call: ToolCall) -> str:
        pol = self._p.cache[tool_call.tool_name]
        return args_hash(tool_call.input_params, {k: self._session.get(k) for k in pol.vary_on})

    def _fresh(self) -> dict[tuple[str, str], dict]:
        t = self._now()
        return {k: e for k, e in self._entries.items() if float(e["expires_at"]) > t}

    def lookup(self, tool_call: ToolCall) -> ToolResult | None:
        """Return a stored result for this exact call, or None to call live."""
        pol = self._p.cache.get(tool_call.tool_name)
        if pol is None:
            return None
        force_refresh_val = (tool_call.input_params or {}).get(FORCE_REFRESH)
        if force_refresh_val is True or (isinstance(force_refresh_val, str) and force_refresh_val.lower() == "true"):
            _log("refresh", tool_call.tool_name, pol.scope)
            return None
        e = self._fresh().get((tool_call.tool_name, self._hash(tool_call)))
        if e is None:
            _log("miss", tool_call.tool_name, pol.scope)
            return None
        age = self._now() - float(e["fetched_at"])
        self._served[tool_call.tool_name] = str(e["args_hash"])
        _log("hit", tool_call.tool_name, pol.scope, age_s=int(age), ttl_s=pol.ttl_seconds)
        data = e["data"]
        return ToolResult(
            tool_use_id=tool_call.tool_use_id, tool_name=tool_call.tool_name,
            result=data if isinstance(data, dict) else {"items": data}, success=True,
            result_text=f"(stored result, fetched {_ago(age)}) {json.dumps(data, ensure_ascii=False)}",
        )

    def prepare(self, tool_call: ToolCall) -> ToolCall:
        """Return the call with the framework-only force_refresh param removed."""
        if FORCE_REFRESH not in (tool_call.input_params or {}):
            return tool_call
        return replace(tool_call, input_params={
            k: v for k, v in tool_call.input_params.items() if k != FORCE_REFRESH})

    def after_call(self, tool_call: ToolCall, result: ToolResult, origin: str = "turn") -> dict | None:
        """Record a live call: invalidate what a write affects, store a cacheable result.

        Returns the stored entry dict (tool, args_hash, data, fetched_at, expires_at, origin, scope),
        or None when nothing was stored.
        """
        for target in self._p.invalidates.get(tool_call.tool_name, ()):
            self._invalidate(target)
        pol = self._p.cache.get(tool_call.tool_name)
        if pol is None or not result.success:
            return None
        if not getattr(result, "projected", False):
            _log("reject_unprojected", tool_call.tool_name, pol.scope)
            return None
        try:
            data: Any = json.loads(result.result_text)
        except (TypeError, ValueError):
            _log("reject_invalid", tool_call.tool_name, pol.scope)
            return None
        # keep applies only to dict payloads; other JSON shapes are stored whole.
        if pol.keep and isinstance(data, dict):
            data = {k: data[k] for k in pol.keep if k in data}
        h, t = self._hash(tool_call), self._now()
        self._entries[(tool_call.tool_name, h)] = {
            "tool": tool_call.tool_name, "args_hash": h, "data": data, "fetched_at": t,
            "expires_at": t + pol.ttl_seconds, "origin": origin, "scope": pol.scope}
        self._puts.append({"scope": pol.scope, "tool": tool_call.tool_name, "args_hash": h,
                           "data": data, "ttl_seconds": pol.ttl_seconds, "origin": origin})
        self._served[tool_call.tool_name] = h
        _log("store", tool_call.tool_name, pol.scope, ttl_s=pol.ttl_seconds)
        return self._entries[(tool_call.tool_name, h)]

    def _invalidate(self, tool: str) -> None:
        for (t, _), e in self._entries.items():
            if t == tool:
                self._invalidated_data.setdefault(tool, []).append(
                    json.dumps(e["data"], ensure_ascii=False))
        self._entries = {k: v for k, v in self._entries.items() if k[0] != tool}
        self._puts = [p for p in self._puts if p["tool"] != tool]
        if tool not in self._invalidated:
            self._invalidated.append(tool)
        _log("invalidate", tool)

    def fresh_tools(self) -> set[str]:
        """Tools with at least one unexpired entry (their exchanges leave the replay)."""
        return {tool for tool, _ in self._fresh()}

    def latest_entry(self, tool: str) -> dict | None:
        """Return the unexpired entry with the highest ``fetched_at`` for a tool.

        Used by the dialogue-act NLU frame and option resolver (NLU
        dialogue-acts spec §5.2, §6.3): a new search with different arguments
        is a different entry, and the latest one is the list on offer.

        Args:
            tool: Tool name.

        Returns:
            A copy of the normalised entry dict, or None when none is fresh.
        """
        candidates = [e for (t, _), e in self._fresh().items() if t == tool]
        if not candidates:
            return None
        return dict(max(candidates, key=lambda e: float(e["fetched_at"])))

    def served(self) -> dict[str, str]:
        """Return tool → args_hash of the entry last served this turn (hit or live store).

        The orchestrator persists the merged map as ``served_tool_results`` so the
        next turn's NLU frame offers the list the caller last heard (NLU
        dialogue-acts spec §5.2).

        Returns:
            A copy of the map; empty when nothing was served.
        """
        return dict(self._served)

    def entry(self, tool: str, args_hash_value: str) -> dict | None:
        """Return a copy of the fresh entry for ``(tool, args_hash)``, or None.

        Args:
            tool: Tool name.
            args_hash_value: The entry's args hash.

        Returns:
            A copy of the normalised entry dict, or None when absent or expired.
        """
        e = self._fresh().get((tool, args_hash_value))
        return dict(e) if e is not None else None

    def stored_results_by_tool(self) -> dict[str, list[str]]:
        """Serialised data per tool, for grounding checks.

        Includes unexpired entries and the data of entries invalidated earlier
        in this turn: those are no longer served, but the model has seen them.
        """
        out: dict[str, list[str]] = {t: list(v) for t, v in self._invalidated_data.items()}
        for (tool, _), e in self._fresh().items():
            out.setdefault(tool, []).append(json.dumps(e["data"], ensure_ascii=False))
        return out

    def render_known_facts(self) -> str:
        """Body of the <known_facts> prompt block; empty when nothing is fresh."""
        t = self._now()
        lines = []
        for (tool, _), e in sorted(self._fresh().items()):
            remaining = max(1, int((float(e["expires_at"]) - t) // 60))
            lines.append(f"- {tool} — fetched {_ago(t - float(e['fetched_at']))}, valid for "
                         f"{remaining} more min: {json.dumps(e['data'], ensure_ascii=False)}")
        return _KNOWN_FACTS_HEADER + "\n" + "\n".join(lines) if lines else ""

    def has_pending(self) -> bool:
        """True when there are changes not yet sent to Memory Layer."""
        return bool(self._puts or self._invalidated)

    def drain_batch(self) -> dict:
        """Return pending changes for Memory Layer and clear them (the overlay stays)."""
        batch = {"invalidate": list(self._invalidated), "puts": list(self._puts)}
        self._invalidated, self._puts = [], []
        return batch


def augment_tool_definitions(defs: list[dict] | None, policies: ToolResultPolicies,
                             remember_definition: dict | None = None) -> list[dict] | None:
    """Add force_refresh to cached tools and the remember tool to a non-empty tool list.

    ``None`` and ``[]`` pass through unchanged: a subagent with no tools must not
    gain one. Input dicts are never mutated.
    """
    if not defs:
        return defs
    out: list[dict] = []
    for d in defs:
        if d.get("name") in policies.cache:
            schema = dict(d.get("input_schema") or {"type": "object"})
            props = dict(schema.get("properties") or {})
            props[FORCE_REFRESH] = dict(_FORCE_REFRESH_SCHEMA)
            schema["properties"] = props
            d = {**d, "input_schema": schema}
        out.append(d)
    if remember_definition and all(x.get("name") != remember_definition["name"] for x in out):
        out.append(remember_definition)
    return out
