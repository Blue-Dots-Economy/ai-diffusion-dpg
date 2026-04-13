# Gap Analysis: VoicERA Mono Repository as Telephony Adapter for AI-Diffusion-DPG

**Prepared for:** VoicERA Maintainers  
**Date:** 2026-04-13  
**Author:** Sanketika Labs  
**Context:** Evaluation of `voicera_mono_repository` as the telephony/VOIP adapter for the `reach_layer` block in the AI-Diffusion DPG framework.

---

## Executive Summary

We evaluated the `voicera_mono_repository` — specifically `voice_2_voice_server`, `ai4bharat_stt_server`, and `ai4bharat_tts_server` — as a drop-in telephony adapter for the `reach_layer` building block in our AI-Diffusion DPG framework.

**The short answer is: VoicERA cannot be used as-is.** The incompatibilities are architectural, not cosmetic. VoicERA is a self-contained voice AI platform with an embedded LLM orchestration pipeline (STT → LLM → TTS inside the same process). Our Reach Layer is a thin channel normalisation boundary that delegates all AI logic downstream to a separate Agent Core service. These two architectural contracts are mutually exclusive.

This document details each specific incompatibility, the code locations that surface it, and what would need to change on either side to make integration viable.

---

## Background: The DPG Reach Layer Contract

The Reach Layer in AI-Diffusion-DPG is defined by a strict, minimal interface:

```python
# reach_layer/src/base.py
@dataclass
class TurnInput:
    session_id: str
    user_message: str      # plain text — always
    channel: str
    timestamp_ms: int
    user_id: Optional[str] = None

@dataclass
class TurnResult:
    session_id: str
    response_text: str     # plain text — always
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0

class ReachLayerBase(ABC):
    @abstractmethod
    def receive(self) -> TurnInput: ...

    @abstractmethod
    def deliver(self, result: TurnResult) -> None: ...
```

Every adapter — CLI, Web, or VOIP — must:
1. Translate inbound channel input into a `TurnInput` (text).
2. Call `POST /process_turn` on Agent Core with that text.
3. Translate the text `TurnResult` back into channel-specific output.

The adapter is **not** permitted to call any other DPG block (Knowledge Engine, Trust Layer, etc.) or to embed AI logic (STT, LLM, TTS). All intelligence lives in Agent Core.

---

## Gap 1 — Fundamentally Different Communication Models

| Dimension | AI-Diffusion-DPG Reach Layer | VoicERA Voice Server |
|---|---|---|
| Protocol | HTTP request-response | WebSocket bidirectional stream |
| Payload type | Plain text (`TurnInput` / `TurnResult`) | Raw PCM audio frames |
| Turn model | Synchronous: one request per turn | Continuous: real-time audio pipeline |
| State between turns | Stateless (Agent Core holds it) | In-process coroutine (Pipecat `PipelineRunner`) |

**Where this surfaces in VoicERA:**

`voice_2_voice_server/api/server.py:54–78` — The WebSocket endpoint accepts a raw audio stream from the Vobiz telephony platform. There is no HTTP request-response boundary; audio arrives continuously until the call ends.

`voice_2_voice_server/api/bot.py:203–215` — The Pipecat pipeline processes audio in real time, chaining transport input → STT → LLM → TTS → transport output as a single in-process pipeline.

**Why it blocks integration:**  
A VOIP adapter for the DPG must break the audio stream into discrete turns (utterance detection), produce a text `TurnInput`, and wait for a text `TurnResult` before generating audio output. None of this boundary logic exists in VoicERA. The VoicERA voice server has no concept of a "turn boundary" — it streams continuously.

---

## Gap 2 — VoicERA Owns the LLM; DPG Forbids This

This is the most fundamental architectural conflict.

**VoicERA's pipeline (`voice_2_voice_server/api/bot.py:178–185`):**

```python
llm = create_llm_service(
    llm_config,
    vistaar_session_id=vistaar_session_id,
    language=agent_config.get("language"),
    org_id=org_id,
)
# Pipeline: ... → stt → context_aggregator.user() → llm → tts → ...
```

The voice server instantiates and calls the LLM directly. The LLM (OpenAI, Anthropic, etc.) is treated as a pipeline component within the Pipecat process.

**DPG's constraint (from CLAUDE.md and `reach_layer/server.py:163–178`):**

```python
# The ONLY thing the Reach Layer may do with AI is call this endpoint:
payload = {
    "session_id": turn.session_id,
    "user_message": turn.user_message,
    "channel": turn.channel,
}
response = ac_client.post(ac_endpoint, json=payload, timeout=ac_timeout)
```

The Reach Layer is architecturally prohibited from calling the LLM. All LLM calls must go through `agent_core/src/llm_wrapper/claude_wrapper.py`. There is no pathway through which VoicERA's embedded LLM call could be rerouted to comply with this constraint without eliminating the Pipecat pipeline entirely.

**Consequence:** Using VoicERA's voice server as a Reach Layer adapter would mean the LLM is called twice per turn (once inside VoicERA, once in Agent Core) or the Agent Core LLM is bypassed entirely — both are unacceptable.

---

## Gap 3 — Agent Configuration Schema Conflict

**VoicERA agent config** (fetched by `voice_2_voice_server/api/bot.py:154–175` from the backend API at `GET /api/v1/agents/config/{agent_type}`):

```python
{
    "system_prompt": "...",
    "greeting_message": "...",
    "session_timeout_minutes": 10,
    "llm_model": {
        "name": "openai",
        "args": { "model": "gpt-4o", "temperature": 0.7 }
    },
    "stt_model": { "name": "deepgram", "language": "English", "args": {...} },
    "tts_model": { "name": "cartesia", "language": "English", "args": {...} },
    "knowledge_base_enabled": True,
    "knowledge_document_ids": [...],
    ...
}
```

This config embeds the identity and parameters of the LLM, STT, and TTS services. In DPG, those choices belong to Agent Core (LLM) and are driven by the domain configuration kit YAML, not by the Reach Layer. The Reach Layer's config is intentionally minimal (`dev-kit/configs/kkb/reach_layer.yaml`):

```yaml
agent_core_client:
  endpoint: http://localhost:8000/process_turn
  timeout_s: 30.0
```

A VOIP adapter in DPG would need a place to express STT and TTS provider selection. Currently there is no configuration slot for this in the domain config kit.

---

## Gap 4 — Session State Management Conflict

**VoicERA accumulates state in-process** (`voice_2_voice_server/api/bot.py:327–342`):

```python
call_data = {
    "audio_chunks": [],
    "audio_sample_rate": None,
    "audio_num_channels": None,
    "transcript_lines": []
}
```

This local variable is built up over the lifetime of a call inside the `bot()` coroutine. After the call ends, it is flushed to MinIO and the backend API (`call_recording_utils.py:14–86`).

**DPG's state model:** All session state lives in the Memory Layer, accessed exclusively through Agent Core. The Reach Layer is stateless between turns; it holds no per-session data. An in-process audio buffer would violate this constraint and introduce horizontal scaling problems (the DPG requires that any Reach Layer instance can handle any session).

---

## Gap 5 — Audio Sample Rate Mismatches

| Component | Sample Rate | Format |
|---|---|---|
| Vobiz telephony input | 8000 Hz | µ-law PCM |
| AI4Bharat STT input (`ai4bharat_stt_server/server.py`) | 16000 Hz | int16 PCM |
| VoicERA resampling (inside STT wrapper) | 8000 → 16000 Hz | handled internally |
| AI4Bharat TTS output (`ai4bharat_tts_server/server.py`) | **44100 Hz** | float32 PCM |
| Vobiz telephony output expected | **8000 Hz** | µ-law PCM |

The TTS output is hardcoded at 44100 Hz in `ai4bharat_tts_server/server.py`. The voice server must downsample before returning audio to the caller. This logic lives inside the Pipecat pipeline and is not exposed as a standalone resampling utility. A DPG VOIP adapter would need to replicate or extract this resampling, and the DPG domain config kit has no mechanism to express sample rate preferences.

---

## Gap 6 — Latency Model Incompatibility

**DPG's current latency model** (`reach_layer/server.py:87–93`):

```python
ac_timeout = float(ac_cfg.get("timeout_s", 30.0))
```

The Reach Layer tolerates up to 30 seconds for a complete Agent Core response. This is workable for text/web channels. For telephony, ITU-T G.114 recommends ≤150ms one-way delay for acceptable voice quality. Even a relaxed voice UX target would be <3 seconds total latency.

**VoicERA's approach** (`voice_2_voice_server/api/bot.py:44–61`):

```python
ws_params = FastAPIWebsocketParams(
    audio_in_sample_rate=8000,
    audio_out_sample_rate=8000,
    add_wav_header=False,
    vad_enabled=True,
    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.7)),
    vad_audio_passthrough=True,
)
```

VoicERA achieves low latency by keeping STT, LLM, and TTS in-process and streaming audio output as soon as the first TTS chunk is available. It streams 10ms audio chunks (`bot.py:315`). This architecture is incompatible with a synchronous HTTP call to Agent Core, which imposes an irreducible round-trip cost before any audio can be emitted.

The DPG HTTP request-response model makes sub-second telephony latency structurally impossible without redesigning the Reach Layer ↔ Agent Core interface.

---

## Gap 7 — Recording and Transcript Storage

VoicERA records calls to MinIO and submits metadata to its backend API after call completion:

```python
# call_recording_utils.py:14–86
await storage.save_recording_from_chunks(call_sid, audio_chunks, ...)
await storage.save_transcript_from_lines(call_sid, transcript_lines)
requests.post(f"{backend_url}/api/v1/call-recordings", json=payload)
```

The DPG framework has no equivalent recording facility. Recording and audit trail responsibility belongs to the Observability Layer, which emits events asynchronously after turn delivery — it does not handle binary audio storage. There is no MinIO integration or binary-storage abstraction anywhere in the DPG codebase.

If call recording is a requirement for the VoicERA use case, a DPG VOIP adapter would need either (a) a new storage component outside the defined block responsibilities, or (b) VoicERA's recording utilities to be retained as a sidecar to the adapter, separate from the DPG block structure.

---

## Gap 8 — Authentication and Tenant Isolation

**VoicERA's auth model:**
- Frontend ↔ Backend: JWT
- Voice Server → Backend: `INTERNAL_API_KEY` header (simple shared secret)
- Agent configs are keyed by `agent_type` and scoped to `org_id`

**DPG's trust model:**
- Reach Layer → Agent Core: no authentication (internal network assumed)
- Tenant isolation via `session_id` and `user_id` parameters
- No `org_id` concept visible in Reach Layer or Agent Core interfaces

A VOIP adapter would receive inbound calls from a telephony platform (Vobiz). Mapping a phone number or SIP identity to an `org_id` and then to a DPG `session_id` requires a routing layer that exists in VoicERA's backend (`GET /api/v1/agents/by-phone/{phone_number}`) but not in DPG.

---

## Summary of Incompatibilities

| # | Area | VoicERA | DPG Reach Layer | Severity |
|---|---|---|---|---|
| 1 | Communication model | WebSocket audio stream | HTTP request-response | **Critical** |
| 2 | LLM ownership | Embedded in voice server | Agent Core only | **Critical** |
| 3 | Agent config schema | Includes LLM/STT/TTS config | Channel metadata only | **Critical** |
| 4 | Session state | In-process coroutine | Agent Core / Memory Layer | **Critical** |
| 5 | Audio sample rates | 8 kHz in, 44.1 kHz out (hardcoded) | No audio handling | **Major** |
| 6 | Latency model | <100ms streaming | 30s HTTP timeout | **Major** |
| 7 | Recording / storage | MinIO + backend API | Observability Layer only | **Major** |
| 8 | Auth / tenant routing | org_id, API keys, phone lookup | session_id only | **Moderate** |

---

## What Would Need to Change

### Changes Required in VoicERA

For VoicERA components to be usable as building blocks (rather than a monolith), the following separations are needed:

**1. Extract STT as a standalone HTTP service**  
The `ai4bharat_stt_server` already exists as a standalone REST server. However, `IndicConformerRESTSTTService` and `IndicParlerRESTTTSService` inside `voice_2_voice_server/api/services/ai4bharat/` wrap this with Pipecat-specific frame types. A simple, Pipecat-free HTTP client that accepts `bytes` and returns `str` would be directly reusable.

**2. Extract TTS as a standalone HTTP service**  
`ai4bharat_tts_server` is already standalone. The same applies: a thin client that accepts `str` and returns an audio stream at a configurable sample rate (not hardcoded 44.1 kHz) would be immediately useful.

**3. Decouple the LLM from the voice pipeline**  
Provide a configuration option to replace the embedded LLM call with an HTTP call to an external agent (e.g., `POST /process_turn`). This would allow VoicERA's audio I/O pipeline to remain intact while delegating AI logic to Agent Core.

**4. Expose a turn-boundary callback**  
After VAD detects end-of-speech and STT produces text, emit a callback (HTTP or queue message) with the transcript. Receive a response text in return and feed it to TTS. This makes the LLM swappable without restructuring the Pipecat pipeline.

**5. Make TTS sample rate configurable**  
The 44.1 kHz hardcoding in `ai4bharat_tts_server` should become a query parameter or config value. For telephony, 8 kHz or 16 kHz output is required.

### Changes Required in AI-Diffusion-DPG

**1. Add a VOIP adapter class in `reach_layer/src/voip_reach.py`**  
Implements `ReachLayerBase`, accepts WebSocket audio, runs VAD, calls STT, calls `POST /process_turn` on Agent Core, calls TTS, streams audio back.

**2. Extend the domain config kit with STT/TTS provider config**  
Add a `reach_layer.voip` section to the domain YAML for STT endpoint, TTS endpoint, language, sample rate, and VAD parameters.

**3. Add streaming response support to Agent Core (optional but recommended)**  
For sub-second first-word latency, Agent Core would need to support streaming (`text/event-stream`) so the VOIP adapter can begin TTS before the full response is available. This is a significant change to Agent Core's interface.

**4. Add phone number → session_id routing**  
A small routing table (in config or Memory Layer) mapping inbound caller IDs to known users or creating new sessions for unknown callers.

---

## Recommended Path Forward

Given the architectural gaps, we recommend the following approach rather than attempting to adapt the existing VoicERA voice server:

1. **Reuse VoicERA's AI4Bharat STT and TTS servers as-is.** They are already standalone HTTP services with clean REST APIs.

2. **Build a new `VoipReachLayer` adapter** in the DPG `reach_layer/src/` that:
   - Accepts telephony WebSocket connections
   - Runs Silero VAD (reuse VoicERA's VAD configuration)
   - POSTs transcribed text to Agent Core's `/process_turn`
   - Streams TTS audio back to the caller

3. **Do not use `voice_2_voice_server`** as a library — its Pipecat coupling, embedded LLM, and in-process state make it unsuitable for reuse as a component. The AI4Bharat STT/TTS services are the reusable parts.

4. **Collaborate with VoicERA maintainers** on the decoupling changes listed above if VoicERA needs to support multi-framework integration in the future.

---

*For questions or discussion, please open an issue against `sanketika-labs/ai-diffusion-dpg`.*
