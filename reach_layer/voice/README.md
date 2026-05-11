# Telephony Adapter

DPG building block — **Reach Layer / Telephony channel adapter**.

Bridges inbound PSTN calls (via Vobiz) to the Agent Core. Handles per-call audio streaming over WebSocket, voice activity detection via Silero VAD, speech-to-text via Raya STT, text-to-speech via Raya TTS, and outbound call campaigns.

All component choices (operator, VAD, STT, TTS) are abstracted behind DPG base classes so alternative providers can be wired in without changing the adapter lifecycle.

---

## Role in the DPG framework

```
PSTN caller
  → Vobiz /answer webhook   (server.py extracts caller_id, returns XML)
    → Vobiz WebSocket       (/ws/{call_sid})
      → VobizOperator       (handshake → stream_id/call_id)
        → SileroVAD         (µ-law audio → VAD-segmented utterances)
          → RayaSTTService  (utterance WAV → transcript text)
            → Agent Core    (POST /process_turn  {user_id: caller_id})
              → RayaTTSService  (response text → PCM16 audio, SSE streaming)
                → Vobiz WebSocket (audio back to caller)
```

`caller_id` (E.164 phone number from the `/answer` webhook `From` field) is passed to Agent Core as `user_id`, allowing the Memory Layer to recognise returning callers across sessions.

The adapter is stateless across calls. All session state lives in the Memory Layer and is accessed through Agent Core.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/answer` | Vobiz webhook on call answered. Extracts `caller_id` from `From` field, returns XML instructing Vobiz to open a WebSocket stream. |
| `WS` | `/ws/{call_sid}` | Bidirectional audio stream for an active call. Runs the full VAD→STT→Agent→TTS pipeline. |
| `POST` | `/campaign` | Trigger an outbound PSTN call. Also callable by Action Gateway as `telephony_channel_switch`. |
| `POST` | `/recording-finished` | Vobiz webhook: recording has stopped. |
| `POST` | `/recording-ready` | Vobiz webhook: recording MP3 is ready. |
| `GET` | `/health` | Liveness probe. Returns `{"status": "ok"}`. |

---

## Per-call lifecycle

1. Vobiz POSTs to `/answer` → `server.py` extracts `caller_id` from `From` field, stores `call_sid → caller_id`, returns Vobiz XML with `wss://<public_url>/ws/{call_sid}`.
2. Vobiz opens a WebSocket → `VobizOperator.parse_handshake()` reads `stream_id` and `call_id` from the Vobiz start messages.
3. `VobizOperator.create_transport()` builds a `FastAPIWebsocketTransport` with `VobizFrameSerializer`.
4. `SileroVADWrapper` creates a `SileroVADAnalyzer` with config-driven parameters.
5. Pipeline runs: `transport.input → VADProcessor → UserTurnProcessor → RayaSTTService → AgentCoreLLMProcessor → TTSTextSanitizerProcessor → RayaTTSService → transport.output`.
6. On client connect, the adapter opens a `?user_id=<caller_id>` SSE subscription to Agent Core. For a brand-new session, Agent Core emits the entry subagent's `opening_phrase` as a `SentenceEvent` (GH-149); any other channel behaviour reuses the normal turn flow, so there is no static pickup greeting to maintain per domain.
7. Each VAD-segmented utterance is transcribed by `RayaSTTService.transcribe()` (HTTP multipart POST to Raya).
8. The transcript is forwarded to Agent Core (`POST /process_turn`) with `user_id = caller_id`.
9. Agent Core response text is synthesised by `RayaTTSService.synthesize()` (SSE stream, F32LE → PCM16).
10. If Agent Core returns `was_escalated=true`, an `EndFrame` is pushed to hang up.

---

## Barge-in (GH-152)

The adapter supports mid-response interruption: when the caller starts speaking while the bot is playing, the bot goes silent immediately and treats the new speech as a fresh turn rather than a continuation.

Two layers cooperate:

1. **Audio-level (voice pipeline).** A `UserTurnProcessor` between `VADProcessor` and STT converts `VADUserStartedSpeakingFrame` into a pipecat `InterruptionFrame` whenever the bot is currently speaking. Pipecat flushes the TTS queue, and `AgentCoreLLMProcessor._start_interruption()` stops forwarding further `SentenceEvent`s from the in-flight Agent Core stream. If `barge_in_acknowledgement` is configured, it is spoken briefly (e.g. `"ठीक है, एक सेकंड।"`).
2. **Turn-logic (Agent Core).** Once STT produces the caller's new transcript, `TurnAssembler.add_segment()` sees the turn is `INVOKED`, calls `cancel()` (emitting `DoneEvent(turn_status="interrupted")`), and discards the original interrupted segments. Only the barge-in segment replays into the next turn — so the LLM sees just the correction, not `"<original> <correction>"`.

Relevant config keys under `reach_layer.channels.voice.agent_core`:

| Key | Default | Purpose |
|---|---|---|
| `barge_in_acknowledgement` | `""` | Short phrase spoken when the caller interrupts. Empty → bot simply goes silent. |
| `fallback_phrase` | `""` | Spoken only when the Agent Core HTTP call fails (timeout / 5xx / empty stream). **Not** a conversational fallback. |

Relevant log lines (useful for Loki dashboards):
- `agent_core_llm.interruption` — pipecat InterruptionFrame reached the processor.
- `agent_core_llm.interrupted_during_stream` — SSE consumer exited because of the flag.
- `turn_assembler.barge_in_discarded_segments` — Agent Core dropped original segments; includes `discarded_count` and `pending_count`.

VAD tuning lives under `reach_layer.channels.voice.vad`; the UserTurnProcessor's stop strategy reuses `vad.stop_secs` as its `user_speech_timeout`.

---

## Source layout

```
reach_layer/voice/
├── server.py              # FastAPI app factory — /answer, /ws/{call_sid}, /campaign endpoints
├── src/
│   ├── base.py            # TelephonyAdapterBase ABC; STTError, TTSError, TelephonyError
│   ├── bot.py             # run_bot() — thin entry point, delegates to VobizAdapter
│   ├── vobiz_adapter.py   # VobizAdapter — concrete TelephonyAdapterBase; owns call lifecycle
│   ├── campaign_manager.py
│   ├── operators/
│   │   ├── operator_base.py    # TelephonyOperatorBase ABC (parse_handshake, create_transport, webhook_xml)
│   │   └── vobiz_operator.py   # VobizOperator — Vobiz/Plivo handshake + VobizFrameSerializer
│   ├── vad/
│   │   ├── vad_base.py         # VADAnalyzerBase ABC (create_analyzer)
│   │   └── silero_vad.py       # SileroVADWrapper — config-driven SileroVADAnalyzer factory
│   └── pipecat_services/
│       ├── stt_base.py         # STTServiceBase ABC — no Pipecat imports (transcribe method)
│       ├── tts_base.py         # TTSServiceBase ABC — no Pipecat imports (synthesize method)
│       ├── raya_stt.py         # RayaSTTService — HTTP multipart → transcript
│       ├── raya_tts.py         # RayaTTSService — SSE stream → PCM16 chunks
│       └── agent_core_llm.py   # AgentCoreLLMProcessor — TranscriptionFrame → POST /process_turn → TTSSpeakFrame
└── tests/
    ├── test_base.py
    ├── test_vobiz_adapter.py
    ├── test_server.py
    ├── test_campaign_manager.py
    ├── operators/
    │   ├── test_operator_base.py
    │   └── test_vobiz_operator.py
    ├── vad/
    │   ├── test_vad_base.py
    │   └── test_silero_vad.py
    └── pipecat_services/
        ├── test_stt_base.py
        ├── test_tts_base.py
        ├── test_raya_stt.py
        ├── test_raya_tts.py
        └── test_agent_core_llm.py
```

---

## Configuration

Config is loaded by the shared Reach Layer loader (`reach_layer_base.load_reach_config("voice", ...)`) from two files, deep-merged at startup:

| File | Purpose |
|---|---|
| `reach_layer/config/dpg.yaml` (or `$DPG_CONFIG_PATH`) | Unified Reach Layer defaults (framework) |
| `reach_layer/config/domain.yaml` (or `$DOMAIN_CONFIG_PATH`) | Domain-specific overrides |

The voice channel reads its slice from `reach_layer.channels.voice.*`. For backward compatibility the loader also aliases that block at the legacy top-level key `telephony_adapter.*` — existing code in `src/` still reads from `config["telephony_adapter"][...]`.

Environment variables consumed by the voice section:

| Variable | Required | Description |
|---|---|---|
| `PUBLIC_URL` | Yes | Publicly reachable base URL of this service (e.g. `https://telephony.example.com`). Used to build the WebSocket URL in `/answer` XML. |
| `VOBIZ_AUTH_ID` | Yes | Vobiz account auth ID. |
| `VOBIZ_AUTH_TOKEN` | Yes | Vobiz account auth token. |
| `VOBIZ_FROM_NUMBER` | Yes | E.164 caller ID for outbound calls. |
| `RAYA_API_KEY` | Yes | Raya API key (used for both STT and TTS). |

Voice section (under `reach_layer.channels.voice`) with defaults:

```yaml
reach_layer:
  common:
    observability:
      otel:
        collector_endpoint: http://otelcol:4317
        sample_rate: 1.0
        export_interval_ms: 5000
  channels:
    voice:
      enabled: true
      assembly_mode: session
      port: 8006
      public_url: ${PUBLIC_URL}
      vobiz:
        auth_id: ${VOBIZ_AUTH_ID}
        auth_token: ${VOBIZ_AUTH_TOKEN}
        api_base: https://api.vobiz.ai/api/v1
        from_number: ${VOBIZ_FROM_NUMBER}
      vad:
        stop_secs: 0.35
        min_volume: 0.3
        confidence: 0.4
        start_secs: 0.1
        smoothing_factor: 0.1
      raya:
        api_key: ${RAYA_API_KEY}
        stt_wss_url: https://hub.getraya.app/transcribe
        tts_base_url: https://hub.getraya.app/v1
        tts_model: standard
        language: hi
        voice_id: voice_001
        tts_speed: 1.0
      agent_core:
        base_url: http://agent_core:8000
        timeout_ms: 5000
        # Transport-failure fallback only. Conversational / low-confidence
        # fallbacks are handled by the `clarification` subagent in
        # agent_core.yaml. The per-call welcome is the entry subagent's
        # opening_phrase, emitted via SSE (GH-149).
        fallback_phrase: "I'm sorry, I couldn't process that. Please try again."
        # Short phrase spoken when the caller interrupts mid-response (GH-152).
        # Empty = bot simply goes silent on barge-in.
        barge_in_acknowledgement: ""
```

---

## Running locally

**With Docker (recommended):**

```bash
# Build from repo root so observability_layer/ and reach_layer/base/ are in the build context
docker build -f reach_layer/voice/Dockerfile -t reach_layer_voice .

docker run --rm \
  -e PUBLIC_URL=https://your-tunnel.example.com \
  -e VOBIZ_AUTH_ID=your_auth_id \
  -e VOBIZ_AUTH_TOKEN=your_auth_token \
  -e VOBIZ_FROM_NUMBER=+91xxxxxxxxxx \
  -e RAYA_API_KEY=your_raya_key \
  -p 8006:8006 \
  reach_layer_voice
```

**Without Docker:**

```bash
cd reach_layer/voice
export PUBLIC_URL=https://your-tunnel.example.com
export VOBIZ_AUTH_ID=...
export VOBIZ_AUTH_TOKEN=...
export VOBIZ_FROM_NUMBER=...
export RAYA_API_KEY=...

uv sync
uv run uvicorn server:create_app --factory --host 0.0.0.0 --port 8006
```

Service listens on port `8006`. Use a tunnelling tool (e.g. ngrok) to expose the endpoint so Vobiz can reach `/answer` and `/ws/{call_sid}`.

---

## Running tests

```bash
cd reach_layer/voice
uv run pytest                                          # all tests
uv run pytest tests/test_vobiz_adapter.py             # single file
uv run pytest --cov=src --cov-report=term-missing     # with coverage
```

Coverage threshold: **70%** (enforced in `pyproject.toml`). Current: ~93%.

---

## Observability

Structured logs are emitted for every significant operation (`operation`, `status`, `latency_ms`, `error` fields). No PII or caller phone numbers are logged outside the designated audit log path.

Configure the OTel collector endpoint via:

```yaml
reach_layer:
  common:
    observability:
      otel:
        collector_endpoint: http://otelcol:4317
```

---

## Recording

Per-call audio recording is available behind the `reach_layer.channels.voice.recording.source` config switch. Default is `disabled`.

| Source | Mechanism | Output format |
|---|---|---|
| `disabled` (default) | No recording. | — |
| `pipeline` | A `RecordingTapProcessor` inserted into the Pipecat pipeline captures inbound + outbound audio into a single-channel WAV. | `wav` |
| `vobiz` | Vobiz's REST `Record/` API records server-side; the MP3 URL is fed back via the `/recording-ready` webhook and fetched. | `mp3` |

Recording starts ONLY after Trust Layer consent is granted for `purpose = recording.consent_purpose` (default `"recording"`). The greeting/consent prompt is NOT included.

Each finalised recording produces:
- An audio file under `{base_path}/YYYY/MM/DD/{call_sid}.{ext}` (local) or `s3://{bucket}/{prefix}YYYY/MM/DD/{call_sid}.{ext}` (S3).
- A sidecar JSON manifest next to it (same path with `.json`) carrying `call_sid`, `session_id`, `caller_id_hash` (16-hex SHA256 of salt+caller_id, never raw), `source`, `format`, `duration_ms`, `bytes`, `sha256`, `recording_uri`, `consent_granted_ts`, `start_ts`, `end_ts`, `trace_id`.
- A `recording.started` / `recording.stored` / `recording.empty` / `recording.failed` signal emitted to the Observability Layer.
- An OTel `recording.lifecycle` span linked to the inbound call's span, with child spans per stage.

Retention is delegated to the storage backend (S3 lifecycle rules, ops cron, etc.) — the app does not delete files itself.

Design doc: `docs/superpowers/specs/2026-05-08-voice-call-recording-design.md`.

---

## Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | HTTP and WebSocket server |
| `uvicorn` | ASGI server |
| `httpx` | Async HTTP client (Agent Core, Raya STT/TTS, Vobiz REST) |
| `pipecat-ai[websocket,silero]` | Audio pipeline framework, VAD, frame types |
| `pipecat-vobiz` | VobizFrameSerializer — Vobiz/Plivo wire protocol |
| `numpy` | F32LE → PCM16 audio conversion |
| `pyyaml` | Config loading |
| `reach-layer-base` | Shared Reach Layer base classes + config loader (local path dep) |
| `observability-layer` | OTel initialisation (local path dep) |
| `opentelemetry-instrumentation-fastapi` | Auto-instrumentation |
| `opentelemetry-instrumentation-httpx` | Auto-instrumentation |
