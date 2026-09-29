---
title: Caller facts from the participant fetch
date: 2026-09-29
status: approved
audience: blue-dots engineering
---

# Caller facts from the participant fetch

## The problem

The KKB voice agent asks every caller for consent and age, then fetches their
Signals profile *afterwards* — so a caller whose consent and age are already on
file is asked for both anyway. The fetch happens in `profile_resolve`; the gate
that needs its answers is in `opening`, which runs first.

Underneath that is a structural gap: **routing depends on fields that nothing
writes.** Three instances have been found and patched one at a time —
`consent_response`, `profile_setup_done`, and now the whole fetch result.
Routing reads `session ∪ profile`; a tool response reaches only the LLM.
`ResponseConfig.field_mapping` is marked *"Reserved — see GH-93"* and has a
different meaning (slimming what the LLM sees), so it cannot be reused.

## What Signals actually provides

`GET /api/v1/admin/participant?phone_number=…` returns
`{ user_id, compliance, items }` and nothing else. Verified against the source:

- `has_age: age != null` reads the **participant-level** `user.age`. The
  profile's `item_state.age` is unrelated and must be ignored.
- `user_terms` / `user_privacy` come from `consent_record` at `level: 'user'`,
  **version-scoped** — `true` only while the accepted version matches the
  document currently served.
- The **age value is never returned**. We learn only whether one exists.
- For a voice caller the endpoint **rejects minors itself** with
  `400 U18_NOT_ALLOWED`: *"the voice channel would otherwise be told 'consent
  incomplete' and then be unable to complete it."*

So the fetch outcome is a three-way gate:

| result | meaning |
| --- | --- |
| `400 U18_NOT_ALLOWED` | minor → portal, ask nothing |
| `200`, `has_age: true` | adult, age on file → ask nothing |
| `200`, `has_age: false` | no age on file → must collect it |

`items` arrive **newest-first** (`ITEMS_NEWEST_FIRST = [desc(created_at),
desc(item_id)]`, on `origin/develop`). Take the first item whose
`lifecycle_status` is `live` — not `items[0]`, which can be `retired`.

## Decisions

| | decision | why |
| --- | --- | --- |
| Storage | **session scope, re-fetched every call** | consent is version-scoped; a cached `true` would skip a question the upstream now wants asked |
| Profile pick | **first `live` item, confirmed aloud** | names collide ("Rahul", "Rahul2", "last profile ") and 3 of 4 carry no role or location, so a menu is unspeakable |
| Mechanism | **connector-declared `session_mapping`** | config-driven, fits the DPG design, retires the bug class rather than a third instance of it |
| Trigger | **`fetch_profile` moves into `opening`** with a must-call rule | a missed fetch degrades to today's behaviour rather than breaking |
| Turn 1 | **greet + "are you looking for work?"** | tools run after routing, so the fetch can only steer routing from turn 2; this keeps turn 1 useful instead of wasted |
| U18 | **driven by the fetch's 400** | the upstream is the authority and already fails closed |

## Design

### `session_mapping` on a connector

Alongside the existing `projection`, which is unchanged:

```yaml
response:
  projection:        # what the LLM reads — unchanged
    fields: {...}
  session_mapping:   # what ROUTING reads — new
    - source: "compliance[key=has_age].value"
      target: has_age
    - source: "compliance[key=user_terms].value"
      target: user_terms
    - source: "compliance[key=user_privacy].value"
      target: user_privacy
    - source: "items[?lifecycle_status=live][0].item_id"
      target: profile_item_id
    - source: "user_id"
      target: acting_as_user_id
```

The adapter evaluates each `source` against the raw response and returns the
results on `ToolResult.session_values`. Agent Core writes them at session scope
after the tool loop, the same place entity results are already persisted.

**The one-turn lag is inherent and accepted.** Tools run after routing, so
values land in time for the *next* turn. The flow is built around it: turn 1
greets and fetches, turn 2 routes on what came back.

### Call shape

```
turn 1  opening — greet, ask "are you looking for work?", fetch_profile
turn 2  routing sees the flags
        ├─ 400 U18_NOT_ALLOWED  → u18_blocked, nothing asked
        ├─ no user_id           → new caller: ask consent, then age
        ├─ a flag false         → ask only that
        └─ all true             → profile_resolve, ask nothing
turn 3  profile_resolve — "your details say X — use that?"
        ├─ yes → job_match, anchored on that item_id
        └─ no  → trade + city → job_match, text search
```

### Failure handling

A failed or unreachable fetch yields no flags. Treat every flag as **unknown**
and fall back to today's behaviour — ask consent and age. Annoying, never
unsafe. Never assume `true`.

## Out of scope

Writing consent or age back to Signals (`record_consent`), and the durable
storage of caller facts in Memgraph. Both were considered and deferred:
consent is version-scoped, so anything cached can be wrong by the next call.
