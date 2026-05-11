# Realtime Voice Test

Vobiz ↔ `gpt-realtime-mini` bridge for benchmarking voice-model latency on
real Hindi phone calls. Captures per-turn TTFT, transcripts, token usage,
and cost.

Design spec: [`docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md`](../../docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md)

## Prerequisites

- An OpenAI API key with access to `gpt-realtime-mini`.
- A Vobiz account with auth credentials (`X-Auth-ID`, `X-Auth-Token`).
- ngrok installed and authenticated (`ngrok authtoken ...`) for exposing
  the local server to Vobiz.
- `uv` installed.

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
export PUBLIC_URL=https://<your-ngrok-subdomain>.ngrok-free.app
export PROMPT_NAME=SHORT_HINDI          # SHORT_HINDI | KKB_PERSONA | STRICT_HINDI_ONLY
export VOICE=alloy                       # alloy | nova | sage | etc.
export MODEL=gpt-realtime-mini
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
`{YYYYmmddTHHMMSSZ}_{vobiz_call_uuid}/`, containing `turns.jsonl` —
one JSON row per user turn:

```
results/
├── 20260512T103045Z_abc123/turns.jsonl
├── 20260512T110212Z_def456/turns.jsonl
└── 20260512T142800Z_ghi789/turns.jsonl
```

```json
{"call_sid": "abc123", "turn": 1, "ttft_ms": 743, "total_response_ms": 2107,
 "transcript_in": "नमस्ते, मुझे काम चाहिए",
 "transcript_out": "नमस्ते। आप किस तरह का काम ढूंढ रहे हैं?",
 "input_audio_tokens": 125, "output_audio_tokens": 95, "cost_usd": 0.0034, ...}
```

## Summarise collected data

```bash
uv run python aggregate.py                  # all calls in results/
uv run python aggregate.py --latest         # most recent call only
uv run python aggregate.py --prompt-name KKB_PERSONA   # filter
```

Prints headline p50 / p99 TTFT, total response, and average cost. Combine
flags freely (e.g. `--latest --prompt-name STRICT_HINDI_ONLY`).

## Troubleshooting

| Symptom | What to try |
|---|---|
| Phone rings then immediately hangs up | Check ngrok inspector (`http://localhost:4040`) — did `/answer` return 200 with the right XML? If not, `PUBLIC_URL` is stale. |
| User audio reaches Vobiz but no response audio plays | Check server logs for `bridge.openai_loop_error`. Likely `OPENAI_API_KEY` is wrong or the model name isn't `gpt-realtime-mini`. |
| Response is in English instead of Hindi | The selected prompt isn't strict enough. Set `PROMPT_NAME=STRICT_HINDI_ONLY` and restart. |
| Model cuts the user off mid-Hindi-sentence | Bump `VAD_SILENCE_MS=900` or `1000` and restart. Hindi has longer mid-clause pauses than English. |
| JSONL is empty after a call | Check `bridge.turn_finished` log lines — if you don't see any, the model didn't complete a turn. Often the call was too short or the user didn't speak. |

## Files

- `server.py` — FastAPI entry point
- `bridge.py` — per-call coordinator + state machine
- `vobiz_protocol.py` — Vobiz WebSocket frame codec
- `openai_realtime.py` — OpenAI Realtime WebSocket wrapper
- `prompts.py` — Hindi system prompts
- `pricing.py` — cost computation
- `aggregate.py` — summary report
- `results/` — per-call JSONL files
