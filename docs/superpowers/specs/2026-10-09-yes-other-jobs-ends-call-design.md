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

Found while tracing case 1; it has not yet been seen on a call. A caller enters `apply_confirm` as soon as they pick a job. That includes "तीसरे नंबर के जॉब के बारे में जानना चाहता हूँ" ("I want to know about the third job"), which is before they have decided anything. If they then ask a few questions and agree, the call ends before the application is sent:

| Turn | Caller | Counter at routing | Result |
| --- | --- | --- | --- |
| 1 | picks a job | 0 | enters `apply_confirm` |
| 2 | "सैलरी कितनी है?" ("what is the salary?") | 1 | stays |
| 3 | "जगह कहाँ है?" ("where is it?") | 2 | stays |
| 4 | "काम कैसा है?" ("what is the work like?") | 3 | stays |
| 5 | "हाँ, भेज दीजिए" ("yes, send it") | 4 | `ended`, no application |

## Cause

### A. The open question is stale (case 1)

After the "already applied" line, the question actually asked is "shall I show you other jobs?". But `apply_confirm`'s pending question is still `submit_confirm` ("shall I send the application?"). A pending question is chosen from session state, and nothing in session records that the apply failed:

- On any HTTP error, Action Gateway's REST adapter returns from its error branch before `session_mapping` runs. So the 422 writes nothing to session, even though its body is the full bulk envelope (`results[].error`, `summary.failed`).

Against `submit_confirm`, the act-intent row `{acts: [affirm], pending: submit_confirm} → apply_now` matches first, because rows match on a subset of acts. So "yes, other jobs" is read as "apply now".

### B. The turn-count backstop ends the call (cases 1 and 2)

`apply_confirm` has no rule for `apply_now`, or for a question about the job, so those turns fall through to the catch-alls:

```
"*"  if applications_submitted > 0              → ended
"*"  if subagent_entry_count.apply_confirm > 3  → ended   ← fires
"*"                                             → apply_confirm
```

The backstop is meant as "three turns here without an application, so stop". But `subagent_entry_count.apply_confirm` goes up on every turn spent in the phase, and nothing resets it. That includes questions about the job, re-picks and returns from `job_match`. After about four turns, any reply in `apply_confirm` ends the call:

- In case 1, the reply is "yes, other jobs", read as `apply_now`.
- In case 2, the reply is "yes, send it". Routing runs before the tool call, so the call ends before `apply_job` is ever made.

The same rules are on `develop`, so both cases are live.

## Changes

Changes 1–4 and 6 fix cause A (case 1). Change 5 fixes cause B (cases 1 and 2).

### 1. `session_mapping` on error responses (Action Gateway, opt-in)

Add `on_error: bool = False` to `SessionMapping`. In the REST adapter's HTTP error branch, when the body decodes as JSON, apply only the mappings with `on_error: true`.

It is opt-in so that existing mappings keep their meaning. Otherwise a failed apply after an earlier success would overwrite `applications_submitted` with 0, bringing back the "send it?" question and stopping the call from ending after a real application.

### 2. Record the failure (`action_gateway.yaml`, `apply_job`)

Map `summary.failed` to a new session field `apply_failed`, with `on_error: true`. A failed apply writes 1, and a successful one writes 0 through the existing success path.

### 3. A pending question for "other jobs?" (`agent_core.yaml`, `apply_confirm`)

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
| names a job | `select` | `job_pick` | `apply_confirm`, which asks "send it?" |
| "नहीं" ("no") | `deny` | declines more jobs | `confirm_close`, with `close_return_to: apply_confirm` |

### 4. Clear the flag when the caller moves on

Every routing rule that takes the caller to a new pick or back to the list writes `apply_failed: 0` via `session_writes`. That is `job_pick` and `explore_more` in `apply_confirm`, and `job_pick` / `apply_now` from `job_match` into `apply_confirm`. Without this, a stale `more_jobs_offer` would answer the next "send it?".

### 5. Remove the turn-count backstop, and keep "apply now" in the phase

- Delete the `subagent_entry_count.apply_confirm > 3 → ended` rule. Turns are the wrong measure: questions about the job, re-picks and returns from the list all count, and the rule fires before the apply it was meant to wait for. Its purpose, not looping when an application will not happen, is now covered by `more_jobs_offer`. After a failed apply, the caller is always offered other jobs.
- Add `apply_now → apply_confirm` to `apply_confirm`'s routing, ahead of the catch-alls. A "yes, send it" then always reaches the apply, however many questions came before it.

The call still ends on a goodbye from any phase, or once an application exists. A caller who keeps going round in circles is no longer cut off automatically. A limit on failed applies, rather than on turns, would cover that, but it is out of scope here.

### 6. Safety net

Add `{acts: [affirm, request_change], pending: submit_confirm, intent: explore_more}` above the `apply_now` row. It catches "yes, other jobs" if it still reaches the old question, for example after a timeout that wrote no flag.
