# Agent Core

The sole orchestrator and sole LLM caller in the AI Diffusion DPG framework. Stateless between turns. Supports both **synchronous** (`process_turn`) and **async streaming** (`stream_turn`) execution, with an optional **TurnAssembler** for multi-segment (voice / VAD / rapid-correction) input.

---

## What this service does

Agent Core is the central coordinator for every user turn. It is the only component that calls the Anthropic LLM and the only block that initiates calls to other DPG services. It runs a fixed 13-step sequence on every turn, enforces safety on both input and output, and returns the final response to the caller.

Two execution paths are available:

- **`POST /process_turn`** — synchronous; returns a single `TurnResult` JSON after the entire pipeline completes.
- **`POST /stream_turn`** — Server-Sent Events; yields `SignalEvent`s between pipeline stages, `SentenceEvent`s as the LLM streams, and a final `DoneEvent`.

For channels that deliver input as multiple segments (voice VAD, rapid typing corrections), a **TurnAssembler** can be injected to buffer segments and decide when to invoke the pipeline via a configurable policy stack (silence trigger, semantic completeness gate, max-wait ceiling). When TurnAssembler is enabled, three additional session endpoints are exposed.

All session state lives in the Memory Layer — any instance can handle any session.

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
│   ├── base.py                          # AgentCoreBase ABC — process_turn() + stream_turn()
│   ├── models.py                        # TurnInput, TurnResult, ContextBundle, NLUResult,
│   │                                    #   TrustCheckResult, ToolCall, ToolResult,
│   │                                    #   TurnEvent, RetrievalChunk,
│   │                                    #   SignalEvent, SentenceEvent, DoneEvent,
│   │                                    #   StreamEvent, SegmentInput
│   ├── exceptions.py                    # AgentCoreError, LLMCallError, TrustViolationError,
│   │                                    #   ToolExecutionError, ConsentRequiredError,
│   │                                    #   ConfigurationError, ToolUseRequested
│   ├── orchestrator.py                  # AgentCore — process_turn() + stream_turn()
│   ├── turn_assembler.py                # TurnAssemblerBase, TurnAssembler
│   ├── session.py                       # Session per-session lifecycle object
│   ├── turn.py                          # Turn per-turn lifecycle object, TurnStatus enum
│   ├── manager_agent.py                 # ManagerAgent — LLM → tool → LLM loop
│   ├── tool_registry.py                 # ToolRegistry — loads and routes tools at startup
│   ├── workflow_loader.py               # AgentWorkflowLoader — parses subagent graph
│   ├── interfaces/                      # Sync ABCs for all 6 downstream DPG block contracts
│   │   ├── memory_layer.py
│   │   ├── trust_layer.py
│   │   ├── knowledge_engine.py
│   │   ├── action_gateway.py
│   │   ├── reach_layer.py
│   │   ├── observability_layer.py
│   │   └── async_/                      # Async ABCs used by stream_turn()
│   │       ├── memory_layer.py          # AsyncMemoryLayerBase (8 methods)
│   │       ├── trust_layer.py           # AsyncTrustLayerBase  (6 methods)
│   │       ├── knowledge_engine.py      # AsyncKnowledgeEngineBase
│   │       ├── action_gateway.py        # AsyncActionGatewayBase
│   │       └── observability_layer.py   # AsyncObservabilityLayerBase
│   ├── chat_provider/
│   │   ├── base.py                      # ChatProviderBase, Capabilities, error types
│   │   ├── types.py                     # neutral Pydantic types (Message, ChatRequest, …)
│   │   ├── anthropic_provider.py        # AnthropicChatProvider — only file that imports `anthropic`
│   │   ├── openai_provider.py           # OpenAIChatProvider     — only file that imports `openai`
│   │   ├── metrics.py                   # provider-agnostic OTel instruments
│   │   └── __init__.py                  # public exports + build_chat_provider() factory
│   ├── preprocessing/
│   │   ├── language_normalisation.py
│   │   └── nlu_processor.py
│   ├── http_clients/                    # Sync HTTP adapters
│   │   ├── memory_layer.py
│   │   ├── trust_layer.py               # fail-closed on any error
│   │   ├── knowledge_engine.py
│   │   ├── action_gateway.py
│   │   ├── learning_client.py
│   │   └── async_/                      # Async HTTP adapters (httpx.AsyncClient)
│   │       ├── memory_layer.py
│   │       ├── trust_layer.py
│   │       ├── knowledge_engine.py
│   │       ├── action_gateway.py
│   │       └── observability_layer.py
│   └── servers/
│       ├── orchestration_server.py      # FastAPI:
│       │                                #   POST /process_turn            (sync)
│       │                                #   POST /stream_turn             (SSE)
│       │                                #   POST /sessions/{id}/input     (TurnAssembler)
│       │                                #   GET  /sessions/{id}/events    (TurnAssembler SSE)
│       │                                #   DELETE /sessions/{id}/active_turn (barge-in)
│       │                                #   GET  /health
│       └── llm_proxy_server.py          # POST /internal/llm/call
└── tests/                               # 457+ tests across 18 files, ≥70% coverage
    ├── test_orchestrator.py
    ├── test_manager_agent.py
    ├── test_chat_provider_anthropic.py
    ├── test_chat_provider_openai.py
    ├── test_chat_provider_base.py
    ├── test_chat_provider_factory.py
    ├── test_chat_provider_metrics.py
    ├── test_chat_provider_types.py
    ├── test_workflow_loader.py
    ├── test_tool_registry.py
    ├── test_nlu_processor.py
    ├── test_language_normalisation.py
    ├── test_http_clients.py
    ├── test_memory_http_client.py
    ├── test_orchestration_server.py
    ├── test_llm_proxy_server.py
    ├── test_models.py
    ├── test_main.py
    ├── test_stream_events.py            # SSE serialisation
    ├── test_stream_turn.py              # stream_turn() + _split_sentences()
    ├── test_stream_endpoint.py          # POST /stream_turn
    ├── test_turn_assembler.py           # policy stack, session buffer, end-to-end
    └── test_session_endpoints.py        # POST /sessions/{id}/input etc.
```

---

## Turn execution sequence

Both `process_turn()` and `stream_turn()` run the same 13-step sequence:

```
1.  Read session state          Memory Layer — loads ContextBundle for the session
2.  Trust check input           Trust Layer — block, escalate, or allow
3.  Language Normalisation      Internal LLM call (haiku model) — dialect, code-switching,
                                transliteration
4.  NLU Processor               Internal LLM call (haiku model) — intent, entities,
                                sentiment, confidence score
5.  Routing                     Deterministic — NLU result + session conditions select subagent
6.  Assemble constraints        Trust Layer.assemble_constraints if active_risks present
7.  Build system prompt         Subagent prompt + guardrail constraints + required disclosures
8.  LLM call #1                 ChatProviderBase — call() (sync) or stream() (streaming),
                                via the configured provider (anthropic or openai)
9.  Tool-use loop               ManagerAgent — if LLM returns tool_use: route via ToolRegistry;
                                knowledge_retrieval → KE; all other tools → Action Gateway;
                                append result, LLM call #2; bounded by max_tool_rounds
10. Trust check output          Trust Layer — mandatory; blocked sentences → fallback text
11. Return                      process_turn: TurnResult returned; stream_turn: DoneEvent yielded

── async (after response returned / DoneEvent yielded) ─────────────────────────────
12. Write memory                Memory Layer — persists updated ContextBundle
13. Emit turn event             Observability Layer — audit log, quality signals
```

**`stream_turn()` differences:**

- Uses async HTTP clients (`interfaces/async_/`, `http_clients/async_/`) for all external calls.
- Yields `SignalEvent(stage=..., status="start"|"complete")` before and after each pipeline step.
- Step 8 uses `llm.stream_call()` → incoming tokens are split into sentences on `.`, `?`, `!`, `।` (Devanagari danda), and `？` (fullwidth). Each complete sentence is run through Trust output check, then emitted as a `SentenceEvent`.
- Trust _block_ on a sentence → fallback text replaces that sentence (stream continues). Trust _infra failure_ → treat as "allow" and log (never block the stream on infra failure).
- `ToolUseRequested` mid-stream → `tool_start` / `tool_end` signal events, execute via Action Gateway, resume streaming.
- Final `DoneEvent` carries `was_escalated`, `was_tool_used`, `model_used`, `latency_ms`, `turn_id`, `turn_status` (`completed` / `interrupted` / `abandoned`).
- Steps 12–13 fire via `asyncio.create_task` _after_ `DoneEvent` is yielded.

**Hard rules (both paths):**

- Trust Layer runs on every input (step 2) and every output (step 10). Neither check is skippable.
- Steps 12–13 run after the response/DoneEvent and never add latency to the caller.
- Special subagents (`hitl`, `whatsapp_handoff`) bypass LLM inference.
- Routing is deterministic and config-driven — not LLM-driven.

---

## TurnAssembler (multi-segment input)

For channels that deliver input as multiple partial segments (voice VAD, rapid corrections, barge-in), `TurnAssembler` sits between the HTTP server and `AgentCore.stream_turn()`.

```
POST /sessions/{id}/input  ─►  TurnAssembler.add_segment()
                                    │
                                    ▼
                            Session.current_turn: Turn (segments, timers, queue, abort)
                                    │
                     ┌──────────────┼──────────────┐
                     │              │              │
              semantic_gate    silence_trigger   max_wait_ceiling
              (NLU confidence)  (resets on every  (absolute ceiling,
                                 new segment)     never resets)
                                    │
                                    ▼
                           agent_core.stream_turn()  ──►  Turn.event_queue
                                                                  │
                                                                  ▼
                                                    GET /sessions/{id}/events (SSE)
```

**State machine** (`TurnStatus`):  `WAITING → INVOKED → {COMPLETED, INTERRUPTED, ABANDONED}`

**Policy stack** — first to fire wins:

1. **Semantic completeness gate** — runs NLU on assembled text; if `confidence ≥ threshold` and intent is not `unknown`, invoke immediately.
2. **Silence trigger** — `asyncio.Task` started on first segment, reset (cancel + restart) on every subsequent `add_segment()`. Fires after `silence_ms`.
3. **Max-wait ceiling** — `asyncio.Task` started once on buffer creation, never reset. Fires after `max_wait_ms`.

If both the silence timer and the ceiling fire simultaneously, only the first to acquire the session-buffer lock wins the state transition.

**Barge-in / cancellation** — `DELETE /sessions/{id}/active_turn` cancels the in-flight `stream_turn()` task. The async memory-write task is also cancelled, so no partial writes land in the Memory Layer. `DoneEvent.turn_status` carries `"interrupted"` or `"abandoned"` for observability.

**Invocation path is in-process** — `TurnAssembler._invoke()` calls `agent_core.stream_turn()` directly as a Python method (no HTTP hop, no serialisation). `StreamEvent`s flow into `Turn.event_queue` (one queue per Turn; a cancelled Turn's queue is sealed and the subscriber rebinds to the new Turn's queue).

---

## HTTP API

The service runs on port **8000**.

### Core endpoints

#### `POST /process_turn` (sync)

Request:
```json
{
  "session_id": "sess-abc123",
  "user_message": "electrician ka kaam kahan milega?",
  "channel": "cli",
  "timestamp_ms": 1700000000000,
  "user_id": "u-optional"
}
```

Response:
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

#### `POST /stream_turn` (SSE)

Same request body as `/process_turn`. Response is `text/event-stream`; one `data: <json>\n\n` event per pipeline signal / sentence, ending with a `DoneEvent`.

```
data: {"type":"signal","stage":"memory_read","status":"start"}
data: {"type":"signal","stage":"memory_read","status":"complete"}
...
data: {"type":"sentence","text":"Hubli mein electrician ke liye salary...","sentence_index":0}
data: {"type":"sentence","text":"Aap ko aur details chahiye?","sentence_index":1}
data: {"type":"done","turn_status":"completed","was_escalated":false,"was_tool_used":true,
       "model_used":"claude-haiku-4-5","latency_ms":1102,"turn_id":"turn-..."}
```

On unhandled exception → a terminal `DoneEvent(turn_status="abandoned")` is emitted before the stream closes.

### Session endpoints (registered only when TurnAssembler is provided)

#### `POST /sessions/{session_id}/input`

Submit one segment. Returns **202 Accepted** immediately. Returns 422 if text is empty.

```json
{
  "text": "electrician ka kaam",
  "channel": "voice",
  "user_id": "u-optional"
}
```

#### `GET /sessions/{session_id}/events` (SSE)

Long-lived subscription; yields each `StreamEvent` from the session buffer. The connection is multi-turn — after a `DoneEvent` the buffer resets to `WAITING` and the same connection continues serving subsequent turns.

#### `DELETE /sessions/{session_id}/active_turn`

Barge-in — interrupts the active turn. Returns 200 if the session existed, 404 otherwise.

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /internal/llm/call`

LLM proxy endpoint (implemented, not yet wired).

---

## Key components

**`orchestrator.py` — AgentCore**
Implements both `process_turn()` (sync) and `stream_turn()` (async generator). Runs the 13-step sequence. Holds no session state. All dependencies are injected at construction, including the async HTTP clients used by `stream_turn()`. `_split_sentences()` is a small utility that splits LLM tokens into sentence boundaries (supports Devanagari and fullwidth punctuation).

**`turn_assembler.py` — TurnAssembler**
Buffers multi-segment input and decides when to invoke `stream_turn()`. Holds `_sessions: dict[str, Session]` in memory; each Session owns the current Turn. Constructor takes optional `nlu_processor`, `workflow`, `async_memory` — if absent, the semantic gate is effectively disabled.

**`manager_agent.py` — ManagerAgent**
LLM → tool → LLM loop. Both sync and async variants. Used by `process_turn()` for synchronous tool rounds; `stream_turn()` handles tool use via the `ToolUseRequested` exception raised from `provider.stream()`.

**`chat_provider/` — multi-provider LLM interface**
`ChatProviderBase` is the only LLM type the rest of agent_core depends on. `build_chat_provider(agent_config)` selects a concrete provider from `agent.provider` (anthropic or openai today; AzureOpenAI/Ollama as follow-ups). Each provider lives in its own file and is the sole importer of its SDK. `call()` is sync; `stream()` yields text deltas and raises `ToolUseRequested(list[ToolUseBlock])` when the model emits tool calls — the caller executes the tools and resumes. Capabilities (prompt cache, image input, structured output, etc.) are declared per provider class and reconciled against deployment YAML at startup; mismatches fail loud with `ProviderConfigError`.

**`preprocessing/language_normalisation.py` — LanguageNormaliser**
Runs before NLU. Detects dialect, normalises code-switching (Hindi/Kannada/English), and transliterates Romanised Indic text. Currently only the `internal` provider (LLM-based normalisation via a haiku model) is implemented.

**`preprocessing/nlu_processor.py` — NLUProcessor**
Classifies intent, extracts entities, produces confidence score. Low-confidence → clarification response without a second LLM call. Also used by TurnAssembler's semantic gate.

**`tool_registry.py` — ToolRegistry**
Loads tool definitions from config at startup and routes tool calls by name. Tracks which tools require consent (`write` and `identity` connector types).

**`workflow_loader.py` — AgentWorkflowLoader**
Parses the subagent graph from `agent_workflow` config. Runs 7 structural validation checks at startup.

**`http_clients/trust_layer.py` — TrustLayerHttpClient**
Fail-closed: returns `"block"` / `False` on any exception. Both sync and async variants.

**`interfaces/` and `interfaces/async_/`**
ABCs defining the contracts Agent Core expects from each of the 6 other DPG blocks. Stub and production implementations must inherit from these and match exact signatures. Sync interfaces are used by `process_turn()`; async interfaces (`interfaces/async_/`) are used by `stream_turn()`.

---

## Configuration

Config is loaded at startup from two YAML files: `config/dpg.yaml` (framework defaults) deep-merged with `config/domain.yaml` (domain values). Nothing is hardcoded in source.

### Agent, conversation, and connectors

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
| `connectors.read[]` / `write[]` / `identity[]` / `internal[]` | Tool definitions |

### Preprocessing

| Key | Description |
|---|---|
| `preprocessing.language_normalisation.model` / `provider` / `supported_languages` | Dialect/transliteration config |
| `preprocessing.nlu_processor.model` / `confidence_threshold` / `intents` / `entities` | NLU config |

### Agent workflow (subagents)

| Key | Description |
|---|---|
| `agent_workflow.workflow_id` | Workflow identifier |
| `agent_workflow.agent_system_prompt` | Base system prompt |
| `agent_workflow.global_intents` | Intents handled at the global level |
| `agent_workflow.subagents[]` | Subagent definitions with intent scopes and tool lists |

### Reach Layer / TurnAssembler (new)

TurnAssembler is an Agent Core component but is tuned per channel. Config lives under the `reach_layer` key in `agent_core.yaml`.

| Key | Description |
|---|---|
| `reach_layer.turn_assembler.semantic_gate.enabled` | Enable NLU-based early trigger |
| `reach_layer.turn_assembler.semantic_gate.confidence_threshold` | Invoke immediately if NLU ≥ this value |
| `reach_layer.turn_assembler.silence_trigger.silence_ms` | Silence timer (resets on every segment) |
| `reach_layer.turn_assembler.max_wait_ceiling.max_wait_ms` | Absolute wait ceiling (never resets) |
| `reach_layer.channels.<name>.turn_assembler.*` | Per-channel override of any of the above |

`assembly_mode` (which endpoint a channel hits) is a Reach Layer concern and lives in `reach_layer.yaml`, not here.

---

## Running the service

```bash
cd agent_core
uv run uvicorn src.servers.orchestration_server:app --port 8000
```

Requires `ANTHROPIC_API_KEY` to be set in the environment.

To enable the TurnAssembler session endpoints, construct the FastAPI app via `create_orchestration_app(agent_core, turn_assembler=<instance>)`. When `turn_assembler=None` (default), only `/process_turn`, `/stream_turn`, and `/health` are registered — zero breaking changes for deployments that don't use session-based input.

---

## Running tests

```bash
cd agent_core
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

457+ tests across 18 files. Coverage threshold: 70% (currently ~75%). `turn_assembler.py` is covered at 96%.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `anthropic` | >=0.40.0 | Anthropic SDK — used only in `chat_provider/anthropic_provider.py` |
| `openai` | >=1.50.0 | OpenAI SDK — used only in `chat_provider/openai_provider.py` |
| `httpx` | >=0.27.0 | HTTP clients (sync + async) for all downstream DPG services |
| `pydantic` | >=2.0 | Request/response models |
| `pyyaml` | >=6.0 | Config loading |
| `fastapi` | >=0.111.0 | HTTP server (sync + SSE endpoints) |
| `uvicorn[standard]` | >=0.29.0 | ASGI server |
| `python-dotenv` | >=1.0.0 | Environment variable loading |
| `observability-layer` | local path | OTel initialisation shared library |
| `opentelemetry-instrumentation-httpx` | — | HTTP client tracing |
| `opentelemetry-instrumentation-fastapi` | — | FastAPI request tracing |

Dev extras: `pytest`, `pytest-cov`, `pytest-mock`, `pytest-asyncio>=0.23.0` (with `asyncio_mode = "auto"`).

Requires Python 3.11+.

---

## Integration contract

Agent Core expects implementations of the 6 sync interfaces in `src/interfaces/` and — for `stream_turn()` callers — the 5 async interfaces in `src/interfaces/async_/`. Any concrete implementation must:

- Inherit from the corresponding ABC
- Implement every declared method with the exact signature
- Return the correct type and structure documented on the base class

See `CLAUDE.md` and `ARCHITECTURE.md` in the repository root for full engineering standards and block responsibilities.

---

## Known gaps

**Anthropic and OpenAI providers are implemented (#287).** AzureOpenAI and Ollama are planned follow-ups; they slot into `chat_provider/` without changing the orchestration layer.

**`POST /internal/llm/call` proxy is not yet wired to downstream callers.** The endpoint is implemented and registered, but no other DPG block calls it. The intended architecture routes all LLM calls from other blocks through this proxy; current state has only Agent Core calling the configured provider's SDK directly.

**HiTL output escalation path deferred.** When `POST /check/output` returns `action: "escalate"`, Agent Core does not call `/escalate` — this path is not wired. Blocked sentences in the stream are replaced with fallback text only.

**Channel-aware prompt assembly not yet implemented.** All channels (voice, web, CLI) receive the same system prompt regardless of channel. A future optimisation should shorten prompts for voice channels, which have tighter latency budgets and no Markdown rendering (#97).

**NLU mode switching not yet implemented.** The `workflow_step`-based NLU mode switching described in issue #4 (conditional NLU execution) is not yet built. NLU runs on every turn regardless of workflow step.
