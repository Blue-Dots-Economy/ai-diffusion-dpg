# Blue Dots — consent flow redesign

Date: 2026-10-27
Issue: ai-diffusion-dpg#488 (and its sub-issues #489, #490, #491)
Branch: `feat/488-consent-flow`

## Intent

Record only the consent the caller actually gave, and ask for it at the point
it is needed rather than at the start of the call.

Today the bot asks for consent as its first question, before the caller knows
what the service does. The answer is accepted loosely enough that an
acknowledgement of an unrelated question can be recorded as consent, and the
values sent upstream are hardcoded literals rather than anything the caller
said. This redesign moves the question to the moment an upstream write needs
it, drives the upstream payload from session state, and closes the gap that
lets a non-answer count as an answer.

Success looks like: no consent is sent upstream that the caller did not give;
a caller who only wants to browse jobs is never asked; a caller whose consent
is already on file at the current document version is not asked again; and a
returning minor is told at the start, not after a full conversation.

## What exists today

The fetch-and-store half already works. `session_bootstrap`
(`dev-kit/configs/blue-dots/agent_core.yaml:68-73`) runs `fetch_profile`
before turn-1 routing, and `seeker_fetch`'s `session_mapping`
(`dev-kit/configs/blue-dots/action_gateway.yaml:108-129`) lifts `has_age`,
`user_terms`, `user_privacy`, `profile_item_id` and the stored trade, city and
name into session state where routing can read them.

Three things are wrong with what happens next.

**Consent is asked first, and accepted too loosely.** The `consent` pending
(`agent_core.yaml:1944`) resolves on state alone — consent not yet given — so
it stays open across every turn of the `opening` phase. `PendingResolver`
returns the first candidate whose conditions hold
(`agent_core/src/understanding/pending.py:48`), and `accept_slots` only checks
the pending id (`agent_core/src/understanding/postprocess.py:101`). The result
is that a "हाँ" (*yes*) to the age question is recorded as consent. The same
defect exists in `profile_resolve`, where `use_saved_details`
(`agent_core.yaml:2445`) is keyed on an entry-count proxy.

**What goes upstream is hardcoded.** `save_profile`
(`action_gateway.yaml:301`) and `update_profile` (`action_gateway.yaml:512`)
each send five literals — three `compliance` entries fixed at `true`, plus
`terms_accepted` and `privacy_accepted`. None of them reflects session state.

**The Trust Layer gate is not what it appears to be.** `trust_layer/main.py:145`
constructs `BasicTrustLayer`, whose `check_consent` returns `True`
unconditionally (`trust_layer/src/guardrails.py:266`). Its SQLite consent store
resolves to `:memory:` (`guardrails.py:127`), and nothing writes to it for Blue
Dots because `ask_for_consent` is `false` (`agent_core.yaml:41`), so the only
caller of `/consent/verify` never runs. See "Trust Layer" below.

## The new flow

1. Turn 1: fetch the participant and store the compliance picture in session.
   Unchanged from today.
2. No consent question at the opening. The caller browses and searches jobs
   with no consent at all — no upstream write is involved.
3. At the point of an upstream participant write, check session state. Ask for
   whatever is missing, then perform the write.

Consent is needed at exactly two places: `save_profile` (create) and
`update_profile` (update). The other write connectors, `apply`
(`action_gateway.yaml:607`) and `connect` (`action_gateway.yaml:835`), send no
`compliance` — they are actions, not participant upserts.

## Design

### 1. A dedicated `consent` subagent

Non-terminal, no tools. Entered only when an upstream write needs consent the
session does not hold, and returns to whichever phase sent it via a
`consent_return_to` session field — the pattern `update_return_to` and
`close_return_to` already use.

It asks at most two questions:

- The consent question, always one question covering every consent needed.
  `user_terms` and `user_privacy` are a both-or-none pair upstream
  (`Signals-DPG/apps/api/src/routes/v1/admin/participant.ts:491`), so they
  cannot be asked separately, and asking a voice caller three legal questions
  in sequence is not usable.
- The age question, only when `has_age` is false.

The `consent` and `age` pendings move here from `opening`. Their ids are
unchanged, so the `accept_when_pending` scopes on the `consent_response` and
`age` slots (`agent_core.yaml:791`, `:800`) stay valid as written.

The consent question has two phrasings, chosen from session state:

- Nothing on file: one sentence covering terms, privacy, recording, and saving
  their details.
- Terms and privacy already accepted at the current version, only profile
  consent missing: one sentence about saving this profile. Re-reading the terms
  to someone who already accepted them is noise, and this is the common
  returning-caller case.

Both phrasings must contain the word "अनुमति" (*permission*). See §8.

### 2. State the subagent reads

All four are in session before turn-1 routing completes.

| Session field | Source | Status |
|---|---|---|
| `user_terms` | `compliance[key=user_terms].value` | exists |
| `user_privacy` | `compliance[key=user_privacy].value` | exists |
| `has_age` | `compliance[key=has_age].value` | exists |
| `profile_consent` | `items[lifecycle_status=live].profile_consent_accepted` | **new** |

`profile_creation` is deliberately absent from the GET's `compliance` array —
it is item-level and reported per item
(`Signals-DPG/packages/schemas/src/admin/participant.ts:200`), which is why the
new mapping reads it off the item rather than the array.

`profile_consent` must be declared in the session schema
(`dev-kit/configs/blue-dots/memory_layer.yaml`) as a 2-value enum
`["true", "false"]` with `default: "false"`, matching how `has_age`,
`user_terms` and `user_privacy` are declared. The session schema has no boolean
type; Memory Layer JSON-decodes on read, so routing compares against the YAML
boolean `true`. An undeclared field would have no default, and "absent" must
behave identically to "false" here — absent is what a caller with no live
profile produces, and that caller must be asked.

All four flags are version-scoped by Signals: `true` only when accepted at the
document version currently being served. A consent-document version bump
therefore sends every returning caller back through this subagent. That is the
intended behaviour, not an edge case.

### 3. Where the gate sits

Each of the two write phases is reached by a routing rule today. That rule
gains a sibling, placed above it, that routes to `consent` instead when any
required flag is false and sets `consent_return_to` to the phase it displaced.
`consent` routes back to that phase once every flag is true, so the write
happens on the original path rather than from inside the consent subagent:

- `profile_setup` → `save_profile` (create)
- `profile_update_confirm` → `update_profile` (update)

The update path matters in practice: a live profile whose `profile_consent`
has gone stale after a version bump needs consent before a city change can be
written.

### 4. Under-19

Two gates.

**At turn 1.** `GET /api/v1/admin/participant` answers `400 U18_NOT_ALLOWED`
when the stored age is 18 or under and the caller is `network_service` or
`voice` (`Signals-DPG/apps/api/src/routes/v1/admin/participant_read.ts:160`),
which Blue Dots is. Today that failure is swallowed:
`session_bootstrap.py:158-162` records `"failed"` and the call continues as if
the caller were new. A `session_mapping` entry with `on_error: true`
(`action_gateway/src/adapters/rest_api.py:848`,
`action_gateway/src/schema/config.py:256`) lifts the error code out of the 400
body into session, and routing sends the caller to `u18_blocked` before any
question is asked. Config only.

**At the age question.** An unknown caller who states an age under 19 routes to
`u18_blocked`.

A minor is `age <= 18` in Signals
(`Signals-DPG/apps/api/src/services/minor.ts`, `isMinor`): age is derived from
the birth year alone, so the whole boundary year is treated as under-age,
fail-closed. Adult at 19 and over. The existing "say NINETEEN" copy in
`u18_blocked` (`agent_core.yaml:2242`) is correct and unchanged.

`has_age` means only that an age is on file — it is literally `age != null`
(`participant_read.ts:488`). It implies adult for Blue Dots only because minors
are rejected earlier in the same request. That inference depends on the acting
org being `network_service`/`voice` and would not hold for an aggregator caller.

### 5. Decline

First decline: ask once more, saying the profile cannot be created and the
application cannot be sent without permission. Second decline: route to
`consent_declined`, which is terminal and ends the call. The two attempts are
counted with `subagent_entry_count.consent`, the same mechanism
`use_saved_details` uses today — no new session field. Both consents are
treated as one decision; there is no partial accept.

### 6. What `opening` becomes

It loses both questions and the routing rules keyed on `consent_response`,
`consent_given`, `has_age` and `age` (`agent_core.yaml:2072-2200`). It keeps
its greeting and routes onward. `consent_declined` and `u18_blocked` are
unchanged but are now reached from `consent` rather than from `opening`.

### 7. The upstream payload

In both `save_profile` and `update_profile`:

- Delete `terms_accepted` and `privacy_accepted`. They have been ignored since
  Signals #309 and were removed from the schema in #692; the object is not
  `.strict()`, so they are stripped rather than rejected. They are dead weight.
- Bind each compliance value to its own session field:

```yaml
compliance:
  - { key: "user_terms",       value: "{user_terms}" }
  - { key: "user_privacy",     value: "{user_privacy}" }
  - { key: "profile_creation", value: "{profile_consent}" }
```

Each value is either lifted from the fetch or set by the consent subagent, so
the payload is exactly what the session holds. A whole-string `"{placeholder}"`
preserves type (`action_gateway/src/adapters/rest_api.py:216`) and
`_coerce_to_declared_type` turns the session enum into a Python bool, so the
wire payload is JSON `true`.

The precondition for reaching either write is that all three flags are true.
Upstream rejects `value: false` outright with `400 CONSENT_DECLINED`, so there
is no "send false" path by design.

### 8. Gating acceptance on the question actually asked

The `consent` subagent asks two questions, so the original #489 defect still
applies and the fix is the same: anchor the pending on what the bot actually
said.

The `consent` pending gains:

```yaml
- { field: current_question, operator: contains, value: ["अनुमति", "रिकॉर्ड", "सेव"] }
```

(*permission / record / save*) — `contains` with a list is an OR
(`agent_core/src/conditions.py:50`). "अनुमति" is mandated in both phrasings of
the consent question so the anchor holds whichever one is spoken; none of the
three can appear in the age question.

`use_saved_details` swaps its entry-count proxy for the real anchor:

```yaml
- id: use_saved_details
  expects: "saved काम और शहर इस्तेमाल करें? हाँ/नहीं"
  when:
    - { field: profile_item_id, operator: not_eq, value: "" }
    - { field: current_question, operator: contains, value: ["इन्हीं से"] }
```

"इन्हीं से" (*with these*) comes from `profile_resolve`'s `fixed_opening`,
which is emitted by code rather than the model, so the anchor cannot drift. The
same phrase is already used as a pending anchor at `agent_core.yaml:3382`. This
also fixes the case where the fixed opening never fires — with no saved trade
or city the phrase is absent and the pending correctly stays shut.

Both gates fail closed: if an anchor is missed the pending does not resolve,
the slot is rejected as `not_pending`, and the caller is re-asked. Consent is
never recorded wrongly.

### 9. Trust Layer

No code change. The pre-LLM gate is already off (`ask_for_consent: false`), and
the tool-execution gate calls a stub that always returns `True`, so there is
nothing enforcing to disable. Adding a config flag to skip a call that cannot
fail would be a schema change plus three dev-kit mirror updates for no change
in behaviour.

Recorded here instead, because the next person to read `check_consent` needs
it: enabling the real `TrustLayer` (`trust_layer/src/orchestrator.py`) would
block every Blue Dots participant write, since nothing populates its consent
store. That store also holds one boolean per `session_id`, ignores the
connector name it is passed (`trust_layer/src/orchestrator.py:94`), and has no
version scoping — it cannot express three named keys at a document version,
which is what the upstream requires.

## Constraints and invariants

**One profile, one consent flag.** `profile_item_id` and `profile_consent` must
use the identical selector. `items[lifecycle_status=live]` returns the first
match (`action_gateway/src/adapters/response_path.py:46`) and items are
newest-first, so both resolve to the same item by construction. Introducing
profile selection — where the caller picks among several live profiles —
invalidates the single flag, because the consent flag would have to be
re-resolved for the chosen item. Session state has no map type
(`memory_layer.yaml:18-19`), so that would need a JSON-encoded string or a
re-fetch. Multi-profile behaviour is tracked separately as #496 and is out of
scope here.

**Storage mode stays `saved`.** `user_data_persistence.default_mode` is
per-domain configurable (`memory_layer/src/memory_layer.py:116`, set for Blue
Dots at `memory_layer.yaml:171`). It stays `saved`: Blue Dots takes no
ai-diffusion-level consent, only the upstream's, so there is no refusal that
should trigger a graph delete. A consequence is that nothing writes
`user_storage_mode: anonymous` any more.

## Out of scope

- Multi-profile selection (#496).
- Recovery when `fetch_profile` fails outright — not a 400, but a timeout or
  5xx. The call then proceeds as if the caller were new, which is the one
  remaining path where a minor could state an adult age and overwrite a stored
  minor age (the POST takes `body.age ?? existing.age`, body winning —
  `Signals-DPG/apps/api/src/routes/v1/admin/participant.ts:1138`). Rare, and
  the fix needs its own decision. Filed as a follow-up.
- Replacing `BasicTrustLayer` with the real implementation.
- Any change to the `age` slot's `accept_when_pending: [consent, age]`, so a
  caller who volunteers their age during the consent question is still heard.

## Testing

- Pending resolution with and without each anchor, including the two consent
  phrasings (`agent_core/tests/`).
- `body_template` rendering with each consent flag true and absent
  (`action_gateway/tests/`).
- `on_error` session mapping against a 400 body (`action_gateway/tests/`).
- Routing: each entry condition into `consent`, the return to both callers, the
  re-ask counter, and both U18 gates (`agent_core/tests/`).
- Manual, on the local host-mode stack: a new caller through to profile
  creation; a returning caller with current consent (no question); a returning
  caller with stale profile consent (asked, profile-only phrasing); a returning
  minor (blocked at turn 1).

## Open questions

None blocking. The wording of both consent phrasings needs review by whoever
owns the consent copy before implementation, since "अनुमति" becomes a
load-bearing token rather than a stylistic choice.
