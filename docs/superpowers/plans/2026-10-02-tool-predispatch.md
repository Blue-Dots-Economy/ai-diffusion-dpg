# Spec E: Tool Pre-dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the NLU result and the session fully determine a tool call, run it before the main LLM. The main LLM then speaks the result in one call instead of two.

**Architecture:**
- Pure rule resolution in `agent_core/src/predispatch/rules.py`.
- A guarded runner in `predispatch/runner.py`.
- One shared guard and cache decision in `agent_core/src/tool_guard.py`, used by every tool-execution site.
- Orchestrator wiring after routing on both turn paths. It injects a synthetic tool exchange and drops the tool from the speaking call.
- The config lives on subagents: `predispatch`, plus top-level `predispatch_tables` and `agent.predispatch_timeout_ms`. It is mirrored in the dev-kit.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest + pytest-asyncio, `uv`, and the OpenTelemetry metrics API (already a dependency).

**Spec:** `docs/superpowers/specs/2026-10-02-tool-predispatch-design.md`.

## Global Constraints

- Work only in `/Users/aniket/Documents/github/aniketsaki/ai-diffusion-spec-e` on branch `spec/tool-predispatch`, which is stacked on `spec/main-llm-context` 5aa39ec. Commit there; never push.
- Run tests per module:
  - `cd agent_core && uv run pytest -q`
  - `cd dev-kit && uv run pytest -q`
  - `cd reach_layer/bridge && uv run --with pytest python -m pytest -q tests/test_blue_dots_config.py`
- Baselines, which are not yours to fix:
  - agent_core: 1498 passed, 1 skipped, and 1 UserWarning (`OutputFormat.schema`);
  - dev-kit: 1 failure, `test_dpg_yaml_validates[reach_layer]`;
  - reach_layer bridge: 1 failure, `test_existing_channels_are_untouched`.
- The main LLM still writes every reply. Spec E adds no template replies and no added model calls.
- Read tools ship enabled. Every write rule ships `enabled: false`. A write/identity rule without an explicit `enabled` is rejected at startup.
- A pre-dispatch never changes this turn's routing. It never raises into the turn. A rule that fails to resolve falls back to today's path.
- The `apply_job` and `save_profile` per-turn cap of 1 must hold across a pre-dispatched call and any model call in the same turn.
- Logs never carry caller text or argument values. Log argument keys, the tool name, the outcome and timings only.
- Sync and stream stay in parity: same rule, same messages shape, same tool list and same outcome for the same session.
- Runtime ↔ dev-kit sync (`.claude/rules/runtime-devkit-sync.md`) covers the runtime schema, the domain mirror, the flat schema, FIELD_RULES, dpg defaults, the dpg schema and the cross-block checks.
- The runtime `agent_core/src/schema/config.py` imports only from `pydantic`, `enum`, `typing` and `__future__`. The orchestrator reads the raw config dict.
- Use Google-style docstrings. Keep agent_core coverage ≥ 70%. The repo is public, so commits carry no vulnerability details. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec clarifications (rulings made while planning)

1. **The shared helper (spec §5.1) is the guard and cache decision.** `tool_guard.check_tool_call(...) -> GuardVerdict` holds the "refuse / cache-hit / go" logic for:
   - sync `ManagerAgent.run_turn`;
   - stream round 1;
   - the stream nested rounds;
   - pre-dispatch.

   The live execute, shape, map, cache and persist steps stay at each site. Sync writes session values after `run_turn` and stream writes them inline, so merging those steps would change ordering.
2. **Consent.** The guard takes `consent_ok: bool | None`, where None means not required.
   - Stream sites and pre-dispatch pass the real value from `trust.check_consent`.
   - Sync `run_turn` passes None, because its `_execute_tool` already gates consent and that gate stays.
3. **Sync budget.** The sync pre-dispatch relies on the gateway's own per-tool timeout; Python cannot abort a blocking call. The stream path enforces `agent.predispatch_timeout_ms` with `asyncio.wait_for`.
4. **Placeholder rejection (spec §7)** is a per-argument `reject: <table name>` field. A value that matches any table entry case-insensitively makes the rule `skipped_invalid_arg`.
5. **Argument schema.** Validation is against the tool definition the model sees: `ToolRegistry.get_definitions_for([tool])[0]["input_schema"]`, using `properties` and `required`.
   - An argument key not in `properties` is a startup error. This check is raised from `AgentCore.__init__`, because `MergedConfig` cannot see the gateway tool list.
   - The dev-kit check runs against the merged `action_gateway` tool params with `source: agent`.
6. **`normalise`.**
   - On a `template` it is a dict from a placeholder's first key name to a table name or an inline map.
   - On `from: session` / `literal` it is a table name, a built-in (`title`, `lower`) or an inline map.
7. **Empty values** are `None`, `""`, `[]`, `0`, `0.0` and `"0"`, the same set `_tool_session_values` drops. Booleans are never empty.
8. **`llm_calls`.**
   - Stream counts each `self._llm.stream(` call this turn.
   - Sync uses `self._manager_agent.last_llm_calls`, a new attribute set by `run_turn` to 1 + rounds, plus 1 for the orchestrator's initial `self._llm.call`. Do not double count; see Task 5.

## Review Focus

These are the cases most likely to bite. Each is pinned by a test in the named task.

1. **A pre-dispatched `apply_job` followed by the model also calling `apply_job`.** There must be exactly one gateway call, because the cap is shared and the tool is removed. (Task 5, `test_predispatched_write_is_never_repeated`, using a test config with the rule enabled.)
2. **The caller corrects trade or city in the same turn, then routing enters job_match.** The query uses the corrected value, because NLU writes are applied before pre-dispatch. (Task 5, `test_predispatch_uses_values_written_this_turn`.)
3. **A slow `fetch_jobs` on the stream path.** It times out within `predispatch_timeout_ms`, and the turn proceeds with the tool still offered and two LLM calls. (Task 5, `test_read_timeout_falls_back`.)
4. **A city outside the canonical table, or `stored_location` holding a full address.** The value passes through unchanged rather than blocking. A full-address `stored_location` becomes the city only if Spec D shaping already reduced it; nothing new is invented. (Task 2, `test_normalise_passthrough_when_not_in_table`.)
5. **A fresh `fetch_jobs` already in the cache (re-entering job_match).** It is skipped (`skipped_fresh`), the model speaks from `<known_facts>`, and nothing is re-fetched. (Task 2, `test_unless_fresh_skips`, and Task 5, `test_fresh_cache_no_predispatch`.)

---

### Task 1: Runtime schema and workflow loading

**Files:**
- Modify `agent_core/src/schema/config.py`:
  - `AgentConfig` (around L204);
  - `SubAgent` (around L752);
  - `MergedConfig` (around L966; add a validator after `_check_output_rules`).
- Modify `agent_core/src/workflow_loader.py`:
  - the `SubAgent` dataclass (L115-152);
  - the parse site (around L519-560, next to `pending` / `fixed_opening`).
- Test: `agent_core/tests/test_schema_config.py`, `agent_core/tests/test_workflow_loader.py`.

**Interfaces:**
- Produces Pydantic models (`frozen=True`, `extra="forbid"`):
  - `PredispatchArg(from_: Literal["session","literal"] | None (alias "from"), key: str | None, value: Any, template: str | None, normalise: str | dict | None, reject: str | None)`. Exactly one of `from_` or `template` is set. `from: session` needs `key`; `from: literal` needs a non-None `value`.
  - `PredispatchRule(tool: str, enabled: bool | None = None, on_intent: list[str] = [], when: list[RoutingCondition] = [], unless_fresh: bool = False, args: dict[str, PredispatchArg] = {})`.
- Produces these fields:
  - `SubAgent.predispatch: list[PredispatchRule] = []`;
  - `MergedConfig.predispatch_tables: dict[str, dict[str, str] | list[str]] = {}`;
  - `AgentConfig.predispatch_timeout_ms: int = Field(default=1500, gt=0)`.
- Produces the workflow dataclass field `SubAgent.predispatch: list[dict]`, holding raw rule dicts in config order. The runtime reads dicts.

- [ ] **Step 1: Write the failing tests.** Append them to `tests/test_schema_config.py` and use its valid-config helper (`_valid_config()` or the real name in the file):

```python
import copy

import pytest
from pydantic import ValidationError

from src.schema.config import MergedConfig


def _with_rule(cfg, rule, subagent_index=0):
    cfg["agent_workflow"]["subagents"][subagent_index]["predispatch"] = [rule]
    return cfg


def _read_tool(cfg):
    return cfg["connectors"]["read"][0]["name"]


def test_predispatch_rule_accepted():
    cfg = copy.deepcopy(_valid_config())
    tool = _read_tool(cfg)
    cfg["predispatch_tables"] = {"city_canonical": {"Bangalore": "Bengaluru"}, "name_placeholders": ["unknown"]}
    cfg.setdefault("agent", {})["predispatch_timeout_ms"] = 1200
    _with_rule(cfg, {"tool": tool, "unless_fresh": True,
                     "args": {"query_text": {"template": "{trade|stored_trade} jobs in {location}",
                                             "normalise": {"location": "city_canonical"}}}})
    MergedConfig.validate_full(cfg)


def test_write_rule_needs_explicit_enabled():
    cfg = copy.deepcopy(_valid_config())
    write = cfg["connectors"].setdefault("write", [])
    if not write:
        pytest.skip("valid config fixture has no write connector; add one mirroring an existing read connector")
    _with_rule(cfg, {"tool": write[0]["name"], "args": {"x": {"from": "session", "key": "x"}}})
    with pytest.raises(ValidationError, match="enabled"):
        MergedConfig.validate_full(cfg)


def test_unknown_tool_rejected():
    cfg = _with_rule(copy.deepcopy(_valid_config()), {"tool": "no_such_tool", "args": {}})
    with pytest.raises(ValidationError, match="no_such_tool"):
        MergedConfig.validate_full(cfg)


def test_arg_binding_shapes():
    cfg = copy.deepcopy(_valid_config())
    tool = _read_tool(cfg)
    for bad in ({"from": "session"}, {"from": "literal"}, {"template": "x", "from": "session", "key": "k"}, {}):
        c = _with_rule(copy.deepcopy(cfg), {"tool": tool, "args": {"a": bad}})
        with pytest.raises(ValidationError):
            MergedConfig.validate_full(c)


def test_unknown_normalise_table_rejected():
    cfg = copy.deepcopy(_valid_config())
    _with_rule(cfg, {"tool": _read_tool(cfg), "args": {"a": {"from": "session", "key": "k", "normalise": "nope"}}})
    with pytest.raises(ValidationError, match="nope"):
        MergedConfig.validate_full(cfg)


def test_on_intent_must_be_producible():
    cfg = copy.deepcopy(_valid_config())
    _with_rule(cfg, {"tool": _read_tool(cfg), "on_intent": ["never_made"], "args": {}})
    with pytest.raises(ValidationError, match="never_made"):
        MergedConfig.validate_full(cfg)
```

In `tests/test_workflow_loader.py`, add `test_predispatch_rules_loaded_in_order`. It loads a workflow whose subagent has two `predispatch` rules and asserts that `workflow.subagents[id].predispatch` is the two dicts in order. Mirror the file's existing `pending` / `fixed_opening` loader tests.

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py tests/test_workflow_loader.py -q`
Expected: the new tests FAIL with extra-field errors on `predispatch` / `predispatch_tables`.

- [ ] **Step 3: Implement.** In `config.py`, place this near `SubAgent`. The routing-condition model's real class name may differ; grep `class .*Condition` in the file and use it.

```python
class PredispatchArg(BaseModel):
    """One argument binding for a pre-dispatched tool call (Spec E §3.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    from_: Optional[Literal["session", "literal"]] = Field(default=None, alias="from")
    key: Optional[str] = None
    value: Any = None
    template: Optional[str] = None
    normalise: Optional[Union[str, dict[str, Any]]] = None
    reject: Optional[str] = None

    @model_validator(mode="after")
    def _one_source(self) -> "PredispatchArg":
        if (self.from_ is None) == (self.template is None):
            raise ValueError("predispatch arg needs exactly one of 'from' or 'template'")
        if self.from_ == "session" and not self.key:
            raise ValueError("predispatch arg 'from: session' needs 'key'")
        if self.from_ == "literal" and self.value is None:
            raise ValueError("predispatch arg 'from: literal' needs 'value'")
        if self.template is not None and not self.template.strip():
            raise ValueError("predispatch arg 'template' must be non-empty")
        return self


class PredispatchRule(BaseModel):
    """Run a tool before the main LLM when NLU + session determine it (Spec E §3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1)
    enabled: Optional[bool] = None
    on_intent: list[str] = Field(default_factory=list)
    when: list[RoutingCondition] = Field(default_factory=list)
    unless_fresh: bool = False
    args: dict[str, PredispatchArg] = Field(default_factory=dict)
```

Make sure `Union` and `Any` are imported from `typing`.

Then make these field changes:
- `SubAgent`: `predispatch: list[PredispatchRule] = Field(default_factory=list)`.
- `AgentConfig`: `predispatch_timeout_ms: int = Field(default=1500, gt=0)`.
- `MergedConfig`: `predispatch_tables: dict[str, Union[dict[str, str], list[str]]] = Field(default_factory=dict)`.

Add the validator:

```python
    @model_validator(mode="after")
    def _check_predispatch_rules(self) -> "MergedConfig":
        """Spec E §4: tools declared, writes explicit, normalise/reject tables exist, intents producible."""
        conns = getattr(self, "connectors", None)
        groups = {g: {c.name for c in (getattr(conns, g, None) or [])} for g in ("read", "write", "identity")}
        known = set().union(*groups.values())
        writes = groups["write"] | groups["identity"]
        tables = set(self.predispatch_tables or {})
        builtins = {"title", "lower"}
        nlu = getattr(getattr(self, "preprocessing", None), "nlu_processor", None)
        producible = {r.intent for r in (getattr(nlu, "act_intents", None) or [])} | set(_FRAMEWORK_HANDLED_INTENTS)
        wf = getattr(self, "agent_workflow", None)
        for sa in (getattr(wf, "subagents", None) or []):
            for i, rule in enumerate(sa.predispatch):
                where = f"agent_workflow.subagents[{sa.id}].predispatch[{i}]"
                if rule.tool not in known:
                    raise ValueError(f"{where}: tool '{rule.tool}' is not a declared connector")
                if rule.tool in writes and rule.enabled is None:
                    raise ValueError(f"{where}: '{rule.tool}' is a write tool; set 'enabled' explicitly")
                for intent in rule.on_intent:
                    if intent not in producible:
                        raise ValueError(f"{where}: on_intent '{intent}' is not produced by any act_intents row")
                for name, arg in rule.args.items():
                    names = []
                    if isinstance(arg.normalise, str):
                        names.append(arg.normalise)
                    elif isinstance(arg.normalise, dict):
                        names += [v for v in arg.normalise.values() if isinstance(v, str)]
                    if arg.reject:
                        names.append(arg.reject)
                    for n in names:
                        if n not in tables and n not in builtins:
                            raise ValueError(f"{where}.args.{name}: unknown table '{n}'")
        return self
```

Confirm that `_FRAMEWORK_HANDLED_INTENTS` is defined in this file; Spec C put it here. The attribute paths must match the real `MergedConfig` field names. When a subagent's `tools` list is non-empty, also check `rule.tool in sa.tools` or in the workflow's `global_tools`. If neither list is populated, skip that check.

In `workflow_loader.py`:
- add `predispatch: list[dict] = field(default_factory=list)` to the dataclass;
- populate it at the parse site with `predispatch=[dict(r) for r in (raw.get("predispatch") or []) if isinstance(r, dict)]`.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd agent_core && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/schema/config.py agent_core/src/workflow_loader.py agent_core/tests
git commit -m "feat(agent_core): schema and loading for predispatch rules (Spec E §3-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Rule resolution (pure)

**Files:**
- Create `agent_core/src/predispatch/__init__.py` and `agent_core/src/predispatch/rules.py`.
- Test: `agent_core/tests/predispatch/__init__.py` and `agent_core/tests/predispatch/test_rules.py`.

**Interfaces:**
- Consumes `src.conditions.evaluate_condition(condition, state)`, which reads `.field` / `.operator` / `.value` attributes.
- Produces:
  - `is_empty(value) -> bool`;
  - `resolve_args(rule: dict, session: dict, tables: dict, tool_schema: dict | None) -> tuple[dict | None, str]`, which returns `(args, "")`, `(None, "skipped_missing_arg")` or `(None, "skipped_invalid_arg")`;
  - `Selection`, a frozen dataclass: `tool: str | None`, `args: dict`, `outcome: str | None`, `is_write: bool`;
  - `select(rules: list[dict], *, intent: str, state: dict, session: dict, tables: dict, tool_schemas: dict[str, dict], write_tools: set[str], has_fresh: Callable[[str], bool]) -> Selection`.

- [ ] **Step 1: Write the failing tests.**

```python
# agent_core/tests/predispatch/test_rules.py
from src.predispatch.rules import is_empty, resolve_args, select

TABLES = {"city_canonical": {"Bangalore": "Bengaluru", "Bombay": "Mumbai"}, "name_placeholders": ["unknown", "n/a"]}
JOBS_SCHEMA = {"properties": {"query_text": {"type": "string"}, "offset": {"type": "integer"}}, "required": ["query_text"]}
APPLY_SCHEMA = {"properties": {"profile_item_id": {"type": "string"},
                               "job_item_id": {"type": "string", "format": "uuid"}},
                "required": ["profile_item_id", "job_item_id"]}
PROFILE_SCHEMA = {"properties": {"name": {"type": "string"}, "gender": {"type": "string", "enum": ["Male", "Female", "Other"]},
                                 "experience_years": {"type": "string"}},
                  "required": ["name"]}
JOBS_RULE = {"tool": "fetch_jobs", "unless_fresh": True,
             "args": {"query_text": {"template": "{trade|stored_trade} jobs in {location|stored_location}",
                                     "normalise": {"location": "city_canonical"}}}}
UUID = "3f2b8c1e-9a4d-4e2f-8b1a-0c9d8e7f6a5b"


def test_is_empty():
    assert all(is_empty(v) for v in (None, "", [], 0, 0.0, "0"))
    assert not any(is_empty(v) for v in ("x", 5, False, True, [1]))


def test_template_with_fallback_and_normalise():
    args, why = resolve_args(JOBS_RULE, {"stored_trade": "Welder", "location": "Bangalore"}, TABLES, JOBS_SCHEMA)
    assert (args, why) == ({"query_text": "Welder jobs in Bengaluru"}, "")


def test_normalise_passthrough_when_not_in_table():
    args, _ = resolve_args(JOBS_RULE, {"trade": "Plumber", "location": "Hubballi"}, TABLES, JOBS_SCHEMA)
    assert args == {"query_text": "Plumber jobs in Hubballi"}


def test_missing_required_placeholder_blocks():
    assert resolve_args(JOBS_RULE, {"trade": "Welder"}, TABLES, JOBS_SCHEMA) == (None, "skipped_missing_arg")


def test_session_binding_uuid_validation():
    rule = {"tool": "apply_job", "args": {"profile_item_id": {"from": "session", "key": "profile_item_id"},
                                          "job_item_id": {"from": "session", "key": "selected_job_item_id"}}}
    ok, _ = resolve_args(rule, {"profile_item_id": "p1", "selected_job_item_id": UUID}, TABLES, APPLY_SCHEMA)
    assert ok == {"profile_item_id": "p1", "job_item_id": UUID}
    assert resolve_args(rule, {"profile_item_id": "p1", "selected_job_item_id": "तीसरा"}, TABLES, APPLY_SCHEMA) == \
        (None, "skipped_invalid_arg")
    assert resolve_args(rule, {"selected_job_item_id": UUID}, TABLES, APPLY_SCHEMA) == (None, "skipped_missing_arg")


def test_optional_args_omitted_enum_map_coercion_and_reject():
    rule = {"tool": "save_profile", "args": {
        "name": {"from": "session", "key": "name", "reject": "name_placeholders"},
        "gender": {"from": "session", "key": "gender", "normalise": {"male": "Male", "female": "Female"}},
        "experience_years": {"from": "session", "key": "experience_years"}}}
    ok, _ = resolve_args(rule, {"name": "अजय सिंह", "gender": "male", "experience_years": 3}, TABLES, PROFILE_SCHEMA)
    assert ok == {"name": "अजय सिंह", "gender": "Male", "experience_years": "3"}
    ok2, _ = resolve_args(rule, {"name": "अजय सिंह"}, TABLES, PROFILE_SCHEMA)
    assert ok2 == {"name": "अजय सिंह"}
    assert resolve_args(rule, {"name": "Unknown"}, TABLES, PROFILE_SCHEMA) == (None, "skipped_invalid_arg")
    assert resolve_args(rule, {"name": "x", "gender": "robot"}, TABLES, PROFILE_SCHEMA) == (None, "skipped_invalid_arg")


def test_literal_and_builtin_normalise():
    rule = {"tool": "t", "args": {"a": {"from": "literal", "value": "fixed"},
                                  "b": {"from": "session", "key": "b", "normalise": "title"}}}
    assert resolve_args(rule, {"b": "welder"}, TABLES, None) == ({"a": "fixed", "b": "Welder"}, "")


def _sel(rules, **kw):
    base = dict(intent="any_input", state={}, session={}, tables=TABLES,
                tool_schemas={"fetch_jobs": JOBS_SCHEMA, "apply_job": APPLY_SCHEMA},
                write_tools={"apply_job"}, has_fresh=lambda t: False)
    base.update(kw)
    return select(rules, **base)


def test_select_first_eligible_rule():
    s = _sel([JOBS_RULE], session={"trade": "Welder", "location": "Bengaluru"})
    assert (s.tool, s.args, s.outcome, s.is_write) == ("fetch_jobs", {"query_text": "Welder jobs in Bengaluru"}, "fired", False)


def test_unless_fresh_skips():
    s = _sel([JOBS_RULE], session={"trade": "Welder", "location": "Bengaluru"}, has_fresh=lambda t: t == "fetch_jobs")
    assert (s.tool, s.outcome) == (None, "skipped_fresh")


def test_on_intent_and_when_gate():
    rule = {"tool": "apply_job", "enabled": True, "on_intent": ["apply_now"],
            "when": [{"field": "applications_submitted", "operator": "eq", "value": 0}],
            "args": {"profile_item_id": {"from": "session", "key": "p"}, "job_item_id": {"from": "session", "key": "j"}}}
    sess = {"p": "p1", "j": UUID}
    assert _sel([rule], intent="any_input", session=sess, state={"applications_submitted": 0}).outcome is None
    assert _sel([rule], intent="apply_now", session=sess, state={"applications_submitted": 1}).outcome is None
    s = _sel([rule], intent="apply_now", session=sess, state={"applications_submitted": 0})
    assert (s.tool, s.is_write, s.outcome) == ("apply_job", True, "fired")


def test_disabled_rule_reports_disabled_and_never_fires():
    rule = {"tool": "apply_job", "enabled": False, "on_intent": ["apply_now"],
            "args": {"profile_item_id": {"from": "session", "key": "p"}, "job_item_id": {"from": "session", "key": "j"}}}
    s = _sel([rule], intent="apply_now", session={"p": "p1", "j": UUID})
    assert (s.tool, s.outcome) == (None, "disabled")


def test_no_matching_rule_no_outcome():
    assert _sel([], session={}).outcome is None


def test_select_never_raises_on_bad_rule():
    s = _sel([{"tool": "fetch_jobs", "when": [{"field": None}], "args": "nonsense"}])
    assert s.tool is None and s.outcome == "error"
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd agent_core && uv run pytest tests/predispatch -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# agent_core/src/predispatch/__init__.py
"""Tool pre-dispatch (Spec E): run a determined tool call before the main LLM."""
```

```python
# agent_core/src/predispatch/rules.py
"""
agent_core/src/predispatch/rules.py
Pure resolution of predispatch rules (Spec E §3): bind arguments from session,
literal or template; normalise; validate against the tool's agent schema; pick
the first eligible rule. Never raises. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from src.conditions import evaluate_condition

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*)\}")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MISSING = "skipped_missing_arg"
_INVALID = "skipped_invalid_arg"


class _Invalid(Exception):
    pass


def is_empty(value: Any) -> bool:
    """Seeded-empty values never count as a bound argument (same set as ``_tool_session_values``)."""
    if isinstance(value, bool):
        return False
    return value is None or value == "" or value == [] or value == "0" or (
        isinstance(value, (int, float)) and value == 0)


def _apply_table(value: Any, spec: Any, tables: dict) -> Any:
    if spec is None:
        return value
    if spec == "title":
        return str(value).title()
    if spec == "lower":
        return str(value).lower()
    table = tables.get(spec) if isinstance(spec, str) else spec
    if isinstance(table, dict):
        low = {str(k).lower(): v for k, v in table.items()}
        return low.get(str(value).lower(), value)
    return value


def _bind(arg: dict, session: dict, tables: dict) -> Any:
    """Return the bound value, or None when missing."""
    if "template" in arg:
        norm = arg.get("normalise") if isinstance(arg.get("normalise"), dict) else {}

        def sub(m: re.Match) -> str:
            keys = m.group(1).split("|")
            for k in keys:
                if not is_empty(session.get(k)):
                    return str(_apply_table(session[k], norm.get(keys[0]), tables))
            raise KeyError(keys[0])

        try:
            return _PLACEHOLDER.sub(sub, str(arg["template"])).strip() or None
        except KeyError:
            return None
    if arg.get("from") == "literal":
        return arg.get("value")
    value = session.get(arg.get("key", ""))
    if is_empty(value):
        return None
    return _apply_table(value, arg.get("normalise"), tables)


def _validate(name: str, value: Any, prop: dict, arg: dict, tables: dict) -> Any:
    reject = tables.get(arg.get("reject")) if arg.get("reject") else None
    if isinstance(reject, (list, tuple)) and str(value).strip().lower() in {str(r).lower() for r in reject}:
        raise _Invalid(name)
    typ = prop.get("type")
    if typ == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    elif typ == "integer" and not isinstance(value, int):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        else:
            raise _Invalid(name)
    if "enum" in prop and value not in prop["enum"]:
        raise _Invalid(name)
    if prop.get("format") == "uuid" and not (isinstance(value, str) and _UUID.match(value)):
        raise _Invalid(name)
    return value


def resolve_args(rule: dict, session: dict, tables: dict, tool_schema: dict | None) -> tuple[dict | None, str]:
    """Bind and validate one rule's arguments.

    Args:
        rule: Raw predispatch rule dict.
        session: Session values as written so far this turn.
        tables: ``predispatch_tables``.
        tool_schema: The tool's ``input_schema`` (``properties``/``required``), or None.

    Returns:
        ``(args, "")`` on success, else ``(None, "skipped_missing_arg" | "skipped_invalid_arg")``.
    """
    props = (tool_schema or {}).get("properties") or {}
    required = set((tool_schema or {}).get("required") or []) if tool_schema else None
    out: dict = {}
    for name, arg in (rule.get("args") or {}).items():
        value = _bind(arg, session, tables)
        if value is None or is_empty(value):
            if required is None or name in required:
                return None, _MISSING
            continue
        try:
            out[name] = _validate(name, value, props.get(name) or {}, arg, tables)
        except _Invalid:
            return None, _INVALID
    for name in (required or set()) - set(out):
        return None, _MISSING
    return out, ""


@dataclass(frozen=True)
class Selection:
    """The turn's pre-dispatch decision.

    Attributes:
        tool: Tool to run, or None.
        args: Bound arguments (empty when tool is None).
        outcome: "fired" when a tool was chosen; otherwise the first gated rule's skip reason, or None.
        is_write: Whether ``tool`` is a write/identity tool.
    """

    tool: str | None
    args: dict = field(default_factory=dict)
    outcome: str | None = None
    is_write: bool = False


def _gates_hold(rule: dict, intent: str, state: dict) -> bool:
    on = rule.get("on_intent") or []
    if on and intent not in on:
        return False
    return all(evaluate_condition(SimpleNamespace(**c), state) for c in (rule.get("when") or []))


def select(rules: list[dict], *, intent: str, state: dict, session: dict, tables: dict,
           tool_schemas: dict[str, dict], write_tools: set[str], has_fresh: Callable[[str], bool]) -> Selection:
    """First rule whose gates hold, that is enabled, not fresh, and whose args resolve. Never raises.

    Args:
        rules: The subagent's raw predispatch rules, in order.
        intent: This turn's routing intent.
        state: Routing state (session ∪ profile ∪ NLU-owned) after this turn's writes.
        session: Session values for argument binding.
        tables: ``predispatch_tables``.
        tool_schemas: Tool name → input_schema.
        write_tools: Names of write/identity tools.
        has_fresh: Whether the turn cache already holds a fresh entry for a tool.

    Returns:
        Selection.
    """
    try:
        first_skip: str | None = None
        for rule in rules or []:
            if not _gates_hold(rule, intent, state):
                continue
            tool = rule["tool"]
            is_write = tool in write_tools
            enabled = rule.get("enabled")
            if enabled is False or (enabled is None and is_write):
                first_skip = first_skip or "disabled"
                continue
            if rule.get("unless_fresh") and has_fresh(tool):
                first_skip = first_skip or "skipped_fresh"
                continue
            args, why = resolve_args(rule, session, tables, tool_schemas.get(tool))
            if args is None:
                first_skip = first_skip or why
                continue
            return Selection(tool=tool, args=args, outcome="fired", is_write=is_write)
        return Selection(tool=None, outcome=first_skip)
    except Exception:  # noqa: BLE001 — never raise into the turn
        return Selection(tool=None, outcome="error")
```

Empty `__init__.py` files go in `tests/predispatch/`.

**Confirm the condition evaluator.** `src.conditions.evaluate_condition` must accept `SimpleNamespace(field=..., operator=..., value=...)`, where `operator` is a plain string. The code map says it reads `condition.operator`. If it compares against an enum, wrap the value accordingly. `test_select_never_raises_on_bad_rule` relies on the outer try.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd agent_core && uv run pytest tests/predispatch -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/predispatch agent_core/tests/predispatch
git commit -m "feat(agent_core): pure predispatch rule resolution (Spec E §3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Shared tool guard, used by every execution site

**Files:**
- Create `agent_core/src/tool_guard.py`.
- Modify `agent_core/src/manager_agent.py` `run_turn` (L297-540): the guard block at about L445-510 and the `_turn_tool_counts` local at L378.
- Modify `agent_core/src/orchestrator.py`: stream round 1 at about L4550-4690 and the nested rounds at about L4950-5020.
- Test: `agent_core/tests/test_tool_guard.py`, plus the existing tool-loop tests (`test_manager_agent.py`, `test_stream_turn.py`, `test_turn_path_identity_parity.py`, `test_tool_results.py`), which must stay green.

**Interfaces:**
- Consumes `over_call_cap`, `refusal_result` and `ungrounded_params` from `src.manager_agent`, and `TurnToolCache.lookup`.
- Produces:
  - `GuardVerdict`, a frozen dataclass: `kind: Literal["refuse", "hit", "go"]`, `result: ToolResult | None`;
  - `check_tool_call(tc, *, cap: int | None, used: int, grounded_spec: dict | None, messages: list, stored_results: dict, session_grounded: dict | None, consent_ok: bool | None, cache_lookup: Callable[[Any], ToolResult | None]) -> GuardVerdict`;
  - `ManagerAgent.run_turn(..., turn_tool_counts: dict[str, int] | None = None, session_grounded: dict | None = None)`, which uses the passed counts dict if given;
  - `ManagerAgent.last_llm_calls: int`, set at the end of each `run_turn` to the number of `self._llm.call` invocations it made.

- [ ] **Step 1: Write the failing tests.**

```python
# agent_core/tests/test_tool_guard.py
from src.models import ToolCall, ToolResult
from src.tool_guard import check_tool_call


def _tc(name="apply_job", **params):
    return ToolCall(tool_name=name, tool_use_id="t1", input_params=params)


def _hit(tc):
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True, result_text="[]")


BASE = dict(cap=None, used=0, grounded_spec=None, messages=[], stored_results={}, session_grounded=None,
            consent_ok=None, cache_lookup=lambda tc: None)


def test_go_when_nothing_blocks():
    assert check_tool_call(_tc(), **BASE).kind == "go"


def test_consent_refusal_first():
    v = check_tool_call(_tc(), **{**BASE, "consent_ok": False, "cap": 1, "used": 1})
    assert v.kind == "refuse" and "consent" in (v.result.result_text or str(v.result.result)).lower()


def test_cap_refusal():
    assert check_tool_call(_tc(), **{**BASE, "cap": 1, "used": 1}).kind == "refuse"


def test_ungrounded_refusal():
    v = check_tool_call(_tc(job_item_id="made-up"), **{**BASE, "grounded_spec": {"job_item_id": ["fetch_jobs"]}})
    assert v.kind == "refuse"


def test_cache_hit_after_guards():
    v = check_tool_call(_tc("fetch_jobs"), **{**BASE, "cache_lookup": _hit})
    assert v.kind == "hit" and v.result.result_text == "[]"
```

**Adapt the ungrounded test to `ungrounded_params`' real contract.** Read `manager_agent.py` L68-135: the spec format, and the meaning of a non-empty return. The assertion that a param with no grounding source and no stored evidence is refused is binding.

**Sync seeding test.** Add a test in `tests/test_manager_agent.py`. Call `run_turn(..., turn_tool_counts={"apply_job": 1})` on a manager whose tool caps include `apply_job: 1`. When the LLM requests `apply_job`, assert the gateway is not called and a refusal is returned.

**Sync LLM-call count.** Add `test_run_turn_reports_last_llm_calls`: one tool round makes `last_llm_calls` 1, and zero rounds makes it 0. Count only `run_turn`'s own `self._llm.call` invocations; the orchestrator's initial call is separate.

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd agent_core && uv run pytest tests/test_tool_guard.py tests/test_manager_agent.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**

```python
# agent_core/src/tool_guard.py
"""
agent_core/src/tool_guard.py
One guard + cache decision for every tool-execution site (Spec E §5.1-5.2):
consent → per-turn cap → grounding → cache. Live execution, shaping,
mapping and persistence stay at each site. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from src.manager_agent import over_call_cap, refusal_result, ungrounded_params
from src.models import ToolResult


@dataclass(frozen=True)
class GuardVerdict:
    """Decision for one tool call.

    Attributes:
        kind: "refuse" (result is the refusal), "hit" (result is the cached result), or "go" (execute live).
        result: The ToolResult for refuse/hit; None for go.
    """

    kind: Literal["refuse", "hit", "go"]
    result: ToolResult | None = None


def check_tool_call(tc: Any, *, cap: int | None, used: int, grounded_spec: dict | None, messages: list,
                    stored_results: dict, session_grounded: dict | None, consent_ok: bool | None,
                    cache_lookup: Callable[[Any], ToolResult | None]) -> GuardVerdict:
    """Apply the guards in order and decide refuse / cache-hit / go.

    Args:
        tc: The ToolCall.
        cap: ``max_calls_per_turn`` for this tool, or None.
        used: Calls of this tool already made this turn.
        grounded_spec: ``grounded_params[tool]`` (param → source tools), or None.
        messages: The turn's messages so far (grounding evidence).
        stored_results: ``tool_cache.stored_results_by_tool()``.
        session_grounded: ``_session_grounded_values(bundle, spec)``, or None.
        consent_ok: None when consent is not required; else whether it is granted.
        cache_lookup: ``tool_cache.lookup``.

    Returns:
        GuardVerdict.
    """
    if consent_ok is False:
        return GuardVerdict("refuse", refusal_result(tc.tool_name, tc.tool_use_id,
                                                     "consent_required: the caller has not given consent for this action"))
    if over_call_cap(cap, used):
        return GuardVerdict("refuse", refusal_result(tc.tool_name, tc.tool_use_id,
                                                     f"{tc.tool_name} already ran this turn; do not call it again"))
    if grounded_spec:
        bad = ungrounded_params(grounded_spec, tc, messages, stored_results=stored_results,
                                session_grounded=session_grounded)
        if bad:
            return GuardVerdict("refuse", refusal_result(tc.tool_name, tc.tool_use_id, _ungrounded_reason(bad)))
    hit = cache_lookup(tc)
    if hit is not None:
        return GuardVerdict("hit", hit)
    return GuardVerdict("go")
```

**Match the existing refusal texts.** Take the cap-refusal and ungrounded-refusal reason strings verbatim from the current sites (`manager_agent.py` around L450-500 and orchestrator around L4630-4670), so that model-facing text is unchanged. Implement `_ungrounded_reason(bad)` as the exact existing message builder; if one exists, move it here. Grep for "did not come from any tool result". The consent refusal text must match `_execute_tool`'s existing consent refusal, which is about L866+.

**Refactor the three sites.** At each site, replace the inline cap, grounding and cache-lookup branches with one `check_tool_call` call:
- `verdict.kind == "refuse"` or `"hit"` gives `tool_result = verdict.result`, with no count increment.
- `"go"` increments the counts, executes live, shapes and so on, exactly as today.

Keep each site's special branches before the guard, unchanged: `end_session`, KE route and `remember`.

Per site:
- **Stream round 1 and nested.** Hoist `_turn_tool_counts: dict[str, int] = {}` out of the `except ToolUseRequested` block, to just before the LLM call 1 loop (around L4467). Delete the inner declaration at about L4550; the nested rounds already share it. Pass `consent_ok = None if not self._tool_registry.requires_consent(tc.tool_name) else await self._async_trust.check_consent(...)`. Use the same call `SessionBootstrap` uses (grep `check_consent`) and the tool registry attribute the orchestrator holds.
- **Sync `run_turn`.**
  - Add the `turn_tool_counts` and `session_grounded` params. Use `counts = turn_tool_counts if turn_tool_counts is not None else {}` in place of the L378 local.
  - Pass `consent_ok=None`; its `_execute_tool` consent gate stays.
  - Pass `session_grounded`, which fixes the sync/stream divergence.
  - Set `self.last_llm_calls` from a local counter incremented at each `self._llm.call(` inside `run_turn`.
- **Orchestrator sync call to `run_turn`.** Pass `turn_tool_counts=` (a dict created before the initial `self._llm.call`) and `session_grounded=self._session_grounded_values(bundle, <spec>)`. Read how the stream site builds the spec per tool and mirror it. If `session_grounded` is per-tool, pass a callable or precomputed dict, following the stream site's shape.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd agent_core && uv run pytest -q`
Expected: all pass, including the unchanged parity and tool-loop tests.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/tool_guard.py agent_core/src/manager_agent.py agent_core/src/orchestrator.py agent_core/tests
git commit -m "refactor(agent_core): one tool guard + cache decision for every execution site (Spec E §5.1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Predispatch runner

**Files:**
- Create `agent_core/src/predispatch/runner.py`.
- Modify `agent_core/src/chat_provider/metrics.py`: add the counter `agent_core.predispatch.outcomes_total{tool,outcome}` and the helper `record_predispatch(tool: str | None, outcome: str)`, following the existing lazy-meter helpers.
- Test: `agent_core/tests/predispatch/test_runner.py`.

**Interfaces:**
- Consumes Task 2 `Selection` and Task 3 `GuardVerdict`.
- Produces:
  - `PredispatchResult`, a frozen dataclass: `outcome: str | None`, `tool: str | None`, `tool_call: ToolCall | None`, `tool_result: ToolResult | None`, `inject: bool`, `remove_tool: bool`, `ms: int`;
  - `async run_async(sel: Selection, *, guard: Callable[[ToolCall], Awaitable[GuardVerdict]], execute: Callable[[ToolCall], Awaitable[ToolResult]], timeout_s: float) -> PredispatchResult`;
  - `run_sync(sel: Selection, *, guard: Callable[[ToolCall], GuardVerdict], execute: Callable[[ToolCall], ToolResult]) -> PredispatchResult`;
  - `PREDISPATCH_ID = "predispatch-1"`.

- [ ] **Step 1: Write the failing tests.**

```python
# agent_core/tests/predispatch/test_runner.py
import asyncio

import pytest

from src.models import ToolResult
from src.predispatch.rules import Selection
from src.predispatch.runner import PREDISPATCH_ID, run_async, run_sync
from src.tool_guard import GuardVerdict


def _res(tc, ok=True):
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=ok,
                      result_text='[{"item_id": "j1"}]' if ok else "", error=None if ok else "boom")


READ = Selection(tool="fetch_jobs", args={"query_text": "Welder jobs in Bengaluru"}, outcome="fired", is_write=False)
WRITE = Selection(tool="apply_job", args={"profile_item_id": "p1", "job_item_id": "j"}, outcome="fired", is_write=True)


async def _go(tc):
    return GuardVerdict("go")


@pytest.mark.asyncio
async def test_read_success_injects_and_removes():
    async def ex(tc):
        assert tc.tool_use_id == PREDISPATCH_ID and tc.input_params == READ.args
        return _res(tc)
    r = await run_async(READ, guard=_go, execute=ex, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("fired", True, True) and r.tool_result.success


@pytest.mark.asyncio
async def test_no_selection_is_noop():
    r = await run_async(Selection(tool=None, outcome="skipped_fresh"), guard=_go, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.tool_call) == ("skipped_fresh", False, None)


@pytest.mark.asyncio
async def test_guard_refusal_falls_back():
    async def refuse(tc):
        return GuardVerdict("refuse", _res(tc, ok=False))
    r = await run_async(WRITE, guard=refuse, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("refused_guard", False, False)


@pytest.mark.asyncio
async def test_cache_hit_injects():
    async def hit(tc):
        return GuardVerdict("hit", _res(tc))
    r = await run_async(READ, guard=hit, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("cache_hit", True, True)


@pytest.mark.asyncio
async def test_read_failure_falls_back_write_failure_injects():
    async def fail(tc):
        return _res(tc, ok=False)
    r = await run_async(READ, guard=_go, execute=fail, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("failed", False, False)
    w = await run_async(WRITE, guard=_go, execute=fail, timeout_s=1.5)
    assert (w.outcome, w.inject, w.remove_tool) == ("failed", True, True)


@pytest.mark.asyncio
async def test_timeout_read_falls_back_write_injects_failure():
    async def slow(tc):
        await asyncio.sleep(1)
        return _res(tc)
    r = await run_async(READ, guard=_go, execute=slow, timeout_s=0.05)
    assert (r.outcome, r.inject) == ("timeout", False)
    w = await run_async(WRITE, guard=_go, execute=slow, timeout_s=0.05)
    assert (w.outcome, w.inject, w.remove_tool, w.tool_result.success) == ("timeout", True, True, False)


@pytest.mark.asyncio
async def test_internal_error_is_did_not_fire():
    async def boom(tc):
        raise RuntimeError("x")
    r = await run_async(READ, guard=boom, execute=None, timeout_s=1.5)
    assert (r.outcome, r.inject, r.remove_tool) == ("error", False, False)


def test_sync_mirrors_async():
    r = run_sync(READ, guard=lambda tc: GuardVerdict("go"), execute=lambda tc: _res(tc))
    assert (r.outcome, r.inject, r.remove_tool) == ("fired", True, True)
    w = run_sync(WRITE, guard=lambda tc: GuardVerdict("go"), execute=lambda tc: _res(tc, ok=False))
    assert (w.outcome, w.inject, w.remove_tool) == ("failed", True, True)
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd agent_core && uv run pytest tests/predispatch/test_runner.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# agent_core/src/predispatch/runner.py
"""
agent_core/src/predispatch/runner.py
Runs the selected pre-dispatch through the shared guard and the path's own
execute callable, under a budget (Spec E §5.2-5.5). Decides inject /
remove-tool by outcome and tool kind. Never raises. Belongs to the Agent Core
DPG block.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from src.models import ToolCall, ToolResult
from src.predispatch.rules import Selection

logger = logging.getLogger(__name__)
PREDISPATCH_ID = "predispatch-1"


@dataclass(frozen=True)
class PredispatchResult:
    """What the turn should do with the pre-dispatch.

    Attributes:
        outcome: Metric outcome, or None when no rule applied.
        tool: Tool name, or None.
        tool_call: The ToolCall made (or attempted), or None.
        tool_result: Result to inject (when ``inject``), or None.
        inject: Add the synthetic tool exchange to the messages.
        remove_tool: Drop ``tool`` from this turn's main-LLM tool list.
        ms: Wall time spent.
    """

    outcome: str | None
    tool: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    inject: bool = False
    remove_tool: bool = False
    ms: int = 0


def _failure(tc: ToolCall, reason: str) -> ToolResult:
    return ToolResult(tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=False,
                      result_text=f"Error: {reason}", error=reason)


def _decide(sel: Selection, tc: ToolCall, kind: str, result: ToolResult | None, start: float) -> PredispatchResult:
    ms = int((time.time() - start) * 1000)
    if kind == "refuse":
        return PredispatchResult("refused_guard", sel.tool, tc, None, False, False, ms)
    if kind == "hit":
        return PredispatchResult("cache_hit", sel.tool, tc, result, True, True, ms)
    if result is not None and result.success:
        return PredispatchResult("fired", sel.tool, tc, result, True, True, ms)
    outcome = "timeout" if kind == "timeout" else "failed"
    if sel.is_write:
        return PredispatchResult(outcome, sel.tool, tc, result or _failure(tc, outcome), True, True, ms)
    return PredispatchResult(outcome, sel.tool, tc, None, False, False, ms)


def _call(sel: Selection) -> ToolCall:
    return ToolCall(tool_name=sel.tool, tool_use_id=PREDISPATCH_ID, input_params=dict(sel.args))


async def run_async(sel: Selection, *, guard: Callable[[ToolCall], Awaitable], execute: Callable[[ToolCall], Awaitable[ToolResult]] | None,
                    timeout_s: float) -> PredispatchResult:
    """Stream path: guard, then execute under ``timeout_s``.

    Args:
        sel: Task 2 selection.
        guard: Async callable returning a GuardVerdict.
        execute: Async live execute (gateway + shape + map + after_call + persist); unused unless the guard says go.
        timeout_s: Pre-dispatch budget.

    Returns:
        PredispatchResult (never raises).
    """
    if sel.tool is None:
        return PredispatchResult(sel.outcome)
    start = time.time()
    tc = _call(sel)
    try:
        verdict = await guard(tc)
        if verdict.kind != "go":
            return _decide(sel, tc, verdict.kind, verdict.result, start)
        try:
            result = await asyncio.wait_for(execute(tc), timeout=timeout_s)
        except asyncio.TimeoutError:
            return _decide(sel, tc, "timeout", None, start)
        return _decide(sel, tc, "go", result, start)
    except Exception as e:  # noqa: BLE001 — never raise into the turn
        logger.warning("predispatch.error", extra={"operation": "predispatch.run", "status": "failure",
                                                   "tool": sel.tool, "error": type(e).__name__})
        return PredispatchResult("error", sel.tool, tc, None, False, False, int((time.time() - start) * 1000))


def run_sync(sel: Selection, *, guard: Callable[[ToolCall], object], execute: Callable[[ToolCall], ToolResult] | None) -> PredispatchResult:
    """Sync path: same decisions; budget is the gateway's own per-tool timeout (plan ruling 3)."""
    if sel.tool is None:
        return PredispatchResult(sel.outcome)
    start = time.time()
    tc = _call(sel)
    try:
        verdict = guard(tc)
        if verdict.kind != "go":
            return _decide(sel, tc, verdict.kind, verdict.result, start)
        return _decide(sel, tc, "go", execute(tc), start)
    except Exception as e:  # noqa: BLE001
        logger.warning("predispatch.error", extra={"operation": "predispatch.run", "status": "failure",
                                                   "tool": sel.tool, "error": type(e).__name__})
        return PredispatchResult("error", sel.tool, tc, None, False, False, int((time.time() - start) * 1000))
```

In `metrics.py`, add `record_predispatch(tool, outcome)`, which no-ops when OTel is unavailable, alongside Spec D's helpers. The runner does not call it; Task 5 calls it once per turn.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd agent_core && uv run pytest tests/predispatch -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/predispatch/runner.py agent_core/src/chat_provider/metrics.py agent_core/tests/predispatch
git commit -m "feat(agent_core): predispatch runner with budget and inject/fallback policy (Spec E §5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Orchestrator wiring on both paths

**Files:**
- Modify `agent_core/src/orchestrator.py`:
  - `__init__`;
  - a new `_select_predispatch` helper;
  - the stream insertion point, after Step 6b, around L4358, before `# ── Step 7`;
  - the stream messages / active_tools assembly around L4382-4440;
  - the sync insertion point, around L1297, before Step 7;
  - the sync messages / active_tools assembly around L1340-1400;
  - the turn-complete extras on both paths.
- Test: `agent_core/tests/test_predispatch_wiring.py` (new) and `agent_core/tests/test_turn_path_identity_parity.py`.

**Interfaces:**
- Consumes:
  - Task 2 `select`;
  - Task 3 `check_tool_call` and the hoisted `_turn_tool_counts`;
  - Task 4 `run_async`, `run_sync`, `PREDISPATCH_ID` and `record_predispatch`;
  - Spec D `_result_shaper`;
  - `_write_mapped_session_values`, `tool_cache.after_call`, `_persist_tool_cache` (async and sync), `_capture_tool_exchange` and `_tool_session_values`.
- Produces:
  - `AgentCore._select_predispatch(self, bundle, subagent_id: str, intent: str, tool_cache) -> Selection`;
  - the log extras `predispatch_tool`, `predispatch_outcome`, `predispatch_ms` and `llm_calls` on `orchestrator.stream_turn_complete` and the sync turn-complete log.

- [ ] **Step 1: Write the failing tests.** Use `test_stream_turn.py`'s `_make_agent_core`, `_collect_events` and `_make_turn_input`, and the parity file's `_sync_run`/`_stream_run` and `_jobs_entry`. Read them first; plumbing is adaptable, assertions are binding.

```python
# agent_core/tests/test_predispatch_wiring.py
"""Spec E §5.4-5.5: pre-dispatch after routing on both paths."""
import pytest

from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input

JOBS_RULE = {"tool": "fetch_jobs", "unless_fresh": True,
             "args": {"query_text": {"template": "{trade|stored_trade} jobs in {location|stored_location}"}}}


def _agent(session, rules, *, gateway_result=None):
    agent = _make_agent_core()
    # Route into a subagent that offers fetch_jobs and carries the rule; set session values; make the
    # gateway mock return a projected fetch_jobs ToolResult (success, projected=True, result_text JSON).
    ...
    return agent


@pytest.mark.asyncio
async def test_read_predispatch_one_llm_call_and_tool_removed():
    agent = _agent({"trade": "Welder", "location": "Bengaluru"}, [JOBS_RULE])
    events = await _collect_events(agent, _make_turn_input())
    req = agent._llm.stream.call_args_list[0]           # adapt to how the LLM mock records requests
    names = [t["name"] for t in (req.args[0].tools or [])]
    assert "fetch_jobs" not in names
    msgs = req.args[0].messages
    assert msgs[-2].content[0].tool_use_id == "predispatch-1" and msgs[-1].content[0].tool_use_id == "predispatch-1"
    assert agent._llm.stream.call_count == 1
    assert agent._async_gateway.execute.call_count == 1


@pytest.mark.asyncio
async def test_fresh_cache_no_predispatch():
    ...  # session has a fresh fetch_jobs cache entry → gateway not called by predispatch; outcome skipped_fresh


@pytest.mark.asyncio
async def test_read_timeout_falls_back():
    ...  # gateway execute sleeps > predispatch_timeout_ms → tool still offered, no synthetic messages


@pytest.mark.asyncio
async def test_predispatch_uses_values_written_this_turn():
    ...  # fake_understander writes trade=Welder over session trade=Electrician → query_text uses Welder


@pytest.mark.asyncio
async def test_predispatched_write_is_never_repeated():
    ...  # enabled apply_job rule + LLM mock that requests apply_job → exactly one gateway apply_job call


def test_sync_path_predispatch_parity():
    ...  # same session through process_turn: one llm.call, synthetic pair present, tool removed, extras logged
```

**The `...` bodies must be written in full.** They are elided here only because their plumbing depends on `_make_agent_core`'s mocks. For each, follow the assertion named in its comment. The required behaviours:
- one main-LLM call on a pre-dispatched read;
- no `fetch_jobs` in the speaking call's tools;
- the synthetic pair present and ending on a user `tool_result`;
- the real utterance still before the pair;
- `served_tool_results` and `recent_tool_exchanges` updated;
- `skipped_fresh`, timeout and refusal fallbacks keep the tool offered with no synthetic pair;
- the corrected value is used;
- exactly one `apply_job` gateway call when the rule is enabled and the model also asks for it;
- sync/stream parity of the request (system, messages, tools);
- the extras `llm_calls`, `predispatch_tool` and `predispatch_outcome`;
- `record_predispatch` called once per turn when an outcome exists.

Also add `test_post_applied_hook_fires_for_predispatched_apply_job`. Mirror the existing post_applied hook test, with an enabled apply_job rule.

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd agent_core && uv run pytest tests/test_predispatch_wiring.py -q`
Expected: FAIL. No pre-dispatch happens yet, so the LLM is called twice.

- [ ] **Step 3: Implement.**

In `__init__`, after the workflow and tool registry are set:

```python
        self._predispatch_tables = dict(self._config.get("predispatch_tables") or {})
        self._predispatch_timeout_s = int((self._config.get("agent") or {}).get("predispatch_timeout_ms", 1500)) / 1000
        conns = self._config.get("connectors") or {}
        self._write_tools = {c["name"] for g in ("write", "identity") for c in (conns.get(g) or []) if isinstance(c, dict) and c.get("name")}
        self._tool_schemas = {d["name"]: d.get("input_schema") or {} for d in self._tool_registry.get_tool_definitions()}
        for sa in self._workflow.subagents.values():           # plan ruling 5: arg keys must be agent params
            for i, rule in enumerate(getattr(sa, "predispatch", []) or []):
                props = (self._tool_schemas.get(rule.get("tool")) or {}).get("properties") or {}
                unknown = [k for k in (rule.get("args") or {}) if k not in props]
                if props and unknown:
                    raise ValueError(f"subagents[{sa.id}].predispatch[{i}]: args {unknown} are not agent parameters "
                                     f"of '{rule.get('tool')}'")
```

Use the real attribute names for the tool registry and the workflow, and check that `get_tool_definitions()` items carry `input_schema`. Startup must not raise when the gateway tool list is empty (no props), for example in tests.

Add the helper:

```python
    def _select_predispatch(self, bundle, subagent_id: str, intent: str, tool_cache):
        """Spec E §3.3: the turn's pre-dispatch selection for the post-routing subagent. Never raises."""
        sa = self._workflow.subagents.get(subagent_id)
        return select(list(getattr(sa, "predispatch", []) or []), intent=intent, state=self._routing_state(bundle),
                      session=self._tool_session_values(bundle) | dict(bundle.session),
                      tables=self._predispatch_tables, tool_schemas=self._tool_schemas,
                      write_tools=self._write_tools, has_fresh=lambda t: tool_cache.latest_entry(t) is not None)
```

Session precedence: `_tool_session_values` drops seeded empties and lets NLU-owned values win. Merge so that NLU-owned values from this turn win over stale bundle values; reuse `_tool_session_values(bundle)` alone if it already covers `bundle.session`. Check this, because `test_predispatch_uses_values_written_this_turn` depends on it.

**Stream insertion,** before Step 7:

```python
            _pd_sel = self._select_predispatch(bundle, next_subagent_id, nlu_result.intent, tool_cache)

            async def _pd_guard(tc):
                spec = self._manager_agent._grounded_params.get(tc.tool_name)
                consent = None
                if self._tool_registry.requires_consent(tc.tool_name):
                    consent = await self._async_trust.check_consent(session_id, tc.tool_name)   # match the real signature
                return check_tool_call(
                    tc, cap=self._manager_agent._tool_call_caps.get(tc.tool_name),
                    used=_turn_tool_counts.get(tc.tool_name, 0), grounded_spec=spec, messages=[],
                    stored_results=tool_cache.stored_results_by_tool(),
                    session_grounded=self._session_grounded_values(bundle, spec) if spec else None,
                    consent_ok=consent, cache_lookup=tool_cache.lookup)

            async def _pd_execute(tc):
                _turn_tool_counts[tc.tool_name] = _turn_tool_counts.get(tc.tool_name, 0) + 1
                r = await self._async_gateway.execute(tool_cache.prepare(tc), session_id, user_id,
                                                      session_values=self._tool_session_values(bundle))
                r = self._result_shaper.shape(r)
                await self._write_mapped_session_values(session_id, user_id, r, bundle)
                tool_cache.after_call(tc, r)
                await self._persist_tool_cache(session_id, user_id, tool_cache)
                return r

            _pd = await run_async(_pd_sel, guard=_pd_guard, execute=_pd_execute,
                                  timeout_s=self._predispatch_timeout_s)
```

`_turn_tool_counts` must already be hoisted (Task 3) above this point. If Task 3 hoisted it below this insertion, move the declaration up to just before this block.

**Stream messages and tools,** after `messages` is built and `_prepend_tool_replay` has run, and after `active_tools` is computed:

```python
            if _pd.inject and _pd.tool_result is not None:
                messages.append(Message(role="assistant", content=[ToolUseBlock(
                    tool_use_id=PREDISPATCH_ID, tool_name=_pd.tool, input=_pd.tool_call.input_params)]))
                messages.append(Message(role="user", content=[ToolResultBlock(
                    tool_use_id=PREDISPATCH_ID,
                    content=_pd.tool_result.result_text or str(_pd.tool_result.result),
                    is_error=not _pd.tool_result.success)]))
                _stream_tool_results.append(_pd.tool_result)
                _ex = self._capture_tool_exchange([_pd.tool_call], [{"type": "tool_result", "tool_use_id": PREDISPATCH_ID,
                                                   "content": _pd.tool_result.result_text or str(_pd.tool_result.result)}], _max_chars)
                if _ex is not None:
                    _captured_exchanges_this_turn.append(_ex)
            if _pd.remove_tool:
                active_tools = [t for t in active_tools if t.get("name") != _pd.tool]
```

Check that the names `_stream_tool_results`, `_captured_exchanges_this_turn` and `_max_chars`, and the `_capture_tool_exchange` signature, exist at that point. Declare `_stream_tool_results` earlier if it is currently declared inside the tool loop.

`served_tool_results` updates through `tool_cache.served()`, because the pre-dispatch went through `tool_cache.prepare` / `after_call`. Confirm that `prepare` records served, or call the same method the loop uses.

**Sync insertion.** Mirror the stream code with `run_sync`:
- the guard uses `self._trust.check_consent` synchronously;
- execute does `tool_cache.prepare`, `self._gateway.execute`, shape, `tool_cache.after_call` and `_persist_tool_cache_sync`;
- write the mapped session values the way the sync path does after `run_turn`, at about L1503-1508, applied to the pre-dispatch result at once;
- inject into the sync `messages`, filter the sync `active_tools`, and append to the sync `tool_results` list and captured exchanges;
- pass the same `turn_tool_counts` dict (incremented by the pre-dispatch) into `run_turn`.

**`llm_calls`.**
- Stream: initialise `_llm_calls = 0` and increment it at every `self._llm.stream(` call in the turn.
- Sync: `1 + self._manager_agent.last_llm_calls` (the initial `self._llm.call` plus the rounds).

Add `"llm_calls"`, `"predispatch_tool": _pd.tool`, `"predispatch_outcome": _pd.outcome` and `"predispatch_ms": _pd.ms` to both turn-complete log extras. Call `record_predispatch(_pd.tool, _pd.outcome)` once when `_pd.outcome` is not None.

Imports: `from src.predispatch.rules import select`, `from src.predispatch.runner import PREDISPATCH_ID, run_async, run_sync`, `from src.tool_guard import check_tool_call` and `from src.chat_provider.metrics import record_predispatch`. Also import `ToolUseBlock`/`ToolResultBlock` if they aren't already.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd agent_core && uv run pytest -q`
Expected: all pass, coverage ≥ 70%.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/src/orchestrator.py agent_core/tests
git commit -m "feat(agent_core): pre-dispatch determined tool calls before the main LLM on both paths (Spec E §5.4-5.5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Dev-kit sync

**Files:**
- Modify `dev-kit/dev_kit/schemas/domain/agent_core.py`: add the `PredispatchArg` / `PredispatchRule` mirrors, `SubAgent.predispatch` and `AgentSection.predispatch_timeout_ms`. Add `predispatch_tables` to whichever top-level domain section holds top-level keys; find where `prompt_session_fields` / `session_bootstrap` live.
- Modify `dev-kit/dev_kit/schema.py`, the flat schema: `SubAgentSchema.predispatch` (validated through the domain mirror, as Spec D did for `result_shaping`), `AgentConfig.predispatch_timeout_ms` and the top-level `predispatch_tables`.
- Modify `dev-kit/dev_kit/agent/field_rules/agent_core.py`: add `agent.predispatch_timeout_ms` (`framework_default_only`), `agent_workflow.subagents.predispatch` (`auto_answer`) and `predispatch_tables` (`auto_answer`).
- Modify `dev-kit/dpg/agent_core.yaml`: add `predispatch_timeout_ms: 1500` under `agent:`.
- Modify `dev-kit/dev_kit/schemas/dpg/agent_core.py`: `AgentDpgDefaults.predispatch_timeout_ms`.
- Modify `dev-kit/dev_kit/schemas/cross_block_validation.py`: add a rule mirroring Task 1's `_check_predispatch_rules`. It also checks that rule arg keys are `source: agent` params of the matching `action_gateway.tools[id]` (plan ruling 5).
- Test: `dev-kit/tests/schemas/domain/test_agent_core.py`, `dev-kit/tests/test_schema.py`, `dev-kit/tests/schemas/test_cross_block_validation.py` and `dev-kit/tests/agent/test_field_rules_agent_core.py`.

**Interfaces:**
- Consumes the Task 1 runtime models. The dev-kit must accept and reject exactly the same shapes.

- [ ] **Step 1: Write the failing tests.**
  - **Domain mirror:** a valid rule is accepted. These are rejected:
    - an arg with both `from` and `template`;
    - `from: session` without `key`;
    - an unknown field.
  - **Flat schema:** reject tests for the same three cases (validated through the mirror), plus acceptance of the Blue Dots merged config.
  - **Cross-block** (each with a reject test; the Blue Dots merged config is accepted):
    - a write rule without `enabled`;
    - an unknown tool;
    - an unknown normalise table;
    - `on_intent` not producible;
    - an arg key that is not an agent param of the gateway tool.
  - **FIELD_RULES:** the three new keys are present, and the reach and tools phases still complete for a web-only project. Reuse Spec D's `test_spec_d_rules_do_not_stall_reach_and_tools_for_web_only` pattern.

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd dev-kit && uv run pytest tests/schemas tests/test_schema.py tests/agent/test_field_rules_agent_core.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**
  - Copy the Task 1 models verbatim into the domain mirror, in the file's `ConfigDict(extra="forbid")` style.
  - In the flat schema, add `predispatch: Optional[list[dict]] = None` to `SubAgentSchema`, with a `field_validator` running `PredispatchRule.model_validate` per item. Add `predispatch_timeout_ms: int = Field(default=1500, gt=0)` to `AgentConfig` and `predispatch_tables: dict = Field(default_factory=dict)` at the top level.
  - Add the cross-block rule to `validate_cross_block`, gated as Spec D's output-contract rule is (`applicable_after` the tools phase).
  - Add the FIELD_RULES entries in the neighbouring rules' constructor shape.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd dev-kit && uv run pytest -q`
Expected: only the baseline reach_layer failure.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit
git commit -m "feat(dev-kit): mirror predispatch rules, tables and timeout; cross-block checks (Spec E §9)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Blue Dots rules, tables and prompt lines

**Files:**
- Modify `dev-kit/configs/blue-dots/agent_core.yaml`:
  - a top-level `predispatch_tables`;
  - a `predispatch` list on the `job_match`, `apply_confirm` and `profile_setup` subagents;
  - prompt lines in those three subagents.
- Test: `agent_core/tests/test_blue_dots_spec_e_config.py` (new).

**Interfaces:**
- Consumes the Task 1 schema, the Task 2 semantics and the Task 6 dev-kit checks.

- [ ] **Step 1: Write the failing test.**

```python
# agent_core/tests/test_blue_dots_spec_e_config.py
from pathlib import Path

import yaml

from eval.nlu.offline import load_merged_config
from src.predispatch.rules import select
from src.schema.config import MergedConfig

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
CFG = yaml.safe_load((BD / "agent_core.yaml").read_text(encoding="utf-8"))
SUB = {s["id"]: s for s in CFG["agent_workflow"]["subagents"]}
UUID = "3f2b8c1e-9a4d-4e2f-8b1a-0c9d8e7f6a5b"


def test_validates():
    MergedConfig.validate_full(load_merged_config(BD))


def test_fetch_jobs_rule_on_and_writes_off():
    jm = SUB["job_match"]["predispatch"]
    assert jm[0]["tool"] == "fetch_jobs" and jm[0].get("enabled", True) is True and jm[0]["unless_fresh"] is True
    assert SUB["apply_confirm"]["predispatch"][0]["enabled"] is False
    assert SUB["profile_setup"]["predispatch"][0]["enabled"] is False


def test_fetch_jobs_query_from_session():
    s = select(SUB["job_match"]["predispatch"], intent="any_input", state={}, session={"stored_trade": "Welder",
               "location": "Bangalore"}, tables=CFG["predispatch_tables"],
               tool_schemas={"fetch_jobs": {"properties": {"query_text": {"type": "string"}}, "required": ["query_text"]}},
               write_tools={"apply_job", "save_profile"}, has_fresh=lambda t: False)
    assert (s.tool, s.args) == ("fetch_jobs", {"query_text": "Welder jobs in Bengaluru"})


def test_prompts_updated():
    text = (BD / "agent_core.yaml").read_text(encoding="utf-8")
    assert "do not call the tool again" in text
    for gone in ("re-fetch it, do not recall it", "an ordinal such as"):
        assert gone not in text, gone
```

- [ ] **Step 2: Run the test and confirm it fails.**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_spec_e_config.py -q`
Expected: FAIL with `KeyError: 'predispatch'`.

- [ ] **Step 3: Edit the config.** Locate each edit by its quoted text.

1. **Top level** (next to `session_bootstrap`). Fill the `city_canonical` table with every pair in the job_match prompt's "City name normalisation" section, verbatim. Then shorten that prompt section to one line: "Use canonical city names (the system normalises them for the search)."

```yaml
predispatch_tables:
  city_canonical: { Bangalore: Bengaluru, Bombay: Mumbai }   # extend with every pair from the job_match city map
  name_placeholders: [unknown, "n/a", na, none, caller, user, "-", "नाम", "पता नहीं"]
```

2. **`job_match`:**

```yaml
      predispatch:
        - tool: fetch_jobs
          unless_fresh: true
          args:
            query_text:
              template: "{trade|stored_trade} jobs in {location|stored_location}"
              normalise: { location: city_canonical }
```

3. **`apply_confirm`:**

```yaml
      predispatch:
        - tool: apply_job
          enabled: false
          on_intent: [apply_now]
          when: [{ field: applications_submitted, operator: eq, value: 0 }]
          args:
            profile_item_id: { from: session, key: profile_item_id }
            job_item_id:     { from: session, key: selected_job_item_id }
```

4. **`profile_setup`:**

```yaml
      predispatch:
        - tool: save_profile
          enabled: false
          when: [{ field: profile_item_id, operator: in, value: [null, ""] }]
          args:
            name:             { from: session, key: name, reject: name_placeholders }
            gender:           { from: session, key: gender, normalise: { male: Male, female: Female, other: Other } }
            location:         { from: session, key: location }
            trade:            { from: session, key: trade }
            work_experience:  { from: session, key: work_experience }
            experience_years: { from: session, key: experience_years }
            qualification:    { from: session, key: qualification }
            job_nature:       { from: session, key: job_nature }
```

Only keep the arg keys that are `source: agent` params of the gateway tool in `dev-kit/configs/blue-dots/action_gateway.yaml`. The Task 5 startup check and the Task 6 cross-block check reject others, so drop any that are not.

5. **Prompts.** In each of job_match, apply_confirm and profile_setup, add this line near the tool instruction: "If a result for this step's tool is already in the conversation, speak from it; do not call the tool again."

   In apply_confirm:
   - delete the instruction to re-fetch `fetch_jobs` before `apply_job` (the text containing "re-fetch it, do not recall it");
   - replace the "holds an ordinal such as 'तीसरा'" wording with "selected_job_item_id holds the chosen job's item_id."

- [ ] **Step 4: Run the tests and confirm they pass.**

Run:

```bash
cd agent_core && uv run pytest -q
cd ../dev-kit && uv run pytest -q
cd ../reach_layer/bridge && uv run --with pytest python -m pytest -q tests/test_blue_dots_config.py
```

Expected: all at baseline.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml agent_core/tests/test_blue_dots_spec_e_config.py
git commit -m "feat(blue-dots): predispatch fetch_jobs on job_match; apply/save rules off; prompt lines (Spec E §6-7)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Scenario runner scrape, docs and the deploy gate

**Files:**
- Modify `agent_core/eval/scenarios/run.py`: add an optional `--agent-container NAME`. After each turn, scrape `docker logs --since <turn start> NAME` for the `stream_turn_complete` record and read `llm_calls`, `predispatch_tool` and `predispatch_outcome`. In the summary, add per-scenario `llm_calls` and the p50/p95 `first_sentence_ms` split by `predispatch_outcome == "fired"`. Remove those keys from `not_collected`.
- Test: `agent_core/tests/eval/test_scenario_scrape.py`.
- Modify `agent_core/README.md` (a short "Tool pre-dispatch" subsection, plus config table rows for `predispatch`, `predispatch_tables` and `agent.predispatch_timeout_ms`), `ARCHITECTURE.md` (the turn sequence) and `CLAUDE.md` (the runtime sequence line).

**Interfaces:**
- Produces `parse_turn_extras(log_text: str) -> dict`, which returns the last `stream_turn_complete` record's `llm_calls`, `predispatch_tool` and `predispatch_outcome` (missing keys become None).

- [ ] **Step 1: Write the failing test.**
  - Read how agent_core formats log extras. Grep the logging setup for a JSON formatter, or check how `extra=` fields are rendered.
  - Write `test_parse_turn_extras` against one real-format sample line containing `stream_turn_complete` with `llm_calls=1` / `predispatch_outcome=fired`, in whatever format the formatter emits.
  - Also test that text with no matching line returns all-None.

- [ ] **Step 2: Run the test and confirm it fails.**

Run: `cd agent_core && uv run pytest tests/eval/test_scenario_scrape.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** `parse_turn_extras` and the `--agent-container` wiring. The scrape must never print replies and never fail the run: on any docker error, record None. Then write the docs.

- [ ] **Step 4: Verify.**

```bash
cd agent_core && uv run pytest -q
cd ../dev-kit && uv run pytest -q
docker build -q -f dev-kit/Dockerfile -t dpg-dev-kit:spec-e .
docker run -d --rm --name dpg-dev-kit-spec-e -e OPENAI_API_KEY=placeholder-not-used -p 18083:8080 dpg-dev-kit:spec-e
curl -s -X POST http://127.0.0.1:18083/api/projects/blue-dots/deploy/validate | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['validator'], {b: not e for b, e in r['block_errors'].items()}, r.get('warnings'))"
docker rm -f dpg-dev-kit-spec-e
```

Expected:
- the suites are at baseline;
- the validator reports `runtime_baked` with every block True;
- warnings are empty.

The pre-existing cross-block invariant warnings from PR #428 are acceptable. The before/after live scenario run is a pre-merge step for the human partner, because it writes to the shared dev Signals cluster. It is not part of this task's pass criteria.

- [ ] **Step 5: Commit.**

```bash
git add agent_core/eval/scenarios agent_core/tests/eval agent_core/README.md ARCHITECTURE.md CLAUDE.md
git commit -m "feat(eval): scenario runner reads llm_calls and predispatch outcome; docs for tool pre-dispatch (Spec E §10-12)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
