# Observability Layer DPG

Asynchronous observability layer. Runs entirely out-of-band — never in the response path.

---

## What this service does

The Observability Layer receives turn metadata and feedback signals from Agent Core after each response is delivered. It logs these events for audit, quality evaluation, and future model improvement.

**Critical constraint:** Agent Core calls this layer asynchronously in a daemon thread, after the user response has already been returned. The Observability Layer must never be in the response path — a slow or unavailable Observability Layer must not affect turn latency.

For the PoC, this is `ConsoleLogger`: structured JSON output to stdout. The interface is identical to what a production observability pipeline (e.g. sending events to a data warehouse, LLM evaluation service, or feedback store) would implement.

---

## Folder structure

```
observability_layer/
├── main.py                 # Uvicorn entrypoint (port 8004)
├── pyproject.toml
├── config/
│   └── config.yaml         # Log level
├── src/
│   ├── console_logger.py   # ConsoleLogger — ObservabilityLayerBase implementation
│   └── server.py           # FastAPI app (all endpoints)
└── tests/
    ├── test_console_logger.py
    └── test_server.py
```

---

## HTTP API

The service runs on port **8004**.

### `POST /emit/turn`

Records a complete turn event — called once per turn, after the response has been returned to the user.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "response_text": "Hubli mein electrician ke liye salary ₹15,000–₹28,000/month hai.",
  "tool_calls": ["onest_market_lookup"],
  "trust_input_result": { "passed": true, "action": "allow", "reason": null },
  "trust_output_result": { "passed": true, "action": "allow", "reason": null },
  "model_used": "claude-haiku-4-5-20251001",
  "input_tokens": 342,
  "output_tokens": 87,
  "latency_ms": 1243,
  "timestamp_ms": 1700000000000
}
```

**Response:** `{ "status": "ok" }`

The PoC implementation logs this as a structured JSON line to stdout with `operation: emit_turn`.

### `POST /emit/signal`

Records an explicit or implicit feedback signal — called when the user gives a thumbs up/down, or when implicit signals (e.g. re-ask, escalation request) are detected.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "signal_type": "thumbs_up",
  "turn_reference": "sess-abc123:3",
  "metadata": {}
}
```

Signal types: `thumbs_up`, `thumbs_down`, `re_ask`, `escalation_requested`, `task_completed`

**Response:** `{ "status": "ok" }`

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

---

## What gets logged (PoC)

The `ConsoleLogger` emits structured log entries for every event:

**Turn event log entry:**
```json
{
  "operation": "emit_turn",
  "status": "success",
  "session_id": "sess-abc123",
  "model_used": "claude-haiku-4-5-20251001",
  "input_tokens": 342,
  "output_tokens": 87,
  "latency_ms": 1243,
  "tool_calls": ["onest_market_lookup"],
  "trust_input_passed": true,
  "trust_output_passed": true,
  "timestamp_ms": 1700000000000
}
```

**Signal event log entry:**
```json
{
  "operation": "emit_signal",
  "status": "success",
  "session_id": "sess-abc123",
  "signal_type": "thumbs_up",
  "turn_reference": "sess-abc123:3"
}
```

---

## Configuration

| Key | Description |
|---|---|
| `server.port` | HTTP port (default: 8004) |
| `learning.log_level` | Python logging level (`INFO`, `DEBUG`, etc.) |

---

## Running the service

```bash
source ../.venv/bin/activate
cd observability_layer
uvicorn src.server:app --port 8004
```

---

## Running tests

```bash
source ../.venv/bin/activate
cd observability_layer
pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected: 34 tests, ≥96% line coverage.

---

## Dependencies

```
fastapi   >= 0.110
uvicorn   >= 0.29
pydantic  >= 2.0
pyyaml    >= 6.0
```

Requires Python 3.11+.

---

## Replacing the stub

To replace `ConsoleLogger` with a production observability pipeline:

1. Create a class that inherits from `ObservabilityLayerBase` (defined in `agent_core/src/interfaces/observability_layer.py`).
2. Implement `emit_turn` and `emit_signal` with identical signatures.
3. Wire the new class into `src/server.py` — no other files need to change.

The real implementation might forward events to BigQuery, Langfuse, or a custom evaluation service. The async contract (called after response delivery) remains unchanged regardless of the backend.
