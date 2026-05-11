# How the Realtime Voice Test Works

## Goal

Measure end-to-end voice latency of `gpt-realtime-mini` on real Hindi
phone calls — primarily **Time-To-First-Token (TTFT)** at p99, plus
per-turn token usage, transcripts, and cost.

## Architecture

Vobiz routes each call to OpenAI directly via SIP. Audio never passes
through our server. We sit on the **control plane only**: one
WebSocket to OpenAI for session configuration and event capture.

```
Phone caller
   │ PSTN
   ▼
Vobiz (telephony provider)
   │ SIP / RTP   (audio direct, does not touch us)
   ▼
OpenAI SIP endpoint  (proj_<id>@sip.api.openai.com:5061)
   │ HTTP POST  (incoming-call webhook)
   ▼
our FastAPI server  (server.py + bridge.py)
   │ ONE WebSocket  (control + per-turn events, no audio)
   ▼
gpt-realtime-mini
```

- **SIP/RTP leg** — Vobiz dials `proj_<id>@sip.api.openai.com:5061`.
  RTP carries the audio both ways. ngrok is not in this path.
- **Webhook** — OpenAI POSTs to `/webhook/incoming` on call start
  (with HMAC signature). Our server validates the signature, then
  opens the control WebSocket for that call.
- **Control WebSocket** — we send `session.update` (prompt, voice,
  `g711_ulaw` audio format, VAD, transcription language) and listen
  for the per-turn event stream.

## What we capture per turn

OpenAI emits per-turn control events over the WebSocket whether the
audio flows over WS or SIP. We timestamp them with a per-call
monotonic clock and derive:

| Metric | Definition |
|---|---|
| `ttft_ms` | first `response.audio.delta` − `speech_stopped` |
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
├── 20260512T103045Z_<call_id>/turns.jsonl
├── 20260512T110212Z_<call_id>/turns.jsonl
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

Prints p50/p99 of `ttft_ms` and `total_response_ms`, plus average
cost per turn. p99 is the headline — voice UX is dominated by
worst-case latency.

## Module map

| File | Responsibility |
|---|---|
| `server.py` | FastAPI: `/webhook/incoming` — validates OpenAI HMAC, kicks off the per-call WS handler |
| `bridge.py` | Per-call WS event loop + `TurnAccumulator` state machine + JSONL writer |
| `openai_realtime.py` | Async wrapper around the OpenAI Realtime control WebSocket |
| `prompts.py` | Three Hindi system-prompt variants |
| `pricing.py` | Per-1M token rates + per-turn cost calc |
| `aggregate.py` | Read all JSONL → p50/p99 summary |

## One-time provider setup

1. **Vobiz** — configure the SIP trunk to dial
   `proj_<your-project-id>@sip.api.openai.com:5061`.
2. **OpenAI platform** — register the webhook URL
   `https://<your-ngrok-subdomain>.ngrok-free.app/webhook/incoming`.
   Save the webhook secret it generates.

## Environment variables

| Var | Purpose |
|---|---|
| `OPENAI_API_KEY` | Auth for the OpenAI Realtime WebSocket |
| `OPENAI_PROJECT_ID` | SIP routing target |
| `OPENAI_WEBHOOK_SECRET` | HMAC validation of incoming webhooks |
| `PUBLIC_URL` | ngrok URL OpenAI reaches the webhook on |
| `PROMPT_NAME` | `SHORT_HINDI` / `KKB_PERSONA` / `STRICT_HINDI_ONLY` |
| `MODEL` | `gpt-realtime-mini` |
| `VOICE` | `alloy`, `nova`, `sage`, etc. |
| `VAD_SILENCE_MS` | server_vad silence threshold (default 600) |

## Open questions to confirm on first test call

These are not blockers — they're items to verify with the first end-to-end call. The plan adjusts if any of them surprise us.

- **Does `response.audio.delta` arrive on the control WS in SIP mode?**
  If yes: TTFT is measured exactly as defined above. If no: we fall
  back to `response.created` − `speech_stopped` as a proxy and
  document the change. Worst case is a small definitional shift, not
  a missing metric.
- **Does OpenAI emit `input_audio_buffer.speech_started` /
  `speech_stopped` in SIP mode?** Expected yes — these come from the
  model's server VAD, which runs regardless of transport. Confirm on
  the first call.
- **Webhook signature scheme** — OpenAI uses HMAC; verify the exact
  header name and signing-payload shape on the first webhook.

## Scope

This experiment captures the **voice model's own latency** in
isolation — no DPG framework, no Memory/Trust/NLU, no Raya STT/TTS.
A matching production-side test (full production stack with
`gpt-4.1-mini` swapped in as the LLM, captured the same way) lives
in a separate spec; we compare the two p99 numbers to decide whether
replacing Raya STT/TTS with a single voice-native model is worth
integrating.
