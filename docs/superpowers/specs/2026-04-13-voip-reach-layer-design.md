# VoIP Reach Layer — Design Spec

**Status:** Approved, ready for implementation  
**Date:** 2026-04-13  
**Scope:** `telephony_adapter/`  
**Related issue:** sanketika-labs/ai-diffusion-dpg#65 (Agent Core async streaming — deferred, tracked separately)

---

## Context

The `telephony_adapter` module already exists with:
- `TelephonyAdapterBase` — ABC with `handle_call()` / `teardown()`
- `RayaSTTService` — extends Pipecat's `SegmentedSTTService` directly
- `RayaTTSService` — extends Pipecat's `TTSService` directly
- `AgentCoreLLMProcessor` — extends Pipecat's `FrameProcessor`, calls `POST /process_turn`
- `run_bot()` — bare function, hardcodes `VobizFrameSerializer` and `SileroVADAnalyzer`
- No concrete class implementing `TelephonyAdapterBase`
- No DPG-owned base classes for STT, TTS, VAD, or telephony operator

**Goal:** Introduce DPG-conformant base classes for each component, create a concrete `VobizAdapter` implementing `TelephonyAdapterBase`, and wire `caller_id` → `user_id` for cross-call memory.

---

## Design Decisions

### D1 — Telephony operator is abstracted (other operators are planned)
`TelephonyOperatorBase` + `VobizOperator` concrete. The serializer is bundled with the operator — it encodes the same wire protocol and is never swapped independently.

### D2 — VAD is abstracted separately from the operator
`VADAnalyzerBase` + `SileroVADWrapper` concrete. VAD is operator-agnostic; Silero works with Twilio just as well as Vobiz.

### D3 — STT and TTS base classes are Pipecat-independent
`STTServiceBase` and `TTSServiceBase` are pure DPG ABCs with no Pipecat import. Concrete implementations (`RayaSTTService`, `RayaTTSService`) inherit from both the DPG base and the relevant Pipecat base. This keeps Pipecat as an implementation detail.

### D4 — `caller_id` (E.164) is `user_id`; fresh `uuid4()` per call is `session_id`
Stable `user_id` enables Memory Layer to associate sessions and recognise returning callers. `caller_id` is passed from the `/answer` webhook `From` field through `server.py` → `run_bot()` → `VobizAdapter`.

### D5 — Agent Core streaming deferred to #65
`AgentCoreLLMProcessor` continues using synchronous `POST /process_turn`. Once #65 ships, it migrates to `/process_turn/stream`.

### D6 — Barge-in is handled in two cooperating layers (GH-152)

Mid-response interruption is NOT a `handle_barge_in()` RPC on the adapter. It is implemented at two levels that each own a different concern:

1. **Audio-level, immediate (voice pipeline).** A `pipecat.turns.UserTurnProcessor` sits between `VADProcessor` and STT. It converts `VADUserStartedSpeakingFrame` into an `InterruptionFrame` whenever the bot is currently speaking. Pipecat flushes queued TTS audio system-wide. `AgentCoreLLMProcessor._start_interruption()` sets an internal flag that makes the in-flight SSE consumer exit on its next iteration, so further `SentenceEvent`s from Agent Core never reach TTS. Optionally pushes a single short `TTSSpeakFrame(barge_in_acknowledgement)` so the caller hears acknowledgement.
2. **Turn-logic, deferred (Agent Core).** Once the new speech is transcribed and reaches `TurnAssembler.add_segment()`, the state machine observes the active turn is `INVOKED` and calls `cancel()` → emits `DoneEvent(turn_status="interrupted")`. The subscribe-loop's replay hook **discards the original segments** (the LLM was responding to input the caller is now rejecting) and carries only the barge-in (pending) segments into the next turn.

The two layers are decoupled: (1) ends audio playback within ~200 ms of VAD onset regardless of what the Agent Core side is doing; (2) ensures the next turn's prompt is clean regardless of what the audio side did. Neither flows through `VobizAdapter.handle_barge_in()`, which remains a no-op retained only for the VoiceChannelBase contract.

Config key: `reach_layer.channels.voice.agent_core.barge_in_acknowledgement` (empty default → silent barge-in).

### D7 — Opening phrase is delivered via SSE on connect, not as a static TTS (GH-149)

Replaces the earlier "queue a greeting TTSSpeakFrame on on_client_connected" step. On call pickup the adapter opens `GET /sessions/{id}/events?user_id=<caller_id>` eagerly; Agent Core checks `session.opening_phrase_emitted` and, if unset, emits the entry subagent's `opening_phrase` as a `SentenceEvent` + `DoneEvent` pair, persisting the flag first. The voice pipeline renders these as normal TTS frames. No per-channel greeting string needs to exist.

---

## File Structure

```
telephony_adapter/src/
├── base.py                          # EXISTING — TelephonyAdapterBase (handle_call, teardown)
│
├── operators/
│   ├── __init__.py
│   ├── operator_base.py             # NEW — TelephonyOperatorBase ABC
│   └── vobiz_operator.py            # NEW — VobizOperator (concrete)
│
├── vad/
│   ├── __init__.py
│   ├── vad_base.py                  # NEW — VADAnalyzerBase ABC
│   └── silero_vad.py                # NEW — SileroVADWrapper (concrete)
│
├── pipecat_services/
│   ├── __init__.py
│   ├── stt_base.py                  # NEW — STTServiceBase ABC (no Pipecat import)
│   ├── tts_base.py                  # NEW — TTSServiceBase ABC (no Pipecat import)
│   ├── agent_core_llm.py            # EXISTING — unchanged for now
│   ├── raya_stt.py                  # EXISTING — updated to inherit STTServiceBase
│   └── raya_tts.py                  # EXISTING — updated to inherit TTSServiceBase
│
├── vobiz_adapter.py                 # NEW — VobizAdapter implements TelephonyAdapterBase
├── bot.py                           # EXISTING — slimmed to delegate to VobizAdapter
├── campaign_manager.py              # EXISTING — unchanged
└── server.py                        # EXISTING — passes caller_id to run_bot()
```

---

## Base Class Contracts

### `TelephonyOperatorBase` (`operators/operator_base.py`)

```python
class TelephonyOperatorBase(ABC):

    @abstractmethod
    async def parse_handshake(self, websocket) -> tuple[str, str]:
        """Parse provider-specific WebSocket handshake messages.

        Args:
            websocket: Active WebSocket connection from the telephony provider.

        Returns:
            Tuple of (stream_id, call_id) extracted from the handshake.
        """

    @abstractmethod
    def create_transport(
        self, websocket, stream_id: str, call_id: str
    ) -> FastAPIWebsocketTransport:
        """Build the Pipecat transport with the provider's frame serializer.

        Args:
            websocket: Active WebSocket connection.
            stream_id: Stream identifier from the handshake.
            call_id: Call identifier from the handshake.

        Returns:
            Configured FastAPIWebsocketTransport ready for pipeline use.
        """

    @abstractmethod
    def webhook_response_xml(self, websocket_url: str) -> str:
        """Return the XML response body for the telephony provider's /answer webhook.

        Args:
            websocket_url: Full WebSocket URL the provider should connect to.

        Returns:
            Provider-specific XML string.
        """
```

**`VobizOperator`** (`operators/vobiz_operator.py`):
- `parse_handshake`: calls `pipecat.runner.utils.parse_telephony_websocket()`, reads `stream_id` / `call_id`
- `create_transport`: instantiates `VobizFrameSerializer(stream_id, call_id, auth_id, auth_token, params)` and wraps in `FastAPIWebsocketTransport`
- `webhook_response_xml`: returns Vobiz/Plivo XML with `<Stream bidirectional="true" contentType="audio/x-l16;rate=16000">`
- Config read: `telephony_adapter.vobiz.auth_id`, `telephony_adapter.vobiz.auth_token`, `telephony_adapter.vobiz.sample_rate`

---

### `VADAnalyzerBase` (`vad/vad_base.py`)

```python
class VADAnalyzerBase(ABC):

    @abstractmethod
    def create_analyzer(self, config: dict) -> VADAnalyzer:
        """Instantiate and return a configured Pipecat VADAnalyzer.

        Args:
            config: Full merged config dict.

        Returns:
            Configured VADAnalyzer ready to pass to VADProcessor.
        """
```

**`SileroVADWrapper`** (`vad/silero_vad.py`):
- `create_analyzer`: reads `telephony_adapter.vad.stop_secs` (default 0.35), `min_volume` (default 0.3), `confidence` (default 0.4), `start_secs` (default 0.1), `smoothing_factor` (default 0.1)
- Returns `SileroVADAnalyzer` with those parameters — none hardcoded in `bot.py`

---

### `STTServiceBase` (`pipecat_services/stt_base.py`)

```python
class STTServiceBase(ABC):

    @abstractmethod
    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete utterance to text.

        Args:
            audio: Complete WAV file bytes (PCM16, mono) for one utterance.

        Returns:
            Transcribed text, or None if audio is silent or unintelligible.

        Raises:
            STTError: If transcription fails after retries.
        """
```

**`RayaSTTService`** (`pipecat_services/raya_stt.py`) updated:
- Inherits `STTServiceBase` and Pipecat's `SegmentedSTTService`
- `run_stt(audio)` (Pipecat hook) delegates to `transcribe(audio)` and yields `TranscriptionFrame`
- STT logic lives in `transcribe()` — the DPG interface

---

### `TTSServiceBase` (`pipecat_services/tts_base.py`)

```python
class TTSServiceBase(ABC):

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesize text to PCM16 audio chunks.

        Args:
            text: Text to synthesize.

        Yields:
            Raw PCM16 bytes at the configured sample rate (8000 Hz).

        Raises:
            TTSError: If synthesis fails.
        """
```

**`RayaTTSService`** (`pipecat_services/raya_tts.py`) updated:
- Inherits `TTSServiceBase` and Pipecat's `TTSService`
- `run_tts(text, context_id)` (Pipecat hook) delegates to `synthesize(text)` and yields `TTSAudioRawFrame`
- SSE streaming and F32LE→PCM16 conversion live in `synthesize()`

---

## `VobizAdapter` (`vobiz_adapter.py`)

Concrete implementation of `TelephonyAdapterBase`. Owns the full call lifecycle.

```python
class VobizAdapter(TelephonyAdapterBase):

    def __init__(self, config: dict) -> None:
        # Validates config; instantiates VobizOperator, SileroVADWrapper
        # Reads greeting from telephony_adapter.agent_core.greeting

    async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
        # 1. operator.parse_handshake(ws) → stream_id, call_id
        # 2. operator.create_transport(ws, stream_id, call_id) → transport
        # 3. vad_wrapper.create_analyzer(config) → vad_analyzer
        # 4. session_id = str(uuid4())
        # 5. user_id = caller_id  (E.164 — stable cross-call identifier)
        # 6. Instantiate RayaSTTService, AgentCoreLLMProcessor(call_sid, session_id, user_id), RayaTTSService
        # 7. Build Pipeline: transport.input → VADProcessor(vad_analyzer)
        #    → RayaSTTService → AgentCoreLLMProcessor → RayaTTSService → transport.output
        # 8. on_client_connected: queue greeting TTSSpeakFrame
        # 9. on_client_disconnected: cancel task
        # 10. runner.run(task)

    async def teardown(self, call_sid: str) -> None:
        # Log call ended; Pipecat handles WebSocket cleanup
```

**`bot.py`** after refactor:

```python
async def run_bot(websocket: WebSocket, call_sid: str, caller_id: str, config: dict) -> None:
    adapter = VobizAdapter(config)
    await adapter.handle_call(call_sid, caller_id, websocket)
    await adapter.teardown(call_sid)
```

**`server.py`** change:  
Extract `From` field from Vobiz `/answer` webhook form data. Pass as `caller_id` to `run_bot()`.

---

## `AgentCoreLLMProcessor` changes

Pass `user_id` (caller_id) in the Agent Core request payload:

```python
payload = {
    "session_id": self._session_id,
    "user_message": frame.text,
    "channel": "telephony",
    "user_id": self._user_id,       # caller E.164 — was call_sid before
    "timestamp_ms": int(start * 1000),
}
```

Constructor gains `user_id` parameter alongside existing `call_sid` and `session_id`.

---

## Config Shape (domain YAML additions)

```yaml
telephony_adapter:
  vobiz:
    auth_id: ""          # Vobiz auth ID
    auth_token: ""       # Vobiz auth token
    sample_rate: 8000    # Input sample rate from Vobiz

  vad:
    stop_secs: 0.35      # Silence duration to detect end-of-speech
    min_volume: 0.3      # Minimum volume threshold
    confidence: 0.4      # VAD confidence threshold
    start_secs: 0.1      # Duration of speech to confirm start
    smoothing_factor: 0.1

  raya:
    api_key: ""
    stt_language: "hi"
    tts_base_url: "https://hub.getraya.app/v1"
    tts_language: "hi"
    voice_id: "voice_001"
    tts_speed: 1.0
    stt_timeout_s: 30.0
    tts_timeout_s: 30.0

  agent_core:
    base_url: "http://agent_core:8000"
    timeout_ms: 5000
    # Transport-failure fallback only. GH-149 replaced the per-channel
    # `greeting` with the entry subagent's opening_phrase (emitted by Agent
    # Core on SSE connect).
    fallback_phrase: "माफ़ करें, मैं समझ नहीं पाया। कृपया दोबारा बोलें।"
    # Optional short acknowledgement spoken on barge-in (GH-152).
    barge_in_acknowledgement: ""
```

---

## Data Flow

```
Vobiz /answer webhook
  → server.py extracts caller_id (From field), call_sid (CallUUID)
  → returns XML with WebSocket URL
  → Vobiz connects WebSocket to /ws/{call_sid}

WebSocket accepted
  → run_bot(ws, call_sid, caller_id, config)
  → VobizAdapter.handle_call(call_sid, caller_id, ws)

Per-turn pipeline:
  Vobiz audio (8kHz PCMU)
    → VobizFrameSerializer.deserialize → AudioRawFrame
    → VADProcessor(SileroVADAnalyzer)
        emits VADUserStartedSpeakingFrame / VADUserStoppedSpeakingFrame
    → UserTurnProcessor (GH-152)
        converts VAD-start → InterruptionFrame when bot is speaking;
        SpeechTimeoutUserTurnStopStrategy closes user turn on silence
    → RayaSTTService.transcribe(wav_bytes) → TranscriptionFrame
    → AgentCoreLLMProcessor
        (session mode, assembly_mode=session):
          POST /sessions/{id}/input {user_message, user_id:caller_id}
          SSE consumer: SentenceEvent → TTSSpeakFrame per sentence;
          DoneEvent ends the turn
        (direct mode, assembly_mode=direct):
          POST /process_turn → TTSSpeakFrame(response_text)
    → RayaTTSService.synthesize(text)
        SSE chunks F32LE → PCM16 → TTSAudioRawFrame (8kHz)
    → VobizFrameSerializer.serialize → playAudio JSON
    → Vobiz (µ-law 8kHz audio to caller)

Barge-in (GH-152):
  VADUserStartedSpeakingFrame (user speaks during bot TTS)
    → UserTurnProcessor broadcasts InterruptionFrame
    → TTS service flushes queued audio (pipecat framework)
    → AgentCoreLLMProcessor._start_interruption():
        sets _interrupted flag, stops forwarding SentenceEvents,
        optionally speaks barge_in_acknowledgement
    → (STT of the new utterance completes a moment later)
    → TurnAssembler.add_segment() while INVOKED → cancel()
        emits DoneEvent(turn_status="interrupted"),
        DISCARDS the original segments,
        carries only the barge-in segments into the next turn

Session opening (GH-149):
  On WebSocket connect, vobiz_adapter opens
    GET /sessions/{id}/events?user_id=<caller_id>
  For a brand-new session, Agent Core emits the entry subagent's
  opening_phrase as SentenceEvent + DoneEvent on first subscribe;
  the flag session.opening_phrase_emitted is persisted first so
  reconnects and subsequent turns do not replay.

On was_escalated:
  → EndFrame → VobizFrameSerializer signals hang-up
```

---

## Testing Requirements

Each new file needs unit tests in `telephony_adapter/tests/`:

| File | Test file | Coverage target |
|---|---|---|
| `operators/operator_base.py` | `test_operator_base.py` | ABC enforcement |
| `operators/vobiz_operator.py` | `test_vobiz_operator.py` | parse_handshake, create_transport, webhook_xml |
| `vad/vad_base.py` | `test_vad_base.py` | ABC enforcement |
| `vad/silero_vad.py` | `test_silero_vad.py` | Config-driven parameters, defaults |
| `pipecat_services/stt_base.py` | `test_stt_base.py` | ABC enforcement |
| `pipecat_services/tts_base.py` | `test_tts_base.py` | ABC enforcement |
| `pipecat_services/raya_stt.py` | `test_raya_stt.py` | transcribe success/empty/http-error/timeout |
| `pipecat_services/raya_tts.py` | `test_raya_tts.py` | synthesize success/http-error/timeout/f32le-conversion |
| `vobiz_adapter.py` | `test_vobiz_adapter.py` | handle_call wiring, user_id=caller_id, teardown |

Existing `agent_core_llm.py` tests updated: `user_id` field in payload assertions.

---

## Exception Types

`STTError` and `TTSError` referenced in base class docstrings must be defined. Add to `telephony_adapter/src/base.py` alongside `TelephonyError`:

```python
class STTError(Exception):
    """Raised when transcription fails after retries."""

class TTSError(Exception):
    """Raised when speech synthesis fails."""
```

---

## Out of Scope

- Agent Core streaming (`/process_turn/stream`) — tracked in #65
- Outbound call campaigns (`campaign_manager.py`) — existing, unchanged
- Multi-tenancy / org routing — not in PoC scope
- Call recording / transcript storage — not in PoC scope
