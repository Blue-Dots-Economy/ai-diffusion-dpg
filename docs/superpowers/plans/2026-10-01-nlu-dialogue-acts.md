# NLU Dialogue Acts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `dialogue_act` NLU mode. The NLU classifies what the caller did (acts + relation to the pending question) using the context it needs. Deterministic code then validates slots, resolves references against stored tool results, derives the routing intent, gates termination and hands a structured `<caller_turn>` to the main LLM. A replay eval harness measures it against today's `intent` mode.

**Architecture:**
- A new pure package `agent_core/src/understanding/` does everything between the memory read and routing. Its parts:
  - pending-question resolution;
  - frame rendering;
  - one strict-JSON LLM call;
  - post-processing (normalise → accept → resolve → derive → off-track);
  - a write plan.
- It performs no I/O apart from the LLM call. The orchestrator applies the returned `StateWrite`s through its existing Memory Layer helpers on both paths.
- `intent` mode is untouched. In `dialogue_act` mode, the orchestrator:
  - builds `TurnToolCache` before NLU;
  - calls `TurnUnderstander.understand()` instead of `NLUProcessor.process()`;
  - skips the legacy entity loop;
  - renders `<caller_turn>` in Tier 3.

**Tech Stack:** Python 3.11+, pydantic v2, pytest + pytest-asyncio, OpenAI/Anthropic SDKs, OpenTelemetry metrics, `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-01-nlu-dialogue-acts-design.md`. Read it before starting. Section references (§) below are to that spec.

## Global Constraints

- Baseline: `origin/deploy/voicera-vm` at `9efa8cc` (Spec A #421 and Spec B #424 merged). At execution time, cut `feat/nlu-dialogue-acts` from it in a separate worktree (superpowers:using-git-worktrees). Never work in the user's `ai-diffusion-dpg` checkout.
- Run tests per module: `cd agent_core && uv run pytest …`, `cd dev-kit && uv run pytest …`.
- Every new or changed public class, method and function gets a Google-style docstring (`.claude/rules/code-documentation.md`). Every core component gets an ABC before its implementation (`.claude/rules/base-class-pattern.md`).
- Structured logs carry `operation`, `status` and `latency_ms`. Never log caller text, slot values, phone numbers or raw `user_id`; log keys, counts, ids and reasons only (`.claude/rules/logging-observability.md`).
- Coverage stays ≥ 70% for `agent_core`.
- `mode: intent` (the default) must behave **exactly** as today. `agent_core/tests/test_nlu_processor.py` must pass unchanged.
- Runtime schema `agent_core/src/schema/config.py` may import only `pydantic`, `enum`, `typing`, `__future__`. No imports from `src.understanding`.
- Any runtime schema change updates, in the same PR: the dev-kit domain mirror, the flat `dev-kit/dev_kit/schema.py`, `FIELD_RULES`, and `dev-kit/dpg/agent_core.yaml` (`.claude/rules/runtime-devkit-sync.md`).
- Dialogue acts: `affirm, deny, acknowledge, provide_info, correct, select, ask, request_change, repeat, hold, close, other`. Relations: `answers_pending, answers_other, new_topic, unrelated, unclear`.
- Dialogue-act NLU client: `timeout_ms` default 2500, `retry_attempts` default 2 (total attempts), `sdk_max_retries: 0`, `retry_on_timeout: false`.
- Routing conditions keep the existing shape `{field, operator ∈ eq|not_eq|in|lt|gt, value}`. "Unset" is written `operator: in, value: [null, ""]`.
- The sync (`process_turn`) and stream (`stream_turn`) paths must behave identically in `dialogue_act` mode, and both send the **raw** caller text to NLU.
- The repo is public: no vulnerability details in commits. Real-call eval cases are never committed.
- Do not push, open PRs or merge without the user's explicit per-action approval.

## Review Focus

These are the failure modes most likely to bite, with the task that pins each one:
1. **A caller says "धन्यवाद" / "ठीक है" while the submit question is pending.** It must never become `apply_now` or `termination_intent`. Pinned in Task 13 (relation guard and gate) and Task 16 (end to end through `understand`).
2. **The provider times out or returns invalid JSON.** The turn must still proceed in under ~2.5 s with `any_input`, the pending question kept and no termination. Pinned in Tasks 1 and 10 (no retry on timeout, fallback) and Task 16.
3. **A stale stored profile value vs a spoken correction, plus the age-0 seed.** A slot written by NLU this session must win, and a seeded `0` / `""` in session must never outrank a real profile value. Pinned in Task 14.
4. **"पहले वाला" after a second search.** It must resolve against the **latest** `fetch_jobs` entry. An out-of-range option or a missing store must resolve nothing, and must not derive `job_pick`. Pinned in Tasks 7, 12 and 13.
5. **An interrupted streaming turn.** The successor's frame must show what the caller actually heard, and the carried segments as separate `[interrupted]` lines. Pinned in Task 17.

---

## File Structure

| File | Responsibility |
|---|---|
| `agent_core/src/chat_provider/openai_provider.py`, `anthropic_provider.py` | `sdk_max_retries`, `retry_on_timeout` options |
| `agent_core/src/conditions.py` (new) | Shared `evaluate_condition` / `all_conditions`, used by routing and the understanding package |
| `agent_core/src/schema/config.py` | Runtime schema: dialogue-act NLU fields, `SubAgent.pending`, cross-checks |
| `agent_core/src/workflow_loader.py` | `PendingQuestion` / `OptionsFrom` dataclasses, parsing `pending`, the dialogue-act intent set |
| `agent_core/src/tool_results.py` | `TurnToolCache.latest_entry` |
| `agent_core/src/understanding/__init__.py` (new) | Package exports |
| `agent_core/src/understanding/models.py` (new) | Result dataclasses, `ACTS`, `RELATIONS` |
| `agent_core/src/understanding/config.py` (new) | `DialogueActConfig.from_config` (parsed once at startup) |
| `agent_core/src/understanding/pending.py` (new) | `PendingResolverBase` / `PendingResolver` |
| `agent_core/src/understanding/frame.py` (new) | `FrameBuilderBase` / `FrameBuilder`, `offered_rows` |
| `agent_core/src/understanding/dialogue_act_nlu.py` (new) | Static system prompt, output schema, the LLM call, fallback |
| `agent_core/src/understanding/postprocess.py` (new) | `normalise_slots`, `accept_slots`, `resolve_reference`, `gate_passes`, `derive_intent`, `next_off_track` |
| `agent_core/src/understanding/slot_writer.py` (new) | `plan_writes` (pure write plan) |
| `agent_core/src/understanding/precedence.py` (new) | `nlu_owned_values`, the provenance-based precedence |
| `agent_core/src/understanding/history.py` (new) | `append_recent_turn` |
| `agent_core/src/understanding/caller_turn.py` (new) | `render_caller_turn` |
| `agent_core/src/understanding/understander.py` (new) | `TurnUnderstanderBase` / `TurnUnderstander`, `TurnContext`, logging and metrics |
| `agent_core/src/manager_agent.py` | `build_system_prompt(..., caller_turn="")` |
| `agent_core/src/models.py` | `TurnRecord.spoken` |
| `agent_core/src/orchestrator.py` | Construction, precedence in the three merge sites, stream and sync wiring, end-of-turn and interrupted writes |
| `agent_core/src/preprocessing/nlu_processor.py` | Docstring correction only |
| `agent_core/eval/…` (new) | Replay harness and synthetic cases |
| `dev-kit/dev_kit/schemas/domain/agent_core.py`, `dev_kit/schema.py`, `dev_kit/agent/field_rules/agent_core.py`, `dev_kit/schemas/cross_block_validation.py`, `dev-kit/dpg/agent_core.yaml` | Dev-kit sync |
| `dev-kit/configs/blue-dots/agent_core.yaml` | `fetch_jobs` cache; dialogue-act config authored with `mode: intent` kept |

---

### Task 1: Provider options `sdk_max_retries` and `retry_on_timeout`

Today a timeout is retried by our loop *and* by the SDK's own default `max_retries=2`. That is how one NLU call reached 22.8 s. Both options default to today's behaviour.

**Files:**
- Modify: `agent_core/src/chat_provider/openai_provider.py` (`__init__`, `_call_with_retry`)
- Modify: `agent_core/src/chat_provider/anthropic_provider.py` (`__init__`, the sync retry loop)
- Test: `agent_core/tests/test_chat_provider_openai.py`, `agent_core/tests/test_chat_provider_anthropic.py`

**Interfaces:**
- Consumes: none
- Produces: provider config keys `sdk_max_retries: int | None` (absent = SDK default) and `retry_on_timeout: bool` (default `True`). Used by Task 16's dedicated NLU provider.

- [ ] **Step 1: Write the failing tests (OpenAI)**

Append to `agent_core/tests/test_chat_provider_openai.py`:

```python
class _FakeTimeout(_openai.APITimeoutError):
    def __init__(self):  # noqa: D401
        pass


class TestRetryOptions:
    def test_sdk_max_retries_is_passed_to_both_clients(self):
        cfg = {**VALID_CONFIG, "sdk_max_retries": 0}
        with patch("openai.OpenAI") as sync_cls, patch("openai.AsyncOpenAI") as async_cls:
            OpenAIChatProvider(cfg)
        assert sync_cls.call_args.kwargs == {"max_retries": 0}
        assert async_cls.call_args.kwargs == {"max_retries": 0}

    def test_sdk_max_retries_absent_keeps_sdk_default(self):
        with patch("openai.OpenAI") as sync_cls, patch("openai.AsyncOpenAI"):
            OpenAIChatProvider(VALID_CONFIG)
        assert sync_cls.call_args.kwargs == {}

    def test_timeout_not_retried_when_disabled(self):
        cfg = {**VALID_CONFIG, "retry_on_timeout": False}
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(cfg)
        p._client.chat.completions.create = MagicMock(side_effect=_FakeTimeout())
        resp = p.call(ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])]))
        assert resp.stop_reason == "error"
        assert resp.error_type == "timeout"
        assert p._client.chat.completions.create.call_count == 1

    def test_rate_limit_still_retried_when_timeout_retry_disabled(self):
        cfg = {**VALID_CONFIG, "retry_on_timeout": False}
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            p = OpenAIChatProvider(cfg)
        p._client.chat.completions.create = MagicMock(
            side_effect=[_FakeRateLimit(), _mk_openai_completion(text="ok")])
        resp = p.call(ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])]))
        assert resp.stop_reason == "end_turn"
        assert p._client.chat.completions.create.call_count == 2

    def test_timeout_retried_by_default(self):
        p = _make_provider()
        p._client.chat.completions.create = MagicMock(
            side_effect=[_FakeTimeout(), _mk_openai_completion(text="ok")])
        resp = p.call(ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])]))
        assert resp.stop_reason == "end_turn"
```

(`MagicMock`, `_mk_openai_completion` and `_FakeRateLimit` already exist in this file. If `MagicMock` is not imported at module top, add `from unittest.mock import MagicMock`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_openai.py::TestRetryOptions -v`
Expected: FAIL. The kwargs assertion fails (`{}` vs `{"max_retries": 0}`), and `call_count == 2` on the timeout-disabled test.

- [ ] **Step 3: Implement (OpenAI)**

In `OpenAIChatProvider.__init__`, replace the two client lines:

```python
        # sdk_max_retries: None keeps the SDK default; NLU sets 0 so this
        # class's loop is the only retry layer (NLU dialogue-acts spec §9.1).
        sdk_max_retries = config.get("sdk_max_retries")
        client_kwargs: dict = {} if sdk_max_retries is None else {"max_retries": int(sdk_max_retries)}
        self._retry_on_timeout: bool = bool(config.get("retry_on_timeout", True))
        self._client = openai.OpenAI(**client_kwargs)
        self._async_client = openai.AsyncOpenAI(**client_kwargs)
```

In `_call_with_retry`, at the end of the `except (openai.APITimeoutError, openai.RateLimitError) as e:` block, after the existing `logger.warning(...)`:

```python
                if isinstance(e, openai.APITimeoutError) and not self._retry_on_timeout:
                    break
```

The existing code after the loop raises `_RetryableExhausted` from `last_error`. Check that it maps a timeout to `error_type="timeout"`. If it doesn't, set `error_type = "rate_limit" if isinstance(last_error, openai.RateLimitError) else "timeout"`, matching the Anthropic provider.

Document both keys in the `__init__` docstring's `config` description.

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_openai.py -v`
Expected: all PASS.

- [ ] **Step 5: Same for Anthropic. Tests first**

Append to `agent_core/tests/test_chat_provider_anthropic.py`. Mirror its existing fixtures: find the module's `VALID_CONFIG`, its provider factory, and how it fakes `messages.create`.

```python
import anthropic as _anthropic


class _FakeAnthropicTimeout(_anthropic.APITimeoutError):
    def __init__(self):  # noqa: D401
        pass


class TestAnthropicRetryOptions:
    def test_sdk_max_retries_is_passed_to_both_clients(self):
        cfg = {**VALID_CONFIG, "sdk_max_retries": 0}
        with patch("anthropic.Anthropic") as sync_cls, patch("anthropic.AsyncAnthropic") as async_cls:
            AnthropicChatProvider(cfg)
        assert sync_cls.call_args.kwargs == {"max_retries": 0}
        assert async_cls.call_args.kwargs == {"max_retries": 0}

    def test_timeout_not_retried_when_disabled(self):
        cfg = {**VALID_CONFIG, "retry_on_timeout": False}
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            p = AnthropicChatProvider(cfg)
        p._client.messages.create = MagicMock(side_effect=_FakeAnthropicTimeout())
        resp = p.call(ChatRequest(messages=[Message(role="user", content=[TextBlock(text="hi")])]))
        assert resp.stop_reason == "error"
        assert p._client.messages.create.call_count == 1
```

- [ ] **Step 6: Run to verify failure, then implement**

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py::TestAnthropicRetryOptions -v` → FAIL.

Apply the same two changes to `AnthropicChatProvider`:
- In `__init__`, use `anthropic.Anthropic(**client_kwargs)` / `anthropic.AsyncAnthropic(**client_kwargs)`.
- In the sync loop's `except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:` block, add `if isinstance(e, anthropic.APITimeoutError) and not self._retry_on_timeout: break`.

Google and Ollama are out of scope. Add one sentence to their `__init__` docstrings: "`sdk_max_retries` / `retry_on_timeout` are not honoured by this provider."

Run: `cd agent_core && uv run pytest tests/test_chat_provider_anthropic.py tests/test_chat_provider_openai.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add agent_core/src/chat_provider/openai_provider.py agent_core/src/chat_provider/anthropic_provider.py \
        agent_core/src/chat_provider/google_provider.py agent_core/src/chat_provider/ollama_provider.py \
        agent_core/tests/test_chat_provider_openai.py agent_core/tests/test_chat_provider_anthropic.py
git commit -m "feat(chat-provider): optional sdk_max_retries and retry_on_timeout"
```

---

### Task 2: Shared routing-condition evaluator

The pending resolver and the termination gate must evaluate conditions exactly as routing does. Move the logic out of `AgentCore` into a module both can import.

**Files:**
- Create: `agent_core/src/conditions.py`
- Modify: `agent_core/src/orchestrator.py` (`_evaluate_condition` delegates)
- Test: `agent_core/tests/test_conditions.py`

**Interfaces:**
- Consumes: any object with `.field`, `.operator`, `.value` (the `workflow_loader.RoutingCondition` dataclass)
- Produces: `evaluate_condition(condition, state: dict) -> bool`, `all_conditions(conditions, state: dict) -> bool` (True for an empty list)

- [ ] **Step 1: Write the failing test**

```python
# agent_core/tests/test_conditions.py
"""Tests for the shared routing-condition evaluator."""
from src.conditions import all_conditions, evaluate_condition
from src.workflow_loader import RoutingCondition


def C(field, op, value):
    return RoutingCondition(field=field, operator=op, value=value)


def test_eq_and_not_eq():
    assert evaluate_condition(C("a", "eq", True), {"a": True})
    assert evaluate_condition(C("a", "not_eq", 1), {"a": 2})


def test_in_treats_missing_and_empty_as_unset():
    unset = C("consent_response", "in", [None, ""])
    assert evaluate_condition(unset, {})
    assert evaluate_condition(unset, {"consent_response": ""})
    assert not evaluate_condition(unset, {"consent_response": "granted"})


def test_in_scalar_value():
    assert evaluate_condition(C("a", "in", "x"), {"a": "x"})


def test_gt_lt_coerce_and_fail_closed():
    assert evaluate_condition(C("n", "gt", 0), {"n": "2"})
    assert evaluate_condition(C("n", "lt", 19), {"n": 16})
    assert not evaluate_condition(C("n", "gt", 0), {"n": "abc"})
    assert not evaluate_condition(C("n", "gt", 0), {})


def test_dotted_field_reads_nested_dict_default_zero():
    cond = C("subagent_entry_count.job_match", "gt", 0)
    assert evaluate_condition(cond, {"subagent_entry_count": {"job_match": 1}})
    assert not evaluate_condition(cond, {"subagent_entry_count": {}})
    assert not evaluate_condition(cond, {"subagent_entry_count": "bad"})


def test_unknown_operator_is_false():
    assert not evaluate_condition(C("a", "regex", "x"), {"a": "x"})


def test_all_conditions_empty_is_true():
    assert all_conditions([], {})
    assert not all_conditions([C("a", "eq", 1), C("b", "eq", 2)], {"a": 1, "b": 3})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_conditions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.conditions'`.

- [ ] **Step 3: Implement**

```python
# agent_core/src/conditions.py
"""
agent_core/src/conditions.py

Routing-condition evaluation shared by workflow routing (orchestrator) and the
understanding package (pending questions, termination gate). One evaluator so
the two can never disagree about what a condition means.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any, Iterable


def evaluate_condition(condition: Any, state: dict) -> bool:
    """Evaluate one routing condition against a state dict.

    A dotted field ``parent.child`` reads ``state[parent][child]``, defaulting
    to 0 when the parent is missing or not a dict (the
    ``subagent_entry_count.<id>`` convention).

    Args:
        condition: Object with ``field``, ``operator`` (eq | not_eq | in | lt | gt)
            and ``value`` attributes.
        state: Merged session/profile state.

    Returns:
        True if the condition holds. Unknown operators and non-numeric
        comparisons return False.
    """
    field = condition.field
    if "." in field:
        parent, child = field.split(".", 1)
        container = (state or {}).get(parent, {})
        value = container.get(child, 0) if isinstance(container, dict) else 0
    else:
        value = (state or {}).get(field)

    op = condition.operator
    cond_val = condition.value
    if op == "eq":
        return value == cond_val
    if op == "not_eq":
        return value != cond_val
    if op == "in":
        return value in (cond_val if isinstance(cond_val, list) else [cond_val])
    if op in ("lt", "gt"):
        try:
            left, right = float(value or 0), float(cond_val)
        except (TypeError, ValueError):
            return False
        return left < right if op == "lt" else left > right
    return False


def all_conditions(conditions: Iterable[Any], state: dict) -> bool:
    """Return True when every condition holds (True for an empty iterable).

    Args:
        conditions: Conditions as accepted by :func:`evaluate_condition`.
        state: Merged session/profile state.

    Returns:
        True if all conditions hold.
    """
    return all(evaluate_condition(c, state) for c in conditions)
```

In `orchestrator.py`, replace the body of `AgentCore._evaluate_condition` (keep its signature and docstring) with:

```python
        return evaluate_condition(condition, session)
```

and add `from src.conditions import evaluate_condition` to the imports.

> Note: behaviour-preserving. The old `gt`/`lt` code already used `float(value or 0)`. A missing field with `gt 0` stays False.

- [ ] **Step 4: Run the new and existing routing tests**

Run: `cd agent_core && uv run pytest tests/test_conditions.py tests/test_orchestrator.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/conditions.py agent_core/src/orchestrator.py agent_core/tests/test_conditions.py
git commit -m "refactor(agent-core): share routing-condition evaluation in src.conditions"
```

---

### Task 3: Runtime schema for `dialogue_act` mode and `SubAgent.pending`

**Files:**
- Modify: `agent_core/src/schema/config.py`
- Test: `agent_core/tests/test_schema_config.py`

**Interfaces:**
- Consumes: existing `RoutingCondition`, `RoutingOperator`, `ConnectorsConfig`, `MemoryToolConfig`
- Produces:
  - `NLUProcessorConfig` gains: `mode`, `timeout_ms`, `retry_attempts`, `history_turns`, `topics`, `signals`, `slots: dict[str, NLUSlotConfig]`, `known_fields`, `examples: list[NLUExampleConfig]`, `act_intents: list[ActIntentRuleConfig]`, `termination_gate: TerminationGateConfig`, `off_track: OffTrackConfig`.
  - `SubAgent` gains `pending: list[PendingQuestionConfig]`.
  - New module constants `_DIALOGUE_ACTS`, `_DIALOGUE_RELATIONS` (tuples).

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_schema_config.py`. Reuse its existing minimal-config helper if it has one. Otherwise use the `_base()` below, which builds a dict accepted by `MergedConfig.validate_full`.

```python
import copy

import pytest
from pydantic import ValidationError

from src.schema.config import MergedConfig, _DIALOGUE_ACTS


def _da_base() -> dict:
    """Minimal merged config in dialogue_act mode that validates."""
    return {
        "connectors": {"read": [{"name": "fetch_jobs", "description": "jobs",
                                 "cache": {"scope": "session", "ttl_seconds": 600}}]},
        "preprocessing": {"nlu_processor": {
            "mode": "dialogue_act",
            "topics": ["salary", "search"],
            "slots": {"age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
                      "consent": {"type": "enum", "values": ["granted", "declined"],
                                  "accept_when_pending": ["consent"]}},
            "act_intents": [
                {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending",
                 "intent": "apply_now"},
                {"acts": ["close"], "intent": "termination_intent", "gated": True},
            ],
            "termination_gate": {"any_of": [{"pending": "closing_offer"}]},
            "off_track": {"threshold": 3, "intent": "off_track"},
        }},
        "agent_workflow": {
            "global_routing": [{"intent": "termination_intent", "next_subagent_id": "ended"}],
            "subagents": [
                {"id": "opening", "is_start": True,
                 "pending": [{"id": "consent", "when": [{"field": "consent_response", "operator": "in",
                                                         "value": [None, ""]}]},
                             {"id": "age"}],
                 "routing": [{"intent": "off_track", "next_subagent_id": "recovery"}]},
                {"id": "apply_confirm",
                 "pending": [{"id": "submit_confirm"}, {"id": "closing_offer"}],
                 "routing": [{"intent": "apply_now", "next_subagent_id": "ended"}]},
                {"id": "job_match",
                 "pending": [{"id": "select_job",
                              "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"},
                              "resolves_to": "selected_job_item_id"}]},
                {"id": "recovery"}, {"id": "ended", "is_terminal": True},
            ],
        },
    }


def test_dialogue_act_minimal_config_validates():
    cfg = MergedConfig.validate_full(_da_base())
    nlu = cfg.preprocessing.nlu_processor
    assert nlu.mode == "dialogue_act" and nlu.timeout_ms == 2500 and nlu.retry_attempts == 2
    assert cfg.agent_workflow.subagents[2].pending[0].options_from.tool == "fetch_jobs"


def test_intent_mode_default_and_new_keys_optional():
    cfg = MergedConfig.validate_full({})
    assert cfg.preprocessing.nlu_processor.mode == "intent"


def test_acts_constant_is_the_framework_list():
    assert _DIALOGUE_ACTS == ("affirm", "deny", "acknowledge", "provide_info", "correct", "select",
                              "ask", "request_change", "repeat", "hold", "close", "other")


@pytest.mark.parametrize("mutate, match", [
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(acts=["shout"]),
     "unknown act"),
    (lambda c: c["preprocessing"]["nlu_processor"]["slots"]["age"].update(accept_when_pending=["nope"]),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(pending="nope"),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["termination_gate"]["any_of"].append({"pending": "nope"}),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(intent="unrouted"),
     "intent 'unrouted' is not used by any routing rule"),
    (lambda c: c["preprocessing"]["nlu_processor"]["off_track"].update(intent="nowhere"),
     "off_track.intent 'nowhere' is not used by any routing rule"),
    (lambda c: c["connectors"]["read"][0].pop("cache"),
     "options_from.tool 'fetch_jobs' has no cache policy"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"].append(
        {"acts": ["ask"], "topic": "weather", "intent": "apply_now"}),
     "topic 'weather' is not in topics"),
    (lambda c: c["preprocessing"]["nlu_processor"]["slots"].update(bad={"type": "enum"}),
     "enum slot needs values"),
    (lambda c: c["agent_workflow"]["subagents"][2]["pending"][0].pop("options_from"),
     "resolves_to requires options_from"),
])
def test_dialogue_act_rejections(mutate, match):
    cfg = copy.deepcopy(_da_base())
    mutate(cfg)
    with pytest.raises((ValidationError, ValueError), match=match):
        MergedConfig.validate_full(cfg)


def test_dialogue_act_rules_not_enforced_in_intent_mode():
    cfg = copy.deepcopy(_da_base())
    cfg["preprocessing"]["nlu_processor"]["mode"] = "intent"
    cfg["preprocessing"]["nlu_processor"]["act_intents"][0]["intent"] = "unrouted"
    MergedConfig.validate_full(cfg)  # authored-but-inactive config is allowed


def test_memory_tool_field_collision_rejected():
    cfg = copy.deepcopy(_da_base())
    cfg["connectors"]["internal"] = []
    cfg["memory_tool"] = {"name": "remember", "fields": {
        "selected_job_item_id": {"scope": "session", "description": "x"}}}
    with pytest.raises(ValueError, match="collides with memory_tool field"):
        MergedConfig.validate_full(cfg)
```

If `ConnectorDef` requires more keys than `name`/`description`, add the minimum it needs to `_da_base()`; copy them from an existing test in this file. If the `memory_tool` field schema differs, mirror an existing memory_tool test in this file.

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -k "dialogue_act or acts_constant or memory_tool_field_collision or intent_mode_default" -v`
Expected: FAIL (`ImportError: _DIALOGUE_ACTS`, or extra keys forbidden).

- [ ] **Step 3: Implement the models**

In `schema/config.py`, above `class NLUProcessorConfig`:

```python
_DIALOGUE_ACTS: tuple[str, ...] = (
    "affirm", "deny", "acknowledge", "provide_info", "correct", "select",
    "ask", "request_change", "repeat", "hold", "close", "other",
)
_DIALOGUE_RELATIONS: tuple[str, ...] = (
    "answers_pending", "answers_other", "new_topic", "unrelated", "unclear",
)


class NLUSlotConfig(BaseModel):
    """One caller-stated value the dialogue-act NLU extracts (NLU dialogue-acts spec §7.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["string", "int", "enum"] = "string"
    values: list[str] = Field(default_factory=list)
    min: Optional[int] = None
    max: Optional[int] = None
    normalise: Optional[Literal["title", "lower"]] = None
    accept_when_pending: list[str] = Field(default_factory=list)
    description: str = ""
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "NLUSlotConfig":
        """Enum slots need values; int bounds must be ordered."""
        if self.type == "enum" and not self.values:
            raise ValueError("enum slot needs values")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("slot min must be <= max")
        return self


class NLUExampleConfig(BaseModel):
    """A few-shot example rendered into the static NLU prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: str = ""
    caller: str
    out: dict[str, Any]


class ActIntentRuleConfig(BaseModel):
    """(acts, pending, relation, topic) → routing intent (spec §6.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acts: list[str] = Field(default_factory=list)
    pending: Optional[str] = None
    relation: Optional[Literal["answers_pending", "answers_other", "new_topic", "unrelated", "unclear"]] = None
    topic: Optional[str] = None
    intent: str
    gated: bool = False

    @field_validator("acts")
    @classmethod
    def _known_acts(cls, value: list[str]) -> list[str]:
        """Reject acts outside the framework list."""
        bad = [a for a in value if a not in _DIALOGUE_ACTS]
        if bad:
            raise ValueError(f"unknown act(s) {bad}; allowed: {list(_DIALOGUE_ACTS)}")
        return value


class TerminationGateItem(BaseModel):
    """Either a pending id or a routing condition (spec §6.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: Optional[str] = None
    field: Optional[str] = None
    operator: Optional[RoutingOperator] = None
    value: Any = None

    @model_validator(mode="after")
    def _one_form(self) -> "TerminationGateItem":
        """Exactly one of ``pending`` or ``field``+``operator``."""
        has_pending = self.pending is not None
        has_cond = self.field is not None and self.operator is not None
        if has_pending == has_cond:
            raise ValueError("termination_gate item needs exactly one of 'pending' or 'field'+'operator'")
        return self


class TerminationGateConfig(BaseModel):
    """Conditions under which a gated act-intent row may fire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    any_of: list[TerminationGateItem] = Field(default_factory=list)


class OffTrackConfig(BaseModel):
    """Consecutive off-track turns before routing to recovery (spec §6.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold: int = Field(default=3, ge=1)
    intent: str = "off_track"
```

Add to `NLUProcessorConfig` (keep every existing field):

```python
    mode: Literal["intent", "dialogue_act"] = "intent"
    # dialogue_act mode only (NLU dialogue-acts spec §7.1, §9.1).
    timeout_ms: int = Field(default=2500, gt=0)
    retry_attempts: int = Field(default=2, ge=1)
    history_turns: int = Field(default=2, ge=0)
    topics: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    slots: dict[str, NLUSlotConfig] = Field(default_factory=dict)
    known_fields: list[str] = Field(default_factory=list)
    examples: list[NLUExampleConfig] = Field(default_factory=list)
    act_intents: list[ActIntentRuleConfig] = Field(default_factory=list)
    termination_gate: TerminationGateConfig = Field(default_factory=TerminationGateConfig)
    off_track: OffTrackConfig = Field(default_factory=OffTrackConfig)
```

Above `class SubAgent`:

```python
class OptionsFromConfig(BaseModel):
    """Where a pending question's offered options come from (a cached tool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    fields: list[str] = Field(min_length=1)
    id_field: str


class PendingQuestionConfig(BaseModel):
    """What a subagent may be waiting for (NLU dialogue-acts spec §7.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    expects: str = ""
    when: list[RoutingCondition] = Field(default_factory=list)
    options_from: Optional[OptionsFromConfig] = None
    resolves_to: Optional[str] = None

    @model_validator(mode="after")
    def _resolves_needs_options(self) -> "PendingQuestionConfig":
        """``resolves_to`` is only meaningful with ``options_from``."""
        if self.resolves_to and self.options_from is None:
            raise ValueError("resolves_to requires options_from")
        return self
```

and add `pending: list[PendingQuestionConfig] = Field(default_factory=list)` to `SubAgent`.

- [ ] **Step 4: Implement the cross-checks**

Add a second `@model_validator(mode="after")` method to `MergedConfig`, after `_check_tool_result_rules`:

```python
    @model_validator(mode="after")
    def _check_dialogue_act_rules(self) -> "MergedConfig":
        """Cross-check dialogue_act NLU config against the workflow and connectors.

        Skipped entirely in ``intent`` mode, so a domain can author the
        dialogue_act blocks before switching.

        Returns:
            The validated config.

        Raises:
            ValueError: On an undeclared pending id, an unrouted intent, an
                unknown topic, an ``options_from`` tool without a cache policy,
                or a state key colliding with a ``memory_tool`` field.
        """
        nlu = self.preprocessing.nlu_processor
        if nlu.mode != "dialogue_act":
            return self
        wf = self.agent_workflow
        declared = {p.id for s in wf.subagents for p in s.pending}

        def _pending_ok(pid: str | None, where: str) -> None:
            if pid and pid not in declared:
                raise ValueError(f"{where}: undeclared pending id '{pid}'")

        for name, slot in nlu.slots.items():
            for pid in slot.accept_when_pending:
                _pending_ok(pid, f"preprocessing.nlu_processor.slots.{name}.accept_when_pending")
        for i, ex in enumerate(nlu.examples):
            _pending_ok(ex.pending, f"preprocessing.nlu_processor.examples[{i}]")
        for i, row in enumerate(nlu.act_intents):
            _pending_ok(row.pending, f"preprocessing.nlu_processor.act_intents[{i}]")
            if row.topic is not None and row.topic not in nlu.topics:
                raise ValueError(f"preprocessing.nlu_processor.act_intents[{i}]: topic '{row.topic}' is not in topics")
        for i, item in enumerate(nlu.termination_gate.any_of):
            _pending_ok(item.pending, f"preprocessing.nlu_processor.termination_gate.any_of[{i}]")

        routed = {r.intent for s in wf.subagents for r in s.routing} | {r.intent for r in wf.global_routing}
        for i, row in enumerate(nlu.act_intents):
            if row.intent not in routed:
                raise ValueError(
                    f"preprocessing.nlu_processor.act_intents[{i}]: intent '{row.intent}' "
                    f"is not used by any routing rule")
        if nlu.off_track.intent not in routed:
            raise ValueError(
                f"preprocessing.nlu_processor.off_track.intent '{nlu.off_track.intent}' "
                f"is not used by any routing rule")

        cached = {c.name for c in self.connectors.read if c.cache is not None}
        state_keys = {self.entity_to_profile_field.get(n, n) for n in nlu.slots}
        for s in wf.subagents:
            for p in s.pending:
                if p.options_from and p.options_from.tool not in cached:
                    raise ValueError(
                        f"subagent '{s.id}' pending '{p.id}': options_from.tool "
                        f"'{p.options_from.tool}' has no cache policy")
                if p.resolves_to:
                    state_keys.add(p.resolves_to)
        if self.memory_tool:
            clash = state_keys & set(self.memory_tool.fields)
            if clash:
                raise ValueError(f"dialogue_act state key(s) {sorted(clash)} collide with memory_tool field")
        return self
```

> Note: the collision check against connector `session_mapping` targets needs `action_gateway.yaml`, which Agent Core does not load. It lives in dev-kit cross-block validation (Task 5).

- [ ] **Step 5: Run tests**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -v`
Expected: all PASS. If the dict key order makes `subagents[2]` not `job_match`, use the index the helper builds.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/schema/config.py agent_core/tests/test_schema_config.py
git commit -m "feat(agent-core): runtime schema for dialogue_act NLU and subagent pending questions"
```

---

### Task 4: Workflow loader parses `pending`, with the dialogue-act intent set

**Files:**
- Modify: `agent_core/src/workflow_loader.py`
- Test: `agent_core/tests/test_workflow_loader.py`

**Interfaces:**
- Consumes: `RoutingCondition` (same module), `_parse_routing_condition`
- Produces:
  - `@dataclass(frozen=True) OptionsFrom(tool: str, fields: tuple[str, ...], id_field: str)`
  - `@dataclass(frozen=True) PendingQuestion(id: str, expects: str = "", when: tuple[RoutingCondition, ...] = (), options_from: OptionsFrom | None = None, resolves_to: str | None = None)`
  - `SubAgent.pending: list[PendingQuestion]` (default empty)

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_workflow_loader.py`. It uses that file's existing `_minimal_config()` and `_make_tool_registry()` helpers.

```python
from src.workflow_loader import AgentWorkflowLoader, OptionsFrom, PendingQuestion


def _with_pending(cfg: dict) -> dict:
    start = cfg["agent_workflow"]["subagents"][0]
    start["pending"] = [
        {"id": "consent", "expects": "हाँ/नहीं",
         "when": [{"field": "consent_response", "operator": "in", "value": [None, ""]}]},
        {"id": "select_job", "options_from": {"tool": "fetch_jobs", "fields": ["role", "company"],
                                              "id_field": "item_id"},
         "resolves_to": "selected_job_item_id"},
    ]
    return cfg


def test_pending_questions_are_parsed_in_order():
    wf = AgentWorkflowLoader().load(_with_pending(_minimal_config()), _make_tool_registry())
    start = wf.subagents[wf.start_subagent_id]
    assert [p.id for p in start.pending] == ["consent", "select_job"]
    assert start.pending[0].when[0].operator == "in"
    assert start.pending[1].options_from == OptionsFrom("fetch_jobs", ("role", "company"), "item_id")
    assert start.pending[1].resolves_to == "selected_job_item_id"


def test_pending_defaults_to_empty():
    wf = AgentWorkflowLoader().load(_minimal_config(), _make_tool_registry())
    assert all(s.pending == [] for s in wf.subagents.values())


def test_dialogue_act_mode_needs_no_nlu_intents_and_skips_valid_intents_check():
    cfg = _minimal_config()
    nlu = cfg["preprocessing"]["nlu_processor"]
    nlu["mode"] = "dialogue_act"
    nlu["intents"] = []
    nlu["act_intents"] = [{"acts": ["affirm"], "intent": "apply_now"}]
    cfg["agent_workflow"]["subagents"][0]["valid_intents"] = ["legacy_only_intent"]
    AgentWorkflowLoader().load(cfg, _make_tool_registry())   # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py -k "pending or dialogue_act" -v`
Expected: FAIL (`ImportError: OptionsFrom`).

- [ ] **Step 3: Implement**

Add above `class SubAgent`:

```python
@dataclass(frozen=True)
class OptionsFrom:
    """Cached tool whose latest result lists the options a pending question offers.

    Attributes:
        tool: Connector name with a Spec A cache policy.
        fields: Row fields rendered for each option, in order.
        id_field: Row field holding the option's id (e.g. ``item_id``).
    """

    tool: str
    fields: tuple[str, ...]
    id_field: str


@dataclass(frozen=True)
class PendingQuestion:
    """What a subagent may be waiting for (NLU dialogue-acts spec §7.2).

    Attributes:
        id: Pending-question id referenced by NLU config.
        expects: Short description rendered into the NLU frame.
        when: Conditions that make this the pending question; empty = always.
        options_from: Source of offered options, or None.
        resolves_to: State key that receives the resolved option id.
    """

    id: str
    expects: str = ""
    when: tuple[RoutingCondition, ...] = ()
    options_from: OptionsFrom | None = None
    resolves_to: str | None = None
```

Add `pending: list["PendingQuestion"] = field(default_factory=list)` as the last field of `SubAgent`, after `opening_phrase`, and document it in the class docstring.

In `_parse_subagent`, before `return SubAgent(`:

```python
        pending: list[PendingQuestion] = []
        for i, raw_p in enumerate(raw.get("pending") or []):
            ctx = f"subagent '{subagent_id}'.pending[{i}]"
            pid = (raw_p or {}).get("id")
            if not pid:
                raise ConfigurationError(f"{ctx}: missing required 'id'")
            when = tuple(self._parse_routing_condition(c, context=f"{ctx}.when[{j}]")
                         for j, c in enumerate(raw_p.get("when") or []))
            of_raw = raw_p.get("options_from")
            options_from = (OptionsFrom(tool=of_raw["tool"], fields=tuple(of_raw.get("fields") or ()),
                                        id_field=of_raw["id_field"]) if of_raw else None)
            pending.append(PendingQuestion(id=pid, expects=str(raw_p.get("expects", "") or ""),
                                           when=when, options_from=options_from,
                                           resolves_to=raw_p.get("resolves_to") or None))
```

and pass `pending=pending` to the `SubAgent(...)` constructor.

In `load()`, compute the mode once and gate the two intent rules:

```python
        nlu_cfg = (config.get("preprocessing") or {}).get("nlu_processor") or {}
        dialogue_act = nlu_cfg.get("mode") == "dialogue_act"
        all_nlu_intents: set[str] = (
            self._dialogue_act_intents(nlu_cfg) if dialogue_act else self._load_nlu_intents(config))
        ...
        if not dialogue_act:
            self._validate_subagent_intents(subagents, all_nlu_intents)
```

(Keep `_validate_global_intents_not_in_subagents` unchanged.) Add:

```python
    def _dialogue_act_intents(self, nlu_cfg: dict) -> set[str]:
        """Intents dialogue_act mode can produce: act_intents rows, any_input, off-track.

        Args:
            nlu_cfg: ``preprocessing.nlu_processor`` dict.

        Returns:
            Set of producible intent names.
        """
        rows = nlu_cfg.get("act_intents") or []
        off = ((nlu_cfg.get("off_track") or {}).get("intent")) or "off_track"
        return {r.get("intent") for r in rows if r.get("intent")} | {"any_input", off}
```

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/workflow_loader.py agent_core/tests/test_workflow_loader.py
git commit -m "feat(agent-core): parse subagent pending questions; dialogue_act intent set"
```

---

### Task 5: Dev-kit sync (mirror, flat schema, FIELD_RULES, defaults, cross-block)

**Files:**
- Modify: `dev-kit/dev_kit/schemas/domain/agent_core.py` (`NLUProcessorSection`, `SubAgent`)
- Modify: `dev-kit/dev_kit/schema.py` (`NLUProcessorConfig`, `SubAgentSchema`)
- Modify: `dev-kit/dev_kit/agent/field_rules/agent_core.py`
- Modify: `dev-kit/dpg/agent_core.yaml` (`preprocessing.nlu_processor` defaults)
- Modify: `dev-kit/dev_kit/schemas/cross_block_validation.py`
- Test: `dev-kit/tests/schemas/domain/test_agent_core.py`, `dev-kit/tests/schemas/test_cross_block_validation.py`, `dev-kit/tests/agent/test_field_rules_agent_core.py`

**Interfaces:**
- Consumes: the runtime shapes from Task 3 (mirror them exactly; lenient where the mirror is lenient today)
- Produces:
  - `_dialogue_act_session_mapping_rules(ac: dict, ag: dict) -> list[str]`
  - In both mirrors, `intents` is required non-empty **only** in `intent` mode.

- [ ] **Step 1: Failing tests (mirror + cross-block)**

Append to `dev-kit/tests/schemas/domain/test_agent_core.py`. Use the file's existing import of `NLUProcessorSection` and `SubAgent`.

```python
def test_nlu_section_dialogue_act_needs_no_intents():
    s = NLUProcessorSection(mode="dialogue_act", slots={"age": {"type": "int", "min": 14, "max": 80}},
                            act_intents=[{"acts": ["affirm"], "intent": "apply_now"}])
    assert s.mode == "dialogue_act" and s.intents == []


def test_nlu_section_intent_mode_still_requires_intents():
    with pytest.raises(ValidationError, match="intents"):
        NLUProcessorSection(mode="intent", intents=[])


def test_nlu_section_rejects_unknown_act():
    with pytest.raises(ValidationError, match="unknown act"):
        NLUProcessorSection(mode="dialogue_act", act_intents=[{"acts": ["shout"], "intent": "x"}])


def test_subagent_accepts_pending():
    sa = SubAgent(id="job_match", name="Job match", system_prompt="p", opening_phrase="o",
                  pending=[{"id": "select_job", "expects": "one of the jobs",
                            "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"},
                            "resolves_to": "selected_job_item_id"}])
    assert sa.pending[0].options_from.id_field == "item_id"
```

Append to `dev-kit/tests/schemas/test_cross_block_validation.py`:

```python
from dev_kit.schemas.cross_block_validation import _dialogue_act_session_mapping_rules


def _ac_da(resolves_to="selected_job_item_id"):
    return {
        "entity_to_profile_field": {"consent": "consent_response"},
        "preprocessing": {"nlu_processor": {"mode": "dialogue_act", "slots": {"consent": {}, "trade": {}}}},
        "agent_workflow": {"subagents": [{"id": "job_match", "pending": [
            {"id": "select_job", "resolves_to": resolves_to,
             "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"}}]}]},
    }


def _ag(target):
    return {"tools": [{"id": "fetch_profile", "response": {"session_mapping": [
        {"source": "items[0].x", "target": target}]}}]}


def test_session_mapping_collision_with_slot_state_key():
    errs = _dialogue_act_session_mapping_rules(_ac_da(), _ag("consent_response"))
    assert any("consent_response" in e for e in errs)


def test_session_mapping_collision_with_resolves_to():
    errs = _dialogue_act_session_mapping_rules(_ac_da(), _ag("selected_job_item_id"))
    assert any("selected_job_item_id" in e for e in errs)


def test_no_collision_and_intent_mode_skipped():
    assert _dialogue_act_session_mapping_rules(_ac_da(), _ag("stored_trade")) == []
    ac = _ac_da()
    ac["preprocessing"]["nlu_processor"]["mode"] = "intent"
    assert _dialogue_act_session_mapping_rules(ac, _ag("consent_response")) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd dev-kit && uv run pytest tests/schemas/domain/test_agent_core.py tests/schemas/test_cross_block_validation.py -k "dialogue_act or pending or nlu_section or collision" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the domain mirror**

In `dev-kit/dev_kit/schemas/domain/agent_core.py`:
- Copy the Task 3 classes (`NLUSlotConfig`, `NLUExampleConfig`, `ActIntentRuleConfig`, `TerminationGateItem`, `TerminationGateConfig`, `OffTrackConfig`, `OptionsFromConfig`, `PendingQuestionConfig`) **verbatim**.
- Use `model_config = ConfigDict(extra="forbid")`, matching this file's style (no `frozen`).
- Reuse the file's own `RoutingCondition` / operator types if it defines them; otherwise copy those too.
- Add `_DIALOGUE_ACTS` with the same tuple.

Then change `NLUProcessorSection`:

```python
    mode: Literal["intent", "dialogue_act"] = "intent"
    intents: list[str] = Field(default_factory=list)   # required only in intent mode (validator)
    timeout_ms: int = Field(default=2500, gt=0)
    retry_attempts: int = Field(default=2, ge=1)
    history_turns: int = Field(default=2, ge=0)
    topics: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    slots: dict[str, NLUSlotConfig] = Field(default_factory=dict)
    known_fields: list[str] = Field(default_factory=list)
    examples: list[NLUExampleConfig] = Field(default_factory=list)
    act_intents: list[ActIntentRuleConfig] = Field(default_factory=list)
    termination_gate: TerminationGateConfig = Field(default_factory=TerminationGateConfig)
    off_track: OffTrackConfig = Field(default_factory=OffTrackConfig)

    @model_validator(mode="after")
    def intents_required_in_intent_mode(self) -> "NLUProcessorSection":
        """workflow_loader rejects an empty intents list in intent mode only."""
        if self.mode == "intent" and not self.intents:
            raise ValueError("intents must be non-empty in intent mode")
        return self
```

Add `pending: list[PendingQuestionConfig] = Field(default_factory=list)` to the mirror `SubAgent`. Update the `NLUProcessorSection` docstring: "intents must be non-empty in intent mode".

- [ ] **Step 4: Flat schema, FIELD_RULES, defaults**

`dev-kit/dev_kit/schema.py`:
- In `NLUProcessorConfig`, change `intents` and `entities` from `Field(...)` to `Field(default_factory=list, description=<keep text>)`.
- Add the same new fields and the same `intents_required_in_intent_mode` validator. Copy the Task 3 helper classes with `Field(description=…)` where this file uses descriptions.
- Add `pending` to `SubAgentSchema`.

`dev-kit/dev_kit/agent/field_rules/agent_core.py`: add one `FieldRule` per new path, next to the existing `preprocessing.nlu_processor.*` rules:

```python
    "preprocessing.nlu_processor.mode": FieldRule(
        category="chat", phase="language", default="intent",
        description="NLU contract: 'intent' (per-subagent intents) or 'dialogue_act' (acts + pending questions).",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.slots": FieldRule(
        category="chat", phase="language", applies_if="nlu_mode == 'dialogue_act'", default={},
        invalidated_by=["preprocessing.nlu_processor.mode"],
        description="Caller-stated values to extract: type, bounds/values, normalise, accept_when_pending.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.act_intents": FieldRule(
        category="chat", phase="workflow", applies_if="nlu_mode == 'dialogue_act'", default=[],
        invalidated_by=["preprocessing.nlu_processor.mode", "agent_workflow.subagents"],
        description="Ordered (acts, pending, relation, topic) → routing intent table.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.known_fields": FieldRule(
        category="chat", phase="language", applies_if="nlu_mode == 'dialogue_act'", default=[],
        description="State fields whose values are shown to NLU in the frame.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.examples": FieldRule(
        category="chat", phase="language", applies_if="nlu_mode == 'dialogue_act'", default=[],
        description="Few-shot examples rendered into the static NLU prompt.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.termination_gate": FieldRule(
        category="chat", phase="workflow", applies_if="nlu_mode == 'dialogue_act'", default={"any_of": []},
        description="When a gated act-intent row (e.g. close → termination) may fire.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.topics": FieldRule(
        category="chat", phase="language", applies_if="nlu_mode == 'dialogue_act'", default=[],
        description="Topics for 'ask' / 'request_change' acts.", pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.signals": FieldRule(
        category="chat", phase="language", applies_if="nlu_mode == 'dialogue_act'", default=[],
        description="Signal names NLU may emit (written as Signal nodes).", pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.off_track": FieldRule(
        category="framework_default_only", description="Off-track threshold and recovery intent.",
    ),
    "preprocessing.nlu_processor.timeout_ms": FieldRule(
        category="framework_default_only", description="Dialogue-act NLU call timeout.",
    ),
    "preprocessing.nlu_processor.retry_attempts": FieldRule(
        category="framework_default_only", description="Dialogue-act NLU total attempts.",
    ),
    "preprocessing.nlu_processor.history_turns": FieldRule(
        category="framework_default_only", description="Recent exchanges rendered into the NLU frame.",
    ),
```

**Before using `applies_if="nlu_mode == 'dialogue_act'"`**, check how `applies_if` expressions are evaluated in `field_rules/__init__.py` / `skeleton.py`. If they can only reference `IntakeState` flags, drop `applies_if` from these rules rather than inventing a flag. The phase prompt still offers them. Match `framework_default_only` to the exact constructor arguments other rules of that category use.

`dev-kit/dpg/agent_core.yaml`: under `preprocessing.nlu_processor`, add the framework defaults:

```yaml
    mode: intent
    timeout_ms: 2500
    retry_attempts: 2
    history_turns: 2
    off_track:
      threshold: 3
      intent: off_track
```

- [ ] **Step 5: Cross-block rule**

In `cross_block_validation.py`, after `_tool_result_session_mapping_rules`:

```python
def _dialogue_act_session_mapping_rules(ac: dict, ag: dict) -> list[str]:
    """Reject dialogue_act state keys that a connector session_mapping also writes.

    NLU slots (mapped through ``entity_to_profile_field``) and pending
    ``resolves_to`` keys are written by Agent Core's SlotWriter; a
    ``response.session_mapping`` target of the same name would make two
    writers race on one field (NLU dialogue-acts spec §7.3).

    Args:
        ac: The agent_core block.
        ag: The action_gateway block.

    Returns:
        One error per colliding key; empty in intent mode.
    """
    nlu = ((ac.get("preprocessing") or {}).get("nlu_processor")) or {}
    if nlu.get("mode") != "dialogue_act":
        return []
    emap = ac.get("entity_to_profile_field") or {}
    keys = {emap.get(n, n) for n in (nlu.get("slots") or {})}
    for s in ((ac.get("agent_workflow") or {}).get("subagents")) or []:
        for p in (s or {}).get("pending") or []:
            if (p or {}).get("resolves_to"):
                keys.add(p["resolves_to"])
    errors: list[str] = []
    for t in ag.get("tools") or []:
        for m in ((t or {}).get("response") or {}).get("session_mapping") or []:
            target = (m or {}).get("target")
            if target in keys:
                errors.append(
                    f"action_gateway tool '{t.get('id') or t.get('name')}' session_mapping target "
                    f"'{target}' is also a dialogue_act NLU state key; rename one of them.")
    return errors
```

Call it from `validate_cross_block` exactly where `_tool_result_session_mapping_rules` is called, with the same phase gate and the same `ag` block variable.

- [ ] **Step 6: Run dev-kit tests**

Run: `cd dev-kit && uv run pytest -q`
Expected: all PASS, including the existing field-rules expected-paths test. If that test enumerates paths, add the new ones there.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/
git commit -m "feat(dev-kit): mirror dialogue_act NLU schema, field rules, defaults and session_mapping collision rule"
```

---

### Task 6: Understanding models and parsed config

**Files:**
- Create: `agent_core/src/understanding/__init__.py`, `agent_core/src/understanding/models.py`, `agent_core/src/understanding/config.py`
- Test: `agent_core/tests/understanding/__init__.py` (empty), `agent_core/tests/understanding/test_models.py`, `agent_core/tests/understanding/test_config.py`

**Interfaces:**
- Consumes: `src.models.NLUResult`, `src.workflow_loader.RoutingCondition`
- Produces (exact names used by later tasks):
  - `ACTS`, `RELATIONS` (tuples, equal to the schema constants)
  - `DialogueActResult(acts, relation, topic=None, slots={}, option=None, spoken_reference=None, signals=(), extras=())`, with `.fallback()` and `.from_parsed(parsed, *, slot_names, topics, signals)` (raises `ValueError`)
  - `ResolvedReference(option, id, label, id_field)`, `UnresolvedReference(option, offered, reason)`
  - `SlotRejection(slot, value, reason)`, `SlotUpdate(key, old, new)`, `StateWrite(scope, key, value)`
  - `TurnUnderstanding` (fields below)
  - `SlotSpec`, `ActIntentRule`, `GateItem`, `DialogueActConfig`, with `.from_config(config) -> DialogueActConfig | None` and `.state_key(slot) -> str`

- [ ] **Step 1: Write failing tests**

```python
# agent_core/tests/understanding/test_models.py
"""Tests for understanding result models."""
import pytest

from src.schema.config import _DIALOGUE_ACTS, _DIALOGUE_RELATIONS
from src.understanding.models import ACTS, RELATIONS, DialogueActResult

KW = dict(slot_names=("age", "trade"), topics=("salary",), signals=("pay_disappointment",))


def test_constants_match_schema():
    assert ACTS == _DIALOGUE_ACTS and RELATIONS == _DIALOGUE_RELATIONS


def test_from_parsed_happy_path():
    r = DialogueActResult.from_parsed({
        "acts": ["select", "affirm"], "relation": "answers_pending", "topic": None,
        "slots": {"age": None, "trade": "Welder"},
        "reference": {"option": 1, "spoken": "पहले वाला"},
        "signals": ["pay_disappointment", "made_up"], "extras": [{"key": "tool", "value": "drill"}],
    }, **KW)
    assert r.acts == ("select", "affirm") and r.option == 1 and r.spoken_reference == "पहले वाला"
    assert r.slots == {"age": None, "trade": "Welder"}
    assert r.signals == ("pay_disappointment",)            # unknown signal dropped
    assert r.extras == (("tool", "drill"),)


def test_from_parsed_fills_missing_slots_with_none_and_drops_unknown_slots():
    r = DialogueActResult.from_parsed({"acts": ["other"], "relation": "unclear",
                                       "slots": {"bogus": "x"}}, **KW)
    assert r.slots == {"age": None, "trade": None}


def test_from_parsed_unknown_topic_becomes_none_and_more_than_3_acts_truncate():
    r = DialogueActResult.from_parsed({"acts": ["ask", "ask", "ask", "ask"], "relation": "new_topic",
                                       "topic": "weather"}, **KW)
    assert r.topic is None and len(r.acts) == 3


@pytest.mark.parametrize("parsed", [
    None, [], {"acts": [], "relation": "unclear"}, {"acts": ["shout"], "relation": "unclear"},
    {"acts": ["other"], "relation": "sideways"}, {"acts": ["other"], "relation": "unclear", "slots": []},
])
def test_from_parsed_rejects_bad_shapes(parsed):
    with pytest.raises(ValueError):
        DialogueActResult.from_parsed(parsed, **KW)


def test_bool_option_is_not_an_int():
    r = DialogueActResult.from_parsed({"acts": ["select"], "relation": "answers_pending",
                                       "reference": {"option": True, "spoken": None}}, **KW)
    assert r.option is None


def test_fallback_shape():
    f = DialogueActResult.fallback()
    assert f.acts == ("other",) and f.relation == "unclear" and f.slots == {} and f.option is None
```

```python
# agent_core/tests/understanding/test_config.py
"""Tests for DialogueActConfig parsing."""
from src.understanding.config import DialogueActConfig


def _cfg(mode="dialogue_act"):
    return {
        "entity_to_profile_field": {"consent": "consent_response"},
        "entity_persistence": {"scope": "session"},
        "preprocessing": {"nlu_processor": {
            "mode": mode, "timeout_ms": 2000, "history_turns": 3,
            "slots": {"consent": {"type": "enum", "values": ["granted", "declined"],
                                  "accept_when_pending": ["consent"]},
                      "age": {"type": "int", "min": 14, "max": 80}},
            "known_fields": ["consent", "age", "stored_trade"],
            "act_intents": [{"acts": ["close"], "intent": "termination_intent", "gated": True}],
            "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                            {"field": "applications_submitted", "operator": "gt", "value": 0}]},
            "off_track": {"threshold": 2, "intent": "off_track"},
        }},
    }


def test_from_config_none_in_intent_mode():
    assert DialogueActConfig.from_config(_cfg("intent")) is None
    assert DialogueActConfig.from_config({}) is None


def test_from_config_parses_everything():
    c = DialogueActConfig.from_config(_cfg())
    assert c.timeout_ms == 2000 and c.retry_attempts == 2 and c.history_turns == 3
    assert c.slots["consent"].values == ("granted", "declined")
    assert c.slots["age"].min == 14 and c.slots["age"].type == "int"
    assert c.act_intents[0].gated is True and c.act_intents[0].acts == ("close",)
    assert c.gate[0].pending == "closing_offer" and c.gate[0].condition is None
    assert c.gate[1].condition.field == "applications_submitted"
    assert c.off_track_threshold == 2 and c.entity_scope == "session"


def test_state_key_maps_through_entity_to_profile_field():
    c = DialogueActConfig.from_config(_cfg())
    assert c.state_key("consent") == "consent_response" and c.state_key("age") == "age"


def test_known_fields_map_slots_but_keep_raw_session_keys():
    c = DialogueActConfig.from_config(_cfg())
    assert c.known_state_keys() == (("consent", "consent_response"), ("age", "age"),
                                    ("stored_trade", "stored_trade"))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/understanding -v`
Expected: FAIL (`ModuleNotFoundError: src.understanding`).

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/__init__.py
"""
agent_core/src/understanding — dialogue-act NLU mode (NLU dialogue-acts spec).

Pure turn-understanding pipeline: pending question → frame → one strict-JSON
LLM call → post-processing → write plan. No I/O except the LLM call; the
orchestrator applies the returned writes.

Belongs to the Agent Core DPG block.
"""
```

```python
# agent_core/src/understanding/models.py
"""
agent_core/src/understanding/models.py

Result types of the dialogue-act understanding pipeline (NLU dialogue-acts
spec §4, §5.3, §8). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from src.models import NLUResult

ACTS: tuple[str, ...] = (
    "affirm", "deny", "acknowledge", "provide_info", "correct", "select",
    "ask", "request_change", "repeat", "hold", "close", "other",
)
RELATIONS: tuple[str, ...] = (
    "answers_pending", "answers_other", "new_topic", "unrelated", "unclear",
)
MAX_ACTS = 3


@dataclass(frozen=True)
class DialogueActResult:
    """Validated NLU output in dialogue_act mode.

    Attributes:
        acts: 1–3 acts, in the order the caller performed them.
        relation: How the turn relates to the pending question.
        topic: Topic for ask / request_change, else None.
        slots: Every configured slot name → raw value or None.
        option: Offered-option number the caller referred to, or None.
        spoken_reference: The caller's words for that reference, or None.
        signals: Configured signal names the turn carries.
        extras: Ad-hoc (key, value) details; never read by routing.
    """

    acts: tuple[str, ...]
    relation: str
    topic: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    option: int | None = None
    spoken_reference: str | None = None
    signals: tuple[str, ...] = ()
    extras: tuple[tuple[str, str], ...] = ()

    @classmethod
    def fallback(cls) -> "DialogueActResult":
        """Result used when the NLU call fails (spec §9.2)."""
        return cls(acts=("other",), relation="unclear")

    @classmethod
    def from_parsed(cls, parsed: Any, *, slot_names: Iterable[str], topics: Iterable[str],
                    signals: Iterable[str]) -> "DialogueActResult":
        """Validate a provider's parsed JSON into a result.

        Tolerant where a wrong value is harmless (unknown topic → None,
        unknown signal dropped, >3 acts truncated, unknown slot keys ignored);
        strict where routing depends on it (acts and relation enums).

        Args:
            parsed: ``ChatResponse.parsed_output``.
            slot_names: Configured slot names.
            topics: Configured topics.
            signals: Configured signal names.

        Returns:
            The validated result.

        Raises:
            ValueError: If the object, acts, relation or slots are malformed.
        """
        if not isinstance(parsed, dict):
            raise ValueError("parsed output must be an object")
        acts = parsed.get("acts")
        if not isinstance(acts, list) or not acts or any(a not in ACTS for a in acts):
            raise ValueError(f"invalid acts: {acts!r}")
        relation = parsed.get("relation")
        if relation not in RELATIONS:
            raise ValueError(f"invalid relation: {relation!r}")
        raw_slots = parsed.get("slots", {})
        if raw_slots is None:
            raw_slots = {}
        if not isinstance(raw_slots, dict):
            raise ValueError("slots must be an object")
        topic = parsed.get("topic")
        topic = topic if isinstance(topic, str) and topic in set(topics) else None
        ref = parsed.get("reference") if isinstance(parsed.get("reference"), dict) else {}
        option = ref.get("option")
        option = option if isinstance(option, int) and not isinstance(option, bool) else None
        spoken = ref.get("spoken") if isinstance(ref.get("spoken"), str) else None
        allowed_signals = set(signals)
        sig = tuple(s for s in (parsed.get("signals") or []) if s in allowed_signals)
        extras = tuple((str(e["key"]), str(e["value"])) for e in (parsed.get("extras") or [])
                       if isinstance(e, dict) and "key" in e and "value" in e)
        return cls(
            acts=tuple(acts[:MAX_ACTS]), relation=relation, topic=topic,
            slots={n: raw_slots.get(n) for n in slot_names},
            option=option, spoken_reference=spoken, signals=sig, extras=extras,
        )


@dataclass(frozen=True)
class ResolvedReference:
    """An offered option the caller chose, mapped to its id (spec §6.3)."""

    option: int
    id: str
    label: str
    id_field: str


@dataclass(frozen=True)
class UnresolvedReference:
    """An option reference that could not be mapped. reason: no_options | out_of_range | missing_id."""

    option: int
    offered: int
    reason: str


@dataclass(frozen=True)
class SlotRejection:
    """A slot value that was dropped, and why (``normalise:<rule>`` or ``not_pending``)."""

    slot: str
    value: Any
    reason: str


@dataclass(frozen=True)
class SlotUpdate:
    """A state key whose existing non-empty value changed this turn."""

    key: str
    old: Any
    new: Any


@dataclass(frozen=True)
class StateWrite:
    """One Memory Layer write the orchestrator must apply (scope, key, value)."""

    scope: str
    key: str
    value: Any


@dataclass
class TurnUnderstanding:
    """Everything the turn learned from the caller (spec §8).

    ``nlu_result`` is what routing consumes, in both modes. ``dialogue`` is
    None in intent mode. ``writes`` and ``signals`` are applied by the
    orchestrator.
    """

    nlu_result: NLUResult
    dialogue: DialogueActResult | None = None
    pending_id: str | None = None
    resolved: ResolvedReference | None = None
    unresolved: UnresolvedReference | None = None
    accepted_slots: dict[str, Any] = field(default_factory=dict)
    updates: list[SlotUpdate] = field(default_factory=list)
    rejected_slots: list[SlotRejection] = field(default_factory=list)
    writes: list[StateWrite] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    latency_ms: int = 0
    gate_blocked: bool = False
    off_track_tripped: bool = False
```

```python
# agent_core/src/understanding/config.py
"""
agent_core/src/understanding/config.py

DialogueActConfig: the dialogue_act NLU config parsed once at startup
(configuration-discipline rule). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.workflow_loader import RoutingCondition


@dataclass(frozen=True)
class SlotSpec:
    """One configured slot (spec §7.1)."""

    name: str
    type: str = "string"
    values: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    normalise: str | None = None
    accept_when_pending: tuple[str, ...] = ()
    description: str = ""
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActIntentRule:
    """One row of the act → intent table (spec §6.4)."""

    intent: str
    acts: tuple[str, ...] = ()
    pending: str | None = None
    relation: str | None = None
    topic: str | None = None
    gated: bool = False


@dataclass(frozen=True)
class GateItem:
    """Termination-gate entry: a pending id, or a routing condition."""

    pending: str | None = None
    condition: RoutingCondition | None = None


@dataclass(frozen=True)
class DialogueActConfig:
    """Parsed ``preprocessing.nlu_processor`` for dialogue_act mode."""

    slots: dict[str, SlotSpec]
    known_fields: tuple[str, ...]
    topics: tuple[str, ...]
    signals: tuple[str, ...]
    examples: tuple[dict, ...]
    act_intents: tuple[ActIntentRule, ...]
    gate: tuple[GateItem, ...]
    off_track_threshold: int
    off_track_intent: str
    history_turns: int
    timeout_ms: int
    retry_attempts: int
    entity_map: dict[str, str]
    entity_scope: str
    signal_types: dict[str, str]
    log_raw_response: bool = False

    @classmethod
    def from_config(cls, config: dict | None) -> "DialogueActConfig | None":
        """Parse the merged config; None unless ``mode == "dialogue_act"``.

        Args:
            config: Full merged agent_core config (already schema-validated).

        Returns:
            The parsed config, or None in intent mode.
        """
        nlu: dict[str, Any] = ((config or {}).get("preprocessing") or {}).get("nlu_processor") or {}
        if nlu.get("mode") != "dialogue_act":
            return None
        slots = {
            name: SlotSpec(
                name=name, type=s.get("type", "string"), values=tuple(s.get("values") or ()),
                min=s.get("min"), max=s.get("max"), normalise=s.get("normalise"),
                accept_when_pending=tuple(s.get("accept_when_pending") or ()),
                description=s.get("description", ""), examples=tuple(s.get("examples") or ()),
            )
            for name, s in (nlu.get("slots") or {}).items()
        }
        rows = tuple(
            ActIntentRule(intent=r["intent"], acts=tuple(r.get("acts") or ()), pending=r.get("pending"),
                          relation=r.get("relation"), topic=r.get("topic"), gated=bool(r.get("gated", False)))
            for r in (nlu.get("act_intents") or [])
        )
        gate = tuple(
            GateItem(pending=g["pending"]) if g.get("pending") else GateItem(
                condition=RoutingCondition(field=g["field"], operator=str(getattr(g["operator"], "value", g["operator"])),
                                           value=g.get("value")))
            for g in ((nlu.get("termination_gate") or {}).get("any_of") or [])
        )
        off = nlu.get("off_track") or {}
        return cls(
            slots=slots,
            known_fields=tuple(nlu.get("known_fields") or ()),
            topics=tuple(nlu.get("topics") or ()),
            signals=tuple(nlu.get("signals") or ()),
            examples=tuple(nlu.get("examples") or ()),
            act_intents=rows,
            gate=gate,
            off_track_threshold=int(off.get("threshold", 3)),
            off_track_intent=str(off.get("intent", "off_track")),
            history_turns=int(nlu.get("history_turns", 2)),
            timeout_ms=int(nlu.get("timeout_ms", 2500)),
            retry_attempts=int(nlu.get("retry_attempts", 2)),
            entity_map=dict((config or {}).get("entity_to_profile_field") or {}),
            entity_scope=str(((config or {}).get("entity_persistence") or {}).get("scope", "persistent")),
            signal_types=dict(nlu.get("signal_intents") or {}),
            log_raw_response=bool(nlu.get("log_raw_response", False)),
        )

    def state_key(self, slot: str) -> str:
        """State key a slot is written to (via ``entity_to_profile_field``)."""
        return self.entity_map.get(slot, slot)

    def known_state_keys(self) -> tuple[tuple[str, str], ...]:
        """(label, state key) for each known field; non-slot names are raw state keys."""
        return tuple((f, self.state_key(f) if f in self.slots else f) for f in self.known_fields)
```

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/understanding -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/__init__.py agent_core/src/understanding/models.py \
        agent_core/src/understanding/config.py agent_core/tests/understanding/
git commit -m "feat(agent-core): understanding result models and parsed dialogue_act config"
```

---

### Task 7: `TurnToolCache.latest_entry`

**Files:**
- Modify: `agent_core/src/tool_results.py`
- Test: `agent_core/tests/test_tool_results.py`

**Interfaces:**
- Consumes: existing `TurnToolCache` internals (`_fresh()`, normalised entries)
- Produces: `TurnToolCache.latest_entry(tool: str) -> dict | None`. Returns the fresh entry with the highest `fetched_at`, in the normalised shape (`tool, args_hash, data, fetched_at, expires_at, origin, scope`).

- [ ] **Step 1: Failing test**

Append to `agent_core/tests/test_tool_results.py`. Reuse that file's policy and entry helpers if present; otherwise:

```python
def test_latest_entry_picks_newest_fresh_entry_for_tool():
    policies = ToolResultPolicies.from_config(
        {"connectors": {"read": [{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600}}]}})
    now = 10_000.0
    entries = [
        {"tool": "fetch_jobs", "args_hash": "a", "data": [{"item_id": "old"}], "fetched_at": now - 300,
         "expires_at": now + 300, "origin": "turn", "scope": "session"},
        {"tool": "fetch_jobs", "args_hash": "b", "data": [{"item_id": "new"}], "fetched_at": now - 10,
         "expires_at": now + 590, "origin": "turn", "scope": "session"},
        {"tool": "fetch_jobs", "args_hash": "c", "data": [{"item_id": "expired"}], "fetched_at": now - 700,
         "expires_at": now - 100, "origin": "turn", "scope": "session"},
    ]
    cache = TurnToolCache(policies, entries, {}, now=lambda: now)
    assert cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "new"}]
    assert cache.latest_entry("fetch_profile") is None


def test_latest_entry_sees_entry_stored_this_turn():
    policies = ToolResultPolicies.from_config(
        {"connectors": {"read": [{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600}}]}})
    cache = TurnToolCache(policies, [], {}, now=lambda: 50.0)
    call = ToolCall(tool_use_id="t1", tool_name="fetch_jobs", input_params={"query_text": "welder"})
    res = ToolResult(tool_use_id="t1", tool_name="fetch_jobs", result={}, success=True,
                     result_text='[{"item_id": "j1"}]', projected=True)
    cache.after_call(call, res)
    assert cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "j1"}]
```

(Import `ToolCall` and `ToolResult` from `src.models` if this file doesn't already. If `ToolResultPolicies.from_config` reads connectors from another key, copy the config shape from the file's existing tests.)

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/test_tool_results.py -k latest_entry -v`
Expected: FAIL (`AttributeError: 'TurnToolCache' object has no attribute 'latest_entry'`).

- [ ] **Step 3: Implement**

Add to `TurnToolCache`, after `fresh_tools`:

```python
    def latest_entry(self, tool: str) -> dict | None:
        """Return the unexpired entry with the highest ``fetched_at`` for a tool.

        Used by the dialogue-act NLU frame and option resolver (NLU
        dialogue-acts spec §5.2, §6.3): a new search with different arguments
        is a different entry, and the latest one is the list on offer.

        Args:
            tool: Tool name.

        Returns:
            A copy of the normalised entry dict, or None when none is fresh.
        """
        candidates = [e for (t, _), e in self._fresh().items() if t == tool]
        if not candidates:
            return None
        return dict(max(candidates, key=lambda e: float(e["fetched_at"])))
```

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/test_tool_results.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/tool_results.py agent_core/tests/test_tool_results.py
git commit -m "feat(agent-core): TurnToolCache.latest_entry for the newest stored result of a tool"
```

---

### Task 8: Pending-question resolver

**Files:**
- Create: `agent_core/src/understanding/pending.py`
- Test: `agent_core/tests/understanding/test_pending.py`

**Interfaces:**
- Consumes: `AgentWorkflow.subagents[id].pending: list[PendingQuestion]` (Task 4), `all_conditions` (Task 2)
- Produces: `PendingResolverBase` (ABC) and `PendingResolver(workflow)`, with `.resolve(subagent_id: str, state: dict) -> PendingQuestion | None`

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_pending.py
"""Tests for PendingResolver."""
from types import SimpleNamespace

from src.understanding.pending import PendingResolver
from src.workflow_loader import PendingQuestion, RoutingCondition

UNSET = [None, ""]


def _wf():
    opening = SimpleNamespace(pending=[
        PendingQuestion("consent", "हाँ/नहीं", when=(RoutingCondition("consent_response", "in", UNSET),)),
        PendingQuestion("age", "उम्र", when=(RoutingCondition("has_age", "not_eq", True),
                                            RoutingCondition("age", "in", [None, "", 0]))),
    ])
    job = SimpleNamespace(pending=[PendingQuestion("select_job")])
    return SimpleNamespace(subagents={"opening": opening, "job_match": job,
                                      "ended": SimpleNamespace(pending=[])})


def test_first_matching_candidate_wins():
    r = PendingResolver(_wf())
    assert r.resolve("opening", {}).id == "consent"
    assert r.resolve("opening", {"consent_response": "granted"}).id == "age"


def test_returning_caller_with_flags_has_nothing_pending():
    r = PendingResolver(_wf())
    assert r.resolve("opening", {"consent_response": "granted", "has_age": True}) is None


def test_entry_without_when_always_matches():
    assert PendingResolver(_wf()).resolve("job_match", {}).id == "select_job"


def test_unknown_subagent_or_no_pending_is_none():
    r = PendingResolver(_wf())
    assert r.resolve("ended", {}) is None
    assert r.resolve("nope", {}) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/understanding/test_pending.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/pending.py
"""
agent_core/src/understanding/pending.py

Picks what the bot is waiting for, from the subagent's declared pending
questions and current state (NLU dialogue-acts spec §7.2). Deterministic; no
LLM. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.conditions import all_conditions
from src.workflow_loader import PendingQuestion


class PendingResolverBase(ABC):
    """Interface for pending-question resolution."""

    @abstractmethod
    def resolve(self, subagent_id: str, state: dict) -> PendingQuestion | None:
        """Return the pending question for this subagent and state, or None."""


class PendingResolver(PendingResolverBase):
    """First declared candidate whose ``when`` conditions all hold.

    Args:
        workflow: Loaded workflow; only ``subagents[id].pending`` is read.
    """

    def __init__(self, workflow: Any) -> None:
        self._workflow = workflow

    def resolve(self, subagent_id: str, state: dict) -> PendingQuestion | None:
        """Return the pending question for this subagent and state, or None.

        Args:
            subagent_id: Subagent the caller is currently in (before routing).
            state: Merged routing state (session + profile + NLU-owned values).

        Returns:
            The first matching PendingQuestion, or None when the subagent is
            unknown, declares none, or none match.
        """
        sub = (getattr(self._workflow, "subagents", None) or {}).get(subagent_id)
        for candidate in getattr(sub, "pending", None) or []:
            if all_conditions(candidate.when, state or {}):
                return candidate
        return None
```

- [ ] **Step 4: Run tests**: `cd agent_core && uv run pytest tests/understanding/test_pending.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/pending.py agent_core/tests/understanding/test_pending.py
git commit -m "feat(agent-core): pending-question resolver"
```

---

### Task 9: Frame builder

**Files:**
- Create: `agent_core/src/understanding/frame.py`
- Test: `agent_core/tests/understanding/test_frame.py`

**Interfaces:**
- Consumes: `PendingQuestion`, `OptionsFrom` (Task 4)
- Produces:
  - `offered_rows(entry: dict | None) -> list[dict]`
  - `FrameBuilderBase` / `FrameBuilder(reply_cap: int = 600)`, with `.build(*, step: str, pending: PendingQuestion | None, rows: list[dict], known: list[tuple[str, Any]], recent: list[dict], segments: list[str]) -> str`. `recent` entries are `{"caller": str, "bot": str, "interrupted": bool}`.

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_frame.py
"""Tests for the NLU frame renderer."""
from src.understanding.frame import FrameBuilder, offered_rows
from src.workflow_loader import OptionsFrom, PendingQuestion

JOBS = PendingQuestion("select_job", "offered jobs में से एक",
                       options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"))
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan", "extra": "x"}]


def test_offered_rows_from_list_dict_or_nothing():
    assert offered_rows({"data": ROWS}) == ROWS
    assert offered_rows({"data": {"items": ROWS}}) == ROWS
    assert offered_rows({"data": {"other": 1}}) == []
    assert offered_rows(None) == []
    assert offered_rows({"data": [1, "x", {"item_id": "j"}]}) == [{"item_id": "j"}]


def test_full_frame_snapshot():
    text = FrameBuilder().build(
        step="job_match", pending=JOBS, rows=ROWS,
        known=[("consent", "granted"), ("trade", "Welder"), ("age", None)],
        recent=[{"caller": "वेल्डर", "bot": "बेंगलुरु में तीन नौकरियां हैं...", "interrupted": False}],
        segments=["पहले वाला"])
    assert text == (
        "<frame>\n"
        "step: job_match\n"
        "pending: select_job — offered jobs में से एक\n"
        "offered:\n"
        "  1. Welder · Flipkart\n"
        "  2. Welder · Titan\n"
        "known: consent=granted · trade=Welder\n"
        "</frame>\n"
        "<recent>\n"
        "caller: वेल्डर\n"
        "bot: बेंगलुरु में तीन नौकरियां हैं...\n"
        "</recent>\n"
        "<caller_now>\n"
        "पहले वाला\n"
        "</caller_now>"
    )


def test_no_pending_no_recent_no_known():
    text = FrameBuilder().build(step="opening", pending=None, rows=[], known=[], recent=[],
                                segments=["हाँ"])
    assert "pending: none" in text and "offered:" not in text and "known:" not in text
    assert "<recent>" not in text


def test_interrupted_segments_and_bot_reply_are_marked():
    text = FrameBuilder().build(
        step="job_match", pending=JOBS, rows=[], known=[],
        recent=[{"caller": "x", "bot": "आधा जवाब", "interrupted": True}],
        segments=["इलेक्ट्रीशियन", "नहीं वेल्डर"])
    assert "bot: [interrupted] आधा जवाब" in text
    assert "[interrupted] इलेक्ट्रीशियन\nनहीं वेल्डर" in text
    assert "offered:" not in text                     # options_from but no rows


def test_long_bot_reply_keeps_the_tail():
    reply = "शुरू " + ("बीच " * 400) + "क्या आप आवेदन करना चाहेंगे?"
    text = FrameBuilder(reply_cap=60).build(step="s", pending=None, rows=[], known=[],
                                            recent=[{"caller": "", "bot": reply, "interrupted": False}],
                                            segments=["हाँ"])
    bot_line = [l for l in text.splitlines() if l.startswith("bot: ")][0]
    assert bot_line.endswith("क्या आप आवेदन करना चाहेंगे?") and bot_line.startswith("bot: …")
    assert len(bot_line) <= len("bot: …") + 60
```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/understanding/test_frame.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/frame.py
"""
agent_core/src/understanding/frame.py

Renders the per-turn NLU user message: <frame>, <recent>, <caller_now>
(NLU dialogue-acts spec §5.2). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.workflow_loader import PendingQuestion

_EMPTY = (None, "", [], {})


def offered_rows(entry: dict | None) -> list[dict]:
    """Rows of a stored tool-result entry: the list itself, or ``data["items"]``.

    Args:
        entry: A ``TurnToolCache.latest_entry`` dict, or None.

    Returns:
        Dict rows in stored order; non-dict rows are skipped.
    """
    data = (entry or {}).get("data")
    rows = data if isinstance(data, list) else (data.get("items") if isinstance(data, dict) else None)
    return [r for r in (rows or []) if isinstance(r, dict)]


def option_label(row: dict, fields: tuple[str, ...]) -> str:
    """Join a row's display fields with " · ", skipping empty ones."""
    return " · ".join(str(row[f]) for f in fields if row.get(f) not in _EMPTY)


class FrameBuilderBase(ABC):
    """Interface for rendering the NLU user message."""

    @abstractmethod
    def build(self, *, step: str, pending: PendingQuestion | None, rows: list[dict],
              known: list[tuple[str, Any]], recent: list[dict], segments: list[str]) -> str:
        """Render the user message for one NLU call."""


class FrameBuilder(FrameBuilderBase):
    """Default renderer.

    Args:
        reply_cap: Max characters kept from each recent bot reply (tail kept,
            because the question is at the end).
    """

    def __init__(self, reply_cap: int = 600) -> None:
        self._cap = max(1, reply_cap)

    def _tail(self, text: str) -> str:
        text = (text or "").strip()
        return text if len(text) <= self._cap else "…" + text[-self._cap:]

    def build(self, *, step: str, pending: PendingQuestion | None, rows: list[dict],
              known: list[tuple[str, Any]], recent: list[dict], segments: list[str]) -> str:
        """Render the user message for one NLU call.

        Args:
            step: Subagent the caller is in (before routing).
            pending: Resolved pending question, or None.
            rows: Offered rows (only rendered when ``pending.options_from``).
            known: (label, value) pairs; empty values are skipped.
            recent: Last exchanges, oldest first.
            segments: This turn's utterances; all but the last are marked interrupted.

        Returns:
            The rendered message.
        """
        lines = ["<frame>", f"step: {step}"]
        if pending is None:
            lines.append("pending: none")
        else:
            lines.append(f"pending: {pending.id}" + (f" — {pending.expects}" if pending.expects else ""))
            if pending.options_from and rows:
                lines.append("offered:")
                lines += [f"  {i}. {option_label(r, pending.options_from.fields)}"
                          for i, r in enumerate(rows, start=1)]
        shown = [f"{k}={v}" for k, v in known if v not in _EMPTY]
        if shown:
            lines.append("known: " + " · ".join(shown))
        lines.append("</frame>")
        if recent:
            lines.append("<recent>")
            for e in recent:
                if e.get("caller"):
                    lines.append(f"caller: {str(e['caller']).strip()}")
                if e.get("bot"):
                    mark = "[interrupted] " if e.get("interrupted") else ""
                    lines.append(f"bot: {mark}{self._tail(str(e['bot']))}")
            lines.append("</recent>")
        lines.append("<caller_now>")
        segs = [s for s in segments if str(s).strip()]
        lines += [("[interrupted] " if i < len(segs) - 1 else "") + str(s).strip()
                  for i, s in enumerate(segs)]
        lines.append("</caller_now>")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests**: `cd agent_core && uv run pytest tests/understanding/test_frame.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/frame.py agent_core/tests/understanding/test_frame.py
git commit -m "feat(agent-core): NLU frame renderer"
```

---

### Task 10: Dialogue-act NLU call (static prompt, strict schema, fallback)

**Files:**
- Create: `agent_core/src/understanding/dialogue_act_nlu.py`
- Test: `agent_core/tests/understanding/test_dialogue_act_nlu.py`

**Interfaces:**
- Consumes: `DialogueActConfig` (Task 6), `ChatProviderBase.call`, `ChatRequest`, `OutputFormat`, `SystemPrompt`, `TextBlock`, `Message` (`src.chat_provider.types`), `DialogueActResult` (Task 6)
- Produces:
  - `build_output_schema(cfg) -> dict`
  - `build_system_prompt_text(cfg) -> str`
  - `DialogueActNLUBase` / `DialogueActNLU(cfg, chat_provider)`, with `.classify(user_message: str) -> tuple[DialogueActResult, str | None, int]` returning (result, fallback_reason, latency_ms). `fallback_reason` is one of `None`, `"provider_error:<error_type>"`, `"schema_violation"`, `"exception"`.

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_dialogue_act_nlu.py
"""Tests for the dialogue-act NLU call."""
from unittest.mock import MagicMock

from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage
from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import DialogueActNLU, build_output_schema, build_system_prompt_text
from src.understanding.models import ACTS


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["salary"], "signals": ["pay_disappointment"],
        "slots": {"age": {"type": "int", "min": 14, "max": 80, "description": "उम्र"},
                  "consent": {"type": "enum", "values": ["granted", "declined"]},
                  "trade": {"type": "string", "normalise": "title"}},
        "examples": [{"pending": "closing_offer", "caller": "ठीक है धन्यवाद",
                      "out": {"acts": ["acknowledge"], "relation": "answers_pending"}}],
    }}})


def _resp(parsed=None, stop="end_turn", error_type=None):
    return ChatResponse(content=[TextBlock(text="{}")], parsed_output=parsed, stop_reason=stop,
                        error_type=error_type, model_used="m", usage=TokenUsage())


GOOD = {"acts": ["acknowledge"], "relation": "answers_pending", "topic": None,
        "slots": {"age": None, "consent": None, "trade": None},
        "reference": {"option": None, "spoken": None}, "signals": [], "extras": []}


def _walk(node):
    yield node
    for v in (node.get("properties") or {}).values():
        yield from _walk(v)
    if isinstance(node.get("items"), dict):
        yield from _walk(node["items"])


def test_schema_is_strict_compatible():
    schema = build_output_schema(_cfg())
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
    props = schema["properties"]
    assert props["acts"]["items"]["enum"] == list(ACTS)
    assert props["slots"]["properties"]["age"]["type"] == ["integer", "null"]
    assert props["slots"]["properties"]["consent"]["enum"] == ["granted", "declined", None]
    assert props["topic"]["enum"] == ["salary", None]


def test_schema_with_no_topics_or_signals():
    cfg = DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act"}}})
    schema = build_output_schema(cfg)
    assert schema["properties"]["topic"] == {"type": "null"}
    assert schema["properties"]["signals"]["items"] == {"type": "string"}


def test_system_prompt_is_static_and_mentions_traps():
    text = build_system_prompt_text(_cfg())
    assert text == build_system_prompt_text(_cfg())
    for needle in ("acknowledge", "answers_pending", "age", "ठीक है धन्यवाद", "null"):
        assert needle in text


def test_classify_success_sends_strict_output_format():
    provider = MagicMock()
    provider.capabilities.supports_prompt_cache = True
    provider.call.return_value = _resp(GOOD)
    nlu = DialogueActNLU(_cfg(), provider)
    result, reason, ms = nlu.classify("<frame>...</frame>")
    assert reason is None and result.acts == ("acknowledge",) and ms >= 0
    req = provider.call.call_args.args[0]
    assert req.output_format.strict is True and req.output_format.schema == build_output_schema(_cfg())
    assert req.messages[0].content[0].text == "<frame>...</frame>"
    assert req.system.blocks[0].cache_hint == "session"


def test_classify_provider_error_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp(None, stop="error", error_type="timeout")
    result, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "provider_error:timeout" and result.acts == ("other",)


def test_classify_schema_violation_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp({"acts": ["shout"], "relation": "unclear"})
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "schema_violation"


def test_classify_missing_parsed_output_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp(None)
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "schema_violation"


def test_classify_exception_falls_back():
    provider = MagicMock()
    provider.call.side_effect = RuntimeError("boom")
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "exception"


def test_classify_empty_message_skips_llm():
    provider = MagicMock()
    result, reason, _ = DialogueActNLU(_cfg(), provider).classify("")
    assert reason == "empty_input" and provider.call.call_count == 0
```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/understanding/test_dialogue_act_nlu.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/dialogue_act_nlu.py
"""
agent_core/src/understanding/dialogue_act_nlu.py

The dialogue-act NLU LLM call: a static, prompt-cached system prompt, a
strict JSON-schema output, and a bounded fallback (NLU dialogue-acts spec
§5.1, §5.3, §9). Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatRequest, Message, OutputFormat, SystemPrompt, TextBlock
from src.understanding.config import DialogueActConfig, SlotSpec
from src.understanding.models import ACTS, RELATIONS, DialogueActResult

logger = logging.getLogger(__name__)

_ACT_TEXT = {
    "affirm": "says yes / agrees to what was asked (हाँ, जी हाँ, ठीक है — only as an answer to a yes/no question)",
    "deny": "says no / refuses what was asked (नहीं, मत करो)",
    "acknowledge": "only acknowledges or thanks, without answering anything (ठीक है, अच्छा, धन्यवाद, जी)",
    "provide_info": "gives a fact about themselves (age, trade, city, name, …)",
    "correct": "corrects something said earlier (\"X नहीं, Y\")",
    "select": "chooses one of the offered options (by number, name or description)",
    "ask": "asks a question",
    "request_change": "asks for something different (another search, change details)",
    "repeat": "asks the bot to say it again",
    "hold": "asks the bot to wait (एक मिनट, रुको)",
    "close": "clearly wants to end the call (बस, रखता हूँ, बाद में बात करते हैं) — a thank-you alone is NOT close",
    "other": "none of the above",
}
_RELATION_TEXT = {
    "answers_pending": "the turn answers the pending question",
    "answers_other": "the turn answers something else the bot needs, not the pending question",
    "new_topic": "the caller raises a different in-domain topic (a question, a change)",
    "unrelated": "the turn is unrelated to the call (background talk, other people, off-topic)",
    "unclear": "the turn cannot be understood or is only an acknowledgement with nothing pending to answer",
}


def _slot_schema(spec: SlotSpec) -> dict:
    if spec.type == "int":
        return {"type": ["integer", "null"]}
    if spec.type == "enum":
        return {"type": ["string", "null"], "enum": [*spec.values, None]}
    return {"type": ["string", "null"]}


def _obj(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def build_output_schema(cfg: DialogueActConfig) -> dict:
    """Strict-mode JSON schema for the NLU output (every object closed, every key required).

    Args:
        cfg: Parsed dialogue-act config.

    Returns:
        JSON schema dict.
    """
    signal_items = {"type": "string", "enum": list(cfg.signals)} if cfg.signals else {"type": "string"}
    return _obj({
        "acts": {"type": "array", "items": {"type": "string", "enum": list(ACTS)}},
        "relation": {"type": "string", "enum": list(RELATIONS)},
        "topic": {"type": ["string", "null"], "enum": [*cfg.topics, None]} if cfg.topics else {"type": "null"},
        "slots": _obj({name: _slot_schema(s) for name, s in cfg.slots.items()}),
        "reference": _obj({"option": {"type": ["integer", "null"]}, "spoken": {"type": ["string", "null"]}}),
        "signals": {"type": "array", "items": signal_items},
        "extras": {"type": "array", "items": _obj({"key": {"type": "string"}, "value": {"type": "string"}})},
    })


def _slot_line(spec: SlotSpec) -> str:
    kind = {"int": "integer", "enum": "one of " + ", ".join(spec.values)}.get(spec.type, "text")
    bounds = f" ({spec.min}–{spec.max})" if spec.type == "int" and spec.min is not None and spec.max is not None else ""
    ex = f" e.g. {'; '.join(spec.examples)}" if spec.examples else ""
    desc = f" — {spec.description}" if spec.description else ""
    return f"- {spec.name}: {kind}{bounds}{desc}{ex}"


def build_system_prompt_text(cfg: DialogueActConfig) -> str:
    """Render the static NLU system prompt. Depends only on config, never on the turn.

    Args:
        cfg: Parsed dialogue-act config.

    Returns:
        Prompt text (identical on every call — prompt-cacheable).
    """
    parts = [
        "You understand one caller turn in a phone conversation. You do NOT reply to the caller.",
        "Read <frame> (what the bot is waiting for, what was offered, what is already known), "
        "<recent> (the last exchanges) and <caller_now> (what the caller just said). "
        "Return only the JSON object described by the schema.",
        "",
        "Acts — what the caller did (1 to 3, in order):",
        *[f"- {a}: {_ACT_TEXT[a]}" for a in ACTS],
        "",
        "Relation — how the turn relates to the pending question:",
        *[f"- {r}: {_RELATION_TEXT[r]}" for r in RELATIONS],
        "",
        "Topics (only for ask / request_change; otherwise null): " + (", ".join(cfg.topics) or "none"),
        "",
        "Slots — values the caller SAID in <caller_now>; null when not said:",
        *[_slot_line(s) for s in cfg.slots.values()],
        "",
        "Rules:",
        "- Extract only what the caller said this turn. Never copy values from <frame> known: or <recent>.",
        "- A yes to a question other than the pending one is not an answer to the pending one.",
        "- Never infer consent from a yes to anything except the consent question.",
        "- Hindi number words become digits (बाईस → 22).",
        "- For select, set reference.option to the number of the offered option the caller means "
        "(by position, company or role) and reference.spoken to their words; otherwise both null.",
        "- extras: other personal details worth keeping, as key/value text pairs; usually empty.",
        "- signals: only from this list, when clearly present: " + (", ".join(cfg.signals) or "none"),
    ]
    if cfg.examples:
        parts += ["", "Examples:"]
        for ex in cfg.examples:
            parts.append(f"- pending: {ex.get('pending') or 'none'} | caller: {ex.get('caller', '')} → "
                         f"{json.dumps(ex.get('out', {}), ensure_ascii=False)}")
    return "\n".join(parts)


class DialogueActNLUBase(ABC):
    """Interface for the dialogue-act NLU call."""

    @abstractmethod
    def classify(self, user_message: str) -> tuple[DialogueActResult, str | None, int]:
        """Return (result, fallback_reason, latency_ms). Never raises."""


class DialogueActNLU(DialogueActNLUBase):
    """Calls the dedicated NLU provider with a strict output schema.

    Args:
        cfg: Parsed dialogue-act config (read once at startup).
        chat_provider: Dedicated provider (timeout/retry per spec §9.1).
    """

    def __init__(self, cfg: DialogueActConfig, chat_provider: ChatProviderBase) -> None:
        self._cfg = cfg
        self._provider = chat_provider
        self._system_text = build_system_prompt_text(cfg)
        self._schema = build_output_schema(cfg)

    def classify(self, user_message: str) -> tuple[DialogueActResult, str | None, int]:
        """Classify one rendered frame. Never raises.

        Args:
            user_message: Output of ``FrameBuilder.build``.

        Returns:
            (result, fallback_reason, latency_ms). ``fallback_reason`` is None
            on success; otherwise the result is ``DialogueActResult.fallback()``.
        """
        start = time.time()

        def _done(result: DialogueActResult, reason: str | None) -> tuple[DialogueActResult, str | None, int]:
            return result, reason, int((time.time() - start) * 1000)

        if not (user_message or "").strip():
            return _done(DialogueActResult.fallback(), "empty_input")
        try:
            hint = "session" if getattr(self._provider.capabilities, "supports_prompt_cache", False) else None
            response = self._provider.call(ChatRequest(
                messages=[Message(role="user", content=[TextBlock(text=user_message)])],
                system=SystemPrompt(blocks=[TextBlock(text=self._system_text, cache_hint=hint)]),
                output_format=OutputFormat(schema=self._schema, strict=True),
                max_tokens=400,
            ))
            if response.stop_reason == "error" and response.parsed_output is None:
                return _done(DialogueActResult.fallback(), f"provider_error:{response.error_type or 'unknown'}")
            try:
                return _done(DialogueActResult.from_parsed(
                    response.parsed_output, slot_names=tuple(self._cfg.slots),
                    topics=self._cfg.topics, signals=self._cfg.signals), None)
            except ValueError:
                return _done(DialogueActResult.fallback(), "schema_violation")
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.error("dialogue_act_nlu.error", extra={
                "operation": "dialogue_act_nlu.classify", "status": "failure",
                "error": type(e).__name__, "latency_ms": int((time.time() - start) * 1000)})
            return _done(DialogueActResult.fallback(), "exception")
```

> Note: the OpenAI provider's `_from_wire` sets `stop_reason="error"` and `parsed_output=None` when structured output fails to parse. That reaches the `provider_error:` branch with `error_type` None → `provider_error:unknown`, which is fine. The `test_classify_missing_parsed_output_falls_back` case has `stop_reason="end_turn"` and `parsed_output=None`, so it reaches `from_parsed(None)` → `schema_violation`.

- [ ] **Step 4: Run tests**: `cd agent_core && uv run pytest tests/understanding/test_dialogue_act_nlu.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/dialogue_act_nlu.py agent_core/tests/understanding/test_dialogue_act_nlu.py
git commit -m "feat(agent-core): dialogue-act NLU call with strict schema and bounded fallback"
```

---

### Task 11: Post-processing — normalise and accept slots

**Files:**
- Create: `agent_core/src/understanding/postprocess.py`
- Test: `agent_core/tests/understanding/test_postprocess_slots.py`

**Interfaces:**
- Consumes: `DialogueActConfig`, `SlotSpec`, `SlotRejection`
- Produces:
  - `normalise_slots(raw: dict, cfg) -> tuple[dict, list[SlotRejection]]` (slot name → normalised value; `None` values dropped silently)
  - `accept_slots(slots: dict, cfg, pending_id: str | None, acts: tuple[str, ...]) -> tuple[dict, list[SlotRejection]]`

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_postprocess_slots.py
"""Tests for slot normalisation and pending-scoped acceptance."""
from src.understanding.config import DialogueActConfig
from src.understanding.postprocess import accept_slots, normalise_slots


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act", "slots": {
        "age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
        "consent": {"type": "enum", "values": ["granted", "declined"], "accept_when_pending": ["consent"]},
        "trade": {"type": "string", "normalise": "title"},
        "city": {"type": "string", "normalise": "lower"},
    }}}})


def test_int_digits_and_range():
    ok, rej = normalise_slots({"age": "22", "trade": None}, _cfg())
    assert ok == {"age": 22} and rej == []
    ok, rej = normalise_slots({"age": 150}, _cfg())
    assert ok == {} and rej[0].reason == "normalise:out_of_range" and rej[0].value == 150
    ok, rej = normalise_slots({"age": "बाईस"}, _cfg())
    assert ok == {} and rej[0].reason == "normalise:not_integer"
    ok, rej = normalise_slots({"age": True}, _cfg())
    assert rej[0].reason == "normalise:not_integer"
    ok, rej = normalise_slots({"age": 0}, _cfg())
    assert rej[0].reason == "normalise:out_of_range"       # a seeded-looking 0 never passes


def test_enum_and_strings():
    ok, rej = normalise_slots({"consent": "granted", "trade": "  welder  ", "city": "Bengaluru"}, _cfg())
    assert ok == {"consent": "granted", "trade": "Welder", "city": "bengaluru"}
    ok, rej = normalise_slots({"consent": "maybe", "trade": "   "}, _cfg())
    assert ok == {} and {r.reason for r in rej} == {"normalise:not_in_enum", "normalise:empty"}


def test_unknown_slot_ignored():
    ok, rej = normalise_slots({"bogus": "x"}, _cfg())
    assert ok == {} and rej == []


def test_accept_when_pending():
    slots = {"consent": "granted", "age": 25, "trade": "Welder"}
    ok, rej = accept_slots(slots, _cfg(), pending_id="consent", acts=("affirm",))
    assert ok == {"consent": "granted", "trade": "Welder"}
    assert [(r.slot, r.reason) for r in rej] == [("age", "not_pending")]


def test_correct_act_overrides_accept_when_pending():
    ok, rej = accept_slots({"age": 26}, _cfg(), pending_id="select_job", acts=("correct",))
    assert ok == {"age": 26} and rej == []


def test_no_pending_rejects_scoped_slots():
    ok, rej = accept_slots({"consent": "granted"}, _cfg(), pending_id=None, acts=("affirm",))
    assert ok == {} and rej[0].reason == "not_pending"
```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/understanding/test_postprocess_slots.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/postprocess.py
"""
agent_core/src/understanding/postprocess.py

Deterministic post-processing of a DialogueActResult (NLU dialogue-acts spec
§6.1–§6.4, §6.6, §6.7): normalise and accept slots, resolve the option
reference, gate termination, derive the routing intent, count off-track turns.
Pure functions. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import re
from typing import Any

from src.understanding.config import DialogueActConfig, SlotSpec
from src.understanding.models import SlotRejection

_DIGITS = re.compile(r"^\s*\d+\s*$")


def _normalise_one(spec: SlotSpec, value: Any) -> tuple[Any, str | None]:
    """Return (value, None) on success or (None, reason)."""
    if spec.type == "int":
        if isinstance(value, bool):
            return None, "not_integer"
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and _DIGITS.match(value):
            number = int(value.strip())
        else:
            return None, "not_integer"
        if (spec.min is not None and number < spec.min) or (spec.max is not None and number > spec.max):
            return None, "out_of_range"
        return number, None
    if spec.type == "enum":
        return (value, None) if value in spec.values else (None, "not_in_enum")
    text = str(value).strip()
    if not text:
        return None, "empty"
    if spec.normalise == "title":
        text = text.title()
    elif spec.normalise == "lower":
        text = text.lower()
    return text, None


def normalise_slots(raw: dict, cfg: DialogueActConfig) -> tuple[dict, list[SlotRejection]]:
    """Validate and normalise slot values per config (spec §6.1).

    Args:
        raw: Slot name → raw value (None means "not said").
        cfg: Parsed dialogue-act config.

    Returns:
        (normalised slot name → value, rejections). None values and unknown
        slot names are skipped without a rejection.
    """
    ok: dict = {}
    rejected: list[SlotRejection] = []
    for name, value in (raw or {}).items():
        spec = cfg.slots.get(name)
        if spec is None or value is None:
            continue
        normalised, reason = _normalise_one(spec, value)
        if reason:
            rejected.append(SlotRejection(slot=name, value=value, reason=f"normalise:{reason}"))
        else:
            ok[name] = normalised
    return ok, rejected


def accept_slots(slots: dict, cfg: DialogueActConfig, pending_id: str | None,
                 acts: tuple[str, ...]) -> tuple[dict, list[SlotRejection]]:
    """Keep pending-scoped slots only while their question is pending (spec §6.2).

    Args:
        slots: Normalised slots.
        cfg: Parsed config.
        pending_id: Resolved pending id, or None.
        acts: The turn's acts; ``correct`` lifts the restriction.

    Returns:
        (accepted slots, ``not_pending`` rejections).
    """
    ok: dict = {}
    rejected: list[SlotRejection] = []
    for name, value in slots.items():
        scope = cfg.slots[name].accept_when_pending
        if scope and pending_id not in scope and "correct" not in acts:
            rejected.append(SlotRejection(slot=name, value=value, reason="not_pending"))
        else:
            ok[name] = value
    return ok, rejected
```

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/postprocess.py agent_core/tests/understanding/test_postprocess_slots.py
git commit -m "feat(agent-core): slot normalisation and pending-scoped acceptance"
```

---

### Task 12: Post-processing — option resolver

**Files:**
- Modify: `agent_core/src/understanding/postprocess.py`
- Test: `agent_core/tests/understanding/test_postprocess_resolve.py`

**Interfaces:**
- Consumes: `PendingQuestion`, `offered_rows`/`option_label` (Task 9), `ResolvedReference`, `UnresolvedReference`
- Produces: `resolve_reference(option: int | None, pending: PendingQuestion | None, rows: list[dict]) -> tuple[ResolvedReference | None, UnresolvedReference | None]`

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_postprocess_resolve.py
"""Tests for option resolution."""
from src.understanding.postprocess import resolve_reference
from src.workflow_loader import OptionsFrom, PendingQuestion

P = PendingQuestion("select_job", options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"),
                    resolves_to="selected_job_item_id")
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan"},
        {"role": "Welder", "company": "NoId"}]


def test_resolves_in_range():
    r, u = resolve_reference(2, P, ROWS)
    assert u is None and (r.option, r.id, r.label, r.id_field) == (2, "j2", "Welder · Titan", "item_id")


def test_out_of_range_and_zero():
    for opt in (0, 4, -1):
        r, u = resolve_reference(opt, P, ROWS)
        assert r is None and u.reason == "out_of_range" and u.offered == 3


def test_no_rows():
    r, u = resolve_reference(1, P, [])
    assert r is None and u.reason == "no_options" and u.offered == 0


def test_row_without_id():
    r, u = resolve_reference(3, P, ROWS)
    assert r is None and u.reason == "missing_id"


def test_nothing_to_resolve():
    assert resolve_reference(None, P, ROWS) == (None, None)
    assert resolve_reference(1, None, ROWS) == (None, None)
    assert resolve_reference(1, PendingQuestion("age"), ROWS) == (None, None)
```

- [ ] **Step 2: Run to verify failure** → FAIL (`ImportError`).

- [ ] **Step 3: Implement** (append to `postprocess.py`, and add the imports at the top)

```python
from src.understanding.frame import option_label
from src.understanding.models import ResolvedReference, UnresolvedReference
from src.workflow_loader import PendingQuestion


def resolve_reference(option: int | None, pending: PendingQuestion | None,
                      rows: list[dict]) -> tuple[ResolvedReference | None, UnresolvedReference | None]:
    """Map an offered-option number to its row id (spec §6.3).

    Args:
        option: 1-based option number from NLU, or None.
        pending: Resolved pending question; only one with ``options_from`` resolves.
        rows: The rows rendered as ``offered`` this turn (same entry, same order).

    Returns:
        (resolved, None), (None, unresolved), or (None, None) when there is
        nothing to resolve.
    """
    if option is None or pending is None or pending.options_from is None:
        return None, None
    if not rows:
        return None, UnresolvedReference(option=option, offered=0, reason="no_options")
    if not 1 <= option <= len(rows):
        return None, UnresolvedReference(option=option, offered=len(rows), reason="out_of_range")
    row = rows[option - 1]
    of = pending.options_from
    row_id = row.get(of.id_field)
    if row_id in (None, ""):
        return None, UnresolvedReference(option=option, offered=len(rows), reason="missing_id")
    return ResolvedReference(option=option, id=str(row_id), label=option_label(row, of.fields),
                             id_field=of.id_field), None
```

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/postprocess.py agent_core/tests/understanding/test_postprocess_resolve.py
git commit -m "feat(agent-core): resolve offered-option references against stored rows"
```

---

### Task 13: Post-processing — termination gate, intent derivation, off-track counter

**Files:**
- Modify: `agent_core/src/understanding/postprocess.py`
- Test: `agent_core/tests/understanding/test_postprocess_derive.py`

**Interfaces:**
- Consumes: `DialogueActConfig.act_intents`, `.gate`, `.off_track_*`; `evaluate_condition` (Task 2)
- Produces:
  - `gate_passes(cfg, pending_id: str | None, state: dict) -> bool`
  - `derive_intent(dialogue: DialogueActResult, pending_id: str | None, cfg, *, gate_ok: bool, resolved: bool) -> tuple[str, ActIntentRule | None]`. Returns `("any_input", None)` when no row matches. A row containing `select` needs `resolved=True`.
  - `next_off_track(prev: int, relation: str, cfg, *, is_fallback: bool) -> tuple[int, bool]` (new count, tripped)

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_postprocess_derive.py
"""Tests for the termination gate, intent derivation and off-track counter."""
from src.understanding.config import DialogueActConfig
from src.understanding.models import DialogueActResult
from src.understanding.postprocess import derive_intent, gate_passes, next_off_track


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["search", "salary"],
        "act_intents": [
            {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "apply_now"},
            {"acts": ["deny"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "decline"},
            {"acts": ["select"], "pending": "select_job", "intent": "job_pick"},
            {"acts": ["request_change"], "topic": "search", "intent": "explore_more"},
            {"acts": ["close"], "intent": "termination_intent", "gated": True},
        ],
        "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                        {"field": "applications_submitted", "operator": "gt", "value": 0}]},
        "off_track": {"threshold": 3, "intent": "off_track"},
    }}})


def D(*acts, relation="answers_pending", topic=None):
    return DialogueActResult(acts=tuple(acts), relation=relation, topic=topic)


def test_affirm_to_submit_is_apply_now():
    assert derive_intent(D("affirm"), "submit_confirm", _cfg(), gate_ok=False, resolved=False)[0] == "apply_now"


def test_thank_you_while_submit_pending_is_never_apply_or_terminate():
    for d in (D("acknowledge", relation="unclear"), D("acknowledge", relation="answers_pending"),
              D("affirm", relation="unclear")):
        intent, _ = derive_intent(d, "submit_confirm", _cfg(), gate_ok=False, resolved=False)
        assert intent == "any_input"


def test_affirm_answering_something_else_is_not_apply_now():
    intent, _ = derive_intent(D("affirm", relation="answers_other"), "submit_confirm", _cfg(),
                              gate_ok=False, resolved=False)
    assert intent == "any_input"


def test_select_needs_a_resolved_reference():
    assert derive_intent(D("select"), "select_job", _cfg(), gate_ok=False, resolved=True)[0] == "job_pick"
    assert derive_intent(D("select"), "select_job", _cfg(), gate_ok=False, resolved=False)[0] == "any_input"


def test_multi_act_row_matches_subset():
    intent, _ = derive_intent(D("select", "affirm"), "select_job", _cfg(), gate_ok=False, resolved=True)
    assert intent == "job_pick"


def test_topic_row():
    d = D("request_change", relation="new_topic", topic="search")
    assert derive_intent(d, "select_job", _cfg(), gate_ok=False, resolved=False)[0] == "explore_more"


def test_close_is_gated():
    intent, rule = derive_intent(D("close"), "select_job", _cfg(), gate_ok=False, resolved=False)
    assert intent == "any_input" and rule is None
    intent, rule = derive_intent(D("close"), "closing_offer", _cfg(), gate_ok=True, resolved=False)
    assert intent == "termination_intent" and rule.gated


def test_gate_passes_on_pending_or_condition():
    c = _cfg()
    assert gate_passes(c, "closing_offer", {})
    assert gate_passes(c, "select_job", {"applications_submitted": 1})
    assert not gate_passes(c, "select_job", {"applications_submitted": 0})
    assert not gate_passes(c, None, {})


def test_off_track_counter():
    c = _cfg()
    assert next_off_track(0, "unrelated", c, is_fallback=False) == (1, False)
    assert next_off_track(2, "unclear", c, is_fallback=False) == (3, True)
    assert next_off_track(2, "answers_pending", c, is_fallback=False) == (0, False)
    assert next_off_track(2, "new_topic", c, is_fallback=False) == (2, False)
    assert next_off_track(2, "unclear", c, is_fallback=True) == (2, False)
```

- [ ] **Step 2: Run to verify failure** → FAIL (`ImportError`).

- [ ] **Step 3: Implement** (append to `postprocess.py`; add `from src.conditions import evaluate_condition`, `from src.understanding.config import ActIntentRule`, `from src.understanding.models import DialogueActResult`)

```python
_OFF_TRACK_RELATIONS = ("unrelated", "unclear")


def gate_passes(cfg: DialogueActConfig, pending_id: str | None, state: dict) -> bool:
    """True when any termination-gate item holds (spec §6.7).

    Args:
        cfg: Parsed config.
        pending_id: Resolved pending id, or None.
        state: Merged routing state.

    Returns:
        True if a gated row may fire this turn.
    """
    for item in cfg.gate:
        if item.pending is not None:
            if pending_id == item.pending:
                return True
        elif item.condition is not None and evaluate_condition(item.condition, state or {}):
            return True
    return False


def derive_intent(dialogue: DialogueActResult, pending_id: str | None, cfg: DialogueActConfig, *,
                  gate_ok: bool, resolved: bool) -> tuple[str, ActIntentRule | None]:
    """First matching act_intents row → routing intent (spec §6.4).

    A row matches when the turn's acts contain all of the row's acts, and the
    row's pending / relation / topic (each optional) equal the turn's. A row
    containing ``select`` also needs a resolved reference; a gated row needs
    ``gate_ok``. Unmatched turns are ``any_input``.

    Args:
        dialogue: Validated NLU result.
        pending_id: Resolved pending id, or None.
        cfg: Parsed config.
        gate_ok: Result of :func:`gate_passes`.
        resolved: Whether the option reference resolved.

    Returns:
        (intent, matched rule or None).
    """
    acts = set(dialogue.acts)
    for rule in cfg.act_intents:
        if rule.acts and not set(rule.acts) <= acts:
            continue
        if rule.pending is not None and rule.pending != pending_id:
            continue
        if rule.relation is not None and rule.relation != dialogue.relation:
            continue
        if rule.topic is not None and rule.topic != dialogue.topic:
            continue
        if "select" in rule.acts and not resolved:
            continue
        if rule.gated and not gate_ok:
            continue
        return rule.intent, rule
    return "any_input", None


def next_off_track(prev: int, relation: str, cfg: DialogueActConfig, *,
                   is_fallback: bool) -> tuple[int, bool]:
    """Advance the consecutive off-track counter (spec §6.6).

    Args:
        prev: Current ``off_track_count``.
        relation: This turn's relation.
        cfg: Parsed config.
        is_fallback: True when NLU failed (a system failure never counts).

    Returns:
        (new count, tripped) — tripped when the count reaches the threshold.
    """
    if is_fallback:
        return prev, False
    if relation == "answers_pending":
        return 0, False
    if relation in _OFF_TRACK_RELATIONS:
        count = prev + 1
        return count, count >= cfg.off_track_threshold
    return prev, False
```

- [ ] **Step 4: Run tests** → PASS. Run `cd agent_core && uv run pytest tests/understanding -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/postprocess.py agent_core/tests/understanding/test_postprocess_derive.py
git commit -m "feat(agent-core): termination gate, act→intent derivation and off-track counter"
```

---

### Task 14: Write plan and provenance-based precedence

**Files:**
- Create: `agent_core/src/understanding/precedence.py`, `agent_core/src/understanding/slot_writer.py`
- Modify: `agent_core/src/orchestrator.py`:
  - add a `_routing_state` helper and use it at both `routing_state = dict(bundle.session)` sites;
  - extend `_build_profile_context` and `_tool_session_values`.
- Test: `agent_core/tests/understanding/test_slot_writer.py`, `agent_core/tests/test_orchestrator_precedence.py`

**Interfaces:**
- Consumes: `DialogueActConfig`, `PendingQuestion`, `ResolvedReference`, `StateWrite`, `SlotUpdate`
- Produces:
  - `PROVENANCE_KEY = "slot_provenance"`, `nlu_owned_values(session: dict) -> dict`
  - `plan_writes(*, accepted: dict, cfg, state: dict, session: dict, pending: PendingQuestion | None, resolved: ResolvedReference | None, extras: tuple[tuple[str, str], ...], off_track_count: int) -> tuple[list[StateWrite], list[SlotUpdate]]`
  - `AgentCore._routing_state(bundle) -> dict` (static)

- [ ] **Step 1: Failing tests (pure)**

```python
# agent_core/tests/understanding/test_slot_writer.py
"""Tests for the write plan and NLU-owned precedence."""
from src.understanding.config import DialogueActConfig
from src.understanding.models import ResolvedReference, SlotUpdate, StateWrite
from src.understanding.precedence import PROVENANCE_KEY, nlu_owned_values
from src.understanding.slot_writer import plan_writes
from src.workflow_loader import OptionsFrom, PendingQuestion


def _cfg():
    return DialogueActConfig.from_config({
        "entity_to_profile_field": {"consent": "consent_response"},
        "entity_persistence": {"scope": "session"},
        "preprocessing": {"nlu_processor": {"mode": "dialogue_act", "slots": {
            "consent": {"type": "enum", "values": ["granted", "declined"]},
            "trade": {"type": "string"}, "age": {"type": "int", "min": 14, "max": 80}}}}})


def _plan(**kw):
    base = dict(accepted={}, cfg=_cfg(), state={}, session={}, pending=None, resolved=None,
                extras=(), off_track_count=0)
    base.update(kw)
    return plan_writes(**base)


def test_new_value_written_with_provenance_and_no_update_record():
    writes, updates = _plan(accepted={"consent": "granted"})
    assert StateWrite("session", "consent_response", "granted") in writes
    assert StateWrite("session", PROVENANCE_KEY, ["consent_response"]) in writes
    assert updates == []


def test_correction_over_existing_value_is_an_update():
    writes, updates = _plan(accepted={"trade": "Welder"}, state={"trade": "Electrician"},
                            session={PROVENANCE_KEY: ["trade"]})
    assert StateWrite("session", "trade", "Welder") in writes
    assert updates == [SlotUpdate("trade", "Electrician", "Welder")]
    assert not any(w.key == PROVENANCE_KEY for w in writes)        # unchanged provenance not rewritten


def test_seeded_zero_is_not_an_update():
    _, updates = _plan(accepted={"age": 25}, state={"age": 0})
    assert updates == []


def test_unchanged_value_writes_nothing():
    writes, updates = _plan(accepted={"trade": "Welder"}, state={"trade": "Welder"})
    assert writes == [] and updates == []


def test_resolved_reference_written_to_resolves_to():
    p = PendingQuestion("select_job", options_from=OptionsFrom("fetch_jobs", ("role",), "item_id"),
                        resolves_to="selected_job_item_id")
    writes, _ = _plan(pending=p, resolved=ResolvedReference(1, "j1", "Welder", "item_id"))
    assert StateWrite("session", "selected_job_item_id", "j1") in writes


def test_extras_merge_and_off_track_count_change():
    writes, _ = _plan(extras=(("tool", "drill"),), session={"nlu_extras": {"pet": "dog"}, "off_track_count": 1},
                      off_track_count=2)
    assert StateWrite("session", "nlu_extras", {"pet": "dog", "tool": "drill"}) in writes
    assert StateWrite("session", "off_track_count", 2) in writes


def test_nlu_owned_values_skips_seeds_and_unlisted():
    s = {PROVENANCE_KEY: ["trade", "age", "ghost"], "trade": "Welder", "age": 0, "location": "X"}
    assert nlu_owned_values(s) == {"trade": "Welder"}
    assert nlu_owned_values({}) == {}
    assert nlu_owned_values({PROVENANCE_KEY: "bad"}) == {}
```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/understanding/test_slot_writer.py -v` → FAIL.

- [ ] **Step 3: Implement the pure modules**

```python
# agent_core/src/understanding/precedence.py
"""
agent_core/src/understanding/precedence.py

Provenance-based precedence (NLU dialogue-acts spec §6.5): a value the
SlotWriter wrote this session beats the stored profile; every other session
copy keeps the profile-first rule that guards against seeded defaults
(e.g. a session ``age`` of 0 outranking a real profile 25).

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

PROVENANCE_KEY = "slot_provenance"
_SEEDS = (None, "", [], {}, 0, "0")


def nlu_owned_values(session: dict | None) -> dict:
    """Session values for keys the SlotWriter wrote, excluding seeded defaults.

    Args:
        session: Session state (``bundle.session``).

    Returns:
        key → value to layer over the profile. Empty in intent mode.
    """
    session = session or {}
    keys = session.get(PROVENANCE_KEY)
    if not isinstance(keys, list):
        return {}
    return {k: session[k] for k in keys if isinstance(k, str) and session.get(k) not in _SEEDS}
```

```python
# agent_core/src/understanding/slot_writer.py
"""
agent_core/src/understanding/slot_writer.py

Builds the list of state writes a turn's understanding implies (NLU
dialogue-acts spec §6.3, §6.5, §6.6). Pure: the orchestrator applies the
writes through its existing Memory Layer helpers on both paths.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from src.understanding.config import DialogueActConfig
from src.understanding.models import ResolvedReference, SlotUpdate, StateWrite
from src.understanding.precedence import PROVENANCE_KEY
from src.workflow_loader import PendingQuestion

_SEEDS = (None, "", [], {}, 0, "0")


def plan_writes(*, accepted: dict, cfg: DialogueActConfig, state: dict, session: dict,
                pending: PendingQuestion | None, resolved: ResolvedReference | None,
                extras: tuple[tuple[str, str], ...], off_track_count: int
                ) -> tuple[list[StateWrite], list[SlotUpdate]]:
    """Plan the turn's writes.

    Args:
        accepted: Accepted slot name → normalised value (never None).
        cfg: Parsed config (state keys, entity scope).
        state: Merged routing state, for change detection.
        session: Raw session state, for provenance / extras / counter.
        pending: Resolved pending question, or None.
        resolved: Resolved option reference, or None.
        extras: Ad-hoc (key, value) pairs.
        off_track_count: New off-track counter value.

    Returns:
        (writes, updates) — updates list only changes over a non-seed value.
    """
    writes: list[StateWrite] = []
    updates: list[SlotUpdate] = []
    provenance = [k for k in (session.get(PROVENANCE_KEY) or []) if isinstance(k, str)]
    added = False
    for slot, value in accepted.items():
        key = cfg.state_key(slot)
        old = state.get(key)
        if old == value:
            continue
        writes.append(StateWrite(cfg.entity_scope, key, value))
        if old not in _SEEDS:
            updates.append(SlotUpdate(key, old, value))
        if key not in provenance:
            provenance.append(key)
            added = True
    if added:
        writes.append(StateWrite("session", PROVENANCE_KEY, provenance))
    if resolved is not None and pending is not None and pending.resolves_to:
        writes.append(StateWrite("session", pending.resolves_to, resolved.id))
    if extras:
        merged = dict(session.get("nlu_extras") or {})
        merged.update(dict(extras))
        writes.append(StateWrite("session", "nlu_extras", merged))
    if off_track_count != int(session.get("off_track_count") or 0):
        writes.append(StateWrite("session", "off_track_count", off_track_count))
    return writes, updates
```

- [ ] **Step 4: Run pure tests** → PASS.

- [ ] **Step 5: Failing tests (orchestrator precedence)**

```python
# agent_core/tests/test_orchestrator_precedence.py
"""NLU-owned session values win over the stored profile; nothing else changes."""
from src.models import ContextBundle
from src.orchestrator import AgentCore
from src.understanding.precedence import PROVENANCE_KEY
from tests.test_orchestrator import _make_agent


def _bundle(session, profile):
    return ContextBundle(session=session, profile=profile, journey=None)


def test_routing_state_profile_first_unless_nlu_owned():
    b = _bundle({"trade": "Welder", "age": 0, PROVENANCE_KEY: ["trade"]},
                {"trade": "Electrician", "age": 25})
    state = AgentCore._routing_state(b)
    assert state["trade"] == "Welder" and state["age"] == 25


def test_routing_state_without_provenance_is_todays_merge():
    b = _bundle({"trade": "Welder"}, {"trade": "Electrician"})
    assert AgentCore._routing_state(b)["trade"] == "Electrician"


def test_profile_context_and_tool_values_honour_provenance():
    agent = _make_agent()
    b = _bundle({"trade": "Welder", PROVENANCE_KEY: ["trade"]}, {"trade": "Electrician"})
    assert agent._build_profile_context(b, {"trade": "trade"})["trade"] == "Welder"
    assert AgentCore._tool_session_values(b)["trade"] == "Welder"
```

- [ ] **Step 6: Run to verify failure**: `cd agent_core && uv run pytest tests/test_orchestrator_precedence.py -v` → FAIL (`_routing_state` missing; Welder vs Electrician).

- [ ] **Step 7: Implement in the orchestrator**

Add the import `from src.understanding.precedence import nlu_owned_values`. Add the static method next to `_tool_session_values`:

```python
    @staticmethod
    def _routing_state(bundle) -> dict:
        """Session, then profile, then NLU-owned session values (NLU dialogue-acts spec §6.5).

        Identical to the previous inline merge when no SlotWriter provenance
        exists (intent mode).

        Args:
            bundle: The turn's ContextBundle.

        Returns:
            Merged state for routing and pending resolution.
        """
        state = dict(bundle.session or {})
        if bundle.profile:
            state.update(bundle.profile)
        state.update(nlu_owned_values(bundle.session))
        return state
```

Replace **both** occurrences of

```python
        routing_state = dict(bundle.session)
        if bundle.profile:
            routing_state.update(bundle.profile)
```

(sync ~line 1105, stream ~line 4093; keep the stream site's indentation) with `routing_state = self._routing_state(bundle)`.

At the end of `_build_profile_context`, before `return profile_context`, add `profile_context.update(nlu_owned_values(bundle.session))`. At the end of `_tool_session_values`, before `return values`, add `values.update(nlu_owned_values(getattr(bundle, "session", None)))`. In each docstring, add one sentence: "NLU-owned values (slot_provenance) win; see NLU dialogue-acts spec §6.5."

- [ ] **Step 8: Run tests**

Run: `cd agent_core && uv run pytest tests/test_orchestrator_precedence.py tests/test_orchestrator.py tests/test_stream_turn.py tests/test_turn_path_identity_parity.py -q`
Expected: all PASS (intent-mode behaviour unchanged).

- [ ] **Step 9: Commit**

```bash
git add agent_core/src/understanding/precedence.py agent_core/src/understanding/slot_writer.py \
        agent_core/src/orchestrator.py agent_core/tests/understanding/test_slot_writer.py \
        agent_core/tests/test_orchestrator_precedence.py
git commit -m "feat(agent-core): write plan and provenance-based precedence for NLU-owned slots"
```

---

### Task 15: `<caller_turn>` render and prompt slot

**Files:**
- Create: `agent_core/src/understanding/caller_turn.py`
- Modify: `agent_core/src/manager_agent.py` (`build_system_prompt` gains `caller_turn: str = ""`)
- Test: `agent_core/tests/understanding/test_caller_turn.py`, `agent_core/tests/test_manager_agent.py`

**Interfaces:**
- Consumes: `TurnUnderstanding` and friends (Task 6)
- Produces: `render_caller_turn(u: TurnUnderstanding | None) -> str` (empty for None or intent mode); `ManagerAgent.build_system_prompt(..., caller_turn: str = "")` renders `<caller_turn>` in Tier 3, directly after `<known_facts>`

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_caller_turn.py
"""Snapshot tests for the <caller_turn> body."""
from src.models import NLUResult
from src.understanding.caller_turn import render_caller_turn
from src.understanding.models import (DialogueActResult, ResolvedReference, SlotRejection, SlotUpdate,
                                      TurnUnderstanding, UnresolvedReference)

NR = NLUResult(intent="job_pick", entities={}, sentiment="neutral", confidence=1.0)


def test_full_render():
    u = TurnUnderstanding(
        nlu_result=NR,
        dialogue=DialogueActResult(acts=("select", "correct"), relation="answers_pending",
                                   signals=("pay_disappointment",)),
        pending_id="select_job",
        resolved=ResolvedReference(1, "7f3c", "Welder · Flipkart", "item_id"),
        updates=[SlotUpdate("trade", "Electrician", "Welder")],
        rejected_slots=[SlotRejection("age", 150, "normalise:out_of_range"),
                        SlotRejection("consent", "granted", "not_pending")],
        signals=["pay_disappointment"])
    assert render_caller_turn(u) == (
        "acts: select, correct · relation: answers_pending\n"
        "pending: select_job (now answered)\n"
        "resolved: option 1 — Welder · Flipkart (item_id 7f3c)\n"
        "updated: trade Electrician → Welder (caller corrected)\n"
        'not accepted: age "150" (out_of_range)\n'
        "signals: pay_disappointment"
    )


def test_off_script_keeps_question_open():
    u = TurnUnderstanding(nlu_result=NR, pending_id="age",
                          dialogue=DialogueActResult(acts=("ask",), relation="new_topic", topic="salary"))
    assert render_caller_turn(u) == ("acts: ask · relation: new_topic · topic: salary\n"
                                     "open: age — still unanswered")


def test_unresolved_and_changed_without_correct():
    u = TurnUnderstanding(nlu_result=NR, pending_id="select_job",
                          dialogue=DialogueActResult(acts=("select",), relation="answers_pending"),
                          unresolved=UnresolvedReference(4, 3, "out_of_range"),
                          updates=[SlotUpdate("location", "Pune", "Hubli")])
    text = render_caller_turn(u)
    assert "caller referred to option 4; 3 offered" in text
    assert "updated: location Pune → Hubli" in text and "corrected" not in text


def test_off_track_line():
    u = TurnUnderstanding(nlu_result=NR, pending_id="age", off_track_tripped=True,
                          dialogue=DialogueActResult(acts=("other",), relation="unrelated"))
    assert render_caller_turn(u).endswith("re-ask the open question simply, or offer to end the call")


def test_fallback_and_none():
    u = TurnUnderstanding(nlu_result=NR, fallback_reason="provider_error:timeout")
    assert render_caller_turn(u) == "understanding unavailable this turn"
    assert render_caller_turn(None) == ""
    assert render_caller_turn(TurnUnderstanding(nlu_result=NR)) == ""     # intent mode: no dialogue
```

Append to `agent_core/tests/test_manager_agent.py`:

```python
def test_caller_turn_rendered_after_known_facts_in_tier3():
    agent, *_ = _make_manager(llm_responses=[], tool_result=None)
    sp = agent.build_system_prompt(agent_system_prompt="a", subagent_system_prompt="b",
                                   detected_language="hindi", channel="voice", profile={},
                                   known_facts="F1", caller_turn="acts: affirm")
    tier3 = sp.blocks[-1].text
    assert "<caller_turn>" in tier3 and tier3.index("known_facts") < tier3.index("caller_turn")
    assert sp.blocks[-1].cache_hint is None


def test_caller_turn_absent_by_default():
    agent, *_ = _make_manager(llm_responses=[], tool_result=None)
    sp = agent.build_system_prompt(agent_system_prompt="a", subagent_system_prompt="b",
                                   detected_language="hindi", channel="voice", profile={})
    assert all("<caller_turn>" not in b.text for b in sp.blocks)
```

(If `_make_manager` needs different arguments for "no LLM calls", follow how the file's existing `build_system_prompt` tests construct the agent.)

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement**

```python
# agent_core/src/understanding/caller_turn.py
"""
agent_core/src/understanding/caller_turn.py

Renders the <caller_turn> body: NLU's structured conclusion for the main LLM
(NLU dialogue-acts spec §6.8). Values here reach only the LLM prompt, like
<known_profile>; never logs.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from src.understanding.models import TurnUnderstanding


def render_caller_turn(u: TurnUnderstanding | None) -> str:
    """Render the <caller_turn> body, or "" when there is nothing to say.

    Args:
        u: This turn's understanding; None or a dialogue-less (intent-mode)
            result renders nothing.

    Returns:
        Newline-joined lines; empty lines are omitted.
    """
    if u is None:
        return ""
    if u.fallback_reason:
        return "understanding unavailable this turn"
    d = u.dialogue
    if d is None:
        return ""
    head = f"acts: {', '.join(d.acts)} · relation: {d.relation}"
    if d.topic:
        head += f" · topic: {d.topic}"
    lines = [head]
    if u.pending_id:
        lines.append(f"pending: {u.pending_id} (now answered)" if d.relation == "answers_pending"
                     else f"open: {u.pending_id} — still unanswered")
    if u.resolved:
        r = u.resolved
        lines.append(f"resolved: option {r.option} — {r.label} ({r.id_field} {r.id})")
    if u.unresolved:
        lines.append(f"caller referred to option {u.unresolved.option}; {u.unresolved.offered} offered")
    corrected = "correct" in d.acts
    for up in u.updates:
        lines.append(f"updated: {up.key} {up.old} → {up.new}" + (" (caller corrected)" if corrected else ""))
    for rej in u.rejected_slots:
        if rej.reason.startswith("normalise:"):
            lines.append(f'not accepted: {rej.slot} "{rej.value}" ({rej.reason.split(":", 1)[1]})')
    if u.signals:
        lines.append("signals: " + ", ".join(u.signals))
    if u.off_track_tripped:
        lines.append("off track: several turns in a row — re-ask the open question simply, or offer to end the call")
    return "\n".join(lines)
```

In `manager_agent.py`, `build_system_prompt`:
- add the parameter `caller_turn: str = ""` after `known_facts`;
- document it in Args: "Rendered NLU conclusion (`render_caller_turn`); empty elides `<caller_turn>`.";
- add `<caller_turn>` to the Tier 3 list in the docstring;
- in `tier3 = join([...])`, insert `xml("caller_turn", caller_turn),` directly after `xml("known_facts", known_facts),`.

- [ ] **Step 4: Run tests**: `cd agent_core && uv run pytest tests/understanding/test_caller_turn.py tests/test_manager_agent.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/understanding/caller_turn.py agent_core/src/manager_agent.py \
        agent_core/tests/understanding/test_caller_turn.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent-core): render NLU's conclusion as <caller_turn> in prompt tier 3"
```

---

### Task 16: `TurnUnderstander` — composition, history helper, logging and metrics

**Files:**
- Create: `agent_core/src/understanding/understander.py`, `agent_core/src/understanding/history.py`
- Test: `agent_core/tests/understanding/test_understander.py`, `agent_core/tests/understanding/test_history.py`

**Interfaces:**
- Consumes: everything from Tasks 6–14
- Produces:
  - `TurnContext(subagent_id: str, state: dict, session: dict, segments: list[str], recent: list[dict], tool_cache: Any | None)` (frozen dataclass)
  - `TurnUnderstanderBase.understand(ctx) -> TurnUnderstanding` (never raises)
  - `TurnUnderstander(cfg, workflow, nlu, frame=None, resolver=None)`, `TurnUnderstander.from_config(config, workflow, chat_provider) -> TurnUnderstander | None`, `.config -> DialogueActConfig`
  - `RECENT_TURNS_KEY = "recent_turns"`; `append_recent_turn(existing, *, caller: str, bot: str, interrupted: bool, history_turns: int) -> list[dict]`. Keeps the last `history_turns` exchanges; `history_turns == 0` keeps none.

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/understanding/test_history.py
from src.understanding.history import append_recent_turn


def test_appends_and_caps():
    e = append_recent_turn(None, caller="a", bot="b", interrupted=False, history_turns=2)
    e = append_recent_turn(e, caller="c", bot="d", interrupted=True, history_turns=2)
    e = append_recent_turn(e, caller="e", bot="f", interrupted=False, history_turns=2)
    assert e == [{"caller": "c", "bot": "d", "interrupted": True},
                 {"caller": "e", "bot": "f", "interrupted": False}]


def test_zero_history_and_junk_existing():
    assert append_recent_turn([{"caller": "x"}], caller="a", bot="b", interrupted=False, history_turns=0) == []
    assert append_recent_turn("bad", caller="a", bot="b", interrupted=False, history_turns=1) == [
        {"caller": "a", "bot": "b", "interrupted": False}]
```

```python
# agent_core/tests/understanding/test_understander.py
"""End-to-end tests of TurnUnderstander with a scripted NLU."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.understanding.config import DialogueActConfig
from src.understanding.models import DialogueActResult, StateWrite
from src.understanding.precedence import PROVENANCE_KEY
from src.understanding.understander import TurnContext, TurnUnderstander
from src.workflow_loader import OptionsFrom, PendingQuestion, RoutingCondition

CONFIG = {
    "entity_to_profile_field": {"consent": "consent_response"},
    "entity_persistence": {"scope": "session"},
    "preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["salary", "search"], "signals": ["pay_disappointment"],
        "slots": {"consent": {"type": "enum", "values": ["granted", "declined"], "accept_when_pending": ["consent"]},
                  "age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
                  "trade": {"type": "string", "normalise": "title"}},
        "known_fields": ["consent", "trade"],
        "act_intents": [
            {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending", "intent": "apply_now"},
            {"acts": ["select"], "pending": "select_job", "intent": "job_pick"},
            {"acts": ["close"], "intent": "termination_intent", "gated": True},
        ],
        "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                        {"field": "applications_submitted", "operator": "gt", "value": 0}]},
        "off_track": {"threshold": 2, "intent": "off_track"},
    }},
}
WF = SimpleNamespace(subagents={
    "opening": SimpleNamespace(pending=[PendingQuestion(
        "consent", "हाँ/नहीं", when=(RoutingCondition("consent_response", "in", [None, ""]),))]),
    "job_match": SimpleNamespace(pending=[PendingQuestion(
        "select_job", "एक नौकरी", options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"),
        resolves_to="selected_job_item_id")]),
    "apply_confirm": SimpleNamespace(pending=[
        PendingQuestion("closing_offer", when=(RoutingCondition("applications_submitted", "gt", 0),)),
        PendingQuestion("submit_confirm")]),
})


def _u(result: DialogueActResult, reason=None):
    nlu = MagicMock()
    nlu.classify.return_value = (result, reason, 7)
    return TurnUnderstander(DialogueActConfig.from_config(CONFIG), WF, nlu), nlu


def _ctx(subagent, state=None, session=None, cache=None, segments=("x",)):
    return TurnContext(subagent_id=subagent, state=state or {}, session=session or {},
                       segments=list(segments), recent=[], tool_cache=cache)


def test_consent_answer_writes_mapped_key_and_provenance():
    und, nlu = _u(DialogueActResult(acts=("affirm",), relation="answers_pending",
                                    slots={"consent": "granted", "age": None, "trade": None}))
    u = und.understand(_ctx("opening"))
    assert u.pending_id == "consent" and u.nlu_result.intent == "any_input"
    assert StateWrite("session", "consent_response", "granted") in u.writes
    assert StateWrite("session", PROVENANCE_KEY, ["consent_response"]) in u.writes
    assert "pending: consent — हाँ/नहीं" in nlu.classify.call_args.args[0]


def test_select_resolves_latest_job_and_derives_job_pick():
    cache = MagicMock()
    cache.latest_entry.return_value = {"data": [{"item_id": "j1", "role": "Welder", "company": "Flipkart"}]}
    und, nlu = _u(DialogueActResult(acts=("select",), relation="answers_pending",
                                    slots={"consent": None, "age": None, "trade": None}, option=1))
    u = und.understand(_ctx("job_match", cache=cache))
    cache.latest_entry.assert_called_once_with("fetch_jobs")
    assert u.nlu_result.intent == "job_pick" and u.resolved.id == "j1"
    assert StateWrite("session", "selected_job_item_id", "j1") in u.writes
    assert "1. Welder · Flipkart" in nlu.classify.call_args.args[0]


def test_select_without_store_is_unresolved_and_not_job_pick():
    und, _ = _u(DialogueActResult(acts=("select",), relation="answers_pending", option=1,
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("job_match", cache=None))
    assert u.nlu_result.intent == "any_input" and u.unresolved.reason == "no_options"


def test_thank_you_after_submit_question_never_applies_or_ends():
    und, _ = _u(DialogueActResult(acts=("acknowledge",), relation="unclear",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 0}))
    assert u.pending_id == "submit_confirm" and u.nlu_result.intent == "any_input"


def test_close_after_application_terminates_with_full_confidence():
    und, _ = _u(DialogueActResult(acts=("close",), relation="answers_pending",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 1}))
    assert u.nlu_result.intent == "termination_intent" and u.nlu_result.confidence == 1.0


def test_off_track_trips_and_resets():
    und, _ = _u(DialogueActResult(acts=("other",), relation="unrelated",
                                  slots={"consent": None, "age": None, "trade": None}))
    u = und.understand(_ctx("job_match", session={"off_track_count": 1}))
    assert u.nlu_result.intent == "off_track" and u.off_track_tripped
    assert StateWrite("session", "off_track_count", 0) in u.writes


def test_fallback_is_any_input_with_no_writes():
    und, _ = _u(DialogueActResult.fallback(), reason="provider_error:timeout")
    u = und.understand(_ctx("apply_confirm", state={"applications_submitted": 1}))
    assert u.fallback_reason == "provider_error:timeout"
    assert u.nlu_result.intent == "any_input" and u.nlu_result.confidence == 0.0 and u.writes == []


def test_out_of_scope_consent_is_rejected_not_written():
    und, _ = _u(DialogueActResult(acts=("affirm",), relation="answers_other",
                                  slots={"consent": "granted", "age": None, "trade": "welder"}))
    u = und.understand(_ctx("job_match"))
    assert not any(w.key == "consent_response" for w in u.writes)
    assert StateWrite("session", "trade", "Welder") in u.writes
    assert [r.reason for r in u.rejected_slots] == ["not_pending"]


def test_understand_never_raises():
    nlu = MagicMock()
    nlu.classify.side_effect = RuntimeError("boom")
    und = TurnUnderstander(DialogueActConfig.from_config(CONFIG), WF, nlu)
    u = und.understand(_ctx("opening"))
    assert u.fallback_reason == "exception" and u.nlu_result.intent == "any_input"


def test_from_config_none_in_intent_mode():
    assert TurnUnderstander.from_config({}, WF, chat_provider=MagicMock()) is None
```

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement `history.py`**

```python
# agent_core/src/understanding/history.py
"""
agent_core/src/understanding/history.py

The ``recent_turns`` session list read by the NLU frame (NLU dialogue-acts
spec §6.9, §7.5). One entry per exchange. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any

RECENT_TURNS_KEY = "recent_turns"


def append_recent_turn(existing: Any, *, caller: str, bot: str, interrupted: bool,
                       history_turns: int) -> list[dict]:
    """Append one exchange and keep the last ``history_turns``.

    Args:
        existing: Current ``recent_turns`` value (anything non-list is reset).
        caller: What the caller said.
        bot: What the caller heard (full reply, or the emitted part if interrupted).
        interrupted: True when the bot's reply was cut off.
        history_turns: How many exchanges to keep; 0 keeps none.

    Returns:
        The new list.
    """
    if history_turns <= 0:
        return []
    entries = [e for e in existing if isinstance(e, dict)] if isinstance(existing, list) else []
    entries.append({"caller": caller or "", "bot": bot or "", "interrupted": bool(interrupted)})
    return entries[-history_turns:]
```

- [ ] **Step 4: Implement `understander.py`**

```python
# agent_core/src/understanding/understander.py
"""
agent_core/src/understanding/understander.py

TurnUnderstander: the dialogue_act replacement for NLUProcessor.process()
(NLU dialogue-acts spec §5–§6, §9, §10). Pure apart from the LLM call; the
orchestrator applies ``TurnUnderstanding.writes`` and ``signals``.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models import NLUResult
from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import DialogueActNLU, DialogueActNLUBase
from src.understanding.frame import FrameBuilder, FrameBuilderBase, offered_rows
from src.understanding.models import DialogueActResult, TurnUnderstanding
from src.understanding.pending import PendingResolver, PendingResolverBase
from src.understanding.postprocess import (accept_slots, derive_intent, gate_passes, next_off_track,
                                           normalise_slots, resolve_reference)
from src.understanding.slot_writer import plan_writes

logger = logging.getLogger(__name__)
_counters: dict[str, Any] = {}


def _counter(name: str, description: str):
    """Lazily create an OTel counter; a no-op meter when OTel is not configured."""
    if name not in _counters:
        from opentelemetry import metrics
        _counters[name] = metrics.get_meter("agent_core.understanding").create_counter(name, description=description)
    return _counters[name]


@dataclass(frozen=True)
class TurnContext:
    """Inputs for one understanding pass.

    Attributes:
        subagent_id: Subagent the caller is in (before routing).
        state: Merged routing state (``AgentCore._routing_state``).
        session: Raw session state (provenance, extras, counters).
        segments: This turn's utterances; earlier ones were interrupted.
        recent: ``recent_turns`` entries, oldest first.
        tool_cache: This turn's ``TurnToolCache``, or None.
    """

    subagent_id: str
    state: dict
    session: dict
    segments: list[str]
    recent: list[dict] = field(default_factory=list)
    tool_cache: Any | None = None


class TurnUnderstanderBase(ABC):
    """Interface for turn understanding."""

    @abstractmethod
    def understand(self, ctx: TurnContext) -> TurnUnderstanding:
        """Understand one caller turn. Never raises."""


class TurnUnderstander(TurnUnderstanderBase):
    """Composes pending resolution, frame, NLU call and post-processing.

    Args:
        cfg: Parsed dialogue-act config.
        workflow: Loaded workflow (pending declarations).
        nlu: The NLU call.
        frame: Frame renderer (default FrameBuilder).
        resolver: Pending resolver (default PendingResolver(workflow)).
    """

    def __init__(self, cfg: DialogueActConfig, workflow: Any, nlu: DialogueActNLUBase,
                 frame: FrameBuilderBase | None = None, resolver: PendingResolverBase | None = None) -> None:
        self._cfg = cfg
        self._nlu = nlu
        self._frame = frame or FrameBuilder()
        self._resolver = resolver or PendingResolver(workflow)

    @property
    def config(self) -> DialogueActConfig:
        """The parsed dialogue-act config."""
        return self._cfg

    @classmethod
    def from_config(cls, config: dict, workflow: Any, chat_provider: Any) -> "TurnUnderstander | None":
        """Build from the merged config; None in intent mode.

        Args:
            config: Merged agent_core config.
            workflow: Loaded workflow.
            chat_provider: Dedicated NLU provider (orchestrator builds it, §9.1).

        Returns:
            A TurnUnderstander, or None unless ``mode == "dialogue_act"``.
        """
        cfg = DialogueActConfig.from_config(config)
        if cfg is None:
            return None
        return cls(cfg, workflow, DialogueActNLU(cfg, chat_provider))

    def understand(self, ctx: TurnContext) -> TurnUnderstanding:
        """Understand one caller turn (spec §6). Never raises.

        Args:
            ctx: Turn inputs.

        Returns:
            The understanding; on any failure, a fallback with intent ``any_input``.
        """
        start = time.time()
        pending = None
        message = ""
        try:
            pending = self._resolver.resolve(ctx.subagent_id, ctx.state)
            rows: list[dict] = []
            if pending is not None and pending.options_from is not None and ctx.tool_cache is not None:
                rows = offered_rows(ctx.tool_cache.latest_entry(pending.options_from.tool))
            known = [(label, ctx.state.get(key)) for label, key in self._cfg.known_state_keys()]
            recent = ctx.recent[-self._cfg.history_turns:] if self._cfg.history_turns > 0 else []
            message = self._frame.build(step=ctx.subagent_id, pending=pending, rows=rows, known=known,
                                        recent=recent, segments=ctx.segments)
            dialogue, reason, _ = self._nlu.classify(message)
            if reason:
                return self._finish(self._fallback(dialogue, pending, reason), ctx, message, start)
            u = self._post(dialogue, pending, rows, ctx)
            return self._finish(u, ctx, message, start)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.error("nlu.understanding_error", extra={"operation": "turn_understander.understand",
                                                          "status": "failure", "error": type(e).__name__})
            return self._finish(self._fallback(DialogueActResult.fallback(), pending, "exception"),
                                ctx, message, start)

    @staticmethod
    def _fallback(dialogue: DialogueActResult, pending: Any, reason: str) -> TurnUnderstanding:
        return TurnUnderstanding(
            nlu_result=NLUResult(intent="any_input", entities={}, sentiment="neutral", confidence=0.0),
            dialogue=dialogue, pending_id=getattr(pending, "id", None), fallback_reason=reason)

    def _post(self, dialogue: DialogueActResult, pending: Any, rows: list[dict],
              ctx: TurnContext) -> TurnUnderstanding:
        cfg = self._cfg
        pid = getattr(pending, "id", None)
        slots, rejected = normalise_slots(dialogue.slots, cfg)
        accepted, not_pending = accept_slots(slots, cfg, pid, dialogue.acts)
        resolved, unresolved = resolve_reference(dialogue.option, pending, rows)
        gate_ok = gate_passes(cfg, pid, ctx.state)
        intent, rule = derive_intent(dialogue, pid, cfg, gate_ok=gate_ok, resolved=resolved is not None)
        count, tripped = next_off_track(int(ctx.session.get("off_track_count") or 0), dialogue.relation,
                                        cfg, is_fallback=False)
        if tripped and not (rule is not None and rule.gated):
            intent = cfg.off_track_intent
            count = 0   # recovery owns the next turn; start counting afresh
        writes, updates = plan_writes(accepted=accepted, cfg=cfg, state=ctx.state, session=ctx.session,
                                      pending=pending, resolved=resolved, extras=dialogue.extras,
                                      off_track_count=count)
        entities = {cfg.state_key(k): v for k, v in accepted.items()}
        if resolved is not None and getattr(pending, "resolves_to", None):
            entities[pending.resolves_to] = resolved.id
        gate_blocked = (not gate_ok) and any(
            r.gated and set(r.acts) <= set(dialogue.acts) for r in cfg.act_intents)
        u = TurnUnderstanding(
            nlu_result=NLUResult(intent=intent, entities=entities, sentiment="neutral", confidence=1.0),
            dialogue=dialogue, pending_id=pid, resolved=resolved, unresolved=unresolved,
            accepted_slots=accepted, updates=updates, rejected_slots=rejected + not_pending,
            writes=writes, signals=list(dialogue.signals), gate_blocked=gate_blocked,
            off_track_tripped=tripped and intent == cfg.off_track_intent)
        return u

    def _finish(self, u: TurnUnderstanding, ctx: TurnContext, message: str, start: float) -> TurnUnderstanding:
        u.latency_ms = int((time.time() - start) * 1000)
        d = u.dialogue
        extra = {
            "operation": "turn_understander.understand",
            "status": "fallback" if u.fallback_reason else "success",
            "latency_ms": u.latency_ms,
            "subagent_id": ctx.subagent_id,
            "pending_id": u.pending_id,
            "acts": list(d.acts) if d else [],
            "relation": d.relation if d else None,
            "topic": d.topic if d else None,
            "derived_intent": u.nlu_result.intent,
            "resolved": u.resolved is not None,
            "slot_keys_written": sorted({w.key for w in u.writes}),
            "slots_rejected": [f"{r.slot}:{r.reason}" for r in u.rejected_slots],
            "fallback_reason": u.fallback_reason,
        }
        logger.info("nlu.understanding", extra=extra)
        _counter("agent_core.nlu.turns_total", "Dialogue-act NLU turns by pending, first act, relation, intent").add(
            1, {"pending": u.pending_id or "none", "act": (d.acts[0] if d else "none"),
                "relation": (d.relation if d else "none"), "intent": u.nlu_result.intent,
                "fallback": u.fallback_reason or "none"})
        events = _counter("agent_core.nlu.events_total", "Dialogue-act NLU events")
        if u.gate_blocked:
            events.add(1, {"event": "termination_gate_blocked"})
        if u.off_track_tripped:
            events.add(1, {"event": "off_track_route"})
        if u.unresolved is not None:
            events.add(1, {"event": f"resolver_miss:{u.unresolved.reason}"})
        for r in u.rejected_slots:
            events.add(1, {"event": f"slot_rejected:{r.reason}"})
        if self._cfg.log_raw_response:
            logger.info("nlu.eval_case", extra={
                "operation": "turn_understander.eval_capture", "status": "success",
                "case": json.dumps({"step": ctx.subagent_id, "pending": u.pending_id, "frame": message,
                                    "output": d.__dict__ if d else None}, ensure_ascii=False, default=str)})
        return u
```

> Note: the off-track counter resets to 0 when it trips, so recovery is not re-triggered on every following turn (spec §6.6).

- [ ] **Step 5: Run tests**: `cd agent_core && uv run pytest tests/understanding -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/understanding/understander.py agent_core/src/understanding/history.py \
        agent_core/tests/understanding/test_understander.py agent_core/tests/understanding/test_history.py
git commit -m "feat(agent-core): TurnUnderstander composes the dialogue-act pipeline with logs and metrics"
```

---

### Task 17: Wire `dialogue_act` mode into the stream path

**Files:**
- Modify: `agent_core/src/models.py` (`TurnRecord.spoken`)
- Modify: `agent_core/src/orchestrator.py`: `__init__`, `stream_turn` body (`_stream_turn_impl`), `_persist_interrupted`, new helpers `_build_dialogue_act_provider`, `_turn_context`, `_apply_understanding_async`, `_write_signals_async`
- Test: `agent_core/tests/test_stream_turn_dialogue_act.py`

**Interfaces:**
- Consumes: `TurnUnderstander`, `TurnContext`, `render_caller_turn`, `append_recent_turn`, `RECENT_TURNS_KEY`, `TurnToolCache`
- Produces:
  - `AgentCore._understander: TurnUnderstanderBase | None`, `AgentCore._dialogue_cfg: DialogueActConfig | None`
  - `AgentCore._turn_context(bundle, subagent_id: str, segments: list[str], tool_cache) -> TurnContext`
  - `AgentCore._apply_understanding_async(session_id, user_id, bundle, understanding, raw_text) -> None`
  - `TurnRecord.spoken: list[str]`

- [ ] **Step 1: Failing tests**

```python
# agent_core/tests/test_stream_turn_dialogue_act.py
"""Stream-path wiring of dialogue_act mode."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import ContextBundle, NLUResult, TurnRecord
from src.understanding.history import RECENT_TURNS_KEY
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

pytestmark = pytest.mark.asyncio


def _understanding(intent="any_input", writes=(), signals=()):
    return TurnUnderstanding(
        nlu_result=NLUResult(intent=intent, entities={}, sentiment="neutral", confidence=1.0),
        dialogue=DialogueActResult(acts=("provide_info",), relation="answers_pending"),
        pending_id="age", writes=list(writes), signals=list(signals))


def _agent(understanding):
    agent = _make_agent_core()
    agent._understander = MagicMock()
    agent._understander.understand.return_value = understanding
    agent._dialogue_cfg = MagicMock(history_turns=2, signal_types={})
    agent._nlu_processor = MagicMock()

    async def mock_stream(*args, **kwargs):
        yield "ठीक है। "

    agent._llm.stream = mock_stream
    return agent


async def test_dialogue_act_mode_replaces_nlu_and_applies_writes():
    u = _understanding(writes=[StateWrite("session", "age", 25), StateWrite("session", "slot_provenance", ["age"])])
    agent = _agent(u)
    await _collect_events(agent, _make_turn_input())
    agent._nlu_processor.process.assert_not_called()
    ctx = agent._understander.understand.call_args.args[0]
    assert ctx.tool_cache is not None                       # cache built before NLU
    written = {(c.args[2], c.args[3]): c.args[4] for c in agent._async_memory.write.await_args_list}
    assert written[("session", "age")] == 25 and written[("session", "slot_provenance")] == ["age"]


async def test_caller_turn_reaches_the_prompt():
    agent = _agent(_understanding())
    await _collect_events(agent, _make_turn_input())
    kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert kwargs["caller_turn"].startswith("acts: provide_info")


async def test_recent_turn_appended_at_end_of_turn():
    agent = _agent(_understanding())
    await _collect_events(agent, _make_turn_input())
    writes = [c for c in agent._async_memory.write.await_args_list if c.args[3] == RECENT_TURNS_KEY]
    assert writes and writes[-1].args[4][-1]["bot"].startswith("ठीक है")


async def test_signals_written_as_signal_nodes():
    agent = _agent(_understanding(signals=["pay_disappointment"]))
    await _collect_events(agent, _make_turn_input())
    sig = [c for c in agent._async_memory.write.await_args_list if c.args[2] == "signal"]
    assert sig and sig[0].args[4]["type"] == "pay_disappointment"


async def test_intent_mode_untouched():
    agent = _make_agent_core()
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = NLUResult(intent="greeting", entities={}, sentiment="neutral",
                                                           confidence=0.9)

    async def mock_stream(*args, **kwargs):
        yield "Hi. "

    agent._llm.stream = mock_stream
    await _collect_events(agent, _make_turn_input())
    agent._nlu_processor.process.assert_called_once()
    assert agent._manager_agent.build_system_prompt.call_args.kwargs.get("caller_turn", "") == ""
    assert not any(c.args[3] == RECENT_TURNS_KEY for c in agent._async_memory.write.await_args_list)


async def test_interrupted_turn_persists_what_was_spoken():
    agent = _agent(_understanding())
    agent._async_memory.context_bundle.return_value = ContextBundle(session={RECENT_TURNS_KEY: []}, profile={})
    record = TurnRecord()
    record.spoken = ["बेंगलुरु में तीन नौकरियां हैं।"]
    record.segments = ["वेल्डर"]
    await agent._persist_interrupted("s1", "u1", record, exchanges=[], carry=None)
    by_key = {c.args[3]: c.args[4] for c in agent._async_memory.write.await_args_list}
    assert by_key["current_question"] == "बेंगलुरु में तीन नौकरियां हैं।"
    assert by_key[RECENT_TURNS_KEY][-1] == {"caller": "वेल्डर", "bot": "बेंगलुरु में तीन नौकरियां हैं।",
                                           "interrupted": True}


async def test_interrupted_turn_with_nothing_spoken_writes_no_question():
    agent = _agent(_understanding())
    record = TurnRecord()
    await agent._persist_interrupted("s1", "u1", record, exchanges=[], carry=None)
    assert not any(c.args[3] == "current_question" for c in agent._async_memory.write.await_args_list)
```

> If `TurnRecord()` has required constructor fields, build it the way `tests/test_turn_carryover_replay.py` does. If `_collect_events` needs the workflow's start subagent to have `pending`, the mock understander makes that irrelevant.

- [ ] **Step 2: Run to verify failure**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_dialogue_act.py -v`
Expected: FAIL (`AttributeError: _understander` is never consulted, or `TurnRecord` has no attribute `spoken`).

- [ ] **Step 3: `TurnRecord.spoken`**

In `src/models.py`, `TurnRecord`: add `spoken: list[str] = field(default_factory=list)` and document it: "Sentences emitted to the caller so far (for interrupted-turn persistence)."

In `_stream_turn_impl`'s `_stamp` closure, before `return ev`:

```python
            if isinstance(ev, SentenceEvent) and getattr(ev, "text", ""):
                record.spoken.append(ev.text)
```

- [ ] **Step 4: Construction**

In `AgentCore.__init__`, right after `self._nlu_processor = NLUProcessor(...)`:

```python
        # NLU dialogue-acts spec: opt-in mode; None keeps intent mode untouched.
        self._dialogue_cfg = DialogueActConfig.from_config(self._config)
        self._understander: TurnUnderstanderBase | None = None
        if self._dialogue_cfg is not None:
            self._understander = TurnUnderstander.from_config(
                self._config, self._workflow, chat_provider=self._build_dialogue_act_provider())
```

Add the imports:
- `from src.understanding.config import DialogueActConfig`
- `from src.understanding.understander import TurnContext, TurnUnderstander, TurnUnderstanderBase`
- `from src.understanding.caller_turn import render_caller_turn`
- `from src.understanding.history import RECENT_TURNS_KEY, append_recent_turn`

If `build_chat_provider` is not already imported in `orchestrator.py`, add `from src.chat_provider import build_chat_provider`.

Add the helpers, next to `_build_helper_provider`:

```python
    def _build_dialogue_act_provider(self) -> ChatProviderBase:
        """Dedicated NLU provider: own timeout, no retry after a timeout, no SDK retries (spec §9.1).

        Always a new instance, even when the model matches the main agent's, so
        the main LLM's timeout/retry policy is unaffected.

        Returns:
            A ChatProviderBase for the dialogue-act NLU call.
        """
        agent_cfg = dict(self._config.get("agent", {}) or {})
        nlu = (self._config.get("preprocessing", {}) or {}).get("nlu_processor", {}) or {}
        return build_chat_provider({
            **agent_cfg,
            "provider": nlu.get("provider") or agent_cfg.get("provider", "anthropic"),
            "primary_model": nlu.get("model") or agent_cfg.get("primary_model", ""),
            "timeout_ms": self._dialogue_cfg.timeout_ms,
            "retry_attempts": self._dialogue_cfg.retry_attempts,
            "sdk_max_retries": 0,
            "retry_on_timeout": False,
        })

    def _turn_context(self, bundle, subagent_id: str, segments: list[str], tool_cache) -> TurnContext:
        """Build the understanding inputs from the bundle (both paths).

        Args:
            bundle: The turn's ContextBundle (after bootstrap).
            subagent_id: Current subagent, before routing.
            segments: Utterances this turn answers.
            tool_cache: This turn's TurnToolCache.

        Returns:
            TurnContext.
        """
        session = dict(bundle.session or {})
        return TurnContext(subagent_id=subagent_id, state=self._routing_state(bundle), session=session,
                           segments=[s for s in segments if s], recent=list(session.get(RECENT_TURNS_KEY) or []),
                           tool_cache=tool_cache)

    async def _apply_understanding_async(self, session_id: str, user_id: str, bundle,
                                         understanding, raw_text: str) -> None:
        """Apply an understanding's writes and signals (stream path).

        Mirrors the legacy entity loop's bundle updates so routing and the
        prompt see the values on the same turn.

        Args:
            session_id: Session id.
            user_id: User id.
            bundle: The turn's ContextBundle (mutated).
            understanding: This turn's TurnUnderstanding.
            raw_text: Caller text, for the Signal node payload.
        """
        for w in understanding.writes:
            await self._async_memory.write(session_id, user_id, w.scope, w.key, w.value)
            bundle.session[w.key] = w.value
            if w.scope == "persistent":
                bundle.profile[w.key] = w.value
        turn = str(int(bundle.session.get("turn_count", 0) or 0))
        for name in understanding.signals:
            try:
                await self._async_memory.write(session_id, user_id, "signal", "signal", {
                    "type": self._dialogue_cfg.signal_types.get(name, name), "turn": turn,
                    "raw": raw_text, "journey_id": session_id})
            except Exception as e:  # noqa: BLE001 — a signal never breaks the turn
                logger.warning("orchestrator.signal_write_failed", extra={
                    "operation": "orchestrator.apply_understanding", "status": "failure",
                    "session_id": session_id, "error": type(e).__name__})
```

- [ ] **Step 5: Stream body edits** (in `_stream_turn_impl`)

1. Near the top, with the other per-turn locals (`nlu_result = NLUResult(...)` etc.), add `understanding = None` and `tool_cache = None`.
2. Right after `await self._run_session_bootstrap_async(bundle, session_id, user_id)`:

```python
            if self._understander is not None:
                # Built before NLU so the frame can read stored results (spec §8);
                # reused at prompt assembly and in the tool loop.
                tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)
```

3. Wrap the existing `[STEP 4+5]` NLU/LN dispatch (`if _ln_enabled_s: ... asyncio.gather(...) else: ...`) so dialogue_act mode runs first:

```python
            if self._understander is not None:
                _ctx = self._turn_context(bundle, current_subagent_id,
                                          list(record.segments) or [turn_input.user_message], tool_cache)
                if _ln_enabled_s:
                    (normalised_input, turn_language), understanding = await asyncio.gather(
                        asyncio.to_thread(self._language_normaliser.normalise, turn_input.user_message, self._config),
                        asyncio.to_thread(self._understander.understand, _ctx),
                    )
                else:
                    normalised_input, turn_language = turn_input.user_message, ""
                    understanding = await asyncio.to_thread(self._understander.understand, _ctx)
                early_nlu_result = understanding.nlu_result
            elif _ln_enabled_s:
                ...existing gather, unchanged...
            else:
                ...existing branch, unchanged...
```

> Check first: `record.segments` must already be set by `_fold_carryover` at this point. Confirm the fold call precedes the `[STEP 4+5]` block. If it doesn't, compute the segments with the same fold result the block already uses for `turn_input.user_message`.

4. In the consent-replay branch (`early_nlu_result = await asyncio.to_thread(self._nlu_processor.process, pending_msg, ...)`), make it conditional:

```python
                        if self._understander is not None:
                            understanding = await asyncio.to_thread(
                                self._understander.understand,
                                self._turn_context(bundle, current_subagent_id, [pending_msg], tool_cache))
                            early_nlu_result = understanding.nlu_result
                        else:
                            early_nlu_result = await asyncio.to_thread(self._nlu_processor.process, ...)  # unchanged
```

5. Replace the legacy `# Write entities` loop (`for entity_key, entity_val in (nlu_result.entities or {}).items(): ...`) with:

```python
            if understanding is not None:
                await self._apply_understanding_async(session_id, user_id, bundle, understanding,
                                                      turn_input.user_message)
            else:
                ...existing entity loop, unchanged...
```

6. At prompt assembly, change `tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)` to:

```python
            if tool_cache is None:
                tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)
```

and add `caller_turn=render_caller_turn(understanding),` to the `self._manager_agent.build_system_prompt(...)` call. `render_caller_turn(None)` returns `""`.

7. At Step 11, after the `asyncio.create_task(... "current_question", stream_cq_value)` call:

```python
            if self._understander is not None:
                asyncio.create_task(self._async_memory.write(
                    session_id, user_id, "session", RECENT_TURNS_KEY,
                    append_recent_turn(bundle.session.get(RECENT_TURNS_KEY),
                                       caller=" ".join(record.segments or [turn_input.user_message]),
                                       bot=full_response_text, interrupted=False,
                                       history_turns=self._dialogue_cfg.history_turns)))
```

- [ ] **Step 6: Interrupted-turn writes**

In `_persist_interrupted`:
- Change `if write_exchanges or append_carry:` (the re-read guard) to `if write_exchanges or append_carry or record.spoken:`.
- Then, after the carry-over block, add:

```python
            if record.spoken:
                heard = " ".join(s.strip() for s in record.spoken if s.strip())
                await self._async_memory.write(
                    session_id, user_id, "session", "current_question",
                    self._sanitize_current_question(prev=session_state.get("current_question", "") or "",
                                                    new=heard, session_id=session_id))
                if self._understander is not None:
                    await self._async_memory.write(
                        session_id, user_id, "session", RECENT_TURNS_KEY,
                        append_recent_turn(session_state.get(RECENT_TURNS_KEY),
                                           caller=" ".join(record.segments), bot=heard, interrupted=True,
                                           history_turns=self._dialogue_cfg.history_turns))
```

Update the docstring: "Also writes `current_question` from the sentences actually emitted (`record.spoken`), and in dialogue_act mode a `recent_turns` entry marked interrupted (NLU dialogue-acts spec §6.9)."

> Note: the `current_question` write applies in **both** modes. It fixes a stale question after interruption; spec §6.9 says "for both modes".

- [ ] **Step 7: Run tests**

Run: `cd agent_core && uv run pytest tests/test_stream_turn_dialogue_act.py tests/test_stream_turn.py tests/test_stream_turn_lifecycle.py tests/test_turn_carryover_replay.py tests/test_turn_assembler_interrupt.py -q`
Expected: all PASS. If an existing interrupted-turn test now sees an extra `current_question` write, check it was caused by `record.spoken` being non-empty in that test, and update only its write-count assertion.

- [ ] **Step 8: Commit**

```bash
git add agent_core/src/models.py agent_core/src/orchestrator.py agent_core/tests/test_stream_turn_dialogue_act.py
git commit -m "feat(agent-core): dialogue_act mode on the stream path, with interrupted-turn question fix"
```

---

### Task 18: Wire `dialogue_act` mode into the sync path, with parity

**Files:**
- Modify: `agent_core/src/orchestrator.py` (`_process_turn_inner`, new `_apply_understanding_sync`)
- Test: `agent_core/tests/test_turn_path_identity_parity.py` (append)

**Interfaces:**
- Consumes: Task 17 helpers (`_turn_context`, `RECENT_TURNS_KEY`, `append_recent_turn`, `render_caller_turn`)
- Produces: `AgentCore._apply_understanding_sync(session_id, user_id, bundle, understanding, raw_text) -> None`

- [ ] **Step 1: Failing parity test**

Append to `agent_core/tests/test_turn_path_identity_parity.py`:

```python
from src.understanding.history import RECENT_TURNS_KEY  # noqa: E402
from src.understanding.models import DialogueActResult, StateWrite, TurnUnderstanding  # noqa: E402

_DA_U = TurnUnderstanding(
    nlu_result=NLUResult(intent="any_input", entities={"age": 25}, sentiment="neutral", confidence=1.0),
    dialogue=DialogueActResult(acts=("provide_info",), relation="answers_pending"),
    pending_id="age", writes=[StateWrite("session", "age", 25), StateWrite("session", "slot_provenance", ["age"])])


def _da(agent):
    agent._understander = MagicMock()
    agent._understander.understand.return_value = _DA_U
    agent._dialogue_cfg = MagicMock(history_turns=2, signal_types={})
    return agent


async def test_both_paths_apply_identical_understanding():
    # opening_phrase_emitted: past the sync canned-greeting gate, so turn 1 reaches NLU.
    sync_agent = _da(_make_agent(session_data={"current_subagent_id": "start", "opening_phrase_emitted": True}))
    sync_agent.process_turn(_turn_input())
    sync_writes = {(c.args[2], c.args[3]): c.args[4] for c in sync_agent._memory.write.call_args_list}
    sync_ctx = sync_agent._understander.understand.call_args.args[0]
    sync_prompt = sync_agent._manager_agent.build_system_prompt.call_args.kwargs["caller_turn"]

    stream_agent = _da(_make_agent_core())

    async def mock_stream(*args, **kwargs):
        yield "ok. "

    stream_agent._llm.stream = mock_stream
    await _collect_events(stream_agent, _make_turn_input())
    stream_writes = {(c.args[2], c.args[3]): c.args[4] for c in stream_agent._async_memory.write.await_args_list}
    stream_ctx = stream_agent._understander.understand.call_args.args[0]
    stream_prompt = stream_agent._manager_agent.build_system_prompt.call_args.kwargs["caller_turn"]

    for key in (("session", "age"), ("session", "slot_provenance")):
        assert sync_writes[key] == stream_writes[key]
    assert ("session", RECENT_TURNS_KEY) in sync_writes and ("session", RECENT_TURNS_KEY) in stream_writes
    assert sync_ctx.segments == stream_ctx.segments            # raw caller text on both paths
    assert sync_ctx.tool_cache is not None and stream_ctx.tool_cache is not None
    assert sync_prompt == stream_prompt != ""
    sync_agent._nlu_processor.process.assert_not_called()
```

> If `_make_agent` does not mock `_nlu_processor` as a MagicMock, set `sync_agent._nlu_processor = MagicMock()` inside `_da`. If the sync `_make_agent` default session lacks a subagent the workflow mock knows, pass `session_data` as the existing parity tests do. Make sure both turn inputs carry the same `user_message`; if the two factories differ, set it explicitly on both.

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/test_turn_path_identity_parity.py::test_both_paths_apply_identical_understanding -v` → FAIL (sync path still calls `_nlu_processor.process`).

- [ ] **Step 3: Implement**

Add the sync counterpart next to `_apply_understanding_async`:

```python
    def _apply_understanding_sync(self, session_id: str, user_id: str, bundle,
                                  understanding, raw_text: str) -> None:
        """Apply an understanding's writes and signals (sync path); see the async twin.

        Args:
            session_id: Session id.
            user_id: User id.
            bundle: The turn's ContextBundle (mutated).
            understanding: This turn's TurnUnderstanding.
            raw_text: Caller text, for the Signal node payload.
        """
        for w in understanding.writes:
            self._write_memory_sync(session_id, user_id, w.scope, w.key, w.value)
            bundle.session[w.key] = w.value
            if w.scope == "persistent":
                bundle.profile[w.key] = w.value
        turn = str(int(bundle.session.get("turn_count", 0) or 0))
        for name in understanding.signals:
            try:
                self._write_memory_sync(session_id, user_id, "signal", "signal", {
                    "type": self._dialogue_cfg.signal_types.get(name, name), "turn": turn,
                    "raw": raw_text, "journey_id": session_id})
            except Exception as e:  # noqa: BLE001 — a signal never breaks the turn
                logger.warning("orchestrator.signal_write_failed", extra={
                    "operation": "orchestrator.apply_understanding", "status": "failure",
                    "session_id": session_id, "error": type(e).__name__})
```

In `_process_turn_inner`:
1. Add `understanding = None` and `tool_cache = None` near the start. Right after `self._run_session_bootstrap_sync(bundle, session_id, user_id)`, add `if self._understander is not None: tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)`.
2. Around the `[STEP 5]` block:

```python
        if self._understander is not None:
            understanding = self._understander.understand(
                self._turn_context(bundle, current_subagent_id, [turn_input.user_message], tool_cache))
            nlu_result = understanding.nlu_result
        else:
            nlu_result = self._nlu_processor.process(...)   # existing call, unchanged
```

   Keep the `[STEP 5]` log lines. In dialogue_act mode the "→ LLM call" line can stay; it logs the model of the legacy provider, which is harmless. Optionally guard it with `if understanding is None`.
3. Wrap the legacy `for entity_key, entity_val in (nlu_result.entities or {}).items():` loop **and** the `signal_intents` block that follows it:

```python
        if understanding is not None:
            self._apply_understanding_sync(session_id, user_id, bundle, understanding, turn_input.user_message)
        else:
            ...existing entity loop and signal_intents block, unchanged...
```

4. At prompt assembly: change `tool_cache = TurnToolCache(...)` to `if tool_cache is None: tool_cache = TurnToolCache(...)`. If the cache is constructed after `build_system_prompt` on this path, keep the order: only the assignment becomes conditional. Add `caller_turn=render_caller_turn(understanding),` to `build_system_prompt(...)`.
5. After `bundle.session["current_question"] = cq_value` (Step 11):

```python
        if self._understander is not None:
            entries = append_recent_turn(bundle.session.get(RECENT_TURNS_KEY), caller=turn_input.user_message,
                                         bot=final_text, interrupted=False,
                                         history_turns=self._dialogue_cfg.history_turns)
            self._write_memory_sync(session_id, user_id, "session", RECENT_TURNS_KEY, entries)
            bundle.session[RECENT_TURNS_KEY] = entries
```

> Sync passes the **raw** `turn_input.user_message` to understanding (not `normalised_input`), for parity with stream (spec §8).

- [ ] **Step 4: Run tests**

Run: `cd agent_core && uv run pytest tests/test_turn_path_identity_parity.py tests/test_orchestrator.py tests/test_nlu_processor.py -q`
Expected: all PASS. `test_nlu_processor.py` must pass unchanged.

- [ ] **Step 5: Full agent_core suite and coverage**

Run: `cd agent_core && uv run pytest --cov=src --cov-report=term-missing -q`
Expected: all PASS; total coverage ≥ 70%; `src/understanding/*` ≥ 90%.

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_turn_path_identity_parity.py
git commit -m "feat(agent-core): dialogue_act mode on the sync path, at parity with stream"
```

---

### Task 19: Replay eval harness

**Files:**
- Create: `agent_core/eval/__init__.py` (empty), `agent_core/eval/nlu/__init__.py` (empty)
- Create: `agent_core/eval/nlu/cases.py`, `offline.py`, `adapters.py`, `score.py`, `run.py`
- Create: `agent_core/eval/nlu/cases/synthetic.jsonl`
- Test: `agent_core/tests/eval/__init__.py` (empty), `agent_core/tests/eval/test_nlu_eval.py`

**Interfaces:**
- Consumes: `TurnUnderstander`, `TurnContext`, `NLUProcessor.process`, `AgentWorkflowLoader`, `ToolRegistry`, `ActionGatewayBase`, `MergedConfig`
- Produces (CLI): `cd agent_core && uv run python -m eval.nlu.run --config ../dev-kit/configs/blue-dots --mode intent|dialogue_act --cases eval/nlu/cases/synthetic.jsonl --repeat 3 --out report.json`, plus `--compare a.json b.json` for the §11.5 gate
- Produces (library): `EvalCase`, `load_cases(path) -> list[EvalCase]`, `Prediction`, `predict_dialogue_act(case, understander) -> Prediction`, `predict_intent(case, nlu, workflow, entity_map, routed, resolves_to) -> Prediction`, `score(cases, preds: dict[str, list[Prediction]], mode) -> dict`, `gate(intent_report, da_report) -> list[str]`

- [ ] **Step 1: Failing tests (pure parts)**

```python
# agent_core/tests/eval/test_nlu_eval.py
"""Tests for the NLU replay harness (no real LLM calls)."""
import json
from unittest.mock import MagicMock

from eval.nlu.adapters import Prediction, predict_dialogue_act, predict_intent
from eval.nlu.cases import EvalCase, load_cases
from eval.nlu.score import gate, score
from src.models import NLUResult
from src.understanding.models import DialogueActResult, ResolvedReference, TurnUnderstanding

CASE = {"id": "ack-01", "tags": ["acknowledge"], "step": "apply_confirm",
        "state": {"applications_submitted": 1}, "caller_now": ["ठीक है धन्यवाद"],
        "recent": [{"caller": "हाँ", "bot": "आवेदन भेज दिया है।", "interrupted": False}],
        "expect": {"acts": ["acknowledge"], "intent": "any_input", "terminate": False}}


def test_load_cases_roundtrip_and_validation(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps(CASE, ensure_ascii=False) + "\n\n", encoding="utf-8")
    cases = load_cases(p)
    assert cases[0].id == "ack-01" and cases[0].caller_now == ["ठीक है धन्यवाद"]
    p.write_text('{"id": "x"}\n', encoding="utf-8")
    try:
        load_cases(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "x" in str(e)


def test_predict_dialogue_act_uses_offered_rows_and_reports_resolution():
    case = EvalCase.from_dict({**CASE, "id": "sel", "step": "job_match", "offered_tool": "fetch_jobs",
                               "offered": [{"item_id": "j1", "role": "Welder"}],
                               "expect": {"intent": "job_pick", "option_id": "j1", "terminate": False}})
    und = MagicMock()
    und.understand.return_value = TurnUnderstanding(
        nlu_result=NLUResult("job_pick", {}, "neutral", 1.0),
        dialogue=DialogueActResult(acts=("select",), relation="answers_pending"), pending_id="select_job",
        resolved=ResolvedReference(1, "j1", "Welder", "item_id"), latency_ms=900)
    pred = predict_dialogue_act(case, und)
    ctx = und.understand.call_args.args[0]
    assert ctx.tool_cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "j1", "role": "Welder"}]
    assert ctx.tool_cache.latest_entry("other") is None
    assert (pred.intent, pred.option_id, pred.terminate, pred.acts) == ("job_pick", "j1", False, ("select",))


def test_predict_intent_maps_unrouted_intents_and_termination():
    case = EvalCase.from_dict(CASE)
    nlu = MagicMock()
    nlu.process.return_value = NLUResult("termination_intent", {"trade": "welder"}, "neutral", 0.95)
    wf = MagicMock(nlu_intent_set={"apply_confirm": ["any_input", "termination_intent"]})
    pred = predict_intent(case, nlu, wf, entity_map={}, routed={"termination_intent", "apply_now"},
                          resolves_to="selected_job_item_id")
    assert pred.intent == "termination_intent" and pred.terminate is True and pred.slots == {"trade": "welder"}
    kwargs = nlu.process.call_args.kwargs
    assert kwargs["current_question"] == "आवेदन भेज दिया है।" and kwargs["normalised_input"] == "ठीक है धन्यवाद"
    nlu.process.return_value = NLUResult("profile_answer", {}, "neutral", 0.9)
    assert predict_intent(case, nlu, wf, {}, {"apply_now"}, "x").intent == "any_input"


def _p(intent, terminate=False, ms=1000, acts=("acknowledge",), pending="closing_offer", slots=None):
    return Prediction(intent=intent, terminate=terminate, slots=slots or {}, option_id=None, acts=acts,
                      relation="answers_pending", topic=None, pending=pending, latency_ms=ms, fallback=None)


def test_score_counts_termination_false_positives_and_tags():
    cases = [EvalCase.from_dict(CASE)]
    rep = score(cases, {"ack-01": [_p("termination_intent", terminate=True), _p("any_input")]}, mode="intent")
    assert rep["fields"]["intent"]["accuracy"] == 0.5
    assert rep["termination_false_positives"] == 1
    assert rep["tags"]["acknowledge"]["intent_accuracy"] == 0.5
    assert rep["agreement"] == 0.0
    assert rep["latency_ms"]["p50"] == 1000


def test_slot_accuracy_is_canonical():
    case = EvalCase.from_dict({**CASE, "expect": {"intent": "any_input", "slots": {"age": 22, "trade": "Welder"}}})
    rep = score([case], {"ack-01": [_p("any_input", slots={"age": "22", "trade": "welder "})]}, mode="intent")
    assert rep["fields"]["slots"]["accuracy"] == 1.0


def test_gate_reports_each_failed_condition():
    base = {"fields": {"intent": {"accuracy": 0.8}, "slots": {"accuracy": 0.9}},
            "tags": {"consent": {"intent_accuracy": 1.0}, "age": {"intent_accuracy": 1.0},
                     "termination": {"intent_accuracy": 1.0}, "acknowledge": {"termination_fp": 0}},
            "latency_ms": {"p50": 1000}}
    worse = json.loads(json.dumps(base))
    worse["fields"]["intent"]["accuracy"] = 0.7
    worse["tags"]["acknowledge"]["termination_fp"] = 1
    worse["latency_ms"]["p50"] = 1100
    fails = gate(base, worse)
    assert len(fails) == 3 and any("p50" in f for f in fails)
    assert gate(base, base) == []
```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/eval -v` → FAIL (`ModuleNotFoundError: eval`).

> If `eval` is not importable from tests, add `pythonpath = ["."]` under `[tool.pytest.ini_options]` in `agent_core/pyproject.toml`, unless `src` is already importable that way, in which case `eval` will be too.

- [ ] **Step 3: Implement `cases.py` and `offline.py`**

```python
# agent_core/eval/nlu/cases.py
"""NLU replay eval cases (NLU dialogue-acts spec §11.2). Structured inputs, so one case runs in both modes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REQUIRED = ("id", "step", "caller_now", "expect")


@dataclass(frozen=True)
class EvalCase:
    """One labelled turn.

    Attributes:
        id: Unique case id.
        tags: Tags for per-tag reporting (e.g. acknowledge, consent, age, termination).
        step: Subagent the caller is in.
        state: Merged routing state (drives pending resolution and the gate).
        session: Raw session (defaults to ``state``).
        caller_now: This turn's utterances (all but last interrupted).
        recent: Prior exchanges ``{caller, bot, interrupted}``.
        offered_tool: Tool whose stored result lists ``offered`` rows.
        offered: Rows on offer this turn.
        expect: Expected ``intent`` (required), ``terminate``, ``slots``, ``option_id``,
            ``acts``, ``relation``, ``topic``, ``pending``.
    """

    id: str
    step: str
    caller_now: list[str]
    expect: dict
    tags: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    session: dict | None = None
    recent: list[dict] = field(default_factory=list)
    offered_tool: str | None = None
    offered: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        """Build from a JSON object; raises ValueError naming the case on missing keys."""
        missing = [k for k in _REQUIRED if k not in d]
        if missing or "intent" not in (d.get("expect") or {}):
            raise ValueError(f"case {d.get('id', '?')}: missing {missing or ['expect.intent']}")
        return cls(id=str(d["id"]), step=d["step"], caller_now=list(d["caller_now"]), expect=dict(d["expect"]),
                   tags=list(d.get("tags") or []), state=dict(d.get("state") or {}), session=d.get("session"),
                   recent=list(d.get("recent") or []), offered_tool=d.get("offered_tool"),
                   offered=list(d.get("offered") or []))


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load a JSONL file of cases (blank lines ignored).

    Args:
        path: JSONL path.

    Returns:
        Cases in file order.

    Raises:
        ValueError: On a malformed case.
    """
    out: list[EvalCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(EvalCase.from_dict(json.loads(line)))
    return out
```

```python
# agent_core/eval/nlu/offline.py
"""Offline stand-ins for the eval runner: no Action Gateway, no Memory Layer."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.interfaces.action_gateway import ActionGatewayBase
from src.models import ToolCall, ToolResult


class OfflineGateway(ActionGatewayBase):
    """Lists connector names as tools so the workflow loader validates; never executes.

    Args:
        config: Merged agent_core config.
    """

    def __init__(self, config: dict) -> None:
        connectors = config.get("connectors") or {}
        self._tools = [{"name": c["name"], "description": c.get("description", ""),
                        "input_schema": {"type": "object", "properties": {}}}
                       for group in ("read", "write", "identity") for c in (connectors.get(group) or [])
                       if isinstance(c, dict) and c.get("name")]

    def list_available_tools(self) -> list[dict]:
        """Return connector names as tool definitions."""
        return list(self._tools)

    def execute(self, tool_call: ToolCall, session_id: str, user_id: str = "",
                session_values: dict | None = None) -> ToolResult:
        """Always fail: the eval never calls tools."""
        return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=tool_call.tool_name, result={},
                          success=False, error="eval: offline gateway")


class StaticToolCache:
    """Minimal TurnToolCache stand-in: ``latest_entry`` over fixed rows per tool."""

    def __init__(self, rows_by_tool: dict[str, list[dict]]) -> None:
        self._rows = rows_by_tool

    def latest_entry(self, tool: str) -> dict | None:
        """Return ``{"data": rows}`` for a tool with rows, else None."""
        rows = self._rows.get(tool)
        return {"data": list(rows)} if rows else None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_merged_config(domain_dir: str | Path, mode: str) -> dict:
    """agent_core/config/dpg.yaml deep-merged with ``<domain_dir>/agent_core.yaml``; mode forced.

    Args:
        domain_dir: Directory holding the domain's agent_core.yaml.
        mode: ``intent`` or ``dialogue_act``.

    Returns:
        Merged config dict.
    """
    root = Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((root / "config" / "dpg.yaml").read_text(encoding="utf-8")) or {}
    domain = yaml.safe_load((Path(domain_dir) / "agent_core.yaml").read_text(encoding="utf-8")) or {}
    merged = _deep_merge(dpg, domain)
    merged.setdefault("preprocessing", {}).setdefault("nlu_processor", {})["mode"] = mode
    return merged
```

- [ ] **Step 4: Implement `adapters.py`**

```python
# agent_core/eval/nlu/adapters.py
"""Run one EvalCase through either NLU mode and return a comparable Prediction."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from eval.nlu.cases import EvalCase
from eval.nlu.offline import StaticToolCache
from src.understanding.understander import TurnContext


@dataclass(frozen=True)
class Prediction:
    """What one mode concluded for one case (one repeat)."""

    intent: str
    terminate: bool
    slots: dict
    option_id: str | None
    acts: tuple[str, ...]
    relation: str | None
    topic: str | None
    pending: str | None
    latency_ms: int
    fallback: str | None


def predict_dialogue_act(case: EvalCase, understander: Any) -> Prediction:
    """Run a case through TurnUnderstander.

    Args:
        case: The case.
        understander: A TurnUnderstander (real provider in the CLI).

    Returns:
        Prediction (terminate = derived termination_intent; gate already applied).
    """
    cache = StaticToolCache({case.offered_tool: case.offered}) if case.offered_tool else None
    u = understander.understand(TurnContext(
        subagent_id=case.step, state=dict(case.state), session=dict(case.session or case.state),
        segments=list(case.caller_now), recent=list(case.recent), tool_cache=cache))
    d = u.dialogue
    return Prediction(intent=u.nlu_result.intent, terminate=u.nlu_result.intent == "termination_intent",
                      slots=dict(u.nlu_result.entities), option_id=u.resolved.id if u.resolved else None,
                      acts=tuple(d.acts) if d else (), relation=d.relation if d else None,
                      topic=d.topic if d else None, pending=u.pending_id, latency_ms=u.latency_ms,
                      fallback=u.fallback_reason)


def predict_intent(case: EvalCase, nlu: Any, workflow: Any, entity_map: dict, routed: set[str],
                   resolves_to: str | None) -> Prediction:
    """Run a case through today's NLUProcessor, the way the stream path calls it.

    Intents no routing rule uses (e.g. profile_answer) count as ``any_input``,
    so both modes are scored on the intents routing actually sees.

    Args:
        case: The case.
        nlu: NLUProcessor.
        workflow: Loaded intent-mode workflow (for per-subagent intent lists).
        entity_map: entity_to_profile_field.
        routed: Intents used by routing rules.
        resolves_to: State key for the selected option (compared to ``expect.option_id``).

    Returns:
        Prediction (terminate mirrors the short-circuit: termination_intent at ≥0.7).
    """
    start = time.time()
    result = nlu.process(
        normalised_input=" ".join(case.caller_now),
        current_question=(case.recent[-1].get("bot", "") if case.recent else ""),
        current_subagent_id=case.step,
        allowed_intents=workflow.nlu_intent_set.get(case.step, []),
        existing_profile_keys=[k for k, v in case.state.items() if v not in (None, "", 0)],
        previous_user_state=None,
    )
    slots = {entity_map.get(k, k): v for k, v in (result.entities or {}).items()}
    intent = result.intent if result.intent in routed else "any_input"
    return Prediction(intent=intent, terminate=result.intent == "termination_intent" and result.confidence >= 0.7,
                      slots=slots, option_id=str(slots.get(resolves_to)) if resolves_to and slots.get(resolves_to) else None,
                      acts=(), relation=None, topic=None, pending=None,
                      latency_ms=int((time.time() - start) * 1000), fallback=None)
```

- [ ] **Step 5: Implement `score.py`**

```python
# agent_core/eval/nlu/score.py
"""Scoring and the §11.5 switch-over gate for the NLU replay harness."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from eval.nlu.adapters import Prediction
from eval.nlu.cases import EvalCase

_GATED_TAGS = ("consent", "age", "termination")


def _canon(v) -> str:
    return str(v).strip().casefold()


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def score(cases: list[EvalCase], preds: dict[str, list[Prediction]], mode: str) -> dict:
    """Score predictions (one list per case, one entry per repeat).

    Args:
        cases: The cases.
        preds: case id → predictions.
        mode: ``intent`` or ``dialogue_act`` (acts/relation/topic scored only for the latter).

    Returns:
        Report dict: fields, tags, confusion, termination_false_positives,
        latency_ms {p50, p95}, fallback_rate, agreement, precision_by_act_pending.
    """
    fields: dict[str, list[int]] = defaultdict(list)
    tags: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    prec: dict[str, list[int]] = defaultdict(list)
    latencies: list[int] = []
    fallbacks = 0
    term_fp = 0
    agree = []
    total = 0
    for case in cases:
        runs = preds.get(case.id) or []
        agree.append(int(len({p.intent for p in runs}) <= 1 and bool(runs)))
        exp = case.expect
        for p in runs:
            total += 1
            latencies.append(p.latency_ms)
            fallbacks += int(p.fallback is not None)
            ok = int(p.intent == exp["intent"])
            fields["intent"].append(ok)
            confusion[exp["intent"]][p.intent] += 1
            fp = int(not exp.get("terminate", False) and p.terminate)
            term_fp += fp
            if "terminate" in exp:
                fields["terminate"].append(int(p.terminate == exp["terminate"]))
            for k, v in (exp.get("slots") or {}).items():
                fields["slots"].append(int(k in p.slots and _canon(p.slots[k]) == _canon(v)))
            if "option_id" in exp:
                fields["option"].append(int(p.option_id == exp["option_id"]))
            if mode == "dialogue_act":
                for key, attr in (("acts", "acts"), ("relation", "relation"), ("topic", "topic"),
                                  ("pending", "pending")):
                    if key in exp:
                        got = list(p.acts) if key == "acts" else getattr(p, attr)
                        fields[key].append(int(got == exp[key]))
                for act in p.acts:
                    prec[f"{act}|{p.pending or 'none'}"].append(ok)
            for t in case.tags:
                tags[t]["intent"].append(ok)
                tags[t]["fp"].append(fp)
    return {
        "mode": mode, "cases": len(cases), "runs": total,
        "fields": {k: {"accuracy": round(sum(v) / len(v), 4), "n": len(v)} for k, v in fields.items()},
        "tags": {t: {"intent_accuracy": round(sum(d["intent"]) / len(d["intent"]), 4) if d["intent"] else None,
                     "termination_fp": sum(d["fp"]), "n": len(d["intent"])} for t, d in tags.items()},
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "termination_false_positives": term_fp,
        "latency_ms": {"p50": int(median(latencies)) if latencies else 0, "p95": _pct(latencies, 0.95)},
        "fallback_rate": round(fallbacks / total, 4) if total else 0.0,
        "agreement": round(sum(agree) / len(agree), 4) if agree else 0.0,
        "precision_by_act_pending": {k: {"precision": round(sum(v) / len(v), 4), "n": len(v)}
                                     for k, v in prec.items()},
    }


def gate(intent_report: dict, da_report: dict) -> list[str]:
    """§11.5 switch-over gate; returns one message per failed condition (empty = pass).

    Args:
        intent_report: ``score`` output for intent mode.
        da_report: ``score`` output for dialogue_act mode on the same cases.

    Returns:
        Failure messages.
    """
    fails: list[str] = []

    def acc(rep: dict, field: str) -> float:
        return (rep.get("fields", {}).get(field) or {}).get("accuracy", 0.0)

    for field in ("intent", "slots"):
        if acc(da_report, field) < acc(intent_report, field):
            fails.append(f"{field} accuracy {acc(da_report, field)} < intent mode {acc(intent_report, field)}")
    for tag in _GATED_TAGS:
        old = (intent_report.get("tags", {}).get(tag) or {}).get("intent_accuracy")
        new = (da_report.get("tags", {}).get(tag) or {}).get("intent_accuracy")
        if old is not None and (new is None or new < old):
            fails.append(f"tag '{tag}' regressed: {new} < {old}")
    if (da_report.get("tags", {}).get("acknowledge") or {}).get("termination_fp", 0) > 0:
        fails.append("termination false positives on acknowledge cases")
    if da_report["latency_ms"]["p50"] > intent_report["latency_ms"]["p50"] + 50:
        fails.append(f"p50 {da_report['latency_ms']['p50']} ms > intent-mode p50 + 50 ms")
    return fails
```

- [ ] **Step 6: Implement `run.py`**

```python
# agent_core/eval/nlu/run.py
"""CLI: replay labelled cases through one NLU mode, or compare two reports (spec §11)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.nlu.adapters import predict_dialogue_act, predict_intent
from eval.nlu.cases import load_cases
from eval.nlu.offline import OfflineGateway, load_merged_config
from eval.nlu.score import gate, score
from src.chat_provider import build_chat_provider
from src.preprocessing.nlu_processor import NLUProcessor
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.understanding.understander import TurnUnderstander
from src.workflow_loader import AgentWorkflowLoader


def _routed(workflow) -> set[str]:
    rules = [r for s in workflow.subagents.values() for r in s.routing] + list(workflow.global_routing)
    return {r.intent for r in rules if r.intent != "*"}


def _resolves_to(workflow) -> str | None:
    return next((p.resolves_to for s in workflow.subagents.values() for p in s.pending if p.resolves_to), None)


def main(argv: list[str] | None = None) -> int:
    """Run the harness. Exit code 0 on success (and gate pass with --compare), 1 otherwise."""
    ap = argparse.ArgumentParser(prog="eval.nlu.run")
    ap.add_argument("--config", help="domain config dir, e.g. ../dev-kit/configs/blue-dots")
    ap.add_argument("--mode", choices=["intent", "dialogue_act"])
    ap.add_argument("--cases", help="JSONL cases")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", help="write the report JSON here")
    ap.add_argument("--compare", nargs=2, metavar=("INTENT_REPORT", "DA_REPORT"))
    args = ap.parse_args(argv)

    if args.compare:
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        fails = gate(a, b)
        print("GATE PASS" if not fails else "GATE FAIL\n- " + "\n- ".join(fails))
        return 0 if not fails else 1

    config = load_merged_config(args.config, args.mode)
    MergedConfig.validate_full(config)
    workflow = AgentWorkflowLoader().load(config=config, tool_registry=ToolRegistry(config, OfflineGateway(config)))
    cases = load_cases(args.cases)
    agent_cfg = dict(config.get("agent") or {})
    nlu_cfg = config["preprocessing"]["nlu_processor"]
    provider_cfg = {**agent_cfg, "provider": nlu_cfg.get("provider") or agent_cfg.get("provider"),
                    "primary_model": nlu_cfg.get("model") or agent_cfg.get("primary_model")}
    preds: dict[str, list] = {}
    if args.mode == "dialogue_act":
        provider = build_chat_provider({**provider_cfg, "timeout_ms": nlu_cfg.get("timeout_ms", 2500),
                                        "retry_attempts": nlu_cfg.get("retry_attempts", 2),
                                        "sdk_max_retries": 0, "retry_on_timeout": False})
        und = TurnUnderstander.from_config(config, workflow, provider)
        for c in cases:
            preds[c.id] = [predict_dialogue_act(c, und) for _ in range(args.repeat)]
    else:
        nlu = NLUProcessor(config, chat_provider=build_chat_provider(provider_cfg))
        routed, rt = _routed(workflow), _resolves_to(workflow) or "selected_job_item_id"
        emap = dict(config.get("entity_to_profile_field") or {})
        for c in cases:
            preds[c.id] = [predict_intent(c, nlu, workflow, emap, routed, rt) for _ in range(args.repeat)]
    report = score(cases, preds, args.mode)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Intent mode needs the domain's `intents` (it stays authored), and dialogue_act mode needs the Task 20 blocks. Running the CLI makes **real** provider calls: an API key must be in the environment. Never run it in CI.

- [ ] **Step 7: Synthetic cases**

Create `agent_core/eval/nlu/cases/synthetic.jsonl`. It uses the blue-dots step names and the state that makes the intended pending question resolve, per the Task 20 config. Start with these 24 lines, one JSON object per line:

```jsonl
{"id":"ack-close-01","tags":["acknowledge","termination"],"step":"apply_confirm","state":{"applications_submitted":1,"profile_item_id":"p1"},"recent":[{"caller":"हाँ","bot":"आपका आवेदन भेज दिया गया है। क्या कुछ और मदद चाहिए?","interrupted":false}],"caller_now":["ठीक है धन्यवाद"],"expect":{"acts":["acknowledge"],"pending":"closing_offer","intent":"any_input","terminate":false}}
{"id":"ack-submit-01","tags":["acknowledge"],"step":"apply_confirm","state":{"applications_submitted":0,"profile_item_id":"p1"},"recent":[{"caller":"पहले वाला","bot":"क्या मैं फ्लिपकार्ट में आवेदन भेज दूँ?","interrupted":false}],"caller_now":["ठीक है धन्यवाद"],"expect":{"pending":"submit_confirm","intent":"any_input","terminate":false}}
{"id":"ack-jobs-01","tags":["acknowledge"],"step":"job_match","state":{"trade":"Welder","location":"Bengaluru"},"offered_tool":"fetch_jobs","offered":[{"item_id":"j1","role":"Welder","company":"Flipkart"}],"recent":[{"caller":"वेल्डर","bot":"बेंगलुरु में फ्लिपकार्ट में वेल्डर की नौकरी है।","interrupted":false}],"caller_now":["अच्छा जी धन्यवाद"],"expect":{"acts":["acknowledge"],"intent":"any_input","terminate":false}}
{"id":"ack-mid-01","tags":["acknowledge"],"step":"profile_resolve","state":{"consent_given":true,"age":25},"recent":[{"caller":"पच्चीस","bot":"आप कौन सा काम करते हैं?","interrupted":false}],"caller_now":["धन्यवाद"],"expect":{"intent":"any_input","terminate":false}}
{"id":"consent-yes-01","tags":["consent"],"step":"opening","state":{"opening_phrase_emitted":true},"recent":[{"caller":"हेलो","bot":"हम आपकी जानकारी सेव करेंगे ताकि आपके लिए आवेदन कर सकें, ठीक है?","interrupted":false}],"caller_now":["हाँ जी ठीक है"],"expect":{"acts":["affirm"],"pending":"consent","relation":"answers_pending","intent":"any_input","slots":{"consent_response":"granted"},"terminate":false}}
{"id":"consent-yes-02","tags":["consent"],"step":"opening","state":{"opening_phrase_emitted":true},"recent":[{"caller":"हेलो","bot":"हम आपकी जानकारी सेव करेंगे, ठीक है?","interrupted":false}],"caller_now":["हाँ मैं काम के बारे में बात करना चाहता हूँ"],"expect":{"pending":"consent","intent":"any_input","slots":{"consent_response":"granted"},"terminate":false}}
{"id":"consent-no-01","tags":["consent"],"step":"opening","state":{"opening_phrase_emitted":true},"recent":[{"caller":"हेलो","bot":"हम आपकी जानकारी सेव करेंगे, ठीक है?","interrupted":false}],"caller_now":["नहीं मत कीजिए"],"expect":{"acts":["deny"],"intent":"decline","slots":{"consent_response":"declined"},"terminate":false}}
{"id":"consent-wrongq-01","tags":["consent"],"step":"job_match","state":{"consent_given":true,"consent_response":"","trade":"Welder","location":"Bengaluru"},"recent":[{"caller":"वेल्डर","bot":"क्या मैं आपके लिए नौकरियां ढूंढूँ?","interrupted":false}],"caller_now":["हाँ ढूंढो"],"expect":{"intent":"any_input","terminate":false}}
{"id":"age-01","tags":["age"],"step":"opening","state":{"opening_phrase_emitted":true,"consent_response":"granted","consent_given":true},"recent":[{"caller":"हाँ","bot":"आपकी उम्र कितनी है?","interrupted":false}],"caller_now":["बाईस साल"],"expect":{"pending":"age","intent":"any_input","slots":{"age":22},"terminate":false}}
{"id":"age-02","tags":["age"],"step":"opening","state":{"opening_phrase_emitted":true,"consent_response":"granted","consent_given":true},"recent":[{"caller":"हाँ","bot":"आपकी उम्र कितनी है?","interrupted":false}],"caller_now":["मैं सोलह का हूँ"],"expect":{"intent":"any_input","slots":{"age":16},"terminate":false}}
{"id":"age-03","tags":["age"],"step":"opening","state":{"opening_phrase_emitted":true,"consent_response":"granted","consent_given":true},"recent":[{"caller":"हाँ","bot":"आपकी उम्र कितनी है?","interrupted":false}],"caller_now":["twenty five"],"expect":{"intent":"any_input","slots":{"age":25},"terminate":false}}
{"id":"trade-01","tags":["provide_info"],"step":"profile_resolve","state":{"consent_given":true,"age":25},"recent":[{"caller":"पच्चीस","bot":"आप कौन सा काम करते हैं?","interrupted":false}],"caller_now":["इलेक्ट्रीशियन का काम"],"expect":{"acts":["provide_info"],"intent":"any_input","slots":{"trade":"Electrician"},"terminate":false}}
{"id":"correct-01","tags":["correction"],"step":"job_match","state":{"trade":"Electrician","location":"Bengaluru"},"recent":[{"caller":"इलेक्ट्रीशियन","bot":"बेंगलुरु में इलेक्ट्रीशियन की नौकरियां...","interrupted":true}],"caller_now":["इलेक्ट्रीशियन नहीं वेल्डर का काम ढूंढ रहा हूँ"],"expect":{"acts":["correct"],"intent":"any_input","slots":{"trade":"Welder"},"terminate":false}}
{"id":"select-01","tags":["select"],"step":"job_match","state":{"trade":"Welder","location":"Bengaluru"},"offered_tool":"fetch_jobs","offered":[{"item_id":"j1","role":"Welder","company":"Flipkart"},{"item_id":"j2","role":"Welder","company":"Titan"}],"recent":[{"caller":"वेल्डर","bot":"दो नौकरियां हैं — फ्लिपकार्ट और टाइटन।","interrupted":false}],"caller_now":["हाँ पहले वाला को अप्लाई कर दो"],"expect":{"intent":"job_pick","option_id":"j1","terminate":false}}
{"id":"select-02","tags":["select"],"step":"job_match","state":{"trade":"Welder","location":"Bengaluru"},"offered_tool":"fetch_jobs","offered":[{"item_id":"j1","role":"Welder","company":"Flipkart"},{"item_id":"j2","role":"Welder","company":"Titan"}],"recent":[{"caller":"वेल्डर","bot":"दो नौकरियां हैं — फ्लिपकार्ट और टाइटन।","interrupted":false}],"caller_now":["टाइटन वाली"],"expect":{"intent":"job_pick","option_id":"j2","terminate":false}}
{"id":"select-oor-01","tags":["select"],"step":"job_match","state":{"trade":"Welder"},"offered_tool":"fetch_jobs","offered":[{"item_id":"j1","role":"Welder","company":"Flipkart"}],"recent":[{"caller":"वेल्डर","bot":"एक नौकरी है — फ्लिपकार्ट।","interrupted":false}],"caller_now":["तीसरा वाला"],"expect":{"intent":"any_input","terminate":false}}
{"id":"submit-yes-01","tags":["submit"],"step":"apply_confirm","state":{"applications_submitted":0,"profile_item_id":"p1"},"recent":[{"caller":"पहले वाला","bot":"क्या मैं आवेदन भेज दूँ?","interrupted":false}],"caller_now":["हाँ भेज दो"],"expect":{"acts":["affirm"],"intent":"apply_now","terminate":false}}
{"id":"submit-no-01","tags":["submit"],"step":"apply_confirm","state":{"applications_submitted":0,"profile_item_id":"p1"},"recent":[{"caller":"पहले वाला","bot":"क्या मैं आवेदन भेज दूँ?","interrupted":false}],"caller_now":["नहीं अभी नहीं"],"expect":{"acts":["deny"],"intent":"decline","terminate":false}}
{"id":"explore-01","tags":["request_change"],"step":"apply_confirm","state":{"applications_submitted":0,"profile_item_id":"p1"},"recent":[{"caller":"पहले वाला","bot":"क्या मैं आवेदन भेज दूँ?","interrupted":false}],"caller_now":["और कोई नौकरी है क्या"],"expect":{"intent":"explore_more","terminate":false}}
{"id":"ask-salary-01","tags":["off_script"],"step":"opening","state":{"opening_phrase_emitted":true,"consent_response":"granted","consent_given":true},"recent":[{"caller":"हाँ","bot":"आपकी उम्र कितनी है?","interrupted":false}],"caller_now":["सैलरी कितनी मिलेगी"],"expect":{"acts":["ask"],"relation":"new_topic","topic":"salary","intent":"any_input","terminate":false}}
{"id":"unrelated-01","tags":["off_script"],"step":"job_match","state":{"trade":"Welder"},"recent":[{"caller":"वेल्डर","bot":"क्या मैं नौकरियां ढूंढूँ?","interrupted":false}],"caller_now":["अरे बेटा पानी ले आ"],"expect":{"relation":"unrelated","intent":"any_input","terminate":false}}
{"id":"garbled-01","tags":["off_script"],"step":"profile_resolve","state":{"consent_given":true,"age":25},"recent":[{"caller":"पच्चीस","bot":"आप कौन सा काम करते हैं?","interrupted":false}],"caller_now":["एक मिनट एलेक्ट्रीशियनली"],"expect":{"intent":"any_input","terminate":false}}
{"id":"close-01","tags":["termination"],"step":"apply_confirm","state":{"applications_submitted":1,"profile_item_id":"p1"},"recent":[{"caller":"हाँ","bot":"आवेदन भेज दिया है। क्या कुछ और मदद चाहिए?","interrupted":false}],"caller_now":["नहीं बस अब रखता हूँ"],"expect":{"acts":["close"],"intent":"termination_intent","terminate":true}}
{"id":"close-mid-01","tags":["termination"],"step":"job_match","state":{"trade":"Welder"},"recent":[{"caller":"वेल्डर","bot":"क्या मैं नौकरियां ढूंढूँ?","interrupted":false}],"caller_now":["बाद में बात करते हैं"],"expect":{"acts":["close"],"intent":"any_input","terminate":false}}
```

> Note: `close-mid-01` expects `any_input`. Mid-journey, an ungated `close` is confirmed by the main LLM, not hung up on (spec §6.7).

Then extend the file to **at least 10 cases per tag** (`acknowledge`, `consent`, `age`, `provide_info`, `correction`, `select`, `submit`, `request_change`, `off_script`, `termination`). Vary the wording with these phrasings, keeping state and `expect` consistent with the tag's examples above:

| tag | add utterances |
|---|---|
| acknowledge | "अच्छा", "जी", "ठीक है", "हम्म ठीक", "शुक्रिया", "ओके थैंक यू" (spread across closing_offer / submit_confirm / select_job / trade pending) |
| consent | "बिल्कुल", "कर दीजिए", "हाँ हाँ", "ओके", "नहीं चाहिए", "मना है" |
| age | "अट्ठारह साल", "उन्नीस", "35", "मेरी उम्र चालीस है", "पंद्रह", "साठ साल" |
| provide_info | "प्लंबर हूँ", "बेंगलुरु में", "हुबली", "ड्राइवर का काम", "मेरा नाम अरुण है", "पहले काम किया है" |
| correction | "नहीं हुबली नहीं धारवाड़", "उम्र छब्बीस है पच्चीस नहीं", "प्लंबर नहीं इलेक्ट्रीशियन", "नाम अरुण नहीं अर्जुन", "शहर गलत है मैसूर", "बेंगलुरु नहीं, मुंबई" |
| select | "दूसरा", "फ्लिपकार्ट वाला", "आखिरी वाला", "पहली नौकरी", "सोनाटा वाली", "1 नंबर" |
| submit | "हाँ कर दो", "भेज दीजिए", "ठीक है भेजो", "रुको मत भेजो", "नहीं", "अभी नहीं" |
| request_change | "दूसरे शहर में", "कोई और काम", "दूसरी नौकरियां दिखाओ", "मेरी डिटेल बदलनी है", "पार्ट टाइम है क्या", "पास में कुछ है" |
| off_script | "आप कौन बोल रही हैं", "ये कॉल किस लिए है", "दोबारा बोलो", "एक मिनट", "टीवी की आवाज़", "क्या?" |
| termination | "बाय", "फोन रख रहा हूँ", "बाद में करूँगा", "नहीं चाहिए अब कुछ", "रहने दो", "बंद करो" (closing_offer → terminate true; others → any_input, terminate false) |

- [ ] **Step 8: Run tests**

Run: `cd agent_core && uv run pytest tests/eval -v`
Expected: PASS. Also check every synthetic line loads:
`cd agent_core && uv run python -c "from eval.nlu.cases import load_cases; print(len(load_cases('eval/nlu/cases/synthetic.jsonl')))"` → prints ≥ 100.

- [ ] **Step 9: Commit**

```bash
git add agent_core/eval agent_core/tests/eval agent_core/pyproject.toml
git commit -m "feat(agent-core): NLU replay eval harness with synthetic Hindi cases and switch-over gate"
```

---

### Task 20: Blue Dots config (authored, `mode: intent` kept), real-config validation, docstring fix

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml`
- Modify: `agent_core/src/preprocessing/nlu_processor.py` (docstrings only)
- Test: `agent_core/tests/test_blue_dots_dialogue_act_config.py`

**Interfaces:**
- Consumes: everything above
- Produces: a Blue Dots config that validates in both modes. Switching is a one-line change (`mode: dialogue_act`), made later in its own commit after the §11.5 gate passes. **Not part of this plan.**

- [ ] **Step 1: Failing test**

```python
# agent_core/tests/test_blue_dots_dialogue_act_config.py
"""The Blue Dots domain config validates in both NLU modes; pending questions resolve as designed."""
from pathlib import Path

import pytest

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.understanding.config import DialogueActConfig
from src.understanding.pending import PendingResolver
from src.workflow_loader import AgentWorkflowLoader

BLUE_DOTS = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"


def _load(mode):
    cfg = load_merged_config(BLUE_DOTS, mode)
    MergedConfig.validate_full(cfg)
    wf = AgentWorkflowLoader().load(config=cfg, tool_registry=ToolRegistry(cfg, OfflineGateway(cfg)))
    return cfg, wf


def test_intent_mode_still_loads_and_is_the_default():
    cfg, _ = _load("intent")
    import yaml
    raw = yaml.safe_load((BLUE_DOTS / "agent_core.yaml").read_text(encoding="utf-8"))
    assert raw["preprocessing"]["nlu_processor"]["mode"] == "intent"


def test_dialogue_act_mode_loads():
    cfg, _ = _load("dialogue_act")
    da = DialogueActConfig.from_config(cfg)
    assert {"consent_response", "age", "trade", "location", "name"} <= set(da.slots)
    assert da.slots["age"].min == 5 and da.slots["age"].max == 99


@pytest.mark.parametrize("step, state, expected", [
    ("opening", {}, "consent"),
    ("opening", {"consent_response": "granted"}, "age"),
    ("opening", {"consent_given": True, "has_age": True}, None),
    ("job_match", {}, "select_job"),
    ("apply_confirm", {"applications_submitted": 0}, "submit_confirm"),
    ("apply_confirm", {"applications_submitted": 1}, "closing_offer"),
])
def test_pending_resolution(step, state, expected):
    _, wf = _load("dialogue_act")
    p = PendingResolver(wf).resolve(step, state)
    assert (p.id if p else None) == expected


def test_off_track_rule_precedes_catch_all_everywhere():
    _, wf = _load("dialogue_act")
    for sid, sub in wf.subagents.items():
        if sub.is_terminal or not sub.routing:
            continue
        intents = [r.intent for r in sub.routing]
        if "*" in intents:
            assert "off_track" in intents and intents.index("off_track") < intents.index("*"), sid

def test_blue_dots_journey_routes_end_to_end():
    """Real TurnUnderstander + real routing over the Blue Dots workflow, scripted NLU (spec §12)."""
    from eval.nlu.offline import StaticToolCache
    from src.understanding.dialogue_act_nlu import DialogueActNLUBase
    from src.understanding.models import DialogueActResult
    from src.understanding.understander import TurnContext, TurnUnderstander
    from tests.test_stream_turn import _make_agent_core

    class _Scripted(DialogueActNLUBase):
        def __init__(self, results):
            self._results = list(results)

        def classify(self, user_message):
            return self._results.pop(0), None, 1

    def R(*acts, relation="answers_pending", option=None, **slots):
        return DialogueActResult(acts=acts, relation=relation, option=option, slots=slots)

    seeds = {"opening_phrase_emitted": True, "trade": "", "location": "", "name": "", "age": 0,
             "profile_item_id": "", "applications_submitted": 0}
    jobs = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
            {"item_id": "j2", "role": "Welder", "company": "Titan"}]
    # (subagent the turn starts in, scripted NLU result, tool effects landing after routing)
    script = [
        ("opening", R("affirm", consent_response="granted"), {}),
        ("opening", R("provide_info", age=25), {}),
        ("profile_resolve", R("provide_info", trade="welder"), {}),
        ("profile_resolve", R("provide_info", location="bengaluru"), {}),
        ("job_match", R("other", relation="unrelated"), {}),
        ("job_match", R("other", relation="unrelated"), {}),
        ("job_match", R("other", relation="unclear"), {}),                       # 3rd → off_track
        ("job_match", R("select", option=1), {}),
        ("profile_setup", R("provide_info", name="arun"), {"profile_item_id": "p1"}),  # save_profile mapping
        ("profile_setup", R("affirm", relation="answers_other"), {}),
        ("apply_confirm", R("acknowledge", relation="unclear"), {}),             # thank-you before submit
        ("apply_confirm", R("affirm"), {"applications_submitted": 1}),           # apply_job mapping
        ("apply_confirm", R("acknowledge"), {}),
    ]
    cfg, wf = _load("dialogue_act")
    und = TurnUnderstander(DialogueActConfig.from_config(cfg), wf, _Scripted([r for _, r, _ in script]))
    agent = _make_agent_core(workflow=wf)
    state, sid, trail = dict(seeds), "opening", []
    for expected_step, _, tool_effects in script:
        assert sid == expected_step, trail
        u = und.understand(TurnContext(subagent_id=sid, state=dict(state), session=dict(state), segments=["x"],
                                       recent=[], tool_cache=StaticToolCache({"fetch_jobs": jobs})))
        for w in u.writes:
            state[w.key] = w.value
        nxt, rule = agent._resolve_next_subagent(current_subagent=wf.subagents[sid], nlu_result=u.nlu_result,
                                                 session=state)
        state.update(rule.session_writes if rule else {})
        state.update(tool_effects)
        trail.append((u.nlu_result.intent, nxt))
        sid = nxt
    intents = [i for i, _ in trail]
    assert trail[6] == ("off_track", "job_match")
    assert intents[7] == "job_pick" and state["selected_job_item_id"] == "j1"
    assert trail[10] == ("any_input", "apply_confirm")        # no apply, no hang-up on a thank-you
    assert intents[11] == "apply_now"
    assert sid == "ended"
    assert (state["trade"], state["location"], state["name"], state["consent_response"]) == (
        "Welder", "Bengaluru", "Arun", "granted")

```

- [ ] **Step 2: Run to verify failure**: `cd agent_core && uv run pytest tests/test_blue_dots_dialogue_act_config.py -v` → FAIL (no `mode` key; validation errors in dialogue_act mode).

- [ ] **Step 3: Author the config**

In `dev-kit/configs/blue-dots/agent_core.yaml`:

**a. `fetch_jobs` cache.** Under `connectors.read`, on the `fetch_jobs` connector, beside the existing `fetch_profile` `cache:` block:

```yaml
      # Stored so the dialogue-act NLU frame can list the jobs on offer and
      # resolve "पहले वाला" to an item_id (NLU dialogue-acts spec §5.2, §6.3).
      # Session scope: a later call searches afresh. The projection is a list,
      # so `keep` does not apply. Each query_text is its own entry; the latest
      # one is the list on offer.
      cache:
        scope: session
        ttl_seconds: 1800
```

**b. NLU block.** Under `preprocessing.nlu_processor`, keep every existing key (intent mode still uses them) and add:

```yaml
    # NLU dialogue-acts spec. Authored but INACTIVE: switch to dialogue_act in
    # its own commit once the replay harness gate (§11.5) passes.
    mode: intent
    topics: [job_details, salary, location, search, profile, process, identity, other]
    signals: [pay_disappointment, distance_issue, counsellor_request]
    slots:
      consent_response: { type: enum, values: [granted, declined], accept_when_pending: [consent],
                          description: "details save करने की अनुमति का जवाब" }
      # 5–99, not adult-only: the u18_blocked route must see under-age answers.
      age:              { type: int, min: 5, max: 99, accept_when_pending: [age],
                          description: "उम्र, digits में", examples: ["बाईस → 22", "सोलह → 16"] }
      trade:            { type: string, normalise: title, description: "काम/ट्रेड, English में (Welder, Plumber)" }
      location:         { type: string, normalise: title, description: "शहर, canonical English (Bengaluru, Hubli)" }
      name:             { type: string, description: "caller का नाम" }
      gender:           { type: enum, values: [male, female, other] }
      work_experience:  { type: enum, values: [Fresher, Worked before, Returning after a break] }
      experience_years: { type: int, min: 0, max: 60 }
      qualification:    { type: string, description: "School / College / ITI / Certification" }
      job_nature:       { type: enum, values: [Internship, Apprenticeship, Full-time, Flexible] }
      monthly_in_hand:  { type: int, min: 0, max: 1000000 }
    known_fields: [consent_response, age, trade, location, name, stored_trade, stored_location]
    examples:
      - { pending: closing_offer, caller: "ठीक है धन्यवाद", out: { acts: [acknowledge], relation: answers_pending } }
      - { pending: submit_confirm, caller: "ठीक है धन्यवाद", out: { acts: [acknowledge], relation: unclear } }
      - { pending: submit_confirm, caller: "हाँ भेज दो", out: { acts: [affirm], relation: answers_pending } }
      - { pending: consent, caller: "हाँ जी ठीक है", out: { acts: [affirm], relation: answers_pending, slots: { consent_response: granted } } }
      - { pending: select_job, caller: "हाँ ढूंढो", out: { acts: [affirm], relation: answers_other } }
      - { pending: age, caller: "बाईस साल", out: { acts: [provide_info], relation: answers_pending, slots: { age: 22 } } }
      - { pending: age, caller: "वेल्डिंग का काम चाहिए", out: { acts: [provide_info], relation: answers_other, slots: { trade: Welder } } }
      - { pending: select_job, caller: "इलेक्ट्रीशियन नहीं वेल्डर", out: { acts: [correct], relation: answers_other, slots: { trade: Welder } } }
      - { pending: select_job, caller: "फ्लिपकार्ट वाला", out: { acts: [select], relation: answers_pending, reference: { option: 1, spoken: "फ्लिपकार्ट वाला" } } }
      - { pending: age, caller: "सैलरी कितनी है", out: { acts: [ask], relation: new_topic, topic: salary } }
      - { pending: select_job, caller: "बाद में बात करते हैं", out: { acts: [close], relation: new_topic } }
    act_intents:
      - { acts: [deny], pending: consent, relation: answers_pending, intent: decline }
      - { acts: [select], pending: select_job, intent: job_pick }
      - { acts: [affirm], pending: submit_confirm, relation: answers_pending, intent: apply_now }
      - { acts: [deny], pending: submit_confirm, relation: answers_pending, intent: decline }
      - { acts: [request_change], topic: search, intent: explore_more }
      - { acts: [close], intent: termination_intent, gated: true }
    termination_gate:
      any_of:
        - { pending: closing_offer }
        - { field: applications_submitted, operator: gt, value: 0 }
```

**c. Pending questions.** Add `pending:` to these subagents, each next to its `routing:` key:

```yaml
    # opening
      pending:
        - id: consent
          expects: "हाँ/नहीं — details save करने की अनुमति"
          when:
            - { field: consent_given, operator: not_eq, value: true }
            - { field: consent_response, operator: in, value: [null, ""] }
        - id: age
          expects: "उम्र, साल में"
          when:
            - { field: has_age, operator: not_eq, value: true }
            - { field: age, operator: in, value: [null, "", 0] }
    # profile_resolve
      pending:
        - id: use_saved_details
          expects: "saved काम और शहर इस्तेमाल करें? हाँ/नहीं"
          when:
            - { field: profile_item_id, operator: not_eq, value: "" }
            - { field: profile_item_id, operator: not_eq, value: null }
            - { field: subagent_entry_count.profile_resolve, operator: lt, value: 2 }
        - id: trade
          expects: "कौन सा काम"
          when: [{ field: trade, operator: in, value: [null, ""] }]
        - id: location
          expects: "किस शहर में काम"
          when: [{ field: location, operator: in, value: [null, ""] }]
    # job_match
      pending:
        - id: select_job
          expects: "offered jobs में से एक, या दूसरी search"
          options_from: { tool: fetch_jobs, fields: [role, company], id_field: item_id }
          resolves_to: selected_job_item_id
    # profile_setup
      pending:
        - id: name
          expects: "caller का नाम"
          when: [{ field: name, operator: in, value: [null, ""] }]
    # apply_confirm
      pending:
        - id: closing_offer
          expects: "और कुछ मदद चाहिए? / कॉल खत्म"
          when: [{ field: applications_submitted, operator: gt, value: 0 }]
        - id: submit_confirm
          expects: "आवेदन भेजूँ? हाँ/नहीं"
```

**d. Off-track rule.** In `opening`, `profile_resolve`, `job_match`, `profile_setup`, `apply_confirm` and `clarification`, add as the **first** routing rule:

```yaml
        # dialogue_act mode only (intent mode never emits it): after
        # off_track.threshold off-script turns, stay here; <caller_turn> tells
        # the model to re-ask simply or offer to end the call (spec §6.6).
        - intent: off_track
          next_subagent_id: <this subagent's id>
```

Put it first because routing is first-match: `termination_intent` and intent-specific rules come before `*`, and `off_track` never coincides with them.

> `selected_job_item_id` stays in `entity_to_profile_field` for intent mode. It is not declared as a slot; in dialogue_act mode only the resolver writes it.

- [ ] **Step 4: NLU docstring fix**

In `agent_core/src/preprocessing/nlu_processor.py`:
- Module docstring (lines ~7–9): replace the claim that recent session history is injected with: "The user message carries the current workflow step, the last bot reply (`current_question`), stored profile field names and the utterance. Conversation history is not injected; see the dialogue_act mode (`src/understanding/`) for a context-rich alternative."
- Class docstring (lines ~98–99): make the same correction.

No code changes.

- [ ] **Step 5: Run tests**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_dialogue_act_config.py tests/test_nlu_processor.py -v` → PASS.
Run: `cd dev-kit && uv run pytest -q` → PASS. Any test that validates every domain config, including the cross-block session_mapping rule from Task 5, must stay green.

- [ ] **Step 6: Full regression**

Run: `cd agent_core && uv run pytest --cov=src -q` → PASS, coverage ≥ 70%.

- [ ] **Step 7: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/src/preprocessing/nlu_processor.py \
        agent_core/tests/test_blue_dots_dialogue_act_config.py
git commit -m "config(blue-dots): author dialogue_act NLU config (inactive), cache fetch_jobs; fix NLU docstrings"
```

---

## After the plan (not tasks — for the user to decide)

1. Run the harness on both modes against the synthetic set, plus private real-call cases captured with `log_raw_response`:
   ```bash
   cd agent_core
   uv run python -m eval.nlu.run --config ../dev-kit/configs/blue-dots --mode intent --cases <cases> --out intent.json
   uv run python -m eval.nlu.run --config ../dev-kit/configs/blue-dots --mode dialogue_act --cases <cases> --out da.json
   uv run python -m eval.nlu.run --compare intent.json da.json
   ```
2. If `GATE PASS`, switch to `mode: dialogue_act` in its own commit and compare VM calls with the 28 Sep and 30 Sep baselines (spec §14).
3. Spec D (main-LLM context and prompts) consumes `<caller_turn>`. It owns the stored-order job readout that §6.3 depends on.
