# Agent Core DPG

The central orchestration and LLM execution layer of the AI Composition Framework.

---

## What this service does

Agent Core is the **only component that calls the LLM** and the **only orchestrator** in the framework. Every user message passes through it. It coordinates all other DPG blocks in a fixed sequence, enforces safety on every turn, and returns the final response.

It is stateless between turns — all session state lives in the Memory Layer. Any instance can handle any session, enabling horizontal scaling with no coordination.

---

## Folder structure

```
agent_core/
├── src/                        # Python package (import as `src.X`)
│   ├── models.py               # Shared dataclasses (TurnInput, TurnResult, etc.)
│   ├── exceptions.py           # Typed exceptions for all failure modes
│   ├── base.py                 # AgentCoreBase ABC
│   ├── orchestrator.py         # AgentCore — main entry point (process_turn)
│   ├── manager_agent.py        # LLM → tool → LLM loop handler
│   ├── tool_registry.py        # Tool definition loader and consent tracker
│   ├── interfaces/             # ABCs for the 6 other DPG blocks
│   │   ├── memory_layer.py
│   │   ├── trust_layer.py
│   │   ├── knowledge_engine.py
│   │   ├── action_gateway.py
│   │   ├── reach_layer.py
│   │   └── observability_layer.py
│   ├── llm_wrapper/            # LLM inferencing
│   │   ├── base.py             # LLMWrapperBase ABC
│   │   └── claude_wrapper.py   # Anthropic SDK implementation (only file that imports anthropic)
│   ├── preprocessing/          # Language normalisation and NLU
│   │   ├── language_normaliser.py   # Dialect detection, code-switching, transliteration
│   │   └── nlu_processor.py         # Intent classification, entity extraction, sentiment
│   ├── http_clients/           # HTTP client adapters for all 5 downstream DPG services
│   │   ├── memory_client.py
│   │   ├── trust_client.py
│   │   ├── knowledge_engine_client.py
│   │   ├── action_gateway_client.py
│   │   └── learning_client.py
│   └── servers/                # FastAPI HTTP server
│       └── orchestration_server.py  # POST /process_turn, GET /health, POST /internal/llm/call
├── tests/                      # Unit tests (177 tests, 90% coverage)
│   ├── test_llm_wrapper.py
│   ├── test_tool_registry.py
│   ├── test_manager_agent.py
│   ├── test_orchestrator.py
│   ├── test_http_clients.py
│   └── test_server.py
├── config/
│   └── config.yaml             # Service-level config (models, timeouts, messages)
└── pyproject.toml              # Package definition and dependencies
```

---

## Turn execution sequence

Every call to `process_turn()` runs this fixed sequence:

```
1. Read session state          (Memory Layer)
2. Safety check on input       (Trust Layer) → block or escalate if needed
3. Language Normalisation      (internal — dialect, code-switching, transliteration)
4. NLU Processor               (internal — intent, entities, sentiment)
5. Assemble prompt             (Knowledge Engine — receives NLU results in request body)
6. LLM call #1                 (LLM Wrapper)
7. Tool-use loop               (Manager Agent + Action Gateway)  ← only if LLM requests a tool
8. Safety check on output      (Trust Layer) → replace with fallback if blocked
9. Return response to caller
── async (daemon thread) ──────────────────────────────────────────────────────
10. Write updated session state (Memory Layer)
11. Emit turn event             (Observability Layer)
```

**Hard rules:**
- Trust Layer runs on **every** input and **every** output — neither check is skippable.
- Steps 10–11 run after the response is returned and never delay the caller.

---

## HTTP API

The service runs on port **8000**.

### `POST /process_turn`

Main entry point for all user messages.

**Request body:**
```json
{
  "session_id": "sess-abc123",
  "user_message": "electrician ka kaam kahan milega?",
  "channel": "cli",
  "timestamp_ms": 1700000000000
}
```

**Response:**
```json
{
  "session_id": "sess-abc123",
  "response_text": "Hubli mein electrician ke liye salary ₹15,000–₹28,000/month hai.",
  "was_escalated": false,
  "was_tool_used": true,
  "model_used": "claude-haiku-4-5-20251001",
  "latency_ms": 1243
}
```

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

### `POST /internal/llm/call`

Internal proxy endpoint — allows future DPG services to get LLM access without holding their own Anthropic API key. Currently implemented but not wired into any other service.

---

## Key components

### `orchestrator.py` — AgentCore
The main entry point. Implements `process_turn(TurnInput) → TurnResult`.
Holds zero session state. All dependencies are injected at construction.

### `manager_agent.py` — ManagerAgent
Drives the LLM → tool → LLM loop. If the LLM returns a `tool_use` block, it checks consent, calls the Action Gateway, appends the result, and makes a second LLM call. Bounded by `max_tool_rounds` from config.

### `llm_wrapper/claude_wrapper.py` — ClaudeLLMWrapper
The only file in the entire codebase that imports the `anthropic` SDK.
- Retries on `RateLimitError` and `APITimeoutError` with exponential backoff
- Switches to fallback model after primary exhausts all retries
- Non-retryable errors (`APIError`) fail immediately without triggering fallback

### `preprocessing/language_normaliser.py` — LanguageNormaliser
Runs before KE on every turn. Detects dialect, normalises code-switching (Hindi/Kannada/English mixed input), and transliterates Romanised Indic text. Uses the LLM (haiku model) via the configured preprocessing prompt.

### `preprocessing/nlu_processor.py` — NLUProcessor
Runs after Language Normalisation. Classifies intent from the configured intent list, extracts entities (trade, location, scheme), and provides a confidence score. Low-confidence results trigger an early clarification response without calling the LLM again.

### `tool_registry.py` — ToolRegistry
Loads tool definitions from the Action Gateway at startup. Tracks which tools require user consent (`write` and `identity` connector types).

### `interfaces/`
Abstract base classes defining the contracts Agent Core expects from each of the other 6 DPG blocks. Stub and production implementations must inherit from these.

---

## Configuration

Service config lives in `config/config.yaml`. Nothing is hardcoded in source.

| Key | Description |
|---|---|
| `agent.primary_model` | Claude model ID for all LLM calls |
| `agent.fallback_model` | Model used after primary exhausts retries |
| `agent.timeout_ms` | Per-request timeout (default: 10000ms) |
| `agent.retry_attempts` | Retries on transient failures before fallback (default: 2) |
| `agent.max_tool_rounds` | Max tool → LLM cycles per turn (default: 1) |
| `conversation.blocked_message` | Returned when input is blocked by Trust Layer |
| `conversation.escalation_message` | Returned when input triggers escalation |
| `conversation.output_blocked_message` | Returned when LLM output is blocked |
| `connectors.read/write/identity` | Tool definitions injected into the LLM |
| `preprocessing.language_normalisation.*` | Model and prompt for dialect/transliteration |
| `preprocessing.nlu.*` | Intent list, entity types, confidence threshold |

---

## Running the service

```bash
# From the repo root, activate the shared virtual environment
source .venv/bin/activate

# Start Agent Core (port 8000)
cd agent_core
uvicorn src.servers.orchestration_server:app --port 8000
```

---

## Running tests

From the `agent_core/` directory:

```bash
# Activate the shared virtual environment
source ../.venv/bin/activate

# Run all tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=src --cov-report=term-missing
```

Expected: 177 tests, 90% line coverage.

---

## Dependencies

```
anthropic >= 0.40.0    # LLM API — used only in src/llm_wrapper/claude_wrapper.py
httpx    >= 0.27.0     # HTTP clients for all downstream DPG services
pyyaml   >= 6.0        # Config loading from config/config.yaml
fastapi  >= 0.110      # HTTP server
uvicorn  >= 0.29       # ASGI server
pydantic >= 2.0        # Request/response models
```

Dev: `pytest`, `pytest-cov`, `pytest-mock`

Requires Python 3.11+.

---

## Integration contract

Agent Core expects implementations of the 6 interfaces in `src/interfaces/`. For the PoC these are lightweight stubs. Any concrete implementation must:

- Inherit from the corresponding ABC in `src/interfaces/`
- Implement every method with the exact signature defined
- Return the correct type and structure documented on the base class

See `CLAUDE.md` in the repository root for full engineering standards.
