# Agent Core: Async Streaming & Multi-Segment Input Design

**Status:** Open for design discussion  
**Date:** 2026-04-13  
**Scope:** Agent Core (`agent_core/`)  
**Depends on:** None — independent of VoIP reach layer work  
**Blocks:** Telephony Adapter latency improvement, multi-turn voice UX

---

## Problem Statement

Agent Core's `POST /process_turn` is fully synchronous and 1:1 turn-based:
- One request in → wait for complete LLM response → one response out.
- All input arriving during processing is dropped or handled by the caller with a timeout.
- For voice/telephony channels, this creates two compounding problems:

### Problem 1 — First-Audio Latency

The pipeline today:

```
VAD detects end-of-speech
  → STT transcribes (500ms–1.5s)
  → POST /process_turn (LLM full turn: 1–3s)
  → TTS receives full text, starts synthesizing (500ms–1s)
  → First audio to caller
```

Total gap from end-of-speech to first audio: **3–6 seconds**. ITU-T G.114 targets ≤150ms one-way; acceptable voice UX is <2s. We are 2–3× over budget.

If Agent Core streamed sentence-by-sentence, TTS could begin sentence 1 while the LLM is still generating sentence 2, cutting first-audio latency to ~1.5–2.5s.

### Problem 2 — Multi-Segment Input (the harder problem)

Real callers do not speak in single clean utterances:

> Caller: "मुझे…" (VAD fires, STT transcribes "मुझे")  
> Caller: "…जॉब चाहिए" (second VAD segment, STT transcribes "जॉब चाहिए")

Today, each VAD segment becomes an independent `process_turn` call. Agent Core sees two turns (`"मुझे"` and `"जॉब चाहिए"`) with no awareness they're fragments of one thought. The first turn may trigger an LLM response ("What do you need?") before the second segment even arrives.

Other real-world patterns:
- Caller interrupts the agent mid-sentence (barge-in)
- Caller adds a correction before the agent responds ("Wait, actually…")
- Network jitter causes one utterance to be split into two VAD segments
- Caller speaks multiple short sentences quickly: "Yes. My name is Rahul. I called yesterday."

None of these are handled correctly by the current 1:1 synchronous model.

---

## Proposed Changes

### Change 1 — SSE Streaming Endpoint

**New endpoint:** `POST /process_turn/stream`  
Same request body as `/process_turn`. Returns `text/event-stream`.

```
data: {"type": "sentence", "text": "हाँ, मैं आपकी मदद कर सकता हूँ।"}
data: {"type": "sentence", "text": "आपका रजिस्ट्रेशन नंबर क्या है?"}
data: {"type": "done", "was_escalated": false, "was_tool_used": false, "model_used": "claude-sonnet-4-6", "latency_ms": 1240}
```

**Why sentence-level:** TTS requires at least a complete sentence for natural prosody. The Trust Layer output check is also sentence-grained — a partial sentence cannot be independently safety-checked. Sentence boundaries include `.`, `?`, `!`, `।` (Devanagari danda), `?` (full-width).

**Files changed:**
- `agent_core/src/llm_wrapper/claude_wrapper.py` — add `stream_call()` using `anthropic.messages.stream()`
- `agent_core/src/orchestrator.py` — add `stream_turn(turn_input) → AsyncGenerator[StreamEvent, None]`
- `agent_core/src/servers/orchestration_server.py` — new endpoint using `fastapi.responses.StreamingResponse`

**Trust Layer under streaming:** Each sentence is independently passed through `/check/output` before emission. If blocked, a fallback phrase is emitted and `was_escalated=true` is set on the `done` event.

**Existing `/process_turn` is unchanged** — it stays synchronous for web/CLI channels.

---

### Change 2 — Multi-Segment Input (Design Discussion Required)

This is the more fundamental problem. Options:

#### Option A — Input Accumulation Window (simplest)

Agent Core holds an input buffer per `session_id`. When a turn request arrives, instead of immediately calling the LLM:
1. Start a short accumulation window (e.g. 300ms).
2. Any additional `process_turn` calls for the same `session_id` within the window append to the buffer.
3. After the window closes, the accumulated text is sent to the LLM as one combined input.

*Pro:* Handles split VAD segments transparently. No interface change for callers.  
*Con:* Adds 300ms latency to every turn. Does not handle barge-in or long pauses mid-thought.

#### Option B — Streaming Input Endpoint

New endpoint `POST /process_turn/input_stream` accepts a sequence of input segments for one logical turn:

```json
{"session_id": "...", "segment": "मुझे", "is_final": false}
{"session_id": "...", "segment": "जॉब चाहिए", "is_final": true}
```

Agent Core accumulates segments until `is_final: true`, then calls the LLM. Callers (telephony adapter) decide when a turn is "final" based on silence duration or other signals.

*Pro:* Clean separation of "still speaking" vs "done speaking". No arbitrary timer.  
*Con:* Requires telephony adapter to distinguish short pauses (mid-thought) from end-of-turn pauses. This is a hard VAD problem.

#### Option C — Cancellable Turns

When a new `process_turn` request arrives for a `session_id` that already has an in-flight turn, cancel the in-flight turn, merge inputs, restart:

```
Turn 1 starts: "मुझे" → LLM call begins
Turn 2 arrives: "जॉब चाहिए" → Turn 1 cancelled, combined input "मुझे जॉब चाहिए" → new LLM call
```

*Pro:* Handles barge-in naturally (new input always wins). No window timer needed.  
*Con:* Wasted LLM tokens on cancelled turns. Race conditions if Turn 1 has already emitted sentences via SSE.

#### Option D — Stateful Session WebSocket on Agent Core

Replace the per-turn HTTP model with a persistent WebSocket per session. The reach layer opens a WebSocket to Agent Core at session start. Text segments are sent as messages. Agent Core decides internally when to respond.

*Pro:* Most natural model for voice. Full control over accumulation and barge-in.  
*Con:* Major architectural change. All existing HTTP callers need updating. Session management becomes more complex.

---

## Open Questions for Design Discussion

1. **What is the right accumulation primitive?** Timer window (Option A), explicit `is_final` flag (Option B), cancellation (Option C), or WebSocket (Option D)?

2. **Who decides end-of-turn?** Should the telephony adapter (which has VAD context) signal end-of-turn explicitly, or should Agent Core decide based on silence duration / semantic completeness?

3. **Barge-in handling:** If the agent is speaking (TTS is active) and the caller speaks, the telephony adapter must interrupt TTS. Should Agent Core also know about barge-in (to cancel any queued sentences not yet sent to TTS), or is barge-in purely a telephony adapter concern?

4. **Trust Layer and cancelled turns:** If a turn is cancelled mid-output-check, what happens to the Trust Layer state? Does it need a cancel/rollback API?

5. **Backward compatibility:** Web and CLI channels are 1:1 synchronous and should stay that way. The new behaviour should only activate for channels that opt in (e.g. `channel: "telephony"`).

6. **Memory Layer consistency:** If Turn 1 is cancelled after Memory Layer read but before write, the session state is unchanged — is that correct? Or does partial processing need to be rolled back?

---

## Recommended Sequencing

1. **First: implement Change 1 (SSE streaming endpoint)** — standalone, no interface risk, immediate latency win.
2. **Then: design Change 2 (multi-segment input)** — requires agreement on the accumulation model before implementation.
3. `AgentCoreLLMProcessor` in the telephony adapter uses synchronous `process_turn` until Change 1 is shipped, then migrates to `/process_turn/stream`.

---

## Files Affected

| File | Change |
|---|---|
| `agent_core/src/llm_wrapper/claude_wrapper.py` | Add `stream_call()` |
| `agent_core/src/orchestrator.py` | Add `stream_turn()`, optionally `accumulate_input()` |
| `agent_core/src/servers/orchestration_server.py` | New `/process_turn/stream` endpoint; optionally new input segment endpoint |
| `agent_core/src/models.py` | New `StreamEvent`, `SentenceEvent`, `DoneEvent` dataclasses |
| `telephony_adapter/src/pipecat_services/agent_core_llm.py` | Migrate to streaming endpoint (after Change 1 ships) |

---

*Raised by Sanketika Labs — telephony adapter team.*
