# KKB domain — ONEST → Signals-DPG integration design

**Status:** proposed — no code or config changed yet
**Date:** 2026-09-15
**Repo:** `ai-diffusion-dpg`
**Base:** `main` @ `51d36f1`
**Proposed branch:** `feat/kkb-signals-integration`
**Target upstream:** Signals-DPG test cluster `https://dev-signals.serveirc.com`, network `blue_dot`

---

## 1. Purpose

The `kkb` domain's job-market tool currently calls the **ONEST** network
(`https://up-onest-lite-bap.dhiway.net/api/v1/search/top`), and its three
profile/apply tools call **Action Gateway's own `/mock/*` fixtures**. This
design replaces all four with real **Signals-DPG** APIs on the test cluster,
so the KKB agent runs a genuine end-to-end journey: look up the caller's
profile → search live job postings → save/update the profile → submit an
application.

**Validation channel for this phase is Reach Layer *web*** (`:8005`), not
voice — the voice (Vobiz/Raya) credentials have expired. The design keeps the
voice path working unchanged so switching back later is a config flip, not a
rewrite.

### In scope
- Repoint all 4 KKB Action Gateway tools at Signals / signals-search.
- Map KKB's profile vocabulary onto the real `blue_dot / seeker / profile_1.0` schema.
- Adjust the KKB prompts that make now-false assumptions about the data.
- Web-channel identity so `{user_id}` resolves to the caller's phone.

### Out of scope
- `dev-kit/configs/blue-dots-economy/*` — untouched by this work.
- Any Python change in `action_gateway/`, `agent_core/` or `reach_layer/`.
  Everything below is achievable with the adapter features that already exist.
- Voice channel re-enablement (see §10, deferred).
- Signals-side changes. We consume the cluster as it is.

---

## 2. Decisions taken

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| D1 | Which API backs the job search | **signals-search `POST /v1/search`, text-only** | KKB's tool contract is already a natural-language `query_text`. `/v1/search` is the only option that returns a relevance `score`, which KKB's `ranking_order: [match_score, …]` rule needs. Exact-city filtering is impossible anyway (see F1). |
| D2 | How much to wire | **All four tools** | A read-only slice would leave the apply path — the most fragile part — untested. |
| D3 | How the phone reaches the tools on web | **Type the phone as the web user ID** | The KKB web UI already prompts for a user ID. Typing `919920067821` makes `{user_id}` resolve identically on web and voice, so one config serves both channels. |
| D4 | Consent payload shape | **Send both `compliance[]` and `terms_accepted`/`privacy_accepted`** | The cluster's build is ambiguous (see F2). The newer build ignores the deprecated pair; the older build ignores the unknown array. Sending both is correct on either. |
| D5 | Tool ID `onest_market_lookup` | **Renamed to `job_market_lookup`** | No part of the domain talks to ONEST any more, so the name was actively misleading. Renamed across `action_gateway.yaml` and every `agent_core.yaml` prompt reference. |
| D6 | `channel` value on participant writes | **`self`**, via a static param | The schema enum is `bulk \| link \| voice \| self`. `web` is **not** a legal value. One-line switch to `voice` later. |

---

## 3. Verified facts about the target cluster

Everything below was probed live on 2026-09-15 against `https://dev-signals.serveirc.com`.
These are measurements, not assumptions.

| ID | Probe | Result |
|---|---|---|
| V1 | `POST /api/v1/action/perform` body `{}` | `400 FST_ERR_VALIDATION` — `body/action_type`, `body/source_item`, `body/target_item` expected → **single object contract** |
| V2 | `POST /api/v1/action/perform` body `[]` | `400` — `"body/ Invalid input: expected object, received array"` |
| V3 | `POST /api/v1/action/perform/bulk` body `[]` | `400 BULK_EMPTY_ARRAY` → the bulk route exists and takes the array |
| V4 | `POST /api/v1/action/perform` with zero-UUIDs and **no** `consent` | `422 {"results":[{"index":0,"status":"error","error":"USER_NOT_FOUND",…}],"summary":{…}}` → `consent` is **not** schema-required, and the single route still returns the `{results[],summary}` envelope |
| V5 | `GET /api/v1/admin/participant?phone_number=…` + `x-api-key` + `x-acting-org-id` | `200 { user_id, user_consent{terms_accepted,privacy_accepted,has_age}, items[] }` |
| V6 | `POST /api/v1/network/item/fetch_local` (no auth) | `200`, `meta.total = 135` blue_dot job postings |
| V7 | `GET /signals-search/health` | `{"status":"ok"}` |
| V8 | `POST /signals-search/v1/search` with `searchApiKey` | `200`, items carry `score`, `item_locations[].lat/lng`, `lifecycle_status`; envelope is `{context, message:{items[], meta{total,limit,offset}}}` |
| V9 | `POST /api/v1/admin/participant` body `{}` | `400` — requires `name` and `channel ∈ bulk\|link\|voice\|self` |
| V10 | `GET /api/v1/network/schema/blue_dot/seeker/profile_1.0` | Full field list; `required: ["name","phone"]` |
| V11 | `GET /api/v1/network/schema/blue_dot/provider/job_posting_1.0` | Full field list; `required: ["hiringManagerName","hiringManagerPhoneNumber"]` |

### Findings that shape the design

**F1 — Job location is PII-masked.** `item_state.jobProviderLocation` comes back
as `"B***"` on **both** `fetch_local` and `/v1/search`, even with a valid API
key. Consequences:
- The blue-dots approach (server-side exact-match filter on
  `jobProviderLocation`) cannot work here.
- The bot **cannot read a job's city aloud**. KKB's current "deep dive"
  prompt format does exactly that and must change.
- The only unmasked geography is `item_locations[].lat/lng`, which we are
  deliberately not using in this phase (D1).

**F2 — The cluster is on a mixed build.** It has the *new* single-object
`/action/perform` contract (V1–V4) but its `GET /admin/participant` still
returns the *old* `user_consent{}` object rather than `compliance[]` (V5).
Whether a `compliance` array is honoured on write cannot be determined without
creating a participant. D4 makes this moot for the request; §11 has the
verification step.

**F3 — Test-cluster data is sparse.** Many job postings carry no
`salaryMin`/`salaryMax`. The prompt's "always present exact salary ranges"
instruction will frequently have nothing to present.

---

## 4. How the wiring works (mechanics that justify the YAML)

Read this before reviewing §5 — it explains why the configs are shaped the way
they are. All of it is existing, unmodified `RestApiAdapter` behaviour.

1. **Tool definitions come from Action Gateway, not Agent Core.**
   `ToolRegistry.__init__` calls `gateway.list_available_tools()`, built by
   `RestApiAdapter.get_tool_definitions()` from `action_gateway.yaml`. Only
   params with `source: agent` become the LLM-visible `input_schema`;
   `source: static` params are injected server-side and hidden from the model.

2. **`agent_core.yaml`'s `connectors.read` / `.write` blocks are not consumed
   at runtime.** Only `connectors.internal` is read (for `knowledge_retrieval`,
   in `ToolRegistry._load_internal_tools`). The rich `invocation_rules` there
   are schema-validated and serve as documentation. **Therefore the functional
   changes all land in `action_gateway.yaml`**; `agent_core.yaml` edits are for
   consistency and for the subagent prompts.

3. **Session identity is injected automatically.** For non-GET methods the
   adapter builds `template_values = {session_id, user_id, **all_params}` and
   renders `body_template` against it. For any method, `{…}` placeholders in
   the endpoint `path` are substituted the same way. The LLM never has to know
   or transcribe the caller's identity.

4. **Static params are visible to `body_template`.** `all_params` merges agent
   params with `source: static` params, so a literal like `channel` can be a
   one-line-switchable static rather than hardcoded in the template (D6).

5. **A missing placeholder drops its enclosing field.** In
   `_render_body_template`, a value that resolves to `None` or `""` removes the
   whole key from the parent dict, and a dict pruned to empty is itself
   dropped. This is what makes **create-vs-update on one endpoint** work:
   omit `item_id` → create, pass it → update.

6. **Projections accept dot-paths.** `_get_nested` splits on `.`, so
   `list_key: message.items` correctly reaches into the signals-search
   envelope. `_apply_projection` with a `list_key` returns a list of flat
   `{target: value}` dicts; without one it projects the response root.

7. **Workflow validation.** `WorkflowLoader._validate_tool_names` checks every
   name in `global_tools` against `ToolRegistry.get_tool_names() ∪
   connectors.internal`. Because all four tool **IDs are unchanged**, this
   validation continues to pass with no edit.

---

## 5. Tool-by-tool specification

### Summary

| KKB tool | Today | After this change | Auth |
|---|---|---|---|
| `job_market_lookup` (was `onest_market_lookup`) | `POST up-onest-lite-bap.dhiway.net/api/v1/search/top` | `POST {searchBaseUrl}/v1/search` | `x-api-key: SIGNALS_SEARCH_API_KEY` |
| `get_profile` | `GET action_gateway:9999/mock/profile/{user_id}` | `GET {baseUrl}/api/v1/admin/participant?phone_number={user_id}` | `x-api-key: SIGNALS_API_KEY` + `x-acting-org-id: SIGNALS_ORG_ID` |
| `update_profile` | `POST action_gateway:9999/mock/profile/{user_id}` | `POST {baseUrl}/api/v1/admin/participant` | same |
| `apply_job` | `POST action_gateway:9999/mock/apply` | `POST {baseUrl}/api/v1/action/perform` | same |
| `knowledge_retrieval` | Knowledge Engine | **unchanged** | — |

Where `baseUrl = https://dev-signals.serveirc.com` and
`searchBaseUrl = https://dev-signals.serveirc.com/signals-search`.

---

### 5.1 `job_market_lookup` — live job discovery

**Signals API:** `POST {searchBaseUrl}/v1/search` (signals-search semantic discovery)
**Category:** `read` (no Trust Layer consent gate)
**Auth:** `x-api-key: ${SIGNALS_SEARCH_API_KEY}` — **a different key from the
Signals API key.** Do not reuse `SIGNALS_API_KEY` here.

**Request**

```json
{
  "context": {
    "messageId": "{session_id}",
    "networkId":  "blue_dot",
    "domain":     "provider",
    "itemType":   "job_posting_1.0"
  },
  "message": {
    "intent":     { "textSearch": "{query_text}" },
    "pagination": { "limit": 10, "offset": 0 }
  }
}
```

`messageId` is bound to `{session_id}` so a search can be traced back to the
conversation in Loki/Jaeger.

**LLM-visible params**

| Param | Source | Type | Required | Note |
|---|---|---|---|---|
| `query_text` | agent | string | **yes** | One natural-English sentence including trade **and** city. Unchanged from today's contract. |

All of today's structured params (`industry`, `age`, `languages`,
`preferred_work_mode`, `monthly_in_hand`, `work_hours_per_day`, `role`,
`location`) are **removed** from the tool schema. Under D1 they have nowhere
to go: the corresponding `item_state` fields are either masked (`location`) or
not reliably populated on the cluster. Keeping them would let the model pass
parameters that silently do nothing.

**Response** (`200`)

```json
{ "context": {...},
  "message": {
    "items": [ { "item_id": "...", "item_state": {...},
                 "item_locations": [{"lat":…, "lng":…}],
                 "lifecycle_status": "live", "score": 0.5489 } ],
    "meta": { "total": 3, "limit": 10, "offset": 0 } } }
```

**Projection** — `list_key: message.items`

| LLM field | Source path |
|---|---|
| `job_id` | `item_id` |
| `role` | `item_state.role` |
| `company` | `item_state.jobProviderName` |
| `positions` | `item_state.positions` |
| `nature_of_job` | `item_state.natureOfJob` |
| `salary_min` / `salary_max` | `item_state.salaryMin` / `salaryMax` |
| `stipend_min` / `stipend_max` | `item_state.stipendMin` / `stipendMax` |
| `task_rate_min` / `task_rate_max` | `item_state.taskRateMin` / `taskRateMax` |
| `experience_required` | `item_state.candidateExperienceType` |
| `work_experience_years` | `item_state.workExperienceYears` |
| `min_qualification` | `item_state.minEducationalInstitute` |
| `job_type` | `item_state.typeOfJob` |
| `job_category` | `item_state.jobCategory` |
| `working_hours` | `item_state.workingHours` |
| `status` | `lifecycle_status` |
| `match_score` | `score` |

**Deliberately not projected:** `jobProviderLocation` (masked — F1),
`hiringManagerName` / `hiringManagerPhoneNumber` / `hiringManagerEmail` (PII;
KKB's prompt already forbids speaking them), `item_locations` (GPS; the prompt
forbids speaking coordinates), `companyGstOrUdyamNumber`.

`match_score` **is** projected because KKB's `ranking_order` rule ranks on it,
and **is** already on the prompt's "never speak aloud" list.

---

### 5.2 `get_profile` — look the caller up by phone

**Signals API:** `GET {baseUrl}/api/v1/admin/participant?phone_number={user_id}`
**Category:** `read`
**Auth:** `x-api-key: ${SIGNALS_API_KEY}` + `x-acting-org-id: ${SIGNALS_ORG_ID}`

`{user_id}` is substituted from the session by the adapter's path templating
(mechanic 3). The LLM supplies **no parameters at all** — `params: []`.

> **Phone format.** This endpoint wants the country-code form without a `+`:
> `919920067821`. That is what D3 has the web user type as their user ID.
> Note the asymmetry with §5.3, whose body wants `+919920067821`.

**Response** (`200`, verified empty case V5)

```json
{ "user_id": "<uuid|null>",
  "user_consent": { "terms_accepted": true, "privacy_accepted": true, "has_age": true },
  "items": [ /* the caller's seeker profiles */ ] }
```

**Projection** — `list_key: items`, one row per profile

| LLM field | Source path | Purpose |
|---|---|---|
| `profile_item_id` | `item_id` | → `apply_job.source_item.item_id`, and → `update_profile.profile_item_id` for the update path |
| `acting_as_user_id` | `created_by` | → `apply_job.acting_as_user_id` |
| `name` | `item_state.name` | |
| `phone` | `item_state.phone` | |
| `age` | `item_state.age` | |
| `gender` | `item_state.gender` | |
| `location` | `item_state.location` | the *seeker's* own location — **not** masked, unlike a job's |
| `trade` | `item_state.nameOfJobRolesInterestedIn` | |
| `work_experience` | `item_state.workExperience` | |
| `experience_years` | `item_state.workExperienceYearsConditional` | |
| `education` | `item_state.educationCategory` | |
| `languages` | `item_state.languageSpoken` | |
| `job_nature` | `item_state.natureOfJobsInterestedIn` | |
| `salary_expected` | `item_state.salaryMin` | |
| `updated_at` | `updated_at` | multi-profile disambiguation — newest wins |

**Design note — why `created_by` and not the top-level `user_id`.** A
projection with `list_key` can only read fields *inside each list element*, so
the response root's `user_id` is not reachable in the same projection. Each
item carries `created_by`, which is the owning participant's UUID — the same
value `apply_job` needs as `acting_as_user_id`. This is the same trick
`blue-dots-economy`'s `fetch_profile` uses (`source_item_owner: created_by`).

> **To verify at test time (§11-T2):** the probe returned `items: []` for the
> test phone, so the populated element shape is inferred from `fetch_local`
> (which does carry `created_by`) and from the Postman collection's
> documentation. If `created_by` turns out to be absent on
> `/admin/participant` items, the fallback is to take `acting_as_user_id`
> from `update_profile`'s response instead, which returns it at the root.

**New-user case:** `items: []` → projection yields `[]`. The agent then treats
the caller as new and goes to `update_profile` (create).

---

### 5.3 `update_profile` — create **or** update the seeker profile

**Signals API:** `POST {baseUrl}/api/v1/admin/participant`
**Category:** `write` → **Trust Layer consent gate applies** before execution
(`ToolRegistry._build_consent_set` treats `write`/`identity` as consent-requiring).
**Auth:** `x-api-key: ${SIGNALS_API_KEY}` + `x-acting-org-id: ${SIGNALS_ORG_ID}`

One endpoint serves both operations, distinguished by `item_id`:
- `profile_item_id` **absent** → the placeholder drops the `item_id` key
  (mechanic 5) → upstream **creates**.
- `profile_item_id` **present** → upstream **updates** that profile.

**Request**

```json
{
  "name":         "{name}",
  "phone_number": "+{user_id}",
  "age":          "{age}",
  "channel":      "{channel}",          // static param, value "self"  (D6)
  "source_id":    "{session_id}",
  "network":      "blue_dot",
  "domain":       "seeker",
  "item_type":    "profile_1.0",
  "item_id":      "{profile_item_id}",  // dropped when absent → create
  "compliance": [
    { "key": "user_terms",       "value": true },
    { "key": "user_privacy",     "value": true },
    { "key": "profile_creation", "value": true }
  ],
  "terms_accepted":   true,             // legacy pair, for the older build (D4)
  "privacy_accepted": true,
  "item_state": { /* see §6 */ }
}
```

**LLM-visible params**

| Param | Type | Required | Maps to |
|---|---|---|---|
| `name` | string | **yes** | `name` + `item_state.name` — schema-required (V10) |
| `profile_item_id` | string | no | `item_id` — presence selects update |
| `trade` | string | no | `item_state.nameOfJobRolesInterestedIn` |
| `location` | string | no | `item_state.location` |
| `age` | integer | no | top-level `age` + `item_state.age` |
| `gender` | string | no | `item_state.gender` — enum |
| `work_experience` | string | no | `item_state.workExperience` — enum |
| `experience_years` | string | no | `item_state.workExperienceYearsConditional` — enum |
| `education` | string | no | `item_state.educationCategory` — enum |
| `certifications` | string | no | `item_state.certificationDetails` |
| `languages` | array | no | `item_state.languageSpoken` |
| `job_nature` | array | no | `item_state.natureOfJobsInterestedIn` |
| `monthly_in_hand_expected` | integer | no | `item_state.salaryMin` |

Static: `channel = "self"`.

> **Partial-update ambiguity.** The `blue-dots-economy` config asserts the
> upstream does **not** support partial updates ("pass the full `item_state`
> every time"), while the Postman collection's request 3b says the update
> "sends only the `updateItemState` fields (merged onto the stored profile)".
> These contradict. This design takes the **safe** reading: the tool
> description instructs the model to pass the full known `item_state` on every
> update, which is correct under either behaviour. §11-T4 settles it
> empirically.

**Projection** (root, no `list_key`)

| LLM field | Source |
|---|---|
| `acting_as_user_id` | `user_id` |
| `user_existed` | `user_existed` |
| `profiles` | `items` |

On **create** the `items` array contains all of the user's profiles, the new
one last by `created_at`. On **update** it contains just the updated profile.

---

### 5.4 `apply_job` — submit the application

**Signals API:** `POST {baseUrl}/api/v1/action/perform` — **single object** (V1, V2)
**Category:** `write` → Trust Layer consent gate applies
**Auth:** `x-api-key: ${SIGNALS_API_KEY}` + `x-acting-org-id: ${SIGNALS_ORG_ID}`

**Request**

```json
{
  "action_type": "apply",
  "source_item": {
    "item_network": "blue_dot", "item_domain": "seeker",
    "item_type": "profile_1.0", "item_id": "{profile_item_id}"
  },
  "target_item": {
    "item_network": "blue_dot", "item_domain": "provider",
    "item_type": "job_posting_1.0", "item_id": "{job_id}",
    "item_instance_url": "{instance_url}"   // static, = baseUrl
  },
  "requirements_snapshot": {
    "role": "{role}", "location": "{location}",
    "workExperience": "{work_experience}"
  },
  "acting_as_user_id": "{acting_as_user_id}",
  "consent": { "acknowledged": true, "version": 1 }
}
```

`consent` is not schema-required (V4) but is sent because KKB's prompt already
requires explicit spoken consent before applying, and recording it upstream is
the honest representation of that.

**LLM-visible params**

| Param | Required | Origin in the conversation |
|---|---|---|
| `profile_item_id` | **yes** | `get_profile.profile_item_id`, or `update_profile.profiles[…].item_id` after a create |
| `job_id` | **yes** | `job_market_lookup.job_id` for the job the user chose |
| `acting_as_user_id` | **yes** | `get_profile.acting_as_user_id` or `update_profile.acting_as_user_id` |
| `role` | no | snapshot only |
| `location` | no | snapshot only |
| `work_experience` | no | snapshot only |

Static: `instance_url = https://dev-signals.serveirc.com`.

**Response** — the `{results[],summary}` envelope on both success and business
failure (V4). HTTP `422` carries per-item errors; the adapter surfaces the body
either way.

**Projection** — `list_key: results`

| LLM field | Source |
|---|---|
| `status` | `status` |
| `action_id` | `action_id` |
| `action_status` | `action_status` |
| `error` | `error` |
| `message` | `message` |

Known error codes to expect: `USER_NOT_FOUND` (bad `acting_as_user_id`),
and item-not-found variants for a stale `profile_item_id` / `job_id`.

---

## 6. Data model mapping

KKB's profile vocabulary vs. the real `blue_dot / seeker / profile_1.0` schema
(fetched live, V10; `required: ["name","phone"]`).

| KKB concept | Signals `item_state` field | Type | Notes |
|---|---|---|---|
| name | `name` | string | **required** |
| phone | `phone` | string | **required**; from `{user_id}`, bare `91…` form |
| trade / trade_or_stream | `nameOfJobRolesInterestedIn` | string | |
| location | `location` | string | seeker location is *not* masked |
| age | `age` | integer | |
| gender | `gender` | enum | `Male \| Female \| Other \| Don't want to share` |
| years_experience | `workExperienceYearsConditional` | enum | `< 1 Year \| 1 Year \| 2 Years \| 3 Years \| 3-5 Years \| 5-10 Years \| 10-15 Years \| 15+ Years` |
| (experience bucket) | `workExperience` | enum | `Fresher \| Worked before \| Returning after a break` |
| education_level | `educationCategory` | enum | `School \| PU College \| College \| ITI \| Other Vocational Training \| Polytechnic \| …` |
| certifications | `certificationDetails` | string | free text; `itiTrade` is a large controlled enum, not worth forcing |
| languages / language_skills | `languageSpoken` | array | |
| (job nature) | `natureOfJobsInterestedIn` | array | `Internship \| Apprenticeship \| Full-time \| Flexible` |
| monthly_in_hand_expected | `salaryMin` | number | |

**Not mapped — `preferred_work_mode`.** KKB uses
`on-site-no-shift / on-site-shifts / remote / hybrid`; the Signals field
`typeOfJobPreferred` is `WFH / Desk / On-Field / Standing`. These are different
axes (shift pattern vs. physical posture), and a lossy guess would write wrong
data into a real participant record. The field is **dropped** from the write.
KKB's NLU may still extract it into session state; it simply is not persisted.

**Not mapped — KKB session-only fields.** `informal_skills`,
`digital_literacy`, `income_urgency`, `max_commute_km`, `availability_hours`,
`mobility_owned`, `dependents`, `primary_goal`, `sector_preference`,
`open_to_training`, `growth_horizon`, `distance_km`, `disability_status` have
no `profile_1.0` counterpart. They stay in Memory Layer session state and
inform the conversation without being written upstream.

---

## 7. End-to-end sequence (web test flow)

```
Browser (Reach Layer Web :8005)
  │  user types phone "919920067821" as their user ID   ← D3
  ▼
POST /chat → Agent Core POST /process_turn          (web = direct mode)
  │
  ├─ Memory read → consent gate (ask_for_consent: true) → NLU
  ├─ Trust /check/input → Language Normalisation
  └─ Manager Agent → subagent `enquiry` → LLM

  TURN A — identify the caller
    LLM → get_profile()                                      [no params]
      AG → GET /admin/participant?phone_number=919920067821
           x-api-key + x-acting-org-id
      ← items[]  → new user      → collect name/trade/location
      ← items[n] → returning     → newest updated_at wins;
                                   hold profile_item_id + acting_as_user_id

  TURN B — show the market                        (needs trade + location)
    LLM → job_market_lookup(query_text="electrician jobs in Bengaluru…")
      AG → POST {searchBaseUrl}/v1/search   x-api-key: SEARCH key
      ← message.items[] → top 3 read out, each with job_id held

  TURN C — persist the profile           (subagent `commitment` on entry)
    LLM → update_profile(name=…, trade=…, location=…, …)
      Trust Layer consent gate  (category: write)
      AG → POST /admin/participant
           item_id absent → CREATE ; present → UPDATE
      ← user_id (= acting_as_user_id), items[].item_id (= profile_item_id)

  TURN D — apply                          (after explicit spoken consent)
    LLM → apply_job(profile_item_id, job_id, acting_as_user_id)
      Trust Layer consent gate
      AG → POST /action/perform            single object
      ← { results:[{action_status:"created", action_id}], summary }
      → confirm to user → route to subagent `post_applied`

  Trust /check/output → deliver → [async] Memory write + Observability emit
```

**Tool-round budget.** KKB sets `max_tool_rounds: 2` (GH-206, to cap voice
latency). The sequence above deliberately spreads the four calls across four
*turns*, so no single turn needs more than one tool round. If testing shows the
model trying to chain `get_profile → job_market_lookup → apply_job` inside
one turn, the options are to raise the cap to 3 (blue-dots uses 3) or to tighten
the prompt. Noted as a risk, not pre-emptively changed.

---

## 8. Changes outside the connector definitions

### 8.1 `dev-kit/configs/kkb/agent_core.yaml`

| Location | Change | Why |
|---|---|---|
| `connectors.read.job_market_lookup` | Rewrite the description and `input_schema` to the single `query_text` param; drop the ONEST-era structured properties | Keep the documented contract aligned with what AG actually registers (mechanic 2) |
| `connectors.write` | Add `get_profile` / `update_profile` / `apply_job` entries (documentation parity — not runtime-consumed) | These three tools are in `global_tools` but have never been documented in `connectors` |
| `enquiry.system_prompt` → "On session start" | "call `get_profile` **with the user's phone number**" → `get_profile` takes **no** parameters; the phone comes from the session | The instruction is currently false and invites the model to invent a param |
| `enquiry.system_prompt` → "Market picture delivery" | `is_active=false, status≠open` → `status != "live"`; add "job location is not available — never state a job's city" | Signals uses `lifecycle_status: "live"`; F1 |
| `commitment.system_prompt` → "Deep dive" | Remove `[locality], [city] — लगभग [distance] किलोमीटर दूर` from the spoken format | F1 — neither city nor distance is available |
| `commitment.system_prompt` → "Pay / distance concerns" | Drop "re-run ONEST with tighter radius"; distance-based refinement is not available | D1 is text-only; no spatial clause |
| `commitment.system_prompt` → "Apply" | State the three IDs `apply_job` needs and where each comes from | The model must carry `profile_item_id` / `job_id` / `acting_as_user_id` across turns |
| all prompts | "ONEST" → "the job network"; tool renamed `onest_market_lookup` → `job_market_lookup` | The upstream is no longer ONEST and the name should not imply it |

### 8.2 `dev-kit/configs/kkb/reach_layer.yaml`

| Key | From | To |
|---|---|---|
| `channels.web.ui.setup_subtitle` | "अपना यूज़र ID दर्ज करें · Enter your user ID…" | ask for the mobile number with the `91` prefix |
| `channels.web.ui.user_id_placeholder` | `e.g. rahul_electrician` | `e.g. 919920067821` |
| `channels.web.ui.user_id_hint` | "Use your name and trade…" | "Enter your mobile number with country code, no +" |

No Reach Layer code changes — the SPA already sends whatever the user types as
`user_id`, and `POST /chat` passes it through to Agent Core.

### 8.4 Removing the last ONEST references

`dev-kit/configs/kkb/` must not name ONEST anywhere — the domain no longer talks to it.

| File | Change | Why |
|---|---|---|
| `action_gateway.yaml`, `agent_core.yaml` | `onest_market_lookup` → `job_market_lookup` (12 references) | D5 |
| `observability_layer.yaml` | `trigger_tool: "onest_apply"` → `"apply_job"` | **Latent bug** — no tool named `onest_apply` has ever been registered, so the `applied` outcome state could never fire |
| `observability_layer.yaml` | `trigger_tool: "onest_status_check"` → `null` (2 states) | No status-check connector exists against Signals. Explicit null beats naming a tool that is never registered |
| `observability_layer.yaml` | metric description "submitted via ONEST" → "to the Signals job network" | |
| `knowledge_engine.yaml` | `./data/onest_market_truth_framing.md` → `./data/market_truth_framing.md` | |

The knowledge-base document itself was rewritten, not just renamed. It is an
`always_include` doc, so it entered **every** KE retrieval, and it instructed the
agent to say *"as per ONEST data"* and to *"encourage the user to apply through
ONEST"* — the most user-visible ONEST leak in the domain. Two of its other
instructions were also factually wrong against Signals: it told the agent to
quote a *growth trend* (Signals returns none) and to offer *nearest-district
data* (the job location is masked). Rewritten to describe the three pay models,
state plainly that location and trend data do not exist, and offer to submit the
application directly.

> `knowledge_engine/data/` is covered by `**/data` in `.gitignore`, so the KB
> documents are local artefacts and this rewrite does not appear in the diff.
> Anyone with an existing checkout must rename their own copy to match the new
> path, or KE ingestion will silently skip it.

### 8.3 What is **not** changing

- No Python in `action_gateway/`, `agent_core/`, `reach_layer/`.
- No `dev-kit/dpg/*.yaml` framework defaults.
- No `dev-kit/dev_kit/schemas/*` mirrors — the KKB change uses only fields
  that already exist in the schemas, so the runtime↔dev-kit sync rule in
  `.claude/rules/runtime-devkit-sync.md` is not triggered.
- `blue-dots-economy` is untouched.
- `knowledge_retrieval` and the Knowledge Engine path are untouched.

---

## 9. Environment and secrets

Three new variables, all present in
`voice_postman_env.test-cluster.blue_dot.json`. They go in the git-ignored
`~/.config/kkb/ai-diffusion.env` (chmod 600), which the `run-ai-diffusion-dpg`
skill sources and exports before `docker compose`:

```
SIGNALS_API_KEY=<apiKey from the test-cluster Postman env>
SIGNALS_ORG_ID=<orgId from the test-cluster Postman env>
SIGNALS_SEARCH_API_KEY=<searchApiKey from the test-cluster Postman env>
```

- `ONEST_API_KEY` becomes unused and can be removed.
- The skill's preflight derives required vars from `secret_env:` refs in the
  domain's `action_gateway.yaml`, so these three are picked up automatically
  once the config lands — and the run aborts early if any is missing.
- These must reach the **`action_gateway` container**, not just the host shell.

**This edit must be made by the operator — `.env*` files cannot be written by
the agent.**

---

## 10. Risks, gaps, deferred work

| # | Item | Impact | Disposition |
|---|---|---|---|
| R1 | Job city is masked (F1) | The bot can name the employer and pay but not the location; a job-search assistant that cannot say *where* the job is, is materially degraded | Accepted for this phase under D1. Fix path: spatial `s_dwithin` on the unmasked `item_locations`, which needs a city→lat/lng source. |
| R2 | `created_by` on `/admin/participant` items is inferred, not observed | If absent, `apply_job` has no `acting_as_user_id` on the returning-user path | §11-T2 verifies. Fallback: always call `update_profile` first and take `user_id` from its root. |
| R3 | Partial vs. full `item_state` update is contradicted by two sources | A partial write could blank stored fields | Mitigated by instructing full-state writes. §11-T4 settles it. |
| R4 | `compliance[]` vs `user_consent{}` build skew (F2) | Consent may not be recorded upstream | Mitigated by D4 (send both). §11-T3 verifies. |
| R5 | `max_tool_rounds: 2` | A turn needing 3 chained calls gets the graceful fallback instead of completing | Observe in testing before changing. |
| R6 | Sparse cluster data (F3) | Many results have no salary; prompt promises exact ranges | Prompt already has an "honest uncertainty" rule; watch for it being ignored. |
| R7 | Writes land on a shared test cluster | Real participants and actions are created | Use a dedicated throwaway phone number per run; never a real user's number. |
| R8 | Voice channel | `server.py` strips the `91` prefix from `caller_id`, but `GET /admin/participant` needs it | **Deferred.** Voice re-enablement needs either a config flag on the strip, or a `+91`-aware template. Out of scope here. |
| R9 | `blue-dots-economy` sends the deprecated `terms_accepted`/`privacy_accepted` and a **top-level array** to `/action/perform` | That domain will 400 on the new contract and records no consent | Out of scope. Flagged separately; see the `action-perform-raya-293` note. |
| R10 | ~~Tool still named `onest_market_lookup`~~ | — | **Resolved.** Renamed to `job_market_lookup`; no ONEST reference remains anywhere in `dev-kit/configs/kkb/`. |

---

## 11. Test plan

Run with the `run-ai-diffusion-dpg` skill: domain `kkb`, scope `core`, voice `no`.
Only `reach_layer_web` publishes a port → http://localhost:8005.

**Preconditions**
- The three env vars of §9 present in `~/.config/kkb/ai-diffusion.env`.
- `docker compose ps` shows all 7 layers healthy.
- A throwaway phone number not belonging to a real user (R7).

| # | Test | Method | Pass criteria |
|---|---|---|---|
| T1 | AG registers the tools | `GET` Action Gateway `/tools` from inside `dpg_net` | 4 tools; `job_market_lookup.input_schema` has exactly `query_text` |
| T2 | `get_profile` returning user | Chat as an existing phone | `200`; projection yields `profile_item_id` **and a non-null `acting_as_user_id`** → resolves R2 |
| T3 | `get_profile` new user | Chat as the throwaway phone | `items: []`, projection `[]`, agent treats caller as new |
| T4 | `update_profile` create | Complete name/trade/location, let it write | `200`; `user_id` + new `item_id` returned; re-run T2 and confirm the stored `item_state` — resolves R3 and R4 |
| T5 | `update_profile` update | Change one field in a later turn | Same `item_id`; **previously-stored fields still present** |
| T6 | `job_market_lookup` | Ask for jobs in a trade | `200`; ≥1 item; `match_score` populated; bot names employer + pay and **never** a city |
| T7 | `apply_job` happy path | Consent, then apply to a listed job | `results[0].action_status == "created"`; `action_id` present |
| T8 | `apply_job` bad id | Force a stale `job_id` | `422` envelope surfaced as a graceful spoken failure, not a crash |
| T9 | Trust consent gate | Attempt a write before consent | Write is blocked by Trust Layer |
| T10 | Full journey | greet → profile → search → save → apply → post_applied | Completes without the model inventing jobs, pay or locations |
| T11 | Observability | Jaeger / logs | `action.execute` spans for all 4 tools; no PII (phone, name) in span attributes |

**Acceptance:** T1–T11 pass, and R2/R3/R4 are each resolved to a recorded
answer rather than left ambiguous.

---

## 12. Files changed

| File | Type | Scale |
|---|---|---|
| `dev-kit/configs/kkb/action_gateway.yaml` | config | 4 tools rewritten (~270 lines → ~350) |
| `dev-kit/configs/kkb/agent_core.yaml` | config | `connectors` block + 2 subagent prompts |
| `dev-kit/configs/kkb/reach_layer.yaml` | config | 3 UI strings |
| `docs/superpowers/specs/2026-09-15-kkb-signals-integration-design.md` | doc | this file |
| `~/.config/kkb/ai-diffusion.env` | secrets | **operator-applied**, 3 vars |

No Python. No tests to update — there are no KKB-config-specific tests in the
suite; validation is via the workflow loader at Agent Core startup (mechanic 7)
plus the §11 manual plan.
