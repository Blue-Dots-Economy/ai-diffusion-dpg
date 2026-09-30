# TurnAssembler for Streaming Turns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every streaming turn (`/stream_turn` and the session endpoints) through the TurnAssembler. An interrupted turn then stops at a safe point, and the next turn gets what it held (the user's utterances and its completed tool rounds) via Memory Layer.

**Architecture:** The TurnAssembler decides *when* a turn stops. It interrupts cooperatively, never cancels the task, drains the predecessor before starting its successor, and runs one turn per session. `AgentCore.stream_turn` decides *what survives*. It folds `turn_carryover` into the input on entry, captures each tool round before yielding `tool_end`, and on an interrupted exit persists `recent_tool_exchanges` (marked `delivered: false`) and `turn_carryover` from a `finally`. `/stream_turn` becomes a request-scoped adapter over the assembler (`submit` / `attach` / `detach`). The bridge stops sending its no-op cancel.

**Tech Stack:** Python 3.12+, asyncio, FastAPI/Starlette SSE, Pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md`

## Global Constraints

- Branch `feat/turn-assembler-request-mode`, cut from `origin/deploy/voicera-vm` @ `cdeb622`. Commit per task; do not push without an explicit OK from the user.
- Run module tests with `uv run pytest` from inside the module directory (`agent_core/`, `reach_layer/bridge/`, `dev-kit/`). Baseline: `agent_core` is 934 passed, 1 skipped.
- `/process_turn` and the sync `process_turn` pipeline must not change behaviour.
- Nothing may name a domain tool or use case. Tools are whatever the domain schema declares.
- Agent Core stays stateless between turns. Carry-over lives in Memory Layer, session scope, keys `turn_carryover` and `recent_tool_exchanges`.
- Logs carry `operation` / `status` (`latency_ms` where timed), and **never message content or phone numbers** (`.claude/rules/logging-observability.md`).
- Runtime schema `agent_core/src/schema/config.py` may import only `pydantic`, `enum`, `typing`, `__future__`.
- Every schema change is mirrored in the same task (`.claude/rules/runtime-devkit-sync.md`).
- Google-style docstrings on every public class, method and function. Base-class signatures and their implementations change together.
- Config defaults (verbatim from the spec): `on_new_input: abort_and_fold`, `on_disconnect: abort`, `drain_max_ms: 3000`, `fold.max_segments: 3`, `carryover.max_age_ms: 60000`, `session_idle_ttl_ms: 1800000`.
- No `task.cancel()` on a turn's `invocation_task` anywhere after Task 6.

## Review Focus

1. **The client closes right after reading `DoneEvent`.** A completed turn must never be treated as interrupted and write a stray carry-over. Pinned in Task 8 (`test_disconnect_after_done_is_not_an_interrupt`).
2. **A caller rings back hours later.** Their old call's last utterance must not be folded into the new call's first turn. Pinned in Task 5 (`test_stale_carryover_is_discarded_and_cleared`).
3. **The turn is interrupted before its step-1 memory read finished.** A carry-over already in Memory Layer must not be overwritten and lost. Pinned in Task 4 (`test_interrupt_before_fold_appends_to_existing_carryover`).
4. **Malformed `turn_carryover`** (a string, a list, non-string segments, from a bad write or an older build) must not crash the turn. Pinned in Task 5 (`test_malformed_carryover_is_ignored`).
5. **A burst of check-ins while turns keep being interrupted.** The fold must stay capped, and the substantive utterance must survive while it is within the cap. Pinned in Task 10 (`test_checkin_cascade_keeps_newest_segments`).

---

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `agent_core/src/schema/config.py` | Runtime schema: `InterruptionConfig`, `FoldConfig`, `CarryoverConfig`, extended `TurnAssemblerConfig` | 1 |
| `agent_core/src/turn_policy.py` (new) | `TurnPolicy` dataclass + `resolve_turn_policy()` / `resolve_session_idle_ttl_ms()`: the one place config is merged | 1 |
| `dev-kit/dev_kit/schemas/domain/agent_core.py`, `dev-kit/dev_kit/schemas/dpg/agent_core.py`, `dev-kit/dev_kit/schema.py`, `dev-kit/dpg/agent_core.yaml` | Dev-kit mirrors + framework defaults | 1 |
| `agent_core/src/models.py` | `TurnRecord`; `DoneEvent.interrupted_at_stage`; `SegmentInput.fresh` | 2 |
| `agent_core/src/base.py` | `AgentCoreBase.stream_turn` gains `record` | 2 |
| `agent_core/src/orchestrator.py` | `stream_turn` wrapper → `_stream_turn_impl`; capture-first; `_persist_interrupted`; `_fold_carryover`; undelivered replay note | 3, 4, 5 |
| `agent_core/src/turn.py`, `agent_core/src/session.py` | `Turn.record`, `Turn.predecessor`; `Session.last_activity_ms`, `Session.subscribers` | 6, 7 |
| `agent_core/src/turn_assembler.py` | Cooperative `_interrupt`, draining `_invoke`, `_await_predecessor`, `submit` / `attach` / `detach`, eviction | 6, 7 |
| `agent_core/src/servers/orchestration_server.py` | `/stream_turn` via assembler (direct fallback when none) | 8 |
| `reach_layer/bridge/src/server.py`, `reach_layer/bridge/src/agent_core_client.py`, `reach_layer/bridge/main.py` | Remove the cancel path | 9 |
| `agent_core/tests/test_turn_carryover_replay.py` (new) | 24 Sep replay scenarios, real AgentCore + real TurnAssembler | 10 |
| `ARCHITECTURE.md`, `CLAUDE.md` | Document the new turn lifecycle | 10 |

---

### Task 1: Turn policy config (runtime schema, resolver, dev-kit sync)

**Files:**
- Modify: `agent_core/src/schema/config.py` (after `MaxWaitCeilingConfig`, and inside `TurnAssemblerConfig`)
- Create: `agent_core/src/turn_policy.py`
- Modify: `dev-kit/dev_kit/schemas/domain/agent_core.py` (after `MaxWaitCeilingConfig`, inside `TurnAssemblerConfig`)
- Modify: `dev-kit/dev_kit/schemas/dpg/agent_core.py` (`TurnAssemblerDpg`)
- Modify: `dev-kit/dev_kit/schema.py` (`ChannelTurnAssemblerConfig`)
- Modify: `dev-kit/dpg/agent_core.yaml` (`reach_layer.turn_assembler`)
- Test: `agent_core/tests/test_turn_policy.py` (new), `dev-kit/tests/schemas/domain/test_agent_core.py`

**Interfaces:**
- Produces:
  - `TurnPolicy` (frozen dataclass) with fields `on_new_input: str`, `on_disconnect: str`, `drain_max_ms: int`, `fold_max_segments: int`, `carryover_max_age_ms: int`, `undelivered_note: str`
  - `resolve_turn_policy(config: dict, channel: str | None) -> TurnPolicy`
  - `resolve_session_idle_ttl_ms(config: dict) -> int`
  - module constants `ON_NEW_INPUT_ABORT_AND_FOLD = "abort_and_fold"`, `ON_NEW_INPUT_REPLACE = "replace"`, `ON_DISCONNECT_ABORT = "abort"`, `ON_DISCONNECT_CONTINUE = "continue"`

- [ ] **Step 1: Write the failing resolver tests**

Create `agent_core/tests/test_turn_policy.py`:

```python
"""Tests for turn_policy: merging of turn-lifecycle config (Agent Core block)."""

import logging

from src.turn_policy import (
    ON_DISCONNECT_ABORT,
    ON_DISCONNECT_CONTINUE,
    ON_NEW_INPUT_ABORT_AND_FOLD,
    ON_NEW_INPUT_REPLACE,
    TurnPolicy,
    resolve_session_idle_ttl_ms,
    resolve_turn_policy,
)


def _cfg(default_ta=None, channels=None):
    return {
        "reach_layer": {"turn_assembler": default_ta or {}},
        "channels": channels or {},
    }


class TestResolveTurnPolicy:

    def test_defaults_when_nothing_configured(self):
        policy = resolve_turn_policy({}, "bridge")
        assert policy == TurnPolicy()
        assert policy.on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
        assert policy.on_disconnect == ON_DISCONNECT_ABORT
        assert policy.drain_max_ms == 3000
        assert policy.fold_max_segments == 3
        assert policy.carryover_max_age_ms == 60000
        assert policy.undelivered_note == ""

    def test_reach_layer_default_applies(self):
        cfg = _cfg(default_ta={
            "interruption": {"drain_max_ms": 500},
            "carryover": {"undelivered_note": "NOTE"},
        })
        policy = resolve_turn_policy(cfg, "voice")
        assert policy.drain_max_ms == 500
        assert policy.undelivered_note == "NOTE"

    def test_channel_override_merges_per_subsection(self):
        cfg = _cfg(
            default_ta={"interruption": {"drain_max_ms": 500, "on_disconnect": "abort"}},
            channels={"bridge": {"turn_assembler": {
                "interruption": {"on_disconnect": "continue"},
                "fold": {"max_segments": 5},
            }}},
        )
        policy = resolve_turn_policy(cfg, "bridge")
        assert policy.on_disconnect == ON_DISCONNECT_CONTINUE
        assert policy.drain_max_ms == 500          # inherited, not reset
        assert policy.fold_max_segments == 5

    def test_unknown_channel_uses_defaults(self):
        cfg = _cfg(channels={"voice": {"turn_assembler": {"fold": {"max_segments": 9}}}})
        assert resolve_turn_policy(cfg, "nope").fold_max_segments == 3
        assert resolve_turn_policy(cfg, None).fold_max_segments == 3

    def test_invalid_enum_falls_back_with_warning(self, caplog):
        cfg = _cfg(default_ta={"interruption": {"on_new_input": "explode"}})
        with caplog.at_level(logging.WARNING):
            policy = resolve_turn_policy(cfg, "voice")
        assert policy.on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
        assert any("turn_policy.invalid_value" in r.message for r in caplog.records)

    def test_negative_numbers_fall_back(self):
        cfg = _cfg(default_ta={"fold": {"max_segments": -1},
                               "interruption": {"drain_max_ms": "x"}})
        policy = resolve_turn_policy(cfg, "voice")
        assert policy.fold_max_segments == 3
        assert policy.drain_max_ms == 3000

    def test_replace_is_accepted(self):
        cfg = _cfg(default_ta={"interruption": {"on_new_input": "replace"}})
        assert resolve_turn_policy(cfg, "voice").on_new_input == ON_NEW_INPUT_REPLACE

    def test_non_dict_sections_ignored(self):
        cfg = {"reach_layer": {"turn_assembler": {"fold": "bad"}}, "channels": {"voice": None}}
        assert resolve_turn_policy(cfg, "voice") == TurnPolicy()


class TestSessionIdleTtl:

    def test_default(self):
        assert resolve_session_idle_ttl_ms({}) == 1800000

    def test_configured(self):
        assert resolve_session_idle_ttl_ms(_cfg(default_ta={"session_idle_ttl_ms": 1000})) == 1000

    def test_invalid_falls_back(self):
        assert resolve_session_idle_ttl_ms(_cfg(default_ta={"session_idle_ttl_ms": -5})) == 1800000
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_turn_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.turn_policy'`

- [ ] **Step 3: Implement `agent_core/src/turn_policy.py`**

```python
"""agent_core/turn_policy.py

Turn-lifecycle policy for streaming turns (Agent Core block).

Resolves the ``interruption`` / ``fold`` / ``carryover`` sections of
``turn_assembler`` config into one immutable ``TurnPolicy`` per channel. Used by
both the TurnAssembler (when to stop a turn) and ``stream_turn`` (what an
interrupted turn leaves for its successor), so the two can never disagree about
a default.

Resolution order, merged per sub-section: built-in defaults, then
``reach_layer.turn_assembler``, then ``channels.<name>.turn_assembler``.
Invalid values fall back to the default with a warning. Boot-time schema
validation rejects them first; this is the runtime backstop.

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

ON_NEW_INPUT_ABORT_AND_FOLD = "abort_and_fold"
ON_NEW_INPUT_REPLACE = "replace"
ON_DISCONNECT_ABORT = "abort"
ON_DISCONNECT_CONTINUE = "continue"

_ON_NEW_INPUT_VALUES = (ON_NEW_INPUT_ABORT_AND_FOLD, ON_NEW_INPUT_REPLACE)
_ON_DISCONNECT_VALUES = (ON_DISCONNECT_ABORT, ON_DISCONNECT_CONTINUE)
_DEFAULT_SESSION_IDLE_TTL_MS = 1_800_000


@dataclass(frozen=True)
class TurnPolicy:
    """Resolved interruption, fold and carry-over settings for one channel.

    Attributes:
        on_new_input: ``abort_and_fold`` or ``replace`` — what a new input does
            to a turn still in flight.
        on_disconnect: ``abort`` or ``continue`` — what a request-scoped
            client closing its connection does to its turn.
        drain_max_ms: How long a successor waits for its predecessor to reach
            a safe point and persist.
        fold_max_segments: Newest utterances kept when folding; 0 disables.
        carryover_max_age_ms: Carry-over older than this is discarded.
        undelivered_note: Appended to replayed tool results the user has not
            heard. Empty disables the annotation.
    """

    on_new_input: str = ON_NEW_INPUT_ABORT_AND_FOLD
    on_disconnect: str = ON_DISCONNECT_ABORT
    drain_max_ms: int = 3000
    fold_max_segments: int = 3
    carryover_max_age_ms: int = 60000
    undelivered_note: str = ""


def _section(block: Any, name: str) -> dict:
    """Return ``block[name]`` when both are dicts, else an empty dict."""
    if not isinstance(block, dict):
        return {}
    value = block.get(name)
    return value if isinstance(value, dict) else {}


def _merged(config: dict, channel: Optional[str], name: str) -> dict:
    """Merge one sub-section: reach_layer default, then channel override."""
    default_ta = _section(_section(config, "reach_layer"), "turn_assembler")
    merged = dict(_section(default_ta, name))
    if channel:
        channel_block = _section(_section(config, "channels"), channel)
        merged.update(_section(_section(channel_block, "turn_assembler"), name))
    return merged


def _choice(value: Any, allowed: tuple[str, ...], default: str, field: str) -> str:
    """Return ``value`` if allowed, else ``default`` with a warning."""
    if value is None:
        return default
    if value in allowed:
        return value
    logger.warning(
        "turn_policy.invalid_value",
        extra={"operation": "turn_policy.resolve", "status": "failure",
               "field": field, "error": f"not one of {allowed}"},
    )
    return default


def _non_negative_int(value: Any, default: int, field: str) -> int:
    """Return ``value`` if it is a non-negative int, else ``default``."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    logger.warning(
        "turn_policy.invalid_value",
        extra={"operation": "turn_policy.resolve", "status": "failure",
               "field": field, "error": "expected a non-negative integer"},
    )
    return default


def resolve_turn_policy(config: dict, channel: Optional[str]) -> TurnPolicy:
    """Resolve the turn-lifecycle policy for ``channel``.

    Args:
        config: Full merged agent_core config dict.
        channel: Channel name, or None when unknown (defaults apply).

    Returns:
        The resolved, immutable TurnPolicy.
    """
    config = config if isinstance(config, dict) else {}
    base = TurnPolicy()
    interruption = _merged(config, channel, "interruption")
    fold = _merged(config, channel, "fold")
    carryover = _merged(config, channel, "carryover")
    note = carryover.get("undelivered_note", base.undelivered_note)
    return TurnPolicy(
        on_new_input=_choice(interruption.get("on_new_input"), _ON_NEW_INPUT_VALUES,
                             base.on_new_input, "interruption.on_new_input"),
        on_disconnect=_choice(interruption.get("on_disconnect"), _ON_DISCONNECT_VALUES,
                              base.on_disconnect, "interruption.on_disconnect"),
        drain_max_ms=_non_negative_int(interruption.get("drain_max_ms"),
                                       base.drain_max_ms, "interruption.drain_max_ms"),
        fold_max_segments=_non_negative_int(fold.get("max_segments"),
                                            base.fold_max_segments, "fold.max_segments"),
        carryover_max_age_ms=_non_negative_int(carryover.get("max_age_ms"),
                                               base.carryover_max_age_ms,
                                               "carryover.max_age_ms"),
        undelivered_note=note if isinstance(note, str) else base.undelivered_note,
    )


def resolve_session_idle_ttl_ms(config: dict) -> int:
    """Return ``reach_layer.turn_assembler.session_idle_ttl_ms`` or its default.

    Args:
        config: Full merged agent_core config dict.

    Returns:
        Idle time in ms after which an idle in-process session is evicted.
    """
    config = config if isinstance(config, dict) else {}
    default_ta = _section(_section(config, "reach_layer"), "turn_assembler")
    return _non_negative_int(default_ta.get("session_idle_ttl_ms"),
                             _DEFAULT_SESSION_IDLE_TTL_MS, "session_idle_ttl_ms")
```

- [ ] **Step 4: Run to verify the resolver passes**

Run: `cd agent_core && uv run pytest tests/test_turn_policy.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Add the runtime schema classes and a failing schema test**

Append to `agent_core/tests/test_turn_policy.py`:

```python
import pytest
from pydantic import ValidationError

from src.schema.config import TurnAssemblerConfig


class TestRuntimeSchema:

    def test_defaults(self):
        ta = TurnAssemblerConfig()
        assert ta.interruption.on_new_input == "abort_and_fold"
        assert ta.interruption.on_disconnect == "abort"
        assert ta.interruption.drain_max_ms == 3000
        assert ta.fold.max_segments == 3
        assert ta.carryover.max_age_ms == 60000
        assert ta.carryover.undelivered_note == ""
        assert ta.session_idle_ttl_ms == 1800000

    def test_accepts_valid(self):
        ta = TurnAssemblerConfig.model_validate({
            "interruption": {"on_new_input": "replace", "on_disconnect": "continue",
                             "drain_max_ms": 0},
            "fold": {"max_segments": 0},
            "carryover": {"max_age_ms": 1, "undelivered_note": "x"},
            "session_idle_ttl_ms": 5,
        })
        assert ta.interruption.on_new_input == "replace"

    @pytest.mark.parametrize("payload", [
        {"interruption": {"on_new_input": "explode"}},
        {"interruption": {"on_disconnect": "maybe"}},
        {"interruption": {"drain_max_ms": -1}},
        {"fold": {"max_segments": -1}},
        {"carryover": {"max_age_ms": -1}},
        {"carryover": {"enabled": True}},          # removed from the design
        {"session_idle_ttl_ms": -1},
    ])
    def test_rejects_invalid(self, payload):
        with pytest.raises(ValidationError):
            TurnAssemblerConfig.model_validate(payload)
```

Run: `cd agent_core && uv run pytest tests/test_turn_policy.py::TestRuntimeSchema -v`
Expected: FAIL — `AttributeError: 'TurnAssemblerConfig' object has no attribute 'interruption'`

- [ ] **Step 6: Implement the runtime schema**

In `agent_core/src/schema/config.py`, make sure `Literal` is imported from `typing` (add it to the existing `typing` import line). Then insert after `class MaxWaitCeilingConfig`:

```python
class InterruptionConfig(BaseModel):
    """What stops an in-flight streaming turn, and how long a successor waits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_new_input: Literal["abort_and_fold", "replace"] = "abort_and_fold"
    on_disconnect: Literal["abort", "continue"] = "abort"
    drain_max_ms: int = Field(default=3000, ge=0)


class FoldConfig(BaseModel):
    """How many interrupted utterances a successor turn folds into its input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_segments: int = Field(default=3, ge=0)


class CarryoverConfig(BaseModel):
    """Lifetime of carried state and the note marking unheard tool results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_age_ms: int = Field(default=60000, ge=0)
    undelivered_note: str = ""
```

Replace the body of `class TurnAssemblerConfig` with:

```python
class TurnAssemblerConfig(BaseModel):
    """Turn-assembler policy stack and streaming-turn lifecycle.

    ``session_idle_ttl_ms`` is read from ``reach_layer.turn_assembler`` only;
    a per-channel value is accepted but has no effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_gate: SemanticGateConfig = Field(default_factory=SemanticGateConfig)
    silence_trigger: SilenceTriggerConfig = Field(default_factory=SilenceTriggerConfig)
    max_wait_ceiling: MaxWaitCeilingConfig = Field(default_factory=MaxWaitCeilingConfig)
    interruption: InterruptionConfig = Field(default_factory=InterruptionConfig)
    fold: FoldConfig = Field(default_factory=FoldConfig)
    carryover: CarryoverConfig = Field(default_factory=CarryoverConfig)
    session_idle_ttl_ms: int = Field(default=1_800_000, ge=0)
```

Run: `cd agent_core && uv run pytest tests/test_turn_policy.py -v`
Expected: PASS (all)

- [ ] **Step 7: Dev-kit mirrors — failing tests first**

Append to `dev-kit/tests/schemas/domain/test_agent_core.py`:

```python
class TestTurnAssemblerLifecycleMirror:
    """Mirror of runtime InterruptionConfig / FoldConfig / CarryoverConfig."""

    def test_accepts_valid(self):
        ta = TurnAssemblerConfig.model_validate({
            "interruption": {"on_new_input": "replace", "on_disconnect": "continue",
                             "drain_max_ms": 100},
            "fold": {"max_segments": 2},
            "carryover": {"max_age_ms": 10, "undelivered_note": "n"},
            "session_idle_ttl_ms": 1000,
        })
        assert ta.fold.max_segments == 2

    @pytest.mark.parametrize("payload", [
        {"interruption": {"on_new_input": "explode"}},
        {"fold": {"max_segments": -1}},
        {"carryover": {"enabled": True}},
    ])
    def test_rejects_invalid(self, payload):
        with pytest.raises(ValidationError):
            TurnAssemblerConfig.model_validate(payload)
```

Run: `cd dev-kit && uv run pytest tests/schemas/domain/test_agent_core.py -k Lifecycle -v`
Expected: FAIL (`extra_forbidden` on `interruption`)

- [ ] **Step 8: Implement the dev-kit mirrors and defaults**

In `dev-kit/dev_kit/schemas/domain/agent_core.py`, make sure `Literal` is imported from `typing`. Insert after `class MaxWaitCeilingConfig` and extend `TurnAssemblerConfig`:

```python
class InterruptionConfig(BaseModel):
    """Mirrors runtime InterruptionConfig 1:1."""
    model_config = ConfigDict(extra="forbid")
    on_new_input: Literal["abort_and_fold", "replace"] = "abort_and_fold"
    on_disconnect: Literal["abort", "continue"] = "abort"
    drain_max_ms: int = Field(default=3000, ge=0)


class FoldConfig(BaseModel):
    """Mirrors runtime FoldConfig 1:1."""
    model_config = ConfigDict(extra="forbid")
    max_segments: int = Field(default=3, ge=0)


class CarryoverConfig(BaseModel):
    """Mirrors runtime CarryoverConfig 1:1."""
    model_config = ConfigDict(extra="forbid")
    max_age_ms: int = Field(default=60000, ge=0)
    undelivered_note: str = ""
```

and add inside `class TurnAssemblerConfig` (after `max_wait_ceiling`):

```python
    interruption: InterruptionConfig = Field(default_factory=InterruptionConfig)
    fold: FoldConfig = Field(default_factory=FoldConfig)
    carryover: CarryoverConfig = Field(default_factory=CarryoverConfig)
    session_idle_ttl_ms: int = Field(default=1_800_000, ge=0)
```

In `dev-kit/dev_kit/schemas/dpg/agent_core.py`, add to `class TurnAssemblerDpg` (keeping its existing `dict` style):

```python
    interruption: dict = Field(default_factory=lambda: {
        "on_new_input": "abort_and_fold", "on_disconnect": "abort", "drain_max_ms": 3000})
    fold: dict = Field(default_factory=lambda: {"max_segments": 3})
    carryover: dict = Field(default_factory=lambda: {"max_age_ms": 60000, "undelivered_note": ""})
    session_idle_ttl_ms: int = 1_800_000
```

In `dev-kit/dev_kit/schema.py`, add to `class ChannelTurnAssemblerConfig`:

```python
    interruption: dict[str, Any] = Field(
        default_factory=lambda: {"on_new_input": "abort_and_fold",
                                 "on_disconnect": "abort", "drain_max_ms": 3000},
        description="What stops an in-flight streaming turn",
    )
    fold: dict[str, Any] = Field(
        default_factory=lambda: {"max_segments": 3},
        description="Interrupted utterances folded into the next turn",
    )
    carryover: dict[str, Any] = Field(
        default_factory=lambda: {"max_age_ms": 60000, "undelivered_note": ""},
        description="Carry-over lifetime and the unheard-result note",
    )
    session_idle_ttl_ms: int = Field(
        default=1_800_000, description="Idle in-process session eviction (reach_layer level)",
    )
```

In `dev-kit/dpg/agent_core.yaml`, under `reach_layer.turn_assembler`, after `max_wait_ceiling`:

```yaml
    # Streaming-turn lifecycle (spec 2026-09-29). Applies to /stream_turn and the
    # session endpoints; /process_turn is unaffected.
    interruption:
      on_new_input: abort_and_fold   # abort_and_fold | replace
      on_disconnect: abort           # abort | continue
      drain_max_ms: 3000             # successor waits this long for its predecessor
    fold:
      max_segments: 3                # newest interrupted utterances kept; 0 disables
    carryover:
      max_age_ms: 60000              # older carry-over is discarded, not folded
      undelivered_note: "[The user has not yet heard the outcome of this action. Tell them the result before moving on.]"
    session_idle_ttl_ms: 1800000     # idle in-process session eviction
```

- [ ] **Step 9: Run the dev-kit and agent_core tests**

Run: `cd dev-kit && uv run pytest tests/schemas -q` then `cd ../agent_core && uv run pytest -q`
Expected: dev-kit schemas all PASS; agent_core 934 + the new tests passed, 1 skipped

- [ ] **Step 10: Commit**

```bash
git add agent_core/src/turn_policy.py agent_core/src/schema/config.py agent_core/tests/test_turn_policy.py \
  dev-kit/dev_kit/schemas/domain/agent_core.py dev-kit/dev_kit/schemas/dpg/agent_core.py \
  dev-kit/dev_kit/schema.py dev-kit/dpg/agent_core.yaml dev-kit/tests/schemas/domain/test_agent_core.py
git commit -m "feat(agent_core): turn-lifecycle policy config (interruption, fold, carryover)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `TurnRecord`, `DoneEvent.interrupted_at_stage`, `SegmentInput.fresh`, base signature

**Files:**
- Modify: `agent_core/src/models.py` (`SegmentInput`, `DoneEvent`, new `TurnRecord` after `DoneEvent`)
- Modify: `agent_core/src/base.py:43-76` (`stream_turn` signature + docstring)
- Test: `agent_core/tests/test_turn_record.py` (new)

**Interfaces:**
- Produces:
  - `TurnRecord` dataclass with fields:
    - `captured_exchanges: list[dict]`
    - `prior_exchanges: list[dict]`
    - `max_items: int = 0`
    - `segments: list[str]`
    - `fold_ran: bool = False`
    - `last_stage: str = ""`
    - `write_carryover: bool = True`
    - `persist_task: Optional[asyncio.Task] = None`
  - `DoneEvent.interrupted_at_stage: Optional[str] = None`
  - `SegmentInput.fresh: bool = False`
  - `AgentCoreBase.stream_turn(turn_input, *, abort_event=None, turn_id="", record: TurnRecord | None = None)`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_turn_record.py`:

```python
"""Tests for TurnRecord and the streaming-lifecycle model fields (Agent Core block)."""

import json

from src.models import DoneEvent, SegmentInput, TurnRecord


def test_turn_record_defaults_are_independent():
    a, b = TurnRecord(), TurnRecord()
    a.captured_exchanges.append({"x": 1})
    a.segments.append("hi")
    assert b.captured_exchanges == [] and b.segments == []
    assert a.max_items == 0 and a.fold_ran is False
    assert a.last_stage == "" and a.write_carryover is True
    assert a.persist_task is None


def test_done_event_interrupted_at_stage_serialises():
    ev = DoneEvent(turn_status="interrupted", interrupted_at_stage="tool_end")
    payload = json.loads(ev.to_sse()[len("data: "):])
    assert payload["interrupted_at_stage"] == "tool_end"
    assert json.loads(DoneEvent().to_sse()[len("data: "):])["interrupted_at_stage"] is None


def test_segment_input_fresh_defaults_false():
    assert SegmentInput(text="hi").fresh is False
    assert SegmentInput(text="hi", fresh=True).fresh is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_turn_record.py -v`
Expected: FAIL — `ImportError: cannot import name 'TurnRecord'`

- [ ] **Step 3: Implement**

In `agent_core/src/models.py`:
- `import asyncio` at the top, if absent.
- In `class SegmentInput`, after `metadata`, add:

```python
    fresh: bool = False  # request adapter: caller wants a clean session (no adoption)
```

- In `class DoneEvent`, after `error_message`, add:

```python
    interrupted_at_stage: Optional[str] = None  # last SignalEvent stage of an interrupted turn
```

- After `class DoneEvent` (before `StreamEvent = ...`), add:

```python
@dataclass
class TurnRecord:
    """Mutable per-turn ledger shared by the TurnAssembler and ``stream_turn``.

    The TurnAssembler creates one per Turn and reads it after an interruption.
    ``stream_turn`` fills it as the turn runs, so what an interrupted turn did is
    known without waiting for its end-of-turn memory write.

    Attributes:
        captured_exchanges: Tool rounds completed this turn (#193 shape).
        prior_exchanges: ``recent_tool_exchanges`` as read at turn start.
        max_items: The ``recent_tool_exchanges`` cap in force.
        segments: User utterances this turn answers, after folding.
        fold_ran: True once the carry-over fold has run for this turn.
        last_stage: Stage of the last SignalEvent emitted.
        write_carryover: False when policy says an interruption must not carry
            the utterances forward (``on_new_input: replace``).
        persist_task: Background task persisting an interrupted turn, if any.
    """

    captured_exchanges: list[dict] = field(default_factory=list)
    prior_exchanges: list[dict] = field(default_factory=list)
    max_items: int = 0
    segments: list[str] = field(default_factory=list)
    fold_ran: bool = False
    last_stage: str = ""
    write_carryover: bool = True
    persist_task: Optional["asyncio.Task"] = None
```

In `agent_core/src/base.py`, import `TurnRecord` next to the existing `src.models` imports. Change the abstract signature to:

```python
    @abstractmethod
    async def stream_turn(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
        record: "TurnRecord | None" = None,
    ) -> AsyncGenerator[StreamEvent, None]:
```

Add to its docstring `Args:`:

```
            record: Optional per-turn ledger. When supplied (the TurnAssembler
                does), stream_turn records the stage reached, the tool rounds
                completed and the folded utterances in it, and sets
                ``record.persist_task`` when the turn ends without completing.
                When None, a private record is used.
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd agent_core && uv run pytest tests/test_turn_record.py -v && uv run pytest -q`
Expected: 3 PASS. The full suite still passes, because the orchestrator's signature is unchanged until Task 3 and the ABC default keeps callers working.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/models.py agent_core/src/base.py agent_core/tests/test_turn_record.py
git commit -m "feat(agent_core): TurnRecord ledger, DoneEvent.interrupted_at_stage, SegmentInput.fresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `stream_turn` wrapper, stage tracking, capture-first

**Files:**
- Modify: `agent_core/src/orchestrator.py`
  - `stream_turn` at `:2834`: rename the method to `_stream_turn_impl`, add the new wrapper above it
  - replay setup at `:3596-3603`
  - first tool round at `:3747-3816`
  - loop-start capture at `:3856-3863`
  - nested round at `:3929-3975`
- Test: `agent_core/tests/test_stream_turn_lifecycle.py` (new)

**Interfaces:**
- Consumes: `TurnRecord` (Task 2).
- Produces:
  - `AgentCore.stream_turn(turn_input, *, abort_event=None, turn_id="", record=None)` is the public wrapper
  - `AgentCore._stream_turn_impl(turn_input, *, abort_event, turn_id, record)` is the old body
  - `record.captured_exchanges` holds one entry per completed tool round, appended before that round's `tool_end` is yielded
  - `record.last_stage` tracks the latest `SignalEvent.stage`
  - `record.prior_exchanges` and `record.max_items` are set at replay setup

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_stream_turn_lifecycle.py`:

```python
"""Lifecycle tests for AgentCore.stream_turn: record, capture-first, persistence, fold.

Belongs to the Agent Core block. Spec:
docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.5-4.6
"""

import asyncio

import pytest

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolUseBlock
from src.models import DoneEvent, NLUResult, SignalEvent, ToolResult, TurnRecord

from tests.test_stream_turn import _make_agent_core, _make_turn_input


def _tool_agent(rounds: int = 1):
    """AgentCore whose LLM requests ``rounds`` tool rounds, then answers."""
    agent = _make_agent_core()
    calls = {"n": 0}

    async def mock_stream(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= rounds:
            yield "Checking. "
            raise ChatToolUseRequested([ToolUseBlock(
                tool_name=f"tool_{calls['n']}",
                tool_use_id=f"tu_{calls['n']}",
                input={"q": calls["n"]},
            )])
        yield "Done. "

    agent._llm.stream = mock_stream
    agent._async_gateway.execute.side_effect = lambda tc, *a, **k: ToolResult(
        tool_use_id=tc.tool_use_id, tool_name=tc.tool_name,
        result={"ok": True}, success=True, result_text=f"result-{tc.tool_use_id}",
    )
    agent._language_normaliser = type("N", (), {"normalise": lambda self, *a, **k: ("msg", "english")})()
    agent._nlu_processor = type("P", (), {"process": lambda self, *a, **k: NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9)})()
    agent._tool_registry.get_route.return_value = None
    return agent


async def _run(agent, record, abort_after_tool_end: int | None = None):
    """Consume stream_turn; set abort once the Nth tool_end is seen."""
    abort = asyncio.Event()
    events, tool_ends = [], 0
    async for ev in agent.stream_turn(_make_turn_input(), abort_event=abort, record=record):
        events.append(ev)
        if isinstance(ev, SignalEvent) and ev.stage == "tool_end":
            tool_ends += 1
            if abort_after_tool_end is not None and tool_ends == abort_after_tool_end:
                abort.set()
    return events


class TestRecordAndCapture:

    async def test_completed_tool_turn_captures_each_round_once(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        events = await _run(agent, record)
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        names = [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges]
        assert names == ["tool_1", "tool_2"]          # no double capture
        assert record.last_stage == "memory_write"

    async def test_abort_after_first_round_still_captures_it(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        events = await _run(agent, record, abort_after_tool_end=1)
        assert not any(isinstance(e, DoneEvent) for e in events)
        assert [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges] == ["tool_1"]
        assert record.last_stage == "tool_end"

    async def test_abort_after_nested_round_still_captures_it(self):
        agent = _tool_agent(rounds=3)
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=2)
        names = [ex["tool_uses"][0]["name"] for ex in record.captured_exchanges]
        assert names == ["tool_1", "tool_2"]

    async def test_prior_exchanges_and_cap_recorded(self):
        agent = _tool_agent(rounds=1)
        prior = [{"tool_uses": [{"type": "tool_use", "id": "p", "name": "old", "input": {}}],
                  "tool_results": [{"type": "tool_result", "tool_use_id": "p", "content": "c"}]}]
        agent._async_memory.context_bundle.return_value.session["recent_tool_exchanges"] = prior
        record = TurnRecord()
        await _run(agent, record)
        assert record.prior_exchanges == prior
        assert record.max_items > 0

    async def test_stream_turn_without_record_still_works(self):
        agent = _tool_agent(rounds=1)
        events = [e async for e in agent.stream_turn(_make_turn_input())]
        assert isinstance(events[-1], DoneEvent)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py -v`
Expected: FAIL — `TypeError: stream_turn() got an unexpected keyword argument 'record'`

- [ ] **Step 3: Rename the body and add the wrapper**

In `agent_core/src/orchestrator.py`, add `TurnRecord` to the `from src.models import (...)` / `from .models import (...)` block, whichever the file uses. Rename the existing `async def stream_turn(` at `:2834` to `async def _stream_turn_impl(`. Change its signature to require the record, and replace its docstring's first line:

```python
    async def _stream_turn_impl(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
        record: TurnRecord,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run the streaming pipeline body; see :meth:`stream_turn` for the contract.
```

Insert the new public method directly above it:

```python
    async def stream_turn(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
        record: "TurnRecord | None" = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute one conversation turn with streaming SSE output.

        Wraps the pipeline body to keep the turn's ledger: the last stage
        reached, and whether the turn completed. A turn that ends without a
        completed DoneEvent (aborted, errored, or closed early) has its state
        persisted for the successor turn (spec §4.5) from ``finally``, via a
        background task, never awaited here.

        Args:
            turn_input: Normalised inbound message from the Reach Layer.
            abort_event: When set, the turn stops at its next safe point.
            turn_id: Identifier stamped on every event; uuid4 when empty.
            record: Per-turn ledger; a private one is used when None.

        Yields:
            SignalEvent, SentenceEvent, or DoneEvent.
        """
        record = record if record is not None else TurnRecord()
        completed = False
        try:
            async for event in self._stream_turn_impl(
                turn_input, abort_event=abort_event, turn_id=turn_id, record=record,
            ):
                if isinstance(event, SignalEvent) and event.stage:
                    record.last_stage = event.stage
                elif isinstance(event, DoneEvent) and event.turn_status == "completed":
                    completed = True
                yield event
        finally:
            if not completed:
                self._on_turn_not_completed(turn_input, record, turn_id)

    def _on_turn_not_completed(
        self, turn_input: TurnInput, record: TurnRecord, turn_id: str = "",
    ) -> None:
        """Hook for a turn that ended without completing; filled in by Task 4."""
        return None
```

- [ ] **Step 4: Record prior exchanges and share the capture list**

At `:3596-3603` replace

```python
            # Tool exchanges captured during *this* turn's tool rounds; persisted
            # at the end of the turn so the next turn can replay them.
            _captured_exchanges_this_turn: list[dict] = []
```

with

```python
            # Tool exchanges captured during *this* turn's tool rounds; persisted
            # at the end of the turn so the next turn can replay them. The list
            # lives on the record so an interrupted turn's rounds survive it.
            record.prior_exchanges = list(_prior_exchanges)
            record.max_items = _max_items
            _captured_exchanges_this_turn: list[dict] = record.captured_exchanges
```

- [ ] **Step 5: Capture the first round before its `tool_end`**

In the first-round block, find these lines (around `:3814-3816`):

```python
                yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                if _aborted():
                    return
```

and replace them with:

```python
                # Capture before yielding tool_end: a turn stopped at this yield
                # must still record the round it just completed (spec §4.5).
                _ex = self._capture_tool_exchange(
                    all_tool_calls, tool_results_for_llm, _max_chars,
                )
                if _ex is not None:
                    _captured_exchanges_this_turn.append(_ex)
                yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                if _aborted():
                    return
```

- [ ] **Step 6: Remove the loop-start capture**

At `:3856-3863` delete:

```python
                    # #193: snapshot this round so it can be replayed next turn.
                    _ex = self._capture_tool_exchange(
                        _current_tool_calls, _current_tool_results, _max_chars,
                    )
                    if _ex is not None:
                        _captured_exchanges_this_turn.append(_ex)
```

(keep the `if _aborted(): return` that follows it).

- [ ] **Step 7: Capture the nested round before its `tool_end`**

In the nested block (around `:3972-3974`), replace:

```python
                        yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                        if _aborted():
                            return
```

with:

```python
                        _ex = self._capture_tool_exchange(
                            _nested_tool_calls, _nested_results, _max_chars,
                        )
                        if _ex is not None:
                            _captured_exchanges_this_turn.append(_ex)
                        yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                        if _aborted():
                            return
```

- [ ] **Step 8: Run the tests**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py tests/test_stream_turn.py tests/test_trust_output_batching.py tests/test_turn_path_identity_parity.py -v`
Expected: all PASS. The existing `TestStreamTurnRecentToolExchanges` tests still see one exchange per round, because the capture only moved.

- [ ] **Step 9: Full suite and commit**

Run: `cd agent_core && uv run pytest -q` (expected: all pass)

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_stream_turn_lifecycle.py
git commit -m "feat(agent_core): stream_turn keeps a TurnRecord; capture each tool round before tool_end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Persist an interrupted turn (`recent_tool_exchanges` + `turn_carryover`)

**Files:**
- Modify: `agent_core/src/orchestrator.py`: replace `_on_turn_not_completed`; add `_persist_interrupted`; add `_turn_policy(channel)` with a cache initialised in `__init__`
- Test: `agent_core/tests/test_stream_turn_lifecycle.py`

**Interfaces:**
- Consumes: `resolve_turn_policy` (Task 1), `TurnRecord` (Task 2), the record fields (Task 3).
- Produces:
  - `AgentCore._turn_policy(channel: str | None) -> TurnPolicy` (cached per channel)
  - `AgentCore._persist_interrupted(session_id, user_id, record, exchanges, carry) -> None` (async, never raises)
  - The `turn_carryover` value shape: `{"segments": list[str], "stopped_at_stage": str, "turn_id": str, "written_at_ms": int}`
  - Exchanges persisted on interrupt carry `"delivered": False`

- [ ] **Step 1: Write the failing tests**

Append to `agent_core/tests/test_stream_turn_lifecycle.py`:

```python
def _writes(agent, key):
    return [c.args[4] for c in agent._async_memory.write.await_args_list if c.args[3] == key]


class TestInterruptedPersist:

    async def test_interrupt_persists_exchanges_undelivered_and_carryover(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=1)
        assert record.persist_task is not None
        await record.persist_task

        rte = _writes(agent, "recent_tool_exchanges")[-1]
        assert [ex["tool_uses"][0]["name"] for ex in rte] == ["tool_1"]
        assert rte[0]["delivered"] is False

        carry = _writes(agent, "turn_carryover")[-1]
        assert carry["segments"] == ["Hello"]
        assert carry["stopped_at_stage"] == "tool_end"
        assert isinstance(carry["written_at_ms"], int)
        # the question the turn never delivered is not recorded
        assert _writes(agent, "current_question") == []

    async def test_completed_turn_schedules_no_persist(self):
        agent = _tool_agent(rounds=1)
        record = TurnRecord()
        await _run(agent, record)
        assert record.persist_task is None
        assert _writes(agent, "turn_carryover") in ([], [None])

    async def test_write_carryover_false_persists_exchanges_only(self):
        agent = _tool_agent(rounds=2)
        record = TurnRecord(write_carryover=False)
        await _run(agent, record, abort_after_tool_end=1)
        await record.persist_task
        assert _writes(agent, "recent_tool_exchanges")
        assert [v for v in _writes(agent, "turn_carryover") if v is not None] == []

    async def test_interrupt_before_any_tool_writes_carryover_only(self):
        agent = _tool_agent(rounds=0)
        record = TurnRecord()
        abort = asyncio.Event()
        async for ev in agent.stream_turn(_make_turn_input(), abort_event=abort, record=record):
            if isinstance(ev, SignalEvent) and ev.stage == "nlu":
                abort.set()
        await record.persist_task
        assert _writes(agent, "recent_tool_exchanges") == []
        assert _writes(agent, "turn_carryover")[-1]["segments"] == ["Hello"]

    async def test_interrupt_before_fold_appends_to_existing_carryover(self):
        """Review focus 3: an abort during step 1 must not overwrite older carry-over."""
        agent = _tool_agent(rounds=0)
        import time as _t
        existing = {"segments": ["I want work in Ghaziabad"], "stopped_at_stage": "nlu",
                    "turn_id": "old", "written_at_ms": int(_t.time() * 1000)}
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = existing
        record = TurnRecord()
        abort = asyncio.Event()
        abort.set()                              # aborted before step 1 completes
        async for _ in agent.stream_turn(_make_turn_input(user_message="hello?"),
                                         abort_event=abort, record=record):
            pass
        assert record.fold_ran is False
        await record.persist_task
        carry = _writes(agent, "turn_carryover")[-1]
        assert carry["segments"] == ["I want work in Ghaziabad", "hello?"]

    async def test_persist_failure_is_logged_not_raised(self, caplog):
        agent = _tool_agent(rounds=2)
        agent._async_memory.write.side_effect = RuntimeError("down")
        record = TurnRecord()
        await _run(agent, record, abort_after_tool_end=1)
        await record.persist_task                 # must not raise
        assert any("orchestrator.interrupted_persist" in r.message for r in caplog.records)

    async def test_error_turn_is_treated_as_not_completed(self):
        agent = _tool_agent(rounds=0)
        async def boom(*a, **k):
            raise RuntimeError("llm down")
            yield  # pragma: no cover
        agent._llm.stream = boom
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(_make_turn_input(), record=record)]
        assert events[-1].turn_status == "abandoned"
        await record.persist_task
        assert _writes(agent, "turn_carryover")[-1]["segments"] == ["Hello"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py::TestInterruptedPersist -v`
Expected: FAIL — `assert record.persist_task is not None`

- [ ] **Step 3: Implement**

In `agent_core/src/orchestrator.py`, import `from src.turn_policy import TurnPolicy, resolve_turn_policy` (match the file's import style: `src.` vs relative). In `AgentCore.__init__`, after the config is stored, add:

```python
        # Resolved per channel on first use; config is immutable after startup.
        self._turn_policies: dict[str, TurnPolicy] = {}
```

Replace the Task 3 placeholder `_on_turn_not_completed` with:

```python
    def _turn_policy(self, channel: "str | None") -> TurnPolicy:
        """Return the cached turn-lifecycle policy for ``channel``.

        Args:
            channel: Channel name from the TurnInput.

        Returns:
            The resolved TurnPolicy.
        """
        key = channel or ""
        policy = self._turn_policies.get(key)
        if policy is None:
            policy = resolve_turn_policy(self._config, channel)
            self._turn_policies[key] = policy
        return policy

    def _on_turn_not_completed(
        self, turn_input: TurnInput, record: TurnRecord, turn_id: str = "",
    ) -> None:
        """Schedule persistence of an interrupted or failed turn's state.

        Runs from ``stream_turn``'s ``finally`` so it must not await: it only
        builds the payloads and hands them to a background task, stored on
        ``record.persist_task`` so the TurnAssembler can wait for it.

        Args:
            turn_input: The turn's input (identity + utterance).
            record: The turn's ledger.
            turn_id: The caller-supplied turn id (may be empty).
        """
        if turn_input is None or not turn_input.session_id or self._async_memory is None:
            return
        user_id = turn_input.user_id or turn_input.session_id
        exchanges = [dict(ex, delivered=False) for ex in record.captured_exchanges]
        carry = None
        if record.write_carryover:
            segments = list(record.segments) if record.fold_ran else [turn_input.user_message]
            segments = [s for s in segments if isinstance(s, str) and s.strip()]
            if segments:
                carry = {
                    "segments": segments,
                    "stopped_at_stage": record.last_stage,
                    "turn_id": turn_id,
                    "written_at_ms": int(time.time() * 1000),
                }
        if not exchanges and carry is None:
            return
        try:
            record.persist_task = asyncio.get_running_loop().create_task(
                self._persist_interrupted(turn_input.session_id, user_id, record,
                                          exchanges, carry, turn_input.channel)
            )
        except RuntimeError:
            logger.warning(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted",
                       "status": "skipped", "error": "no running event loop"},
            )

    async def _persist_interrupted(
        self,
        session_id: str,
        user_id: str,
        record: TurnRecord,
        exchanges: list[dict],
        carry: "dict | None",
        channel: "str | None" = None,
    ) -> None:
        """Write an interrupted turn's tool rounds and utterances to Memory Layer.

        Tool rounds are merged into ``recent_tool_exchanges`` marked
        ``delivered: false``. Utterances go to ``turn_carryover``. If the turn
        was interrupted before its own fold ran, any existing carry-over is read
        and appended to rather than overwritten. Never raises.

        Args:
            session_id: Session identifier.
            user_id: User identifier.
            record: The interrupted turn's ledger.
            exchanges: Captured rounds, already marked undelivered.
            carry: The ``turn_carryover`` payload, or None.
            channel: Channel, for the fold cap when appending.
        """
        start = time.time()
        try:
            if exchanges and record.max_items > 0:
                merged = (list(record.prior_exchanges) + exchanges)[-record.max_items:]
                await self._async_memory.write(
                    session_id, user_id, "session", "recent_tool_exchanges", merged,
                )
            if carry is not None:
                if not record.fold_ran:
                    bundle = await self._async_memory.context_bundle(session_id, user_id)
                    earlier = self._valid_carryover_segments(
                        (bundle.session or {}).get("turn_carryover"),
                        self._turn_policy(channel),
                    )
                    carry = dict(carry, segments=earlier + carry["segments"])
                cap = self._turn_policy(channel).fold_max_segments
                if cap > 0:
                    carry["segments"] = carry["segments"][-cap:]
                await self._async_memory.write(
                    session_id, user_id, "session", "turn_carryover", carry,
                )
            logger.info(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted", "status": "success",
                       "session_id": session_id, "exchange_count": len(exchanges),
                       "segment_count": len(carry["segments"]) if carry else 0,
                       "stopped_at_stage": record.last_stage,
                       "latency_ms": int((time.time() - start) * 1000)},
            )
        except Exception as e:  # noqa: BLE001 — background task, must not raise
            logger.error(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted", "status": "failure",
                       "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                       "latency_ms": int((time.time() - start) * 1000)},
            )

    def _valid_carryover_segments(self, raw: Any, policy: TurnPolicy) -> list[str]:
        """Return the usable segments of a stored ``turn_carryover`` value.

        Args:
            raw: The stored value (any type — upstream data is not trusted).
            policy: Policy supplying ``carryover_max_age_ms``.

        Returns:
            Non-empty string segments, or [] when the value is absent,
            malformed, or older than the policy allows.
        """
        if raw is None:
            return []
        if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
            logger.warning(
                "orchestrator.carryover_discarded",
                extra={"operation": "orchestrator.fold_carryover", "status": "skipped",
                       "reason": "malformed"},
            )
            return []
        written = raw.get("written_at_ms")
        age_ms = int(time.time() * 1000) - written if isinstance(written, int) else -1
        if age_ms < 0 or age_ms > policy.carryover_max_age_ms:
            logger.info(
                "orchestrator.carryover_discarded",
                extra={"operation": "orchestrator.fold_carryover", "status": "skipped",
                       "reason": "stale"},
            )
            return []
        return [s for s in raw["segments"] if isinstance(s, str) and s.strip()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py -v`
Expected: all PASS.

Note on `test_interrupt_before_fold_appends_to_existing_carryover`: with `abort` already set, the impl returns at its first `_aborted()` check, before step 1. So `fold_ran` is False and `_persist_interrupted` reads the bundle.

- [ ] **Step 5: Full suite and commit**

Run: `cd agent_core && uv run pytest -q`

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_stream_turn_lifecycle.py
git commit -m "feat(agent_core): persist an interrupted turn's tool rounds and utterances to Memory Layer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Fold carry-over on entry; replay note; clear flags on completion

**Files:**
- Modify: `agent_core/src/orchestrator.py`
  - add `_fold_carryover`
  - call it right after step 1 (after the `yield ... memory_read complete` / `if _aborted(): return` at `:2984-2986`)
  - `_build_tool_exchange_messages` (`:1913`) and `_prepend_tool_replay` (`:1988`) gain `undelivered_note`
  - the stream path's call at `:3597` passes the note
  - the step-11 persist at `:4055-4079` clears `delivered` flags
- Test: `agent_core/tests/test_stream_turn_lifecycle.py`

**Interfaces:**
- Consumes: `_turn_policy`, `_valid_carryover_segments` (Task 4).
- Produces:
  - `AgentCore._fold_carryover(turn_input, bundle, record, user_id) -> TurnInput` (async)
  - `_build_tool_exchange_messages(exchanges, undelivered_note: str = "")`
  - `_prepend_tool_replay(messages, bundle, session_id, operation, undelivered_note: str = "")`

- [ ] **Step 1: Write the failing tests**

Append to `agent_core/tests/test_stream_turn_lifecycle.py`:

```python
import time as _time

from src.chat_provider.types import ToolResultBlock


def _carry(segments, age_ms=0):
    return {"segments": segments, "stopped_at_stage": "nlu", "turn_id": "t0",
            "written_at_ms": int(_time.time() * 1000) - age_ms}


class TestFold:

    async def test_carryover_folded_into_user_message_and_cleared(self):
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["I want work in Ghaziabad"])
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(
            _make_turn_input(user_message="hello, anyone there?"), record=record)]
        assert isinstance(events[-1], DoneEvent)
        assert record.fold_ran is True
        assert record.segments == ["I want work in Ghaziabad", "hello, anyone there?"]
        # cleared (None written) before anything else
        assert _writes(agent, "turn_carryover")[0] is None
        # the model's user turn contains both utterances
        built = agent._manager_agent.build_messages.call_args
        assert "I want work in Ghaziabad" in str(built) and "hello, anyone there?" in str(built)

    async def test_no_carryover_leaves_message_unchanged(self):
        agent = _tool_agent(rounds=0)
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"), record=record)]
        assert record.segments == ["hi"] and record.fold_ran is True
        assert _writes(agent, "turn_carryover") == []

    async def test_stale_carryover_is_discarded_and_cleared(self):
        """Review focus 2: a callback must not fold the previous call's goodbye."""
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["thank you, bye"], age_ms=10 * 60 * 1000)
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"), record=record)]
        assert record.segments == ["hi"]
        assert _writes(agent, "turn_carryover")[0] is None

    @pytest.mark.parametrize("raw", ["oops", ["a"], {"segments": "a"},
                                     {"segments": [1, None, " "], "written_at_ms": 0}])
    async def test_malformed_carryover_is_ignored(self, raw):
        """Review focus 4."""
        agent = _tool_agent(rounds=0)
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = raw
        record = TurnRecord()
        events = [e async for e in agent.stream_turn(_make_turn_input(user_message="hi"),
                                                     record=record)]
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        assert record.segments == ["hi"]

    async def test_fold_cap(self):
        agent = _tool_agent(rounds=0)
        agent._config.setdefault("reach_layer", {})["turn_assembler"] = {"fold": {"max_segments": 2}}
        agent._turn_policies.clear()
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = \
            _carry(["a", "b", "c"])
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="d"), record=record)]
        assert record.segments == ["c", "d"]

    async def test_max_segments_zero_disables_fold(self):
        agent = _tool_agent(rounds=0)
        agent._config.setdefault("reach_layer", {})["turn_assembler"] = {"fold": {"max_segments": 0}}
        agent._turn_policies.clear()
        agent._async_memory.context_bundle.return_value.session["turn_carryover"] = _carry(["a"])
        record = TurnRecord()
        [e async for e in agent.stream_turn(_make_turn_input(user_message="d"), record=record)]
        assert record.segments == ["d"]


class TestUndeliveredReplay:

    def test_note_appended_only_to_undelivered(self):
        agent = _make_agent_core()
        ex = lambda i, d: {"tool_uses": [{"type": "tool_use", "id": i, "name": "t", "input": {}}],
                           "tool_results": [{"type": "tool_result", "tool_use_id": i,
                                             "content": "R"}], **d}
        msgs = agent._build_tool_exchange_messages(
            [ex("a", {}), ex("b", {"delivered": False})], undelivered_note="NOTE")
        results = [b for m in msgs if m.role == "user" for b in m.content
                   if isinstance(b, ToolResultBlock)]
        assert results[0].content == "R"
        assert results[1].content == "R\nNOTE"

    def test_no_note_leaves_content(self):
        agent = _make_agent_core()
        msgs = agent._build_tool_exchange_messages([{
            "tool_uses": [{"type": "tool_use", "id": "a", "name": "t", "input": {}}],
            "tool_results": [{"type": "tool_result", "tool_use_id": "a", "content": "R"}],
            "delivered": False}])
        results = [b for m in msgs if m.role == "user" for b in m.content
                   if isinstance(b, ToolResultBlock)]
        assert results[0].content == "R"

    async def test_completed_turn_clears_delivered_flags_without_new_rounds(self):
        agent = _tool_agent(rounds=0)
        prior = [{"tool_uses": [{"type": "tool_use", "id": "p", "name": "t", "input": {}}],
                  "tool_results": [{"type": "tool_result", "tool_use_id": "p", "content": "c"}],
                  "delivered": False}]
        agent._async_memory.context_bundle.return_value.session["recent_tool_exchanges"] = prior
        [e async for e in agent.stream_turn(_make_turn_input())]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stored = _writes(agent, "recent_tool_exchanges")[-1]
        assert "delivered" not in stored[0]
```

If `ToolResultBlock` is not in `src.chat_provider.types`, import it from wherever `orchestrator.py` imports it (`grep -n "ToolResultBlock" agent_core/src/orchestrator.py | head -2`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py::TestFold tests/test_stream_turn_lifecycle.py::TestUndeliveredReplay -v`
Expected: FAIL (`record.fold_ran` False; `unexpected keyword argument 'undelivered_note'`)

- [ ] **Step 3: Implement `_fold_carryover` and call it after step 1**

Add to `AgentCore` (next to `_valid_carryover_segments`), with `import dataclasses` at the top of the file if absent:

```python
    async def _fold_carryover(
        self,
        turn_input: TurnInput,
        bundle: Any,
        record: TurnRecord,
        user_id: str,
    ) -> TurnInput:
        """Fold an interrupted predecessor's utterances into this turn's input.

        Reads ``turn_carryover`` from the context bundle, clears it in Memory
        Layer (awaited, so a later write from this turn cannot land first), and
        joins the usable carried segments with this turn's utterance, capped to
        ``fold.max_segments``. Records the result in ``record.segments``.

        Args:
            turn_input: This turn's input.
            bundle: The step-1 context bundle (``bundle.session`` is mutated).
            record: This turn's ledger.
            user_id: Resolved user identifier.

        Returns:
            ``turn_input`` itself when nothing was folded, else a copy with the
            folded ``user_message``.
        """
        policy = self._turn_policy(turn_input.channel)
        session = bundle.session if isinstance(getattr(bundle, "session", None), dict) else {}
        raw = session.get("turn_carryover")
        carried: list[str] = []
        if raw is not None:
            if policy.fold_max_segments > 0:
                carried = self._valid_carryover_segments(raw, policy)
            session["turn_carryover"] = None
            await self._async_memory.write(
                turn_input.session_id, user_id, "session", "turn_carryover", None,
            )
        segments = carried + [turn_input.user_message]
        if policy.fold_max_segments > 0:
            segments = segments[-policy.fold_max_segments:]
        else:
            segments = [turn_input.user_message]
        record.segments = segments
        record.fold_ran = True
        if len(segments) == 1:
            return turn_input
        logger.info(
            "orchestrator.carryover_folded",
            extra={"operation": "orchestrator.fold_carryover", "status": "success",
                   "session_id": turn_input.session_id,
                   "folded_segment_count": len(segments) - 1},
        )
        return dataclasses.replace(
            turn_input, user_message=" ".join(s.strip() for s in segments),
        )
```

In `_stream_turn_impl`, directly after the step-1 lines

```python
            yield _stamp(SignalEvent(stage="memory_read", status="complete"))
            if _aborted():
                return
```

insert:

```python
            # Spec §4.6: fold an interrupted predecessor's utterances into this
            # turn before NLU and the input trust check see the message.
            turn_input = await self._fold_carryover(turn_input, bundle, record, user_id)
```

Check that no local variable captured `turn_input.user_message` before this point: `grep -n "turn_input.user_message" agent_core/src/orchestrator.py` and confirm every hit inside `_stream_turn_impl` is after the fold, apart from the log line at the top (message preview, `[:120]`). **That log line prints message content, which the logging rule forbids. Leave it untouched here; it is pre-existing and out of scope.**

- [ ] **Step 4: Implement the replay note**

Change `_build_tool_exchange_messages` to `def _build_tool_exchange_messages(exchanges: list[dict], undelivered_note: str = "") -> list[Message]:`, document the new arg ("Appended on its own line to every tool result of an exchange marked `delivered: false`; empty disables"), and inside the per-exchange loop:

```python
            note = undelivered_note if (undelivered_note and ex.get("delivered") is False) else ""
```

and build each `ToolResultBlock` with

```python
                        content=(r.get("content", "") + ("\n" + note if note else "")),
```

Change `_prepend_tool_replay(self, messages, bundle, session_id, operation, undelivered_note: str = "")`, document the arg, and pass it on: `self._build_tool_exchange_messages(prior[-max_items:], undelivered_note)`.

At the stream path's call (`:3597`) pass it:

```python
            _prior_exchanges, _max_items, _max_chars = self._prepend_tool_replay(
                messages, bundle, session_id, "orchestrator.stream_turn",
                undelivered_note=self._turn_policy(turn_input.channel).undelivered_note,
            )
```

The sync path's call (`:1107`) is unchanged.

- [ ] **Step 5: Clear `delivered` flags on a completed turn**

In step 11 (`:4055-4060`), replace

```python
            _capped = self._merge_tool_exchanges(
                _prior_exchanges, _captured_exchanges_this_turn, _max_items,
            )
```

with

```python
            _capped = self._merge_tool_exchanges(
                _prior_exchanges, _captured_exchanges_this_turn, _max_items,
            )
            # Spec §4.6: this turn completed, so replayed undelivered results
            # have now been spoken about — drop the flags, even with no new round.
            if _capped is None and _max_items > 0 and any(
                isinstance(ex, dict) and ex.get("delivered") is False for ex in _prior_exchanges
            ):
                _capped = list(_prior_exchanges)[-_max_items:]
            if _capped is not None:
                _capped = [
                    {k: v for k, v in ex.items() if k != "delivered"} if isinstance(ex, dict) else ex
                    for ex in _capped
                ]
```

- [ ] **Step 6: Run the tests**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_lifecycle.py tests/test_stream_turn.py -v`
Expected: all PASS

- [ ] **Step 7: Full suite and commit**

Run: `cd agent_core && uv run pytest -q`

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_stream_turn_lifecycle.py
git commit -m "feat(agent_core): fold carried utterances on entry; mark unheard tool results on replay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Cooperative interruption and predecessor drain in the TurnAssembler

**Files:**
- Modify: `agent_core/src/turn.py` (`Turn` gains `record`, `predecessor`)
- Modify: `agent_core/src/turn_assembler.py`
  - `__init__`: policy cache
  - `add_segment` barge-in block at `:319-346`
  - `cancel` at `:636-681`
  - `_invoke` at `:969-1063`
  - new `_interrupt`, `_policy`, `_await_predecessor`
- Modify: `agent_core/tests/test_turn_assembler.py`
  - mocks: `abort_event=None, turn_id=""` → add `**kwargs`, 8 places
  - `test_session_end_cancels_tasks` (`:798`)
- Modify: `agent_core/tests/test_turn_assembler_integration.py` (1 mock)
- Test: `agent_core/tests/test_turn_assembler_interrupt.py` (new)

**Interfaces:**
- Consumes: `resolve_turn_policy`, `TurnPolicy`, the `ON_*` constants (Task 1); `TurnRecord`, `DoneEvent.interrupted_at_stage` (Task 2); `stream_turn(..., record=)` (Task 3).
- Produces:
  - `Turn.record: TurnRecord`, `Turn.predecessor: Optional[Turn]`
  - `TurnAssembler._interrupt(turn: Turn, reason: str) -> None` (sync; reasons `"new_input" | "disconnect" | "cancel"`)
  - `TurnAssembler._policy(channel) -> TurnPolicy`
  - `TurnAssembler._await_predecessor(pred: Turn, drain_max_ms: int) -> None`
  - `_invoke` passes `record=turn.record`, drains the generator after an abort, and waits for its predecessor first

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_turn_assembler_interrupt.py`:

```python
"""Cooperative interruption and predecessor drain (Agent Core block, TurnAssembler).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.4
"""

import asyncio

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn_assembler import TurnAssembler, TurnStatus


def _cfg(**interruption):
    return {"reach_layer": {"turn_assembler": {
        "silence_trigger": {"silence_ms": 10},
        "max_wait_ceiling": {"max_wait_ms": 5000},
        "interruption": interruption,
    }}, "channels": {}}


class _SlowAgent:
    """stream_turn that emits a stage, then sleeps in a 'tool', then sentences.

    Records whether it ran to its abort check (``stopped``), and never yields
    content after abort — mimicking the orchestrator's cooperative checks.
    """

    def __init__(self, tool_s=0.2):
        self.tool_s = tool_s
        self.calls = []
        self.stopped = []

    async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
        # The real stream_turn wrapper tracks record.last_stage; mimic it.
        self.calls.append(turn_input.user_message)
        if record is not None:
            record.last_stage = "tool_start"
        yield SignalEvent(stage="tool_start", status="start")
        await asyncio.sleep(self.tool_s)             # a dispatched tool call
        if record is not None:
            record.last_stage = "tool_end"
        yield SignalEvent(stage="tool_end", status="complete")
        if abort_event is not None and abort_event.is_set():
            self.stopped.append(turn_input.user_message)
            return
        yield SentenceEvent(text="answer", sentence_index=0)
        yield DoneEvent(turn_id=turn_id, turn_status="completed")


def _seg(text):
    return SegmentInput(text=text, channel="voice", user_id="u1")


async def _wait_status(turn, status, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if turn.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"turn stayed {turn.status}")


class TestCooperativeInterrupt:

    async def test_barge_in_does_not_cancel_the_running_task(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await asyncio.sleep(0.05)                   # inside the 'tool'
        await ta.add_segment("s1", _seg("second"))
        assert first.status == TurnStatus.INTERRUPTED
        assert first.abort_event.is_set()
        assert not first.invocation_task.cancelled()
        await asyncio.wait_for(first.invocation_task, 1)
        assert agent.stopped == ["first"]           # reached its safe point

    async def test_sealed_queue_carries_stage_and_no_content(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await asyncio.sleep(0.05)
        await ta.add_segment("s1", _seg("second"))
        await asyncio.wait_for(first.invocation_task, 1)
        events = [e async for e in first.iter_events()]
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert done[0].turn_status == "interrupted"
        assert done[0].interrupted_at_stage == "tool_start"
        assert not any(isinstance(e, SentenceEvent) for e in events)

    async def test_successor_waits_for_predecessor(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        await _wait_status(second, TurnStatus.COMPLETED, timeout=3)
        # the successor's stream_turn started only after the predecessor stopped
        assert agent.calls == ["first", "second"]
        assert first.invocation_task.done()

    async def test_drain_timeout_proceeds_without_cancelling(self, caplog):
        agent = _SlowAgent(tool_s=0.5)
        ta = TurnAssembler(agent_core=agent, config=_cfg(drain_max_ms=50))
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        second = ta._sessions["s1"].current_turn
        await _wait_status(second, TurnStatus.INVOKED, timeout=1)
        await asyncio.sleep(0.1)
        assert not first.invocation_task.done()      # still draining, not cancelled
        assert any("turn_assembler.drain_timeout" in r.message for r in caplog.records)
        await asyncio.wait_for(first.invocation_task, 2)

    async def test_replace_policy_disables_carryover(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg(on_new_input="replace"))
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.add_segment("s1", _seg("second"))
        assert first.record.write_carryover is False

    async def test_cancel_is_cooperative(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        await ta.add_segment("s1", _seg("first"))
        first = ta._sessions["s1"].current_turn
        await _wait_status(first, TurnStatus.INVOKED)
        await ta.cancel("s1")
        assert first.status == TurnStatus.INTERRUPTED
        assert not first.invocation_task.cancelled()
        await asyncio.wait_for(first.invocation_task, 1)
        assert agent.stopped == ["first"]

    async def test_invoke_passes_record(self):
        seen = {}

        class _Agent:
            async def stream_turn(self, turn_input, *, abort_event=None, turn_id="", record=None):
                seen["record"] = record
                yield DoneEvent(turn_id=turn_id, turn_status="completed")

        ta = TurnAssembler(agent_core=_Agent(), config=_cfg())
        await ta.add_segment("s1", _seg("hi"))
        turn = ta._sessions["s1"].current_turn
        await _wait_status(turn, TurnStatus.COMPLETED)
        assert seen["record"] is turn.record
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler_interrupt.py -v`
Expected: FAIL (`invocation_task.cancelled()` True; no `record`; `interrupted_at_stage` None)

- [ ] **Step 3: Extend `Turn`**

In `agent_core/src/turn.py`, import `TurnRecord` next to the existing `.models` import, and add after `ceiling_task`:

```python
    # Streaming-lifecycle ledger filled by stream_turn (spec §4.3), and the
    # interrupted turn this one must wait for before invoking (spec §4.4).
    record: TurnRecord = field(default_factory=TurnRecord)
    predecessor: Optional["Turn"] = None
```

- [ ] **Step 4: Add `_policy`, `_interrupt` and `_await_predecessor`; rewrite barge-in and `cancel`**

In `agent_core/src/turn_assembler.py` import `from src.turn_policy import ON_NEW_INPUT_ABORT_AND_FOLD, TurnPolicy, resolve_turn_policy` (match the file's import style). In `__init__`, after `self._sessions`, add `self._policies: dict[str, TurnPolicy] = {}`.

Add these private helpers in the "Session management helpers" section:

```python
    def _policy(self, channel: str | None) -> TurnPolicy:
        """Return the cached streaming-turn policy for ``channel``.

        Args:
            channel: Channel name.

        Returns:
            The resolved TurnPolicy.
        """
        key = channel or ""
        policy = self._policies.get(key)
        if policy is None:
            policy = resolve_turn_policy(self._config, channel)
            self._policies[key] = policy
        return policy

    def _interrupt(self, turn: Turn, reason: str) -> None:
        """Stop ``turn`` cooperatively. Synchronous: safe from a generator ``finally``.

        Marks the turn INTERRUPTED (ABANDONED if it never invoked), sets its
        abort signal, cancels only its timer tasks, and seals its queue with a
        terminal DoneEvent carrying the last stage reached. The invocation task
        is never cancelled: it runs on to the orchestrator's next safe point,
        which records what the turn did (spec §4.4-4.5).

        Args:
            turn: The turn to stop. No-op unless WAITING or INVOKED.
            reason: ``new_input`` | ``disconnect`` | ``cancel`` — logged, and
                ``new_input`` applies the channel's ``on_new_input`` policy.
        """
        if turn.status not in (TurnStatus.WAITING, TurnStatus.INVOKED):
            return
        invoked = turn.status == TurnStatus.INVOKED
        turn.status = TurnStatus.INTERRUPTED if invoked else TurnStatus.ABANDONED
        if reason == "new_input":
            turn.record.write_carryover = (
                self._policy(turn.channel).on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
            )
        turn.abort_event.set()
        self._cancel_timer_tasks(turn)
        turn.event_queue.put_nowait(DoneEvent(
            turn_status=turn.status.value,
            turn_id=turn.turn_id,
            interrupted_at_stage=turn.record.last_stage or None,
        ))
        logger.info(
            "turn_assembler.interrupt_requested",
            extra={"operation": "turn_assembler.interrupt", "status": "success",
                   "session_id": turn.session_id, "turn_id": turn.turn_id,
                   "reason": reason, "stage": turn.record.last_stage,
                   "turn_status": turn.status.value},
        )

    async def _await_predecessor(self, pred: Turn, drain_max_ms: int) -> None:
        """Wait for an interrupted predecessor to stop and persist, within budget.

        Waits first for its invocation task (reaching a safe point), then for
        the persist task that task's exit created. Never cancels anything: on
        timeout the caller proceeds, and the predecessor still stops at its next
        safe point and persists late (spec §4.4).

        Args:
            pred: The interrupted turn.
            drain_max_ms: Total budget for both waits.
        """
        start = time.monotonic()
        deadline = start + drain_max_ms / 1000.0

        async def _within(task: "asyncio.Task | None") -> bool:
            if task is None or task.done():
                return True
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.wait({task}, timeout=remaining)
            return task.done()

        # The persist task only exists once the invocation task has exited,
        # so it must be read after the first wait, not before.
        reached = await _within(pred.invocation_task) and await _within(pred.record.persist_task)
        drain_ms = int((time.monotonic() - start) * 1000)
        if not reached:
            logger.warning(
                "turn_assembler.drain_timeout",
                extra={"operation": "turn_assembler.await_predecessor",
                       "status": "failure", "session_id": pred.session_id,
                       "turn_id": pred.turn_id, "drain_ms": drain_ms},
            )
            return
        logger.info(
            "turn_assembler.safe_point_reached",
            extra={"operation": "turn_assembler.await_predecessor", "status": "success",
                   "session_id": pred.session_id, "turn_id": pred.turn_id,
                   "stage": pred.record.last_stage, "drain_ms": drain_ms},
        )
```

Replace the barge-in block in `add_segment` (`:319-346`, from `# Barge-in: new segment arrived while a turn is in flight.` through `turn = new_turn`) with:

```python
            # Barge-in: new segment arrived while a turn is in flight. Stop it
            # cooperatively; the successor waits for it (spec §4.4) and folds
            # its utterances from Memory Layer (spec §4.6).
            if turn is not None and turn.status == TurnStatus.INVOKED:
                logger.info(
                    "turn_assembler.cancel_and_fold",
                    extra={
                        "operation": "turn_assembler.cancel_and_fold",
                        "status": "success",
                        "session_id": session_id,
                        "cancelled_turn_id": turn.turn_id,
                        "reason": "new segment arrived while INVOKED — interrupting current turn",
                    },
                )
                self._interrupt(turn, "new_input")
                new_turn = await session.replace_turn(seed_segments=[segment])
                new_turn.predecessor = turn
                turn = new_turn
```

Replace the body of `cancel` after `async with session._lock:` with:

```python
        async with session._lock:
            turn = session.current_turn
            if turn is None:
                return
            self._interrupt(turn, "cancel")
```

Update its docstring: "Sets abort_event, cancels timer tasks only, and seals the queue. The invocation task is not cancelled: it stops at its next safe point."

- [ ] **Step 5: Rewrite `_invoke`**

Replace `_invoke` (`:969-1063`) with:

```python
    async def _invoke(self, turn: Turn) -> None:
        """Wait for any interrupted predecessor, then run stream_turn for this turn.

        Events go to the Turn's queue while the turn is live. Once it is
        interrupted, the remaining events are drained and discarded, so the
        orchestrator reaches its next abort check and persists (spec §4.4-4.5),
        and nothing stale is delivered (#224).

        Args:
            turn: The Turn whose invocation this manages.
        """
        pred, turn.predecessor = turn.predecessor, None
        if pred is not None:
            await self._await_predecessor(pred, self._policy(turn.channel).drain_max_ms)
        if turn.abort_event.is_set():
            return

        assembled_text = " ".join(s.text.strip() for s in turn.segments)
        first_segment = turn.segments[0] if turn.segments else None
        turn_input = TurnInput(
            session_id=turn.session_id,
            user_message=assembled_text,
            channel=turn.channel,
            timestamp_ms=turn.started_at_ms,
            user_id=turn.user_id,
            caller_agent_id=turn.caller_agent_id,
            fresh=bool(getattr(first_segment, "fresh", False)) if first_segment else False,
            locale=getattr(first_segment, "locale", None) if first_segment else None,
            metadata=getattr(first_segment, "metadata", None) if first_segment else None,
        )

        logger.info(
            "turn_assembler.invoke_start",
            extra={
                "operation": "turn_assembler.invoke",
                "status": "success",
                "session_id": turn.session_id,
                "turn_id": turn.turn_id,
                "epoch": turn.epoch,
                "segment_count": len(turn.segments),
                "assembled_length": len(assembled_text),
            },
        )

        start = time.time()
        gen = self._agent_core.stream_turn(
            turn_input,
            abort_event=turn.abort_event,
            turn_id=turn.turn_id,
            record=turn.record,
        )
        try:
            async for event in gen:
                if turn.abort_event.is_set() or turn.status != TurnStatus.INVOKED:
                    continue  # sealed: drain to the next safe point, deliver nothing
                await turn.event_queue.put(event)
                if isinstance(event, DoneEvent):
                    turn.status = TurnStatus.COMPLETED
        except asyncio.CancelledError:
            logger.info(
                "turn_assembler.invoke_cancelled",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return
        except Exception as e:
            logger.error(
                "turn_assembler.invoke_error",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            if turn.status == TurnStatus.INVOKED:
                turn.status = TurnStatus.COMPLETED
                await turn.event_queue.put(
                    DoneEvent(
                        turn_status="abandoned",
                        turn_id=turn.turn_id,
                        latency_ms=int((time.time() - start) * 1000),
                    )
                )
        finally:
            await gen.aclose()
```

- [ ] **Step 6: Update the existing tests to the new contract**

In `agent_core/tests/test_turn_assembler.py` and `agent_core/tests/test_turn_assembler_integration.py`, change every mock signature `abort_event=None, turn_id=""` to `abort_event=None, turn_id="", **kwargs`. Find them with `grep -n 'abort_event=None, turn_id=""' tests/test_turn_assembler*.py`.

Replace `test_session_end_cancels_tasks` (`test_turn_assembler.py:798`) with:

```python
    @pytest.mark.asyncio
    async def test_session_end_cancels_timers_not_invocation(self):
        ta = _make_assembler()
        session = ta._get_or_create_session("s1")
        turn = await session.replace_turn(seed_segments=[])
        silence = asyncio.create_task(asyncio.sleep(10))
        ceiling = asyncio.create_task(asyncio.sleep(10))
        invocation = asyncio.create_task(asyncio.sleep(10))
        turn.silence_task = silence
        turn.ceiling_task = ceiling
        turn.invocation_task = invocation
        turn.status = TurnStatus.INVOKED

        await ta.session_end("s1")
        await asyncio.sleep(0)

        assert silence.cancelled()
        assert ceiling.cancelled()
        # Spec §4.4: the invocation stops cooperatively at a safe point.
        assert not invocation.cancelled()
        assert turn.abort_event.is_set()
        invocation.cancel()
```

Then run `uv run pytest tests/test_turn_assembler.py tests/test_turn_assembler_integration.py -v`. For any remaining failure that asserts `invocation_task.cancelled()` or that `_invoke` stops iterating on abort, change it to assert `abort_event.is_set()` plus no further queued events. Record each changed test name in the commit body. **Do not change a test that asserts #224's guarantee** (no stale sentences delivered); those must pass unmodified.

- [ ] **Step 7: Run everything**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler_interrupt.py -v && uv run pytest -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add agent_core/src/turn.py agent_core/src/turn_assembler.py agent_core/tests/
git commit -m "feat(agent_core): cooperative turn interruption; successor drains its predecessor

Replaces task.cancel() with abort + drain so an interrupted turn reaches a safe
point and persists. Updated tests: <list names changed in step 6>.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Request adapter (`submit` / `attach` / `detach`) and idle eviction

**Files:**
- Modify: `agent_core/src/session.py` (`last_activity_ms`, `subscribers`)
- Modify: `agent_core/src/turn_assembler.py`
  - `TurnAssemblerBase`: three new abstract methods
  - `TurnAssembler`: implementations; `clock` constructor kwarg; `_evict_idle`
  - `subscribe`: count subscribers
  - `_get_or_create_session`: sweep, touch
- Test: `agent_core/tests/test_turn_assembler_request.py` (new)

**Interfaces:**
- Consumes: `_interrupt`, `_policy`, `_invoke` (Task 6); `resolve_session_idle_ttl_ms`, `ON_DISCONNECT_ABORT` (Task 1).
- Produces:
  - `TurnAssemblerBase.submit(session_id: str, segment: SegmentInput) -> Turn` (async)
  - `TurnAssemblerBase.attach(turn: Turn) -> AsyncGenerator[StreamEvent, None]` (async generator, like `subscribe`)
  - `TurnAssemblerBase.detach(turn: Turn, reason: str) -> None` (sync)
  - `TurnAssembler(..., clock: Callable[[], float] | None = None)`

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_turn_assembler_request.py`:

```python
"""Request-scoped adapter and idle eviction (Agent Core block, TurnAssembler).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.1-4.2, §4.7
"""

import asyncio

import pytest

from src.models import DoneEvent, SegmentInput, SentenceEvent, SignalEvent
from src.turn_assembler import TurnAssembler, TurnAssemblerBase, TurnStatus

from tests.test_turn_assembler_interrupt import _SlowAgent, _cfg, _wait_status


def _req(text, channel="bridge"):
    return SegmentInput(text=text, channel=channel, user_id="u1")


class TestSubmitAttach:

    async def test_submit_invokes_immediately_and_attach_streams(self):
        agent = _SlowAgent(tool_s=0.01)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        assert turn.status == TurnStatus.INVOKED             # no silence timer
        events = [e async for e in ta.attach(turn)]
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        assert any(isinstance(e, SentenceEvent) for e in events)

    async def test_second_submit_interrupts_first_and_waits(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        first = await ta.submit("s1", _req("I want work nearby"))
        await asyncio.sleep(0.05)
        second = await ta.submit("s1", _req("hello?"))
        assert first.status == TurnStatus.INTERRUPTED
        events = [e async for e in ta.attach(second)]
        assert events[-1].turn_status == "completed"
        assert agent.calls == ["I want work nearby", "hello?"]
        assert agent.stopped == ["I want work nearby"]

    async def test_submit_rejects_empty(self):
        ta = TurnAssembler(agent_core=_SlowAgent(), config=_cfg())
        with pytest.raises(ValueError):
            await ta.submit("", _req("hi"))
        with pytest.raises(ValueError):
            await ta.submit("s1", _req("  "))

    async def test_submit_carries_fresh(self):
        seen = {}

        class _Agent:
            async def stream_turn(self, ti, *, abort_event=None, turn_id="", record=None):
                seen["fresh"] = ti.fresh
                yield DoneEvent(turn_id=turn_id, turn_status="completed")

        ta = TurnAssembler(agent_core=_Agent(), config=_cfg())
        turn = await ta.submit("s1", SegmentInput(text="hi", channel="bridge", fresh=True))
        [e async for e in ta.attach(turn)]
        assert seen["fresh"] is True


class TestDetach:

    async def test_detach_aborts_by_default(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        await asyncio.sleep(0.05)
        ta.detach(turn, "disconnect")                       # sync call
        assert turn.status == TurnStatus.INTERRUPTED
        await asyncio.wait_for(turn.invocation_task, 1)
        assert agent.stopped == ["hi"]

    async def test_detach_continue_lets_turn_finish(self):
        agent = _SlowAgent(tool_s=0.05)
        ta = TurnAssembler(agent_core=agent, config=_cfg(on_disconnect="continue"))
        turn = await ta.submit("s1", _req("hi"))
        ta.detach(turn, "disconnect")
        await asyncio.wait_for(turn.invocation_task, 1)
        assert turn.status == TurnStatus.COMPLETED
        assert agent.stopped == []

    async def test_detach_after_completion_is_noop(self):
        agent = _SlowAgent(tool_s=0.01)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        turn = await ta.submit("s1", _req("hi"))
        [e async for e in ta.attach(turn)]
        ta.detach(turn, "disconnect")
        assert turn.status == TurnStatus.COMPLETED

    async def test_detach_of_superseded_turn_is_noop(self):
        agent = _SlowAgent(tool_s=0.2)
        ta = TurnAssembler(agent_core=agent, config=_cfg())
        first = await ta.submit("s1", _req("a"))
        second = await ta.submit("s1", _req("b"))
        ta.detach(first, "disconnect")                      # already interrupted
        assert second.status == TurnStatus.INVOKED


class TestEviction:

    async def test_idle_session_evicted_on_next_lookup(self):
        now = [1000.0]
        cfg = _cfg()
        cfg["reach_layer"]["turn_assembler"]["session_idle_ttl_ms"] = 1000
        ta = TurnAssembler(agent_core=_SlowAgent(tool_s=0.01), config=cfg, clock=lambda: now[0])
        turn = await ta.submit("s1", _req("hi"))
        [e async for e in ta.attach(turn)]
        now[0] += 5.0
        await ta.submit("s2", _req("hi"))
        assert "s1" not in ta._sessions and "s2" in ta._sessions

    async def test_busy_or_subscribed_session_not_evicted(self):
        now = [1000.0]
        cfg = _cfg()
        cfg["reach_layer"]["turn_assembler"]["session_idle_ttl_ms"] = 1000
        ta = TurnAssembler(agent_core=_SlowAgent(tool_s=10), config=cfg, clock=lambda: now[0])
        await ta.submit("busy", _req("hi"))                 # stays INVOKED
        sub = ta._get_or_create_session("sub")
        sub.subscribers = 1
        now[0] += 5.0
        ta._get_or_create_session("other")
        assert "busy" in ta._sessions and "sub" in ta._sessions
        ta._sessions["busy"].current_turn.invocation_task.cancel()   # test cleanup only


def test_base_declares_request_methods():
    for name in ("submit", "attach", "detach"):
        assert name in TurnAssemblerBase.__abstractmethods__
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler_request.py -v`
Expected: FAIL (`AttributeError: 'TurnAssembler' object has no attribute 'submit'`)

- [ ] **Step 3: Extend `Session`**

In `agent_core/src/session.py`, add fields after `ended`:

```python
    last_activity: float = 0.0  # assembler clock (monotonic s) at last lookup
    subscribers: int = 0        # open subscribe() loops; evicting under one would end it
```

- [ ] **Step 4: Add the abstract methods to `TurnAssemblerBase`**

After `session_end`, add:

```python
    @abstractmethod
    async def submit(self, session_id: str, segment: SegmentInput) -> "Turn":
        """Start a complete-utterance turn now, interrupting any turn in flight.

        Request-scoped adapter entry (``/stream_turn``). The client has already
        decided the turn is complete, so no trigger policy runs.

        Args:
            session_id: Unique session identifier.
            segment: The complete utterance with its metadata.

        Returns:
            The new, already-invoked Turn. Stream it with :meth:`attach`.

        Raises:
            ValueError: If session_id or the segment text is empty.
        """

    @abstractmethod
    async def attach(self, turn: "Turn") -> AsyncGenerator[StreamEvent, None]:
        """Yield ``turn``'s events until its terminal DoneEvent.

        Args:
            turn: A Turn returned by :meth:`submit`.

        Yields:
            StreamEvent instances in order.
        """
        yield  # pragma: no cover

    @abstractmethod
    def detach(self, turn: "Turn", reason: str) -> None:
        """Tell the assembler a request-scoped consumer went away.

        Synchronous so it can be called from a generator ``finally`` during
        ``GeneratorExit``. Applies the channel's ``on_disconnect`` policy to a
        turn that is still current and in flight; otherwise a no-op.

        Args:
            turn: The Turn whose consumer closed.
            reason: Why (logged), normally ``disconnect``.
        """
```

- [ ] **Step 5: Implement in `TurnAssembler`**

Imports: `from collections.abc import Callable`, and add `ON_DISCONNECT_ABORT, resolve_session_idle_ttl_ms` to the `turn_policy` import.

Constructor: add a trailing kwarg `clock: Optional[Callable[[], float]] = None`, and document it ("Monotonic seconds source; injectable for tests"). At the end of `__init__`:

```python
        self._clock: Callable[[], float] = clock or time.monotonic
        self._idle_ttl_s: float = resolve_session_idle_ttl_ms(config) / 1000.0
        self._sweep_interval_s: float = min(self._idle_ttl_s, 60.0)
        self._last_sweep: float = self._clock()
```

In `_get_or_create_session`, at the top of the body:

```python
        self._evict_idle()
```

and just before `return session`:

```python
        session.last_activity = self._clock()
```

Add:

```python
    def _evict_idle(self) -> None:
        """Evict idle in-process sessions, at most once per sweep interval.

        A session is evicted only if it has no WAITING/INVOKED turn and no
        subscriber, and was last touched more than ``session_idle_ttl_ms`` ago.
        Memory Layer state is untouched: this is in-process housekeeping for
        request-mode sessions, which never receive ``session_end`` (spec §4.7).
        """
        now = self._clock()
        if now - self._last_sweep < self._sweep_interval_s:
            return
        self._last_sweep = now
        for sid, session in list(self._sessions.items()):
            turn = session.current_turn
            busy = turn is not None and turn.status in (TurnStatus.WAITING, TurnStatus.INVOKED)
            if busy or session.subscribers > 0:
                continue
            if now - session.last_activity <= self._idle_ttl_s:
                continue
            session.ended = True
            session.turn_changed.set()
            self._sessions.pop(sid, None)
            logger.info(
                "turn_assembler.session_evicted",
                extra={"operation": "turn_assembler.evict_idle", "status": "success",
                       "session_id": sid},
            )
```

Add the public methods in the "Public interface" section:

```python
    async def submit(self, session_id: str, segment: SegmentInput) -> Turn:
        """Start a complete-utterance turn now, interrupting any turn in flight.

        See :meth:`TurnAssemblerBase.submit`.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")
        if segment is None or not segment.text or not segment.text.strip():
            raise ValueError("segment text must not be empty")
        session = self._get_or_create_session(
            session_id,
            user_id=segment.user_id,
            channel=segment.channel,
            caller_agent_id=segment.caller_agent_id,
        )
        async with session._lock:
            prev = session.current_turn
            predecessor = None
            if prev is not None and prev.status in (TurnStatus.WAITING, TurnStatus.INVOKED):
                was_invoked = prev.status == TurnStatus.INVOKED
                self._interrupt(prev, "new_input")
                predecessor = prev if was_invoked else None
            elif prev is not None and prev.invocation_task is not None \
                    and not prev.invocation_task.done():
                predecessor = prev  # interrupted earlier, still draining
            turn = await session.replace_turn(seed_segments=[segment])
            turn.predecessor = predecessor
            turn.status = TurnStatus.INVOKED
            turn.invocation_task = asyncio.create_task(self._invoke(turn))
        return turn

    async def attach(self, turn: Turn) -> AsyncGenerator[StreamEvent, None]:
        """Yield ``turn``'s events until its DoneEvent. See :meth:`TurnAssemblerBase.attach`."""
        async for event in turn.iter_events():
            yield event

    def detach(self, turn: Turn, reason: str) -> None:
        """Apply ``on_disconnect`` to a still-current, in-flight turn. See base."""
        if turn is None:
            return
        session = self._sessions.get(turn.session_id)
        if session is None or session.current_turn is not turn:
            return
        if turn.status != TurnStatus.INVOKED:
            return
        if self._policy(turn.channel).on_disconnect == ON_DISCONNECT_ABORT:
            self._interrupt(turn, reason or "disconnect")
```

In `subscribe`, wrap the drain loop so the subscriber count is kept:

```python
        session.subscribers += 1
        try:
            seen: Optional[Turn] = None
            while not session.ended:
                ...existing loop body unchanged...
        finally:
            session.subscribers -= 1
```

Note: `turn.iter_events()` returns after the first DoneEvent. The sealing DoneEvent from `_interrupt` is always the first one on an interrupted turn, because `_invoke` stops enqueuing once aborted.

- [ ] **Step 6: Run the tests**

Run: `cd agent_core && uv run pytest tests/test_turn_assembler_request.py tests/test_turn_assembler.py tests/test_turn_assembler_interrupt.py -v`
Expected: all PASS

- [ ] **Step 7: Full suite and commit**

Run: `cd agent_core && uv run pytest -q`

```bash
git add agent_core/src/session.py agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler_request.py
git commit -m "feat(agent_core): request-scoped TurnAssembler adapter (submit/attach/detach) and idle eviction

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `/stream_turn` through the TurnAssembler

**Files:**
- Modify: `agent_core/src/servers/orchestration_server.py:218-296` (`stream_turn` handler)
- Modify: `agent_core/tests/test_session_endpoints.py` (`test_stream_turn_works_with_assembler` uses a real assembler)
- Test: `agent_core/tests/test_stream_turn_endpoint.py` (new)

**Interfaces:**
- Consumes: `submit`, `attach`, `detach` (Task 7); `SegmentInput.fresh` (Task 2).
- Produces: `/stream_turn` behaviour.
  - With an assembler: submit, then stream the turn, calling `detach(turn, "disconnect")` if no DoneEvent was read.
  - Without one: the existing direct path, unchanged.

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_stream_turn_endpoint.py`:

```python
"""/stream_turn through the TurnAssembler (Agent Core block, orchestration server).

Spec: docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md §4.2
"""

import asyncio
import json
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.models import DoneEvent, SentenceEvent
from src.servers.orchestration_server import create_orchestration_app
from src.turn_assembler import TurnAssembler, TurnStatus

from tests.test_turn_assembler_interrupt import _SlowAgent, _cfg


def _app(agent, config=None):
    assembler = TurnAssembler(agent_core=agent, config=config or _cfg())
    return create_orchestration_app(agent, turn_assembler=assembler), assembler


def _events(resp):
    return [json.loads(l[len("data: "):]) for l in resp.text.splitlines() if l.startswith("data: ")]


def test_stream_turn_goes_through_assembler():
    agent = _SlowAgent(tool_s=0.01)
    app, assembler = _app(agent)
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={
            "session_id": "s1", "user_message": "hello", "channel": "bridge", "user_id": "u1"})
    assert resp.status_code == 200
    evs = _events(resp)
    assert evs[-1]["type"] == "done" and evs[-1]["turn_status"] == "completed"
    assert "s1" in assembler._sessions
    assert agent.calls == ["hello"]


def test_disconnect_after_done_is_not_an_interrupt():
    """Review focus 1: reading DoneEvent then closing must not interrupt."""
    agent = _SlowAgent(tool_s=0.01)
    app, assembler = _app(agent)
    with TestClient(app) as client:
        client.post("/stream_turn", json={
            "session_id": "s1", "user_message": "hello", "channel": "bridge", "user_id": "u1"})
    turn = assembler._sessions["s1"].current_turn
    assert turn.status == TurnStatus.COMPLETED
    assert turn.record.write_carryover is True and not turn.abort_event.is_set()


async def test_generator_close_before_done_detaches():
    """Starlette closing the SSE body early must interrupt, cooperatively."""
    agent = _SlowAgent(tool_s=0.3)
    app, assembler = _app(agent)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/stream_turn")
    from src.servers.orchestration_server import ProcessTurnRequest
    resp = await route.endpoint(ProcessTurnRequest(
        session_id="s1", user_message="hello", channel="bridge", user_id="u1"))
    body = resp.body_iterator
    await body.__anext__()                       # first event read
    await body.aclose()                          # client went away
    turn = assembler._sessions["s1"].current_turn
    assert turn.status == TurnStatus.INTERRUPTED
    assert not turn.invocation_task.cancelled()
    await asyncio.wait_for(turn.invocation_task, 2)
    assert agent.stopped == ["hello"]


def test_without_assembler_direct_path_unchanged():
    agent = MagicMock()

    async def mock_stream(ti, **kwargs):
        yield DoneEvent(turn_status="completed")

    agent.stream_turn = mock_stream
    app = create_orchestration_app(agent)
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={"session_id": "s1", "user_message": "hi"})
    assert _events(resp)[-1]["turn_status"] == "completed"


def test_empty_message_rejected_with_assembler():
    app, _ = _app(_SlowAgent())
    with TestClient(app) as client:
        resp = client.post("/stream_turn", json={"session_id": "s1", "user_message": "  "})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_endpoint.py -v`
Expected: FAIL (`"s1" not in assembler._sessions`)

- [ ] **Step 3: Implement the handler**

In `orchestration_server.py`, the session endpoints are registered only when `turn_assembler is not None`. Keep that. Rewrite the `/stream_turn` handler body:
- keep the existing start log
- build `turn_input` exactly as today in the no-assembler branch
- add the assembler branch

```python
        if turn_assembler is not None:
            if not request.user_message or not request.user_message.strip():
                return JSONResponse(status_code=422,
                                    content={"detail": "user_message must not be empty"})
            segment = SegmentInput(
                text=request.user_message,
                user_id=request.user_id,
                channel=request.channel,
                timestamp_ms=request.timestamp_ms or int(time.time() * 1000),
                caller_agent_id=request.caller_agent_id,
                locale=request.locale,
                metadata=request.metadata,
                fresh=request.fresh,
            )
            turn = await turn_assembler.submit(session_id, segment)

            async def assembled_generator():
                # Spec §4.2: the turn belongs to the assembler. Closing this body
                # only detaches; the finally must not await (GeneratorExit).
                done_read = False
                event_count = 0
                try:
                    async for event in turn_assembler.attach(turn):
                        if isinstance(event, DoneEvent):
                            done_read = True
                        event_count += 1
                        yield event.to_sse()
                finally:
                    if not done_read:
                        turn_assembler.detach(turn, "disconnect")
                    logger.info(
                        "orchestration_server.stream_turn_complete",
                        extra={
                            "operation": "orchestration_server.stream_turn",
                            "status": "success" if done_read else "skipped",
                            "session_id": session_id,
                            "event_count": event_count,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )

            return StreamingResponse(
                assembled_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
```

Place this before the existing `turn_input = TurnInput(...)`. The rest of the handler (direct path) stays as it is. Add `SegmentInput` to the handler module's `src.models` import if it is not already there (it is used by `session_input`). Update the endpoint docstring: "With a TurnAssembler (production), the turn runs through the assembler: a new request interrupts one in flight, and a client closing the stream stops its turn at a safe point. Without one, the turn runs directly."

- [ ] **Step 4: Fix the mocked-assembler test**

In `tests/test_session_endpoints.py`, replace `test_stream_turn_works_with_assembler` with:

```python
    def test_stream_turn_works_with_assembler(self):
        agent = _make_mock_agent_core()

        async def mock_stream(ti, **kwargs):
            yield DoneEvent(turn_status="completed")

        agent.stream_turn = mock_stream
        assembler = TurnAssembler(agent_core=agent, config={"reach_layer": {}, "channels": {}})
        app = create_orchestration_app(agent, turn_assembler=assembler)
        client = TestClient(app)

        resp = client.post("/stream_turn", json={
            "session_id": "s1", "user_message": "hello",
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
```

Also change the module-level `mock_stream(ti)` in `_make_mock_agent_core` to `mock_stream(ti, **kwargs)`.

- [ ] **Step 5: Run and commit**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_endpoint.py tests/test_session_endpoints.py -v && uv run pytest -q`
Expected: all pass

```bash
git add agent_core/src/servers/orchestration_server.py agent_core/tests/test_stream_turn_endpoint.py agent_core/tests/test_session_endpoints.py
git commit -m "feat(agent_core): /stream_turn runs through the TurnAssembler; disconnect detaches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Bridge — remove the no-op cancel

**Files:**
- Modify: `reach_layer/bridge/src/server.py`
  - delete `_pending_cancel_tasks` (`:43`) and `_schedule_cancel` (`:248-278`)
  - simplify `_stream` (`:281-437`): drop the `finished` flag, `finally` and `session_id` param
  - update its call site
- Modify: `reach_layer/bridge/src/agent_core_client.py` (delete `cancel_turn`; module docstring line 9)
- Modify: `reach_layer/bridge/main.py:32-33` (comment)
- Modify: `reach_layer/bridge/tests/test_server.py`, `reach_layer/bridge/tests/test_agent_core_client.py`

**Interfaces:**
- Produces: `_stream(client, turn, model, terminal_word, include_usage, hangup_tool=None, tool_status_phrases=None)`, i.e. `session_id` removed.

- [ ] **Step 1: Write the failing test**

In `reach_layer/bridge/tests/test_server.py`, replace the five cancel tests:
- `test_cancel_turn_fires_on_early_generator_close`
- `test_cancel_turn_not_called_when_stream_finishes_normally`
- `test_no_cancel_fires_when_client_closes_right_after_reading_done`
- `test_streaming_without_a_done_event_closes_cleanly_and_cancels`
- `test_agent_core_timeout_mid_stream_still_cancels`

Keep each one's *close-cleanly* assertions, and replace the cancel assertions with:

```python
def test_client_has_no_cancel_path():
    """Spec §6: Agent Core stops the turn when the upstream stream closes."""
    from src.agent_core_client import AgentCoreClient
    assert not hasattr(AgentCoreClient, "cancel_turn")
```

For the tests that checked clean closing (`...without_a_done_event_closes_cleanly...`, `...timeout_mid_stream...`), rename them without `_and_cancels` / `_still_cancels`, delete the lines asserting `cancel_turn` was awaited or called, and keep the assertions on the emitted chunks and `[DONE]`. Delete the two `cancel_turn` tests in `test_agent_core_client.py`.

Run: `cd reach_layer/bridge && uv run pytest -q`
Expected: FAIL (`test_client_has_no_cancel_path`)

- [ ] **Step 2: Implement the removal**

- `agent_core_client.py`: delete `async def cancel_turn` and the docstring line `- DELETE /sessions/{id}/active_turn  barge-in cancel (spec section 8)`.
- `server.py`:
  - delete `_pending_cancel_tasks` and `_schedule_cancel`
  - in `_stream`, remove the `session_id` parameter, the `finished` variable and its assignments, and the whole `finally:` block
  - replace the long docstring paragraphs about cancel with: "Closing this stream (client disconnect) closes the upstream `/stream_turn` body; Agent Core's TurnAssembler then stops the turn at its next safe point and keeps what it did for the next turn (spec 2026-09-29 §4.2). The bridge sends no cancel."
  - drop the `session_id=` argument at `_stream`'s call site (`grep -n "_stream(" reach_layer/bridge/src/server.py`)
  - remove `asyncio` from the imports if nothing else uses it
- `main.py:32-33`: delete the comment referring to `cancel_turn`.

- [ ] **Step 3: Run and commit**

Run: `cd reach_layer/bridge && uv run pytest -q`
Expected: all pass

```bash
git add reach_layer/bridge
git commit -m "refactor(bridge): drop the no-op turn cancel; Agent Core stops the turn on disconnect

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 24 Sep replay scenarios, regression, docs

**Files:**
- Create: `agent_core/tests/test_turn_carryover_replay.py`
- Modify: `ARCHITECTURE.md:157-159, 346-360, 501-502`; `CLAUDE.md` (the "Two execution paths" paragraph)

**Interfaces:**
- Consumes: everything above. A real `AgentCore` (via `tests.test_stream_turn._make_agent_core`) and a real `TurnAssembler`, over an in-memory Memory Layer fake.

- [ ] **Step 1: Write the replay tests**

Create `agent_core/tests/test_turn_carryover_replay.py`:

```python
"""24 Sep 2026 PoC replays: interrupted streaming turns keep what they held.

Real AgentCore + real TurnAssembler over an in-memory Memory Layer fake.
Belongs to the Agent Core block. Evidence: voicera-cancelled-turns.md §2.
"""

import asyncio
import copy

from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import ToolUseBlock
from src.models import ContextBundle, DoneEvent, NLUResult, SegmentInput, ToolResult
from src.turn_assembler import TurnAssembler, TurnStatus

from tests.test_stream_turn import _make_agent_core


class FakeMemory:
    """Session-scope key/value store with the AsyncMemoryLayer surface used here."""

    def __init__(self):
        self.session = {"current_subagent_id": "start"}
        self.writes = []

    async def context_bundle(self, session_id, user_id, adopt=True):
        return ContextBundle(session=copy.deepcopy(self.session), profile={})

    async def write(self, session_id, user_id, scope, key, value):
        self.writes.append((key, copy.deepcopy(value)))
        if scope == "session":
            self.session[key] = copy.deepcopy(value)

    def __getattr__(self, name):                      # flush, audit, etc.: no-ops
        async def _noop(*a, **k):
            return None
        return _noop


def _agent(memory, llm_script):
    agent = _make_agent_core(async_memory=memory)
    agent._config["channels"]["bridge"] = {"system_prompt_suffix": ""}
    agent._llm.stream = llm_script
    agent._language_normaliser = type("N", (), {"normalise": lambda s, *a, **k: ("m", "hindi")})()
    agent._nlu_processor = type("P", (), {"process": lambda s, *a, **k: NLUResult(
        intent="search", entities={}, sentiment="neutral", confidence=0.9)})()
    agent._tool_registry.get_route.return_value = None
    return agent


def _user_text(agent):
    """Text of the user message the last LLM call was built from."""
    return str(agent._manager_agent.build_messages.call_args)


async def test_checkin_during_first_llm_call_folds_the_lost_utterance():
    """Class A, row 2: 'I want to work in Ghaziabad' cut at 2.8 s by 'hello, anyone?'."""
    memory = FakeMemory()
    started = asyncio.Event()

    async def llm(*args, abort_event=None, **kwargs):
        started.set()
        for _ in range(100):                          # slow first token
            if abort_event is not None and abort_event.is_set():
                return
            await asyncio.sleep(0.01)
        yield "Which trade? "

    agent = _agent(memory, llm)
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    first = await ta.submit("919900112233", SegmentInput(
        text="मैं ग़ाज़ियाबाद में ही काम करना चाहता हूँ", channel="bridge", user_id="919900112233"))
    await asyncio.wait_for(started.wait(), 2)
    ta.detach(first, "disconnect")                    # VoicERA closed the request
    second = await ta.submit("919900112233", SegmentInput(
        text="हेलो कोई है", channel="bridge", user_id="919900112233"))
    events = [e async for e in ta.attach(second)]
    assert isinstance(events[-1], DoneEvent)
    assert "ग़ाज़ियाबाद" in _user_text(agent) and "हेलो कोई है" in _user_text(agent)
    assert memory.session.get("turn_carryover") is None      # consumed


async def test_tool_rounds_before_disconnect_reach_the_next_turn():
    """Row 9: fetch -> apply -> save ran, then the caller hung up / re-spoke."""
    memory = FakeMemory()
    calls = {"n": 0}
    hold = asyncio.Event()

    async def llm(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 3:
            yield ""
            raise ChatToolUseRequested([ToolUseBlock(
                tool_name=f"t{calls['n']}", tool_use_id=f"tu{calls['n']}", input={})])
        await hold.wait()                              # 4th call: never gets to speak
        yield "Applied. "

    agent = _agent(memory, llm)
    agent._async_gateway.execute.side_effect = lambda tc, *a, **k: ToolResult(
        tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True,
        result_text=f"ok-{tc.tool_name}")
    ta = TurnAssembler(agent_core=agent, config=agent._config)
    first = await ta.submit("s", SegmentInput(text="हाँ ठीक है फिर से कोशिश कर लीजिए",
                                              channel="bridge", user_id="s"))
    for _ in range(200):
        if len(first.record.captured_exchanges) == 3:
            break
        await asyncio.sleep(0.01)
    ta.detach(first, "disconnect")
    hold.set()
    await asyncio.wait_for(first.invocation_task, 2)
    await asyncio.wait_for(first.record.persist_task, 2)
    stored = memory.session["recent_tool_exchanges"]
    assert [ex["tool_uses"][0]["name"] for ex in stored] == ["t1", "t2", "t3"]
    assert all(ex["delivered"] is False for ex in stored)


async def test_checkin_cascade_keeps_newest_segments():
    """Review focus 5: the substantive utterance survives within the cap; a long
    cascade stays capped at fold.max_segments (default 3)."""

    async def llm(*args, abort_event=None, **kwargs):
        for _ in range(100):
            if abort_event is not None and abort_event.is_set():
                return
            await asyncio.sleep(0.01)
        yield "ok "

    async def run(texts):
        memory = FakeMemory()
        agent = _agent(memory, llm)
        ta = TurnAssembler(agent_core=agent, config=agent._config)
        turn = None
        for text in texts:
            turn = await ta.submit("s", SegmentInput(text=text, channel="bridge", user_id="s"))
            await asyncio.sleep(0.05)
        events = [e async for e in ta.attach(turn)]
        assert isinstance(events[-1], DoneEvent)
        return turn.record.segments

    job = "मुझे डिलीवरी बॉय का जॉब चाहिए"
    hello = "हेलो कोई है"
    assert await run([job, hello, hello]) == [job, hello, hello]
    long = await run([job] + [hello] * 6)
    assert len(long) == 3 and long == [hello] * 3
```

- [ ] **Step 2: Run the replays**

Run: `cd agent_core && uv run pytest tests/test_turn_carryover_replay.py -v`
Expected: 3 PASS. If `_make_agent_core`'s `manager_agent.build_messages` mock does not receive the folded text, assert on `NLU` input instead: add a spy on `_nlu_processor.process` capturing `normalised_input`. Do not weaken the assertion that both utterances reach the model.

- [ ] **Step 3: Update the docs**

In `ARCHITECTURE.md`, replace line 159 (the TurnAssembler paragraph) with:

```markdown
**TurnAssembler:** Every streaming turn runs through `TurnAssembler`, both `POST /stream_turn` (request-scoped: one request = one complete utterance, invoked immediately) and the session endpoints `POST /sessions/{id}/input` + `GET /sessions/{id}/events` (segment stream: semantic gate · silence trigger · max-wait ceiling). It holds `Session` objects keyed by `session_id`; each owns one current `Turn` (segments, event queue, abort signal, `TurnRecord` ledger). A new input or a client disconnect interrupts the turn *cooperatively*: its queue is sealed (#224), it runs on to the orchestrator's next safe point (tool calls are never cut), and the successor waits for it (`interruption.drain_max_ms`). What the interrupted turn held survives in Memory Layer. `turn_carryover` holds its utterances, which the next turn folds into its input (`fold.max_segments`, `carryover.max_age_ms`). `recent_tool_exchanges` holds its completed tool rounds, marked `delivered: false` and replayed with `carryover.undelivered_note`. `POST /process_turn` does not use the assembler. Spec: `docs/superpowers/specs/2026-09-29-turn-assembler-request-mode-design.md`.
```

At line 501, change the Streaming row's first cell description to add: "via TurnAssembler (request-scoped)". In `CLAUDE.md`, replace the sentence `Two execution paths: ... Both run the same sequence.` with:

```markdown
Two execution paths: `POST /process_turn` (sync JSON, fire-and-wait; web/CLI/MCP/voice in direct mode) and `POST /stream_turn` (SSE). Every streaming turn — `/stream_turn` and the session endpoints — runs through the TurnAssembler, which interrupts cooperatively and carries an interrupted turn's utterances and tool rounds to the next turn via Memory Layer. Both paths run the same sequence.
```

- [ ] **Step 4: Full regression across the touched modules**

Run:
```bash
cd agent_core && uv run pytest -q
cd ../reach_layer/bridge && uv run pytest -q
cd ../../dev-kit && uv run pytest -q
cd ../reach_layer && uv run pytest -q --ignore=bridge
```
Expected: all green. `reach_layer` voice's `agent_core_llm` interrupted handling is unchanged: it still receives `DoneEvent(turn_status="interrupted")`. Report any pre-existing failure separately rather than fixing it here.

- [ ] **Step 5: Coverage check**

Run: `cd agent_core && uv run pytest --cov=src --cov-report=term-missing -q | grep -E "turn_assembler|turn_policy|orchestrator|TOTAL"`
Expected: TOTAL ≥ 70% (repo rule); `turn_policy.py` ≥ 90%.

- [ ] **Step 6: Commit**

```bash
git add agent_core/tests/test_turn_carryover_replay.py ARCHITECTURE.md CLAUDE.md
git commit -m "test(agent_core): 24 Sep replay scenarios for interrupted streaming turns; document the lifecycle

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Dev-kit Docker gate (before merge, not before push)**

Per `.claude/rules/runtime-devkit-sync.md`:
- rebuild with `docker build -f dev-kit/Dockerfile -t dpg-dev-kit .`
- run a deploy dry-run and confirm `"validator": "runtime_baked"`

If Docker/`dhi.io` login is unavailable in the executing environment, record that as an open verification item in the PR description. Do not claim it was done.
