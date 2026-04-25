# Turn Lifecycle Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace conflated `SessionBuffer` with explicit `Session` and `Turn` objects so cancel structurally stops the event stream and stale sentences cannot leak to TTS. Implements [#224](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/224).

**Architecture:** Per-turn `Turn` objects own their event queue, abort signal, and segments. `Session` holds the current `Turn` pointer and a fan-out `turn_changed` event for subscribers. `Orchestrator.stream_turn` and `ClaudeLLMWrapper.stream_call` accept an `abort_event` and check it before every yield/chunk; tool calls and trust calls run to completion. `TurnAssembler` is refactored to operate on `Session`/`Turn` while preserving its public API.

**Tech Stack:** Python 3.11+, asyncio, `uv` for environment management, pytest + pytest-asyncio for tests.

**Spec:** [`docs/superpowers/specs/2026-04-25-turn-lifecycle-redesign-design.md`](../specs/2026-04-25-turn-lifecycle-redesign-design.md)

**Working directory throughout:** `/Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg/agent_core` (unless otherwise specified). Run all tests with `uv run pytest` per project rules.

---

## File Structure

**New files:**
- `agent_core/src/turn.py` — `Turn` dataclass, `iter_events` generator.
- `agent_core/src/session.py` — `Session` dataclass, `replace_turn` method.
- `agent_core/tests/test_turn.py` — unit tests for `Turn`.
- `agent_core/tests/test_session.py` — unit tests for `Session`.
- `agent_core/tests/test_turn_assembler_integration.py` — T4-T7 replay test.

**Modified files:**
- `agent_core/src/models.py` — add `turn_id: str = ""` to `SignalEvent`, `SentenceEvent`, `DoneEvent`.
- `agent_core/src/llm_wrapper/claude_wrapper.py` — add `abort_event` parameter to `stream_call` and propagate to `_stream_with_retry`.
- `agent_core/src/orchestrator.py` — add `abort_event` and `turn_id` parameters to `stream_turn`, check `abort_event` at stage boundaries, stamp emitted events with `turn_id`.
- `agent_core/src/turn_assembler.py` — replace `SessionBuffer` with `Session`/`Turn` internally. Public API unchanged.
- `agent_core/tests/test_turn_assembler.py` — extend with stale-event suppression, epoch monotonicity, rollover, idempotency, concurrency tests.
- `agent_core/tests/test_orchestrator.py` — extend with abort-event tests at every boundary and `turn_id` stamping test.
- `agent_core/tests/llm_wrapper/test_claude_wrapper.py` (or wherever the wrapper tests live — verify in Task 4) — extend with abort-event tests.
- `reach_layer/voice/tests/test_pipecat_cancel_integration.py` — extend with new lifecycle assertions (verify barge-in + GH-149 opening-phrase + ack flow still pass).

**Out of scope for this plan:** `#200` cancel-and-fold policy (separate plan). `#98` TTS audio mid-playback flush (separate). Multi-subscriber fan-out (deferred until needed).

---

## Task 1: Add `turn_id` to StreamEvent classes

This is the foundation: every event-producing component will stamp `turn_id`, and consumers will read it. Additive default-empty change keeps all existing tests passing.

**Files:**
- Modify: `agent_core/src/models.py:264-318`
- Test: `agent_core/tests/test_models.py` (extend; create if missing)

- [ ] **Step 1.1: Inspect existing models test file**

```bash
ls agent_core/tests/test_models.py 2>&1 || echo "does not exist — will create"
```

- [ ] **Step 1.2: Write failing tests for `turn_id` field**

Add to `agent_core/tests/test_models.py` (create if it doesn't exist; if it exists, append the four tests):

```python
"""Tests for StreamEvent classes after adding turn_id field (#224)."""
import json

from src.models import DoneEvent, SentenceEvent, SignalEvent


def test_signal_event_has_turn_id_default_empty():
    ev = SignalEvent(stage="memory_read", status="start")
    assert ev.turn_id == ""


def test_sentence_event_carries_turn_id():
    ev = SentenceEvent(text="hello", sentence_index=0, turn_id="abc-123")
    assert ev.turn_id == "abc-123"
    assert "abc-123" in ev.to_sse()


def test_done_event_serialises_turn_id_in_sse():
    ev = DoneEvent(turn_status="completed", turn_id="t-1")
    payload = ev.to_sse()
    assert json.loads(payload.removeprefix("data: ").rstrip())["turn_id"] == "t-1"


def test_signal_event_to_sse_includes_turn_id_field():
    ev = SignalEvent(stage="trust_input", status="complete", turn_id="t-2")
    parsed = json.loads(ev.to_sse().removeprefix("data: ").rstrip())
    assert parsed["turn_id"] == "t-2"
```

- [ ] **Step 1.3: Run tests to verify they fail**

```bash
cd agent_core && uv run pytest tests/test_models.py -v
```

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'turn_id'` (or AttributeError on `.turn_id`).

- [ ] **Step 1.4: Add `turn_id` field to all three event classes**

Edit `agent_core/src/models.py`:

In `SignalEvent` (around line 264), add as the last field before `to_sse`:
```python
    turn_id: str = ""
```

In `SentenceEvent` (around line 282), add as the last field before `to_sse`:
```python
    turn_id: str = ""
```

In `DoneEvent` (around line 299), add as the last field before `to_sse`:
```python
    turn_id: str = ""
```

The existing `to_sse` implementation uses `asdict(self)` so no change needed there — `turn_id` automatically appears in the JSON.

- [ ] **Step 1.5: Run new tests to verify they pass**

```bash
cd agent_core && uv run pytest tests/test_models.py -v
```

Expected: PASS (4 new tests).

- [ ] **Step 1.6: Run full agent_core suite to verify no regression**

```bash
cd agent_core && uv run pytest -x -q
```

Expected: all existing tests pass (additive change, default empty preserves behavior).

- [ ] **Step 1.7: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/models.py agent_core/tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(agent_core): add turn_id to StreamEvent classes (#224)

Additive schema change with default-empty value. Foundation for
turn-lifecycle redesign — producers will stamp turn_id, consumers
can read it for stale-event detection and trace correlation.
EOF
)"
```

---

## Task 2: Create `Turn` class

`Turn` owns the per-turn state currently scattered across `SessionBuffer`: status, segments, abort signal, event queue, invocation task, policy timers.

**Files:**
- Create: `agent_core/src/turn.py`
- Test: `agent_core/tests/test_turn.py`

- [ ] **Step 2.1: Write failing tests**

Create `agent_core/tests/test_turn.py`:

```python
"""Unit tests for the per-turn lifecycle object (#224)."""
import asyncio

import pytest

from src.models import DoneEvent, SentenceEvent, SignalEvent
from src.turn import Turn, TurnStatus


@pytest.mark.asyncio
async def test_turn_default_status_is_waiting():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    assert t.status == TurnStatus.WAITING
    assert t.segments == []
    assert not t.abort_event.is_set()
    assert t.invocation_task is None


@pytest.mark.asyncio
async def test_iter_events_yields_until_done():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    await t.event_queue.put(SignalEvent(stage="memory_read", status="start"))
    await t.event_queue.put(SentenceEvent(text="hi", sentence_index=0))
    await t.event_queue.put(DoneEvent(turn_status="completed"))
    # Anything enqueued *after* Done must not be yielded by iter_events.
    await t.event_queue.put(SentenceEvent(text="leak", sentence_index=1))

    collected = []
    async for ev in t.iter_events():
        collected.append(ev)
    assert len(collected) == 3
    assert isinstance(collected[-1], DoneEvent)
    # The leaked event remains in the queue but iter_events has exited.
    assert t.event_queue.qsize() == 1


@pytest.mark.asyncio
async def test_seed_segments_are_preserved():
    from src.models import SegmentInput
    seg = SegmentInput(session_id="s", text="hello", channel="cli")
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0, segments=[seg])
    assert t.segments == [seg]


@pytest.mark.asyncio
async def test_abort_event_settable_independently():
    t = Turn(turn_id="t-1", epoch=1, session_id="s", channel="cli", user_id=None,
             started_at_ms=0)
    assert not t.abort_event.is_set()
    t.abort_event.set()
    assert t.abort_event.is_set()
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
cd agent_core && uv run pytest tests/test_turn.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.turn'`.

- [ ] **Step 2.3: Implement `Turn`**

Create `agent_core/src/turn.py`:

```python
"""Per-turn lifecycle object for the TurnAssembler (#224).

Belongs to the Agent Core block. Owns turn-scoped state (segments, queue,
abort signal, invocation task) so that cancellation is a structural property:
a cancelled Turn is dead and its queue is sealed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

from .models import DoneEvent, SegmentInput, StreamEvent


class TurnStatus(str, Enum):
    """State machine for a single turn's lifecycle.

    Transitions:
        WAITING → INVOKED      (policy triggered, invocation task created)
        WAITING → ABANDONED    (cancel() while waiting)
        INVOKED → COMPLETED    (DoneEvent emitted naturally)
        INVOKED → INTERRUPTED  (cancel() while LLM call in flight)
    """

    WAITING = "waiting"
    INVOKED = "invoked"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"


@dataclass
class Turn:
    """One conversational turn within a Session.

    Each Turn owns its own event queue and abort signal. When cancelled,
    the queue is sealed with a terminal DoneEvent and the Turn becomes dead;
    a successor Turn gets a fresh queue.
    """

    turn_id: str
    epoch: int
    session_id: str
    channel: str
    user_id: Optional[str]
    started_at_ms: int
    segments: list[SegmentInput] = field(default_factory=list)
    status: TurnStatus = TurnStatus.WAITING
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    invocation_task: Optional[asyncio.Task] = None
    silence_task: Optional[asyncio.Task] = None
    ceiling_task: Optional[asyncio.Task] = None

    async def iter_events(self) -> AsyncIterator[StreamEvent]:
        """Drain the event queue until DoneEvent is yielded, then exit.

        Yields:
            Each StreamEvent in queue order. Terminates after DoneEvent.
            Any events enqueued after DoneEvent remain in the queue and
            are not yielded.
        """
        while True:
            ev = await self.event_queue.get()
            yield ev
            if isinstance(ev, DoneEvent):
                return
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
cd agent_core && uv run pytest tests/test_turn.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 2.5: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/turn.py agent_core/tests/test_turn.py
git commit -m "$(cat <<'EOF'
feat(agent_core): introduce Turn class for per-turn lifecycle (#224)

Turn owns turn-scoped state: status, segments, abort_event, event_queue,
invocation_task. iter_events() drains the queue until DoneEvent and exits,
leaving any post-Done leaks unread. TurnStatus enum moves here from
turn_assembler.py — re-exported there for backward compat in next task.
EOF
)"
```

---

## Task 3: Create `Session` class

`Session` holds the current `Turn` pointer and the cross-turn signaling primitives. It's the long-lived object per session.

**Files:**
- Create: `agent_core/src/session.py`
- Test: `agent_core/tests/test_session.py`

- [ ] **Step 3.1: Write failing tests**

Create `agent_core/tests/test_session.py`:

```python
"""Unit tests for the per-session lifecycle object (#224)."""
import asyncio

import pytest

from src.session import Session
from src.turn import Turn, TurnStatus


@pytest.mark.asyncio
async def test_session_starts_empty():
    s = Session(session_id="s1", user_id=None, channel="cli")
    assert s.current_turn is None
    assert s.ended is False
    assert not s.turn_changed.is_set()


@pytest.mark.asyncio
async def test_replace_turn_installs_fresh_turn_and_signals():
    s = Session(session_id="s1", user_id="u1", channel="voice")
    new_turn = await s.replace_turn(seed_segments=[])
    assert s.current_turn is new_turn
    assert new_turn.session_id == "s1"
    assert new_turn.user_id == "u1"
    assert new_turn.channel == "voice"
    assert new_turn.status == TurnStatus.WAITING
    assert new_turn.epoch == 1
    assert s.turn_changed.is_set()


@pytest.mark.asyncio
async def test_replace_turn_bumps_epoch_monotonically():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    # Mark t1 terminal so replace_turn precondition holds.
    t1.status = TurnStatus.COMPLETED
    t2 = await s.replace_turn()
    t2.status = TurnStatus.INTERRUPTED
    t3 = await s.replace_turn()
    assert (t1.epoch, t2.epoch, t3.epoch) == (1, 2, 3)


@pytest.mark.asyncio
async def test_replace_turn_carries_seed_segments():
    from src.models import SegmentInput
    s = Session(session_id="s1", user_id=None, channel="cli")
    seeds = [SegmentInput(session_id="s1", text="seed", channel="cli")]
    t = await s.replace_turn(seed_segments=seeds)
    assert t.segments == seeds
    # Segments list must be a fresh list, not the same reference.
    t.segments.append(SegmentInput(session_id="s1", text="x", channel="cli"))
    assert len(seeds) == 1


@pytest.mark.asyncio
async def test_replace_turn_rejects_active_prior_turn():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    # t1 is WAITING (active), so replace_turn must refuse.
    with pytest.raises(RuntimeError):
        await s.replace_turn()


@pytest.mark.asyncio
async def test_turn_changed_event_clearable_for_resignal():
    s = Session(session_id="s1", user_id=None, channel="cli")
    t1 = await s.replace_turn()
    assert s.turn_changed.is_set()
    s.turn_changed.clear()
    t1.status = TurnStatus.COMPLETED
    await s.replace_turn()
    assert s.turn_changed.is_set()
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
cd agent_core && uv run pytest tests/test_session.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.session'`.

- [ ] **Step 3.3: Implement `Session`**

Create `agent_core/src/session.py`:

```python
"""Per-session lifecycle object for the TurnAssembler (#224).

Belongs to the Agent Core block. Long-lived across many turns.
Holds the current Turn pointer and a fan-out signal that subscribers
use to learn when a new Turn becomes current.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .models import SegmentInput
from .turn import Turn, TurnStatus


_TERMINAL_STATUSES = (
    TurnStatus.COMPLETED,
    TurnStatus.INTERRUPTED,
    TurnStatus.ABANDONED,
)


@dataclass
class Session:
    """Long-lived per-session state.

    Owns identity (session_id, user_id, channel), the current Turn pointer,
    a per-session lock (for atomic turn rollover), and a turn_changed Event
    (single-subscriber fan-out signal — switch to asyncio.Condition if
    multi-subscriber lands).
    """

    session_id: str
    user_id: Optional[str]
    channel: str
    current_turn: Optional[Turn] = None
    ended: bool = False
    turn_changed: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _epoch_counter: int = 0

    async def replace_turn(
        self, *, seed_segments: list[SegmentInput] | None = None
    ) -> Turn:
        """Install a fresh Turn as ``current_turn`` and return it.

        Precondition: prior ``current_turn`` (if any) is in a terminal state
        (COMPLETED / INTERRUPTED / ABANDONED). Caller is responsible for
        cancelling an active turn before calling ``replace_turn``.

        Args:
            seed_segments: Optional initial segments for the new Turn.
                Copied into a fresh list so mutation does not leak.

        Returns:
            The newly created Turn.

        Raises:
            RuntimeError: If prior current_turn is still WAITING or INVOKED.
        """
        if self.current_turn is not None and self.current_turn.status not in _TERMINAL_STATUSES:
            raise RuntimeError(
                f"replace_turn called while prior turn is "
                f"{self.current_turn.status.value}; cancel first"
            )
        self._epoch_counter += 1
        turn = Turn(
            turn_id=str(uuid.uuid4()),
            epoch=self._epoch_counter,
            session_id=self.session_id,
            channel=self.channel,
            user_id=self.user_id,
            started_at_ms=int(time.time() * 1000),
            segments=list(seed_segments) if seed_segments else [],
        )
        self.current_turn = turn
        self.turn_changed.set()
        return turn
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
cd agent_core && uv run pytest tests/test_session.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 3.5: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/session.py agent_core/tests/test_session.py
git commit -m "$(cat <<'EOF'
feat(agent_core): introduce Session class with replace_turn (#224)

Session is long-lived per session_id. Holds current_turn pointer and
a turn_changed Event for subscriber fan-out. replace_turn enforces
the terminal-status precondition, bumps the monotonic epoch counter,
and signals subscribers waiting for a new turn.
EOF
)"
```

---

## Task 4: Add `abort_event` to `ClaudeLLMWrapper.stream_call`

The LLM stream chunk loop is the hot path for stale-sentence generation. `abort_event` checked between chunks lets the wrapper exit cleanly the moment cancel fires.

**Files:**
- Modify: `agent_core/src/llm_wrapper/claude_wrapper.py:273-328` and `_stream_with_retry`
- Test: locate the existing claude wrapper test file first.

- [ ] **Step 4.1: Locate the claude wrapper test file**

```bash
find agent_core/tests -name 'test_claude*' -o -name 'test_llm*' | head
```

Expected output: a path like `agent_core/tests/llm_wrapper/test_claude_wrapper.py` or similar. **Use that exact path** for the modify/test paths in subsequent steps.

- [ ] **Step 4.2: Read the existing tests for `stream_call` to see how the SDK is mocked**

Read the test file located in Step 4.1, focusing on tests for `stream_call`. Note the mock pattern (likely `AsyncMock` returning an async iterator over fake chunks).

- [ ] **Step 4.3: Write failing tests for `abort_event` parameter**

Add to the existing claude_wrapper test file (use the same fixtures and mock patterns the file already uses; the snippets below are the new test bodies — adjust mock setup to match existing style):

```python
@pytest.mark.asyncio
async def test_stream_call_accepts_abort_event_kwarg(claude_wrapper_with_mock_sdk):
    """abort_event is an optional kwarg that defaults to None (backward compat)."""
    wrapper, mock_sdk = claude_wrapper_with_mock_sdk
    # configure mock_sdk to yield two fake text tokens then end ...
    tokens = []
    async for tok in wrapper.stream_call([{"role": "user", "content": "hi"}]):
        tokens.append(tok)
    assert tokens  # default None abort_event leaves behavior unchanged


@pytest.mark.asyncio
async def test_stream_call_aborts_between_chunks_when_event_set(
    claude_wrapper_with_mock_sdk,
):
    """When abort_event is set mid-stream, no further tokens yield."""
    wrapper, mock_sdk = claude_wrapper_with_mock_sdk
    abort_event = asyncio.Event()

    # Configure mock_sdk to yield a slow stream of 5 tokens with an await
    # between each (so the consumer can set abort_event between chunks).
    async def slow_stream():
        for i in range(5):
            await asyncio.sleep(0)  # yield to event loop
            yield f"tok{i}"
    # ... wire slow_stream into the SDK mock per the file's existing pattern ...

    collected = []
    async def consume():
        async for tok in wrapper.stream_call(
            [{"role": "user", "content": "hi"}],
            abort_event=abort_event,
        ):
            collected.append(tok)
            if len(collected) == 2:
                abort_event.set()

    await consume()
    # After abort fires, stream must stop — at most one chunk may slip through
    # the await boundary, so collected length is 2 or 3 but never 5.
    assert len(collected) <= 3
    assert "tok0" in collected and "tok1" in collected
    assert "tok4" not in collected
```

> **Note for the engineer:** if the file's existing mock pattern doesn't expose a slow async iterator hook, copy the pattern from any existing streaming retry test in the same file and adapt. Do not add a new mocking framework.

- [ ] **Step 4.4: Run new tests to verify they fail**

```bash
cd agent_core && uv run pytest <test_file_from_step_4.1> -k abort -v
```

Expected: FAIL with `TypeError: stream_call() got an unexpected keyword argument 'abort_event'`.

- [ ] **Step 4.5: Add `abort_event` parameter to `stream_call`**

Edit `agent_core/src/llm_wrapper/claude_wrapper.py`:

Update `stream_call` signature (around line 273):

```python
    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | list[dict] | None = None,
        model_override: str | None = None,
        max_tokens: int | None = None,
        *,
        abort_event: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
```

Add to the docstring `Args:` section:
```
            abort_event: Optional asyncio.Event. When set during streaming,
                the chunk loop exits cleanly between chunks and the underlying
                httpx response is closed. Used by TurnAssembler to honour
                upstream cancel without relying on cooperative task cancellation.
```

Replace the body's two `async for token in self._stream_with_retry(...)` loops with abort-aware versions:

```python
        try:
            async for token in self._stream_with_retry(
                model, messages, tools, system, effective_max_tokens
            ):
                if abort_event is not None and abort_event.is_set():
                    return
                yield token
        except _RetryableExhausted:
            if model != self._primary_model:
                return
            logger.warning(
                "llm_wrapper.stream_fallback_triggered",
                extra={"operation": "llm_wrapper.stream_call", "primary_model": model},
            )
            self._switch_to_fallback()
            try:
                async for token in self._stream_with_retry(
                    self._fallback_model, messages, tools, system, effective_max_tokens
                ):
                    if abort_event is not None and abort_event.is_set():
                        return
                    yield token
            except _RetryableExhausted:
                return
```

Make sure `import asyncio` is present at the top of the file (add it if missing).

- [ ] **Step 4.6: Run new tests to verify they pass**

```bash
cd agent_core && uv run pytest <test_file_from_step_4.1> -k abort -v
```

Expected: PASS.

- [ ] **Step 4.7: Run full wrapper test file to verify no regression**

```bash
cd agent_core && uv run pytest <test_file_from_step_4.1> -v
```

Expected: all existing tests pass.

- [ ] **Step 4.8: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/llm_wrapper/claude_wrapper.py agent_core/tests
git commit -m "$(cat <<'EOF'
feat(agent_core): add abort_event kwarg to ClaudeLLMWrapper.stream_call (#224)

When the abort_event is set mid-stream, the chunk loop exits cleanly
between chunks. Default None preserves all existing call sites.
Used by Orchestrator.stream_turn to honour upstream cancel without
relying on cooperative task cancellation.
EOF
)"
```

---

## Task 5: Add `abort_event` and `turn_id` to `Orchestrator.stream_turn`

`stream_turn` checks `abort_event` at every stage boundary and stamps emitted events with `turn_id`. Tool calls and trust calls run to completion (no abort wiring inside them — see spec §Error handling).

**Files:**
- Modify: `agent_core/src/orchestrator.py:2193-3230` (`stream_turn` method)
- Test: `agent_core/tests/test_orchestrator.py`

- [ ] **Step 5.1: Locate `stream_turn` and read its current shape**

```bash
grep -n "async def stream_turn\|abort\|turn_id" agent_core/src/orchestrator.py | head -30
```

Read `agent_core/src/orchestrator.py:2190-2260` to capture the current signature and the early portion of the method (before LLM call).

- [ ] **Step 5.2: Write failing test for accepting `abort_event` and `turn_id` kwargs**

Add to `agent_core/tests/test_orchestrator.py` (use the file's existing fixtures for a constructed `Orchestrator` with mocked memory/trust/gateway/wrapper):

```python
@pytest.mark.asyncio
async def test_stream_turn_accepts_abort_event_and_turn_id_kwargs(
    orchestrator_with_mocks,
):
    orch, mocks = orchestrator_with_mocks
    abort_event = asyncio.Event()
    events = []
    async for ev in orch.stream_turn(
        TurnInput(session_id="s", user_message="hi", channel="cli", timestamp_ms=0),
        abort_event=abort_event,
        turn_id="t-123",
    ):
        events.append(ev)
    # All emitted events carry the supplied turn_id
    assert all(getattr(ev, "turn_id", "") == "t-123" for ev in events)


@pytest.mark.asyncio
async def test_stream_turn_aborts_before_llm_call(orchestrator_with_mocks):
    orch, mocks = orchestrator_with_mocks
    abort_event = asyncio.Event()
    abort_event.set()  # already aborted before stream_turn even starts
    events = []
    async for ev in orch.stream_turn(
        TurnInput(session_id="s", user_message="hi", channel="cli", timestamp_ms=0),
        abort_event=abort_event,
        turn_id="t-1",
    ):
        events.append(ev)
    # No SentenceEvent should be yielded; LLM wrapper should not be called.
    assert not any(isinstance(ev, SentenceEvent) for ev in events)
    mocks.llm_wrapper.stream_call.assert_not_called()


@pytest.mark.asyncio
async def test_stream_turn_aborts_after_tool_returns_before_replay(
    orchestrator_with_mocks_with_tool,
):
    """Tool runs to completion, but abort fires before result feeds back to LLM."""
    orch, mocks = orchestrator_with_mocks_with_tool
    abort_event = asyncio.Event()

    # Configure: first LLM stream emits tool_use; tool completes; set abort_event
    # before the second LLM call would happen.
    mocks.action_gateway.execute = AsyncMock(side_effect=lambda *a, **kw: (
        abort_event.set() or {"status": "ok"}
    ))

    events = []
    async for ev in orch.stream_turn(
        TurnInput(session_id="s", user_message="hi", channel="cli", timestamp_ms=0),
        abort_event=abort_event,
        turn_id="t-2",
    ):
        events.append(ev)

    # Tool was called (ran to completion), but the second LLM round was not.
    mocks.action_gateway.execute.assert_called_once()
    assert mocks.llm_wrapper.stream_call.call_count == 1
```

> **Note:** `orchestrator_with_mocks` and `orchestrator_with_mocks_with_tool` fixtures may not exist verbatim. Use whatever the file's existing async streaming tests already use, and follow that pattern. The point of these tests is to exercise: (1) kwarg acceptance, (2) pre-LLM abort gate, (3) post-tool-pre-replay abort gate. Adapt fixture wiring to match.

- [ ] **Step 5.3: Run tests to verify they fail**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -k "abort_event or turn_id" -v
```

Expected: FAIL with `TypeError: stream_turn() got an unexpected keyword argument 'abort_event'`.

- [ ] **Step 5.4: Add `abort_event` and `turn_id` parameters to `stream_turn`**

Edit `agent_core/src/orchestrator.py`. Update `stream_turn` signature (around line 2193):

```python
    async def stream_turn(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
```

Update the docstring to mention both kwargs. Then thread them through the body:

**Helper (define near the top of the method):**

```python
        def _aborted() -> bool:
            return abort_event is not None and abort_event.is_set()

        def _stamp(ev):
            """Set turn_id on the event if it has the field and one was supplied."""
            if turn_id and hasattr(ev, "turn_id"):
                ev.turn_id = turn_id
            return ev
```

**Stamp every `yield` site.** Find every place in `stream_turn` that does `yield SignalEvent(...)`, `yield SentenceEvent(...)`, or `yield DoneEvent(...)`. Wrap each emitted event in `_stamp(...)`. Example:

```python
        # before:  yield SignalEvent(stage="memory_read", status="start")
        yield _stamp(SignalEvent(stage="memory_read", status="start"))
```

**Insert abort checks at stage boundaries.** Add `if _aborted(): return` immediately:

1. At the very top of the method (before the first `yield`).
2. Before the call to `self._llm_wrapper.stream_call(...)` (so an abort that fired during memory_read / trust_input / NLU / routing prevents the LLM call entirely).
3. **After** each tool call returns (after the `await action_gateway.execute(...)` line), before feeding the result back to the LLM.
4. **After** trust_output completes, before yielding the (now stale) sentences. Trust call must run to completion — do not abort it.
5. Before scheduling the async memory_write task at the end of the method.

**Pass `abort_event` into the LLM wrapper call:**

```python
        # before:
        # async for token in self._llm_wrapper.stream_call(messages, ...):
        async for token in self._llm_wrapper.stream_call(
            messages,
            ...,
            abort_event=abort_event,
        ):
            if _aborted():
                return
            # existing per-token sentence-buffering logic ...
```

> Add `if _aborted(): return` *inside* the per-token loop too — between accumulating a sentence and yielding the corresponding `SentenceEvent` (after the per-sentence trust check returns, before the yield).

Make sure `import asyncio` is present at the top of `orchestrator.py` (it almost certainly is; verify).

- [ ] **Step 5.5: Run new tests to verify they pass**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -k "abort_event or turn_id" -v
```

Expected: PASS.

- [ ] **Step 5.6: Run full orchestrator test file to verify no regression**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -v
```

Expected: all existing tests pass. Default `abort_event=None` and `turn_id=""` preserve current behaviour.

- [ ] **Step 5.7: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(agent_core): thread abort_event and turn_id through stream_turn (#224)

abort_event checked at every stage boundary (pre-LLM, post-tool,
post-trust-output, pre-memory-write) and between LLM chunks via
ClaudeLLMWrapper. turn_id stamped on every emitted StreamEvent.

Tool calls and trust calls run to completion when abort fires
mid-call — preserves external-side-effect safety and the
trust-runs-on-every-output invariant.
EOF
)"
```

---

## Task 6: Refactor `TurnAssembler` to use `Session` and `Turn`

Replace `SessionBuffer`-based internals with `Session`/`Turn`. Public API (`add_segment`, `subscribe`, `cancel`, `session_end`) is unchanged. This task is the largest in the plan — break it into the six sub-steps below, each with its own commit.

**Files:**
- Modify: `agent_core/src/turn_assembler.py` (extensive)
- Test: `agent_core/tests/test_turn_assembler.py` (extensions)

- [ ] **Step 6.1: Pre-flight — read the current TurnAssembler in full**

```bash
wc -l agent_core/src/turn_assembler.py
```

Read the whole file. Catalogue the methods that touch `SessionBuffer`: `_get_or_create_buffer`, `add_segment`, `subscribe`, `cancel`, `session_end`, `_invoke`, `_evaluate_policies`, `_run_silence_timer`, `_run_ceiling_timer`, `_emit_opening_phrase_if_first`, `_fetch_context`, `_cancel_all_tasks`. The next sub-steps modify all of them.

- [ ] **Step 6.2: Re-export `TurnStatus` from `turn.py` and remove the duplicate**

The duplicate `TurnStatus` enum currently lives in `turn_assembler.py:68-82`. We added the canonical one in Task 2. Remove the duplicate and import from `src.turn`.

In `agent_core/src/turn_assembler.py`:
- Delete the `class TurnStatus(...)` block at lines 68-82.
- Add `from .turn import Turn, TurnStatus` near the top imports.
- Add `from .session import Session` near the top imports.

Run `cd agent_core && uv run pytest tests/test_turn_assembler.py -x -q` — expect failures from `SessionBuffer` references (next sub-steps fix). But verify imports resolve.

- [ ] **Step 6.3: Switch `_sessions` from `dict[str, SessionBuffer]` to `dict[str, Session]`; refactor `add_segment`**

Replace the `SessionBuffer` dataclass usage with `Session`/`Turn`. The internal dict stores `Session`s; the active turn is `session.current_turn`.

Plan for `add_segment`:

1. Get-or-create `Session` from `self._sessions`.
2. Acquire `session._lock`.
3. If `session.current_turn is None` or `session.current_turn.status` is terminal → `await session.replace_turn(seed_segments=[])`.
4. Append the segment to `session.current_turn.segments`.
5. Run policy stack on `session.current_turn` (semantic_gate / silence / ceiling).
6. On policy trigger, set `session.current_turn.status = INVOKED`, then create the invocation task: `session.current_turn.invocation_task = asyncio.create_task(self._invoke(session.current_turn))`.

Write failing tests first in `agent_core/tests/test_turn_assembler.py`:

```python
@pytest.mark.asyncio
async def test_add_segment_creates_session_lazily(turn_assembler_with_mocks):
    ta, _ = turn_assembler_with_mocks
    seg = SegmentInput(session_id="s1", text="hi", channel="cli")
    await ta.add_segment("s1", seg)
    assert "s1" in ta._sessions
    session = ta._sessions["s1"]
    assert session.current_turn is not None
    assert session.current_turn.segments[0].text == "hi"


@pytest.mark.asyncio
async def test_add_segment_after_terminal_turn_installs_new_turn(
    turn_assembler_with_mocks,
):
    ta, _ = turn_assembler_with_mocks
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="a", channel="cli"))
    session = ta._sessions["s1"]
    first = session.current_turn
    first.status = TurnStatus.COMPLETED  # simulate natural completion
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="b", channel="cli"))
    assert session.current_turn is not first
    assert session.current_turn.epoch > first.epoch
```

Run: `cd agent_core && uv run pytest tests/test_turn_assembler.py -k "creates_session_lazily or installs_new_turn" -v` — expect FAIL.

Now refactor `add_segment` body and the internal helpers it calls (`_get_or_create_session` replacing `_get_or_create_buffer`). Reuse policy-evaluation helpers but pass `session.current_turn` where they previously took `buffer`.

Run the same tests — expect PASS. Run the full file — expect any tests that *don't* depend on `SessionBuffer` internal field names to pass; tests that read `buffer.segments` or `buffer.status` directly will break and need updating in the next sub-steps. Note them but do not fix yet.

Commit:
```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler.py
git commit -m "refactor(agent_core): TurnAssembler.add_segment uses Session/Turn (#224)"
```

- [ ] **Step 6.4: Refactor `_invoke` to take `Turn` parameter; thread `abort_event` and `turn_id`**

Change `_invoke(self, session_id: str)` to `_invoke(self, turn: Turn)`. Pass `turn.abort_event` and `turn.turn_id` into `stream_turn`. Push events into `turn.event_queue`.

Write failing test:

```python
@pytest.mark.asyncio
async def test_invoke_passes_abort_event_and_turn_id_to_stream_turn(
    turn_assembler_with_mocks,
):
    ta, mocks = turn_assembler_with_mocks
    # Configure the orchestrator mock's stream_turn to record kwargs.
    captured = {}

    async def fake_stream(turn_input, *, abort_event=None, turn_id=""):
        captured["abort_event"] = abort_event
        captured["turn_id"] = turn_id
        yield DoneEvent(turn_status="completed", turn_id=turn_id)

    mocks.agent_core.stream_turn = fake_stream

    await ta.add_segment("s1", SegmentInput(session_id="s1", text="hi", channel="cli"))
    # Drive policy to trigger (depending on fixture, may need to await silence
    # timer or call _evaluate_policies directly — match the file's existing
    # pattern for triggering invocation).
    # ... await invocation completion ...
    assert captured["turn_id"]  # non-empty uuid
    assert captured["abort_event"] is ta._sessions["s1"].current_turn.abort_event
```

Run, expect FAIL. Implement, expect PASS. Update `_invoke` body:

```python
    async def _invoke(self, turn: Turn) -> None:
        """Call agent_core.stream_turn() with this turn's abort signal.

        Pushes events into the turn's own event_queue. On abort,
        cancel() has already enqueued the terminal DoneEvent — this
        method simply exits without enqueuing further events.
        """
        assembled_text = " ".join(s.text for s in turn.segments)
        turn_input = TurnInput(
            session_id=turn.session_id,
            user_message=assembled_text,
            channel=turn.channel,
            timestamp_ms=turn.started_at_ms,
            user_id=turn.user_id,
        )
        start = time.time()
        try:
            async for event in self._agent_core.stream_turn(
                turn_input,
                abort_event=turn.abort_event,
                turn_id=turn.turn_id,
            ):
                if turn.abort_event.is_set():
                    return
                await turn.event_queue.put(event)
                if isinstance(event, DoneEvent):
                    turn.status = TurnStatus.COMPLETED
                    return
        except asyncio.CancelledError:
            # cancel() already enqueued Done(interrupted)
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
            turn.status = TurnStatus.COMPLETED
            await turn.event_queue.put(
                DoneEvent(
                    turn_status="abandoned",
                    turn_id=turn.turn_id,
                    latency_ms=int((time.time() - start) * 1000),
                )
            )
```

Update the call site in `add_segment` / policy-trigger code to pass the `Turn` instance: `asyncio.create_task(self._invoke(session.current_turn))`.

Commit.

- [ ] **Step 6.5: Rewrite `cancel` using `Session`/`Turn`**

Failing test:

```python
@pytest.mark.asyncio
async def test_cancel_seals_turn_queue_and_signals_abort(
    turn_assembler_with_mocks,
):
    ta, mocks = turn_assembler_with_mocks
    # Configure stream_turn to push 5 sentences with awaits between them.
    sentences = [SentenceEvent(text=f"s{i}", sentence_index=i) for i in range(5)]
    invoke_started = asyncio.Event()

    async def slow_stream(turn_input, *, abort_event=None, turn_id=""):
        invoke_started.set()
        for s in sentences:
            await asyncio.sleep(0)  # yield
            if abort_event is not None and abort_event.is_set():
                return
            s.turn_id = turn_id
            yield s
        yield DoneEvent(turn_status="completed", turn_id=turn_id)

    mocks.agent_core.stream_turn = slow_stream

    # Trigger invocation
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="hi", channel="cli"))
    # ... force INVOKED via the file's existing pattern (e.g. await
    # ta._evaluate_policies-like helper or fast-track via test config) ...
    await invoke_started.wait()

    # Let two sentences through, then cancel.
    await asyncio.sleep(0); await asyncio.sleep(0)
    await ta.cancel("s1")

    session = ta._sessions["s1"]
    turn = session.current_turn
    assert turn.status == TurnStatus.INTERRUPTED
    assert turn.abort_event.is_set()

    # Drain the queue: must end with DoneEvent(interrupted), and any
    # SentenceEvents preceding it must come from before the cancel.
    drained = []
    async for ev in turn.iter_events():
        drained.append(ev)
    assert isinstance(drained[-1], DoneEvent)
    assert drained[-1].turn_status == "interrupted"
    # Cancelled before sentence 4, so s4 must not appear.
    assert not any(isinstance(e, SentenceEvent) and e.text == "s4" for e in drained)


@pytest.mark.asyncio
async def test_cancel_is_idempotent(turn_assembler_with_mocks):
    ta, mocks = turn_assembler_with_mocks
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="hi", channel="cli"))
    await ta.cancel("s1")
    qsize_after_first = ta._sessions["s1"].current_turn.event_queue.qsize()
    await ta.cancel("s1")  # second cancel must be no-op
    assert ta._sessions["s1"].current_turn.event_queue.qsize() == qsize_after_first


@pytest.mark.asyncio
async def test_cancel_on_unknown_session_is_noop(turn_assembler_with_mocks):
    ta, _ = turn_assembler_with_mocks
    await ta.cancel("does-not-exist")  # must not raise
```

Run, expect FAIL. Implement:

```python
    async def cancel(self, session_id: str) -> None:
        """Interrupt the active or waiting turn for this session.

        Idempotent: if the current turn is already terminal, no-op.
        Sets abort_event, cancels the invocation task, and seals the
        turn's queue with a terminal DoneEvent. The producer
        (_invoke / stream_turn / claude_wrapper) sees abort_event before
        every yield/put and exits without enqueuing further events.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        async with session._lock:
            turn = session.current_turn
            if turn is None or turn.status not in (TurnStatus.WAITING, TurnStatus.INVOKED):
                return
            new_status = (
                TurnStatus.INTERRUPTED
                if turn.status == TurnStatus.INVOKED
                else TurnStatus.ABANDONED
            )
            turn.status = new_status
            turn.abort_event.set()
            for task in (turn.invocation_task, turn.silence_task, turn.ceiling_task):
                if task is not None and not task.done():
                    task.cancel()
            await turn.event_queue.put(
                DoneEvent(
                    turn_status=new_status.value,
                    turn_id=turn.turn_id,
                )
            )
            logger.info(
                "turn_assembler.cancel",
                extra={
                    "operation": "turn_assembler.cancel",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn.turn_id,
                    "epoch": turn.epoch,
                    "turn_status": new_status.value,
                },
            )
```

Run tests, expect PASS. Commit.

- [ ] **Step 6.6: Refactor `subscribe` with cross-turn rollover loop**

Failing test:

```python
@pytest.mark.asyncio
async def test_subscribe_rolls_over_to_new_turn_after_done(turn_assembler_with_mocks):
    ta, mocks = turn_assembler_with_mocks
    # First turn: configure stream_turn to yield one sentence + Done.
    call = {"n": 0}

    async def stream(turn_input, *, abort_event=None, turn_id=""):
        call["n"] += 1
        yield SentenceEvent(text=f"reply{call['n']}", sentence_index=0, turn_id=turn_id)
        yield DoneEvent(turn_status="completed", turn_id=turn_id)

    mocks.agent_core.stream_turn = stream

    received = []

    async def consume():
        async for ev in ta.subscribe("s1"):
            received.append(ev)
            if len(received) == 4:  # two turns × (sentence + done)
                break

    consumer = asyncio.create_task(consume())
    # Drive turn 1
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="q1", channel="cli"))
    # ... wait for first Done received ...
    while len([e for e in received if isinstance(e, DoneEvent)]) < 1:
        await asyncio.sleep(0)
    # Drive turn 2
    ta._sessions["s1"].current_turn.status = TurnStatus.COMPLETED  # ensure terminal
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="q2", channel="cli"))
    await asyncio.wait_for(consumer, timeout=2.0)

    sentence_texts = [e.text for e in received if isinstance(e, SentenceEvent)]
    assert sentence_texts == ["reply1", "reply2"]


@pytest.mark.asyncio
async def test_subscribe_blocks_on_empty_session(turn_assembler_with_mocks):
    ta, _ = turn_assembler_with_mocks
    # No add_segment yet — subscribe must block until a Turn appears.
    received = []

    async def consume():
        async for ev in ta.subscribe("s1"):
            received.append(ev)
            break  # take the first event

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # subscribe should still be blocking
    assert not received
    # Now produce a turn
    # ... fixture-specific minimal trigger ...
    # consumer should now receive an event
```

Run, expect FAIL. Implement:

```python
    async def subscribe(
        self, session_id: str, user_id: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents for this session across multiple turns.

        Holds a long-lived connection: drains the current Turn's queue
        until DoneEvent, then awaits session.turn_changed for the next
        Turn to become current. Exits when session.ended is True.
        """
        session = self._get_or_create_session(session_id, user_id=user_id)
        # GH-149 opening_phrase emission goes here (preserved from prior
        # behaviour — call into the same helper, but it now emits onto
        # session.current_turn.event_queue after replace_turn).
        await self._emit_opening_phrase_if_first(session)
        seen: Optional[Turn] = None
        while not session.ended:
            turn = session.current_turn
            if turn is None or turn is seen:
                # Wait for a new Turn to be installed (replace_turn signals).
                session.turn_changed.clear()
                await session.turn_changed.wait()
                continue
            async for event in turn.iter_events():
                yield event
            seen = turn
```

> **Caveat:** the `turn_changed.clear()` + `wait()` pattern is single-subscriber-safe. Multi-subscriber requires `asyncio.Condition`; this is documented in code with a one-line comment per spec.

Update `_emit_opening_phrase_if_first` to take `Session` and push into `session.current_turn.event_queue` (creating a turn first if one isn't present).

Run tests, expect PASS. Run the full `test_turn_assembler.py` to surface any other tests that need updating for new field names; fix them. Commit.

- [ ] **Step 6.7: Refactor `session_end`**

Failing test:

```python
@pytest.mark.asyncio
async def test_session_end_cancels_active_turn_and_wakes_subscriber(
    turn_assembler_with_mocks,
):
    ta, _ = turn_assembler_with_mocks
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="hi", channel="cli"))
    session = ta._sessions["s1"]

    received = []
    finished = asyncio.Event()

    async def consume():
        async for ev in ta.subscribe("s1"):
            received.append(ev)
        finished.set()

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await ta.session_end("s1")
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    assert session.ended is True
```

Run, expect FAIL. Implement:

```python
    async def session_end(self, session_id: str) -> None:
        """Clean up all resources for a completed session.

        Cancels the active turn (if any), marks the session ended,
        and signals subscribers to exit their iteration loop.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        await self.cancel(session_id)  # idempotent
        session.ended = True
        session.turn_changed.set()  # wake any blocked subscriber
        # Pop after subscribers have had a chance to observe ended=True.
        # In practice subscribers see it on their next iteration; safe to pop.
        self._sessions.pop(session_id, None)
        logger.info(
            "turn_assembler.session_end",
            extra={
                "operation": "turn_assembler.session_end",
                "status": "success",
                "session_id": session_id,
            },
        )
```

> **Note:** popping `_sessions` immediately is fine because the subscriber holds its own `session` reference (captured at subscribe time). The dict pop just prevents new operations from finding it.

Run all `test_turn_assembler.py` — should be green. Run full `agent_core` suite to surface broader regressions:

```bash
cd agent_core && uv run pytest -x -q
```

Fix any cascading test failures in places that imported `SessionBuffer` or accessed it directly. Commit when green.

- [ ] **Step 6.8: Final commit for Task 6**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler.py
git commit -m "$(cat <<'EOF'
refactor(agent_core): TurnAssembler operates on Session/Turn (#224)

Replaces SessionBuffer with explicit Session + Turn objects:
- Per-turn event queue + abort_event (no shared session queue,
  no epoch-tag filter needed)
- subscribe() rolls across turns via session.turn_changed
- cancel() seals the cancelled turn structurally; producer respects
  abort_event before each put/yield
- session_end() cancels active turn and wakes subscribers

Public API unchanged. Existing tests updated to reference Session.current_turn
fields instead of SessionBuffer fields.
EOF
)"
```

---

## Task 7: Integration test — T4-T7 utterance pattern replay

End-to-end test against the new lifecycle: replay the four-cluster pattern from the 2026-04-24 voice triage and assert exactly one DoneEvent reaches the consumer and stale sentences from cancelled turns never appear.

**Files:**
- Create: `agent_core/tests/test_turn_assembler_integration.py`

- [ ] **Step 7.1: Write the integration test**

```python
"""Integration test for the cancel mechanism (#224 acceptance #4).

Replays the T4-T7 cluster from the 2026-04-24 voice UX triage:
multiple silence_triggers fired during a single user intent, leading
to multiple turns piling up. With the new lifecycle, only one
DoneEvent should reach the consumer for a given user intent.
"""
import asyncio

import pytest

from src.models import DoneEvent, SegmentInput, SentenceEvent
from src.turn import TurnStatus
from src.turn_assembler import TurnAssembler


@pytest.mark.asyncio
async def test_t4_t7_replay_yields_at_most_one_completed_done(
    turn_assembler_with_mocks,
):
    ta, mocks = turn_assembler_with_mocks

    # Configure stream_turn to be slow so cancel-and-replace can interrupt it.
    async def slow_stream(turn_input, *, abort_event=None, turn_id=""):
        for i in range(5):
            await asyncio.sleep(0.01)
            if abort_event is not None and abort_event.is_set():
                return
            yield SentenceEvent(text=f"sent{i}", sentence_index=i, turn_id=turn_id)
        yield DoneEvent(turn_status="completed", turn_id=turn_id)

    mocks.agent_core.stream_turn = slow_stream

    received = []
    finished = asyncio.Event()

    async def consume():
        async for ev in ta.subscribe("s1"):
            received.append(ev)
            if isinstance(ev, DoneEvent) and ev.turn_status == "completed":
                finished.set()
                break

    consumer = asyncio.create_task(consume())

    # Simulate VAD pausing four times inside one spoken sentence:
    # Each pause triggers a silence_trigger which (in the new design with
    # an INVOKED current_turn) results in cancel + replace. The successor
    # turn carries the next utterance segment.
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="hello", channel="cli"))
    # ... force INVOKED for turn 1 (fixture-specific) ...
    await asyncio.sleep(0.005)  # let turn 1 emit a couple of sentences
    await ta.cancel("s1")
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="who is", channel="cli"))
    await asyncio.sleep(0.005)
    await ta.cancel("s1")
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="the prime", channel="cli"))
    await asyncio.sleep(0.005)
    await ta.cancel("s1")
    await ta.add_segment("s1", SegmentInput(session_id="s1", text="minister of india", channel="cli"))
    # Final turn runs to completion
    await asyncio.wait_for(finished.wait(), timeout=2.0)
    consumer.cancel()

    completed_dones = [
        e for e in received
        if isinstance(e, DoneEvent) and e.turn_status == "completed"
    ]
    interrupted_dones = [
        e for e in received
        if isinstance(e, DoneEvent) and e.turn_status == "interrupted"
    ]
    assert len(completed_dones) == 1
    assert len(interrupted_dones) == 3  # three cancel calls

    # Stale sentences — those from interrupted turns — must all carry the
    # interrupted turn's turn_id, not the final turn's. Sentences from the
    # final turn must outnumber any single interrupted turn.
    final_turn_id = completed_dones[0].turn_id
    final_sentences = [
        e for e in received
        if isinstance(e, SentenceEvent) and e.turn_id == final_turn_id
    ]
    assert len(final_sentences) == 5  # full slow_stream yielded for last turn
```

> **Note on the fixture:** if `turn_assembler_with_mocks` doesn't exist, build one in `agent_core/tests/conftest.py` that constructs a `TurnAssembler` with mocked `agent_core` (whose `stream_turn` is the test-supplied async gen), mocked memory/trust/gateway, and a config that triggers invocation immediately on segment add (semantic_gate threshold = 0.0 or equivalent fast-track). Match the file's existing fixture patterns.

- [ ] **Step 7.2: Run the integration test**

```bash
cd agent_core && uv run pytest tests/test_turn_assembler_integration.py -v
```

Expected: PASS.

- [ ] **Step 7.3: Run the full agent_core suite**

```bash
cd agent_core && uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 7.4: Coverage check**

```bash
cd agent_core && uv run pytest --cov=src --cov-report=term-missing -q
```

Expected: coverage on `src/turn.py` ≥80%, `src/session.py` ≥80%, `src/turn_assembler.py` not lower than before, overall agent_core ≥70%.

- [ ] **Step 7.5: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/tests/test_turn_assembler_integration.py agent_core/tests/conftest.py
git commit -m "$(cat <<'EOF'
test(agent_core): integration test for T4-T7 cancel replay (#224)

Replays the four-cluster pattern from the 2026-04-24 voice triage.
With the new Session/Turn lifecycle, exactly one completed DoneEvent
reaches the consumer; interrupted turns produce three interrupted
DoneEvents; no stale sentences from cancelled turns leak past their
own queue.
EOF
)"
```

---

## Task 8: Voice processor regression test

Verify the reach_layer voice processor still works with the new wire-format addition (`turn_id`) and still receives only the cancelled turn's terminal Done plus successor turn events on barge-in.

**Files:**
- Modify: `reach_layer/voice/tests/test_pipecat_cancel_integration.py` (extend)

- [ ] **Step 8.1: Locate the existing pipecat cancel integration test**

```bash
find reach_layer/voice/tests -name '*pipecat*cancel*' -o -name '*pipecat*interrupt*' | head
```

If a file with this name doesn't exist, locate the closest existing voice integration test (likely `reach_layer/voice/tests/test_agent_core_llm_integration.py` or similar) and add the new test there.

- [ ] **Step 8.2: Add a regression test for stale-sentence suppression on barge-in**

In the located test file, add (use the file's existing fixtures for the AgentCoreLLMProcessor + mocked SSE source):

```python
@pytest.mark.asyncio
async def test_barge_in_drops_stale_sentences_after_done_interrupted(
    pipecat_processor_with_mocked_sse,
):
    """After receiving DoneEvent(turn_status='interrupted'), no further
    SentenceEvents from the cancelled turn reach TTS — guaranteed by
    the new Turn lifecycle (#224)."""
    proc, sse_source = pipecat_processor_with_mocked_sse

    # Configure the mocked SSE source to push: 2 sentences, then Done(interrupted),
    # then 0 more (per-turn queue is sealed). The next turn pushes a fresh
    # sentence + Done(completed).
    sse_source.push(SentenceEvent(text="cancelled-1", sentence_index=0, turn_id="t-old"))
    sse_source.push(SentenceEvent(text="cancelled-2", sentence_index=1, turn_id="t-old"))
    sse_source.push(DoneEvent(turn_status="interrupted", turn_id="t-old"))
    sse_source.push(SentenceEvent(text="fresh-1", sentence_index=0, turn_id="t-new"))
    sse_source.push(DoneEvent(turn_status="completed", turn_id="t-new"))

    tts_frames = await drain_tts_speak_frames(proc)

    tts_texts = [f.text for f in tts_frames]
    # Both pre-Done sentences from the cancelled turn DID reach TTS (they were
    # produced before cancel returned, which is acceptable per spec). What is
    # guaranteed is that no stale sentence appears AFTER the Done(interrupted).
    assert "fresh-1" in tts_texts
    # The order must put cancelled-* before Done(interrupted) before fresh-1.
    fresh_idx = tts_texts.index("fresh-1")
    cancelled_idxs = [i for i, t in enumerate(tts_texts) if t.startswith("cancelled-")]
    assert all(i < fresh_idx for i in cancelled_idxs)
```

> **Note:** the test asserts what spec §Error handling guarantees: "anything already enqueued sits on the cancelled turn's queue *before* the terminal Done." It does **not** require the voice processor to drop pre-Done sentences — that is #200 / #98's territory.

- [ ] **Step 8.3: Run the voice integration test**

```bash
cd reach_layer/voice && uv run pytest <test_file_from_step_8.1> -v
```

Expected: PASS.

- [ ] **Step 8.4: Run the full reach_layer voice suite**

```bash
cd reach_layer/voice && uv run pytest -x -q
```

Expected: all tests pass; in particular, the GH-149 opening_phrase test and barge-in acknowledgement test must remain green.

- [ ] **Step 8.5: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add reach_layer/voice/tests
git commit -m "$(cat <<'EOF'
test(reach_layer/voice): verify barge-in lifecycle with new turn_id wire field (#224)

Asserts that after Done(turn_status=interrupted) reaches the voice
processor, no further SentenceEvents from the cancelled turn appear
in the TTS frame stream. Guaranteed structurally by per-turn queues
in TurnAssembler.
EOF
)"
```

---

## Final verification

- [ ] **Step F.1: Run the full agent_core suite with coverage**

```bash
cd agent_core && uv run pytest --cov=src --cov-report=term-missing
```

Expected: ≥70% line coverage on `agent_core/src`. New files (`turn.py`, `session.py`) ≥80%.

- [ ] **Step F.2: Run the full reach_layer voice suite**

```bash
cd reach_layer/voice && uv run pytest -x -q
```

Expected: all tests pass.

- [ ] **Step F.3: Run any other module suites that touch StreamEvent or stream_turn**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
for d in knowledge_engine memory_layer trust_layer action_gateway observability_layer; do
  echo "=== $d ==="
  (cd "$d" && uv run pytest -x -q) || echo "FAILED in $d"
done
```

Expected: all pass (StreamEvent change is additive default-empty; no other module imports `SessionBuffer`).

- [ ] **Step F.4: Confirm acceptance criteria from the issue**

Walk through #224's five acceptance criteria and verify each maps to a passing test:
1. 5-sentence cancel → only 1-2 reach consumer + Done(interrupted): `test_cancel_seals_turn_queue_and_signals_abort`.
2. abort_event between LLM chunks: `test_stream_call_aborts_between_chunks_when_event_set`.
3. Epoch monotonicity: `test_replace_turn_bumps_epoch_monotonically`.
4. T4-T7 voice replay: `test_t4_t7_replay_yields_at_most_one_completed_done`.
5. No regression on barge-in / GH-149 / session_end: full reach_layer voice suite.

- [ ] **Step F.5: Final commit (if any cleanup)**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git status
# If anything stray, commit. Otherwise nothing to do.
```
