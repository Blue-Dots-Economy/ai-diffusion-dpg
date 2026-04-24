# Voice UX Triage — KKB DPG (2026-04-24)

Master triage doc for a batch of voice-UX issues filed as individual GH
issues, each tracked as an independent PR in its own Claude Code session.
Every issue in the batch references the relevant section here so the PR
author does not repeat log analysis.

## 1. Session used for evidence

One KKB voice call, Vobiz + Pipecat, session `00906274-386f-…`.
Time range: **16:31:27 → 16:36:38 IST, 2026-04-24** (~5 min, 11 turns).

Logs shared by the user:
- `agent_core_logs_1..4.json` (4 shards, reverse-chronological per file,
  merged and sorted ascending).
- `reach_layer_voice_logs_1..2.json` (2 shards, same).

### Reconstructed timeline

| # | Start | Dur | Intent | Tools | Sentences | Notes |
|---|---|---|---|---|---|---|
| — | 16:31:27 | — | — | opening_phrase emitted on SSE connect | — | |
| T1 | 16:31:38 | ~2 s | — | consent prompt (no LLM) | — | |
| T2 | 16:31:58 | 8.2 s | any_input | none | 3 | consent reply; pending-message replay |
| T3 | 16:32:38 | 15.3 s | evaluate_option | onest_market_lookup | 11 | LLM#2 = 5.3 s |
| T4 | 16:33:32 | 4.9 s | any_input | none | 3 | interruption @ 16:33:30 |
| T5 | 16:33:38 | 5.6 s | any_input | none | 3 | starts 1 s after T4 ends |
| T6 | 16:33:44 | 4.0 s | any_input | none | 4 | starts 1 s after T5 ends |
| T7 | 16:33:48 | 10.0 s | any_input | onest_market_lookup | 10 | starts 0 s after T6 ends |
| T8 | 16:34:55 | 9.2 s | apply_now | onest_market_lookup | 7 | interruption @ 16:34:53 |
| T9 | 16:35:27 | 17.8 s | apply_now | get_profile + onest_market_lookup + apply_job | 11 | 3 tool rounds; LLM#2 = 9.1 s |
| T10 | 16:36:08 | 9.7 s | termination_intent | end_session | 8 | DoneEvent.session_ended=false |
| T11 | 16:36:20 | 9.5 s | termination_intent | end_session | 9 | starts 2 s after T10; user said "bye" twice |
| — | 16:36:38 | — | — | call_disconnected (client-side) | — | |

Three turn-pileup clusters: T4→T5→T6→T7 (one user utterance chunked across
four VAD pauses), T10→T11 (duplicate termination), and T9 (three serial
tool rounds compounding). `onest_market_lookup` ran **4 times** in 5
minutes, no reuse.

## 2. Repo context

- Branch at time of triage: `main` (commits `65aaf65`, `fa8e3ae`, `c28bfbe`).
- PR #187 is load-bearing for issue P3-A below — it removed the
  `tool_result_mappings` → Memgraph journey-event write loop that would
  previously have kept tool results across turns.
- Runtime blocks involved: Agent Core, Reach Layer (voice/Vobiz), Trust
  Layer (per-sentence output check), Action Gateway (REST adapter + response
  projection).
- CLAUDE.md rules apply to all fixes: base-class pattern, error handling
  with timeouts + structured errors, configuration discipline (no
  hardcoded domain values), structured logging, Google-style docstrings,
  ≥ 70 % line coverage on agent_core and knowledge_engine.

## 3. Issues, grouped by concern and priority

Issues are listed in the order they should be shipped. Each is self-
contained enough to be its own PR.

### Priority P1 — Call ending is broken

#### P1-A. `end_session` tool doesn't set `DoneEvent.session_ended` in streaming path

**Problem.** User ends the call; bot recognises `termination_intent`; LLM
invokes `end_session` tool; the call stays open. Caller has to hang up.

**Evidence.** In `reach_layer_voice_logs_*.json`, both termination turns
(T10 @ 16:36:18, T11 @ 16:36:30) log `agent_core_llm.done` with
`session_ended=false`. Call finally ends via `vobiz_adapter.call_disconnected`
at 16:36:38 — client-side hangup, not bot-signalled.

**Root cause.** The streaming tool loop in
`agent_core/src/orchestrator.py:2721-2743` executes every tool through
`self._async_gateway.execute(...)` and never flips
`self._manager_agent._session_ended_flag`. That flag is only set inside
`manager_agent.run_turn()` (sync path, `manager_agent.py:151-153`) when it
sees `tool_call.tool_name == "end_session"`. At `orchestrator.py:2932`,
`session_ended = bool(getattr(self._manager_agent, "session_ended",
False))` therefore always returns False in the streaming (voice) path.
`DoneEvent(session_ended=False)` → `AgentCoreLLMProcessor._handle_done_event`
early-returns → `close_call()` never fires.

**Fix.**
1. In the streaming tool loop, after executing each tool, check
   `if tc.tool_name == "end_session": self._manager_agent._session_ended_flag = True`.
   Equivalent: compute `session_ended` locally from `_stream_tool_results`
   and pass it to the DoneEvent builder directly.
2. Add a unit test in `agent_core/tests/test_orchestrator_stream.py`
   asserting that a turn whose tool loop contains `end_session` emits a
   `DoneEvent(session_ended=True)`.
3. Ensure `manager_agent.reset()` (or equivalent) clears the flag at the
   start of every turn so it cannot leak across turns.

#### P1-B. `terminal_word` is English on Hindi KKB voice calls

**Problem.** Even after P1-A lands, the final utterance before hangup is
`"Goodbye"` — jarring on a Hindi call.

**Evidence.** `dev-kit/configs/kkb/agent_core.yaml:50` — `terminal_word: "Goodbye"`.
`reach_layer/voice/src/pipecat_services/agent_core_llm.py:467` pushes
`TextFrame(terminal_word)` before `close_call`.

**Fix.** Change `terminal_word` to a Hindi phrase (e.g. `"धन्यवाद"`) or an
empty string (letting the LLM's own closing sentence stand). Add a warning
test for "terminal_word language should match domain primary language"
if cheap.

#### P1-C. Confirm `ws.close()` actually terminates the Vobiz telephony leg

**Problem.** Even if the bot asks the WebSocket to close, the telephony
vendor may keep the call up until its own timeout.

**Evidence.** `vobiz_adapter.py:312-352` calls `await ws.close()` but does
not send any Vobiz-specific hangup signal. In the captured session the call
stayed up until client-side hangup — we cannot distinguish "bot didn't try"
(P1-A) from "bot tried but Vobiz ignored close".

**Fix.** Investigate Vobiz vendor docs for explicit hangup / stop-stream /
end-session signals. If one exists, emit it before `ws.close()`. Add an
integration-ish test with a fake Vobiz WebSocket server that asserts the
expected hangup payload is sent. This issue should land after P1-A so the
end-to-end close path can be exercised.

---

### Priority P2 — "Bot keeps talking" / turn pileup

#### P2-A. TurnAssembler: cancel-and-fold on silence trigger during active turn

**Problem.** One user utterance becomes 2–4 bot responses queued
back-to-back. Caller perceives the bot monologuing.

**Evidence.** T4→T5→T6→T7 in §1: four full pipelines kicked off in 16 s
from a single user intent. Each produced 3, 3, 4, 10 sentences → 20 TTS
sentences queued. T10→T11: user said "bye", bot spent 9 s responding,
user said "bye" again while audio still queued, got a second full
termination turn.

**Root cause.** `turn_assembler.silence_trigger` (voice `silence_ms: 400`,
see `dev-kit/configs/kkb/agent_core.yaml:88`) fires on any 400 ms VAD
pause without inspecting whether a turn is already `INVOKED`. No cancel,
no coalesce. `add_segment` only cancels on barge-in while bot is speaking.

**Fix — cancel-and-fold.**
1. On `silence_trigger`, if the session's current turn status is
   `INVOKED`, cancel the in-flight turn (emit `DoneEvent(turn_status="interrupted")`,
   as already done for barge-in in `turn_assembler.py:600-615`) and
   **preserve any new segments that arrived during the in-flight turn**
   as the seed segments of a single successor turn.
2. The new turn starts from those seed segments plus whatever arrives
   until the next normal silence trigger fires for it.
3. Cap `agent.max_tool_rounds = 2` for voice (T9 ran 3 rounds and blew
   budget) — config change in the same PR since it prevents a compounding
   failure mode.
4. Tests: simulate a silence trigger while an INVOKED turn is running;
   assert old turn is cancelled, successor turn contains the folded
   segments, no sentences from cancelled turn reach TTS.
5. `sentences_pushed` should go to 0 (or unchanged-before-cancel) for
   the cancelled turn; the TurnAssembler's existing interrupted-segments
   discard path already covers this.

#### P2-B. Suppress `opening_phrase` when `ask_for_consent=true` until consent is granted

**Problem.** At session start the caller hears the entry subagent's
`opening_phrase` (~13 words) immediately followed by the consent prompt
(~20 words) — two full utterances, no user turn between them.

**Evidence.** `opening_phrase emitted` log at 16:31:27; T1 consent prompt
at 16:31:38 — 11 s later, but the phrases play back-to-back before any
user input because `_play_opening_phrase` is a detached asyncio task
running in parallel with the consent gate.

**Root cause.** `turn_assembler._emit_opening_phrase_if_first`
(`turn_assembler.py:472-578`) is called from `subscribe()` on SSE connect
regardless of consent state. The consent gate in
`orchestrator.py:388-471` is only checked once the first user turn
arrives.

**Fix.** In `_emit_opening_phrase_if_first`, short-circuit when
`config.agent.ask_for_consent == True` and `session.user_storage_mode`
is unset. Emit the opening_phrase on the first post-consent turn instead
(either inline with the consent-granted branch in the orchestrator or by
keeping the `opening_phrase_emitted` flag unset until consent resolves).
Tests must cover both `ask_for_consent=true` (phrase suppressed on
connect) and `ask_for_consent=false` (phrase emitted on connect — existing
GH-149 behaviour preserved).

#### P2-C. Opening-phrase emission race with first user turn

**Problem.** `_play_opening_phrase` runs as a detached `asyncio.create_task`
from `on_client_connected` (`vobiz_adapter.py:209-211`). If the user speaks
immediately after connect, the opening-phrase SSE stream and the first
turn's SSE stream are open concurrently. Frames can interleave
unpredictably.

**Evidence.** Not directly triggered in the captured session, but the
code path is clearly racy. The fix for P2-B will partially mitigate by
delaying the opening phrase until after consent; this issue covers the
general case.

**Fix.** Serialise opening-phrase emission and first-turn subscribe.
Options: (a) await opening-phrase task completion before accepting the
first `submit_input`; (b) use a per-session asyncio lock; (c) emit the
opening phrase inline from the orchestrator's first turn instead of as a
side task. Pick the simplest that keeps voice-startup latency under the
current budget.

#### P2-D. Tighten voice VAD windows for Hindi

**Problem.** `vad.stop_secs: 0.6` and turn-assembler `silence_ms: 400`
are aggressive. Hindi (especially older/rural speakers) has long
inter-word pauses; these trip as "user finished" mid-utterance, driving
P2-A's pileup.

**Evidence.** T4–T7 in §1: the same user intent was split across four
`submit_input` calls within 16 s. The recording would show a single
spoken sentence.

**Fix.** Bump defaults in `dev-kit/configs/kkb/agent_core.yaml` voice
block: `silence_ms: 600–800`, `vad.stop_secs: 1.0`. Keep web/cli values
unchanged. This is a config PR and should land *after* P2-A so we can
tell which UX improvement came from which change.

---

### Priority P3 — Response correctness / state hygiene

#### P3-A. Persist and replay prior `tool_use`/`tool_result` pairs across turns

**Problem.** The LLM re-invokes the same tools every turn because it has
no memory of prior tool results.

**Evidence.** `onest_market_lookup` invoked in T3, T7, T8, T9 — four
separate calls in 5 minutes, all against the same profile. PR #187
removed the `tool_result_mappings` → Memgraph journey-event write loop;
nothing replaced it. Streaming path (`orchestrator.py:2739-2767`) appends
tool_use/tool_result to `messages` within a single turn only. Next turn
rebuilds `messages` from `current_question` alone.

**Fix — Anthropic tool-protocol-faithful.**
1. On turn completion in the streaming path, after the tool loop finishes,
   capture the *actual* `tool_use` and `tool_result` content blocks
   (Anthropic schema) for the last N tool exchanges in that turn.
2. Persist them into Memory Layer under a new session-scoped key, e.g.
   `recent_tool_exchanges`, as an ordered list. Cap at **last 3 exchanges**,
   each capped at `max_size_chars` (already enforced by action_gateway's
   `result_text`).
3. On the next turn's streaming path, prepend these as real
   `{"role": "assistant", "content": [{"type": "tool_use", ...}]}` +
   `{"role": "user", "content": [{"type": "tool_result", ...}]}` message
   pairs in the `messages` list, *before* the current user turn.
4. Update voice/suffix system prompt to tell the LLM: "Prior tool
   results in this conversation are visible as real tool_result messages.
   Do not re-invoke a tool unless the relevant parameters have changed."
5. Make the cap configurable via `agent.recent_tool_exchanges.max_items`
   (default 3) and `agent.recent_tool_exchanges.max_chars` (default
   inherits from action_gateway).
6. Tests: two-turn scenario where T1 calls `onest_market_lookup`, T2
   asks a clarifying question on the same results — assert T2's
   `messages` contains the T1 `tool_use`/`tool_result` pair and that the
   LLM is not invoked with the tool again.

#### P3-B. Barge-in ack plays on every user turn (stale `_bot_speaking`)

**Problem.** The configured barge-in acknowledgement phrase plays every
time the user starts speaking, including when the bot is silent.

**Evidence.** In voice logs, 5 `agent_core_llm._start_interruption` events
fired (16:33:30, 16:34:53, 16:35:26, 16:36:06, 16:36:19) — all with
`bot_was_speaking=true`, `has_acknowledgement=true`, ack played every
time.

**Root cause.** `agent_core_llm.py:208` gates the ack on
`_bot_speaking`, which is set by `BotStartedSpeakingFrame` and cleared by
`BotStoppedSpeakingFrame`. On the Vobiz pipeline the stop-frame is
unreliable — the flag stays True across the whole call, so the gate is
effectively unconditional.

**Fix.** Replace the single flag with a compound signal:
- `sentences_pushed_within_last_N_ms` — track a timestamp each time a
  SentenceEvent is pushed as TTSSpeakFrame; the bot is "actively
  speaking" if now − last < N ms (default 1500 ms).
- AND/OR: "turn_assembler has an INVOKED turn" (exposed via the existing
  SSE SignalEvent stream).
- Fire ack only when at least one leg of the compound signal says the
  bot is currently speaking.
- Log which leg(s) passed/failed on each interruption for debuggability.

Tests: simulate interruption while bot is (a) mid-sentence, (b) just
finished, (c) never spoke — assert ack fires only for (a).

#### P3-C. Hard response-length cap on voice channel

**Problem.** Single turns produce 10–11 sentences of TTS. Even without
pileup, the bot is too verbose for voice.

**Evidence.** `sentences_pushed=10, 11` on T3, T9 in voice logs. The
voice `system_prompt_suffix` (`dev-kit/configs/kkb/agent_core.yaml:12-34`)
says "short to medium sentences" — not a hard cap — and Haiku-4.5 does
not self-constrain.

**Fix — two-pronged.**
1. **Prompt cap.** Append a hard rule to voice `system_prompt_suffix`:
   `"Reply in at most 2 short sentences. The only exception is presenting a market listing, where you may use at most 3 items, one short line each."`
2. **Token cap.** Add `channels.voice.max_tokens` (int, default ~200)
   and plumb it into `llm_wrapper.stream_call`, which today sends
   `max_tokens=4096`. Set KKB voice to 200 in
   `dev-kit/configs/kkb/agent_core.yaml`.
3. Schema: add `max_tokens: Optional[int] = None` to `ChannelConfig` in
   `agent_core/src/schema/config.py`.

Tests: assert sentence-cap prompt appears in prompt assembly when
channel is voice; assert `max_tokens` flows from config into the
`stream_call` invocation.

---

### Priority P4 — Latency reductions

#### P4-A. Short-circuit termination — skip LLM when NLU is confidently `termination_intent`

**Problem.** Every termination turn pays ~9 s latency (NLU 2.2 s + LLM#1
2.5–3.4 s + tool + LLM#2 2.3–2.7 s). Bot should sign off within 500 ms.

**Evidence.** T10 = 9.7 s, T11 = 9.5 s; NLU confidence was 0.92 and 0.82.
Final response is already a configured phrase
(`conversation.termination_message`).

**Fix.** In the streaming orchestrator, after NLU returns, if
`intent == "termination_intent"` and `confidence >= threshold` (new
config, default 0.7), skip LLM entirely: emit the configured
`termination_message` translated to detected language, set
`session_ended=True` on the DoneEvent directly, emit one SentenceEvent
and one DoneEvent, write the flush-state memory updates, return. Total
should be ≤ 500 ms.

Tests: termination_intent high confidence → LLM not called, DoneEvent
has `session_ended=True`.

#### P4-B. Filler utterance on `SignalEvent(llm_start)` during long turns

**Problem.** On turns that call tools (T3, T7, T9), the caller hears
silence for 5–17 s before the first bot sentence. Feels like a dead line.

**Evidence.** T9 start → first TTS ~8 s; T3 start → first TTS ~5 s.

**Fix.** In `agent_core_llm.py._handle_transcription_session`, when a
`SignalEvent(stage="llm_start", status="start")` is received and no
SentenceEvent has yet been pushed, push a short filler TTSSpeakFrame
from config (`channels.voice.filler_phrase`, default `"एक सेकंड"`).
Gate to fire only when latency to first SentenceEvent exceeds
`channels.voice.filler_threshold_ms` (default 1500 ms) — use a timed
wait, not an eager push, so fast turns stay quiet.

Tests: fast turn (first sentence < threshold) → no filler; slow turn
(threshold exceeded) → single filler before the first real sentence.

#### P4-C. NLU on Sonnet is not hitting the prompt cache

**Problem.** NLU latency stays at ~2.2 s every turn, not the 500–800 ms
promised by the config comment (`dev-kit/configs/kkb/agent_core.yaml:369-375`).

**Evidence.** `nlu_processor.process latency_ms`: 2228, 2219, 2172,
2200 — no warmup benefit across an entire session.

**Root cause hypotheses (to investigate).**
- Cache breakpoint not being emitted on the NLU system prompt.
- The NLU system prompt includes per-turn dynamic context (session
  snapshot, language, entities already known) that invalidates cache
  every call.
- Model ID vs cache-eligible model ID mismatch.

**Fix.** Investigate in `agent_core/src/nlu_processor.py` (exact path to
confirm during PR):
- Confirm `cache_control` block is on the static portion of the system
  prompt.
- Split prompt into static (cached) + dynamic (per-turn) halves.
- Log raw `cache_creation_input_tokens` and `cache_read_input_tokens`
  from the Anthropic response on every call; emit as structured log.
- Fix whatever the logs reveal.

#### P4-D. Batch per-sentence Trust Layer `check_output` calls

**Problem.** On a 10-sentence turn (T3, T9) the Trust Layer is hit 10
times, each 50–200 ms, adding 0.5–2 s of pure overhead.

**Evidence.** Agent core logs show consecutive `async_trust_http_client.check_output`
lines per sentence with latencies 3–200 ms — one per SentenceEvent.

**Fix.** Batch: check every N sentences (default N=3) or every M ms
(default 500), whichever comes first. On `block`/`escalate`, flush the
pending buffer of unspoken sentences immediately. Update Trust Layer
`/check/output` if necessary (it already accepts variable-length text;
no schema change expected).

Tests: 9-sentence turn → 3 trust checks (batched by N=3); block verdict
on batch 2 → sentence #3–#9 never reach TTS.

---

### Priority P5 — Investigations / hygiene

#### P5-A. `current_question` accumulates concatenated prior responses during pileup

**Problem.** Under pileup, `current_question` in Memory Layer ends up
holding two or more concatenated bot responses, which then get injected
into the next turn's prompt as `[Last question asked: ...]`, confusing
history.

**Evidence.** At T11 the log shows
`[Last question asked: बिल्कुल। आपका अप्लीकेशन सबमिट हो गया है...ठीक है। आपका एप्लीकेशन सबमिट हो चुका है...]`
— clearly two distinct responses glued into one field.

**Fix.** Indirectly resolved by P2-A (no more pileup → no more concat).
File separately as a defense-in-depth test: assert that `current_question`
is overwritten, not appended, and that its length stays below a sane cap
(e.g. 500 chars).

#### P5-B. NLU returns `any_input` on 5/11 turns — ASR / normalisation quality

**Problem.** Nearly half of turns get the catch-all `any_input` intent,
meaning NLU isn't pulling useful routing signal out of ASR output.

**Evidence.** T2, T4, T5, T6, T7 all classified as `any_input`.

**Fix.** Not a code fix — an investigation issue. Sample the ASR
transcripts for these turns against the raw audio; identify whether it's
ASR quality (garbled Hindi), post-transcription normalisation removing
signal, or NLU prompt under-specification. Output a follow-up issue (or
no-op) with findings.

#### P5-C. `action_gateway`: projection-on-raw-dict test + optional `max_size_chars` bump

**Problem.** Behaviour is correct today, but there's no regression guard.

**Evidence.** `action_gateway/src/adapters/rest_api.py:381-394` —
`_apply_projection` runs on the full raw `result_dict`; `max_size_chars`
(default 4000) truncates only `result_text` (LLM-visible text), not the
projection input or `ToolResult.result`.

**Fix.** Add a test pinning the invariant: projection receives the
untruncated raw dict even when the response body exceeds `max_size_chars`.
Optionally bump `max_size_chars` for KKB's `onest_market_lookup` in
`dev-kit/configs/kkb/action_gateway.yaml` if product wants the LLM to
see more list items.

## 4. Shared references for all PR sessions

Files each session is likely to touch (read-before-edit list):

| File | What it is |
|---|---|
| `agent_core/src/orchestrator.py` | Sole orchestrator, 3000+ lines. Streaming path around 2100–3000. |
| `agent_core/src/turn_assembler.py` | VAD-driven turn boundary / silence / barge-in. |
| `agent_core/src/manager_agent.py` | Subagent selection + LLM tool loop (sync path). `session_ended` flag here. |
| `agent_core/src/nlu_processor.py` | NLU implementation. P4-C lives here. |
| `agent_core/src/schema/config.py` | Pydantic config. `ChannelConfig`, `UserStateModel`, etc. |
| `agent_core/src/models.py` | `DoneEvent`, `TurnResult`, etc. |
| `reach_layer/voice/src/pipecat_services/agent_core_llm.py` | Pipecat LLMProcessor; barge-in, done handling, filler. |
| `reach_layer/voice/src/vobiz_adapter.py` | Vobiz-specific adapter; opening phrase, `close_call`. |
| `action_gateway/src/adapters/rest_api.py` | REST adapter + projection + truncation. |
| `dev-kit/configs/kkb/agent_core.yaml` | KKB agent config; channels.voice suffix, VAD, consent. |
| `dev-kit/configs/kkb/action_gateway.yaml` | KKB action_gateway config; projection, `max_size_chars`. |

CLAUDE.md rules to verify on every PR:
- Base-class pattern preserved.
- External calls have timeout + retry + structured errors.
- Logging is structured (`operation`, `status`, `latency_ms`, no PII).
- Tests cover normal/edge/failure; `uv run pytest` passes.
- ≥ 70 % line coverage on agent_core and knowledge_engine if touched.

## 5. Out of scope for this batch

- ASR quality (covered by P5-B as investigation only).
- Model swaps (Haiku → Sonnet etc.) — behaviour tuning, not a bug.
- Multi-tenancy, rollback, dashboards (framework-level, tracked elsewhere).
- Rewriting the streaming orchestrator. Fixes should be localised.
