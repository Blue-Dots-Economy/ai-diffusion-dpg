# Tool-Result Persistence — Design

**Date:** 2026-09-30
**Status:** Draft, awaiting review
**Supersedes the design in:** #329 (Action Gateway tool-call response caching). Resolves the open question in #18.
**Followed by:** Spec B — Session Bootstrap (separate spec, depends on this one).

## 1. Problem

Tool results in Agent Core are short-lived, and the LLM has no reliable memory of what it already fetched.

- A tool result is sent back to the LLM for the current turn only. Across turns, the only thing kept is `recent_tool_exchanges`: the last 3 raw exchanges, up to 4000 characters each, replayed as tool_use/tool_result messages. Once three other tools run, older results are gone, and replayed results carry no age.
- There is no cache. Every tool call hits the live API, and the LLM has to re-plan the call (an extra round trip per repeat). Blue Dots invocation rules try to fix this in prose, for example `fetch_profile`'s *"call when … fetch_profile has not been called yet this session"*. That asks the LLM to track state it cannot see.
- There is no way to turn a tool result into user state. The Blue Dots session schema declares `profile_item_id` / `source_item_owner`, and the prompt tells the LLM to "save profile_item_id", but no code writes these from a tool result, and the LLM has no tool that can write them. Later write tools (`apply_job`) then rely on the LLM to repeat IDs correctly. We've seen what goes wrong when the LLM supplies values it can't see (the `age=0 → U18_NOT_ALLOWED` incident that moved `age` to `source: session`).

### What #329 got right and what it missed

#329 proposed a per-tool `cache: {scope, ttl_seconds}` block inside Action Gateway, invisible to Agent Core. This spec keeps its good ideas: opt-in per tool, write tools never cached, hashed keys. It changes four things:

1. **The LLM sees cached results.** An invisible cache saves the API call, but not the LLM's decision to make it or the round trip that decision costs.
2. **Storage is Memory Layer, not Action Gateway / Knowledge Engine.** Memory Layer already owns session/persistent state and the per-turn read/write. #329 assumed tool results were already persisted to the user profile. They are not.
3. **Writes clear the reads they affect.** #329 had no invalidation, so a cached `fetch_profile` would survive a `save_profile`.
4. **Some tool results are user state, not cache entries** (§6, §7).

## 2. Goals and non-goals

**Goals**
- Config-driven and domain-agnostic. No Blue Dots logic in framework code.
- Show the LLM unexpired tool results, with their age, so it can skip redundant calls.
- A TTL counted from the write. Reads never extend it.
- Deterministic promotion of unambiguous tool-result values into user state.
- An LLM-driven, validated way to save a value the caller chose.
- Write tools clear the cached reads they affect.
- Privacy by default: only projected, explicitly kept fields are stored. Owners are pseudonymised in keys and logs. Stored data is covered by existing erasure.
- Identical behaviour on the sync (`process_turn`) and stream (`stream_turn`) paths.

**Non-goals**
- A cross-user ("agent"/global) cache scope. Deferred until a use case exists; it would need its own privacy guard.
- Serving stale data when the live API fails (#329's `allow_stale_on_failure`). Deferred.
- Session bootstrap: running tools at session start. That is Spec B, which reuses this mechanism unchanged.
- Refactoring `user_id` away from the raw channel identifier (phone/email). That is tracked separately under Auth & IAM v2. This spec treats `user_id` as opaque (§9).
- Invalidation on changes made outside the conversation (e.g. the profile edited on the web mid-call). Only the TTL bounds these (§11).

## 3. Prerequisite

Session-sourced connector params (`source: session` in `action_gateway.yaml`, filled from `session_values` by the rest_api adapter) exist on `deploy/voicera-vm` (commits `73cd84b`, `6f86049`) but not yet on `main`. §7 depends on them. They must land on `main` before, or as the first task of, this spec's plan.

## 4. Concepts

| Concept | What it is | Lifetime | Where |
|---|---|---|---|
| **Projection** (existing) | Action Gateway's per-tool `response.projection`, which selects and renames fields from the raw response. It stays the **only** transformation of a tool response. | per call | Action Gateway |
| **Tool-result entry** (new) | A stored copy of a projected result, optionally narrowed by `keep`. Shown to the LLM in `<known_facts>`. | TTL from write | Memory Layer, Redis |
| **`writes`** (new) | A deterministic copy of an unambiguous projected value into a declared user-state field. | the field's scope (session / persistent) | Memory Layer (existing stores) |
| **`remember`** (new) | A framework tool the LLM calls to save a value the caller chose into a declared field, validated. | the field's scope | Memory Layer (existing stores) |
| **`<known_profile>`** (existing) | NLU-extracted caller-stated values, rendered as "already collected". Unchanged. | session / persistent | prompt, Tier 3 |

`<known_facts>` (what systems told us, expiring) and `<known_profile>` (what the caller told us, persisted) are deliberately separate blocks. They differ in trust, lifetime and storage.

## 5. Configuration

Cache and write rules sit in `agent_core.yaml` `connectors:`, beside each tool's LLM-facing description and `invocation_rules`, because Agent Core enforces them. Action Gateway stays unaware of caching.

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
      writes:
        - { field: acting_as_user_id, from: acting_as_user_id, scope: persistent }
  write:
    - name: save_profile
      invalidates: [fetch_profile]
      writes:
        - { field: profile_item_id, from: "items[0].item_id", scope: persistent }

memory_tool:
  name: remember
  fields:
    profile_item_id:
      scope: persistent
      description: "item_id of the profile the caller chose to use or update"
      must_match: { fact: fetch_profile, path: "items[].item_id" }
    profile_action:
      scope: session                 # value set comes from the enum in memory_layer.yaml
```

**Config validation** (at domain config load, in the dev-kit loader that already sees both `agent_core.yaml` and `memory_layer.yaml`; startup fails on error):
- `cache` is only allowed on `read` connectors. Write connectors cannot be cached.
- A session-scope `ttl_seconds` must be ≤ the session TTL (`memory_layer.state.session.ttl_minutes`). A user-scope `ttl_seconds` must be ≤ `tool_results.max_user_ttl_seconds`.
- `invalidates` entries must name existing read connectors.
- Every `writes[].field` and `memory_tool.fields.*` must be declared in `memory_layer.yaml`: the session schema for `scope: session`, `UserProfile.declared_fields` for `scope: persistent`. Memory Layer also gets a strict-write option so these writes are rejected instead of falling back to ad-hoc `UserAttribute` storage.
- `must_match.fact` must name a connector with a `cache` block.
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
- **Write:** `SET <key> <json> EX ttl_seconds`, then `SADD <idx> <key>` and `EXPIRE <idx> ttl_seconds GT`. `GT` only extends, so the index outlives its longest-lived entry. It needs Redis ≥ 7.0 / Valkey. Nothing here depends on 7.4-only features such as `HEXPIRE`.
- **Read:** `SMEMBERS` both indexes, then `MGET`, pipelined with the existing per-turn context read. Entries with `expires_at ≤ now` are dropped in code as well, in case Redis and app clocks disagree. Index members whose entry has already expired are removed with `SREM`.
- **`args_hash`:** a hash of the canonical JSON (sorted keys) of the LLM-supplied arguments, plus the current values of the `vary_on` session fields. A job search is only reusable while the session-sourced trade and location are unchanged.
- **`pseudonym(x)`:** `HMAC-SHA256(TOOL_RESULT_KEY_SECRET, x)`, truncated. A plain hash of a phone number can be reversed by brute force, so it is not enough. Every key is built by one helper, so the future `user_id` refactor touches one function.
- **Why not fields in the session hash:** the session hash's TTL resets on every write, so per-entry expiry would rely purely on app code. User scope must outlive the session. Session adoption would copy entries into new sessions. Concurrent writers (Spec B's async bootstrap and a turn) would contend on one key.

## 7. Per-turn flow

1. **Read.** Memory Layer's context bundle returns the unexpired tool-result entries alongside session and profile.
2. **Prompt.** `build_system_prompt` renders `<known_facts>` in Tier 3 (not prompt-cached, since ages change every turn):
   ```
   <known_facts>
   Results of earlier tool calls. Use them instead of calling the tool again.
   Call the tool only if what you need is missing here, or the caller says it changed.
   - fetch_profile — fetched 3 min ago, valid for 27 more min: {"acting_as_user_id": "…", "items": […]}
   </known_facts>
   ```
   Connectors with a `cache` block are excluded from the `recent_tool_exchanges` replay, so the same data does not appear twice. Other tools keep today's replay.
3. **Pre-tool check.** One shared helper, used by both the sync and stream tool loops, runs before `gateway.execute`:
   - **Unexpired entry for `(tool, args_hash)`** → return its `data` without calling Action Gateway, labelled `(stored result, fetched N min ago)`. Outcome `hit`.
   - **`force_refresh: true`** → skip the entry and call live. This optional boolean is added by the framework to the input schema of cached connectors only, and stripped before the request reaches Action Gateway. Outcome `refresh`.
   - **Otherwise** → live call. Outcome `miss`.
4. **After a live call.**
   - **Store:** only if the call succeeded, `ToolResult.projected` is `true`, and `result_text` parses as JSON. Otherwise nothing is stored (outcome `reject_unprojected` when the result wasn't projected). Errors are never stored.
   - **`writes`:** each rule's `from` path is resolved against the projected result. A path that resolves to nothing writes nothing and never clears an existing value.
   - **`invalidates`:** applied when a write connector is **called**, whatever its outcome, because a timeout may still have written upstream.
5. **Within the turn.** A turn-local overlay applies stored entries, writes and invalidations immediately, so a repeat call in the same turn sees them.
6. **Persist.** Queued entries, invalidations and field writes are added to the existing post-turn Memory Layer write.

**Path syntax** (`writes.from`, `must_match.path`): dot paths with list indexing, e.g. `a.b`, `items[0].item_id`, and `items[].item_id` (all elements, only for `must_match`). A small resolver lives in Agent Core, independent of Action Gateway's projection resolver.

**Action Gateway change:** `ToolResult` gains `projected: bool` (true when a `response.projection` was applied). This is Agent Core's guard against ever storing a raw payload.

## 8. The `remember` tool

The LLM decides values that depend on the caller's choice, for example which of several profiles to use. It saves them with the framework tool `remember(field, value)`, which is handled in Agent Core and never sent to Action Gateway.

- Only `memory_tool.fields` are writable. The tool's input schema lists them as an enum, with their descriptions.
- **Validation:** the declared type/enum from `memory_layer.yaml`, plus `must_match` when configured. `must_match` requires the value to appear at `path` in the unexpired `fact` entry. If that entry has expired, the tool returns an error asking for the fact to be refreshed. A rejected value returns a tool error the LLM can correct (outcome `remember_reject`). Nothing is written.
- Accepted values go through the same `memory_layer.write(scope=…)` path as NLU entities. A persistent field then also shows up in `<known_profile>`, and connectors use it through `source: session`.
- **Latency:** `remember` runs locally. If an assistant message contains text for the caller and its only tool calls are `remember`, the turn ends without a follow-up LLM generation. The tool results are recorded as usual. The plan must confirm this is valid for each chat provider's tool-use message rules before relying on it.
- **Automatic `writes` vs `remember`:** `writes` is for values that can't be ambiguous, such as a root-level ID, or the item a write tool just created or updated. Anything that needs the caller's choice goes through `remember`.

## 9. Privacy and data protection

- **Minimisation:** only projected fields, narrowed by `keep`, are stored. Raw payloads never are (the `projected` guard). This is stricter than today's raw replay of up to 4000 characters.
- **Retention:** tool-result entries live in Redis only, never in Memgraph or SQLite, with write-time TTLs capped per scope. `writes` and `remember` fields follow the retention of their declared scope, the same as NLU entities.
- **Pseudonymisation:** keys and logs use `pseudonym(owner)`. Neither ever contains argument values or result data.
- **Erasure:** `DELETE /user/{user_id}` also deletes the user-scope index and its entries. `flush_session` also deletes the session-scope index and its entries.
- **No cross-user sharing:** there is no scope that isn't per-owner.
- **`user_id` is opaque:** this spec never parses `user_id` or uses it as a tool input. Owner derivation lives in one helper, ready for the Auth & IAM v2 identity refactor.

## 10. Observability

- A structured log event `tool_result` with `{tool, scope, outcome, age_s, ttl_s, owner: pseudonym}`. `outcome` is one of `hit | miss | refresh | write | invalidate | reject_unprojected | remember_write | remember_reject | store_unavailable`.
- An OTel counter `tool_result_outcomes{tool, outcome}`, giving the cache hit rate per tool.

## 11. Failure modes and known limitations

- **Redis unavailable:** the read returns no entries and the writes are dropped (logged `store_unavailable`). The turn behaves as today: the LLM calls tools live. The cache never blocks a turn.
- **Upstream changed outside the conversation:** only the TTL bounds staleness. Choose TTLs accordingly. `force_refresh` lets the LLM react to "check again".
- **The stream path doesn't call `flush_session` today:** session-scope entries still expire through their own TTL. Adding the flush is out of scope here.
- **Concurrent writers of the same key:** the last writer wins. Entries are idempotent snapshots, so this is acceptable.

## 12. Blue Dots rollout (config only, separate commit)

- `fetch_profile`: add `cache` (`scope: user`) and `writes` (`acting_as_user_id`). Narrow its projection or `keep` to what the choice prompt needs: `item_id`, lifecycle state, `updated_at`, the fields read out to the caller. No whole `item_state`.
- `save_profile`: add `invalidates: [fetch_profile]` and `writes` (`profile_item_id` from the affected item).
- `apply_job`: `item_id` and `acting_as_user_id` become `source: session`.
- Add `memory_tool` with `profile_item_id` (`must_match` on `fetch_profile`) and `profile_action`.
- Prompts: replace "fetch_profile has not been called yet this session" and "save profile_item_id" with references to `<known_facts>` and `remember`.
- Set `TOOL_RESULT_KEY_SECRET` in deploy secrets.

## 13. Testing

- **Unit (Memory Layer):** key building (canonical args, `vary_on`, pseudonym), write-time TTL (a read doesn't extend it), index `GT` expiry and cleanup of expired members, erasure and flush deletion, strict-write rejection of undeclared fields.
- **Unit (Agent Core):** the pre-tool check (hit / miss / refresh), `force_refresh` removed before execute, storing only projected results that are valid JSON, `writes` path resolution (missing path → no write), invalidation on failed write calls, `remember` validation (enum, type, `must_match`, expired fact), the `<known_facts>` render snapshot, and exclusion from the `recent_tool_exchanges` replay.
- **Config validation:** each rule in §5 has a failing-config test.
- **Path parity:** the same scenario table run through both the sync and stream tool loops.
- **Failure:** Redis down → the live call succeeds and the turn completes.
- **Blue Dots scenarios:** a single-profile caller; a multi-profile caller → `remember` a valid ID → `apply_job` uses the session ID; `remember` an invented ID → rejected; `save_profile` → next `fetch_profile` is live; a repeat `fetch_profile` → hit.
- **End to end:** the scenarios above feed into the voice-call test in #414.

## 14. Relationship to Spec B (Session Bootstrap)

Spec B adds config-declared steps that run at session start (step types `tool` and `set`, per-step `requires_consent`, inputs bound to named caller attributes such as `caller.phone` rather than `user_id`). A `tool` step's result goes through this spec's store, `writes` and `<known_facts>` unchanged, with `origin: bootstrap`. Spec B only decides when things run.
