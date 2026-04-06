# Reach Layer DPG

Manages inbound and outbound communication channels. For the PoC: a CLI REPL over stdin/stdout.

---

## What this service does

The Reach Layer normalises communication across all channels (WhatsApp, VOIP, Web, Mobile SDK) and presents a uniform interface to Agent Core. In the other direction, it delivers Agent Core's response back to the user on the originating channel.

For the PoC, two adapters are included:
- **CLI REPL** (`CLIReachLayer` in `src/cli_reach.py`): Reads one line from stdin, sends it to Agent Core via HTTP, and prints the response to stdout.
- **Web channel adapter** (`server.py`): A FastAPI app that includes a `GET /user-history/{user_id}` endpoint. This endpoint calls the Memory Layer directly to restore chat history before the first turn. This is a deliberate, scoped exception for the dev/demo web adapter — all other cross-module state access must go through Agent Core. Future production channel adapters must route state retrieval through Agent Core.

Each CLI session generates a UUID session ID at startup and reuses it for the entire conversation. This maintains conversation continuity — Agent Core can retrieve the session state from Memory Layer on every turn.

---

## Folder structure

```
reach_layer/
├── main.py                 # CLI entrypoint — starts the REPL loop
├── server.py               # FastAPI web channel adapter (GET /user-history/{user_id})
├── pyproject.toml
├── config/
│   └── config.yaml         # CLI prompts, agent_core endpoint, timeout
├── src/
│   ├── cli_reach.py        # CLIReachLayer — ReachLayerBase implementation
│   └── agent_core_client.py  # HTTP client for Agent Core POST /process_turn
└── tests/
    └── test_cli_reach.py
```

---

## Running the CLI

Start all backend services first (Agent Core, Knowledge Engine, Memory, Trust, Observability, Action Gateway), then:

```bash
cd reach_layer
uv run python main.py
```

The REPL starts with `You: `. Type any message and press Enter. The agent's response is printed after `Agent: `. Type `quit` or `exit` to end the session, or press Ctrl+D.

**Example session:**
```
You: electrician ka kaam chahiye Hubli mein
Agent: Hubli mein electrician ke liye current salary ₹15,000–₹28,000 per month hai.
       Demand strong hai — 12% QoQ growth. Top employers: Hubli Distribution Co,
       Karnataka Power. Kya aap apply karna chahte hain?

You: PMKVY kya hai?
Agent: PMKVY (Pradhan Mantri Kaushal Vikas Yojana) ek free skill training scheme hai.
       Indian citizen hona chahiye, age 15–45, school dropout ya unemployed.
       Training ke baad certificate milta hai aur ₹8,000 tak ka reward.

You: quit
```

---

## How it works

```
1. User types a message (stdin)
2. CLIReachLayer wraps it in a TurnInput (session_id, message, channel="cli", timestamp)
3. AgentCoreClient sends POST /process_turn to Agent Core (port 8000)
4. Agent Core runs the full pipeline and returns a TurnResult
5. CLIReachLayer prints the response_text to stdout
6. If was_escalated=true, a special escalation notice is printed
```

The HTTP call has a configurable timeout (default: 60 seconds) to accommodate cold-start latency on the first turn (LLM model loading, ChromaDB initialisation).

---

## Configuration

| Key | Description |
|---|---|
| `reach_layer.cli.prompt` | Input prompt displayed to user (default: `"You: "`) |
| `reach_layer.cli.agent_prefix` | Prefix for agent responses (default: `"Agent: "`) |
| `agent_core_client.endpoint` | Agent Core URL (default: `http://localhost:8000/process_turn`) |
| `agent_core_client.timeout_s` | HTTP timeout in seconds (default: 60.0) |
| `server.port` | Reserved port for future HTTP channel adapter (default: 8005) |

The 60-second timeout is intentional: the first request after a cold start can take 30–45 seconds due to LLM model loading and ChromaDB initialisation. Subsequent requests are typically 1–3 seconds.

---

## Session management

- Each `python main.py` invocation creates a **new session** with a fresh UUID.
- The session ID is reused for every turn within that invocation.
- When the process exits, the session persists in the Memory Layer until its TTL expires (1 hour by default).
- Restarting `main.py` starts a completely new session — previous context is not recovered.

---

## Running tests

```bash
source ../.venv/bin/activate
cd reach_layer
pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
httpx    >= 0.27    # HTTP client for Agent Core
pyyaml   >= 6.0    # Config loading
```

Requires Python 3.11+.

---

## Replacing the stub

To add a new channel (e.g. WhatsApp via a webhook):

1. Create a class that inherits from `ReachLayerBase` (defined in `agent_core/src/interfaces/reach_layer.py`).
2. Implement `receive() → TurnInput` and `deliver(TurnResult) → None` with identical signatures.
3. Wire the new class into `main.py` alongside or instead of `CLIReachLayer`.

The Agent Core and all other services require no changes — they only interact with the reach layer through `AgentCoreClient.process_turn()`.
