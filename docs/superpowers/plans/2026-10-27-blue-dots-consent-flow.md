# Blue Dots Consent Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ask the Blue Dots caller for consent only when an upstream participant write needs it, and send upstream only the consent they actually gave.

**Architecture:** Config-only, across the three Blue Dots domain configs. A new non-terminal `consent` subagent holds the consent and age questions; every routing rule that enters `profile_setup` gains a sibling above it that diverts to `consent` when a required flag is missing, and `consent` returns to `profile_setup` once they are all true. The flags come from the turn-1 participant fetch, which already runs in `session_bootstrap`. No Python changes: every mechanism used here (`on_error` session mappings, `current_question` pending anchors, `session_writes`, `subagent_entry_count`) already exists and is in use elsewhere in this config.

**Tech Stack:** YAML domain config (`dev-kit/configs/blue-dots/`), pytest, `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-10-27-blue-dots-consent-flow-design.md`

**Issue:** ai-diffusion-dpg#531 (sub-issue of #488)

**Branch:** `feat/488-consent-flow`, cut from `origin/develop`.

## Global Constraints

- **Base is `develop`.** `update_profile`, `profile_update` and `profile_update_confirm` do NOT exist here — they arrive with PR #527. Never reference them. `save_profile` is the only participant write.
- **Config only.** If a task appears to need a Python change, stop and raise it rather than writing one. Every mechanism needed already exists.
- **No code comments beyond one line.** This repo's rule applies to YAML exactly as to Python: default to none; one line maximum where something exists for a non-obvious reason the config cannot show. Never write a comment explaining what went wrong before, why an ordering matters, or referencing an issue or PR number. The surrounding file is full of long comments — that is not permission to add more.
- **Every Hindi string shown in a review or report must be paired with its English translation.**
- **Session booleans are 2-value enums.** The session schema has no bool type. Declare `{ type: enum, values: ["true", "false"], default: "false" }` and compare against the YAML boolean `true` in routing — Memory Layer JSON-decodes on read.
- **Anchor word:** both consent phrasings must contain `"अनुमति"` (*permission*). This is load-bearing, not stylistic.
- **Test commands:** `cd agent_core && uv run pytest` and `cd action_gateway && uv run pytest`. This is an 8 GB machine — do not run both suites in parallel.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has a test assigned to the task that owns the code.

1. **`subagent_entry_count.consent` counts entries, not declines.** A caller who reaches `consent`, grants, and later re-enters from a different route would arrive with the counter already at 2 and be hung up on without being asked twice. Task 5 owns this and uses a dedicated `consent_declines` counter instead.
2. **`profile_consent_accepted` missing from the fetch response.** An older Signals build omits the field; the mapping resolves to `None`, the key is never written, and the session default must make the caller be asked rather than silently skipped. Task 1.
3. **A brand-new caller (`user_id: null`).** The fetch returns 200 with all flags false and no items — not an error, and not the U18 path. Must route to the full-consent phrasing. Task 1 and Task 4.
4. **Consent and age in one breath** — *"हाँ, मैं पच्चीस का हूँ"* (*"yes, I'm twenty-five"*). Both slots land on the same turn and the age question must be skipped. Task 3.
5. **A caller who volunteers consent before being asked.** At turn 1 `current_question` is empty, so the anchor does not match, the slot is rejected as `not_pending`, and the caller must still be asked properly rather than stalling. Task 6.

---

### Task 1: Lift the per-profile consent flag and the U18 rejection into session

The turn-1 fetch already lifts `user_terms`, `user_privacy` and `has_age`. It does not lift the per-profile `profile_creation` consent (Signals reports it on the item, not in the `compliance` array) and it throws away the `400 U18_NOT_ALLOWED` body entirely.

**Files:**
- Modify: `dev-kit/configs/blue-dots/action_gateway.yaml:108-129` (`seeker_fetch` `session_mapping`)
- Modify: `dev-kit/configs/blue-dots/memory_layer.yaml:19-35` (session schema)
- Test: `agent_core/tests/test_blue_dots_consent_flow.py` (create)

**Interfaces:**
- Produces: session fields `profile_consent` (enum `"true"`/`"false"`, default `"false"`) and `fetch_error` (string, default `""`). Tasks 2, 4, 5 and 7 read these by name.

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_blue_dots_consent_flow.py`:

```python
"""The Blue Dots consent flow: what the fetch lifts, and what the write sends."""
from pathlib import Path

import yaml

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
AG = yaml.safe_load((BD / "action_gateway.yaml").read_text(encoding="utf-8"))
ML = yaml.safe_load((BD / "memory_layer.yaml").read_text(encoding="utf-8"))


def _tool(tool_id):
    return next(t for t in AG["tools"] if t["id"] == tool_id)


def _seeker_mappings():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "seeker_fetch":
                return {m["target"]: m for m in t["response"]["session_mapping"]}
    raise AssertionError("seeker_fetch not found")


def test_fetch_lifts_the_per_profile_consent_flag():
    m = _seeker_mappings()["profile_consent"]
    assert m["source"] == "items[lifecycle_status=live].profile_consent_accepted"
    assert not m.get("on_error")


def test_profile_consent_resolves_from_the_same_item_as_profile_item_id():
    m = _seeker_mappings()
    assert (m["profile_consent"]["source"].rsplit(".", 1)[0]
            == m["profile_item_id"]["source"].rsplit(".", 1)[0])


def test_fetch_lifts_the_upstream_error_code():
    m = _seeker_mappings()["fetch_error"]
    assert m["source"] == "error"
    assert m["on_error"] is True


def test_consent_flags_are_declared_with_a_false_default():
    schema = ML["state"]["session"]["schema"]
    for field in ("profile_consent", "user_terms", "user_privacy", "has_age"):
        assert schema[field]["type"] == "enum", field
        assert schema[field]["values"] == ["true", "false"], field
        assert schema[field]["default"] == "false", field


def test_fetch_error_is_declared():
    assert ML["state"]["session"]["schema"]["fetch_error"] == {"type": "string", "default": ""}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: FAIL — `KeyError: 'profile_consent'`

- [ ] **Step 3: Add the two mappings**

In `dev-kit/configs/blue-dots/action_gateway.yaml`, inside `seeker_fetch`'s `session_mapping` list, after the `user_privacy` entry:

```yaml
        - source: "items[lifecycle_status=live].profile_consent_accepted"
          target: profile_consent
        - source: "error"
          target: fetch_error
          on_error: true
```

- [ ] **Step 4: Declare both session fields**

In `dev-kit/configs/blue-dots/memory_layer.yaml`, after the `user_privacy` line in `state.session.schema`:

```yaml
      profile_consent:         { type: enum,   values: ["true", "false"], default: "false" }
      fetch_error:             { type: string, default: "" }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Prove the selector resolves against a real response body**

This is Review Focus items 2 and 3. Append to `agent_core/tests/test_blue_dots_consent_flow.py`:

```python
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "action_gateway"))
from src.adapters.response_path import resolve  # noqa: E402

_LIVE_BODY = {
    "user_id": "d1455336-fb14-4fc0-a8ef-547213a00831",
    "compliance": [{"key": "user_terms", "value": True},
                   {"key": "user_privacy", "value": True},
                   {"key": "has_age", "value": True}],
    "items": [
        {"item_id": "72e45bfc", "lifecycle_status": "live", "profile_consent_accepted": False},
        {"item_id": "ecf7a8a2", "lifecycle_status": "live", "profile_consent_accepted": True},
    ],
}


def test_newest_live_item_wins_and_its_consent_travels_with_it():
    m = _seeker_mappings()
    assert resolve(_LIVE_BODY, m["profile_item_id"]["source"]) == "72e45bfc"
    assert resolve(_LIVE_BODY, m["profile_consent"]["source"]) is False


def test_an_unknown_caller_yields_no_item_and_no_consent():
    body = {"user_id": None, "compliance": [{"key": "user_terms", "value": False},
                                            {"key": "user_privacy", "value": False},
                                            {"key": "has_age", "value": False}], "items": []}
    m = _seeker_mappings()
    assert resolve(body, m["profile_item_id"]["source"]) is None
    assert resolve(body, m["profile_consent"]["source"]) is None


def test_an_older_upstream_without_the_field_reads_as_absent():
    body = {"items": [{"item_id": "a", "lifecycle_status": "live"}]}
    assert resolve(body, _seeker_mappings()["profile_consent"]["source"]) is None
```

An unresolved mapping is omitted rather than written (`action_gateway/src/adapters/rest_api.py:267`), so the session keeps its declared `"false"` default and the caller is asked. That is the behaviour these two tests pin.

- [ ] **Step 7: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (8 tests)

- [ ] **Step 8: Commit**

```bash
git add dev-kit/configs/blue-dots/action_gateway.yaml dev-kit/configs/blue-dots/memory_layer.yaml agent_core/tests/test_blue_dots_consent_flow.py
git commit -m "feat(blue-dots): read per-profile consent and the upstream rejection from the participant fetch"
```

---

### Task 2: Send the consent the session holds

**Files:**
- Modify: `dev-kit/configs/blue-dots/action_gateway.yaml:301-309` (`save_profile` `body_template`) and its `params` list
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`, `action_gateway/tests/test_rest_api_adapter.py`

**Interfaces:**
- Consumes: session fields `user_terms`, `user_privacy`, `profile_consent` from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_blue_dots_consent_flow.py`:

```python
def _save_profile():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "save_profile":
                return ep
    raise AssertionError("save_profile not found")


def test_compliance_values_come_from_session():
    body = _save_profile()["body_template"]
    assert body["compliance"] == [
        {"key": "user_terms", "value": "{user_terms}"},
        {"key": "user_privacy", "value": "{user_privacy}"},
        {"key": "profile_creation", "value": "{profile_consent}"},
    ]


def test_the_dead_consent_fields_are_gone():
    body = _save_profile()["body_template"]
    assert "terms_accepted" not in body
    assert "privacy_accepted" not in body


def test_consent_params_are_session_sourced_booleans():
    params = {p["name"]: p for p in _save_profile()["params"]}
    for name in ("user_terms", "user_privacy", "profile_consent"):
        assert params[name]["source"] == "session", name
        assert params[name]["type"] == "boolean", name
        assert params[name].get("required") is False, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k compliance -v`
Expected: FAIL — the template still holds literal `true` values

- [ ] **Step 3: Rewrite the compliance block**

Replace `save_profile`'s `compliance` block and the two lines beneath it:

```yaml
          compliance:
            - { key: "user_terms",       value: "{user_terms}" }
            - { key: "user_privacy",     value: "{user_privacy}" }
            - { key: "profile_creation", value: "{profile_consent}" }
```

Delete `terms_accepted: true` and `privacy_accepted: true` entirely. Delete the comment above the block that explains the old pair — it describes config that no longer exists.

- [ ] **Step 4: Declare the three params**

In `save_profile`'s `params` list, after the existing `age` entry:

```yaml
          - name: user_terms
            source: session
            type: boolean
            required: false
            description: "Supplied by the framework from turn state. Not visible to the model."
          - name: user_privacy
            source: session
            type: boolean
            required: false
            description: "Supplied by the framework from turn state. Not visible to the model."
          - name: profile_consent
            source: session
            type: boolean
            required: false
            description: "Supplied by the framework from turn state. Not visible to the model."
```

- [ ] **Step 5: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Prove the rendered payload is a JSON boolean, not a string**

Add to `action_gateway/tests/test_rest_api_adapter.py`:

```python
def test_session_enum_renders_as_a_json_boolean_in_compliance():
    from src.adapters.rest_api import _render_body_template, _coerce_to_declared_type

    template = {"compliance": [{"key": "user_terms", "value": "{user_terms}"}]}
    values = {"user_terms": _coerce_to_declared_type("true", "boolean")}
    assert _render_body_template(template, values) == {
        "compliance": [{"key": "user_terms", "value": True}]
    }


def test_a_missing_consent_flag_drops_its_value_rather_than_sending_false():
    from src.adapters.rest_api import _render_body_template

    template = {"compliance": [{"key": "user_terms", "value": "{user_terms}"}]}
    assert _render_body_template(template, {}) == {"compliance": [{"key": "user_terms"}]}
```

The second test pins the fail-closed behaviour: an absent flag yields a body the upstream rejects, rather than a fabricated `true`. Routing is what keeps that path unreachable.

- [ ] **Step 7: Run and verify**

Run: `cd action_gateway && uv run pytest tests/test_rest_api_adapter.py -k consent -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add dev-kit/configs/blue-dots/action_gateway.yaml agent_core/tests/test_blue_dots_consent_flow.py action_gateway/tests/test_rest_api_adapter.py
git commit -m "feat(blue-dots): build the participant compliance array from session consent"
```

---

### Task 3: Add the `consent` subagent

Added here but not yet routed to — Task 4 wires it up. Keeping those separate means the config validates at every commit.

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml` — insert after the `opening` subagent ends and before `consent_declined` (line 2157 on develop)
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`

**Interfaces:**
- Produces: subagent id `consent`; pendings `consent` and `age` (moved from `opening`); session fields `consent_return_to` (string) and `consent_declines` (int).

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_blue_dots_consent_flow.py`:

```python
from src.understanding.pending import PendingResolver  # noqa: E402
from tests.test_blue_dots_dialogue_act_config import _load  # noqa: E402

_ASKED_CONSENT = "आपकी जानकारी सेव करने की अनुमति चाहिए; यह बातचीत रिकॉर्ड की जा सकती है। ठीक है?"
_ASKED_AGE = "आपकी उम्र क्या है?"


def test_consent_subagent_exists_and_is_not_terminal():
    _, wf = _load()
    sub = wf.subagents["consent"]
    assert sub.is_terminal is False
    assert sub.tools == []


def test_consent_pending_needs_the_consent_question_to_have_been_asked():
    _, wf = _load()
    r = PendingResolver(wf)
    assert r.resolve("consent", {"current_question": _ASKED_CONSENT}).id == "consent"
    assert r.resolve("consent", {"current_question": _ASKED_AGE, "has_age": "false"}).id == "age"


def test_nothing_is_pending_before_the_bot_has_asked_anything():
    _, wf = _load()
    assert PendingResolver(wf).resolve("consent", {"current_question": ""}) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k consent_subagent -v`
Expected: FAIL — `KeyError: 'consent'`

- [ ] **Step 3: Insert the subagent**

Insert into `agent_workflow.subagents`, immediately before `- id: consent_declined`:

```yaml
    - id: consent
      name: Consent
      description: >
        Collect the permissions the upstream needs before a profile can be
        written: the terms and privacy acceptance, permission to create the
        profile, and the caller's age when it is not already on file. Entered
        only when something is missing, and returns to the phase that sent it.
      is_start: false
      is_terminal: false
      tools: []
      pending:
        - id: consent
          expects: "हाँ/नहीं — details save करने की अनुमति"
          when:
            - { field: current_question, operator: contains, value: ["अनुमति", "रिकॉर्ड", "सेव"] }
        - id: age
          expects: "उम्र, साल में"
          when:
            - { field: has_age, operator: not_eq, value: true }
            - { field: age, operator: in, value: [null, "", 0, "0"] }
      system_prompt: |
        You are collecting permission before anything is saved. Ask ONE
        question per turn, in Hindi, and never both in the same turn.

        ## Question 1 — permission
        Ask this whenever `consent_response` is not yet set.

        Which version you ask depends on what is already on file.

        **If `user_terms` and `user_privacy` are both true** — they have
        already accepted the terms and the privacy notice, and only this
        profile needs permission. One sentence: ask for their अनुमति
        (permission) to save these details as their profile. Do NOT read
        the terms back to them.

        **Otherwise** — one sentence covering all of it: that you need their
        अनुमति (permission) to save their details and send applications on
        their behalf, and that यह बातचीत रिकॉर्ड की जा सकती है।

        Both versions MUST contain the word "अनुमति". One sentence, ending in
        a clear yes/no question.

        If the answer is unclear, ask again more plainly. Never assume
        permission from silence or from an ambiguous reply.

        ## Question 2 — age
        Only when the age is not already known. Ask for their age in years.
        Nothing else — not the trade, not the city, not their name.

        ## If they refuse
        The first refusal is not the end. Say, in one sentence, that without
        that permission their profile cannot be created and applications
        cannot be sent, and ask once more. If they refuse again, say nothing
        further here — routing closes the call.

        Never call a tool in this phase.
      routing:
        - intent: off_track
          next_subagent_id: consent
        - intent: termination_intent
          next_subagent_id: ended
```

- [ ] **Step 4: Run and verify the config still loads**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py tests/test_blue_dots_dialogue_act_config.py -v`
Expected: PASS for the three new tests. `test_blue_dots_dialogue_act_config.py` still passes — `opening` is untouched so far, and two subagents may declare the same pending id.

- [ ] **Step 5: Add the consent-and-age-in-one-breath test**

This is Review Focus item 4. Append:

```python
def test_age_is_not_asked_when_it_arrived_with_the_consent():
    _, wf = _load()
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": "false", "age": 25}
    assert PendingResolver(wf).resolve("consent", state).id == "consent"
```

The `age` pending must not resolve once an age is present, so the model is never prompted to re-ask it. Routing (Task 4) is what then leaves the phase.

- [ ] **Step 6: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (15 tests)

- [ ] **Step 7: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/test_blue_dots_consent_flow.py
git commit -m "feat(blue-dots): add the consent phase that collects permission and age"
```

---

### Task 4: Route into and out of the consent phase

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml` — the `consent` subagent's `routing` (from Task 3), plus eight rules in `job_match` (2878), `job_details` (2948), `apply_confirm` (3720), `service_match` (3918), `confirm_close` (4081, 4089) and `handoff` (4140, 4148). Line numbers shift as you edit; locate each by its `next_subagent_id: profile_setup`.
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`

**Interfaces:**
- Consumes: subagent `consent` (Task 3); session fields `user_terms`, `user_privacy`, `profile_consent`, `has_age` (Task 1).

- [ ] **Step 1: Write the failing structural test**

This is the invariant the spec names. Append:

```python
def _profile_setup_routes(wf):
    for sid, sub in wf.subagents.items():
        if sid == "profile_setup":
            continue
        for i, rule in enumerate(sub.routing):
            if rule.next_subagent_id == "profile_setup":
                yield sid, i, sub.routing


def test_every_route_into_profile_setup_is_preceded_by_a_consent_gate():
    _, wf = _load()
    seen = list(_profile_setup_routes(wf))
    assert len(seen) == 8, f"expected 8 routes, found {len(seen)}"
    for sid, i, routing in seen:
        earlier = [r.next_subagent_id for r in routing[:i]]
        assert "consent" in earlier, f"{sid} rule {i} reaches profile_setup ungated"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k preceded -v`
Expected: FAIL — `job_match rule N reaches profile_setup ungated`

- [ ] **Step 3: Insert the gate above each of the eight rules**

In each of the six subagents, immediately **above** every rule whose `next_subagent_id` is `profile_setup`, insert a rule with the same `intent` and the same existing `conditions`, plus the four consent conditions, pointing at `consent`. The gate must fire when **any** flag is missing, and `conditions` is an AND — so it is four separate rules per site, one per flag.

For `job_match`'s `- intent: apply_now` rule, that is:

```yaml
        - intent: apply_now
          conditions: [{ field: user_terms, operator: not_eq, value: true }]
          next_subagent_id: consent
          session_writes: { consent_return_to: profile_setup, apply_failed: 0 }
        - intent: apply_now
          conditions: [{ field: user_privacy, operator: not_eq, value: true }]
          next_subagent_id: consent
          session_writes: { consent_return_to: profile_setup, apply_failed: 0 }
        - intent: apply_now
          conditions: [{ field: profile_consent, operator: not_eq, value: true }]
          next_subagent_id: consent
          session_writes: { consent_return_to: profile_setup, apply_failed: 0 }
        - intent: apply_now
          conditions: [{ field: has_age, operator: not_eq, value: true }]
          next_subagent_id: consent
          session_writes: { consent_return_to: profile_setup, apply_failed: 0 }
        - intent: apply_now
          next_subagent_id: profile_setup
          session_writes: { apply_failed: 0 }
```

Carry each site's own `intent`, `conditions` and `session_writes` through unchanged, adding only `consent_return_to: profile_setup`. The eight sites and their intents:

| Subagent | Intent | Existing conditions | Existing `session_writes` |
|---|---|---|---|
| `job_match` | `apply_now` | none | `{ apply_failed: 0 }` |
| `job_details` | `apply_now` | none | none |
| `apply_confirm` | `*` | `profile_item_id eq ""` | none |
| `service_match` | `service_pick` | none | none |
| `confirm_close` | `close_declined` | `close_return_to eq profile_setup` | none |
| `confirm_close` | `*` | `close_return_to eq profile_setup` | none |
| `handoff` | `close_declined` | `close_return_to eq profile_setup` | none |
| `handoff` | `*` | `close_return_to eq profile_setup` | none |

- [ ] **Step 4: Run the structural test**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k preceded -v`
Expected: PASS

- [ ] **Step 5: Write the failing return-path test**

Append:

```python
import pytest  # noqa: E402

from tests.test_stream_turn import _make_agent_core  # noqa: E402

_ALL_GIVEN = {"user_terms": True, "user_privacy": True, "profile_consent": True, "has_age": True}


def _route(sid, state, intent="*"):
    from src.models import NLUResult
    _, wf = _load()
    nxt, rule = _make_agent_core(workflow=wf)._resolve_next_subagent(
        current_subagent=wf.subagents[sid],
        nlu_result=NLUResult(intent=intent, entities={}, confidence=1.0),
        session=state)
    return nxt, rule


@pytest.mark.parametrize("missing", ["user_terms", "user_privacy", "profile_consent", "has_age"])
def test_a_missing_flag_diverts_to_consent(missing):
    state = {**_ALL_GIVEN, missing: False}
    nxt, rule = _route("job_match", state, intent="apply_now")
    assert nxt == "consent"
    assert rule.session_writes["consent_return_to"] == "profile_setup"


def test_every_flag_given_reaches_profile_setup_directly():
    assert _route("job_match", dict(_ALL_GIVEN), intent="apply_now")[0] == "profile_setup"


def test_consent_returns_to_the_phase_that_sent_it():
    state = {**_ALL_GIVEN, "consent_return_to": "profile_setup", "consent_response": "granted"}
    assert _route("consent", state)[0] == "profile_setup"


def test_a_brand_new_caller_is_sent_to_consent():
    state = {"user_terms": False, "user_privacy": False, "profile_consent": False,
             "has_age": False, "profile_item_id": ""}
    assert _route("job_match", state, intent="apply_now")[0] == "consent"
```

The last one is Review Focus item 3.

- [ ] **Step 6: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k "diverts or returns or directly or brand_new" -v`
Expected: the divert tests PASS from Step 3; `test_consent_returns_to_the_phase_that_sent_it` FAILS — `consent` has no return rule yet.

- [ ] **Step 7: Add the return rules to `consent`**

Append to the `consent` subagent's `routing`, after the `termination_intent` rule:

```yaml
        - intent: "*"
          conditions:
            - { field: user_terms,      operator: eq, value: true }
            - { field: user_privacy,    operator: eq, value: true }
            - { field: profile_consent, operator: eq, value: true }
            - { field: has_age,         operator: eq, value: true }
            - { field: consent_return_to, operator: eq, value: profile_setup }
          next_subagent_id: profile_setup
          session_writes: { consent_return_to: "" }
        - intent: "*"
          next_subagent_id: consent
```

- [ ] **Step 8: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/test_blue_dots_consent_flow.py
git commit -m "feat(blue-dots): divert every route into profile setup through the consent phase"
```

---

### Task 5: Record the answer, and close the call on a second refusal

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml` — the `consent` subagent's `routing`
- Modify: `dev-kit/configs/blue-dots/memory_layer.yaml` — declare `consent_declines`
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`

**Interfaces:**
- Consumes: pending `consent` and slot `consent_response` (Task 3); routing shape (Task 4).
- Produces: session field `consent_declines` (int, default 0).

A dedicated counter rather than `subagent_entry_count.consent` — Review Focus item 1. Entry count rises on every arrival at the phase, including a second visit after a granted consent, which would hang up on a caller who had never refused.

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_granting_writes_every_flag_the_write_needs():
    state = {"current_question": _ASKED_CONSENT, "consent_response": "granted",
             "has_age": True, "consent_return_to": "profile_setup"}
    _, rule = _route("consent", state)
    assert rule.session_writes["user_terms"] is True
    assert rule.session_writes["user_privacy"] is True
    assert rule.session_writes["profile_consent"] is True


def test_the_first_refusal_asks_again():
    state = {"consent_response": "declined", "consent_declines": 0,
             "consent_return_to": "profile_setup"}
    nxt, rule = _route("consent", state)
    assert nxt == "consent"
    assert rule.session_writes["consent_declines"] == 1


def test_the_second_refusal_ends_the_call():
    state = {"consent_response": "declined", "consent_declines": 1,
             "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "consent_declined"


def test_declines_are_counted_separately_from_visits():
    assert ML["state"]["session"]["schema"]["consent_declines"] == {"type": "int", "default": 0}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k "refusal or granting or separately" -v`
Expected: FAIL — no rule reads `consent_response`

- [ ] **Step 3: Declare the counter**

In `dev-kit/configs/blue-dots/memory_layer.yaml`, beside the other consent fields:

```yaml
      consent_declines:        { type: int,    default: 0 }
```

- [ ] **Step 4: Add the three rules**

In the `consent` subagent's `routing`, immediately **after** the `termination_intent` rule and **before** the return rule from Task 4:

```yaml
        - intent: "*"
          conditions:
            - { field: consent_response, operator: eq, value: "declined" }
            - { field: consent_declines, operator: gt, value: 0 }
          next_subagent_id: consent_declined
          session_writes: { user_storage_mode: "anonymous", consent_given: false }
        - intent: "*"
          conditions: [{ field: consent_response, operator: eq, value: "declined" }]
          next_subagent_id: consent
          session_writes: { consent_declines: 1, consent_response: "" }
        - intent: "*"
          conditions: [{ field: consent_response, operator: eq, value: "granted" }]
          next_subagent_id: consent
          session_writes:
            user_terms: true
            user_privacy: true
            profile_consent: true
            consent_given: true
            user_storage_mode: "saved"
            consent_response: ""
```

The granted rule returns to `consent` rather than straight to `profile_setup`: the age may still be outstanding, and Task 4's return rule — which sits below these and tests all four flags — is what leaves the phase on the following turn.

- [ ] **Step 5: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml dev-kit/configs/blue-dots/memory_layer.yaml agent_core/tests/test_blue_dots_consent_flow.py
git commit -m "feat(blue-dots): record the consent answer and close the call on a second refusal"
```

---

### Task 6: Take consent and age out of the opening

Until this task the bot asks twice — once at the opening, once at the gate. This removes the first.

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml` — the `opening` subagent (1641-2156): its `pending` block (1894-1905), its consent and age routing rules, and the consent and age sections of its `system_prompt`
- Modify: `agent_core/tests/test_blue_dots_dialogue_act_config.py` — the parametrised pending table
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`

**Interfaces:**
- Consumes: everything from Tasks 3-5. Do not start this task until they are green.

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_blue_dots_consent_flow.py`:

```python
def test_the_opening_asks_nothing_about_consent_or_age():
    _, wf = _load()
    assert [p.id for p in wf.subagents["opening"].pending] == []


def test_the_opening_routes_a_caller_onward_without_consent():
    assert _route("opening", {"profile_item_id": "p1"})[0] == "profile_resolve"


def test_a_volunteered_consent_before_any_question_is_not_recorded():
    _, wf = _load()
    assert PendingResolver(wf).resolve("opening", {"current_question": ""}) is None
```

The third is Review Focus item 5: with no pending, `accept_slots` rejects `consent_response` as `not_pending`, so the caller is asked properly at the gate instead.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k opening -v`
Expected: FAIL — `opening` still declares `consent` and `age` pendings

- [ ] **Step 3: Delete the opening's pendings**

Remove the whole `pending:` block from the `opening` subagent (both the `consent` and `age` entries). Leave no empty key — delete the `pending:` line too.

- [ ] **Step 4: Delete the consent and age routing rules**

From `opening`'s `routing`, remove every rule that reads `consent_response`, `consent_given`, `has_age` or `age`. Keep `off_track`, `termination_intent`, both `termination_blocked` rules, and the final `- intent: "*"` catch-all — but repoint that catch-all:

```yaml
        - intent: "*"
          next_subagent_id: profile_resolve
```

Delete the long comment block above the removed rules that explains the two kinds of state and the `opening_phrase_emitted` guard: it describes rules that no longer exist.

- [ ] **Step 5: Trim the prompt**

From `opening`'s `system_prompt`, delete the `## Question 1 — consent` section, the `## If the caller REFUSES consent` section, the `## If the caller is under 19` section, and the `**2. The ONLY question you ask after consent is the age.**` absolute. Keep the "never call `end_session` here" absolute and the "you must always say something" section. Replace the removed material with a single instruction to greet the caller and ask what work they are looking for.

- [ ] **Step 6: Update the existing pending table**

In `agent_core/tests/test_blue_dots_dialogue_act_config.py`, remove these five rows from `test_pending_resolution`'s parameter list — they assert the behaviour just deleted:

```python
    ("opening", {}, "consent"),
    ("opening", {"consent_response": "granted"}, "age"),
    ("opening", {"consent_given": True, "has_age": True}, None),
    ("opening", {"opening_phrase_emitted": True, "consent_response": "granted", "consent_given": True,
                 "has_age": "false", "age": "0"}, "age"),
    ("opening", {"consent_response": "granted", "consent_given": True, "age": 0}, "age"),
```

Add in their place:

```python
    ("opening", {}, None),
    ("consent", {"current_question": "आपकी जानकारी सेव करने की अनुमति चाहिए। ठीक है?"}, "consent"),
    ("consent", {"current_question": "आपकी उम्र क्या है?", "has_age": "false"}, "age"),
```

- [ ] **Step 7: Run the full agent_core suite**

Run: `cd agent_core && uv run pytest`
Expected: PASS. Other tests reference the opening's consent behaviour — notably `test_blue_dots_journey_routes_end_to_end` and anything in `test_orchestrator_consent_event.py`. Fix each by moving its consent expectation to the `consent` phase. If a failure cannot be fixed by updating the expectation, stop: it means something outside this config depends on consent living in the opening, and that is a design question, not a test fix.

- [ ] **Step 8: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/
git commit -m "feat(blue-dots): stop asking for consent and age at the start of the call"
```

---

### Task 7: Block an under-19 returning caller on turn 1

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml` — `opening`'s routing, and `u18_blocked`'s `opening_phrase` and `system_prompt` (2192-2224)
- Test: `agent_core/tests/test_blue_dots_consent_flow.py`

**Interfaces:**
- Consumes: session field `fetch_error` (Task 1); `opening`'s trimmed routing (Task 6).

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_an_upstream_u18_rejection_ends_the_call_at_the_opening():
    assert _route("opening", {"fetch_error": "U18_NOT_ALLOWED"})[0] == "u18_blocked"


def test_an_under_19_answer_in_the_consent_phase_ends_the_call():
    state = {"current_question": _ASKED_AGE, "age": 17, "consent_return_to": "profile_setup"}
    assert _route("consent", state)[0] == "u18_blocked"


def test_other_fetch_failures_do_not_block_the_call():
    assert _route("opening", {"fetch_error": "INTERNAL_ERROR"})[0] == "profile_resolve"


def test_the_under_19_line_says_where_the_age_came_from():
    _, wf = _load()
    line = wf.subagents["u18_blocked"].opening_phrase
    assert "जानकारी के अनुसार" in line
    assert "उन्नीस" in line
    assert "पोर्टल" in line
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -k "u18 or under_19" -v`
Expected: FAIL — nothing reads `fetch_error`

- [ ] **Step 3: Add the opening gate**

In `opening`'s `routing`, as the **first** rule, above `off_track`:

```yaml
        - intent: "*"
          conditions: [{ field: fetch_error, operator: eq, value: "U18_NOT_ALLOWED" }]
          next_subagent_id: u18_blocked
```

- [ ] **Step 4: Add the age gate**

In the `consent` subagent's `routing`, above the consent-answer rules from Task 5:

```yaml
        - intent: "*"
          conditions:
            - { field: age, operator: gt, value: 0 }
            - { field: age, operator: lt, value: 19 }
          next_subagent_id: u18_blocked
```

- [ ] **Step 5: Reword the under-19 line**

Replace `u18_blocked`'s `opening_phrase`:

```yaml
      opening_phrase: >
        आपकी जानकारी के अनुसार आपकी उम्र उन्नीस साल से कम है, और इतनी उम्र में
        फ़ोन पर आवेदन पूरा नहीं हो सकता। कृपया ब्लू डॉट्स पोर्टल पर अपने
        माता-पिता या अभिभावक के साथ रजिस्टर कीजिए। धन्यवाद।
```

*("According to your details you are under nineteen, and at that age an application cannot be completed over the phone. Please register on the Blue Dots portal with your parent or guardian. Thank you.")*

In its `system_prompt`, add that the line must say the age came from their details, keeping the existing "say NINETEEN" instruction unchanged.

- [ ] **Step 6: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_consent_flow.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/test_blue_dots_consent_flow.py
git commit -m "feat(blue-dots): end the call at the start when the upstream reports an under-19 caller"
```

---

### Task 8: Anchor `use_saved_details` on the question actually asked

The second half of #489. Independent of everything above.

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml:2395-2401` (`profile_resolve`'s `use_saved_details` pending)
- Test: `agent_core/tests/test_blue_dots_dialogue_act_config.py`

- [ ] **Step 1: Write the failing test**

In `test_blue_dots_dialogue_act_config.py`, replace these two rows of `test_pending_resolution`:

```python
    ("profile_resolve", {"profile_item_id": "p1", "subagent_entry_count": {"profile_resolve": 1}},
     "use_saved_details"),
    ("profile_resolve", {"profile_item_id": "p1", "subagent_entry_count": {"profile_resolve": 2}}, "trade"),
```

with:

```python
    ("profile_resolve", {"profile_item_id": "p1",
                         "current_question": "मुझे आपकी जानकारी मिली — वेल्डर, बेंगलुरु। "
                                             "क्या इन्हीं से नौकरियाँ ढूंढूँ?"}, "use_saved_details"),
    ("profile_resolve", {"profile_item_id": "p1", "current_question": "कौन सा काम?"}, "trade"),
    ("profile_resolve", {"profile_item_id": "p1", "current_question": ""}, "trade"),
```

The second row is the bug: *"कौन सा काम?"* (*"which trade?"*) must resolve `trade`, not `use_saved_details`. The third covers the fixed opening never firing.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_dialogue_act_config.py -k pending_resolution -v`
Expected: FAIL — the second row resolves `use_saved_details`

- [ ] **Step 3: Swap the entry-count proxy for the anchor**

```yaml
        - id: use_saved_details
          expects: "saved काम और शहर इस्तेमाल करें? हाँ/नहीं"
          when:
            - { field: profile_item_id, operator: not_eq, value: "" }
            - { field: current_question, operator: contains, value: ["इन्हीं से"] }
```

Delete the `subagent_entry_count.profile_resolve` condition and the comment above the pending that explains the ordering workaround.

- [ ] **Step 4: Run and verify**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_dialogue_act_config.py -v`
Expected: PASS

- [ ] **Step 5: Run both full suites**

Run: `cd agent_core && uv run pytest` then `cd action_gateway && uv run pytest`
Expected: PASS. Run them one after the other, not together.

- [ ] **Step 6: Check for stray comments before committing**

Run: `git diff origin/develop -- dev-kit/ | grep '^+ *#'`

Read every line returned. Each stays only if you can name the specific wrong change a reader would make without it. Delete the rest in this commit.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/test_blue_dots_dialogue_act_config.py
git commit -m "fix(blue-dots): accept a saved-details answer only when that was the question"
```

---

## Before opening the PR

- [ ] File the follow-up issue for the `fetch_profile` timeout/5xx path: the call proceeds as if the caller were new, which is the one remaining route by which a minor could state an adult age and overwrite the stored one. Get the issue preview approved before creating it.
- [ ] Comment on PR #527 that its `update_profile` endpoint carries a second copy of the hardcoded `compliance` block plus `terms_accepted` / `privacy_accepted`, and should bind to session the way `save_profile` now does.
- [ ] Manual verification on the local host-mode stack: a new caller through to profile creation; a returning caller with current consent (not asked); a returning caller with stale profile consent (asked, profile-only phrasing); a returning minor (blocked at turn 1); a caller who only searches jobs and hangs up (never asked).
- [ ] Open as a draft PR with `Closes #531`, title `feat(blue-dots): #531 ask for consent when the upstream needs it`.
