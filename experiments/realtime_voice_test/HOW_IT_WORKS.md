# How the Realtime Voice Test Works

## Goal

Measure end-to-end voice latency of `gpt-realtime-mini` on real Hindi
phone calls — primarily **Time-To-First-Token (TTFT)** at p99, plus
per-turn token usage, transcripts, and cost.

## Architecture

Built on **Pipecat**, a Python framework for voice pipelines. Vobiz
streams call audio to our server; our server runs a Pipecat pipeline
that detects voice activity, sends audio to OpenAI Realtime, and
streams the model's reply back to Vobiz. Pipecat handles the
transport plumbing on both sides; we plug in a custom processor to
capture per-turn metrics.

```
Phone caller
   │ PSTN
   ▼
Vobiz (telephony provider)
   │ Vobiz protocol, mu-law @ 8 kHz
   ▼
our FastAPI server
   │ ┌──────────────── Pipecat pipeline ────────────────┐
   │ │ transport.input()                                │
   │ │       ▼                                          │
   │ │ VADProcessor              (Silero VAD)           │
   │ │       ▼                                          │
   │ │ UserTurnProcessor         (turn boundaries)      │
   │ │       ▼                                          │
   │ │ OpenAIRealtimeLLMService ───────────┐            │
   │ │       ▼                             │            │
   │ │ LatencyObserverProcessor            └────────────┼─► OpenAI Realtime
   │ │       ▼                                          │   (Pipecat opens
   │ │ transport.output()                               │    this internally)
   │ └──────────────────────────────────────────────────┘
   ▼
back to Vobiz → caller hears the reply
```

- **Vobiz → our server** — On call answer, Vobiz opens a connection
  to our `/ws/{call_sid}` endpoint. Pipecat's
  `FastAPIWebsocketTransport` + `VobizFrameSerializer` handle the
  Vobiz JSON protocol and the mu-law audio codec for us.
- **Our server → OpenAI** — Pipecat's `OpenAIRealtimeLLMService`
  connects to OpenAI Realtime when the pipeline starts. It sends the
  session config (system prompt, voice, audio format, VAD,
  transcription language) and streams audio bidirectionally.
- **LatencyObserverProcessor** — our custom Pipecat processor sits in
  the pipeline, observes specific frames going past, timestamps them,
  and writes one JSONL row per turn.

## What we capture per turn

The Pipecat pipeline emits typed frames at known moments. We timestamp
them with a per-call monotonic clock and derive:

| Metric | Source frame | Definition |
|---|---|---|
| `t_user_start_ms` | `UserStartedSpeakingFrame` | Caller started speaking |
| `t_user_stop_ms` | `UserStoppedSpeakingFrame` | Caller stopped speaking |
| `t_bot_start_ms` | first `TTSAudioRawFrame` | First bot audio chunk |
| `t_bot_stop_ms` | `BotStoppedSpeakingFrame` | Bot finished speaking |
| `ttft_ms` | derived | `t_bot_start − t_user_stop` — **headline metric** |
| `silence_to_ttft_ms` | derived | `t_bot_start − t_user_start` — full silence gap as caller perceives it |
| `total_response_ms` | derived | `t_bot_stop − t_user_stop` |
| `tpot_ms` | derived | mean inter-chunk gap across all `TTSAudioRawFrame`s — sustained throughput |
| `bot_speaking_ms` | derived | `t_bot_stop − t_bot_start` |
| `user_speech_duration_ms` | derived | `t_user_stop − t_user_start` |

Plus per turn:
- Token usage (input_text / input_audio / input_cached / output_text /
  output_audio) — from the OpenAI Realtime `response.done` event,
  surfaced by Pipecat.
- `cost_usd` — sum of (tokens × per-1M rate from `pricing.py`).

We intentionally do **not** capture `transcript_in` / `transcript_out`.
Both would require extra OpenAI work (side-channel STT for the user
audio, text-modality output for the bot reply) that adds cost without
serving the latency-measurement goal of this experiment.

## Output layout

One subdirectory per call. Each contains:
- `turns.jsonl` — one JSON row per user turn.
- `recording.wav` — mono PCM 16 kHz, both caller and bot audio.

OpenAI Realtime emits bot audio at 24 kHz while the rest of the pipeline
runs at 16 kHz. The recording tap resamples bot audio to 16 kHz on the
fly (numpy linear interpolation — speech-band only, sub-µs per chunk),
so the output is a single WAV at one rate that plays both directions at
correct speed.

```
results/
├── 20260512T103045Z_<call_id>/
│   ├── turns.jsonl
│   └── recording.wav
└── ...
```

```json
{"call_sid":"abc-123","turn":1,
 "ttft_ms":743,"silence_to_ttft_ms":2940,"total_response_ms":2843,
 "tpot_ms":34,"bot_speaking_ms":1900,
 "user_speech_duration_ms":2222,
 "input_audio_tokens":125,"output_audio_tokens":95,"cost_usd":0.0034}
```

## Summarising

```bash
uv run python aggregate.py            # all calls so far
uv run python aggregate.py --latest   # most recent call only
```

Prints p50/p99 of `ttft_ms` and `total_response_ms`, plus average
`tpot_ms` and average cost per turn. p99 is the headline — voice UX
is dominated by worst-case latency.

## Module map

| File | Responsibility |
|---|---|
| `server.py` | FastAPI: `/answer` (Vobiz webhook) + `/ws/{call_sid}` (accepts the WS and starts the pipeline) |
| `pipeline.py` | Builds the Pipecat pipeline for one call: transport, VAD, user-turn processor, LLM service, latency observer |
| `latency_observer.py` | Custom Pipecat `FrameProcessor` — captures per-turn metrics and writes JSONL |
| `prompts.py` | Default system prompt (short, non-language-specific — language is set via `LANGUAGE` env var) |
| `pricing.py` | Per-1M token rates + per-turn cost calc |
| `aggregate.py` | Read all JSONL → p50/p99 summary |
| `recording_tap.py` | Pipecat FrameProcessor (ported from reach_layer/voice) — captures both audio directions into a WAV buffer for the per-call `recording.wav` |

## Prerequisites

**OpenAI account:**
- API Key
- `gpt-realtime-mini` enabled in your project
- Account has active billing

**Vobiz account:**
- Phone number assigned
- Auth credentials (`auth_id`, `auth_token`)
- Account funded for test calls

**Dev environment:**
- Python 3.11+
- `uv` installed
- `ngrok` installed (free tier works; the random subdomain changes
  each restart, so you'll re-update Vobiz's answer-URL each time —
  paid ngrok / Cloudflare Tunnel give a stable subdomain)
- Pipecat installed (`pipecat-ai` with the `silero`, `openai`, and
  Vobiz transport extras)

## Environment variables

| Var | Purpose |
|---|---|
| `OPENAI_API_KEY` | Auth for OpenAI Realtime |
| `VOBIZ_AUTH_ID` | Vobiz REST API auth (Pipecat uses this for hangup) |
| `VOBIZ_AUTH_TOKEN` | Vobiz REST API auth |
| `PUBLIC_URL` | ngrok HTTPS URL that Vobiz will reach `/answer` on |
| `MODEL` | `gpt-realtime-mini` |
| `VOICE` | `alloy`, `nova`, `sage`, etc. |
| `LANGUAGE` | Transcription language hint (`hi` for Hindi). Passed to OpenAI as `input_audio_transcription.language` |
| `VAD_SILENCE_MS` | VAD silence threshold (default 600 ms — tuned for Hindi pauses) |

## Open questions to confirm on first test call

These are not blockers — items to verify with the first end-to-end call.

- **Does Pipecat surface OpenAI's `response.done` token-usage payload
  to our observer?** If yes: cost calculation is straightforward. If
  no: hook directly into the Realtime service's raw event callback,
  or omit `cost_usd` until the integration is wired.
- **TPOT semantics** — `tpot_ms` is measured as mean wall-clock gap
  between chunks delivered to our observer, which includes any network
  jitter between OpenAI and our server. Useful as a relative number
  across runs; not an absolute measure of OpenAI's generation speed.

## Scope

This experiment captures the **voice model's own latency** through a
clean Pipecat pipeline — no DPG framework, no Memory / Trust / NLU,
no Raya STT/TTS.
