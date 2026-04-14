# Reach Layer: Channel-Based Hierarchy with Independent Deployables — Spec

**Status:** Approved for implementation
**Date:** 2026-04-14
**Issue:** [#73](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/73)
**Sub-tasks:** [#84](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/84) [#85](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/85) [#86](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/86) [#87](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/87) [#88](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/88)
**Depends on:** TurnAssembler (#72) — reach layers call session-based Agent Core endpoints
**Moves:** `telephony_adapter/` → `reach_layer/voice/`

---

## Problem

Two separate class hierarchies exist today:
- `ReachLayerBase` in `reach_layer/src/base.py` — synchronous `receive()` / `deliver()` interface, 1:1 model
- `TelephonyAdapterBase` in `telephony_adapter/src/base.py` — async call lifecycle, standalone hierarchy

Both are conceptually the same thing — channel adapters. The existing synchronous interface is incompatible with the new session-based Agent Core endpoints (`POST /sessions/{id}/input`, `GET /sessions/{id}/events`). There is no shared base for voice-specific behaviour (barge-in, VAD events).

---

## Target structure

```
reach_layer/
├── base/
│   ├── __init__.py
│   ├── reach_layer_base.py     # ReachLayerBase ABC
│   ├── text_channel.py         # TextChannelBase(ReachLayerBase)
│   └── voice_channel.py        # VoiceChannelBase(ReachLayerBase)
│
├── cli/
│   ├── src/
│   │   └── cli_reach.py        # CLIReachLayer(TextChannelBase)
│   ├── Dockerfile
│   └── pyproject.toml
│
├── web/
│   ├── src/
│   │   ├── web_reach.py        # WebReachLayer(TextChannelBase)
│   │   ├── server.py           # FastAPI server (migrated)
│   │   └── auth.py             # Google SSO (migrated)
│   ├── Dockerfile
│   └── pyproject.toml
│
└── voice/
    ├── src/
    │   ├── base.py             # TelephonyAdapterBase(VoiceChannelBase)
    │   ├── bot.py
    │   ├── vobiz_adapter.py
    │   ├── campaign_manager.py
    │   ├── operators/
    │   │   ├── operator_base.py
    │   │   └── vobiz_operator.py
    │   ├── pipecat_services/
    │   │   ├── raya_stt.py
    │   │   ├── raya_tts.py
    │   │   ├── tts_sanitizer.py
    │   │   ├── stt_base.py
    │   │   └── tts_base.py
    │   └── vad/
    │       ├── vad_base.py
    │       └── silero_vad.py
    ├── Dockerfile
    └── pyproject.toml
```

`telephony_adapter/` top-level directory is deleted. All CI references, Docker Compose service entries, and import paths updated.

---

## Base class hierarchy

### ReachLayerBase (`reach_layer/base/reach_layer_base.py`)

```python
class ReachLayerBase(ABC):

    @abstractmethod
    async def submit_segment(self, session_id: str, text: str, is_final: bool = False) -> None:
        """Submit one text segment to Agent Core TurnAssembler (POST /sessions/{id}/input)."""

    @abstractmethod
    async def subscribe_events(self, session_id: str) -> AsyncGenerator[StreamEvent, None]:
        """Open SSE subscription to Agent Core (GET /sessions/{id}/events)."""

    @abstractmethod
    async def cancel_turn(self, session_id: str) -> None:
        """Interrupt the active turn (DELETE /sessions/{id}/active_turn)."""

    @abstractmethod
    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """Called when a new session begins. Sets up channel-specific state."""

    @abstractmethod
    async def on_session_end(self, session_id: str) -> None:
        """Called when a session ends. Tears down channel-specific state."""
```

### TextChannelBase (`reach_layer/base/text_channel.py`)

```python
class TextChannelBase(ReachLayerBase, ABC):

    @abstractmethod
    async def run_loop(self) -> None:
        """Read input from the text channel, submit segments, render events to UI.
        
        Implementations read from their input source (stdin, HTTP body, WebSocket),
        call submit_segment(), subscribe to events, and render SentenceEvents
        to their output surface. Runs until channel signals end of input.
        """
```

### VoiceChannelBase (`reach_layer/base/voice_channel.py`)

```python
class VoiceChannelBase(ReachLayerBase, ABC):

    @abstractmethod
    async def handle_barge_in(self, session_id: str) -> None:
        """Interrupt TTS playback and cancel the active turn on barge-in.
        
        Implementations must: (1) stop TTS audio queue, (2) call cancel_turn(),
        (3) prepare to accept new segments.
        """

    @abstractmethod
    async def on_vad_event(self, session_id: str, event: VADEvent) -> None:
        """Handle VAD signal — speech_start, speech_end, silence_detected."""
```

---

## Channel implementations

### CLIReachLayer (TextChannelBase)

`run_loop()`: reads from stdin line-by-line. Each line → `submit_segment()`. Subscribes to `subscribe_events()` and writes `SentenceEvent.text` to stdout as sentences arrive. Signal events optionally printed as status lines if `--verbose` flag set. On EOF → `on_session_end()`.

Turn assembler config: `silence_ms: 200`, `max_wait_ms: 10000` (generous for interactive typing).

### WebReachLayer (TextChannelBase)

Browser sends message via `POST /chat` → `server.py` calls `submit_segment()`. Browser opens `GET /chat/stream/{session_id}` → `server.py` calls `subscribe_events()`, forwards `StreamEvent`s as SSE to browser. `SentenceEvent`s produce typing-indicator effect; `SignalEvent`s can render "thinking…" states. Cancel from browser → `cancel_turn()`.

**Preserved:** `GET /user-history/{user_id}` direct Memory Layer call (approved exception per CLAUDE.md). Google SSO (`auth.py`) migrated unchanged. All existing browser-facing routes preserved.

Turn assembler config: `silence_ms: 1500`, `max_wait_ms: 15000`.

### TelephonyAdapterBase → VoiceChannelBase (voice/)

`TelephonyAdapterBase` extends `VoiceChannelBase`.

`AgentCoreLLMProcessor` (pipecat `FrameProcessor` posting to `/process_turn`) is **deleted**. Its responsibilities move into `VobizAdapter` / a new `VoiceSessionManager` that uses `VoiceChannelBase` directly:

| Event | Action |
|---|---|
| Call start | `on_session_start(session_id, caller_id)` + open `subscribe_events()` subscription |
| `TranscriptionFrame` from STT | `submit_segment(session_id, text)` |
| `SentenceEvent` received | Push `TTSSpeakFrame` downstream to RayaTTSService |
| `SignalEvent(stage="tool_start")` | Optionally play hold music / filler phrase |
| `DoneEvent(was_escalated=True)` | Push `EndFrame` to hang up |
| Caller barge-in (VAD detects speech during TTS) | `handle_barge_in(session_id)` → `cancel_turn()` + stop TTS queue |
| Call end | `on_session_end(session_id)` |

All pipecat pipeline internals (STT, TTS, VAD) are **unchanged** — only the Agent Core interface changes.

Turn assembler config: `silence_ms: 400`, `max_wait_ms: 8000`.

---

## Config-driven deployment

### Domain YAML

```yaml
reach_layer:
  channels:
    - cli      # starts reach_layer_cli service
    - web      # starts reach_layer_web service
    - voice    # starts reach_layer_voice service
```

Framework default (`dev-kit/dpg/reach_layer.yaml`): `channels: []` — domain config must declare explicitly.

### Docker Compose (`automation/docker/docker-compose.dev.yml`)

```yaml
services:
  reach_layer_cli:
    build: ../../reach_layer/cli
    profiles: ["cli"]
    depends_on: [agent_core]

  reach_layer_web:
    build: ../../reach_layer/web
    profiles: ["web"]
    ports: ["3000:3000"]
    depends_on: [agent_core, memory_layer]

  reach_layer_voice:
    build: ../../reach_layer/voice
    profiles: ["voice"]
    ports: ["8765:8765"]
    depends_on: [agent_core]
```

`telephony_adapter` service entry removed.

### Startup script

`automation/docker/start.sh` (or deploy wizard update): reads `reach_layer.channels` from merged config, maps to `--profile` flags, runs Docker Compose.

```bash
# example: channels: [web, voice]
docker compose --profile web --profile voice up -d
```

---

## Migration notes

- Old `reach_layer/src/base.py` (sync `receive()` / `deliver()`) deleted after `#85` and `#86` complete.
- Old `reach_layer/src/cli_reach.py` and `reach_layer/src/web_reach.py` deleted after respective migrations.
- `telephony_adapter/` top-level directory deleted after `#87` completes.
- All tests migrated to new locations; coverage maintained.
- CLAUDE.md "Run the full system" example updated with profile-based startup.

---

## Execution order

`#84` (base classes) must merge before `#85`, `#86`, `#87` can begin. Those three are independent of each other. `#88` (Docker Compose + config) depends on all channel migrations completing.
