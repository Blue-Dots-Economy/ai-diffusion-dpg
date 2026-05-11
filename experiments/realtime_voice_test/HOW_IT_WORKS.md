# How the Realtime Voice Test Works

## Goal

Measure end-to-end voice latency of `gpt-realtime-mini` on real Hindi
phone calls — primarily **Time-To-First-Token (TTFT)** at p99, plus
per-turn token usage, transcripts, and cost.

## Architecture

```
Phone caller
   │ PSTN
   ▼
Vobiz (telephony provider)
   │ WS #1: Vobiz ⇄ our server   (g711_ulaw audio)
   ▼
our FastAPI server  (server.py + bridge.py)
   │ WS #2: our server ⇄ OpenAI Realtime   (g711_ulaw audio)
   ▼
gpt-realtime-mini
```

Two WebSockets per call:

- **WS #1** — Vobiz opens it to our `/ws/{call_sid}` endpoint. The URL
  is handed to Vobiz in the XML response from `/answer`. ngrok is just
  a public tunnel so Vobiz can reach our laptop.
- **WS #2** — our server opens it to OpenAI's Realtime API.

Both legs use the same `g711_ulaw` codec at 8 kHz, so audio passes
through with no resampling. The bridge runs two concurrent async tasks
— one forwarding caller audio into OpenAI, the other forwarding
OpenAI's response audio back to the caller and reading OpenAI's event
stream for latency markers.

## What we capture per turn

OpenAI emits specific events at known moments. We timestamp them with
a per-call monotonic clock and derive:

| Metric | Definition |
|---|---|
| `ttft_ms` | first `response.audio.delta` − `speech_stopped` — user-perceived "thinking" time |
| `total_response_ms` | `response.done` − `speech_stopped` |
| `response_decision_ms` | `response.created` − `speech_stopped` |
| `user_speech_duration_ms` | `speech_stopped` − `speech_started` |

Token usage (input_text / input_audio / input_cached / output_text /
output_audio) comes from `response.done`. Cost = sum of (tokens ×
per-1M rate from `pricing.py`).

## Output layout

One subdirectory per call, one JSONL row per user turn:

```
results/
├── 20260512T103045Z_abc-123/turns.jsonl
├── 20260512T110212Z_def-456/turns.jsonl
└── ...
```

```json
{"call_sid":"abc-123","turn":1,"ttft_ms":743,"total_response_ms":2107,
 "transcript_in":"नमस्ते, मुझे काम चाहिए",
 "transcript_out":"नमस्ते। आप किस तरह का काम ढूंढ रहे हैं?",
 "input_audio_tokens":125,"output_audio_tokens":95,"cost_usd":0.0034}
```

## Summarising

```bash
uv run python aggregate.py            # all calls so far
uv run python aggregate.py --latest   # most recent call only
```

Prints p50/p99 of `ttft_ms` and `total_response_ms`, plus average cost
per turn. p99 is the headline — voice UX is dominated by worst-case
latency.

## Module map

| File | Responsibility |
|---|---|
| `server.py` | FastAPI: `/answer` webhook + `/ws/{call_sid}` endpoint |
| `bridge.py` | Per-call coordinator: two WS tasks + `TurnAccumulator` state machine + JSONL writer |
| `vobiz_protocol.py` | Pure codec — Vobiz JSON frames ↔ Python dataclasses |
| `openai_realtime.py` | Async wrapper around OpenAI Realtime WebSocket |
| `prompts.py` | Three Hindi system-prompt variants |
| `pricing.py` | Per-1M token rates + per-turn cost calc |
| `aggregate.py` | Read all JSONL → p50/p99 summary |

## Scope

This experiment captures the **voice model's own latency** in
isolation — no DPG framework, no Memory/Trust/NLU, no Raya STT/TTS.
A matching production-side test (full production stack with
`gpt-4.1-mini` swapped in as the LLM, captured the same way) lives in
a separate spec; we compare the two p99 numbers to decide whether
replacing Raya STT + Raya TTS with a single voice-native model is
worth integrating.
