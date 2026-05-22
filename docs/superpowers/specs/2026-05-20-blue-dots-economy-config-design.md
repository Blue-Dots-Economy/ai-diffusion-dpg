# Blue Dots Economy — Domain Configuration Design

**Slug:** `blue-dots-economy`
**Observability domain:** `blue_dot`
**Channel:** voice only (pipecat / Reach Layer voice)
**Languages:** English (default), Hindi, Gujarati
**Agent persona name:** Blue Dots Agent

A new domain configuration under `dev-kit/configs/blue-dots-economy/` that assembles a voice-first job-seeker agent on top of the existing 7 DPG runtime blocks. The agent answers an inbound voice call, fetches/creates the caller's seeker profile, captures missing details, lists matching jobs, and submits an application — all via four tool calls. No Knowledge Engine retrieval is needed; KE YAML is included for completeness but the service may be omitted from `docker-compose.dev.yml`.

---

## 1. End-to-end runtime flow

Inbound voice call → Reach Layer Voice → Agent Core. Per the standard turn sequence in CLAUDE.md:

```
Reach Layer (voice, session mode)
  → Agent Core: Memory read (seeker node by phone)
  → Agent Core: consent gate (one-time at session start, DPDP)
  → Agent Core: NLU + Language Normalisation
  → Agent Core: Trust Layer /check/input
  → Agent Core: Manager Agent selects subagent
        profile not fetched yet OR (trade missing OR preferred_city missing)
            → profile_intake subagent
        else
            → job_match_apply subagent
  → Agent Core: LLM call #1 (with subagent system prompt + selected tools)
  → [tool_use] Agent Core → Action Gateway → real API
  → Agent Core: LLM call #2 with tool_result
  → Agent Core: Trust Layer /check/output
  → Agent Core: deliver via Reach Layer Voice (TTS)
  → [async] Memory write, Observability emit
```

Two subagents under the Manager Agent. Routing is recomputed every turn from Memory Layer state (Agent Core is stateless per turn).

**Conversational arc:**

1. **Turn 1 (profile_intake):** Greeting + DPDP consent + immediate `fetch_profile` call using the caller's phone. Profile is stored in memory with `profile_item_id` and any pre-existing `item_state`.
2. **Turns 2–N (profile_intake):** Ask for trade and preferred_city if missing. Save each to memory as captured. As soon as both are present, the next turn Manager Agent routes to `job_match_apply`.
3. **Turn N+1 (job_match_apply):** Call `fetch_jobs` filtered by role. Client-side filter the projection by `preferred_city` (case-insensitive). Read 1–3 strongest matches aloud (role, company, salary range, city).
4. **Turn N+2 (job_match_apply):** User picks one. Persist `selected_job_item_id` and the picked job dict in memory.
5. **Turns N+3–M (job_match_apply):** Capture `name` if missing (required). Optionally capture `age`, `experience_years`, `open_to_remote` if user volunteers — never block on these. Call `update_profile` with the accumulated `item_state`.
6. **Turn M+1 (job_match_apply):** Call `apply_job` with the stored `profile_item_id` and `selected_job_item_id`. Confirm `application_id` to caller. End of arc.

---

## 2. Agent Core — Manager Agent and subagents

Mirror KKB's `agent_core.yaml` structure. Replace KKB-specific persona, language defaults, and subagents. Keep KKB's `primary_model` (currently `gpt-4.1-mini-2025-04-14`) until tuning shows otherwise. Keep the same Language Normalisation tool-path config; default language switched from `hindi` to `english`, supported set is `[english, hindi, gujarati]`.

### Manager Agent routing prompt

The Manager Agent prompt selects a subagent every turn based on session state. Encoded rule:

> Route to `profile_intake` when `seeker.profile_item_id` is missing OR `seeker.trade` is missing OR `seeker.preferred_city` is missing.
> Otherwise route to `job_match_apply`.

This is a one-line decision inside the existing manager prompt — no new orchestration code, just configuration.

### Subagent 1: `profile_intake`

- **Goal:** Greet, take consent, fetch/create profile, gather trade and preferred_city.
- **Tools allowed:** `fetch_profile`.
- **System prompt content:** persona introduction; DPDP consent text; explicit instruction to call `fetch_profile` on turn 1 using the session phone (substituted by adapter); after fetching, summarise what was found in one sentence; ask for trade if missing, then preferred_city if missing; one question per turn; never request fields beyond trade and preferred_city in this subagent. Mirror caller's language (English / Hindi / Gujarati).
- **Voice-channel constraints:** short turns (≤ 2 sentences), no lists read out, no markdown, ASCII digits.

### Subagent 2: `job_match_apply`

- **Goal:** Search jobs, present picks, fill remaining profile fields, update profile, apply.
- **Tools allowed:** `fetch_jobs`, `update_profile`, `apply_job`.
- **System prompt content:** on first turn here, immediately call `fetch_jobs` with the stored trade (capitalised when sent — e.g. "Plumber"); explain that city filtering happens locally; read out top 1–3 results that match `preferred_city`; ask user to pick by number or name; once picked, ensure `name` is captured (required) and optionally invite (not require) `age`, `experience_years`, `open_to_remote`; call `update_profile` once before `apply_job`; finally call `apply_job` and confirm the returned `application_id` to the caller. Mirror caller's language.
- **Behavioural rules:** never call `apply_job` before `update_profile` has succeeded at least once in the session; never re-call `fetch_jobs` unless the user explicitly asks for a different trade or location; if zero matches after city filter, fall back to reading the unfiltered top result and explain.

### Other agent_core fields

- `agent.primary_model`, `agent.fallback_model`: copy from KKB.
- `conversation.persona`: short paragraph describing "Blue Dots Agent" — warm, polite, India job-market context, voice-first.
- `language_normalisation.default_language: english`, `supported_languages: [english, hindi, gujarati]`, `unsupported_language_message` translated/written in English with a short Hindi fallback line.
- `intent_taxonomy`: lift KKB's list verbatim (job_search, language_switch_request, etc.) — same job-seeker domain so it transfers cleanly.

---

## 3. Action Gateway — tool catalogue

Single shared bearer secret across all four tools: `BLUE_DOTS_API_KEY`. All four tools register under `tools:` in `action_gateway.yaml` with `type: rest_api`. The `rest_api` adapter substitutes `{phone}` and `{profile_item_id}` from session state — neither is supplied by the LLM.

### 3.1 `fetch_profile`

- **Category:** `read` (no consent gate; profile read is implicit in placing the call).
- **Base URL:** `http://65.2.66.144:2742`
- **Auth:** `bearer`, `secret_env: BLUE_DOTS_API_KEY`
- **Endpoint:** `GET /api/v1/seeker/profile/{phone}` — adapter substitutes `{phone}` from session.
- **Behaviour:** returns existing profile if found, otherwise creates a blank `profile_1.0` item and returns it. Response always carries `item_id`, `item_state`, `item_latitude`, `item_longitude`.
- **Projection (LLM-visible):**
  - `item_id`, `name` ← `item_state.name`, `trade` ← `item_state.skills[0]` (best-effort first skill), `preferred_city` ← `item_state.preferred_city`, `experience_years` ← `item_state.experience_years`, `open_to_remote` ← `item_state.open_to_remote`.
- **Description (LLM-facing):** instructs the LLM to call it once on session start, summarise findings in one sentence, then ask for missing trade/city.

### 3.2 `fetch_jobs`

- **Category:** `read`.
- **Base URL:** `http://65.2.66.144:2742`
- **Auth:** `bearer`, `secret_env: BLUE_DOTS_API_KEY`
- **Endpoint:** `GET /api/v1/network/item/fetch`
- **Static params:** `item_network=blue_dot`, `item_domain=provider`, `item_type=job_posting_1.0`, `limit=100`, `offset=0`.
- **Agent params:**
  - `item_state.role` (string, required) — trade in capitalised form (e.g. `Plumber`). LLM description tells it to capitalise.
- **Local filter:** API does not multi-filter reliably; LLM is instructed in the subagent prompt to filter the projected result by `preferred_city` (case-insensitive) before reading out.
- **Projection:** `list_key: items`, fields per element:
  - `item_id` ← `item_id`
  - `role` ← `item_state.role`
  - `company` ← `item_state.company_name`
  - `city` ← `item_state.city`
  - `salary_range` ← `item_state.salary_range`
  - `employment_type` ← `item_state.employment_type`
- **`max_size_chars`:** 4000 (same as KKB market lookup).

### 3.3 `update_profile`

- **Category:** `write` (gated by start-of-session consent, not per-call).
- **Base URL:** `http://65.2.66.144:2742`
- **Auth:** `bearer`, `secret_env: BLUE_DOTS_API_KEY`
- **Endpoint:** `PATCH /api/v1/item/{profile_item_id}` — adapter substitutes `{profile_item_id}` from session memory.
- **Body shape:** JSON with a single `item_state` object. Send only fields the user has confirmed; never send empty string. Omit `item_instance_url`, `item_schema_url`, `item_latitude`, `item_longitude` (let upstream keep what it has).
- **Agent params (all optional; LLM picks which to include based on what the user said):**
  - `name` (string), `skills` (array of strings — wraps trade), `preferred_city` (string), `age` (integer), `experience_years` (integer), `open_to_remote` (boolean).
- **Response projection:** `applied_fields` list so the LLM can confirm in one line.

### 3.4 `apply_job`

- **Category:** `write`.
- **Base URL:** `http://65.2.66.144:2742`
- **Auth:** `bearer`, `secret_env: BLUE_DOTS_API_KEY`
- **Endpoint:** `POST /api/v1/action/perform`
- **Body shape (assembled by adapter from session memory + the one LLM-supplied job_id):**

```json
{
  "action_name": "apply",
  "source_item": {
    "item_network": "blue_dot",
    "item_domain": "seeker",
    "item_type": "profile_1.0",
    "item_id": "{profile_item_id}"
  },
  "target_item": {
    "item_network": "blue_dot",
    "item_domain": "provider",
    "item_type": "job_posting_1.0",
    "item_id": "{job_item_id}",
    "item_instance_url": "http://65.2.66.144:2742/"
  },
  "requirements_snapshot": {
    "job_id": "{job_item_id}",
    "cover_note": "",
    "resume_url": ""
  }
}
```

- **Agent params:**
  - `job_item_id` (string, required) — from a prior `fetch_jobs` projection result; the LLM passes the chosen one.
- **Projection:** `application_id`, `status`, plus an echo of `target_item.item_id` so the LLM can confirm naturally.

---

## 4. Memory Layer — state shape

Mirror KKB's `memory_layer.yaml` for backend wiring (Redis + Memgraph + persistent SQLite). Replace the Context Graph node definitions with:

```yaml
context_graph:
  nodes:
    seeker:
      attributes:
        phone: string                # session identity
        profile_item_id: string      # from fetch_profile
        name: string
        trade: string                # captured during profile_intake
        preferred_city: string       # captured during profile_intake
        age: integer
        experience_years: integer
        open_to_remote: boolean
        skills: array<string>
        consent_given: boolean       # one-time start-of-session consent
    job_search:
      attributes:
        last_results: array<object>  # compact dicts: item_id, role, company, city, salary_range, employment_type
        selected_job_item_id: string
        applied_job_item_ids: array<string>
        applications: array<object>  # {application_id, target_item_id, applied_at}
```

The rest_api adapter substitutions:
- `{phone}` → `seeker.phone` (set at session creation from Reach Layer voice channel identity).
- `{profile_item_id}` → `seeker.profile_item_id` (set by `fetch_profile` post-call).

Write-back rules:
- After `fetch_profile`: persist `profile_item_id` plus any `item_state` fields that came back populated.
- After `update_profile`: merge `applied_fields` into the seeker node.
- After `apply_job`: append to `applications` and `applied_job_item_ids`.

---

## 5. Trust Layer

Mirror `dev-kit/configs/kkb/trust_layer.yaml` verbatim except:

- **Persona references:** swap "KKB" with "Blue Dots Agent".
- **Consent text:** rewrite DPDP consent line for English/Hindi/Gujarati covering "we will save your profile and submit job applications on your behalf during this call". One consent block, one-time at session start, covering `update_profile` and `apply_job`. No per-apply re-prompt.
- **Topic firewall, escalation topics, content/output rules:** unchanged — KKB's set was authored for the job-seeker domain so it transfers as-is.
- **HiTL escalation:** keep KKB's log-only backend. No live operator integration in this scope.

---

## 6. Knowledge Engine

No KE retrieval is used. The `knowledge_retrieval` internal tool is **not** listed in either subagent's `tools` block — therefore KE is never hit at runtime.

`knowledge_engine.yaml` is still authored (so the configuration is structurally complete and KE can be enabled later without YAML edits):
- Minimal `glossary` block, no domain documents declared.
- `static_kb` config present but pointing at an empty `documents:` list.
- No ingestion ledger entries.

The KE service may be omitted from `docker-compose.dev.yml` for the Blue Dots deployment without affecting any other block.

---

## 7. Observability Layer

Mirror `dev-kit/configs/kkb/observability_layer.yaml` with `domain: blue_dot`. Outcome events to track:

- `consent_given_at_start`
- `profile_fetched` (new vs returning — distinguished by whether `item_state` was empty)
- `gap_collected` (trade / preferred_city)
- `jobs_fetched` (with count, count_after_city_filter)
- `job_selected`
- `profile_updated` (with field count)
- `application_submitted` (with application_id)
- `drop_off` (with stage: pre-consent / pre-trade / pre-city / pre-pick / pre-apply)

OTel + audit log paths unchanged from KKB.

---

## 8. Reach Layer

Voice channel only; web is not configured.

- `channels.voice.enabled: true`
- `channels.web.enabled: false`, `routing_only: true` (so the Reach Layer web container starts but only serves the routing endpoints — or it can be excluded from compose entirely; either works).
- `channels.cli.enabled: false`
- Assembly mode: `session` (VAD-driven), forced by voice.
- Identity: caller phone (E.164) → injected as `seeker.phone` at session creation.
- Voice TTS/ASR locale defaults to `en-IN`, with caller-language inference for `hi-IN`, `gu-IN`.

---

## 9. File layout — what to create under `dev-kit/configs/blue-dots-economy/`

```
dev-kit/configs/blue-dots-economy/
├── action_gateway.yaml      # 4 tools above + observability.domain
├── agent_core.yaml          # persona, language, manager + 2 subagents, intent taxonomy
├── knowledge_engine.yaml    # minimal — no documents, no glossary content
├── memory_layer.yaml        # context_graph seeker + job_search nodes
├── observability_layer.yaml # domain: blue_dot, outcomes list
├── reach_layer.yaml         # voice only
└── trust_layer.yaml         # KKB rules + Blue Dots consent text in en/hi/gu
```

No `_meta/` directory (this is hand-authored, not dev-kit-generated).

Deployment: `automation/docker/docker-compose.dev.yml` parameterised by `DOMAIN=blue-dots-economy`. Knowledge Engine service may be commented out; all other blocks run unchanged.

`.env` additions:
- `BLUE_DOTS_API_KEY=<bearer token>`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` per chosen `primary_model`.

---

## 10. Open items deferred to implementation

- `fetch_profile` endpoint path is the assumed `/api/v1/seeker/profile/{phone}`; if the real endpoint differs, update only `action_gateway.yaml` — no other block is affected.
- If `fetch_jobs` returns large payloads under load, revisit the projection `max_size_chars` and `limit`.
- One-time consent recording today writes to Trust Layer's in-process consent store (PoC limitation); production hardening is out of scope for this configuration.

---

## 11. Out of scope

- Web/CLI channels.
- Knowledge Engine retrieval.
- Multi-subagent splits beyond the two described.
- Cross-session profile sync beyond what Memory Layer already provides.
- Multilingual ASR/TTS infrastructure changes (config-level only — uses existing pipeline).
- Production-grade consent persistence.
