# Memory Layer

Single source of truth for all session and user state in the DPG framework. Agent Core reads from it at the start of every turn and writes back asynchronously after response delivery. No other service reads or writes state directly, with one documented exception (the Reach Layer web adapter — see CLAUDE.md).

---

## What this service does

The Memory Layer manages state at three scopes:

- **Turn** — data live only within the current request/response cycle (held by Agent Core, not persisted here).
- **Session** — conversation-level data: turn count, confirmed entities, workflow step, conversation history. Stored in Redis with a configurable TTL.
- **Persistent** — cross-session user profile and journey data. Stored in a Memgraph graph database.

All writes are routed through a single `write(scope=...)` call. Agent Core never touches Redis or Memgraph directly.

An SQLite audit log records every session lifecycle event and every turn for DPDP Act compliance.

**Backing stores — all three are required at runtime:**

| Store | What it holds |
|---|---|
| Redis | Session data (`session:{session_id}`) and user session index (`user:{user_id}`) |
| Memgraph | User profile graph, journey graph, context signals |
| SQLite | Audit log — session lifecycle events and full turn history |

---

## Folder structure

```
memory_layer/
├── main.py
├── pyproject.toml
├── config/
│   ├── dpg.yaml          # Redis/Memgraph connection config, server port
│   └── domain.yaml       # Session schema, persistent graph schema, merge rules, TTLs
├── src/
│   ├── memory_layer.py         # MemoryLayer orchestrator — 5-method public interface + audit methods
│   ├── server.py               # FastAPI app — 10 HTTP endpoints
│   ├── session_store.py        # RedisSessionStore — session:{session_id} and user:{user_id} hash keys
│   ├── graph_user_store.py     # GraphUserStore — User, UserProfile, UserAttribute nodes
│   ├── graph_journey_store.py  # GraphJourneyStore — Journey nodes, child nodes, merge-on-flush
│   ├── graph_context_store.py  # GraphContextStore — Signal and ContextAttribute nodes
│   ├── audit_store_base.py     # AuditStoreBase ABC (4 abstract methods)
│   └── audit_store.py          # SQLiteAuditStore — session lifecycle + turn history
└── tests/                      # 226 tests across 7 files
    ├── test_memory_layer.py    (48 tests)
    ├── test_server.py          (29 tests)
    ├── test_session_store.py   (40 tests)
    ├── test_graph_stores.py    (41 tests)
    ├── test_audit_store.py     (24 tests)
    └── test_main.py            (23 tests)
```

---

## HTTP API

The service runs on port **8002**.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/context_bundle` | Load full context (session + profile + journey). Creates graph if new user. |
| POST | `/write` | Route write by scope: `session`→Redis, `persistent`→Memgraph, `signal`→Signal node, `journey_event`→Journey child. |
| POST | `/flush_session` | End session: promote fields to Journey node, DPDP delete if anonymous, close Redis/Memgraph session. |
| GET | `/sessions/{user_id}` | Active sessions for user, sorted by `last_accessed` DESC. |
| POST | `/audit/session` | Record session lifecycle event (start/end/escalate) in SQLite. |
| POST | `/audit/turn` | Record a single turn (user_msg, system_msg, subagent, intent, model, latency) in SQLite. |
| GET | `/audit/sessions/{session_id}/history` | Full turn history for a session from SQLite. |
| GET | `/users/{user_id}/active-history` | Most recent active session and its full turn history. |
| DELETE | `/user/{user_id}` | Right-to-erasure (DPDP) — delete all Memgraph nodes and Redis data for user. |
| GET | `/health` | Liveness probe. Returns `{"status": "ok"}`. |

---

## State scopes

### Redis — session state

Two key patterns:

- `session:{session_id}` — Hash, TTL-bound (default 1440 min / 24 h). All session schema fields stored as strings; lists and dicts stored as JSON-encoded strings. TTL is reset on every `write` and `context_bundle` call.
- `user:{user_id}` — Hash, TTL-bound. Fields: `{session_id: ISO-8601 last_accessed}`. Lazy cleanup of expired entries on `get_active_sessions`.

Types are coerced back on read by `_coerce_session_types()` using the session schema from config.

### Memgraph — user profile and journey graph

Nine node types:

| Node | Key fields |
|---|---|
| `User` | user_id, created_at |
| `UserProfile` | user_id + declared_fields from config |
| `UserAttribute` | key, value, raw, turn, journey_id (ad-hoc fields not in declared_fields) |
| `JourneyHistory` | user_id |
| `Journey` | journey_id (= session_id), started_at, ended_at, end_reason + merge_on_session_end fields |
| Journey child nodes | Labels defined in config (e.g. `Role`, `DropOff`) |
| `ContextGraph` | user_id |
| `Signal` | type, turn, raw, journey_id |
| `ContextAttribute` | key, value, raw, turn, journey_id |

Edge types: `HAS_PROFILE`, `HAS_JOURNEY_HISTORY`, `HAS_CONTEXT`, `JOURNEY`, `HAS_ATTRIBUTE`, `SIGNAL`, and domain-specific edges from config (e.g. `OFFERED`, `DROPPED_AT`).

### SQLite — audit log

`SQLiteAuditStore` serves two distinct purposes:

1. **Session lifecycle events** — records session start, end, and escalate events with `consent_given` flag for DPDP Act compliance (`session_audit` table).
2. **Raw conversation turn history** — records every turn's user message, agent response, subagent_id, intent, model, and latency_ms (`turn_audit` table). This is the conversation transcript log, used for DPDP audit, conversation replay, and retrieval via the history endpoints below.

Two tables:

- `session_audit` — session_id (PK), user_id, created_at, closed_at, status, end_reason, consent_given.
- `turn_audit` — turn_id (PK), session_id (FK), user_message, system_message, timestamp, subagent_id, intent, model, latency_ms, metadata (JSON).

This is distinct from the OTel pipeline (Loki/Jaeger), which handles structured observability telemetry — spans, metrics, and traces. The SQLite store holds the raw conversation transcript; the OTel pipeline holds instrumentation data. Both are needed and serve different purposes.

Accessed via `POST /audit/session`, `POST /audit/turn`, `GET /audit/sessions/{session_id}/history`, and `GET /users/{user_id}/active-history`.

All four `AuditStoreBase` abstract methods are fully implemented — no stubs, no `NotImplementedError`.

---

## Public interface

`MemoryLayer` exposes five methods. All other logic is internal.

### `context_bundle(session_id, user_id) -> dict`

Loads the full context for a turn. Returns `{session: dict, profile: dict, journey: dict|None}`.

- New user: creates User, UserProfile, and ContextGraph nodes in Memgraph.
- Existing returning user: pre-populates session from declared profile fields and last journey data.
- Hot path for existing sessions: reads from Redis.

### `write(session_id, user_id, scope, key, value) -> None`

Routes write to the correct store by scope:

| Scope | Destination |
|---|---|
| `session` | Redis session hash |
| `persistent` | Memgraph UserProfile or UserAttribute node |
| `signal` | Memgraph Signal node |
| `journey_event` | Memgraph Journey child node |

Special case: a `user_storage_mode` write also persists consent to SQLite for DPDP record-keeping.

### `flush_session(session_id, user_id, end_reason) -> None`

Closes a session in order:
1. Promote configured session fields to the Journey node (merge_on_session_end rules).
2. Close the Journey node (set ended_at, end_reason).
3. If user is anonymous: DPDP-delete all Memgraph nodes.
4. Delete Redis session key.
5. Write session-end event to SQLite audit log.

### `get_active_sessions(user_id) -> list[dict]`

Returns `[{session_id, last_accessed}, ...]` from the Redis user index, sorted by recency. Lazily removes expired entries.

### `delete_user(user_id) -> None`

DPDP right-to-erasure. Runs `DETACH DELETE` on all Memgraph nodes for the user and deletes the Redis user index.

---

## Configuration

| Key | Description |
|---|---|
| `redis.host` / `redis.port` / `redis.db` / `redis.password` | Redis connection |
| `memgraph.uri` / `memgraph.user` / `memgraph.password` | Memgraph Bolt connection |
| `state.session.ttl_minutes` | Session TTL (default: 1440 — 24 h) |
| `state.session.schema` | `{field_name: {type?, default}}` — defines session fields and their types for coercion |
| `state.persistent.graph.subnodes.UserProfile.declared_fields` | Named profile fields stored as UserProfile properties |
| `state.persistent.graph.subnodes.JourneyHistory.child.children[]` | Journey child node config — labels, edge types, field mappings |
| `state.persistent.merge_on_session_end[]` | `{session_field, target: "Journey.<prop>"}` — fields promoted to Journey on flush |
| `user_data_persistence.default_mode` | `"saved"` or `"anonymous"` |
| `audit.db_path` | Path to SQLite audit database |

---

## Running the service

Redis and Memgraph must be running before starting the Memory Layer. Both are included in the dev Docker Compose file:

```bash
cd automation/docker
docker compose -f docker-compose.dev.yml up -d
```

Then start the Memory Layer:

```bash
cd memory_layer
uv run uvicorn src.server:app --port 8002
```

---

## Running tests

All external dependencies are mocked. No running Redis or Memgraph is required.

```bash
cd memory_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
fastapi                                  >= 0.110
uvicorn[standard]                        >= 0.29
pydantic                                 >= 2.0
pyyaml                                   >= 6.0
redis                                    >= 4.0
neo4j                                    >= 5.0   # Bolt driver — works with Memgraph
python-dotenv                            >= 1.0.0
httpx                                    >= 0.28.1
observability-layer                      (local path)
opentelemetry-instrumentation-fastapi
```

Requires Python 3.11+.

---

## Production notes

### Extending the graph schema via config

No Python changes are needed to extend the graph model. All extensions are driven by `domain.yaml`:

**Add declared profile fields** — add field names under `state.persistent.graph.subnodes.UserProfile.declared_fields`. They become properties on the `UserProfile` node.

**Add journey child node types** — add entries under `state.persistent.graph.subnodes.JourneyHistory.child.children[]`. Each entry specifies the node label, the edge type from the Journey node, and the session fields to copy.

**Add session-to-journey promotion rules** — add entries to `state.persistent.merge_on_session_end[]` mapping a session field to a Journey node property. These run automatically on `flush_session`.

**Add new edge types** — edge labels are read from config at startup. New domain-specific edges (e.g. `OFFERED`, `APPLIED`) require only a config entry, not a code change.

**Adjust TTLs** — set `state.session.ttl_minutes` per deployment. The same TTL is applied to both `session:{id}` and `user:{id}` Redis keys.

---

## Known gaps

**SQLite audit log has no retention or cleanup policy.** The `turn_audit` and `session_audit` tables grow unboundedly. Periodic cleanup and cold storage migration are not yet implemented (#62). For production deployments, a scheduled job should archive or prune rows older than the configured `audit.retention_days`.

**Consent store is not shared across instances.** The Trust Layer's `ConsentStore` (SQLite, in-process) is separate from the Memory Layer. Consent granted in one Trust Layer instance is invisible to another instance. Multi-instance deployments require a shared consent store (Redis or PostgreSQL) to be wired across both services (#47).

**`BROADCAST` write scope not implemented.** The `write()` interface accepts `scope: "broadcast"` in the design but this scope is not handled — it is reserved for a future multi-session fan-out use case.

**Memgraph `KGExternal` market knowledge graph not implemented.** The design includes a `KGExternal` node type for cross-domain market data stored in Memgraph. This track is deferred to post-PoC.
