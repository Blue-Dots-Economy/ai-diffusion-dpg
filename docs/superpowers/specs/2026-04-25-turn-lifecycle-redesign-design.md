# Turn Lifecycle Redesign — Explicit `Session` and `Turn` Objects

**Issue:** [#224 — bug(agent_core): turn cancel is advisory only — stale events leak to TTS, blocks #200](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/224)
**Blocks:** #200 (cancel-and-fold)
**Adjacent:** #98 (TTS audio mid-playback flush)
**Date:** 2026-04-25
**Status:** Design — pending implementation plan

---

## Problem

`TurnAssembler.cancel()` is advisory only. It flips `SessionBuffer.status` and requests cancellation of the invocation task, but events already enqueued by `_invoke` continue to be drained by the SSE subscriber and pushed to TTS regardless. The upstream `DoneEvent(interrupted)` arrives behind any number of stale `SentenceEvent`s that the consumer has no way to recognise as stale.

Root cause: `SessionBuffer` conflates three lifetimes — session-scoped (identity, channel), turn-scoped (segments, queue, abort, status), and subscriber-scoped (event delivery) — onto a single object. The cancel mechanism is a single in-memory flag with no shared abort signal across the four owners of a turn (assembler, orchestrator, queue, subscriber).

`asyncio.Task.cancel()` is cooperative: between the cancel request and the next `await` boundary inside `_invoke`, the loop may push more events. There is no per-event abort check, no per-turn epoch, and no structural guarantee that stale events stay sealed off from a successor turn.

This blocks #200. Cancel-and-fold semantics depend on cancel actually stopping the stream.

## Design summary

Introduce two explicit domain objects — `Session` (long-lived) and `Turn` (per-turn) — replacing the conflated `SessionBuffer`. Each `Turn` owns its event queue, its abort signal, and its segments. Cancellation becomes a structural property: a cancelled `Turn` is dead, its queue is sealed, and a successor `Turn` gets a fresh queue that the subscriber rebinds to.

Threaded through the orchestrator: `stream_turn` accepts an `abort_event` and stamps every emitted event with `turn_id`. `claude_wrapper.stream_call` accepts the same `abort_event` and breaks its chunk loop the moment it is set, closing the underlying httpx stream.

Tool calls and Trust Layer calls run to completion when abort fires mid-call (side-effect safety, safety-invariant preservation). Their results are discarded if the turn was aborted before the result would have been yielded.

## Architecture

Three objects replace today's `SessionBuffer`:

- **`Session`** (long-lived, `agent_core/src/session.py`) — owns `session_id`, `user_id`, `channel`, `current_turn`, `ended` flag, a per-session lock, and a `turn_changed` event for subscriber fan-out. Holds an internal monotonic `_epoch_counter`.
- **`Turn`** (per-turn, `agent_core/src/turn.py`) — owns `turn_id` (uuid), `epoch` (assembler-internal), `segments`, `status` (`TurnStatus` enum), `abort_event`, `event_queue`, `invocation_task`, policy timers (silence/ceiling), `started_at_ms`. Provides `iter_events()` async generator that drains its queue until `DoneEvent`.
- **`TurnAssembler`** (existing file, refactored) — same public API surface as today (`add_segment`, `subscribe`, `cancel`, `session_end`), reimplemented over `Session` + `Turn`. Internal `_invoke(turn)` threads `turn.abort_event` and `turn.turn_id` into `Orchestrator.stream_turn`.

The renaming over the issue's original "TurnContext / TurnController" framing: the per-turn object is a first-class domain entity that owns the queue, abort signal, and invocation task — not a metadata bag and not an external controller. `Turn` is the clearest name in the domain model.

### Component interfaces

**`Turn`**

```python
@dataclass
class Turn:
    turn_id: str
    epoch: int
    session_id: str
    channel: str
    user_id: str | None
    started_at_ms: int
    segments: list[SegmentInput] = field(default_factory=list)
    status: TurnStatus = TurnStatus.WAITING
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    invocation_task: asyncio.Task | None = None
    silence_timer: asyncio.TimerHandle | None = None
    ceiling_timer: asyncio.TimerHandle | None = None

    async def iter_events(self) -> AsyncIterator[StreamEvent]:
        """Drain queue until DoneEvent. Closes generator after Done."""
```

**`Session`**

```python
@dataclass
class Session:
    session_id: str
    user_id: str | None
    channel: str
    current_turn: Turn | None = None
    ended: bool = False
    turn_changed: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _epoch_counter: int = 0

    async def replace_turn(self, *, seed_segments: list[SegmentInput] = ()) -> Turn:
        """Atomically install a fresh Turn as current_turn.

        Precondition: prior current_turn (if any) is in a terminal state
        (COMPLETED, INTERRUPTED, or ABANDONED). Caller is responsible for
        having cancelled an active turn before calling replace_turn.
        Bumps _epoch_counter and assigns it to the new Turn.
        Sets turn_changed to wake subscribers."""
```

**`Orchestrator.stream_turn` — signature change**

```python
async def stream_turn(
    self,
    turn_input: TurnInput,
    *,
    abort_event: asyncio.Event | None = None,
    turn_id: str = "",
) -> AsyncGenerator[StreamEvent, None]:
```

- Checks `abort_event.is_set()` before each `yield`, before each tool dispatch, before trust_output, before memory_write scheduling.
- Stamps every emitted event with `turn_id` (default empty preserves backward compat for callers that don't pass it — e.g., the web direct mode and existing tests).
- On abort: exits cleanly with no further yields. Caller (`_invoke`) is responsible for the terminal `DoneEvent(interrupted)`.

**`ClaudeLLMWrapper.stream_call` — signature change**

```python
async def stream_call(
    self,
    messages: list[dict],
    *,
    abort_event: asyncio.Event | None = None,
    ...
) -> AsyncGenerator[StreamChunk, None]:
```

- Checks `abort_event.is_set()` between chunks; breaks loop and closes the httpx response on set. SDK's `CancelledError` handling stays as a fallback for blocked-on-network cases.

**`StreamEvent` schema change** (`agent_core/src/models.py`)

Additive: `turn_id: str = ""` on `SignalEvent`, `SentenceEvent`, `DoneEvent`. Default-empty preserves all existing tests and the web-direct path. SSE consumers that don't read it are unaffected. The session-internal `epoch` is **not** placed on the wire — it is assembler-internal for logs and ordering invariants only.

**`TurnAssembler.cancel`** — rewritten

```python
async def cancel(self, session_id: str) -> None:
    session = self._sessions.get(session_id)
    if session is None:
        return
    async with session._lock:
        turn = session.current_turn
        if turn is None or turn.status not in (TurnStatus.WAITING, TurnStatus.INVOKED):
            return  # idempotent
        new_status = (
            TurnStatus.INTERRUPTED if turn.status == TurnStatus.INVOKED
            else TurnStatus.ABANDONED
        )
        turn.status = new_status
        turn.abort_event.set()
        if turn.invocation_task is not None:
            turn.invocation_task.cancel()
        await turn.event_queue.put(
            DoneEvent(turn_status=new_status.value, turn_id=turn.turn_id)
        )
```

The cancel path does **not** drain the queue. Producer-side `abort_event` checks before each `put` close the stale-event window structurally; per-turn queue isolation closes it for any successor turn.

## Data flow

### Happy path

```
Reach Layer ──TranscriptionFrame──▶ TurnAssembler.add_segment(session_id, segment)
   │
   ├─ session = sessions[session_id]            (created lazily on first segment)
   ├─ if session.current_turn is None or current_turn.status in terminal:
   │      turn = await session.replace_turn(seed_segments=[])
   │      session.turn_changed.set()
   ├─ turn.segments.append(segment)
   └─ evaluate policy stack (semantic_gate / silence / ceiling)
          └─ on trigger: turn.status = INVOKED;
                          turn.invocation_task = create_task(_invoke(turn))

_invoke(turn):
   try:
      async for event in orchestrator.stream_turn(
              turn_input,
              abort_event=turn.abort_event,
              turn_id=turn.turn_id):
          if turn.abort_event.is_set():
              break
          await turn.event_queue.put(event)
          if isinstance(event, DoneEvent):
              turn.status = TurnStatus.COMPLETED
              break
   except CancelledError:
      pass   # cancel() already enqueued Done(interrupted)

subscribe(session_id):                    # one long SSE GET per session
   seen = None
   while not session.ended:
      turn = session.current_turn
      if turn is None or turn is seen:
          await session.turn_changed.wait()
          continue
      async for event in turn.iter_events():
          yield event
      seen = turn
```

### Cancel path

```
TurnAssembler.cancel(session_id)
   ├─ async with session._lock:
   ├─    turn = session.current_turn
   ├─    if terminal: return        # idempotent
   ├─    turn.status = INTERRUPTED (or ABANDONED if WAITING)
   ├─    turn.abort_event.set()
   ├─    turn.invocation_task.cancel()
   └─    turn.event_queue.put(DoneEvent(turn_status="interrupted", turn_id=turn.turn_id))
   #  Producer (_invoke / stream_turn / claude_wrapper) sees abort_event before
   #  every put/yield/chunk and exits without enqueuing further events.
   #  Subscriber finishes draining this turn (incl. terminal Done), then on next
   #  iteration sees session.current_turn == seen and awaits session.turn_changed
   #  for the next add_segment to install a fresh Turn.
```

## Error handling and edge cases

**Abort race window.** Between `cancel()` setting `abort_event` and `_invoke` next checking it, the producer may be mid-`put`. Per-turn queue isolation makes this benign: anything already enqueued sits on the cancelled turn's queue **before** the terminal `DoneEvent(interrupted)`. The subscriber consumes them in order and sees Done last; downstream consumers (voice processor) decide what to do with sentences emitted before Done — same as today, but bounded to events produced before `cancel` returned.

**LLM stream chunk in flight.** `claude_wrapper.stream_call` checks `abort_event` between chunks; on set, breaks the loop and closes the httpx stream. A partial sentence not yet meeting the per-sentence trust threshold is dropped — never yielded as a `SentenceEvent`. SDK-side `CancelledError` (from `invocation_task.cancel()`) is the fallback if the chunk loop is blocked on network read.

**Tool call in flight.** Tool runs to completion. After `await action_gateway.execute(...)` returns, `stream_turn` checks `abort_event` and exits without feeding the result back to the LLM and without yielding further events. Tool side effects (writes already applied externally; consent already granted) are kept; memory state is not updated. Cancelling external HTTP writes mid-flight would leave external systems in indeterminate state and is explicitly avoided.

**Trust output check in flight.** Trust call runs to completion (small, ~50ms), then `abort_event` is checked before yielding the (now stale) sentences. Safety invariant preserved: every output that *would have been* delivered was checked; the delivery just doesn't happen.

**Memory write scheduled at end of stream_turn.** `stream_turn` checks `abort_event` before scheduling the async memory-write task. If aborted, the write is not scheduled; the cancelled turn produces no memory state. The next turn re-reads the same state as turn N's start (consistent with #83's memory consistency invariant).

**`session_end` while a turn is INVOKED.** Internally calls `cancel(session_id)` first (sets abort, cancels task, seals queue), then sets `session.ended = True` and `session.turn_changed.set()` to wake subscribers. Subscriber sees `session.ended` on next iteration and exits its generator.

**`add_segment` arriving while current turn is INTERRUPTED but not yet GC'd.** Status check in `add_segment` treats `INTERRUPTED`, `COMPLETED`, and `ABANDONED` identically — installs a new `Turn` via `session.replace_turn`. The old `Turn` object is GC'd once the subscriber finishes draining its terminal Done.

**Multiple cancels in quick succession.** `cancel()` is idempotent: status check at top of the locked section returns early if `current_turn` is already in a terminal state. No duplicate Done emitted, no double-cancel of the task.

**Concurrent `cancel` and `add_segment`.** Both acquire `session._lock` for the rollover-critical region. `add_segment` sees the cancel's status update; `cancel` sees `add_segment`'s installed turn. The lock makes the rollover atomic.

**Empty session corner.** First-ever `subscribe` on a brand-new session: `session.current_turn is None`, subscriber awaits `turn_changed`. First `add_segment` installs a Turn and sets `turn_changed` — subscriber wakes.

**Multi-subscriber fan-out.** Today's PoC has one subscriber per session (voice processor or web SPA tab). `turn_changed` as an `asyncio.Event` is fine for one subscriber. If multi-subscriber lands (e.g., observability hooking the stream), the field switches to `asyncio.Condition`. This is documented in code with a one-line comment but not implemented now.

## Testing strategy

**Unit tests** (`agent_core/tests/`):

- `test_turn.py` (new) — Turn state transitions, `iter_events` drains until Done, `abort_event` semantics, queue isolation between turns.
- `test_session.py` (new) — `replace_turn` swap atomicity, `_epoch_counter` monotonicity, `turn_changed` signaling, `ended` flag.
- `test_turn_assembler.py` (extended):
  - Stale-event suppression (Acceptance #1): mock `stream_turn` yielding 5 `SentenceEvent`s with awaits between them; call `cancel()` after sentence 2; subscriber receives sentences 1–2 + `DoneEvent(interrupted)` and nothing further. Verify by asserting the cancelled turn's queue is sealed and a fresh `add_segment` installs a new Turn whose events arrive without contamination.
  - Epoch monotonicity (Acceptance #3): back-to-back cancel + `add_segment` produces strictly increasing `Turn.epoch`.
  - Cancel idempotency: double `cancel()` does not enqueue two Dones.
  - Concurrent cancel + add_segment: lock prevents torn state.
  - Subscriber rollover: subscriber drains turn N's Done, then receives turn N+1's events without busy-loop or missed events. Test the "N+1 was cancelled before subscriber reached it, so subscriber jumps to N+2" race.
  - Empty session: subscribe on a fresh session blocks on `turn_changed`; first `add_segment` wakes it.
- `test_orchestrator_stream.py` (extended):
  - Abort between LLM chunks (Acceptance #2): mock `stream_call` yielding chunks with awaits; set `abort_event` after chunk 2; no further `SentenceEvent` yielded.
  - Abort before tool dispatch: tool not called.
  - Abort after tool returns: tool ran but result not fed back to LLM, no further yields.
  - Abort before trust_output: trust_output call runs (safety invariant), but no yield.
  - Abort before memory_write: memory write task not scheduled.
  - `turn_id` stamping: every emitted event carries the passed `turn_id`.
- `test_claude_wrapper.py` (extended):
  - Abort breaks chunk loop: mock httpx response yielding chunks; setting `abort_event` mid-stream causes loop to exit and stream to close.
  - No `abort_event` passed: existing behavior unchanged.

**Integration tests:**

- `agent_core/tests/test_turn_assembler_integration.py` (new): full pipeline with real `Orchestrator` + mocked LLM/Trust/Action — replay the T4–T7 utterance pattern from the 2026-04-24 voice triage; assert exactly one `DoneEvent` reaches the consumer and stale sentences from cancelled turns never appear (Acceptance #4).
- `reach_layer/voice/tests/test_pipecat_cancel_integration.py` (extended): with the new lifecycle, replay a barge-in mid-response; voice processor receives `DoneEvent(interrupted)` and does **not** receive prior turn's `SentenceEvent`s after that point. Asserts no regression on GH-149 opening_phrase emission and on barge-in acknowledgement flow.

**Regression coverage:**

- Existing 457+ agent_core tests pass with default `abort_event=None` and default `turn_id=""` — schema change is additive, signatures are backward-compatible.
- Existing 217 reach_layer Python tests pass; voice processor receives the new `turn_id` field on events but reads it only optionally.
- Web SPA SSE tests pass — `turn_id` is an extra field in the JSON, ignored by current consumer.

**Coverage target:** ≥70% on `agent_core/` per project rules; new files (`turn.py`, `session.py`) at ≥80% (small, focused).

## Acceptance criteria

Mapping to the issue's acceptance criteria:

1. With a long-running mocked `stream_turn` that pushes 5 sentences, calling `cancel()` after sentence 2 → only sentences 1 and 2 reach the SSE consumer, plus `DoneEvent(turn_status="interrupted")`. Sentences 3–5 are never enqueued (producer respects `abort_event` before each `put`). Verified by `test_turn_assembler.py::test_cancel_stops_event_emission`.
2. `abort_event` is checked between LLM stream chunks — once set, no further chunks emit events. Verified by `test_orchestrator_stream.py::test_abort_breaks_llm_chunk_loop` and `test_claude_wrapper.py::test_abort_event_closes_stream`.
3. Epoch monotonicity — back-to-back cancel + invoke produces strictly increasing `Turn.epoch` values. Verified by `test_session.py::test_epoch_counter_monotonic`.
4. Integration test in reach_layer voice: replay the T4–T7 utterance pattern with the new lifecycle in place — only one `DoneEvent` reaches the consumer; stale sentences are dropped before TTS. Verified by `test_pipecat_cancel_integration.py::test_t4_t7_replay`.
5. No regression on the existing barge-in path, the GH-149 opening_phrase emission, or session_end semantics. Verified by existing test suites passing unchanged.

## Out of scope

- **Fold-vs-replace decision (#200).** The `Turn(seed_segments=...)` constructor parameter is the data shape the fold policy will use, but the policy itself — when to fold vs. when to replace — is decided in #200. This spec deliberately leaves `add_segment` calling `replace_turn(seed_segments=[])` for now; #200 changes the seed list it passes.
- **TTS audio mid-playback flush (#98).** This spec only guarantees that stale sentences never *reach* TTS server-side. Audio already buffered in the telephony provider is #98's concern.
- **VAD tuning (#206 / Wave 3).**
- **Trust Layer / Memory Layer interface changes.** Abort propagates via `asyncio.CancelledError` for those today and stays unchanged. Mid-call aborts let the call complete and check `abort_event` afterward.
- **Multi-subscriber fan-out.** Single-subscriber `Event` is sufficient for current PoC; switch to `Condition` only when multi-subscriber lands.

## Impact on #200

Per-turn queues and `Turn`-owned segments make #200 a small policy change on top of this spec rather than a parallel structural concern:

- The "no sentences from cancelled turn reach TTS" criterion becomes structural, not test-only. The cancelled `Turn`'s queue is sealed; the subscriber rolls to the new `Turn`'s fresh queue.
- Segment custody moves from `SessionBuffer` to `Turn`. The fold becomes one constructor call: `session.replace_turn(seed_segments=old.pending_segments)`.
- `add_segment`'s policy change for #200 reduces to: detect `current_turn.status == INVOKED`, call `cancel(session_id)`, then `replace_turn(seed_segments=...)`. The cancel mechanism, abort propagation, and consumer drop semantics are all already in place from this spec.

## References

- `agent_core/src/turn_assembler.py:601-636, 920-996` — current `cancel` and `_invoke`.
- `agent_core/src/orchestrator.py:2193+` — `stream_turn` (entry point that needs `abort_event` parameter).
- `agent_core/src/llm_wrapper/claude_wrapper.py` — `stream_call` (gains `abort_event` parameter).
- `agent_core/src/models.py:233-320` — `StreamEvent` family (gains `turn_id` field).
- `reach_layer/voice/src/pipecat_services/agent_core_llm.py:102-200, 342-410` — voice consumer (no required changes; receives `turn_id` it can optionally use).
- `docs/superpowers/specs/2026-04-24-voice-ux-triage.md` §3 P2-A — original symptom.
