# GH-139 User-State Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a domain-configurable user-state model that the NLU classifies per turn, injects into the main LLM system prompt, persists via the existing Memory Layer round-trip, and emits as OTel attributes + a Observability Layer event on transition.

**Architecture:** Spec at `docs/superpowers/specs/2026-04-20-gh139-user-state-model-design.md`. Zero new services, zero new LLM calls. `NLUResult` grows an optional `user_state` field. `ManagerAgent.build_system_prompt` grows a `user_state_guidance` kwarg. `AgentCore.process_turn` reads previous state from `ContextBundle.session`, passes it to NLU, resolves the new state via a new helper, fills the template section via ManagerAgent, writes state back via the existing per-turn memory write. Feature off by default (`conversation.user_state_model.enabled: false`); existing KKB / farmer-friendly / obsrv-docs-assistant domains keep booting unchanged.

**Tech Stack:** Python 3.13 + `uv`, pytest, OpenTelemetry SDK, dataclass models. Follow `.claude/rules/base-class-pattern.md`, `.claude/rules/error-handling.md`, `.claude/rules/testing-requirements.md`, `.claude/rules/code-documentation.md`.

**Branch:** `GH-139-user-state` (already checked out).

---

## File Structure

**Source files** (paths relative to repo root):

| File | Role in this plan |
|---|---|
| `agent_core/src/models.py` | Add `UserStateClassification` dataclass + optional `user_state` field on `NLUResult`. |
| `agent_core/src/preprocessing/nlu_processor.py` | Read `conversation.user_state_model` at init. Conditionally extend the NLU prompt template. Accept new `previous_user_state` kwarg. Parse, validate, sticky-fallback on low confidence. |
| `agent_core/src/preprocessing/user_state_resolver.py` | **NEW.** Pure function `resolve_user_state(...)` that produces the new session payload from the NLU classification + previous payload + config + now. |
| `agent_core/src/manager_agent.py` | `build_system_prompt` gains `user_state_guidance: str | None = None` kwarg. Render section between subagent prompt and guardrail constraints. |
| `agent_core/src/orchestrator.py` | Read previous state from `bundle.session`, pass to NLU, resolve new payload, look up guidance, pass to `build_system_prompt`, write back via `_write_memory_sync`, set OTel span attrs, emit Obs Layer event on transition. Cache guidance dict at init time. |
| `dev-kit/dev_kit/schemas/agent_core.yaml` | Add `conversation.user_state_model` block template. Add `preprocessing.nlu_processor.user_state_confidence_threshold`. |
| `dev-kit/dpg/agent_core.yaml` | Add `preprocessing.nlu_processor.user_state_confidence_threshold: 0.4` default. |
| `dev-kit/dev_kit/agent/accumulator.py` | Insert `"user_state"` into `PHASES` between `memory` and `trust`. |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Add `user_state` branch in `get_phase_addition`. Update the phase list documented in the `overview` branch. |

**Test files:**

| File | Role |
|---|---|
| `agent_core/tests/test_models.py` | Verify `UserStateClassification` shape and `NLUResult.user_state` default. |
| `agent_core/tests/test_nlu_processor.py` | Six cases: disabled no-op, valid classification, below-threshold sticky, invalid id fallback, missing key fallback, existing intent tests still green. |
| `agent_core/tests/test_user_state_resolver.py` | **NEW.** Four cases: disabled, first-turn init, sticky, transition. |
| `agent_core/tests/test_manager_agent.py` | Two cases: guidance None renders nothing; guidance populated renders section between subagent prompt and guardrails. |
| `agent_core/tests/test_orchestrator.py` | Extend one existing test to verify the span attrs + event emission on transition. |
| `dev-kit/tests/test_accumulator.py` | Verify `PHASES` contains `user_state` at index 5. |
| `dev-kit/tests/test_phases.py` | Verify `get_phase_addition("user_state")` returns a non-empty branch with the expected schema reference. |

**Commit granularity:** one commit per task. Use Conventional Commits (`feat`, `test`, `docs`). All commits on branch `GH-139-user-state`.

---

## Task 1: UserStateClassification dataclass + NLUResult extension

**Files:**
- Modify: `agent_core/src/models.py`
- Test: `agent_core/tests/test_models.py`

- [ ] **Step 1: Write failing test**

Append to `agent_core/tests/test_models.py`:

```python
from src.models import NLUResult, UserStateClassification


def test_user_state_classification_defaults():
    usc = UserStateClassification(id="fog", confidence=0.82)
    assert usc.id == "fog"
    assert usc.confidence == 0.82


def test_nlu_result_user_state_default_is_none():
    result = NLUResult(intent="greeting", entities={}, sentiment="neutral", confidence=0.9)
    assert result.user_state is None


def test_nlu_result_accepts_user_state():
    usc = UserStateClassification(id="orientation", confidence=0.7)
    result = NLUResult(
        intent="greeting", entities={}, sentiment="neutral",
        confidence=0.9, user_state=usc,
    )
    assert result.user_state is usc
    assert result.user_state.id == "orientation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_models.py -v -k user_state`
Expected: 3 FAILED with `ImportError: cannot import name 'UserStateClassification'` (or similar).

- [ ] **Step 3: Implement minimal dataclass + field**

Edit `agent_core/src/models.py`. Find the NLU section (around line 112). Add before `NLUResult`:

```python
@dataclass
class UserStateClassification:
    """
    Classification output for the user's mental state dimension.

    Populated by NLU Processor when the domain declares conversation.user_state_model.
    None on NLUResult when the model is disabled or absent.
    """
    id: str
    confidence: float
```

Then change `NLUResult` to add the optional field:

```python
@dataclass
class NLUResult:
    """..."""  # existing docstring
    intent: str
    entities: dict[str, Any]
    sentiment: str
    confidence: float
    active_risks: list[str] | None = None
    user_state: UserStateClassification | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent_core && uv run pytest tests/test_models.py -v -k user_state`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/models.py agent_core/tests/test_models.py
git commit -m "feat(agent-core): add UserStateClassification dataclass and NLUResult.user_state field (GH-139)"
```

---

## Task 2: Schema additions in dev-kit templates

**Files:**
- Modify: `dev-kit/dev_kit/schemas/agent_core.yaml`
- Modify: `dev-kit/dpg/agent_core.yaml`

- [ ] **Step 1: Extend the authoring template**

Edit `dev-kit/dev_kit/schemas/agent_core.yaml`. In the `conversation:` block (around line 29), append:

```yaml
  user_state_model:              # optional — Conversational agents only
    enabled: false               # when false, block is a no-op
    default_state: ""            # required when enabled=true; must match a state id below
    states:                      # required when enabled=true; non-empty
      - id: ""                   # unique, non-empty, snake_case
        signals: []              # natural-language phrases the NLU uses as hints
        guidance: ""             # required when enabled=true; injected into main LLM prompt
```

In the `preprocessing.nlu_processor:` block (around line 85), add after `confidence_threshold`:

```yaml
    user_state_confidence_threshold: 0.4   # below this, user-state classification stays sticky (previous state retained)
```

- [ ] **Step 2: Extend the framework defaults**

Edit `dev-kit/dpg/agent_core.yaml`. The file currently does not declare a `preprocessing` block (it inherits from the domain/template). Do NOT add `preprocessing` here — the threshold default lives in the Python code (Task 3) so domains without a value still work. Leave this file unchanged.

Verify by reading the file: no change. Skip file staging if unchanged.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/dev_kit/schemas/agent_core.yaml
git commit -m "feat(dev-kit): add user_state_model schema slot to agent_core template (GH-139)"
```

---

## Task 3: NLUProcessor — read user-state config at init with validation

**Files:**
- Modify: `agent_core/src/preprocessing/nlu_processor.py`
- Test: `agent_core/tests/test_nlu_processor.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_nlu_processor.py`:

```python
import pytest
from src.preprocessing.nlu_processor import NLUProcessor
from src.exceptions import ConfigurationError


def _base_config(user_state_model=None):
    cfg = {
        "preprocessing": {
            "nlu_processor": {
                "model": "claude-haiku-4-5-20251001",
                "confidence_threshold": 0.5,
                "domain_instruction": "d",
                "intents": ["unknown"],
                "entities": [],
                "sentiment_classes": ["neutral"],
            },
        },
    }
    if user_state_model is not None:
        cfg["conversation"] = {"user_state_model": user_state_model}
    return cfg


def test_nlu_user_state_disabled_by_default():
    p = NLUProcessor(_base_config())
    assert p._user_state_enabled is False
    assert p._user_states == []
    assert p._user_state_threshold == 0.4


def test_nlu_user_state_threshold_read_from_config():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 0.3
    p = NLUProcessor(cfg)
    assert p._user_state_threshold == 0.3


def test_nlu_user_state_enabled_reads_states():
    p = NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently."},
            {"id": "orientation", "signals": [], "guidance": "Show the map."},
        ],
    }))
    assert p._user_state_enabled is True
    assert {s["id"] for s in p._user_states} == {"fog", "orientation"}
    assert p._user_state_default == "fog"


def test_nlu_user_state_enabled_without_default_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }))


def test_nlu_user_state_enabled_without_states_raises():
    with pytest.raises(ConfigurationError, match="states"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [],
        }))


def test_nlu_user_state_default_not_in_states_raises():
    with pytest.raises(ConfigurationError, match="default_state"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "nonexistent",
            "states": [{"id": "fog", "signals": [], "guidance": "g"}],
        }))


def test_nlu_user_state_duplicate_ids_raise():
    with pytest.raises(ConfigurationError, match="unique"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "signals": [], "guidance": "g1"},
                {"id": "fog", "signals": [], "guidance": "g2"},
            ],
        }))


def test_nlu_user_state_empty_guidance_raises():
    with pytest.raises(ConfigurationError, match="guidance"):
        NLUProcessor(_base_config({
            "enabled": True,
            "default_state": "fog",
            "states": [{"id": "fog", "signals": [], "guidance": ""}],
        }))


def test_nlu_user_state_threshold_out_of_range_raises():
    cfg = _base_config()
    cfg["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 1.5
    with pytest.raises(ConfigurationError, match="user_state_confidence_threshold"):
        NLUProcessor(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v -k user_state`
Expected: 9 FAILED (attribute errors / no raise).

- [ ] **Step 3: Implement init reads + validation**

Edit `agent_core/src/preprocessing/nlu_processor.py`. Extend `__init__` (after the existing `_default_intents` assignment):

```python
        # ------------------------------------------------------------------
        # User-state model (GH-139) — optional, Conversational domains only
        # ------------------------------------------------------------------
        usm = (config or {}).get("conversation", {}).get("user_state_model", {}) or {}
        self._user_state_enabled: bool = bool(usm.get("enabled", False))
        self._user_states: list[dict] = []
        self._user_state_default: str = ""

        raw_threshold = nlu_config.get("user_state_confidence_threshold", 0.4)
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise ConfigurationError(
                f"preprocessing.nlu_processor.user_state_confidence_threshold "
                f"must be a float, got {raw_threshold!r}"
            ) from e
        if not 0.0 <= threshold <= 1.0:
            raise ConfigurationError(
                f"preprocessing.nlu_processor.user_state_confidence_threshold "
                f"must be in [0.0, 1.0], got {threshold}"
            )
        self._user_state_threshold: float = threshold

        if self._user_state_enabled:
            states = usm.get("states") or []
            if not states:
                raise ConfigurationError(
                    "conversation.user_state_model.states must be non-empty when enabled=true"
                )
            ids: list[str] = []
            for idx, s in enumerate(states):
                sid = (s or {}).get("id", "")
                guidance = (s or {}).get("guidance", "")
                if not sid:
                    raise ConfigurationError(
                        f"conversation.user_state_model.states[{idx}].id must be non-empty"
                    )
                if not guidance:
                    raise ConfigurationError(
                        f"conversation.user_state_model.states[{idx}].guidance "
                        f"must be non-empty for state {sid!r}"
                    )
                ids.append(sid)
            if len(ids) != len(set(ids)):
                raise ConfigurationError(
                    "conversation.user_state_model.states ids must be unique"
                )
            default = usm.get("default_state", "")
            if not default:
                raise ConfigurationError(
                    "conversation.user_state_model.default_state is required when enabled=true"
                )
            if default not in ids:
                raise ConfigurationError(
                    f"conversation.user_state_model.default_state {default!r} "
                    f"must match one of the declared state ids: {ids}"
                )
            self._user_states = list(states)
            self._user_state_default = default
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v -k user_state`
Expected: 9 PASSED.

Also run the full file to confirm no regression:

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v`
Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/preprocessing/nlu_processor.py agent_core/tests/test_nlu_processor.py
git commit -m "feat(agent-core): validate user_state_model config in NLUProcessor (GH-139)"
```

---

## Task 4: NLUProcessor — prompt extension + parsing when enabled

**Files:**
- Modify: `agent_core/src/preprocessing/nlu_processor.py`
- Test: `agent_core/tests/test_nlu_processor.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_nlu_processor.py`:

```python
from unittest.mock import MagicMock
from src.models import LLMResponse, UserStateClassification


def _enabled_processor():
    return NLUProcessor(_base_config({
        "enabled": True,
        "default_state": "fog",
        "states": [
            {"id": "fog", "signals": ["vague"], "guidance": "Orient gently. Surface 2-3 directions."},
            {"id": "orientation", "signals": ["asking about options"], "guidance": "Show the real market picture."},
        ],
    }))


def _disabled_processor():
    return NLUProcessor(_base_config())


def _mock_llm(payload_json: str):
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=payload_json, stop_reason="end_turn", model_used="haiku",
    )
    return llm


def test_process_returns_user_state_when_enabled_and_valid():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.82}}'
    )
    result = p.process(
        normalised_input="kitna pay hai",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"
    assert abs(result.user_state.confidence - 0.82) < 1e-6


def test_process_sticky_when_below_threshold():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"orientation","confidence":0.2}}'
    )
    result = p.process(
        normalised_input="hmm",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"  # sticky
    assert result.user_state.confidence == 0.2


def test_process_sticky_when_id_unknown():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"gibberish","confidence":0.95}}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    assert result.user_state is not None
    assert result.user_state.id == "fog"  # sticky fallback


def test_process_sticky_when_key_missing():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="orientation",
    )
    assert result.user_state is not None
    assert result.user_state.id == "orientation"


def test_process_returns_none_when_disabled():
    p = _disabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}'
    )
    result = p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state=None,
    )
    assert result.user_state is None


def test_process_prompt_includes_state_section_when_enabled():
    p = _enabled_processor()
    llm = _mock_llm(
        '{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9,'
        '"user_state":{"id":"fog","confidence":0.9}}'
    )
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state="fog",
    )
    system_prompt = llm.call.call_args.kwargs["system"]
    assert "User mental state classification" in system_prompt
    assert "fog" in system_prompt
    assert "orientation" in system_prompt
    assert "Previous state: fog" in system_prompt


def test_process_prompt_excludes_state_section_when_disabled():
    p = _disabled_processor()
    llm = _mock_llm('{"intent":"unknown","entities":{},"sentiment":"neutral","confidence":0.9}')
    p.process(
        normalised_input="x",
        current_question="",
        current_subagent_id="main",
        llm=llm,
        previous_user_state=None,
    )
    system_prompt = llm.call.call_args.kwargs["system"]
    assert "User mental state classification" not in system_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v -k "process_returns_user_state or process_sticky or process_returns_none_when_disabled or process_prompt"`
Expected: 7 FAILED.

- [ ] **Step 3: Extend the prompt template**

Edit `agent_core/src/preprocessing/nlu_processor.py`. Replace the prompt template constant to accept an optional state section, and add a helper to build it.

Above `_NLU_SYSTEM_PROMPT_TEMPLATE`, add:

```python
_USER_STATE_SECTION_TEMPLATE = """

## User mental state classification
The user may be in one of these mental states. Classify which one best fits
their latest message, using the signals as hints (not strict rules).

Previous state: {previous_state}
If the message is ambiguous or does not clearly shift the state, return the
previous state with lower confidence.

States:
{states_block}

Return an additional top-level field in your JSON:
  "user_state": {{ "id": "<one of the declared ids>", "confidence": <0.0..1.0> }}
"""
```

Change `_NLU_SYSTEM_PROMPT_TEMPLATE` to terminate with a `{user_state_section}` placeholder right before the closing triple-quote:

Find the current final line of the template (ends with `"Never include keys outside the four specified ...").` or similar). Replace the end of the string literal with:

```python
...
- Never include keys outside the four specified (intent, entities, sentiment, confidence).{user_state_section}"""
```

- [ ] **Step 4: Build the section at process-time**

Inside `NLUProcessor.process()`, just before the `system_prompt = _NLU_SYSTEM_PROMPT_TEMPLATE.format(...)` call, insert:

```python
            # User-state classification section (GH-139) — empty string when disabled
            if self._user_state_enabled:
                state_lines: list[str] = []
                for s in self._user_states:
                    sid = s.get("id", "")
                    signals = s.get("signals", []) or []
                    guidance_first = (s.get("guidance", "") or "").strip().splitlines()[0]
                    signals_str = " | ".join(f'"{sig}"' for sig in signals) if signals else "(none)"
                    state_lines.append(
                        f"- {sid}:\n    signals: {signals_str}\n    meaning: {guidance_first}"
                    )
                user_state_section = _USER_STATE_SECTION_TEMPLATE.format(
                    previous_state=previous_user_state or self._user_state_default,
                    states_block="\n".join(state_lines),
                )
            else:
                user_state_section = ""
```

Then include `user_state_section=user_state_section` in the `.format(...)` call.

- [ ] **Step 5: Add the `previous_user_state` kwarg**

Edit the `process()` signature. Add `previous_user_state: str | None = None` as the final kwarg (after `existing_profile_keys`). Update the docstring `Args:` block:

```python
        previous_user_state: User-state id from the prior turn (or config default
                             on first turn). Passed to the LLM as "previous state"
                             context and used as the sticky fallback when the
                             returned classification is below threshold or invalid.
                             Ignored when the user-state model is disabled.
```

- [ ] **Step 6: Parse `user_state` from the response and return it**

Inside `process()`, after the existing `intent` / `entities` extraction and before the `result = NLUResult(...)` construction, add:

```python
            user_state_obj: UserStateClassification | None = None
            if self._user_state_enabled:
                raw_state = parsed.get("user_state")
                valid_ids = {s.get("id") for s in self._user_states}
                fallback_id = previous_user_state or self._user_state_default
                if isinstance(raw_state, dict):
                    parsed_id = raw_state.get("id", "")
                    try:
                        parsed_conf = float(raw_state.get("confidence", 0.0))
                    except (TypeError, ValueError):
                        parsed_conf = 0.0
                    if parsed_id in valid_ids and parsed_conf >= self._user_state_threshold:
                        user_state_obj = UserStateClassification(
                            id=parsed_id, confidence=parsed_conf,
                        )
                    else:
                        if parsed_id not in valid_ids and parsed_id:
                            logger.warning(
                                "nlu_processor.user_state_invalid_id",
                                extra={
                                    "operation": "nlu_processor.process",
                                    "status": "skipped",
                                    "returned_id": parsed_id,
                                    "fallback_id": fallback_id,
                                },
                            )
                        user_state_obj = UserStateClassification(
                            id=fallback_id, confidence=parsed_conf,
                        )
                else:
                    logger.warning(
                        "nlu_processor.user_state_missing",
                        extra={
                            "operation": "nlu_processor.process",
                            "status": "skipped",
                            "fallback_id": fallback_id,
                        },
                    )
                    user_state_obj = UserStateClassification(
                        id=fallback_id, confidence=0.0,
                    )
```

Add `user_state=user_state_obj` to the `NLUResult(...)` construction.

Add the import at the top of the file: `from src.models import NLUResult, UserStateClassification`.

Also ensure the fallback path (`_fallback_nlu_result()`) still returns `user_state=None` — no change needed since the dataclass default is None.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v`
Expected: all tests PASS (new + existing).

- [ ] **Step 8: Commit**

```bash
git add agent_core/src/preprocessing/nlu_processor.py agent_core/tests/test_nlu_processor.py
git commit -m "feat(agent-core): classify user_state in NLUProcessor with sticky fallback (GH-139)"
```

---

## Task 5: user_state_resolver helper

**Files:**
- Create: `agent_core/src/preprocessing/user_state_resolver.py`
- Create: `agent_core/tests/test_user_state_resolver.py`

- [ ] **Step 1: Write failing tests**

Create `agent_core/tests/test_user_state_resolver.py`:

```python
"""
Unit tests for agent_core.src.preprocessing.user_state_resolver.
"""
from datetime import datetime, timezone

from src.models import UserStateClassification
from src.preprocessing.user_state_resolver import resolve_user_state


NOW = datetime(2026, 4, 20, 10, 15, 0, tzinfo=timezone.utc)
CONFIG = {
    "conversation": {
        "user_state_model": {
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "signals": [], "guidance": "g1"},
                {"id": "orientation", "signals": [], "guidance": "g2"},
            ],
        },
    },
}


def test_disabled_returns_none():
    payload, transitioned = resolve_user_state(
        classification=None, previous=None,
        config={"conversation": {"user_state_model": {"enabled": False}}},
        now=NOW,
    )
    assert payload is None
    assert transitioned is False


def test_first_turn_initialises_to_default():
    cls = UserStateClassification(id="fog", confidence=0.9)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=None, config=CONFIG, now=NOW,
    )
    assert payload is not None
    assert payload["id"] == "fog"
    assert payload["previous_id"] is None
    assert payload["turn_count"] == 1
    assert payload["confidence"] == 0.9
    assert transitioned is False  # initialisation is not a transition


def test_sticky_increments_turn_count():
    previous = {
        "id": "fog", "confidence": 0.8, "previous_id": None,
        "turn_count": 2, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    cls = UserStateClassification(id="fog", confidence=0.75)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=previous, config=CONFIG, now=NOW,
    )
    assert payload["id"] == "fog"
    assert payload["turn_count"] == 3
    assert payload["previous_id"] is None  # unchanged
    assert payload["updated_at"] == previous["updated_at"]  # unchanged on sticky
    assert transitioned is False


def test_transition_builds_fresh_payload():
    previous = {
        "id": "fog", "confidence": 0.8, "previous_id": None,
        "turn_count": 3, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    cls = UserStateClassification(id="orientation", confidence=0.85)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=previous, config=CONFIG, now=NOW,
    )
    assert payload["id"] == "orientation"
    assert payload["previous_id"] == "fog"
    assert payload["turn_count"] == 1
    assert payload["confidence"] == 0.85
    assert payload["updated_at"] == NOW.isoformat()
    assert transitioned is True


def test_classification_none_with_previous_keeps_previous():
    previous = {
        "id": "orientation", "confidence": 0.7, "previous_id": "fog",
        "turn_count": 2, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    payload, transitioned = resolve_user_state(
        classification=None, previous=previous, config=CONFIG, now=NOW,
    )
    # NLU failure path: classification is None but model is enabled —
    # retain previous state, increment turn_count, no transition event.
    assert payload["id"] == "orientation"
    assert payload["turn_count"] == 3
    assert transitioned is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_user_state_resolver.py -v`
Expected: all FAILED with `ImportError`.

- [ ] **Step 3: Implement the resolver**

Create `agent_core/src/preprocessing/user_state_resolver.py`:

```python
"""
agent_core/src/preprocessing/user_state_resolver.py

Pure helper that resolves the post-NLU user-state payload for the turn,
given the classification from NLU and the previous payload from session state.

Called by the orchestrator after NLU returns. No I/O. No logging.
The orchestrator is responsible for persisting the payload, emitting
observability events, and passing the guidance text to ManagerAgent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.models import UserStateClassification


def resolve_user_state(
    *,
    classification: UserStateClassification | None,
    previous: dict | None,
    config: dict,
    now: datetime,
) -> tuple[Optional[dict], bool]:
    """Derive the new session payload for user_state.

    Args:
        classification: NLU output. None when the model is disabled OR when
                        NLU failed (in which case we keep previous state).
        previous:       The payload from session state at turn start, if any.
        config:         Full agent_core config dict.
        now:            Current UTC timestamp (injected for testability).

    Returns:
        Tuple of (new_payload, transitioned).
        new_payload is None only when the user-state model is disabled.
        transitioned is True only on an actual id change from previous to new.
    """
    usm = (config or {}).get("conversation", {}).get("user_state_model", {}) or {}
    if not usm.get("enabled", False):
        return None, False

    default_state = usm.get("default_state", "")

    # NLU failed or returned nothing — hold the previous state if any.
    if classification is None:
        if previous is None:
            # Very first turn and classification failed — initialise to default.
            return _initial_payload(default_state, 0.0, now), False
        return _sticky_payload(previous), False

    new_id = classification.id
    new_conf = classification.confidence

    if previous is None:
        # First turn of the session — initialise, not a transition.
        return _initial_payload(new_id or default_state, new_conf, now), False

    previous_id = previous.get("id", "")
    if new_id == previous_id or not new_id:
        return _sticky_payload(previous, new_conf), False

    # Real transition.
    return {
        "id": new_id,
        "confidence": new_conf,
        "updated_at": now.isoformat(),
        "previous_id": previous_id,
        "turn_count": 1,
    }, True


def _initial_payload(state_id: str, confidence: float, now: datetime) -> dict:
    return {
        "id": state_id,
        "confidence": confidence,
        "updated_at": now.isoformat(),
        "previous_id": None,
        "turn_count": 1,
    }


def _sticky_payload(previous: dict, new_confidence: float | None = None) -> dict:
    """Retain previous state id and timestamps; bump turn_count."""
    return {
        "id": previous.get("id", ""),
        "confidence": (
            new_confidence if new_confidence is not None
            else float(previous.get("confidence", 0.0))
        ),
        "updated_at": previous.get("updated_at", ""),
        "previous_id": previous.get("previous_id"),
        "turn_count": int(previous.get("turn_count", 0)) + 1,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_user_state_resolver.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/preprocessing/user_state_resolver.py agent_core/tests/test_user_state_resolver.py
git commit -m "feat(agent-core): add user_state_resolver helper with sticky/transition rules (GH-139)"
```

---

## Task 6: ManagerAgent.build_system_prompt renders user_state_guidance

**Files:**
- Modify: `agent_core/src/manager_agent.py`
- Test: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Write failing tests**

Append to `agent_core/tests/test_manager_agent.py`:

```python
def test_build_system_prompt_user_state_guidance_none_no_section():
    agent = _fresh_manager_agent()  # use whatever fixture exists in the file
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance=None,
    )
    assert "Current user state guidance" not in result


def test_build_system_prompt_user_state_guidance_empty_no_section():
    agent = _fresh_manager_agent()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="",
    )
    assert "Current user state guidance" not in result


def test_build_system_prompt_user_state_guidance_rendered():
    agent = _fresh_manager_agent()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="Orient gently. Surface 2-3 directions.",
    )
    assert "## Current user state guidance" in result
    assert "Orient gently. Surface 2-3 directions." in result
    # Section must sit between subagent prompt and any guardrail section
    subagent_idx = result.index("B")
    state_idx = result.index("## Current user state guidance")
    assert state_idx > subagent_idx


def test_build_system_prompt_user_state_guidance_before_guardrails():
    agent = _fresh_manager_agent()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="UG",
        guardrail_constraints={"prompt_constraints": ["C1"], "required_disclosures": []},
    )
    state_idx = result.index("## Current user state guidance")
    guardrail_idx = result.index("## Guardrail Constraints")
    assert state_idx < guardrail_idx
```

If `_fresh_manager_agent` does not exist in the test file, replace with the existing pattern used in `test_build_system_prompt_includes_persona` (look at how that fixture is built around line 242 of the test file and mirror it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -v -k user_state_guidance`
Expected: 4 FAILED.

- [ ] **Step 3: Add kwarg + render logic**

Edit `agent_core/src/manager_agent.py`. Change the `build_system_prompt` signature to add the new kwarg as the final parameter:

```python
    def build_system_prompt(
        self,
        agent_system_prompt: str,
        subagent_system_prompt: str,
        detected_language: str,
        channel: str,
        profile: dict,
        channel_config: dict | None = None,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
        user_state_guidance: str | None = None,
    ) -> str:
```

Add to the docstring `Args:` block:

```
            user_state_guidance:   Optional — when non-empty, rendered as a
                                   "## Current user state guidance" section
                                   between the subagent prompt and the
                                   guardrail constraints. Orchestrator supplies
                                   the text from conversation.user_state_model
                                   based on the current user_state payload.
                                   Empty/None renders no section.
```

In the body, after the existing `if subagent_system_prompt: parts.append(...)` and BEFORE the `if guardrail_constraints:` block, insert:

```python
        if user_state_guidance:
            parts.append(
                "## Current user state guidance\n" + user_state_guidance.strip()
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent-core): inject user_state_guidance section into system prompt (GH-139)"
```

---

## Task 7: Orchestrator — init caches + wire NLU + ManagerAgent

**Files:**
- Modify: `agent_core/src/orchestrator.py`
- Test: `agent_core/tests/test_orchestrator.py`

This task wires together the new pieces in the two turn paths (sync `process_turn` and streaming `stream_turn`). The wiring is additive and conservative: existing domains without `user_state_model.enabled=true` get unchanged behaviour because `nlu_result.user_state` will be `None`, the resolver returns `(None, False)`, and `user_state_guidance` stays `None`.

- [ ] **Step 1: Cache guidance lookup at AgentCore init**

Edit `agent_core/src/orchestrator.py`. Find the `__init__` body (around line 134 where `self._nlu_processor = NLUProcessor(self._config)` is set). After that line, add:

```python
        # User-state model (GH-139) — cached lookup for per-turn guidance injection.
        usm = (self._config or {}).get("conversation", {}).get("user_state_model", {}) or {}
        self._user_state_enabled: bool = bool(usm.get("enabled", False))
        self._user_state_guidance_by_id: dict[str, str] = {
            (s.get("id", "")): (s.get("guidance", "") or "")
            for s in (usm.get("states") or [])
            if s.get("id")
        } if self._user_state_enabled else {}
        self._user_state_default: str = usm.get("default_state", "") if self._user_state_enabled else ""
```

- [ ] **Step 2: Pass `previous_user_state` to NLU in `process_turn`**

Find the `nlu_result = self._nlu_processor.process(...)` call in `process_turn` (around line 445). BEFORE that call, add:

```python
        previous_user_state_payload: dict | None = None
        previous_user_state_id: str | None = None
        if self._user_state_enabled:
            maybe = bundle.session.get("user_state")
            if isinstance(maybe, dict):
                previous_user_state_payload = maybe
                previous_user_state_id = maybe.get("id")
            if previous_user_state_id is None:
                previous_user_state_id = self._user_state_default
```

Add `previous_user_state=previous_user_state_id,` as a new kwarg to the `self._nlu_processor.process(...)` call.

- [ ] **Step 3: Resolve post-NLU payload and write it back**

Immediately after the `nlu_result = ...` block and its logging, add:

```python
        from src.preprocessing.user_state_resolver import resolve_user_state
        from datetime import datetime, timezone

        new_user_state_payload: dict | None = None
        user_state_transitioned: bool = False
        user_state_guidance_text: str | None = None
        if self._user_state_enabled:
            new_user_state_payload, user_state_transitioned = resolve_user_state(
                classification=nlu_result.user_state,
                previous=previous_user_state_payload,
                config=self._config,
                now=datetime.now(timezone.utc),
            )
            if new_user_state_payload is not None:
                # Piggy-back on the per-turn session write — same scope, same call.
                self._write_memory_sync(
                    session_id, user_id, "session", "user_state", new_user_state_payload,
                )
                bundle.session["user_state"] = new_user_state_payload
                user_state_guidance_text = self._user_state_guidance_by_id.get(
                    new_user_state_payload["id"], ""
                ) or None

                # OTel span attributes — available on the active span via the
                # _span argument already threaded through _process_turn_inner.
                try:
                    _span.set_attribute("user_state.enabled", True)
                    _span.set_attribute(
                        "user_state.previous", previous_user_state_id or ""
                    )
                    _span.set_attribute(
                        "user_state.current", new_user_state_payload["id"]
                    )
                    _span.set_attribute("user_state.transitioned", user_state_transitioned)
                    _span.set_attribute(
                        "user_state.confidence", float(new_user_state_payload["confidence"])
                    )
                    _span.set_attribute(
                        "user_state.turn_count", int(new_user_state_payload["turn_count"])
                    )
                except Exception as _otel_err:
                    logger.warning(
                        "orchestrator.user_state_otel_attr_failed",
                        extra={
                            "operation": "orchestrator.user_state",
                            "status": "skipped",
                            "error": f"{type(_otel_err).__name__}: {_otel_err}",
                        },
                    )

        logger.info(
            "user_state.resolved",
            extra={
                "operation": "orchestrator.resolve_user_state",
                "status": "success",
                "transitioned": user_state_transitioned,
                "state_id": (
                    new_user_state_payload["id"] if new_user_state_payload else None
                ),
                "previous_state_id": previous_user_state_id,
                "latency_ms": 0,
            },
        )
```

The `_span` variable is already in scope in `_process_turn_inner` (per the existing OTel instrumentation at line 185). If this block lives in a helper further down the call stack that does not have `_span`, thread `_span` through as a parameter.

- [ ] **Step 4: Pass `user_state_guidance` to `build_system_prompt`**

Find the `system = self._manager_agent.build_system_prompt(...)` call (line 699). Append one kwarg:

```python
        system = self._manager_agent.build_system_prompt(
            agent_system_prompt=self._workflow.agent_system_prompt,
            subagent_system_prompt=next_subagent.system_prompt,
            detected_language=final_language,
            channel=turn_input.channel,
            profile=profile_context,
            channel_config=channel_config,
            is_resumption=is_resumption,
            guardrail_constraints=guardrail_constraints,
            user_state_guidance=user_state_guidance_text,
        )
```

- [ ] **Step 5: Mirror for `stream_turn`**

Repeat Step 2–Step 4 for the streaming path. Find the second `nlu_result = self._nlu_processor.process(...)` call (around line 2057) and the second `build_system_prompt(...)` call (around line 2201). Same shape; thread `_span` if present (stream_turn should also have an active span — confirm when editing).

- [ ] **Step 6: Write the Obs Layer event on transition**

Find where turn events are emitted to the Observability Layer (around line 1579, `record_audit_turn` and similar). Add one conditional sibling emit immediately after the existing `record_audit_turn` call path:

```python
        if self._user_state_enabled and user_state_transitioned and new_user_state_payload:
            try:
                self._learning.emit_event(
                    event_type="user_state_transition",
                    payload={
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "timestamp_ms": int(time.time() * 1000),
                        "from_state": previous_user_state_id,
                        "to_state": new_user_state_payload["id"],
                        "confidence": new_user_state_payload["confidence"],
                        "trigger_intent": nlu_result.intent,
                        "turns_in_previous_state": (
                            int((previous_user_state_payload or {}).get("turn_count", 0))
                            if previous_user_state_payload else 0
                        ),
                    },
                )
            except Exception as _evt_err:
                logger.warning(
                    "orchestrator.user_state_event_emit_failed",
                    extra={
                        "operation": "orchestrator.emit_user_state_transition",
                        "status": "skipped",
                        "error": f"{type(_evt_err).__name__}: {_evt_err}",
                    },
                )
```

**Confirm the Observability interface method name before writing this block.** Look at `agent_core/src/interfaces/observability_layer.py`. If the public method is `emit_event`, keep the above. If it is `emit_turn_event` or similar, match the signature exactly. Do NOT invent a method that doesn't exist.

If a matching emit method does not exist, add one to `ObservabilityLayerBase` following the existing pattern (look at `record_audit_turn`) and implement in the production client. That becomes a sub-task inside this task; add it as two more TDD cycles (one for the interface test, one for the client test) before invoking it in the orchestrator.

- [ ] **Step 7: Add an integration-ish orchestrator test**

Append to `agent_core/tests/test_orchestrator.py` (use existing test scaffolding — mock LLM, stub memory, stub learning):

```python
def test_process_turn_writes_user_state_on_transition(
    orchestrator_with_user_state_enabled,   # fixture: config has 2 states
    mock_llm_returning_state,               # fixture: LLM returns orientation with conf 0.85
    spy_memory, spy_learning,
):
    orchestrator_with_user_state_enabled.process_turn(
        _make_turn_input(user_message="kitna pay hai", session_id="s1"),
    )
    # Memory write includes user_state payload
    writes = [w for w in spy_memory.writes if w["key"] == "user_state"]
    assert len(writes) == 1
    assert writes[0]["value"]["id"] == "orientation"
    # Obs Layer event emitted exactly once with event_type=user_state_transition
    events = [e for e in spy_learning.events if e["event_type"] == "user_state_transition"]
    assert len(events) == 1
    assert events[0]["payload"]["to_state"] == "orientation"


def test_process_turn_user_state_disabled_is_noop(
    orchestrator_with_user_state_disabled, spy_memory, spy_learning,
):
    orchestrator_with_user_state_disabled.process_turn(
        _make_turn_input(user_message="hello", session_id="s1"),
    )
    assert not any(w["key"] == "user_state" for w in spy_memory.writes)
    assert not any(
        e["event_type"] == "user_state_transition" for e in spy_learning.events
    )
```

If the named fixtures don't yet exist, add them to the test file's `conftest.py` or top-of-file helpers following the pattern used by existing orchestrator tests. Do not skip these — they prove the end-to-end wiring.

- [ ] **Step 8: Run the full orchestrator test suite**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v`
Expected: all existing tests still pass + 2 new tests PASS.

- [ ] **Step 9: Run the full agent_core suite**

Run: `cd agent_core && uv run pytest`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "feat(agent-core): wire user_state resolver, memory write, OTel attrs, Obs Layer event (GH-139)"
```

---

## Task 8: Dev-kit PHASES list + accumulator acceptance

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Test: `dev-kit/tests/test_accumulator.py`

- [ ] **Step 1: Write failing test**

Append to `dev-kit/tests/test_accumulator.py`:

```python
from dev_kit.agent.accumulator import PHASES


def test_user_state_phase_between_memory_and_trust():
    assert "user_state" in PHASES
    assert PHASES.index("user_state") == PHASES.index("memory") + 1
    assert PHASES.index("user_state") == PHASES.index("trust") - 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dev-kit && uv run pytest tests/test_accumulator.py -v -k user_state`
Expected: FAILED (assertion or value error).

- [ ] **Step 3: Insert the phase**

Edit `dev-kit/dev_kit/agent/accumulator.py`. Change the `PHASES` list:

```python
PHASES: list[str] = [
    "overview",
    "language",
    "knowledge",
    "memory",
    "user_state",
    "trust",
    "tools",
    "workflow",
    "observability",
    "reach",
    "review",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dev-kit && uv run pytest tests/test_accumulator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/accumulator.py dev-kit/tests/test_accumulator.py
git commit -m "feat(dev-kit): add user_state phase between memory and trust in PHASES (GH-139)"
```

---

## Task 9: Dev-kit phase prompt for user_state

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Test: `dev-kit/tests/test_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `dev-kit/tests/test_phases.py` (create the file if it doesn't exist, using the pattern of existing dev-kit tests):

```python
from dev_kit.agent.prompts.phases import get_phase_addition


def test_user_state_phase_returns_non_empty():
    text = get_phase_addition("user_state")
    assert text
    assert "user_state_model" in text
    assert "Conversational" in text  # mentions the agent type it's for


def test_user_state_phase_mentions_schema_fields():
    text = get_phase_addition("user_state")
    # Must reference the key schema fields so the LLM fills the right keys
    assert "default_state" in text
    assert "states" in text
    assert "signals" in text
    assert "guidance" in text


def test_user_state_phase_mentions_threshold_location():
    text = get_phase_addition("user_state")
    assert "user_state_confidence_threshold" in text
    assert "preprocessing.nlu_processor" in text


def test_overview_phase_lists_user_state_in_sequence():
    text = get_phase_addition("overview")
    assert "user_state" in text
    # Ensure it appears after memory in the sequence text
    assert text.index("memory") < text.index("user_state") < text.index("trust")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dev-kit && uv run pytest tests/test_phases.py -v`
Expected: FAILED.

- [ ] **Step 3: Implement the phase branch**

Edit `dev-kit/dev_kit/agent/prompts/phases.py`. After the `memory` branch and before the `trust` branch (around line 166), insert:

```python
    if phase == "user_state":
        return (
            "## User state phase — valid fields\n\n"
            "This phase is optional and applies to **Conversational** agents only "
            "(per the agent-type selector landing in issue #137). Transactional, "
            "Informational, and Agentic agents should call set_phase('trust') to "
            "skip this phase.\n\n"
            "For Conversational agents (e.g. KKB), you define a user-state model "
            "that describes the user's mental journey — what states they pass "
            "through emotionally and cognitively, what signals indicate each "
            "state, and how the agent should behave in each.\n\n"
            "Use `update_config` with block=`agent_core`, section=`conversation`, "
            "key=`user_state_model`. Schema:\n\n"
            "```yaml\n"
            "conversation:\n"
            "  user_state_model:\n"
            "    enabled: true                # set to true to activate\n"
            "    default_state: \"\"            # required — must match one of the state ids\n"
            "    states:                      # required — non-empty list\n"
            "      - id: \"\"                  # unique snake_case id, e.g. fog\n"
            "        signals: []              # natural-language phrases users say in this state\n"
            "        guidance: \"\"             # required — behaviour text, e.g. 'Orient gently.'\n"
            "```\n\n"
            "Elicit from the domain expert:\n"
            "- Does the agent need to distinguish user mental states? If no → call "
            "set_phase('trust') to skip.\n"
            "- List 2-5 states with short ids (e.g. fog, orientation, evaluation, "
            "commitment, follow-through for KKB).\n"
            "- For each: 2-4 natural-language signals (phrases users say) and 1-3 "
            "sentences of behavioural guidance for the agent.\n"
            "- Which state is the default for a fresh caller?\n\n"
            "Also set `preprocessing.nlu_processor.user_state_confidence_threshold` "
            "if the default (0.4) is not suitable. Use a separate `update_config` "
            "call with block=`agent_core`, section=`preprocessing.nlu_processor`.\n\n"
            "When the model is declared (or the user opts to skip), call "
            "`set_phase('trust')`."
        )
```

In the `overview` branch, update the required sequence text to include `user_state`. Find the block that lists the 10-phase sequence (around line 82). Change it to:

```python
            "**Required 11-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. overview  — understand the use case (current phase)\n"
            "2. language  — LLM models, language normalisation, NLU intents/entities\n"
            "3. knowledge — RAG knowledge base, persona, document sources\n"
            "4. memory    — session state fields, persistent graph, consent mode\n"
            "5. user_state — user mental-state model (Conversational agents only; skip otherwise)\n"
            "6. trust     — blocked phrases, escalation topics, safety guardrails\n"
            "7. tools     — external API / MCP tools (or confirm none needed)\n"
            "8. workflow  — subagent state machine, routing rules\n"
            "9. observability — outcome lifecycle states, metrics, domain name\n"
            "10. reach    — web UI branding (app name, icon, tagline)\n"
            "11. review   — validate, fix missing fields, finalize all blocks\n\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dev-kit && uv run pytest tests/test_phases.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full dev-kit suite**

Run: `cd dev-kit && uv run pytest`
Expected: all PASS. If any phase-related existing test relies on the 10-phase count, update it to 11.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py dev-kit/tests/test_phases.py
git commit -m "feat(dev-kit): add user_state phase prompt + update sequence in overview (GH-139)"
```

---

## Task 10: Backwards-compat smoke — existing domains still boot

**Files:**
- Test: `agent_core/tests/test_backwards_compat.py` (new), or extend `test_main.py`

- [ ] **Step 1: Write smoke test**

Create `agent_core/tests/test_backwards_compat_user_state.py`:

```python
"""
GH-139 backwards-compatibility smoke.

Verifies that agent_core domain configs that do NOT declare
conversation.user_state_model continue to start cleanly — no
ConfigurationError, no changed behaviour.
"""
import yaml
from pathlib import Path

from src.preprocessing.nlu_processor import NLUProcessor


def _load_merged_domain_config(domain: str) -> dict:
    """Mimic the deep-merge the runtime does at startup."""
    repo_root = Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((repo_root / "dev-kit" / "dpg" / "agent_core.yaml").read_text()) or {}
    dom = yaml.safe_load(
        (repo_root / "dev-kit" / "configs" / domain / "agent_core.yaml").read_text()
    ) or {}
    # Shallow merge is enough for this check — the key paths we care about
    # (preprocessing.nlu_processor, conversation) live at the top level.
    merged = {**dpg}
    for k, v in dom.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def test_kkb_config_boots_without_user_state_model():
    cfg = _load_merged_domain_config("kkb")
    # KKB currently does not declare user_state_model — must still construct.
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is False


def test_farmer_friendly_boots():
    cfg = _load_merged_domain_config("farmer-friendly")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is False


def test_obsrv_docs_assistant_boots():
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is False
```

- [ ] **Step 2: Run smoke**

Run: `cd agent_core && uv run pytest tests/test_backwards_compat_user_state.py -v`
Expected: 3 PASSED.

If any domain fails because its `preprocessing.nlu_processor` section is missing fields required by `NLUProcessor.__init__`, that is a pre-existing issue unrelated to GH-139 — document as a separate bug, not blocking.

- [ ] **Step 3: Commit**

```bash
git add agent_core/tests/test_backwards_compat_user_state.py
git commit -m "test(agent-core): backwards-compat smoke for domains without user_state_model (GH-139)"
```

---

## Task 11: Minimal self-documenting example in one domain config

**Files:**
- Modify: ONE of `dev-kit/configs/farmer-friendly/agent_core.yaml` OR `dev-kit/configs/obsrv-docs-assistant/agent_core.yaml`. Pick `farmer-friendly` (smaller, lower stakes than obsrv-docs-assistant).
- Test: extend `test_backwards_compat_user_state.py`

This adds a minimal 2-state example so readers have a working reference. The full 5-state KKB model is NOT part of this plan — it lands with #137.

- [ ] **Step 1: Write failing test**

In `agent_core/tests/test_backwards_compat_user_state.py`, replace the `test_farmer_friendly_boots` function with:

```python
def test_farmer_friendly_boots_with_example_user_state():
    cfg = _load_merged_domain_config("farmer-friendly")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is True
    assert p._user_state_default != ""
    assert len(p._user_states) >= 2
```

- [ ] **Step 2: Run test — it fails**

Run: `cd agent_core && uv run pytest tests/test_backwards_compat_user_state.py::test_farmer_friendly_boots_with_example_user_state -v`
Expected: FAILED (enabled is False).

- [ ] **Step 3: Add the example to `farmer-friendly`**

Edit `dev-kit/configs/farmer-friendly/agent_core.yaml`. Under `conversation:`, append:

```yaml
  user_state_model:
    enabled: true
    default_state: exploring
    states:
      - id: exploring
        signals:
          - "vague query"
          - "not sure what to ask"
        guidance: |
          Offer 2-3 directions. Avoid giving a single-answer recommendation.
          Ask one clarifying question at most.
      - id: ready
        signals:
          - "user picks a crop or scheme"
          - "user says 'apply karo'"
        guidance: |
          Confirm the user's choice explicitly and walk through the next step.
          Do not introduce new options.
```

- [ ] **Step 4: Run test — it passes**

Run: `cd agent_core && uv run pytest tests/test_backwards_compat_user_state.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/configs/farmer-friendly/agent_core.yaml agent_core/tests/test_backwards_compat_user_state.py
git commit -m "feat(dev-kit): add minimal 2-state user_state_model example in farmer-friendly (GH-139)"
```

---

## Task 12: ARCHITECTURE.md update

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: Add the paragraph**

Edit `ARCHITECTURE.md`. Find the Agent Core section. After the paragraph describing system state (subagents, routing), add:

```markdown
**User-state model (optional, Conversational agents only).** Orthogonal to the
system state described above, Conversational domains may declare a
`conversation.user_state_model` block with a list of states (id, signals,
guidance). The NLU Processor classifies the user's current mental state
alongside intent on the same LLM call. The orchestrator resolves the new
state via `src/preprocessing/user_state_resolver.py` — sticky on low
confidence, transition on confident id change. The active state's guidance
text is injected into the main LLM system prompt by
`ManagerAgent.build_system_prompt()`. The state payload piggy-backs on the
existing per-turn Memory Layer session write; transitions emit a
`user_state_transition` event to the Observability Layer and set span
attributes on the turn OTel span. Feature is off by default; domains that do
not declare the block are unaffected.
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: document user_state_model in ARCHITECTURE.md (GH-139)"
```

---

## Task 13: Coverage gate

**Files:**
- None

- [ ] **Step 1: Measure coverage on touched files**

Run:

```bash
cd agent_core && uv run pytest --cov=src/preprocessing/nlu_processor \
  --cov=src/preprocessing/user_state_resolver --cov=src/manager_agent \
  --cov-report=term-missing
```

Expected: ≥70% line coverage on each file.

- [ ] **Step 2: Fill gaps if any file is below 70%**

If coverage is below 70% on any file, add targeted tests for the uncovered branches. Re-run. Commit:

```bash
git add agent_core/tests/
git commit -m "test(agent-core): cover remaining branches to hit 70% on user_state surface (GH-139)"
```

If coverage is already ≥70%, skip this step.

---

## Task 14: Final verification

**Files:**
- None

- [ ] **Step 1: Run all test suites**

```bash
cd agent_core && uv run pytest
cd ../dev-kit && uv run pytest
```

Expected: all PASS.

- [ ] **Step 2: Docker smoke**

```bash
cd automation/docker
docker compose -f docker-compose.dev.yml config   # syntax check
```

Expected: no YAML errors. Do NOT bring the full stack up — that's a reviewer/CI concern.

- [ ] **Step 3: Git log review**

```bash
git log --oneline main..HEAD
```

Expected: ~13 commits on branch `GH-139-user-state`, each mapping to one task, each following Conventional Commits.

- [ ] **Step 4: Push**

```bash
git push -u origin GH-139-user-state
```

---

## Spec coverage self-check

- Schema (`conversation.user_state_model`, `user_state_confidence_threshold`) — Tasks 2, 3.
- Loader validation (all six rules from spec) — Task 3.
- `UserStateClassification` + `NLUResult.user_state` — Task 1.
- NLU prompt extension + parsing + sticky fallback — Task 4.
- `resolve_user_state` helper with all four resolver rules — Task 5.
- `ManagerAgent.build_system_prompt` kwarg + section rendering — Task 6.
- Orchestrator turn-flow integration (read, pass, resolve, write, span attrs, obs event) — Task 7.
- Dev-kit phase insertion — Tasks 8, 9.
- Backwards compatibility (existing domains unaffected, `enabled=false` default) — Task 10.
- Self-documenting example in a domain config — Task 11.
- `ARCHITECTURE.md` paragraph — Task 12.
- Coverage ≥70% on touched files — Task 13.
- Final verification + push — Task 14.

Spec item not explicitly tested: OTel span attributes. Task 7's integration test verifies the Obs Layer event fires but does not inspect span attrs (OTel span assertion in pytest is noisy). Acceptable — the code path is straightforward and a span-inspection test can be added later if span attrs regress. Flagged as a minor gap, not blocking.

## Placeholder scan

No TBDs, TODOs, or vague steps in any task. Every code step shows the actual code. Every command shows the actual invocation.

## Type consistency

- `UserStateClassification` used identically across Tasks 1, 4, 5, 7.
- Resolver signature `resolve_user_state(classification=..., previous=..., config=..., now=...)` identical between Tasks 5 and 7.
- `build_system_prompt(... user_state_guidance=...)` identical between Tasks 6 and 7.
- Payload keys (`id`, `confidence`, `updated_at`, `previous_id`, `turn_count`) consistent between Tasks 5 and 7.
- `NLUProcessor.process(..., previous_user_state=...)` kwarg identical between Tasks 4 and 7.

---
