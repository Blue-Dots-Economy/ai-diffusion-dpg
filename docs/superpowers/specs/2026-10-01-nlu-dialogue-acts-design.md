# NLU Dialogue Acts — Design

**Date:** 2026-10-01
**Status:** Draft, awaiting review
**Baseline:** `deploy/voicera-vm` at `9efa8cc` (Spec A #421 and Spec B #424 merged). All file and function references are against that branch. This work reaches `main` when that branch is promoted.
**Builds on:** Spec A — Tool-Result Persistence (`2026-09-30-tool-result-persistence-design.md`, merged as #421). Spec B — Session Bootstrap (`2026-10-01-session-bootstrap-design.md`, merged as #424). This spec makes one small extension to Spec A's `TurnToolCache` (§8).
**Followed by:** Spec D — Main-LLM context and Blue Dots prompts. Spec E — Deterministic turn actions (depends on this spec).

## 1. Problem

The NLU step classifies each caller turn before routing. On Blue Dots it is the only writer of most routing facts. It is inaccurate in ways that break calls, and it is the least-informed LLM call in the turn.

### 1.1 What the NLU call receives today

`NLUProcessor.process()` sends a static system prompt (the domain `domain_instruction`, the current subagent's intent list, the entity list, a JSON shape) and a single user message with up to four lines:

```
Current workflow step: <subagent id>
Last question asked: <the bot's previous reply, first 500 chars>
Existing profile fields: [<keys of the stored profile>]
User message: <raw utterance>
```

It never sees:

- earlier turns;
- any stored **values**. Only key names appear, and only from the stored profile. Blue Dots stores NLU entities at session scope, so facts said earlier in the call never appear at all;
- tool results, including the job list the caller is choosing from;
- the subagent's purpose, or what answer the bot is waiting for.

The class docstring says recent session history is injected. It is not.

Specs A and B improved what the **main** LLM sees: stored tool results in `<known_facts>`, and `agent.prompt_session_fields` rendered into `<known_profile>`. Spec B also puts the bootstrap `fetch_profile` result into session state before NLU runs on turn 1. None of that reaches NLU. It still gets only the four lines above.

### 1.2 What goes wrong with the output

- **The intent set has no home for common turns.** Intents are per-subagent and journey-shaped (`apply_now`, `explore_more`, `decline`). An acknowledgement ("ठीक है धन्यवाद"), a correction ("इलेक्ट्रीशियन नहीं वेल्डर") and a question ("सैलरी कितनी है?") are forced into the nearest wrong label. On two real calls on 30 Sep, "ठीक है धन्यवाद" was classified `apply_now` at 0.90–0.95. In other sessions a thank-you is read as a goodbye. `termination_intent` at ≥0.7 short-circuits to hang-up without the main LLM.
- **`unknown` is manufactured.** `unknown` is in no subagent's intent list, yet the prompt tells the model to use it. Any label outside the current list is rewritten to `unknown`, and the model's confidence is kept. "unknown at 0.95" on unambiguous speech is this rewrite.
- **Confidence is uninformative.** Wrong answers scored 0.90–0.95, the same as right ones.
- **References cannot be resolved.** The config asks NLU for `selected_job_item_id` "from the just-read-out jobs", but NLU never sees them. It emits the spoken reference ("तीसरा", a company name). No code maps that to a job.
- **The shape is inconsistent.** `domain_instruction` asks for entities "as a {name, value} pair". The schema asks for a flat dict. A non-dict `entities` is silently dropped. There is no JSON mode. The configured `confidence_threshold` is never read.

### 1.3 What goes wrong after the output

- Entity writes have no allowlist and no empty-value guard. An empty value overwrites a good one, and invented keys land in session state where routing reads them.
- Routing and the prompt overlay let the stored profile win over session. A spoken correction loses to a stale stored value.
- The main LLM never sees what NLU concluded. Routing and the reply read the same utterance independently and can disagree.
- `signal_intents` (`pay_disappointment`, `distance_issue`) are recorded on the sync path only.
- Interrupted streaming turns skip the `current_question` write, so the next NLU sees a stale question.

### 1.4 Latency tail

NLU reuses the main agent's chat provider when the model matches: `timeout_ms: 10000`, `retry_attempts: 2`, and the OpenAI SDK's own default retries underneath. One real call spent 22.8 s in NLU. It is blocking, so the caller waits for all of it.

## 2. Goals and non-goals

**Goals**
- NLU classifies **what the caller did**, relative to **what the bot is waiting for**, using a small fixed vocabulary that does not change per subagent.
- NLU receives the context it needs: the pending question, the options on offer, known values and the last two exchanges.
- NLU extracts what the caller **said**. Deterministic code resolves references against known data and derives the intents routing consumes.
- Off-script turns are recognised and do not move journey state.
- A thank-you or an acknowledgement never ends the call.
- The main LLM receives NLU's conclusion in structured form.
- Bounded latency: p50 no worse than today, and a hard ceiling on the tail.
- Accuracy is measured on a replay eval set, and the switch-over is gated on it.
- Config-driven and domain-agnostic, behind an opt-in mode. Existing domains are unchanged.
- Identical behaviour on the sync (`process_turn`) and stream (`stream_turn`) paths.

**Non-goals**
- Moving NLU off the critical path, e.g. running it after the reply. That is a separate decision.
- Skipping the main LLM or pre-dispatching tools from NLU's output. That is Spec E, built on Spec B's step runner and gated by this spec's eval harness.
- Fixing the main LLM's own inputs and the Blue Dots prompts: dead `tts_rules`, prompts that reference state the model cannot see, conversation history for the main LLM, and prompt contradictions. That is Spec D. This spec adds only the `<caller_turn>` hand-off (§6.8).
- Language normalisation, the user-state model, and the turn-assembler semantic gate. These stay on `intent` mode.
- Persisting caller corrections to the upstream profile. `save_profile` still does that.
- The #411 interaction where an interrupted turn commits routing before it aborts (§13).

## 3. Existing mechanisms this builds on

| Mechanism | Where | Role here |
|---|---|---|
| `NLUProcessor` | `agent_core/src/preprocessing/nlu_processor.py` | Stays as `intent` mode, unchanged. |
| Routing rules and `_evaluate_condition` | `orchestrator.py`, `_resolve_next_subagent` | Unchanged. Also reused to resolve the pending question (§7.2). |
| `entity_to_profile_field` | domain `agent_core.yaml` | Maps slot names to state keys. Reused. |
| `ChatRequest.output_format` (`json_schema`, strict) | `chat_provider/types.py`; OpenAI native `response_format`, Anthropic tool-coercion | The NLU output contract (§5.3). Already implemented for `call()`. |
| `build_chat_provider`, `_build_helper_provider` | `chat_provider/__init__.py`, `orchestrator.py` | Builds the dedicated NLU client (§9.1). |
| Spec A store, `TurnToolCache`, `<known_facts>` | Memory Layer Redis; `agent_core/src/tool_results.py` | Source of the `offered` options (§5.2) and of the resolver's IDs (§6.3). Entries are keyed by `(tool, args_hash)` and carry `fetched_at`. Only tools with a `cache` policy are stored. |
| Grounding over stored results | `ungrounded_params(..., stored_results=tool_cache.stored_results_by_tool())` | A resolved ID comes from a stored entry, so `grounded_params` already accepts it. Nothing is added here. |
| Spec B `session_bootstrap` | `agent_core/src/session_bootstrap.py`; runs right after the memory read on both paths | Its `session_mapping` values (e.g. `has_age`, `user_terms`, `stored_trade`) are in session **before** NLU on turn 1, so the pending question and `known` are accurate from the first turn. |
| `_profile_context` overlay, `agent.prompt_session_fields` | `orchestrator.py` | Builds `<known_profile>`. §6.5 changes its precedence for NLU-written slots only. |
| `remember` (Spec A §8) | `agent_core/src/remember.py` | Not used by this spec. Blue Dots leaves it off (unprompted calls, ungrounded rejections). §6.3 writes the caller's choice deterministically instead. |
| `recent_tool_exchanges`, `current_question` | session state | `current_question` stays for `intent` mode. `recent_turns` is new (§7.5). |
| `log_raw_response` | NLU config | Extended to capture eval cases (§10.2). |

## 4. Concepts

| Concept | What it is |
|---|---|
| **Dialogue act** | What the caller did in this turn, from a fixed framework list: `affirm`, `deny`, `acknowledge`, `provide_info`, `correct`, `select`, `ask`, `request_change`, `repeat`, `hold`, `close`, `other`. A turn has 1–3 acts, in order. |
| **Relation** | How the turn relates to the pending question: `answers_pending`, `answers_other`, `new_topic`, `unrelated`, `unclear`. Independent of the act: an `affirm` can answer the pending question or answer something else. |
| **Topic** | For `ask` and `request_change` only: what it is about, from a domain list. |
| **Pending question** | What the bot is waiting for, e.g. `consent`, `age`, `select_job`, `submit_confirm`, `closing_offer`. Each subagent declares candidates, and the orchestrator picks one from state with no LLM involved (§7.2). |
| **Frame** | The per-turn context block sent to NLU: step, pending question, offered options, known values. |
| **Slots** | Caller-stated values from a fixed domain list. `null` when not said. |
| **Reference** | Which offered option the caller meant, as an option number plus the spoken words. |
| **Derived intent** | The `intent` routing consumes, computed in code from (acts, pending, relation, topic) by a config table. Routing rules keep their current shape. |
| **`<caller_turn>`** | A structured summary of the understanding, rendered into the main LLM's prompt. |

## 5. The NLU call contract (`dialogue_act` mode)

### 5.1 System prompt (static, prompt-cached)

Rendered once per domain at startup:

- the act and relation definitions (framework text);
- the topic list and slot definitions from config: each slot's description, its normalisation, and 1–2 examples;
- the domain's few-shot `examples` (§7.1). These cover the known traps: "धन्यवाद" → `acknowledge`; "ठीक है" → `affirm` only when a yes/no question is pending; "X नहीं Y" → `correct`; ordinals and company names → `select` with an option number; Hindi numerals ("बाईस") → digits;
- output rules: extract only what the caller said, `null` for anything not said, never infer consent from a yes to a different question.

It replaces the ~1.3k-token free-text `domain_instruction`. The target is ~900 tokens, with the schema keeping the prefix above the provider's 1024-token cache floor. The system prompt must not vary per subagent, so the whole prompt is cached across the call. Today, the per-subagent intent line ends the shared cached prefix at that point.

### 5.2 User message (per turn)

```
<frame>
step: job_match
pending: select_job — offered jobs में से एक, या दूसरी search
offered:
  1. Welder · Flipkart Fulfilment · Electronic City
  2. Welder · Titan Retail · Malleshwaram
  3. Welder · Sonata Software · Jayanagar
known: consent=granted · age=25 · trade=Welder · location=Bengaluru
</frame>
<recent>
bot: <reply before last>
caller: <utterance before last>
bot: <last reply>
</recent>
<caller_now>
<utterance>
</caller_now>
```

- `pending`: the resolved pending question's id and its `expects` text (§7.2). When no candidate matches, it is `none`.
- `offered`: present only when the pending question declares `options_from`. Rows come from the **last served** unexpired Spec A entry for that tool (the entry the caller last heard, by live store or cache hit, tracked in the `served_tool_results` session map), falling back to the highest `fetched_at` (a new search replaces the previous list). Rows keep stored order, with the configured display fields. Numbering starts at 1. It is capped at the number of rows the tool projection returns. The tool must have a `cache` policy (§7.3).
- `known`: values for `known_fields` only, read from the same overlay as `<known_profile>` with §6.5 precedence. `known_fields` may name session fields written by `session_mapping` (e.g. `stored_trade`, `stored_location`), so NLU can understand a "हाँ" to "shall I use your saved details?". Values are rendered as stored.
- `recent`: the last `history_turns` exchanges from `recent_turns` (§7.5). An interrupted bot reply is marked `[interrupted]`. When a reply exceeds the per-entry cap, the **tail** is kept, because the question is at the end.
- `caller_now`: the utterance. When #411 folds carried-over segments into the turn, each segment is listed on its own line, and all but the last are marked `[interrupted]`. Today the fold joins them with spaces and no marker.

Size: `frame` ~60 tokens, or ~150 with three offered jobs. `recent` ~150–250 tokens.

### 5.3 Output (strict JSON schema)

Sent as `ChatRequest.output_format` with `strict: true`, and generated from config at startup:

```json
{
  "acts": ["select", "affirm"],
  "relation": "answers_pending",
  "topic": null,
  "slots": { "consent": null, "age": null, "trade": null, "location": null, "name": null },
  "reference": { "option": 1, "spoken": "पहले वाला" },
  "signals": [],
  "extras": []
}
```

- `acts`: 1–3 items from the act enum.
- `relation`, `topic`: enums. `topic` is `null` unless an act is `ask` or `request_change`.
- `slots`: every configured slot key, each nullable, typed per config (enum, integer or string).
- `reference`: `option` is an integer or `null`; `spoken` is the caller's words or `null`.
- `signals`: items from the domain's `signals` list (today's `signal_intents` keys).
- `extras`: ad-hoc details as a list of `{key, value}` string pairs. A list, not a free-form map, because strict structured output requires every object to declare its keys (`additionalProperties: false`, all keys `required`; optional values are typed nullable). Written under a namespaced key that routing never reads (§6.5).

Removed compared with `intent` mode: `sentiment` (read by nothing) and the self-reported `confidence`.

## 6. After NLU returns

A pipeline of small stages. Each takes the previous stage's output and the turn context, and each is unit-tested on its own. The result is a `TurnUnderstanding` (§8).

### 6.1 Normalise

Each slot is checked against its config:
- `int` with `min`/`max`: digits only, in range;
- `enum`: exact value;
- `string` with `normalise: title`: trimmed and title-cased.

A slot that fails becomes `null`, and the rejection is recorded with its reason. Nothing downstream ever sees an empty string.

### 6.2 Accept only what was asked for

A slot with `accept_when_pending` is kept only if the resolved pending id is in that list, or the acts include `correct`. Example: `consent` is accepted only while `consent` is pending, so "हाँ, नौकरी ढूंढो" later in the call is never consent. Slots without the setting are accepted on any turn. A trade given while age is pending is kept, and age stays pending.

### 6.3 Resolve the reference

If `reference.option` is set and the pending question has `options_from`, the option number is mapped to the row's `id_field` (e.g. `item_id`) in the same stored entry that rendered `offered` — the last-served entry (`TurnToolCache.served()`, persisted at end of turn as `served_tool_results`), else the newest. The result is `resolved: {option, id, label}`, or `unresolved: {option, reason}` when the option is out of range or the entry has expired. The resolved ID is written to the pending question's `resolves_to` state key (e.g. `selected_job_item_id`). It needs no extra grounding: it comes from a stored entry, and `ungrounded_params` already checks against `stored_results_by_tool()`.

**Dependency on Spec D:** the bot must present options in stored order. Today the job-match prompt re-ranks the first batch by salary before speaking, so spoken order and stored order can differ.

### 6.4 Derive the intent

`act_intents` is an ordered table. The first row whose `acts`, `pending`, `relation` and `topic` all match (each optional) gives the intent. When no row matches, the intent is `any_input`. `acts` in a row matches if the turn's act list contains all of them.

Rows marked `gated: true` are checked against the termination gate (§6.7) during this match. A gated row whose gate fails is skipped, as if it did not match.

A row that requires `relation: answers_pending` never fires on an off-script turn. So an `affirm` that answers something other than "shall I submit?" can never become `apply_now`.

The derived intent is placed in the `NLUResult.intent` routing already consumes. Routing rules are unchanged. `unknown` is no longer produced in this mode.

### 6.5 Write slots

- Only configured slot keys are written, via `entity_to_profile_field`, at the configured `entity_persistence.scope`.
- `null` slots are never written.
- A value that differs from the stored one is overwritten, with the old and new values logged as hashes.
- **A value written by `SlotWriter` this session wins over the stored profile**, in routing state, in `_profile_context` (`<known_profile>`) and in `_tool_session_values`. Today the stored profile always wins, which hides a spoken correction until the profile is saved. The rule is provenance-based: `SlotWriter` records each key it writes in a `slot_provenance` session list, and only those keys get session precedence. Any other session copy keeps today's profile-first rule. That preserves the guard documented on `_profile_context` against the age-stuck-at-0 leak, where a stale session `"0"` outranked a fresh profile `25`. `SlotWriter` itself can never write such a value, because invalid and `null` slots are dropped (§6.1).
- `extras` are written as a single `nlu_extras` session map, which routing never reads.
- `signals` are written through the existing `signal`-scope Memory Layer write (one Signal node per signal, as the sync path does today for `signal_intents`), on both paths.

### 6.6 Off-track counter

`off_track_count` (session integer) increments when `relation` is `unrelated` or `unclear`, and resets to 0 on `answers_pending`. Other relations leave it unchanged. When it reaches `off_track.threshold`, the derived intent is replaced with `off_track.intent` (default `off_track`), the counter resets to 0, and `<caller_turn>` gains an "off track" line telling the main LLM to re-ask the open question simply or offer to end the call. The domain routes the intent with an intent-specific rule placed before its `*` rules. It routes either to a recovery subagent or back to the same subagent. Global routing is not enough, because a subagent's own `*` rules match first. A fallback result (§9.2) never changes the counter. The replacement never overrides a derived `termination_intent` that passed its gate.

### 6.7 Termination gate

A row with `gated: true` (e.g. `close` → `termination_intent`) produces its intent only if one of `termination_gate.any_of` holds: a pending id, or a state condition via `_evaluate_condition`. Otherwise the row is skipped and the derived intent falls through to the next row, normally `any_input`, with `close` visible in `<caller_turn>`. In that case the main LLM confirms before ending the call. `acknowledge` has no path to termination. The existing termination short-circuit only ever sees a gated intent.

### 6.8 Hand-off to the main LLM

`build_system_prompt` renders `<caller_turn>` in Tier 3 (dynamic, not cached), next to Spec A's `<known_facts>`:

```
<caller_turn>
acts: select, affirm · relation: answers_pending
pending: select_job (now answered)
resolved: option 1 — Welder · Flipkart Fulfilment · Electronic City (item_id 7f3c…)
updated: trade Electrician → Welder (caller corrected)
not accepted: age "150" (out of range)
open: age — still unanswered
signals: pay_disappointment
</caller_turn>
```

Lines are omitted when empty. On an off-script turn it states the topic and that the pending question is still open, e.g. `relation: new_topic · topic: salary · open: age`. On a fallback it says `understanding unavailable this turn`. The raw utterance still reaches the main LLM unchanged. Prompt wording that uses the block is Spec D.

### 6.9 End of turn

- Append `{bot, caller, interrupted}` to `recent_turns`, capped at `history_turns × 2` entries. Written on completed **and** interrupted turns.
- On an interrupted streaming turn, write `current_question` and a `recent_turns` entry from the sentences actually emitted before the interruption (`TurnRecord.spoken`, recorded where events are stamped). When nothing was emitted, nothing is written. This fixes a stale question for both modes.

## 7. Configuration

### 7.1 NLU block

Framework defaults (`dev-kit/dpg/agent_core.yaml`) supply the act and relation definitions, `history_turns: 2`, the timeouts, and `off_track`. The domain supplies the rest:

```yaml
preprocessing:
  nlu_processor:
    mode: dialogue_act              # intent (default) | dialogue_act
    provider: openai
    model: gpt-4.1-mini-2025-04-14
    timeout_ms: 2500
    retry_attempts: 2               # total attempts; the 2nd only after a fast failure (§9.1)
    history_turns: 2
    topics: [job_details, salary, location, search, profile, process, identity, other]
    signals: [pay_disappointment, distance_issue, counsellor_request]
    slots:                          # state key via entity_to_profile_field (consent → consent_response)
      consent:  { type: enum, values: [granted, declined], accept_when_pending: [consent],
                  description: "details save करने की अनुमति का जवाब" }
      age:      { type: int, min: 14, max: 80, accept_when_pending: [age],
                  description: "उम्र, digits में", examples: ["बाईस → 22"] }
      trade:    { type: string, normalise: title, description: "काम/ट्रेड" }
      location: { type: string, normalise: title, description: "शहर" }
      name:     { type: string, description: "caller का नाम" }
      # qualification, work_experience, job_nature, monthly_in_hand …
    known_fields: [consent, age, trade, location, name]
    examples:
      - { pending: closing_offer, caller: "ठीक है धन्यवाद",
          out: { acts: [acknowledge], relation: answers_pending } }
      - { pending: submit_confirm, caller: "ठीक है धन्यवाद",
          out: { acts: [acknowledge], relation: unclear } }
      - { pending: select_job, caller: "इलेक्ट्रीशियन नहीं वेल्डर का काम",
          out: { acts: [correct], relation: answers_other, slots: { trade: Welder } } }
    act_intents:
      - { acts: [affirm], pending: submit_confirm, relation: answers_pending, intent: apply_now }
      - { acts: [deny],   pending: submit_confirm, relation: answers_pending, intent: decline }
      - { acts: [select], pending: select_job, intent: job_pick }
      - { acts: [request_change], topic: search, intent: explore_more }
      - { acts: [close], intent: termination_intent, gated: true }
    termination_gate:
      any_of:
        - { pending: closing_offer }
        - { field: applications_submitted, operator: gt, value: 0 }
    off_track: { threshold: 3, intent: off_track }
```

In `dialogue_act` mode, the `intents`, `entities`, `domain_instruction`, `confidence_threshold` and `sentiment_classes` keys are ignored, and a startup warning is logged if they are present.

### 7.2 Pending questions per subagent

```yaml
- id: opening
  pending:
    - { id: consent, expects: "हाँ/नहीं — details save करने की अनुमति",
        when: [{ field: consent_response, operator: in, value: [null, ""] }] }
    - { id: age, expects: "उम्र, साल में",
        when: [{ field: age, operator: in, value: [null, "", 0] }] }
- id: job_match
  pending:
    - id: select_job
      expects: "offered jobs में से एक, या दूसरी search"
      options_from: { tool: fetch_jobs, fields: [role, company, locality], id_field: item_id }
      resolves_to: selected_job_item_id
```

Conditions use the existing routing-condition shape (`field`, `operator` ∈ `eq | not_eq | in | lt | gt`, `value`); "unset" is written `operator: in, value: [null, ""]`. Candidates are evaluated in order, against the same merged state routing uses (§6.5 precedence), with the existing `_evaluate_condition`. The first match is the pending question. An entry with no `when` always matches. Resolution runs **before** NLU, on the subagent the caller is currently in, and **after** Spec B's bootstrap. On turn 1 a returning caller's `has_age`, `user_terms` and `user_privacy` are therefore already set, and consent and age are not pending for them. `valid_intents` is not used in this mode.

### 7.3 Startup validation (`dialogue_act` mode)

Startup fails if:
- an `act_intents` intent is not used by any routing rule, global routing rule or the off-track route;
- an `accept_when_pending`, `examples[].pending` or `termination_gate` pending id is not declared by any subagent;
- an `options_from.tool` has no `cache` policy, so Spec A never stores its results;
- `resolves_to` or a slot's mapped state key collides with a key that a connector `session_mapping` writes, or with a `memory_tool` (`remember`) field;
(The system prompt cannot vary by subagent: it is rendered once from config with no per-turn or per-subagent input. A unit test pins this, rather than a startup check.)

### 7.4 Runtime ↔ dev-kit sync

The same PR updates, per `.claude/rules/runtime-devkit-sync.md`:
- `agent_core/src/schema/config.py`
- the dev-kit mirror `dev-kit/dev_kit/schemas/domain/agent_core.py`
- the flat `dev-kit/dev_kit/schema.py`
- `FIELD_RULES` in `dev-kit/dev_kit/agent/field_rules/agent_core.py`
- the `dev-kit/dpg/agent_core.yaml` defaults

### 7.5 New session state

| Key | Type | Written by |
|---|---|---|
| `recent_turns` | list, capped at `history_turns × 2` | end of turn (§6.9) |
| `slot_provenance` | list of state keys | §6.5 |
| `off_track_count` | int | §6.6 |
| `nlu_extras` | map | §6.5 |

All are session scope in Memory Layer Redis, with the session's TTL. There is no new store.

## 8. Code structure

Every NLU call site (the sync path, the stream path with and without language normalisation, and the consent replay) calls one helper, `understand_turn()`. Both `process_turn` and `stream_turn` get identical inputs and outputs. The helper returns:

```python
class TurnUnderstanding(BaseModel):
    nlu_result: NLUResult                 # what routing consumes (intent + entities), both modes
    dialogue: DialogueActResult | None    # None in intent mode
    pending_id: str | None
    resolved: ResolvedReference | None
    rejected_slots: list[SlotRejection]
    fallback_reason: str | None
```

In `intent` mode, `understand_turn()` calls `NLUProcessor.process()` exactly as today, and the result's `dialogue` is `None`. Behaviour is unchanged.

**Turn order.** Today `TurnToolCache` is built at prompt assembly, after NLU (`orchestrator.py`, both paths). In `dialogue_act` mode it is built once, right after Spec B's bootstrap and before `understand_turn()`, and the same instance is reused for prompt assembly and the tool loop. The resulting per-turn order is: memory read → session bootstrap → `TurnToolCache` → pending resolution → NLU → post-processing → routing → prompt (with `<known_facts>` and `<caller_turn>`) → LLM. In `intent` mode the cache keeps its current construction point.

**Spec A extension.** `TurnToolCache.latest_entry(tool) -> dict | None` returns the fresh entry with the highest `fetched_at` for a tool, using the same normalised entry shape as `after_call`. `FrameBuilder` and the resolver use it. An entry stored earlier in the same turn (e.g. by the bootstrap) is included.

New units under `agent_core/src/preprocessing/understanding/`, each behind an ABC per `.claude/rules/base-class-pattern.md`:

| File | Unit | Depends on |
|---|---|---|
| `pending.py` | `PendingResolver`: subagent + state → pending question | workflow config, `_evaluate_condition` (moved to a shared module) |
| `frame.py` | `FrameBuilder`: renders the §5.2 user message | pending, `TurnToolCache.latest_entry`, `_profile_context`, `recent_turns` |
| `dialogue_act_nlu.py` | `DialogueActNLU`: static prompt, schema, the call | dedicated chat provider |
| `postprocess.py` | normalise, accept, resolve, derive, off-track, gate (§6.1–6.4, 6.6–6.7) | config, stored results |
| `slot_writer.py` | `SlotWriter` (§6.5) | Memory Layer client |
| `caller_turn.py` | `<caller_turn>` render (§6.8) | `TurnUnderstanding` |

`nlu_processor.py` is untouched, apart from correcting its docstrings: session history is not injected.

## 9. Failure handling

### 9.1 Timeout and retry

In `dialogue_act` mode NLU always gets its own chat-provider instance, even when the model matches the main agent's. It has:

- `timeout_ms: 2500`;
- `retry_attempts: 2` (total attempts, as for every provider): the second attempt runs only after a rate-limit (429) error;
- **no retry after a timeout**; a timeout goes straight to the fallback.

This needs two provider options, each defaulting to today's behaviour:
- `sdk_max_retries`: passed to the SDK client constructor. NLU sets 0.
- `retry_on_timeout`: default `true`. NLU sets `false`.

The worst-case NLU wait is ~2.5 s, versus the ~20 s possible today.

### 9.2 Fallback result

On timeout, provider error or schema violation, the result is:
- `acts: [other]`, `relation: unclear`, every slot `null`, no reference;
- derived intent `any_input`, so routing falls through to the `*` rules on existing state;
- the pending question stays open, and `off_track_count` is unchanged;
- no gated intent: termination cannot fire from a fallback;
- `<caller_turn>` shows `understanding unavailable this turn`.

### 9.3 Partial failures

| Failure | Handling |
|---|---|
| Option out of range, or no stored result for `options_from` | Unresolved, and no `select`-based intent is derived. `<caller_turn>` shows `caller referred to option N; M offered`. |
| Slot fails normalisation | Dropped. `<caller_turn>` shows `not accepted: <slot> "<value>" (<reason>)`. |
| Slot not accepted in this pending state | Dropped silently, and counted in metrics. |
| Spec A store unavailable | `offered` is omitted and the resolver is skipped. |
| `recent_turns` write fails | Logged. The next frame has less history. |

## 10. Observability and privacy

### 10.1 Per-turn log and metrics

One `nlu.understanding` log per turn with:
- acts, relation, topic, pending id, derived intent;
- whether the reference resolved;
- slot keys written, and slots rejected with their reason;
- fallback reason, latency, and input/cached/output tokens.

Values are logged only as HMAC hashes, as in Spec A §9. Prometheus counters cover:
- fallback rate by reason;
- act distribution by pending id;
- rate of `relation ≠ answers_pending`;
- termination-gate blocks;
- off-track routes;
- resolver misses;
- slot rejections by reason.

### 10.2 Eval capture

`log_raw_response`, which is off by default, additionally records the structured frame inputs and the parsed output, in the eval case format (§11.2). It contains caller speech and personal details. It is enabled only for a bounded window, and its output is handled as in §11.3.

### 10.3 Retention

`recent_turns` holds caller utterances. It has the same session-scope TTL and erasure path as today's `current_question`, and it is never written to Memgraph or SQLite.

## 11. Eval harness

### 11.1 Runner

`agent_core/eval/nlu/`, run with:

```
uv run python -m eval.nlu.run --config dev-kit/configs/<domain> --mode intent|dialogue_act --cases <path> --repeat 3
```

It makes real provider calls, so it is excluded from the default `pytest` run.

### 11.2 Case format (JSONL)

```json
{"id": "ack-apply-01", "tags": ["acknowledge", "post_apply"],
 "step": "apply_confirm", "pending": "closing_offer",
 "known": {"trade": "Welder", "location": "Bengaluru"}, "offered": [],
 "recent": [{"bot": "…आवेदन भेज दिया है…"}],
 "caller_now": ["ठीक है धन्यवाद"],
 "expect": {"acts": ["acknowledge"], "relation": "answers_pending",
            "intent": "any_input", "terminate": false}}
```

Cases are structured inputs, so the same case runs in both modes. For `intent` mode the runner converts it to today's inputs: step id, the last bot reply as `current_question`, the known keys as profile keys, and the utterance joined. Both modes are scored on what they share: **derived intent, slot values after normalisation, the resolved option, and whether termination fired**. `dialogue_act` mode is also scored on acts, relation and topic.

### 11.3 Sources and storage

- Cases from real calls are captured through §10.2, labelled by a person (drafted labels are allowed), and **stored privately, outside the public repository**.
- Synthetic and scrubbed cases are committed under `agent_core/eval/nlu/cases/`. They cover the known traps: "धन्यवाद" / "ठीक है" under each pending question, corrections, off-topic and garbled turns, ordinals and company references, Hindi numerals, and consent-shaped answers to other questions.
- Initial target: ~150 cases, with at least 10 per tag.

### 11.4 Report

Per mode, per tag and per field:
- accuracy, and a derived-intent confusion matrix;
- **termination false positives**, reported separately;
- p50/p95 latency, tokens and fallback rate;
- variance across repeats.

Precision per (act, pending) is reported for Spec E.

### 11.5 Gate for switching a domain to `dialogue_act`

1. Derived-intent accuracy and slot accuracy ≥ `intent` mode on the same cases.
2. No regression on the `consent`, `age` or `termination` tags.
3. Zero termination false positives on `acknowledge` cases.
4. p50 NLU latency ≤ `intent`-mode p50 + 50 ms.

## 12. Testing

All with a mocked provider, meeting the coverage rule in `.claude/rules/testing-requirements.md`.

- **Unit tests for each stage:**
  - pending resolution: order, `when`, the no-match → `none` case;
  - frame render snapshots: with and without `offered`, interrupted segments, tail truncation;
  - output-schema generation from config;
  - normalisation and accept-when-pending;
  - resolver: in range, out of range, expired store;
  - `act_intents`: order, the relation guard, multi-act rows;
  - off-track counter: increment, reset, threshold, unchanged on fallback;
  - termination gate: blocked, passed, fallback;
  - `SlotWriter`: allowlist, `null` skip, overwrite, session-wins precedence, `extras` namespace;
  - `<caller_turn>` render snapshots.
- **Failure tests:** timeout → fallback with no retry; a fast 429 → one retry; schema violation → fallback.
- **Config tests:** each startup-validation failure in §7.3, and the ignored-keys warning.
- **Parity:** `understand_turn()` gives identical results on the sync and stream paths.
- **Integration:** `stream_turn` with the Blue Dots config and scripted NLU responses across the full journey. It includes an interrupted turn, a correction, an off-script question and an off-track recovery, and asserts routing and state after each turn.
- **Regression:** the existing `test_nlu_processor.py` suite passes **unchanged**, which shows `intent` mode is untouched.
- **Dev-kit:** accept-valid and reject-invalid tests for the new config, per the sync rule.

## 13. Known interactions and limitations

- **#411 interrupted turns:** an interrupted turn commits routing and entry counts before it aborts. Its successor can therefore resolve `pending` from a subagent the caller never heard, and entry-count backstops advance twice. This is reported to #411's owner. This spec does not fix it, and §6.9's `current_question` fix does not depend on it.
- **Background speech:** NLU cannot tell a background speaker from the caller. Such turns appear as `unrelated` or `unclear`, and the off-track counter bounds them.
- **Option order:** resolution is correct only if the bot reads options in stored order. That is a Spec D dependency (§6.3).
- **Stored options must be configured:** `offered` and the resolver work only for tools with a Spec A `cache` policy. Blue Dots caches only `fetch_profile` today, so the rollout adds one for `fetch_jobs` (§14). Without it, `select` falls back to today's behaviour.
- **Spec A R17:** a cache hit does not replay `session_mapping`. This spec reads entries directly and never depends on that replay.
- **One-model assumption:** the latency goal assumes the NLU model stays `gpt-4.1-mini`. A model change needs a fresh harness run.

## 14. Blue Dots rollout (config only, separate commit)

1. The code merges with Blue Dots still on `intent` mode.
2. Add a Spec A `cache` policy for `fetch_jobs` (session scope; `keep` covering `item_id`, role, company and locality; a TTL bounded by the call length). Searches with different arguments are stored as separate entries, and the latest one is the offered list.
3. Author the Blue Dots `dialogue_act` config:
   - slots, known fields (including `stored_trade` and `stored_location`) and examples;
   - `act_intents` for the current routing intents;
   - `pending` for `opening`, `profile_resolve`, `job_match`, `profile_setup` and `apply_confirm`;
   - an `off_track` rule placed first in each non-terminal subagent, routing back to the same subagent, so recovery happens in place through `<caller_turn>`;
   - an `age` slot bounded 5–99, not 14–80, so the under-18 route still sees under-age answers.
4. Run the harness on both modes and check the §11.5 gate.
5. Switch `mode: dialogue_act` in its own commit, then compare VM calls with the 28 Sep and 30 Sep baselines.

## 15. Relationship to other specs

- **Spec A (Tool-Result Persistence, merged):** supplies the stored entries that `offered` and the resolver read. This spec adds `TurnToolCache.latest_entry` and builds the cache before NLU in `dialogue_act` mode. `<caller_turn>` sits next to `<known_facts>`. Grounding of the resolved ID is unchanged Spec A behaviour. `remember` is not used.
- **Spec B (Session Bootstrap, merged):** runs before pending resolution and NLU, so turn 1's frame already reflects the caller's profile flags and stored values. Its `session_mapping` keys are covered by the §7.3 collision check.
- **Spec D (Main-LLM context and prompts):** consumes `<caller_turn>`. Owns option order, conversation history for the main LLM, the dead `tts_rules`, and prompts that reference state the model cannot see.
- **Spec E (Deterministic turn actions):** uses `TurnUnderstanding` to skip the main LLM, or to pre-dispatch a tool, for specific (act, pending) pairs. Each pair is enabled once its precision in §11.4 clears Spec E's bar. Built on Spec B's step runner. Spec B v1 supports literal `args` only, so Spec E has to add argument binding from slots and session values, e.g. `fetch_jobs(query_text=<trade> <location>)`.

## 16. Single mode: removing `intent` mode (amendment, 2026-10-01)

**Decision.** None of the three domains (`blue-dots`, `blue-dots-economy`, `kkb`) is live, and Blue Dots is the pilot. This is a DPG refactor for accuracy, not a migration that must keep the old behaviour. So the dialogue-act contract becomes **the only** NLU contract, and `intent` mode is deleted with no compatibility path. This supersedes the opt-in framing of §2, §5, §7.1 and §14. Where those sections say "in `dialogue_act` mode", read "always".

**Removed:**
- **Config:** the `preprocessing.nlu_processor.mode` key and the old keys `intents`, `entities`, `domain_instruction`, `confidence_threshold`, `sentiment_classes`.
- **Workflow:** subagent `valid_intents`, `agent_workflow.global_intents`, and the workflow-loader rules built on them (NLU intent load, the valid-intent and global-intent checks, `nlu_intent_set`).
- **Runtime:** `NLUProcessor` (`preprocessing/nlu_processor.py`) and its tests. The orchestrator's intent-mode branches on both paths: the legacy entity-write loop, the `signal_intents` → Signal block, and the "build `TurnToolCache` late" path. The cache is always built after bootstrap.
- **Dead code:** the `active_risks` → `assemble_constraints` branch, which NLU never populated. The turn-assembler semantic gate, which depended on `NLUProcessor` and was never wired.
- **Domains:** `dev-kit/configs/kkb/` and `dev-kit/configs/blue-dots-economy/`, the kkb design docs, and every test fixture or default that points at them. Defaults and docs that named `kkb` as the reference domain now name `blue-dots`.
- **Eval harness:** the intent-mode adapter and the `--mode` flag. The baseline comparison against the old method is run separately, outside this codebase. `gate()` and `--compare` stay, so any two report files can be compared.

**Kept, now unconditional:**
- `TurnUnderstander`, the frame and the strict-schema NLU call;
- post-processing, `SlotWriter` and precedence;
- `<caller_turn>`, `recent_turns` and `served_tool_results`;
- the dedicated NLU provider (§9.1).
- `NLUResult` stays as the routing contract (`intent` + `entities`).
- The termination short-circuit and `signal_intents` as the signal-name → type map. `signals` lists the names NLU may emit, and `signal_intents` keeps mapping each to a Signal type.

**Migrated:**
- **User-state model (`conversation.user_state_model`).** It is a per-turn mental-state classification, not a stored fact, so it is not replaced by `<known_facts>` / `<known_profile>`. When it is enabled:
  - the static system prompt gains the state definitions (ids, signals, first guidance line);
  - the frame gains `previous_state: <id>`;
  - the strict output schema gains `user_state: {id: enum(state ids), confidence: number}`.

  `TurnUnderstanding.nlu_result.user_state` is filled from it. The existing `resolve_user_state`, persistence and guidance injection are unchanged, including the sticky fallback below `user_state_confidence_threshold`. On a fallback NLU result, `user_state` is None, which already keeps the previous state.
- **Language switch.** It becomes a derived intent:
  - an `act_intents` row `{acts: [request_change], topic: language, intent: language_switch_request}`;
  - plus a `language_preference` slot (enum of `language_normalisation.supported_languages`).

  The orchestrator's existing language-switch handling runs on the derived intent before routing, as before, on both paths. `language_switch_request` is framework-handled, so the §7.3 rule "an `act_intents` intent must be used by a routing rule" exempts it. Blue Dots declares the topic, slot and row.

**Dev-kit.**
- All intent/entity field rules, mirror fields, phase-prompt instructions, tools, renderer hooks and cross-block checks are removed.
- The schema mirrors keep validating the dialogue-act blocks.
- The wizard does **not** yet author `slots` / `act_intents` / `termination_gate` / `pending`. That is a follow-up, so until then a wizard-generated project has no working NLU section and must be completed by hand.

**Startup.** A config that still carries any removed key (`mode`, `intents`, `entities`, `domain_instruction`, `confidence_threshold`, `sentiment_classes`, `valid_intents`, `global_intents`) fails schema validation, as `extra="forbid"` already does.
