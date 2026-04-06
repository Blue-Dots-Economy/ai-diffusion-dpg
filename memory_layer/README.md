# Memory Layer DPG

Manages all session state for the AI Composition Framework using Redis and Memgraph.

---

## What this service does

The Memory Layer is the single source of truth for conversation state. Agent Core reads state at the start of every turn and writes it back after the response is delivered. No other service reads or writes session state directly (with the documented exception of the Reach Layer web adapter — see CLAUDE.md).

State is managed across two backing stores:

- **Redis (RedisJSON):** Session data and user profiles. Keys: `profile:{phone_number}` (permanent or TTL 4h depending on consent), `session:{session_id}` (TTL 24h for consent=true, 4h for consent=false), `user_sessions:{phone_number}` (Sorted Set, reverse index for session lookup by phone number).
- **Memgraph:** Typed attribute graph per session. Each session is a `Session` node connected to `Attribute` nodes via domain-defined edge types (e.g. `HAS_TRADE`, `HAS_LOCATION`, `HAS_EDUCATION_LEVEL`). Edge types come from config (`state.profile_graph_relations`), never hardcoded. One graph query gives the LLM its complete context — no conversation history needed.

**Public interface — 5 methods:** `context_bundle()`, `write()`, `flush_session()`, `get_active_sessions()`, `delete_user()`.

---

## Folder structure

```
memory_layer/
├── main.py                     # Uvicorn entrypoint (port 8002)
├── pyproject.toml
├── config/
│   └── config.yaml             # Redis/Memgraph connection strings, TTLs, graph edge types
├── src/
│   ├── memory_layer.py         # Main orchestrator — 5-method public interface
│   ├── session_store.py        # RedisSessionStore
│   ├── graph_user_store.py     # Memgraph user profile graph
│   ├── graph_journey_store.py  # Memgraph journey/subagent graph
│   ├── graph_context_store.py  # Memgraph context query assembly
│   ├── audit_store.py          # Audit store (stub)
│   ├── audit_store_base.py     # AuditStoreBase ABC
│   └── server.py               # FastAPI app (all endpoints)
└── tests/
    ├── test_memory_layer.py
    └── test_server.py
```

---

## HTTP API

The service runs on port **8002**.

### `POST /session/read`

Load the current state for a session. Returns an empty dict if the session does not exist or has expired.

**Request:**
```json
{ "session_id": "sess-abc123" }
```

**Response:**
```json
{
  "session_id": "sess-abc123",
  "state": {
    "turn_count": 3,
    "confirmed_entities": {"trade": "electrician", "location": "Hubli"},
    "conversation_history": [...]
  }
}
```

### `POST /session/write`

Persist updated state for a session. Overwrites any existing state for the session ID.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "state": { "turn_count": 4, "confirmed_entities": {...}, "conversation_history": [...] }
}
```

**Response:** `{ "status": "ok" }`

### `GET /profile/{session_id}`

Returns the user profile and context graph bundle for a session.

**Response:**
```json
{
  "session_id": "sess-abc123",
  "turn_count": 3,
  "confirmed_entities": {"trade": "electrician"}
}
```

### `DELETE /session/{session_id}`

Removes all state for a session. Used at end-of-session or for testing cleanup.

**Response:** `{ "status": "ok" }`

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

---

## State schema

Agent Core writes a dict with the following top-level keys. The Memory Layer treats session state as an opaque dict — it stores and returns whatever Agent Core writes. The context graph is managed separately via typed Memgraph edges derived from `confirmed_entities`.

| Key | Type | Description |
|---|---|---|
| `turn_count` | int | Number of completed turns in this session |
| `confirmed_entities` | dict | Entities confirmed across turns (trade, location, etc.) |
| `conversation_history` | list | Alternating user/assistant message dicts |
| `workflow_step` | str | Current step in any multi-step workflow (optional) |
| `current_subagent_id` | str | Active subagent in the conversation graph |
| `user_storage_mode` | str \| None | Consent flag (`"full"`, `"anonymous"`, or `None`) |

---

## Configuration

| Key | Description |
|---|---|
| `server.port` | HTTP port (default: 8002) |
| `memory.redis_url` | Redis connection string |
| `memory.memgraph_host` | Memgraph host |
| `memory.memgraph_port` | Memgraph port |
| `memory.session_ttl_seconds` | Session TTL for consented users (default: 86400 — 24h) |
| `memory.session_ttl_anonymous_seconds` | Session TTL for anonymous/no-consent users (default: 14400 — 4h) |
| `state.profile_graph_relations` | List of Memgraph edge type labels (e.g. `HAS_TRADE`, `HAS_LOCATION`) |

---

## Running the service

Redis and Memgraph must be running before starting the Memory Layer. Both are included in the dev Docker Compose file:

```bash
docker compose -f automation/docker/docker-compose.dev.yml up -d
```

Then start the Memory Layer:

```bash
cd memory_layer
uv run uvicorn src.server:app --port 8002
```

---

## Running tests

```bash
cd memory_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
fastapi      >= 0.110
uvicorn      >= 0.29
pydantic     >= 2.0
pyyaml       >= 6.0
redis        >= 5.0      # RedisJSON support
neo4j        >= 5.0      # Memgraph Bolt driver
```

Requires Python 3.11+.

---

## Production Notes

TTLs, graph edge types, and Redis/Memgraph connection strings all come from config (`memory_layer.yaml`). No values are hardcoded in source.

To add new entity types to the context graph, add new edge type labels to `state.profile_graph_relations` in the domain config YAML (e.g. `dev-kit/configs/kkb/memory_layer.yaml`). No Python code changes are required — the graph store reads edge types from config at startup.

The audit store (`audit_store.py`) is currently a stub that implements `AuditStoreBase` with no-op methods. A production deployment should replace it with a persistent writer (e.g. PostgreSQL or BigQuery) for DPDP Act compliance.
