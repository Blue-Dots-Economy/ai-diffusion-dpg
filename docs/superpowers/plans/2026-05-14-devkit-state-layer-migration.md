# Dev-Kit State Layer Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the deterministic-wizard migration by removing the old wizard's state model (`ConfigAccumulator`, `ConversationEngine`, `checkpoints.py`, `ConfigStatus` enum) and wiring every remaining caller to the new wizard's on-disk state primitives (`IntakeState`, accumulator dict, `field_status.json`, `current_phase.json`, new `history.jsonl`).

**Architecture:** The deterministic wizard already established `_meta/intake_state.json`, `_meta/field_status.json`, `_meta/current_phase.json`. This plan adds two more on-disk primitives — `_meta/accumulator.json` (pure block YAML state, no status/phase) and `_meta/history.jsonl` (append-only chat history) — and replaces every old-wizard call site with thin per-request loaders. The result: a single source of truth on disk for all wizard state, with no in-memory state cache.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI (existing dev-kit), React (existing frontend), uv for env management.

---

## Source documents — READ FIRST

- **Companion design:** [`docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md`](../specs/2026-05-13-devkit-deterministic-wizard-design.md)
- **Field rules catalogue:** [`docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md`](../specs/2026-05-13-devkit-field-rules-catalogue.md)
- **Prior plan (now complete):** [`2026-05-14-devkit-deterministic-wizard-implementation.md`](2026-05-14-devkit-deterministic-wizard-implementation.md)
- **Sync rule:** [`.claude/rules/runtime-devkit-sync.md`](../../../.claude/rules/runtime-devkit-sync.md)

## Locked design decisions (from the audit)

| ID | Decision |
|---|---|
| D1 | `_meta/accumulator.json` — pure YAML state per block (no phase, no status). New module `project_state.py`. |
| D2 | `_meta/history.jsonl` — append-only chat history. Replaces checkpoint-based history reconstruction. New module `history.py`. |
| D3 | Drop `ConfigStatus` enum (`PENDING/DRAFT/STALE/COMPLETE`). Derive block completion at read time from `field_status.json` as `"complete"` or `"incomplete"` strings. |
| D4 | **Drop checkpoints entirely.** Delete `checkpoints.py`, 3 endpoints, history-reconstruction logic, any React UI references. No replacement feature in this plan. |
| D5 | Drop `ConversationEngine`. Replace `_get_engine(slug)` with per-request loaders. `/api/projects/{slug}/chat` calls `phase_driver.run_turn(message, slug)` directly. |

## Out of scope

- Reintroducing an "undo to phase" feature (drop is permanent for now; can be a future intake-state-snapshot plan if requested).
- Pre-existing project-config validation (the 5 failing tests against legacy YAMLs — handled as Phase F decision: `xfail` with reason).
- The 4 legacy projects under `dev-kit/configs/` themselves — they stay (per user; they're test samples).

---

## Target file structure (after migration)

```
dev-kit/dev_kit/agent/
├── intake_state.py                # ✅ already exists
├── project_state.py               # NEW: load/save accumulator dict
├── history.py                     # NEW: append-only history.jsonl
├── block_status.py                # NEW: derive completion from field_status
├── field_status.py                # ✅ already exists
├── path_ops.py                    # ✅ already exists
├── field_rules/                   # ✅ already exists
├── phase_prompts/                 # ✅ already exists
├── phases_config.py               # ✅ already exists
├── phase_driver.py                # ✅ already exists (minor edit: history append)
├── router.py                      # ✅ already exists
├── skeleton.py                    # ✅ already exists
├── tools.py                       # ✅ already exists (minor edit: drop ConfigAccumulator imports)
├── renderer.py                    # MODIFIED: drop ConfigAccumulator dep; signature change
├── derived_fields.py              # ✅ already exists
├── conversation.py                # MODIFIED: drop ConversationEngine class; thin module
├── app.py                         # MODIFIED: ~30 endpoints migrated to per-request loaders
├── deployer/                      # ✅ existing (minor: switch to new state where touched)
└── accumulator.py                 # DELETED (whole file)
└── checkpoints.py                 # DELETED (whole file)
```

**Tests deleted/rewritten:** `test_accumulator_azure.py`, `test_accumulator_connector.py`, `test_renderer.py` (rewrite), `test_app_project_routes.py` (rewrite), `test_app_deploy_routes.py` (rewrite), `test_app_endpoints.py` (rewrite — drop checkpoint tests, migrate others), `test_existing_configs_validate.py` (`xfail`).

---

## Phase A — Build new state modules

### Task A.1: `project_state.py` — accumulator dict persistence

**Files:**
- Create: `dev-kit/dev_kit/agent/project_state.py`
- Create: `dev-kit/tests/agent/test_project_state.py`

The accumulator is a plain `dict[str, dict]` keyed by block name (one of the 7 runtime blocks). Each block's value is the domain YAML structure (nested dicts).

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_project_state.py`:

```python
"""Tests for project_state: load/save accumulator dict."""
from pathlib import Path

import pytest

from dev_kit.agent.project_state import (
    BLOCKS,
    empty_accumulator,
    load_accumulator,
    save_accumulator,
)


def test_blocks_constant_has_seven_entries():
    assert set(BLOCKS) == {
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }


def test_empty_accumulator_has_all_blocks():
    acc = empty_accumulator()
    assert set(acc.keys()) == set(BLOCKS)
    for block in BLOCKS:
        assert acc[block] == {}


def test_save_load_roundtrip(tmp_path: Path):
    acc = empty_accumulator()
    acc["agent_core"]["agent"] = {"primary_model": "claude-sonnet-4-5"}
    acc["trust_layer"]["trust"] = {"policy_pack": "kkb_advisory_jobs"}
    p = tmp_path / "accumulator.json"
    save_accumulator(p, acc)
    loaded = load_accumulator(p)
    assert loaded == acc


def test_load_missing_returns_empty(tmp_path: Path):
    """Missing file → fresh empty accumulator (not an error)."""
    acc = load_accumulator(tmp_path / "missing.json")
    assert acc == empty_accumulator()


def test_load_corrupt_json_raises_value_error(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("not valid json {{{")
    with pytest.raises(ValueError, match="Corrupt"):
        load_accumulator(p)


def test_load_unknown_block_raises_value_error(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text('{"unknown_block": {}}')
    with pytest.raises(ValueError, match="unknown block"):
        load_accumulator(p)
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_project_state.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `project_state.py`.**

`dev-kit/dev_kit/agent/project_state.py`:

```python
"""project_state — accumulator-dict persistence for the deterministic wizard.

The accumulator is a plain dict keyed by runtime block name; each value is the
domain-YAML structure for that block (nested dicts). Persisted to
`_meta/accumulator.json` under the project directory. Read by the renderer,
tool handlers, and read-only API endpoints.

Replaces the storage half of the old `ConfigAccumulator` class. The old
wizard's per-block status enum (PENDING/DRAFT/STALE/COMPLETE) is dropped —
block completion is now derived from `field_status.json` (see block_status.py).

Belongs to the dev-kit deterministic wizard. See:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BLOCKS: tuple[str, ...] = (
    "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
    "action_gateway", "reach_layer", "observability_layer",
)
_BLOCKS_SET = frozenset(BLOCKS)


def empty_accumulator() -> dict[str, dict]:
    """Return a fresh accumulator with one empty dict per block."""
    return {block: {} for block in BLOCKS}


def save_accumulator(path: Path, accumulator: dict[str, dict]) -> None:
    """Persist the accumulator dict to disk as JSON.

    Args:
        path: Target file path (typically `<slug>/_meta/accumulator.json`).
        accumulator: The accumulator dict — one entry per block.

    Raises:
        ValueError: If any top-level key isn't a known block name.
    """
    unknown = set(accumulator) - _BLOCKS_SET
    if unknown:
        raise ValueError(f"unknown blocks in accumulator: {sorted(unknown)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(accumulator, indent=2, ensure_ascii=False, sort_keys=True))


def load_accumulator(path: Path) -> dict[str, dict]:
    """Load the accumulator dict from disk.

    Args:
        path: Source file path.

    Returns:
        The deserialised accumulator dict, with empty entries for any missing
        blocks. If the file doesn't exist, returns a fresh empty accumulator.

    Raises:
        ValueError: If the file is corrupt JSON or contains unknown block names.
    """
    if not path.exists():
        return empty_accumulator()
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "accumulator load failed",
            extra={"operation": "load_accumulator", "status": "failure",
                   "error": str(exc), "path": str(path)},
        )
        raise ValueError(f"Corrupt JSON in accumulator file {path}: {exc}") from exc
    unknown = set(payload) - _BLOCKS_SET
    if unknown:
        raise ValueError(f"unknown blocks in accumulator file {path}: {sorted(unknown)}")
    result = empty_accumulator()
    result.update(payload)
    return result


__all__ = ["BLOCKS", "empty_accumulator", "save_accumulator", "load_accumulator"]
```

- [ ] **Step 4: Run tests.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_project_state.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/project_state.py dev-kit/tests/agent/test_project_state.py
git commit -m "feat(dev-kit): add project_state.py — accumulator dict persistence"
```

### Task A.2: `history.py` — append-only chat history

**Files:**
- Create: `dev-kit/dev_kit/agent/history.py`
- Create: `dev-kit/tests/agent/test_history.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_history.py`:

```python
"""Tests for history: append-only jsonl chat history."""
from pathlib import Path

from dev_kit.agent.history import (
    HistoryEntry,
    append_turn,
    load_history,
)


def test_history_entry_minimal():
    e = HistoryEntry(role="user", content="Hello", phase="tier", timestamp="2026-05-14T10:00:00Z")
    assert e.role == "user"
    assert e.content == "Hello"


def test_append_creates_jsonl(tmp_path: Path):
    project = tmp_path / "proj"
    append_turn(project, HistoryEntry(role="user", content="Hi", phase="tier",
                                       timestamp="2026-05-14T10:00:00Z"))
    p = project / "_meta" / "history.jsonl"
    assert p.exists()
    assert p.read_text().strip().count("\n") == 0  # one line


def test_multiple_appends(tmp_path: Path):
    project = tmp_path / "proj"
    append_turn(project, HistoryEntry(role="user", content="A", phase="tier",
                                       timestamp="2026-05-14T10:00:00Z"))
    append_turn(project, HistoryEntry(role="assistant", content="B", phase="tier",
                                       timestamp="2026-05-14T10:00:01Z"))
    history = load_history(project)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].content == "B"


def test_load_missing_returns_empty(tmp_path: Path):
    history = load_history(tmp_path / "no_project")
    assert history == []


def test_load_skips_blank_lines(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "_meta").mkdir(parents=True)
    (project / "_meta" / "history.jsonl").write_text(
        '{"role": "user", "content": "A", "phase": "tier", "timestamp": "t"}\n'
        '\n'  # blank
        '{"role": "assistant", "content": "B", "phase": "tier", "timestamp": "t2"}\n'
    )
    history = load_history(project)
    assert len(history) == 2
```

- [ ] **Step 2: Run to verify failure.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_history.py -v
```

- [ ] **Step 3: Implement.**

`dev-kit/dev_kit/agent/history.py`:

```python
"""history — append-only jsonl chat history for the deterministic wizard.

Each turn (user + assistant) appends one HistoryEntry per role. Persisted to
`<project>/_meta/history.jsonl`. Replaces the old wizard's checkpoint-based
history reconstruction.

Belongs to the dev-kit deterministic wizard.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryEntry:
    """One chat turn entry."""

    role: str           # "user" | "assistant"
    content: str
    phase: str          # wizard phase at the time this turn happened
    timestamp: str      # UTC ISO-8601


def _history_path(project_path: Path) -> Path:
    return project_path / "_meta" / "history.jsonl"


def append_turn(project_path: Path, entry: HistoryEntry) -> None:
    """Append a single history entry to the project's history.jsonl.

    Creates the `_meta/` directory if needed.

    Args:
        project_path: Project directory (e.g., `dev-kit/configs/<slug>/`).
        entry: The HistoryEntry to write.
    """
    p = _history_path(project_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(entry), ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_history(project_path: Path) -> list[HistoryEntry]:
    """Load all history entries for a project.

    Args:
        project_path: Project directory.

    Returns:
        Ordered list of HistoryEntry. Empty list if no history file.
    """
    p = _history_path(project_path)
    if not p.exists():
        return []
    out: list[HistoryEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            out.append(HistoryEntry(**payload))
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "history line parse failure — skipping",
                extra={"operation": "load_history", "status": "skipped",
                       "error": str(exc), "line_preview": line[:80]},
            )
    return out


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


__all__ = ["HistoryEntry", "append_turn", "load_history", "utc_now_iso"]
```

- [ ] **Step 4: Run tests.**

- [ ] **Step 5: Commit.**

```bash
git commit -am "feat(dev-kit): add history.py — append-only chat history (jsonl)"
```

### Task A.3: `block_status.py` — derived per-block completion

**Files:**
- Create: `dev-kit/dev_kit/agent/block_status.py`
- Create: `dev-kit/tests/agent/test_block_status.py`

- [ ] **Step 1: Write test.**

`dev-kit/tests/agent/test_block_status.py`:

```python
"""Tests for block_status: derive 'complete' | 'incomplete' from field_status."""
from dev_kit.agent.block_status import block_completion_status, all_block_statuses


def test_no_fields_returns_incomplete():
    assert block_completion_status("agent_core", {}) == "incomplete"


def test_all_answered_returns_complete():
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.fallback_model": "answered",
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_any_pending_returns_incomplete():
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.fallback_model": "pending",
    }
    assert block_completion_status("agent_core", fs) == "incomplete"


def test_needs_re_asking_is_incomplete():
    fs = {"agent_core.agent.primary_model": "needs_re_asking"}
    assert block_completion_status("agent_core", fs) == "incomplete"


def test_not_applicable_counts_as_complete_for_that_field():
    """A 'not_applicable' field doesn't block completion."""
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.consent_prompt": "not_applicable",
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_only_fields_for_named_block_counted():
    """Fields from other blocks don't affect this block's status."""
    fs = {
        "agent_core.agent.primary_model": "answered",
        "trust_layer.trust.policy_pack": "pending",  # other block
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_all_block_statuses_returns_one_per_block():
    fs = {"agent_core.agent.primary_model": "answered"}
    statuses = all_block_statuses(fs)
    assert set(statuses.keys()) == {
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }
    assert statuses["agent_core"] == "complete"
    assert statuses["trust_layer"] == "incomplete"  # no fields → incomplete
```

- [ ] **Step 2: Run test (expect failure).**

- [ ] **Step 3: Implement.**

`dev-kit/dev_kit/agent/block_status.py`:

```python
"""block_status — derive per-block completion from field_status.

Replaces the old wizard's `ConfigStatus` enum (PENDING / DRAFT / STALE /
COMPLETE) with on-demand derivation from `field_status.json` values
(pending / answered / needs_re_asking / not_applicable).

A block is "complete" when every tracked field for that block has status
"answered" or "not_applicable". Otherwise "incomplete".
"""
from __future__ import annotations

from typing import Literal

from dev_kit.agent.project_state import BLOCKS

BlockStatus = Literal["complete", "incomplete"]
_COMPLETE_FIELD_STATUSES = {"answered", "not_applicable"}


def block_completion_status(block: str, field_status: dict[str, str]) -> BlockStatus:
    """Return 'complete' iff every field of `block` in field_status is answered/not_applicable.

    Args:
        block: Block name (one of `project_state.BLOCKS`).
        field_status: The full field_status dict (paths are `<block>.<rest>`).

    Returns:
        "complete" or "incomplete". A block with no fields tracked is
        "incomplete" (nothing has been answered yet).
    """
    prefix = f"{block}."
    block_field_statuses = [s for path, s in field_status.items() if path.startswith(prefix)]
    if not block_field_statuses:
        return "incomplete"
    if all(s in _COMPLETE_FIELD_STATUSES for s in block_field_statuses):
        return "complete"
    return "incomplete"


def all_block_statuses(field_status: dict[str, str]) -> dict[str, BlockStatus]:
    """Return {block_name: status} for every block."""
    return {block: block_completion_status(block, field_status) for block in BLOCKS}


__all__ = ["BlockStatus", "block_completion_status", "all_block_statuses"]
```

- [ ] **Step 4: Run tests.**

- [ ] **Step 5: Commit.**

```bash
git commit -am "feat(dev-kit): add block_status.py — derive completion from field_status"
```

---

## Phase B — Refactor renderer to drop ConfigAccumulator

### Task B.1: Refactor `renderer.py`

**Files:**
- Modify: `dev-kit/dev_kit/agent/renderer.py`
- Modify: `dev-kit/tests/agent/test_renderer_runtime_validate.py` (existing)
- Replace: `dev-kit/tests/test_renderer.py` (legacy)

The current `render_all(project_path, accumulator: ConfigAccumulator) -> dict[str, ConfigStatus]` returns ConfigStatus values. Replace with:

```python
def render_all(
    project_path: Path,
    accumulator: dict[str, dict],
    intake_state: IntakeState,
    *,
    deploy_settings: dict | None = None,
) -> dict[str, str]:
    """Render every block's domain YAML and return {block: 'complete'|'failed'} per outcome."""
```

The return shape:
- `"complete"` — YAML written successfully (passed dry-run if available).
- `"failed"` — runtime dry-run rejected (raises `RuntimeValidationError` upstream).
- `"draft"` — not used anymore. Block status is now per-field.

- [ ] **Step 1: Read current `render_all` to understand current behaviour.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && sed -n '243,345p' dev-kit/dev_kit/agent/renderer.py
```

- [ ] **Step 2: Write the new tests in a new file** `dev-kit/tests/agent/test_renderer_render_all.py`:

```python
"""Tests for the new render_all signature (no ConfigAccumulator dependency)."""
from pathlib import Path

import pytest

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.project_state import empty_accumulator
from dev_kit.agent.renderer import render_all


def _intake(**overrides) -> IntakeState:
    defaults = dict(
        has_kb=False, has_external_tools=False, is_multi_turn=False,
        needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english",
        supported_languages=["english"],
        domain_description="Test", project_name="testproj",
    )
    defaults.update(overrides)
    return IntakeState(**defaults)


def test_render_all_writes_yaml_per_block(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    acc = empty_accumulator()
    acc["agent_core"] = {"agent": {"primary_model": "claude-sonnet-4-5"}}
    statuses = render_all(project, acc, _intake())
    # Expect a YAML file per block (block YAMLs written under project dir)
    yaml_files = list(project.glob("*.yaml"))
    assert len(yaml_files) >= 1
    assert all(v in ("complete", "failed") for v in statuses.values())


# (Additional tests as the implementer migrates the existing logic)
```

- [ ] **Step 3: Update the implementation.**

The existing `render_all` does these steps inside (lines 243-345):
1. `_prepare_block_data` — reads accumulator + applies derived fields + applies deploy overlay
2. Validates against mirror schemas
3. Calls `runtime_validate` (dry-run, if available)
4. Writes YAML to disk
5. Returns status per block

For the new signature, replace ConfigAccumulator parameter with `accumulator: dict[str, dict]` and `intake_state: IntakeState`. Remove the `ConfigStatus.PENDING/STALE/DRAFT/COMPLETE` returns; return `"complete" | "failed"` strings.

`_prepare_block_data` must also be updated — its first parameter currently is `accumulator: ConfigAccumulator`. Change to `accumulator: dict[str, dict]` and read directly: `block_data = accumulator.get(block, {})`.

Remove the `from dev_kit.agent.accumulator import BLOCKS, DRAFT_BLOCKS, ConfigAccumulator, ConfigStatus` import. Use `from dev_kit.agent.project_state import BLOCKS`. The `DRAFT_BLOCKS` constant should be inlined into renderer.py (it's small).

- [ ] **Step 4: Run all renderer tests.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_renderer*.py tests/test_renderer.py -v
```

(test_renderer.py is legacy — it'll fail. Don't fix it yet; Phase F handles it.)

- [ ] **Step 5: Commit.**

```bash
git commit -am "refactor(dev-kit): render_all takes dict + IntakeState; drops ConfigAccumulator dependency"
```

---

## Phase C — Migrate app.py endpoints

The audit found ~30 app.py endpoints. They fall into 4 groups (by what they touch):

| Group | Endpoints | New state needed |
|---|---|---|
| **C1 — Project lifecycle** | `POST /api/projects`, `GET /api/projects`, `GET /api/projects/{slug}`, `DELETE /api/projects/{slug}` | intake_state + history |
| **C2 — Chat + history** | `POST /api/projects/{slug}/chat`, `GET /api/projects/{slug}/history` | new history.py; phase_driver.run_turn |
| **C3 — Config read/write** | `GET /api/projects/{slug}/configs[/...]`, `PUT /api/projects/{slug}/configs/{block}`, `POST /api/projects/{slug}/configs/reload`, `POST /api/projects/{slug}/configs/validate`, `GET /api/projects/{slug}/configs/export` | accumulator dict + block_status |
| **C4 — Deploy + ingest** | All `/deploy/...` and `/ingest/...` endpoints | already wired via IntakeState (commit 8fe2b8b); keep as-is |
| **C5 — Delete checkpoints** | `GET /api/projects/{slug}/checkpoints`, `POST /api/projects/{slug}/checkpoints/{phase}/restore`, `GET /api/projects/{slug}/checkpoints/{phase}/preview` | Delete all 3 endpoints (D4 decision) |

### Task C.1: Migrate project lifecycle endpoints

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py` (endpoints around lines 483, 560, 588, 618)

- [ ] **Step 1: Identify the 4 endpoints. Read each one to understand what it returns.**

```bash
sed -n '483,640p' dev-kit/dev_kit/agent/app.py
```

- [ ] **Step 2: Each endpoint currently does:**
   - `engine = _get_engine(slug)`
   - reads `engine.accumulator.X` for various X
   - returns JSON to React UI

**Replace with per-request loaders:**
```python
from dev_kit.agent.intake_state import load_intake_state
from dev_kit.agent.project_state import load_accumulator
from dev_kit.agent.field_status import load_field_status
from dev_kit.agent.block_status import all_block_statuses

intake_state = load_intake_state(PROJECTS_DIR / slug / "_meta" / "intake_state.json")
accumulator = load_accumulator(PROJECTS_DIR / slug / "_meta" / "accumulator.json")
field_status = load_field_status(PROJECTS_DIR / slug / "_meta" / "field_status.json")
block_statuses = all_block_statuses(field_status)
```

The response shape for `GET /api/projects/{slug}` likely includes per-block status. Replace `ConfigStatus.PENDING.value` etc. with `block_statuses[block]` → `"complete"` or `"incomplete"`.

- [ ] **Step 3: Test each endpoint by hand against a project that has intake_state.json + field_status.json.**

(The React UI is on the next phase; we just need the API to return correct shapes.)

- [ ] **Step 4: Commit.**

```bash
git commit -am "refactor(dev-kit): migrate project lifecycle endpoints to per-request state loaders"
```

### Task C.2: Migrate chat + history endpoints

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py` (lines 638, 683)

- [ ] **Step 1: Replace `POST /api/projects/{slug}/chat`** to call `phase_driver.run_turn(message, slug)` directly. Drop the engine wrapper.

- [ ] **Step 2: Replace `GET /api/projects/{slug}/history`** to call `history.load_history(project_path)`. Drop the checkpoint-reconstruction call.

- [ ] **Step 3: Wire history append into `phase_driver.run_turn`.** When the turn completes, append a `HistoryEntry` for both the user message and the assistant response.

Edit `phase_driver.py` to do:
```python
from dev_kit.agent.history import HistoryEntry, append_turn, utc_now_iso

# ... inside run_turn, just before returning:
append_turn(project_path, HistoryEntry(role="user", content=user_message,
                                         phase=current_phase, timestamp=utc_now_iso()))
append_turn(project_path, HistoryEntry(role="assistant", content=response,
                                         phase=next_phase, timestamp=utc_now_iso()))
```

- [ ] **Step 4: Commit.**

```bash
git commit -am "refactor(dev-kit): /chat + /history use phase_driver + history.jsonl directly"
```

### Task C.3: Migrate config read/write endpoints

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py` (lines 787, 804, 840, 852, 885, 903)

These 6 endpoints currently read/write the accumulator state. Replace ConfigAccumulator with the accumulator dict + path_ops:

- [ ] **Step 1:** `GET /api/projects/{slug}/configs/{block}` returns block YAML — `accumulator[block]` directly.
- [ ] **Step 2:** `PUT /api/projects/{slug}/configs/{block}` writes block YAML — `accumulator[block] = parsed; save_accumulator(...)`.
- [ ] **Step 3:** `POST /api/projects/{slug}/configs/reload` re-reads block YAMLs from disk into the accumulator.
- [ ] **Step 4:** Validation endpoints — use existing `validate_partial` against the dev-kit mirror schemas.
- [ ] **Step 5:** Export endpoint — serialise the accumulator dict as YAML zip / tar.

- [ ] **Step 6: Commit.**

```bash
git commit -am "refactor(dev-kit): config read/write endpoints use project_state loaders"
```

### Task C.4: Delete checkpoint endpoints

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py` (delete lines 700-786)
- Modify: `dev-kit/dev_kit/agent/app.py` import block (remove `from dev_kit.agent.checkpoints import ...`)

- [ ] **Step 1: Delete 3 endpoints.**
- [ ] **Step 2: Remove the import.**
- [ ] **Step 3: Verify nothing else imports `checkpoints.*`** — there will be `conversation.py:114` (`_load_history_from_checkpoints`) — that gets removed in Phase D.
- [ ] **Step 4: Commit.**

```bash
git commit -am "chore(dev-kit): drop 3 checkpoint endpoints — feature deprecated"
```

---

## Phase D — Delete dead code

### Task D.1: Delete `checkpoints.py`

- [ ] **Step 1: Verify nothing imports it.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && grep -rn 'from dev_kit.agent.checkpoints\|import checkpoints' dev-kit/dev_kit/ 2>&1 | grep -v __pycache__
```

Expected: empty after Phase C.4.

- [ ] **Step 2: Delete the file + its tests.**

```bash
git rm dev-kit/dev_kit/agent/checkpoints.py
# Find and delete or rewrite checkpoint tests
git rm dev-kit/tests/test_app_endpoints.py  # contains checkpoint tests — re-add a new file in Phase F
```

- [ ] **Step 3: Commit.**

```bash
git commit -m "chore(dev-kit): delete checkpoints.py — replaced by history.jsonl"
```

### Task D.2: Drop `ConversationEngine` from `conversation.py`

**Files:**
- Modify (replace): `dev-kit/dev_kit/agent/conversation.py`

The current ConversationEngine class wraps ConfigAccumulator + history + checkpoint loading. Replace with a thin module — just keep public functions `chat_turn(slug, message) -> LLMResponse` and `get_history(slug) -> list[HistoryEntry]` that delegate to `phase_driver.run_turn` and `history.load_history` respectively.

- [ ] **Step 1: Read current file.**

```bash
cat dev-kit/dev_kit/agent/conversation.py
```

- [ ] **Step 2: Rewrite as a thin module.** Roughly:

```python
"""conversation — public chat/history surface for the deterministic wizard.

Thin wrapper over phase_driver + history modules. Stateless; loads state
from disk on every call.
"""
from __future__ import annotations

from pathlib import Path

from dev_kit.agent import phase_driver
from dev_kit.agent.history import HistoryEntry, load_history


def chat_turn(project_root: Path, slug: str, user_message: str) -> phase_driver.LLMResponse:
    """Run one chat turn via the deterministic phase_driver."""
    return phase_driver.run_turn(user_message, str(project_root / slug))


def get_history(project_root: Path, slug: str) -> list[HistoryEntry]:
    """Return the full chat history for a project."""
    return load_history(project_root / slug)


__all__ = ["chat_turn", "get_history"]
```

- [ ] **Step 3: Update callers in `app.py`** — `_get_engine(slug)` is gone; chat endpoint uses `conversation.chat_turn(...)`.

- [ ] **Step 4: Commit.**

```bash
git commit -am "refactor(dev-kit): drop ConversationEngine class — conversation.py is now a thin wrapper"
```

### Task D.3: Delete `accumulator.py`

**Files:**
- Delete: `dev-kit/dev_kit/agent/accumulator.py`
- Modify: `dev-kit/dev_kit/agent/tools.py` (replace `from dev_kit.agent.accumulator import BLOCKS, PHASES, ConfigAccumulator, ConfigStatus`)
- Modify: `dev-kit/dev_kit/agent/renderer.py` (remove any lingering imports)
- Modify: `dev-kit/dev_kit/schemas/cross_block_validation.py:24` (mirror PHASES if needed)

- [ ] **Step 1: Replace imports in `tools.py`:**

```python
from dev_kit.agent.project_state import BLOCKS
# Drop PHASES import — the old phase ordering list isn't used in the new wizard
# (phases_config.PHASES has the new ordering). Verify no code in tools.py uses the
# old PHASES variable.
# Drop ConfigStatus import — block_status.block_completion_status returns strings.
# Drop ConfigAccumulator import.
```

- [ ] **Step 2: Update `cross_block_validation.py`** — if it referenced the old PHASES order, mirror it from `phases_config.PHASES` keys list.

- [ ] **Step 3: Verify with grep:**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && grep -rn 'from dev_kit.agent.accumulator\|import accumulator\b' dev-kit/dev_kit/ 2>&1 | grep -v __pycache__
```

Expected: empty.

- [ ] **Step 4: Delete the file.**

```bash
git rm dev-kit/dev_kit/agent/accumulator.py
git rm dev-kit/tests/test_accumulator_azure.py dev-kit/tests/test_accumulator_connector.py
```

- [ ] **Step 5: Run all agent tests to confirm nothing's broken.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/ -q
```

- [ ] **Step 6: Commit.**

```bash
git commit -m "chore(dev-kit): delete accumulator.py — replaced by project_state + block_status"
```

---

## Phase E — Update React UI

### Task E.1: Block status strings in UI

**Files:**
- Audit: `dev-kit/frontend/src/components/*.jsx`

Find any code that compares against `"PENDING" | "DRAFT" | "STALE" | "COMPLETE"` (old ConfigStatus enum values returned by the API). Replace with `"complete" | "incomplete"` (new shape).

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && grep -rn '"PENDING"\|"DRAFT"\|"STALE"\|"COMPLETE"\|ConfigStatus' dev-kit/frontend/src/ 2>&1 | grep -v node_modules | grep -v dist
```

- [ ] **Step 1: Survey results, identify affected components.**
- [ ] **Step 2: Map the strings.** Likely just relabel:
  - `PENDING` / `DRAFT` / `STALE` → `incomplete`
  - `COMPLETE` → `complete`
- [ ] **Step 3: Update UI components.**
- [ ] **Step 4: Run frontend tests.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/frontend && npm test -- --run
```

- [ ] **Step 5: Commit.**

```bash
git commit -am "feat(dev-kit/frontend): switch to new block-completion strings (complete/incomplete)"
```

### Task E.2: Remove checkpoint-restore UI

- [ ] **Step 1: Search frontend for any references to checkpoint endpoints.**

```bash
grep -rn 'checkpoints\|/checkpoints/' dev-kit/frontend/src/ 2>&1 | grep -v node_modules | grep -v dist
```

- [ ] **Step 2: Delete any UI button / dialog / route that exposed the now-deleted endpoints.**
- [ ] **Step 3: Run frontend tests; commit.**

```bash
git commit -am "chore(dev-kit/frontend): remove checkpoint-restore UI"
```

---

## Phase F — Migrate / delete legacy tests

### Task F.1: Decide per-file

| Test file | Decision |
|---|---|
| `tests/test_accumulator_azure.py` | DELETE — tests `ConfigAccumulator` Azure helper; class is gone |
| `tests/test_accumulator_connector.py` | DELETE — same |
| `tests/test_renderer.py` | REWRITE to test new `render_all(accumulator: dict, intake_state)` signature |
| `tests/test_app_project_routes.py` | REWRITE — uses ConfigAccumulator fixtures; switch to constructing on-disk state |
| `tests/test_app_deploy_routes.py` | ALREADY MIGRATED (during last task) |
| `tests/test_app_endpoints.py` | DELETED in Phase D.1 (checkpoint tests) |
| `tests/schemas/test_existing_configs_validate.py` | `xfail` with `@pytest.mark.xfail(reason="legacy YAMLs predate deterministic wizard; migration deferred")` |

- [ ] **Step 1: Apply each decision.**
- [ ] **Step 2: Verify all tests pass (or are xfail'd):**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest -q
```

- [ ] **Step 3: Commit per file or one commit.**

```bash
git commit -am "test(dev-kit): migrate/delete legacy ConfigAccumulator-based tests"
```

---

## Phase G — Final smoke + docs

### Task G.1: End-to-end smoke test

Verify the full deploy flow on a fresh project:

- [ ] **Step 1: Start the dev-kit locally.**

```bash
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && docker compose -f automation/docker/docker-compose.dev.yml up -d dev_kit
```

- [ ] **Step 2: Create a new project via the form (or curl):**

```bash
curl -X POST http://localhost:8080/api/projects -H "Content-Type: application/json" \
  -d '{"name":"smoke","project_name":"smoke","domain_description":"smoke test bot","selected_channels":["web"],"default_language":"english","supported_languages":["english"]}'
```

- [ ] **Step 3: Drive a few chat turns through the wizard.** Validate `_meta/intake_state.json`, `_meta/accumulator.json`, `_meta/field_status.json`, `_meta/history.jsonl` all populate correctly.

- [ ] **Step 4: Run a deploy preview** to verify selective compose generation still works (knowledge_engine should NOT appear for a has_kb=false project).

- [ ] **Step 5: Check `_meta/` directory contents:**

```bash
ls -la dev-kit/configs/smoke/_meta/
```

Expected: `intake_state.json`, `accumulator.json`, `field_status.json`, `current_phase.json`, `history.jsonl`. **No `checkpoints/` directory.**

- [ ] **Step 6: Commit a recording / screenshot or summary.**

```bash
git commit -m "test(dev-kit): manual smoke test of new state model — all green" --allow-empty
```

### Task G.2: Documentation updates

- [ ] **Step 1: Update `ARCHITECTURE.md` Tier 1 section** — note the state model is now on-disk-only (no in-memory engine).
- [ ] **Step 2: Update `CLAUDE.md` dev-kit layout** — replace `accumulator.py` references with `project_state.py + block_status.py + history.py`.
- [ ] **Step 3: Append a "Session N notes" section to `2026-05-14-implementation-session-notes.md`** describing this migration.

- [ ] **Step 4: Commit.**

```bash
git commit -am "docs: document state-layer migration in ARCHITECTURE.md + CLAUDE.md"
```

### Task G.3: Final code review (full branch)

- [ ] **Step 1: Dispatch the final code-reviewer subagent against the whole branch (`main..HEAD`).** Expected verdict: ready for PR.

---

## Final checklist (run before declaring complete)

- [ ] `accumulator.py` deleted; no imports anywhere
- [ ] `checkpoints.py` deleted; no imports anywhere; 3 endpoints removed
- [ ] `ConversationEngine` class gone from `conversation.py`
- [ ] `ConfigStatus` enum gone (replaced by `block_status.block_completion_status`)
- [ ] `_get_engine(slug)` removed from `app.py`
- [ ] `_meta/accumulator.json` written by tools at update_config
- [ ] `_meta/history.jsonl` appended on every chat turn
- [ ] `_meta/checkpoints/` directory NEVER created
- [ ] All `/api/projects/{slug}/*` endpoints work with the new state
- [ ] React UI block-status labels render correctly
- [ ] Checkpoint-restore UI removed
- [ ] `tests/agent/` all pass
- [ ] Legacy schema tests `xfail` (configs/ stay; tests still exist as tracking items)
- [ ] Smoke test passes end-to-end
- [ ] ARCHITECTURE.md + CLAUDE.md updated
- [ ] Final code review passes
