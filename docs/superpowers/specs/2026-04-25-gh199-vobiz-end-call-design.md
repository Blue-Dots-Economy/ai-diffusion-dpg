# GH-199 — Vobiz end-of-call hangup signal design

**Issue:** [#199 — investigate: confirm Vobiz WebSocket close terminates the telephony leg](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/199)
**Wave:** Voice UX Wave 2 (parent #189), depends on Wave 1 P1-A (#191, merged in #209/#221)
**Block:** Reach Layer / Voice / Telephony Adapter
**Date:** 2026-04-25

## 1. Problem

In the 2026-04-24 KKB voice triage session the bot recognised
`termination_intent`, the LLM invoked `end_session`, but the call only ended
when the caller hung up at 16:36:38. With Wave 1's P1-A merged the streaming
path now sets `DoneEvent.session_ended=True` correctly, so
`AgentCoreLLMProcessor._handle_done_event` runs and reaches
`VobizAdapter.close_call(reason="session_end")`. We still need to confirm
that the resulting telephony teardown actually drops the caller's leg.

## 2. Investigation results

The `VobizFrameSerializer` shipped by `pipecat` (already wired in
`reach_layer/voice/src/operators/vobiz_operator.py:104-113` with
`auto_hang_up=True`) **does** have a vendor-specific hangup signal — but it
is **HTTP, not a WebSocket payload**:

- On receiving an `EndFrame` or `CancelFrame` in `serialize()`, the
  serializer issues a **`DELETE`** to
  `https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_id}/` with
  `X-Auth-ID` / `X-Auth-Token` headers.
- Vobiz responds `204` for a successful hangup and `404` if the call is
  already terminated.
- The serializer guards against double-issue with a `_hangup_attempted`
  flag.

Source: `pipecat/serializers/vobiz.py:101-115, 149-197`.

The current `VobizAdapter.close_call()`
(`reach_layer/voice/src/vobiz_adapter.py:312-352`) only calls
`await ws.close()`. It never causes an `EndFrame` to flow through the
serializer, so the REST DELETE is **never sent**. That fully explains the
2026-04-24 evidence: the bot closed the WebSocket, Vobiz held the leg up
because nothing requested the call be hung up at the API level.

The escalation path in the same processor already does the right thing —
`agent_core_llm.py:451` pushes `EndFrame()` downstream after a fallback
phrase.

## 3. Design

### 3.1 Load-bearing change (Agent-Core-LLM processor)

In `reach_layer/voice/src/pipecat_services/agent_core_llm.py`,
`_handle_done_event`: after pushing the terminal-word `TextFrame`, push
`EndFrame()` downstream **before** calling
`self._telephony.close_call(...)`. This matches the escalation pattern at
line 451 and triggers `VobizFrameSerializer._hang_up_call()` →
HTTP DELETE → Vobiz drops the leg.

Push order:

```
TextFrame(terminal_word)        # so TTS speaks the goodbye first
EndFrame()                      # serializer issues vobiz REST DELETE,
                                # transports flush + close
```

`EndFrame` is `UninterruptibleFrame` (per `pipecat.frames.frames:1416`) and
flows in-order behind the terminal `TextFrame`, so audio is not cut off.

### 3.2 Adapter-side change (defensive fallback)

`VobizAdapter.close_call()` becomes a defensive fallback rather than the
primary teardown path:

- Log invocation with `reason` and a new `vendor_signal` field so the
  observability story is consistent regardless of which path fires.
- If `_active_websocket` is open, attempt `ws.close()` as a safety net for
  abnormal paths where no `EndFrame` flowed (e.g. caller dropped, processor
  errored before pushing EndFrame).
- The serializer's own `_hangup_attempted` flag means a second
  `EndFrame`/`close_call` after the happy path is a no-op at the API
  layer — idempotent by construction. We do not need to add our own guard.

### 3.3 Structured log fields

Add `vendor_signal: "vobiz_rest_delete"` (or `"none"`) and `outcome` (one
of `success` | `already_terminated` | `failure` | `skipped`) to the
`vobiz_adapter.close_call` log entry. The serializer logs the REST outcome
at `debug` today; we surface the same outcome at `info` from a thin
adapter-side wrapper so it appears in the structured stream we already
ship to observability.

Implementation: add a thin `LoggingVobizFrameSerializer` subclass in
`reach_layer/voice/src/operators/vobiz_operator.py` that overrides
`_hang_up_call` to call `super()._hang_up_call()` while also emitting a
structured log entry (`operation=vobiz_serializer.hangup`,
`outcome={success|already_terminated|failure}`, `latency_ms`,
`call_id`). No behavioural divergence from upstream. Wired in
`VobizOperator.create_transport`.

### 3.4 Configuration

No config changes. `auto_hang_up=True` is already set; no new YAML keys.

## 4. Tests

Per CLAUDE.md `testing-requirements.md`: normal / edge / failure for each.

### 4.1 Serializer-level (unit, mocked HTTP)

File: `reach_layer/voice/tests/test_vobiz_serializer_hangup.py` (new).

Mock `aiohttp.ClientSession.delete` (no live server). Push an `EndFrame`
through a `VobizFrameSerializer` instance; assert:

- **Normal:** `DELETE` issued exactly once to
  `https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_id}/` with the
  expected `X-Auth-ID` / `X-Auth-Token` headers; mocked `204` →
  `outcome=success`.
- **Edge — already terminated:** mocked `404` →
  `outcome=already_terminated`, no exception.
- **Edge — missing call_id:** serializer constructed without `call_id` →
  no DELETE issued, structured warning logged.
- **Edge — `CancelFrame` triggers same path** as `EndFrame`.
- **Edge — second `EndFrame`** does not issue a second DELETE
  (`_hangup_attempted` guard).
- **Failure — 5xx:** mocked `500` → `outcome=failure`, error log emitted,
  no exception raised to caller.

### 4.2 Pipecat-level (integration with mock pipeline)

File: `reach_layer/voice/tests/test_agent_core_llm_done.py` (extend
existing tests if present, otherwise add).

Build a minimal pipecat pipeline: `AgentCoreLLMProcessor` →
no-op output processor that captures all frames pushed downstream.
Drive a synthetic `DoneEvent(session_ended=True)` through the processor
(use the same hook `_handle_done_event` is called from in production —
`subscribe_events` / event subscriber, depending on what the test
existing fixture exposes). Assert frame ordering downstream:

1. `TextFrame(terminal_word)` first.
2. `EndFrame` second.
3. `telephony.close_call(reason="session_end")` invoked after the
   `EndFrame` push.

Edge case: empty `terminal_word` config → no `TextFrame`, but `EndFrame`
still flows.

Failure case: `telephony.close_call` raises → error log emitted, but
`EndFrame` was still pushed (we do not regress the load-bearing path on
adapter errors).

### 4.3 Adapter-level

File: `reach_layer/voice/tests/test_vobiz_adapter.py` (extend).

- **Normal:** `close_call(reason="session_end")` with active WS → logs
  invocation, calls `ws.close()`, structured log includes `reason`,
  `vendor_signal`, `outcome`.
- **Edge — no active WS:** logs `skipped`, returns cleanly.
- **Failure — `ws.close()` raises:** caught, logged as `failure`, does
  not propagate.

### 4.4 Coverage

≥ 70 % line coverage on touched modules in `reach_layer/voice/src/`. No
regression on agent_core / knowledge_engine (untouched).

## 5. Empirical verification (post-merge, on VM)

Not part of this PR's automated test surface. After merge, run a fresh
KKB voice call, say "bye" once, capture `agent_core_logs_*.json` and
`reach_layer_voice_logs_*.json`. Confirm:

- `agent_core_llm.session_ended` log present once.
- New structured log entry recording the Vobiz REST DELETE outcome
  (`outcome=success` or `already_terminated`).
- `vobiz_adapter.call_disconnected` fires within **≤ 2000 ms** of the
  last bot `SentenceEvent` push.
- The disconnect source attributed to the server-side hangup, not to a
  caller-side hangup.

## 6. Module interaction rules

No new cross-module call patterns. The change stays inside the Reach
Layer voice channel:

- `AgentCoreLLMProcessor` → pushes `EndFrame` downstream within its own
  pipeline (existing pattern).
- `VobizFrameSerializer` → calls Vobiz REST API (existing third-party
  call; no change to the call site).
- `AgentCoreLLMProcessor` → `VobizAdapter.close_call()` is unchanged; the
  adapter just becomes a fallback rather than the primary teardown.

CLAUDE.md `Module interaction rules` table is unchanged.

## 7. Out of scope

- Changing `keepCallAlive="true"` in the `<Stream>` XML
  (`vobiz_operator.py:147`).
- Changing the `auto_hang_up` default.
- Refactoring `_handle_done_event` beyond the additive `EndFrame` push.
- Vobiz vendor doc archival (the pipecat serializer already encodes the
  contract).
- Any change to the WebSocket payload protocol — none is needed.

## 8. Acceptance criteria mapping

| AC from #199 | Met by |
|---|---|
| Bot-initiated end-session closes caller's line within ≤ 2000 ms without caller hangup | §3.1 + §5 verification |
| Fake-server test pins the payload sequence | §4.1 (HTTP DELETE contract) + §4.2 (frame ordering) |
| Logs on `close_call` record `reason` and any vendor-specific signal emitted | §3.3 |

## 9. Files touched

| File | Change |
|---|---|
| `reach_layer/voice/src/pipecat_services/agent_core_llm.py` | Push `EndFrame()` in `_handle_done_event` after the terminal-word `TextFrame`. |
| `reach_layer/voice/src/vobiz_adapter.py` | Restructure `close_call` as defensive fallback; structured log adds `vendor_signal` / `outcome`. |
| `reach_layer/voice/src/operators/vobiz_operator.py` | Add `LoggingVobizFrameSerializer` subclass and use it in `create_transport`. ≤ 40 lines. |
| `reach_layer/voice/tests/test_vobiz_serializer_hangup.py` *(new)* | Mocked-HTTP unit tests for hangup path. |
| `reach_layer/voice/tests/test_agent_core_llm_done.py` | Frame-ordering integration tests. |
| `reach_layer/voice/tests/test_vobiz_adapter.py` | Adapter-level tests for close_call. |
