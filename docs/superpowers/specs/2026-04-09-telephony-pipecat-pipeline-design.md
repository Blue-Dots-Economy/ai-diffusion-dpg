# Telephony Adapter — Pipecat Pipeline Refactor

**Date:** 2026-04-09  
**Status:** Approved  
**Scope:** `telephony_adapter/` module only

---

## Problem

Calls connect to the telephony adapter WebSocket but audio is never passed to Agent Core. Root causes:

1. **Track name mismatch** — custom `parse_media` filters on `track == "inbound"` but Vobiz may omit or vary this field, so `audio_buffer` stays empty.
2. **Outbound echo** — Vobiz `bidirectional=true` echoes TTS audio back; the custom loop may re-process it.
3. **TTS audio format** — `RayaTTSService` returns PCM F32LE but `build_media_message` encodes it as mulaw without conversion.
4. **STT silent failure** — if Raya STT fails, the exception is swallowed and Agent Core is never called; there is no structured log entry.
5. **Docker URL** — `agent_core.base_url: http://agent_core:8000` only resolves inside Docker; no startup validation catches a misconfigured or unreachable URL.

---

## Solution: Pipecat Frame-Based Pipeline

Replace the custom silence-timer loop with a Pipecat pipeline. Pipecat handles the Vobiz wire protocol (via `pipecat-vobiz`), VAD-based turn segmentation (Silero), and frame routing. Custom service classes wrap Raya STT, Agent Core, and Raya TTS.

---

## Architecture

### Pipeline (per call)

```
FastAPIWebsocketTransport
  params: SileroVADAnalyzer, audio_in_sample_rate=8000, audio_out_sample_rate=8000
  serializer: VobizFrameSerializer (pipecat-vobiz)
      │  decodes inbound mulaw → PCM16
      ▼
RayaSTTService            (custom BaseSTTService)
      │  HTTP POST multipart/form-data WAV → TranscriptionFrame
      ▼
AgentCoreLLMProcessor     (custom FrameProcessor)
      │  HTTP POST /process_turn → TextFrame
      ▼
RayaTTSService            (custom BaseTTSService)
      │  SSE stream PCM F32LE → PCM16 → AudioRawFrame chunks
      ▼
FastAPIWebsocketTransport output
      │  encodes PCM16 → mulaw, sends playAudio JSON
      ▼
Vobiz WebSocket
```

### Frame types

| Frame | Produced by | Consumed by |
|-------|------------|-------------|
| `AudioRawFrame` | Transport input | `RayaSTTService` |
| `TranscriptionFrame` | `RayaSTTService` | `AgentCoreLLMProcessor` |
| `TextFrame` | `AgentCoreLLMProcessor` | `RayaTTSService` |
| `AudioRawFrame` | `RayaTTSService` | Transport output |
| `EndFrame` | `AgentCoreLLMProcessor` (on escalation) | Pipeline teardown |

---

## File Changes

### New files

```
telephony_adapter/
  src/
    pipecat_services/
      __init__.py
      raya_stt.py          # RayaSTTService
      agent_core_llm.py    # AgentCoreLLMProcessor
      raya_tts.py          # RayaTTSService
  src/bot.py               # per-call pipeline factory
```

### Modified files

- `server.py` — WebSocket handler calls `bot()` instead of `_adapter.handle_call()`; does not pre-accept or pre-read the socket (Pipecat owns the handshake)
- `pyproject.toml` — adds `pipecat-ai[websocket,silero]` and `pipecat-vobiz` dependencies

### Deleted files

| File | Replaced by |
|------|-------------|
| `src/telephony_adapter.py` | `src/bot.py` + Pipecat pipeline |
| `src/raya_stt_service.py` | `src/pipecat_services/raya_stt.py` |
| `src/raya_tts_service.py` | `src/pipecat_services/raya_tts.py` |
| `src/agent_core_service.py` | `src/pipecat_services/agent_core_llm.py` |
| `src/vobiz_serializer.py` | `pipecat-vobiz` `VobizFrameSerializer` |

### Kept unchanged

- `src/base.py`
- `src/campaign_manager.py`
- `config_loader.py`
- `config/telephony.yaml`

---

## Service Implementations

### RayaSTTService

- Extends `BaseSTTService`.
- Pipecat calls `run_stt(audio: bytes)` once per utterance with the full buffered PCM16 audio (Silero VAD delimits the utterance — no manual silence timer).
- Wraps `audio` bytes in a WAV header (PCM16, 8000 Hz, mono).
- POSTs as `multipart/form-data` to `POST https://hub.getraya.app/transcribe` with `X-API-Key` header and `language` field.
- Yields `TranscriptionFrame(text=transcript)` on success.
- On HTTP error or empty transcript: logs structured entry, yields nothing (pipeline waits for next utterance).
- Retry: one retry with 500ms backoff on connection error.

### AgentCoreLLMProcessor

- Extends `FrameProcessor`.
- Receives `session_id` and `call_sid` at construction (set once per call in `bot.py`).
- On `TranscriptionFrame`: POSTs to `{base_url}/process_turn` with `session_id`, `user_message`, `channel="telephony"`, `user_id=call_sid`.
- On HTTP 200: pushes `TextFrame(text=response_text)` downstream.
- On `was_escalated=True`: pushes `TextFrame` then `EndFrame` to close the pipeline gracefully after TTS finishes.
- On HTTP error or timeout: pushes `TextFrame(text=fallback_phrase)` — call continues.
- Startup validation: raises `ValueError` if `base_url` is empty; logs a warning if Agent Core is unreachable at startup (does not block startup).
- All other frame types passed through unchanged.

### RayaTTSService

- Extends `BaseTTSService`.
- Pipecat calls `run_tts(text: str)`.
- POSTs to `POST https://hub.getraya.app/v1/text-to-speech/stream` with `voice_id`, `language`, `speed`, and `sample_rate=8000`.
- Reads SSE response line by line; on `event: chunk` decodes base64 PCM F32LE bytes, converts to PCM16 (numpy `float32 * 32767 → int16`), yields `AudioRawFrame(audio=pcm16_bytes, sample_rate=8000, num_channels=1)`.
- On `event: done`: returns.
- Structured log on completion with total `audio_bytes` and `latency_ms`.

### bot.py (pipeline factory)

```python
async def run_bot(websocket, call_sid, config):
    stt = RayaSTTService(config)
    agent = AgentCoreLLMProcessor(config, call_sid=call_sid)
    tts = RayaTTSService(config)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=VobizFrameSerializer(...),
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        agent,
        tts,
        transport.output(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=8000,
        audio_out_sample_rate=8000,
    ))

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        greeting = config["telephony_adapter"]["agent_core"].get("greeting", "Hello, how can I help you?")
        await task.queue_frame(TextFrame(greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        await task.cancel()

    await PipelineRunner().run(task)
```

### server.py WebSocket handler

```python
@app.websocket("/ws/{call_sid}")
async def websocket_endpoint(websocket: WebSocket, call_sid: str):
    # Do NOT call websocket.accept() — Pipecat transport does this
    await bot.run_bot(websocket, call_sid, _config)
```

---

## Bug Fixes Summary

| # | Bug | Fix |
|---|-----|-----|
| 1 | Track name mismatch | `pipecat-vobiz` `VobizFrameSerializer` handles Vobiz wire protocol; we stop parsing it manually |
| 2 | Outbound echo | Pipecat transport only feeds inbound audio into the pipeline |
| 3 | TTS format (PCM F32LE sent as mulaw) | `RayaTTSService` converts F32LE→PCM16; transport serializer encodes PCM16→mulaw |
| 4 | STT silent failure | `BaseSTTService` surfaces errors as `ErrorFrame`; empty transcripts logged at `INFO` `status=skipped` |
| 5 | Docker URL not resolving | `AgentCoreLLMProcessor.__init__` raises `ValueError` on empty `base_url`; startup logs a warning if Agent Core health check fails |

---

## Logging

Every service emits a structured log entry per the `logging-observability.md` rule (`operation`, `status`, `latency_ms`, `error`):

```
[raya_stt]         operation=raya_stt.transcribe   status=success  latency_ms=340  audio_bytes=14400  transcript_len=23
[agent_core_llm]   operation=agent_core.process_turn  status=success  latency_ms=610  was_escalated=false
[raya_tts]         operation=raya_tts.synthesize    status=success  latency_ms=210  audio_bytes=12800
```

Empty transcript: `status=skipped`, no downstream call.  
HTTP failure: `status=failure`, `error=HTTP 502`, fallback phrase used.

---

## Dependencies Added

```toml
"pipecat-ai[websocket,silero]>=0.0.86",
"pipecat-vobiz>=0.0.2",
"numpy>=1.26",   # F32LE → PCM16 conversion in RayaTTSService
```

---

## Out of Scope

- Tests for existing deleted files are also deleted; new tests for `pipecat_services/` are written to match.
- `CampaignManager` and outbound call flow are unchanged.
- No changes to other DPG modules.
