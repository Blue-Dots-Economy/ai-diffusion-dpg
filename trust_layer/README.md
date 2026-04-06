# Trust Layer

Mandatory safety and compliance gate. Runs on every turn — never skipped. **Fail-closed:** any internal exception causes the endpoint to return `block` or `deny`, never `allow`.

---

## What this service does

Agent Core calls the Trust Layer twice per turn:

1. **Before the LLM** — input check + constraint assembly.
2. **After the LLM** — output check before the response reaches the user.

Neither check is skippable. The Trust Layer is composed of four internal sub-blocks wired together by the `TrustLayer` orchestrator:

- **ContentBlock** — phrase-match input/output blocking and escalation routing.
- **GuardrailsBlock** — maps active risk signals to Policy Pack guardrails; returns prompt constraints, required disclosures, action gates, and refusal templates for injection into the system prompt.
- **ConsentBlock** — stateless evaluation of whether a user message contains consent or decline phrases.
- **HiTLBlock** — escalation queue; generates a ticket and returns a holding message.

Consent state is persisted in a SQLite store (`ConsentStore`) by the orchestrator after `ConsentBlock` returns `granted=True`.

---

## Folder structure

```
trust_layer/
├── main.py
├── pyproject.toml
├── config/
│   ├── dpg.yaml          # Server host/port
│   └── domain.yaml       # Trust rules: blocked phrases, escalation topics, policy packs, consent phrases
├── src/
│   ├── models.py         # All Pydantic request/response schemas for all 7 endpoints
│   ├── server.py         # FastAPI app — all 7 endpoints, fail-closed error handling
│   ├── orchestrator.py   # TrustLayer orchestrator — wires all 4 sub-blocks
│   ├── consent_store.py  # SQLite-backed consent persistence (session_id → granted_at)
│   ├── guardrails.py     # BasicTrustLayer — legacy PoC stub (deprecated; not used by TrustLayer)
│   └── blocks/
│       ├── content.py    # ContentBlock
│       ├── guardrails.py # GuardrailsBlock
│       ├── consent.py    # ConsentBlock
│       └── hitl.py       # HiTLBlock
└── tests/
    ├── test_models.py
    ├── test_server.py
    ├── test_main.py
    ├── test_guardrails.py   # tests legacy BasicTrustLayer
    └── blocks/
        ├── test_content.py
        ├── test_guardrails.py
        ├── test_consent.py
        └── test_hitl.py
```

---

## HTTP API

The service runs on port **8003**. All endpoints are fail-closed — an unhandled exception returns the deny/block response shown, never a 500 that could be misread as allowed.

---

### `POST /check/input`

Pre-LLM input check. Returns `allow`, `block`, or `escalate`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "message": "I need help finding a job",
  "active_risks": ["job_scam_risk"]
}
```
`active_risks` is optional (nullable). Accepted by ContentBlock but not currently acted upon (reserved for future semantic matching).

**Response — allowed:**
```json
{ "passed": true, "action": "allow", "reason": null }
```

**Response — blocked:**
```json
{ "passed": false, "action": "block", "reason": "Input contains blocked phrase: 'bomb'" }
```

**Response — escalate:**
```json
{ "passed": false, "action": "escalate", "reason": "Escalation topic detected: 'suicide'" }
```

**Fail-closed response:**
```json
{ "passed": false, "action": "block" }
```

---

### `POST /check/output`

Post-LLM output phrase-match check. Returns `allow` or `block`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "response": "The salary range for electricians is ₹15,000–₹28,000/month."
}
```

**Response — allowed:**
```json
{ "passed": true, "action": "allow", "reason": null }
```

**Response — blocked:**
```json
{ "passed": false, "action": "block", "reason": "Output contains blocked phrase: 'guaranteed placement'" }
```

**Fail-closed response:**
```json
{ "passed": false, "action": "block" }
```

Note: `action: "escalate"` is a valid ContentBlock return for output, but Agent Core does not currently call `/escalate` in response to it — see Known gaps.

---

### `POST /check/consent`

Checks whether a session has previously granted connector-level consent. Called before any write or identity connector executes.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "connector_name": "onest_apply"
}
```

**Response:**
```json
{ "granted": true }
```

**Fail-closed response:**
```json
{ "granted": false }
```

Behavior: queries SQLite `consent_store` for `session_id`. Does not re-evaluate the user message.

---

### `POST /assemble_constraints`

Pre-LLM, called after input passes. Maps active risk signals to Policy Pack guardrails and returns control artifacts for injection into the system prompt.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "workflow_step": "job_search",
  "active_risks": ["job_scam_risk"],
  "user_segment": null
}
```
`workflow_step` and `user_segment` are accepted but not currently used for filtering — informational only.

**Response:**
```json
{
  "prompt_constraints": ["Do not promise guaranteed placements."],
  "required_disclosures": ["This is an AI assistant. All information is advisory only."],
  "action_gates": {},
  "refusal_templates": {}
}
```

Unknown `risk_id` values are silently skipped. Empty `active_risks` returns empty lists/dicts.

**Fail-closed response:** empty constraints — all four fields as empty lists/dicts.

---

### `POST /consent/verify`

Evaluates a user message against configured consent and decline phrases. If consent is detected, writes to SQLite `consent_store`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "user_message": "haan, theek hai"
}
```

**Response:**
```json
{ "granted": true }
```

Behavior: case-insensitive substring match against `trust.consent.consent_phrases` and `trust.consent.decline_phrases`. `ConsentBlock` is stateless — the orchestrator calls `consent_store.record_consent()` only when `granted=True`.

**Fail-closed response:**
```json
{ "granted": false }
```

---

### `POST /escalate`

Queues a Human-in-the-Loop escalation record and returns a holding message. Called when `/check/input` returns `action: "escalate"`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "escalation_reason": "Escalation topic detected: 'suicide'",
  "user_message": "I don't want to go on",
  "workflow_step": "job_search"
}
```

**Response:**
```json
{
  "queued": true,
  "ticket_id": "TKT-20240406-3FA2B1C0",
  "holding_message": "Main aapki baat samajh raha hoon. Ek counsellor se aapko connect karta hoon."
}
```

Ticket ID format: `TKT-YYYYMMDD-8HEXCHARS`.

Backend behavior: `log` backend writes a structured warning log. `redis` and `webhook` backends log a warning ("unsupported backend") but still return `queued: true` — see Known gaps.

**Fail-closed response:**
```json
{ "queued": false, "ticket_id": "", "holding_message": "" }
```

---

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

---

## Rules — how config drives each sub-block

### ContentBlock

Loaded from config at startup; never re-read on requests.

| Rule type | Config key | Behavior |
|---|---|---|
| Input blocked phrases | `trust.input_rules.blocked_phrases` | Case-insensitive substring match → `action: block` |
| Input escalation topics | `trust.input_rules.escalation_topics` | Case-insensitive substring match → `action: escalate` |
| Output blocked phrases | `trust.output_rules.blocked_phrases` | Case-insensitive substring match → `action: block` |

Empty or `None` input always returns `allow`. `active_risks` is accepted but not acted upon (phrase-match only).

### GuardrailsBlock

| Config key | Description |
|---|---|
| `trust.policy_pack` | Name of the active Policy Pack |
| `trust.policy_packs.{name}.guardrails.{risk_id}.prompt_constraints` | List of constraint strings added to system prompt |
| `trust.policy_packs.{name}.guardrails.{risk_id}.required_disclosures` | List of disclosure strings |
| `trust.policy_packs.{name}.guardrails.{risk_id}.action_gates` | Dict of gated actions |
| `trust.policy_packs.{name}.guardrails.{risk_id}.refusal_template` | Template string for refusals |

`assemble_constraints` iterates `active_risks`, looks each up in the guardrail dict, and merges results. Unknown risk IDs are silently skipped.

### ConsentBlock

| Config key | Description |
|---|---|
| `trust.consent.consent_phrases` | Phrases that indicate user consent |
| `trust.consent.decline_phrases` | Phrases that indicate user decline |

Case-insensitive substring match. Stateless — no store access.

### HiTLBlock

| Config key | Description |
|---|---|
| `trust.hitl.queue_backend` | `log` (default), `redis`, or `webhook` |
| `trust.hitl.holding_message` | Message returned to Agent Core while human review is pending |
| `trust.hitl.notification_webhook` | Webhook URL (used when backend is `webhook`) |

---

## Configuration table

| Key | Description |
|---|---|
| `server.host` / `server.port` | FastAPI bind address (default port: 8003) |
| `trust.input_rules.blocked_phrases` | List of phrases that block the input |
| `trust.input_rules.escalation_topics` | List of phrases that trigger human handoff |
| `trust.output_rules.blocked_phrases` | List of phrases that block the LLM output |
| `trust.policy_pack` | Active policy pack name |
| `trust.policy_packs.{name}.guardrails.{risk_id}.*` | Per-risk guardrail definitions |
| `trust.consent.consent_phrases` | Phrases indicating consent |
| `trust.consent.decline_phrases` | Phrases indicating decline |
| `trust.hitl.queue_backend` | Escalation backend: `log`, `redis`, or `webhook` |
| `trust.hitl.holding_message` | Holding message for escalated sessions |
| `trust.hitl.notification_webhook` | Webhook URL for `webhook` backend |
| `trust.consent_store.db_path` | SQLite DB path (default: `/tmp/dpg_consent.db`) |

---

## Running the service

```bash
cd trust_layer
uv run uvicorn src.server:app --port 8003
```

No external dependencies are required at startup. SQLite is created in-process on first use.

---

## Running tests

```bash
cd trust_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

All external dependencies are mocked. No running services are required.

---

## Dependencies

```
fastapi                                  >= 0.110
uvicorn[standard]                        >= 0.29
pydantic                                 >= 2.0
pyyaml                                   >= 6.0
python-dotenv                            >= 1.0.0
observability-layer                      (local path)
opentelemetry-instrumentation-fastapi
```

Requires Python 3.11+.

---

## Known gaps

**ContentBlock uses phrase-match only.** No semantic or ML-based matching. A production deployment would use a classifier for context-aware risk detection.

**HiTL `redis` and `webhook` backends are not implemented.** Both log a warning and return `queued: true` without actually queuing. Only the `log` backend is functional.

**Output-check escalation is not wired in Agent Core.** When `/check/output` returns `action: "escalate"`, Agent Core does not call `/escalate`. This path is deferred.

**ConsentStore is in-process SQLite.** Consent state is local to the process. Multi-instance deployments require a shared store (e.g. Redis or PostgreSQL) to avoid consent being silently lost across instances.
