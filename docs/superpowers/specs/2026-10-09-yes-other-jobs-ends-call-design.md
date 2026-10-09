# "Yes, show me other jobs" ends the call — design

Issue: #501 (sub-issue of #414). Branch `fix/501-yes-other-jobs-ends-call`, cut from #495.

## What was seen

A tester on the shared VM (7 Oct 2026, image `sha-46fb6e6`) reported two calls that ended with the goodbye straight after the caller said yes to "shall I show you more jobs?".

Both calls followed the same shape:

1. The caller picked a job they had already applied to.
2. `apply_job` returned HTTP 422 `ACTION_LIMIT_REACHED`.
3. The bot said "इस जॉब के लिए आपकी एप्लीकेशन पहले से लगी हुई है… क्या मैं आपको दूसरी जॉब्स बताऊँ?" ("You have already applied for this job… shall I tell you about other jobs?").
4. The caller said "हाँ, दूसरी जॉब बताइए" ("yes, tell me other jobs").
5. The bot spoke the termination message and the call ended. No application was made, and the caller never asked to leave.

## What the logs show

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

## Cause

Two defects stack.

**1. The open question is stale.** After the "already applied" line, the question actually asked is "shall I show you other jobs?". But `apply_confirm`'s pending question is still `submit_confirm` ("shall I send the application?"). A pending question is chosen from session state, and nothing in session records that the apply failed:

- On any HTTP error, Action Gateway's REST adapter returns from its error branch before `session_mapping` runs. So the 422 writes nothing to session, even though its body is the full bulk envelope (`results[].error`, `summary.failed`).

Against `submit_confirm`, the act-intent row `{acts: [affirm], pending: submit_confirm} → apply_now` matches first, because rows match on a subset of acts. So "yes, other jobs" is read as "apply now".

**2. The backstop counts every turn and never resets.** `apply_confirm` has no rule for `apply_now`, so the turn falls through to the catch-alls:

```
"*"  if applications_submitted > 0              → ended
"*"  if subagent_entry_count.apply_confirm > 3  → ended   ← fired
"*"                                             → apply_confirm
```

The backstop is meant as "three turns here without an application, so stop". But `subagent_entry_count.apply_confirm` goes up on every turn spent in the phase, including re-picks and returns from `job_match`, and nothing resets it. After about four turns, any reply in `apply_confirm` ends the call.

The same rules are on `develop`, so this is live.

## Fix

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

### 5. The backstop asks before it hangs up

`subagent_entry_count.apply_confirm > 3` routes to `confirm_close` (with `close_return_to: apply_confirm`) instead of `ended`. It only does so while `subagent_entry_count.confirm_close` is 0. That way the caller is asked "shall I end the call?" at most once, rather than every other turn as the counter keeps rising. A goodbye still ends the call from any phase.

### 6. Safety net

Add `{acts: [affirm, request_change], pending: submit_confirm, intent: explore_more}` above the `apply_now` row. It catches "yes, other jobs" if it still reaches the old question, for example after a timeout that wrote no flag.

## Unchanged

- A goodbye ends the call from any phase.
- The call ends after a real application (`applications_submitted > 0`).
- pick → "send it?" → yes → apply is untouched.

## Known gap

An apply that times out, or returns a body that isn't JSON, writes no `apply_failed`. In that case `submit_confirm` stays the pending question, and only change 6 covers "yes, other jobs".

## Testing

- Unit tests (Action Gateway):
  - An error body with an `on_error` mapping is lifted into session.
  - Without `on_error`, nothing is lifted (unchanged behaviour).
  - A non-JSON error body lifts nothing.
- Agent Core: the config loads, and the new act-intent rows derive the expected intents.
- End to end, local host-mode stack against the test cluster, fresh numbers, every outcome checked in Signals:
  - The 7 Oct shape: an already-applied job, then "हाँ, दूसरी जॉब बताइए" ("yes, tell me other jobs") → other jobs are read out, and the call continues.
  - Five or more turns choosing jobs → no hang-up, and at most one "shall I end the call?".
  - Normal path: pick → yes → the application exists upstream → the call ends.
  - After "already applied": a bare "हाँ" ("yes"), a "नहीं" ("no"), and naming a job.
