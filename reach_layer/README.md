# Reach Layer

Normalises inbound channels and delivers responses. Two adapters are included in the PoC: a CLI REPL over stdin/stdout and a FastAPI web server with a single-page chat UI.

---

## What this service does

The Reach Layer is the channel boundary. On the inbound side it normalises raw user input from any channel into a `TurnInput` struct. On the outbound side it delivers Agent Core's `TurnResult` back to the user on the originating channel.

All state access (except the one approved exception below) must go through Agent Core. The Reach Layer never calls Knowledge Engine, Trust Layer, or other blocks directly.

---

## Folder structure

```
reach_layer/
├── main.py              # CLI entry point — starts REPL loop
├── run.py               # CLI entry point with --phone argument
├── server.py            # FastAPI web server (port 8005)
├── config_loader.py     # Shared YAML loading + deep-merge utilities
├── pyproject.toml
├── Dockerfile           # Container for web server
├── config/
│   ├── dpg.yaml         # DPG defaults: CLI prompts, Agent Core endpoint, Memory Layer endpoint, UI config
│   └── domain.yaml      # KKB overrides: app_name, app_tagline, storage_key
├── src/
│   ├── base.py          # ReachLayerBase ABC — receive() → TurnInput, deliver(TurnResult) → None
│   ├── cli_reach.py     # CLIReachLayer — reads stdin, writes stdout
│   └── web_reach.py     # WebReachLayer — request-driven (build_turn_input, format_result)
├── web/
│   └── index.html       # Single-page chat UI served at GET /
└── tests/
    ├── test_cli_reach.py      (~22 tests)
    ├── test_server.py         (~30 tests)
    ├── test_web_reach.py      (~28 tests)
    ├── test_config_loader.py  (~27 tests)
    └── test_main.py           (~18 tests)
```

---

## CLI adapter

`CLIReachLayer` in `src/cli_reach.py` implements the `ReachLayerBase` interface for stdin/stdout use.

**How it works:**

1. `receive()` — reads one line from stdin, wraps it in a `TurnInput` (fields: `session_id`, `user_message`, `channel="cli"`, `timestamp_ms`).
2. Agent Core is called via HTTP `POST /process_turn`.
3. `deliver(result)` — prints `{agent_prefix}{response_text}` to stdout. If `was_escalated=True`, prepends `[ESCALATED TO HUMAN AGENT]` before the response.

**Session management:**

- A UUID session ID is generated once at construction time and reused for every turn in that process.
- Restarting `main.py` generates a new session ID — prior context is not recovered automatically.
- `user_id` is optional; passed at construction and forwarded on every turn.

---

## Web adapter

`server.py` is a FastAPI app on port **8005**. It serves the single-page chat UI and proxies messages to Agent Core.

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

`user_id` is optional.

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

On failure, returns a safe error message rather than propagating the exception. Retries once on timeout with a 1-second backoff.

Emits OTel span `reach.inbound` with `session_id` and `dpg.channel` attributes.

---

### `GET /user-history/{user_id}`

Returns the user's active session ID and prior turns.

**Response:**
```json
{
  "session_id": "sess-abc123",
  "turns": [ ... ]
}
```

**Approved exception:** This endpoint calls Memory Layer `GET /users/{user_id}/active-history` directly, bypassing Agent Core. This is a deliberate, scoped exception for the dev/demo web adapter only — the browser uses it to restore a prior conversation before the first message. All other state access in all other channel adapters must go through Agent Core.

---

### `GET /app-config`

Returns the `ui:` section of the merged config (used by the web UI to set the app title, tagline, and icon).

---

### `GET /`

Serves `web/index.html` — the single-page chat UI.

---

### `GET /health`

```json
{ "status": "ok" }
```

---

## HTTP clients

| Client | Target | Timeout | Retry |
|--------|--------|---------|-------|
| Agent Core client | `POST /process_turn` | 30s (configurable) | Once on `TimeoutException` with 1s backoff |
| Memory Layer client | `GET /users/{user_id}/active-history` | 10s (configurable) | None |

Both use a persistent `httpx.Client` instance.

---

## Session management

| Adapter | Session ID origin | Persistence |
|---------|-------------------|-------------|
| CLI | UUID generated at `CLIReachLayer` construction | In-process; new session on restart |
| Web | Provided by the browser (stored in `localStorage`) | Browser calls `GET /user-history/{user_id}` before first message to restore prior turns |

---

## Configuration

| Key | Description |
|-----|-------------|
| `reach_layer.cli.prompt` | Input prompt shown to user (default: `"You: "`) |
| `reach_layer.cli.agent_prefix` | Prefix for agent responses (default: `"Agent: "`) |
| `agent_core_client.endpoint` | Agent Core URL (default: `http://localhost:8000/process_turn`) |
| `agent_core_client.timeout_s` | HTTP timeout for Agent Core calls (default: `30.0`) |
| `memory_layer_client.endpoint` | Memory Layer base URL (default: `http://localhost:8002`) |
| `memory_layer_client.timeout_s` | HTTP timeout for Memory Layer calls (default: `10.0`) |
| `server.port` | Web server port (default: `8005`) |
| `ui.app_name` | Application name shown in the chat UI |
| `ui.app_tagline` | Tagline shown in the chat UI |
| `ui.app_icon` | Icon shown in the chat UI |
| `ui.user_id_placeholder` | Placeholder text for the user ID input |
| `ui.storage_key` | `localStorage` key used by the web UI to persist session state |

Config is loaded once at startup by deep-merging `config/dpg.yaml` (framework defaults) with `config/domain.yaml` (domain overrides).

---

## Running the CLI

Start all backend services first (Agent Core, Knowledge Engine, Memory Layer, Trust Layer, Observability Layer, Action Gateway), then:

```bash
cd reach_layer
uv run python main.py
```

With an optional phone/user identifier:

```bash
uv run python run.py --phone rahul_electrician
```

Type any message and press Enter. Type `quit` or `exit`, or press Ctrl+D, to end the session.

---

## Running the web server

```bash
cd reach_layer
uv run uvicorn server:app --host 0.0.0.0 --port 8005
```

Then open `http://localhost:8005` in a browser.

---

## Running tests

```bash
cd reach_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
httpx                                    >= 0.27.0
pyyaml                                   >= 6.0
python-dotenv                            >= 1.0.0
fastapi                                  >= 0.111.0
uvicorn[standard]                        >= 0.29.0
observability-layer                      (local path)
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-httpx
respx                                    >= 0.22.0  (dev only)
```

Requires Python 3.11+.

---

## Adding new channels

1. Create a class that inherits from `ReachLayerBase` (defined in `src/base.py`).
2. Implement `receive() -> TurnInput` and `deliver(result: TurnResult) -> None` with identical signatures.
3. All state access must go through Agent Core — do not call Memory Layer or other blocks directly (the `GET /user-history` exception is scoped to the web adapter only).
4. Wire the new class into the appropriate entry point alongside or instead of the existing adapters.
5. Agent Core and all other services require no changes.
