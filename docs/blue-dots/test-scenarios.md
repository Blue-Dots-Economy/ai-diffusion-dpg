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
> नमस्ते / हाँ ठीक है / मेरा नाम अजय सिंह है / अट्ठाईस साल /
> मैं वेल्डर का काम ढूंढ रहा हूँ / बेंगलुरु / पहला वाला / हाँ भेज दीजिए / बस इतना ही

**Expect:** consent → name → age → trade → city → job list → detail → save →
apply → goodbye. One profile created with the caller's real name. One
application with a real `action_id`.
**Guards:** F17, F18 (no placeholder name, no duplicate profile).

### A2 — returning user
Call A1's number again on a new `call_id`.
**Expect:** the stored trade and city are recognised and offered back; no
re-asking for name or age. Exactly one profile still.
**Guards:** F16, the profile-cap behaviour.

### A3 — returning user with a full address stored
Use a number whose stored `location` is a full postal address.
**Expect:** the bot speaks the **city only** — "आपकी जानकारी में बैंगलोर है".
Never a company name, cross road, sector or layout.
**Guards:** **F22**. Regression here means addresses are being read aloud again.

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
**Expect:** full onboarding — consent, name, age, trade, city. One live profile
created at the pick turn.
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

## C. Terminal paths — the caller must be told WHY

### B1 — under-19 caller
> नमस्ते / हाँ ठीक है / मेरा नाम राहुल है / **मेरी उम्र सोलह साल है**

**Expect:** routing `next_subagent=u18_blocked`, and the caller HEARS the
reason: applications cannot be completed by phone under nineteen, register on
the portal with a parent or guardian.
**Fails if:** the reply is only "धन्यवाद".
**Guards:** **F10, F32.** Also check the log says `u18_blocked` — a correct
route with a generic goodbye is a different bug from a wrong route.

### B2 — consent declined
> नमस्ते / नहीं, मुझे अपनी जानकारी सेव नहीं करवानी

**Expect:** the caller is told nothing was saved and that applications cannot
be sent without consent. Not just "धन्यवाद".
**Guards:** **F10, F32.**

### B3 — "पता नहीं" is not a refusal
> नमस्ते / **पता नहीं**

**Expect:** the consent question is asked again. The call does NOT end.
**Guards:** **F14.** This one dropped live calls.

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
Run three times: **अठारह** (18), **उन्नीस** (19), **बीस** (20).
**Expect:** 18 is blocked (upstream treats the whole 18th year as under-age),
19 and 20 proceed. The blocked reply must say **nineteen**, never "fifteen" or
"eighteen" — both have been spoken by mistake and each tells the caller the
wrong time to come back.
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
| F23 | wrong-city results | search-layer, raise with that team |
| G3 / G4 | barge-in socket + interrupted turn | needs a real mid-turn interrupt; harness is sequential |
