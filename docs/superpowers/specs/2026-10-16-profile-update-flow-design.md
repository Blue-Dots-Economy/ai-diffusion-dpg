# Profile update flow — design

Issue: #514 (under #508). Date: 16 Oct 2026. Status: **for approval, not yet built.**

## The problem

A returning caller's profile is never written to during a call. Call `7cc6d429`
(11 Oct): the caller had Okhla, Delhi on file, searched and applied for data
entry in Ghaziabad, and on the next call the bot still said Okhla, Delhi.

Two reasons, both in our config, neither upstream:

1. `save_profile` is held by one subagent only — `profile_setup` — and that
   phase is reached only when the caller has **no** live profile.
   `profile_resolve` routes profile holders straight to `job_match`. A returning
   caller therefore never reaches a phase that can write.
2. The connector's `invocation_rules.call_when` tells the model the call is
   "always a create" and to "OMIT profile_item_id".

## What the upstream actually supports

Verified against UAT on 16 Oct by creating a profile, posting a partial
`item_state` with `item_id`, and reading it back.

- **Partial update works and merges.** Sending only `location` and
  `nameOfJobRolesInterestedIn` left `gender`, `age`, `workExperience`,
  `languageSpoken` and `natureOfJobsInterestedIn` untouched.
- The existing comment "the upstream does NOT support partial updates — pass
  the full item_state on every call" is **wrong** and is corrected by this work.
- `POST /api/v1/admin/participant` with `item_id` is the update; without it, a
  create. No separate endpoint, no new upstream surface.

### A schema mismatch found on the way

`highestQualificationOrSkill` is not a property of the live
`blue_dot/seeker/profile_1.0` schema. Sending it fails the **entire** write with
`400 INVALID_ITEM_STATE — must NOT have additional properties`. It is only sent
when the model fills `qualification`, which is why it has not surfaced. The
field is dropped by this work; choosing a correct replacement is out of scope.

## The flow

### Entry 1 — from the profile offer

```
BOT     मुझे आपकी जानकारी मिली — दिल्ली में इलेक्ट्रीशियन।
        क्या इन्हीं से नौकरियाँ ढूंढूँ?
        (I found your details — electrician in Delhi. Shall I search with these?)

CALLER  हाँ                        → job_match, unchanged
CALLER  नहीं / कुछ बदलना है        → profile_update, nothing named
CALLER  गाज़ियाबाद में देखिए        → profile_update, city already named
```

The question is deliberately **one clause**. The earlier draft read "…या कुछ
बदलना है?" ("…or change something?"), which cannot be answered yes or no — the
same defect as the three-way job question fixed in #522.

### Entry 2 — an explicit request, anywhere in the call

"मेरी प्रोफ़ाइल अपडेट कीजिए" ("update my profile") from `job_match`,
`apply_confirm` or `service_match` routes to `profile_update`, with
`update_return_to` recording where to go back to. Same pattern as the existing
`close_return_to`.

### Inside profile_update

```
nothing named yet
  BOT     क्या बदलना है — काम या शहर?
          (What would you like to change — the trade or the city?)
  CALLER  कुछ नहीं, रहने दीजिए      → leave, no write, search on saved values
  CALLER  शहर                       → ask which, then confirm

change known
  BOT     शहर गाज़ियाबाद — प्रोफ़ाइल में अपडेट कर दूँ?
          (City Ghaziabad — shall I update your profile?)
  CALLER  नहीं                      → no write; search Ghaziabad anyway
  CALLER  हाँ                       → update_profile tool

after the tool
  ok      profile_just_updated = true  → job_match composes the same turn:
          आपकी प्रोफ़ाइल अपडेट हो गई है। <job list>
          (Your profile has been updated. <job list>)
  failed  अभी प्रोफ़ाइल अपडेट नहीं कर पा रही हूँ  → job_match with the new values
          (I can't update your profile right now)
```

Nothing is claimed before its result exists: the confirmation question states an
intention, the success line is spoken only after a successful result. This keeps
the existing hard rule intact.

A declined confirmation means **do not save**, not **do not search** — the
caller asked for Ghaziabad a turn earlier and only the saving is on the table.

## Capture versus write

Two mechanisms, deliberately not joined.

**Capture — always on, silent.** Any detail the caller states at any point is
extracted by the NLU and written to session (`entity_to_profile_field` →
`slot_writer`). This is today's behaviour and does not change. It causes no
write, no announcement, and no change to the conversation. A caller mentioning
something is not a request to change their profile.

**Write — only in `profile_update`,** entered only by the two routes above.

When a write does happen it carries **every captured field that differs from the
profile**, not only what was said in the last turn. A work-experience value
mentioned at turn 3 and left in memory is included in a write triggered at turn
9. If no update flow runs, nothing is written and the captured values are gone
at the end of the call — unchanged from today.

`age` is excluded from the write set even when it differs: a mid-call age could
move the caller across the U18 gate.

## What gets built

### 1. New connector `update_profile`

Same endpoint as `save_profile`. `profile_item_id` is `required: true`, so the
JSON Schema the provider enforces makes it impossible to call as a create.
`save_profile` is left untouched, keeping its create-only rules and its
`max_calls_per_turn` duplicate guard.

Two connectors rather than one because `invocation_rules` is per connector, not
per subagent. One connector cannot hold "always a create, omit the id" and
"always an update, the id is required" at once.

### 2. New subagent `profile_update`

- tools: `[update_profile]`
- pendings: `what_to_change`, `confirm_update`
- routing: back to `job_match`, or to `update_return_to` when set

### 3. `profile_resolve`

`fixed_opening` reworded to the single-clause question. A change request routes
to `profile_update`.

### 4. Supporting wiring

- intent `update_profile_request`, with `act_intents` rows and NLU examples
- `profile_just_updated` session flag, read once by `job_match` as an opening
  prefix — the same mechanism as `fixed_opening_prefix`
- `update_return_to` session field

### 5. Schema correction

Drop `qualification` from the `save_profile` body template, its connector
params, and the three places in `agent_core.yaml` that feed it.

## Out of scope

- Choosing the correct upstream field for qualification.
- Mid-call detail changes triggering a write on their own (#508 territory).
- Multiple profiles (#496) and re-hearing an earlier list (#513).

## Tests

Rows in the existing `test_pending_resolution` table for both new pendings, and
for the reworded `profile_resolve` opening. The pending collision in #522 showed
that this harness catches these in a second, where a phone call does not.
