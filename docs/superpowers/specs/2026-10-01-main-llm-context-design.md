# Spec D: Main-LLM context, output contract and Blue Dots prompts

**Status:** Draft for review, 2026-10-01.
**Block:** Agent Core (runtime) and dev-kit (schema mirror, authoring), with Blue Dots domain config.
**Builds on:** Spec A, tool-result persistence (#421). Spec B, session bootstrap (#424). Spec C, dialogue-act NLU (#428, including #432).
**Followed by:** Spec E, deterministic turn actions. E depends on §4's order guarantee and §6's context blocks.

## 1. Why

This spec targets latency, and quality must hold. The VM call metrics (30 Sep) put first sentence at 2.7–5.0 s against a ~2.2 s telephony target. Spec E removes main-LLM calls. Spec D makes the main LLM's input correct and small, so that:
- the turns that still use it are right;
- E has an honest baseline;
- nothing in D adds a model call or a network hop.

An audit of what the main LLM receives on `deploy/voicera-vm` found these problems:

1. **The spoken-output rules never reach the model.**
   - Blue Dots declares about 6 KB of `channels.bridge.tts_rules`: numbers as words, Devanagari script, names transliterated, no PIN codes.
   - The runtime parses `ChannelConfig.tts_rules` and never renders it. Dev-kit merges it into the suffix only for `channels.voice`.
   - Blue Dots runs on `bridge`, so the model has never seen them.
   - Live calls show the result: salaries spoken wrong ("25755" as "पैंसठ हज़ार"), and Latin names and addresses read aloud.
   - The STT, TTS and telephony are external, and the TTS does not normalise text. Whatever we emit is spoken verbatim.
2. **The model ranks options itself, and the resolver numbers them in stored order.**
   - `job_match` sorts by `match_score` and then by top salary before speaking.
   - Spec C's option resolver numbers rows in stored order.
   - So "पहला वाला" can resolve to a different job from the one the bot said first: a wrong-job bug, recorded in Spec C as a dependency on D.
3. **The model has no conversational memory.**
   - It receives one user message: "[Last question asked: …]" plus the utterance.
   - It cannot see what it said itself, or which part of an interrupted reply the caller heard.
4. **`<caller_turn>` is unexplained.** Spec C added it to the prompt, but no instruction says what it means or that it is already applied.
5. **The Blue Dots prompts contradict themselves and refer to things that don't exist.** There are nine findings, listed in §8.
6. **The model does work code should do.** It drops unusable rows (role `na`, `Any`, blank), ranks rows, and converts numbers to words.

## 2. Goals and non-goals

**Goals**
- G1. A per-channel **output contract**, declared in domain config and rendered by the runtime for every channel. It cannot go dead the way `tts_rules` did.
- G2. Tool results arrive **shaped**: unusable rows dropped, rows sorted, and ready-to-speak fields such as `salary_spoken` added. This happens once, at tool-result ingress, so the in-turn tool result, the cache, `<known_facts>` and the NLU resolver all see the same rows in the same order.
- G3. A deterministic **output guard** on model-generated text, before the Trust output check:
  - rewrite digits as words in the contract language;
  - strip markdown;
  - count foreign-script words as a metric.
- G4. The main LLM gets **`<recent>`**, the last N exchanges word for word with interruptions marked, and a single **`<state>`** block. Both are built in code from the session. No model call produces them.
- G5. A framework-owned **`<how_to_read_context>`** block that explains `<caller_turn>`, `<recent>` and `<state>`. It is cached.
- G6. The Blue Dots prompt is fixed as described in §8.
- G7. No added model calls or network hops. The cached and dynamic prompt combined is smaller than today.

**Non-goals**
- Skipping the main LLM, or pre-dispatching tools. That is Spec E.
- Running NLU and the main LLM in parallel. This was considered and not chosen.
- Fixing STT or TTS behaviour, which is external. The contract makes *our* output speakable; it does not configure their engines.
- Transliterating Latin names into Devanagari in code. The model keeps that job, and the guard only measures it (§5.3).
- Spoken-PII policy (phone numbers, full addresses) as a Trust Layer rule. §4 handles the formatting side (PIN, plot and sector stripping). A policy rule can come later.

## 3. Output contract (G1)

### 3.1 Config

`tts_rules` is replaced by `output_contract` on each channel. There is no compatibility path: `tts_rules` becomes a rejected key (`extra="forbid"`), as Spec C did for the removed NLU keys.

```yaml
channels:
  bridge:
    system_prompt_suffix: |
      ...unchanged voice etiquette, minus the number drills...
    output_contract:
      default_language: hindi
      languages:
        hindi:
          script: devanagari          # devanagari | latin | any
          numbers: words              # words | digits
          rules:                      # short lines rendered to the model, in order
            - "Devanagari only. English loanwords in Devanagari: जॉब, अप्लाई, लोकेशन."
            - "Employer, role and place names arrive in Latin script: sound them out in Devanagari (QUESS CORP → क्वेस कॉर्प)."
            - "Times as सुबह / दोपहर / शाम / रात, never AM/PM. Dates in full words."
            - "Abbreviations as letters in Devanagari: ITI → आई टी आई."
            - "Speak a city, never a PIN, plot, house, gali or sector number."
            - "Read pay from salary_spoken exactly as given; never convert a number yourself."
        english:
          script: latin
          numbers: words
          rules:
            - "Plain spoken English; numbers in words."
      guard:
        rewrite_digits: true
        strip_markdown: true
        count_foreign_script: true
```

- `languages` is keyed by the same language ids as `preprocessing.language_normalisation.supported_languages`. The startup check is that every supported language has a contract entry, and `default_language` is one of them.
- The turn's contract language is chosen in this order: the session's `language_preference` (Spec C's language switch); else `default_language`.
- `rules` are free text authored by the domain. They are the only prompt-visible part. `script`, `numbers` and `guard` drive code, and are rendered as one generated line each, e.g. "Write numbers in words."

### 3.2 Rendering

`ManagerAgent.build_system_prompt` renders `<output_contract>` in **Tier 1**, the cached tier, right after `<channel_rules>`:
- the `default_language` entry comes first, as plain lines: its generated `script` and `numbers` lines, then its `rules`;
- every other supported language follows as its own group headed "If the conversation is in <language>:", with the same lines.
- The block is static per channel, so it is prompt-cacheable.
- The language-specific *choice* still comes from the dynamic `<channel_context>` line, which already carries the language instruction.

Dev-kit's `channel_tts.py`, the authoring-time merge of `tts_rules` into `system_prompt_suffix`, is deleted. The runtime is now the single renderer, for every channel. Domains whose suffix contains a previously merged `<!-- tts_rules:begin -->` block have it removed when they are migrated (§10).

## 4. Result shaping at tool-result ingress (G2)

### 4.1 Config

Each connector gets an optional `result_shaping` block, alongside Spec A's `cache`:

```yaml
connectors:
  read:
    - name: fetch_jobs
      cache: { scope: session, ttl_seconds: 1800 }
      result_shaping:
        list_key: ""                      # rows are the result list itself (projection output)
        drop_when:                        # a row is dropped if ANY clause matches
          - { field: role, operator: in, value: [null, "", "na", "NA", "Any", "Not Available"] }
          - { field: role, operator: contains, value: "|" }
        sort:                             # stable; nulls last
          - { field: match_score, order: desc }
          - { field: salary_max, order: desc }
        spoken:                           # derived fields, rendered in the contract default language
          salary_spoken: { format: range_thousands, from: [salary_min, salary_max], unit: per_month }
          stipend_spoken: { format: range_thousands, from: [stipend_min, stipend_max], unit: per_month }
          task_rate_spoken: { format: amount, from: [task_rate_min, task_rate_max], unit: per_task }
        strip_numbers_in: [location]      # remove PIN/plot/sector digits from these fields
```

- **Operators** reuse the routing condition evaluator: `eq`, `in`, `gt`, plus a new `contains` for string fields.
- **`format` values (v1):**
  - `range_thousands`: round each bound down to its thousand, say the ascending pair once ("पच्चीस से इकतीस हज़ार"). With one bound: "करीब X हज़ार". With none: the field is omitted.
  - `amount`: the exact number in words.
- **A range is rejected when it can't be right:** a lower bound greater than the upper bound, or a non-numeric value. The field is then omitted, never guessed. This replaces the prompt's "sanity-check the range" rule.
- **`strip_numbers_in`** removes standalone digit runs, plus common address tokens followed by digits, from the named fields. For example, "Dasna, 201015" becomes "Dasna". The city-only rule stays in the prompt, because choosing the city out of a full address string is judgement.

### 4.2 Where it runs

`result_shaping` is applied **once, in Agent Core, when a tool result arrives from the Action Gateway**. That is before the result is:
1. appended as the in-turn `tool_result` message;
2. handed to `TurnToolCache.after_call`;
3. counted toward `recent_tool_exchanges`.

Both paths, sync and stream, use the same function. The cache therefore stores shaped rows. `<known_facts>`, `served_tool_results`, Spec C's option resolver and `<state>`'s offer list (§6.3) all read the same order. "Option N" means the same row everywhere.

Shaping is pure and never raises. If it fails on unexpected data, the unshaped result passes through and `agent_core.result_shaping.errors_total{tool}` is counted. Pagination (`offset`) shapes each page on its own, so the order is "best first within each page of five".

### 4.3 Why Agent Core, not the Action Gateway

The Action Gateway already projects fields, and it could filter and sort. `spoken` fields are rendered in the *output contract language*, though, and the contract is an Agent Core concern. The gateway also stays a transport-and-projection block that can be shared by agents with different output contracts. Keeping drop, sort and spoken together at one ingress point means one place to reason about order.

## 5. Output guard (G3)

### 5.1 Placement

The guard runs on **model-generated text only**, sentence by sentence:
- **Stream path:** each sentence the sentence splitter emits is guarded *before* it enters `_TrustOutputBatcher.add`, in both the round-1 and the post-tool rounds. The Trust check therefore sees exactly the text that will be spoken.
- **Sync path:** the guard runs on `final_text` before `check_output`, splitting it into sentences the same way.

Config-authored copy is not guarded. It is the domain's responsibility, and §10 adds a dev-kit lint for digits in it. That copy covers:
- opening phrases;
- `fixed_opening`;
- `blocked_message`, `escalation_message` and `unsupported_language_message`;
- terminal phrases.

### 5.2 Transformations (in order)

1. **Strip markdown.** Asterisks, `#` headings, backticks, bullet and numbered-list markers at the start of a line, and `[text](url)` (reduced to its text).
2. **Rewrite digits** (when `numbers: words` for the turn's language):
   - A run of **7 or more digits**, ignoring spaces and dashes inside it, is a phone or ID. It is spoken digit by digit in words ("नौ, आठ, सात…").
   - A number range `A-B` or `A–B` becomes "A से B" in Hindi, or "A to B" in English, each part in words. Currency `₹` and `Rs` prefixes become a trailing "रुपये" / "rupees".
   - Any other number, including grouping commas and Devanagari digits ०–९, becomes words. Decimals are spoken as "X दशमलव Y" / "X point Y".
   - The converters are table-driven:
     - **Hindi:** irregular 0–99, then सौ, हज़ार, लाख, करोड़.
     - **English:** the standard scheme.
     - They are tested exhaustively for 0–99,999 and on boundary values.
3. **Count foreign-script words** (when `count_foreign_script`). For a `devanagari` contract, count the Latin-alphabet tokens of length 2 or more in the sentence. The guard emits `agent_core.output_guard.foreign_script_words_total{channel,language}` and a per-turn `foreign_script_words` count in the `orchestrator.stream_turn_complete` extras. **The text is never logged.** Nothing is rewritten, because code cannot reliably transliterate names.

The guard is pure and never raises. On an internal error the sentence passes through unchanged, and `agent_core.output_guard.errors_total` is counted. It adds well under 1 ms per sentence.

### 5.3 What the guard is not

It is a safety net behind §3 and §4, not the main mechanism. A digit in model output means a number escaped the spoken fields. The digit-rewrite count is emitted as `agent_core.output_guard.digits_rewritten_total`, so regressions in prompt or shaping show up as a rising rate.

## 6. Main-LLM context (G4, G5)

### 6.1 Messages

`ManagerAgent.build_messages` stops emitting "[Last question asked: …]". The user message is the caller's utterance only. The question context moves into `<recent>` and `<state>`. Replayed tool exchanges (`_prepend_tool_replay`) are unchanged.

### 6.2 `<recent>` (dynamic tier)

- **Source:** the `recent_turns` session list Spec C writes, each entry `{caller, bot, interrupted}`, where `bot` is what the caller heard. It shows the last `agent.history_turns` entries (new key, default 2, `ge=0`; 0 omits the block).
- **Keeping enough turns:** the writer keeps `max(agent.history_turns, nlu_processor.history_turns)` entries, so each consumer can take its own slice.

Rendered as:

```
caller: मेरा नाम अजय सिंह है
bot: धन्यवाद अजय जी। आपकी उम्र कितनी है?
caller: अट्ठाईस साल
bot (caller heard only): ठीक है। आपके लिए जॉब्स हैं — पहला:
```

The current turn's utterance is the user message, not a `<recent>` line. Interrupted entries use the "(caller heard only)" label.

### 6.3 `<state>` (dynamic tier; replaces `<known_profile>`)

One block, built by `ConversationStateRenderer` from the session, the profile, the workflow and the tool cache:

```
phase: job_match
waiting for: select_job — offered jobs में से एक, या दूसरी search
collected (do not ask again): name=अजय सिंह · age=28 · trade=Welder · location=Bengaluru
offered (read in this order): 1 Welder, Flipkart · 2 Welder, Titan · 3 Welder, Bosch
status: applications_submitted=0
```

Each line comes from:
- **`phase`:** `current_subagent_id`.
- **`waiting for`:** the pending question resolved by Spec C's `PendingResolver` for this subagent, with its `expects` text. Omitted when nothing is pending.
- **`collected`:** today's `_build_profile_context` output (profile fields, NLU-mapped session fields and `prompt_session_fields`), under the same "do not ask again" contract. `<known_profile>` is removed, and its content moves here.
- **`offered`:** for a pending question with `options_from`, the labels of the rows the caller was last served. It reads `served_tool_results` and the cache entry, the same rows and order the resolver uses, rendered with `options_from.fields`. This is the line Spec C's §6.3 order guarantee rests on.
- **`status`:** the new `agent.state_fields`, a list of session keys to show as-is. For Blue Dots: `applications_submitted`, `selected_job_item_id`.

`<known_facts>` stays as it is. It carries the full shaped rows the model needs for details (salary, nature of job). §4's row dropping makes it smaller.

### 6.4 `<how_to_read_context>` (Tier 1, cached)

Framework text rendered by `manager_agent` whenever any of `<caller_turn>`, `<recent>` or `<state>` can appear. Domains neither author it nor override it.

```
<how_to_read_context>
- <caller_turn> is the system's reading of what the caller just did. It is
  already applied: listed updates are saved, and "resolved: option N" is the
  option the caller picked. Act on it; do not ask the caller to confirm what it
  shows, and do not re-ask a value it lists.
- "open: <question>" means that question is still waiting: answer what the
  caller asked, then return to it in the same reply.
- "off_track" means the caller has drifted several times: briefly restate what
  you need and why.
- "understanding unavailable" means rely on the caller's words and <recent>.
- If the caller's words clearly contradict <caller_turn>, act on neither: ask
  one short question to settle it.
- <recent> is the last exchanges. "(caller heard only)" marks a reply they did
  not hear in full: do not repeat what they heard; finish what they did not.
- <state> is where the call stands. Never ask for a value under "collected".
  Read offered options in the order listed and never re-rank them.
</how_to_read_context>
```

### 6.5 Final prompt order

| Tier | Blocks |
|---|---|
| **Tier 1** (session cache) | `<persona>` · `<channel_rules>` · `<output_contract>` · `<how_to_read_context>` · `<session_end_policy>` |
| **Tier 2** (session cache) | `<subagent>` · `<user_state_guidance>` |
| **Tier 3** (dynamic) | `<channel_context>` · `<resumption>` · `<state>` · `<recent>` · `<known_facts>` · `<caller_turn>` |

`<active_guardrails>` is dead code: the orchestrator never passes guardrail constraints. It is removed from `build_system_prompt`, along with its parameter. The unused Trust-client `assemble_constraints` stays, under Spec C's ruling.

## 7. Parity, errors, logging

- **Parity:** everything in §4–§6 is wired into both `process_turn` and `stream_turn`, with a parity test asserting identical `system` blocks and messages for the same session.
- **Never raise:** result shaping, the guard and the state renderer are pure, and fall back to pass-through or an omitted block.
- **No caller text or values in logs:** new logs carry counts, keys and reasons only. `<recent>` and `<state>` contents are never logged. They reach the model only.

## 8. Blue Dots prompt changes (G6)

Each change keeps the rule that matches what the code does now and deletes the stale text. Line numbers refer to `dev-kit/configs/blue-dots/agent_core.yaml` at `cf794ef`.

| # | Finding | Change |
|---|---|---|
| A1 | Persona sample (L864-867) announces "<N> नौकरियाँ मिली हैं", and job_match (L2186-2191) forbids announcing a count | Drop the count from the sample: "आपके लिए जॉब्स हैं —". |
| A2 | Filler fallbacks: "एक पल रुकिए" (L891, L905-907) and the English "Let me share what I found." (L2136), against "never say please wait" (L130, L1255-1258) | Delete all three fallbacks. Empty replies are already retried in code. |
| A3 | "Reuse returned data" (L133-138) vs "never substitute for a fresh fetch_jobs" (L1235-1237) and "never pull a job from a previous tool result" (L2172) | One rule: "Within this call, use the job list you have. Call fetch_jobs again only when the trade or city changes." Delete the other two. |
| A4 | "Trade + city, ONE call" (L2084-2105) vs "TWO-STEP FLOW: location only" (L2116-2127) and "On entry: location only, client-side filter by role" (L2140-2143) | Delete TWO-STEP FLOW and On entry. Fix the stale reference to the "User Profile context the system injects". |
| A5 | "Six phases" lists 1-4 and 6, "name in phase 5" (L1017-1036), and the header says "five" (L5) | Renumber to the five that exist. "phase 5" becomes profile_setup. |
| A6 | "At most 2 short sentences" (L88) vs a 3-sentence sample (L874-876) | Shorten the sample to two sentences. |
| A7 | Two ranking rules (L2118 match_score; L2203-2204 salary), "don't re-rank" (L2247-2248) and "re-ranked on what they said" (L2256) | All ordering moves to `result_shaping.sort` (§4). The prompt says "Read jobs in the order given; never re-rank." Row-cleaning steps L2107-2116 are deleted; only the "skip work they didn't ask for" judgement stays. |
| A8 | Instructions the model can't carry out: "Save selected_job_item_id… route to onboard_prep" (L2259-2262, no such tool or phase); "values… above, in your context" (L1213-1214) | Replace with "When the caller picks a job, <caller_turn> shows it as resolved; say the role and company back once." Refer to `<state>` / `<recent>` by name, not position. |
| A9 | Number drills: the dead `tts_rules` plus job_match's salary drill (L2193-2201) | Delete both. The pay comes from `salary_spoken` (§4), and the remaining rules move into `output_contract.languages.hindi.rules` (§3). |

Blue Dots also gains:
- `connectors.read.fetch_jobs.result_shaping`, as in §4.1;
- `agent.history_turns: 2`;
- `agent.state_fields: [applications_submitted, selected_job_item_id]`.

The `job_match` "Presenting results" templates use `[salary_spoken]` in place of `[salary]`.

## 9. Testing and measurement

### 9.1 Unit and contract tests
- **Number converters:**
  - Hindi and English exhaustive tables, 0–99,999;
  - lakh and crore boundaries;
  - ranges and currency;
  - phone runs;
  - Devanagari digits.
- **Guard:**
  - markdown stripping;
  - digit rewrite on sentence fragments;
  - foreign-script counting;
  - the never-raise path;
  - running before Trust on both paths.
- **Result shaping:**
  - `drop_when` operators;
  - stable sort with nulls last;
  - each `spoken` format, including rejected ranges;
  - `strip_numbers_in`;
  - the same order in the in-turn tool result, the cache, `served_tool_results` and resolver numbering;
  - the pass-through fallback.
- **Context:**
  - the `<recent>` slice and interrupt label;
  - every `<state>` line and its omission rules;
  - the `offered` order matching resolver order;
  - `<how_to_read_context>` presence;
  - Tier placement;
  - "[Last question asked]" no longer emitted.
- **Schema:**
  - `output_contract`, `result_shaping`, `history_turns` and `state_fields` in the runtime schema, the dev-kit mirror, the flat schema, FIELD_RULES and dpg defaults;
  - `tts_rules` rejected;
  - the startup check that every supported language has a contract.
- **Parity:** sync vs stream system blocks and messages.

### 9.2 Scenario runner (new, small)

`agent_core/eval/scenarios/` scripts the regression scenarios from `docs/blue-dots/test-scenarios.md` against a local bridge.
- **Input:** caller lines per scenario.
- **Output:** a report per run, with replies and timings (`first_sentence_ms`, `llm_ttft_ms`, number of LLM calls).
- **Automatic checks:**
  - no digits;
  - no markdown;
  - the foreign-script word count;
  - no job count announced;
  - the job spoken first is option 1 in `<state>` (from the session);
  - no "please wait" phrases.
- **Human review:** a reviewer reads the replies for tone and correctness.

The runner uses a fresh phone number per run, as the scenarios doc requires. Spec E reuses it as its latency and quality baseline.

### 9.3 Acceptance

- **Spec C NLU eval:** no regression on `eval/nlu` (synthetic + scenarios). D does not touch the NLU, but `recent_turns` retention changes.
- **Scenario runner, on the A1, A2, B1–B4, C2–C4, D3–D15 and E1–E4 scenarios:**
  - zero digits and zero markdown in model output after the guard;
  - the digit-rewrite rate is reported, with a target of near zero;
  - zero spoken job counts;
  - the first-spoken job equals stored option 1 every time;
  - no regression in the human review against a pre-D run of the same script.
- **Prompt size:** the combined Tier 1–3 size for the Blue Dots `job_match` and `apply_confirm` phases is smaller than at `cf794ef`. Size is measured by the runner from provider input-token usage.
- **Latency:** `first_sentence_ms` p50 is no worse than the pre-D run on the same script, and the number of LLM calls per turn is unchanged. D's latency gains are expected to be small; E delivers the big ones.

## 10. Rollout and migration

- **One PR per block, all on one branch:**
  1. runtime (shaping, guard, contract rendering, context);
  2. dev-kit sync and the authoring changes;
  3. the Blue Dots config rewrite.
- **No compatibility mode.** `tts_rules` and the `channel_tts.py` merge are removed, and the Blue Dots suffix loses its stale "see tts_rules below" pointer. Other shipped configs carry `tts_rules: null`, and those keys are deleted. The dev-kit wizard authors `output_contract` in place of `tts_rules`.
- **Dev-kit lint (warning, not error):** digits or markdown in config-authored spoken copy (opening phrases, messages, `fixed_opening`).
- **Pre-merge:** rebuild the dev-kit image and run `deploy/validate` with `validator: runtime_baked`. Then run the scenario runner on the local stack, before and after.

## 11. Risks and open questions

- **Over-eager digit rewriting.** Model output that legitimately needs digits would also be rewritten, e.g. an OTP read-back. Mitigation: `rewrite_digits` is per channel, and Blue Dots never needs digits spoken.
- **`range_thousands` rounding loses information**, e.g. "पच्चीस से इकतीस हज़ार" for 25,755–31,121. This is the existing Blue Dots rule, and it is preferred over a wrong exact figure.
- **History cost.** `<recent>` adds about 100–200 uncached tokens per turn. That is offset by the `<known_facts>` shrink from row dropping, and by the removed duplicates. Measured in §9.3.
- **The model may still re-rank on "best" requests.** If a caller asks "सबसे ज़्यादा सैलरी वाली कौन सी है?", the model answers from `<known_facts>` without changing the read order. This is covered by a scenario check.
- **Open:** whether `count_foreign_script` should graduate to an alert threshold, once a baseline exists from the VM.

## 12. Dependency notes for Spec E

- E's skip templates and its pre-dispatched-tool replies read the same shaped rows and `spoken` fields, and pass through the same guard.
- E's "select" and "submit" pairs rely on §4.2's single ordering and §6.3's `offered` line.
- E measures against §9.2's runner baseline.
