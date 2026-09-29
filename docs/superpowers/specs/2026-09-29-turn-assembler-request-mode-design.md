# TurnAssembler for streaming turns — request mode, safe-point abort, carry-over

**Date:** 2026-09-29 (revised same day after code review of the call sites)
**Status:** Design — approved; implementation plan in
`docs/superpowers/plans/2026-09-29-turn-assembler-request-mode.md`
**Evidence:** `voicera-cancelled-turns.md` (24 Sep 2026 PoC, 22 of 229 turns cancelled)
**Builds on:** `2026-04-14-agent-core-turn-assembler-spec.md` (#72),
`2026-04-25-turn-lifecycle-redesign-design.md` (#224), issue #200 (cancel-and-fold),
`2026-09-21-voicera-llm-shim-design.md` (bridge), #193 (`recent_tool_exchanges`)
**Code references:** `deploy/voicera-vm` @ `cdeb622`

---

## 1. Problem

A client may close a streaming turn's request at any moment. VoicERA (pipecat) does so
whenever the caller starts speaking, whether or not the bot is talking, and calls also
end mid-turn. Cancelling on new speech is standard practice for voice stacks built on a
*stateless* LLM whose context the client owns. Agent Core is not stateless: a turn reads
and writes memory and executes schema-declared tools with side effects. Today an
interrupted streaming turn:

1. **Loses the user's words.** On `/stream_turn` (direct mode) the turn dies with the
   connection. On the session path, `add_segment` seeds the successor with only the new
   segment (`turn_assembler.py:344`, `folded_segment_count: 1`). Despite the name,
   nothing is folded. That also defeats #200's own case, where the carried segment is
   the first half of one sentence split by VAD.
2. **Loses what it did.** Tool exchanges are persisted to `recent_tool_exchanges` only at
   step 11 (`orchestrator.py:4040-4079`); every abort check before that `return`s. A
   round is captured only at the start of the *next* LLM round (`:3856-3863`), so an
   abort after any `tool_end` (`:3815`, `:3973`) drops the round that just finished.
3. **May be cut inside a tool call.** Barge-in and `cancel()` call `task.cancel()`
   (`turn_assembler.py:338-340`, `:664`), and Starlette cancels the direct-mode generator
   on disconnect. `CancelledError` can therefore land inside an Action Gateway await.
4. **Leaves the orchestrator suspended.** `_invoke` returns as soon as it sees the abort
   flag (`turn_assembler.py:1011-1014`) without closing the `stream_turn` generator. The
   orchestrator stays parked at a `yield`; nothing after it, including any `finally`,
   runs until garbage collection.
5. **Is not cancellable at all on the bridge path.** `/stream_turn` never registers with
   the TurnAssembler, so the bridge's `DELETE /sessions/{id}/active_turn` returned 404 on
   22 of 22 cancels.

Consequence on 24 Sep: content lost in 4 turns, and one turn (three tool rounds) issued
writes the next turn knew nothing about.

## 2. Scope

**In scope: every streaming turn.** `POST /stream_turn` (bridge) and the session
endpoints `/sessions/{id}/input` + `/events` (voice and CLI in session mode, web or MCP
when configured for session mode) all run through the TurnAssembler.

**Out of scope: `POST /process_turn`.** It is fire-and-wait: the client sends a complete
utterance and blocks for the whole reply, so there is no partial delivery to interrupt and
no turn assembly to do. Web, MCP, CLI and voice in `direct` assembly mode use it and are
unchanged, as is the sync `process_turn` pipeline behind it.

**Goals**

- G1. One turn-execution path for every streaming channel: the TurnAssembler.
- G2. Closing a request never ends a turn uncontrolled. Turns stop only at safe points.
- G3. An interrupted turn's user utterances and completed tool rounds reach its successor.
- G4. Domain-agnostic and config-driven. No tool names, no use-case logic — tools are
  whatever the domain schema declares.
- G5. #224's guarantee holds: no sentence from an interrupted turn is delivered.
- G6. Agent Core stays stateless between turns (CLAUDE.md guideline 5). Only the *live*
  turn (its task, queue, abort signal) is in-process; everything a successor needs is in
  Memory Layer.

**Non-goals**

- Client-side interruption policy (VoicERA's barge-in settings, hold-phrase timing).
- Handling of request `metadata` (deferred).
- Idempotency of upstream tools — a domain / Action Gateway concern, tracked separately.
- Cross-replica *interruption* (§4.8).

## 3. Design overview

```
            segment stream                          request per turn
   voice, cli ─ POST /sessions/{id}/input        bridge ─ POST /stream_turn
             └ GET  /sessions/{id}/events                 (SSE until DoneEvent)
                        │                                    │
                        ▼                                    ▼
              ┌──────────────────── TurnAssembler core ───────────────────┐
              │ Session: one active Turn     trigger policy stack         │
              │ cooperative interrupt        drain predecessor   eviction │
              │ Turn = assembler-owned task; connections attach/detach    │
              └───────────────────────────────┬───────────────────────────┘
                                              ▼
                  AgentCore.stream_turn(turn_input, abort_event, turn_id, record)
                  fold carry-over on entry · capture each tool round first
                  persist interrupted state on exit (finally)
                                              │
                                              ▼
                     Memory Layer (session scope): turn_carryover,
                     recent_tool_exchanges (delivered: false)
```

Responsibilities split cleanly:

- **TurnAssembler** decides *when* a turn stops (interrupt, drain, one-at-a-time).
- **`stream_turn`** decides *what survives* (fold on entry, persist on interrupted exit),
  and it does so through Memory Layer, so any replica can serve the successor.

## 4. Components

### 4.1 `TurnAssemblerBase` interface

Existing methods keep their meaning: `add_segment`, `subscribe`, `cancel`, `session_end`.
Added:

| Method | Purpose |
| --- | --- |
| `async submit(session_id, segment) -> Turn` | Request-adapter entry. Interrupts any active turn (`reason="new_input"`), installs the successor with `predecessor` set, invokes it immediately, returns it. |
| `async attach(turn) -> AsyncIterator[StreamEvent]` | Yields one turn's events until its `DoneEvent`. |
| `detach(turn, reason) -> None` | **Synchronous** (callable from a generator `finally` during `GeneratorExit`, where `await` is illegal). If the turn is still INVOKED and `on_disconnect` is `abort`, interrupts it. |

`cancel(session_id)` becomes cooperative (§4.4).

### 4.2 Ingress adapters

- **Segment stream** (`/sessions/{id}/input`, `/events`): contract unchanged. Uses
  `add_segment` + `subscribe` with the configured trigger policies.
- **Request-scoped** (`/stream_turn`): the handler builds a `SegmentInput` from
  `ProcessTurnRequest`, calls `submit`, streams `attach(turn)` as SSE, and calls
  `detach(turn, "disconnect")` from `finally` if no `DoneEvent` was read. The turn task
  belongs to the assembler, so Starlette cancelling the generator no longer touches it.
  Trigger is always immediate: the client already decided the turn is complete, so the
  semantic gate, silence trigger and ceiling are skipped (the shim spec §8 warned against
  double assembly).
- When `create_orchestration_app` gets no assembler, `/stream_turn` keeps today's direct
  behaviour (back-compat for tests and embedders).
- `DELETE /sessions/{id}/active_turn` stays for segment-stream clients. The bridge stops
  calling it (§6).
- GH-149 opening-phrase emission remains a `subscribe()` behaviour only.

### 4.3 Session, Turn, TurnRecord

- **`TurnRecord`** (new, `models.py`) is a mutable per-turn ledger the assembler creates
  and `stream_turn` fills. It carries `captured_exchanges`, `prior_exchanges`,
  `max_items`, `segments`, `fold_ran`, `last_stage`, `write_carryover` and `persist_task`.
  `stream_turn` creates its own when called without one.
- **`Turn`** gains `record: TurnRecord` and `predecessor: Optional[Turn]`.
- **`Session`** gains `last_activity_ms` and `subscribers: int`. Carry-over is **not**
  held on the Session.
- `TurnStatus` is unchanged. One active turn per session remains enforced by
  `Session._lock` and `replace_turn`'s precondition.

### 4.4 Interruption: cooperative abort and drain

Triggered by `on_new_input` (a segment or request arrives while a turn is INVOKED), by
`on_disconnect` (a request-scoped connection detaches), or by `cancel()`/`session_end()`.

1. `_interrupt(turn, reason)` (synchronous, no awaits): mark INTERRUPTED, set
   `turn.record.write_carryover` from policy, set `abort_event`, cancel the **timer** tasks
   only, and seal the queue with
   `DoneEvent(turn_status="interrupted", interrupted_at_stage=turn.record.last_stage)`.
   The invocation task is **never** cancelled.
2. The predecessor's `_invoke` keeps pulling from `stream_turn` and **discards** every
   event, so the orchestrator runs on to its next abort check and returns. Its `finally`
   then persists (§4.5).
3. The successor's `_invoke` first awaits the predecessor's invocation task and then its
   `record.persist_task`, both within one `interruption.drain_max_ms` budget.
4. On timeout the successor proceeds without the missing carry-over, and
   `turn_assembler.drain_timeout` is logged. There is no hard cancel: the predecessor
   still stops at its next safe point and persists late. Whatever it writes is consumed
   by the turn after.

`on_new_input: replace` keeps the abort but writes no utterance carry-over (the tool
rounds are still persisted, §4.5). `on_disconnect: continue` lets the turn run to
completion with no consumer. Its sentences are dropped, and its step-11 writes happen
normally.

### 4.5 `stream_turn`: safe points, capture, persistence

The existing body becomes `_stream_turn_impl`. The public `stream_turn` becomes a thin
wrapper:

- It tracks `record.last_stage` from each `SignalEvent`, and notes whether a
  `DoneEvent(turn_status="completed")` was produced.
- In `finally`, if the turn did not complete (aborted, errored, or closed early), it
  schedules `record.persist_task = create_task(_persist_interrupted(...))`. It never
  awaits in `finally`.

Inside the body:

- **Capture first.** Each tool round's exchange is captured into
  `record.captured_exchanges` immediately after the round's tools return and **before**
  its `tool_end` is yielded, so a turn stopped at that yield still has the round. The
  capture at the start of the next LLM round is removed (no double capture).
- **Persist on interrupt** (`_persist_interrupted`):
  - `recent_tool_exchanges` ← `prior + captured`, with each new exchange marked
    `delivered: false`. Same caps as #193.
  - `turn_carryover` ← `{segments, stopped_at_stage, turn_id, written_at_ms}` when
    `write_carryover` is set. The `current_question` write is skipped, since that
    question was never delivered.
  - If the turn was interrupted before its own fold ran, it first reads the existing
    `turn_carryover` and appends to it rather than overwriting.
- **Abort checks** stay where they are. LLM streaming aborts immediately; Trust Layer and
  tool calls run to completion (#224). Nothing cancels a dispatched tool call any more,
  because nothing calls `task.cancel()` on a turn.

### 4.6 Fold and replay

On entry, right after the step-1 context read, `_fold_carryover`:

1. Reads `bundle.session["turn_carryover"]`. If it is present, it is cleared in Memory
   Layer (awaited, so a later write from this turn cannot be reordered before it). It is
   ignored when older than `carryover.max_age_ms`. That stops a callback from folding the
   previous call's last utterance.
2. Sets `segments = (carried + [user_message])[-fold.max_segments:]` (no carried segments
   when `max_segments` is 0).
3. Stores them in `record.segments`, and joins them with a space as the turn's
   `user_message`.

Replay uses the existing #193 path. Exchanges marked `delivered: false` get
`carryover.undelivered_note` appended to their tool-result content, so the model knows
the user has not heard those results. The note's default text is in the DPG config and
domains may override it.

A turn that completes rewrites `recent_tool_exchanges` with the flags removed, even if it
captured nothing new. Undelivered assistant text is **not** carried over; the user never
heard it.

A chain of interruptions folds naturally: each interrupted turn's `segments` already
include what it folded.

### 4.7 Session eviction

Request-mode sessions never receive `session_end`. On session lookup, at most once per
sweep interval, the assembler evicts sessions that meet all three conditions:

- idle longer than `session_idle_ttl_ms`
- no WAITING or INVOKED turn
- no attached subscriber

Eviction runs `session_end`. It is purely in-process housekeeping: Memory Layer state is
untouched.

### 4.8 Multiple replicas

- Same replica (a single replica, as on the VM today, or sticky routing by `session_id`):
  full behaviour.
- Different replica: the new request finds no live turn locally, so there is nothing to
  interrupt or drain. It folds whatever `turn_carryover` already exists. The old turn
  runs to completion on its replica and persists as a normal turn; its sentences go
  nowhere, because its connection is closed. This is documented as a degraded mode, not
  solved here.

## 5. Configuration

Under `channels.<name>.turn_assembler`, falling back to `reach_layer.turn_assembler`,
merged per sub-section.

```yaml
turn_assembler:
  semantic_gate:    { enabled: false, confidence_threshold: 0.75 }  # unchanged
  silence_trigger:  { silence_ms: 400 }                              # unchanged
  max_wait_ceiling: { max_wait_ms: 8000 }                            # unchanged
  # the three above do not apply to /stream_turn (trigger is immediate)
  interruption:
    on_new_input: abort_and_fold     # abort_and_fold | replace
    on_disconnect: abort             # abort | continue
    drain_max_ms: 3000
  fold:
    max_segments: 3                  # 0 disables folding
  carryover:
    max_age_ms: 60000                # older carry-over is discarded, not folded
    undelivered_note: "…"            # default in dev-kit/dpg/agent_core.yaml
  session_idle_ttl_ms: 1800000       # read from reach_layer.turn_assembler only
```

Per `.claude/rules/runtime-devkit-sync.md` the same PR updates:

- the runtime schema, `agent_core/src/schema/config.py`
- the domain mirror, `dev-kit/dev_kit/schemas/domain/agent_core.py`
- the DPG mirror, `dev-kit/dev_kit/schemas/dpg/agent_core.py`
- the flat copy, `dev-kit/dev_kit/schema.py`
- the framework defaults, `dev-kit/dpg/agent_core.yaml`
- the tests, `dev-kit/tests/schemas/domain/test_agent_core.py`

No FIELD_RULES entry is added: these are framework defaults that the wizard does not ask
about.

## 6. Bridge changes (`reach_layer/bridge`)

- Stop cancelling on disconnect. Delete `AgentCoreClient.cancel_turn`,
  `_schedule_cancel` and `_pending_cancel_tasks`. Closing the upstream `/stream_turn`
  stream is the signal.
- `to_turn_request` is unchanged. It forwards only the newest `user` message: shim spec
  §11.1 stands, because Agent Core folds from its own record.

## 7. Events and observability

- `DoneEvent` gains `interrupted_at_stage: Optional[str] = None`.
- New structured logs follow the logging rule: `operation`, `status`, `latency_ms` where
  timed, and no message content.
  - `turn_assembler.interrupt_requested`: `reason`, `stage`
  - `turn_assembler.safe_point_reached`: `stage`, `drain_ms`
  - `turn_assembler.drain_timeout`: `drain_ms`
  - `turn_assembler.session_evicted`
  - `orchestrator.carryover_folded`: `folded_segment_count`
  - `orchestrator.carryover_discarded`: `reason` = `stale` | `malformed`
  - `orchestrator.interrupted_persist`: `exchange_count`, `segment_count`,
    `stopped_at_stage`
- `turn_assembler.cancel_and_fold` keeps its name for continuity.

## 8. Behaviour changes by channel

| Path | Before | After |
| --- | --- | --- |
| `/stream_turn` (bridge) | Turn dies with the connection; no fold, no carry-over, no one-turn rule | Assembler-owned turn; abort at a safe point on disconnect or new input; fold and carry-over |
| session endpoints (voice, CLI, session-mode web or MCP) | Barge-in `task.cancel()`; successor gets only the new segment | Cooperative abort; successor folds the carried utterances and sees the carried tool rounds |
| `/process_turn` | — | Unchanged |
| all streaming | Completed tool rounds of an aborted turn discarded (#224) | Persisted with `delivered: false` and replayed |

#224's guarantee is unchanged: a sealed turn's queue yields nothing after its
`DoneEvent`, and the predecessor's `_invoke` discards its remaining events.

## 9. Edge cases

| Case | Behaviour |
| --- | --- |
| Call ends mid-turn, no successor | `on_disconnect: abort`: tool rounds are persisted and a carry-over is written. A callback within `max_age_ms` folds it; after that it is discarded. |
| Turn finished but the client closed before reading it | The `DoneEvent` was produced, so step-11 writes ran and there is no carry-over. The response is lost, as before. |
| Two requests race on one session | Serialised by the session lock; the second interrupts the first. |
| Repeated check-ins while turns keep being interrupted | Fold capped at the `max_segments` newest. |
| Drain timeout while a tool is in flight | Successor proceeds; the predecessor persists late and the turn after consumes it. |
| Interrupted before step 1 finished | `_persist_interrupted` appends to the existing carry-over instead of overwriting it. |
| Agent Core error mid-turn (`DoneEvent` abandoned) | Treated as not completed: tool rounds are persisted and a carry-over is written. |
| Successor on another replica | See §4.8. |
| Agent Core restart mid-turn | The live turn is lost; state already in Memory Layer survives. |

## 10. Testing

- **Unit:**
  - config resolution and validation, in runtime and dev-kit
  - `TurnRecord` plumbing
  - capture before `tool_end`, for round 1 and round N
  - persisting on interrupt, including the append-before-fold case
  - fold: age limit, cap, `max_segments: 0`, clearing
  - the replay note, and flags cleared on completion
  - assembler: cooperative interrupt, draining the predecessor and its timeout,
    `submit`/`attach`/`detach`, eviction
- **Integration:**
  - `/stream_turn` through a real assembler: disconnect aborts, a new request folds
  - session-endpoint regressions
  - `test_turn_path_identity_parity.py`
- **Replay (24 Sep):** a real `AgentCore` and a real `TurnAssembler` over an in-memory
  Memory Layer fake.
  - Call-1 class A: a substantive utterance, then a check-in during the first LLM call.
  - Row 9: three tool rounds, then a disconnect.
- **Bridge:** no cancel on disconnect; translation unchanged.

## 11. Open questions

1. The default `undelivered_note` wording, and whether it should be localisable per
   domain locale.
2. The `fold.max_segments` default: 3 is a guess, and the 24 Sep data had cascades of
   up to 7.
