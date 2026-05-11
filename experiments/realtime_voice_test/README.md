# Realtime Voice Test

Vobiz ↔ `gpt-realtime-mini` bridge for benchmarking voice-model latency on
real Hindi phone calls. Captures per-turn TTFT, transcripts, token usage,
and cost.

Design spec: [`docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md`](../../docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md)

## Prerequisites

- An OpenAI API key with access to `gpt-realtime-mini`.
- A Vobiz account with auth credentials (`auth_id`, `auth_token`) and a phone number with sufficient balance.
- ngrok installed and authenticated (`ngrok authtoken ...`) for exposing the local server to Vobiz.
- Python 3.11+, `uv` installed.
- Pipecat (auto-installed via `uv sync`).

## Setup

```bash
cd experiments/realtime_voice_test
unset VIRTUAL_ENV
uv sync
```

## Run (three terminals)

### Terminal 1 — start the test server

```bash
cd experiments/realtime_voice_test
export OPENAI_API_KEY=sk-...
export VOBIZ_AUTH_ID=...
export VOBIZ_AUTH_TOKEN=...
export PUBLIC_URL=https://<your-ngrok-subdomain>.ngrok-free.app
export MODEL=gpt-realtime-mini
export VOICE=alloy                       # alloy | nova | sage | etc.
export LANGUAGE=hi                       # transcription language hint
export VAD_SILENCE_MS=600
unset VIRTUAL_ENV

uv run python server.py
```

The server listens on port 8007 by default.

### Terminal 2 — expose the server via ngrok

```bash
ngrok http 8007
```

Copy the `https://...ngrok-free.app` URL it prints and use it as
`PUBLIC_URL` in Terminal 1. If `PUBLIC_URL` is wrong or stale, restart
the server with the right value before placing the call.

### Terminal 3 — place an outbound call via Vobiz

```bash
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | \
  python3 -c "import sys,json;print(json.load(sys.stdin)['tunnels'][0]['public_url'])")
echo "Using: $NGROK_URL"

curl -X POST https://api.vobiz.ai/api/v1/Account/<YOUR_ACCOUNT_ID>/Call/ \
  -H "X-Auth-ID: <YOUR_AUTH_ID>" \
  -H "X-Auth-Token: <YOUR_AUTH_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{
    \"from\": \"+91XXXXXXXXXX\",
    \"to\":   \"+91XXXXXXXXXX\",
    \"answer_url\": \"$NGROK_URL/answer\",
    \"answer_method\": \"POST\"
  }"
```

Your phone rings → answer → speak Hindi → hear the model reply → hang up.

## What you get

Per call, one subdirectory is written under `results/`, named
`{YYYYmmddTHHMMSSZ}_{vobiz_call_uuid}/`, containing:
- `turns.jsonl` — one JSON row per user turn.
- `recording.wav` — 16 kHz mono PCM audio of both caller and bot (use
  any media player to review by ear).

```
results/
├── 20260512T103045Z_abc123/
│   ├── turns.jsonl
│   └── recording.wav
├── 20260512T110212Z_def456/
│   ├── turns.jsonl
│   └── recording.wav
```

```json
{"call_sid": "abc123", "turn": 1, "ttft_ms": 743, "silence_to_ttft_ms": 2940,
 "total_response_ms": 2843, "tpot_ms": 34, "bot_speaking_ms": 1900,
 "user_speech_duration_ms": 2222,
 "input_audio_tokens": 125, "output_audio_tokens": 95, "cost_usd": 0.0034}
```

Transcripts are intentionally not captured — they would require extra
OpenAI work (side-channel STT + text-modality output) that costs more
without serving the latency-measurement goal.

## Summarise collected data

```bash
uv run python aggregate.py                  # all calls in results/
uv run python aggregate.py --latest         # most recent call only
```

Prints headline p50 / p99 TTFT, total response, and average cost.

## Troubleshooting

| Symptom | What to try |
|---|---|
| Phone rings then immediately hangs up | Check ngrok inspector (`http://localhost:4040`) — did `/answer` return 200 with the right XML? If not, `PUBLIC_URL` is stale. |
| User audio reaches Vobiz but no response audio plays | Check server logs for errors. Likely `OPENAI_API_KEY` is wrong or the model name isn't `gpt-realtime-mini`. |
| Response is in English instead of Hindi | The model should mirror the user's input language. If it drifts, the fallback is to add a one-line `"Reply in the user's language."` instruction to `prompts.py` and restart. |
| Model cuts the user off mid-Hindi-sentence | Bump `VAD_SILENCE_MS=900` or `1000` and restart. Hindi has longer mid-clause pauses than English. |
| JSONL is empty after a call | Check the server logs — if no turns were recorded, the model didn't complete a turn. Often the call was too short or the user didn't speak. |

## Files

- `server.py` — FastAPI entry point (`/answer` webhook + `/ws/{call_sid}` accepts the WebSocket and runs the Pipecat pipeline)
- `pipeline.py` — Assembles the Pipecat pipeline for one call
- `latency_observer.py` — Custom Pipecat FrameProcessor — captures per-turn metrics and writes JSONL
- `prompts.py` — Default system prompt
- `pricing.py` — Per-1M token rates + per-turn cost calc
- `aggregate.py` — Reads all JSONL → p50/p99 summary
- `recording_tap.py` — Pipecat FrameProcessor (ported from reach_layer/voice) for per-call audio capture
- `results/` — per-call output (subdirectories: `{timestamp}_{call_sid}/turns.jsonl` + `recording.wav`)
