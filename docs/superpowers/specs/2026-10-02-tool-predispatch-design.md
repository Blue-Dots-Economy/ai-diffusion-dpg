# Spec E: Tool pre-dispatch

**Status:** Draft for review, 2026-10-02.
**Block:** Agent Core (runtime), dev-kit (schema mirror) and the Blue Dots domain config.
**Builds on:**
- Spec B, session bootstrap (#424): the callable-injection pattern.
- Spec C, dialogue-act NLU (#428): routing intent, slots and resolved options.
- Spec D, main-LLM context (#433): result shaping, `<state>` and the scenario runner.

This branch is stacked on `spec/main-llm-context`.

## 1. Why

The goal is latency, with reply quality held.

About half of all turns call a tool: 65 of 131 in the 28 Sep VM run. On those turns the main LLM is called twice. Call 1 asks for the tool, the tool runs, then call 2 speaks the result.

| Step | p50 |
|---|---|
| Call 1 | ~985 ms (the same as on a no-tool turn) |
| Tool | 30–350 ms |
| Call 2 | 0.8–1.2 s, with multi-second tails (11.7 s once on 30 Sep) |

The second call usually adds no information. The NLU and the session already determine which tool would be called and with what arguments.

Spec E calls that tool **before** the main LLM, so the main LLM speaks the result in its first and only call. The main LLM still writes every reply, so there are no template replies. The product decision was "tool-only, LLM speaks".

## 2. Goals and non-goals

**Goals**
- **G1.** A per-subagent `predispatch` rule list in domain config: tool, conditions, argument bindings and `enabled`. It is evaluated once per turn after routing, on both paths.
- **G2.** A pre-dispatched call goes through exactly the guards a model-initiated call does: consent, per-turn cap, grounding, cache, result shaping, session-value mapping and cache persistence. It is never a second, weaker path.
- **G3.** On success, the result reaches the main LLM's first call as a normal tool exchange. The tool is removed from that call's tool list, so it cannot be called twice.
- **G4.** Read tools ship enabled. Write tools are supported but each write rule ships `enabled: false` and turns on only after the evidence bar in §8.
- **G5.** No added model calls and no turn slower than today: pre-dispatch has its own budget, and every failure falls back to today's path.

**Non-goals**
- Template replies that skip the main LLM. This was considered and not chosen.
- Speculative dispatch before NLU or routing. This was considered and not chosen.
- "More jobs" (`explore_more` with `offset`). The number already read out is not in state.
- Changing the per-turn tool loop semantics for model-initiated calls, beyond the shared helper in §5.1.

## 3. Config

### 3.1 Shape

Rules are an optional `predispatch` list on any subagent. The schema is the same in the runtime and the dev-kit (§9).

```yaml
- id: job_match
  predispatch:
    - tool: fetch_jobs
      enabled: true
      unless_fresh: true
      args:
        query_text:
          template: "{trade|stored_trade} jobs in {location|stored_location}"
          normalise: { location: city_canonical }
- id: apply_confirm
  predispatch:
    - tool: apply_job
      enabled: false
      on_intent: [apply_now]
      args:
        profile_item_id: { from: session, key: profile_item_id }
        job_item_id:     { from: session, key: selected_job_item_id }
```

| Field | Meaning |
|---|---|
| `tool` | A tool declared in `connectors` and offered to this subagent (`resolve_tools_for`). |
| `enabled` | **Required** for tools in `connectors.write` / `connectors.identity`. Defaults to `true` for `connectors.read`. |
| `on_intent` | Optional list. The rule fires only when this turn's routing intent (`nlu_result.intent`) is one of these. |
| `when` | Optional routing conditions (`field` / `operator` / `value`, the same evaluator as routing), all against the routing state after this turn's writes. |
| `unless_fresh` | When true, the rule does not fire if the turn's tool cache already holds a fresh entry for `tool` (`latest_entry` not None). |
| `args` | One binding per tool parameter the model would supply. Session-sourced parameters (e.g. `acting_as_user_id`) are injected by the Action Gateway as today and must not be listed. |

### 3.2 Argument bindings

| Binding | Value |
|---|---|
| `{ from: session, key: K }` | The session value `K` (as written so far this turn: NLU writes and routing writes included). |
| `{ from: literal, value: V }` | `V`. |
| `{ template: "..." }` | Placeholders `{a}` or `{a\|b\|c}`, meaning the first non-empty of the session keys `a`, `b`, `c`. |
| `normalise` (optional, on any binding) | A map name → table applied to the named placeholder or to the value: `city_canonical`, `title`, `lower`, or an inline `{from: to}` map. |

**Empty values.** A value counts as missing if it is `None`, `""`, `[]` or `0`. These are the same seeded-empty values `_tool_session_values` drops. A rule with any missing required argument does not fire (outcome `skipped_missing_arg`).

**Validation.** Argument values are validated against the tool's agent-parameter schema from the tool registry: required fields, `format: uuid`, `enum`, and type coercion (int → string where the schema says string). A failed validation means the rule does not fire (`skipped_invalid_arg`).

**Built-in `city_canonical` table.** It holds the job_match prompt's existing city map (Bangalore → Bengaluru, Bombay → Mumbai, …). The table lives in the domain config under `predispatch_tables.city_canonical`, so it is not hard-coded Blue Dots data.

### 3.3 Selection

- Rules are evaluated for `next_subagent_id`, the subagent the prompt is built for.
- They are evaluated after routing and after the Step 6a/6b early returns (fixed opening, terminal copy), at most once per turn.
- The first rule whose conditions hold and whose arguments all resolve wins. Later rules are not evaluated.
- A pre-dispatch never changes this turn's routing. Values it maps into the session (`session_mapping`) are visible to the prompt and to the next turn's routing.

## 4. Startup validation (runtime `MergedConfig`, mirrored in the dev-kit)

- `tool` is declared in `connectors` and is in the subagent's resolved tools.
- Write and identity tools state `enabled` explicitly.
- Every `args` key is an agent-sourced parameter of the tool; no session-sourced parameter is listed.
- Template placeholders are non-empty identifiers.
- `normalise` names exist (built-in, or in `predispatch_tables`).
- `on_intent` values are intents some `act_intents` row can produce, or framework-handled intents.

## 5. Execution

### 5.1 One execute helper

The sequence below exists three times today, in sync `ManagerAgent.run_turn`, stream round 1 and the stream nested rounds:

1. cap check
2. grounding check
3. cache lookup
4. gateway call
5. `result_shaper.shape`
6. `_write_mapped_session_values`
7. `tool_cache.after_call`
8. persist

It is extracted into one helper used by all three and by pre-dispatch:

```
execute_tool_call(tc, *, bundle, tool_cache, counts, messages, consent_check) -> ToolResult
```

There are async and sync variants. Behaviour for model-initiated calls is unchanged. This is a refactor, pinned by the existing tool-loop and parity tests.

### 5.2 Guards, in order

1. **Consent.** If the tool requires consent (`ToolRegistry.requires_consent`), check it (`trust.check_consent`). Today the stream loop has no consent check and sync `_execute_tool` has one. The helper applies it on every path. It refuses with the same refusal result the sync path returns now.
2. **Per-turn cap.** `_turn_tool_counts` is hoisted above LLM call 1 on both paths. A pre-dispatch increments it. With `max_calls_per_turn: 1` on `apply_job` and `save_profile`, a pre-dispatched write cannot be repeated by the model in the same turn even if the tool were still offered.
3. **Grounding.** `ungrounded_params` against `stored_results_by_tool()` and `_session_grounded_values`. A job id the NLU resolved from cached `fetch_jobs` rows passes by construction; this is a backstop. The sync path gains `session_grounded`, fixing the existing divergence noted in the code map.
4. **Cache.** A cache hit returns the stored result with no gateway call. That still counts as a fired pre-dispatch.

### 5.3 Budget

A pre-dispatch gateway call has its own timeout: `min(tool timeout_ms, agent.predispatch_timeout_ms)`, where `agent.predispatch_timeout_ms` defaults to 1500.

On timeout:
- A **read** tool falls back (§5.5).
- A **write** tool whose request may have reached the upstream is treated as a write failure (§5.5). It is never silently retried.

### 5.4 Injection (success)

When the result is `success=True`:

- **Messages.** The utterance (`build_messages`) is followed by:
  - an assistant `ToolUseBlock(id="predispatch-1", name=tool, input=args)`;
  - a user `ToolResultBlock(tool_use_id="predispatch-1", content=result_text)`.

  This is the same shape the model sees after calling the tool itself, so prompts that read "the tool result" (apply_confirm's three outcomes) keep working. Anthropic's tool_use → tool_result ordering is respected, and the list ends on a user turn.
- **Cache.** The result is in the turn cache before Step 7, so `<known_facts>` and `<state>` (the offered list) render it.
- **Tools.** The pre-dispatched tool is removed from `active_tools` for this turn's main-LLM calls. Other tools stay available, along with `end_session` and `remember`.
- **Bookkeeping.**
  - The exchange is captured (`_capture_tool_exchange`) and counted in `_stream_tool_results` / sync `tool_results`. So `recent_tool_exchanges`, `served_tool_results` and the post-tool hooks (e.g. `post_applied`) behave as if the model had called it.

### 5.5 Failure

| Case | Read tool | Write tool |
|---|---|---|
| Rule did not fire (missing or invalid argument, fresh cache, disabled) | Today's path | Today's path |
| Guard refused (consent, cap, ungrounded) | Today's path | Today's path |
| Gateway error, `success=False`, or timeout | Today's path (the model may call the tool) | Inject the failure as the tool result, and remove the tool from `active_tools`. The prompt's failure lines are spoken, and the model cannot retry the same write. |

"Today's path" means nothing is injected and the tool stays offered. The turn is exactly as before Spec E, plus the pre-dispatch time spent (bounded by §5.3).

Pre-dispatch never raises into the turn. An internal error is treated as "did not fire" and counted as `error`.

## 6. Prompts (Blue Dots)

- **job_match, apply_confirm and profile_setup.** Each gets one line: "If a result for this step's tool is already in the conversation, speak from it; do not call the tool again."
- **apply_confirm.** Remove the stale instruction to "re-fetch fetch_jobs" before `apply_job`; that tool is not offered in apply_confirm. Replace "holds an ordinal such as 'तीसरा'" with the fact that `selected_job_item_id` holds the job's `item_id`.
- **job_match.** Its city-normalisation map moves to `predispatch_tables.city_canonical`. The prompt keeps one line: "use canonical city names".

## 7. Blue Dots rules

```yaml
predispatch_tables:
  city_canonical: { Bangalore: Bengaluru, Bombay: Mumbai, ... }   # moved from the job_match prompt

# job_match
predispatch:
  - tool: fetch_jobs
    unless_fresh: true
    args:
      query_text:
        template: "{trade|stored_trade} jobs in {location|stored_location}"
        normalise: { location: city_canonical }

# apply_confirm
predispatch:
  - tool: apply_job
    enabled: false
    on_intent: [apply_now]
    when: [{ field: applications_submitted, operator: eq, value: 0 }]
    args:
      profile_item_id: { from: session, key: profile_item_id }
      job_item_id:     { from: session, key: selected_job_item_id }

# profile_setup
predispatch:
  - tool: save_profile
    enabled: false
    when: [{ field: profile_item_id, operator: in, value: [null, ""] }]
    args:
      name:             { from: session, key: name }
      gender:           { from: session, key: gender, normalise: { male: Male, female: Female, other: Other } }
      location:         { from: session, key: location }
      trade:            { from: session, key: trade }
      work_experience:  { from: session, key: work_experience }
      experience_years: { from: session, key: experience_years }   # coerced to string by §3.2 validation
      qualification:    { from: session, key: qualification }
      job_nature:       { from: session, key: job_nature }
```

**Optional arguments** (all `save_profile` arguments except `name`) are omitted when missing. Only required arguments block firing.

**Placeholder names.** `save_profile` fires only if `name` is not a placeholder. The validation step rejects the prompt's forbidden placeholders, kept in `predispatch_tables.name_placeholders`.

## 8. Turning on a write rule

A disabled write rule is enabled only in a separate config PR, and only when both of these hold:

1. **NLU precision.** The NLU eval shows precision ≥ 0.98, on at least 50 cases including real-call replays, for the (act, pending) pair that drives the rule. For `apply_job` that is `affirm|submit_confirm`; for `save_profile`, `provide_info|name`.
2. **Scenario run.** A scenario-runner run with the rule enabled shows no duplicate and no wrong writes: one application per apply, and the job applied to equals the job confirmed.

## 9. Runtime ↔ dev-kit sync

Per `.claude/rules/runtime-devkit-sync.md`, every change below is mirrored in:
- the runtime schema;
- the domain mirror and the flat schema;
- FIELD_RULES;
- dpg defaults (`agent.predispatch_timeout_ms`);
- the dpg schema;
- the cross-block checks (§4).

The changes are:
- `SubagentConfig.predispatch: list[PredispatchRule]`;
- `predispatch_tables`;
- `agent.predispatch_timeout_ms`.

The `predispatch` FIELD_RULES are `auto_answer`, because the wizard does not author pre-dispatch rules in v1.

## 10. Observability

- **Metric.** `agent_core.predispatch.outcomes_total{tool, outcome}`, where `outcome` is one of `fired`, `cache_hit`, `skipped_missing_arg`, `skipped_invalid_arg`, `skipped_fresh`, `disabled`, `refused_guard`, `failed`, `timeout` or `error`.
- **Per-turn log extras** on `stream_turn_complete` and the sync equivalent:
  - `predispatch_tool`;
  - `predispatch_outcome`;
  - `predispatch_ms`;
  - `llm_calls` (the number of main-LLM calls this turn).
- **Never logged:** caller text and argument values. Only argument keys are logged.
- **Scenario runner.** It reads `llm_calls` and `predispatch_*` with an optional `docker logs` scrape of the agent container. This closes part of Spec D's `not_collected` list.

## 11. Testing

- **Pure units:**
  - binding resolution (session, literal, template with fallbacks, normalise maps, `city_canonical`);
  - empty-value rules;
  - schema validation (uuid, enum, coercion, placeholders);
  - rule selection (first wins, `on_intent`, `when`, `unless_fresh`, disabled).
- **Execute helper:**
  - guard order;
  - cap counting shared with model calls;
  - consent refusal;
  - the cache-hit path;
  - shaping and mapping applied;
  - the existing tool-loop tests unchanged.
- **Injection, on both paths:**
  - message shape;
  - the tool removed from `active_tools`;
  - **exactly one main-LLM call** on a pre-dispatched read turn;
  - `served_tool_results` and `recent_tool_exchanges` updated;
  - the `post_applied` hook still fires for a pre-dispatched `apply_job`.
- **Failure:**
  - read failure, refusal and timeout follow today's path (the tool still offered, two calls);
  - write gateway failure is injected and the tool removed;
  - an internal error is treated as did-not-fire.
- **Parity:** sync vs stream messages, tools and outcome for the same session.
- **Schema:** runtime and dev-kit accept and reject the same configs; Blue Dots validates; startup rejects an implicit `enabled` on a write.
- **Duplicate-write safety:** with `apply_job` enabled in a test config, a pre-dispatched apply plus a model attempt to call `apply_job` produces exactly one gateway call.

## 12. Acceptance

**Scenario runner,** Spec D baseline vs Spec E, on the local stack:
- job_match-entry turns drop from 2 main-LLM calls to 1;
- `first_sentence_ms` p50 on those turns drops by ≥ 600 ms;
- no regression in the runner's output checks.

**Eval:** no regression on `eval/nlu`.

**Docker:** deploy/validate is `runtime_baked`.

## 13. Risks

- **The pre-dispatched query differs from what the model would have sent,** e.g. trade wording. Mitigation: the template uses the NLU's English `trade` slot (title-cased) and the canonical city. Results are checked by the runner's job-order and relevance review.
- **Stale session values.** Pre-dispatch uses values after this turn's NLU writes, so a correction in this turn ("इलेक्ट्रीशियन नहीं, वेल्डर") is already applied.
- **Prompt drift.** A prompt still telling the model to call the tool is covered by the tool's removal from `active_tools` and the §6 line.
- **Write misfire,** once enabled. Mitigated by the per-turn cap, grounding, the `on_intent` gate, the §8 evidence bar, and the `applications_submitted == 0` condition on `apply_job`.
