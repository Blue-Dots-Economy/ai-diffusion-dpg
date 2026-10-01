# Session Bootstrap — Design (Spec B)

**Date:** 2026-10-01
**Status:** Draft, awaiting review
**Baseline:** `deploy/voicera-vm` at `5f2bebc` (Spec A — tool-result persistence, #421 — merged).
**Builds on:** `docs/superpowers/specs/2026-09-30-tool-result-persistence-design.md` (Spec A). This spec amends Spec A §14 (§9).

## 1. Problem

Some facts every call needs are fetched only when the LLM decides to call a tool, and they arrive only after that turn's routing. For Blue Dots the fact is the caller's profile: `fetch_profile` returns consent and age flags (`has_age`, `user_terms`, `user_privacy`) and the live profile (`profile_item_id`, `stored_trade`, `stored_location`) via `response.session_mapping`.

A local run against dev Signals (2026-10-01, returning caller, bridge streaming path) showed what that costs:

- **An LLM round trip just to fetch the profile.** Turn 1 is LLM call → `fetch_profile` → LLM call. That is about 1 s of LLM time before the caller hears anything useful.
- **An empty turn 1 for returning callers.** The streaming path skips the canned opening phrase (GH-239). The opening prompt says not to greet, and with consent and age on file it also forbids both of its questions. The flags only reach the session after the turn-1 fetch, so routing cannot move the caller on until turn 2. In two of three runs turn 1 was only the bridge status line.
- **An inconsistent profile offer.** The profile_resolve prompt reads `profile_item_id`, `stored_trade` and `stored_location` from `<known_profile>`, but `<known_profile>` only renders Memgraph profile fields and NLU-mapped session fields. Those three come from `session_mapping`, so they never reach the model. The offer appeared in about one run in three.

The sync path shows a milder form: turn 1 is the canned greeting, the LLM first fetches on turn 2, and routing sees the flags on turn 3 (the one-turn lag accepted in `2026-09-29-caller-facts-from-fetch-design.md`).

## 2. Goals and non-goals

**Goals**
- Config-declared steps run deterministically, without the LLM, on the first turn of a session, before anything reads session state. Generic DPG mechanism; no Blue Dots logic in code.
- Step results go through the same paths as a live tool call: `session_mapping` values are written to session, and the result is recorded in the Spec A store (`origin: bootstrap`) and shown in `<known_facts>`.
- A config-declared list of session fields rendered into `<known_profile>` (`agent.prompt_session_fields`).
- Never break or stall a turn: a bounded budget, fail-open to today's behaviour.
- Identical behaviour on the sync (`process_turn`) and stream (`stream_turn`) paths.

**Non-goals**
- A `set` step type (copying call metadata into session). No channel forwards call metadata to Agent Core today. The step format is typed so it can be added later without a breaking change.
- Early triggers before the caller speaks, such as the voice opening-phrase subscribe. v1 runs inline on the first turn only.
- Binding step arguments to caller attributes (`caller.phone`). v1 supports literal `args` only. Tools that need the caller identity get it the way they do today (`{user_id}` in the Action Gateway path, `source: session` params). Attribute binding belongs with the `user_id` refactor under Auth & IAM v2.
- Changing the sync path's canned turn-1 greeting.
- Replaying stored `session_values` on a cache hit (Spec A R17 follow-up), prompt tuning to stop the LLM re-calling cached tools, and the dev Signals test-profile data quality (an address in `location`).

## 3. Design overview

A new Agent Core unit, `session_bootstrap`, runs right after the turn's memory read on both paths. It does nothing unless `session_bootstrap` is configured and the session has not been bootstrapped yet.

Agent Core is the only turn-time orchestrator and already calls Action Gateway, writes `session_mapping` values and owns the Spec A cache, so this adds no cross-service calls. Alternatives considered and rejected:
- **Memory Layer, during its new-session branch.** Needs a new Memory Layer → Action Gateway call, which the module rules forbid.
- **The channel (bridge), before it calls Agent Core.** Per-channel, and it gives a channel external access.

## 4. Configuration

```yaml
# agent_core.yaml
session_bootstrap:
  timeout_ms: 1500             # budget for the whole bootstrap on turn 1
  steps:
    - type: tool               # the only type in v1
      tool: fetch_profile      # must name an existing `read` connector
      args: {}                 # literal input params (optional; default {})
      requires_consent: false  # true → skipped unless consent is already on record

agent:
  prompt_session_fields: [profile_item_id, stored_trade, stored_location]
```

**Validation** (startup fails on error):
- `session_bootstrap.timeout_ms > 0`. `steps` has at least one entry. Each step has `type: tool` and names an existing connector under `connectors.read`. Write and identity connectors are rejected, because a bootstrap must never have side effects.
- Every `prompt_session_fields` entry must be declared in the `memory_layer.yaml` session schema. This is checked by dev-kit's cross-block validation, because Agent Core never loads `memory_layer.yaml` at runtime.
- The runtime schema (`agent_core/src/schema/config.py`), the dev-kit domain mirror and the dev-kit loader model all gain these keys (see `.claude/rules/runtime-devkit-sync.md`).

A config without `session_bootstrap` or `prompt_session_fields` behaves exactly as today.

## 5. Per-turn flow

### 5.1 When it runs
- **Latch:** a new session field `bootstrap_done`. It is not declared in any session schema: a declared enum latch would be seeded as the truthy string `"false"`. It is added to Memory Layer's `_SESSION_LIFECYCLE_FIELDS`, so it is never copied when a new session adopts an earlier one, and each new call bootstraps afresh.
- **Placement:** immediately after `context_bundle`, before anything reads the bundle. On sync that is before subagent resolution, the consent gate and the opening-phrase gate. On stream it is before the carry-over fold and the pre-NLU arguments.
- **Order:** the latch is written first, so a crash mid-bootstrap never repeats it. It is set whatever the outcome; the bootstrap never retries on later turns. The fallback is today's behaviour: the LLM may still call the tool.

### 5.2 Per step
1. **Consent:** if `requires_consent` is set, check consent through the existing Trust Layer check (`check_consent(session_id, tool)`). If consent is not on record, skip the step (outcome `skipped_consent`).
2. **Execute:** build a `ToolCall(tool_name, tool_use_id="bootstrap-<n>", input_params=args)` and call `gateway.execute(call, session_id, user_id, session_values=_tool_session_values(bundle))`. This is the same call a live tool makes. The stream path uses the async gateway and runs steps concurrently. The sync path uses the sync gateway and runs them in order.
3. **On `success: true`:**
   - Write each `result.session_values` entry (from `session_mapping`) to Memory Layer at session scope and into `bundle.session`, using the existing helpers (`_write_mapped_session_values`, or the sync loop's `_write_memory_sync`).
   - Record the result through Spec A's `TurnToolCache` with `origin="bootstrap"`, persist it with `apply_tool_results`, and append the stored entry to `bundle.tool_results`. This turn's `<known_facts>` then includes it, and a repeat LLM call is a cache hit. All of Spec A's store rules apply: projected results only, JSON only, `keep`, TTL.
4. **On `success: false`:** write nothing (outcome `failed`).

**Budget:** the whole bootstrap is bounded by `timeout_ms`. Steps still pending at the deadline are cancelled and any late result is discarded, never written (outcome `timeout`). The sync path runs steps in order and starts no new step once the deadline has passed; a call already in flight is bounded by the gateway client's own timeout, so the effective worst case there is `timeout_ms` plus one gateway timeout. This is documented, not hidden.

### 5.3 Spec A extension
`TurnToolCache.after_call(tool_call, result, origin="turn")` gains the `origin` parameter (`"turn" | "bootstrap"`, already accepted by Memory Layer). It returns the stored entry dict, or `None` when nothing was stored. Existing callers are unaffected.

### 5.4 `prompt_session_fields`
On both paths, where `<known_profile>` is built (`profile_context`), each listed field whose session value is non-empty (not `None`, `""`, `"[]"`) and not already present is added. Unlisted session fields never reach the prompt. The list is explicit on purpose: nothing new is exposed to the model unless a domain names it.

### 5.5 What this does for Blue Dots
- **Stream, returning caller:** turn-1 routing sees the three flags. The existing `opening` rule 1b (`user_terms`, `user_privacy`, `has_age` all true; not latch-guarded) routes straight to profile_resolve on turn 1. `<known_profile>` now holds `profile_item_id`, `stored_trade` and `stored_location`, so turn 1 can make the "<trade> in <city>" offer. The opening phase never meets an "everything already known" caller, which removes the empty turn.
- **Sync, returning caller:** turn 1 stays the canned greeting, with the bootstrap run before it. Turn 2 routes with the flags, one turn earlier than today, and no LLM `fetch_profile` round trip is needed.
- **New caller:** the bootstrap finds no live profile, so the flags keep their defaults and today's consent → age flow runs, minus one LLM tool round.

## 6. Failure modes

| Case | Behaviour |
|---|---|
| Action Gateway or upstream error, or `success: false` | Logged `failed`. Nothing written. The flags stay at their defaults, so the caller falls back to today's flow. |
| Bootstrap exceeds `timeout_ms` | Pending steps cancelled, late results discarded (`timeout`). The turn proceeds. |
| `requires_consent` with no consent on record | Step skipped (`skipped_consent`). The LLM can call the tool later through the normal consent-gated path. |
| Memory Layer write fails | Logged. This turn still uses the in-memory `bundle.session`, so routing works for turn 1. Later turns read whatever was persisted. |
| Session adopted from an earlier call | `bootstrap_done` is not adopted, so the bootstrap runs fresh. |
| Bootstrap not configured | No-op. |
| Any exception inside the bootstrap unit | Caught, logged, and the turn proceeds. The bootstrap never raises into the turn. |

## 7. Observability

- One structured log entry per step, `session_bootstrap`, with `operation`, `tool`, `outcome` (`ok | failed | timeout | skipped_consent`) and `latency_ms`, plus one per-turn total with `latency_ms`. Argument values and results are never logged.
- An OTel counter `agent_core.session_bootstrap.outcomes_total{tool, outcome}`.
- A `[STEP 1b] Session bootstrap  ✓  tools=…  outcome=…  latency=…ms` text line, alongside the existing step lines, so per-step timing reports include it.

## 8. Testing

- **Unit (bootstrap):**
  - Runs once per session; the latch is set even on failure; it is not adopted.
  - Success writes `session_values` and records a cache entry with `origin=bootstrap` that is visible in the same turn's `bundle.tool_results`.
  - Gateway failure, timeout with a late result discarded, consent skip, no config, and an internal exception swallowed.
- **Unit (`TurnToolCache`):** `origin` is passed through to the put and the entry, and the stored entry is returned.
- **Schema:** read-only connectors; `timeout_ms > 0`; at least one step; `type: tool`; unknown connector rejected. Each failing case asserts its specific error message.
- **dev-kit:** domain mirror accepts and rejects the same inputs; cross-block check that `prompt_session_fields` are declared in the session schema.
- **Rendering:** listed fields only; empty values skipped; existing values not overridden; unlisted fields absent.
- **Path parity** (existing two-path harness):
  - A returning caller's turn 1 routes to profile_resolve on stream.
  - Sync turn 2 routes with the flags without an LLM `fetch_profile` call.
  - A new caller follows the consent flow.
  - Bootstrap failure falls back to today's behaviour.
- **Local end to end** (Docker stack, dev Signals): the returning-caller and new-caller calls from the 2026-10-01 run, repeated several times each. Check turn-1 content, the profile-offer rate, and the per-step latency table including `[STEP 1b]`.

## 9. Blue Dots rollout and Spec A amendment

**Blue Dots** (config and prompt only):
- Add `session_bootstrap` with the single `fetch_profile` step, `timeout_ms: 1500`.
- Add `agent.prompt_session_fields: [profile_item_id, stored_trade, stored_location]`.
- Opening prompt: change "On your FIRST turn call `fetch_profile`" to "`fetch_profile` already ran when the call started; its result is in `<known_facts>`; call it only if that entry is absent."
- No routing changes.

**Spec A §14** is amended: Spec B *does* move the fetch before turn-1 routing. v1 has one step type (`tool`) and literal arguments only; `set` and caller-attribute binding are deferred (§2).
