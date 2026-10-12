# Calls end at the apply step without the caller asking — design

Issue: #501 (sub-issue of #414). Branch `fix/501-yes-other-jobs-ends-call`, cut from #495.

## Observed

Calls end with the goodbye at the apply step (`apply_confirm`) even though the caller never asked to leave and nothing was applied for. There are two cases.

### Case 1: "yes, show me other jobs" ends the call

Seen on the shared VM on 7 Oct 2026 (image `sha-46fb6e6`), in calls `2ee0af51` and `d500461e`. Both followed the same shape:

1. The caller picked a job they had already applied to.
2. `apply_job` returned HTTP 422 `ACTION_LIMIT_REACHED`.
3. The bot said "इस जॉब के लिए आपकी एप्लीकेशन पहले से लगी हुई है… क्या मैं आपको दूसरी जॉब्स बताऊँ?" ("You have already applied for this job… shall I tell you about other jobs?").
4. The caller said "हाँ, दूसरी जॉब बताइए" ("yes, tell me other jobs").
5. The bot spoke the termination message and the call ended.

From Agent Core's logs in Loki, for the final turn of both calls:

```
nlu.understanding  acts=[affirm, request_change]  pending_id=submit_confirm
                   relation=answers_pending  derived_intent=apply_now
Routing ✓ next_subagent=ended  matched_rule_intent=*
```

In call `d500461e` the same sentence was said twice:

| Time (UTC) | Entries into `apply_confirm` | Result |
| --- | --- | --- |
| 14:10:49 | 2 | stayed in `apply_confirm` |
| 14:12:24 | 5 | routed to `ended` |

The NLU output was identical both times. Only the counter differed.

### Case 2: asking about a job before applying ends the call

When the caller picks a job, the bot asks "shall I apply?". If the caller asks something about the job instead, for example its salary, the bot answers and asks "shall I apply?" again, every turn. After about four turns the call ends, before the caller has decided:

| Turn | Caller | Counter at routing | Result |
| --- | --- | --- | --- |
| 1 | "तीसरे नंबर के जॉब के बारे में जानना चाहता हूँ" ("I want to know about the third job") | 0 | enters `apply_confirm`, asked "shall I apply?" |
| 2 | "सैलरी कितनी है?" ("what is the salary?") | 1 | stays, asked "shall I apply?" again |
| 3 | "जगह कहाँ है?" ("where is it?") | 2 | stays, asked again |
| 4 | "काम कैसा है?" ("what is the work like?") | 3 | stays, asked again |
| 5 | "हाँ, भेज दीजिए" ("yes, send it") | 4 | `ended`, no application |

## Cause

### A. The open question is stale (case 1)

After the "already applied" line, the question actually asked is "shall I show you other jobs?". But `apply_confirm`'s pending question is still `submit_confirm` ("shall I send the application?"). A pending question is chosen from session state, and nothing in session records that the apply failed:

- On any HTTP error, Action Gateway's REST adapter returns from its error branch before `session_mapping` runs. So the 422 writes nothing to session, even though its body is the full bulk envelope (`results[].error`, `summary.failed`).

Against `submit_confirm`, the act-intent row `{acts: [affirm], pending: submit_confirm} → apply_now` matches first, because rows match on a subset of acts. So "yes, other jobs" is read as "apply now". `apply_confirm` has no rule for `apply_now`, so the turn falls to the catch-alls, where the turn counter (`subagent_entry_count.apply_confirm > 3 → ended`) ends the call.

### B. There is no step for questions about a job (case 2)

Picking a job, even only to hear about it, sends the caller straight from `job_match` to `apply_confirm`. That step has one purpose, to apply, so it ends every reply on "shall I apply?". A question about the job (`acts: [ask]`, `topic: salary | location | job_details`) maps to no intent, so it falls to the same catch-alls, and each question counts as a turn. On the fifth turn the counter ends the call. Routing runs before the tool call, so a "yes, send it" on that turn ends the call before `apply_job` is ever made.

The counter itself is reasonable for the apply step. The fault is that questions are being asked in the apply step at all.

The same rules are on `develop`, so both cases are live.

### Why the earlier question step failed

A `job_detail` step sat between the pick and the apply from 29 to 30 Sep, and was removed because the flow broke: callers were asked the same question repeatedly and could not get to the apply. Its config shows why:

| What it did | Effect |
| --- | --- |
| Moved on by turn count: one turn to describe the job, then any reply went to `profile_choice` | A question on the second turn was not answered, it was routed away. A caller who wanted to apply on the first turn was held for one more. |
| Ended on "any questions?" and was told never to ask about applying | No `submit_confirm` question was open, so a "yes" could not be read as "apply". |
| Handed off to `profile_choice`, which asked its own question, and then to `apply_confirm`, which asked "shall I send it?" again | Three questions between the pick and the apply. |
| No `apply_now` rule | The only way out to the apply was the turn count. |
| `opening_phrase: ""` and no tools | Produced two of the three empty replies on a live call. |

The new step below is built against each of these.

## Changes

Changes 1–5 fix cause A (case 1). Changes 6–8 fix cause B (case 2).

### Case 1

#### 1. `session_mapping` on error responses (Action Gateway, opt-in)

Add `on_error: bool = False` to `SessionMapping`. In the REST adapter's HTTP error branch, when the body decodes as JSON, apply only the mappings with `on_error: true`.

It is opt-in so that existing mappings keep their meaning. Otherwise a failed apply after an earlier success would overwrite `applications_submitted` with 0, bringing back the "send it?" question and stopping the call from ending after a real application.

#### 2. Record the failure (`action_gateway.yaml`, `apply_job`)

Map `summary.failed` to a new session field `apply_failed`, with `on_error: true`. A failed apply writes 1, and a successful one writes 0 through the existing success path.

#### 3. A pending question for "other jobs?" (`agent_core.yaml`, `apply_confirm`)

| Order | Pending | Applies when |
| --- | --- | --- |
| 1 | `closing_offer` (unchanged) | `applications_submitted > 0` |
| 2 | `more_jobs_offer` (new): "दूसरी जॉब्स बताऊँ? हाँ/नहीं, या कोई job चुनना" ("Shall I tell you about other jobs? yes/no, or pick a job") | `apply_failed > 0` |
| 3 | `submit_confirm` (unchanged) | always |

`more_jobs_offer` takes the same `options_from: fetch_jobs` as `submit_confirm`, so a caller who names a job is still resolved against the offered list.

New act-intent rows:

| Caller | Acts | Intent | Routes to |
| --- | --- | --- | --- |
| "हाँ" ("yes") / "हाँ, दूसरी जॉब बताइए" ("yes, tell me other jobs") | `affirm` | `explore_more` | `job_match` |
| names a job | `select` | `job_pick` | `job_details` (change 6) |
| "नहीं" ("no") | `deny` | declines more jobs | `confirm_close`, with `close_return_to: apply_confirm` |

#### 4. Clear the flag when the caller moves on

Every routing rule that takes the caller to a new pick or back to the list writes `apply_failed: 0` via `session_writes`. Without this, a stale `more_jobs_offer` would answer the next "send it?".

#### 5. Safety net

Add `{acts: [affirm, request_change], pending: submit_confirm, intent: explore_more}` above the `apply_now` row. It catches "yes, other jobs" if it still reaches the old question, for example after a timeout that wrote no flag.

### Case 2

#### 6. A new `job_details` step between the pick and the apply

The caller picks a job and lands here. This step describes the job and answers questions about it. There is no limit on questions: the caller can ask as many as they like, for as many turns as they like. From here they can:

- **apply**, by saying yes, which hands them over to the apply straight away;
- **change the job**, by naming another job from the list, which stays here with that job, or by asking to hear the list again, which goes back to `job_match`;
- **leave the job**, by saying no, which goes back to `job_match`.

##### Every reply ends on "shall I apply?"

Every reply in this step, the first description and each answer to a question, ends on the same question: "कुछ पूछना हो तो पूछिए। क्या मैं इस नौकरी के लिए आवेदन भेज दूँ?" ("Ask if you have any questions. Shall I send the application for this job?").

Why:

- **A "yes" must always mean apply.** The caller's short reply is understood against the question the bot just asked. If one reply ends on "shall I apply?" and the next on "anything else?", the same "हाँ" ("yes") means "apply" after one and "I have another question" after the other. Ending every reply on the apply question means a yes is always read as `apply_now`, whenever the caller is ready, after no questions or after many.
- **A "no" must always mean not this job.** The old step ended on "any questions?", where "नहीं" ("no") meant "no questions, go ahead". It needed special prompt rules to avoid treating that as a refusal, and still went wrong. Here "no" can only mean "don't apply".
- **The way to apply is never more than one word away.** A caller who has asked several questions does not have to guess how to move on; the next step is always offered.

It is one sentence at the end of the answer, not a separate turn, and the caller is free to ignore it and ask another question.

| Setting | Value | Against which old failure |
| --- | --- | --- |
| Pending | `submit_confirm`, with no `when`, so it is always open here, with `options_from: fetch_jobs` | "yes" had no question to answer |
| Job data | The `fetch_jobs` result, already in `<known_facts>` for every step (session cache, 30 min TTL) | No tools, empty replies |
| Tools | None of its own. `end_session` is added to every step by the orchestrator | Applying can only happen through routing, never from this step |
| `opening_phrase` | A short neutral bridge, like `job_match`'s "ठीक है।" ("Okay.") | Empty opening phrase |
| Turn counter | None. The caller can ask any number of questions; nothing here ends or moves on by count | Routed away on turn count |

New act-intent rows, placed above the existing `submit_confirm` rows:

| Caller | Acts / topic | Intent |
| --- | --- | --- |
| "सैलरी कितनी है?" ("what is the salary?"), "जगह कहाँ है?" ("where is it?"), "काम क्या है?" ("what is the work?") | `ask` with topic `salary`, `location` or `job_details` | `job_question` |
| "हाँ, पर सैलरी कितनी है?" ("yes, but what is the salary?") | `affirm` + `ask`, pending `submit_confirm` | `job_question`, not `apply_now`: a yes with a question is not a yes |

Routing in `job_details`, in order. Every exit is driven by the caller's intent, never by a turn count:

| Intent | Goes to | Note |
| --- | --- | --- |
| `off_track` | `job_details` | same as other steps |
| `termination_intent` / `termination_blocked` | `ended` / `confirm_close` | same as other steps, `close_return_to: job_details` |
| `apply_now`, profile exists | **`apply_confirm`** | the caller said yes |
| `apply_now`, no profile | `profile_setup` | it saves the name and goes on to `apply_confirm`, as today |
| `job_pick` | `job_details` | changes to another job from the list; `selected_job_item_id` is already updated by the resolver |
| `job_question` | `job_details` | answer and ask again, any number of times |
| `explore_more`, `decline` | `job_match` | back to choosing a job |
| `services_request` | `service_match` | |
| `*` | `job_details` | |

#### 7. `job_match` and `apply_confirm` changes

- `job_match`: `job_pick` goes to `job_details`, whether or not a profile exists. `apply_now` from `job_match` ("apply to the second one") keeps going straight to `apply_confirm` / `profile_setup`. The caller has already said what they want, so they don't go through `job_details`.
- `apply_confirm` is now entered only after a yes. Its prompt drops the "if the caller reached you by naming a job, ASK FIRST" branch: on arrival it calls `apply_job` in the same turn, without asking again. Routing runs first, then `apply_confirm`'s prompt and its `apply_job` call, all in the turn where the caller said yes.
- `apply_confirm`: `job_pick` (a re-pick after a failed apply) goes to `job_details` instead of staying, and its re-pick prompt section goes. Add `apply_now → apply_confirm` ahead of the catch-alls.
- The turn counter in `apply_confirm` stays. Questions no longer count against it, because they are asked in `job_details`.

#### 8. Transitions to prove before shipping

Each one is a scripted voice-bench run, with the step and intent checked in the turn banner:

| Path | Expected |
| --- | --- |
| pick → "yes" | `job_details` → `apply_confirm`, application sent in the yes turn, no second "shall I send it?" |
| pick → 8 questions → "yes" | every question answered in `job_details`, each reply ending on "shall I apply?"; the call does not end; the yes turn reaches `apply_confirm` and applies |
| pick → questions → "show me the list again" → pick another → "yes" | back to `job_match`, then `job_details` with the new job, applies to the new job only |
| pick → "yes, but what is the salary?" | stays in `job_details`, answers, nothing applied |
| pick → "no" | back to `job_match` |
| pick → another job → "yes" | applies to the second job only |
| "apply to the second one" from the list | straight to `apply_confirm`, applies |
| new caller: pick → "yes" | `profile_setup` asks the name → `apply_confirm` applies |
| already-applied job → "yes, other jobs" | `job_match` (case 1) |
| any step → goodbye | ends as today |
