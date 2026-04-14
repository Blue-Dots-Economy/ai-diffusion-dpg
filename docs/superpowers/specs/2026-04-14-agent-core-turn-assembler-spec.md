# Agent Core: TurnAssembler — Multi-Segment Input with Policy Stack — Spec

**Status:** Approved for implementation
**Date:** 2026-04-14
**Issue:** [#72](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/72)
**Sub-tasks:** [#79](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/79) [#80](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/80) [#81](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/81) [#82](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/82) [#83](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/83)
**Depends on:** Async orchestrator migration (#71)
**Blocks:** Reach Layer restructuring spec (#73)

---

## Problem

Real users do not speak or type in single clean utterances. The current 1:1 synchronous model treats each VAD segment as an independent turn:

- Split VAD segments: `"मुझे"` then `"जॉब चाहिए"` → two `process_turn` calls instead of one
- Caller interrupts mid-response (barge-in)
- Quick correction before the agent responds ("Wait, actually…")
- Multiple short sentences spoken rapidly: "Yes. My name is Rahul. I called yesterday."

None are handled correctly today.

---

## Architecture

`TurnAssembler` sits between the HTTP server and the orchestrator for all session-based input. It is **channel-agnostic** — all channels (CLI, web, voice) route through it. Channel-specific behaviour is controlled by YAML config (silence thresholds, max wait ceilings, etc.).

### New file: `agent_core/src/turn_assembler.py`

### TurnStatus

```python
class TurnStatus(str, Enum):
    WAITING     = "waiting"      # segments arriving, policy not yet triggered
    INVOKED     = "invoked"      # LLM call in flight
    COMPLETED   = "completed"    # DoneEvent emitted successfully
    INTERRUPTED = "interrupted"  # cancel() called while INVOKED
    ABANDONED   = "abandoned"    # max wait ceiling fired with no segments, or cancel() while WAITING
```

### SessionBuffer

```python
@dataclass
class SessionBuffer:
    session_id: str
    segments: list[str]           # accumulated text segments in order
    status: TurnStatus
    event_queue: asyncio.Queue    # StreamEvents pushed here by stream_turn()
    silence_task: asyncio.Task | None
    ceiling_task: asyncio.Task | None
    invocation_task: asyncio.Task | None
    created_at_ms: int
    _lock: asyncio.Lock           # guards WAITING → INVOKED transition
```

### TurnAssemblerBase ABC

```python
class TurnAssemblerBase(ABC):
    @abstractmethod
    async def add_segment(self, session_id: str, text: str) -> None:
        """Accept a text segment for this session and evaluate policies."""

    @abstractmethod
    async def subscribe(self, session_id: str) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents for this session until DoneEvent is received."""

    @abstractmethod
    async def cancel(self, session_id: str) -> None:
        """Interrupt the active or waiting turn for this session."""

    @abstractmethod
    async def session_end(self, session_id: str) -> None:
        """Clean up all resources for a completed session."""
```

### TurnAssembler concrete class

Holds `_sessions: dict[str, SessionBuffer]` in memory.

- `add_segment()`: creates buffer if new session, appends text, evaluates policy stack (see below).
- `subscribe()`: creates buffer if needed, yields from `event_queue` until `DoneEvent` received, calls `session_end()` after.
- `cancel()`: acquires lock, transitions to `INTERRUPTED` (if `INVOKED`) or `ABANDONED` (if `WAITING`), cancels all asyncio tasks, pushes `DoneEvent(turn_status=...)` to queue.
- `session_end()`: cancels all tasks, removes buffer from `_sessions`.

---

## Policy stack

Evaluated in order on each `add_segment()` call.

### 1. Semantic completeness gate (evaluated first)

Run NLU intent classifier (Haiku, reuses `nlu_processor.py`) on the fully assembled text. If `confidence >= threshold` and intent is not `unknown` → acquire lock, transition `WAITING → INVOKED`, trigger `stream_turn()` immediately. Do not start silence timer.

If confidence below threshold → fall through to silence trigger.

If NLU call fails (timeout/error): log structured error, fall through — never block on NLU infra failure.

Config:
```yaml
turn_assembler:
  semantic_gate:
    enabled: true
    confidence_threshold: 0.75
```

### 2. Silence trigger

An `asyncio.Task` sleeping for `silence_ms` milliseconds. Started when the first segment arrives (if semantic gate did not trigger). Reset (cancel + restart) on every subsequent `add_segment()`. If task fires while status is still `WAITING` → acquire lock, transition `WAITING → INVOKED`, trigger `stream_turn()`.

Config:
```yaml
turn_assembler:
  silence_trigger:
    silence_ms: 400   # tune per channel in domain config
```

### 3. Max wait ceiling

A second `asyncio.Task` set once when the buffer is first created. Never reset. Fires after `max_wait_ms`. If status is still `WAITING` → acquire lock, transition, trigger. If already `INVOKED` → no-op.

Config:
```yaml
turn_assembler:
  max_wait_ceiling:
    max_wait_ms: 8000
```

If both timers fire simultaneously: only the first to acquire the lock transitions state. The second is a no-op.

---

## Session-based HTTP interface

All reach layers call these endpoints exclusively. `POST /process_turn` and `POST /process_turn/stream` are never called by reach layers.

### POST /sessions/{session_id}/input
Submit a text segment. Returns 202 immediately.

```json
{"text": "मुझे जॉब चाहिए", "user_id": "...", "channel": "voice", "timestamp_ms": 1234567890}
```

Calls `turn_assembler.add_segment(session_id, text)`. Returns 422 if `text` is empty.

### GET /sessions/{session_id}/events
Long-lived SSE subscription. Reach layer opens once at session start.

Calls `turn_assembler.subscribe(session_id)`. Yields each `StreamEvent` as `data: <json>\n\n`. Closes after `DoneEvent`. On client disconnect: calls `turn_assembler.cancel(session_id)`.

### DELETE /sessions/{session_id}/active_turn
Interrupt the active turn. Returns 200 if session existed, 404 if not.

Calls `turn_assembler.cancel(session_id)`.

---

## Memory Layer consistency on cancelled turns

| Cancellation point | State |
|---|---|
| Before `stream_turn()` invoked (WAITING) | No Memory Layer interaction. Session state unchanged. Correct. |
| During `stream_turn()` (INVOKED) | Memory read (step 1) has happened. Memory write (step 13) has **not** happened. Session state unchanged. Correct. |

The async memory write task (`_schedule_flush()`) is cancelled when `stream_turn()` is interrupted. No partial writes occur. The next turn re-reads the same state and proceeds normally.

`DoneEvent.turn_status` carries `"interrupted"` or `"abandoned"` so the reach layer and Observability Layer know the turn was not completed. `TurnEvent` (observability) also includes `turn_status`.

---

## Config placement

All `turn_assembler` config lives under the channel namespace in domain YAML:

```yaml
reach_layer:
  cli:
    turn_assembler:
      semantic_gate: {enabled: true, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 200}
      max_wait_ceiling: {max_wait_ms: 10000}
  web:
    turn_assembler:
      semantic_gate: {enabled: true, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 1500}
      max_wait_ceiling: {max_wait_ms: 15000}
  voice:
    turn_assembler:
      semantic_gate: {enabled: true, confidence_threshold: 0.75}
      silence_trigger: {silence_ms: 400}
      max_wait_ceiling: {max_wait_ms: 8000}
```

Agent Core reads config once at startup. No runtime re-reads.

---

## Files changed

| File | Change |
|---|---|
| `agent_core/src/turn_assembler.py` | New — `TurnStatus`, `SessionBuffer`, `TurnAssemblerBase`, `TurnAssembler` |
| `agent_core/src/models.py` | `DoneEvent.turn_status` field; `TurnEvent.turn_status` for observability |
| `agent_core/src/servers/orchestration_server.py` | Three new session-based endpoints |
| `agent_core/src/orchestrator.py` | Wire `TurnAssembler` into server startup; cancel async write task on interruption |

---

## Execution order

`#79` (TurnAssembler core) must merge before `#80`, `#81` (policies) can begin. `#82` (endpoints) can proceed once `#79` is in. `#83` (DoneEvent + memory consistency) depends on `#79` and `#77` (stream_turn from #71).

Speculative execution is explicitly out of scope — wasted tokens and complexity outweigh the latency benefit at current scale.
