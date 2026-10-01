# NLU Single Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dialogue-act NLU contract the only NLU contract. Delete `intent` mode and every piece of logic that exists only for it. Migrate the user-state model and language switching onto the new contract. Remove the `kkb` and `blue-dots-economy` domains.

**Architecture:** The work runs in three phases:
1. Two additive tasks give the dialogue-act pipeline the two features only the old path had: user-state classification and language switching.
2. The orchestrator then runs understanding unconditionally on both paths, and its old branches are deleted.
3. Dead modules, config keys and loader rules are deleted, with runtime and dev-kit kept in sync.

The two domains are removed first, so no later task has to keep their old-format configs valid.

**Tech Stack:** Python 3.11+, pydantic v2, pytest + pytest-asyncio, `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-01-nlu-dialogue-acts-design.md` — read **§16** first (it supersedes the opt-in framing of §2/§5/§7.1/§14), then §5–§9 for the contract.

## Global Constraints

- Branch `refactor/nlu-single-mode` in worktree `/Users/aniket/Documents/github/aniketsaki/ai-diffusion-impl-nlu-single`. It is based on `feat/nlu-dialogue-acts` (PR #428). Commit there and never push.
- Run tests per module: `cd agent_core && uv run pytest …`, `cd dev-kit && uv run pytest -q`, `cd knowledge_engine && uv run pytest -q`.
- **No compatibility path.** After the last task, nothing in the code or config may refer to:
  - `mode`, or the NLU keys `intents`, `entities`, `domain_instruction`, `confidence_threshold`, `sentiment_classes`;
  - `valid_intents`, `global_intents`, `nlu_intent_set`;
  - `NLUProcessor`, `sentiment`, the semantic gate, `active_risks`;
  - `kkb`, or the `blue-dots-economy` domain.

  The GHCR org path `ghcr.io/blue-dots-economy/...` is the org name, not the domain: leave it. Generic "e.g. kkb" examples in docstrings become "e.g. blue-dots".
- `NLUResult` stays as the routing contract: `intent`, `entities`, `confidence` (1.0 derived / 0.0 fallback), `user_state`, `active_risks` removed, `sentiment` removed.
- `signal_intents` stays as the signal-name → Signal-type map, and `signals` lists the names NLU may emit.
- Runtime ↔ dev-kit sync (`.claude/rules/runtime-devkit-sync.md`): every runtime schema change updates, in the same task:
  - the dev-kit domain mirror (`dev-kit/dev_kit/schemas/domain/agent_core.py`);
  - the flat `dev-kit/dev_kit/schema.py`;
  - `FIELD_RULES`;
  - `dev-kit/dpg/agent_core.yaml`.
- Docstrings Google-style; ABC before implementation; logs never carry caller text or slot values.
- Coverage ≥ 70% for `agent_core`.
- Baselines (not yours to fix):
  - agent_core has 1 pre-existing UserWarning (`OutputFormat.schema`, chat_provider/types.py:129);
  - dev-kit has 1 pre-existing failure (`test_dpg_yaml_validates[reach_layer]`).
- The repo is public: no vulnerability details in commits. Do not push, open PRs or merge.

## Review Focus

1. **User state stays sticky through failures.** A fallback NLU result (timeout or bad JSON) must keep the previous user state, not reset it to the default. Pinned in Task 2.
2. **A language-switch request for an unsupported language** must be rejected by the existing handler's unsupported-language message, not silently stored. Pinned in Task 3.
3. **A turn whose NLU call fails** on either path still routes on existing state, with `any_input`, and no writes. Pinned in Task 4 (parity tests).
4. **A config that still carries a removed key** (`intents`, `valid_intents`, `mode`, …) fails at startup with a schema error naming the key. Pinned in Task 7.
5. **The dev-kit wizard** loads and validates the Blue Dots config with no intent fields, and doesn't crash when a phase used to ask for intents. Pinned in Task 8.

---

## File Structure

| File | Change |
|---|---|
| `dev-kit/configs/kkb/`, `dev-kit/configs/blue-dots-economy/`, `agent_core/designs/kkb_agent_workflow_config.md` | Delete (Task 1) |
| `agent_core/src/understanding/{config,models,dialogue_act_nlu,frame,understander}.py` | User state (Task 2) |
| `agent_core/src/schema/config.py` | Framework-intent exemption (Task 3); key removals (Task 7) |
| `dev-kit/configs/blue-dots/agent_core.yaml` | Language row/slot/topic (Task 3); remove old keys (Task 7); drop `semantic_gate` (Task 5) |
| `agent_core/src/orchestrator.py` | Single path (Task 4); `ke_context` sentiment removal (Task 6) |
| `agent_core/tests/fakes.py` (new) | `fake_understander` test helper (Task 4) |
| `agent_core/src/preprocessing/nlu_processor.py`, its tests, the turn-assembler semantic gate | Delete (Task 5) |
| `agent_core/src/models.py`, KE interfaces and clients, `knowledge_engine/src/*` | Remove sentiment (Task 6) |
| `agent_core/src/workflow_loader.py` | Remove intent rules and fields (Task 7) |
| `dev-kit/dev_kit/**` | Mirrors in Task 7; wizard strip in Task 8 |
| `agent_core/eval/nlu/*` | Single mode (Task 9) |
| `ARCHITECTURE.md`, `CLAUDE.md`, READMEs, `.env.local.example`, `dev-kit/dev_kit/agent/app.py` defaults | Docs and defaults (Tasks 1 and 10) |

---

### Task 1: Remove the `kkb` and `blue-dots-economy` domains

**Files:**
- Delete: `dev-kit/configs/kkb/`, `dev-kit/configs/blue-dots-economy/`, `agent_core/designs/kkb_agent_workflow_config.md`
- Modify: every test, fixture, default and doc that names them. Find them with:
  `grep -rnE "configs/kkb|configs/blue-dots-economy|\bkkb\b|DOMAIN:-kkb|blue-dots-economy" --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs . | grep -v "ghcr.io/blue-dots-economy"`
- Test: the affected test files

**Interfaces:**
- Consumes: none
- Produces: no remaining reference to either domain, apart from the GHCR org name and `docs/superpowers/` history.

- [ ] **Step 1: Inventory.** Run the grep above and save its output to the report. Classify each hit as:
  - (a) a test or fixture loading the config;
  - (b) a runtime default (`.env.local.example` `CONFIG_FOLDER`, `dev-kit/dev_kit/agent/app.py` `${DOMAIN:-kkb}` (2 sites), `dev_kit/loader.py` CLI examples);
  - (c) docs and docstring examples;
  - (d) knowledge-engine document sources under the kkb folder.
- [ ] **Step 2: Repoint (a).** Tests that load `kkb` or `blue-dots-economy` load `dev-kit/configs/blue-dots` instead. If a test asserts kkb-specific content (subagent names, KE sources, prompts), rewrite it against blue-dots content. If it only covered kkb-only features with no blue-dots equivalent, delete it and list it in the report with the reason.
- [ ] **Step 3: Repoint (b) and (c).** Defaults point to `blue-dots` (`${DOMAIN:-blue-dots}`, `CONFIG_FOLDER=…/dev-kit/configs/blue-dots`). Docstring "e.g. kkb" becomes "e.g. blue-dots". `dev-kit/README.md`, `CLAUDE.md` and `ARCHITECTURE.md` name `blue-dots` as the reference domain. Leave ARCHITECTURE's long kkb journey section for Task 10, but fix every path reference to `configs/kkb` now.
- [ ] **Step 4: Delete** the two config folders and the kkb design doc (`git rm -r`).
- [ ] **Step 5: Verify.**
  - Re-run the Step 1 grep: only the GHCR org path and `docs/superpowers/` may remain.
  - Then run `cd agent_core && uv run pytest -q`, `cd dev-kit && uv run pytest -q`, and each other module whose tests you touched.
  - Expected: no new failures against the baselines.
- [ ] **Step 6: Commit** — `git commit -m "chore: remove the kkb and blue-dots-economy domains; blue-dots is the reference domain"`

---

### Task 2: User-state classification in the dialogue-act NLU

**Files:**
- Modify: `agent_core/src/understanding/config.py`, `models.py`, `dialogue_act_nlu.py`, `frame.py`, `understander.py`
- Modify: `agent_core/src/orchestrator.py` (`_turn_context` passes the previous state)
- Test: `agent_core/tests/understanding/test_user_state.py` (new), plus existing understanding tests

**Interfaces:**
- Consumes: `src.models.UserStateClassification(id: str, confidence: float)`; config `conversation.user_state_model` (`enabled`, `default_state`, `states[{id, signals, guidance}]`) and `preprocessing.nlu_processor.user_state_confidence_threshold`.
- Produces:
  - `DialogueActConfig.user_states: tuple[dict, ...]` (empty when the model is disabled), `.user_state_default: str`, `.user_state_threshold: float`;
  - `DialogueActResult.user_state_id: str | None`, `.user_state_confidence: float | None`;
  - `FrameBuilder.build(..., previous_state: str | None = None)` renders `previous_state: <id>` inside `<frame>` when given;
  - `TurnContext.previous_user_state: str | None = None`;
  - `understand()` sets `nlu_result.user_state`.

- [ ] **Step 1: Failing tests.**

```python
# agent_core/tests/understanding/test_user_state.py
"""User-state classification moved into the dialogue-act NLU (spec §16)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import build_output_schema, build_system_prompt_text
from src.understanding.frame import FrameBuilder
from src.understanding.models import DialogueActResult
from src.understanding.understander import TurnContext, TurnUnderstander

CFG = {
    "conversation": {"user_state_model": {"enabled": True, "default_state": "fog", "states": [
        {"id": "fog", "signals": ["पता नहीं"], "guidance": "Be gentle.\nMore."},
        {"id": "orientation", "signals": ["बताइए"], "guidance": "Give options."}]}},
    # "mode" is still required until Task 7 removes the mode check; Task 7 drops it here.
    "preprocessing": {"nlu_processor": {"mode": "dialogue_act", "user_state_confidence_threshold": 0.5,
                                        "slots": {"trade": {"type": "string"}}}},
}
WF = SimpleNamespace(subagents={"s": SimpleNamespace(pending=[])})


def test_config_and_schema_include_user_state_when_enabled():
    c = DialogueActConfig.from_config(CFG)
    assert [s["id"] for s in c.user_states] == ["fog", "orientation"] and c.user_state_default == "fog"
    us = build_output_schema(c)["properties"]["user_state"]
    assert us["properties"]["id"]["enum"] == ["fog", "orientation"]
    assert set(us["required"]) == {"id", "confidence"} and us["additionalProperties"] is False
    text = build_system_prompt_text(c)
    assert "fog" in text and "Be gentle." in text and "More." not in text


def test_schema_has_no_user_state_when_disabled():
    c = DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act"}}})
    assert c.user_states == () and "user_state" not in build_output_schema(c)["properties"]


def test_frame_shows_previous_state():
    text = FrameBuilder().build(step="s", pending=None, rows=[], known=[], recent=[], segments=["x"],
                                previous_state="fog")
    assert "previous_state: fog" in text


def _und(result, reason=None):
    nlu = MagicMock()
    nlu.classify.return_value = (result, reason, 1)
    return TurnUnderstander(DialogueActConfig.from_config(CFG), WF, nlu)


def _ctx(prev):
    return TurnContext(subagent_id="s", state={}, session={}, segments=["x"], previous_user_state=prev)


def test_confident_classification_is_used():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="orientation",
                               user_state_confidence=0.9)).understand(_ctx("fog"))
    assert (u.nlu_result.user_state.id, u.nlu_result.user_state.confidence) == ("orientation", 0.9)


def test_low_confidence_keeps_previous_state():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="orientation",
                               user_state_confidence=0.2)).understand(_ctx("fog"))
    assert u.nlu_result.user_state.id == "fog"


def test_unknown_or_missing_state_falls_back_to_previous_then_default():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="bogus",
                               user_state_confidence=0.9)).understand(_ctx(None))
    assert u.nlu_result.user_state.id == "fog"


def test_fallback_result_carries_no_user_state():
    u = _und(DialogueActResult.fallback(), reason="provider_error:timeout").understand(_ctx("orientation"))
    assert u.nlu_result.user_state is None          # resolve_user_state then keeps the previous payload
```

- [ ] **Step 2: Run them to verify they fail.** Run: `cd agent_core && uv run pytest tests/understanding/test_user_state.py -v`. Expected: FAIL (unknown attribute / keyword).

- [ ] **Step 3: Implement.**
  - **`config.py`.** In `from_config`, read `usm = (config.get("conversation") or {}).get("user_state_model") or {}`. When `usm.get("enabled")` is true:
    - `user_states = tuple(dict(s) for s in usm.get("states") or [])`;
    - `user_state_default = usm.get("default_state", "")`;
    - `user_state_threshold = float(nlu.get("user_state_confidence_threshold", 0.4))`.

    Otherwise use `()`, `""` and `0.4`. Add the three fields with those defaults at the end of the dataclass, and document them.
  - **`models.py`.** `DialogueActResult` gains `user_state_id: str | None = None` and `user_state_confidence: float | None = None`. `from_parsed` gains a keyword `user_state_ids: Iterable[str] = ()`. Read `parsed.get("user_state")`: when it is a dict whose `id` is in `user_state_ids` and whose `confidence` is a number, set both fields. Otherwise leave them None. Never raise on it.
  - **`dialogue_act_nlu.py`:**
    - In `build_output_schema`, when `cfg.user_states` is non-empty, add `"user_state": _obj({"id": {"type": "string", "enum": [s["id"] for s in cfg.user_states]}, "confidence": {"type": "number"}})`.
    - In `build_system_prompt_text`, when it is non-empty, add a block: `"Caller state — classify their mental state; keep previous_state (in <frame>) when the turn does not clearly shift it:"`, then one line per state: `f"- {id}: signals {' | '.join(signals) or '(none)'} — {first line of guidance}"`.
    - Pass `user_state_ids=[s["id"] for s in cfg.user_states]` to `from_parsed`.
  - **`frame.py`.** Add `previous_state: str | None = None` to `FrameBuilderBase.build` and `FrameBuilder.build`. When it is given, render `previous_state: <id>` as the last line before `</frame>`.
  - **`understander.py`:**
    - `TurnContext` gains `previous_user_state: str | None = None`, passed to `frame.build(previous_state=...)` only when `cfg.user_states` is non-empty.
    - In `_post`, when `cfg.user_states` is non-empty, set `nlu_result.user_state = UserStateClassification(id=chosen, confidence=conf)`:
      - `valid = {s["id"] for s in cfg.user_states}`;
      - `prev = ctx.previous_user_state or cfg.user_state_default`;
      - if `dialogue.user_state_id in valid and (dialogue.user_state_confidence or 0) >= cfg.user_state_threshold`, use that id with that confidence;
      - otherwise use `prev` with confidence `dialogue.user_state_confidence or 0.0`.
    - `_fallback` leaves `user_state` None.
  - **`orchestrator.py` `_turn_context`.** Add `previous_user_state=` set to the id read from `bundle.session.get("user_state")` when it is a dict, else `self._user_state_default or None`. That is the same value the old path computed as `pre_previous_user_state_id` / `previous_user_state_id`.

- [ ] **Step 4: Run tests.** `cd agent_core && uv run pytest tests/understanding tests/test_backwards_compat_user_state.py tests/test_user_state_resolver.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(agent-core): classify user state in the dialogue-act NLU"`

---

### Task 3: Language switch as a derived intent

**Files:**
- Modify: `agent_core/src/schema/config.py` (`_check_dialogue_act_rules`), the same rule in `dev-kit/dev_kit/schemas/domain/agent_core.py` if mirrored, and `dev-kit/configs/blue-dots/agent_core.yaml`
- Test: `agent_core/tests/test_schema_config.py`, `agent_core/tests/test_blue_dots_dialogue_act_config.py`, `agent_core/tests/test_stream_turn_dialogue_act.py`

**Interfaces:**
- Consumes: the existing orchestrator language-switch handling, which runs after NLU on both paths when `nlu_result.intent == "language_switch_request"` and reads `nlu_result.entities["language_preference"]`.
- Produces:
  - `_FRAMEWORK_HANDLED_INTENTS = frozenset({"language_switch_request"})` in `schema/config.py`, exempt from the "intent must be routed" check.
  - Blue Dots config: topic `language`; slot `language_preference: {type: enum, values: [english, hindi], description: "जिस भाषा में बात करनी है"}`; act row `{acts: [request_change], topic: language, intent: language_switch_request}`; and two examples.

- [ ] **Step 1: Failing tests.**
  - In `test_schema_config.py`: a `_da_base()` variant adds topic `language` and a row `{"acts": ["request_change"], "topic": "language", "intent": "language_switch_request"}` without any routing rule for it, and still validates.
  - In `test_blue_dots_dialogue_act_config.py`: use the real config with the scripted NLU from the journey test. A turn `R("request_change", relation="new_topic", topic="language", language_preference="english")` in `job_match` derives `language_switch_request` with `entities["language_preference"] == "english"`.
  - Also there: an unsupported value (`"tamil"`) is rejected by enum normalisation and does not derive a `language_preference` entity.
  - In `test_stream_turn_dialogue_act.py`: a fake understanding with intent `language_switch_request` and `entities={"language_preference": "english"}` makes the stream path's existing handler set `bundle.session["language_preference"] = "english"`.
  - Also there: with `"tamil"` and supported languages `[english, hindi]`, the handler yields the unsupported-language message.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
  - Add the constant. Change the routed-intent check to `if row.intent not in routed and row.intent not in _FRAMEWORK_HANDLED_INTENTS`, and do the same in the dev-kit cross-check if one exists.
  - Author the Blue Dots topic, slot, row, and two examples:
    - `{ pending: select_job, caller: "इंग्लिश में बात करो", out: { acts: [request_change], relation: new_topic, topic: language, slots: { language_preference: english } } }`
    - `{ pending: age, caller: "हिंदी में बोलिए", out: { acts: [request_change], relation: new_topic, topic: language, slots: { language_preference: hindi } } }`
- [ ] **Step 4: Run tests** → PASS. Run `cd dev-kit && uv run pytest -q` too.
- [ ] **Step 5: Commit** — `git commit -m "feat: language switch as a dialogue-act derived intent"`

---

### Task 4: One understanding path in the orchestrator (both paths)

The orchestrator stops branching on the mode. Understanding always runs, and the old NLU call, the old entity loop and the old signal block go. Most of the work is migrating the tests that stubbed `_nlu_processor`.

**Files:**
- Create: `agent_core/tests/fakes.py`
- Modify: `agent_core/src/orchestrator.py`
- Modify: every agent_core test that sets `agent._nlu_processor`, `_dialogue_cfg` or `_understander`, or asserts NLUProcessor call arguments. The main ones are `test_orchestrator.py`, `test_stream_turn.py`, `test_turn_path_identity_parity.py`, `test_stream_turn_dialogue_act.py`, `test_turn_carryover_replay.py`, `test_stream_turn_lifecycle.py`, `test_trust_output_batching.py`, `test_backwards_compat_*.py` and `test_turn_assembler_integration.py`. Find them all with `grep -rln "_nlu_processor\|_dialogue_cfg\|_understander\|_nlu_chat_provider" agent_core/tests`.

**Interfaces:**
- Consumes: `TurnUnderstander.from_config` (Task 2), `_turn_context`, `_apply_understanding_async` / `_apply_understanding_sync`.
- Produces:
  - `AgentCore.__init__(..., nlu_chat_provider: ChatProviderBase | None = None)`. When None, the orchestrator builds the dedicated provider with `_build_dialogue_act_provider()`; tests pass a mock.
  - `self._understander` and `self._dialogue_cfg` are always set.
  - `tests/fakes.py: fake_understander(nlu_result: NLUResult | None = None, *, writes: Sequence[StateWrite] = (), signals: Sequence[str] = ()) -> MagicMock`. Its `.understand()` returns `TurnUnderstanding(nlu_result=nlu_result or NLUResult(intent="any_input", entities={}, confidence=1.0), dialogue=DialogueActResult(acts=("other",), relation="unclear"), writes=list(writes), signals=list(signals))`.

- [ ] **Step 1: Write the helper and one failing guard test.**

```python
# agent_core/tests/fakes.py
"""Shared test doubles for the single (dialogue-act) NLU path."""
from __future__ import annotations

from typing import Sequence
from unittest.mock import MagicMock

from src.models import NLUResult
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding


def fake_understander(nlu_result: NLUResult | None = None, *, writes: Sequence[StateWrite] = (),
                      signals: Sequence[str] = ()) -> MagicMock:
    """A TurnUnderstander stand-in whose understand() returns a fixed TurnUnderstanding."""
    u = MagicMock()
    u.understand.return_value = TurnUnderstanding(
        nlu_result=nlu_result or NLUResult(intent="any_input", entities={}, confidence=1.0),
        dialogue=DialogueActResult(acts=("other",), relation="unclear"),
        writes=list(writes), signals=list(signals))
    return u
```

  In `tests/test_orchestrator.py`, add a guard test. It builds `_make_agent()` with a config whose `preprocessing.nlu_processor` has no `mode` key, and asserts:
  - `agent._understander` is not None;
  - `not hasattr(agent, "_nlu_processor")`;
  - one `process_turn` calls `agent._understander.understand` once.

  `NLUResult(intent=..., entities=..., confidence=...)` without `sentiment` only works after Task 6. Until then, pass `sentiment="neutral"` in `fakes.py` and in new tests. Task 6 removes it everywhere.
- [ ] **Step 2: Run the guard test to verify it fails** (`_nlu_processor` still exists).
- [ ] **Step 3: Orchestrator changes.**
  - **`__init__`:**
    - Remove `self._nlu_chat_provider`, `self._nlu_processor` and the `NLUProcessor` import.
    - Always set `self._dialogue_cfg = DialogueActConfig.from_config(self._config)`. Until Task 7 removes the mode check, call it with a mode-independent path: add a `from_config(config, *, require_mode: bool = True)` keyword in Task 4 and pass `require_mode=False`. Task 7 removes the keyword.
    - Always set `self._understander = TurnUnderstander(self._dialogue_cfg, self._workflow, DialogueActNLU(self._dialogue_cfg, nlu_chat_provider or self._build_dialogue_act_provider()))`.
    - Keep `_build_helper_provider` (the language normaliser still uses it).
  - **Stream path (`_stream_turn_impl`):**
    - Delete the `elif _ln_enabled_s:` and `else:` NLUProcessor branches, keeping only the understander branch, now unconditional.
    - Delete the pre-NLU argument block (`pre_allowed_intents`, `pre_existing_profile_keys`, `pre_previous_user_state_payload/id`). Keep the previous user-state payload/id lookup if `_handle_user_state_turn` still needs `previous_payload` / `previous_state_id`; move it next to that call.
    - In the consent replay, keep only the understander branch.
    - Delete the legacy entity loop's `else` branch, but keep the `entity_map` / `entity_scope` / `supported_langs` assignments, which later code reads.
    - Delete the "build `TurnToolCache` late" fallback: the cache is always built after bootstrap.
    - Delete the `if nlu_result.active_risks:` constraint-assembly block.
    - Every remaining `if self._understander is not None` / `if understanding is not None` becomes unconditional.
  - **Sync path (`_process_turn_inner`):**
    - Delete the NLUProcessor call and its "→ LLM call (model=…)" log, and the pre-NLU argument block (`allowed_intents`, `existing_profile_keys`, the profile-key collection).
    - Delete the legacy entity loop and the `signal_intents` → Signal block.
    - Delete the `active_risks` block.
    - Make the understander branch and the cache construction unconditional.
    - Keep the `[STEP 5] ✓` log with keys only.
  - **`_persist_interrupted` / end-of-turn:** `recent_turns` and `served_tool_results` writes become unconditional.
- [ ] **Step 4: Migrate the tests.**
  - `_make_agent` (test_orchestrator.py) and `_make_agent_core` (test_stream_turn.py) pass `nlu_chat_provider=MagicMock()`, then set `agent._understander = fake_understander(nlu_result)`, using the `nlu_result` param where the factory has one.
  - Every test that did `agent._nlu_processor = MagicMock(); agent._nlu_processor.process.return_value = X` now does `agent._understander = fake_understander(X)`.
  - Tests that asserted entity writes from `nlu_result.entities` now pass the same values as `writes=[StateWrite(scope, key, value)]` and keep their assertions.
  - Tests that assert NLUProcessor-specific inputs (normalised vs raw text, `allowed_intents`, `existing_profile_keys`) are converted to assert `agent._understander.understand.call_args.args[0].segments` where meaningful. Otherwise delete them and list each one in the report.
  - The `_nlu_chat_provider` tests in `test_orchestrator.py` (about lines 300–355) are rewritten to assert the dedicated dialogue-act provider: a new instance with the NLU `timeout_ms` / `retry_attempts`, `sdk_max_retries: 0` and `retry_on_timeout: False`. Patch `build_chat_provider` to capture its argument.
  - `test_stream_turn_dialogue_act.py` and the parity tests drop their manual `_dialogue_cfg` / `_understander` setup where the factory now provides it. Keep their assertions.
- [ ] **Step 5: Run.** `cd agent_core && uv run pytest -q` → all PASS. `tests/test_nlu_processor.py` is the exception: it may still pass on its own, and Task 5 deletes it. Then run `grep -n "_nlu_processor\|NLUProcessor\|_nlu_chat_provider\|active_risks" agent_core/src/orchestrator.py` → no hits.
- [ ] **Step 6: Commit** — `git commit -m "refactor(agent-core): one understanding path on sync and stream; drop the intent-mode branches"`

---

### Task 5: Delete `NLUProcessor` and the turn-assembler semantic gate

**Files:**
- Delete: `agent_core/src/preprocessing/nlu_processor.py`, `agent_core/tests/test_nlu_processor.py`
- Modify:
  - `agent_core/src/turn_assembler.py`: remove the `nlu_processor` ctor param, `_semantic_gate`, the `semantic_gate` defaults, config merge and dispatch;
  - `agent_core/main.py`;
  - `agent_core/src/schema/config.py`: remove `SemanticGateConfig` and the `semantic_gate` field;
  - the dev-kit mirrors and flat schema of `semantic_gate`, its FIELD_RULES, and its `dev-kit/dpg/*.yaml` defaults;
  - `dev-kit/configs/blue-dots/agent_core.yaml`: remove the `semantic_gate:` block (~line 3149).
- Test: `agent_core/tests/test_turn_assembler*.py`, dev-kit tests

**Interfaces:**
- Consumes: Task 4 (nothing imports `NLUProcessor` any more)
- Produces: `TurnAssembler(...)` has no `nlu_processor` parameter; `semantic_gate` is not a valid config key.

- [ ] **Step 1: Failing test.** In `test_schema_config.py`, a config with `channels.<voice>.turn_assembler.semantic_gate` (use the real nesting from the current schema) raises a ValidationError naming `semantic_gate`. In `test_turn_assembler.py`, `TurnAssembler(...)` raises `TypeError` if it is passed `nlu_processor=`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Delete and adjust.**
  - Delete the files and code listed above.
  - Turn-assembler tests that exercised the gate are deleted and listed in the report. The other turn-assembler tests drop the `nlu_processor=` argument.
  - Update `preprocessing/__init__.py` exports if it re-exports `NLUProcessor`.
- [ ] **Step 4: Run** the agent_core and dev-kit suites → PASS (baselines only). Then `grep -rn "NLUProcessor\|nlu_processor\.py\|semantic_gate\|SemanticGate" agent_core dev-kit --exclude-dir=.venv` → no hits outside `docs/`.
- [ ] **Step 5: Commit** — `git commit -m "refactor: delete NLUProcessor and the unused turn-assembler semantic gate"`

---

### Task 6: Remove sentiment end to end

**Files:**
- Modify:
  - `agent_core/src/models.py` (`NLUResult.sentiment` removed; docstrings no longer mention the NLU Processor);
  - `agent_core/src/orchestrator.py` (`ke_context["sentiment"]`, sentiment log args, the `NLUResult(...)` default);
  - `agent_core/src/understanding/understander.py` (both `NLUResult(...)`);
  - `agent_core/src/manager_agent.py` (`ke_context.get("sentiment")` passed to KE);
  - `agent_core/src/interfaces/knowledge_engine.py`, `interfaces/async_/knowledge_engine.py`, `http_clients/knowledge_engine.py`, `http_clients/async_/knowledge_engine.py` (the `sentiment` parameter);
  - `knowledge_engine/src/models.py`, `base.py`, `engine.py` (the `sentiment` field and parameter).
- Test: every test constructing `NLUResult(..., sentiment=...)` (about 46 sites; `grep -rn "sentiment" agent_core/tests knowledge_engine/tests`), plus the KE tests.

**Interfaces:**
- Consumes: Task 4's `fakes.py`, which passes `sentiment` until now.
- Produces: `NLUResult(intent: str, entities: dict, confidence: float, user_state: UserStateClassification | None = None)` — no `sentiment` and no `active_risks`. Remove `active_risks` here too if Task 4 left the field; it is read nowhere after Task 4. KE `retrieve(...)` and request models have no `sentiment`.

- [ ] **Step 1: Failing test.** In `agent_core/tests/test_models.py`, `NLUResult(intent="x", entities={}, confidence=1.0)` constructs, and `not hasattr(NLUResult(...), "sentiment")`. In `knowledge_engine/tests`, a request model built without `sentiment` has no such field.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Remove** the field and parameter at every listed site. Drop `sentiment=` from every test construction, and from the KE client tests' expected payloads.
- [ ] **Step 4: Run** the agent_core and knowledge_engine suites → PASS. Then `grep -rn "sentiment" agent_core/src knowledge_engine/src agent_core/tests knowledge_engine/tests` → no hits.
- [ ] **Step 5: Commit** — `git commit -m "refactor: remove sentiment from the NLU result and the knowledge-engine request"`

---

### Task 7: Remove the old config keys and loader rules (runtime and dev-kit mirrors)

**Files:**
- Modify:
  - `agent_core/src/schema/config.py`:
    - `NLUProcessorConfig`: remove `mode`, `intents`, `entities`, `domain_instruction`, `confidence_threshold` and `sentiment_classes`, and update its docstring;
    - `SubAgent`: remove `valid_intents`;
    - `AgentWorkflowConfig`: remove `global_intents`;
    - `_check_dialogue_act_rules`: runs unconditionally, with no `mode` early return.
  - `agent_core/src/workflow_loader.py`:
    - remove `SubAgent.valid_intents`, `AgentWorkflow.global_intents` and `nlu_intent_set`;
    - remove `_load_nlu_intents`, `_dialogue_act_intents`, `_validate_subagent_intents`, `_validate_global_intents_not_in_subagents` and `_build_nlu_intent_set`;
    - remove the parsing of `valid_intents` and `global_intents`;
    - the mode gate in `load()` goes;
    - renumber the "validation rules" comment.
  - `agent_core/src/understanding/config.py`: `from_config` no longer checks `mode` and always returns a config, so drop `require_mode`. `TurnUnderstander.from_config` always returns an understander, so update its docstring.
  - `dev-kit/dev_kit/schemas/domain/agent_core.py` and `dev-kit/dev_kit/schema.py`: the same field removals, plus removal of the `intents_required_in_intent_mode` validators and any intent-related workflow validators (e.g. "global intents must not appear in valid_intents").
  - `dev-kit/dpg/agent_core.yaml`: remove `mode` and any intent defaults.
  - `agent_core/eval/nlu/offline.py`: `load_merged_config(domain_dir)` stops injecting `mode` (the key is now forbidden). Its callers pass one argument: `eval/nlu/run.py` and `tests/test_blue_dots_dialogue_act_config.py`. Task 9 does the rest of the harness cleanup.
  - Remove `"mode": "dialogue_act"` from every test config that set it, e.g. `tests/understanding/test_user_state.py`, `test_config.py`, `test_understander.py` and `test_dialogue_act_nlu.py` (find them with `grep -rn '"mode"' agent_core/tests`).
  - `agent_core/config/dpg.yaml`: the same, if present.
  - `dev-kit/configs/blue-dots/agent_core.yaml`:
    - remove `mode`, `intents`, `entities`, `domain_instruction`, `confidence_threshold`, `sentiment_classes`;
    - remove every subagent `valid_intents` and `agent_workflow.global_intents`;
    - keep `global_routing`, `signal_intents`, `user_state_confidence_threshold`, `provider`, `model` and `log_raw_response`;
    - delete comments that explain removed keys.
- Test: `test_schema_config.py`, `test_workflow_loader.py` (rewrite `_minimal_config()` without intents), `test_blue_dots_dialogue_act_config.py` (drop the intent-mode test; `_load()` takes no mode), `tests/understanding/test_config.py`, and dev-kit schema tests.

**Interfaces:**
- Consumes: Tasks 4–6, after which nothing reads these fields.
- Produces: `DialogueActConfig.from_config(config) -> DialogueActConfig` (never None).

- [ ] **Step 1: Failing tests.**
  - **Schema.** Parametrized over each removed key in its real location, `MergedConfig.validate_full` raises a ValidationError whose message names the key. The keys are:
    - `preprocessing.nlu_processor.mode`, `.intents`, `.entities`, `.domain_instruction`, `.confidence_threshold`, `.sentiment_classes`;
    - `agent_workflow.subagents[0].valid_intents`;
    - `agent_workflow.global_intents`.
  - **Loader.** It loads a config with no NLU intents, and `AgentWorkflow` has no `nlu_intent_set` attribute.
  - **Understanding config.** `DialogueActConfig.from_config({})` returns a config with empty slots.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Remove** the fields, rules and validators listed above, then update the Blue Dots config.
- [ ] **Step 4: Run** the agent_core and dev-kit suites → PASS (baselines only). Then:
  `grep -rnE "valid_intents|global_intents|nlu_intent_set|domain_instruction|sentiment_classes|confidence_threshold|\bmode\b.*dialogue_act|dialogue_act.*\bmode\b" agent_core/src dev-kit/dev_kit dev-kit/dpg dev-kit/configs`
  must return no hits. The termination short-circuit's own `confidence_threshold` under `agent.termination_short_circuit` is unrelated and stays. Note it in the report, and exclude it from the check with a precise pattern.
- [ ] **Step 5: Commit** — `git commit -m "refactor: remove intent-mode config keys and workflow-loader rules"`

---

### Task 8: Strip intents and entities from the dev-kit wizard

**Files:**
- Modify:
  - `dev-kit/dev_kit/agent/field_rules/agent_core.py`: remove the rules for `preprocessing.nlu_processor.mode`, `.intents`, `.entities`, `.domain_instruction`, `.confidence_threshold` and `.sentiment_classes`, plus `agent_workflow.global_intents` and any rule naming `valid_intents`; drop removed paths from other rules' `invalidated_by`;
  - `field_rules/knowledge_engine.py` if it invalidates on intents;
  - `phase_prompts/language.py`, `workflow.py`, `review.py`, `knowledge.py`, `_helpers.py`;
  - `tools.py`, `router.py`, `renderer.py`, `phase_driver.py`, `skeleton.py`, `derived_fields.py` (wherever intents/entities are seeded, cascaded, rendered or asked about);
  - `dev-kit/dev_kit/schemas/cross_block_validation.py`: intent checks such as global intents vs routing, or subagent valid_intents vs NLU intents;
  - `dev-kit/dev_kit/schemas/domain/knowledge_engine.py`: `intent_filters` keys checked against NLU intents. Re-target that check to `act_intents` intents, or drop it if no cross-block source remains, and say which in the report.
  - The field-rules expected-paths test and its catalogue: `docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md`. Remove the rows; this is the one docs file allowed here.
- Test: `dev-kit/tests/**` (about 23 files mention the removed concepts; `grep -rlE "intents|entities|domain_instruction|valid_intents|global_intents" dev-kit/tests`).

**Interfaces:**
- Consumes: Task 7's mirror removals.
- Produces: the wizard never asks for or writes intents or entities. Phase prompts that used to collect them say: "NLU slots, act→intent rows and pending questions are authored by hand in agent_core.yaml for now (see spec §16)."

- [ ] **Step 1: Failing tests.**
  - The field-rules registry has no key containing `intents`, `entities` or `domain_instruction`, apart from KE `intent_filters` if it is kept.
  - The rendered prompts for the language and workflow phases contain no occurrence of `intents`.
  - An end-to-end skeleton build for a minimal intake produces no `preprocessing.nlu_processor.intents` path.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Strip** each listed site. Where removing intents leaves a phase with nothing to ask about NLU, replace that ask with the one sentence above. Update or delete the tests; list every deleted test in the report with the reason.
- [ ] **Step 4: Run** `cd dev-kit && uv run pytest -q` → PASS (baseline only). Then `grep -rnE "intents|\bentities\b|domain_instruction|valid_intents|global_intents" dev-kit/dev_kit` → only `intent_filters` (if kept) and `act_intents` remain.
- [ ] **Step 5: Commit** — `git commit -m "refactor(dev-kit): stop asking for intents and entities; dialogue-act blocks are hand-authored for now"`

---

### Task 9: Single-mode eval harness

**Files:**
- Modify: `agent_core/eval/nlu/adapters.py` (remove `predict_intent`), `run.py` (remove `--mode` and the NLUProcessor import). `offline.py` was already changed in Task 7.
- Modify: `agent_core/tests/eval/test_nlu_eval.py`, `agent_core/tests/test_blue_dots_dialogue_act_config.py` (`_load()` calls)

**Interfaces:**
- Consumes: Task 7 (no mode key)
- Produces: `uv run python -m eval.nlu.run --config <dir> --cases <path> --repeat 3 --out <file>` and `--compare A B`; `score(cases, preds)` (drop the `mode` parameter; acts/relation/topic/pending are always scored).

- [ ] **Step 1: Failing tests.**
  - `score(cases, preds)` works with no `mode` and scores `acts` when they are expected.
  - `main(["--config", …, "--mode", "intent", …])` exits with argparse's error (`SystemExit` code 2).
  - `load_merged_config(BLUE_DOTS)` takes one argument.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the removals. `gate()` is unchanged.
- [ ] **Step 4: Run** `cd agent_core && uv run pytest tests/eval tests/test_blue_dots_dialogue_act_config.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "refactor(eval): single-mode NLU harness; the baseline comparison runs outside the repo"`

---

### Task 10: Documentation

**Files:**
- Modify:
  - `ARCHITECTURE.md`: the runtime turn sequence and Agent Core section describe dialogue-act understanding (pending question → frame → strict NLU → post-processing → `<caller_turn>`); replace the kkb journey section with a short pointer to the Blue Dots config and spec §16;
  - `CLAUDE.md`: Agent Core paragraph and runtime sequence;
  - `agent_core/README.md` and `dev-kit/README.md`, where they describe NLU intents/entities or a kkb walkthrough;
  - module docstrings in `agent_core/src/models.py` and `understanding/__init__.py` that still say "opt-in" or "dialogue_act mode".
- Test: none (docs). Run the full suites once at the end.

**Interfaces:**
- Consumes: everything above
- Produces: docs consistent with the code.

- [ ] **Step 1:** `grep -rnE "NLU Processor|NLUProcessor|intent mode|dialogue_act mode|opt-in|valid_intents|kkb" ARCHITECTURE.md CLAUDE.md agent_core/README.md dev-kit/README.md agent_core/src` and list the hits.
- [ ] **Step 2:** Rewrite each hit to match §16. The opt-in wording in `docs/superpowers/specs/…` is history and stays; §16 already supersedes it.
- [ ] **Step 3: Final verification.**
  - Run `cd agent_core && uv run pytest --cov=src -q`, `cd dev-kit && uv run pytest -q` and `cd knowledge_engine && uv run pytest -q` → PASS (baselines only), agent_core coverage ≥ 70%.
  - Run the global grep from Global Constraints → only the GHCR org path and `docs/superpowers/` history remain.
- [ ] **Step 4: Commit** — `git commit -m "docs: describe the single dialogue-act NLU path"`
