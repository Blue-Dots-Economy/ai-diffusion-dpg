# Dev-Kit Deterministic Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dev-kit's LLM-driven wizard with a constrained-agent architecture where the LLM mediates conversation only and a Python state machine owns routing, field invalidation, and phase transitions.

**Architecture:** A typed `IntakeState` captured up-front (5 fields from a project creation form + 7 binary flags from a 4-turn chat) drives deterministic behaviour for every downstream phase. Per-block `FIELD_RULES` declares each field's category (`predetermined` / `chat` / `deploy` / `derived` / `framework_default_only`), phase, `applies_if`, `invalidated_by`. A declarative `PHASES` config plus a single phase driver runs all phases; an end-of-turn router handles backtracking. Pre-deploy dry-run validates the merged YAML against runtime schemas baked into the dev-kit image at Docker build time.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI (existing dev-kit), React (existing frontend), Docker Compose, uv for env management.

---

## Source documents — READ FIRST

Every task references these. Implementers must have them open:

- **Design:** `docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md` — overall architecture, components, runtime sequence.
- **Field rules catalogue:** `docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md` — the source of truth for every field's rule. Tasks reference catalogue sections by number (e.g., `catalogue §7.1` is the agent_core field table).
- **Sync rule:** `.claude/rules/runtime-devkit-sync.md` — discipline for runtime schema ↔ dev-kit changes.

When this plan and the catalogue/design disagree, **the catalogue/design wins** — flag the inconsistency and fix the plan before proceeding.

## Locked decisions (from brainstorming)

These are baked in; do NOT revisit:

1. `dignity_check.questions` — predetermined canonical English (Trust evaluator handles cross-language semantics).
2. `agent.max_tool_rounds` — `framework_default_only` (no chat UI). Stays at 3 in dpg.yaml.
3. `state.session.ttl_minutes` — gated by `is_multi_turn`. Single-shot bots have no session state.
4. `conversation.session_end_eval.prompt` — language phase (with conversation messages).
5. `routing[*]` — per-subagent `routing` list is one chat field (whole-list invalidation, no positional addressing).
6. `voice.recording.consent_purpose` — standalone chat field on reach_layer.
7. Multimodal Input Handler — `framework_default_only` until PoC graduates.
8. CI Coverage guard — requires canonical instances per known consumer for open-map fields.

## Out of scope (deferred to separate plans)

- **Memory Layer selective deployment** — dropping Memgraph when `needs_persistent_user_data=false`. Tracked as a future enhancement.
- **Full dev-kit UI revamp** — only the minimal required UI changes are in this plan (project creation form, deploy form deploy-overridable surfacing, field status visibility in chat). Larger UX refactor is a separate plan after this lands.
- **Migration of pre-existing project configs** — projects authored under the old wizard re-create from scratch (per design §9).
- **CI guards** — self-contained-schema, Coverage, no-redundancy guards are documented (design §5) but implementation is deferred. The pre-deploy dry-run is the primary safety net.

---

## Target file structure

After this plan, the dev-kit tree looks like:

```
dev-kit/
├── Dockerfile                                  # MODIFIED: COPY runtime schemas into image
├── dev_kit/
│   ├── agent/
│   │   ├── intake_state.py                     # NEW
│   │   ├── field_rules/                        # NEW
│   │   │   ├── __init__.py                     # FieldRule dataclass + AGGREGATED_FIELD_RULES
│   │   │   ├── agent_core.py
│   │   │   ├── trust_layer.py
│   │   │   ├── knowledge_engine.py
│   │   │   ├── memory_layer.py
│   │   │   ├── action_gateway.py
│   │   │   ├── reach_layer.py
│   │   │   └── observability_layer.py
│   │   ├── phase_prompts/                      # NEW
│   │   │   ├── __init__.py
│   │   │   ├── tier.py                         # intake-state chat (4 turns)
│   │   │   ├── language.py
│   │   │   ├── knowledge.py
│   │   │   ├── memory.py
│   │   │   ├── user_state.py
│   │   │   ├── trust.py
│   │   │   ├── tools.py
│   │   │   ├── workflow.py
│   │   │   ├── observability.py
│   │   │   ├── reach.py
│   │   │   └── review.py
│   │   ├── phases_config.py                    # NEW: PHASES dict
│   │   ├── phase_driver.py                     # NEW
│   │   ├── router.py                           # NEW
│   │   ├── skeleton.py                         # NEW
│   │   ├── path_ops.py                         # NEW
│   │   ├── field_status.py                     # NEW: field_status.json read/write
│   │   ├── tools.py                            # REWRITTEN: 8 tools, ~300 lines
│   │   ├── accumulator.py                      # MODIFIED: add field_status helpers
│   │   ├── conversation.py                     # MODIFIED: call phase_driver.run_turn()
│   │   ├── renderer.py                         # MODIFIED: derived pass + runtime dry-run
│   │   ├── app.py                              # MODIFIED: form endpoints
│   │   ├── prompts/
│   │   │   ├── phases.py                       # DELETED
│   │   │   └── base.py                         # DELETED
│   │   └── deployer/
│   │       └── compose.py                      # MODIFIED: selective + REACH_LAYER_WEB_MODE
│   ├── schemas/
│   │   └── domain/                             # KEPT — chat-time validation, unchanged
│   └── frontend/src/components/
│       ├── ProjectCreationForm.jsx             # MODIFIED: 5 intake fields
│       └── DeploymentForm.jsx                  # MODIFIED: deploy_overridable surfacing
├── dpg_runtime_schemas/                        # NEW: baked-in via Docker COPY (build-time)
│   ├── __init__.py
│   ├── agent_core/__init__.py + config.py
│   ├── trust_layer/__init__.py + config.py
│   ├── knowledge_engine/__init__.py + config.py
│   ├── action_gateway/__init__.py + config.py
│   ├── memory_layer/__init__.py + config.py
│   ├── observability_layer/__init__.py + config.py
│   └── reach_layer/__init__.py + config.py
└── tests/
    └── agent/                                  # NEW & MODIFIED tests
        ├── test_intake_state.py
        ├── test_path_ops.py
        ├── test_field_rules_<block>.py × 7
        ├── test_skeleton.py
        ├── test_phase_driver.py
        ├── test_router.py
        ├── test_renderer_dry_run.py
        ├── test_tools.py
        ├── test_compose_generator.py
        └── test_wizard_flow.py                 # end-to-end
```

Existing tests under `tests/agent/test_phases_*.py` are deleted along with `prompts/phases.py`.

---

## Phase 0: Pre-flight checks

Before touching code, verify two assumptions the design rests on.

### Task 0.1: Verify every runtime block's schema is self-contained

The bake-in approach (design §3) requires every `<block>/src/schema/config.py` to only import from `pydantic`, `enum`, `typing`, and `__future__`. If any block imports from elsewhere, bake-in fails at Docker build.

**Files:**
- Read-only audit: `agent_core/src/schema/config.py`, `trust_layer/src/schema/config.py`, `knowledge_engine/src/schema/config.py`, `action_gateway/src/schema/config.py`, `memory_layer/src/schema/config.py`, `observability_layer/src/schema/config.py`, `reach_layer/base/schema/config.py`.

- [ ] **Step 1: Audit each schema file's imports.**

Run:
```bash
for f in agent_core/src/schema/config.py trust_layer/src/schema/config.py knowledge_engine/src/schema/config.py action_gateway/src/schema/config.py memory_layer/src/schema/config.py observability_layer/src/schema/config.py reach_layer/base/schema/config.py; do
  echo "=== $f ==="
  grep -E "^(import|from)" $f
done
```

Expected: every block shows only `from __future__ import annotations`, `from enum import Enum`, `from typing import ...`, `from pydantic import ...`. Nothing else.

- [ ] **Step 2: If any block has a non-allowlisted import, STOP and fix it.**

Fix by either inlining the import target into the schema file, or moving it into the same `schema/` directory (which is COPY'd as a unit). Do this in the runtime block — that's a runtime change, not a dev-kit change. Document the fix in the commit message.

- [ ] **Step 3: Commit any fixes per block (one commit per block).**

```bash
git add <block>/src/schema/config.py
git commit -m "refactor(<block>): make schema config self-contained for dev-kit bake-in"
```

If no fixes were needed: skip this step and note in the plan that Phase 0 was clean.

### Task 0.2: Confirm directory creation paths exist

- [ ] **Step 1: Create the new top-level directories.**

```bash
mkdir -p dev-kit/dev_kit/agent/field_rules
mkdir -p dev-kit/dev_kit/agent/phase_prompts
touch dev-kit/dev_kit/agent/field_rules/__init__.py
touch dev-kit/dev_kit/agent/phase_prompts/__init__.py
```

- [ ] **Step 2: Verify the parent `dev-kit/dev_kit/agent/` directory exists.**

```bash
ls dev-kit/dev_kit/agent/
```

Expected: shows existing files (accumulator.py, app.py, conversation.py, etc.).

- [ ] **Step 3: Commit the empty package stubs.**

```bash
git add dev-kit/dev_kit/agent/field_rules/__init__.py dev-kit/dev_kit/agent/phase_prompts/__init__.py
git commit -m "feat(dev-kit): scaffold field_rules and phase_prompts packages"
```

---

## Phase 1: Foundation types

`IntakeState`, `FieldRule`, and `path_ops` are the data primitives. Everything else depends on them.

### Task 1.1: `IntakeState` dataclass + persistence

**Files:**
- Create: `dev-kit/dev_kit/agent/intake_state.py`
- Create: `dev-kit/tests/agent/test_intake_state.py`

- [ ] **Step 1: Write the failing test for IntakeState shape and serialisation.**

`dev-kit/tests/agent/test_intake_state.py`:

```python
"""Tests for IntakeState dataclass and persistence."""
import json
import tempfile
from pathlib import Path

import pytest

from dev_kit.agent.intake_state import IntakeState, load_intake_state, save_intake_state


def _empty_state() -> IntakeState:
    return IntakeState(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="",
        project_name="",
    )


def test_intake_state_has_twelve_fields_plus_bookkeeping():
    state = _empty_state()
    # 12 intake fields + completed + updated_at
    assert hasattr(state, "has_kb")
    assert hasattr(state, "completed")
    assert hasattr(state, "updated_at")
    assert state.completed is False
    assert state.updated_at == ""


def test_save_load_roundtrip(tmp_path: Path):
    state = _empty_state()
    state_path = tmp_path / "intake_state.json"
    save_intake_state(state_path, state)
    loaded = load_intake_state(state_path)
    assert loaded == state


def test_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_intake_state(tmp_path / "does_not_exist.json")


def test_selected_channels_only_web_or_voice():
    """Channel literal forbids cli; web+voice only."""
    with pytest.raises(ValueError):
        IntakeState(
            has_kb=False, has_external_tools=False,
            is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
            needs_consent=False, has_hitl=False,
            selected_channels=["cli"],   # invalid
            default_language="english", supported_languages=["english"],
            domain_description="", project_name="",
        )
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd dev-kit && uv run pytest tests/agent/test_intake_state.py -v
```

Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement `intake_state.py`.**

`dev-kit/dev_kit/agent/intake_state.py`:

```python
"""IntakeState — typed intake captured before downstream phases run.

Persisted to `_meta/intake_state.json` under the project directory. Read by
the phase driver, FIELD_RULES handlers, and the renderer.

Belongs to the dev-kit deterministic wizard. See:
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §4
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Channel = Literal["web", "voice"]


@dataclass
class IntakeState:
    """The 12 intake fields plus bookkeeping.

    5 fields come from the project creation form (project_name,
    domain_description, selected_channels, default_language,
    supported_languages). 7 binary flags come from chat.
    """

    # Capabilities
    has_kb: bool
    has_external_tools: bool

    # Conversation pattern
    is_multi_turn: bool
    needs_persistent_user_data: bool
    is_companion_style: bool

    # Operational
    needs_consent: bool
    has_hitl: bool

    # Channels and languages (project creation form)
    selected_channels: list[Channel]
    default_language: str
    supported_languages: list[str]

    # Context (project creation form, LLM-only)
    domain_description: str
    project_name: str

    # Bookkeeping
    completed: bool = False
    updated_at: str = ""

    def __post_init__(self) -> None:
        # Validate Channel literal manually since dataclass doesn't enforce it.
        for ch in self.selected_channels:
            if ch not in ("web", "voice"):
                raise ValueError(
                    f"Invalid channel {ch!r}; only 'web' and 'voice' allowed"
                )

    def touch(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()


def save_intake_state(path: Path, state: IntakeState) -> None:
    """Persist intake state to disk as JSON.

    Args:
        path: Target file path (typically `<slug>/_meta/intake_state.json`).
        state: The IntakeState to save.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def load_intake_state(path: Path) -> IntakeState:
    """Load intake state from disk.

    Args:
        path: Source file path.

    Returns:
        The deserialised IntakeState.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"intake state not found at {path}")
    payload = json.loads(path.read_text())
    return IntakeState(**payload)
```

- [ ] **Step 4: Run the tests to verify they pass.**

```bash
cd dev-kit && uv run pytest tests/agent/test_intake_state.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/intake_state.py dev-kit/tests/agent/test_intake_state.py
git commit -m "feat(dev-kit): add IntakeState dataclass with persistence"
```

### Task 1.2: `FieldRule` dataclass + Category enum

**Files:**
- Create: `dev-kit/dev_kit/agent/field_rules/__init__.py` (replace the empty stub)
- Create: `dev-kit/tests/agent/test_field_rules_dataclass.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_field_rules_dataclass.py`:

```python
"""Tests for FieldRule dataclass shape."""
import pytest

from dev_kit.agent.field_rules import FieldRule


def test_fieldrule_predetermined_minimal():
    rule = FieldRule(category="predetermined", rule="set: is_companion_style")
    assert rule.category == "predetermined"
    assert rule.rule == "set: is_companion_style"
    assert rule.deploy_overridable is False
    assert rule.invalidated_by == []


def test_fieldrule_chat_with_deploy_override():
    rule = FieldRule(
        category="chat",
        phase="language",
        default="anthropic",
        description="LLM provider",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    )
    assert rule.category == "chat"
    assert rule.deploy_overridable is True
    assert rule.phase == "language"


def test_fieldrule_invalid_category_rejected():
    with pytest.raises(ValueError):
        FieldRule(category="invalid_category")


def test_fieldrule_frozen():
    rule = FieldRule(category="chat", phase="trust")
    with pytest.raises((TypeError, AttributeError)):
        rule.category = "deploy"  # type: ignore[misc]
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_rules_dataclass.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `field_rules/__init__.py`.**

`dev-kit/dev_kit/agent/field_rules/__init__.py`:

```python
"""FieldRule dataclass and the aggregated rules registry.

Each runtime block has its own module under this package
(e.g. `field_rules.agent_core`) exporting a `FIELD_RULES` dict keyed by
dotted field path (relative to the block root). This module re-exports
the union as `AGGREGATED_FIELD_RULES` with block-prefixed paths.

See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, Optional

Category = Literal[
    "predetermined", "chat", "deploy", "derived", "framework_default_only"
]
_VALID_CATEGORIES = set(Category.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class FieldRule:
    """Per-field rule. See catalogue §2.2 for category semantics."""

    category: Category

    # For predetermined: Python-expression string referencing intake state.
    #   e.g. "set: is_companion_style", "set: needs_consent"
    rule: Optional[str] = None

    # For chat
    phase: Optional[str] = None
    default: Optional[Any] = None
    must_include: Optional[list[Any]] = None
    description: Optional[str] = None
    applies_if: Optional[str] = None
    invalidated_by: list[str] = dc_field(default_factory=list)

    # For deploy and deploy-overridable chat
    advanced: bool = False
    deploy_overridable: bool = False

    # For derived
    compute: Optional[str] = None

    # For schema injection in prompts
    pydantic_class: Optional[str] = None

    def __post_init__(self) -> None:
        if self.category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category {self.category!r}; "
                f"must be one of {sorted(_VALID_CATEGORIES)}"
            )


# Valid phase names — referenced by per-block FIELD_RULES tests to assert
# every chat field's `phase` is one of these.
FIELD_RULES_PHASES_VALID = {
    "tier", "language", "knowledge", "memory", "user_state", "trust",
    "tools", "workflow", "observability", "reach", "review",
}


# AGGREGATED_FIELD_RULES is built lazily by the loader below; populated
# after every per-block module has registered its FIELD_RULES dict.
# At plan-time it's empty — each block module fills it in Phase 3.
AGGREGATED_FIELD_RULES: dict[str, FieldRule] = {}


def register_block_rules(block_name: str, rules: dict[str, FieldRule]) -> None:
    """Register a block's FIELD_RULES into the aggregate registry.

    Args:
        block_name: e.g. "agent_core", "trust_layer".
        rules: The block's FIELD_RULES dict with paths relative to block root.

    Mutation: prefixes each path with `<block_name>.` and inserts into
    AGGREGATED_FIELD_RULES. Re-registering the same block replaces its entries.
    """
    # Drop previous entries for this block (idempotent re-registration).
    prefix = f"{block_name}."
    for path in list(AGGREGATED_FIELD_RULES.keys()):
        if path.startswith(prefix):
            del AGGREGATED_FIELD_RULES[path]
    for relative_path, rule in rules.items():
        AGGREGATED_FIELD_RULES[f"{prefix}{relative_path}"] = rule


__all__ = [
    "FieldRule", "Category", "FIELD_RULES_PHASES_VALID",
    "AGGREGATED_FIELD_RULES", "register_block_rules",
]
```

- [ ] **Step 4: Run tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_rules_dataclass.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/field_rules/__init__.py dev-kit/tests/agent/test_field_rules_dataclass.py
git commit -m "feat(dev-kit): add FieldRule dataclass + aggregated rules registry"
```

### Task 1.3: `path_ops.py` — dotted-path resolver with `[name=X]` syntax

**Files:**
- Create: `dev-kit/dev_kit/agent/path_ops.py`
- Create: `dev-kit/tests/agent/test_path_ops.py`

- [ ] **Step 1: Write the failing tests.**

`dev-kit/tests/agent/test_path_ops.py`:

```python
"""Tests for path_ops: get/set/clear with [name=X] list-of-objects syntax."""
import pytest

from dev_kit.agent.path_ops import get_path, set_path, clear_path


def test_get_simple_dotted_path():
    data = {"agent": {"timeout_ms": 10000}}
    assert get_path(data, "agent.timeout_ms") == 10000


def test_get_missing_returns_none():
    data = {"agent": {}}
    assert get_path(data, "agent.timeout_ms") is None


def test_get_list_of_objects_by_name():
    data = {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval", "route": "knowledge_engine"},
                {"name": "other", "route": "other"},
            ]
        }
    }
    assert get_path(data, "connectors.internal[name=knowledge_retrieval]") == {
        "name": "knowledge_retrieval",
        "route": "knowledge_engine",
    }
    assert get_path(data, "connectors.internal[name=knowledge_retrieval].route") == "knowledge_engine"


def test_get_list_of_objects_missing_match_returns_none():
    data = {"connectors": {"internal": [{"name": "other"}]}}
    assert get_path(data, "connectors.internal[name=missing]") is None


def test_set_simple_dotted_path_creates_nested():
    data = {}
    set_path(data, "agent.timeout_ms", 5000)
    assert data == {"agent": {"timeout_ms": 5000}}


def test_set_list_of_objects_appends_when_missing():
    data = {"connectors": {"internal": []}}
    set_path(data, "connectors.internal[name=knowledge_retrieval].route", "knowledge_engine")
    assert data == {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval", "route": "knowledge_engine"}
            ]
        }
    }


def test_set_list_of_objects_updates_existing():
    data = {"connectors": {"internal": [{"name": "knowledge_retrieval", "route": "old"}]}}
    set_path(data, "connectors.internal[name=knowledge_retrieval].route", "knowledge_engine")
    assert data["connectors"]["internal"][0]["route"] == "knowledge_engine"


def test_clear_simple_path_removes_key():
    data = {"agent": {"timeout_ms": 10000, "retry_attempts": 2}}
    clear_path(data, "agent.timeout_ms")
    assert data == {"agent": {"retry_attempts": 2}}


def test_clear_list_of_objects_removes_matching_element():
    data = {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval"},
                {"name": "other"},
            ]
        }
    }
    clear_path(data, "connectors.internal[name=knowledge_retrieval]")
    assert data == {"connectors": {"internal": [{"name": "other"}]}}


def test_clear_missing_is_noop():
    data = {"agent": {}}
    clear_path(data, "agent.timeout_ms")  # should not raise
    assert data == {"agent": {}}


def test_set_list_of_objects_multiple_keys():
    """Composite key shouldn't break; only single [key=value] supported."""
    data = {}
    set_path(data, "subagents[id=enquiry].name", "Enquiry")
    assert data == {"subagents": [{"id": "enquiry", "name": "Enquiry"}]}
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
cd dev-kit && uv run pytest tests/agent/test_path_ops.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `path_ops.py`.**

`dev-kit/dev_kit/agent/path_ops.py`:

```python
"""Dotted-path resolver with `[key=value]` list-of-objects syntax.

Used by FIELD_RULES to address fields in the nested accumulator dict.
See docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md §2.1
and §5 "Path syntax (including list-of-objects)".
"""
from __future__ import annotations

import re
from typing import Any

# Matches a single segment that may include a [key=value] selector.
# Examples: "internal[name=knowledge_retrieval]", "subagents[id=enquiry]", "agent".
_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([^=]+)=([^\]]+)\])?$")


def _parse_segment(segment: str) -> tuple[str, str | None, str | None]:
    """Return (attribute_name, selector_key, selector_value) for a segment."""
    m = _SEGMENT_RE.match(segment)
    if not m:
        raise ValueError(f"Invalid path segment: {segment!r}")
    attr, key, value = m.groups()
    return attr, key, value


def _walk_segments(path: str) -> list[tuple[str, str | None, str | None]]:
    """Split a dotted path into parsed segments."""
    return [_parse_segment(seg) for seg in path.split(".")]


def get_path(data: dict, path: str) -> Any:
    """Read the value at `path` in `data`. Returns None if any segment is missing."""
    current: Any = data
    for attr, key, value in _walk_segments(path):
        if not isinstance(current, dict):
            return None
        current = current.get(attr)
        if current is None:
            return None
        if key is not None:
            # current should be a list of dicts; find element matching key=value.
            if not isinstance(current, list):
                return None
            matched = next(
                (item for item in current if isinstance(item, dict) and item.get(key) == value),
                None,
            )
            current = matched
            if current is None:
                return None
    return current


def set_path(data: dict, path: str, value: Any) -> None:
    """Write `value` at `path` in `data`. Creates intermediate dicts/lists as needed.

    For list-of-objects segments (`attr[key=val]`), find-or-append: matching
    element is updated; otherwise a new element with `{key: val}` is appended.
    """
    segments = _walk_segments(path)
    current: Any = data
    for i, (attr, key, sel_value) in enumerate(segments):
        is_last = i == len(segments) - 1
        if key is None:
            if is_last:
                current[attr] = value
                return
            if attr not in current or not isinstance(current[attr], dict):
                current[attr] = {}
            current = current[attr]
        else:
            # List-of-objects segment.
            if attr not in current or not isinstance(current[attr], list):
                current[attr] = []
            lst = current[attr]
            matched = next(
                (item for item in lst if isinstance(item, dict) and item.get(key) == sel_value),
                None,
            )
            if matched is None:
                matched = {key: sel_value}
                lst.append(matched)
            if is_last:
                # Setting the whole element to a value isn't a sensible operation
                # for list-of-objects; we update the matched dict instead.
                if isinstance(value, dict):
                    matched.update(value)
                else:
                    raise ValueError(
                        f"Cannot set list-of-objects element {attr}[{key}={sel_value}] "
                        f"to non-dict value {value!r}"
                    )
                return
            current = matched


def clear_path(data: dict, path: str) -> None:
    """Remove the value at `path`. No-op if absent.

    For list-of-objects segments at the end of the path, removes the matching element.
    """
    segments = _walk_segments(path)
    current: Any = data
    for i, (attr, key, sel_value) in enumerate(segments):
        is_last = i == len(segments) - 1
        if key is None:
            if is_last:
                if isinstance(current, dict) and attr in current:
                    del current[attr]
                return
            if not isinstance(current, dict) or attr not in current:
                return
            current = current[attr]
        else:
            if not isinstance(current, dict) or attr not in current:
                return
            lst = current[attr]
            if not isinstance(lst, list):
                return
            if is_last:
                current[attr] = [
                    item for item in lst
                    if not (isinstance(item, dict) and item.get(key) == sel_value)
                ]
                return
            matched = next(
                (item for item in lst if isinstance(item, dict) and item.get(key) == sel_value),
                None,
            )
            if matched is None:
                return
            current = matched


__all__ = ["get_path", "set_path", "clear_path"]
```

- [ ] **Step 4: Run tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_path_ops.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/path_ops.py dev-kit/tests/agent/test_path_ops.py
git commit -m "feat(dev-kit): add path_ops resolver with [name=X] list-of-objects syntax"
```

---

## Phase 2: Runtime schema bake-in

Make runtime block schemas importable from inside the dev-kit container. See design §3 "How the dry-run runs (schemas baked into the dev-kit image)" and the sync rule.

### Task 2.1: Add `COPY` lines to dev-kit Dockerfile

**Files:**
- Modify: `dev-kit/Dockerfile`

- [ ] **Step 1: Read the existing Dockerfile and locate the post-`COPY dev-kit/ .` line.**

```bash
cat dev-kit/Dockerfile
```

Look for the line `COPY dev-kit/ .` (currently around line 46).

- [ ] **Step 2: Insert the runtime schema COPY block after `COPY dev-kit/ .` and before `COPY automation/`.**

`dev-kit/Dockerfile` — insert these lines:

```dockerfile
# Bake each runtime block's Pydantic config schema into the image so the
# dev-kit's pre-deploy dry-run can validate generated YAML against the
# exact same MergedConfig the runtime service will use at boot. Each schema
# is self-contained (only imports pydantic/enum/typing/__future__); a CI
# guard (planned) enforces this. See:
#   docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §3
#   .claude/rules/runtime-devkit-sync.md
COPY agent_core/src/schema/config.py           /app/dpg_runtime_schemas/agent_core/config.py
COPY trust_layer/src/schema/config.py          /app/dpg_runtime_schemas/trust_layer/config.py
COPY knowledge_engine/src/schema/config.py     /app/dpg_runtime_schemas/knowledge_engine/config.py
COPY action_gateway/src/schema/config.py       /app/dpg_runtime_schemas/action_gateway/config.py
COPY memory_layer/src/schema/config.py         /app/dpg_runtime_schemas/memory_layer/config.py
COPY observability_layer/src/schema/config.py  /app/dpg_runtime_schemas/observability_layer/config.py
COPY reach_layer/base/schema/config.py         /app/dpg_runtime_schemas/reach_layer/config.py
RUN find /app/dpg_runtime_schemas -type d -exec touch {}/__init__.py \;
```

- [ ] **Step 3: Build the dev-kit image to verify the COPY paths resolve.**

```bash
cd automation/docker && docker compose -f docker-compose.dev.yml build dev_kit
```

Expected: build succeeds. If it fails with "COPY failed: file not found", check that the build context (`../..` in the compose file) is the repo root.

- [ ] **Step 4: Verify the schemas import inside the built image.**

```bash
docker run --rm sanketikahub/dpg-dev-kit:latest python -c "
from dpg_runtime_schemas.agent_core.config import MergedConfig as AC
from dpg_runtime_schemas.trust_layer.config import MergedConfig as TL
from dpg_runtime_schemas.knowledge_engine.config import MergedConfig as KE
from dpg_runtime_schemas.action_gateway.config import MergedConfig as AG
from dpg_runtime_schemas.memory_layer.config import MergedConfig as ML
from dpg_runtime_schemas.observability_layer.config import MergedConfig as OL
from dpg_runtime_schemas.reach_layer.config import MergedConfig as RL
print('all schemas imported successfully')
"
```

Expected: prints `all schemas imported successfully`.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/Dockerfile
git commit -m "feat(dev-kit): bake runtime block schemas into image for pre-deploy dry-run"
```

### Task 2.2: Renderer uses baked-in schemas for dry-run

**Files:**
- Modify: `dev-kit/dev_kit/agent/renderer.py`
- Create: `dev-kit/tests/agent/test_renderer_runtime_validate.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_renderer_runtime_validate.py`:

```python
"""Tests for renderer.runtime_validate using baked-in MergedConfig classes."""
import pytest

from dev_kit.agent.renderer import runtime_validate
from dev_kit.agent.errors import RuntimeValidationError


def test_runtime_validate_unknown_block_raises():
    with pytest.raises(KeyError):
        runtime_validate("does_not_exist", {})


def test_runtime_validate_invalid_yaml_raises(monkeypatch):
    """A YAML missing required fields should be rejected by the runtime schema."""
    # Use trust_layer: it has required fields under trust.* that an empty dict lacks.
    with pytest.raises(RuntimeValidationError) as exc_info:
        runtime_validate("trust_layer", {})
    assert "trust_layer" in str(exc_info.value)


def test_runtime_validate_valid_yaml_passes(monkeypatch):
    """A minimal-but-valid agent_core dict should pass."""
    # Construct from the merged-config Pydantic class itself to guarantee validity.
    from dpg_runtime_schemas.agent_core.config import MergedConfig
    instance = MergedConfig.model_construct()
    data = instance.model_dump()
    # No exception expected.
    runtime_validate("agent_core", data)
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd dev-kit && uv run pytest tests/agent/test_renderer_runtime_validate.py -v
```

Expected: ImportError on `runtime_validate` or `RuntimeValidationError`.

- [ ] **Step 3: Add `RuntimeValidationError` to `errors.py`.**

`dev-kit/dev_kit/agent/errors.py` (append at the bottom):

```python
class RuntimeValidationError(ValueError):
    """Raised when a rendered YAML fails the runtime block's Pydantic validation.

    Surfaces the offending block name and the underlying Pydantic error tree.
    Caught by the renderer's deploy path and shown to the user via the wizard
    so they can fix the offending fields before retrying deploy.
    """

    def __init__(self, block: str, pydantic_error: Exception) -> None:
        super().__init__(f"{block}: {pydantic_error}")
        self.block = block
        self.pydantic_error = pydantic_error
```

- [ ] **Step 4: Add `RUNTIME_SCHEMAS` registry and `runtime_validate` to renderer.py.**

`dev-kit/dev_kit/agent/renderer.py` — add at top of file (or near other imports) and add the validator function:

```python
# Add to imports block:
from dpg_runtime_schemas.agent_core.config import MergedConfig as _AgentCoreCfg
from dpg_runtime_schemas.trust_layer.config import MergedConfig as _TrustLayerCfg
from dpg_runtime_schemas.knowledge_engine.config import MergedConfig as _KnowledgeEngineCfg
from dpg_runtime_schemas.action_gateway.config import MergedConfig as _ActionGatewayCfg
from dpg_runtime_schemas.memory_layer.config import MergedConfig as _MemoryLayerCfg
from dpg_runtime_schemas.observability_layer.config import MergedConfig as _ObservabilityLayerCfg
from dpg_runtime_schemas.reach_layer.config import MergedConfig as _ReachLayerCfg

from dev_kit.agent.errors import RuntimeValidationError

RUNTIME_SCHEMAS: dict[str, type] = {
    "agent_core": _AgentCoreCfg,
    "trust_layer": _TrustLayerCfg,
    "knowledge_engine": _KnowledgeEngineCfg,
    "action_gateway": _ActionGatewayCfg,
    "memory_layer": _MemoryLayerCfg,
    "observability_layer": _ObservabilityLayerCfg,
    "reach_layer": _ReachLayerCfg,
}


def runtime_validate(block: str, data: dict) -> None:
    """Validate rendered YAML against the runtime block's MergedConfig.

    Args:
        block: Block name, e.g. "agent_core".
        data: The fully-merged config dict that the running service would receive.

    Raises:
        KeyError: If `block` is not a known runtime block.
        RuntimeValidationError: If the data fails Pydantic validation.
    """
    schema_cls = RUNTIME_SCHEMAS[block]
    try:
        schema_cls.model_validate(data)
    except Exception as e:
        raise RuntimeValidationError(block, e) from e
```

- [ ] **Step 5: Run the tests inside the docker container (since baked schemas only exist there).**

```bash
docker run --rm -v $(pwd)/dev-kit:/app sanketikahub/dpg-dev-kit:latest \
  python -m pytest tests/agent/test_renderer_runtime_validate.py -v
```

Note: when running locally without Docker, this test will skip the import — that's expected. The CI runs against the built image.

Expected: all 3 tests pass inside the container.

- [ ] **Step 6: Commit.**

```bash
git add dev-kit/dev_kit/agent/renderer.py dev-kit/dev_kit/agent/errors.py dev-kit/tests/agent/test_renderer_runtime_validate.py
git commit -m "feat(dev-kit): add runtime_validate using baked-in MergedConfig classes"
```

### Task 2.3: Wire runtime dry-run into `render_all` flow

**Files:**
- Modify: `dev-kit/dev_kit/agent/renderer.py` (the existing `render_all` function)

- [ ] **Step 1: Read the existing `render_all` flow.**

```bash
grep -n "def render_all\|def render_block\|validate_partial\|write_yaml" dev-kit/dev_kit/agent/renderer.py | head -20
```

- [ ] **Step 2: Locate the step that writes YAML files. Add a dry-run pass right before it.**

In `render_all` (or its equivalent), insert this pass after dev-kit validation and before YAML writes:

```python
# Step 4 (new): Pre-deploy dry-run — validate through the runtime's own
# baked-in schemas (see design §3 "How the dry-run runs", and the bake-in
# in dev-kit/Dockerfile). Catches anything the dev-kit mirror accepted but
# the runtime would reject at boot.
for block, data in overlaid.items():
    if block in RUNTIME_SCHEMAS:
        runtime_validate(block, data)
```

(The exact placement depends on the existing function shape. The dry-run MUST run before bind-mounting / writing YAML so the user sees errors before deploy.)

- [ ] **Step 3: Add a test that exercises the integrated flow.**

`dev-kit/tests/agent/test_renderer_runtime_validate.py` — append:

```python
def test_render_all_fails_when_runtime_rejects():
    """If any block's merged YAML is runtime-invalid, render_all raises.

    Skipped at this stage: a full project_path + accumulator + intake_state
    fixture requires `build_skeleton` (Phase 4). The integration test is
    completed in Task 12.3 (end-to-end wizard flow).
    """
    pytest.skip("integration fixture available after Task 4.1 build_skeleton lands")
```

(This test is skipped initially; flesh out the fixture once `build_skeleton` exists in Phase 4.)

- [ ] **Step 4: Verify the dry-run is wired in by running the full renderer test suite.**

```bash
docker run --rm -v $(pwd)/dev-kit:/app sanketikahub/dpg-dev-kit:latest \
  python -m pytest tests/agent/test_renderer*.py -v
```

Expected: existing tests still pass; the skipped integration test does not error.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/renderer.py dev-kit/tests/agent/test_renderer_runtime_validate.py
git commit -m "feat(dev-kit): wire runtime dry-run into render_all before YAML writes"
```

---

## Phase 3: FIELD_RULES content per block

Encode the catalogue (`docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md` §7) into per-block Python modules. Each block follows the same task shape; the *content* differs.

### Common task shape (apply for every block)

For each block N ∈ {agent_core, trust_layer, knowledge_engine, memory_layer, action_gateway, reach_layer, observability_layer}:

1. **Write the test** at `dev-kit/tests/agent/test_field_rules_<block>.py`. The test asserts:
   - Every field path in the catalogue §7.N table has a corresponding entry in the block's `FIELD_RULES`.
   - Every `predetermined` rule references only `IntakeState` field names.
   - Every `chat` rule has a `phase` set to a valid PHASES key.
   - Every `applies_if` and `invalidated_by` expression references only `IntakeState` fields.
   - `framework_default_only` entries have no `phase`, `default`, or `applies_if`.
   - The aggregated registry exposes block-prefixed paths after `register_block_rules(...)` runs.

2. **Run the test to verify it fails** (module doesn't exist yet).

3. **Implement** `dev-kit/dev_kit/agent/field_rules/<block>.py` exporting `FIELD_RULES: dict[str, FieldRule]`, transcribed exactly from catalogue §7.N. At the bottom of the file, call `register_block_rules("<block>", FIELD_RULES)`.

4. **Run the test to verify it passes.**

5. **Commit** per block.

### Task 3.1: `agent_core` FIELD_RULES

**Reference:** catalogue §7.1 (full table) + §3.1 (always-asked) + §4 (gating per IntakeState).

**Files:**
- Create: `dev-kit/dev_kit/agent/field_rules/agent_core.py`
- Create: `dev-kit/tests/agent/test_field_rules_agent_core.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_field_rules_agent_core.py`:

```python
"""Tests for agent_core FIELD_RULES content (per catalogue §7.1)."""
import pytest

from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID
from dev_kit.agent.field_rules.agent_core import FIELD_RULES


# Catalogue §7.1: the full set of domain-half paths under agent_core.
# This list MUST match the catalogue exactly. When the catalogue changes,
# update this list and the FIELD_RULES dict together.
EXPECTED_PATHS = {
    # Always-asked chat (catalogue §3.1)
    "agent.primary_model",
    "agent.fallback_model",
    "agent.provider",
    "conversation.blocked_message",
    "conversation.escalation_message",
    "conversation.output_blocked_message",
    "conversation.unknown_intent_message",
    "conversation.unsupported_language_message",
    "preprocessing.language_normalisation.enabled",
    "preprocessing.language_normalisation.provider",
    "preprocessing.language_normalisation.model",
    "preprocessing.nlu_processor.provider",
    "preprocessing.nlu_processor.model",
    "preprocessing.nlu_processor.domain_instruction",
    "preprocessing.nlu_processor.intents",
    "preprocessing.nlu_processor.entities",
    "agent_workflow.agent_system_prompt",
    "agent_workflow.default_fallback_subagent_id",
    "agent_workflow.subagents",
    "agent_workflow.global_intents",
    "agent_workflow.global_routing",
    "agent_workflow.global_tools",
    "channels.web.system_prompt_suffix",
    "channels.web.turn_assembler.silence_trigger.silence_ms",
    "channels.web.turn_assembler.max_wait_ceiling.max_wait_ms",
    # Gated chat (catalogue §4)
    "agent.consent_prompt",
    "conversation.termination_message",
    "conversation.consent_message",
    "conversation.consent_decline_ack",
    "conversation.profile_complete_message",
    "conversation.returning_user_greeting",
    "conversation.user_state_model.default_state",
    "conversation.user_state_model.states",
    "conversation.session_end_eval.prompt",
    "connectors.read",
    "connectors.write",
    "connectors.identity",
    "connectors.internal[name=knowledge_retrieval].description",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.call_when",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure",
    "connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line",
    "preprocessing.nlu_processor.signal_intents",
    "entity_to_profile_field",
    "hitl.response_message",
    "channels.voice.system_prompt_suffix",
    "channels.voice.tts_rules.numbers",
    "channels.voice.tts_rules.money",
    "channels.voice.tts_rules.dates",
    "channels.voice.tts_rules.time",
    "channels.voice.tts_rules.phone",
    "channels.voice.tts_rules.abbreviations",
    "channels.voice.tts_rules.output_script",
    "channels.voice.tts_rules.english_loanwords",
    "channels.voice.tts_rules.email",
    "channels.voice.tts_rules.named_entities",
    "channels.voice.terminal_word",
    "channels.voice.turn_assembler.semantic_gate",
    # Predetermined (catalogue §7.1)
    "agent.ask_for_consent",
    "conversation.user_state_model.enabled",
    "conversation.session_end_eval.enabled",
    "preprocessing.language_normalisation.default_language",
    "preprocessing.language_normalisation.supported_languages",
    "connectors.internal[name=knowledge_retrieval]",
    "connectors.internal[name=knowledge_retrieval].name",
    "connectors.internal[name=knowledge_retrieval].route",
    "connectors.internal[name=knowledge_retrieval].input_schema",
    "channels.voice.turn_assembler.silence_trigger.silence_ms",
    "channels.voice.turn_assembler.max_wait_ceiling.max_wait_ms",
    # Derived
    "agent_workflow.workflow_id",
    "observability.domain",
}


def test_all_expected_paths_present():
    actual = set(FIELD_RULES.keys())
    missing = EXPECTED_PATHS - actual
    extra = actual - EXPECTED_PATHS
    assert missing == set(), f"missing rules: {sorted(missing)}"
    # `extra` is allowed only if catalogue was updated and this test is stale —
    # but flag it as a warning so the catalogue stays in sync.
    if extra:
        pytest.fail(f"unexpected rules not in catalogue: {sorted(extra)}")


def test_deploy_overridable_fields_are_chat():
    for path in ("agent.primary_model", "agent.fallback_model", "agent.provider"):
        rule = FIELD_RULES[path]
        assert rule.category == "chat", f"{path} must be chat"
        assert rule.deploy_overridable is True, f"{path} must be deploy_overridable"


def test_predetermined_have_rule_expressions():
    for path, rule in FIELD_RULES.items():
        if rule.category == "predetermined":
            assert rule.rule, f"{path}: predetermined rule must define `rule`"


def test_chat_fields_have_phase():
    for path, rule in FIELD_RULES.items():
        if rule.category == "chat":
            assert rule.phase, f"{path}: chat rule must define `phase`"
```

(`FIELD_RULES_PHASES_VALID` is already exported by `field_rules/__init__.py` per Task 1.2.)

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_rules_agent_core.py -v
```

Expected: ImportError (`field_rules.agent_core` doesn't exist).

- [ ] **Step 3: Implement `field_rules/agent_core.py`.**

Transcribe from catalogue §7.1. Pattern (full content goes in the file — this is one representative example):

```python
"""FIELD_RULES for agent_core. See catalogue §7.1 for the source of truth.

Path syntax: dotted, with `[name=X]` / `[id=X]` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ───────────────────────────────────────────────────────
    # agent.*  (catalogue §7.1)
    # ───────────────────────────────────────────────────────
    "agent.primary_model": FieldRule(
        category="chat",
        phase="language",
        description="LLM model for the main loop. Must match provider; cannot equal fallback_model.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),
    "agent.fallback_model": FieldRule(
        category="chat",
        phase="language",
        description="Fallback LLM model. Must match provider; cannot equal primary_model.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),
    "agent.provider": FieldRule(
        category="chat",
        phase="language",
        default="anthropic",
        description="LLM provider. Switching invalidates the two model fields.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),
    "agent.consent_prompt": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_consent",
        invalidated_by=["needs_consent", "default_language", "supported_languages"],
        description="Consent prompt shown to the user.",
        pydantic_class="AgentSection",
    ),
    "agent.ask_for_consent": FieldRule(
        category="predetermined",
        rule="set: needs_consent",
        invalidated_by=["needs_consent"],
        pydantic_class="AgentSection",
    ),

    # ... continue transcribing the rest from catalogue §7.1 ...
    # (Implementer: copy every row from the §7.1 table, including:
    #  - conversation.* messages (with applies_if and invalidated_by from catalogue)
    #  - user_state_model.* (predetermined enabled + chat default_state, states)
    #  - session_end_eval.* (predetermined enabled + chat prompt)
    #  - connectors.{read,write,identity,internal} entries
    #  - preprocessing.{language_normalisation, nlu_processor} fields
    #  - entity_to_profile_field
    #  - hitl.response_message
    #  - agent_workflow.* (workflow_id derived, subagents chat, global_*, etc.)
    #  - channels.{web,voice}.* (turn assembler, tts_rules, etc.)
    #  - observability.domain (derived) )
}

register_block_rules("agent_core", FIELD_RULES)
```

(The implementer transcribes the rest from catalogue §7.1 mechanically. Each row in the catalogue becomes one `FieldRule(...)` entry.)

- [ ] **Step 4: Run the test to verify it passes.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_rules_agent_core.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/field_rules/agent_core.py dev-kit/dev_kit/agent/field_rules/__init__.py dev-kit/tests/agent/test_field_rules_agent_core.py
git commit -m "feat(dev-kit): encode agent_core FIELD_RULES from catalogue §7.1"
```

### Task 3.2: `trust_layer` FIELD_RULES

**Reference:** catalogue §7.2. Apply the same task shape — test asserting EXPECTED_PATHS matches the catalogue §7.2 table, then implement, then commit.

EXPECTED_PATHS for trust_layer (catalogue §7.2):
```
trust.policy_pack
trust.input_rules.blocked_phrases
trust.input_rules.blocked_input_message
trust.input_rules.escalation_topics
trust.output_rules.blocked_phrases
trust.output_rules.output_blocked_message
trust.policy_packs                                       # open map; FIELD_RULES tracks presence
trust.consent.consent_phrases
trust.consent.decline_phrases
trust.hitl.holding_message
trust.hitl.queue_backend                                 # deploy
trust.hitl.notification_webhook                          # deploy (conditional)
dignity_check.enabled                                    # predetermined
dignity_check.questions                                  # predetermined
observability.domain                                     # derived
```

Per the locked decision, `dignity_check.questions` stays predetermined (canonical English):

```python
_CANONICAL_DIGNITY_QUESTIONS = [
    "Does this blame the user?",
    "Does it over-promise?",
    "Does it push urgency?",
    "Does it reduce their agency?",
    "Does it sound like a script instead of a human call?",
]

"dignity_check.questions": FieldRule(
    category="predetermined",
    rule=f"set: {_CANONICAL_DIGNITY_QUESTIONS!r} if is_companion_style else []",
    invalidated_by=["is_companion_style"],
    pydantic_class="DignityCheckSection",
),
```

Commit: `feat(dev-kit): encode trust_layer FIELD_RULES from catalogue §7.2`.

### Task 3.3: `knowledge_engine` FIELD_RULES

**Reference:** catalogue §7.5. Apply same task shape.

EXPECTED_PATHS (catalogue §7.5):
```
knowledge.blocks.glossary.enabled
knowledge.blocks.glossary.mappings
knowledge.blocks.static_knowledge_base.enabled           # predetermined
knowledge.blocks.static_knowledge_base.collection_name   # predetermined
knowledge.blocks.static_knowledge_base.default_doc_type
knowledge.blocks.static_knowledge_base.intent_filters
observability.domain                                     # derived
```

Note: `knowledge.blocks.multimodal_input_handler.*` stays `framework_default_only` per locked decision #7 — do NOT add a chat entry.

Commit: `feat(dev-kit): encode knowledge_engine FIELD_RULES from catalogue §7.5`.

### Task 3.4: `memory_layer` FIELD_RULES

**Reference:** catalogue §7.6. Apply same task shape.

EXPECTED_PATHS (catalogue §7.6):
```
state.session.ttl_minutes
state.session.schema
state.persistent                                         # predetermined (structural)
state.persistent.graph.user_node.label
state.persistent.graph.user_node.key
state.persistent.graph.subnodes
state.persistent.merge_on_session_end
user_data_persistence.default_mode                       # predetermined
reengagement.triggers
observability.domain                                     # derived
```

Per locked decision #3, `state.session.ttl_minutes` is gated by `is_multi_turn`. Per the deferred enhancement (Memory Layer selective deployment), keep `state.persistent` predetermined-Optional logic — Memgraph gating happens later via compose changes only.

Commit: `feat(dev-kit): encode memory_layer FIELD_RULES from catalogue §7.6`.

### Task 3.5: `action_gateway` FIELD_RULES

**Reference:** catalogue §7.4. Apply same task shape.

EXPECTED_PATHS (catalogue §7.4):
```
tools                                                    # the whole list (chat, gated by has_external_tools)
observability.domain                                     # derived
```

The `tools[id=X].*` per-entry shape is enforced by the Pydantic mirror; FIELD_RULES tracks the whole `tools` list as one chat field. Per-entry editing is handled by the `add_tool` / OpenAPI parser tools (Phase 6 of the plan).

Commit: `feat(dev-kit): encode action_gateway FIELD_RULES from catalogue §7.4`.

### Task 3.6: `reach_layer` FIELD_RULES

**Reference:** catalogue §7.3 + §3.3 + §4.8 (selected_channels gating).

EXPECTED_PATHS (catalogue §7.3, web + voice + common):
```
reach_layer.common.observability.domain                  # derived
# Web (catalogue §3.3 — gated by "web" in selected_channels)
reach_layer.channels.web.ui.app_name
reach_layer.channels.web.ui.app_tagline
reach_layer.channels.web.ui.app_icon
reach_layer.channels.web.ui.agent_avatar
reach_layer.channels.web.ui.user_avatar
reach_layer.channels.web.ui.setup_heading
reach_layer.channels.web.ui.setup_subtitle
reach_layer.channels.web.ui.user_id_placeholder
reach_layer.channels.web.ui.user_id_hint
reach_layer.channels.web.ui.start_btn_label
reach_layer.channels.web.ui.new_session_msg
reach_layer.channels.web.ui.returning_user_msg
reach_layer.channels.web.ui.sign_out_confirm
reach_layer.channels.web.ui.switch_user_confirm
reach_layer.channels.web.ui.delete_conversation_confirm
reach_layer.channels.web.ui.storage_key                  # derived
reach_layer.channels.web.ui.theme_storage_key            # derived
reach_layer.channels.web.ke_internal_url                 # chat, gated by has_kb
reach_layer.channels.web.auth.enabled                    # deploy
# Voice (catalogue §3.3 — gated by "voice" in selected_channels)
reach_layer.channels.voice.raya.stt_language             # predetermined
reach_layer.channels.voice.raya.tts_language             # predetermined
reach_layer.channels.voice.raya.voice_id                 # chat, deploy_overridable
reach_layer.channels.voice.agent_core.fallback_phrase
reach_layer.channels.voice.agent_core.barge_in_acknowledgement
reach_layer.channels.voice.agent_core.timeout_ms
reach_layer.channels.voice.filler_threshold_ms
reach_layer.channels.voice.filler_phrase
reach_layer.channels.voice.terminal_word
reach_layer.channels.voice.recording.consent_purpose     # chat, applies_if voice.recording.source!=disabled
reach_layer.channels.voice.raya.api_key                  # deploy
reach_layer.channels.voice.public_url                    # deploy
reach_layer.channels.voice.vobiz                         # deploy (advanced)
reach_layer.channels.voice.vad                           # deploy (advanced)
reach_layer.channels.voice.recording                     # deploy (advanced)
```

Apply locked decision #6: `voice.recording.consent_purpose` is a standalone chat field.

Apply the `REACH_LAYER_WEB_MODE` compose-level note — this is **not** a FIELD_RULES entry (no YAML field) but should be a comment in the file or accompanied by a test asserting it's set by the compose generator (Phase 7 task).

Commit: `feat(dev-kit): encode reach_layer FIELD_RULES from catalogue §7.3`.

### Task 3.7: `observability_layer` FIELD_RULES

**Reference:** catalogue §7.7. Apply same task shape.

EXPECTED_PATHS (catalogue §7.7):
```
observability.outcomes.lifecycle                         # chat (always; mirror min_length=1)
observability.outcomes.metrics                           # chat (always; optional)
observability.domain                                     # derived
```

Skeleton must seed `lifecycle: [{state: "started", trigger_tool: null}]` so the mirror's min_length=1 is satisfied. Reflect this in the `default` field of the FieldRule.

Commit: `feat(dev-kit): encode observability_layer FIELD_RULES from catalogue §7.7`.

### Task 3.8: Wire all per-block modules into the aggregate registry

**Files:**
- Modify: `dev-kit/dev_kit/agent/field_rules/__init__.py`
- Create: `dev-kit/tests/agent/test_field_rules_aggregate.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_field_rules_aggregate.py`:

```python
"""Tests for AGGREGATED_FIELD_RULES — union of all per-block FIELD_RULES."""
from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES


def test_aggregate_contains_all_blocks():
    blocks = {p.split(".", 1)[0] for p in AGGREGATED_FIELD_RULES.keys()}
    expected = {
        "agent_core", "trust_layer", "knowledge_engine",
        "action_gateway", "memory_layer", "observability_layer", "reach_layer",
    }
    assert blocks == expected


def test_no_duplicate_paths():
    paths = list(AGGREGATED_FIELD_RULES.keys())
    assert len(paths) == len(set(paths))


def test_predetermined_rules_reference_intake_fields_only():
    """Every predetermined rule should reference only IntakeState attribute names."""
    from dev_kit.agent.intake_state import IntakeState
    intake_fields = {f for f in IntakeState.__dataclass_fields__}
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "predetermined" or not rule.rule:
            continue
        # Permissive check: rule is a Python expression. We extract identifiers
        # and check that any whose first char is alpha and which is not a Python
        # keyword/builtin is in intake_fields. Full AST parsing is overkill for
        # this guard; the test catches typos like `has_db` (typo of `has_kb`).
        import re
        idents = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", rule.rule))
        suspect = idents - intake_fields - {"set", "if", "else", "and", "or",
                                             "not", "in", "True", "False", "None"}
        assert not suspect, f"{path}: unknown identifiers {suspect}"
```

- [ ] **Step 2: Implement: ensure the `field_rules/__init__.py` imports every block module on package load** so `AGGREGATED_FIELD_RULES` is populated.

Append to `dev-kit/dev_kit/agent/field_rules/__init__.py`:

```python
# Eagerly import every block module so register_block_rules() runs and
# AGGREGATED_FIELD_RULES is fully populated.
from dev_kit.agent.field_rules import (  # noqa: E402, F401
    agent_core,
    trust_layer,
    knowledge_engine,
    memory_layer,
    action_gateway,
    reach_layer,
    observability_layer,
)
```

- [ ] **Step 3: Run all FIELD_RULES tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_rules_*.py -v
```

Expected: every per-block test + aggregate test passes.

- [ ] **Step 4: Commit.**

```bash
git add dev-kit/dev_kit/agent/field_rules/__init__.py dev-kit/tests/agent/test_field_rules_aggregate.py
git commit -m "feat(dev-kit): wire per-block FIELD_RULES modules into aggregate registry"
```

---

## Phase 4: Skeleton + field status tracking

### Task 4.1: `build_skeleton` function

**Files:**
- Create: `dev-kit/dev_kit/agent/skeleton.py`
- Create: `dev-kit/tests/agent/test_skeleton.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_skeleton.py`:

```python
"""Tests for build_skeleton: walks FIELD_RULES, produces domain accumulator + field_status."""
import pytest

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.skeleton import build_skeleton


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="A pilot project", project_name="kkb",
    )
    base.update(overrides)
    return IntakeState(**base)


def test_skeleton_kb_off_omits_kb_connector():
    state = _intake(has_kb=False)
    accumulator, _ = build_skeleton(state)
    internal = accumulator["agent_core"].get("connectors", {}).get("internal", [])
    assert all(c.get("name") != "knowledge_retrieval" for c in internal)


def test_skeleton_kb_on_seeds_knowledge_retrieval():
    state = _intake(has_kb=True)
    accumulator, _ = build_skeleton(state)
    internal = accumulator["agent_core"]["connectors"]["internal"]
    kr = next((c for c in internal if c.get("name") == "knowledge_retrieval"), None)
    assert kr is not None
    assert kr["route"] == "knowledge_engine"


def test_skeleton_companion_sets_dignity_questions():
    state = _intake(is_companion_style=True)
    accumulator, _ = build_skeleton(state)
    questions = accumulator["trust_layer"].get("dignity_check", {}).get("questions", [])
    assert len(questions) == 5


def test_skeleton_companion_off_omits_dignity_questions():
    """When equal to dpg default (empty list), skeleton should suppress write."""
    state = _intake(is_companion_style=False)
    accumulator, _ = build_skeleton(state)
    # dignity_check.questions should NOT be written when value equals the dpg default ([])
    questions = accumulator["trust_layer"].get("dignity_check", {}).get("questions")
    assert questions is None


def test_skeleton_field_status_marks_chat_pending():
    state = _intake()
    _, field_status = build_skeleton(state)
    # `agent_core.preprocessing.nlu_processor.intents` is always-asked chat → pending
    assert field_status["agent_core.preprocessing.nlu_processor.intents"] == "pending"


def test_skeleton_field_status_marks_inapplicable_when_gated_off():
    state = _intake(has_kb=False)
    _, field_status = build_skeleton(state)
    # KE chat fields are not_applicable when has_kb=false
    kf = "knowledge_engine.knowledge.blocks.static_knowledge_base.default_doc_type"
    assert field_status[kf] == "not_applicable"
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
cd dev-kit && uv run pytest tests/agent/test_skeleton.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `skeleton.py`.**

`dev-kit/dev_kit/agent/skeleton.py`:

```python
"""build_skeleton — pure function producing a domain-only accumulator + field_status.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8
("build_skeleton()") and the field rules catalogue.
"""
from __future__ import annotations

from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.path_ops import set_path

# The 7 runtime blocks. Each gets a (possibly empty) accumulator dict.
BLOCKS = (
    "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
    "action_gateway", "reach_layer", "observability_layer",
)


def _eval_expr(expr: str | None, state: IntakeState) -> Any:
    """Evaluate an applies_if/invalidated_by expression against IntakeState.

    Expressions are Python expressions referencing IntakeState attributes by name
    (e.g., `has_kb`, `is_multi_turn and is_companion_style`, `"voice" in selected_channels`).
    For safety, we evaluate in a restricted namespace containing only the
    intake fields plus Python builtins for boolean operators.
    """
    if expr is None:
        return True
    namespace = {f: getattr(state, f) for f in IntakeState.__dataclass_fields__}
    # No builtins — boolean operators don't need them.
    return eval(expr, {"__builtins__": {}}, namespace)


def _eval_rule(rule_str: str, state: IntakeState) -> Any:
    """Evaluate a `predetermined` rule's `rule` expression.

    Rule format: `set: <python_expression>`. The expression is evaluated in the
    same namespace as applies_if/invalidated_by.
    """
    if not rule_str.startswith("set:"):
        raise ValueError(f"predetermined rule must start with 'set:': {rule_str!r}")
    expr = rule_str.removeprefix("set:").strip()
    namespace = {f: getattr(state, f) for f in IntakeState.__dataclass_fields__}
    return eval(expr, {"__builtins__": {}}, namespace)


def _get_framework_default(path: str) -> Any:
    """Return the framework default for a path (from dpg.yaml or Pydantic).

    For Phase 4 we use a minimal stub that knows the canonical dpg defaults
    for predetermined fields whose dpg value is well-known (e.g.,
    `dignity_check.enabled: false`, `dignity_check.questions: []`).
    The full lookup against parsed dpg.yaml is Phase 5 work.
    """
    KNOWN_DPG_DEFAULTS: dict[str, Any] = {
        "trust_layer.dignity_check.enabled": False,
        "trust_layer.dignity_check.questions": [],
        "agent_core.agent.ask_for_consent": False,
        "agent_core.conversation.user_state_model.enabled": False,
        "agent_core.conversation.session_end_eval.enabled": False,
        "knowledge_engine.knowledge.blocks.static_knowledge_base.enabled": False,
        "memory_layer.user_data_persistence.default_mode": "saved",
    }
    return KNOWN_DPG_DEFAULTS.get(path)


def build_skeleton(
    intake_state: IntakeState,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Walk FIELD_RULES → (accumulator, field_status).

    Args:
        intake_state: The complete IntakeState (all 12 fields).

    Returns:
        accumulator: `{block_name: domain_yaml_dict, ...}` for every block.
            Predetermined rules whose value equals the framework default are
            NOT written (no-redundancy principle from design §3).
        field_status: `{full_path: status, ...}` for every chat field.
            Statuses: "pending", "not_applicable" (when applies_if=false).
    """
    accumulator: dict[str, dict] = {block: {} for block in BLOCKS}
    field_status: dict[str, str] = {}

    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        block, relative_path = full_path.split(".", 1)
        applies = _eval_expr(rule.applies_if, intake_state)

        if rule.category == "chat":
            if not applies:
                field_status[full_path] = "not_applicable"
                continue
            field_status[full_path] = "pending"
            if rule.default is not None:
                set_path(accumulator[block], relative_path, rule.default)

        elif rule.category == "predetermined":
            if not applies:
                continue
            if not rule.rule:
                continue
            value = _eval_rule(rule.rule, intake_state)
            framework_default = _get_framework_default(full_path)
            if value != framework_default:
                set_path(accumulator[block], relative_path, value)

        elif rule.category in ("deploy", "derived", "framework_default_only"):
            # deploy: nothing in domain YAML; deploy overlay applies at render time.
            # derived: renderer computes at write time (Phase 5).
            # framework_default_only: lives in dpg.yaml.
            continue

    return accumulator, field_status


__all__ = ["build_skeleton", "BLOCKS"]
```

- [ ] **Step 4: Run the tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_skeleton.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/skeleton.py dev-kit/tests/agent/test_skeleton.py
git commit -m "feat(dev-kit): add build_skeleton walking FIELD_RULES → accumulator + field_status"
```

### Task 4.2: `field_status.py` — read/write helpers

**Files:**
- Create: `dev-kit/dev_kit/agent/field_status.py`
- Create: `dev-kit/tests/agent/test_field_status.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_field_status.py`:

```python
"""Tests for field_status.json read/write."""
import json
from pathlib import Path

import pytest

from dev_kit.agent.field_status import (
    FIELD_STATUS_VALUES,
    load_field_status,
    save_field_status,
)


def test_status_set_complete():
    assert FIELD_STATUS_VALUES == {"pending", "answered", "needs_re_asking", "not_applicable"}


def test_save_load_roundtrip(tmp_path: Path):
    status = {"agent_core.foo": "pending", "trust_layer.bar": "answered"}
    p = tmp_path / "field_status.json"
    save_field_status(p, status)
    loaded = load_field_status(p)
    assert loaded == status


def test_load_missing_returns_empty(tmp_path: Path):
    loaded = load_field_status(tmp_path / "missing.json")
    assert loaded == {}


def test_save_validates_status_values(tmp_path: Path):
    p = tmp_path / "field_status.json"
    with pytest.raises(ValueError):
        save_field_status(p, {"foo.bar": "wrong_status"})
```

- [ ] **Step 2: Run to verify failure.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_status.py -v
```

- [ ] **Step 3: Implement.**

`dev-kit/dev_kit/agent/field_status.py`:

```python
"""field_status.json — per-chat-field tracking of pending/answered/needs_re_asking/not_applicable.

Persisted to `<slug>/_meta/field_status.json`. Read and updated by the phase
driver, the on_intake_update handler, and the end-of-turn router.
"""
from __future__ import annotations

import json
from pathlib import Path

FIELD_STATUS_VALUES = {"pending", "answered", "needs_re_asking", "not_applicable"}


def save_field_status(path: Path, status: dict[str, str]) -> None:
    """Persist field statuses to disk after validating every value."""
    for k, v in status.items():
        if v not in FIELD_STATUS_VALUES:
            raise ValueError(
                f"Invalid field status {v!r} for {k!r}; "
                f"allowed: {sorted(FIELD_STATUS_VALUES)}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True))


def load_field_status(path: Path) -> dict[str, str]:
    """Return field statuses from disk; empty dict if file missing."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


__all__ = ["FIELD_STATUS_VALUES", "save_field_status", "load_field_status"]
```

- [ ] **Step 4: Run tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_field_status.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/field_status.py dev-kit/tests/agent/test_field_status.py
git commit -m "feat(dev-kit): add field_status.json read/write helpers"
```

---

## Phase 5: Router + intake/config mutation handlers

The router decides phase transitions and runs the cascade when intake state changes.

### Task 5.1: `on_intake_update` handler

**Files:**
- Create: `dev-kit/dev_kit/agent/router.py`
- Create: `dev-kit/tests/agent/test_router_on_intake_update.py`

- [ ] **Step 1: Write the failing test.**

`dev-kit/tests/agent/test_router_on_intake_update.py`:

```python
"""Tests for router.on_intake_update — the FIELD_RULES cascade."""
from dataclasses import replace

from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import on_intake_update


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="", project_name="proj",
    )
    base.update(overrides)
    return IntakeState(**base)


def test_flip_has_kb_marks_nlu_intents_for_re_ask():
    state = _intake(has_kb=False)
    accumulator = {"agent_core": {"preprocessing": {"nlu_processor": {"intents": ["unknown"]}}},
                   "knowledge_engine": {}, "trust_layer": {}, "memory_layer": {},
                   "action_gateway": {}, "reach_layer": {}, "observability_layer": {}}
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "answered"}

    result = on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert state.has_kb is True
    assert field_status["agent_core.preprocessing.nlu_processor.intents"] == "needs_re_asking"
    assert result["affected_count"] >= 1
    assert result["earliest_affected_phase"] in ("language", "knowledge")


def test_flip_companion_style_recomputes_dignity_enabled():
    state = _intake(is_companion_style=False)
    accumulator = {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}
    field_status: dict[str, str] = {}

    on_intake_update(
        field="is_companion_style", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    # dignity_check.enabled is predetermined `set: is_companion_style`
    assert accumulator["trust_layer"]["dignity_check"]["enabled"] is True
    assert len(accumulator["trust_layer"]["dignity_check"]["questions"]) == 5


def test_noop_when_value_unchanged():
    state = _intake(has_kb=True)
    accumulator: dict = {b: {} for b in (
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    )}
    field_status: dict[str, str] = {}

    result = on_intake_update(
        field="has_kb", new_value=True,
        state=state, accumulator=accumulator, field_status=field_status,
    )

    assert result["noop"] is True
```

- [ ] **Step 2: Run to verify failure.**

```bash
cd dev-kit && uv run pytest tests/agent/test_router_on_intake_update.py -v
```

- [ ] **Step 3: Implement `router.py` (partial — just `on_intake_update`).**

`dev-kit/dev_kit/agent/router.py`:

```python
"""Router — handles intake updates and decides phase transitions.

See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §7.
"""
from __future__ import annotations

from typing import Any

from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES, FieldRule
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.path_ops import clear_path, set_path
from dev_kit.agent.skeleton import _eval_expr, _eval_rule, _get_framework_default

PHASE_ORDER = (
    "tier", "language", "knowledge", "memory", "user_state",
    "trust", "tools", "workflow", "observability", "reach", "review",
)


def _earlier_phase(a: str | None, b: str | None) -> str | None:
    """Return the earlier of two phase names (by PHASE_ORDER)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if PHASE_ORDER.index(a) <= PHASE_ORDER.index(b) else b


def on_intake_update(
    field: str,
    new_value: Any,
    state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Apply an intake field change and cascade through FIELD_RULES.

    Args:
        field: Name of the IntakeState field being changed.
        new_value: The new value.
        state: IntakeState (mutated in-place).
        accumulator: Per-block YAML dicts (mutated in-place).
        field_status: Field status registry (mutated in-place).

    Returns:
        Dict with `ok`, `field`, `old_value`, `new_value`, `affected_count`,
        `earliest_affected_phase`. If old_value == new_value, returns
        `{"ok": True, "noop": True}`.
    """
    old_value = getattr(state, field)
    if old_value == new_value:
        return {"ok": True, "noop": True}

    setattr(state, field, new_value)
    state.touch()

    affected: list[tuple[str, FieldRule]] = [
        (full_path, rule)
        for full_path, rule in AGGREGATED_FIELD_RULES.items()
        if field in rule.invalidated_by
    ]

    earliest_phase: str | None = None
    for full_path, rule in affected:
        block, relative_path = full_path.split(".", 1)
        applies = _eval_expr(rule.applies_if, state)

        if rule.category == "predetermined":
            if applies and rule.rule:
                value = _eval_rule(rule.rule, state)
                fw_default = _get_framework_default(full_path)
                if value != fw_default:
                    set_path(accumulator[block], relative_path, value)
                else:
                    clear_path(accumulator[block], relative_path)
            else:
                clear_path(accumulator[block], relative_path)

        elif rule.category == "chat":
            if not applies:
                clear_path(accumulator[block], relative_path)
                field_status[full_path] = "not_applicable"
            else:
                # If the field was not_applicable and we have a default,
                # seed the default and mark it as needs_re_asking.
                if (rule.default is not None
                        and field_status.get(full_path) == "not_applicable"):
                    set_path(accumulator[block], relative_path, rule.default)
                field_status[full_path] = "needs_re_asking"
                earliest_phase = _earlier_phase(earliest_phase, rule.phase)

        elif rule.category == "derived":
            # Flag for renderer recompute; we don't track stale-derived status today.
            pass

    return {
        "ok": True,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "affected_count": len(affected),
        "earliest_affected_phase": earliest_phase,
    }


__all__ = ["on_intake_update", "PHASE_ORDER"]
```

- [ ] **Step 4: Run tests.**

```bash
cd dev-kit && uv run pytest tests/agent/test_router_on_intake_update.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add dev-kit/dev_kit/agent/router.py dev-kit/tests/agent/test_router_on_intake_update.py
git commit -m "feat(dev-kit): add on_intake_update cascade through FIELD_RULES"
```

### Task 5.2: `decide_next_phase` end-of-turn router

**Files:**
- Modify: `dev-kit/dev_kit/agent/router.py` (add `decide_next_phase`)
- Create: `dev-kit/tests/agent/test_router_decide_next_phase.py`

- [ ] **Step 1: Write the test.**

`dev-kit/tests/agent/test_router_decide_next_phase.py`:

```python
"""Tests for router.decide_next_phase."""
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.router import decide_next_phase


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False,
        is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="english", supported_languages=["english"],
        domain_description="", project_name="p",
    )
    base.update(overrides)
    return IntakeState(**base)


def test_stays_when_current_incomplete():
    state = _intake()
    field_status = {"agent_core.preprocessing.nlu_processor.intents": "pending"}
    nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)
    assert nxt == "language"


def test_advances_when_current_complete():
    state = _intake()
    # All language-phase chat fields are answered.
    # The router walks PHASES from "language" forward.
    field_status = {}  # empty = no pending fields anywhere
    nxt = decide_next_phase("language", state, accumulator={}, field_status=field_status)
    # Should advance to the next relevant phase ("memory" — knowledge is skipped because has_kb=false)
    assert nxt == "memory"


def test_backtracks_when_earlier_phase_invalidated():
    state = _intake()
    field_status = {
        "agent_core.preprocessing.nlu_processor.intents": "needs_re_asking",
    }
    nxt = decide_next_phase("workflow", state, accumulator={}, field_status=field_status)
    assert nxt == "language"


def test_skips_irrelevant_phase():
    """user_state phase is_relevant only when is_companion_style=true."""
    state = _intake(is_companion_style=False)
    nxt = decide_next_phase("memory", state, accumulator={}, field_status={})
    # user_state should be skipped → next relevant is trust
    assert nxt == "trust"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Append to `router.py`.**

```python
from dev_kit.agent.field_rules import FIELD_RULES_PHASES_VALID

# Per-phase relevance predicates (mirrors PHASES config).
PHASE_RELEVANCE: dict[str, callable] = {
    "tier": lambda s: True,
    "language": lambda s: True,
    "knowledge": lambda s: s.has_kb,
    "memory": lambda s: s.is_multi_turn or s.needs_persistent_user_data,
    "user_state": lambda s: s.is_companion_style,
    "trust": lambda s: True,
    "tools": lambda s: s.has_external_tools,
    "workflow": lambda s: True,
    "observability": lambda s: True,
    "reach": lambda s: True,
    "review": lambda s: True,
}


def _phase_for_path(path: str) -> str | None:
    """Look up the phase name for a path via AGGREGATED_FIELD_RULES."""
    rule = AGGREGATED_FIELD_RULES.get(path)
    return rule.phase if rule else None


def _earliest_phase_with_needs_re_asking(field_status: dict[str, str]) -> str | None:
    earliest: str | None = None
    for path, status in field_status.items():
        if status != "needs_re_asking":
            continue
        phase = _phase_for_path(path)
        if phase is None:
            continue
        earliest = _earlier_phase(earliest, phase)
    return earliest


def _is_phase_complete(
    phase: str,
    state: IntakeState,
    field_status: dict[str, str],
) -> bool:
    """A phase is complete when every relevant chat field in it has status 'answered'."""
    for full_path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "chat" or rule.phase != phase:
            continue
        if not _eval_expr(rule.applies_if, state):
            continue
        status = field_status.get(full_path, "pending")
        if status != "answered":
            return False
    return True


def _next_relevant_phase(current: str, state: IntakeState) -> str | None:
    """Walk PHASE_ORDER forward from `current`, returning the first relevant phase."""
    idx = PHASE_ORDER.index(current)
    for nxt in PHASE_ORDER[idx + 1:]:
        if PHASE_RELEVANCE[nxt](state):
            return nxt
    return None


def decide_next_phase(
    current_phase: str,
    state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> str:
    """Decide which phase the wizard should be in for the next turn."""
    invalidated = _earliest_phase_with_needs_re_asking(field_status)
    if invalidated and PHASE_ORDER.index(invalidated) < PHASE_ORDER.index(current_phase):
        return invalidated

    if _is_phase_complete(current_phase, state, field_status):
        nxt = _next_relevant_phase(current_phase, state)
        return nxt if nxt else current_phase

    return current_phase
```

- [ ] **Step 4: Run tests.**

- [ ] **Step 5: Commit.**

```bash
git commit -am "feat(dev-kit): add decide_next_phase end-of-turn router"
```

### Task 5.3: `on_config_update` handler

**Files:**
- Modify: `dev-kit/dev_kit/agent/router.py`
- Create: `dev-kit/tests/agent/test_router_on_config_update.py`

Implementation: applies user's chat answer to a path, validates against the mirror, marks `field_status` answered, persists. Follows the same TDD shape as 5.1/5.2 with tests asserting valid writes are accepted and Pydantic-invalid writes raise.

Commit: `feat(dev-kit): add on_config_update handler with mirror validation`.

---

## Phase 6: Phase prompts + phase driver

### Task 6.1: `phases_config.py` — declarative PHASES dict

**Files:**
- Create: `dev-kit/dev_kit/agent/phases_config.py`
- Create: `dev-kit/tests/agent/test_phases_config.py`

- [ ] **Step 1: Write the test asserting PHASES has 11 entries with correct order, `is_relevant`, and `next_default`.**

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

```python
"""PHASES — declarative phase definitions. See design §6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from dev_kit.agent.intake_state import IntakeState


@dataclass(frozen=True)
class PhaseDefinition:
    id: str
    label: str
    prompt_module: str  # dotted name under dev_kit.agent.phase_prompts
    next_default: Optional[str]
    is_relevant: Optional[Callable[[IntakeState], bool]] = None


def _always(_: IntakeState) -> bool: return True

PHASES: dict[str, PhaseDefinition] = {
    "tier":          PhaseDefinition("tier", "Intake", "tier", "language", _always),
    "language":      PhaseDefinition("language", "Language & NLU", "language", "knowledge", _always),
    "knowledge":     PhaseDefinition("knowledge", "Knowledge base", "knowledge", "memory",
                                      lambda s: s.has_kb),
    "memory":        PhaseDefinition("memory", "Memory & sessions", "memory", "user_state",
                                      lambda s: s.is_multi_turn or s.needs_persistent_user_data),
    "user_state":    PhaseDefinition("user_state", "User state", "user_state", "trust",
                                      lambda s: s.is_companion_style),
    "trust":         PhaseDefinition("trust", "Trust & safety", "trust", "tools", _always),
    "tools":         PhaseDefinition("tools", "External tools", "tools", "workflow",
                                      lambda s: s.has_external_tools),
    "workflow":      PhaseDefinition("workflow", "Workflow", "workflow", "observability", _always),
    "observability": PhaseDefinition("observability", "Observability", "observability", "reach", _always),
    "reach":         PhaseDefinition("reach", "Channels", "reach", "review", _always),
    "review":        PhaseDefinition("review", "Review", "review", None, _always),
}
```

- [ ] **Step 4: Run tests.**

- [ ] **Step 5: Commit.** `feat(dev-kit): add declarative PHASES config`.

### Task 6.2: Per-phase prompt modules (× 11)

For each phase (tier, language, knowledge, memory, user_state, trust, tools, workflow, observability, reach, review):

1. Create `dev-kit/dev_kit/agent/phase_prompts/<phase>.py`.
2. Each exports a `build(pending_fields, pydantic_schemas, cross_phase_refs, intake_state) -> str`.
3. The prompt text follows the structure in design §6 (intro + fields + Pydantic schema injection + cross-phase refs + closing instruction).
4. **Source content** for each phase comes from today's `prompts/phases.py` (read the relevant section) plus the catalogue's per-phase field list.
5. Each prompt is 50-100 lines.

Per phase: write test that asserts `build(...)` returns a non-empty string and contains expected sections (e.g., `"## Fields to capture this phase"`, the field names listed); implement; commit.

Commits: `feat(dev-kit): add <phase> phase prompt builder` × 11.

**Tier phase note:** This is the new 4-turn intake chat (design §4 "Chat intake — 4 yes/no turns"). The prompt orchestrates Turn 1 → 4 logic. The 5 form-captured fields (`project_name`, `domain_description`, etc.) are NOT asked here — they're set by the form server-side via `update_intake` before the tier phase begins.

### Task 6.3: `phase_driver.py` — single shared phase runner

**Files:**
- Create: `dev-kit/dev_kit/agent/phase_driver.py`
- Create: `dev-kit/tests/agent/test_phase_driver.py`

Implements `run_turn(user_message, project_slug)` per design §6:

1. Load intake_state, accumulator, field_status, current_phase from disk.
2. Filter pending/needs_re_asking fields for current phase.
3. Resolve Pydantic class closure for those fields.
4. Build prompt via the phase's `build()` function.
5. Call LLM.
6. Process tool calls (route to `on_intake_update`, `on_config_update`, `add_subagent`, etc.).
7. Call `router.decide_next_phase` and persist new state.

Test with a mocked LLM returning a known tool call sequence; assert state transitions correctly.

Commit: `feat(dev-kit): add phase_driver.run_turn integrating LLM + tools + router`.

---

## Phase 7: Tool surface (trim to 8)

### Task 7.1: Rewrite `tools.py` with the 8-tool set

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py` (full rewrite — back up first)
- Create: `dev-kit/tests/agent/test_tools.py`

The 8 tools (design §6 "Slimmed tool surface"):

| Tool | Purpose |
|---|---|
| `update_intake(field, value)` | Mutates IntakeState; runs `on_intake_update` cascade. |
| `update_config(block, section, values)` | Mutates accumulator; Pydantic-validated; runs `on_config_update`. |
| `add_subagent(definition)` | Adds a subagent to `agent_workflow.subagents`. |
| `update_subagent(id, fields)` | Modifies a subagent in-place. |
| `add_routing_rule(from_subagent_id, intent, to_subagent_id, condition?)` | Adds a rule to a subagent's routing. |
| `add_tool(spec)` | Adds an action_gateway tool + matching agent_core connector. |
| `parse_openapi_spec(spec)` | Utility — parses uploaded OpenAPI JSON. |
| `discover_mcp_tools(server_url)` | Utility — lists MCP server tools. |

Removed (per design §6): `set_phase`, `skip_optional_phase`, `set_agent_type`, `set_project_meta`, `set_reach_channels`, `set_response_transformation`, `declare_azure_storage`, `rollback_to_checkpoint`, `finalize_config`, `set_agent_core_connector`, `update_routing_rule`, `remove_subagent`, plus internal helpers.

- [ ] **Step 1: Back up current tools.py** to `dev-kit/dev_kit/agent/tools.py.bak` (don't commit the backup; just keep for reference during rewrite).

- [ ] **Step 2: Write tests for the new 8-tool set** — one test per tool asserting signature + side effects.

- [ ] **Step 3: Rewrite `tools.py` from scratch** with the 8 tools, each ~30-40 lines.

- [ ] **Step 4: Run tests, ensure all pass.**

- [ ] **Step 5: Delete the .bak file. Commit.**

```bash
rm dev-kit/dev_kit/agent/tools.py.bak
git add dev-kit/dev_kit/agent/tools.py dev-kit/tests/agent/test_tools.py
git commit -m "refactor(dev-kit): trim tool surface from 20 to 8 per design §6"
```

---

## Phase 8: Selective deployment + REACH_LAYER_WEB_MODE

### Task 8.1: Compose generator — selective service inclusion

**Files:**
- Modify: `dev-kit/dev_kit/agent/deployer/compose.py`
- Create: `dev-kit/tests/agent/test_compose_generator.py`

- [ ] **Step 1: Write tests asserting:**
  - `has_kb=false` → no `knowledge_engine` service in generated compose.
  - `has_external_tools=false` → no `action_gateway` service.
  - `"voice" not in selected_channels` → no `reach_layer_voice` + `ngrok`.
  - `"web" not in selected_channels` → `REACH_LAYER_WEB_MODE=routing_only`.
  - `"web" in selected_channels` → `REACH_LAYER_WEB_MODE=full`.

- [ ] **Step 2: Run tests; implement; iterate.**

- [ ] **Step 3: Verify `depends_on` references to omitted services are stripped.**

- [ ] **Step 4: Commit.** `feat(dev-kit): selective compose generation based on IntakeState`.

---

## Phase 9: Renderer derived-field pass

### Task 9.1: Renderer computes derived fields at write time

**Files:**
- Modify: `dev-kit/dev_kit/agent/renderer.py` (already touched in Task 2.3)
- Create: `dev-kit/tests/agent/test_renderer_derived_fields.py`

Tests assert that:
- `observability.domain` is set to `slug(project_name)` for every block.
- `agent_workflow.workflow_id` is set to `f"{slug}_workflow"`.
- `reach_layer.channels.web.ui.{storage_key, theme_storage_key}` are slug-derived.

Implementation: add a pass before YAML write that walks `AGGREGATED_FIELD_RULES` for `category == "derived"` and runs `compute` expressions.

Commit: `feat(dev-kit): renderer computes derived fields at write time`.

---

## Phase 10: Decision logging

Add structured logs at the 13 decision points listed in design §8 ("Decision logging").

### Task 10.1: Add logs to `on_intake_update`

- [ ] **Step 1: Add `logger.info` at:** start of handler, after each cascade step, at return.
- [ ] **Step 2: Required fields:** `operation, status, field, old_value, new_value, affected_count, earliest_affected_phase` (per design §8 table).
- [ ] **Step 3: Test:** capture logs (pytest's `caplog`) and assert structure.
- [ ] **Step 4: Commit.**

### Task 10.2: Add logs to `on_config_update`

Same pattern: `operation, status, block, section, paths_written, validation_errors`.

### Task 10.3: Add logs to phase transitions, skips, skeleton, dry-run, LLM calls

One commit per decision point. Each adds the required fields per design §8 table.

After all 13 decision points are logged:

Commit: `feat(dev-kit): add structured decision logging at all state transitions`.

---

## Phase 11: UI changes (required only)

The full UI revamp is out of scope. These are the minimal changes to support the new wizard.

### Task 11.1: Project creation form — 5 intake fields

**Files:**
- Modify: `dev-kit/frontend/src/components/ProjectCreationForm.jsx` (or equivalent)
- Modify: `dev-kit/dev_kit/agent/app.py` (the project-creation endpoint)
- Create: `dev-kit/tests/agent/test_project_creation_endpoint.py`

The form must capture (catalogue §4 intake-state fields, design §4 "What's captured before chat"):

1. `project_name` — text input
2. `domain_description` — textarea (1-2 sentences)
3. `selected_channels` — multi-select checkboxes (`web`, `voice`)
4. `default_language` — dropdown
5. `supported_languages` — multi-select picker

On submit, the form posts to the existing creation endpoint, which:
1. Creates the `<slug>/_meta/` directory.
2. Initialises `IntakeState` with all 12 fields (the 5 form fields + 7 binary flags as `False`).
3. Calls `save_intake_state(...)`.
4. Sets `current_phase = "tier"` (the chat intake phase).

- [ ] **Step 1: Backend test.**

```python
def test_project_creation_endpoint_writes_intake_state(client, tmp_path):
    payload = {
        "project_name": "Test Bot",
        "domain_description": "Helps users do X.",
        "selected_channels": ["web"],
        "default_language": "english",
        "supported_languages": ["english", "hindi"],
    }
    resp = client.post("/api/projects", json=payload)
    assert resp.status_code == 201
    slug = resp.json()["slug"]
    state = load_intake_state(tmp_path / slug / "_meta" / "intake_state.json")
    assert state.project_name == "Test Bot"
    assert state.selected_channels == ["web"]
    assert state.completed is False  # 7 binary flags not yet captured
```

- [ ] **Step 2: Implement backend endpoint changes.**

- [ ] **Step 3: Update the frontend form to include all 5 fields. Use existing form patterns (don't redesign — the full UI revamp comes later).**

- [ ] **Step 4: E2E test the form (manual in browser; document in commit).**

- [ ] **Step 5: Commit.** `feat(dev-kit): project creation form captures 5 intake fields`.

### Task 11.2: Deploy form — surface `deploy_overridable` fields

**Files:**
- Modify: `dev-kit/frontend/src/components/DeploymentForm.jsx` (or equivalent)
- Modify: `dev-kit/dev_kit/agent/app.py` (the deploy endpoint)

The deploy form pre-fills `agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id` from the domain YAML and lets the operator change them per-deploy.

- [ ] **Step 1: Backend endpoint reads the FIELD_RULES and lists every `deploy_overridable=true` entry plus every `category="deploy"` entry to render in the form.**

- [ ] **Step 2: Backend writes overrides into `_meta/deploy_settings.json` and renderer's `apply_deploy_overlay` reads from there.**

- [ ] **Step 3: Frontend renders the form. Group by category: required (deploy), advanced (deploy_overridable).**

- [ ] **Step 4: Commit.** `feat(dev-kit): deploy form surfaces deploy_overridable fields with pre-fill`.

### Task 11.3: Chat UI — minimal field-status visibility

The full UI revamp is out of scope. The minimal change here: when the phase driver lists fields in a phase prompt, the user-facing UI shows which fields are pending and which are answered. (Today's UI already shows progress; the change is to read from `field_status.json` instead of from the old phase-completion enum.)

Commit: `feat(dev-kit): chat UI reads field_status.json for phase progress`.

---

## Phase 12: Integration + cleanup

### Task 12.1: Wire `conversation.py` to call `phase_driver.run_turn`

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py`

The existing `conversation.py` handler must call `phase_driver.run_turn(user_message, project_slug)` instead of today's LLM-orchestrated flow.

Commit: `refactor(dev-kit): conversation.py delegates to phase_driver`.

### Task 12.2: Delete old phase prompts and orchestration

**Files:**
- Delete: `dev-kit/dev_kit/agent/prompts/phases.py`
- Delete: `dev-kit/dev_kit/agent/prompts/base.py`
- Delete: any tests under `tests/agent/test_phases_*.py` that exercise the old API.

Commit: `chore(dev-kit): remove legacy phase prompts and orchestration`.

### Task 12.3: End-to-end smoke test

**Files:**
- Create: `dev-kit/tests/agent/test_wizard_flow.py`

Simulate a full conversation through the wizard for 3 canonical intake combinations:

1. **Single-shot KB-only:** `has_kb=true`, `has_external_tools=false`, `is_multi_turn=false`, `needs_persistent_user_data=false`, `is_companion_style=false`, `needs_consent=false`, `has_hitl=false`, `selected_channels=["web"]`.

2. **Multi-turn API-calling:** `has_external_tools=true`, `is_multi_turn=true`, others false, `selected_channels=["web"]`.

3. **Conversational companion with voice:** `is_multi_turn=true`, `needs_persistent_user_data=true`, `is_companion_style=true`, `selected_channels=["voice"]`, `has_kb=true`, `needs_consent=true`, `has_hitl=true`.

For each, drive the wizard through all relevant phases with mocked LLM responses, assert the final YAML matches a golden file, assert dry-run passes.

Commit: `test(dev-kit): e2e wizard flow for 3 canonical intake combinations`.

### Task 12.4: Backtracking smoke test

**Files:**
- Create: `dev-kit/tests/agent/test_backtracking.py`

Test the "user changes their mind mid-conversation" cases described in design §7 worked example. Specifically:

1. Start a project with `has_kb=false`, advance to workflow phase.
2. Mid-workflow, simulate the LLM calling `update_intake(field="has_kb", value=True)`.
3. Assert the router lands in the `language` phase (because NLU intents need re-asking).
4. Drive through language → knowledge phases.
5. Assert wizard returns to workflow phase with previous state intact.

Commit: `test(dev-kit): backtracking flow when intake changes mid-conversation`.

---

## Phase 13: Documentation

### Task 13.1: Update `ARCHITECTURE.md` block status

Mark the dev-kit wizard's status:
- "Configuration Agent (Tier 1): Deterministic wizard architecture ✅"

Commit: `docs: update ARCHITECTURE.md with deterministic wizard status`.

### Task 13.2: Add a section in `CLAUDE.md` for the new dev-kit layout

Brief paragraph + file-structure tree + pointer to the design doc and catalogue.

Commit: `docs(CLAUDE.md): document new dev-kit deterministic wizard layout`.

---

## Final checklist (run before declaring complete)

- [ ] All FIELD_RULES tests pass (Task 3.1 through 3.7).
- [ ] Aggregate registry has every Pydantic field from every block's MergedConfig.
- [ ] `build_skeleton` produces a valid accumulator + field_status for all 3 canonical intake combinations.
- [ ] Pre-deploy dry-run catches the obvious "missing required field" cases.
- [ ] Selective compose generates 9 services for a no-KB / no-tools / web-only / voice-absent bot (vs 12 for everything-on).
- [ ] `REACH_LAYER_WEB_MODE=routing_only` is set for voice-only projects.
- [ ] Decision logs surface in INFO at the 13 documented points.
- [ ] Project creation form captures the 5 intake fields and persists `IntakeState`.
- [ ] Deploy form pre-fills `deploy_overridable` fields and writes overrides to `_meta/deploy_settings.json`.
- [ ] No reference to legacy `set_phase`, `set_agent_type`, etc. remains in code.
- [ ] `prompts/phases.py` and `prompts/base.py` are deleted.

---

## Deferred enhancements (NOT in this plan)

- **Memory Layer selective deployment** — drop Memgraph when `needs_persistent_user_data=false`. Tracked as a future plan. (Memory Layer + Redis stay always-included; Memgraph gating is the only addition.)
- **Full dev-kit UI revamp** — separate plan after this lands. The minimal UI changes here are functional but not redesigned.
- **CI guards** — self-contained-schema, Coverage, no-redundancy. Documented in design §5; implementation deferred. Until then, code review and `.claude/rules/runtime-devkit-sync.md` enforce the discipline.
- **Migration of pre-existing project configs** — projects authored under the old wizard re-create from scratch. A separate migration plan can be written later.
- **`trust.consent.purposes` typed taxonomy** — would let `reach_layer.channels.voice.recording.consent_purpose` derive from a structured set rather than being a free-form chat field.

---

## Reference: catalogue ↔ task index

| Catalogue section | Implementing task(s) |
|---|---|
| §2 Notation | 1.2 (FieldRule), 1.3 (path_ops) |
| §3 Common fields | 3.1 (agent_core), 3.2 (trust_layer), 3.3 (reach_layer), 3.4 (observability_layer); 3.5 (note: KE/memory/AG have none) |
| §4 IntakeState-gated | 3.1–3.7 per-block FIELD_RULES |
| §5 Cross-field interactions | 9.1 (slug derivation), 12.3 (e2e tests for invariants), CI guards (deferred) |
| §6 Mid-conversation transitions | 5.1 (`on_intake_update` cascade), 12.4 (backtracking tests) |
| §7 Per-block coverage | 3.1–3.7 |
| §7 framework_default_only allowlists | covered by FIELD_RULES omissions; CI Coverage guard (deferred) |
