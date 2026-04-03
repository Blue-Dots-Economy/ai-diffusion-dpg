# Trust Layer — End-to-End Design Spec

**Date:** 2026-04-03
**Branch:** GH-5-trust-layer
**Status:** Approved — ready for implementation

---

## 1. Overview

The Trust Layer (port 8003) is the mandatory safety gate for every turn. It is restructured from a single `BasicTrustLayer` class into four focused internal sub-blocks, all served from the same FastAPI service. No DPG boundary changes — it remains one block.

The core additions:
- **GuardrailsBlock** — pre-LLM constraint assembly driven by a Risk Taxonomy and domain Policy Pack
- **ConsentBlock** — DPDP Act consent phrase evaluation (stateless)
- **HiTLBlock** — human-in-the-loop escalation queue with queue+resume model
- **ContentBlock** — upgrade of existing phrase-match checks, now receiving `active_risks` from NLU
- **Fail-closed** — all endpoints and Agent Core HTTP client error handlers block on failure

---

## 2. Internal Structure

```
trust_layer/src/
  blocks/
    content.py       # ContentBlock    — phrase + risk-signal I/O checks
    guardrails.py    # GuardrailsBlock — pre-LLM constraint assembly
    consent.py       # ConsentBlock    — DPDP consent phrase evaluation
    hitl.py          # HiTLBlock       — escalation queue + resume
  trust_layer.py     # TrustLayer orchestrator (replaces current guardrails.py)
  server.py          # FastAPI — all endpoints
  models.py          # All Pydantic request/response types
```

---

## 3. Risk Taxonomy

Ten domain-agnostic risks form the foundation. Policy Packs select which apply to a given domain.

| ID | Risk | Description |
|---|---|---|
| `false_certainty` | False Certainty | AI implies guaranteed outcomes |
| `hallucinated_reality` | Hallucinated Reality | AI invents facts without data |
| `authority_illusion` | Authority Illusion | AI presents itself as decision-maker |
| `agency_violation` | Agency Violation | AI acts without explicit user consent |
| `emotional_overreach` | Emotional Overreach | AI counsels or reassures beyond scope |
| `dignity_harm` | Dignity Harm | AI implies blame or judgment about user |
| `scope_breach` | Scope Breach | AI answers outside its domain |
| `silent_failure` | Silent Failure | AI hides uncertainty or missing data |
| `action_escalation` | Action Escalation | AI triggers irreversible actions prematurely |
| `compliance_misrepresentation` | Compliance Misrepresentation | AI implies legal authority it lacks |

---

## 4. API

| Method | Endpoint | Caller | Purpose |
|---|---|---|---|
| POST | `/check/input` | Agent Core, pre-LLM | Phrase-match + risk-signal input check |
| POST | `/assemble_constraints` | Agent Core, pre-LLM | Guardrail control artifact assembly |
| POST | `/check/output` | Agent Core, post-LLM | Output phrase-match + contract check |
| POST | `/consent/verify` | Agent Core, turn 2 of fresh session | DPDP consent phrase evaluation |
| POST | `/check/consent` | Agent Core, before write tool execution | Connector-level consent check |
| POST | `/escalate` | Agent Core, when input returns "escalate" | HiTL queue submission |
| GET | `/health` | Liveness probe | — |

### `/check/input`
```python
# Request (active_risks optional — only present when workflow is "ready")
{ "session_id": str, "message": str, "active_risks": list[str] | None }

# Response
{ "passed": bool, "action": "allow" | "block" | "escalate", "reason": str | None }
```

### `/assemble_constraints`
```python
# Request
{ "session_id": str, "workflow_step": str,
  "active_risks": list[str], "user_segment": str | None }

# Response
{ "prompt_constraints": list[str],
  "required_disclosures": list[str],
  "action_gates": dict[str, bool],
  "refusal_templates": dict[str, str] }
```

### `/consent/verify`
```python
# Request
{ "session_id": str, "user_message": str }

# Response
{ "granted": bool }
```

### `/escalate`
```python
# Request
{ "session_id": str, "escalation_reason": str,
  "user_message": str, "workflow_step": str }

# Response
{ "queued": bool, "ticket_id": str, "holding_message": str }
```

**Fail-closed rule:** On any internal error, all endpoints return `action: "block"` / `granted: False` / `queued: False`. Never return `allow` on error.

---

## 5. Per-Turn Flow (Agent Core)

```
1. Read session state from Memory Layer
2. [if ask_for_consent: true] Consent gate:
     user_storage_mode=None, no prior turns → return consent_prompt (no LLM, no TL call)
     user_storage_mode=None, prior turn exists → POST /consent/verify → write user_storage_mode → continue
     user_storage_mode set → skip
3. NLU → { intent, entities, confidence, active_risks? }
     low confidence → early exit
4. POST /check/input { session_id, message, active_risks }
     block    → TurnResponse(blocked_input_message from config)
     escalate → POST /escalate → TurnResponse(holding_message)
     allow    → continue
5. Language Normalisation (internal)
6. [if active_risks present] POST /assemble_constraints
     → { prompt_constraints, required_disclosures, action_gates, refusal_templates }
7. Manager Agent: active subagent selected by current_subagent_id + NLU intent routing
     system_prompt = subagent_prompt + guardrail_constraints + required_disclosures
     tool list filtered by action_gates
8. LLM call #1 (tool-use loop if needed; action_gates enforced)
9. POST /check/output
     block → TurnResponse(output_blocked_message from config)
     allow → deliver
10. [async] write state to Memory Layer (current_subagent_id, user_storage_mode, session data)
11. [async] emit TurnEvent to Learning Layer (all turns — block/escalate included)
```

---

## 6. Consent Flow

Only active when `ask_for_consent: true` in `dev-kit/dpg/agent_core.yaml` (default: `false`).

| Turn | Condition | Action |
|---|---|---|
| 1 | `user_storage_mode` is None, no prior turns | Return `consent_prompt` from config. No LLM, no Trust Layer call. |
| 2 | `user_storage_mode` is None, prior turn exists | `POST /consent/verify`. Write `user_storage_mode: "saved" \| "anonymous"` to Memory Layer. Proceed to first subagent. |
| 3+ | `user_storage_mode` is set | Skip consent block entirely. |

- `user_storage_mode: "saved"` — user granted consent; data persisted across sessions.
- `user_storage_mode: "anonymous"` — user declined or response unclear; cleanup service deletes session data at session end (DPDP-compliant).
- No retry, no re-asking. One ask, one evaluate, done.

**Removed from Agent Core:** `greeting` subagent, `awaiting_consent` workflow step, `session_writes` on consent routing rules.

---

## 7. YAML Configuration

### `dev-kit/dpg/agent_core.yaml` (framework default)
```yaml
agent:
  ask_for_consent: false    # domain builder sets true to enable DPDP consent
  consent_prompt: ""        # required if ask_for_consent: true
```

### `dev-kit/configs/kkb/trust_layer.yaml`
```yaml
trust:
  policy_pack: "kkb_advisory_jobs"

  input_rules:
    blocked_phrases: ["bomb", "weapon", "kill", "threat", "violence"]
    escalation_topics: ["suicide", "arrested", "police case", "FIR", "jail"]
    blocked_input_message: "Yeh baat main handle nahi kar sakta."

  output_rules:
    blocked_phrases: ["guaranteed placement", "100% job guarantee", "as an AI, I"]
    output_blocked_message: "Mujhe yeh jawab dene mein dikkat aa rahi hai."

  policy_packs:
    kkb_advisory_jobs:
      risks:
        - false_certainty
        - hallucinated_reality
        - emotional_overreach
        - dignity_harm
        - agency_violation

      guardrails:
        false_certainty:
          id: "GR-001"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT guarantee or imply certainty about job outcomes"
            - "MUST frame all opportunities as possibilities, not certainties"
          required_disclosures:
            - "Hiring decisions rest with the employer"
          refusal_template: "Main kisi bhi naukri ki guarantee nahi de sakta."

        hallucinated_reality:
          id: "GR-002"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT infer or generalise from missing data"
            - "MUST only reference verified signals available in context"
          required_disclosures:
            - "Information is based only on verified data"
          refusal_template: "Is baare mein koi verified data available nahi hai."

        emotional_overreach:
          id: "GR-003"
          severity: "warning"
          failure_mode: "constrain"
          prompt_constraints:
            - "MUST NOT provide counselling or motivational language"
            - "MUST limit empathetic language to one brief acknowledgement"
          required_disclosures: []
          refusal_template: null

        dignity_harm:
          id: "GR-004"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT imply blame, deficiency, or judgment about the user"
          required_disclosures: []
          refusal_template: null

        agency_violation:
          id: "GR-005"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT act without explicit user confirmation"
            - "MUST present options and wait for user decision before any action"
          required_disclosures:
            - "Any action requires your explicit confirmation"
          refusal_template: null

  consent:
    consent_phrases: ["haan", "yes", "theek hai", "manzoor hai"]
    decline_phrases: ["nahi", "no", "nahi chahiye"]

  hitl:
    queue_backend: "log"    # "log" | "redis" | "webhook"
    holding_message: "Aapki baat ek advisor tak pahunch rahi hai. Thodi der mein aapse sampark hoga."
    notification_webhook: null
```

---

## 8. Agent Core File Changes

| File | Change |
|---|---|
| `preprocessing/nlu_processor.py` | Add `active_risks: list[str] \| None = None` to `NLUResult`. Populate when subagent is in "ready" state. |
| `orchestrator.py` | Add consent gate logic. Add pre-LLM `/assemble_constraints` call. Change escalate short-circuit to call `/escalate`. |
| `manager_agent.py` | Inject `prompt_constraints + required_disclosures` into system prompt. Filter tool list by `action_gates`. Remove `awaiting_consent` branch. |
| `http_clients/trust_layer_client.py` | Add `assemble_constraints()`, `verify_consent()`, `escalate()`. Change all error handlers to fail-closed. |

---

## 9. Testing Requirements

### ContentBlock
- Normal: blocked phrase → `block`; escalation topic → `escalate`; clean input → `allow`
- Edge: empty message → `allow`; mixed-case phrase → `block`; `active_risks: None` → no error
- Failure: missing phrase lists in config → empty lists, no crash

### GuardrailsBlock
- Normal: known risk → correct constraints returned; no active risks → empty response
- Edge: `active_risks: []` → empty response; unknown risk ID → skipped silently
- Failure: malformed policy pack YAML → structured error, not crash

### ConsentBlock
- Normal: consent phrase → `granted: true`; decline phrase → `granted: false`; unclear → `granted: false`
- Edge: empty message → `granted: false`; partial phrase match → no false positive
- Failure: `consent_phrases` missing from config → `granted: false` (fail-closed)

### HiTLBlock
- Normal: escalation queued → `queued: true`, `ticket_id` non-empty, `holding_message` returned
- Edge: missing `user_message` → queues with empty message, no crash
- Failure: queue backend unavailable → structured error returned

### Orchestrator consent gate
- `ask_for_consent: false` → consent block never entered on any turn
- Fresh user, turn 1 → `consent_prompt` returned, no LLM call, no Trust Layer call
- Turn 2, granted → `user_storage_mode: "saved"` written, proceeds to `profile_building`
- Turn 2, declined → `user_storage_mode: "anonymous"` written, proceeds to `profile_building`
- Turn 3+ → consent block skipped

### Fail-closed
- Trust Layer HTTP error on any endpoint → Agent Core returns `block` / `deny`

---

## 10. Out of Scope

- ML/semantic matching for ContentBlock (phrase-match only for now)
- Live human agent interface for HiTL resume side
- Redis/webhook queue backend for HiTL (log only initially)
- ASR/TTS, model training, multi-tenancy
