# KKB Subagent Collapse & Intent Pruning

**Date:** 2026-04-23
**Target domain:** KKB (`dev-kit/configs/kkb/agent_core.yaml`)
**Scope:** Config-only refactor. No changes to orchestrator core, NLU processor, or LLM wrapper beyond one small post-tool hook and a tool-list wiring change.

## Problem

Today's KKB workflow has 14 subagents and 41 NLU intents with heavy overlap:

- Many intents route to the same `next_subagent_id` (e.g. `interested_engaged`, `skill_direct_match`, `skill_partial_match` all lead to `evaluation`).
- Mental-state, persona, and intent taxonomies are cross-cutting — the user can hit `pay_disappointment` from anywhere, but it's wired as subagent-specific routing.
- Pay/distance/normalise "branches" are hesitation signals with a reason, not distinct journey states.
- Per-subagent tool scoping is cosmetic — tool `invocation_rules` already enforce preconditions (trade+location before `onest_market_lookup`, consent before `apply_job`).

The KKB user journey is fundamentally three states:

1. **Enquiry** — exploring the landscape, no specific path in focus.
2. **Commitment** — engaged with a specific path: comparing, checking fit, trading off, or acting.
3. **Post-applied** — an application has been submitted; the next session (or later turns) is about follow-up.

Signals like counsellor-request, drop-off, and clarification are user signals — not subagent-specific states.

## Goals

- Collapse 14 subagents → 3 journey subagents + 2 infrastructure subagents + 1 escalation terminal.
- Prune 41 intents → 13.
- Make all tools available on every turn via a new `global_tools` list; rely on each connector's `invocation_rules` for gating.
- Keep NLU unchanged in this spec. Keep orchestrator, manager_agent, and schema loader largely unchanged.
- Preserve semantic content of the old subagent prompts by merging them into the three new prompts with a validity-check pass to eliminate dead references.

## Non-goals

- Changing routing model from NLU-driven to LLM-driven (deferred).
- Making NLU async or rebuilding its prompt for caching (future spec).
- Removing per-subagent `tools` field from the config schema — kept for other domains.
- Changes to Trust Layer, Memory Layer, or Observability.

## Design

### 1. Subagent set

| ID | Name | Role | Terminal? |
|---|---|---|---|
| `enquiry` | Enquiry | Exploring landscape: profile capture, market picture. Absorbs `profile_building` + `market_truth`. `is_start: true`. | No |
| `commitment` | Commitment | Engaged with specific path: compare, skill-fit, trade-offs, consent, apply. Absorbs `skill_check`, `evaluation`, `pay_branch`, `distance_branch`, `normalise_branch`, old `commitment`. | No |
| `post_applied` | Post-Applied Follow-Through | Post-action: did employer call, did job match, outcome capture. Absorbs `follow_through`. | Yes |
| `escalation` | Counsellor Escalation | Terminal used when `enquiry` 5-turn safety net trips and minimums can't be met. Fires `counsellor_schedule` and ends. | Yes |
| `clarification` | Clarification | `default_fallback_subagent_id`. Unchanged from today. | No |
| `ended` | Session Ended | Triggered by `termination_intent` or `end_session` tool. Unchanged from today. | Yes |

**Dropped:** `profile_building`, `profile_incomplete`, `market_truth`, `skill_check`, `evaluation`, `pay_branch`, `distance_branch`, `normalise_branch`, `commitment` (old), `follow_through`, `counsellor_request`, `capture_dropoff`.

Drop-off codes (DOP_MT, DOP_OP, DOP_EV, DOP_WA, DOP_MS, DOP_SI, DOP_RL) are now inferred at session flush by Observability/Memory from `(last_subagent, last_intents, session_state)` — no subagent needed.

### 2. Intent taxonomy (13)

**Routing intents (5):**

| Intent | Drives |
|---|---|
| `evaluate_option` | `enquiry` → `commitment` (user picks a path to go deeper on) |
| `apply_now` | Within `commitment`: triggers `apply_job` tool. Also valid as `enquiry` → `commitment` shortcut. |
| `explore_more` | `commitment` → `enquiry` (hesitation, wants other roles, "let me think") |
| `termination_intent` | any → `ended` (global) |
| `language_switch_request` | special-handled by orchestrator language-switch branch (global) |

**Signal intents (no routing; emit to Neo4j `signal_intents` map) (4):**

| Intent | Signal type |
|---|---|
| `pay_disappointment` | objection |
| `distance_issue` | constraint |
| `overwhelmed_silent` | emotion |
| `counsellor_request` | escalation_signal — orchestrator also allows LLM to call `counsellor_schedule` tool |

**Post-apply intents (2):**

| Intent | Signal type |
|---|---|
| `outcome_positive` | outcome_signal |
| `outcome_negative` | outcome_signal — NLU entity `outcome_reason` ∈ {`employer_ghost`, `job_mismatch`, `no_response`} captures the sub-cause |

**Infrastructure (2):** `any_input`, `unknown`.

**Absorbed/dropped:**

- `interested_engaged`, `skill_direct_match`, `skill_partial_match`, `skill_significant_gap`, `ready_to_apply` → `evaluate_option` or `apply_now`
- `wants_to_think`, `not_ready_yet`, `action_declined`, `re_engaged`, `expectation_adjusted`, `constraint_flexible`, `skip_question`, `wants_counsellor` (user wants to think / back out) → `explore_more`
- `expectation_firm` → `pay_disappointment`
- `constraint_hard` → `distance_issue`
- `hang_up`, `drop_off_acknowledged` → `overwhelmed_silent`
- `enrol_course` → `apply_now` (with tool choosing apply vs enrol based on context)
- `whatsapp_handoff_request` → folded into `commitment` prompt handling; a dedicated `whatsapp_send` tool will be added later when the connector lands (out of this spec).
- `profile_answer`, `profile_complete`, `greeting`, `returning_user`, `new_user`, `market_truth_query`, `evaluation_question` → `any_input`
- `outcome_employer_ghost`, `outcome_job_mismatch`, `outcome_no_response` → `outcome_negative` + entity

Entity list unchanged. `signal_intents` map under `preprocessing.nlu_processor.signal_intents` updated to the new 4-signal + 2-outcome set.

### 3. Tool globalization

Add at `agent_workflow` level:

```yaml
agent_workflow:
  global_tools:
    - get_profile
    - update_profile
    - onest_market_lookup
    - knowledge_retrieval
    - apply_job
    - counsellor_schedule   # once action_gateway connector is uncommented
    - end_session
```

Orchestrator change (one site, `agent_core/src/orchestrator.py` around line 938):

```python
# Before
active_tools = self._workflow.tool_defs.get(next_subagent_id, [])
# After
active_tools = self._workflow.global_tool_defs or self._workflow.tool_defs.get(next_subagent_id, [])
```

Schema change (`agent_core/src/schema/config.py`): add optional `global_tools: list[str]` field on the workflow model. `workflow_loader.py` resolves tool defs the same way it resolves per-subagent tools.

Per-subagent `tools` field preserved in schema (marked optional, unused for KKB, honored if `global_tools` absent for other domains).

### 4. Routing rules

Per subagent, short and non-overlapping.

```yaml
# enquiry
routing:
  # 5-turn safety net — minimums still missing → escalation
  - intent: "*"
    conditions:
      - field: subagent_entry_count.enquiry
        operator: gt
        value: 4
      - field: trade_or_stream
        operator: eq
        value: null
    next_subagent_id: escalation
  - intent: "*"
    conditions:
      - field: subagent_entry_count.enquiry
        operator: gt
        value: 4
      - field: location
        operator: eq
        value: null
    next_subagent_id: escalation
  - intent: evaluate_option
    next_subagent_id: commitment
  - intent: apply_now
    next_subagent_id: commitment
  - intent: "*"
    next_subagent_id: enquiry

# commitment
routing:
  - intent: explore_more
    next_subagent_id: enquiry
  - intent: "*"
    next_subagent_id: commitment
  # enquiry ← post_applied transition handled by orchestrator
  # post-tool hook, not by NLU intent (see §5).

# post_applied
routing: []   # terminal

# escalation
routing: []   # terminal

# ended / clarification — unchanged
```

Global routing simplified to:

```yaml
global_routing:
  - intent: termination_intent
    next_subagent_id: ended
```

`counsellor_request` becomes a non-routing signal (signal-only); the LLM is expected to call `counsellor_schedule` tool when appropriate, prompted by enquiry/commitment system prompts.

### 5. Orchestrator post-tool hook — one new branch

Add to orchestrator's post-tool-result processing (single site):

```python
# After tool_use loop, before output trust check
for tr in tool_results:
    if tr.name == "apply_job" and tr.status == "success":
        self._write_memory_sync(session_id, user_id, "session",
                                "current_subagent_id", "post_applied")
        bundle.session["current_subagent_id"] = "post_applied"
        break
```

This replaces the current `subagent_entry_count.commitment > 1 → follow_through` routing rules, which are brittle proxies for "tool ran already". Exact location: inside `ManagerAgent.run_turn` result handling or immediately after it in `orchestrator.process_turn_sync`.

### 6. Prompt merge with validity check

Each new subagent's `system_prompt` is assembled from the corresponding old prompts, deduplicated and tone-harmonised:

| New subagent | Merged from |
|---|---|
| `enquiry` | `profile_building.system_prompt` + `market_truth.system_prompt` + `profile_incomplete` 5-turn escalation guidance |
| `commitment` | `skill_check` + `evaluation` + `pay_branch` + `distance_branch` + `normalise_branch` + old `commitment` |
| `post_applied` | `follow_through` (mostly unchanged) |
| `escalation` | terse new content: acknowledge, offer counsellor callback via tool, close |

**Validity check pass** (manual, applied after merge):

For each merged prompt, grep for and fix:

- References to dropped subagent names: `skill_check`, `evaluation`, `pay_branch`, `distance_branch`, `normalise_branch`, `profile_building`, `profile_incomplete`, `market_truth`, `follow_through`, `commitment` (as a name), `counsellor_request`, `capture_dropoff`.
- References to dropped intents: `skip_question`, `wants_to_think`, `interested_engaged`, `ready_to_apply`, `action_declined`, `constraint_flexible`, `expectation_adjusted`, `re_engaged`, `profile_answer`, `profile_complete`, `enrol_course`, `hang_up`, `drop_off_acknowledged`, `skill_direct_match`, `skill_partial_match`, `skill_significant_gap`, `market_truth_query`, `evaluation_question`, `wants_counsellor`, `expectation_firm`, `constraint_hard`, `outcome_employer_ghost`, `outcome_job_mismatch`, `outcome_no_response`, `returning_user`, `new_user`, `greeting`, `whatsapp_accepted`, `whatsapp_declined`, `whatsapp_handoff_request`.
- References to dropped `special_handler`: `whatsapp_handoff`, `hitl`.
- Dangling routing directives ("hand off to X", "transition to Y") — rewrite to describe in-prompt behaviour.

User mental-state guidance (`conversation.user_state_model`) retained unchanged — it's cross-cutting and layered in by the orchestrator at prompt-assembly time.

### 7. What stays unchanged

- NLU processor, preprocessing config (`preprocessing.nlu_processor` except for the intent list and `signal_intents` map), language normalisation.
- Trust Layer, Memory Layer, Observability Layer.
- `tool_result_mappings` (ONEST → Neo4j Role nodes).
- `conversation.user_state_model` (5 mental states: fog / orientation / evaluation / commitment / follow_through). Note: the `commitment` mental state and the `commitment` subagent are orthogonal — both kept, no renaming.
- `channels.*` (voice/web/cli prompt suffixes and turn assembler config).
- All connector definitions (`connectors.read.*`, `connectors.internal.*`) and their `invocation_rules`.
- Entity list and `entity_to_profile_field` map (add only `outcome_reason`).

## Migration & testing

1. **Write new config** in a branch. Full file replacement of `dev-kit/configs/kkb/agent_core.yaml`.
2. **Loader validation** — `uv run pytest agent_core/tests/test_workflow_loader.py` must pass against the new config. Add a test asserting `global_tools` resolves to 7 tool defs.
3. **Orchestrator hook** — add post-`apply_job` subagent-switch branch. New unit test: apply_job returns success → next turn `current_subagent_id == "post_applied"`.
4. **NLU intent set** — update `nlu_processor.intents` list. Existing NLU tests that reference pruned intents: rewrite or delete. Add tests for the four absorption cases (`wants_to_think` user message → `explore_more`, etc.).
5. **Integration smoke** — run the KKB reach_layer CLI against a known trade+location scenario and verify: (a) `get_profile` fires on turn 1, (b) `onest_market_lookup` fires once minimums met, (c) `apply_now` intent on a specific option routes to `commitment`, (d) `apply_job` success moves user to `post_applied`.
6. **5-turn safety net** — scripted test: 5 turns without trade or location → `escalation` subagent entry and `counsellor_schedule` tool invocation attempt.

## Risks

- **Prompt merge loses nuance.** Three long merged prompts may blur tone that was subagent-scoped (e.g. `market_truth`'s strict no-invention rule vs `evaluation`'s trade-off framing). Mitigation: keep the strict data rules as a separate anchored section in the `enquiry` prompt, and the trade-off framing as a separate section in `commitment`. Review via real voice runs.
- **`counsellor_schedule` tool not yet live.** `escalation` subagent's fallback should degrade gracefully to a spoken message referencing a later callback if the tool is not registered. The `action_gateway` config currently has it commented out.
- **NLU re-training.** Reducing 41 → 13 intents shifts the classifier's task. Sonnet 4.6 will be fine on the new prompt (labels are semantically clearer), but the threshold (`confidence_threshold: 0.5`) may need re-tuning after initial runs.
- **Entity freshness.** Today's entity sync-write before routing is still needed because routing conditions (e.g. `trade_or_stream == null`) depend on this turn's NLU output. Unchanged.

## Deferred / follow-up

- NLU prompt restructure with cache-checkpoint boundaries and XML-delimited sections (separate spec).
- Replace NLU-driven routing with LLM-driven routing via structured output or meta-tool (separate spec, non-trivial latency + schema implications).
- WhatsApp send connector and intent handling.
- `counsellor_schedule` connector wire-up in `action_gateway.yaml`.

## Files touched

- `dev-kit/configs/kkb/agent_core.yaml` — full rewrite (subagents, intents, routing, global_tools).
- `agent_core/src/schema/config.py` — add `global_tools: list[str] | None` to workflow model.
- `agent_core/src/workflow_loader.py` — resolve `global_tool_defs` from `global_tools`.
- `agent_core/src/orchestrator.py` — use `global_tool_defs` if present; add post-`apply_job` subagent-switch branch (~15 lines total).
- `agent_core/tests/test_workflow_loader.py` — add `global_tools` resolution test.
- `agent_core/tests/test_orchestrator.py` — add post-tool subagent-switch test.
- `agent_core/tests/test_nlu_processor.py` — update intent assertions to new 13-intent taxonomy.
