# Session Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run config-declared `tool` steps (Blue Dots: `fetch_profile`) inline on the first turn of a session, before routing, and render config-listed session fields into `<known_profile>`.

**Architecture:**
- A new, pure Agent Core unit `session_bootstrap.py` executes the steps through injected callables:
  - gateway execute;
  - consent check;
  - session write;
  - `apply_tool_results`.
- It writes `session_mapping` values into `bundle.session` and records the results in the Spec A cache with `origin="bootstrap"`.
- The orchestrator calls it right after `context_bundle` on both paths.
- `agent.prompt_session_fields` adds listed session values to `<known_profile>` via one shared helper.

**Tech Stack:** Python 3.11+, pydantic v2, asyncio, pytest (`asyncio_mode=auto`), `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-01-session-bootstrap-design.md`. Read it before starting. It builds on `docs/superpowers/specs/2026-09-30-tool-result-persistence-design.md` (Spec A, already merged).

## Global Constraints

- Baseline: `origin/deploy/voicera-vm` at `5f2bebc`. At execution, cut `feat/session-bootstrap` from `spec/session-bootstrap` in a separate worktree. Never use the user's `ai-diffusion-dpg` checkout. **Never use `git stash`**: stashes are shared across worktrees and the user's live there.
- Run tests per module: `cd <module> && uv run --extra dev pytest -q -p no:cacheprovider …`. Known pre-existing failures to leave alone:
  - action_gateway (3): `test_user_id_substituted_into_path`, `test_rejects_unknown_key_on_param`, `test_get_profile_returns_deterministic_payload`.
  - dev-kit (1): `test_dpg_yaml_validates[reach_layer]`.
- Latch name: `bootstrap_done`. It is never declared in any session schema and is added to Memory Layer's `_SESSION_LIFECYCLE_FIELDS`.
- Step type: `tool` only. Steps may name only `connectors.read` entries. `args` are literal. `requires_consent` defaults to `false`.
- `session_bootstrap.timeout_ms` defaults to `1500` and must be `> 0`. `steps` needs at least 1 entry.
- Outcomes: `ok | failed | timeout | skipped_consent`. The latch is written before any step runs and is set whatever the outcome. The bootstrap never raises into the turn.
- Cache records use `origin="bootstrap"`. Late (post-deadline) results are discarded, never written.
- `agent.prompt_session_fields`: list of session field names, default `[]`. A field is rendered only when the value is not in `(None, "", "[]")` and the key is not already present.
- Logs: `operation`, `status`, `latency_ms`. Never log argument values, results, phone numbers or raw ids. The text line format is `  [STEP 1b] Session bootstrap  ✓  tools=%s  outcomes=%s  latency=%dms`.
- Google-style docstrings on all public code. Coverage stays ≥ 70%.
- Do not push, open PRs or merge without explicit per-action approval.

## Review Focus

Each item names the task whose tests pin it.

1. **Bootstrap runs again on a later turn.** Example: a turn-1 crash after the latch write, or an adopted session that carried `bootstrap_done`. Expected: it runs exactly once per new session. Pinned by Tasks 1 and 4.
2. **A result arriving after the deadline is written anyway.** Expected: discarded. It must not be written to session or the cache, and must not race the turn. Pinned by Task 4.
3. **Bootstrap values not visible to this same turn's routing and prompt.** Expected: `bundle.session` and `bundle.tool_results` are updated in place before routing. Pinned by Tasks 4 and 6.
4. **`prompt_session_fields` exposes a field not listed, or overrides an NLU value.** Expected: only listed fields; the existing value wins. Pinned by Task 5.
5. **A bootstrap step that names a write connector.** Expected: rejected at config load, so the bootstrap can never have side effects. Pinned by Tasks 3 and 7.

---

## File Structure

| File | Responsibility |
|---|---|
| `memory_layer/src/memory_layer.py` | Add `bootstrap_done` to `_SESSION_LIFECYCLE_FIELDS` |
| `agent_core/src/tool_results.py` | `after_call(..., origin=)` returns the stored entry |
| `agent_core/src/schema/config.py` | `AgentConfig.prompt_session_fields`, `SessionBootstrapStep`, `SessionBootstrapConfig`, `MergedConfig.session_bootstrap` + validator |
| `agent_core/src/session_bootstrap.py` (new) | `SessionBootstrap`: steps, latch, sync + async run, outcomes, logging/metric |
| `agent_core/src/orchestrator.py` | `_build_profile_context` helper (both sites); bootstrap call after `context_bundle` on both paths |
| `dev-kit/dev_kit/schemas/domain/agent_core.py`, `schemas/validation.py`, `dev_kit/schema.py`, `schemas/cross_block_validation.py` | dev-kit mirror + cross-block rules |
| `dev-kit/configs/blue-dots/agent_core.yaml`, `dev-kit/dpg/agent_core.yaml` | Blue Dots rollout + documented defaults |

---

### Task 1: Memory Layer — `bootstrap_done` is never adopted

**Files:**
- Modify: `memory_layer/src/memory_layer.py` (`_SESSION_LIFECYCLE_FIELDS`, ~line 40)
- Test: `memory_layer/tests/test_memory_layer.py`

**Interfaces:**
- Produces: when a new session adopts a prior session's state, `bootstrap_done` is not copied. This is the same treatment `opening_phrase_emitted` gets.

- [ ] **Step 1: Write the failing test.** Find the existing test that covers adoption skipping `opening_phrase_emitted` (grep `opening_phrase_emitted` in `memory_layer/tests/test_memory_layer.py`). Add a sibling test that uses the same setup, with the adopted prior session hash also containing `"bootstrap_done": "true"`. Assert the new session's state (the `init_session` call's mapping, or the returned bundle session, matching how the sibling asserts) does **not** contain `bootstrap_done`. Also assert that `opening_phrase_emitted` is still excluded.

- [ ] **Step 2: Run and check it fails.**

Run: `cd memory_layer && uv run --extra dev pytest -q -p no:cacheprovider tests/test_memory_layer.py -k bootstrap_done -v`
Expected: FAIL (`bootstrap_done` present)

- [ ] **Step 3: Implement.**

```python
_SESSION_LIFECYCLE_FIELDS: frozenset[str] = frozenset({
    "opening_phrase_emitted",
    # Session bootstrap latch (session-bootstrap spec §5.1): a new call must
    # bootstrap afresh, never inherit "already done" from the call it adopts.
    "bootstrap_done",
})
```

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd memory_layer && uv run --extra dev pytest -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add memory_layer/src/memory_layer.py memory_layer/tests/test_memory_layer.py
git commit -m "feat(memory-layer): never adopt the session bootstrap latch

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `TurnToolCache.after_call` takes an origin and returns the stored entry

**Files:**
- Modify: `agent_core/src/tool_results.py` (`after_call`, ~line 221)
- Test: `agent_core/tests/test_tool_results.py`

**Interfaces:**
- Produces: `TurnToolCache.after_call(tool_call, result, origin: str = "turn") -> dict | None`. It returns the stored entry dict (`tool, args_hash, data, fetched_at, expires_at, origin, scope`), or `None` when nothing was stored. `origin` is written into both the entry and the queued put. Existing callers (which ignore the return value and pass no `origin`) are unaffected.

- [ ] **Step 1: Write the failing tests.** Append to `agent_core/tests/test_tool_results.py`, reusing that file's `POL`, `tc`, `live` and `clock` helpers:

```python
def test_after_call_origin_bootstrap_and_returns_entry():
    cache = TurnToolCache(POL, [], {}, clock())
    entry = cache.after_call(tc("fetch_profile"), live("fetch_profile", {"items": []}), origin="bootstrap")
    assert entry is not None
    assert entry["origin"] == "bootstrap" and entry["tool"] == "fetch_profile"
    assert entry["data"] == {"items": []} and entry["scope"] == "user"
    [put] = cache.drain_batch()["puts"]
    assert put["origin"] == "bootstrap"


def test_after_call_default_origin_turn_and_none_when_not_stored():
    cache = TurnToolCache(POL, [], {}, clock())
    assert cache.after_call(tc("fetch_profile"), live("fetch_profile", {}))["origin"] == "turn"
    assert cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, success=False)) is None
    assert cache.after_call(tc("uncached"), live("uncached", {})) is None
    assert cache.after_call(tc("save_profile"), live("save_profile", {})) is None
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_tool_results.py -k "origin" -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'origin'`)

- [ ] **Step 3: Implement.** Change the signature to `def after_call(self, tool_call: ToolCall, result: ToolResult, origin: str = "turn") -> dict | None:` and update the docstring. Every early `return` becomes `return None`. Replace both hardcoded `"origin": "turn"` with `"origin": origin`. After the `_log("store", …)` line, add `return self._entries[(tool_call.tool_name, h)]`.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/tool_results.py agent_core/tests/test_tool_results.py
git commit -m "feat(agent-core): after_call records an origin and returns the stored entry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Runtime config schema

**Files:**
- Modify: `agent_core/src/schema/config.py` (`AgentConfig` ~line 204; `MergedConfig` ~line 820)
- Test: `agent_core/tests/test_schema_config.py`

**Interfaces:**
- Produces:
  - `AgentConfig.prompt_session_fields: list[str] = []`
  - `SessionBootstrapStep(type: Literal["tool"], tool: str (min_length=1), args: dict[str, Any] = {}, requires_consent: bool = False)`
  - `SessionBootstrapConfig(timeout_ms: int = 1500 (gt 0), steps: list[SessionBootstrapStep] (min_length=1))`
  - `MergedConfig.session_bootstrap: Optional[SessionBootstrapConfig] = None`
- Validator: each step's `tool` must be the name of a `connectors.read` entry. The error message is `session_bootstrap.steps[<i>]: '<tool>' is not a read connector`.

- [ ] **Step 1: Write the failing tests.** Append to `test_schema_config.py`, building on that file's existing minimal valid config helper (`_minimal_valid_config()`) and `MergedConfig.validate_full`:

```python
import copy
import pytest
from pydantic import ValidationError
from src.schema.config import MergedConfig


def _boot(steps=None, read=None, write=None, **agent):
    cfg = copy.deepcopy(_minimal_valid_config())
    cfg.setdefault("connectors", {})
    cfg["connectors"]["read"] = read if read is not None else [{"name": "fetch_profile"}]
    cfg["connectors"]["write"] = write or []
    if steps is not None:
        cfg["session_bootstrap"] = {"steps": steps}
    if agent:
        cfg.setdefault("agent", {}).update(agent)
    return cfg


def test_valid_bootstrap_and_prompt_session_fields():
    cfg = _boot([{"type": "tool", "tool": "fetch_profile"}],
                prompt_session_fields=["profile_item_id", "stored_trade"])
    m = MergedConfig.validate_full(cfg)
    assert m.session_bootstrap.timeout_ms == 1500
    assert m.session_bootstrap.steps[0].args == {} and m.session_bootstrap.steps[0].requires_consent is False
    assert m.agent.prompt_session_fields == ["profile_item_id", "stored_trade"]


def test_no_bootstrap_is_default():
    assert MergedConfig.validate_full(_boot()).session_bootstrap is None


@pytest.mark.parametrize("cfg,match", [
    pytest.param(_boot([{"type": "tool", "tool": "save_profile"}], write=[{"name": "save_profile"}]),
                 "is not a read connector", id="write-connector"),
    pytest.param(_boot([{"type": "tool", "tool": "ghost"}]), "is not a read connector", id="unknown-connector"),
    pytest.param(_boot([{"type": "set", "tool": "fetch_profile"}]), "type", id="bad-type"),
    pytest.param(_boot([]), "at least 1", id="no-steps"),
    pytest.param({**_boot([{"type": "tool", "tool": "fetch_profile"}]),
                  "session_bootstrap": {"timeout_ms": 0, "steps": [{"type": "tool", "tool": "fetch_profile"}]}},
                 "greater than 0", id="zero-timeout"),
    pytest.param(_boot([{"type": "tool", "tool": "fetch_profile", "extra": 1}]), "extra", id="extra-key"),
])
def test_invalid_bootstrap_rejected(cfg, match):
    with pytest.raises(ValidationError, match=match):
        MergedConfig.validate_full(cfg)
```

If `validate_full` returns nothing rather than the model, use `MergedConfig.model_validate(cfg)` for the attribute assertions in the first test. Keep `validate_full` for the rejection tests.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_schema_config.py -k "bootstrap or prompt_session" -v`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - Add to `AgentConfig`: `prompt_session_fields: list[str] = Field(default_factory=list)`, with a docstring line: "Session fields rendered into <known_profile> (session-bootstrap spec §5.4)".
  - Above `MergedConfig`:

```python
class SessionBootstrapStep(BaseModel):
    """One deterministic step run on the first turn of a session (session-bootstrap spec §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tool"]
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    requires_consent: bool = False


class SessionBootstrapConfig(BaseModel):
    """Steps run inline on the first turn, before routing, within ``timeout_ms``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_ms: int = Field(default=1500, gt=0)
    steps: list[SessionBootstrapStep] = Field(min_length=1)
```

  - Add `session_bootstrap: Optional[SessionBootstrapConfig] = None` to `MergedConfig`. Add to its existing `@model_validator(mode="after")` (`_check_tool_result_rules`), or a new validator next to it:

```python
        if self.session_bootstrap:
            read_names = {c.name for c in self.connectors.read}
            for i, step in enumerate(self.session_bootstrap.steps):
                if step.tool not in read_names:
                    raise ValueError(f"session_bootstrap.steps[{i}]: '{step.tool}' is not a read connector")
```

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/schema/config.py agent_core/tests/test_schema_config.py
git commit -m "feat(agent-core): schema for session bootstrap and prompt session fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `session_bootstrap.py` — the bootstrap unit

**Files:**
- Create: `agent_core/src/session_bootstrap.py`
- Test: `agent_core/tests/test_session_bootstrap.py`

**Interfaces:**
- Consumes: `TurnToolCache(policies, entries, session)` and `after_call(tc, result, origin=)` from Task 2; `ToolResultPolicies`; `ToolCall`, `ToolResult`.
- Produces:
  - `LATCH = "bootstrap_done"`
  - `BootstrapReport(outcomes: dict[str, str], latency_ms: int)` (frozen dataclass)
  - `SessionBootstrap.from_config(config: dict | None, policies) -> SessionBootstrap | None`. Returns None when `session_bootstrap` is absent.
  - `.needed(bundle) -> bool`. True when the latch is not truthy in `bundle.session`.
  - `.run_sync(bundle, *, execute, check_consent, write_session, apply_tool_results, session_values) -> BootstrapReport`
  - `async .run_async(bundle, *, execute, check_consent, write_session, apply_tool_results, session_values) -> BootstrapReport`
- Callable shapes (async variants are awaitables):
  - `execute(tool_call) -> ToolResult`
  - `check_consent(tool_name) -> bool`
  - `write_session(key, value) -> None`
  - `apply_tool_results(batch: dict) -> None`
- Both run methods mutate `bundle.session` and `bundle.tool_results` in place and never raise.

- [ ] **Step 1: Write the failing tests.** Create `agent_core/tests/test_session_bootstrap.py`:

```python
"""Unit tests for the session bootstrap unit (session-bootstrap spec §5)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.models import ToolResult
from src.session_bootstrap import LATCH, SessionBootstrap
from src.tool_results import ToolResultPolicies

CONFIG = {
    "connectors": {"read": [{"name": "fetch_profile", "cache": {"scope": "session", "ttl_seconds": 1800}}]},
    "session_bootstrap": {"timeout_ms": 500, "steps": [{"type": "tool", "tool": "fetch_profile"}]},
}
POL = ToolResultPolicies.from_config(CONFIG)


def bundle(session=None):
    return SimpleNamespace(session=dict(session or {}), profile={}, tool_results=[])


def ok_result(**session_values):
    return ToolResult(tool_use_id="bootstrap-0", tool_name="fetch_profile", result={}, success=True,
                      result_text=json.dumps({"items": [{"item_id": "p1"}]}), projected=True,
                      session_values=session_values)


class Rec:
    def __init__(self):
        self.writes, self.batches, self.calls = [], [], []


def sync_deps(rec, result=None, consent=True, raise_exc=None):
    def execute(tc):
        rec.calls.append(tc)
        if raise_exc:
            raise raise_exc
        return result or ok_result(has_age=True, profile_item_id="p1")
    return dict(execute=execute, check_consent=lambda t: consent,
                write_session=lambda k, v: rec.writes.append((k, v)),
                apply_tool_results=lambda b: rec.batches.append(b), session_values={})


def test_from_config_none_without_section():
    assert SessionBootstrap.from_config({}, POL) is None


def test_needed_respects_latch():
    boot = SessionBootstrap.from_config(CONFIG, POL)
    assert boot.needed(bundle()) is True
    assert boot.needed(bundle({LATCH: True})) is False


def test_sync_success_writes_values_cache_and_latch_first():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec))
    assert report.outcomes == {"fetch_profile": "ok"}
    assert rec.writes[0] == (LATCH, True)                     # latch before anything else
    assert ("has_age", True) in rec.writes and b.session["has_age"] is True
    assert b.session["profile_item_id"] == "p1" and b.session[LATCH] is True
    assert len(b.tool_results) == 1 and b.tool_results[0]["origin"] == "bootstrap"
    assert rec.batches[0]["puts"][0]["origin"] == "bootstrap"
    assert rec.calls[0].tool_use_id == "bootstrap-0" and rec.calls[0].input_params == {}


def test_sync_failure_sets_latch_writes_nothing_else():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=False, error="boom")
    report = boot.run_sync(b, **sync_deps(rec, result=bad))
    assert report.outcomes == {"fetch_profile": "failed"}
    assert rec.writes == [(LATCH, True)] and b.tool_results == [] and rec.batches == []


def test_sync_exception_never_raises():
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec, raise_exc=RuntimeError("down")))
    assert report.outcomes == {"fetch_profile": "failed"} and b.session[LATCH] is True


def test_consent_skip():
    cfg = {**CONFIG, "session_bootstrap": {"steps": [{"type": "tool", "tool": "fetch_profile", "requires_consent": True}]}}
    boot, rec, b = SessionBootstrap.from_config(cfg, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec, consent=False))
    assert report.outcomes == {"fetch_profile": "skipped_consent"} and rec.calls == []


def test_sync_late_result_discarded(monkeypatch):
    import src.session_bootstrap as sb
    times = iter([0.0, 0.0, 10.0, 10.0, 10.0])          # start, pre-step check, after call (past 0.5s), ...
    monkeypatch.setattr(sb.time, "monotonic", lambda: next(times, 10.0))
    boot, rec, b = SessionBootstrap.from_config(CONFIG, POL), Rec(), bundle()
    report = boot.run_sync(b, **sync_deps(rec))
    assert report.outcomes == {"fetch_profile": "timeout"}
    assert "has_age" not in b.session and b.tool_results == [] and rec.batches == []


async def test_async_success_and_timeout_discard():
    boot = SessionBootstrap.from_config(CONFIG, POL)
    rec, b = Rec(), bundle()

    async def execute(tc):
        return ok_result(has_age=True)

    async def noop(*a):
        rec.writes.append(a) if len(a) == 2 else rec.batches.append(a[0])

    async def consent(t):
        return True

    report = await boot.run_async(b, execute=execute, check_consent=consent, write_session=noop,
                                  apply_tool_results=noop, session_values={})
    assert report.outcomes == {"fetch_profile": "ok"} and b.session["has_age"] is True

    rec2, b2 = Rec(), bundle()

    async def slow(tc):
        await asyncio.sleep(2)
        return ok_result(has_age=True)

    report2 = await boot.run_async(b2, execute=slow, check_consent=consent, write_session=noop,
                                   apply_tool_results=noop, session_values={})
    assert report2.outcomes == {"fetch_profile": "timeout"}
    assert "has_age" not in b2.session and b2.tool_results == [] and b2.session[LATCH] is True
```

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_session_bootstrap.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement.** Create `agent_core/src/session_bootstrap.py`:

```python
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
        logger.info("  [STEP 1b] Session bootstrap  ✓  tools=%s  outcomes=%s  latency=%dms",
                    [s.tool for s in self._steps], outcomes, latency)
        logger.info("session_bootstrap.turn", extra={
            "operation": "session_bootstrap.run", "status": "success", "latency_ms": latency})
        return BootstrapReport(outcomes=outcomes, latency_ms=latency)

    def run_sync(self, bundle, *, execute: Callable[[ToolCall], ToolResult],
                 check_consent: Callable[[str], bool], write_session: Callable[[str, Any], None],
                 apply_tool_results: Callable[[dict], None], session_values: dict) -> BootstrapReport:
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
                        apply_tool_results: Callable[[dict], Awaitable[None]],
                        session_values: dict) -> BootstrapReport:
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
        for t in pending:
            t.cancel()
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
                outcome = "timeout" if any(not t.done() or t.cancelled() for t in [tasks[i]]) else "failed"
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
```

Notes for the implementer:
- In `run_async`, a step whose task raised (it is in `done` but not in `finished`) must be reported as `failed`, and a step cancelled at the deadline as `timeout`. If the expression above doesn't distinguish the two cleanly, track `pending` membership explicitly (`timeout` when the task was in `pending`). That is the intended behaviour.
- `session_values` is accepted for symmetry with the orchestrator's call. The `execute` callables passed in already bind it. Keep the parameter, documented as "passed through by the caller's execute binding; unused here", or have `execute` receive it. Choose one and test it.
- Sync timing test: the monkeypatched `time.monotonic` sequence must match your call order. Adjust the iterator so the post-call check sees a time past the deadline.

- [ ] **Step 4: Run and check it passes.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_session_bootstrap.py -v`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/session_bootstrap.py agent_core/tests/test_session_bootstrap.py
git commit -m "feat(agent-core): session bootstrap unit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `prompt_session_fields` rendering via one shared helper

**Files:**
- Modify: `agent_core/src/orchestrator.py`. The two `profile_context` blocks are at ~1128 (sync) and ~4088 (stream); each starts with `profile_context = dict(bundle.profile)` / `profile_field_names = set(entity_map.values())`.
- Test: `agent_core/tests/test_orchestrator.py`

**Interfaces:**
- Produces: `AgentCore._build_profile_context(bundle, entity_map: dict) -> dict`, used by both sites. The existing NLU-field overlay is unchanged. Each field in `self._prompt_session_fields` (read once in `__init__` from `config["agent"].get("prompt_session_fields", [])`) is added when the session value is not in `(None, "", "[]")` and `not profile_context.get(field)`.

- [ ] **Step 1: Write the failing tests.** In `test_orchestrator.py`, construct the agent with `_make_agent()`, then set `agent._prompt_session_fields = ["profile_item_id", "stored_trade"]`:

```python
def test_build_profile_context_adds_listed_session_fields_only():
    agent = _make_agent()
    agent._prompt_session_fields = ["profile_item_id", "stored_trade"]
    b = ContextBundle(session={"profile_item_id": "p1", "stored_trade": "", "stored_location": "Pune",
                               "trade": "Welding"}, profile={"name": "Asha"}, journey=None)
    ctx = agent._build_profile_context(b, {"trade": "trade"})
    assert ctx["profile_item_id"] == "p1"
    assert "stored_trade" not in ctx            # empty value skipped
    assert "stored_location" not in ctx         # not listed
    assert ctx["trade"] == "Welding" and ctx["name"] == "Asha"


def test_build_profile_context_does_not_override_existing():
    agent = _make_agent()
    agent._prompt_session_fields = ["name"]
    b = ContextBundle(session={"name": "Other"}, profile={"name": "Asha"}, journey=None)
    assert agent._build_profile_context(b, {})["name"] == "Asha"


def test_prompt_session_fields_reach_build_system_prompt():
    agent = _make_agent(session_data={"current_subagent_id": "market_truth", "profile_item_id": "p1"})
    agent._prompt_session_fields = ["profile_item_id"]
    agent.process_turn(_turn_input())
    profile = agent._manager_agent.build_system_prompt.call_args.kwargs["profile"]
    assert profile["profile_item_id"] == "p1"
```

Add one stream-path test in `test_stream_turn.py`, using its `_make_agent_core` pattern, asserting the same `profile` kwarg on the stream path's `build_system_prompt` call.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_orchestrator.py tests/test_stream_turn.py -k "profile_context or prompt_session" -v`
Expected: FAIL

- [ ] **Step 3: Implement.** In `AgentCore.__init__`, after `self._remember = …`, add:

```python
        self._prompt_session_fields: list[str] = list(
            ((config.get("agent") or {}).get("prompt_session_fields")) or [])
```

Add the method, moving the existing comment block into its docstring:

```python
    def _build_profile_context(self, bundle, entity_map: dict) -> dict:
        """Profile facts for <known_profile>: profile, NLU-mapped session fields, listed session fields.

        bundle.profile is the source of truth; session values only fill what it
        does not carry (see the age-stuck-at-0 note). ``agent.prompt_session_fields``
        names further session fields — e.g. ones written by session_mapping —
        that the prompt may read (session-bootstrap spec §5.4).
        """
        profile_context = dict(bundle.profile)
        overlay = set(entity_map.values()) | set(self._prompt_session_fields)
        for k, v in bundle.session.items():
            if k in overlay and v not in (None, "", "[]") and not profile_context.get(k):
                profile_context[k] = v
        return profile_context
```

Replace both inline blocks, from `profile_context = dict(bundle.profile)` through the end of the `for` loop, with `profile_context = self._build_profile_context(bundle, entity_map)`.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/orchestrator.py agent_core/tests
git commit -m "feat(agent-core): render configured session fields into known_profile

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Wire the bootstrap into both paths

**Files:**
- Modify: `agent_core/src/orchestrator.py`:
  - `__init__`;
  - sync, right after `bundle = self._memory.context_bundle(...)` (~line 559);
  - stream, right after `bundle = await self._async_memory.context_bundle(...)` (~line 3497).
- Test: `agent_core/tests/test_orchestrator.py`, `agent_core/tests/test_stream_turn.py`, `agent_core/tests/test_turn_path_identity_parity.py`

**Interfaces:**
- Consumes: `SessionBootstrap` (Task 4); existing `self._tool_session_values(bundle)`, `self._write_memory_sync`, `self._memory.apply_tool_results`, `self._async_memory.write`, `self._async_memory.apply_tool_results`, `self._trust.check_consent`, `self._async_trust.check_consent`, `self._async_gateway.execute`, and the sync gateway `self._manager_agent._gateway`.
- Produces: `AgentCore._bootstrap: SessionBootstrap | None`, built in `__init__`.

- [ ] **Step 1: Write the failing tests.**
  - **Sync,** in `test_orchestrator.py`. Build with `_make_agent()`, then set `agent._bootstrap = SessionBootstrap.from_config(<config with fetch_profile read connector + session_bootstrap>, agent._tool_policies)` and `agent._manager_agent._gateway = MagicMock()`, where `_gateway.execute.return_value` is a successful `ToolResult` with `session_values={"has_age": True}`. Assert:
    - (a) `_gateway.execute` was called once with a `ToolCall` named `fetch_profile`, before `manager.run_turn`;
    - (b) `agent._memory.write` was called with `"bootstrap_done"`;
    - (c) a second `process_turn`, where the bundle session now has `bootstrap_done: True`, does not call `execute` again;
    - (d) with `agent._bootstrap = None`, `execute` is never called by the bootstrap.
  - **Stream,** in `test_stream_turn.py`. The same four checks using `async_gateway.execute = AsyncMock(...)`, with the bootstrap's `execute` call happening before the stream's first LLM call.
  - **Parity,** in `test_turn_path_identity_parity.py`. A returning caller whose bootstrap result sets `user_terms`, `user_privacy` and `has_age` to `True`, with a workflow whose `opening` routing rule 1b requires them: on the stream path, turn 1 routes to the next subagent (`profile_resolve` in that harness's workflow), not `opening`. A bootstrap failure (`success=False`) leaves routing as today on both paths.
  - **Exception safety:** if `_gateway.execute` raises, `process_turn` still returns a normal `TurnResult`.

- [ ] **Step 2: Run and check they fail.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider tests/test_orchestrator.py tests/test_stream_turn.py tests/test_turn_path_identity_parity.py -k bootstrap -v`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - Add `from src.session_bootstrap import SessionBootstrap` to the imports. In `__init__`, after `self._tool_policies = …`, add `self._bootstrap = SessionBootstrap.from_config(config, self._tool_policies)`.
  - **Sync,** immediately after the `bundle = self._memory.context_bundle(...)` assignment:

```python
        if self._bootstrap is not None and self._bootstrap.needed(bundle):
            try:
                _gw = getattr(self._manager_agent, "_gateway", None)
                if _gw is not None:
                    self._bootstrap.run_sync(
                        bundle,
                        execute=lambda tc: _gw.execute(
                            tc, session_id, user_id, session_values=self._tool_session_values(bundle)),
                        check_consent=lambda tool: self._trust.check_consent(session_id, tool),
                        write_session=lambda k, v: self._write_memory_sync(session_id, user_id, "session", k, v),
                        apply_tool_results=lambda batch: self._memory.apply_tool_results(session_id, user_id, batch),
                        session_values=self._tool_session_values(bundle),
                    )
            except Exception as e:  # never break a turn
                logger.error("orchestrator.session_bootstrap_error", extra={
                    "operation": "orchestrator.process_turn", "status": "failure",
                    "session_id": session_id, "error": type(e).__name__})
```

  - **Stream,** immediately after the `bundle = await self._async_memory.context_bundle(...)` line:

```python
            if self._bootstrap is not None and self._bootstrap.needed(bundle) and self._async_gateway:
                try:
                    async def _exec(tc):
                        return await self._async_gateway.execute(
                            tc, session_id, user_id, session_values=self._tool_session_values(bundle))

                    async def _consent(tool):
                        return await self._async_trust.check_consent(session_id, tool) if self._async_trust else False

                    async def _write(k, v):
                        await self._async_memory.write(session_id, user_id, "session", k, v)

                    async def _apply(batch):
                        await self._async_memory.apply_tool_results(session_id, user_id, batch)

                    await self._bootstrap.run_async(
                        bundle, execute=_exec, check_consent=_consent, write_session=_write,
                        apply_tool_results=_apply, session_values=self._tool_session_values(bundle))
                except Exception as e:  # never break a turn
                    logger.error("orchestrator.session_bootstrap_error", extra={
                        "operation": "orchestrator.stream_turn", "status": "failure",
                        "session_id": session_id, "error": type(e).__name__})
```

  - If `session_id` or `user_id` have different local names at either site, use the names in scope there. Make sure both inserts come before any later code reads `bundle.session` (subagent resolution, consent gate, carry-over, pre-NLU args), and that the turn's `TurnToolCache` is built later from `bundle.tool_results`. It is, at the prompt-build step.

- [ ] **Step 4: Run the module and check it passes.**

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/orchestrator.py agent_core/tests
git commit -m "feat(agent-core): run the session bootstrap before turn-1 routing on both paths

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Dev-kit schemas and cross-block rules

**Files:**
- Modify:
  - `dev-kit/dev_kit/schemas/domain/agent_core.py` (`AgentSection`; new `SessionBootstrapSection`);
  - `dev-kit/dev_kit/schemas/validation.py` (`DOMAIN_SECTION_SCHEMAS`);
  - `dev-kit/dev_kit/schema.py` (loader `AgentConfig`, `AgentCoreConfig`);
  - `dev-kit/dev_kit/schemas/cross_block_validation.py`.
- Test: `dev-kit/tests/schemas/test_cross_block_validation.py`, and the domain-schema tests next to it.

**Interfaces:**
- Produces:
  - dev-kit accepts `agent.prompt_session_fields` and top-level `session_bootstrap`, with the same shapes and rules as Task 3 (`extra="forbid"`, `type: Literal["tool"]`, `timeout_ms > 0`, `min_length=1` steps).
  - New cross-block rule, gated `applicable_after("tools")`:
    - each `agent.prompt_session_fields` entry must be in `memory_layer.state.session.schema` (message: `agent.prompt_session_fields: '<f>' is not a declared session field`);
    - each `session_bootstrap.steps[].tool` must be a `connectors.read` name (message: `session_bootstrap.steps[<i>]: '<tool>' is not a read connector`).

- [ ] **Step 1: Write the failing tests.** In `test_cross_block_validation.py`:

```python
ML_B = {"state": {"session": {"ttl_minutes": 60, "schema": {
    "profile_item_id": {"type": "string"}, "stored_trade": {"type": "string"}}}}}


def _boot_errs(ac):
    return [e for e in validate_cross_block({"agent_core": ac, "memory_layer": ML_B}, [])
            if "prompt_session_fields" in e or "session_bootstrap" in e]


def test_bootstrap_cross_block_valid():
    ac = {"agent": {"prompt_session_fields": ["profile_item_id"]},
          "connectors": {"read": [{"name": "fetch_profile"}], "write": []},
          "session_bootstrap": {"steps": [{"type": "tool", "tool": "fetch_profile"}]}}
    assert _boot_errs(ac) == []


def test_bootstrap_cross_block_errors():
    ac = {"agent": {"prompt_session_fields": ["ghost"]},
          "connectors": {"read": [], "write": [{"name": "save_profile"}]},
          "session_bootstrap": {"steps": [{"type": "tool", "tool": "save_profile"}]}}
    errs = _boot_errs(ac)
    assert any("'ghost' is not a declared session field" in e for e in errs)
    assert any("'save_profile' is not a read connector" in e for e in errs)
```

In the domain-schema tests, add one accept test for `agent.prompt_session_fields` plus a `session_bootstrap` section, and one reject test for `type: set` (match `type`), using that file's existing call into `dev_kit.schemas.validation`.

- [ ] **Step 2: Run and check they fail.**

Run: `cd dev-kit && uv run pytest -q -p no:cacheprovider tests/schemas -k "bootstrap or prompt_session" -v`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - **Domain mirror:** add `prompt_session_fields: list[str] = Field(default_factory=list)` to `AgentSection`. Add `SessionBootstrapStepModel` and `SessionBootstrapSection`, identical in fields and constraints to Task 3 (`extra="forbid"`), and register `("agent_core", "session_bootstrap"): agent_core.SessionBootstrapSection` in `validation.py`.
  - **Loader models:** in `dev_kit/schema.py`, add `prompt_session_fields: list[str] = Field(default_factory=list)` to the loader `AgentConfig`, and `session_bootstrap: Optional[dict] = None` to `AgentCoreConfig`.
  - **Cross-block:** add the helper and call it at the same gated spot as `_tool_result_memory_rules`:

```python
def _session_bootstrap_rules(ac: dict, ml: dict) -> list[str]:
    """Cross-block rules for session bootstrap and prompt_session_fields (agent_core ↔ memory_layer)."""
    errors: list[str] = []
    schema = (((ml.get("state") or {}).get("session") or {}).get("schema")) or {}
    for f in ((ac.get("agent") or {}).get("prompt_session_fields")) or []:
        if f not in schema:
            errors.append(f"agent.prompt_session_fields: '{f}' is not a declared session field")
    read = {c.get("name") for c in ((ac.get("connectors") or {}).get("read") or []) if isinstance(c, dict)}
    for i, s in enumerate(((ac.get("session_bootstrap") or {}).get("steps")) or []):
        tool = (s or {}).get("tool") if isinstance(s, dict) else None
        if tool not in read:
            errors.append(f"session_bootstrap.steps[{i}]: '{tool}' is not a read connector")
    return errors
```

- [ ] **Step 4: Run and check it passes.**

Run: `cd dev-kit && uv run pytest -q -p no:cacheprovider`
Expected: all PASS except the known `test_dpg_yaml_validates[reach_layer]`

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit dev-kit/tests
git commit -m "feat(dev-kit): validate session bootstrap and prompt session fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Blue Dots rollout and defaults docs

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml`, `dev-kit/dpg/agent_core.yaml`

- [ ] **Step 1: Add the config.** In `dev-kit/configs/blue-dots/agent_core.yaml`:
  - Under `agent:`, add `prompt_session_fields: [profile_item_id, stored_trade, stored_location]`, with a comment saying these are written by `fetch_profile`'s `session_mapping` and that the profile_resolve prompt reads them from `<known_profile>`.
  - Add a top-level block:

```yaml
# Session bootstrap: fetch the caller's profile before turn-1 routing, without
# the LLM, so a returning caller's consent/age flags and live profile are in
# session state when turn 1 is routed (session-bootstrap spec). Read connector only.
session_bootstrap:
  timeout_ms: 1500
  steps:
    - type: tool
      tool: fetch_profile
```

- [ ] **Step 2: Change the opening prompt.** Replace exactly these lines (currently ~1313-1316):

```
        ## Turn 1: greet, ask if they want work, and FETCH
        On your FIRST turn call `fetch_profile` — before anything else, and
        without mentioning it. Then say one short Hindi sentence: greet them
        and ask whether they are looking for work.
```

with:

```
        ## Turn 1: greet and ask if they want work
        `fetch_profile` already ran when the call started, before this turn;
        its result is the fetch_profile entry of <known_facts>. Do not call it
        again unless that entry is absent. Say one short Hindi sentence: greet
        them and ask whether they are looking for work.
```

Leave the rest of the opening prompt unchanged, including "Ask NOTHING about consent or age on this turn".

- [ ] **Step 3: Document the defaults.** In `dev-kit/dpg/agent_core.yaml`, add commented examples of `agent.prompt_session_fields` and `session_bootstrap` (`type: tool`, read connector only, `timeout_ms`, `requires_consent`), each with a one-line explanation and a pointer to the spec.

- [ ] **Step 4: Validate.**
  - Build the merged config the way `agent_core/main.py` does (deep-merge `dev-kit/dpg/agent_core.yaml` with `dev-kit/configs/blue-dots/agent_core.yaml`; do **not** import `main`, which builds the app on import) and run `MergedConfig.validate_full`.
  - Run dev-kit `validate_cross_block` over the blue-dots blocks.
  - Expected: no new errors. The 4 rule-14 errors that already existed remain.
  - Then run both module suites:

Run: `cd agent_core && uv run --extra dev pytest -q -p no:cacheprovider && cd ../dev-kit && uv run pytest -q -p no:cacheprovider`
Expected: all PASS except the known dev-kit failure

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml dev-kit/dpg/agent_core.yaml
git commit -m "feat(blue-dots): bootstrap fetch_profile and surface the live profile fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9 (controller-run): local end-to-end verification

This task is not dispatched to a subagent. The controller runs it after the final review, with the user's `.env.local`.

- [ ] Rebuild the 7 images from the branch and start the bridge chain locally. Use the same scratch override as the 2026-10-01 run: Blue Dots keys, `TOOL_RESULT_KEY_SECRET`, bridge on `127.0.0.1:8008`, `env -u OPENAI_API_KEY`.
- [ ] Returning caller `917892487848`, streaming, 3 calls:
  - turn 1 has spoken content;
  - turn 1 routes to `profile_resolve`;
  - the "<trade> in <city>" offer is made;
  - `[STEP 1b]` appears with outcome `ok`, and its latency is recorded.
- [ ] New caller `917999021577`, non-streaming: the turn-1 canned greeting, then the consent flow as today, and no LLM `fetch_profile` call on turn 2.
- [ ] Report the per-step latency table and turn-1 behaviour against the 2026-10-01 baseline. Bring the stack down afterwards.
