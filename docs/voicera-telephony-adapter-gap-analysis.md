# Gap Analysis: VoicERA Mono Repository vs AI-Diffusion-DPG Voice Channel

**Prepared for:** VoicERA Maintainers
**Date:** 2026-05-07
**Author:** Sanketika Labs
**Context:** Evaluation of `voicera_mono_repository` against the AI-Diffusion-DPG voice channel (`reach_layer/voice/`), identifying which parts of VoicERA can be reused and what needs to change on the VoicERA side for that reuse to be safe across DPG deployments.

---

## 1. Executive summary

The AI-Diffusion-DPG voice channel is a complete, first-class building block of the framework. It owns the caller WebSocket, runs the full Pipecat audio pipeline in-process, and holds a persistent streaming connection to Agent Core for AI logic. Every audio component (operator, VAD, STT, TTS) sits behind a DPG base class, so alternative providers can be wired in via config without touching the pipeline.

VoicERA's `voice_2_voice_server` covers the same problem space but with a fundamentally different architecture: the LLM, knowledge base, recording, and tenant routing are all embedded inside the voice service. As a result:

- **`voice_2_voice_server` cannot be adopted as a whole into the DPG.** Doing so would invert the framework's architecture by moving AI logic out of Agent Core into the voice service.
- **VoicERA's AI4Bharat STT and TTS servers (`ai4bharat_stt_server`, `ai4bharat_tts_server`) are reusable as-is once they meet a small set of API-contract requirements.** They slot directly into the DPG voice channel's `STTServiceBase` and `TTSServiceBase` extension points.

This document describes the DPG voice channel's architecture (Section 2), states the architectural conflicts that prevent whole-service reuse of `voice_2_voice_server` (Section 3), gives the exact base-class contracts a pluggable STT/TTS provider must satisfy (Section 4), and closes with a prioritised list of changes the VoicERA team can make to unblock AI4Bharat plug-in (Section 5).

---

## 2. The AI-Diffusion-DPG voice channel

The voice channel lives at `reach_layer/voice/` and runs as its own service on port 8006. It is not a wrapper around Agent Core — it is the full inbound and outbound voice path.

- **Owns the caller WebSocket** (Vobiz μ-law 8 kHz, bidirectional, via `FastAPIWebsocketTransport` + `LoggingVobizFrameSerializer`).
- **Owns the audio pipeline** — Pipecat is *inside* the voice channel, not external.
- **Owns VAD, STT, TTS, sanitiser, observer** — all configurable, all behind DPG base classes.
- **Holds a persistent streaming connection to Agent Core** for the duration of the call.
- **Drives call lifecycle** — opening phrase before the caller speaks, mid-response barge-in, terminal-word + EndFrame teardown on session end.
- **Stateless across calls.** No per-session in-process buffers. All session state lives in Memory Layer; the channel just relays.

### 2.1 Pipeline composition (config-driven, swappable)

```
WebSocket (Vobiz / future operators)
  → FastAPIWebsocketTransport.input()      ← TelephonyOperatorBase
  → VADProcessor                            ← VADAnalyzerBase  (SileroVADWrapper today)
  → UserTurnProcessor                       (VADUserTurnStartStrategy + SpeechTimeoutUserTurnStopStrategy)
  → STT service                             ← STTServiceBase    (RayaSTTService today)
  → VADObserverProcessor                    (passive observability + heartbeat)
  → AgentCoreLLMProcessor                   (HTTP/SSE bridge to Agent Core)
  → TTSTextSanitizerProcessor
  → TTS service                             ← TTSServiceBase    (RayaTTSService today)
  → FastAPIWebsocketTransport.output()      ← TelephonyOperatorBase
```

Every box marked with `← <Base>` is replaceable by another concrete class. The base classes (`reach_layer/voice/src/`):

| Base | Concrete today | Contract |
|---|---|---|
| `TelephonyOperatorBase` (`operators/operator_base.py`) | `VobizOperator` | `parse_handshake(ws) → (stream_id, call_id)`, `create_transport(...)`, `webhook_response_xml(url)` |
| `VADAnalyzerBase` (`vad/vad_base.py`) | `SileroVADWrapper` | `create_analyzer(config) → VADAnalyzer` |
| `STTServiceBase` (`pipecat_services/stt_base.py`) | `RayaSTTService` | `transcribe(audio: bytes) → str \| None` (8 kHz mono PCM16 WAV in, transcript out) |
| `TTSServiceBase` (`pipecat_services/tts_base.py`) | `RayaTTSService` | `synthesize(text: str) → AsyncGenerator[bytes]` (PCM16 chunks at the configured sample rate) |

Adding AI4Bharat (or any other provider) is therefore: write `AI4BharatSTTService(STTServiceBase, SegmentedSTTService)` + `AI4BharatTTSService(TTSServiceBase, TTSService)`, register under a new config key, point `reach_layer.channels.voice.stt_provider` / `tts_provider` at it. No pipeline changes, no Agent Core changes.

### 2.2 Voice channel ↔ Agent Core protocol

The voice channel uses `assembly_mode: session` and talks to Agent Core over a long-lived HTTP/SSE pair:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/sessions/{session_id}/input` | Submit one VAD-segmented utterance. Returns 202; the response arrives on the events stream. |
| `GET` | `/sessions/{session_id}/events?user_id=…&channel=voice` | **Persistent streaming connection** (Server-Sent Events). Yields `signal` / `sentence` / `done` events sentence-by-sentence as Agent Core's LLM produces them. Stays open across turns. |
| `DELETE` | `/sessions/{session_id}/active_turn` | Cancel the in-flight turn (defensive barge-in path). |

The events stream is opened **before the caller speaks** — `VobizAdapter._play_opening_phrase` issues `subscribe_events(session_id, user_id=caller_id)` from `on_client_connected` so Agent Core can emit the entry subagent's `opening_phrase` as a `SentenceEvent`. That sentence is queued as a `TTSSpeakFrame` immediately, so the caller hears the greeting before the first VAD-segmented utterance.

For each subsequent turn, `AgentCoreLLMProcessor`:

1. Cancels and joins the opening-phrase consumer so the per-turn subscribe owns the queue.
2. Posts the transcript via `submit_input(...)`.
3. Iterates `subscribe_events(...)`. Each `SentenceEvent` becomes a `TTSSpeakFrame` immediately — TTS streaming begins while the LLM is still generating later sentences.
4. On `DoneEvent`, ends the turn. If `was_escalated` or `session_ended` is true, pushes `TextFrame(terminal_word)` then `EndFrame`, which makes `LoggingVobizFrameSerializer` issue the Vobiz REST DELETE.

Agent Core supports async streaming responses, and the voice channel consumes them sentence-by-sentence rather than waiting for a synchronous reply. First-audio latency on the wire is sub-second in practice.

### 2.3 Barge-in

Two cooperating layers, both already in the channel:

1. **Pipeline / audio level.** `UserTurnProcessor` between VAD and STT converts `VADUserStartedSpeakingFrame` into a Pipecat `InterruptionFrame` while the bot is speaking. Pipecat flushes the TTS queue. `AgentCoreLLMProcessor._start_interruption()` flips an internal flag so the SSE consumer breaks. A configured `barge_in_acknowledgement` is spoken only when a compound gate (last TTS push within `barge_in_recency_ms` *or* an SSE turn in flight) confirms the bot was actually playing.
2. **Turn-logic level.** When the caller's new utterance is transcribed and submitted, Agent Core's `TurnAssembler.add_segment()` sees the active turn is INVOKED, calls `cancel()` (emits `DoneEvent(turn_status="interrupted")`), and discards the original segments. Only the barge-in speech drives the next turn — the LLM sees the correction, not `"<original> <correction>"`.

### 2.4 Other call-lifecycle features

| Feature | Behaviour | Config key |
|---|---|---|
| Filler phrase | If no `SentenceEvent` reaches TTS within `filler_threshold_ms`, push a single `TTSSpeakFrame(filler_phrase)`. Cancelled on first real sentence. | `reach_layer.channels.voice.filler_threshold_ms`, `filler_phrase` |
| Terminal word + clean hangup | `DoneEvent.session_ended=True` → `TextFrame(terminal_word)` then `EndFrame` → Vobiz REST DELETE via the serializer. | `reach_layer.channels.voice.terminal_word` |
| Pipeline heartbeat | `VADObserverProcessor` + heartbeat task log every `heartbeat_interval_s`, surfacing pipeline stalls. | `reach_layer.channels.voice.observability.heartbeat_interval_s` |
| Caller phone → user_id | `caller_id` (E.164) from the Vobiz `/answer` `From` field is passed through as `user_id` so Memory Layer recognises returning callers. | (no key — automatic) |

### 2.5 Latency and observability targets

Set by the voice UX, enforced as SLOs any pluggable STT/TTS must hit:

| Stage | Budget |
|---|---|
| STT — time-to-first-transcript after VAD stop on an 8 kHz utterance ≤ 8 s | ≤ 500 ms p95 |
| Agent Core — `submit_input` → first `SentenceEvent` | ≤ 800 ms p95 |
| TTS — request accepted → first audio chunk on the wire | ≤ 300 ms p95 |
| End-to-end — caller stops speaking → first audio of the response | ≤ 1.5 s p95 |

All channel components emit structured logs (`operation`, `status`, `latency_ms`, `error`) and an OTel span tree rooted at `reach.inbound`.

---

## 3. Architectural conflicts with `voice_2_voice_server`

These conflicts are why `voice_2_voice_server` cannot be reused as a whole. They are restated here so the requirements section can reference them — they are not blockers for component-level reuse of the AI4Bharat servers.

| # | Conflict | VoicERA today | DPG voice channel |
|---|---|---|---|
| A | LLM ownership | LLM is a Pipecat processor *inside* `voice_2_voice_server` (`bot.py:178–183`), provider-aware (OpenAI / Anthropic / Grok / Kenpath). | The voice channel must not call any LLM. All LLM calls go through Agent Core's `chat_provider`. The voice channel is a transport, not an agent. |
| B | Session/state | In-process `call_data` dict accumulated in `bot()` (`bot.py:327–342`); flushed to MinIO at end-of-call. | Stateless across calls. Per-session facts live in Memory Layer. |
| C | Configuration scope | `GET /api/v1/agents/config/{agent_type}` ships `system_prompt`, `llm_model`, `stt_model`, `tts_model`, `knowledge_base_enabled`, `knowledge_document_ids` to the voice service. | Voice channel config carries channel/transport keys only. Persona, model, tools, KB, prompts live in Agent Core + the Domain Configuration Kit YAML. |
| D | Tenant routing | `org_id` and `agent_type` are first-class. Phone-number → agent lookup happens in the VoicERA backend. | Sessions are identified by `session_id`, callers by `user_id`. No `org_id` in the PoC. |
| E | KB / RAG ownership | OpenAI provider in `voice_2_voice_server` reads `knowledge_document_ids` and pulls excerpts inside the voice service. | Knowledge Engine is a separate DPG block. Retrieval happens only when Agent Core's LLM emits a `knowledge_retrieval` tool call. The voice channel never sees document IDs. |
| F | Recording / audit | Voice service writes raw WAV + transcript JSON to MinIO and `POST /api/v1/call-recordings`. | Voice channel never persists audio. Audit goes to the Observability Layer (OTel + planned Grafana boards). No binary-storage abstraction in DPG, will need to be added. |
| G | Hangup / lifecycle control | `session_timeout_minutes` (per-agent) and Pipecat session timeout drive call end. | Hangup is driven by `DoneEvent.session_ended` from Agent Core, set when the conversation graph reaches an end state. |

`voice_2_voice_server` is a complete voice agent platform. The DPG voice channel covers the same surface area but factors AI logic out into Agent Core. Adopting `voice_2_voice_server` whole would invert the framework's architecture.

---

## 4. The DPG voice channel's swappable provider interfaces

Every audio model used by the voice channel — operator, VAD, STT, TTS — is reached only through a DPG base class. Concrete providers (Raya today, AI4Bharat tomorrow, anything else later) plug in as alternative implementations of the same interface, selected via a single config key. The base classes deliberately expose **model configurability** as part of the contract: language, voice, speed, sample rate, model name and any other tunable that varies between deployments must be passed through at request time, not baked into the provider class.

This section describes those interfaces, the operational behaviours every provider must honour (barge-in disconnect, turn cancellation, silent-utterance signalling, sample-rate negotiation, error surfacing), and where in the channel each one is enforced. None of this is specific to AI4Bharat — it is what *any* provider has to satisfy to be safe inside the DPG voice channel.

### 4.1 Provider selection and model configurability

```yaml
reach_layer:
  channels:
    voice:
      stt_provider: raya          # or ai4bharat, deepgram, …
      tts_provider: raya
      vad_provider: silero
      operator: vobiz
      raya:
        api_key: ${RAYA_API_KEY}
        stt_wss_url: …
        tts_base_url: …
        tts_model: standard
        voice_id: voice_001
        language: hi
        tts_speed: 1.0
      ai4bharat:
        # Same shape — only the keys the provider needs.
        stt_url: …
        tts_url: …
        language: hi
        ...
```

A provider implementation reads only its own config block; the rest of the pipeline does not change. Adding a provider is a self-contained piece of work — write the concrete class, register it in the provider factory, document the config block.

### 4.2 STT — `STTServiceBase` (`pipecat_services/stt_base.py`)

Required surface:

```python
class STTServiceBase(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete VAD-segmented utterance.

        Args:
            audio: WAV bytes — mono, PCM16, sample rate per config.

        Returns:
            Transcribed text on success.
            "" if the utterance was genuinely silent (distinct from error).
            None on service error (timeout, non-2xx, malformed response).
        """
```

Every concrete STT must:

- **Honour configured language and sample rate** — both are deployment-driven, not provider-baked.
- **Distinguish silent input from service failure.** `""` means "no speech"; `None` means "service unhealthy". The pipeline routes these differently — silent utterances are absorbed quietly, service failures trigger the fallback phrase and surface in observability.
- **Tolerate barge-in disconnect.** The channel may abandon a transcription mid-flight when the caller speaks again. The provider must release resources without retrying or buffering.
- **Stay inside latency budget.** Time-to-first-transcript ≤ 500 ms p95 for ≤ 8 s of 8 kHz audio.
- **Retry idempotent transient errors once** with bounded backoff before returning `None` (see `RayaSTTService.transcribe` for the reference pattern).

### 4.3 TTS — `TTSServiceBase` (`pipecat_services/tts_base.py`)

Required surface:

```python
class TTSServiceBase(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream PCM16 audio chunks for the given text.

        Yields:
            PCM16 bytes at the configured sample rate, mono.
            Yields nothing on transport error (logs and returns cleanly).
        """
```

Every concrete TTS must:

- **Honour configured sample rate per request.** Telephony deployments need 8 kHz; voice-bot deployments may want 16 kHz or higher. The channel will not resample on the consumer side.
- **Honour configured voice, language, model and speed at request time** — model selection is a config concern, not a provider concern.
- **Stream audio incrementally.** First chunk on the wire ≤ 300 ms p95 after the request is accepted. Sentence-by-sentence input drives sentence-by-sentence output; multi-second per-sentence latency breaks the conversational feel.
- **Treat mid-stream disconnect as the normal barge-in path.** The channel will close the connection when the caller interrupts. The provider must release server-side resources, stop synthesising, and not retry.
- **Surface transport errors visibly.** Empty stream + no error is the worst failure mode — the channel falls back to the configured fallback phrase, but only if the provider returns *some* signal that synthesis failed (yields nothing while logging the error is acceptable; silently returning nothing without a log entry is not).

### 4.4 VAD and operator interfaces

Less likely to be reused from VoicERA, but listed for completeness:

| Base | Concrete today | Required surface |
|---|---|---|
| `VADAnalyzerBase` (`vad/vad_base.py`) | `SileroVADWrapper` | `create_analyzer(config) → VADAnalyzer`. Tunables (`stop_secs`, `min_volume`, `confidence`, `start_secs`, `smoothing_factor`) all flow from config. |
| `TelephonyOperatorBase` (`operators/operator_base.py`) | `VobizOperator` | `parse_handshake(ws) → (stream_id, call_id)`, `create_transport(...)`, `webhook_response_xml(url)`. Per-operator REST hangup logic lives in a serializer subclass (`LoggingVobizFrameSerializer`). |

### 4.5 Operational features the channel implements on top of any provider

The following are channel-level features — the voice channel implements them once, and any pluggable provider has to be **compatible with them**:

| Feature | What the channel does | What the provider must support |
|---|---|---|
| **Barge-in (audio level)** | `UserTurnProcessor` between VAD and STT converts a `VADUserStartedSpeakingFrame` during bot TTS into a Pipecat `InterruptionFrame`. Pipecat flushes the TTS queue. | TTS provider must accept mid-stream HTTP disconnect as a clean cancel. STT provider's prior-utterance call (if still in flight) must tolerate cancellation. |
| **Barge-in (turn logic)** | `AgentCoreLLMProcessor._start_interruption()` flips the in-flight flag so the SSE consumer breaks; Agent Core's `TurnAssembler.cancel()` discards the interrupted segments. | No direct provider impact — but TTS must not have buffered the next several sentences locally; chunks the channel did not yet receive must not get sent later. |
| **Barge-in acknowledgement** | A configured short phrase is spoken when the gate (recency of last TTS push *or* SSE turn in flight) confirms the bot was actively speaking. | TTS provider must handle a fresh `synthesize()` call landing immediately after a cancelled one without conflict. |
| **Turn cancellation (defensive path)** | `DELETE /sessions/{id}/active_turn` on Agent Core; `_interrupted` flag on the processor; in-flight TTS chunks are dropped. | Same as barge-in TTS path. |
| **Silent-utterance handling** | STT returning `""` (silent) is logged and absorbed without invoking Agent Core. STT returning `None` (error) triggers the configured fallback phrase. | STT provider must distinguish these — never raise 4xx for "no speech detected"; never return a non-empty placeholder transcript. |
| **Filler phrase for slow turns** | If no `SentenceEvent` lands within `filler_threshold_ms`, the channel pushes a single `TTSSpeakFrame(filler_phrase)`. Cancelled on first real sentence; never speaks over the response. | TTS provider must cope with a short filler synthesis call landing concurrently with the eventual response synthesis. |
| **Opening phrase pre-emit** | On `on_client_connected`, the channel opens the SSE subscription before the caller speaks; Agent Core may emit an opening sentence which the channel synthesises immediately. | TTS provider must be ready to synthesise the first request without warm-up cost — first-chunk-on-wire ≤ 300 ms still applies. |
| **Terminal-word + clean hangup** | On `DoneEvent.session_ended=True`, the channel pushes a configured terminal-word `TextFrame` and an `EndFrame`; the operator serializer issues the REST DELETE. | Operator provider must implement REST hangup (or an equivalent leg-down signal) on `EndFrame`. |
| **Pipeline heartbeat** | `VADObserverProcessor` + heartbeat task log every `heartbeat_interval_s`; absence of frames between VAD and STT for an extended period surfaces as a stall. | Provider implementations should emit structured logs (`operation`, `status`, `latency_ms`) so heartbeat absence can be triaged against provider latency. |

The takeaway for any team contributing a provider: the channel already handles barge-in, turn cancellation, silent utterances, hangup, and filler. The provider's job is to be a clean, cancellable, configurable, low-latency I/O wrapper around a model — nothing more.

---

## 5. Requirements from the VoicERA team

Priority order. Everything is scoped to the `voicera_mono_repository` repo so it can be picked up without touching DPG.

### P0 — Telephony-blocking (the AI4Bharat servers are not production-usable for telephony without these)

1. **Make the AI4Bharat TTS sample rate request-driven, not model-bound.**
   - File: `ai4bharat_tts_server/server.py` (`state.sample_rate = state.model.config.sampling_rate`).
   - Change: accept `sample_rate` as a JSON field on `POST /tts/stream`. Resample server-side from the model's native rate to the requested rate (Parler → 8 kHz, 16 kHz, 24 kHz). Default unchanged.
   - Why: telephony is 8 kHz μ-law. 44.1 kHz output forces every adapter to ship a resampler.
   - Exit criterion: a `POST /tts/stream` with `"sample_rate": 8000` returns audio chunks at exactly 8 kHz.

2. **Document and stabilise the AI4Bharat STT contract for 8 kHz telephony WAV.**
   - File: `ai4bharat_stt_server/server.py`.
   - Change: state explicitly that `POST /transcribe` accepts 8 kHz mono PCM WAV (and resamples internally to 16 kHz). Return `{"transcript": ""}` on silent input — never 4xx for "no speech detected." Document the language code set (preferably ISO 639-1).
   - Why: today, the contract is implicit in `IndicConformerRESTSTTService` (a Pipecat-flavoured wrapper). External callers have to reverse-engineer it.
   - Exit criterion: a non-VoicERA HTTP client (`STTServiceBase`-style) can transcribe an 8 kHz WAV without any `voicera_mono_repository` dependency.

3. **API-key auth on both AI4Bharat servers.**
   - Files: both `ai4bharat_*_server/server.py`.
   - Change: require `X-API-Key`; reject without it; configurable from env. Shared-secret key is sufficient — no OAuth/JWT needed.
   - Why: services are open today. Putting them behind a sidecar pushes the security boundary out of the service that owns the data.
   - Exit criterion: requests without a configured `X-API-Key` return 401.

### P1 — Improves integration quality

4. **Streaming STT (optional but recommended).**
   - Today's STT is one-shot (full utterance → final transcript). Fine for the `SegmentedSTTService` path, but it forces VAD-end-only turns and prevents partial-transcript optimisations (early Trust Layer input checks, eager intent prefetch).
   - If a streaming endpoint is on the roadmap: please make it JSON SSE — `data: {"type": "partial", "text": "…"}` and `data: {"type": "final", "text": "…"}`. A streaming `STTServiceBase` implementation can be added against this without redesigning the pipeline.

5. **Document the AI4Bharat TTS streaming format and chunking guarantees.**
   - The client currently relies on `data: {"type": "chunk", "data": "<base64 F32LE>"}`. Other DPG deployments need a written spec — chunk size bounds, encoding (F32LE vs PCM16, after P0#1 lands), end-of-stream sentinel, error event shape.

6. **Provide a separate readiness endpoint.**
   - `GET /health` exists; please also expose `GET /ready` distinct from liveness (model loaded, GPU available, warmup done). Orchestrators gate pipeline startup on readiness.

### P2 — Architectural polish (optional, for VoicERA's own modularity)

7. **Decouple the LLM from `voice_2_voice_server`.**
   - This is conflict #A. Even if VoicERA does not adopt the DPG Agent Core endpoint, exposing a config switch where the Pipecat pipeline calls *any* HTTP endpoint that returns text (instead of `create_llm_service`) lets VoicERA's own deployments swap LLMs without code changes — and lets DPG-style integrations point the LLM step at Agent Core's `POST /sessions/{id}/input` + SSE.
   - Concretely, a Pipecat processor `HTTPLLMProcessor` that:
     - On `TranscriptionFrame`, POSTs `{"text": …, "session_id": …, "user_id": …}` to a configured URL.
     - Consumes SSE sentence events from the response.
     - Pushes each sentence as a `TTSSpeakFrame`.
   - This is essentially the inverse of the DPG `AgentCoreLLMProcessor`.

8. **Make recording / MinIO storage opt-in.**
   - `bot.py` unconditionally writes audio chunks and transcripts to MinIO at end-of-call. Gate behind a `recording.enabled: bool` flag so deployments without MinIO don't have to stub the storage layer.

9. **Pluggable phone-number → agent routing.**
   - The voice service depends on the VoicERA backend for `org_id` and `GET /api/v1/agents/by-phone/{phone}`. A pluggable resolver interface (`OrgResolverBase`) would let other deployments wire in their own routing.

### P3 — Documentation only

10. **Publish a "VoicERA components as building blocks" guide:**
    - Lists components reusable in isolation (AI4Bharat STT, AI4Bharat TTS).
    - Lists what is not (`voice_2_voice_server` as a whole).
    - States the explicit HTTP contracts above.
    - Calls out required env vars and ports.

---

## 6. Recommended path forward

1. **VoicERA team:** ship the three P0 items (TTS sample-rate parameter, STT 8 kHz contract & docs, API-key auth). Small, additive, unblock external reuse of `ai4bharat_*_server` for any DPG instance.
2. **DPG side:** once P0#1 lands, add `AI4BharatSTTService(STTServiceBase, SegmentedSTTService)` and `AI4BharatTTSService(TTSServiceBase, TTSService)` alongside the Raya implementations, switchable via `reach_layer.channels.voice.stt_provider` / `tts_provider` in config.
3. **Future:** P2#7 (`HTTPLLMProcessor` in `voice_2_voice_server`) is the highest-leverage architectural change for VoicERA. It lets the same VoicERA voice service front *any* HTTP-text-LLM endpoint, including Agent Core. Worth scoping separately.

---

*For questions or discussion, please open an issue against `sanketika-labs/ai-diffusion-dpg` referencing this document.*
