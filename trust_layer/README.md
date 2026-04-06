# Trust Layer DPG

Mandatory safety and compliance gate. Every input and every output passes through this layer — never skipped.

---

## What this service does

The Trust Layer enforces content safety, consent, and escalation rules on every turn. Agent Core calls it twice per turn — once on the raw user input (before the LLM sees it) and once on the LLM output (before it reaches the user). Neither check is skippable.

The Trust Layer is composed of four sub-blocks:

- **ContentBlock** (`blocks/content.py`): Phrase-match input/output blocking and escalation routing. Receives `active_risks` from NLU when available.
- **GuardrailsBlock** (`blocks/guardrails.py`): Pre-LLM constraint assembly. Maps active risks → Policy Pack → prompt constraints, required disclosures, action gates, and refusal templates.
- **ConsentBlock** (`blocks/consent.py`): Evaluates user message against consent/decline phrases from config. Stateless — Agent Core owns flag management and writes `user_storage_mode` to Memory Layer.
- **HiTLBlock** (`blocks/hitl.py`): Escalation queue. Returns `holding_message` and `ticket_id`. Queue backend is configurable (`log` → `redis` → `webhook`).

**Fail-closed:** All endpoints return `block` or `deny` on internal error. A failing Trust Layer never allows a turn through.

---

## Folder structure

```
trust_layer/
├── main.py                 # Uvicorn entrypoint (port 8003)
├── pyproject.toml
├── config/
│   └── config.yaml         # Blocked phrases, escalation topics, consent phrases, Policy Pack
├── src/
│   ├── orchestrator.py     # TrustLayer orchestrator — routes requests to sub-blocks
│   ├── models.py           # All Pydantic request/response types
│   ├── consent_store.py    # SQLite consent store (in-process only)
│   ├── blocks/
│   │   ├── content.py      # ContentBlock
│   │   ├── guardrails.py   # GuardrailsBlock
│   │   ├── consent.py      # ConsentBlock
│   │   └── hitl.py         # HiTLBlock
│   └── server.py           # FastAPI app (all endpoints)
└── tests/
    ├── test_content.py
    ├── test_guardrails.py
    ├── test_consent.py
    ├── test_hitl.py
    └── test_server.py
```

---

## HTTP API

The service runs on port **8003**.

### `POST /check/input`

Pre-LLM phrase-match and risk-signal input check. Returns `allow`, `block`, or `escalate`.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "message": "electrician ka kaam chahiye",
  "active_risks": []
}
```

**Response — allowed:**
```json
{ "passed": true, "action": "allow", "reason": null }
```

**Response — blocked:**
```json
{ "passed": false, "action": "block", "reason": "Input contains blocked phrase: 'bomb'" }
```

**Response — escalate to human:**
```json
{ "passed": false, "action": "escalate", "reason": "Escalation topic detected: 'suicide'" }
```

### `POST /assemble_constraints`

Pre-LLM, called after input passes. Returns guardrail control artifacts for injection into the system prompt.

**Request:**
```json
{
  "session_id": "sess-abc123",
  "active_risks": ["job_scam_risk"]
}
```

**Response:**
```json
{
  "prompt_constraints": ["Do not promise guaranteed placements."],
  "required_disclosures": ["This is an AI assistant. All information is advisory only."],
  "action_gates": [],
  "refusal_templates": {}
}
```

### `POST /check/output`

Post-LLM output phrase-match and guardrail contract check.

**Request:**
```json
{ "session_id": "sess-abc123", "response": "The salary range for electricians is ₹15,000–₹28,000/month." }
```

**Response:**
```json
{ "passed": true, "action": "allow", "reason": null }
```

If blocked, Agent Core replaces the response with the configured `output_blocked_message`.

### `POST /consent/verify`

Turn 2 of a fresh session. Evaluates the user's response against configured consent/decline phrases. Returns `granted: bool`. Agent Core owns writing the result to Memory Layer.

**Request:**
```json
{ "session_id": "sess-abc123", "message": "haan, theek hai" }
```

**Response:**
```json
{ "granted": true }
```

### `POST /check/consent`

Before write or identity tool execution. Verifies connector-level consent. Fail-closed — returns `granted: false` on any internal error.

**Request:**
```json
{ "session_id": "sess-abc123", "connector_name": "onest_apply" }
```

**Response:**
```json
{ "granted": true, "reason": null }
```

### `POST /escalate`

Called when `/check/input` returns `"escalate"`. Queues a HiTL escalation record and returns a holding message.

**Request:**
```json
{ "session_id": "sess-abc123", "reason": "Escalation topic detected: 'suicide'" }
```

**Response:**
```json
{
  "holding_message": "Main aapki baat samajh raha hoon. Ek counsellor se aapko connect karta hoon.",
  "ticket_id": "ticket-abc123"
}
```

### `GET /health`

Returns `{"status": "ok"}` when the service is running.

---

## Rules (configured in YAML)

All rules are loaded from `config/config.yaml` at startup. Nothing is hardcoded.

### Input rules

| Rule type | Config key | Behaviour |
|---|---|---|
| Blocked phrases | `trust.input_rules.blocked_phrases` | Case-insensitive substring match. Returns `action: block`. |
| Escalation topics | `trust.input_rules.escalation_topics` | Case-insensitive substring match. Returns `action: escalate`. |

### Output rules

| Rule type | Config key | Behaviour |
|---|---|---|
| Blocked phrases | `trust.output_rules.blocked_phrases` | Blocks LLM responses containing these strings. |

### Consent rules

| Rule type | Config key | Behaviour |
|---|---|---|
| Consent phrases | `trust.consent_rules.consent_phrases` | Phrases indicating user consent (e.g. "haan", "theek hai"). |
| Decline phrases | `trust.consent_rules.decline_phrases` | Phrases indicating user decline. |

### Guardrail rules

| Config key | Description |
|---|---|
| `trust.policy_packs` | Risk taxonomy → Policy Pack mapping. Each pack defines prompt constraints, required disclosures, action gates, and refusal templates. |

---

## Configuration

| Key | Description |
|---|---|
| `server.port` | HTTP port (default: 8003) |
| `trust.input_rules.blocked_phrases` | List of phrases that block the input entirely |
| `trust.input_rules.escalation_topics` | List of phrases that trigger human handoff |
| `trust.output_rules.blocked_phrases` | List of phrases that block the LLM output |
| `trust.consent_rules.consent_phrases` | Phrases that indicate consent |
| `trust.consent_rules.decline_phrases` | Phrases that indicate decline |
| `trust.policy_packs` | Risk → constraint/disclosure/gate mapping |
| `trust.hitl.queue_backend` | Escalation queue backend: `log` (default), `redis`, `webhook` |

---

## Running the service

```bash
cd trust_layer
uv run uvicorn src.server:app --port 8003
```

---

## Running tests

```bash
cd trust_layer
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected: 39 tests, 100% line coverage (ContentBlock). GuardrailsBlock, ConsentBlock, and HiTLBlock test suites are pending.

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

## Known gaps

- **No ML-based semantic matching.** ContentBlock uses phrase-match only; a production deployment would use an ML classifier for context-aware risk detection.
- **HiTL queue: `log` backend only.** The `redis` and `webhook` backends are reserved for a future issue.
- **`consent_store` is in-process only.** The SQLite consent store writes consent state locally. Multi-instance deployments need a shared consent store (e.g. Redis or PostgreSQL).
- **Output-check escalation not wired.** When `trust_output.action == "escalate"`, the orchestrator does not yet call `HiTLBlock.escalate()` — deferred to the HiTL queue issue.
