# KKB config remap + layered system prompt — design

Date: 2026-04-22
Authors: Aniket, Claude
Related: PR #148, GH issue #175 (monolithic-no-NLU RFC)
Scope: `agent_core` — `dev-kit/configs/kkb/*.yaml`, `agent_core/src/manager_agent.py`, supporting tests

---

## 1. Context

PR #148 landed KKB config updates for issue #137 (part 1). Review of the merged shape showed that `agent_workflow.agent_system_prompt` in `dev-kit/configs/kkb/agent_core.yaml` had accumulated content that was already-correctly-homed in other config slots:

- Voice / TTS rules — duplicated into `agent_system_prompt` while `channels.voice.system_prompt_suffix` and `channels.voice.tts_rules` are populated.
- Prohibited language — duplicated while `trust_layer.output_rules.blocked_phrases` is populated.
- Dignity Safety Check (6 questions) — duplicated while `trust_layer.dignity_check.questions` is populated.
- 5 mental states (fog / orientation / evaluation / commitment / follow-through) — state-specific behaviour duplicated while `conversation.user_state_model.states[].guidance` already holds it.
- Market-truth rules, feedback-loop rules, short-list format, deep-dive format — duplicated while the `market_truth` subagent already owns them.
- `get_profile` / `get_jobs` / `onest_market_lookup` invocation contract — duplicated while `connectors.read[*].invocation_rules` already owns it.

`agent_core/src/manager_agent.py::build_system_prompt` already assembles eight distinct layers (persona → channel → profile → subagent → user_state → session-end → guardrails → suffix) via flat concatenation. The eight-layer design exists in code. The PR collapsed layers 2–8 of its content into layer 1, defeating the design.

GH #175 proposes measuring whether a single monolithic prompt beats the current three-LLM-call architecture. That benchmark's baseline must be a clean PR 148 or results will be biased. This spec is the prerequisite.

## 2. Goals

1. Redistribute the 33-page KKB prompt (`docs/KKB Current Prompt.pdf`) to the correct layers across the seven KKB configs.
2. Restructure `manager_agent.build_system_prompt` to return a list of Anthropic content blocks with cache-control breakpoints aligned to cache-volatility tiers.
3. Zero functional regression on existing KKB test suites.
4. Measurable cache hit-rate improvement in `cache_read_input_tokens` metrics after merge.

## 3. Non-goals

- Collapsing the subagent graph or dropping NLU / Language Normalisation. Tracked in GH #175.
- A dev-kit classifier shim that routes free-form expert input to the correct block/section. Separate follow-up issue.
- A multi-provider structured-prompt abstraction (Option 3 from the brainstorming). Framework is Anthropic-only today; XML delimiters are accepted for this PR.
- Cross-provider caching strategy. `cache_control: ephemeral` is Anthropic-specific; porting to OpenAI / Gemini is out of scope.

## 4. Design — Part A: layered prompt structure

### 4.1. Tier layout

Three cache-volatility tiers. Tag names are XML; Claude handles them natively.

```
TIER 1 — session-stable (cache breakpoint after this tier)
  <persona>             agent_system_prompt (persona + cross-cutting safety)
  <channel_rules>       channel_config.system_prompt_suffix (voice TTS, web HTML, etc.)
  <session_end_policy>  session_end_eval_prompt (if enabled)

TIER 2 — state-stable (cache breakpoint after this tier)
  <subagent>            subagent_system_prompt
  <user_state_guidance> user_state_guidance (from conversation.user_state_model)

TIER 3 — dynamic (no cache marker)
  <channel_context>     channel + detected_language line
  <resumption>          resumption flag (first-turn-after-adoption only)
  <known_profile>       profile grounding (keys collected so far)
  <active_guardrails>   guardrail_constraints (present only when active_risks non-empty)
```

### 4.2. Expected cache behaviour

- Tier 1: hits cache every turn after turn 1 of a session, for any channel. Invalidates only on `language_switch_request` (moves language line into dynamic tier, keeps channel suffix stable).
- Tier 2: hits cache across consecutive turns in the same subagent + same user_state. Misses on state transitions. For a typical 10-turn KKB session with ~2 state transitions, ~8 tier-2 hits.
- Tier 3: never cached. This is the correct answer — profile grows, guardrails flip, resumption is one-shot.

### 4.3. Return type change

`build_system_prompt` currently returns `str`. Proposal: return `list[dict]` matching the Anthropic content-block format.

```python
def build_system_prompt(self, ...) -> list[dict]:
    tier1 = _join_nonempty([
        _xml("persona", agent_system_prompt),
        _xml("channel_rules", suffix),
        _xml("session_end_policy", session_end_eval_prompt),
    ])
    tier2 = _join_nonempty([
        _xml("subagent", subagent_system_prompt),
        _xml("user_state_guidance", user_state_guidance),
    ])
    tier3 = _join_nonempty([
        _xml("channel_context", channel_ctx_line),
        _xml("resumption", resumption_note) if is_resumption else "",
        _xml("known_profile", profile_grounding),
        _xml("active_guardrails", constraints_text) if guardrail_constraints else "",
    ])

    blocks: list[dict] = []
    if tier1:
        blocks.append({"type": "text", "text": tier1,
                       "cache_control": {"type": "ephemeral"}})
    if tier2:
        blocks.append({"type": "text", "text": tier2,
                       "cache_control": {"type": "ephemeral"}})
    if tier3:
        blocks.append({"type": "text", "text": tier3})
    return blocks
```

`_xml(tag, body)` wraps `body` as `<tag>\n{body.strip()}\n</tag>` and returns `""` if `body` is empty/None so downstream joins skip it.

### 4.4. Wrapper contract

`ClaudeLLMWrapper._wrap_system_for_caching` (`claude_wrapper.py:186–213`) already honours list-of-blocks input:

```python
if not isinstance(system, str):
    return system  # already structured; trust the caller
```

No wrapper change required. The breakpoint count stays within Anthropic's 4-breakpoint limit (we use at most 2).

### 4.5. Caller audit

Callers of `build_system_prompt` outside tests:

- `agent_core/src/orchestrator.py:815` — passes result directly to `llm.call(..., system=system)`. `llm.call` forwards to `_call_with_retry` which forwards to Anthropic SDK. SDK accepts both `str` and `list[dict]` for the `system` parameter. No change needed at this call site — the variable `system` simply holds `list[dict]` now.
- `agent_core/src/orchestrator.py` streaming path (`_stream_with_retry`) — same forwarding shape. Verify in implementation.

Tests that string-match the returned prompt need to join the block texts before matching. Bounded change. Expected affected files:

- `agent_core/tests/test_manager_agent.py`
- Any orchestrator integration test that inspects the assembled prompt.

### 4.6. Open verification item

`channels.voice.system_prompt_suffix` currently lands as the *last* section of the concatenated prompt. Some prompt-engineering patterns rely on "last instruction wins." Review of the KKB `system_prompt_suffix` content (line 12–26 of `agent_core.yaml`) shows it is TTS / script-formatting rules — no claim to last-word authority. Moving it to tier 1 is safe. If the review during implementation finds any prompt that does depend on last-position authority, escalate in the PR.

## 5. Design — Part B: KKB config remap

Source: `docs/KKB Current Prompt.pdf` (33 pages, ~5000 words).
Target: the 7 KKB configs in `dev-kit/configs/kkb/`.

### 5.1. Methodology

For each H1 / H2 section of the PDF:

1. Locate current yaml home(s).
2. Classify the delta:
   - **DROP** — content is already correctly homed; remove duplicate from `agent_system_prompt`.
   - **OVERWRITE** — content in yaml is stale or contradicts PDF; replace with PDF version.
   - **APPEND** — correct slot exists but content is missing; add from PDF.
   - **MOVE** — content is in `agent_system_prompt` but belongs in a more specific slot; migrate and delete source.
   - **NEW** — no home exists; escalate during implementation (rare).
3. Produce a yaml patch per target file.

### 5.2. Diff matrix — per PDF section

| # | PDF section | Current home in yaml | Target home | Action |
|---|---|---|---|---|
| 1 | Introduction — persona, voice, what you are / are not | `agent_workflow.agent_system_prompt` (full) | `agent_workflow.agent_system_prompt` (condensed — Tier 1 persona only, ~15 lines) | OVERWRITE (shrink) |
| 2 | Core Role — what agent may do / must not push | `agent_system_prompt` (partial) | `agent_system_prompt` (Tier 1) | KEEP + tighten |
| 3 | User Universe — 7 personas | `agent_system_prompt` ## USER UNIVERSE | `agent_system_prompt` (Tier 1, one line: "personas are defined; infer gradually, do not label aloud") — full list moves to subagent context or drops | MOVE (shrink) |
| 4 | Conversation Principle — voice call, not chatbot | `agent_system_prompt` ## CONVERSATION PRINCIPLE | `agent_system_prompt` (Tier 1) + `channels.voice.system_prompt_suffix` (reiterate for voice) | KEEP + ensure voice suffix echoes |
| 5 | Critical Tool Routing Override — `get_jobs` MUST | `agent_system_prompt` ## CRITICAL TOOL ROUTING OVERRIDE + `connectors.read[onest_market_lookup].invocation_rules` (partial) | `connectors.read[onest_market_lookup].invocation_rules` (canonical) + `agent_system_prompt` Tier 1 (one-line summary: "tool invocation contracts are authoritative — follow each `invocation_rules` block") | MOVE (delete prose from agent_system_prompt; expand `must_not_substitute` in connector) |
| 6 | Call Introduction Rules — 6 opening scripts | `agent_system_prompt` ## CALL INTRODUCTION RULES | `agent_workflow.subagents[id=profile_building].system_prompt` (already exists; ensure all 6 branches are present) + each branch's `opening_phrase` if applicable | MOVE + verify subagent prose covers all branches |
| 7 | Introduction Priority Rule (override) | `agent_system_prompt` + `profile_building.system_prompt` | `profile_building.system_prompt` (canonical) | MOVE |
| 8 | New Contact Handling — ask-to-fetch-profile | `agent_system_prompt` ## NEW CONTACT HANDLING + `profile_building.system_prompt` | `profile_building.system_prompt` | MOVE |
| 9 | Language and Script Rules (Devanagari-only output) | `agent_system_prompt` (absent) + `channels.voice.tts_rules.output_script` (present) | `channels.voice.tts_rules.output_script` (canonical) | DROP from agent_system_prompt |
| 10 | English-origin words → Devanagari transliteration examples | `channels.voice.tts_rules.english_loanwords` (present) | same | DROP from agent_system_prompt if present; no yaml change |
| 11 | Named entities → Devanagari | not explicitly in yaml | APPEND to `channels.voice.tts_rules.english_loanwords` or new subkey `channels.voice.tts_rules.named_entities` | APPEND |
| 12 | TTS Normalization Rules (numbers, money, dates, time, phone, email, abbreviations) | `channels.voice.tts_rules.{numbers,money,dates,time,phone,abbreviations}` | same | DROP from agent_system_prompt if present; APPEND `email` subkey if missing |
| 13 | Style Rules (speak like / never sound like) | `agent_system_prompt` (partial, mixed with persona) | `agent_system_prompt` Tier 1 (persona-adjacent: one short paragraph). No duplication into voice suffix. | KEEP (consolidate) |
| 14 | Prohibited Language (Strict) — 13 Hindi phrases | `agent_system_prompt` ## PROHIBITED LANGUAGE + `trust_layer.output_rules.blocked_phrases` | `trust_layer.output_rules.blocked_phrases` (canonical) | MOVE (delete from agent_system_prompt) |
| 15 | What You Must Always Preserve — truth / clarity / agency / dignity / trade-off | `agent_system_prompt` (absent explicit section) | `agent_system_prompt` Tier 1 (5 short bullets) | APPEND |
| 16 | Conversation State Model — 5 mental states + System State vs User State | `agent_system_prompt` ## DECISION SUPPORT... + `conversation.user_state_model.states[]` (present) | `conversation.user_state_model.states[].guidance` (canonical) + manager_agent already injects via `user_state_guidance_text` at orchestrator.py:578 | MOVE (delete from agent_system_prompt; enrich state.guidance if thinner than PDF) |
| 17 | State 1 — Fog behaviour | `user_state_model.states[id=fog].guidance` | same | APPEND if thinner |
| 18 | State 2 — Orientation behaviour + Minimum Viable Entity Set for Job Fetch | `user_state_model.states[id=orientation].guidance` + `connectors.read[onest_market_lookup].invocation_rules.required_before_calling` | same | APPEND to user_state guidance; verify `required_before_calling` lists `location` + `trade_or_stream` |
| 19 | Emergency mode | `agent_system_prompt` + `profile_building.system_prompt` (hint) | `profile_building.system_prompt` | MOVE |
| 20 | State 3 — Evaluation | `user_state_model.states[id=evaluation].guidance` + `evaluation` subagent | both | APPEND if thinner |
| 21 | State 4 — Commitment | `user_state_model.states[id=commitment].guidance` + `commitment` subagent | both | APPEND if thinner |
| 22 | State 5 — Follow-through | `user_state_model.states[id=follow_through].guidance` | same | APPEND if thinner |
| 23 | Core Flow Rule — 5-step flow | `agent_system_prompt` ## CORE FLOW RULE | `agent_system_prompt` Tier 1 (~5 lines) | KEEP (consolidate) |
| 24 | Data Gathering Rule | `agent_system_prompt` ## DATA GATHERING RULE + `profile_building.system_prompt` | `profile_building.system_prompt` | MOVE |
| 25 | Market Truth Rule | `agent_system_prompt` ## MARKET TRUTH RULE + `market_truth` subagent | `market_truth` subagent | MOVE |
| 26 | Progressive Disclosure Rule | `agent_system_prompt` ## PROGRESSIVE DISCLOSURE RULE | Distributed: `market_truth`, `evaluation`, `commitment` subagents | MOVE (split per state) |
| 27 | Trade-off Rule | `agent_system_prompt` ## TRADE-OFF RULE | `evaluation` subagent (primary) + `market_truth` subagent (when presenting options) | MOVE |
| 28 | Decision Support by Persona Shape | `agent_system_prompt` ## DECISION SUPPORT BY PERSONA | `agent_system_prompt` Tier 1 (one-line summary + pointer) + enriched `evaluation` subagent | KEEP summary in Tier 1; MOVE details to evaluation subagent |
| 29 | Intent Handling — if user says X, do Y | `agent_system_prompt` ## SPECIAL JOURNEY PATTERNS (partial) + `preprocessing.nlu_processor.intents[]` | `preprocessing.nlu_processor.intents[]` (canonical for signals); branch behaviour in the relevant subagent | MOVE |
| 30 | Silence Handling | `agent_system_prompt` ## SILENCE HANDLING | `channels.voice.system_prompt_suffix` (voice-specific; silence is a voice artefact) | MOVE |
| 31 | Emotional Handling (Allowed / Not allowed) | `agent_system_prompt` ## EMOTIONAL HANDLING | `trust_layer.output_rules.blocked_phrases` (for "not allowed" hard-blocks — "डोंट वरी", "सब ठीक हो जाएगा", etc.) + `agent_system_prompt` Tier 1 (one-line policy: "acknowledge without coaching, pitying, pushing") | MOVE |
| 32 | Drop and Re-entry Handling | `agent_system_prompt` ## DROP AND RE-ENTRY | `profile_building` / `evaluation` / respective subagents depending on drop point | MOVE (split per subagent) |
| 33 | Special Journey Patterns — proxy caller | `agent_system_prompt` ## SPECIAL JOURNEY PATTERNS | `profile_building.system_prompt` (detection happens at entry) | MOVE |
| 34 | Special Journey Patterns — immediate-work / emergency | `agent_system_prompt` + `profile_building.system_prompt` | `profile_building.system_prompt` | MOVE |
| 35 | Special Journey Patterns — repeated indecision | `agent_system_prompt` | `evaluation` subagent | MOVE |
| 36 | Special Journey Patterns — do-not-call request | `agent_system_prompt` | `trust_layer` (as a hard-routed intent) + NLU `intents[]` include `do_not_call` + terminal subagent handles the confirmation script | MOVE |
| 37 | Special Journey Patterns — complaint / mismatch | `agent_system_prompt` | follow-through-state subagent | MOVE |
| 38 | Action and Consent Rule (Mandatory) | `agent_system_prompt` ## ACTION AND CONSENT RULE + `trust_layer.consent` | `commitment` subagent (for action phrasing) + `trust_layer.consent` (for consent-phrase detection) + one-line policy in `agent_system_prompt` Tier 1 | MOVE |
| 39 | Follow-through Rule | `agent_system_prompt` ## FOLLOW-THROUGH RULE | follow-through-state subagent | MOVE |
| 40 | Error and Uncertainty Handling (weak data, scarce market, unrealistic expectations) | `agent_system_prompt` ## ERROR AND UNCERTAINTY HANDLING + `connectors.read[onest_market_lookup].invocation_rules.on_empty/on_failure` | connector `on_empty` / `on_failure` (tool-level errors) + `market_truth` subagent (uncertainty phrasing) | MOVE |
| 41 | Toll call general instructions — never waiting messages | `agent_system_prompt` (absent) | `channels.voice.system_prompt_suffix` | APPEND |
| 42 | `get_profile` Tool Call Rules | `agent_system_prompt` ## NEW CONTACT HANDLING (partial) + `connectors.read[get_profile]` (needs creation — currently only `onest_market_lookup` + `knowledge_retrieval` are declared) | `connectors.read[get_profile].invocation_rules` + `profile_building` subagent | NEW connector entry (verify against ONEST profile service) + MOVE prose |
| 43 | `get_jobs` (onest_market_lookup) payload construction — query_text rules | `agent_system_prompt` + `connectors.read[onest_market_lookup].input_schema.query_text.description` (partial) | `connectors.read[onest_market_lookup].input_schema.query_text.description` canonical | MOVE (enrich schema description with the PDF's natural-language examples) |
| 44 | `get_jobs` field specs — industry, age, languages, preferred_work_mode, monthly_in_hand, work_hours_per_day | `connectors.read[onest_market_lookup].input_schema` (present) | same | VERIFY completeness; APPEND any missing constraint |
| 45 | `get_jobs` fetch prerequisites / mandatory fetch before job discussion | `agent_system_prompt` ## CRITICAL TOOL ROUTING OVERRIDE + `connectors.read[onest_market_lookup].invocation_rules` | connector `invocation_rules.call_when` + `must_not_substitute` | MOVE (strengthen connector text) |
| 46 | `get_jobs` exception — when NOT to fetch | `agent_system_prompt` | connector `invocation_rules.call_when` negative branch | MOVE |
| 47 | Conversational bridge before fetch | `agent_system_prompt` + `connectors.read[onest_market_lookup].invocation_rules.bridge_line` | connector `bridge_line` | MOVE |
| 48 | Filters (income_minimum, commute, etc.) | `agent_system_prompt` | `market_truth` subagent + connector `input_schema` | MOVE |
| 49 | Broad exploration mode | `agent_system_prompt` | `profile_building` or `market_truth` subagent | MOVE |
| 50 | Ranking order (match_score, distance, freshness, positions) | `agent_system_prompt` | `market_truth` subagent | MOVE |
| 51 | Presentation limit (top 3) + Deep dive | `agent_system_prompt` ## JOB PRESENTATION FORMAT + `market_truth.system_prompt` (partial) | `market_truth` subagent | MOVE |
| 52 | Application rule — explicit consent | `agent_system_prompt` + `commitment` subagent | `commitment` subagent | MOVE |
| 53 | Safety / data quality — never present inactive / closed jobs; never speak GPS / hiring manager phone / internal IDs / match_score | `agent_system_prompt` + `market_truth.system_prompt` (partial) | `market_truth` subagent | MOVE |
| 54 | Job Presentation Format — short-list spoken format | `market_truth.system_prompt` | same | APPEND if thinner |
| 55 | Deep-dive spoken format | `agent_system_prompt` + `evaluation` / `commitment` subagent | `evaluation` subagent | MOVE |
| 56 | API → speech field rules (e.g. `descriptor.name` → "हेल्पर का काम") | `agent_system_prompt` (absent structured) | `market_truth` subagent | APPEND |
| 57 | Salary normalisation (Critical) — if min/max inverted | `agent_system_prompt` + `market_truth` (partial) | `market_truth` subagent | MOVE |
| 58 | Availability rules — is_active / status / positions | `agent_system_prompt` + `market_truth` (partial) | `market_truth` subagent | MOVE |
| 59 | Feedback loop after presentation + Max loop rule (3) | `agent_system_prompt` + `market_truth` (partial) | `market_truth` subagent | MOVE |
| 60 | Graceful Exit — final word "Goodbye" | `agent_system_prompt` + `channels.voice.terminal_word` (present) | `channels.voice.terminal_word` (canonical) + terminal subagent for the closing line | MOVE |
| 61 | Dignity Safety Check (6 Qs) | `agent_system_prompt` ## DIGNITY SAFETY CHECK + `trust_layer.dignity_check.questions` (present, currently 5) | `trust_layer.dignity_check.questions` (canonical) | MOVE (verify list is 6 Qs; currently KKB yaml has 5 — compare against PDF and add the missing "Am I saying more than this state needs?" if absent) |
| 62 | Sample Conversational Patterns (examples 1–3) | `agent_system_prompt` (absent explicit) | no runtime slot — reference material only. Move to a sibling doc `docs/kkb/conversation-examples.md` (not injected into any prompt) | NEW doc (out of runtime) |

### 5.3. Target shape of `agent_workflow.agent_system_prompt` after remap

Approximately 30–40 lines total, structured as:

```
You are काम की बात — a calm, grounded, fact-based female voice guide for Indian workers.
[3-line persona + tone]

## What you are / are not
[3-line not-list]

## Core belief
[1 line]

## What to always preserve
- Truth over persuasion
- Clarity over completeness
- Agency over pressure
- Dignity over conversion
- Trade-off over simplification

## Core flow (every turn)
1. Understand just enough.
2. Show market truth.
3. Let the user react.
4. Help evaluate trade-offs.
5. Move only with consent.

## Tool invocation
Tool-use contracts in each connector's `invocation_rules` are authoritative.
Follow `required_before_calling` and `must_not_substitute` exactly.

## User mental state
The active state guidance is injected at runtime in `<user_state_guidance>`.
Adapt tone, detail level, and pacing to the current state.
```

Everything else — state-specific behaviour, voice rules, prohibited language, dignity questions, tool payload specs — lives in its dedicated slot and flows in through its own prompt layer.

### 5.4. Files touched

| File | Nature of change |
|---|---|
| `dev-kit/configs/kkb/agent_core.yaml` | Rewrite `agent_system_prompt` (~1500 lines → ~40). Enrich some subagent `system_prompt` blocks. Ensure `entity_to_profile_field` and `signal_intents` cover PDF-implied fields. Potentially add `get_profile` connector. |
| `dev-kit/configs/kkb/trust_layer.yaml` | Verify `dignity_check.questions` matches PDF (6 Qs). Verify `output_rules.blocked_phrases` covers PDF prohibited list. Verify consent phrases cover PDF examples. |
| `dev-kit/configs/kkb/knowledge_engine.yaml` | No structural change expected. Verify glossary mappings include PDF trade/location synonyms. |
| `dev-kit/configs/kkb/memory_layer.yaml` | No structural change expected. |
| `dev-kit/configs/kkb/reach_layer.yaml` | No structural change expected. |
| `dev-kit/configs/kkb/observability_layer.yaml` | No change. |
| `dev-kit/configs/kkb/action_gateway.yaml` | No change. |
| `agent_core/src/manager_agent.py` | Refactor `build_system_prompt` to return `list[dict]` with tier XML + cache breakpoints per §4. |
| `agent_core/tests/test_manager_agent.py` | Update string-match assertions to join tier texts. |
| `docs/kkb/conversation-examples.md` | New file (not loaded at runtime). Houses PDF sample conversations #1–#3. |

## 6. Verification

### 6.1. Unit

- `test_manager_agent.py` — new tests that verify:
  - Return type is `list[dict]`.
  - Tier 1 and tier 2 blocks carry `cache_control: {"type": "ephemeral"}`.
  - Tier 3 carries no `cache_control`.
  - Empty optional inputs (no subagent_system_prompt, no user_state_guidance, no guardrail_constraints) produce correct block elision, not empty tags.
  - XML tag structure is well-formed.
- Existing tests updated for the new return type.

### 6.2. Integration

- Run KKB CLI reach channel against the `kkb` config with the remapped yaml. Walk the 3 PDF sample conversations; verify outputs match the PDF shape (opening line, profile fetch, follow-up question).
- Run `agent_core` test suite in full (457+ tests). Zero regressions.

### 6.3. Cache metric verification

`claude_wrapper.py:388–411` already emits `cache_read_input_tokens` and `cache_creation_input_tokens` per LLM call. Acceptance criterion for merge:

- Run a scripted 10-turn KKB CLI session pre-merge and post-merge. Capture `sum(cache_read_input_tokens) / sum(input_tokens)` per session.
- Post-merge ratio must be >2× pre-merge for tier-1 content at a minimum. (Exact threshold: pre-merge ~0 cache reads because the whole 2000-token `agent_system_prompt` changes per state; post-merge the Tier 1 persona block (~500 tokens after remap) caches across every turn after the first.)
- If tier 1 falls under 3000 chars and Anthropic's min caches it only as a list-of-blocks, confirm tier 2 (larger subagent + user_state prose) takes up the cache slack.

### 6.4. Behaviour sanity (not metrics — human-eyed)

Run 5 common KKB flows manually:
1. New caller, no profile, job query.
2. Returning caller with options_presented in memory.
3. Post-application callback.
4. Emergency mode.
5. User distressed → counsellor request (hard-route).

Compare response quality vs the pre-merge baseline. The goal is "same or better dignity / same or better tool invocation precision." Any regression is a blocker.

## 7. Rollout

- Single branch off `main`: `chore/kkb-config-remap-pr148-cleanup` (or merge into the existing PR 148 if still open).
- PR body cross-references this spec, GH #175, and the original issue #137.
- Docker compose stack runs on the VM (per CLAUDE.md); author verifies manually there.
- No data migration. Configs re-home content within keys already validated by existing loader logic. Loader will continue to deep-merge `dev-kit/dpg/*.yaml` + `dev-kit/configs/kkb/*.yaml` identically.

## 8. Risks

- **Behaviour regression from over-trimmed `agent_system_prompt`.** Mitigation: §6.4 sanity pass before merge. If a rule really does need to live in tier 1 (cross-cutting enough), keep it there explicitly.
- **Cache breakpoint semantics.** Anthropic caches based on exact prefix match. Any tier-1 drift across sessions (e.g. a config hot-reload changing persona text) resets the cache. Acceptable — `agent_system_prompt` is set at startup and stable across a process lifetime.
- **Tier 1 falling under 3000 chars.** Then `_wrap_system_for_caching` won't cache it as a string. But when passed as `list[dict]` the wrapper returns the list unchanged and Anthropic enforces its own minimum (~1024 tokens). Likely outcome: Tier 1 alone ~500 tokens caches only if combined with Tier 2; practically this is fine because consecutive turns in the same state *do* share prefix. Measure in §6.3 and revise if needed.
- **Test update burden.** Low risk — the concat contract is well-defined; joins of block texts reproduce the old string.

## 9. Follow-ups (separate issues, not this PR)

1. Dev-kit classifier shim — one-LLM-call router that detects when expert input belongs to a different block/section than the active phase, and redirects. Improves dev-kit's ability to produce correctly-segmented configs from free-form expert description.
2. GH #175 monolithic-no-NLU benchmark — depends on this remap landing.
3. Cache breakpoint strategy refinement — once we have post-merge cache-hit-rate data, decide whether to add a third breakpoint, or whether Tier 2 should split further (e.g. split subagent from user_state).
4. Multi-provider prompt abstraction — if/when a second LLM wrapper lands, revisit the hard-coded XML and `cache_control` via the Option 3 structured-section pattern.

## 10. Out-of-runtime file

`docs/kkb/conversation-examples.md` — houses the PDF's sample conversation patterns. Reference material only; not loaded by agent_core. Useful for QA, review, and dev-kit training data.
