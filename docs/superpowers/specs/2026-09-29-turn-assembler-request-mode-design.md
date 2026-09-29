# TurnAssembler for every channel — request mode, safe-point abort, carry-over

**Date:** 2026-09-29
**Status:** Design — approved in conversation, pending written-spec review
**Evidence:** `voicera-cancelled-turns.md` (24 Sep 2026 PoC, 22 of 229 turns cancelled)
**Builds on:** `2026-04-14-agent-core-turn-assembler-spec.md` (#72),
`2026-04-25-turn-lifecycle-redesign-design.md` (#224), issue #200 (cancel-and-fold),
`2026-09-21-voicera-llm-shim-design.md` (bridge), #193 (`recent_tool_exchanges`)
**Code references:** `deploy/voicera-vm` @ `cb1ec68` (30 commits ahead of `main`;
`orchestrator.py` differs — implementation must be based on, or reconciled with, that branch)

---

## 1. Problem

A client may close a turn's request at any moment. VoicERA (pipecat) does so whenever
the caller starts speaking, whether or not the bot is talking; calls also end mid-turn.
That is standard practice for voice stacks built on a *stateless* LLM whose context the
client owns. Agent Core is not stateless: a turn reads and writes memory and executes
schema-declared tools with side effects. Today an interrupted turn:

1. **Loses the user's words.** On `/stream_turn` (direct mode) the turn dies with the
   connection. On the session path, `add_segment` seeds the successor with only the new
   segment (`turn_assembler.py:344`, `folded_segment_count: 1`) — despite the name,
   nothing is folded.
2. **Loses what it did.** Tool exchanges are persisted to `recent_tool_exchanges` only at
   step 11 (`orchestrator.py:4040-4079`); every abort check before that `return`s. The
   round that just finished is captured only at the start of the *next* LLM round
   (`:3858`), so an abort after any `tool_end` (`:3815`, `:3973`) drops it.
3. **May be cut inside a tool call.** Barge-in and `cancel()` call `task.cancel()`
   (`turn_assembler.py:338-340`, `:664`), and Starlette cancels the direct-mode generator
   on disconnect. There is no `asyncio.shield` in Agent Core, so `CancelledError` can land
   inside an Action Gateway await. #224's "tools run to completion" holds only on the
   `abort_event` path.
4. **Is not cancellable at all on the bridge path.** `/stream_turn` never registers with
   the TurnAssembler, so the bridge's `DELETE /sessions/{id}/active_turn` returned 404 on
   22 of 22 cancels.

Consequence on 24 Sep: content lost in 4 turns, and one turn (three tool rounds) issued
writes the next turn knew nothing about.

## 2. Goals and non-goals

**Goals**

- G1. The TurnAssembler is the single turn-execution path for **every** channel: voice,
  CLI (segment stream) and bridge, web, MCP (request per turn).
- G2. Closing a request never ends a turn uncontrolled. Turns stop only at safe points.
- G3. An interrupted turn's user segments and completed tool rounds reach its successor.
- G4. Entirely domain-agnostic and config-driven. No tool names, no use-case logic —
  tools are whatever the domain schema declares.
- G5. #224's guarantee holds: no sentence from an interrupted turn is delivered.

**Non-goals**

- Client-side interruption policy (VoicERA's barge-in settings, hold phrase timing).
- Handling of request `metadata` (deferred).
- `/process_turn` (blocking, manager_agent path) — unchanged.
- Idempotency of upstream tools — a domain/Action Gateway concern, tracked separately.
- Multi-replica Agent Core. Session state stays in-process; scaling out needs sticky
  routing by `session_id` (unchanged constraint).

## 3. Design overview

```
            segment stream                        request per turn
   voice, cli ─ POST /sessions/{id}/input     bridge, web, mcp ─ POST /stream_turn
             └ GET  /sessions/{id}/events                        (SSE until DoneEvent)
                        │                                    │
                        ▼                                    ▼
              ┌──────────────────── TurnAssembler core ───────────────────┐
              │ Session (one active Turn)   trigger policy stack          │
              │ interruption policy         fold   carry-over   eviction  │
              │ Turn = assembler-owned task; connections attach/detach    │
              └───────────────────────────────┬───────────────────────────┘
                                              ▼
                        AgentCore.stream_turn(turn_input, abort_event, carryover)
                        safe-point aborts · shielded tool rounds · capture-first
```

The core owns the turn. A connection only attaches to a turn to receive its events and
detaches when it closes. Detaching is an *input* to the interruption policy, never a
cancellation of the task.

## 4. Components

### 4.1 `TurnAssemblerBase` (core interface)

Existing methods keep their meaning: `add_segment`, `subscribe`, `cancel`, `session_end`.
Added:

| Method | Purpose |
| --- | --- |
| `submit(session_id, segment) -> Turn` | Request adapter entry. Applies the interruption policy to any active turn, creates the successor (with carry-over), triggers it immediately, returns it. |
| `attach(turn) -> AsyncIterator[StreamEvent]` | Yields one turn's events until its `DoneEvent`. |
| `detach(turn, reason)` | Called when a request-scoped connection closes before `DoneEvent`. Applies `on_disconnect`. |

`cancel(session_id)` becomes cooperative (§4.4).

### 4.2 Ingress adapters

- **Segment stream** (`/sessions/{id}/input`, `/events`) — unchanged contract. Uses
  `add_segment` + `subscribe`, trigger policies from config.
- **Request-scoped** (`/stream_turn`) — the handler builds a `SegmentInput` from
  `ProcessTurnRequest`, calls `submit`, streams `attach(turn)` as SSE, and calls
  `detach(turn, "disconnect")` from `finally` if `DoneEvent` was not sent. The turn task
  is created by the assembler, so Starlette cancelling the generator no longer touches it.
  Trigger is always immediate: the client has already decided the turn is complete, so
  semantic gate, silence trigger and ceiling are skipped (avoids the double assembly the
  shim spec §8 warned against).
- `DELETE /sessions/{id}/active_turn` stays for segment-stream clients. The bridge stops
  calling it (§6).
- GH-149 opening-phrase emission remains a `subscribe()` behaviour only.

### 4.3 Session and Turn

- `Session` gains `carryover: Optional[Carryover]` and `last_activity_ms`.
- `Turn` gains `stopped_at_stage: Optional[str]` and `captured_exchanges: list[dict]`
  (the orchestrator appends to it as rounds complete, so the assembler can read what a
  turn did without waiting for step 11).
- `TurnStatus` is unchanged. A new transitional flag `abort_requested_at_ms` on `Turn`
  distinguishes "INVOKED, draining" from "INVOKED, running" for logging and drain timing.
- One active turn per session remains enforced by `Session._lock` and
  `replace_turn`'s precondition.

### 4.4 Interruption: cooperative abort and drain

Triggered by `on_new_input` (a segment or request arrives while a turn is INVOKED) or by
`on_disconnect` (a request-scoped connection detaches).

1. Set `turn.abort_event`. Seal the turn's queue with
   `DoneEvent(turn_status="interrupted", interrupted_at_stage=…)`. Do **not**
   `task.cancel()`.
2. The orchestrator returns at its next abort check (§4.5).
3. The successor waits for the predecessor task to finish, bounded by
   `interruption.drain_max_ms`.
4. On timeout: hard-cancel the predecessor task. The tool round in flight is shielded, so
   it still completes; its result is merged into `recent_tool_exchanges` when it lands
   (late write). The successor proceeds without it and `turn_assembler.drain_timeout` is
   logged.
5. Build the carry-over (§4.6) from the predecessor and start the successor.

`on_new_input: replace` keeps today's behaviour (abort, no fold, no carry-over) for any
channel that wants it. `on_disconnect: continue` lets the turn run to completion with no
attached consumer; its sentences are dropped and its exchanges persist through step 11
normally.

### 4.5 Orchestrator safe points

Changes inside `stream_turn` (both the first round and the multi-round loop):

- **Capture first.** Immediately after each tool round's `tool_end`, capture the exchange
  (`_capture_tool_exchange`) and append it to both `_captured_exchanges_this_turn` and
  `turn.captured_exchanges`, *before* the abort check. Removes the gap at `:3815` / `:3973`.
- **Shield tool rounds.** Execute each round's tool calls inside `asyncio.shield` (one
  shielded task per round), so no cancellation — hard cancel after drain timeout, or any
  other `CancelledError` — interrupts a dispatched call.
- **Persist on abort.** Every abort return after at least one captured round merges the
  captured exchanges into `recent_tool_exchanges` (same `_merge_tool_exchanges`, same
  caps), with `delivered: false` on each new exchange. The `current_question` write is
  skipped, since that question was never delivered.
- **Record the stage.** Each abort return sets `turn.stopped_at_stage` to the last
  `SignalEvent.stage` emitted.
- Existing abort checks stay where they are. LLM streaming continues to abort
  immediately; Trust Layer calls keep #224's run-to-completion behaviour.

### 4.6 Carry-over and fold

```
Carryover
  segments          list[SegmentInput]   # user segments the interrupted turn held
  exchanges         list[dict]           # captured rounds, #193 shape, delivered=false
  stopped_at_stage  str | None
  from_turn_id      str
```

- Held on `Session.carryover` and consumed by the next turn only. It is dropped on
  eviction or `session_end`. The exchanges are durable independently, through
  `recent_tool_exchanges`.
- **Fold:** the successor's segments are `carryover.segments + [new]`, keeping the newest
  `fold.max_segments`. `_invoke` already joins segments with a space. This also covers
  #200's split-sentence case, where the carried segment is the first half of the same
  utterance.
- **Replay:** the successor reads `recent_tool_exchanges` as today (they were persisted
  on abort, §4.5). The replay builder renders exchanges marked `delivered: false` with
  `carryover.undelivered_note` appended to the tool-result content, so the model knows the
  user has not heard those results. The next normal persist clears the flag
  (exchanges replayed in a turn that completes are rewritten with `delivered: true`).
- Undelivered assistant text is **not** carried over; the user never heard it.

### 4.7 Session eviction

Request-mode sessions never receive `session_end`. A periodic sweep evicts sessions that
are idle (`now - last_activity_ms > session_idle_ttl_ms`) with no INVOKED turn, calling
`session_end`. Segment-stream sessions keep their existing explicit lifecycle and are also
covered by the sweep as a backstop.

## 5. Configuration

Under `channels.<name>.turn_assembler`, falling back to `reach_layer.turn_assembler`.
Both `agent_core/src/schema/config.py` and `dev-kit` (`dev_kit/schemas/domain/agent_core.py`,
`dev_kit/schemas/dpg/agent_core.py`) are `extra="forbid"` and are updated together.

```yaml
turn_assembler:
  semantic_gate:    { enabled: false, confidence_threshold: 0.75 }  # unchanged
  silence_trigger:  { silence_ms: 400 }                              # unchanged
  max_wait_ceiling: { max_wait_ms: 8000 }                            # unchanged
  # the three above are ignored on the request-scoped adapter (trigger is immediate)
  interruption:
    on_new_input: abort_and_fold     # abort_and_fold | replace
    on_disconnect: abort             # abort | continue
    drain_max_ms: 3000
  fold:
    max_segments: 3                  # 0 disables folding
  carryover:
    enabled: true                    # false = no fold, no delivered=false marking
    undelivered_note: ""             # default text lives in the DPG config; domains may override
session_idle_ttl_ms: 1800000         # reach_layer.turn_assembler level only
```

All defaults are channel-neutral. The DPG default config supplies `undelivered_note`
text; no domain config is required to adopt this.

## 6. Bridge changes (`reach_layer/bridge`)

- Stop calling `cancel_turn` on disconnect; delete `AgentCoreClient.cancel_turn` and the
  fire-and-forget helper in `server.py`. Closing the upstream `/stream_turn` stream is the
  signal.
- `to_turn_request` is unchanged: it forwards only the newest `user` message (shim spec
  §11.1 stands — Agent Core folds from its own record, so replayed client history is
  neither needed nor trusted).

## 7. Events and observability

- `DoneEvent` gains `interrupted_at_stage: Optional[str]`.
- New structured logs (`operation` / `status` / `session_id` / `turn_id` as elsewhere):
  `turn_assembler.interrupt_requested` (`reason`: `new_input` | `disconnect`),
  `turn_assembler.safe_point_reached` (`stage`, `drain_ms`),
  `turn_assembler.drain_timeout`,
  `turn_assembler.carryover_recorded` (`segment_count`, `exchange_count`),
  `turn_assembler.session_evicted`.
  `turn_assembler.cancel_and_fold` now reports the real `folded_segment_count`.
- Orchestrator: `orchestrator.tool_persist` gains `on_abort: bool`.

## 8. Behaviour changes by channel

| Channel | Before | After |
| --- | --- | --- |
| bridge, web, mcp (`/stream_turn`) | Turn dies with the connection; no fold; no carry-over; no single-active-turn rule | Assembler-owned turn; abort at safe point on disconnect or new input; fold and carry-over |
| voice, cli (session) | Barge-in `task.cancel()`; successor gets only the new segment | Cooperative abort; successor gets folded segments plus carry-over |
| all | Completed tool rounds of an aborted turn discarded (#224) | Persisted with `delivered: false` and replayed |

The #224 guarantee is unchanged: a sealed turn's queue never yields further events, so no
interrupted-turn sentence reaches a client.

## 9. Edge cases

| Case | Behaviour |
| --- | --- |
| Call ends mid-turn, no successor | `on_disconnect: abort`; exchanges persisted; carry-over dropped at eviction. A callback sees the exchanges through `recent_tool_exchanges`. |
| Turn finished but the client closed before reading it | Normal step-11 persist already ran; nothing extra. |
| Two requests race on one session | Serialised by the session lock; the second interrupts the first per `on_new_input`. |
| Repeated check-ins while turns keep being interrupted | Fold capped at `max_segments` newest; the substantive utterance survives while it is within the cap. |
| Drain timeout while a tool is in flight | Successor proceeds; the shielded round's result lands later and is merged into `recent_tool_exchanges`. |
| `carryover.enabled: false` | Behaves as `on_new_input: replace`, except that tool rounds are still shielded, captured first and persisted on abort (without the `delivered: false` marking). |
| Agent Core restart | In-memory carry-over lost; persisted exchanges survive. |

## 10. Testing

**Unit**

- Session/Turn: carry-over set and consumed once; `replace_turn` precondition; eviction.
- Interruption: `on_new_input` × `on_disconnect` combinations; no `task.cancel()` before
  drain timeout; drain timeout hard-cancels.
- Fold: carried + new segments; `max_segments` cap; #200 split-sentence case.
- Orchestrator: capture-before-abort-check for round 1 and round N; shielded round
  completes while an abort and a hard cancel fire (fake gateway that sleeps); abort
  persists `delivered: false`; `current_question` not written on abort; replay renders
  `undelivered_note`.
- Config: new fields validate in agent_core and dev-kit schemas; defaults; `extra=forbid`
  still rejects unknown keys.

**Integration**

- Request adapter: disconnect aborts at a safe point; a new request while INVOKED folds;
  successor prompt contains the carried exchanges; `DoneEvent.interrupted_at_stage` set.
- Segment adapter regression: existing `test_turn_assembler*.py`,
  `test_session_endpoints.py`, `test_stream_turn.py`, `test_turn_path_identity_parity.py`
  with fold expectations updated; voice `agent_core_llm` interrupted handling unchanged.
- Web and MCP direct-mode regression through `/stream_turn`.

**Replay fixtures (from 24 Sep)**

- Call 1 class-A sequence: substantive utterance, then check-in during the first LLM call.
  The successor's input contains both.
- Row 9: three tool rounds, disconnect at the third. All three exchanges are persisted
  and visible to the next turn, marked undelivered.

**Bridge**

- No cancel request on disconnect; request translation unchanged.

## 11. Open questions

1. Default `undelivered_note` wording in the DPG config, and whether it should be
   localisable per domain locale.
2. `fold.max_segments` default — 3 is a guess; the 24 Sep data had cascades up to 7.
3. Should the late result of a drain-timeout tool round also be pushed into the
   *running* successor, or only into the turn after it (current design)?
