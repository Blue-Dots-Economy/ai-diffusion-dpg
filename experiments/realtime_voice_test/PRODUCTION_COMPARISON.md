# Running the production-side comparison

The other half of the latency comparison: a real Hindi call through the **full
production DPG stack** (Vobiz → Pipecat → Silero VAD → Raya STT → Agent Core
[`gpt-4.1-mini`] → Raya TTS → Vobiz), with a per-turn JSONL captured by a
`LatencyObserverProcessor` spliced into the production pipeline.

Result lands at `experiments/realtime_voice_test/results_production/`, parallel
to the gpt-realtime-mini results. Both datasets share the comparable fields
(`ttft_ms`, `total_response_ms`, `tpot_ms`, `bot_speaking_ms`,
`user_speech_duration_ms`). Production rows additionally carry per-stage
breakdowns (`stt_ms`, `agent_ms`, `tts_first_audio_ms`).

## Prerequisites (in addition to the realtime experiment's prerequisites)

- **OpenAI API key** with `gpt-4.1-mini` access (already the production primary
  model in [`dev-kit/configs/kkb/agent_core.yaml`](../../dev-kit/configs/kkb/agent_core.yaml#L8)).
- **Raya credentials** in the environment expected by `reach_layer/voice`'s
  config (the STT/TTS providers production already uses).
- **Anthropic API key** if the agent's fallback path is exercised.
- **Vobiz** account + a phone number whose answer URL can be pointed at
  ngrok (same as the realtime experiment).

## One-time

1. Confirm the LLM model in `dev-kit/configs/kkb/agent_core.yaml`:
   ```yaml
   agent:
     primary_model: gpt-4.1-mini-2025-04-14
   ```
   (Already the case on this branch — nothing to do.)

2. Decide on a results directory. Recommended (parallels the realtime
   experiment's results):
   ```
   experiments/realtime_voice_test/results_production/
   ```

## Run

### Terminal 1 — bring up the DPG framework

The voice service needs Agent Core, Memory Layer, Trust Layer, Knowledge
Engine, Observability, and Action Gateway running. Easiest path is the
existing dev compose stack:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
cd automation/docker
docker compose -f docker-compose.dev.yml up -d
```

Wait until services report healthy (~30 seconds).

### Terminal 2 — start the voice service (with latency capture enabled)

```bash
cd reach_layer/voice
unset VIRTUAL_ENV

# Required for the production-side latency observer to write JSONL.
# Path is relative to repo root or absolute.
export LATENCY_OBSERVER_DIR=$(pwd)/../../experiments/realtime_voice_test/results_production

# Standard reach_layer/voice config (Vobiz, Raya, public URL).
# See reach_layer/voice/README.md for the full env-var list.
export VOBIZ_AUTH_ID=...
export VOBIZ_AUTH_TOKEN=...
export PUBLIC_URL=https://<your-ngrok-subdomain>.ngrok-free.app
# ... plus whatever Raya STT/TTS credentials your domain config needs ...

uv run python server.py
```

The voice service listens on port **8006** by default (different from the
realtime experiment's 8007).

### Terminal 3 — expose port 8006 via ngrok

```bash
ngrok http 8006
```

Copy the HTTPS URL, set it as `PUBLIC_URL` (restart Terminal 2 if you
edited it), and **update your Vobiz number's answer URL** to point at
`https://<ngrok>/answer`.

### Terminal 4 — dial in

Pick up your phone and call the Vobiz number. Speak Hindi as you did for
the realtime experiment. Hang up when done.

## What gets written

```
experiments/realtime_voice_test/results_production/
└── 20260512T140000Z_<call_sid>/
    └── turns.jsonl       ← per-turn latency rows
```

Each JSONL row contains the comparable metrics PLUS per-stage breakdown:

```json
{"call_sid": "abc-123",
 "turn": 1,
 "stack": "production",
 "model": "gpt-4.1-mini-2025-04-14",
 "language": "hi",
 "ttft_ms": 1820.4,          // comparable to realtime experiment
 "silence_to_ttft_ms": 4500.1,
 "total_response_ms": 9842.2,
 "bot_speaking_ms": 8021.8,
 "user_speech_duration_ms": 2680.6,
 "tpot_ms": 38.2,
 "stt_ms":   420.5,          // production-only: Raya STT latency
 "agent_ms": 980.2,          // production-only: Agent Core + LLM latency
 "tts_first_audio_ms": 419.7 // production-only: Raya TTS first-byte
}
```

Recording goes wherever `reach_layer/voice`'s recording config points
(by default `/var/recordings/YYYY/MM/DD/{call_sid}.wav` — override
`reach_layer.channels.voice.recording.local.base_path` if you want it
co-located with the JSONL).

## Comparing the two datasets

After one production call you can already eyeball it. After ~5–10 calls
of comparable length to the realtime runs, run the aggregator:

```bash
# Realtime experiment (existing)
uv run python aggregate.py --dir experiments/realtime_voice_test/results

# Production-stack (new)
uv run python aggregate.py --dir experiments/realtime_voice_test/results_production
```

Both reports include `ttft_p50` and `ttft_p99` — that's the headline
comparison. The decision rule in
[`docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md`](../../docs/superpowers/specs/2026-05-08-realtime-voice-test-design.md)
takes it from here.

## Per-stage diagnostic

If production's p99 is high, the `stt_ms`, `agent_ms`, `tts_first_audio_ms`
fields tell you where the time goes:

- `stt_ms`     — Raya STT latency
- `agent_ms`   — Agent Core orchestration + LLM (`gpt-4.1-mini`) generation
- `tts_first_audio_ms` — Raya TTS time-to-first-audio

`ttft_ms ≈ stt_ms + agent_ms + tts_first_audio_ms` (plus small scheduling
overhead). The biggest contributor is usually the right place to optimise.

## Disabling the observer

The observer is **opt-in via env var**. If `LATENCY_OBSERVER_DIR` is not
set, the pipeline runs exactly as production — no observer instantiated,
no extra processor in the pipeline. So this change is safe on the
production deployment path; only this branch's experiment calls activate
it.
