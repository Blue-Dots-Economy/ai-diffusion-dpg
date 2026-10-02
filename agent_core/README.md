# Agent Core

The turn-time orchestrator and sole LLM caller in the AI Diffusion DPG framework. Stateless between turns. Supports both **synchronous** (`process_turn`) and **async streaming** (`stream_turn`) execution, with an optional **TurnAssembler** for multi-segment (voice VAD, rapid-correction) input. System-prompt assembly lives here in `manager_agent.build_system_prompt()` — Knowledge Engine returns ranked chunks; Agent Core decides how they enter the prompt.

---

## What this service does

Agent Core is the central coordinator for every user turn. It is the only component that calls the LLM and the only block that orchestrates the per-turn pipeline. Other blocks may make scoped, approved direct calls outside the turn pipeline (Reach → Memory for session-restore, Reach → KE for document upload, planned Action Gateway → KE/Memory caching) — see `ARCHITECTURE.md` §5. Agent Core runs a fixed 13-step sequence on every turn, enforces safety on both input and output, and returns the final response to the caller.

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
│   └── domain.yaml       # Domain config template (models, NLU slots/act_intents, connectors, workflow)
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
│   │   └── language_normalisation.py
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
└── tests/                               # 818 tests across 32 files, ≥70% coverage
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

## Output contract and guard

Every channel can declare an `output_contract` (`channels.<name>.output_contract`): per language, the script, whether numbers are written in words, and a short list of spoken-style rules. The runtime renders it into the cached first tier of the system prompt (`<output_contract>`, right after `<channel_rules>`), default language first and the other supported languages under "If the conversation is in <language>:". The turn's language is the session's `language_preference`, else `default_language`. Connectors can add `spoken` fields through `result_shaping` (for example `salary_spoken`), so the model reads pay exactly as given and never converts a number itself.

The output guard is the safety net behind that contract. It runs on model-generated sentences only, before the Trust output check on both paths: it strips markdown, rewrites digits to words (phones digit by digit, ranges as "A से B", everything else as a number in words) and counts Latin-script words in a Devanagari reply without rewriting them. It never raises; on an internal error the sentence passes through unchanged. Prompt blocks, in cache order: Tier 1 `<persona>`, `<channel_rules>`, `<output_contract>`, `<how_to_read_context>`, `<session_end_policy>`; Tier 2 `<subagent>`, `<user_state_guidance>`; Tier 3 (dynamic) `<channel_context>`, `<resumption>`, `<state>`, `<recent>`, `<known_facts>`, `<caller_turn>`. See `docs/superpowers/specs/2026-10-01-main-llm-context-design.md` §3, §5 and §6.5.

## Turn execution sequence

Both `process_turn()` and `stream_turn()` run the same 13-step sequence:

```
1.  Read session state          Memory Layer — loads ContextBundle for the session
2.  Trust check input           Trust Layer — block, escalate, or allow
3.  Language Normalisation      Internal LLM call (haiku model) — dialect, code-switching,
                                transliteration
4.  Dialogue-act NLU            TurnUnderstander — pending question + known fields form the
                                frame; one strict-schema LLM call returns dialogue acts and
                                typed slots; post-processing derives the routing intent and
                                the session writes. A structured summary of the
                                understanding is rendered into the main LLM prompt as
                                <caller_turn>
5.  Routing                     Deterministic — NLU result + session conditions select subagent
5b. Tool pre-dispatch           Optional — a per-subagent `predispatch` rule runs a tool before the
                                main LLM (see "Tool pre-dispatch"); the result is handed to LLM call #1
6.  Assemble constraints        Trust Layer.assemble_constraints
7.  Build system prompt         Tiered blocks: persona, channel rules, output contract (cached);
                                subagent (cached); <state>, <recent>, <known_facts>,
                                <caller_turn> (dynamic) — see "Output contract and guard"
8.  LLM call #1                 ChatProviderBase — call() (sync) or stream() (streaming),
                                via the configured provider (anthropic, openai, or google)
9.  Tool-use loop               ManagerAgent — if LLM returns tool_use: route via ToolRegistry;
                                knowledge_retrieval → KE; all other tools → Action Gateway;
                                append result, LLM call #2; bounded by max_tool_rounds
10. Output guard + Trust check  Guard rewrites digits and strips markdown per sentence, then
                                Trust Layer — mandatory; blocked sentences → fallback text
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

## Tool pre-dispatch

About half of all turns call a tool, so the main LLM is called twice: once to ask for the tool and once to speak the result. A subagent can declare `predispatch` rules that call the tool itself, after routing and before the main LLM, when the NLU result and the session already determine the call. The result reaches the main LLM's first call as a normal tool exchange, the tool is removed from that call's tool list, and the main LLM still writes every reply. There are no template replies and no added model calls.

- A rule has `tool`, `enabled`, optional `on_intent` / `when` / `unless_fresh`, and `args` bindings (`from: session`, `from: literal` or `template`, with optional `normalise` and `reject`).
- It runs once per turn on both the sync and stream paths, and the first rule whose conditions hold and whose arguments resolve wins.
- It uses the same guards as a model-initiated call: the per-turn cap, grounding, the tool cache, result shaping, session-value mapping and cache persistence.
- It never changes the turn's routing and never raises into the turn. On any failure, timeout or missing argument the turn falls back to the normal model-driven path.
- The stream path enforces `agent.predispatch_timeout_ms`. The sync path relies on the gateway's own per-tool timeout.
- Read tools ship enabled. Every write rule ships `enabled: false`, and a write or identity rule without an explicit `enabled` is rejected at startup.
- The `stream_turn_complete` log carries `llm_calls`, `predispatch_tool`, `predispatch_outcome` and `predispatch_ms`. They hold tool names, outcomes and timings only, never caller text or argument values.

**Consent.** Model-initiated calls keep today's behaviour. Only pre-dispatch checks Trust consent (`trust.check_consent`) for tools that require it. Blue Dots records consent in the session rather than in the Trust Layer, so a Blue Dots write rule would be refused until the consent source is unified. That is why the Blue Dots write rules ship disabled.

See `docs/superpowers/specs/2026-10-02-tool-predispatch-design.md`.

## TurnAssembler (multi-segment input)

For channels that deliver input as multiple partial segments (voice VAD, rapid corrections, barge-in), `TurnAssembler` sits between the HTTP server and `AgentCore.stream_turn()`.

```
POST /sessions/{id}/input  ─►  TurnAssembler.add_segment()
                                    │
                                    ▼
                            Session.current_turn: Turn (segments, timers, queue, abort)
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                 silence_trigger        max_wait_ceiling
                 (resets on every       (absolute ceiling,
                  new segment)           never resets)
                                    │
                                    ▼
                           agent_core.stream_turn()  ──►  Turn.event_queue
                                                                  │
                                                                  ▼
                                                    GET /sessions/{id}/events (SSE)
```

**State machine** (`TurnStatus`):  `WAITING → INVOKED → {COMPLETED, INTERRUPTED, ABANDONED}`

**Policy stack** — first to fire wins:

1. **Silence trigger** — `asyncio.Task` started on first segment, reset (cancel + restart) on every subsequent `add_segment()`. Fires after `silence_ms`.
2. **Max-wait ceiling** — `asyncio.Task` started once on buffer creation, never reset. Fires after `max_wait_ms`.

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
Buffers multi-segment input and decides when to invoke `stream_turn()`. Holds `_sessions: dict[str, Session]` in memory; each Session owns the current Turn. Constructor takes optional `workflow` and `async_memory`; when `async_memory` is present, the session context bundle is cached on the Turn at the first segment.

**`manager_agent.py` — ManagerAgent**
LLM → tool → LLM loop. Both sync and async variants. Used by `process_turn()` for synchronous tool rounds; `stream_turn()` handles tool use via the `ToolUseRequested` exception raised from `provider.stream()`.

**`chat_provider/` — multi-provider LLM interface**
`ChatProviderBase` is the only LLM type the rest of agent_core depends on. `build_chat_provider(agent_config)` selects a concrete provider from `agent.provider` (anthropic, openai, or google today; AzureOpenAI/Ollama as follow-ups). Each provider lives in its own file and is the sole importer of its SDK. `call()` is sync; `stream()` yields text deltas and raises `ToolUseRequested(list[ToolUseBlock])` when the model emits tool calls — the caller executes the tools and resumes. Capabilities (prompt cache, image input, structured output, etc.) are declared per provider class and reconciled against deployment YAML at startup; mismatches fail loud with `ProviderConfigError`.

**`preprocessing/language_normalisation.py` — LanguageNormaliser**
Runs before NLU. Detects dialect, normalises code-switching (Hindi/Kannada/English), and transliterates Romanised Indic text. Currently only the `internal` provider (LLM-based normalisation via a haiku model) is implemented.

**`understanding/` — TurnUnderstander (the single, dialogue-act NLU)**
Understands each turn in the context of the question the agent just asked. It resolves the session's pending question, builds a frame (`pending`, `known_fields`, `recent_turns`, `served_tool_results`), makes one strict-schema LLM call that returns dialogue acts and typed slots, and post-processes the result: the routing intent is derived from the `act_intents` table (`NLUResult.confidence` is 1.0 for a derived intent, 0.0 for a fallback) and `SlotWriter` plans the session writes. When `conversation.user_state_model` is enabled the same call also returns `user_state`. A structured summary of the understanding (acts, relation, resolved option, slot updates, signals) is rendered into the main LLM's prompt as `<caller_turn>`. Uses a dedicated NLU provider instance. There is no other NLU mode; see `docs/superpowers/specs/2026-10-01-nlu-dialogue-acts-design.md` §16.

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
| `conversation.unknown_intent_message` | Fallback reply when a subagent declares an unknown `special_handler` |
| `connectors.read[]` / `write[]` / `identity[]` / `internal[]` | Tool definitions |
| `connectors.*.result_shaping` | Per-tool shaping of the result rows before the model sees them: `drop_when`, `sort`, `spoken` fields (e.g. `salary_spoken`) and `strip_numbers_in` |
| `channels.*.output_contract` | Per-channel spoken-output contract: `default_language`, per-language `script` / `numbers` / `rules`, and the `guard` switches. Replaces the removed per-channel TTS-rules key |
| `agent.history_turns` | Past exchanges the main LLM sees in `<recent>` (default 2; 0 omits the block) |
| `agent.state_fields` | Session keys shown as-is on the `<state>` status line |
| `agent.predispatch_timeout_ms` | Budget for one pre-dispatched tool call on the stream path (default 1500). On timeout the turn falls back to the model-driven call |
| `predispatch` (per subagent) | Rules that call a tool before the main LLM: `tool`, `enabled`, `on_intent`, `when`, `unless_fresh`, `args`. Write rules need an explicit `enabled` |
| `predispatch_tables` | Named lookup tables for `normalise` and `reject` (for example `city_canonical`) |

### Preprocessing

| Key | Description |
|---|---|
| `preprocessing.language_normalisation.model` / `provider` / `supported_languages` | Dialect/transliteration config |
| `preprocessing.nlu_processor.model` / `slots` / `act_intents` / `topics` / `signals` / `signal_intents` / `termination_gate` / `examples` / `off_track` | Dialogue-act NLU config |
| `preprocessing.nlu_processor.user_state_confidence_threshold` | Sticky fallback for the user-state classifier |

### Agent workflow (subagents)

| Key | Description |
|---|---|
| `agent_workflow.workflow_id` | Workflow identifier |
| `agent_workflow.agent_system_prompt` | Base system prompt |
| `agent_workflow.subagents[]` | Subagent definitions with `pending` questions, routing rules and tool lists |

### Reach Layer / TurnAssembler (new)

TurnAssembler is an Agent Core component but is tuned per channel. Config lives under the `reach_layer` key in `agent_core.yaml`.

| Key | Description |
|---|---|
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

Requires `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` or `GOOGLE_API_KEY`) to be set in the environment.

To enable the TurnAssembler session endpoints, construct the FastAPI app via `create_orchestration_app(agent_core, turn_assembler=<instance>)`. When `turn_assembler=None` (default), only `/process_turn`, `/stream_turn`, and `/health` are registered — zero breaking changes for deployments that don't use session-based input.

---

## Running tests

```bash
cd agent_core
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

818 tests across 32 files. Coverage threshold: 70% (currently ~75%). `turn_assembler.py` is covered at 96%.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `anthropic` | >=0.40.0 | Anthropic SDK — used only in `chat_provider/anthropic_provider.py` |
| `openai` | >=1.50.0 | OpenAI SDK — used only in `chat_provider/openai_provider.py` |
| `google-genai` | >=1.0.0 | Google SDK — used only in `chat_provider/google_provider.py` |
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

**Anthropic, OpenAI, and Google providers are implemented (#287).** AzureOpenAI and Ollama are planned follow-ups; they slot into `chat_provider/` without changing the orchestration layer.

**`POST /internal/llm/call` proxy is not yet wired to downstream callers.** The endpoint is implemented and registered, but no other DPG block calls it. The intended architecture routes all LLM calls from other blocks through this proxy; current state has only Agent Core calling the configured provider's SDK directly.

**HiTL output escalation path deferred.** When `POST /check/output` returns `action: "escalate"`, Agent Core does not call `/escalate` — this path is not wired. Blocked sentences in the stream are replaced with fallback text only.

**Channel-aware prompt assembly not yet implemented.** All channels (voice, web, CLI) receive the same system prompt regardless of channel. A future optimisation should shorten prompts for voice channels, which have tighter latency budgets and no Markdown rendering (#97).

