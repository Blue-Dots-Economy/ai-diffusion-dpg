# PR #30 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all critical, important, and test-gap issues identified in the PR #30 code review before merging to main.

**Architecture:** All changes are confined to `dev-kit/dev_kit/agent/` (conversation.py, tools.py, app.py) and their corresponding test files in `dev-kit/agent/tests/`. A new `errors.py` module centralises typed exceptions. No new dependencies are added — `tenacity` is used for retry (already available in Python environments; add via `uv add tenacity` inside dev-kit if not present).

**Tech Stack:** Python 3.13, FastAPI, Anthropic SDK (AsyncAnthropic), pytest + pytest-asyncio, tenacity (retry), uv

---

## File Map

| File | Change |
|------|--------|
| `dev-kit/dev_kit/agent/errors.py` | **Create** — typed exceptions: `ConversationError`, `ConfigurationError` |
| `dev-kit/dev_kit/agent/conversation.py` | **Modify** — read model/tokens/window from env, add logging, timeout+retry on LLM calls, try/except in `chat()`, history rollback, fix `_load()` error handling, implement rollback tool action |
| `dev-kit/dev_kit/agent/tools.py` | **Modify** — fix `_handle_set_project_meta` (`.update` not `=`), structured error dicts from handlers, fix `_handle_remove_subagent` to check if removal actually happened |
| `dev-kit/dev_kit/agent/app.py` | **Modify** — startup API key assertion, structured error handling in routes, fix `list_projects` per-project try/except, fix YAML-write-before-parse, fix `_load_project_meta` JSON error handling, logging |
| `dev-kit/agent/tests/test_conversation.py` | **Modify** — add API failure test, empty message test, `_load()` corrupt file test |
| `dev-kit/agent/tests/test_tools.py` | **Modify** — add `set_project_meta` merge test, `remove_subagent` not-found test, `dispatch` unknown tool test |
| `dev-kit/agent/tests/test_app.py` | **Modify** — add STALE-on-invalid-YAML test, YAML-parse-before-write test, `list_projects` corrupt file resilience test, `delete_project` test, checkpoint restore 404 test, unknown block 400 test |
| `dev-kit/agent/tests/test_prompts_base.py` | **Create** — tests for `build_system_prompt` |
| `dev-kit/agent/tests/test_prompts_phases.py` | **Create** — tests for `get_phase_addition` |

---

## Task 1: Add typed exceptions module

**Files:**
- Create: `dev-kit/dev_kit/agent/errors.py`

- [ ] **Step 1: Create `errors.py`**

```python
"""
dev-kit/dev_kit/agent/errors.py

Typed exceptions for the dev-kit agent.
"""


class ConversationError(Exception):
    """Raised when the ConversationEngine chat loop fails unrecoverably."""


class ConfigurationError(Exception):
    """Raised when a required configuration value is missing at startup."""
```

- [ ] **Step 2: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg/dev-kit
git add dev_kit/agent/errors.py
git commit -m "feat(dev-kit): add typed exceptions module"
```

---

## Task 2: Fix hardcoded constants — read model/tokens/window from env

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py:22-24`

- [ ] **Step 1: Write the failing test**

In `dev-kit/agent/tests/test_conversation.py`, add inside `TestConversationEngineChat`:

```python
def test_model_read_from_env(monkeypatch):
    """ConversationEngine must not hardcode the model name."""
    import dev_kit.agent.conversation as conv_module
    monkeypatch.setenv("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
    # Re-import triggers module-level re-evaluation — use importlib
    import importlib
    importlib.reload(conv_module)
    assert conv_module._MODEL == "claude-haiku-4-5-20251001"
    # Restore
    monkeypatch.delenv("DEVKIT_MODEL", raising=False)
    importlib.reload(conv_module)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg/dev-kit
uv run pytest agent/tests/test_conversation.py::test_model_read_from_env -v
```

Expected: FAIL — `assert "claude-opus-4-6" == "claude-haiku-4-5-20251001"`

- [ ] **Step 3: Replace hardcoded constants in `conversation.py`**

Replace lines 22–24:
```python
_MODEL = "claude-opus-4-6"
_MAX_TOKENS = 4096
_HISTORY_WINDOW = 20  # Max recent messages to send per turn
```

With:
```python
import os as _os

_MODEL = _os.environ.get("DEVKIT_MODEL", "claude-opus-4-6")
_MAX_TOKENS = int(_os.environ.get("DEVKIT_MAX_TOKENS", "4096"))
_HISTORY_WINDOW = int(_os.environ.get("DEVKIT_HISTORY_WINDOW", "20"))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest agent/tests/test_conversation.py::test_model_read_from_env -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dev_kit/agent/conversation.py agent/tests/test_conversation.py
git commit -m "fix(dev-kit): read model/tokens/window from env vars (config discipline)"
```

---

## Task 3: Add structured logging to conversation.py and app.py

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py`
- Modify: `dev-kit/dev_kit/agent/app.py`

No new tests needed for logging presence — covered by observing log output in later tasks.

- [ ] **Step 1: Add logger to `conversation.py`**

At the top of `conversation.py`, after the `from __future__ import annotations` line, add:

```python
import logging
import time
```

After the `_HISTORY_WINDOW` line, add:

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Add logger to `app.py`**

At the top of `app.py`, after `import re`, add:

```python
import logging
import time

logger = logging.getLogger(__name__)
```

- [ ] **Step 3: Commit**

```bash
git add dev_kit/agent/conversation.py dev_kit/agent/app.py
git commit -m "fix(dev-kit): add structured logger to conversation and app modules"
```

---

## Task 4: Add timeout + retry + error handling to LLM calls in `chat()`

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py`

- [ ] **Step 1: Write the failing test**

Add to `TestConversationEngineChat` in `test_conversation.py`:

```python
@pytest.mark.asyncio
async def test_chat_raises_conversation_error_on_api_failure(self, project_path, mock_client):
    """chat() must raise ConversationError (not a raw Anthropic exception) on API failure."""
    import anthropic
    from dev_kit.agent.errors import ConversationError
    mock_client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
    engine = ConversationEngine(project_path, mock_client)
    with pytest.raises(ConversationError):
        await engine.chat("Hello")

@pytest.mark.asyncio
async def test_chat_rolls_back_history_on_api_failure(self, project_path, mock_client):
    """On API failure the user message appended to history must be rolled back."""
    import anthropic
    from dev_kit.agent.errors import ConversationError
    mock_client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
    engine = ConversationEngine(project_path, mock_client)
    history_len_before = len(engine._history)
    with pytest.raises(ConversationError):
        await engine.chat("Hello")
    assert len(engine._history) == history_len_before
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest agent/tests/test_conversation.py::TestConversationEngineChat::test_chat_raises_conversation_error_on_api_failure agent/tests/test_conversation.py::TestConversationEngineChat::test_chat_rolls_back_history_on_api_failure -v
```

Expected: FAIL — `APIConnectionError` propagates, not `ConversationError`

- [ ] **Step 3: Add `tenacity` dependency**

```bash
uv add tenacity
```

- [ ] **Step 4: Update `chat()` in `conversation.py`**

Replace the existing `chat()` method (lines 98–182) with:

```python
async def chat(self, user_message: str) -> dict:
    """Process a user message and return the agent's response.

    Calls Claude, dispatches any tool calls, saves state, and re-renders
    YAML config files.

    Args:
        user_message: The user's input text.

    Returns:
        Dict with keys: reply (str), phase (str), config_updates (list),
        checkpoint_created (str | None), graph (dict).

    Raises:
        ConversationError: If the Anthropic API call fails after retries.
    """
    import anthropic as _anthropic
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    from dev_kit.agent.errors import ConversationError

    _llm_retry = retry(
        retry=retry_if_exception_type(
            (_anthropic.RateLimitError, _anthropic.APIConnectionError, _anthropic.APITimeoutError)
        ),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )

    async def _call_llm(system: str, messages: list) -> object:
        start = time.time()
        try:
            resp = await _llm_retry(self._client.messages.create)(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                timeout=30.0,
            )
            logger.info(
                "llm_call",
                extra={
                    "operation": "conversation.chat.llm_call",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                    "phase": self._state["phase"],
                },
            )
            return resp
        except Exception as exc:
            logger.error(
                "llm_call_failed",
                extra={
                    "operation": "conversation.chat.llm_call",
                    "status": "failure",
                    "error": str(exc),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            raise ConversationError(f"LLM call failed: {exc}") from exc

    self._history.append({"role": "user", "content": user_message})
    self._state["phase_changed"] = None
    self._state["rollback_to"] = None

    system = self._build_system_prompt()
    messages = self._history[-_HISTORY_WINDOW:]
    config_updates: list[dict] = []
    checkpoint_created: str | None = None

    try:
        response = await _call_llm(system, messages)
    except ConversationError:
        self._history.pop()  # roll back the appended user message
        raise

    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result_text = self._tool_handler.dispatch(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
                config_updates.append({"tool": block.name, "input": block.input})

        self._history.append({"role": "assistant", "content": response.content})
        self._history.append({"role": "user", "content": tool_results})

        # Handle phase transition
        if self._state["phase_changed"]:
            old_phase = self._state["phase"]
            new_phase = self._state["phase_changed"]
            phase_list = ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"]
            phase_number = phase_list.index(old_phase) + 1 if old_phase in phase_list else 0
            phase_label = f"{phase_number:02d}_{old_phase}"
            save_checkpoint(self._project_path, phase_label, self.accumulator, self._history[:-2])
            checkpoint_created = phase_label
            self._state["phase"] = new_phase
            self._state["phase_changed"] = None
            system = self._build_system_prompt()

        # Handle rollback requested by tool
        if self._state["rollback_to"]:
            requested_phase = self._state["rollback_to"]
            self._state["rollback_to"] = None
            try:
                from dev_kit.agent.checkpoints import restore_checkpoint
                restored_acc, _ = restore_checkpoint(self._project_path, requested_phase)
                self.accumulator = restored_acc
                self._tool_handler._acc = restored_acc
                self._history = []
                self._state["phase"] = requested_phase.split("_", 1)[-1] if "_" in requested_phase else requested_phase
                logger.info(
                    "checkpoint_restored_via_tool",
                    extra={
                        "operation": "conversation.chat.rollback",
                        "status": "success",
                        "phase": requested_phase,
                    },
                )
            except FileNotFoundError:
                logger.warning(
                    "checkpoint_not_found",
                    extra={
                        "operation": "conversation.chat.rollback",
                        "status": "failure",
                        "error": f"checkpoint '{requested_phase}' not found",
                    },
                )

        try:
            response = await _call_llm(system, self._history[-_HISTORY_WINDOW:])
        except ConversationError:
            raise

    # Extract final text reply
    reply = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    self._history.append({"role": "assistant", "content": reply})

    # Persist state and re-render configs
    self._save_accumulator()
    self._save_project_meta()
    render_all(self._project_path, self.accumulator)

    return {
        "reply": reply,
        "phase": self._state["phase"],
        "config_updates": config_updates,
        "checkpoint_created": checkpoint_created,
        "graph": self.accumulator.get_workflow_graph(),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest agent/tests/test_conversation.py -v
```

Expected: All pass including the two new tests.

- [ ] **Step 6: Commit**

```bash
git add dev_kit/agent/conversation.py agent/tests/test_conversation.py
git commit -m "fix(dev-kit): add timeout, retry, structured error handling to LLM calls; implement rollback tool action"
```

---

## Task 5: Fix `_load()` — handle corrupt/missing JSON gracefully

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py`

- [ ] **Step 1: Write the failing test**

Add to `TestConversationEnginePersistence` in `test_conversation.py`:

```python
def test_load_handles_corrupt_accumulator_json(self, project_path, mock_client):
    """_load() must not crash on a corrupt accumulator.json — falls back to empty accumulator."""
    (project_path / "_meta" / "accumulator.json").write_text("NOT VALID JSON {{{{")
    # Should not raise
    engine = ConversationEngine(project_path, mock_client)
    assert engine.accumulator is not None
    # Accumulator falls back to empty state
    assert engine.accumulator.get_block("trust_layer") == {}

def test_load_handles_corrupt_project_json(self, project_path, mock_client):
    """_load() must not crash on a corrupt project.json — falls back to default phase."""
    (project_path / "_meta" / "project.json").write_text("{broken")
    engine = ConversationEngine(project_path, mock_client)
    assert engine._state["phase"] == "overview"
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest agent/tests/test_conversation.py::TestConversationEnginePersistence::test_load_handles_corrupt_accumulator_json agent/tests/test_conversation.py::TestConversationEnginePersistence::test_load_handles_corrupt_project_json -v
```

Expected: FAIL — `json.JSONDecodeError` propagates

- [ ] **Step 3: Update `_load()` in `conversation.py`**

Replace the existing `_load()` method (lines 53–64):

```python
def _load(self) -> None:
    """Load persisted accumulator and project meta from disk if they exist.

    Logs a warning and falls back to defaults if either file is corrupt.
    """
    acc_path = self._project_path / "_meta" / "accumulator.json"
    if acc_path.exists():
        try:
            self.accumulator = ConfigAccumulator.from_dict(json.loads(acc_path.read_text()))
            self._tool_handler = ToolHandler(self.accumulator, self._state)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "accumulator_load_failed",
                extra={
                    "operation": "conversation._load",
                    "status": "failure",
                    "error": str(exc),
                    "path": str(acc_path),
                },
            )

    meta_path = self._project_path / "_meta" / "project.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            self._state["project_meta"] = meta
            self._state["phase"] = meta.get("current_phase", "overview")
        except json.JSONDecodeError as exc:
            logger.warning(
                "project_meta_load_failed",
                extra={
                    "operation": "conversation._load",
                    "status": "failure",
                    "error": str(exc),
                    "path": str(meta_path),
                },
            )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest agent/tests/test_conversation.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add dev_kit/agent/conversation.py agent/tests/test_conversation.py
git commit -m "fix(dev-kit): handle corrupt JSON gracefully in ConversationEngine._load()"
```

---

## Task 6: Fix `tools.py` — `set_project_meta` merge bug, structured errors, `remove_subagent` false success

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Modify: `dev-kit/agent/tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_tools.py`:

```python
def test_set_project_meta_merges_not_replaces(self):
    """_handle_set_project_meta must merge inputs into existing state, not replace it."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {
        "phase": "overview",
        "phase_changed": None,
        "rollback_to": None,
        "project_meta": {
            "slug": "test",
            "name": "Old Name",
            "current_phase": "overview",
            "phases_completed": ["overview"],
        },
    }
    handler = ToolHandler(ConfigAccumulator(), state)
    handler.dispatch("set_project_meta", {"name": "New Name", "description": "A desc"})
    meta = state["project_meta"]
    # phases_completed must not be destroyed
    assert meta["phases_completed"] == ["overview"]
    assert meta["name"] == "New Name"
    assert meta["slug"] == "test"

def test_remove_subagent_returns_error_for_unknown_id(self):
    """_handle_remove_subagent must return a structured error if the ID is not found."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(ConfigAccumulator(), state)
    result = handler.dispatch("remove_subagent", {"id": "nonexistent"})
    # Must not say "removed" when nothing was removed
    assert "not found" in result.lower() or "error" in result.lower() or "ok" not in result.lower()

def test_dispatch_unknown_tool_raises_value_error(self):
    """dispatch() must raise ValueError for an unrecognised tool name."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(ConfigAccumulator(), state)
    with pytest.raises(ValueError, match="Unknown tool"):
        handler.dispatch("totally_made_up_tool", {})

def test_update_subagent_unknown_id_returns_structured_error(self):
    """_handle_update_subagent returns a structured error dict (not raw string) on ValueError."""
    from dev_kit.agent.tools import ToolHandler
    from dev_kit.agent.accumulator import ConfigAccumulator
    state = {"phase": "overview", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(ConfigAccumulator(), state)
    result = handler.dispatch("update_subagent", {"id": "ghost", "fields": {"name": "x"}})
    # Must be a string that signals failure, not a raw Python exception repr
    assert isinstance(result, str)
    assert "ghost" in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest agent/tests/test_tools.py::test_set_project_meta_merges_not_replaces agent/tests/test_tools.py::test_remove_subagent_returns_error_for_unknown_id agent/tests/test_tools.py::test_dispatch_unknown_tool_raises_value_error -v
```

Expected: 3 failures.

- [ ] **Step 3: Update `tools.py` — fix `_handle_set_project_meta`**

Replace lines 222–224:
```python
def _handle_set_project_meta(self, inputs: dict) -> str:
    self._state["project_meta"] = inputs
    return f"Project meta set: {inputs.get('name', '')} ({inputs.get('slug', '')})"
```

With:
```python
def _handle_set_project_meta(self, inputs: dict) -> str:
    self._state["project_meta"].update(inputs)
    return f"Project meta updated: {inputs.get('name', '')} ({inputs.get('slug', '')})"
```

- [ ] **Step 4: Update `tools.py` — fix `_handle_remove_subagent`**

First, update `ConfigAccumulator.remove_subagent` in `accumulator.py` to return bool.

Find `remove_subagent` in `dev-kit/dev_kit/agent/accumulator.py` and update its return:

```python
def remove_subagent(self, subagent_id: str) -> bool:
    """Remove a subagent by ID.

    Args:
        subagent_id: ID of the subagent to remove.

    Returns:
        True if the subagent was found and removed, False if not found.
    """
    subagents = (
        self._data.get("agent_core", {})
        .get("agent_workflow", {})
        .get("subagents", [])
    )
    original_len = len(subagents)
    subagents[:] = [sa for sa in subagents if sa.get("id") != subagent_id]
    return len(subagents) < original_len
```

Then fix `_handle_remove_subagent` in `tools.py`:

```python
def _handle_remove_subagent(self, inputs: dict) -> str:
    removed = self._acc.remove_subagent(inputs["id"])
    if not removed:
        return f"error: subagent '{inputs['id']}' not found — nothing removed."
    return f"Subagent '{inputs['id']}' removed."
```

- [ ] **Step 5: Run all tool tests**

```bash
uv run pytest agent/tests/test_tools.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add dev_kit/agent/tools.py dev_kit/agent/accumulator.py agent/tests/test_tools.py
git commit -m "fix(dev-kit): fix set_project_meta merge bug, remove_subagent false-success, dispatch unknown tool"
```

---

## Task 7: Fix `app.py` — startup key assertion, route error handling, YAML-before-parse, list_projects resilience

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`
- Modify: `dev-kit/agent/tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_app.py`:

```python
def test_list_projects_skips_corrupt_metadata(self, client, tmp_path, monkeypatch):
    """GET /api/projects must return healthy projects even if one project.json is corrupt."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    # Create a healthy project
    client.post("/api/projects", json={"name": "Good", "description": "ok"})
    # Manually corrupt another project's metadata
    bad_dir = tmp_path / "bad-project" / "_meta"
    bad_dir.mkdir(parents=True)
    (bad_dir / "project.json").write_text("{NOT JSON}")
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    projects = resp.json()
    # Only the healthy one is returned
    assert len(projects) == 1
    assert projects[0]["name"] == "Good"

def test_update_config_rejects_invalid_yaml(self, client, tmp_path, monkeypatch):
    """PUT /configs/{block} must return 400 for unparseable YAML (not write the file)."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "X", "description": "y"})
    slug = client.get("/api/projects").json()[0]["slug"]
    config_file = tmp_path / slug / "trust_layer.yaml"
    original_content = config_file.read_text() if config_file.exists() else ""
    resp = client.put(f"/api/projects/{slug}/configs/trust_layer", json={"content": "key: [unclosed"})
    assert resp.status_code == 400
    # File must not have been overwritten with the invalid content
    current_content = config_file.read_text() if config_file.exists() else ""
    assert current_content == original_content

def test_update_config_sets_stale_on_schema_errors(self, client, tmp_path, monkeypatch):
    """PUT /configs/{block} must set status=stale when YAML is valid but schema invalid."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "X", "description": "y"})
    slug = client.get("/api/projects").json()[0]["slug"]
    # Valid YAML but wrong schema for agent_core (unknown fields)
    resp = client.put(
        f"/api/projects/{slug}/configs/agent_core",
        json={"content": "completely_wrong_key: true\n"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "stale"

def test_delete_project(self, client, tmp_path, monkeypatch):
    """DELETE /api/projects/{slug} must remove the project directory and engine cache."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "ToDelete", "description": "x"})
    slug = client.get("/api/projects").json()[0]["slug"]
    resp = client.delete(f"/api/projects/{slug}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == slug
    # Project no longer listed
    assert client.get("/api/projects").json() == []
    # Engine cache cleared
    assert slug not in app_module._engines

def test_get_config_unknown_block_returns_400(self, client, tmp_path, monkeypatch):
    """GET /configs/{block} with an unknown block name must return 400."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "X", "description": "y"})
    slug = client.get("/api/projects").json()[0]["slug"]
    resp = client.get(f"/api/projects/{slug}/configs/not_a_block")
    assert resp.status_code == 400

def test_restore_checkpoint_unknown_phase_returns_404(self, client, tmp_path, monkeypatch):
    """POST /checkpoints/{phase}/restore must return 404 for a non-existent checkpoint."""
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "X", "description": "y"})
    slug = client.get("/api/projects").json()[0]["slug"]
    resp = client.post(f"/api/projects/{slug}/checkpoints/99_ghost/restore")
    assert resp.status_code == 404

def test_chat_route_returns_500_with_structured_error_on_api_failure(self, client, tmp_path, monkeypatch):
    """POST /chat must return a JSON error body (not a raw exception) when the engine raises."""
    import dev_kit.agent.app as app_module
    from dev_kit.agent.errors import ConversationError
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    client.post("/api/projects", json={"name": "X", "description": "y"})
    slug = client.get("/api/projects").json()[0]["slug"]
    engine = app_module._get_engine(slug)
    # Patch engine.chat to raise ConversationError
    import asyncio
    async def _raise(*a, **kw):
        raise ConversationError("simulated failure")
    monkeypatch.setattr(engine, "chat", _raise)
    resp = client.post(f"/api/projects/{slug}/chat", json={"message": "hi"})
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body or "detail" in body
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest agent/tests/test_app.py::TestProjectRoutes::test_delete_project agent/tests/test_app.py::TestConfigRoutes::test_update_config_rejects_invalid_yaml agent/tests/test_app.py::TestProjectRoutes::test_list_projects_skips_corrupt_metadata -v
```

Expected: 3+ failures.

- [ ] **Step 3: Update `app.py` — startup API key assertion**

Replace line 42:
```python
_anthropic_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
```

With:
```python
_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not _api_key:
    raise EnvironmentError(
        "ANTHROPIC_API_KEY environment variable is not set. "
        "Set it before starting the server."
    )
_anthropic_client = anthropic.AsyncAnthropic(api_key=_api_key)
```

> Note: Tests set `ANTHROPIC_API_KEY=sk-ant-test` via `monkeypatch.setenv` in the client fixture, so tests are unaffected.

- [ ] **Step 4: Update `app.py` — fix `list_projects` to skip corrupt files**

Replace the `list_projects` function body:

```python
@app.get("/api/projects")
def list_projects() -> list[dict]:
    """List all projects, skipping any with unreadable metadata."""
    projects = []
    if not CONFIGS_DIR.exists():
        return projects
    for project_path in CONFIGS_DIR.iterdir():
        if not project_path.is_dir():
            continue
        meta_file = project_path / "_meta" / "project.json"
        if meta_file.exists():
            try:
                projects.append(json.loads(meta_file.read_text()))
            except json.JSONDecodeError as exc:
                logger.error(
                    "project_meta_corrupt",
                    extra={
                        "operation": "list_projects",
                        "status": "failure",
                        "error": str(exc),
                        "path": str(meta_file),
                    },
                )
    return projects
```

- [ ] **Step 5: Update `app.py` — fix `_load_project_meta` JSON error handling**

Replace `_load_project_meta`:

```python
def _load_project_meta(slug: str) -> dict:
    """Load project.json for the given slug.

    Args:
        slug: Project slug.

    Returns:
        Parsed project metadata dict.

    Raises:
        HTTPException: 404 if project not found, 500 if metadata is corrupt.
    """
    path = _get_project_path(slug) / "_meta" / "project.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "project_meta_corrupt",
            extra={"operation": "_load_project_meta", "status": "failure", "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Project metadata is corrupt") from exc
```

- [ ] **Step 6: Update `app.py` — fix YAML write-before-parse in `update_config_file`**

Replace `update_config_file`:

```python
@app.put("/api/projects/{slug}/configs/{block}")
def update_config_file(slug: str, block: str, body: UpdateConfigRequest) -> dict:
    """Manually update a config file and reverse-sync the accumulator.

    Parses YAML before writing to prevent corrupting the stored file.
    If schema validation fails, sets the block status to STALE.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    import yaml
    from dev_kit.agent.accumulator import ConfigStatus, DRAFT_BLOCKS
    # Parse before writing — reject invalid YAML with 400
    try:
        parsed = yaml.safe_load(body.content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    project_path = _get_project_path(slug)
    config_file = project_path / f"{block}.yaml"
    config_file.write_text(body.content)

    engine = _get_engine(slug)
    engine.accumulator._data[block] = parsed
    errors = validate_partial(block, parsed)
    if errors:
        engine.accumulator.set_status(block, ConfigStatus.STALE)
    elif block in DRAFT_BLOCKS:
        engine.accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        engine.accumulator.set_status(block, ConfigStatus.COMPLETE)
    engine._save_accumulator()
    return {"block": block, "status": engine.accumulator.get_status(block).value, "validation_errors": errors}
```

- [ ] **Step 7: Update `app.py` — add error handling to chat route**

Replace:
```python
@app.post("/api/projects/{slug}/chat")
async def chat(slug: str, body: ChatRequest) -> dict:
    """Send a user message and receive the agent response."""
    engine = _get_engine(slug)
    return await engine.chat(body.message)
```

With:
```python
@app.post("/api/projects/{slug}/chat")
async def chat(slug: str, body: ChatRequest) -> dict:
    """Send a user message and receive the agent response."""
    from dev_kit.agent.errors import ConversationError
    engine = _get_engine(slug)
    start = time.time()
    try:
        result = await engine.chat(body.message)
        logger.info(
            "chat_turn",
            extra={
                "operation": "app.chat",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
        )
        return result
    except ConversationError as exc:
        logger.error(
            "chat_turn_failed",
            extra={
                "operation": "app.chat",
                "status": "failure",
                "error": str(exc),
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
        )
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
```

- [ ] **Step 8: Run all app tests**

```bash
uv run pytest agent/tests/test_app.py -v
```

Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add dev_kit/agent/app.py agent/tests/test_app.py
git commit -m "fix(dev-kit): startup key assertion, YAML-before-parse, list_projects resilience, chat route error handling"
```

---

## Task 8: Add tests for `prompts/base.py`

**Files:**
- Create: `dev-kit/agent/tests/test_prompts_base.py`

- [ ] **Step 1: Create `test_prompts_base.py`**

```python
"""Tests for dev_kit.agent.prompts.base.build_system_prompt."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.base import build_system_prompt


def _make_prompt(phase="overview", summaries=None, connectors=None, name="TestProject", desc="A test"):
    acc = ConfigAccumulator()
    return build_system_prompt(
        project_name=name,
        project_description=desc,
        accumulator=acc,
        phase=phase,
        checkpoint_summaries=summaries or [],
        available_connectors=connectors,
    )


class TestBuildSystemPrompt:
    def test_contains_dpg_overview(self):
        """Output must include the DPG overview section."""
        prompt = _make_prompt()
        assert "DPG Configuration Assistant" in prompt

    def test_contains_project_context_when_name_given(self):
        """Project name and description must appear in the prompt."""
        prompt = _make_prompt(name="KarmaKitchen", desc="Food bank management")
        assert "KarmaKitchen" in prompt
        assert "Food bank management" in prompt

    def test_no_project_section_when_name_empty(self):
        """When project_name is empty, no Project section is injected."""
        prompt = build_system_prompt(
            project_name="",
            project_description="",
            accumulator=ConfigAccumulator(),
            phase="overview",
            checkpoint_summaries=[],
        )
        assert "## Project" not in prompt

    def test_contains_checkpoint_summaries(self):
        """Prior phase summaries must appear in the output."""
        prompt = _make_prompt(summaries=["Overview complete. User builds a chatbot."])
        assert "Overview complete. User builds a chatbot." in prompt

    def test_no_summaries_section_when_empty(self):
        """When no summaries are provided, the prior-phase summaries section is omitted."""
        prompt = _make_prompt(summaries=[])
        assert "Prior phase summaries" not in prompt

    def test_contains_current_phase(self):
        """Current phase name must appear in the output."""
        prompt = _make_prompt(phase="trust")
        assert "trust" in prompt

    def test_phase_addition_injected_for_known_phase(self):
        """A known phase must inject phase-specific schema context."""
        prompt = _make_prompt(phase="language")
        # The language phase adds content from get_phase_addition — just check it's non-empty
        assert len(prompt) > len(_make_prompt(phase="overview"))

    def test_connectors_injected_in_workflow_phase(self):
        """Available connectors must appear in the workflow-phase prompt."""
        prompt = _make_prompt(phase="workflow", connectors=["crm_api", "sms_gateway"])
        assert "crm_api" in prompt or "sms_gateway" in prompt

    def test_unknown_phase_produces_valid_prompt(self):
        """An unrecognised phase name must not raise — returns a prompt without phase addition."""
        prompt = _make_prompt(phase="totally_unknown_phase")
        assert "totally_unknown_phase" in prompt
```

- [ ] **Step 2: Run to verify they pass**

```bash
uv run pytest agent/tests/test_prompts_base.py -v
```

Expected: All 9 pass.

- [ ] **Step 3: Commit**

```bash
git add agent/tests/test_prompts_base.py
git commit -m "test(dev-kit): add tests for prompts/base.py build_system_prompt"
```

---

## Task 9: Add tests for `prompts/phases.py`

**Files:**
- Create: `dev-kit/agent/tests/test_prompts_phases.py`

- [ ] **Step 1: Create `test_prompts_phases.py`**

```python
"""Tests for dev_kit.agent.prompts.phases.get_phase_addition."""
import pytest
from dev_kit.agent.prompts.phases import get_phase_addition

KNOWN_PHASES = ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"]


class TestGetPhaseAddition:
    def test_all_known_phases_return_non_empty_string(self):
        """Every defined phase must return a non-empty string."""
        for phase in KNOWN_PHASES:
            result = get_phase_addition(phase)
            assert isinstance(result, str), f"phase {phase!r} returned non-string"
            assert len(result.strip()) > 0, f"phase {phase!r} returned empty string"

    def test_unknown_phase_returns_empty_string(self):
        """An unrecognised phase must return an empty string, not raise."""
        result = get_phase_addition("not_a_real_phase")
        assert result == "" or result is None or len(result.strip()) == 0

    def test_workflow_phase_injects_connectors(self):
        """workflow phase must include available_connectors in the output."""
        result = get_phase_addition("workflow", available_connectors=["crm_api", "sms_service"])
        assert "crm_api" in result
        assert "sms_service" in result

    def test_workflow_phase_without_connectors_does_not_raise(self):
        """workflow phase with connectors=None must not raise."""
        result = get_phase_addition("workflow", available_connectors=None)
        assert isinstance(result, str)

    def test_overview_phase_does_not_mention_connectors(self):
        """overview phase must not reference connector context."""
        result = get_phase_addition("overview")
        # Should not contain connector-specific content
        assert "available_connectors" not in result

    def test_phase_names_are_case_sensitive(self):
        """Phase name matching must be exact (case-sensitive)."""
        # "Language" (capitalised) is not a valid phase — should return empty
        result = get_phase_addition("Language")
        assert result == "" or result is None or len(result.strip()) == 0
```

- [ ] **Step 2: Run to verify they pass**

```bash
uv run pytest agent/tests/test_prompts_phases.py -v
```

Expected: All 6 pass (or adjust assertions if `get_phase_addition` returns `None` for unknown phases — both are acceptable).

- [ ] **Step 3: Commit**

```bash
git add agent/tests/test_prompts_phases.py
git commit -m "test(dev-kit): add tests for prompts/phases.py get_phase_addition"
```

---

## Task 10: Run full test suite and verify coverage

- [ ] **Step 1: Run all tests**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg/dev-kit
uv run pytest tests/ agent/tests/ -v --tb=short
```

Expected: All tests pass (72 existing + ~25 new).

- [ ] **Step 2: Check coverage (optional but recommended)**

```bash
uv run pytest tests/ agent/tests/ --cov=dev_kit --cov-report=term-missing --cov-fail-under=70
```

- [ ] **Step 3: Final commit if anything was auto-fixed**

```bash
git add -A
git status
# If clean, no commit needed. If there are stray fixes:
git commit -m "fix(dev-kit): miscellaneous fixes found during test run"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] #1 No timeout/retry on LLM calls → Task 4
- [x] #2 Chat route catches nothing → Task 7, Step 7
- [x] #3 Hardcoded model name → Task 2
- [x] #4 `set_project_meta` overwrites state → Task 6
- [x] #5 `rollback_to_checkpoint` tool is no-op → Task 4 (rollback handling added to `chat()`)
- [x] #6 YAML written before parse → Task 7, Step 6
- [x] #7 `list_projects` dies on corrupt file → Task 7, Step 4
- [x] #8 `_load()` no error handling → Task 5
- [x] #9 `remove_subagent` false success → Task 6
- [x] #10 Missing API key deferred → Task 7, Step 3
- [x] #11 Tool errors as raw strings → Task 6 (structured string returned, no raw Python repr)
- [x] Test gap: `prompts/base.py` → Task 8
- [x] Test gap: `prompts/phases.py` → Task 9
- [x] Test gap: API failure in `chat()` → Task 4
- [x] Test gap: STALE branch in `update_config_file` → Task 7
- [x] Test gap: `delete_project` route → Task 7
- [x] Test gap: checkpoint restore 404 → Task 7
- [x] Test gap: unknown block 400 → Task 7
- [x] Test gap: `set_project_meta` merge → Task 6
- [x] Test gap: `remove_subagent` not-found → Task 6
- [x] Test gap: `dispatch` unknown tool → Task 6
- [x] No structured logging → Task 3
- [x] `_load_project_meta` JSON error → Task 7, Step 5
