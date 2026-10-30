# Blue Dots voice agent — regression scenarios

Every scenario that must pass before a build goes to the VM. Each one names
what it guards, so a failure points at a finding rather than a vague "the bot
is worse".

**How to run.** All scenarios drive the bridge on the streaming path — the one
VoicEra uses. The sync path is not covered here and is not what ships.

```bash
curl -sN http://127.0.0.1:18008/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model":"blue-dots","stream":true,
    "messages":[{"role":"user","content":"<the caller'"'"'s line>"}],
    "metadata":{"caller_phone":"9197XXXXXXXX","call_id":"<one id per call>"}}'
```

- Same `caller_phone` + `call_id` across turns = one call. New `call_id` = new call.
- **Use a fresh phone number per run.** The dev Signals cluster is shared and
  every turn writes real profiles and real applications.
- Local bridge is **18008**; the VM tunnel is **8008**. Check which you are on
  before believing a result.

**Observation points.**

```bash
docker logs dpg_agent_core --since 5m 2>&1 | grep -E 'STEP 5\] NLU|STEP 6\] Routing|STEP 8|STEP 9'
docker exec dpg_redis redis-cli HGETALL "session:<phone>:<call_id>"
curl -s "https://dev-signals.serveirc.com/api/v1/admin/participant?phone_number=<phone>" \
  -H "x-api-key: $BLUE_DOTS_API_KEY" -H "x-acting-org-id: $BLUE_DOTS_ORG_ID"
```

> `recent_tool_exchanges` keeps only the **last three** exchanges. A missing
> successful call is often eviction, not absence — check the orchestrator log
> before concluding a tool did not run. (This mistake produced a retracted
> finding, F29.)

---

## A. Happy paths

### A1 — new user, full journey to apply
> नमस्ते / मैं वेल्डर का काम ढूंढ रहा हूँ / बेंगलुरु / पहला वाला /
> हाँ भेज दीजिए / हाँ अनुमति है / अट्ठाईस साल / मेरा नाम अजय सिंह है / बस इतना ही

**Expect:** trade → city → job list → detail → **permission** → age → name →
save → apply → goodbye. Permission is asked at the apply, never before it.
One profile created with the caller's real name. One application with a real
`action_id`.
**Guards:** F17, F18 (no placeholder name, no duplicate profile).

### A2 — returning user
Call A1's number again on a new `call_id`. **Run A1 first and let it finish** —
a "returning" call placed before its own seed finds no profile, asks for the
age it should already know and loops there. That reads exactly like a broken
returning path and is not one; check the seed before filing it.
**Expect:** the stored trade and city are recognised and offered back; no
re-asking for name or age. Exactly one profile still.
**Guards:** F16, the profile-cap behaviour.

### A3 — returning user with a full address stored
Use a number whose stored `location` is a full postal address.
**Expect:** the address is never spoken — no company name, cross road, sector
or layout. Nor is the record narrated: "आपकी जानकारी में बैंगलोर है" used to be
the expected line here and is now **banned**, along with every other
"आपकी जानकारी में …" form. A stored value is confirmed by asking about it —
"क्या आप बेंगलुरु में ही काम ढूंढ रहे हैं?" — never by reading it back.
**Guards:** **F22**, and the lookup leak.

---

---

## B. Profile state — what the caller already has

These are the highest-value scenarios and the easiest to get wrong, because
the agent's view of a caller is **narrower than the upstream's**. The
`fetch_profile` connector lifts `profile_item_id`, `stored_trade` and
`stored_location` from `items[lifecycle_status=live]` **only**. Draft and
retired rows exist in the response and must be ignored.

**Check the caller's real state before and after every run:**

```bash
curl -s "https://dev-signals.serveirc.com/api/v1/admin/participant?phone_number=<phone>" \
  -H "x-api-key: $BLUE_DOTS_API_KEY" -H "x-acting-org-id: $BLUE_DOTS_ORG_ID" \
  | python3 -c "
import sys,json
for it in json.load(sys.stdin).get('items',[]):
    s=it.get('item_state',{})
    print('%-8s %-16s %-14s %s' % (it.get('lifecycle_status'), s.get('name'),
          s.get('nameOfJobRolesInterestedIn'), s.get('location')))"
```

And what the agent actually saw:

```bash
docker exec dpg_redis redis-cli HMGET "session:<phone>:<call_id>" \
  profile_item_id stored_trade stored_location
```

> **Setting up a draft row is not possible through the voice path** — the
> participant API creates live rows. Arrange these states from the Signals
> side, or run them against a number already known to be in that state. Record
> the number you used in the run notes; these are the scenarios most likely to
> be skipped silently.

### P1 — no profile at all (new caller)
**Expect:** trade and city first, then — only once the caller asks to apply —
permission, age and name. One live profile created after that.
**Session should show:** `profile_item_id` empty on turn 1, populated after the
save.

### P2 — exactly one live profile
**Expect:** the stored trade and city are offered back in ONE sentence —
"मुझे आपकी जानकारी मिली — <city> में <trade>। क्या इन्हीं से नौकरियाँ ढूंढूँ?"
Name and age are NOT re-asked.
**Guards:** the returning-caller path; F8 (the removed use/create/update
question must not reappear).

### P3 — a DRAFT profile and nothing live
**Expect:** the caller is treated as new. The agent must **not** read the
draft's trade, city, name or age back to them.
**Fails if:** it says "मुझे आपकी जानकारी मिली — बेंगलुरु में वेल्डिंग" while
`profile_item_id` is empty. That exact failure is called out in the config: a
draft cannot be applied with, so the offer leads nowhere and the call
dead-ends at the apply.
**Session should show:** `profile_item_id`, `stored_trade`, `stored_location`
all empty.
**Guards:** the draft dead-end.

### P4 — one live AND one draft
**Expect:** the LIVE row is used and the draft is invisible. Trade and city
spoken must match the live row, not the draft.
**Fails if:** the draft's values are spoken, or the caller is asked to choose
between them.

### P5 — several live profiles
**Expect:** one is used without the caller being made to choose, and it should
be the most recently updated (`updated_at`). The call proceeds normally.
**Check:** compare the trade and city spoken against the newest live row.
**Guards:** F16 — logged LOW; silent selection is accepted, picking a stale row
is not.

### P6 — only retired profiles
**Expect:** treated as a new caller — retired is not live. A new profile is
created rather than the retired one being revived or counted against any cap.
**Guards:** the retired-vs-cap behaviour.

### P7 — one live and one or more retired
**Expect:** the live row is used; retired rows are ignored entirely.

### P8 — profile exists but has no trade or city
A live row whose `nameOfJobRolesInterestedIn` or `location` is empty.
**Expect:** the agent asks for the missing field only, and does not re-ask for
name or age it already holds.
**Fails if:** it offers "<empty> में <empty>" or starts onboarding from scratch.

### P9 — same caller, two calls back to back
Run P1, then immediately call again with a **new `call_id`**.
**Expect:** the second call recognises the profile created by the first. One
profile total, not two.
**Guards:** F17, F18 — duplicate-profile creation.

### P10 — same `call_id` resumed
Send a turn, pause, send another with the same `call_id`.
**Expect:** the conversation continues from where it was; no re-greeting and no
repeated questions.

---

## U. Profile update — changing what is stored, mid-call

**Seed first.** Every scenario here needs a caller with one live profile. Run
A1 through to the apply on a fresh number and let it finish; `save_profile`
only fires at the pick turn, so a seed that stops earlier leaves no profile and
every U scenario reads as broken when it is not.

Check the profile upstream before and after, not just the transcript — the
whole point of these is what was written:

```bash
curl -s "https://signals.bluedotseconomy.org/api/v1/admin/participant?phone_number=<phone>" \
  -H "x-api-key: $BLUE_DOTS_API_KEY" -H "x-acting-org-id: $BLUE_DOTS_ORG_ID"
```

### U1 — the saved profile is accepted, nothing is written
> हाँ / हाँ

**Expect:** the offer, then the search on the stored trade and city. No
`update_profile` call. The stored record is byte-identical afterwards.
**Guards:** the update path must not fire on the accept path.

### U2 — the city is changed and the change is confirmed
> हाँ / नहीं / गाज़ियाबाद / हाँ / हाँ

**Expect:** "क्या बदलना है — काम या शहर?" ("what would you like to change —
the trade or the city?"), then the confirm, then "आपकी प्रोफ़ाइल अपडेट हो गई
है" ("your profile has been updated") and the jobs.
**Upstream must show:** `location` changed to the new city, **and everything
else untouched** — name, age, gender, workExperience, languageSpoken. The
upstream merges a partial `item_state`; a write that blanks other fields is a
failure even though the city is right.
**The written value is canonical ENGLISH** — `Ghaziabad`, not `गाज़ियाबाद`. The
job search matches on it, so a Hindi value saved here stops the caller's own
profile finding them work.
**Guards:** #514.

### U3 — the change is named but the save is declined
> हाँ / नहीं / प्लंबर / नहीं / हाँ

**Expect:** nothing written — the stored trade is unchanged. But the search
still uses the trade they just named: a "नहीं" refuses the saving, not the
asking.
**Guards:** #514, decline case 3.

### U4 — "change something", but nothing named
> हाँ / नहीं / कुछ नहीं, रहने दीजिए

**Expect:** the what-to-change question, then the search on the stored values.
No write, no second attempt to ask.

### U5 — an update asked for mid-call
From the job list or the apply step: "मेरी प्रोफ़ाइल अपडेट कीजिए" ("update my
profile").
**Expect:** the same confirm-then-write sequence, and afterwards the caller is
returned to where they were, not dropped back to the start.

### U6 — both questions are spoken word for word
Run U2 four times.
**Expect:** "क्या बदलना है — काम या शहर?" and
"काम <trade> — प्रोफ़ाइल में अपडेट कर दूँ?" are **identical every
run**, with "शहर <city> और " in front whenever the caller has named a city.
The saved location is never spoken — it can be a full postal address.
Any rewording, a second question in the same turn, a feminine form
addressed to the caller, or any claim that the profile was updated before the
tool has run, is a failure — these two lines are spoken by the orchestrator
and the model never composes them.
**Guards:** #514; the three defects that made this flow unusable when the
model wrote these turns.

### U7 — a duplicate profile is never created
After U1 to U5 on one number.
**Expect:** still exactly one live profile. An update must never fall back to
a create.
**Guards:** F16, #496.

---

## C. Terminal paths — the caller must be told WHY

### B1 — under-age caller states their age
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए / हाँ अनुमति है /
> **मेरी उम्र सोलह साल है**

**Expect:** routing `next_subagent=u18_blocked`, and the caller HEARS the
reason: applications cannot be completed by phone under eighteen, register on
the portal with a parent or guardian.
**Fails if:** the reply is only "धन्यवाद".
**Guards:** **F10, F32.** Also check the log says `u18_blocked` — a correct
route with a generic goodbye is a different bug from a wrong route.

### B2 — permission declined twice
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए /
> नहीं, मुझे अनुमति नहीं देनी / नहीं, बिल्कुल नहीं

**Expect:** the first refusal is answered with what refusing costs — details
cannot be saved, no application can be sent — and the question is asked ONCE
more. The second refusal closes the application path, not the call: the
caller is told nothing was saved and asked whether they want anything else on
jobs or training. Not just "धन्यवाद".
**Fails if:** the first refusal ends the call, or the bot says the call is
ending and then carries on.
**Guards:** **F10, F32.**

### B2b — permission granted at the second ask
Same as B2, but answer the second ask with "ठीक है, अनुमति है".

**Expect:** the call continues into age and name, the profile is created, and
upstream shows `profile_consent_accepted: true` with all three compliance keys
true. `consent_declines` stays 1 — the earlier refusal is still on record.

### B2c — a second refusal offers help instead of hanging up
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए /
> नहीं / नहीं / **और नौकरियाँ दिखाओ**

**Expect:** the second refusal is answered with "nothing has been saved" and a
question — anything else on jobs or training, or shall I end the call. The
call does NOT end there. Asking for more jobs returns a real list.
**Fails if:** the bot says goodbye on the second refusal, or the question is
open enough that the caller asks for something outside jobs and training.

### B2d — the caller changes their mind after refusing twice
Continue B2c: pick a job and ask to apply.

**Expect:** the permission question is put again, in the same words as the
first time. Answering "हाँ" creates the profile and sends the application;
upstream shows all three compliance keys true.
**Fails if:** the bot refuses to ask, or tells the caller what they would
rather not give.

### B2e — refusing again costs only one question
Continue B2d, but refuse the permission question instead.

**Expect:** straight back to "nothing has been saved — anything else?" with NO
second re-ask. Saying "नहीं, बस" ends the call. Nothing upstream.
**Fails if:** the re-ask runs a second time, or the call loops.

### B2f — the offer after a refusal reaches training, not just jobs
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए /
> नहीं / नहीं / **मुझे ट्रेनिंग चाहिए**

**Expect:** a real organisation list, the same as any other services request.
Browsing training needs no permission, so the earlier refusal changes nothing
here.
**Fails if:** the refusal closes the services path too.

### B2g — nothing else wanted after a refusal
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए /
> नहीं / नहीं / **नहीं, बस धन्यवाद**

**Expect:** the call ends there, politely, and `end_session` fires. Nothing
upstream: no participant, no profile, no application.
**Fails if:** the bot asks again, or the call hangs open after the goodbye.

### B3 — "पता नहीं" is not a refusal (trade question)
> नमस्ते / **पता नहीं**

**Expect:** the trade question is asked again. The call does NOT end.
**Guards:** **F14.** This one dropped live calls.

### B3b — "पता नहीं" is not a refusal (permission question)
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला / हाँ भेज दीजिए / **पता नहीं**

**Expect:** the permission question is put again, more plainly. The call does
NOT end, and nothing is recorded as either granted or declined — the flags
stay false and `consent_declines` stays 0. An unclear answer is not a refusal.
**Guards:** **F14**, and the consent gate.

### B4 — explicit goodbye mid-call
Any point after turn 1: "बस इतना ही, मैं फोन रखता हूँ".
**Expect:** the call closes. Exactly one closing line, not two.
**Guards:** F27, the `ended` routing from every phase.

---

## D. Job search and selection

### C1 — pay figures are read exactly
After a job list, compare every spoken figure with upstream:
```bash
curl -s -X POST 'https://dev-signals.serveirc.com/signals-search/v1/search' \
  -H "x-api-key: $BLUE_DOTS_SEARCH_API_KEY" -H 'Content-Type: application/json' \
  -d '{"context":{"messageId":"t","networkId":"blue_dot","domain":"provider","itemType":"job_posting_1.0"},
       "message":{"intent":{"textSearch":"welder jobs in Bengaluru"},"pagination":{"limit":5}}}'
```
**Expect:** every min and max matches. A **stipend** is called स्टाइपेंड, never
सैलरी. A job with no pay field is reported as "pay not listed", not given one.
**Known failing:** Aditya Birla Fashion Kengeri (25,755–37,121) has been spoken
as "पच्चीस से इकतीस हज़ार" (25–31k) on three separate runs. Also seen:
"अड़तीस से चौबीस हज़ार" — a range running backwards.
**Guards:** **F5.**

### C2 — more jobs means different jobs
> …job list shown… / **और नौकरियाँ दिखाइए**

**Expect:** a different page. Not the same three again.
**Guards:** **F19.**

### C3 — apply to exactly the job picked
> पहला वाला / हाँ भेज दीजिए

**Expect:** ONE `apply_job` call, carrying the picked job's `job_item_id`.
```bash
docker logs dpg_agent_core --since 5m 2>&1 | grep -E "tools=\['apply_job|stream_tool_call_cap"
```
**Fails if:** several `apply_job` calls with different ids, or any
`stream_tool_call_cap` warning. The cap containing it is not a pass — it means
the model tried to apply to jobs the caller never chose.
**Guards:** **F28, G1.**

### C4 — changing the pick
> पहला वाला / **नहीं नहीं, दूसरा वाला** / हाँ भेज दीजिए

**Expect:** the application goes to the SECOND job. The call does not derail or
hang up.
**Known residual:** it applies on the correction turn without waiting for
confirmation, so the following "हाँ भेज दीजिए" lands on an already-sent
application.
**Guards:** **F12.**

### C5 — the city actually searched
Ask for a city, then check the spoken locations against upstream.
**Expect:** jobs in the caller's city.
**Known failing — upstream, not the agent:** search ranks by city but never
filters. A Delhi caller was read three Bengaluru jobs (0.571/0.560/0.538) while
the two real Delhi jobs scored 0.535/0.531 and were never spoken.
**Guards:** **F23, F1, F4, F9.** Raise with the search team, not fixable here.

---

### C6 — picking a job that was listed on an EARLIER turn
> ... / बेंगलुरु *(bot lists jobs)* / पहला वाला

The pick arrives on the turn **after** the list. Resolving it needs the
`fetch_jobs` result to have survived the turn, via the tool-result store.

**Expect:** `intent=job_pick`, `selected_job_item_id` set in session, routing
moves on to `profile_setup` (new caller) or `apply_confirm` (profile exists).
**Verify:**
```bash
docker exec dpg_redis redis-cli HGET "session:<phone>:<call_id>" selected_job_item_id
docker logs dpg_agent_core --since 5m | grep -E 'acts=|resolver_miss'
```
**Known failure mode:** with `TOOL_RESULT_KEY_SECRET` unset the store is
disabled, the offered list is empty by the next turn, the option cannot resolve
(`resolver_miss:no_options`), and the turn degrades to `any_input` — the caller
is stuck in `job_match` and **no profile and no application are ever created**,
while the bot speaks as though both happened. The NLU is not at fault here: it
returns `act=select`, `pending=select_job` correctly.
**Guards:** **F53**. Measured: `job_pick` 0 → 4 and applications 0 → 3 on an
identical build by setting that one variable.

### C7 — only the caller's own city is offered
> ... / वेल्डर / गाज़ियाबाद

`fetch_jobs` ranks but never filters: it returns its best five for any query.
Measured, "welder in Ghaziabad" came back with one Ghaziabad row and four from
Muzaffarnagar, Kanpur and Lucknow.

**Expect:** only the rows actually in the caller's city are read out. One
match means one job is offered, not three. Rows from elsewhere are not
mentioned in this turn at all.
**Check:** compare each company named against the caller's city.
**Known failure mode:** the list is padded back to three with other cities,
which is how "आपके लिए गाज़ियाबाद में डेटा एंट्री की नौकरियाँ हैं" came to be
said over two Muzaffarnagar rows and one Lucknow row.
**Guards:** 11 Oct P0-3.

---

### C8 — no job in their city: one alternative, and the answer resolves
> ... / सिक्योरिटी गार्ड / गाज़ियाबाद / हाँ बताइए

**Expect:** two sentences and a stop — there is nothing in their city, and a
yes/no question naming ONE other city taken from the rows already held
("बेंगलुरु में एक है, बताऊँ?" — "there is one in Bengaluru, shall I tell you?").
The turn ends on the question. "हाँ" then reads that row out with no new
search; "नहीं, दिल्ली में देखिए" searches Delhi instead.
**Check:** the bot does not read the row out in the same turn as the question,
and a bare "हाँ" does not make it repeat the question.
**Known failure mode:** offering a three-way choice (another city, another
trade, or training) instead. A bare "हाँ" answers none of the three, routing
has nothing to act on, and the phase asks the identical question again.
**Guards:** needs the `offer_other_city` pending; without it the answer falls
to `select_job`, resolves against nothing, and loops.

---

### C9 — a trade the network does not hold
> ... / पायलट / गाज़ियाबाद

**Expect:** said plainly that there is no such work, then the kinds of work we
DO hold are named and offered as a question. No row is presented as though it
were the trade they asked for.
**Check:** the reply names the other roles explicitly rather than listing them
as if they matched.

---

---

### C10 — a job list must not arm the apply confirmation

Caller reaches a job list of two or more rows.

The closing question is "इनमें से किसके बारे में और जानना चाहेंगे?"
("which of these would you like to know more about?"), never
"किसके लिए आवेदन करना चाहेंगे?" ("which would you like to apply for?").

The second wording is matched by the `submit_confirm` pending, so it turns
the caller's next "हाँ" into consent to apply before they have picked a job.
Check the reply after a bare "हाँ": it must not be
"क्या मैं आवेदन भेज दूँ?" ("shall I send the application?").

### C11 — every row names its own city

Caller asks for a trade in a city where only some rows match.

Each row read out carries its own city. A single city named once for the
whole list ("गाज़ियाबाद में दो जॉब्स हैं — पहला …, दूसरा …") is a failure
whenever one of the rows is somewhere else: it tells the caller a job is in
their city when it is not.

## S. Support services

The services path is reached ONLY when the caller asks for help, or answers the
no-jobs question with the third option. It is never entered by a catch-all —
the phase that preceded it was, and it swallowed goodbyes.

One result is one ORGANISATION offering several things, not one course. The
caller picks an organisation and we pass their enquiry to it with
`connect_service` — the network schema defines `apply` for seeker→provider
only; seeker→service_provider is `connect`.

### S1 — new caller asks for training
> नमस्ते / मुझे ट्रेनिंग चाहिए / पहला वाला / हाँ भेज दीजिए /
> हाँ अनुमति है / अट्ठाईस साल / मेरा नाम विकास है / बस इतना ही धन्यवाद

**Expect:** a list of organisations, each with its name, what it offers and
what it costs → profile saved → **asked** whether to pass the enquiry on →
`connect_service` → "आपकी बात <संस्था> तक पहुँचा दी गई है".
**Check upstream:** `POST /api/v1/action/perform` 201, and session
`service_enquiries_submitted` = 1 with `applications_submitted` = 0.

### S2 — returning caller asks for training
Call S1's number again on a new `call_id`, ask for training, pick one.
**Expect:** no re-asking of name or age; the enquiry still **asked for** before
it is sent. This is the case that fails first — nothing stands between the pick
and the confirmation when there are no details left to collect.

### S3 — the cost is always spoken
**Expect:** every organisation read out carries its cost, in Hindi — मुफ़्त /
सरकारी सहायता से / इसके पैसे लगेंगे. The upstream values are `Free`,
`Subsidised / Government-funded` and `Paid`; a row with no cost is passed over
in silence, never guessed at.
**Guards:** a caller sent to a paid course without being told cannot undo it.

### S4 — contact details are never read out
**Expect:** no phone number, no email, no offer to pass one on. They are masked
upstream ("9***", "b***@yopmail.com") and deliberately not projected.

### S5 — naming an organisation is not asking to contact it
> … मुझे ट्रेनिंग चाहिए / दूसरा वाला सुनाइए

**Expect:** the second organisation is described and the caller is **asked**.
Nothing is sent on that turn.

### S6 — services → jobs, mid-call
> … मुझे ट्रेनिंग चाहिए / पहला वाला / नहीं, नौकरी ही दिखाइए /
> वेल्डर / गाज़ियाबाद / पहला वाला / हाँ भेज दीजिए

**Expect:** a real job **search** runs, and the pick applies to a JOB.
**Guards:** the list on offer is dropped when the caller turns away. Without
that, "पहला वाला" resolved against the organisations and sent an enquiry to a
training centre the caller had just declined.

### S7 — jobs → services, mid-call
> … वेल्डर / गाज़ियाबाद / पहला वाला / नहीं, ट्रेनिंग दिखाइए / पहला वाला

**Expect:** a real services **search** runs, and the pick sends an enquiry.
**Guards:** the mirror of S6. This half was live on the job path before the
services path existed — an ordinal after "show me training" applied to a job.

### S8 — a goodbye inside the services path
**Expect:** the same close as anywhere else — "क्या मैं कॉल यहीं ख़त्म करूँ?",
then the call ends on yes. The services phase must not be a place goodbyes go
to die.

### S9 — no jobs offers help as the third option
> … वेल्डर / पोर्ट ब्लेयर

**Expect:** "क्या मैं पास के किसी शहर में या किसी और ट्रेड में देखूँ, **या
आपको ट्रेनिंग जैसी कोई मदद चाहिए?**" — all three, and the tool fires only if
they choose the third.
**Note:** semantic search rarely returns zero today, so this fires mainly once
the wrong-city filtering lands. The answer is resolved either way.

### S10 — a service the network does not hold
> ... / मुझे मोबाइल रिपेयरिंग का कोर्स चाहिए

The whole inventory is a handful of organisations. A request it does not cover
still returns the top rows, because the search ranks and does not filter.

**Expect:** said in one sentence that we have nothing for what they asked for,
then what we do have offered as a question. Welding training is never read out
as though it answered a question about mobile repair.
**Check:** `fetch_services` ran, and the organisations named exist in the
result. A reply that names plausible-sounding organisations without the tool
having run is an invention, not a match.
**Guards:** rows below the `match_score` threshold in `result_shaping` are
dropped before the model sees them. If the inventory changes, re-measure that
threshold — a real match scored 0.578-0.659 and a non-match 0.498-0.527.

---

### S11 — "I don't want a job" opens the help offer
> ... / मुझे नौकरी नहीं चाहिए

**Expect:** not treated as a goodbye and not as an opt-out from being called.
The caller is told what else we have — training, career advice, interview
preparation — and asked whether any of it is useful. A yes routes to
`service_match`; a no closes the call cleanly and the offer is never repeated.
**Check:** "नहीं" does not produce the same question a second time.
**Known failure mode:** closing with "अब हमारी तरफ़ से कॉल नहीं आएगा" ("we won't
call you again") to a caller who dialled us.

---

---

## K. Permission — asked only when something is written

Permission is not asked at the start of the call. It is asked at the point the
upstream is about to be written to, and only for what is still missing. A
caller who just wants to hear what jobs exist is never asked at all.

**Check what the caller's record already holds before every run:**

```bash
curl -s "https://dev-signals.serveirc.com/api/v1/admin/participant?phone_number=<phone>" \
  -H "x-api-key: $BLUE_DOTS_API_KEY" -H "x-acting-org-id: $BLUE_DOTS_ORG_ID" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
print({c['key']:c['value'] for c in d.get('compliance') or []})
print([(i['lifecycle_status'], i.get('profile_consent_accepted')) for i in d.get('items') or []])"
```

And what the agent made of it:

```bash
docker exec dpg_redis redis-cli HMGET "session:<phone>:<call_id>" \
  user_terms user_privacy profile_consent has_age consent_declines consent_return_to
```

### K1 — the first turn is the same every time
Call on a number with no profile. Run it several times.

**Expect:** turn 1 is the trade question, spoken verbatim, identical on every
run — "नमस्ते! ब्लू डॉट्स में आपका स्वागत है। आप किस काम या ट्रेड में नौकरी
ढूंढ रहे हैं?" No greeting variation, and no model call for that turn.
**Fails if:** it asks for permission, an age, or a name. Measured before this
was fixed: 13 of 30 first turns asked for permission or age.
**Verify:** `docker logs dpg_agent_core --since 5m | grep llm_calls` — the turn
must not have one.

### K2 — browsing jobs never asks for permission
> नमस्ते / वेल्डर / बेंगलुरु / पहला वाला *(stop before applying)*

**Expect:** trade, city, a job list and a job description, with no permission
question anywhere. Session flags all still false.

### K3 — permission is asked at the apply, not before
> ... / पहला वाला / हाँ भेज दीजिए

**Expect:** the reply to "हाँ भेज दीजिए" IS the permission question, spoken
verbatim: "आगे बढ़ने से पहले एक बात — क्या मुझे आपकी जानकारी सेव करने और आपकी
ओर से आवेदन भेजने की अनुमति है?" Session shows
`consent_return_to: profile_setup`.
**Fails if:** that turn says an application was sent. Nothing has been sent —
the phase holds no tools — and saying so is the worst failure on this path.

### K4 — the four entry points all gate
Reach the apply from each of: the job list, the job description, the apply
confirmation, and a services pick.

**Expect:** each one routes to the permission question when any of
`user_terms`, `user_privacy`, `profile_consent`, `has_age` is false, and
straight on when all four are true.

### K5 — a returning caller with current permission is never asked
Use a number whose compliance shows all keys true and whose live profile has
`profile_consent_accepted: true`.

**Expect:** the whole journey to a submitted application with no permission
question. `applications_submitted` 1, and a real application id.

### K6 — permission granted
At the permission question, answer "हाँ अनुमति है".

**Expect:** `user_terms`, `user_privacy`, `profile_consent` and `consent_given`
all become true in session, and the next question explains itself — that a
profile has to be created before the application can go, then asks the age.
**Fails if:** the age is asked with no reason given. The caller has just asked
to apply and cannot tell why it stalled.

### K7 — only an answer to the permission question counts
Answer "हाँ" to any OTHER question in the call — the trade question, a job
question, the saved-details offer.

**Expect:** nothing is recorded. `user_terms`, `user_privacy` and
`profile_consent` stay false.
**Guards:** the finding that an acknowledgement of something else was being
stored upstream as consent to everything.

### K8 — what reaches the upstream is what the caller agreed to
Complete a journey that creates a profile.

**Expect:** the participant write carries `user_terms`, `user_privacy` and
`profile_creation` taken from the call, and no `terms_accepted` or
`privacy_accepted` fields at all.
**Verify:** the created item is `live` with `profile_consent_accepted: true`.
**Guards:** the hardcoded `true` that used to be sent for every caller.

### K9 — blocked at the start when the upstream refuses
Use a number the participant lookup rejects with `U18_NOT_ALLOWED`.

**Expect:** the call ends on turn 1 with the reason spoken, and `end_session`
fires. Session shows `fetch_error: U18_NOT_ALLOWED`.
**Fails if:** the caller is taken through the journey and fails at the save.

---

## E. Conversation robustness

### D1 — a question outside the script
Mid-flow: "ये जगह मेरे घर से कितनी दूर है?"
**Expect:** an honest answer or an honest "I don't have that", then the flow
continues. Never silence, never the previous question repeated.
**Guards:** **F15.**

### D2 — a question about a listed job
Mid-flow: "पहली वाली नौकरी के बारे में और बताइए"
**Expect:** answered from the job list already fetched — nature of job, pay,
experience. The search is NOT restarted.
**Guards:** **F6.**

### D3 — vague answers are not stored
> कुछ भी काम चलेगा / आसपास कहीं भी

**Expect:** not stored as trade or city. The bot asks again or asks for
something concrete.
**Guards:** **F7.**

### D4 — a correction mid-flow
> मैं इलेक्ट्रीशियन का काम ढूंढ रहा हूँ / **इलेक्ट्रीशियन नहीं, वेल्डर**

**Expect:** the search uses welder.
**Guards:** the turn-assembler path and trade overwrite.

### D5 — the answer to a different question
Asked for age, caller gives a name or a trade.
**Expect:** the answer is captured, and the bot asks for what is still missing.
Age is genuinely required, so it will keep asking for age — but it must not
discard what WAS given.
**Guards:** F31 (verified not a bug — age is required; this checks the capture).

### D6 — no empty turns
Across a whole run, no turn may produce no text.
```bash
docker logs dpg_agent_core --since 10m 2>&1 | grep 'stream_empty_turn'
```
**Expect:** no hits. If any, the backstop should have spoken
`empty_response_message` rather than leaving silence.
**Guards:** **F3.**

### D7 — an ordinal beyond the list
Three jobs read out, caller says "पाँचवाँ वाला" (the fifth).
**Expect:** "पहला, दूसरा और तीसरा ही विकल्प हैं" — the real options restated.
Never an invented fifth job.
**Guards:** F11.

### D8 — a name correction mid-flow
> मेरा नाम रमेश है / **नाम रमेश नहीं, सुरेश है**

**Expect:** the profile is saved as सुरेश. ONE profile, not two.

### D9 — age at the boundary
Reach the age question through the apply (as A1 does), then run three times:
**सत्रह** (17), **अठारह** (18), **उन्नीस** (19).
**Expect:** 17 and 18 are blocked, 19 proceeds. The blocked reply must say
**eighteen**, never "fifteen" or "nineteen" — both have been spoken by mistake
and each tells the caller the wrong time to come back.
**Known gap:** the upstream refuses 18 as well as 17, so the gate blocks under
19 while the spoken line says eighteen. An 18-year-old is correctly stopped but
hears a threshold that does not describe them. Resolve when "minor/major"
replaces the numbers.
**Guards:** the u18 threshold wording.

### D10 — age given in words, not digits
> मैं पैंतीस साल का हूँ

**Expect:** stored as `age 35`.

### D11 — no jobs found
Ask for a trade and city with no matches.
**Expect:** the caller is told plainly that nothing was found, and offered a
next step. Never an invented listing, never silence.
**Guards:** F11, F10.

### D12 — applying twice to the same job
Apply, then ask to apply to the same job again.
**Expect:** the caller is told it is already in — not a second application and
not an error read aloud.
**Guards:** G6 (the 422 duplicate path), F21.

### D13 — language switch mid-call
Start in Hindi, then speak English.
**Expect:** the agent follows and stays in the new language until switched
back.

### D14 — a language we do not support
**Expect:** `unsupported_language_message` — we support Hindi and English only.
Gujarati must not appear anywhere.
**Guards:** the #419 language cleanup, E3.

### D15 — an unintelligible turn
Send garbled text (e.g. "एक मिनट एलेक्ट्रीशियनली").
**Expect:** a clarifying question, not a guess and not silence.

---

## F. Language and voice

### E1 — the bot speaks as a woman about itself
**Expect:** feminine first person — "मैं देखती हूँ", "मैं भेज रही हूँ".
**Guards:** the #419 rule.

### E2 — the bot does NOT assume the caller's gender
**Expect:** neutral polite second person — "आप ... ढूंढ रहे हैं?", "चाहेंगे?".
**Fails if:** "बता सकती हैं?", "चाहेंगी?" — the caller's gender is never known.
**Known intermittent:** one violation observed in ~12 opportunities.
**Guards:** **F30.**

### E3 — Hindi only, no Gujarati
**Expect:** Hindi, or English if the caller switches. Never a third language.
**Guards:** the #419 language cleanup.

### E4 — names and companies are NOT translated
**Expect:** "Aditya Birla Fashion Kengeri" spoken as-is (Devanagari
transliteration is fine — pronunciation is identical). Not translated into
Hindi words.
**Guards:** F13 — ruled NOT a defect; this scenario prevents an over-correction.

### E5 — company suffixes are spoken in full
> ... / सिक्योरिटी गार्ड / बेंगलुरु

Employer names arrive as "Pvt Ltd". Rendered in Devanagari as "प्रा० लि०", the
"०" is a full stop, not a zero — but the spoken-output guard translates
Devanagari digits to ASCII and the number-to-words step then says it.

**Expect:** "प्राइवेट लिमिटेड".
**Known failure mode:** "प्राशून्य लिशून्य" — literally "pra-zero li-zero".
**Guards:** 11 Oct P2.

---

---

## G. Truthfulness

### F1t — nothing is claimed before its tool result
**Expect:** no "आपकी जानकारी सेव हो गई" or "आवेदन भेज दिया गया है" before the
tool returns. Progress narration like "मैं आपकी एप्लीकेशन भेज रही हूँ" is also
banned — it reads as a completed fact.
**Verify:** compare the spoken claim against the tool result in
`recent_tool_exchanges` and `last_application_id`.
**Guards:** **F2.**

### F2t — a refusal or error is reported honestly
Force a failure (e.g. apply before any save, so `profile_item_id` is ungrounded).
**Expect:** the caller is told plainly that it did not go through and what
happens next.
**Known failing:** the caller currently hears "मुझे आपके आवेदन के लिए सही
जानकारी नहीं मिल पा रही है" and the journey dead-ends with no application.
**Guards:** **F2**, and the save-before-apply gap found 1 Oct.

### F3t — no invented job listings
**Expect:** every company and role spoken appears in the `fetch_jobs` result.
**Guards:** F11.

### F4t — no technical errors spoken to the caller
**Expect:** never "तकनीकी समस्या".
**Guards:** F21.

---

### F5t — no invented profile fields
Run A1 but have the caller mention **only** name, trade and city — never work
history, job type or help needed.

**Expect:** the `save_profile` call carries only what was actually said.
**Verify:** compare the tool input against `slot_provenance` in the same session:
```bash
docker exec dpg_redis redis-cli HGET "session:<phone>:<call_id>" slot_provenance
docker exec dpg_redis redis-cli HGET "session:<phone>:<call_id>" recent_tool_exchanges
```
Every key in the `save_profile` input must appear in `slot_provenance` (or be a
required field).
**Known failure mode:** a caller who said only name, trade and city was sent
with `work_experience="Returning after a break"`, `experience_years="1 Year"`
and `job_nature="Full-time"` — none uttered, all three empty in session. A
prompt rule forbidding this already existed and was ignored.
**Guards:** **F56**. The `session_only_params` contract now takes these from
session or drops them, so invention is structurally impossible.

### F6t — optional array fields are accepted when volunteered
Have the caller volunteer a job type unprompted ("फुल टाइम चाहिए").

**Expect:** the save succeeds (201/200), and the profile upstream carries
`natureOfJobsInterestedIn` as an **array**.
**Known failure mode:** sent as a bare string, the upstream rejected the WHOLE
write with `400 INVALID_ITEM_STATE: "must be array"` — so one optional field the
model chose to fill lost the entire profile. The `help_needed` enum offered to
the model also listed four values that do not exist upstream, making a failed
write the expected outcome whenever that field was used.
**Verify:** `curl .../admin/participant?phone_number=<phone>` and read
`item_state.natureOfJobsInterestedIn`.
**Guards:** **F52.**

### F7t — a caller with no profile is never told they have one
Call on a number with **no** profile at all.

**Expect:** the bot asks for trade and city. It must not describe a saved city,
trade or employer.
**Known failure mode:** a first-time caller was told "आपकी जानकारी में बेंगलुरु
है" ("your information has Bengaluru") — a city nobody in that call had said,
with `stored_location` and `profile_item_id` both empty.
**Guards:** **F45.**

---

## I. Human handoff

### H1 — the caller asks for a person
> ... / मुझे किसी इंसान से बात करनी है

**Expect (handoff enabled, `identity.human_handoff: request`):** a FIXED line
from config, prefixed with the AI disclosure, and **no LLM call** for that turn.
Trust mints a ticket and the webhook receives **one signed payload**.
**Verify:**
```bash
docker logs dpg_agent_core --since 5m | grep -E 'next_subagent=handoff|llm_calls'
```
`llm_calls` must be `None` — the line is spoken by the framework, never written
by the model.
**Guards:** the handoff contract (#437, #457).

### H2 — asking twice in one call sends one payload
Ask for a person three times in the same `call_id`.

**Expect:** the first request delivers; the second and third speak the `already`
line and send **nothing**. Exactly one webhook payload for the call.
**Verify:** count requests at the receiver before and after.
**Guards:** #446's "one signed payload per handoff call".

### H3 — handoff off is the shipped default
With `identity.human_handoff: none` (what Blue Dots ships).

**Expect:** the caller hears the `no_handoff_line` — no human is available,
the bot continues to help. Nothing is escalated, no webhook call.

### H4 — a failed delivery is reported honestly
Point `HITL_WEBHOOK_URL` at a URL that 404s.

**Expect:** the bot speaks the `failed` line ("अभी मैं आपकी बात आगे नहीं भेज
पाई") — never the delivered line. `escalate` returns
`delivered:false, reason:http_404`.
**Guards:** F2 (nothing claimed before its result).

### F8t — an earlier application is never described
> ... *(bot lists jobs)* / पिछली बार जो अप्लाई किया था उसका क्या हुआ

No tool reads a caller's past applications.

**Expect:** said in one sentence that it cannot be checked on this call, then
the conversation carries on.
**Known failure mode:** "कंपनी आपसे संपर्क करेगी" ("the company will contact
you") — which asserts both that an application exists and that someone will
act on it, having read neither. Being ignored outright is the other failure.
**Guards:** 11 Oct P1-7.

---

---

## H. Performance (record, do not gate on)

Per turn, from the orchestrator log:

| metric | current | note |
| --- | --- | --- |
| NLU | 1.2–1.8 s | blocking; routing waits on it |
| LLM #1 | 0.9–2.5 s | |
| LLM #2 | 1.0–2.4 s | skipped now on `end_session`-only rounds |
| first_sentence | 2.0–5.0 s | telephony aborts around 2.2 s |
| cache hit rate | 82–83% | `dpg_agent_core_llm_call_cache_read_tokens_*` |

Outliers up to 22 s have been seen and are provider tail latency, not our code
— one call had two, the next had none.

---

## Deferred — not expected to pass yet

| # | what | why |
| --- | --- | --- |
| F26 / F24 | #204 short-circuit never fires (`turn_count` never written) | changes when we hang up; needs its own test pass |
| F20 | minimum-salary filter | a valueless filter is an upstream 400; parked |
| G3 / G4 | barge-in socket + interrupted turn | needs a real mid-turn interrupt; harness is sequential |
