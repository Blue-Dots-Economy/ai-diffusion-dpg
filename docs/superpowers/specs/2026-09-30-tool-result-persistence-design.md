# Tool-Result Persistence — Design

**Date:** 2026-09-30
**Status:** Draft, awaiting review
**Baseline:** `deploy/voicera-vm`. All file and function references are against that branch. This work reaches `main` when that branch is promoted.
**Supersedes the design in:** #329 (Action Gateway tool-call response caching). Resolves the open question in #18.
**Followed by:** Spec B — Session Bootstrap (separate spec, depends on this one).

## 1. Problem

Agent Core keeps tool results only briefly, and the LLM has no reliable memory of what it already fetched.

- A tool result reaches the LLM for the current turn. Across turns, the only thing kept is `recent_tool_exchanges`: the last 3 raw exchanges, up to 4000 chars each. `_prepend_tool_replay` replays them as tool_use/tool_result messages, and #411 adds rounds from interrupted turns. Once three other tools have run, older results are gone. Replayed results carry no age.
- There is no cache. Every tool call hits the live API, and the LLM has to re-plan the call, costing an extra round trip per repeat. Invocation rules try to patch this in prose ("call once, on the first turn…"), which asks the LLM to track state it cannot see.
- `response.session_mapping` (Action Gateway) already lifts unambiguous values from a response into session state. Examples: `profile_item_id` from `items[lifecycle_status=live].item_id`, `acting_as_user_id`, and the consent flags. But the LLM has no way to save a value that depends on the caller's choice, such as which of several profiles to use. Today it has to repeat such IDs in later tool calls. `grounded_params` catches invented IDs only after the fact.

### What #329 got right and what it missed

#329 proposed a per-tool `cache: {scope, ttl_seconds}` block inside Action Gateway, invisible to Agent Core. This spec keeps its good ideas: opt-in per tool, write tools never cached, hashed keys. It changes four things:

1. **The LLM sees cached results.** An invisible cache saves the API call, but not the LLM's decision to call or the round trip that decision costs.
2. **Storage is Memory Layer, not Action Gateway / Knowledge Engine.** Memory Layer already owns session and persistent state and the per-turn read/write. #329 assumed tool results were already persisted to the user profile. They are not.
3. **Writes clear the reads they affect.** #329 had no invalidation, so a cached `fetch_profile` would survive a `save_profile`.
4. **Caller-chosen values get a validated save path** (`remember`, §8), next to the existing deterministic `session_mapping`.

## 2. Goals and non-goals

**Goals**
- Config-driven and domain-agnostic. No Blue Dots logic in framework code.
- Show the LLM unexpired tool results, with their age, so it can skip redundant calls.
- A TTL counted from the write. Reads never extend it.
- An LLM-driven, grounded way to save a value the caller chose.
- Write tools clear the cached reads they affect.
- Privacy by default: only projected, explicitly kept fields are stored. Owners are pseudonymised in keys and logs. Stored data is covered by existing erasure.
- Identical behaviour on the sync (`process_turn`) and stream (`stream_turn`) paths.
- Existing safeguards keep working: `grounded_params` must still see every value it saw before (§7.4).

**Non-goals**
- A cross-user ("agent"/global) cache scope. Deferred until a use case exists; it would need its own privacy guard.
- Serving stale data when the live API fails (#329's `allow_stale_on_failure`). Deferred.
- New deterministic result → state mapping. `response.session_mapping` already does this and is unchanged.
- Session bootstrap: running tools at session start. That is Spec B, which reuses this mechanism unchanged.
- Refactoring `user_id` away from the raw channel identifier (phone/email). That is tracked separately under Auth & IAM v2. This spec treats `user_id` as opaque (§9).
- Invalidation on changes made outside the conversation (e.g. the profile edited on the web mid-call). Only the TTL bounds these (§11).

## 3. Existing mechanisms this builds on

| Mechanism | Where | Role here |
|---|---|---|
| `response.projection` | Action Gateway, `rest_api.py` | Shapes what the LLM sees. The cache stores its output. |
| `response.session_mapping` + `response_path.resolve` | Action Gateway → `ToolResult.session_values` → Agent Core `_write_mapped_session_values` / sync loop | Deterministic result → session writes. Unchanged. |
| `source: session` params, `_tool_session_values` | Action Gateway / Agent Core | Lets later tools read saved values instead of the LLM repeating them. |
| `grounded_params`, `ungrounded_params()` | `manager_agent.py`; called from the sync loop and both stream-loop sites | Rejects IDs that no tool returned. Extended to see stored results (§7.4), and reused by `remember` (§8). |
| `recent_tool_exchanges`, `_prepend_tool_replay` | `orchestrator.py` | Replay of the last N exchanges. Cached tools are filtered out of it (§7.2). |

## 4. Concepts

| Concept | What it is | Lifetime | Where |
|---|---|---|---|
| **Projection** (existing) | Selects and renames response fields for the LLM. | per call | Action Gateway |
| **`session_mapping`** (existing) | Copies unambiguous raw-response values into session fields. | session | Memory Layer (session hash) |
| **Tool-result entry** (new) | A stored copy of a projected result, optionally narrowed by `keep`. Shown to the LLM in `<known_facts>`. | TTL from write | Memory Layer, Redis |
| **`remember`** (new) | A framework tool the LLM calls to save a caller-chosen value into a declared field, with grounding. | the field's scope | Memory Layer (existing stores) |
| **`<known_profile>`** (existing) | NLU-extracted caller-stated values, rendered as "already collected". Unchanged. | session / persistent | prompt, Tier 3 |

`<known_facts>` (what systems told us, expiring) and `<known_profile>` (what the caller told us, persisted) are deliberately separate blocks. They differ in trust, lifetime and storage.

## 5. Configuration

Cache rules sit in `agent_core.yaml` `connectors:`, beside each tool's LLM-facing description, `invocation_rules` and `grounded_params`, because Agent Core enforces them. Action Gateway stays unaware of caching.

```yaml
# agent_core.yaml
tool_results:
  max_user_ttl_seconds: 86400        # framework cap for scope: user (default 24h)

connectors:
  read:
    - name: fetch_profile
      cache:
        scope: user                  # session | user
        ttl_seconds: 1800            # counted from the write; never extended by a read
        keep: [acting_as_user_id, items]   # projected field NAMES (not paths); omit = all projected fields
        vary_on: []                  # session fields that feed the tool via source: session
  write:
    - name: save_profile
      invalidates: [fetch_profile]

memory_tool:
  name: remember
  fields:
    profile_item_id:
      scope: session
      description: "item_id of the profile the caller chose to use or update"
      grounded_in: [fetch_profile, save_profile]   # same semantics as grounded_params
    profile_action:
      scope: session                 # value set comes from the enum in memory_layer.yaml
```

**Schemas.** Every config schema forbids unknown keys, so all new keys are added explicitly to:
- Agent Core's runtime schema (`agent_core/src/schema/config.py`: `ConnectorDef.cache`, `ConnectorDef.invalidates`, top-level `tool_results` and `memory_tool`);
- the dev-kit domain schema (`dev_kit/schemas/domain/agent_core.py`) and the dev-kit loader model (`dev_kit/schema.py`).

Agent Core never loads `memory_layer.yaml` at runtime. So rules that need both files run in dev-kit's cross-block validation (`cross_block_validation.py`, a new rule that reads the `memory_layer` block). Agent Core's startup validation covers the single-file rules.

**Config validation** (startup fails on error):
- `cache` is only allowed on `read` connectors. Write connectors can never be cached.
- A session-scope `ttl_seconds` must be ≤ the session TTL (`memory_layer.state.session.ttl_minutes`). A user-scope `ttl_seconds` must be ≤ `tool_results.max_user_ttl_seconds`.
- `invalidates` entries must name existing read connectors.
- Every `memory_tool.fields.*` must be declared in `memory_layer.yaml`: the session schema for `scope: session`, `UserProfile.declared_fields` for `scope: persistent`. Memory Layer also gets a strict-write option so these writes are rejected instead of falling back to ad-hoc `UserAttribute` storage.
- `grounded_in` entries must name existing connectors.
- `vary_on` entries must be declared session fields.

A tool with none of these blocks behaves exactly as it does today.

## 6. Storage

One Redis key per entry, in Memory Layer's Redis:

```
ml:tr:s:{owner}:{tool}:{args_hash}   session scope, owner = pseudonym(session_id)
ml:tr:u:{owner}:{tool}:{args_hash}   user scope,    owner = pseudonym(user_id)
ml:tr:idx:s:{owner} / ml:tr:idx:u:{owner}   SET of entry keys for that owner
```

- **Entry value (JSON):** `{tool, data, fetched_at, expires_at, origin}`, where `data` is the kept projected fields and `origin` is `turn | bootstrap`.
- **Write:** `SET <key> <json> EX ttl_seconds`, then `SADD <idx> <key>`, `EXPIRE <idx> ttl_seconds NX` and `EXPIRE <idx> ttl_seconds GT`. `NX` sets the first expiry; `GT` only ever extends it, so the index outlives its longest-lived entry. `GT` alone would never set an expiry on a new key, because Redis treats a key with no expiry as having an infinite TTL. This needs Redis ≥ 7.0 / Valkey, and redis-py ≥ 4.2. Nothing here depends on 7.4-only features such as `HEXPIRE`.
- **Read:** `SMEMBERS` both indexes, then `MGET`, pipelined with the existing per-turn context read. Entries with `expires_at ≤ now` are dropped in code as well, in case Redis and app clocks disagree. Index members whose entry has already expired are removed with `SREM`.
- **`args_hash`:** a hash of the canonical JSON (sorted keys) of the LLM-supplied arguments, plus the current values of the `vary_on` session fields. A job search is only reusable while the session-sourced trade and location are unchanged.
- **`pseudonym(x)`:** `HMAC-SHA256(TOOL_RESULT_KEY_SECRET, x)`, truncated. A plain hash of a phone number can be reversed by brute force, so it is not enough. Every key is built by one helper, so the future `user_id` refactor touches one function.
- **Why not fields in the session hash:** the session hash's TTL resets on every write, so per-entry expiry would rely purely on app code. User scope must outlive the session. Session adoption would copy entries into new sessions. Concurrent writers (Spec B's async bootstrap and a turn) would contend on one key.

## 7. Per-turn flow

### 7.1 Read and prompt
Memory Layer's context bundle returns the unexpired tool-result entries (`ContextBundle.tool_results`) alongside session and profile. `build_system_prompt` renders `<known_facts>` in Tier 3, which is not prompt-cached since ages change every turn:

```
<known_facts>
Results of earlier tool calls. Use them instead of calling the tool again.
Call the tool only if what you need is missing here, or the caller says it changed.
- fetch_profile — fetched 3 min ago, valid for 27 more min: {"acting_as_user_id": "…", "items": […]}
</known_facts>
```

### 7.2 Replay filter
`_prepend_tool_replay` skips exchanges whose tool has a `cache` block **and** an unexpired entry, so the same data does not appear twice. This is the single shared replay point, so it also covers #411's carried-over rounds. Capture and persistence of `recent_tool_exchanges` are unchanged. If a cached tool's entry has expired, its exchange replays as today.

### 7.3 Pre-tool check
One shared helper, used by the sync tool loop and both stream-loop execute sites, runs before `gateway.execute`:
- **Unexpired entry for `(tool, args_hash)`** → return its `data` without calling Action Gateway, labelled `(stored result, fetched N min ago)`. Outcome `hit`. No `session_values` come with it: `session_mapping` ran only when the entry was first fetched live. With session scope that is fine, because the mapped values are already in this session's state. With user scope a later session gets the hit but never the mapped values, so user scope is unsafe for a connector whose tool declares `session_mapping` until hits replay stored `session_values` (tracked follow-up). dev-kit cross-block validation rejects `cache.scope: user` on such a connector.
- **`force_refresh: true`** → skip the entry and call live. This optional boolean is added by the framework to the input schema of cached connectors only, and stripped before the request reaches Action Gateway. Outcome `refresh`.
- **Otherwise** → live call. Outcome `miss`.

### 7.4 Grounding sees stored results
`ungrounded_params()` gains an optional `stored_results: dict[str, list[str]]` argument (tool name → serialised `data` of unexpired entries). All three call sites pass it. A value found in a stored `fetch_profile` entry is grounded exactly as if the `fetch_profile` exchange were still in the message list. Without this, the replay filter (§7.2) would hide grounding sources.

### 7.5 After a live call
- **Store:** only if the call succeeded, `ToolResult.projected` is `true`, and `result_text` parses as JSON. Otherwise nothing is stored: outcome `reject_unprojected` when the result wasn't projected, `reject_invalid` when its text isn't valid JSON. Errors are never stored.
- **`invalidates`:** applied when a write connector is **called**, whatever its outcome, because a timeout may still have written upstream.
- **`session_mapping`:** unchanged; runs as today.

### 7.6 Within the turn, and persisting
A turn-local overlay applies stored entries and invalidations immediately, so a repeat call in the same turn sees them. Queued entries and invalidations are sent to Memory Layer in one batch call (`POST /tool_results/apply`). The sync path sends it once, at the end of the turn. A streaming turn can be interrupted (#411), so the stream path sends it after each live tool call instead; otherwise an invalidation caused by a completed `save_profile` could be lost, and a stale `fetch_profile` served next turn.

**Action Gateway change:** `ToolResult` / `ExecuteResponse` gain `projected: bool` (true when a `response.projection` was applied). This is Agent Core's guard against ever storing a raw payload.

## 8. The `remember` tool

The LLM decides values that depend on the caller's choice, for example which of several profiles to use. It saves them with the framework tool `remember(field, value)`, which is handled in Agent Core and never sent to Action Gateway.

- Only `memory_tool.fields` are writable. The tool's input schema lists them as an enum, with their descriptions.
- **Validation:**
  - the declared type/enum from `memory_layer.yaml`. This check runs in **Memory Layer**, which owns that schema: a strict write returns `rejected` with a reason instead of storing the value;
  - when `grounded_in` is set, the value must be grounded in those tools' results. This is the same `ungrounded_params()` check, over the turn's messages plus stored results.
  - A rejected value returns a tool error the LLM can correct (outcome `remember_reject`), and nothing is written.
- Accepted values go through the same `memory_layer.write(scope=…)` path as NLU entities and `session_mapping`. Connectors then consume them through `source: session`.
- **Latency:** `remember` runs locally. If an assistant message contains text for the caller and its only tool calls are `remember`, the turn ends without a follow-up LLM generation. The tool results are recorded as usual. The plan must confirm this is valid for each chat provider's tool-use message rules before relying on it.
- **`session_mapping` vs `remember`:** `session_mapping` is for values that can't be ambiguous, such as the one live profile, a root-level ID, or the item a write tool just created. Anything that needs the caller's choice goes through `remember`.

## 9. Privacy and data protection

- **Minimisation:** only projected fields, narrowed by `keep`, are stored. Raw payloads never are (the `projected` guard). This is stricter than today's raw replay of up to 4000 characters.
- **Retention:** tool-result entries live in Redis only, never in Memgraph or SQLite, with write-time TTLs capped per scope. `remember` fields follow the retention of their declared scope, the same as NLU entities.
- **Pseudonymisation:** keys and logs use `pseudonym(owner)`. Neither ever contains argument values or result data.
- **Erasure:** `DELETE /user/{user_id}` also deletes the user-scope index and its entries. `flush_session` also deletes the session-scope index and its entries.
- **No cross-user sharing:** there is no scope that isn't per-owner.
- **`user_id` is opaque:** this spec never parses `user_id` or uses it as a tool input. Owner derivation lives in one helper, ready for the Auth & IAM v2 identity refactor.

## 10. Observability

- A structured log event `tool_result` with `{tool, scope, outcome, age_s, ttl_s, owner: pseudonym}`. `outcome` is one of `hit | miss | refresh | store | invalidate | reject_unprojected | reject_invalid | remember_write | remember_reject | store_unavailable`.
- An OTel counter `tool_result_outcomes{tool, outcome}`, giving the cache hit rate per tool.

## 11. Failure modes and known limitations

- **Redis unavailable:** the read returns no entries and the writes are dropped (logged `store_unavailable`). The turn behaves as today: the replay filter finds no entries, so replay is unchanged, and the LLM calls tools live. The cache never blocks a turn.
- **Upstream changed outside the conversation:** only the TTL bounds staleness. Choose TTLs accordingly. `force_refresh` lets the LLM react to "check again".
- **Session-scope entries on the stream path:** they are not deleted when the call ends if `flush_session` isn't called; they still expire through their own TTL.
- **Concurrent writers of the same key:** the last writer wins. Entries are idempotent snapshots, so this is acceptable.

## 12. Blue Dots rollout (config only, separate commit)

- `fetch_profile`: add `cache` (`scope: session`). Not `scope: user`: its tool declares `session_mapping` (`has_age`, `user_terms`, `user_privacy`, the live profile's values), and a hit does not replay it (§7.3), so on a redial a user-scope hit would leave those session fields unset and routing would re-ask for facts already on file. Narrow its projection or `keep` to what the choice prompt needs: `item_id`, `lifecycle_status`, `updated_at`, the fields read out to the caller. No whole `item_state`. Its existing `session_mapping` stays.
- `save_profile`: add `invalidates: [fetch_profile]`. Its existing `session_mapping` stays.
- `fetch_jobs`: evaluate `cache` (`scope: session`, `vary_on: [trade, location]`) during rollout.
- Add `memory_tool` with `profile_item_id` (`grounded_in: [fetch_profile, save_profile]`) and `profile_action`.
- `apply_job` / `save_profile`: move `profile_item_id` and `acting_as_user_id` to `source: session` once `remember` covers the multi-profile case. `grounded_params` stays as defence in depth.
- Prompts: replace "call once / has not been called" wording and "save profile_item_id" with references to `<known_facts>` and `remember`.
- Set `TOOL_RESULT_KEY_SECRET` in deploy secrets.

## 13. Testing

- **Unit (Memory Layer):** key building (canonical args, `vary_on`, pseudonym), write-time TTL (a read doesn't extend it), index `GT` expiry and cleanup of expired members, erasure and flush deletion, the batch apply call, strict-write rejection of undeclared fields.
- **Unit (Agent Core):** the pre-tool check (hit / miss / refresh), `force_refresh` removed before execute, storing only projected results that are valid JSON, invalidation on failed write calls, the replay filter (including a carried-over #411 round), `ungrounded_params` with `stored_results`, `remember` validation (enum, type, grounding against stored results), and the `<known_facts>` render snapshot.
- **Action Gateway:** the `projected` flag is true with a projection and false without one.
- **Config validation:** each rule in §5 has a failing-config test.
- **Path parity:** the same scenario table run through the sync loop and both stream-loop sites.
- **Failure:** Redis down → the live call succeeds, replay is unchanged, and the turn completes.
- **Blue Dots scenarios:**
  - a caller with one live profile (`session_mapping` sets `profile_item_id`);
  - a caller with several profiles → `remember` a grounded ID → `apply_job` uses the session value;
  - `remember` a job ID as a profile ID → rejected;
  - `save_profile` → the next `fetch_profile` is live;
  - a repeat `fetch_profile` → hit, and `save_profile`'s grounding still passes.
- **End to end:** the scenarios above feed into the voice-call test in #414.

## 14. Relationship to Spec B (Session Bootstrap)

Spec B adds config-declared steps that run at session start (step types `tool` and `set`, per-step `requires_consent`, inputs bound to named caller attributes such as `caller.phone` rather than `user_id`). A `tool` step's result goes through this spec's store and `<known_facts>` unchanged, with `origin: bootstrap`. Its `session_mapping` values are written as for a live call. Blue Dots already calls `fetch_profile` on the first turn in `opening`. Spec B removes the LLM decision and the round trip from that call. It does not move the call earlier in the flow.
