# Agent Core

The sole orchestrator and sole LLM caller in the AI Diffusion DPG framework. Stateless between turns.

---

## What this service does

Agent Core is the central coordinator for every user turn. It is the only component that calls the Anthropic LLM and the only block that initiates calls to other DPG services. It runs a fixed 13-step sequence on every turn, enforces safety on both input and output, and returns the final response to the caller. All session state lives in the Memory Layer — any instance can handle any session.

---

## Folder structure

```
agent_core/
├── main.py
├── pyproject.toml
├── config/
│   ├── dpg.yaml          # Framework defaults (server, timeouts, endpoints)
│   └── domain.yaml       # Domain config template (models, intents, connectors, workflow)
├── src/
│   ├── base.py                          # AgentCoreBase ABC
│   ├── models.py                        # TurnInput, TurnResult, ContextBundle, NLUResult,
│   │                                    #   TrustCheckResult, ToolCall, ToolResult,
│   │                                    #   LLMResponse, TurnEvent, RetrievalChunk
│   ├── exceptions.py                    # AgentCoreError, LLMCallError, TrustViolationError,
│   │                                    #   ToolExecutionError, ConsentRequiredError,
│   │                                    #   ConfigurationError
│   ├── orchestrator.py                  # AgentCore — 13-step turn processor
│   ├── manager_agent.py                 # ManagerAgent — LLM → tool → LLM loop
│   ├── tool_registry.py                 # ToolRegistry — loads and routes tools at startup
│   ├── workflow_loader.py               # AgentWorkflowLoader — parses subagent graph from
│   │                                    #   config; runs 7 structural validation checks
│   ├── interfaces/                      # ABCs for all 6 downstream DPG block contracts
│   │   ├── memory_layer.py
│   │   ├── trust_layer.py
│   │   ├── knowledge_engine.py
│   │   ├── action_gateway.py
│   │   ├── reach_layer.py
│   │   └── observability_layer.py
│   ├── llm_wrapper/
│   │   ├── base.py                      # LLMWrapperBase ABC
│   │   └── claude_wrapper.py            # Only file that imports the anthropic SDK
│   ├── preprocessing/
│   │   ├── language_normalisation.py    # Dialect detection, code-switching, transliteration
│   │   └── nlu_processor.py             # Intent classification, entity extraction, sentiment,
│   │                                    #   confidence scoring
│   ├── http_clients/                    # HTTP adapters for all downstream blocks
│   │   ├── memory_layer.py              # MemoryLayerHttpClient
│   │   ├── trust_layer.py               # TrustLayerHttpClient — fail-closed on any error
│   │   ├── knowledge_engine.py          # HttpKnowledgeEngineClient
│   │   ├── action_gateway.py            # ActionGatewayHttpClient
│   │   └── learning_client.py           # ObservabilityLayerHttpClient
│   └── servers/
│       ├── orchestration_server.py      # FastAPI: POST /process_turn, GET /health
│       └── llm_proxy_server.py          # POST /internal/llm/call (implemented, not yet
│                                        #   wired to other blocks)
└── tests/                               # 14 test files, 414 test functions
    ├── test_orchestrator.py
    ├── test_manager_agent.py
    ├── test_llm_wrapper.py
    ├── test_workflow_loader.py
    ├── test_tool_registry.py
    ├── test_nlu_processor.py
    ├── test_language_normalisation.py
    ├── test_http_clients.py
    ├── test_memory_http_client.py
    ├── test_orchestration_server.py
    ├── test_llm_proxy_server.py
    ├── test_models.py
    └── test_main.py
```

---

## Turn execution sequence

Every call to `process_turn()` runs this fixed 13-step sequence:

```
1.  Read session state          Memory Layer — loads ContextBundle for the session
2.  Trust check input           Trust Layer — block, escalate, or allow
3.  Language Normalisation      Internal LLM call (haiku model) — dialect, code-switching,
                                transliteration
4.  NLU Processor               Internal LLM call (haiku model) — intent, entities, sentiment,
                                confidence score
5.  Routing                     Deterministic — NLU result + session conditions select subagent
6.  Assemble constraints        Trust Layer.assemble_constraints if active_risks are present
7.  Build system prompt         Subagent prompt + guardrail constraints + required disclosures
8.  LLM call #1                 ClaudeLLMWrapper — primary model with retry and fallback
9.  Tool-use loop               ManagerAgent — if LLM returns tool_use: route via ToolRegistry;
                                knowledge_retrieval → KE (_execute_knowledge_retrieval);
                                all other tools → Action Gateway; append result, LLM call #2;
                                bounded by max_tool_rounds; KE only called when subagent tool
                                list includes knowledge_retrieval
10. Trust check output          Trust Layer — mandatory; replaces response with fallback if blocked
11. Return TurnResult           Response delivered to caller; steps 12–13 run asynchronously

── async (after response is returned) ──────────────────────────────────────────
12. Write memory                Memory Layer — persists updated ContextBundle
13. Emit turn event             Observability Layer — audit log, quality signals
```

**Hard rules:**
- Trust Layer runs on every input (step 2) and every output (step 10). Neither check is skippable.
- Steps 12–13 run after the response is returned and never add latency to the caller.
- Special subagents (`hitl`, `whatsapp_handoff`) bypass LLM inference.
- Routing is deterministic and config-driven — not LLM-driven.

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
  "timestamp_ms": 1700000000000,
  "user_id": "u-optional"
}
```

`channel` defaults to `"cli"`. `user_id` is optional and used for Memory Layer lookups.

**Response:**
```json
{
  "session_id": "sess-abc123",
  "response_text": "Hubli mein electrician ke liye salary Rs. 15,000–28,000/month hai.",
  "was_escalated": false,
  "was_tool_used": true,
  "model_used": "claude-haiku-4-5",
  "latency_ms": 1102
}
```

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

### `POST /internal/llm/call`

LLM proxy endpoint — implemented but not yet wired to other blocks.

**Request body:**
```json
{
  "messages": [...],
  "tools": [...],
  "system": "...",
  "model_override": "claude-haiku-4-5"
}
```

**Response:**
```json
{
  "content": "...",
  "tool_calls": [],
  "stop_reason": "end_turn",
  "model_used": "claude-haiku-4-5",
  "input_tokens": 312,
  "output_tokens": 88
}
```

---

## Key components

**`orchestrator.py` — AgentCore**
Implements `process_turn(TurnInput) -> TurnResult`. Runs the 13-step sequence. Holds no session state. All dependencies are injected at construction.

**`manager_agent.py` — ManagerAgent**
Drives the LLM → tool → LLM loop. When the LLM returns a `tool_use` block, checks consent, calls the Action Gateway, appends the result as `tool_result`, and issues a second LLM call. Bounded by `max_tool_rounds`.

**`llm_wrapper/claude_wrapper.py` — ClaudeLLMWrapper**
The only file in the codebase that imports the `anthropic` SDK. Retries `RateLimitError` and `APITimeoutError` with exponential backoff. Switches to the fallback model after the primary exhausts all retries. Non-retryable errors fail immediately.

**`preprocessing/language_normalisation.py` — LanguageNormaliser**
Runs before NLU on every turn. Detects dialect, normalises code-switching (Hindi/Kannada/English mixed input), and transliterates Romanised Indic text. Uses the configured haiku model via an internal LLM call. The `bhashini` provider raises `NotImplementedError` — not yet implemented.

**`preprocessing/nlu_processor.py` — NLUProcessor**
Runs after Language Normalisation. Classifies intent from the configured intent list, extracts entities, and produces a confidence score. Low-confidence results trigger a clarification response without a second LLM call.

**`tool_registry.py` — ToolRegistry**
Loads tool definitions from config at startup and routes tool calls by name. Tracks which tools require consent (`write` and `identity` connector types). Each subagent receives only its scoped tool list.

**`workflow_loader.py` — AgentWorkflowLoader**
Parses the subagent graph from `agent_workflow` config. Runs 7 structural validation checks at startup. Pre-computes intent sets and tool definitions per subagent.

**`http_clients/trust_layer.py` — TrustLayerHttpClient**
Fail-closed: returns `"block"` / `False` on any exception. Trust is never assumed on error.

**`interfaces/`**
ABCs defining the contracts Agent Core expects from each of the 6 other DPG blocks. All stub and production implementations must inherit from these and match exact method signatures.

---

## Configuration

Config is loaded at startup from two YAML files: `config/dpg.yaml` (framework defaults) deep-merged with `config/domain.yaml` (domain values). Nothing is hardcoded in source.

| Key | Description |
|---|---|
| `agent.primary_model` | Claude model ID for main LLM calls |
| `agent.fallback_model` | Model used after primary exhausts retries |
| `agent.timeout_ms` | Per-request timeout in milliseconds |
| `agent.retry_attempts` | Retries on transient failures before fallback |
| `agent.max_tool_rounds` | Max tool → LLM cycles per turn |
| `agent.ask_for_consent` | Whether to gate write/identity connectors on user consent |
| `conversation.blocked_message` | Returned when input is blocked by Trust Layer |
| `conversation.escalation_message` | Returned when input triggers escalation |
| `conversation.output_blocked_message` | Returned when LLM output is blocked |
| `conversation.unknown_intent_message` | Returned on low-confidence NLU result |
| `connectors.read[]` | Read-only tool definitions injected into the LLM |
| `connectors.write[]` | Write tool definitions (require consent) |
| `connectors.identity[]` | Identity tool definitions (require consent) |
| `connectors.internal[]` | Internal tool definitions |
| `preprocessing.language_normalisation.model` | Model for dialect/transliteration calls |
| `preprocessing.language_normalisation.provider` | `llm_native` or `bhashini` |
| `preprocessing.language_normalisation.supported_languages` | Languages the normaliser handles |
| `preprocessing.nlu_processor.model` | Model for NLU classification calls |
| `preprocessing.nlu_processor.confidence_threshold` | Minimum confidence before early exit |
| `preprocessing.nlu_processor.intents` | Recognised intent list |
| `preprocessing.nlu_processor.entities` | Entity types to extract |
| `agent_workflow.workflow_id` | Workflow identifier |
| `agent_workflow.agent_system_prompt` | Base system prompt for the agent |
| `agent_workflow.global_intents` | Intents handled at the global level |
| `agent_workflow.subagents[]` | Subagent definitions with intent scopes and tool lists |

---

## Running the service

```bash
cd agent_core
uv run uvicorn src.servers.orchestration_server:app --port 8000
```

Requires `ANTHROPIC_API_KEY` to be set in the environment.

---

## Running tests

```bash
cd agent_core
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

414 tests across 14 files. Coverage threshold: 70% (enforced via `fail_under`).

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `anthropic` | >=0.40.0 | Anthropic SDK — used only in `llm_wrapper/claude_wrapper.py` |
| `httpx` | >=0.27.0 | HTTP clients for all downstream DPG services |
| `pydantic` | >=2.0 | Request/response models |
| `pyyaml` | >=6.0 | Config loading |
| `fastapi` | >=0.111.0 | HTTP server |
| `uvicorn[standard]` | >=0.29.0 | ASGI server |
| `python-dotenv` | >=1.0.0 | Environment variable loading |
| `observability-layer` | local path | OTel initialisation shared library |
| `opentelemetry-instrumentation-httpx` | — | HTTP client tracing |
| `opentelemetry-instrumentation-fastapi` | — | FastAPI request tracing |

Requires Python 3.11+.

Dev extras: `pytest`, `pytest-cov`, `pytest-mock`.

---

## Integration contract

Agent Core expects implementations of the 6 interfaces in `src/interfaces/`. For the PoC these are backed by HTTP stubs. Any concrete implementation must:

- Inherit from the corresponding ABC in `src/interfaces/`
- Implement every declared method with the exact signature
- Return the correct type and structure documented on the base class

See `CLAUDE.md` and `ARCHITECTURE.md` in the repository root for full engineering standards and block responsibilities.
