# Reach Layer

Normalises inbound channels and delivers responses. The Reach Layer ships as **three independently-deployable channel services** (CLI, Web, Voice) that share a common `reach_layer_base` package.

---

## What this service does

The Reach Layer is the channel boundary. On the inbound side it accepts raw user input from a channel (stdin, HTTP body, SIP audio) and submits it to Agent Core. On the outbound side it delivers Agent Core's response on the originating channel.

All state access (except the one approved exception below) must go through Agent Core. The Reach Layer never calls Knowledge Engine, Trust Layer, or other blocks directly.

---

## Architecture

### Three channels, one shared base

```
reach_layer/
├── base/                 # Shared library — NOT a service.
│   ├── reach_layer_base.py   # ReachLayerBase async ABC + concrete HTTP helpers
│   ├── text_channel.py       # TextChannelBase   (CLI, Web inherit)
│   ├── voice_channel.py      # VoiceChannelBase  (Voice inherits)
│   ├── events.py             # SignalEvent, SentenceEvent, DoneEvent dataclasses
│   ├── config_loader.py      # load_reach_config() + deep-merge + env-var expansion
│   ├── pyproject.toml        # Installed as "reach-layer-base"
│   └── __init__.py
│
├── config/               # Unified Reach Layer config (shared by all 3 channels)
│   ├── dpg.yaml          # framework defaults (reach_layer.common + channels.{cli,web,voice})
│   └── domain.yaml       # local-dev domain overrides (deploy mounts dev-kit configs over this)
│
├── cli/                  # Deployable 1 — interactive CLI (session mode)
│   ├── main.py
│   ├── src/cli_reach.py
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml    # depends on reach-layer-base via ../base
│
├── web/                  # Deployable 2 — FastAPI + React SPA (direct mode)
│   ├── server.py
│   ├── src/web_reach.py, src/auth.py
│   ├── web-src/          # React 19 + Vite source
│   ├── dist/             # built UI (gitignored)
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
│
└── voice/                # Deployable 3 — pipecat VOIP (session mode)
    ├── server.py
    ├── src/vobiz_adapter.py, src/bot.py, src/campaign_manager.py
    ├── src/pipecat_services/, src/vad/, src/operators/
    ├── tests/
    ├── Dockerfile
    └── pyproject.toml
```

`base/` is a **library** — it has no server, no port, no `main`. Each channel runs as its own process that imports the base classes (and concrete HTTP helpers) from `reach_layer_base`.

### Class hierarchy

```
ReachLayerBase (async ABC)
├── concrete:  submit_input(), subscribe_events(), cancel_turn(), close()
├── abstract:  on_session_start(), on_session_end()
│
├── TextChannelBase              VoiceChannelBase
│   └── + abstract run_loop()    └── + abstract handle_call(),
│       ├── CLIReach (cli/)          handle_barge_in(), on_vad_event()
│       └── WebReachLayer (web/)     └── VobizAdapter (voice/)
```

`submit_input`, `subscribe_events`, and `cancel_turn` are concrete on the base class because the HTTP wire protocol to Agent Core is identical for every channel — only the input/output surface differs.

### assembly_mode — how input reaches Agent Core

Each channel reads `reach_layer.channels.<name>.assembly_mode` from `reach_layer.yaml` and routes accordingly:

| assembly_mode | `submit_input()` endpoint                  | Response delivery                          | Used by     |
|---------------|--------------------------------------------|--------------------------------------------|-------------|
| `session`     | `POST /sessions/{id}/input` → 202          | long-lived `GET /sessions/{id}/events` SSE | cli, voice  |
| `direct`      | `POST /process_turn` → sync TurnResult     | returned inline from `submit_input()`      | web         |

Session mode additionally supports barge-in via `DELETE /sessions/{id}/active_turn` (`cancel_turn()`).

---

## CLI channel

`CLIReach` (`cli/src/cli_reach.py`) inherits `TextChannelBase`. Uses **session** assembly mode.

**How it works:**

1. `run_loop()` reads stdin line by line.
2. Each line is submitted via `submit_input(session_id, text, user_id)` → Agent Core's TurnAssembler (`POST /sessions/{id}/input`).
3. In parallel the CLI subscribes via `subscribe_events(session_id)` → long-lived SSE.
4. Each `SentenceEvent` is written to stdout as it arrives.
5. On EOF (Ctrl-D) or `quit` → `on_session_end()` and cleanup.

A UUID session ID is generated once per process. Restart to start fresh.

---

## Web channel

`WebReachLayer` (`web/src/web_reach.py`) is instantiated per-request by the FastAPI server in `web/server.py`. Uses **direct** assembly mode.

### `POST /chat`

Proxies a user message to Agent Core and returns the response.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "user_id": "rahul_electrician",
  "message": "electrician ka kaam chahiye"
}
```

**Response:**
```json
{
  "response_text": "Hubli mein electrician ke liye salary ₹15,000–₹28,000/month hai.",
  "was_escalated": false,
  "was_tool_used": true,
  "session_id": "sess-abc123",
  "latency_ms": 1102
}
```

On failure, returns a safe error message. Retries once on timeout.

### `GET /user-history/{user_id}`

Returns the user's active session ID and prior turns.

**Approved exception:** this endpoint calls Memory Layer directly, bypassing Agent Core. Scoped to the dev/demo web adapter only. All other channels and all other paths must route state access through Agent Core.

### `GET /app-config`

Returns the `ui:` section of the merged config (app title, tagline, icon, etc.) for the browser.

### `GET /` and `GET /assets/*`

Serves the compiled React SPA from `web/dist/` (built from `web/web-src/`).

### `GET /health`

```json
{ "status": "ok" }
```

### Authentication (optional)

When `auth.enabled: true` in the domain config, the web channel requires a Google Sign-In session cookie on `/chat` and `/user-history`. When disabled (default), the legacy setup screen is used. See `docs/` for deployment details.

---

## Voice channel

`VobizAdapter` (`voice/src/vobiz_adapter.py`) inherits `VoiceChannelBase`. Uses **session** assembly mode.

Built on the [pipecat](https://github.com/pipecat-ai/pipecat) pipeline:

```
VAD (silero)  →  STT (raya_stt)  →  AgentCoreLLM  →  TTS (raya_tts)  →  SIP
```

- VAD detects speech boundaries → emits segments
- Each segment → `submit_input(session_id, text, user_id)` (session mode)
- `AgentCoreLLM` subscribes to `GET /sessions/{id}/events` and pushes `SentenceEvent.text` into the TTS queue as sentences arrive
- Barge-in: pipecat's `VADProcessor` interrupts TTS automatically; `handle_barge_in()` → `cancel_turn()`
- Campaign support via `campaign_manager.py` for outbound calls

Lifecycle hooks (`on_session_start`, `on_session_end`, `handle_barge_in`, `on_vad_event`) are structured-log no-ops today — pipecat owns the real lifecycle. They exist so the Observability Layer can be wired in later without changing the class hierarchy.

The VOIP operator is pluggable: `operator_base.py` defines the ABC, `vobiz_operator.py` is the concrete Vobiz implementation.

---

## Web UI (React SPA)

React 19 + Vite 6 + Tailwind CSS 3 single-page application located in `web/web-src/`. It is the demo/dev interface served by the web channel.

### Features

- Dark/light theme (persisted)
- Server-driven branding from `/app-config`
- Setup screen with user ID entry or auto-restore via `/user-history/{user_id}`
- Google Sign-In (optional, config-gated)
- Rich Markdown rendering — tables, fenced code blocks with highlighting, blockquotes, collapsible long responses
- Message bubbles with latency/tool-use/escalation badges
- Auto-scroll with "Latest message" FAB
- 2000-char input with auto-grow textarea, Enter to send, Shift+Enter newline
- Debug panel showing session ID (click to copy)
- Toast notifications on connection errors
- Typing indicator

### Tech stack

| Layer     | Library                                                         |
|-----------|-----------------------------------------------------------------|
| Framework | React 19 + Vite 6                                               |
| Styling   | Tailwind CSS 3 + CSS custom properties                          |
| Markdown  | react-markdown, remark-gfm, rehype-highlight, highlight.js      |
| Tests     | Vitest 3 + @testing-library/react                               |

### Building the UI

```bash
cd reach_layer/web/web-src
npm install
npm run build        # outputs to reach_layer/web/dist/
```

### Development with HMR

```bash
# Terminal 1 — Python backend
cd reach_layer/web
uv run uvicorn server:app --port 8005 --reload

# Terminal 2 — Vite dev server
cd reach_layer/web/web-src
npm run dev          # http://localhost:5174, proxies API to :8005
```

### Running UI tests

```bash
cd reach_layer/web/web-src
npm test                 # run once
npm run test:watch       # watch mode
npm run test:coverage    # with HTML coverage report
```

---

## Configuration

Config is split across two layers:

1. **`reach_layer.yaml`** (in `reach_layer/config/`) — one unified file shared by all three channels. Each service reads only its own slice via `load_reach_config(channel_name)`.
2. **`agent_core.yaml`** — TurnAssembler tuning lives inside Agent Core because it runs there.

### `reach_layer/config/{dpg,domain}.yaml` — per-channel routing

Single source of truth for all three channel services. Schema:

```yaml
reach_layer:
  common:
    agent_core_client: { endpoint, timeout_s }
    memory_layer_client: { endpoint, timeout_s }
    observability: { otel: {...}, domain: "" }
  channels:
    cli:   { enabled, assembly_mode, prompt, agent_prefix }
    web:   { enabled, assembly_mode, server, sessions, auth, ui }
    voice: { enabled, assembly_mode, port, public_url, vobiz, vad, raya, agent_core }
```

| Key | Description |
|-----|-------------|
| `reach_layer.channels.<name>.enabled` | `false` causes the service to refuse to start (selective deployment) |
| `reach_layer.channels.<name>.assembly_mode` | `session` or `direct` — selects the wire protocol |
| `reach_layer.channels.cli.prompt` / `agent_prefix` | CLI prompts |
| `reach_layer.channels.web.sessions.limit` | Sidebar conversations list size (web) |
| `reach_layer.channels.web.auth.*` | Google SSO config for web channel |
| `reach_layer.channels.web.ui.*` | Web UI copy (app name, tagline, storage keys, localisation) |
| `reach_layer.channels.voice.{vobiz,vad,raya,agent_core}` | VOIP, VAD, STT/TTS, and Agent Core call config for voice |
| `reach_layer.common.agent_core_client.{endpoint,timeout_s}` | Agent Core URL + timeout shared across channels |
| `reach_layer.common.memory_layer_client.endpoint` | Memory Layer base URL (web session-restore only) |
| `reach_layer.common.observability.otel.collector_endpoint` | OTel collector |

For backward compatibility the loader injects legacy top-level aliases (`agent_core_client`, `ui`, `auth`, `telephony_adapter`, …) so existing service code does not need to be rewritten.

Domain overrides live in `reach_layer/config/domain.yaml` (local dev) or `dev-kit/configs/<domain>/reach_layer.yaml` (deploy). Both files share the exact schema of `dpg.yaml` and are deep-merged on top of it. Env-var placeholders (`${VAR}` / `${VAR:-default}`) are expanded at load time.

### `agent_core.yaml` — turn-assembler tuning (per channel)

TurnAssembler lives inside Agent Core but is tuned per channel, so the tuning keys are co-located with the code:

| Key | Description |
|-----|-------------|
| `reach_layer.turn_assembler.semantic_gate.{enabled,confidence_threshold}` | NLU-based early trigger defaults |
| `reach_layer.turn_assembler.silence_trigger.silence_ms` | Silence timer default |
| `reach_layer.turn_assembler.max_wait_ceiling.max_wait_ms` | Max wait default |
| `reach_layer.channels.<name>.turn_assembler.*` | Per-channel override of any of the above |

---

## Running

### Docker (recommended)

Each channel is a separate docker-compose service. Voice and Web start with the rest of the stack; CLI is opt-in via a profile.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd automation/docker

# Start backend + web + voice
docker compose -f docker-compose.dev.yml up -d

# Interactive CLI (opt-in)
docker compose -f docker-compose.dev.yml run --rm reach_layer_cli
```

Ports: `reach_layer_web:8005`, `reach_layer_voice:8006` (via ngrok tunnel to Vobiz SIP). CLI has no exposed port — it's stdin/tty only.

### Local (without Docker)

Each channel is a standalone uv project. From the repo root:

```bash
# CLI
cd reach_layer/cli && uv sync && uv run python main.py

# Web
cd reach_layer/web/web-src && npm install && npm run build
cd reach_layer/web && uv sync && uv run uvicorn server:app --port 8005

# Voice
cd reach_layer/voice && uv sync && uv run python server.py
```

Agent Core, Memory Layer, Trust Layer, Knowledge Engine, Action Gateway, and Observability Layer must be running first.

---

## Running tests

Each channel has its own test suite and runs independently.

```bash
# CLI
cd reach_layer/cli  && uv run pytest tests/ -v

# Web
cd reach_layer/web  && uv run pytest tests/ -v

# Voice
cd reach_layer/voice && uv run pytest tests/ -v

# Web UI (React)
cd reach_layer/web/web-src && npm test
```

Each channel's `tests/conftest.py` loads `reach_layer_base` directly from `../base/__init__.py` via `importlib` — bare `uv run pytest` works from a fresh clone with no install step.

---

## Dependencies

Each channel declares its own dependencies. `reach-layer-base` is shared.

### `reach_layer/base/pyproject.toml`

```
httpx                                    >= 0.27.0
```

### `reach_layer/cli/pyproject.toml`

```
reach-layer-base                         (path: ../base)
observability-layer                      (path: ../../observability_layer)
httpx, pyyaml, python-dotenv
```

### `reach_layer/web/pyproject.toml`

```
reach-layer-base, observability-layer
fastapi, uvicorn[standard], aiofiles
httpx, pyyaml, python-dotenv
authlib, PyJWT                           (Google SSO)
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-httpx
```

### `reach_layer/voice/pyproject.toml`

```
reach-layer-base, observability-layer
pipecat-ai, pipecat-ai-silero-vad, pipecat-ai-daily
fastapi, uvicorn[standard]
httpx, pyyaml, python-dotenv
```

### `reach_layer/web/web-src/package.json`

```
react ^19.0.0, react-dom ^19.0.0
react-markdown, remark-gfm, rehype-highlight, highlight.js
vite ^6.0.0, tailwindcss ^3.4, vitest ^3.x
```

Python 3.11+; Node 18+.

---

## Adding a new channel

1. Create a new folder under `reach_layer/` with its own `pyproject.toml`, declaring `reach-layer-base` as a path dependency.
2. Inherit from `TextChannelBase` (for text channels) or `VoiceChannelBase` (for voice channels) — _not_ from `ReachLayerBase` directly unless you need something neither specialisation offers.
3. Implement only the abstract methods: `on_session_start`, `on_session_end`, plus `run_loop` (text) or `handle_call` / `handle_barge_in` / `on_vad_event` (voice). The HTTP wire methods (`submit_input`, `subscribe_events`, `cancel_turn`) come for free from the base class.
4. Add the channel to `reach_layer.yaml` under `reach_layer.channels.<name>` with its `assembly_mode`.
5. Optionally add per-channel turn-assembler overrides under `agent_core.yaml` `reach_layer.channels.<name>.turn_assembler`.
6. Write a `Dockerfile` following the pattern in `cli/`, `web/`, or `voice/` (build context = repo root; `sed` rewrite of the `../base` path dep).
7. Register the service in `automation/docker/docker-compose*.yml`.
8. Agent Core and all other services require no changes.

---

## Known gaps

**Web channel does not stream sentences to the browser.** The web server buffers all `SentenceEvent`s from Agent Core's SSE stream and returns a single JSON response to `/chat`. A planned `POST /chat/stream` endpoint (#99) will expose the per-sentence stream to the browser with typewriter animation, eliminating the 4–6 s blank wait.

**TTS audio does not stop mid-playback on barge-in.** The voice channel cancels the active turn via `cancel_turn()` when barge-in is detected, but Raya TTS audio that is already buffered in the pipecat pipeline continues playing. Stopping in-flight audio output mid-utterance requires an additional signal to the TTS service (#98).

**Voice notes input not supported in web UI.** The web channel accepts only text. A planned enhancement (#52) will add microphone recording → audio blob upload → whisper transcription in the browser.

**Production channel adapters pending.** Three channels described in the original DPG spec are not yet implemented: WhatsApp (Gupshup/Twilio), Mobile SDK (iOS/Android), and outbound campaign manager (#9). The current CLI, Web, and Voice implementations cover the PoC scope only.

**Voice channel TTS audio does not stop on barge-in (#98).** `handle_barge_in()` cancels the active turn but cannot stop Raya TTS audio already in-flight through the pipecat pipeline. The user and agent speech overlap until the buffered audio drains.

**`on_session_start` / `on_session_end` / `on_vad_event` hooks are structured-log no-ops.** These lifecycle hooks exist on `VobizAdapter` and are called by pipecat at the right moments, but their bodies only emit structured log entries. Wiring them to the Observability Layer (emit signal events) is deferred.
