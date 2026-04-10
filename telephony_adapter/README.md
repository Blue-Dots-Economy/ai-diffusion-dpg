# Telephony Adapter

DPG building block — **Reach Layer / Telephony channel adapter**.

Bridges inbound and outbound PSTN calls (via Vobiz) to the Agent Core. Handles per-call audio streaming over WebSocket, speech-to-text via Raya STT, text-to-speech via Raya TTS, and outbound call campaigns.

---

## Role in the DPG framework

```
PSTN caller
  → Vobiz WebSocket  (/ws/{call_sid})
    → Raya STT        (µ-law audio → transcript)
      → Agent Core    (POST /process_turn)
        → Raya TTS    (response text → PCM audio)
          → Vobiz WebSocket (audio back to caller)
```

The adapter is stateless across calls. All session state lives in the Memory Layer and is accessed through Agent Core.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/answer` | Vobiz webhook on call answered. Returns XML that instructs Vobiz to open a WebSocket stream. |
| `WS` | `/ws/{call_sid}` | Bidirectional audio stream for an active call. |
| `POST` | `/campaign` | Trigger an outbound PSTN call. Also callable by Action Gateway as `telephony_channel_switch`. |
| `POST` | `/recording-finished` | Vobiz webhook: recording has stopped. |
| `POST` | `/recording-ready` | Vobiz webhook: recording MP3 is ready. |
| `GET` | `/health` | Liveness probe. Returns `{"status": "ok"}`. |

---

## Per-call turn loop

1. Vobiz POSTs to `/answer` → adapter returns XML with `wss://<public_url>/ws/{call_sid}`.
2. Vobiz opens a WebSocket and sends a `start` event (call SID, stream SID, caller ID).
3. Vobiz streams `media` events (µ-law 8000 Hz audio chunks).
4. On a `stop` event, accumulated audio is sent to **Raya STT** for transcription.
5. Transcript is forwarded to **Agent Core** (`POST /process_turn`).
6. Agent Core response text is sent to **Raya TTS** (SSE streaming → PCM F32LE).
7. Audio is base64-encoded and sent back to Vobiz as a `media` event.
8. If Agent Core marks the turn as escalated, the WebSocket is closed.

Silence (empty transcript) is discarded without calling Agent Core.

---

## Source layout

```
telephony_adapter/
├── server.py              # FastAPI app factory, all HTTP/WS endpoints
├── config_loader.py       # YAML config deep-merge
├── config/
│   └── telephony.yaml     # DPG framework defaults (env-var placeholders)
├── src/
│   ├── base.py            # TelephonyAdapterBase ABC, TelephonyTurnInput/Result dataclasses
│   ├── telephony_adapter.py   # VobizTelephonyAdapter — per-call turn loop
│   ├── agent_core_service.py  # HTTP client → Agent Core /process_turn
│   ├── raya_stt_service.py    # WebSocket client → Raya STT
│   ├── raya_tts_service.py    # HTTP/SSE client → Raya TTS
│   ├── vobiz_serializer.py    # Encode/decode Vobiz WebSocket JSON frames
│   └── campaign_manager.py    # Outbound call trigger via Vobiz REST API
└── tests/
    ├── test_telephony_adapter.py
    ├── test_agent_core_service.py
    ├── test_raya_stt_service.py
    ├── test_raya_tts_service.py
    ├── test_vobiz_serializer.py
    ├── test_campaign_manager.py
    ├── test_server.py
    ├── test_base.py
    └── test_config_loader.py
```

---

## Configuration

Config is deep-merged from two YAML files at startup:

| File | Purpose |
|---|---|
| `config/telephony.yaml` | DPG framework defaults (env-var placeholders) |
| `$DOMAIN_CONFIG_PATH` | Domain-specific overrides |

Environment variables used by `config/telephony.yaml`:

| Variable | Required | Description |
|---|---|---|
| `PUBLIC_URL` | Yes | Publicly reachable base URL of this service (e.g. `https://telephony.example.com`). Used to build the WebSocket URL returned in `/answer` XML. |
| `VOBIZ_AUTH_ID` | Yes | Vobiz account auth ID. |
| `VOBIZ_AUTH_TOKEN` | Yes | Vobiz account auth token. |
| `VOBIZ_FROM_NUMBER` | Yes | E.164 caller ID for outbound calls. |
| `RAYA_API_KEY` | Yes | Raya API key (used for both STT and TTS). |

Additional config keys (with defaults):

```yaml
telephony_adapter:
  port: 8006
  vobiz:
    api_base: https://api.vobiz.ai/api/v1
    max_retries: 3
  raya:
    stt_wss_url: wss://hub.getraya.app/transcribe
    tts_base_url: https://hub.getraya.app/v1
    language: hi
    voice_id: voice_001
    tts_speed: 1.0
    tts_timeout_s: 30.0
  agent_core:
    base_url: http://agent_core:8000
    timeout_ms: 5000
    fallback_phrase: "I'm sorry, I couldn't process that. Please try again."
```

Override any key in your domain config file to change runtime behaviour.

---

## Running locally

**With Docker (recommended):**

```bash
# Build from repo root so observability_layer/ is in the build context
docker build -f telephony_adapter/Dockerfile -t telephony_adapter .

docker run --rm \
  -e PUBLIC_URL=https://your-tunnel.example.com \
  -e VOBIZ_AUTH_ID=your_auth_id \
  -e VOBIZ_AUTH_TOKEN=your_auth_token \
  -e VOBIZ_FROM_NUMBER=+91xxxxxxxxxx \
  -e RAYA_API_KEY=your_raya_key \
  -p 8006:8006 \
  telephony_adapter
```

**Without Docker:**

```bash
cd telephony_adapter
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
cd telephony_adapter
uv run pytest                                          # all tests
uv run pytest tests/test_telephony_adapter.py          # single file
uv run pytest --cov=src --cov-report=term-missing      # with coverage
```

Coverage threshold: **70%** (enforced in `pyproject.toml`).

---

## Observability

Structured logs are emitted for every significant operation (`operation`, `status`, `latency_ms`, `error` fields). No PII or caller phone numbers are logged.

OpenTelemetry traces span the full turn (`telephony.turn`), STT call (`telephony.stt`), TTS call (`telephony.tts`), and Agent Core call (`telephony.agent_core_call`). Metrics exported:

| Metric | Type | Description |
|---|---|---|
| `telephony.active_calls` | UpDownCounter | Number of currently active concurrent calls. |
| `telephony.turn.latency_ms` | Histogram | End-to-end per-turn latency in milliseconds. |

Configure the OTel collector endpoint via:

```yaml
observability:
  otel:
    collector_endpoint: http://otelcol:4317
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | HTTP and WebSocket server |
| `uvicorn` | ASGI server |
| `httpx` | Async HTTP client (Agent Core, Raya TTS, Vobiz REST) |
| `websockets` | WebSocket client (Raya STT) |
| `pyyaml` | Config loading |
| `observability-layer` | OTel initialisation (local path dep) |
| `opentelemetry-instrumentation-fastapi` | Auto-instrumentation |
| `opentelemetry-instrumentation-httpx` | Auto-instrumentation |
