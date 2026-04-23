# KKB Subagent Collapse & Intent Pruning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse KKB's 14 subagents to 3 journey states + 3 infrastructure subagents, prune 41 NLU intents to 13, and make all 7 tools available on every turn via a new `global_tools` list.

**Architecture:** Config-only refactor for domain values plus two small framework additions: (1) an optional `global_tools` field on `agent_workflow` resolved to a shared tool-def list that overrides per-subagent scoping; (2) an orchestrator post-tool hook that moves the session to `post_applied` when `apply_job` returns success. NLU, Trust, Memory, Observability unchanged.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, uv. Domain config in YAML.

**Spec:** [`docs/superpowers/specs/2026-04-23-kkb-subagent-collapse-design.md`](../specs/2026-04-23-kkb-subagent-collapse-design.md).

**Issue:** [sanketika-labs/ai-diffusion-dpg#182](https://github.com/sanketika-labs/sanketika-labs/issues/182).

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `agent_core/src/schema/config.py` | Modify (~1260) | Add optional `global_tools: list[str]` to `AgentWorkflowConfig`. |
| `agent_core/src/workflow_loader.py` | Modify | Parse `global_tools`, resolve definitions via `ToolRegistry`, attach to `AgentWorkflow`, add resolver helper. |
| `agent_core/src/orchestrator.py` | Modify | Use resolver at two LLM-call sites (sync + streaming). Add `apply_job` post-tool hook (two sites). |
| `agent_core/tests/test_schema_config.py` | Modify | Assert `global_tools` parses and defaults to empty. |
| `agent_core/tests/test_workflow_loader.py` | Modify | Assert `global_tool_defs` resolves and `resolve_tools_for` returns it. |
| `agent_core/tests/test_orchestrator.py` | Modify | Assert `active_tools` source swaps when `global_tool_defs` set; assert `apply_job` success flips subagent to `post_applied`. |
| `agent_core/tests/test_nlu_processor.py` | Modify | Update intent assertions to the 13-intent KKB taxonomy (where the test uses the KKB fixture). |
| `dev-kit/configs/kkb/agent_core.yaml` | Full rewrite of `agent_workflow.*` + `preprocessing.nlu_processor.intents` + `preprocessing.nlu_processor.signal_intents` | New 3-subagent workflow, 13 intents, `global_tools`, merged prompts with validity pass. |

No new files. No moves or renames.

---

## Phase 1 — `global_tools` schema + loader plumbing

### Task 1: Add `global_tools` field to `AgentWorkflowConfig`

**Files:**
- Modify: `agent_core/src/schema/config.py:377-389`
- Test: `agent_core/tests/test_schema_config.py`

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_schema_config.py`:

```python
def test_agent_workflow_config_global_tools_default_empty():
    from src.schema.config import AgentWorkflowConfig
    cfg = AgentWorkflowConfig()
    assert cfg.global_tools == []


def test_agent_workflow_config_global_tools_accepts_list():
    from src.schema.config import AgentWorkflowConfig
    cfg = AgentWorkflowConfig(
        workflow_id="w",
        version="1.0.0",
        global_tools=["get_profile", "onest_market_lookup"],
    )
    assert cfg.global_tools == ["get_profile", "onest_market_lookup"]


def test_agent_workflow_config_rejects_unknown_field():
    """extra='forbid' must still reject typos."""
    from pydantic import ValidationError
    from src.schema.config import AgentWorkflowConfig
    with pytest.raises(ValidationError):
        AgentWorkflowConfig(workflow_id="w", version="1.0.0", globall_tools=["x"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py::test_agent_workflow_config_global_tools_default_empty tests/test_schema_config.py::test_agent_workflow_config_global_tools_accepts_list -v`
Expected: FAIL — `global_tools` unknown field (rejected by `extra="forbid"`).

- [ ] **Step 3: Add field in `agent_core/src/schema/config.py`**

Change `AgentWorkflowConfig` at line 377-389 to:

```python
class AgentWorkflowConfig(BaseModel):
    """Multi-subagent workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = ""
    version: str = "1.0.0"
    agent_system_prompt: str = ""
    global_intents: list[str] = Field(default_factory=list)
    global_routing: list[RoutingRule] = Field(default_factory=list)
    default_fallback_subagent_id: str = ""
    tool_result_mappings: dict[str, ToolResultMapping] = Field(default_factory=dict)
    global_tools: list[str] = Field(default_factory=list)
    subagents: list[SubAgent] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -v`
Expected: PASS for the three new tests; all pre-existing tests still green.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/schema/config.py agent_core/tests/test_schema_config.py
git commit -m "feat(agent_core): add optional global_tools field to workflow schema (#182)"
```

---

### Task 2: Parse `global_tools` in loader; expose `global_tool_defs` + resolver

**Files:**
- Modify: `agent_core/src/workflow_loader.py:119-146` (dataclass), `200-281` (load method), `720-740` (helpers)
- Test: `agent_core/tests/test_workflow_loader.py`

Context: `AgentWorkflow` is the runtime dataclass assembled by the loader. We add `global_tool_defs: list[dict]` and a `resolve_tools_for(subagent_id)` method that returns `global_tool_defs` when non-empty, otherwise falls back to `tool_defs[subagent_id]`.

- [ ] **Step 1: Write the failing tests**

Append to `agent_core/tests/test_workflow_loader.py`:

```python
def test_global_tools_resolved_to_definitions(loader):
    registry = _make_tool_registry(["get_profile", "onest_market_lookup"])
    config = _minimal_config(tools=[])
    config["agent_workflow"]["global_tools"] = ["get_profile", "onest_market_lookup"]
    workflow = loader.load(config, registry)
    assert [d["name"] for d in workflow.global_tool_defs] == [
        "get_profile",
        "onest_market_lookup",
    ]


def test_resolve_tools_for_prefers_global_tool_defs(loader):
    registry = _make_tool_registry(["get_profile", "local_only"])
    config = _minimal_config(tools=["local_only"])
    config["agent_workflow"]["global_tools"] = ["get_profile"]
    workflow = loader.load(config, registry)
    names = [d["name"] for d in workflow.resolve_tools_for("start")]
    assert names == ["get_profile"]


def test_resolve_tools_for_falls_back_to_subagent_tools_when_global_empty(loader):
    registry = _make_tool_registry(["local_only"])
    config = _minimal_config(tools=["local_only"])
    workflow = loader.load(config, registry)
    names = [d["name"] for d in workflow.resolve_tools_for("start")]
    assert names == ["local_only"]


def test_resolve_tools_for_unknown_subagent_returns_empty_when_global_empty(loader):
    registry = _make_tool_registry(["local_only"])
    config = _minimal_config(tools=["local_only"])
    workflow = loader.load(config, registry)
    assert workflow.resolve_tools_for("nope") == []


def test_global_tools_validated_against_registry(loader):
    """Unregistered global tool name must fail validation rule 3."""
    registry = _make_tool_registry(["get_profile"])  # no 'ghost_tool'
    config = _minimal_config()
    config["agent_workflow"]["global_tools"] = ["ghost_tool"]
    with pytest.raises(ConfigurationError, match="ghost_tool"):
        loader.load(config, registry)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py::test_global_tools_resolved_to_definitions tests/test_workflow_loader.py::test_resolve_tools_for_prefers_global_tool_defs tests/test_workflow_loader.py::test_resolve_tools_for_falls_back_to_subagent_tools_when_global_empty tests/test_workflow_loader.py::test_resolve_tools_for_unknown_subagent_returns_empty_when_global_empty tests/test_workflow_loader.py::test_global_tools_validated_against_registry -v`
Expected: FAIL — `AgentWorkflow` has no `global_tool_defs` / `resolve_tools_for` / no validation.

- [ ] **Step 3: Extend the `AgentWorkflow` dataclass**

In `agent_core/src/workflow_loader.py` replace the dataclass definition around line 119-146 so the docstring mentions the new field and the method, and add both:

```python
@dataclass
class AgentWorkflow:
    """
    Immutable, fully parsed and pre-computed representation of the multi-subagent
    workflow graph for a single deployment.

    Pre-computed fields (``nlu_intent_set``, ``tool_defs``, ``global_tool_defs``)
    are populated by :class:`AgentWorkflowLoader` at startup.

    Attributes:
        workflow_id:                Unique identifier for this workflow.
        version:                    SemVer string for this workflow.
        agent_system_prompt:        Top-level system prompt shared across subagents.
        global_intents:             Intents handled globally before subagent routing.
        global_routing:             Routing rules applied globally after intent classification.
        default_fallback_subagent_id: Subagent to route to when no routing rule matches.
        subagents:                  All subagents keyed by their id for O(1) lookup.
        start_subagent_id:          ID of the subagent with ``is_start=True``.
        nlu_intent_set:             Per-subagent scoped intent list (subagent + global intents).
        tool_defs:                  Per-subagent tool definition slices (excludes
                                    built-in ``knowledge_retrieval``).
        global_tool_defs:           Shared tool-def list applied to every subagent when
                                    non-empty. Empty means fall back to per-subagent
                                    ``tool_defs``. Validated against the registry.
    """

    workflow_id: str
    version: str
    agent_system_prompt: str
    global_intents: list[str]
    global_routing: list[RoutingRule]
    default_fallback_subagent_id: str
    subagents: dict[str, SubAgent]
    start_subagent_id: str

    nlu_intent_set: dict[str, list[str]] = field(default_factory=dict)
    tool_defs: dict[str, list[dict]] = field(default_factory=dict)
    global_tool_defs: list[dict] = field(default_factory=list)

    def resolve_tools_for(self, subagent_id: str) -> list[dict]:
        """Return the tool definitions to inject into the LLM call for a subagent.

        When ``global_tool_defs`` is non-empty, it takes precedence and every
        subagent sees the same tool set (KKB behaviour). Otherwise the per-subagent
        ``tool_defs`` slice is returned, or an empty list if the subagent is unknown.

        Args:
            subagent_id: Subagent id whose tool set is being assembled.

        Returns:
            List of Anthropic-shaped tool definition dicts.
        """
        if self.global_tool_defs:
            return self.global_tool_defs
        return self.tool_defs.get(subagent_id, [])
```

- [ ] **Step 4: Parse + validate + resolve `global_tools` in `load()`**

In `agent_core/src/workflow_loader.py`, inside `load()` after line 212 where `default_fallback_subagent_id` is read, add:

```python
        global_tools_raw: list[str] = workflow_cfg.get("global_tools") or []
        if not isinstance(global_tools_raw, list) or not all(isinstance(t, str) for t in global_tools_raw):
            raise ConfigurationError(
                "agent_workflow.global_tools must be a list of tool name strings"
            )
```

Then after the `_validate_tool_names(subagents, tool_registry)` call (around line 251), add a global-tools validation call. First, extend `_validate_tool_names` or add a sibling `_validate_global_tool_names`. Choose the sibling to keep the existing method untouched:

```python
        self._validate_global_tool_names(global_tools_raw, tool_registry)
```

And add this method near `_build_tool_defs` (around line 720):

```python
    def _validate_global_tool_names(
        self,
        global_tools: list[str],
        tool_registry: ToolRegistry,
    ) -> None:
        """Fail fast if any name in agent_workflow.global_tools is not registered.

        Args:
            global_tools:   Names declared under ``agent_workflow.global_tools``.
            tool_registry:  Registry whose :meth:`get_tool_names` lists all known tools.

        Raises:
            ConfigurationError: If any name is not registered.
        """
        if not global_tools:
            return
        known = tool_registry.get_tool_names()
        unknown = [t for t in global_tools if t not in known]
        if unknown:
            raise ConfigurationError(
                "agent_workflow.global_tools references unregistered tools: "
                f"{sorted(unknown)}"
            )

    def _build_global_tool_defs(
        self,
        global_tools: list[str],
        tool_registry: ToolRegistry,
    ) -> list[dict]:
        """Resolve ``global_tools`` names to Anthropic-shaped definitions.

        Args:
            global_tools:   Validated list of tool names.
            tool_registry:  Registry that produces definition dicts.

        Returns:
            List of tool definitions — empty list when ``global_tools`` is empty.
        """
        if not global_tools:
            return []
        return tool_registry.get_definitions_for(global_tools)
```

- [ ] **Step 5: Wire `global_tool_defs` into the constructed `AgentWorkflow`**

In `load()`, after the existing `tool_defs = self._build_tool_defs(...)` block (around line 263), add:

```python
        global_tool_defs: list[dict] = self._build_global_tool_defs(
            global_tools_raw, tool_registry
        )
```

And pass it through on construction (around line 270):

```python
        workflow = AgentWorkflow(
            workflow_id=workflow_id,
            version=version,
            agent_system_prompt=agent_system_prompt,
            global_intents=global_intents,
            global_routing=global_routing,
            default_fallback_subagent_id=default_fallback_subagent_id,
            subagents=subagents,
            start_subagent_id=start_subagent_id,
            nlu_intent_set=nlu_intent_set,
            tool_defs=tool_defs,
            global_tool_defs=global_tool_defs,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_workflow_loader.py -v`
Expected: PASS for the five new tests; all pre-existing tests still green (there are ~45 in this file).

- [ ] **Step 7: Commit**

```bash
git add agent_core/src/workflow_loader.py agent_core/tests/test_workflow_loader.py
git commit -m "feat(agent_core): resolve global_tools and expose resolve_tools_for (#182)"
```

---

## Phase 2 — Orchestrator wiring

### Task 3: Use `resolve_tools_for` at the sync LLM-call site

**Files:**
- Modify: `agent_core/src/orchestrator.py:908`
- Test: `agent_core/tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_orchestrator.py`. Uses the existing `_make_agent` / `_make_workflow` helpers — look at the top of the file to confirm names. Tests the LLM receives the resolver's output.

```python
def test_active_tools_use_global_tool_defs_when_set():
    """When workflow.global_tool_defs is non-empty, every turn injects that list."""
    workflow = _make_workflow()
    # Monkey-patch the live workflow instance (frozen dict-style dataclass).
    workflow.global_tool_defs = [
        {"name": "shared_tool", "description": "", "input_schema": {}},
    ]
    agent = _make_agent(workflow=workflow)
    agent.process_turn(_make_turn_input(user_message="hi"))
    call_kwargs = agent._llm.call.call_args.kwargs
    tool_names = [t["name"] for t in call_kwargs["tools"]]
    assert tool_names == ["shared_tool"]


def test_active_tools_fall_back_to_subagent_tool_defs_when_global_empty():
    workflow = _make_workflow()
    workflow.global_tool_defs = []
    agent = _make_agent(workflow=workflow)
    agent.process_turn(_make_turn_input(user_message="hi"))
    # Default workflow fixture puts no tools on the start subagent; the important
    # assertion is that we did not inject the global list when it was empty.
    call_kwargs = agent._llm.call.call_args.kwargs
    assert call_kwargs["tools"] == workflow.tool_defs.get(workflow.start_subagent_id, [])
```

Check lines ~150-240 of `test_orchestrator.py` for `_make_workflow`, `_make_turn_input`, and `_make_agent`. If those helpers don't match these names exactly, adapt the calls to match (the test file's existing tests show the real names).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py::test_active_tools_use_global_tool_defs_when_set tests/test_orchestrator.py::test_active_tools_fall_back_to_subagent_tool_defs_when_global_empty -v`
Expected: FAIL — `active_tools` is still computed via `self._workflow.tool_defs.get(next_subagent_id, [])`.

- [ ] **Step 3: Swap the call site**

In `agent_core/src/orchestrator.py` line 908, change:

```python
        active_tools = self._workflow.tool_defs.get(next_subagent_id, [])
```

to:

```python
        active_tools = self._workflow.resolve_tools_for(next_subagent_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k "active_tools or process_turn"`
Expected: PASS for the two new tests; all pre-existing orchestrator tests still green.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "refactor(agent_core): resolve per-turn tools via workflow.resolve_tools_for (sync) (#182)"
```

---

### Task 4: Use `resolve_tools_for` at the streaming LLM-call site

**Files:**
- Modify: `agent_core/src/orchestrator.py:2647`

No new test — the streaming path has its own broader integration tests; this is a one-line parity change with the sync path.

- [ ] **Step 1: Swap the call site**

In `agent_core/src/orchestrator.py` line 2647, change:

```python
            active_tools = self._workflow.tool_defs.get(next_subagent_id, [])
```

to:

```python
            active_tools = self._workflow.resolve_tools_for(next_subagent_id)
```

- [ ] **Step 2: Run the streaming test suite**

Run: `cd agent_core && uv run pytest tests/test_stream_turn.py tests/test_stream_events.py tests/test_stream_endpoint.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add agent_core/src/orchestrator.py
git commit -m "refactor(agent_core): resolve per-turn tools via workflow.resolve_tools_for (streaming) (#182)"
```

---

### Task 5: Post-tool hook — `apply_job` success flips session to `post_applied`

**Files:**
- Modify: `agent_core/src/orchestrator.py` (sync path: insert immediately after the tool-result → journey-event block that ends around line 1029, before the Step 10 Trust check at line 1030)
- Test: `agent_core/tests/test_orchestrator.py`

Contract: if any returned tool result has `tool_name == "apply_job"` and `success == True`, the orchestrator writes `current_subagent_id = "post_applied"` to memory and mirrors it to `bundle.session`. The flip only applies when the subagent id `post_applied` exists in the workflow (so the framework stays domain-agnostic — other domains without that id are unaffected).

- [ ] **Step 1: Write the failing test**

Append to `agent_core/tests/test_orchestrator.py`:

```python
def test_apply_job_success_moves_session_to_post_applied():
    """When apply_job returns success, the next-turn subagent becomes post_applied."""
    from src.models import ToolResult

    # Build a workflow that contains a post_applied subagent.
    workflow = _make_workflow(extra_subagents=[{
        "id": "post_applied",
        "name": "Post Applied",
        "is_start": False,
        "is_terminal": True,
        "valid_intents": [],
        "tools": [],
        "system_prompt": "You are in post-apply follow-up.",
        "routing": [],
    }])
    agent = _make_agent(
        workflow=workflow,
        manager_tool_calls=[{"tool_name": "apply_job", "input": {}}],
    )
    # Override manager.run_turn to return a successful apply_job ToolResult.
    agent._manager_agent.run_turn.return_value = (
        "Applied successfully.",
        [{"tool_name": "apply_job", "input": {}}],
        [ToolResult(tool_name="apply_job", success=True, result={"applied": True})],
    )

    agent.process_turn(_make_turn_input(user_message="haan apply kar do"))

    # Memory should have been written with post_applied as current_subagent_id
    # at least once after the tool loop completes.
    writes = [
        c.args for c in agent._memory.write_state.call_args_list
        if len(c.args) >= 4 and c.args[3] == "current_subagent_id"
    ]
    assert any(w[4] == "post_applied" for w in writes)


def test_apply_job_failure_does_not_move_session():
    from src.models import ToolResult

    workflow = _make_workflow(extra_subagents=[{
        "id": "post_applied",
        "name": "Post Applied",
        "is_start": False,
        "is_terminal": True,
        "valid_intents": [],
        "tools": [],
        "system_prompt": "x",
        "routing": [],
    }])
    agent = _make_agent(workflow=workflow)
    agent._manager_agent.run_turn.return_value = (
        "Apply failed.",
        [{"tool_name": "apply_job", "input": {}}],
        [ToolResult(tool_name="apply_job", success=False, result={"error": "upstream"})],
    )

    agent.process_turn(_make_turn_input(user_message="haan apply kar do"))

    writes = [
        c.args for c in agent._memory.write_state.call_args_list
        if len(c.args) >= 4 and c.args[3] == "current_subagent_id"
    ]
    assert not any(w[4] == "post_applied" for w in writes)


def test_apply_job_hook_skipped_when_post_applied_subagent_missing():
    """Framework stays domain-agnostic — no crash when workflow has no post_applied."""
    from src.models import ToolResult

    workflow = _make_workflow()  # no post_applied
    agent = _make_agent(workflow=workflow)
    agent._manager_agent.run_turn.return_value = (
        "ok",
        [{"tool_name": "apply_job", "input": {}}],
        [ToolResult(tool_name="apply_job", success=True, result={})],
    )

    # Should not raise
    agent.process_turn(_make_turn_input(user_message="apply"))
```

Before writing these, confirm two things in `test_orchestrator.py` and `src/models.py`:
1. How `_make_workflow` accepts extra subagents — if its signature differs, inline a minimal loader-based workflow fixture.
2. Exact shape of `ToolResult` (fields `tool_name`, `success`, `result`). If the current dataclass names differ, fix the imports and attribute names in the tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py::test_apply_job_success_moves_session_to_post_applied tests/test_orchestrator.py::test_apply_job_failure_does_not_move_session tests/test_orchestrator.py::test_apply_job_hook_skipped_when_post_applied_subagent_missing -v`
Expected: FAIL — no hook exists yet.

- [ ] **Step 3: Implement the hook in the sync path**

In `agent_core/src/orchestrator.py`, right after the tool-result → journey-event loop (immediately before `# ── Step 10: Trust check on output ────────────────────────────` at line 1030), insert:

```python
        # Post-tool hook: apply_job success moves the user to the post_applied
        # subagent for the NEXT turn. The current turn's response was already
        # produced under the commitment subagent's system prompt — intended.
        # Hook is a no-op when the workflow has no post_applied subagent, so
        # the framework stays domain-agnostic.
        if tool_results and "post_applied" in self._workflow.subagents:
            for tr in tool_results:
                if getattr(tr, "tool_name", None) == "apply_job" and getattr(tr, "success", False):
                    self._write_memory_sync(
                        session_id, user_id, "session",
                        "current_subagent_id", "post_applied",
                    )
                    bundle.session["current_subagent_id"] = "post_applied"
                    logger.info(
                        "orchestrator.post_applied_transition",
                        extra={
                            "operation": "orchestrator.post_tool_hook",
                            "status": "success",
                            "session_id": session_id,
                            "trigger_tool": "apply_job",
                        },
                    )
                    break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_orchestrator.py -v -k "apply_job"`
Expected: PASS for the three new tests; all pre-existing tests still green.

- [ ] **Step 5: Mirror the hook into the streaming path**

Find the matching location in the streaming path. Run: `cd agent_core && grep -n "tool_result_mappings\|tool_results" src/orchestrator.py | awk -F: '$1>2000 {print}'` and locate the equivalent post-tool-loop block between line 2700 and 2820. Insert the same block (same body, referencing `tool_results` variable in scope). Keep diff minimal — the logic is identical.

If the streaming path's tool_results variable has a different name (e.g. `_current_tool_results`), adapt. Do not add a new variable.

- [ ] **Step 6: Run the streaming test suite**

Run: `cd agent_core && uv run pytest tests/test_stream_turn.py tests/test_stream_events.py tests/test_stream_endpoint.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "feat(agent_core): flip session to post_applied on apply_job success (#182)"
```

---

## Phase 3 — KKB config rewrite

### Task 6: Replace `agent_workflow.subagents` in the KKB config

**Files:**
- Modify: `dev-kit/configs/kkb/agent_core.yaml` (lines ~504-1533)

This is the large domain edit. Keep everything above `agent_workflow:` (agent, channels, conversation, connectors, preprocessing) untouched in this task — we modify `preprocessing.nlu_processor.intents` + `signal_intents` in Task 7.

- [ ] **Step 1: Replace the entire `agent_workflow:` block**

In `dev-kit/configs/kkb/agent_core.yaml`, replace the `agent_workflow:` block (from line ~504 through the last subagent definition around line ~1533) with:

```yaml
agent_workflow:
  workflow_id: kkb_journey
  version: "2.0.0"

  # Shared across every turn — tool invocation_rules (in connectors.*) enforce
  # preconditions, so tool access is no longer scoped per-subagent.
  global_tools:
    - get_profile
    - update_profile
    - onest_market_lookup
    - knowledge_retrieval
    - apply_job
    - end_session
    # - counsellor_schedule   # enable once action_gateway connector is live

  tool_result_mappings:
    onest_market_lookup:
      journey_event_label: Role
      result_list_key: data.items
      field_map:
        role_id: job.job_id
        title: job.beckn_structure.tags.jobDetails.title
        employer: job.beckn_structure.tags.basicInfo.jobProviderName
        pay_min: job.beckn_structure.tags.jobDetails.minMonthlyInHand
        pay_max: job.beckn_structure.tags.jobDetails.maxMonthlyInHand
        city: job.beckn_structure.locations.city
        openings: job.beckn_structure.tags.jobDetails.positions

  agent_system_prompt: |
    You are काम की बात — a calm, grounded, fact-based female voice guide for Indian workers.

    Your job is NOT to sell hope, motivate, or push decisions. Your job is to show the labour
    market clearly, so the user can decide with dignity.

    ## Tone
    Practical. Steady. Respectful. Regionally familiar. Honest about trade-offs.
    Never bureaucratic. Never form-like. Never promotional.

    ## Core belief
    I am not here to correct the user or decide for them. I am here to show
    the true picture of the market, honestly, so they can choose.

    ## What to always preserve
    - Truth over persuasion — if the signal is weak, say it is weak.
    - Clarity over completeness — do not say everything at once.
    - Agency over pressure — the user decides.
    - Dignity over conversion — a user who understands the market and chooses not to act is still a good outcome.
    - Trade-off over simplification — if there is a downside, say it clearly.

    ## Tool invocation
    Each tool's invocation_rules are authoritative. Follow required_before_calling,
    must_not_substitute, on_empty, and on_failure exactly. Stored memory, prior options,
    and summaries must NEVER substitute for a fresh tool call on current availability.

    ## User mental state
    The active state's behavioural guidance is injected at runtime (see <user_state_guidance>).
    Adapt tone, detail level, and pacing accordingly. Mental state is inferred — never label it aloud.

    ## Caller personas (never label aloud; infer gradually)
    ITI graduate first-job seeker, woman returning to work, daily wage labourer,
    displaced formal-sector worker, person with disability, proxy caller, confused/undecided caller.

  global_intents:
    - termination_intent

  global_routing:
    - intent: termination_intent
      next_subagent_id: ended

  default_fallback_subagent_id: clarification

  subagents:

    # ─────────────────────────────────────────────────────────────
    # ENQUIRY — exploring the landscape
    # ─────────────────────────────────────────────────────────────
    - id: enquiry
      name: Enquiry
      description: >
        User is exploring the labour market. Collect the minimum profile (trade +
        location), then show the market picture. Absorbs former profile_building
        and market_truth.
      is_start: true
      is_terminal: false
      opening_phrase: "नमस्ते। काम की बात में आपका स्वागत है। यह बातचीत रिकॉर्ड की जा सकती है। आप काम, स्किल या करियर के बारे में बात करना चाहते हैं?"

      valid_intents:
        - any_input
        - evaluate_option
        - apply_now
        - pay_disappointment
        - distance_issue
        - overwhelmed_silent
        - counsellor_request
        - language_switch_request

      tools: []   # ignored — global_tools is in effect

      system_prompt: |
        You are in the **enquiry** stage. The user is exploring; nothing has been chosen yet.

        ## On session start
        1. If session has no profile, call `get_profile` with the user's phone number.
        2. Open with a short, natural greeting. If the profile already has trade + location,
           ask whether to show the current market picture now.
        3. Never ask for user_id, full address, or government ID.

        ## Minimum viable fetch set
        Before calling `onest_market_lookup` you need:
          - location
          - trade_or_stream
          - user consent to fetch
        Income and commute are optional filters on top of a valid fetch.

        ## Data gathering
        Ask only what changes the next useful answer. Never ask for everything upfront.
        Never ask more than two questions before showing something if a job-search path is active.
        Commute and income are refinement fields — ask only after the first list, unless volunteered earlier.

        ## Market picture delivery
        - Use ONLY `onest_market_lookup` results. Never invent roles, employers, or pay figures.
        - Present exact salary ranges (lower → higher). If min > max, silently swap.
        - Never present jobs with is_active=false, status≠open, or positions=null/0.
        - Never speak GPS, hiring-manager phone, internal IDs, or match_score.
        - Short-list the top 3 in one compact spoken block; end with a question inviting selection.

        ## If the user selects or drills into one option
        The natural next step is the commitment stage — trade-offs, skill fit, consent, apply.
        Let them guide you there.

        ## Hesitation signals
        Pay concerns, distance concerns, overwhelm, wanting time — acknowledge warmly,
        optionally offer a WhatsApp summary via text, and stay in enquiry unless the user
        explicitly wants to compare a specific path (→ commitment).

        ## Emergency / immediate-work mode
        If same-day or next-day work is clearly needed: location → trade → fetch → skip non-essentials.

        ## Empty / scarce market
        Be honest: "इस वक्त इस एरिया में verified listing नहीं मिली।" Offer wider radius,
        adjacent trade, or training path. Do not pretend scarcity is opportunity.

      routing:
        # 5-turn safety net — if we still don't have minimums, escalate.
        - intent: "*"
          conditions:
            - field: subagent_entry_count.enquiry
              operator: gt
              value: 4
            - field: trade_or_stream
              operator: eq
              value: null
          next_subagent_id: escalation
        - intent: "*"
          conditions:
            - field: subagent_entry_count.enquiry
              operator: gt
              value: 4
            - field: location
              operator: eq
              value: null
          next_subagent_id: escalation

        - intent: evaluate_option
          next_subagent_id: commitment
        - intent: apply_now
          next_subagent_id: commitment

        - intent: "*"
          next_subagent_id: enquiry

    # ─────────────────────────────────────────────────────────────
    # COMMITMENT — engaged with a specific path
    # ─────────────────────────────────────────────────────────────
    - id: commitment
      name: Commitment
      description: >
        User is engaged with a specific option: compare, fit-check, trade-off, consent, apply.
        Absorbs former skill_check, evaluation, pay_branch, distance_branch, normalise_branch,
        old commitment.
      is_start: false
      is_terminal: false
      opening_phrase: "नमस्ते। काम की बात में आपका स्वागत है। पिछली बार options compare कर रहे थे — आगे बढ़ें?"

      valid_intents:
        - any_input
        - apply_now
        - explore_more
        - pay_disappointment
        - distance_issue
        - overwhelmed_silent
        - counsellor_request
        - language_switch_request

      tools: []   # ignored — global_tools is in effect

      system_prompt: |
        You are in the **commitment** stage. The user is engaged with a specific path
        and moving between compare / fit-check / consent / apply.

        ## Profile persistence on entry
        If `consent == true` in the session and the profile has changes since last write,
        call `update_profile` once at the start of this stage — then continue.

        ## Skill fit (in-prompt, no separate subagent)
        Using the known profile and the ONEST results, classify honestly:
          - DIRECT MATCH — "good news, you are a direct match for these roles."
          - PARTIAL MATCH — state the gap ("certificate", "specific skill") and how to close it.
          - SIGNIFICANT GAP — if income is urgent: bridge income + parallel training.
                            if flexible: training path first.
        Present as honest trade-offs. Never push. Do not invent "Private Contractors" /
        "Local Projects" — only what ONEST returned. Use exact salary ranges from the tool.

        ## Deep dive on a selected option
        Spoken format: "[employer], [locality], [city] — लगभग [distance] किलोमीटर दूर.
        [nature], [salary range], [positions] positions. [qualification] चाहिए.
        एक्ज़ैक्ट काम वहाँ जाकर क्लियर होगा."
        End with: "यह ठीक लगता है? अप्लाई कर दूँ?"
        Always include one honest uncertainty line when details are incomplete.

        ## Trade-off framing
        Plain language, name the downside: distance vs pay, immediate income vs growth,
        easy entry vs competition, training cost vs later range.

        ## Persona-weighted framing (apply quietly, never label aloud)
          ITI graduate          → distance, certainty of first income, stepping stone vs dead end.
          Woman returning       → available hours, distance/safety, skill gap after break, dignity.
          Daily wage labourer   → work today, walkable/cheap distance, certainty of payment.
          Displaced formal      → income continuity, dignity, whether prior experience counts.
          Person with disability → role accessibility, respect, realistic remote options.

        ## Pay / distance concerns
        Acknowledge. Test flexibility gently. If expectation is close to market: show the
        upper end and a 1–2 year growth trajectory. If far: state the real rate, offer
        lateral options, do not push. For distance: re-run ONEST with tighter radius if
        needed; mention transport / allowance when known.

        ## Overwhelmed / wants to think
        Short pause → wait. Longer pause → one gentle bridge, not another question.
        After disappointing facts → let truth land. Offer a WhatsApp-style summary via
        text if useful; do not pressure.

        ## Repeated indecision (many turns here)
        Gently probe for external blockers; offer counsellor help as support, not escalation.
        To invoke a counsellor callback, call the counsellor tool when live. Until then,
        acknowledge and say a counsellor can call back.

        ## Apply
        Never apply without explicit user consent. Ask clearly in natural Hindi:
        "क्या मैं आपकी तरफ़ से आगे बढ़ूँ?", "अप्लाई कर दूँ?". Do not pressure. Once consent is
        clear, call `apply_job`. On success, confirm briefly — the orchestrator moves the
        user to post-apply follow-up for the next turn.

        ## If the user changes their mind
        Acknowledge calmly. Use `explore_more` signal by rejoining enquiry for alternatives.

      routing:
        - intent: explore_more
          next_subagent_id: enquiry
        - intent: "*"
          next_subagent_id: commitment

    # ─────────────────────────────────────────────────────────────
    # POST_APPLIED — after apply_job success
    # ─────────────────────────────────────────────────────────────
    - id: post_applied
      name: Post-Applied Follow-Through
      description: >
        Something has already happened — applied, interview, outcome, return after
        time. Do not restart the journey. Absorbs former follow_through.
      is_start: false
      is_terminal: true
      opening_phrase: "नमस्ते। काम की बात में आपका स्वागत है। पिछली अप्लिकेशन पर कोई update है?"

      valid_intents:
        - any_input
        - outcome_positive
        - outcome_negative
        - counsellor_request
        - language_switch_request

      tools: []   # ignored — global_tools is in effect

      system_prompt: |
        You are in **post-apply follow-up**. An application already happened in this or a
        prior session. Continue the journey, do not restart it.

        ## Focus
        - Did the employer call?
        - Did the job match what was described?
        - Did something change in the user's life?
        - Should another option be reopened?

        ## When something fails
        Do not defend. Do not dismiss. Acknowledge:
          "यह सुनकर बुरा लगा। क्या difference था, थोड़ा बताइए."
        Then understand what changed, then reopen options (via knowledge_retrieval /
        onest_market_lookup as needed).

        ## Return after training or life change
        Resume directly with upgraded fit. Update only the changed constraint, re-evaluate.

        ## Emotional acknowledgement (allowed)
        "समझ में आता है.", "हाँ, यह निराश करने वाला लग सकता है.",
        "इस सिचुएशन में काफ़ी लोग ऐसा महसूस करते हैं.", "यह आसान नहीं रहा होगा."

      routing: []   # terminal

    # ─────────────────────────────────────────────────────────────
    # ESCALATION — 5-turn safety net in enquiry
    # ─────────────────────────────────────────────────────────────
    - id: escalation
      name: Counsellor Escalation
      description: >
        Entered when the enquiry 5-turn safety net trips (trade or location still missing).
        Offers a human counsellor and ends the session.
      is_start: false
      is_terminal: true
      opening_phrase: ""

      valid_intents: []
      tools: []

      system_prompt: |
        We have talked for a bit, but trade or location is still unclear — without either,
        I cannot search live jobs. Offer a counsellor callback and close warmly:
          "हम एक काउंसलर से आपको कनेक्ट करेंगे। वो चौबीस घंटे के अंदर कॉल करेंगे।"
        When the `counsellor_schedule` tool is live, call it. Do not ask more discovery questions here.

      routing: []   # terminal

    # ─────────────────────────────────────────────────────────────
    # ENDED — graceful termination
    # ─────────────────────────────────────────────────────────────
    - id: ended
      name: Session Ended
      description: Graceful session termination triggered by user.
      is_start: false
      is_terminal: true
      opening_phrase: ""
      special_handler: null

      valid_intents: []
      tools: []

      system_prompt: |
        The user has chosen to end the call. Always respond in the user's detected language.
        Close warmly — thank them and let them know they are welcome back whenever they need help.
        Flush session state. Emit termination event to Observability Layer.

      routing: []   # terminal

    # ─────────────────────────────────────────────────────────────
    # CLARIFICATION — default fallback
    # ─────────────────────────────────────────────────────────────
    - id: clarification
      name: Clarification
      description: >
        Fallback subagent for unknown or unclassifiable intent.
      is_start: false
      is_terminal: false
      opening_phrase: ""
      special_handler: null

      valid_intents:
        - any_input

      tools: []

      system_prompt: |
        The system could not classify the user's intent. Re-prompt gently. Do not say
        "I don't understand." Reflect what you know and ask an open question, e.g.:
        "हाँ, समझ रही हूँ। थोड़ा और बता दीजिए — काम ढूंढना है, या कुछ specific पूछना था?"
        Keep it short. Return to the main path quickly.

      routing:
        - intent: "*"
          next_subagent_id: clarification
```

- [ ] **Step 2: Verify the config loads by running the schema-validation test**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -v`
Expected: PASS — the extra="forbid" check catches any typoed YAML key.

- [ ] **Step 3: Run the workflow loader against the actual KKB config**

Write a scratch check script (do not commit it — or add as a permanent test if preferred):

```bash
cd agent_core && uv run python -c "
import yaml, json
from pathlib import Path
from src.workflow_loader import AgentWorkflowLoader
from src.tool_registry import ToolRegistry

cfg_path = Path('../dev-kit/configs/kkb/agent_core.yaml')
cfg = yaml.safe_load(cfg_path.read_text())

# Fake registry that reports every name used in the config as registered.
tool_names = set(cfg['agent_workflow'].get('global_tools') or [])
for sa in cfg['agent_workflow']['subagents']:
    tool_names.update(sa.get('tools') or [])

class FakeRegistry:
    def get_tool_names(self):
        return tool_names
    def get_definitions_for(self, names):
        return [{'name': n, 'description': '', 'input_schema': {}} for n in names]

wf = AgentWorkflowLoader().load(cfg, FakeRegistry())
print(json.dumps({
    'subagents': list(wf.subagents.keys()),
    'start': wf.start_subagent_id,
    'global_tools': [d['name'] for d in wf.global_tool_defs],
    'nlu_intents_per_subagent': {k: len(v) for k, v in wf.nlu_intent_set.items()},
}, indent=2))
"
```

Expected output keys match: `subagents` contains `['enquiry', 'commitment', 'post_applied', 'escalation', 'ended', 'clarification']`; `start` is `'enquiry'`; `global_tools` lists 6 names (or 7 if counsellor_schedule has been un-commented).

If the loader raises, fix the YAML error it reports, re-run, repeat.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/configs/kkb/agent_core.yaml
git commit -m "feat(kkb): collapse to 3 journey subagents + 3 infra, wire global_tools (#182)"
```

---

## Phase 4 — NLU intents + signal mapping

### Task 7: Prune `preprocessing.nlu_processor.intents` and update `signal_intents`

**Files:**
- Modify: `dev-kit/configs/kkb/agent_core.yaml` (lines ~382-475, the `preprocessing.nlu_processor` block)
- Update: tests that hard-code old KKB intents (at minimum `agent_core/tests/test_nlu_processor.py` where it constructs a fixture referencing KKB's intent list).

- [ ] **Step 1: Replace `intents:` and `signal_intents:` in the KKB yaml**

In `dev-kit/configs/kkb/agent_core.yaml`, inside `preprocessing.nlu_processor`, replace the `intents:` list (41 entries) with:

```yaml
    intents:
      # Routing intents
      - evaluate_option
      - apply_now
      - explore_more
      - termination_intent
      - language_switch_request
      # Signal intents (no routing; emit to Neo4j Signal node)
      - pay_disappointment
      - distance_issue
      - overwhelmed_silent
      - counsellor_request
      # Post-apply intents
      - outcome_positive
      - outcome_negative
      # Infrastructure
      - any_input
      - unknown
```

Replace `signal_intents:` with:

```yaml
    signal_intents:
      pay_disappointment: objection
      distance_issue: constraint
      overwhelmed_silent: emotion
      counsellor_request: escalation_signal
      outcome_positive: outcome_signal
      outcome_negative: outcome_signal
```

Also update the `domain_instruction:` block of the NLU config so the classifier is told about the new intent semantics — keep it brief, one-paragraph:

```yaml
    domain_instruction: |
      You are an NLU (Natural Language Understanding) classifier for Kaam Ki Baat (KKB),
      an employment assistance chatbot. Classify user turns into one of three classes:
      (1) routing intents that move the user between enquiry / commitment / ended stages;
      (2) signal intents that record objections, constraints, emotions, or outcomes for
          longitudinal analysis but do not change stage;
      (3) infrastructure intents (any_input, unknown) for generic forward progress.
```

Leave `entities`, `sentiment_classes`, thresholds, and model selection unchanged. Add `outcome_reason` to `entities` if you want to capture the post-apply sub-cause; optional in v2.

- [ ] **Step 2: Update affected tests**

Run: `cd agent_core && uv run pytest tests/test_nlu_processor.py -v`
Many tests will likely still pass (most use fabricated configs, not KKB). Any that fail because they reference a pruned intent name: update the test to use an intent from the new set or rewrite the assertion. Do not introduce new production behaviour to satisfy a test — the test should reflect the new taxonomy.

Also scan: `cd agent_core && grep -rn "skill_direct_match\|interested_engaged\|wants_to_think\|profile_answer\|constraint_hard\|expectation_firm\|outcome_employer_ghost" tests/ src/` — any surviving reference to a pruned intent name in code or tests is now dead; delete or rewrite.

- [ ] **Step 3: Run the full agent_core test suite**

Run: `cd agent_core && uv run pytest -v`
Expected: all pass. If a backwards-compat test asserts the old 41-intent shape, update it to the new shape — this is an intentional breaking change for the KKB domain, and no other domain depends on these specific intent names.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/configs/kkb/agent_core.yaml agent_core/tests/
git commit -m "feat(kkb): prune NLU intents to 13 and remap signal_intents (#182)"
```

---

## Phase 5 — Integration smoke

### Task 8: Manual smoke on the reach_layer CLI

Not a code change; records the evidence that the whole thing works end-to-end before marking the issue complete. Docker stack already runs on the dev VM (see memory note — don't run `docker compose build/up` locally).

- [ ] **Step 1: Trigger a rebuild of agent_core + reach_layer CLI on the dev stack**

Ask the stack owner to rebuild and restart the `agent_core` and `reach_layer` services on the dev VM after pushing this branch. From the VM, tail the agent_core logs:

```bash
docker logs -f agent_core 2>&1 | grep -E "\[STEP|current_subagent_id|global_tool|apply_job|post_applied"
```

- [ ] **Step 2: Run the scripted smoke script via the reach_layer CLI**

Seed a session with a known trade + location (e.g. Hubballi / Electrician), then run the CLI through this scripted sequence and capture the logs:

1. "नमस्ते" — expect `current_subagent_id=enquiry` and a `get_profile` tool call.
2. "मैं Hubballi में electrician हूँ" — expect entity writes for `trade_or_stream` and `location`, then an `onest_market_lookup` tool call on the next user prompt.
3. "current market picture दिखाओ" — expect top-3 option spoken block.
4. "पहला वाला ठीक है, apply कर दो" — expect intent `apply_now`, routing to `commitment`, then `apply_job` tool call and success.
5. After apply success: send "thanks" — expect `current_subagent_id=post_applied` in logs for this turn (via the post-tool hook fired in turn 4).
6. "termination_intent"-shaped utterance (e.g. "bye") — expect `ended` and session flush.

Capture each turn's final log line and paste into the PR description.

- [ ] **Step 3: Negative path — 5-turn escalation**

Start a fresh session. Avoid giving trade or location. Feed 5 vague turns ("hello", "काम चाहिए", "कुछ बताओ", etc.). On turn 6, expect `current_subagent_id=escalation` and the session to end with a counsellor callback message.

- [ ] **Step 4: Record results in the GH issue**

Comment on issue #182 with:
- The two log excerpts (happy path + escalation).
- Any unexpected behaviour you saw and a short judgement on whether it blocks merge.

- [ ] **Step 5: Open PR**

```bash
git push -u origin gh182-kkb-subagent-collapse
gh pr create --title "KKB: collapse subagents (14→3) and prune intents (41→13) (#182)" --body "$(cat <<'EOF'
## Summary
- Collapse 14 KKB subagents to 3 journey + 3 infrastructure subagents
- Prune 41 NLU intents to 13 and remap signal_intents
- Add `global_tools` on `agent_workflow`; orchestrator resolves via `workflow.resolve_tools_for(subagent_id)`
- Orchestrator post-tool hook: `apply_job` success flips session to `post_applied`

Spec: `docs/superpowers/specs/2026-04-23-kkb-subagent-collapse-design.md`
Closes #182

## Test plan
- [ ] `cd agent_core && uv run pytest`
- [ ] Manual smoke on dev VM (captures above)
- [ ] 5-turn escalation path (capture above)
EOF
)"
```

---

## Self-review notes (recorded from plan authoring)

- **Spec coverage:** Every goal in the spec (§Design sections 1–7) maps to a task in phases 1–4. Drop-off inference at session flush is left to Observability — spec §1 explicitly delegates this, no new plan task needed.
- **Schema compat:** `extra="forbid"` on `AgentWorkflowConfig` means the first run against the new YAML will fail loudly if any KKB key has drifted — good. The existing `_validate_tool_names` is unchanged; new `_validate_global_tool_names` covers the new field specifically.
- **Idempotent hook:** the `apply_job` post-tool hook writes the same subagent id regardless of how many apply_job successes appear in `tool_results` (break on first). Failed apply_job leaves the session in `commitment`.
- **Streaming parity:** Task 4 + Task 5 Step 5 mirror the same two behaviour changes into the streaming path; streaming's own tests guard against regressions.
- **Counsellor tool:** `counsellor_schedule` stays commented in the YAML; `escalation` subagent's system prompt handles the degraded case by describing the callback verbally. When the connector is wired up in a future PR, just un-comment the name in `global_tools`.
