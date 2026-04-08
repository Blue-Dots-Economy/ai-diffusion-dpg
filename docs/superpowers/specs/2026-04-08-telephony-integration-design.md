# Telephony Integration Design — GH-53

**Date:** 2026-04-08  
**Issue:** [sanketika-labs/ai-diffusion-dpg#53](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/53)  
**Branch:** GH-53-telephony

---

## Overview

Integrate a telephonic channel into the DPG framework as a separate `telephony_adapter/` service. The adapter handles inbound PSTN/SIP calls and outbound campaign calls via the **Vobiz** telephony platform, using **Pipecat** for audio pipeline management, **Raya/Bakbak** for STT and TTS, and **Agent Core's HTTP API** as the sole orchestrator for each turn.

The telephony adapter is a Reach Layer channel — it normalises audio ↔ text at the channel boundary and calls Agent Core per utterance. Agent Core runs the full turn sequence (Trust Layer, NLU, LLM, Memory, Observability) unchanged.

---

## Service Structure

```
telephony_adapter/
├── src/
│   ├── __init__.py
│   ├── base.py                  # TelephonyAdapterBase ABC + dataclasses
│   ├── vobiz_serializer.py      # VobizFrameSerializer (Pipecat FrameSerializer)
│   ├── raya_stt_service.py      # RayaSTTService (Pipecat STT, Raya WebSocket)
│   ├── raya_tts_service.py      # RayaTTSService (Pipecat TTS, Raya SSE streaming)
│   ├── agent_core_service.py    # AgentCoreLLMService (Pipecat LLM node → Agent Core HTTP)
│   ├── telephony_adapter.py     # VobizTelephonyAdapter — wires Pipecat pipeline per call
│   └── campaign_manager.py      # CampaignManager — triggers outbound Vobiz calls
├── server.py                    # FastAPI: /answer, /ws/{call_sid}, /campaign, /recording-finished, /recording-ready
├── config/
│   └── telephony.yaml           # Default config (overridden by domain config)
├── tests/
│   ├── test_vobiz_serializer.py
│   ├── test_raya_stt_service.py
│   ├── test_raya_tts_service.py
│   ├── test_agent_core_service.py
│   ├── test_campaign_manager.py
│   └── test_telephony_adapter.py
├── pyproject.toml
└── Dockerfile
```

**Port:** `8006` (8005 is taken by Reach Layer web adapter; all ports configurable).

---

## Architecture

### Base Class

`base.py` defines `TelephonyAdapterBase` (ABC) before any concrete implementation, per codebase rules. It declares the interface that `VobizTelephonyAdapter` implements. Dataclasses `TelephonyTurnInput` and `TelephonyTurnResult` carry call metadata (call SID, caller ID) alongside text.

### Pipecat Pipeline (per call)

```
VobizTransport.input()
  → RayaSTTService          # wss://hub.getraya.app/transcribe
  → AgentCoreLLMService     # POST http://agent_core:8000/process_turn
  → RayaTTSService          # POST https://hub.getraya.app/v1/text-to-speech/stream
  → VobizTransport.output()
```

Each component is a Pipecat pipeline node. One pipeline instance runs per active call, managed by `VobizTelephonyAdapter`.

### VobizFrameSerializer

Handles the Vobiz-specific WebSocket envelope (similar to `TwilioFrameSerializer` in Pipecat). Encodes/decodes audio frames to/from the Vobiz WebSocket binary/JSON format. Call metadata (call SID, caller ID) is extracted from the initial Vobiz handshake message.

### RayaSTTService

- Maintains a persistent WebSocket connection to `wss://hub.getraya.app/transcribe` per call.
- Receives `AudioRawFrame` objects from the Vobiz transport, accumulates into utterance buffers.
- On utterance end (VAD silence or end-of-speech signal), sends `{"audio_base64": "<base64 WAV>", "language": "<lang>"}` over the Raya WS.
- Receives `{"transcript": "...", "status": "success"}` and emits a Pipecat `TranscriptionFrame`.
- Auth: `X-API-Key` header sent during WebSocket connect.

### RayaTTSService

- Receives `LLMFullResponseEndFrame` (text) from `AgentCoreLLMService`.
- Calls `POST https://hub.getraya.app/v1/text-to-speech/stream` (SSE).
- SSE stream delivers `event: chunk` with base64 PCM F32LE audio data.
- Emits `AudioRawFrame` objects to `VobizTransport.output()` as chunks arrive.
- Auth: `X-API-Key` header.

### AgentCoreLLMService

A custom Pipecat LLM service node. Instead of calling an LLM directly:

1. Receives `TranscriptionFrame` from `RayaSTTService`.
2. Constructs request: `{session_id, user_message: transcript, channel: "telephony", user_id: caller_id}`.
3. `POST http://agent_core:8000/process_turn` with `timeout=5s`.
4. Parses `TurnResult`: emits `LLMFullResponseEndFrame(text=response_text)`.
5. If `was_escalated=true`: emits an escalation frame → pipeline triggers HiTL path (call transfer + hangup).

Session ID is generated at call start (UUID) and reused for all turns in the call. Caller ID is passed as `user_id`.

### CampaignManager

Triggers outbound calls via the Vobiz REST API:

```
POST https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/
{
  "from": "<vobiz_number>",
  "to": "<destination>",
  "answer_url": "<PUBLIC_URL>/answer",
  "answer_method": "POST"
}
```

Auth: `X-Auth-ID` + `X-Auth-Token` headers.

Exposed via `POST /campaign` on the telephony service. The Action Gateway can call this endpoint as a `telephony_channel_switch` connector tool when the LLM decides to switch channels mid-session.

---

## Data Flow

### Inbound Call

1. Vobiz receives PSTN call → sends `POST /answer` to telephony adapter.
2. Adapter returns XML: `<Response><Stream url="wss://<PUBLIC_URL>/ws/{call_sid}"/></Response>`.
3. Vobiz opens WebSocket to `/ws/{call_sid}`.
4. `VobizTelephonyAdapter` spawns a new Pipecat pipeline for this call SID.
5. `VobizFrameSerializer` extracts `call_sid`, `caller_id` from handshake.
6. Pipeline runs for the duration of the call; each utterance → Agent Core turn → TTS audio response.
7. On call end (Vobiz hangs up or escalation): pipeline teardown, session cleanup logged async.

### Outbound Call

1. Client calls `POST /campaign` with `{to, from, metadata}`.
2. `CampaignManager` calls Vobiz REST API → Vobiz dials the number.
3. When callee answers: same inbound flow from step 1 above (Vobiz calls `/answer`).

### Agent Core Integration

- Session ID: UUID generated at call start, stable for the call's lifetime.
- `user_id`: caller phone number (passed to Agent Core; not logged directly per PII rules).
- `channel`: always `"telephony"`.
- Agent Core is unaware it's talking to a telephony channel — same HTTP API as web/CLI channels.

---

## Configuration

`config/telephony.yaml` (defaults; overridden by domain config):

```yaml
telephony_adapter:
  port: 8006
  public_url: ${PUBLIC_URL}
  vobiz:
    auth_id: ${VOBIZ_AUTH_ID}
    auth_token: ${VOBIZ_AUTH_TOKEN}
    api_base: https://api.vobiz.ai/api/v1
    from_number: ${VOBIZ_FROM_NUMBER}
  raya:
    api_key: ${RAYA_API_KEY}
    stt_wss_url: wss://hub.getraya.app/transcribe
    tts_base_url: https://hub.getraya.app/v1
    language: hi
    voice_id: voice_001
    tts_speed: 1.0
  agent_core:
    base_url: http://agent_core:8000
    timeout_ms: 5000
    fallback_phrase: "I'm sorry, I couldn't process that. Please try again."
```

No credentials or URLs hardcoded in Python source.

---

## Error Handling

| Failure | Behaviour |
|---|---|
| Raya STT WS disconnect | Retry once with exponential backoff; on second failure log `status: failure`, emit silence frame |
| Raya TTS SSE failure | Log error, return empty audio frame; call continues (response text not lost) |
| Agent Core HTTP timeout (5 s) | Return configurable `fallback_phrase` as TTS audio, log structured error |
| Agent Core escalation response | Emit escalation frame → pipeline triggers HiTL (call transfer + hangup) |
| Vobiz WebSocket drop | Pipecat pipeline teardown; async session cleanup logged to Observability Layer |
| Vobiz 429 on outbound call | Exponential backoff retry (up to 3 attempts) in `CampaignManager` |

All errors use structured logging with `operation`, `status`, `error`, `latency_ms`. No PII (phone numbers) in logs outside the designated audit path.

---

## Server Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/answer` | POST | Called by Vobiz when call answered; returns XML with WebSocket URL |
| `/ws/{call_sid}` | WebSocket | Bidirectional audio stream per call |
| `/campaign` | POST | Trigger outbound call (direct or via Action Gateway) |
| `/recording-finished` | POST | Vobiz webhook: call recording stopped |
| `/recording-ready` | POST | Vobiz webhook: recording MP3 ready |

---

## Testing Plan

| File | What it covers |
|---|---|
| `test_vobiz_serializer.py` | Encode/decode Vobiz WebSocket envelope; handshake metadata extraction |
| `test_raya_stt_service.py` | Mock Raya WS; `TranscriptionFrame` on valid response; error frame on WS failure; retry logic |
| `test_raya_tts_service.py` | Mock SSE stream; audio chunks forwarded correctly; empty frame on failure |
| `test_agent_core_service.py` | Mock Agent Core HTTP; correct `TurnInput` construction; escalation flag handling; timeout fallback |
| `test_campaign_manager.py` | Mock Vobiz REST API; correct outbound payload; retry on 429 |
| `test_telephony_adapter.py` | Full integration with mocked pipeline components; inbound call flow; turn exchange; call-end cleanup |

---

## Module Interaction Rules

This service is a Reach Layer channel adapter. It:

- **Calls Agent Core** (`POST /process_turn`) per utterance — same rule as all other Reach Layer adapters.
- **Does not call** Memory Layer, Trust Layer, Knowledge Engine, or Observability Layer directly.
- **Exposes** `POST /campaign` for Action Gateway to call as a connector tool.

The approved exception pattern (Reach Layer → Memory Layer for session restore, per PR #29) does **not** apply here. The telephony adapter is stateless between calls; session state lives in Memory Layer via Agent Core.

---

## Observability

The telephony adapter instruments itself using the shared `dpg_telemetry` package (from `observability_layer/`), exactly as all other DPG blocks do.

**Initialisation** (`server.py` startup): `init_otel(service_name="telephony_adapter", config=config)`.

**Spans emitted:**

| Span | When | Key attributes |
|---|---|---|
| `telephony.inbound_call` | Call answered | `call_sid`, `channel=telephony` |
| `telephony.turn` | Per-utterance turn (wraps STT → Agent Core → TTS) | `session_id`, `call_sid`, `latency_ms` |
| `telephony.stt` | Raya STT call | `status`, `latency_ms`, `language` |
| `telephony.agent_core_call` | POST /process_turn | `status`, `latency_ms`, `was_escalated` |
| `telephony.tts` | Raya TTS call | `status`, `latency_ms`, `voice_id` |
| `telephony.outbound_call` | Campaign call triggered | `to_number` (no PII), `status` |

**Metrics emitted:**

| Metric | Type | Description |
|---|---|---|
| `telephony.active_calls` | Gauge | Concurrent live calls |
| `telephony.turn.latency_ms` | Histogram | End-to-end per-turn latency |
| `telephony.stt.latency_ms` | Histogram | Raya STT round-trip |
| `telephony.tts.latency_ms` | Histogram | Raya TTS streaming start latency |
| `telephony.agent_core.latency_ms` | Histogram | Agent Core HTTP call latency |
| `telephony.campaign.calls_initiated` | Counter | Outbound calls triggered |

**PII rules:** Phone numbers (`caller_id`, `to_number`) are excluded from all spans and metrics. Only `session_id` and `call_sid` (opaque identifiers) appear in telemetry. This matches the `observability.telemetry.pii_fields_excluded` contract in ARCHITECTURE.md.

Telemetry flows via OTLP/gRPC to the OTel Collector sidecar (same as all other blocks). No direct calls to the Observability Layer HTTP API — instrumentation only.

---

## Dependencies

New Python dependencies for `telephony_adapter/`:

- `pipecat-ai` — voice agent pipeline framework
- `fastapi` + `uvicorn` — HTTP/WebSocket server (same as other modules)
- `websockets` — Raya STT WebSocket client
- `httpx` — Raya TTS HTTP client + Agent Core HTTP client
- `pyyaml` — config loading

---

## Stub Interface

For local testing without live Vobiz/Raya credentials, a `TelephonyAdapterStub` in `base.py` returns pre-recorded responses. It honours the same `TelephonyAdapterBase` interface so the test suite can run without network access.

---

## Acceptance Criteria (from GH-53)

- [ ] Inbound PSTN call triggers new Agent Core session with `channel: telephony`
- [ ] Each utterance (post-STT) submitted as a turn; response converted to audio and played back
- [ ] Call metadata (caller ID, call SID) passed as session context without PII in logs
- [ ] Trust Layer input/output checks run on every telephony turn (via Agent Core)
- [ ] Call handoff/escalation triggers HiTL block (via Agent Core escalation response)
- [ ] Outbound campaign calls initiated via `POST /campaign`
- [ ] Action Gateway can trigger `POST /campaign` as a connector tool
- [ ] Stub honours the same interface for local testing
- [ ] Unit tests cover inbound flow, turn exchange, STT/TTS boundary, call-end cleanup
