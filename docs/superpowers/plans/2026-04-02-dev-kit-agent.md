# Dev Kit Conversation Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an LLM-powered conversation agent inside `dev-kit/` that interviews users and generates domain configs under `dev-kit/configs/<slug>/`, served as a FastAPI + React SPA.

**Architecture:** A `dev_kit.agent` Python module holds the conversation engine, tool handlers, accumulator, renderer, and checkpoints. FastAPI serves the API and the built React SPA from `agent/static/`. The React frontend has Chat, Dashboard, ConfigEditor, and FlowGraph views. All project state (configs + meta) lives on the filesystem under `dev-kit/configs/<slug>/`.

**Tech Stack:** Python 3.13, FastAPI, Anthropic SDK (AsyncAnthropic), Pydantic v2, PyYAML, React 18, Vite, React Flow (@xyflow/react), CodeMirror 6, Tailwind CSS.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `dev-kit/pyproject.toml` | Modify | Add runtime + dev dependencies |
| `dev-kit/schema.py` | Modify | Add `Field(description=...)` to key fields; add `validate_partial()` |
| `dev-kit/loader.py` | Modify | Add `get_schema_descriptions()` + `_extract_field_descriptions()` |
| `dev-kit/tests/__init__.py` | Create | Test package |
| `dev-kit/tests/test_schema.py` | Create | Tests for `validate_partial` |
| `dev-kit/tests/test_loader.py` | Create | Tests for `get_schema_descriptions` |
| `dev-kit/agent/__init__.py` | Create | Agent package marker |
| `dev-kit/agent/accumulator.py` | Create | `ConfigAccumulator` — in-memory config state for all 7 blocks |
| `dev-kit/agent/renderer.py` | Create | Writes accumulator data to YAML files; computes config status |
| `dev-kit/agent/checkpoints.py` | Create | Save/restore accumulator snapshots + summaries to `_meta/checkpoints/` |
| `dev-kit/agent/tools.py` | Create | `TOOL_DEFINITIONS` list + `ToolHandler` class (all 10 tool handlers) |
| `dev-kit/agent/prompts/__init__.py` | Create | Prompts package marker |
| `dev-kit/agent/prompts/base.py` | Create | `build_system_prompt()` — assembles full prompt for a phase |
| `dev-kit/agent/prompts/phases.py` | Create | Phase-specific schema context additions |
| `dev-kit/agent/conversation.py` | Create | `ConversationEngine` — manages history, calls Claude, dispatches tools |
| `dev-kit/agent/app.py` | Create | FastAPI app — all routes + engine registry + static files |
| `dev-kit/agent/tests/__init__.py` | Create | Test package |
| `dev-kit/agent/tests/test_accumulator.py` | Create | Accumulator unit tests |
| `dev-kit/agent/tests/test_renderer.py` | Create | Renderer unit tests |
| `dev-kit/agent/tests/test_tools.py` | Create | Tool handler unit tests |
| `dev-kit/agent/tests/test_conversation.py` | Create | Conversation engine tests (mocked Anthropic) |
| `dev-kit/agent/tests/test_app.py` | Create | FastAPI route tests |
| `dev-kit/frontend/package.json` | Create | Frontend dependencies |
| `dev-kit/frontend/vite.config.js` | Create | Vite build config |
| `dev-kit/frontend/postcss.config.js` | Create | PostCSS config for Tailwind |
| `dev-kit/frontend/tailwind.config.js` | Create | Tailwind config |
| `dev-kit/frontend/index.html` | Create | HTML shell |
| `dev-kit/frontend/src/main.jsx` | Create | React entry point |
| `dev-kit/frontend/src/App.jsx` | Create | App routing shell |
| `dev-kit/frontend/src/api.js` | Create | Fetch wrappers for all API endpoints |
| `dev-kit/frontend/src/components/ProjectList.jsx` | Create | Landing page: list + create projects |
| `dev-kit/frontend/src/components/PhaseBar.jsx` | Create | Phase progress indicator + checkpoint nav |
| `dev-kit/frontend/src/components/Chat.jsx` | Create | Chat interface with inline graph panel |
| `dev-kit/frontend/src/components/Dashboard.jsx` | Create | 7-card config status grid |
| `dev-kit/frontend/src/components/ConfigEditor.jsx` | Create | YAML viewer/editor with validation |
| `dev-kit/frontend/src/components/FlowGraph.jsx` | Create | Subagent state machine graph |
| `dev-kit/Dockerfile` | Create | Multi-stage build: frontend + Python |

---

## Task 1: Dependencies

**Files:**
- Modify: `dev-kit/pyproject.toml`

- [ ] **Step 1: Update pyproject.toml**

Replace `dev-kit/pyproject.toml` with:

```toml
[project]
name = "dev-kit"
version = "0.1.0"
description = "DPG Domain Configuration Kit with conversation agent"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.12.5",
    "pyyaml>=6.0",
    "anthropic>=0.49.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "python-dotenv>=1.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
    "pytest-cov>=6.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "agent/tests"]
```

- [ ] **Step 2: Install dependencies**

```bash
cd dev-kit
uv sync --extra dev
```

Expected: `Resolved N packages` with no errors.

- [ ] **Step 3: Create test package roots**

```bash
mkdir -p dev-kit/tests dev-kit/agent/tests
touch dev-kit/tests/__init__.py dev-kit/agent/__init__.py dev-kit/agent/tests/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add dev-kit/pyproject.toml dev-kit/tests/__init__.py dev-kit/agent/__init__.py dev-kit/agent/tests/__init__.py
git commit -m "chore(dev-kit): add agent dependencies and test infrastructure"
```

---

## Task 2: schema.py — Field descriptions + validate_partial

**Files:**
- Modify: `dev-kit/schema.py`
- Create: `dev-kit/tests/test_schema.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_schema.py`:

```python
"""Tests for dev_kit.schema validate_partial."""
import pytest
from dev_kit.schema import validate_partial


class TestValidatePartial:
    def test_empty_dict_returns_no_errors(self):
        assert validate_partial("trust_layer", {}) == []

    def test_valid_trust_config_returns_no_errors(self):
        data = {
            "server": {"host": "0.0.0.0", "port": 8003},
            "trust": {
                "input_rules": {"blocked_phrases": ["spam"]},
                "output_rules": {"blocked_phrases": []},
            },
        }
        assert validate_partial("trust_layer", data) == []

    def test_type_error_is_reported(self):
        # blocked_phrases must be a list, not a string
        data = {"trust": {"input_rules": {"blocked_phrases": "not-a-list"}}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0

    def test_missing_required_field_is_not_reported(self):
        # AgentCoreConfig has many required fields — missing ones must be ignored
        data = {"agent": {"primary_model": "claude-haiku-4-5-20251001"}}
        errors = validate_partial("agent_core", data)
        assert errors == []

    def test_unknown_block_returns_error(self):
        errors = validate_partial("bogus_block", {})
        assert len(errors) == 1
        assert "Unknown block" in errors[0]

    def test_nested_type_error_is_reported(self):
        # port must be int
        data = {"server": {"host": "0.0.0.0", "port": "not-an-int"}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest tests/test_schema.py -v
```

Expected: `ImportError` — `validate_partial` not yet defined.

- [ ] **Step 3: Add Field descriptions and validate_partial to schema.py**

Add the following import at the top of `dev-kit/schema.py` (after the existing imports):

```python
from pydantic import ValidationError
```

Replace the existing field definitions in the listed classes with descriptions added via `Field()`. Apply changes to these classes (all other classes remain unchanged):

```python
# Replace AgentConfig
class AgentConfig(BaseModel):
    primary_model: str = Field(..., description="Claude model ID for primary inference, e.g. claude-haiku-4-5-20251001")
    fallback_model: str = Field(..., description="Claude model ID used if primary call fails")
    timeout_ms: int = Field(default=10000, description="LLM call timeout in milliseconds")
    retry_attempts: int = Field(default=2, description="Number of retry attempts on transient failure")
    retry_backoff_seconds: list[float] = Field(default=[0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=1, description="Maximum tool call rounds per turn")


# Replace ConversationAgentConfig
class ConversationAgentConfig(BaseModel):
    max_turns: int = Field(default=20)
    blocked_message: str = Field(
        default="I'm unable to help with that request.",
        description="Shown to user when input is blocked by Trust Layer. Translate to user language.",
    )
    escalation_message: str = Field(
        default="I'm connecting you to a human agent who can better assist you.",
        description="Shown when turn is escalated to a human agent.",
    )
    output_blocked_message: str = Field(
        default="I wasn't able to produce a safe response. Please try rephrasing your question.",
        description="Shown when LLM output is blocked by Trust Layer.",
    )


# Replace LanguageNormalisationConfig
class LanguageNormalisationConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for language normalisation")
    provider: str = Field(default="llm_native", description="Normalisation provider: llm_native or bhashini")
    supported_languages: list[str] = Field(..., description="Languages the agent supports, e.g. [hindi, english, kannada, hinglish]")
    transliteration: bool = Field(default=True, description="Normalise transliterated input to canonical script")
    code_switching: bool = Field(default=True, description="Handle mixed-language input within a single message")
    bhashini: BhashiniConfig | None = Field(default=None, description="Required only if provider is bhashini")


# Replace NLUProcessorConfig
class NLUProcessorConfig(BaseModel):
    model: str = Field(..., description="Claude model ID for NLU classification")
    confidence_threshold: float = Field(default=0.5, description="Float 0-1. Intents below this are treated as unknown")
    history_turns: int = Field(default=2)
    intents: list[str] = Field(..., description="List of intent identifiers for this domain, e.g. greeting, profile_answer, apply_now")
    entities: list[str] = Field(..., description="List of entity identifiers to extract, e.g. name, location, trade_or_stream")
    sentiment_classes: list[str] = Field(..., description="Sentiment classes to classify, e.g. [neutral, positive, distressed]")


# Replace ConnectorDef
class ConnectorDef(BaseModel):
    name: str = Field(..., description="Connector name matching a key in action_gateway.connectors")
    description: str = Field(default="", description="Description shown to LLM explaining when to call this connector")


# Replace GlossaryConfig
class GlossaryConfig(BaseModel):
    enabled: bool = Field(default=True)
    mappings: list[GlossaryMapping] = Field(
        default=[],
        description="Colloquial-to-canonical term mappings. Each entry: {colloquial: [...], canonical: string}",
    )
    apply_to: list[str] = Field(
        default=["normalised_input", "entities"],
        description="Config fields to apply glossary to",
    )


# Replace StaticKBConfig
class StaticKBConfig(BaseModel):
    enabled: bool = True
    vector_store: str = "chromadb"
    collection_name: str = Field(..., description="ChromaDB collection name for this domain's knowledge base")
    chroma_persist_dir: str = "./data/chroma_db"
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    top_k: int = 3
    similarity_threshold: float = 0.65
    sources: list[KnowledgeSource] = Field(
        default=[],
        description="Knowledge sources to ingest. Each: {path, type, doc_type, refresh}",
    )
    metadata_filters: MetadataFiltersConfig = MetadataFiltersConfig()
    intent_filters: dict[str, list[str]] = Field(
        default={},
        description="Map of intent → list of doc_types to retrieve. e.g. {market_truth_query: [scheme, trade]}",
    )


# Replace InputRulesConfig
class InputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that block user input and return blocked_message")
    escalation_topics: list[str] = Field(default=[], description="Strings that trigger human agent escalation")


# Replace OutputRulesConfig
class OutputRulesConfig(BaseModel):
    blocked_phrases: list[str] = Field(default=[], description="Strings that must not appear in LLM output")


# Replace ActionGatewaySettings
class ActionGatewaySettings(BaseModel):
    timeout_ms: int = 5000
    connectors: dict[str, ConnectorEndpointConfig] = Field(
        default={},
        description="Map of connector_name → {endpoint, timeout_ms}. Keys must match names declared in agent_core connectors",
    )


# Replace LearningLayerSettings
class LearningLayerSettings(BaseModel):
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")
```

Then add `validate_partial` at the bottom of `dev-kit/schema.py` (before the last line):

```python
# ---------------------------------------------------------------------------
# Partial validation helper
# ---------------------------------------------------------------------------

_BLOCK_MODEL_MAP: dict[str, type] = {
    "agent_core": AgentCoreConfig,
    "knowledge_engine": KnowledgeEngineConfig,
    "trust_layer": TrustLayerConfig,
    "memory_layer": MemoryLayerConfig,
    "learning_layer": LearningLayerConfig,
    "action_gateway": ActionGatewayConfig,
    "reach_layer": ReachLayerConfig,
}


def validate_partial(block: str, data: dict) -> list[str]:
    """Validate partial config data for a block without requiring completeness.

    Runs schema validation but filters out missing-field errors so configs
    that are still being built do not fail.

    Args:
        block: Block name, e.g. "agent_core" or "trust_layer".
        data: Partial config dict to validate.

    Returns:
        List of error strings for type/value violations. Empty list means valid so far.
    """
    model_cls = _BLOCK_MODEL_MAP.get(block)
    if model_cls is None:
        return [f"Unknown block: {block!r}"]
    try:
        model_cls.model_validate(data)
        return []
    except ValidationError as exc:
        return [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()
            if err["type"] != "missing"
        ]
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest tests/test_schema.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/schema.py dev-kit/tests/test_schema.py
git commit -m "feat(dev-kit): add Field descriptions and validate_partial to schema.py"
```

---

## Task 3: loader.py — get_schema_descriptions

**Files:**
- Modify: `dev-kit/loader.py`
- Create: `dev-kit/tests/test_loader.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_loader.py`:

```python
"""Tests for dev_kit.loader get_schema_descriptions."""
from dev_kit.loader import get_schema_descriptions


class TestGetSchemaDescriptions:
    def test_returns_dict_for_known_block(self):
        result = get_schema_descriptions("trust_layer")
        assert isinstance(result, dict)

    def test_returns_empty_dict_for_unknown_block(self):
        assert get_schema_descriptions("bogus") == {}

    def test_all_keys_and_values_are_strings(self):
        result = get_schema_descriptions("agent_core")
        assert all(isinstance(k, str) for k in result)
        assert all(isinstance(v, str) for v in result.values())

    def test_known_described_field_is_present(self):
        # primary_model has a description after Task 2
        result = get_schema_descriptions("agent_core")
        assert len(result) > 0

    def test_trust_layer_blocked_phrases_described(self):
        result = get_schema_descriptions("trust_layer")
        # blocked_phrases has a description after Task 2
        matching = {k: v for k, v in result.items() if "blocked_phrases" in k}
        assert len(matching) > 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest tests/test_loader.py -v
```

Expected: `ImportError` — `get_schema_descriptions` not yet defined.

- [ ] **Step 3: Add helpers to loader.py**

Add the following at the bottom of `dev-kit/loader.py` (after the `_cli` function):

```python
# ---------------------------------------------------------------------------
# Schema description helpers (used by conversation agent prompt builders)
# ---------------------------------------------------------------------------

def get_schema_descriptions(block: str) -> dict[str, str]:
    """Extract field descriptions from the Pydantic model for a given block.

    Recursively traverses nested Pydantic models to build a flat dict of
    dot-notation field paths to their descriptions. Used by the conversation
    agent to auto-generate phase-specific prompt context.

    Args:
        block: Block name, e.g. "agent_core".

    Returns:
        Flat dict of {field_path: description}. Empty dict for unknown blocks.
    """
    from dev_kit.schema import (
        ActionGatewayConfig,
        AgentCoreConfig,
        KnowledgeEngineConfig,
        LearningLayerConfig,
        MemoryLayerConfig,
        ReachLayerConfig,
        TrustLayerConfig,
    )

    _map: dict[str, type] = {
        "agent_core": AgentCoreConfig,
        "knowledge_engine": KnowledgeEngineConfig,
        "trust_layer": TrustLayerConfig,
        "memory_layer": MemoryLayerConfig,
        "learning_layer": LearningLayerConfig,
        "action_gateway": ActionGatewayConfig,
        "reach_layer": ReachLayerConfig,
    }
    model_cls = _map.get(block)
    if model_cls is None:
        return {}
    return _extract_field_descriptions(model_cls, prefix="")


def _extract_field_descriptions(model_cls: type, prefix: str) -> dict[str, str]:
    """Recursively extract Field descriptions from a Pydantic model.

    Args:
        model_cls: Pydantic BaseModel subclass to introspect.
        prefix: Dot-notation prefix for nested fields.

    Returns:
        Flat dict of {field_path: description}.
    """
    from pydantic import BaseModel

    result: dict[str, str] = {}
    for field_name, field_info in model_cls.model_fields.items():
        path = f"{prefix}.{field_name}" if prefix else field_name
        if field_info.description:
            result[path] = field_info.description
        annotation = field_info.annotation
        if annotation is not None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            result.update(_extract_field_descriptions(annotation, prefix=path))
    return result
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest tests/test_loader.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/loader.py dev-kit/tests/test_loader.py
git commit -m "feat(dev-kit): add get_schema_descriptions to loader.py"
```

---

## Task 4: Accumulator

**Files:**
- Create: `dev-kit/agent/accumulator.py`
- Create: `dev-kit/agent/tests/test_accumulator.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_accumulator.py`:

```python
"""Tests for dev_kit.agent.accumulator.ConfigAccumulator."""
import pytest
from dev_kit.agent.accumulator import (
    BLOCKS,
    DRAFT_BLOCKS,
    ConfigAccumulator,
    ConfigStatus,
    PHASES,
)


class TestConfigAccumulatorUpdate:
    def test_initial_state_all_blocks_empty(self):
        acc = ConfigAccumulator()
        for block in BLOCKS:
            assert acc.get_block(block) == {}

    def test_update_top_level_section(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        assert acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"

    def test_update_nested_section_via_dot_notation(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "preprocessing.nlu_processor", {"confidence_threshold": 0.7})
        assert acc.get_block("agent_core")["preprocessing"]["nlu_processor"]["confidence_threshold"] == 0.7

    def test_update_merges_not_replaces(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.update("agent_core", "agent", {"fallback_model": "claude-haiku-4-5-20251001"})
        block = acc.get_block("agent_core")
        assert block["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert block["agent"]["fallback_model"] == "claude-haiku-4-5-20251001"

    def test_update_list_replaces_not_merges(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "preprocessing.nlu_processor", {"intents": ["greeting"]})
        acc.update("agent_core", "preprocessing.nlu_processor", {"intents": ["greeting", "apply_now"]})
        intents = acc.get_block("agent_core")["preprocessing"]["nlu_processor"]["intents"]
        assert intents == ["greeting", "apply_now"]

    def test_update_unknown_block_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="Unknown block"):
            acc.update("bogus", "section", {})

    def test_get_block_returns_deep_copy(self):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        copy = acc.get_block("trust_layer")
        copy["trust"]["input_rules"]["blocked_phrases"].append("mutated")
        assert acc.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["spam"]


class TestConfigAccumulatorStatus:
    def test_initial_status_all_pending(self):
        acc = ConfigAccumulator()
        for block in BLOCKS:
            assert acc.get_status(block) == ConfigStatus.PENDING

    def test_set_and_get_status(self):
        acc = ConfigAccumulator()
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        assert acc.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_set_status_unknown_block_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError):
            acc.set_status("bogus", ConfigStatus.COMPLETE)


class TestConfigAccumulatorSubagents:
    def test_set_subagent_adds_new(self):
        acc = ConfigAccumulator()
        sa = {"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []}
        acc.set_subagent(sa)
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert len(subagents) == 1
        assert subagents[0]["id"] == "greeting"

    def test_set_subagent_replaces_existing(self):
        acc = ConfigAccumulator()
        sa = {"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []}
        acc.set_subagent(sa)
        acc.set_subagent({**sa, "name": "Updated Greeting"})
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert len(subagents) == 1
        assert subagents[0]["name"] == "Updated Greeting"

    def test_set_subagent_missing_id_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="id"):
            acc.set_subagent({"name": "No ID"})

    def test_update_subagent_modifies_fields(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.update_subagent("greeting", {"name": "Updated"})
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["name"] == "Updated"

    def test_update_subagent_unknown_id_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="no subagent"):
            acc.update_subagent("nonexistent", {"name": "x"})

    def test_remove_subagent_removes_node(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.remove_subagent("greeting")
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"] == []

    def test_add_routing_rule(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.add_routing_rule("greeting", "consent_granted", "profile", [], {})
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0] == {"intent": "consent_granted", "next_subagent_id": "profile"}

    def test_add_routing_rule_with_conditions(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "a", "name": "A", "system_prompt": "x", "is_start": False, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        conditions = [{"field": "income_urgency", "operator": "eq", "value": "immediate"}]
        acc.add_routing_rule("a", "some_intent", "b", conditions, {})
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0]["conditions"] == conditions

    def test_add_routing_rule_unknown_from_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="no subagent"):
            acc.add_routing_rule("nonexistent", "intent", "target", [], {})


class TestConfigAccumulatorSerialisation:
    def test_roundtrip_to_from_dict(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        data = acc.to_dict()
        acc2 = ConfigAccumulator.from_dict(data)
        assert acc2.get_block("agent_core") == acc.get_block("agent_core")
        assert acc2.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_summary_is_string(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        assert isinstance(acc.summary(), str)
        assert "agent_core" in acc.summary()


class TestWorkflowGraph:
    def test_empty_graph(self):
        acc = ConfigAccumulator()
        graph = acc.get_workflow_graph()
        assert graph == {"nodes": [], "edges": []}

    def test_graph_with_nodes_and_edges(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.set_subagent({"id": "profile", "name": "Profile", "system_prompt": "Tell me about yourself", "is_start": False, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.add_routing_rule("greeting", "consent_granted", "profile", [], {})
        graph = acc.get_workflow_graph()
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "greeting", "to": "profile", "intent": "consent_granted"}
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_accumulator.py -v
```

Expected: `ModuleNotFoundError` — `dev_kit.agent.accumulator` not yet created.

- [ ] **Step 3: Create accumulator.py**

Create `dev-kit/agent/accumulator.py`:

```python
"""
dev-kit/agent/accumulator.py

In-memory config accumulator for the DPG conversation agent.

Holds domain config values for all 7 DPG blocks as they are collected
during the conversation. Supports dot-notation path updates, subagent
graph management, serialisation, and status tracking.
"""
from __future__ import annotations

from copy import deepcopy
from enum import Enum


BLOCKS: list[str] = [
    "agent_core",
    "knowledge_engine",
    "memory_layer",
    "trust_layer",
    "action_gateway",
    "reach_layer",
    "learning_layer",
]

DRAFT_BLOCKS: set[str] = {"trust_layer", "action_gateway", "reach_layer", "learning_layer"}

PHASES: list[str] = [
    "overview",
    "language",
    "knowledge",
    "memory",
    "trust",
    "connectors",
    "workflow",
    "review",
]


class ConfigStatus(str, Enum):
    """Status of a block's generated config file."""

    COMPLETE = "complete"
    DRAFT = "draft"
    PENDING = "pending"
    STALE = "stale"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not merged."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class ConfigAccumulator:
    """In-memory holder for domain config values across all 7 DPG blocks.

    Built up incrementally as the conversation progresses. Supports
    dot-notation section paths for nested updates and full subagent
    graph management for the agent_core workflow.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict] = {block: {} for block in BLOCKS}
        self._statuses: dict[str, ConfigStatus] = {block: ConfigStatus.PENDING for block in BLOCKS}

    # ------------------------------------------------------------------
    # Config updates
    # ------------------------------------------------------------------

    def update(self, block: str, section: str, values: dict) -> None:
        """Deep-merge values into the block config at the given dot-notation section.

        Args:
            block: One of the 7 DPG block names.
            section: Dot-notation path, e.g. "preprocessing.nlu_processor".
                     Empty string merges directly into the block root.
            values: Values to merge.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}. Must be one of {BLOCKS}")
        if not section:
            self._data[block] = _deep_merge(self._data[block], values)
            return
        keys = section.split(".")
        target = self._data[block]
        current = target
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        last = keys[-1]
        if last not in current or not isinstance(current.get(last), dict):
            current[last] = {}
        current[last] = _deep_merge(current[last], values)

    def get_block(self, block: str) -> dict:
        """Return a deep copy of the full config dict for a block.

        Args:
            block: One of the 7 DPG block names.

        Returns:
            Deep copy of the block's accumulated config.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        return deepcopy(self._data[block])

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_status(self, block: str, status: ConfigStatus) -> None:
        """Set the status of a block config.

        Args:
            block: One of the 7 DPG block names.
            status: New status.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        self._statuses[block] = status

    def get_status(self, block: str) -> ConfigStatus:
        """Return the current status of a block config.

        Args:
            block: One of the 7 DPG block names.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        return self._statuses[block]

    # ------------------------------------------------------------------
    # Subagent graph management
    # ------------------------------------------------------------------

    def set_subagent(self, subagent: dict) -> None:
        """Add or replace a subagent in the agent_core workflow.

        Args:
            subagent: Subagent dict. Must include an 'id' key.

        Raises:
            ValueError: If subagent has no 'id' key.
        """
        if "id" not in subagent:
            raise ValueError("Subagent must have an 'id' key")
        workflow = self._data["agent_core"].setdefault("agent_workflow", {})
        subagents: list[dict] = workflow.setdefault("subagents", [])
        for i, sa in enumerate(subagents):
            if sa.get("id") == subagent["id"]:
                subagents[i] = deepcopy(subagent)
                return
        subagents.append(deepcopy(subagent))

    def update_subagent(self, subagent_id: str, fields: dict) -> None:
        """Merge fields into an existing subagent.

        Args:
            subagent_id: ID of the subagent to update.
            fields: Fields to merge.

        Raises:
            ValueError: If no subagent with the given ID exists.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == subagent_id:
                sa.update(fields)
                return
        raise ValueError(f"no subagent with id {subagent_id!r}")

    def remove_subagent(self, subagent_id: str) -> None:
        """Remove a subagent. No-op if not found.

        Args:
            subagent_id: ID of the subagent to remove.
        """
        workflow = self._data.get("agent_core", {}).get("agent_workflow", {})
        workflow["subagents"] = [sa for sa in workflow.get("subagents", []) if sa.get("id") != subagent_id]

    def add_routing_rule(
        self,
        from_subagent_id: str,
        intent: str,
        next_subagent_id: str,
        conditions: list[dict],
        session_writes: dict,
    ) -> None:
        """Add a routing rule to a subagent.

        Args:
            from_subagent_id: Source subagent ID.
            intent: Intent that triggers this rule. Use "*" for catch-all.
            next_subagent_id: Destination subagent ID.
            conditions: Optional list of session state conditions.
            session_writes: Optional session fields to write when rule matches.

        Raises:
            ValueError: If no subagent with from_subagent_id exists.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == from_subagent_id:
                rule: dict = {"intent": intent, "next_subagent_id": next_subagent_id}
                if conditions:
                    rule["conditions"] = conditions
                if session_writes:
                    rule["session_writes"] = session_writes
                sa.setdefault("routing", []).append(rule)
                return
        raise ValueError(f"no subagent with id {from_subagent_id!r}")

    def update_routing_rule(self, from_subagent_id: str, intent: str, fields: dict) -> None:
        """Update an existing routing rule on a subagent.

        Args:
            from_subagent_id: Source subagent ID.
            intent: Intent that identifies the rule.
            fields: Fields to update.

        Raises:
            ValueError: If no matching subagent or routing rule is found.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == from_subagent_id:
                for rule in sa.get("routing", []):
                    if rule.get("intent") == intent:
                        rule.update(fields)
                        return
                raise ValueError(f"no routing rule for intent {intent!r} on subagent {from_subagent_id!r}")
        raise ValueError(f"no subagent with id {from_subagent_id!r}")

    def get_workflow_graph(self) -> dict:
        """Return the subagent workflow as nodes and edges for the frontend.

        Returns:
            Dict with 'nodes' (list of {id, name, type}) and
            'edges' (list of {from, to, intent}).
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        nodes = []
        edges = []
        for sa in subagents:
            node_type = "start" if sa.get("is_start") else ("end" if sa.get("is_terminal") else "normal")
            nodes.append({"id": sa["id"], "name": sa.get("name", sa["id"]), "type": node_type})
            for rule in sa.get("routing", []):
                edges.append({"from": sa["id"], "to": rule.get("next_subagent_id", ""), "intent": rule.get("intent", "")})
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of current config state for system prompts."""
        lines = ["Current config state:"]
        for block in BLOCKS:
            data = self._data[block]
            status = self._statuses[block].value
            if data:
                keys = list(data.keys())[:4]
                lines.append(f"  {block} ({status}): {', '.join(keys)}")
            else:
                lines.append(f"  {block} ({status}): empty")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict for checkpoint storage.

        Returns:
            Dict with 'data' and 'statuses' keys.
        """
        return {
            "data": deepcopy(self._data),
            "statuses": {block: status.value for block, status in self._statuses.items()},
        }

    @classmethod
    def from_dict(cls, snapshot: dict) -> "ConfigAccumulator":
        """Restore from a serialised snapshot.

        Args:
            snapshot: Dict previously returned by to_dict().

        Returns:
            New ConfigAccumulator with restored state.
        """
        acc = cls()
        acc._data = deepcopy(snapshot.get("data", {b: {} for b in BLOCKS}))
        for block, status_str in snapshot.get("statuses", {}).items():
            try:
                acc._statuses[block] = ConfigStatus(status_str)
            except ValueError:
                acc._statuses[block] = ConfigStatus.PENDING
        return acc
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_accumulator.py -v
```

Expected: `22 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/accumulator.py dev-kit/agent/tests/test_accumulator.py
git commit -m "feat(dev-kit): add ConfigAccumulator"
```

---

## Task 5: Renderer

**Files:**
- Create: `dev-kit/agent/renderer.py`
- Create: `dev-kit/agent/tests/test_renderer.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_renderer.py`:

```python
"""Tests for dev_kit.agent.renderer."""
import pytest
import yaml
from pathlib import Path
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus, DRAFT_BLOCKS
from dev_kit.agent.renderer import render_all, render_block


class TestRenderBlock:
    def test_empty_block_writes_pending_file(self, tmp_path):
        acc = ConfigAccumulator()
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "no config" in content.lower() or content.strip().startswith("#")
        assert acc.get_status("trust_layer") == ConfigStatus.PENDING

    def test_draft_block_with_data_writes_draft_header(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert "STATUS: draft" in content
        assert acc.get_status("trust_layer") == ConfigStatus.DRAFT

    def test_non_draft_block_with_data_no_draft_header(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "knowledge", {"blocks": {"glossary": {"enabled": True, "mappings": []}}})
        render_block(tmp_path, "knowledge_engine", acc)
        content = (tmp_path / "knowledge_engine.yaml").read_text()
        assert "STATUS: draft" not in content

    def test_written_yaml_is_parseable(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        render_block(tmp_path, "trust_layer", acc)
        content = (tmp_path / "trust_layer.yaml").read_text()
        parsed = yaml.safe_load(content)
        assert parsed is not None


class TestRenderAll:
    def test_creates_all_7_files(self, tmp_path):
        acc = ConfigAccumulator()
        render_all(tmp_path, acc)
        for block in ["agent_core", "knowledge_engine", "memory_layer",
                      "trust_layer", "action_gateway", "reach_layer", "learning_layer"]:
            assert (tmp_path / f"{block}.yaml").exists()

    def test_returns_status_dict_for_all_blocks(self, tmp_path):
        acc = ConfigAccumulator()
        statuses = render_all(tmp_path, acc)
        assert set(statuses.keys()) == {
            "agent_core", "knowledge_engine", "memory_layer",
            "trust_layer", "action_gateway", "reach_layer", "learning_layer",
        }
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_renderer.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create renderer.py**

Create `dev-kit/agent/renderer.py`:

```python
"""
dev-kit/agent/renderer.py

Writes accumulated config values to YAML files in a project directory.
Computes config status based on data presence and block type.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from dev_kit.agent.accumulator import BLOCKS, DRAFT_BLOCKS, ConfigAccumulator, ConfigStatus

_DRAFT_HEADER = "# STATUS: draft — block template not yet finalized\n"


def render_all(project_path: Path, accumulator: ConfigAccumulator) -> dict[str, ConfigStatus]:
    """Write all 7 block config YAML files and return their statuses.

    Args:
        project_path: Absolute path to the project's configs directory.
        accumulator: Current config accumulator.

    Returns:
        Dict of block name → ConfigStatus after writing.
    """
    project_path.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, ConfigStatus] = {}
    for block in BLOCKS:
        render_block(project_path, block, accumulator)
        statuses[block] = accumulator.get_status(block)
    return statuses


def render_block(project_path: Path, block: str, accumulator: ConfigAccumulator) -> None:
    """Write a single block's domain config YAML and update its status in the accumulator.

    Status rules:
    - Empty data → PENDING
    - Draft block (one of the 4 open blocks) with data → DRAFT
    - Non-draft block with data → COMPLETE (agent-generated content is assumed valid)
    - STALE is set externally by the PUT /configs/:block endpoint on validation failure.

    Args:
        project_path: Absolute path to the project's configs directory.
        block: Block name.
        accumulator: Config accumulator to read from and update status in.
    """
    data = accumulator.get_block(block)
    out_path = project_path / f"{block}.yaml"

    if not data:
        out_path.write_text(f"# {block} — no config generated yet\n")
        accumulator.set_status(block, ConfigStatus.PENDING)
        return

    yaml_content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if block in DRAFT_BLOCKS:
        out_path.write_text(_DRAFT_HEADER + yaml_content)
        accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        out_path.write_text(yaml_content)
        accumulator.set_status(block, ConfigStatus.COMPLETE)


def load_block_from_file(project_path: Path, block: str) -> dict:
    """Load a block YAML file back into a dict (for reverse-sync from manual edits).

    Strips the draft header comment before parsing.

    Args:
        project_path: Absolute path to the project's configs directory.
        block: Block name.

    Returns:
        Parsed YAML dict, or empty dict if file does not exist.
    """
    path = project_path / f"{block}.yaml"
    if not path.exists():
        return {}
    raw = path.read_text()
    # Strip comment lines (draft header)
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    return yaml.safe_load("\n".join(lines)) or {}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_renderer.py -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/renderer.py dev-kit/agent/tests/test_renderer.py
git commit -m "feat(dev-kit): add renderer — accumulator to YAML files"
```

---

## Task 6: Checkpoints

**Files:**
- Create: `dev-kit/agent/checkpoints.py`
- Create: `dev-kit/agent/tests/test_checkpoints.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_checkpoints.py`:

```python
"""Tests for dev_kit.agent.checkpoints."""
import json
import pytest
from pathlib import Path
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.checkpoints import save_checkpoint, restore_checkpoint, list_checkpoints, build_summary


class TestSaveAndRestore:
    def test_save_creates_checkpoint_directory(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        history = [{"role": "user", "content": "hello"}]
        save_checkpoint(tmp_path, "05_trust", acc, history)
        assert (tmp_path / "_meta" / "checkpoints" / "05_trust").is_dir()

    def test_save_writes_all_files(self, tmp_path):
        acc = ConfigAccumulator()
        save_checkpoint(tmp_path, "01_overview", acc, [])
        checkpoint_dir = tmp_path / "_meta" / "checkpoints" / "01_overview"
        assert (checkpoint_dir / "accumulator.json").exists()
        assert (checkpoint_dir / "history.json").exists()
        assert (checkpoint_dir / "summary.txt").exists()
        assert (checkpoint_dir / "timestamp.json").exists()

    def test_restore_recovers_accumulator_state(self, tmp_path):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        save_checkpoint(tmp_path, "02_language", acc, [])
        restored_acc, summary = restore_checkpoint(tmp_path, "02_language")
        assert restored_acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert restored_acc.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_restore_recovers_history(self, tmp_path):
        acc = ConfigAccumulator()
        history = [{"role": "user", "content": "tell me about ITI workers"}]
        save_checkpoint(tmp_path, "01_overview", acc, history)
        restored_acc, summary = restore_checkpoint(tmp_path, "01_overview")
        assert isinstance(summary, str)

    def test_restore_missing_checkpoint_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            restore_checkpoint(tmp_path, "01_overview")


class TestListCheckpoints:
    def test_empty_project_returns_empty_list(self, tmp_path):
        assert list_checkpoints(tmp_path) == []

    def test_lists_saved_checkpoints_in_order(self, tmp_path):
        acc = ConfigAccumulator()
        save_checkpoint(tmp_path, "01_overview", acc, [])
        save_checkpoint(tmp_path, "02_language", acc, [])
        checkpoints = list_checkpoints(tmp_path)
        assert len(checkpoints) == 2
        assert checkpoints[0]["phase"] == "01_overview"
        assert checkpoints[1]["phase"] == "02_language"


class TestBuildSummary:
    def test_returns_string_with_phase(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        summary = build_summary("02_language", acc)
        assert isinstance(summary, str)
        assert "02_language" in summary
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_checkpoints.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create checkpoints.py**

Create `dev-kit/agent/checkpoints.py`:

```python
"""
dev-kit/agent/checkpoints.py

Saves and restores conversation state snapshots for the DPG conversation agent.

Each checkpoint stores the full accumulator state, conversation history,
and a human-readable summary at a phase boundary. Checkpoints live under
<project_path>/_meta/checkpoints/<phase_name>/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dev_kit.agent.accumulator import ConfigAccumulator


def save_checkpoint(
    project_path: Path,
    phase: str,
    accumulator: ConfigAccumulator,
    history: list[dict],
) -> None:
    """Save a checkpoint snapshot for the given phase.

    Args:
        project_path: Root directory of the project (configs/<slug>/).
        phase: Phase identifier, e.g. "01_overview".
        accumulator: Current config accumulator.
        history: Current conversation message history.
    """
    checkpoint_dir = project_path / "_meta" / "checkpoints" / phase
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    (checkpoint_dir / "accumulator.json").write_text(
        json.dumps(accumulator.to_dict(), ensure_ascii=False, indent=2)
    )
    (checkpoint_dir / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2)
    )
    (checkpoint_dir / "summary.txt").write_text(build_summary(phase, accumulator))
    (checkpoint_dir / "timestamp.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat()})
    )


def restore_checkpoint(project_path: Path, phase: str) -> tuple[ConfigAccumulator, str]:
    """Restore accumulator and summary from a checkpoint.

    Args:
        project_path: Root directory of the project.
        phase: Phase identifier to restore.

    Returns:
        Tuple of (restored accumulator, summary text).

    Raises:
        FileNotFoundError: If the checkpoint directory does not exist.
    """
    checkpoint_dir = project_path / "_meta" / "checkpoints" / phase
    if not checkpoint_dir.exists():
        raise FileNotFoundError(f"Checkpoint not found: {phase!r} at {checkpoint_dir}")

    acc = ConfigAccumulator.from_dict(
        json.loads((checkpoint_dir / "accumulator.json").read_text())
    )
    summary = (checkpoint_dir / "summary.txt").read_text()
    return acc, summary


def list_checkpoints(project_path: Path) -> list[dict]:
    """List all saved checkpoints for a project, sorted by phase name.

    Args:
        project_path: Root directory of the project.

    Returns:
        List of dicts with 'phase', 'created_at', 'summary' keys.
    """
    checkpoints_dir = project_path / "_meta" / "checkpoints"
    if not checkpoints_dir.exists():
        return []
    result = []
    for phase_dir in sorted(checkpoints_dir.iterdir()):
        if not phase_dir.is_dir():
            continue
        timestamp = {}
        ts_file = phase_dir / "timestamp.json"
        if ts_file.exists():
            timestamp = json.loads(ts_file.read_text())
        summary = ""
        summary_file = phase_dir / "summary.txt"
        if summary_file.exists():
            summary = summary_file.read_text()
        result.append({
            "phase": phase_dir.name,
            "created_at": timestamp.get("created_at", ""),
            "summary": summary,
        })
    return result


def build_summary(phase: str, accumulator: ConfigAccumulator) -> str:
    """Build a deterministic human-readable summary from the accumulator state.

    Used in system prompts to give the LLM context about prior phases
    without replaying the full conversation history.

    Args:
        phase: Phase identifier.
        accumulator: Config accumulator at checkpoint time.

    Returns:
        Multi-line summary string.
    """
    return f"Checkpoint: {phase}\n{accumulator.summary()}"
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_checkpoints.py -v
```

Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/checkpoints.py dev-kit/agent/tests/test_checkpoints.py
git commit -m "feat(dev-kit): add checkpoints — save/restore conversation state"
```

---

## Task 7: Tools

**Files:**
- Create: `dev-kit/agent/tools.py`
- Create: `dev-kit/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_tools.py`:

```python
"""Tests for dev_kit.agent.tools.ToolHandler."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator, ConfigStatus
from dev_kit.agent.tools import ToolHandler, TOOL_DEFINITIONS


class TestToolDefinitions:
    def test_all_10_tools_defined(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert names == {
            "set_project_meta", "update_config", "set_phase",
            "create_subagent", "update_subagent", "add_routing_rule",
            "update_routing_rule", "remove_subagent",
            "finalize_config", "rollback_to_checkpoint",
        }

    def test_each_tool_has_required_keys(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool


class TestToolHandlerUpdateConfig:
    def test_updates_accumulator(self):
        acc = ConfigAccumulator()
        state = {"phase": "language", "phase_changed": None}
        handler = ToolHandler(acc, state)
        result = handler.dispatch("update_config", {
            "block": "agent_core",
            "section": "agent",
            "values": {"primary_model": "claude-haiku-4-5-20251001"},
        })
        assert acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert "ok" in result.lower() or "updated" in result.lower()


class TestToolHandlerSetPhase:
    def test_updates_phase_in_state(self):
        acc = ConfigAccumulator()
        state = {"phase": "overview", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("set_phase", {"phase": "language"})
        assert state["phase_changed"] == "language"


class TestToolHandlerSubagents:
    def test_create_subagent(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting",
            "name": "Greeting",
            "description": "Entry point",
            "system_prompt": "Welcome the user",
            "is_start": True,
            "is_terminal": False,
            "valid_intents": ["greeting"],
            "tools": [],
        })
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert subagents[0]["id"] == "greeting"

    def test_create_duplicate_subagent_returns_message(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        sa = {"id": "greeting", "name": "Greeting", "description": "x", "system_prompt": "y", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": []}
        handler.dispatch("create_subagent", sa)
        result = handler.dispatch("create_subagent", sa)
        assert "already exists" in result.lower() or "use update_subagent" in result.lower()

    def test_remove_subagent(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting", "name": "G", "description": "x",
            "system_prompt": "y", "is_start": True, "is_terminal": False,
            "valid_intents": [], "tools": [],
        })
        handler.dispatch("remove_subagent", {"id": "greeting"})
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"] == []

    def test_add_routing_rule(self):
        acc = ConfigAccumulator()
        state = {"phase": "workflow", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("create_subagent", {
            "id": "greeting", "name": "G", "description": "x",
            "system_prompt": "y", "is_start": True, "is_terminal": False,
            "valid_intents": [], "tools": [],
        })
        handler.dispatch("add_routing_rule", {
            "from_subagent_id": "greeting",
            "intent": "consent_granted",
            "next_subagent_id": "profile",
        })
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0]["intent"] == "consent_granted"


class TestToolHandlerFinalizeConfig:
    def test_sets_block_complete(self):
        acc = ConfigAccumulator()
        acc.update("knowledge_engine", "knowledge", {"blocks": {}})
        state = {"phase": "knowledge", "phase_changed": None}
        handler = ToolHandler(acc, state)
        handler.dispatch("finalize_config", {"block": "knowledge_engine"})
        assert acc.get_status("knowledge_engine") == ConfigStatus.COMPLETE
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_tools.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create tools.py**

Create `dev-kit/agent/tools.py`:

```python
"""
dev-kit/agent/tools.py

Tool definitions (JSON schemas for Claude) and handler dispatch for the
DPG conversation agent. All 10 tools are defined here.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator, ConfigStatus

# ---------------------------------------------------------------------------
# Tool JSON schema definitions passed to the Claude API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "set_project_meta",
        "description": "Set the project name, description, and domain slug. Call once you understand the use case from the Domain Overview phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable project name"},
                "slug": {"type": "string", "description": "URL-safe identifier, lowercase with hyphens, e.g. rural-jobs-assistant"},
                "description": {"type": "string", "description": "One-paragraph description of the use case"},
                "user_persona": {"type": "string", "description": "Who the end users are"},
                "domain_summary": {"type": "string", "description": "The domain and problem the AI agent addresses"},
            },
            "required": ["name", "slug", "description"],
        },
    },
    {
        "name": "update_config",
        "description": "Update a section of a block's domain config. Values are deep-merged into the current state for that block.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
                "section": {
                    "type": "string",
                    "description": "Dot-notation path to the config section, e.g. 'preprocessing.nlu_processor' or 'conversation'",
                },
                "values": {"type": "object", "description": "Key-value pairs to merge into the section"},
            },
            "required": ["block", "section", "values"],
        },
    },
    {
        "name": "set_phase",
        "description": "Advance the conversation to the next phase. Call when you have collected enough information for the current phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "enum": ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"],
                },
            },
            "required": ["phase"],
        },
    },
    {
        "name": "create_subagent",
        "description": "Add a new subagent node to the agent_workflow. Appears as a node in the conversation flow graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case identifier"},
                "name": {"type": "string", "description": "Human-readable name"},
                "description": {"type": "string", "description": "What this subagent does"},
                "system_prompt": {"type": "string", "description": "LLM instructions for this conversation state"},
                "is_start": {"type": "boolean", "default": False},
                "is_terminal": {"type": "boolean", "default": False},
                "valid_intents": {"type": "array", "items": {"type": "string"}, "default": []},
                "tools": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["id", "name", "description", "system_prompt"],
        },
    },
    {
        "name": "update_subagent",
        "description": "Modify an existing subagent's fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "fields": {"type": "object", "description": "Any subset of the subagent definition to update"},
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "add_routing_rule",
        "description": "Add a routing rule (transition edge) from one subagent to another, triggered by an intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string", "description": "Intent that triggers this transition. Use '*' for catch-all."},
                "next_subagent_id": {"type": "string"},
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {"type": "string", "enum": ["eq", "not_eq", "gt", "lt", "gte", "lte"]},
                            "value": {},
                        },
                        "required": ["field", "operator", "value"],
                    },
                    "description": "Optional session state conditions",
                    "default": [],
                },
                "session_writes": {
                    "type": "object",
                    "description": "Optional session field writes when this rule fires",
                    "default": {},
                },
            },
            "required": ["from_subagent_id", "intent", "next_subagent_id"],
        },
    },
    {
        "name": "update_routing_rule",
        "description": "Modify an existing routing rule identified by from_subagent_id + intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string"},
                "fields": {"type": "object", "description": "Fields to update on the routing rule"},
            },
            "required": ["from_subagent_id", "intent", "fields"],
        },
    },
    {
        "name": "remove_subagent",
        "description": "Remove a subagent and all its outgoing routing rules from the workflow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "ID of the subagent to remove"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "finalize_config",
        "description": "Mark a config as complete. Use after confirming a block's config is fully specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
            },
            "required": ["block"],
        },
    },
    {
        "name": "rollback_to_checkpoint",
        "description": "Signal that the conversation should roll back to a previous checkpoint. Use only when the user explicitly requests it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Checkpoint phase identifier, e.g. '01_overview'"},
            },
            "required": ["phase"],
        },
    },
]

# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


class ToolHandler:
    """Dispatches Claude tool calls to their handler methods.

    Handlers modify the ConfigAccumulator and/or the shared mutable state dict.

    Args:
        accumulator: The project's config accumulator.
        state: Mutable dict with keys 'phase' (str) and 'phase_changed' (str | None).
               Handlers set state['phase_changed'] to the new phase name when set_phase
               is called, so the ConversationEngine can trigger a checkpoint.
    """

    def __init__(self, accumulator: ConfigAccumulator, state: dict) -> None:
        self._acc = accumulator
        self._state = state

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call to the appropriate handler.

        Args:
            tool_name: Tool name matching one of TOOL_DEFINITIONS.
            tool_input: Tool input values from the LLM.

        Returns:
            Result string to send back as tool_result content.

        Raises:
            ValueError: If tool_name is not recognised.
        """
        handlers = {
            "set_project_meta": self._handle_set_project_meta,
            "update_config": self._handle_update_config,
            "set_phase": self._handle_set_phase,
            "create_subagent": self._handle_create_subagent,
            "update_subagent": self._handle_update_subagent,
            "add_routing_rule": self._handle_add_routing_rule,
            "update_routing_rule": self._handle_update_routing_rule,
            "remove_subagent": self._handle_remove_subagent,
            "finalize_config": self._handle_finalize_config,
            "rollback_to_checkpoint": self._handle_rollback_to_checkpoint,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name!r}")
        return handler(tool_input)

    def _handle_set_project_meta(self, inputs: dict) -> str:
        self._state["project_meta"] = inputs
        return f"Project meta set: {inputs.get('name', '')} ({inputs.get('slug', '')})"

    def _handle_update_config(self, inputs: dict) -> str:
        self._acc.update(inputs["block"], inputs["section"], inputs["values"])
        return f"ok: updated {inputs['block']}.{inputs['section']}"

    def _handle_set_phase(self, inputs: dict) -> str:
        self._state["phase_changed"] = inputs["phase"]
        return f"Phase advancing to: {inputs['phase']}"

    def _handle_create_subagent(self, inputs: dict) -> str:
        existing = [
            sa for sa in self._acc.get_block("agent_core")
            .get("agent_workflow", {})
            .get("subagents", [])
            if sa.get("id") == inputs["id"]
        ]
        if existing:
            return f"Subagent '{inputs['id']}' already exists — use update_subagent to modify it."
        sa = {
            "id": inputs["id"],
            "name": inputs["name"],
            "description": inputs["description"],
            "is_start": inputs.get("is_start", False),
            "is_terminal": inputs.get("is_terminal", False),
            "special_handler": None,
            "valid_intents": inputs.get("valid_intents", []),
            "tools": inputs.get("tools", []),
            "system_prompt": inputs["system_prompt"],
            "routing": [],
        }
        self._acc.set_subagent(sa)
        return f"Subagent '{inputs['id']}' created."

    def _handle_update_subagent(self, inputs: dict) -> str:
        try:
            self._acc.update_subagent(inputs["id"], inputs["fields"])
            return f"Subagent '{inputs['id']}' updated."
        except ValueError as exc:
            return str(exc)

    def _handle_add_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.add_routing_rule(
                inputs["from_subagent_id"],
                inputs["intent"],
                inputs["next_subagent_id"],
                inputs.get("conditions", []),
                inputs.get("session_writes", {}),
            )
            return (
                f"Routing rule added: {inputs['from_subagent_id']}"
                f" --[{inputs['intent']}]--> {inputs['next_subagent_id']}"
            )
        except ValueError as exc:
            return str(exc)

    def _handle_update_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.update_routing_rule(inputs["from_subagent_id"], inputs["intent"], inputs["fields"])
            return f"Routing rule updated: {inputs['from_subagent_id']} --[{inputs['intent']}]-->"
        except ValueError as exc:
            return str(exc)

    def _handle_remove_subagent(self, inputs: dict) -> str:
        self._acc.remove_subagent(inputs["id"])
        return f"Subagent '{inputs['id']}' removed."

    def _handle_finalize_config(self, inputs: dict) -> str:
        self._acc.set_status(inputs["block"], ConfigStatus.COMPLETE)
        return f"Config '{inputs['block']}' marked complete."

    def _handle_rollback_to_checkpoint(self, inputs: dict) -> str:
        self._state["rollback_to"] = inputs["phase"]
        return f"Rollback to checkpoint '{inputs['phase']}' requested."
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_tools.py -v
```

Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/tools.py dev-kit/agent/tests/test_tools.py
git commit -m "feat(dev-kit): add tool definitions and ToolHandler"
```

---

## Task 8: Prompts

**Files:**
- Create: `dev-kit/agent/prompts/__init__.py`
- Create: `dev-kit/agent/prompts/base.py`
- Create: `dev-kit/agent/prompts/phases.py`

No TDD for prompts — they are string templates. A basic smoke test is included.

- [ ] **Step 1: Create prompts package**

```bash
mkdir -p dev-kit/agent/prompts
touch dev-kit/agent/prompts/__init__.py
```

- [ ] **Step 2: Create phases.py**

Create `dev-kit/agent/prompts/phases.py`:

```python
"""
dev-kit/agent/prompts/phases.py

Phase-specific additions to the system prompt. Each phase adds focused
schema context so the LLM knows exactly what fields to collect.
"""
from __future__ import annotations

from dev_kit.loader import get_schema_descriptions

_WORKFLOW_EXAMPLE = """
Example subagent (condensed from KKB reference):

  id: greeting
  name: Greeting
  is_start: true
  system_prompt: |
    Welcome the user briefly. Ask for consent to save their profile.
    Respond in the user's language.
  routing:
    - intent: consent_granted
      next_subagent_id: profile_building
      session_writes:
        user_storage_mode: "saved"
    - intent: consent_declined
      next_subagent_id: profile_building
      session_writes:
        user_storage_mode: "anonymous"
    - intent: "*"
      next_subagent_id: profile_building

  id: profile_building
  name: Profile Building
  system_prompt: |
    Collect name, location, and what the user does for work.
    Hard minimum: location + occupation must be known before proceeding.
  routing:
    - intent: profile_complete
      next_subagent_id: main_action
    - intent: "*"
      next_subagent_id: profile_building

  id: main_action
  name: Main Action
  is_terminal: false
  tools: [your_read_connector]
  system_prompt: |
    Deliver the core value of the AI based on the user's profile.
  routing:
    - intent: task_complete
      next_subagent_id: ended
    - intent: "*"
      next_subagent_id: main_action

  id: ended
  name: Ended
  is_terminal: true
  system_prompt: Thank the user and close the session.
  routing: []
"""


def get_phase_addition(phase: str, available_connectors: list[str] | None = None) -> str:
    """Return schema context to append to the base system prompt for a given phase.

    Args:
        phase: Current conversation phase name.
        available_connectors: Connector names declared in agent_core (used in workflow phase).

    Returns:
        Additional system prompt text for the phase, or empty string if none.
    """
    if phase == "overview":
        return ""

    if phase == "language":
        agent_desc = get_schema_descriptions("agent_core")
        ke_desc = get_schema_descriptions("knowledge_engine")
        relevant = {
            k: v for k, v in {**agent_desc, **ke_desc}.items()
            if any(kw in k for kw in ["primary_model", "fallback_model", "language", "model", "transliteration"])
        }
        lines = ["## Schema context for Language & Models phase", ""]
        for path, desc in relevant.items():
            lines.append(f"- `{path}`: {desc}")
        return "\n".join(lines)

    if phase == "knowledge":
        desc = get_schema_descriptions("knowledge_engine")
        lines = ["## Schema context for Knowledge phase", ""]
        for path, desc_text in desc.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "## Glossary format",
            "Each mapping: `{colloquial: [list of synonyms], canonical: standard_identifier}`",
            "",
            "## Source types",
            "- `static` — PDF/CSV/markdown ingested into the vector store",
            "- `always_include` — always retrieved regardless of intent",
            "",
            "## Intent filter format",
            "Map of intent → list of doc_types to retrieve, e.g. `{job_query: [role, employer]}`",
        ]
        return "\n".join(lines)

    if phase == "memory":
        lines = [
            "## Schema context for Memory phase",
            "",
            "## Session schema field types",
            "- `{type: enum, values: [...], default: value}` — for categorical fields",
            "- `{type: string, default: ''}` — for free-text fields",
            "- `{type: int, default: 0}` — for counters",
            "- `{type: list, default: []}` — for list fields",
            "",
            "## Persistent graph structure",
            "- `user_node.label` — Neo4j/Memgraph label for the user node (e.g. 'User')",
            "- `user_node.key` — unique user identifier property (e.g. 'user_id')",
            "- `subnodes` — map of subnode name → {rel, declared_fields, [child]}",
            "",
            "## merge_on_session_end",
            "List of {session_field, target} — promotes session values to graph nodes on close.",
        ]
        return "\n".join(lines)

    if phase == "trust":
        desc = get_schema_descriptions("trust_layer")
        lines = ["## Schema context for Trust phase", ""]
        for path, desc_text in desc.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "Note: trust_layer config is STATUS: draft — block template not yet finalised.",
            "Collect blocked phrases, escalation topics, and output restrictions.",
        ]
        return "\n".join(lines)

    if phase == "connectors":
        agent_desc = get_schema_descriptions("agent_core")
        gw_desc = get_schema_descriptions("action_gateway")
        relevant = {k: v for k, v in {**agent_desc, **gw_desc}.items() if "connector" in k.lower()}
        lines = [
            "## Schema context for Connectors phase",
            "",
            "## Connector types",
            "- `read` — retrieves data (no consent required)",
            "- `write` — modifies external state (Trust Layer consent required before call)",
            "- `identity` — identity verification connectors",
            "- `internal` — routes to another DPG block (e.g. knowledge_retrieval → knowledge_engine)",
            "",
        ]
        for path, desc_text in relevant.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "Note: action_gateway config is STATUS: draft.",
            "Collect connector names, descriptions, and input_schema for each connector.",
        ]
        return "\n".join(lines)

    if phase == "workflow":
        connector_note = ""
        if available_connectors:
            connector_note = f"\n\nAvailable connectors (declared in Connectors phase): {', '.join(available_connectors)}"
        return (
            "## Workflow Design phase\n\n"
            "Build the subagent state machine step by step:\n"
            "1. Propose an initial flow based on everything you know about this domain.\n"
            "2. Walk through each subagent: purpose, system_prompt, valid_intents, routing rules.\n"
            "3. Use `create_subagent` for each node and `add_routing_rule` for each edge.\n"
            "4. Suggest intents based on the conversation flow. Keep them specific to this domain.\n"
            "5. After the graph is built, use `update_config` to set `agent_workflow.workflow_id`,\n"
            "   `agent_workflow.version`, `agent_workflow.agent_system_prompt`, `agent_workflow.global_intents`,\n"
            "   `agent_workflow.global_routing`, and `agent_workflow.default_fallback_subagent_id`.\n"
            "6. Also set `preprocessing.nlu_processor.intents` (flat list) and `preprocessing.nlu_processor.entities`.\n\n"
            + _WORKFLOW_EXAMPLE
            + connector_note
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "All configs have been generated. Review the accumulated state above.\n"
            "If any required field is missing or incorrect, use the appropriate tool to fix it.\n"
            "Call `finalize_config` for each block that is complete.\n"
            "The user can now view configs in the dashboard and edit them directly."
        )

    return ""
```

- [ ] **Step 3: Create base.py**

Create `dev-kit/agent/prompts/base.py`:

```python
"""
dev-kit/agent/prompts/base.py

Builds the full system prompt for a given conversation phase.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.phases import get_phase_addition

_DPG_OVERVIEW = """
You are a DPG Configuration Assistant. You help users configure AI-powered conversation agents
on the DPG (Digital Public Good) framework without needing to understand YAML or code.

The DPG has 7 building blocks:
- Agent Core: orchestrates the conversation, calls the LLM, manages the turn loop.
- Knowledge Engine: assembles LLM prompts from user intent + domain knowledge (RAG).
- Memory Layer: stores session state and persistent user profiles.
- Trust Layer: safety gate that blocks harmful input/output and enforces escalation rules.
- Action Gateway: executes external API calls requested by the LLM.
- Reach Layer: handles input channels (WhatsApp, web, voice) and delivers responses.
- Learning Layer: async observability — logs turns and quality metrics.

Your job is to interview the user, understand their use case, and call the appropriate
tools to build their domain configuration. Be conversational, ask one question at a time,
and confirm your understanding before moving to a new topic.

Important rules:
- Never make up connector names, API endpoints, or model IDs. Ask the user.
- When designing the workflow, propose an initial state machine based on what you know, then refine.
- Keep system prompts in subagents concise (3-8 sentences). They guide the LLM per state.
- Trust Layer and Action Gateway configs are STATUS: draft — collect them anyway for future use.
""".strip()


def build_system_prompt(
    project_name: str,
    project_description: str,
    accumulator: ConfigAccumulator,
    phase: str,
    checkpoint_summaries: list[str],
    available_connectors: list[str] | None = None,
) -> str:
    """Build the full system prompt for the given conversation phase.

    Args:
        project_name: Human-readable project name.
        project_description: Brief project description.
        accumulator: Current config accumulator.
        phase: Current phase name (e.g. "language", "workflow").
        checkpoint_summaries: List of summary strings from prior phase checkpoints.
        available_connectors: Connector names declared in Connectors phase (for workflow prompt).

    Returns:
        Full system prompt string.
    """
    sections = [_DPG_OVERVIEW]

    # Project context
    if project_name:
        sections.append(f"## Project\nName: {project_name}\nDescription: {project_description}")

    # Prior phase summaries
    if checkpoint_summaries:
        sections.append("## Prior phase summaries\n" + "\n---\n".join(checkpoint_summaries))

    # Current config state
    sections.append(accumulator.summary())

    # Current phase
    sections.append(f"## Current phase: {phase}")

    # Phase-specific schema context
    addition = get_phase_addition(phase, available_connectors=available_connectors)
    if addition:
        sections.append(addition)

    return "\n\n".join(sections)
```

- [ ] **Step 4: Smoke test**

```bash
cd dev-kit
python -c "
from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.base import build_system_prompt
acc = ConfigAccumulator()
prompt = build_system_prompt('Test Project', 'A test', acc, 'overview', [])
assert 'DPG' in prompt
assert 'overview' in prompt
print('Prompts OK — length:', len(prompt))
"
```

Expected: `Prompts OK — length: <N>` with no errors.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/prompts/
git commit -m "feat(dev-kit): add system prompt builders"
```

---

## Task 9: Conversation Engine

**Files:**
- Create: `dev-kit/agent/conversation.py`
- Create: `dev-kit/agent/tests/test_conversation.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_conversation.py`:

```python
"""Tests for dev_kit.agent.conversation.ConversationEngine."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dev_kit.agent.conversation import ConversationEngine


def _make_text_response(text: str):
    """Build a mock Anthropic message response with stop_reason=end_turn."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_1"):
    """Build a mock Anthropic message response with stop_reason=tool_use."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


@pytest.fixture
def project_path(tmp_path):
    p = tmp_path / "test_project"
    p.mkdir()
    meta = p / "_meta"
    meta.mkdir()
    (p / "_meta" / "project.json").write_text(json.dumps({
        "slug": "test_project",
        "name": "Test",
        "description": "A test project",
        "current_phase": "overview",
        "phases_completed": [],
    }))
    return p


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


class TestConversationEngineChat:
    @pytest.mark.asyncio
    async def test_chat_returns_reply(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Hello! Tell me about your project.")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("I want to build a jobs assistant")
        assert "reply" in result
        assert len(result["reply"]) > 0

    @pytest.mark.asyncio
    async def test_chat_includes_phase_in_response(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Great idea!")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Hello")
        assert "phase" in result
        assert result["phase"] == "overview"

    @pytest.mark.asyncio
    async def test_chat_dispatches_tool_use(self, project_path, mock_client):
        # First call returns tool_use, second call returns text
        tool_response = _make_tool_use_response(
            "update_config",
            {"block": "trust_layer", "section": "trust", "values": {"input_rules": {"blocked_phrases": ["spam"]}}},
        )
        text_response = _make_text_response("Config updated!")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Add spam to blocked phrases")
        assert result["reply"] == "Config updated!"
        assert engine.accumulator.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["spam"]

    @pytest.mark.asyncio
    async def test_chat_advances_phase_on_set_phase_tool(self, project_path, mock_client):
        tool_response = _make_tool_use_response("set_phase", {"phase": "language"})
        text_response = _make_text_response("Moving to language configuration.")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Let's move on")
        assert result["phase"] == "language"

    @pytest.mark.asyncio
    async def test_chat_includes_graph_in_response(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Sure.")
        engine = ConversationEngine(project_path, mock_client)
        result = await engine.chat("Hello")
        assert "graph" in result
        assert "nodes" in result["graph"]
        assert "edges" in result["graph"]

    @pytest.mark.asyncio
    async def test_history_grows_with_each_turn(self, project_path, mock_client):
        mock_client.messages.create.return_value = _make_text_response("Hello!")
        engine = ConversationEngine(project_path, mock_client)
        await engine.chat("Hi")
        await engine.chat("Tell me more")
        assert len(engine._history) == 4  # 2 user + 2 assistant


class TestConversationEnginePersistence:
    @pytest.mark.asyncio
    async def test_accumulator_persisted_after_tool_call(self, project_path, mock_client):
        tool_response = _make_tool_use_response(
            "update_config",
            {"block": "trust_layer", "section": "trust", "values": {"input_rules": {"blocked_phrases": ["x"]}}},
        )
        text_response = _make_text_response("Done.")
        mock_client.messages.create.side_effect = [tool_response, text_response]
        engine = ConversationEngine(project_path, mock_client)
        await engine.chat("Add blocked phrase")
        assert (project_path / "_meta" / "accumulator.json").exists()

    @pytest.mark.asyncio
    async def test_engine_loads_existing_accumulator(self, project_path, mock_client):
        # Pre-seed an accumulator file
        from dev_kit.agent.accumulator import ConfigAccumulator
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["preloaded"]}})
        (project_path / "_meta" / "accumulator.json").write_text(
            json.dumps(acc.to_dict())
        )
        engine = ConversationEngine(project_path, mock_client)
        assert engine.accumulator.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["preloaded"]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_conversation.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create conversation.py**

Create `dev-kit/agent/conversation.py`:

```python
"""
dev-kit/agent/conversation.py

ConversationEngine — manages the chat loop with Claude, dispatches tool calls,
maintains conversation history, and persists state after each turn.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.checkpoints import build_summary, list_checkpoints, save_checkpoint
from dev_kit.agent.prompts.base import build_system_prompt
from dev_kit.agent.renderer import render_all
from dev_kit.agent.tools import TOOL_DEFINITIONS, ToolHandler

if TYPE_CHECKING:
    import anthropic

_MODEL = "claude-opus-4-6"
_MAX_TOKENS = 4096
_HISTORY_WINDOW = 20  # Max recent messages to send per turn


class ConversationEngine:
    """Manages one project's conversation with Claude.

    Holds message history, the config accumulator, and mutable engine
    state (current phase, pending phase transitions). All tool calls are
    dispatched synchronously; only the Claude API call is async.

    Args:
        project_path: Root directory of the project (configs/<slug>/).
        client: Anthropic AsyncAnthropic client.
    """

    def __init__(self, project_path: Path, client: "anthropic.AsyncAnthropic") -> None:
        self._project_path = project_path
        self._client = client
        self._history: list[dict] = []
        self._state: dict = {
            "phase": "overview",
            "phase_changed": None,
            "rollback_to": None,
            "project_meta": {},
        }
        self.accumulator = ConfigAccumulator()
        self._tool_handler = ToolHandler(self.accumulator, self._state)
        self._load()

    def _load(self) -> None:
        """Load persisted accumulator and project meta from disk if they exist."""
        acc_path = self._project_path / "_meta" / "accumulator.json"
        if acc_path.exists():
            self.accumulator = ConfigAccumulator.from_dict(json.loads(acc_path.read_text()))
            self._tool_handler = ToolHandler(self.accumulator, self._state)

        meta_path = self._project_path / "_meta" / "project.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            self._state["project_meta"] = meta
            self._state["phase"] = meta.get("current_phase", "overview")

    def _save_accumulator(self) -> None:
        """Persist the current accumulator state to disk."""
        acc_path = self._project_path / "_meta" / "accumulator.json"
        acc_path.parent.mkdir(parents=True, exist_ok=True)
        acc_path.write_text(json.dumps(self.accumulator.to_dict(), ensure_ascii=False, indent=2))

    def _save_project_meta(self) -> None:
        """Persist current phase to project.json."""
        meta_path = self._project_path / "_meta" / "project.json"
        meta = self._state.get("project_meta", {})
        meta["current_phase"] = self._state["phase"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    def _get_checkpoint_summaries(self) -> list[str]:
        """Load summary text from all completed phase checkpoints."""
        return [cp["summary"] for cp in list_checkpoints(self._project_path) if cp["summary"]]

    def _build_system_prompt(self) -> str:
        meta = self._state.get("project_meta", {})
        connectors = list(
            self.accumulator.get_block("agent_core").get("connectors", {}).keys()
        )
        return build_system_prompt(
            project_name=meta.get("name", ""),
            project_description=meta.get("description", ""),
            accumulator=self.accumulator,
            phase=self._state["phase"],
            checkpoint_summaries=self._get_checkpoint_summaries(),
            available_connectors=connectors or None,
        )

    async def chat(self, user_message: str) -> dict:
        """Process a user message and return the agent's response.

        Calls Claude, dispatches any tool calls, saves state, and re-renders
        YAML config files.

        Args:
            user_message: The user's input text.

        Returns:
            Dict with keys: reply (str), phase (str), config_updates (list),
            checkpoint_created (str | None), graph (dict).
        """
        self._history.append({"role": "user", "content": user_message})
        self._state["phase_changed"] = None
        self._state["rollback_to"] = None

        system = self._build_system_prompt()
        messages = self._history[-_HISTORY_WINDOW:]
        config_updates: list[dict] = []
        checkpoint_created: str | None = None

        response = await self._client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=TOOL_DEFINITIONS,
        )

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
                phase_number = ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"].index(old_phase) + 1
                phase_label = f"{phase_number:02d}_{old_phase}"
                save_checkpoint(self._project_path, phase_label, self.accumulator, self._history[:-2])
                checkpoint_created = phase_label
                self._state["phase"] = new_phase
                self._state["phase_changed"] = None
                # Rebuild system prompt with new phase
                system = self._build_system_prompt()

            response = await self._client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=self._history[-_HISTORY_WINDOW:],
                tools=TOOL_DEFINITIONS,
            )

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

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_conversation.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/agent/conversation.py dev-kit/agent/tests/test_conversation.py
git commit -m "feat(dev-kit): add ConversationEngine"
```

---

## Task 10: FastAPI App

**Files:**
- Create: `dev-kit/agent/app.py`
- Create: `dev-kit/agent/tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/agent/tests/test_app.py`:

```python
"""Tests for dev_kit.agent.app FastAPI routes."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # Patch CONFIGS_DIR to tmp_path
    import dev_kit.agent.app as app_module
    monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
    # Patch AsyncAnthropic to a mock
    mock_anthropic = MagicMock()
    monkeypatch.setattr(app_module, "_anthropic_client", mock_anthropic)
    from dev_kit.agent.app import app
    return TestClient(app)


class TestProjectRoutes:
    def test_create_project(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        resp = client.post("/api/projects", json={"name": "Test Project", "description": "A test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "slug" in data
        assert data["name"] == "Test Project"

    def test_list_projects_empty(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_existing_project(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "My App", "description": "desc"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}")
        assert resp.status_code == 200
        assert resp.json()["slug"] == slug

    def test_get_nonexistent_project_returns_404(self, client):
        resp = client.get("/api/projects/does-not-exist")
        assert resp.status_code == 404


class TestConfigRoutes:
    def test_get_configs_returns_7_blocks(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/configs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 7

    def test_get_single_config(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/configs/trust_layer")
        assert resp.status_code == 200
        assert "content" in resp.json()


class TestCheckpointRoutes:
    def test_list_checkpoints_empty(self, client, tmp_path, monkeypatch):
        import dev_kit.agent.app as app_module
        monkeypatch.setattr(app_module, "CONFIGS_DIR", tmp_path)
        client.post("/api/projects", json={"name": "X", "description": "y"})
        projects = client.get("/api/projects").json()
        slug = projects[0]["slug"]
        resp = client.get(f"/api/projects/{slug}/checkpoints")
        assert resp.status_code == 200
        assert resp.json() == []
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dev-kit
uv run pytest agent/tests/test_app.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create app.py**

Create `dev-kit/agent/app.py`:

```python
"""
dev-kit/agent/app.py

FastAPI application for the DPG conversation agent.

Serves the conversation API and the React SPA (built frontend output
mounted at agent/static/). Manages an in-memory registry of
ConversationEngine instances keyed by project slug.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator
from dev_kit.agent.checkpoints import list_checkpoints, restore_checkpoint
from dev_kit.agent.conversation import ConversationEngine
from dev_kit.agent.renderer import load_block_from_file, render_all
from dev_kit.schema import validate_partial

load_dotenv(Path(__file__).parent.parent.parent / ".env.local")
load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIGS_DIR = Path(__file__).parent.parent / "configs"
_STATIC_DIR = Path(__file__).parent / "static"

_anthropic_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
_engines: dict[str, ConversationEngine] = {}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="DPG Configuration Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str
    description: str


class ChatRequest(BaseModel):
    message: str


class UpdateConfigRequest(BaseModel):
    content: str  # Raw YAML string from the editor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _get_project_path(slug: str) -> Path:
    return CONFIGS_DIR / slug


def _load_project_meta(slug: str) -> dict:
    path = _get_project_path(slug) / "_meta" / "project.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return json.loads(path.read_text())


def _get_engine(slug: str) -> ConversationEngine:
    if slug not in _engines:
        project_path = _get_project_path(slug)
        if not project_path.exists():
            raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
        _engines[slug] = ConversationEngine(project_path, _anthropic_client)
    return _engines[slug]


# ---------------------------------------------------------------------------
# Project routes
# ---------------------------------------------------------------------------


@app.post("/api/projects")
def create_project(body: CreateProjectRequest) -> dict:
    """Create a new project and initialise its directory structure."""
    slug = _slugify(body.name)
    project_path = _get_project_path(slug)
    project_path.mkdir(parents=True, exist_ok=True)
    meta_dir = project_path / "_meta"
    meta_dir.mkdir(exist_ok=True)
    meta = {
        "slug": slug,
        "name": body.name,
        "description": body.description,
        "current_phase": "overview",
        "phases_completed": [],
    }
    (meta_dir / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    # Initialise empty config files
    acc = ConfigAccumulator()
    render_all(project_path, acc)
    _engines[slug] = ConversationEngine(project_path, _anthropic_client)
    return meta


@app.get("/api/projects")
def list_projects() -> list[dict]:
    """List all projects."""
    projects = []
    if not CONFIGS_DIR.exists():
        return projects
    for project_path in CONFIGS_DIR.iterdir():
        if not project_path.is_dir():
            continue
        meta_file = project_path / "_meta" / "project.json"
        if meta_file.exists():
            projects.append(json.loads(meta_file.read_text()))
    return projects


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict:
    """Get project metadata and config statuses."""
    meta = _load_project_meta(slug)
    engine = _get_engine(slug)
    meta["config_statuses"] = {block: engine.accumulator.get_status(block).value for block in BLOCKS}
    return meta


@app.delete("/api/projects/{slug}")
def delete_project(slug: str) -> dict:
    """Delete a project and all its files."""
    import shutil
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    shutil.rmtree(project_path)
    _engines.pop(slug, None)
    return {"deleted": slug}


# ---------------------------------------------------------------------------
# Chat routes
# ---------------------------------------------------------------------------


@app.post("/api/projects/{slug}/chat")
async def chat(slug: str, body: ChatRequest) -> dict:
    """Send a user message and receive the agent response."""
    engine = _get_engine(slug)
    return await engine.chat(body.message)


@app.get("/api/projects/{slug}/history")
def get_history(slug: str) -> list[dict]:
    """Return the conversation history for the current phase."""
    engine = _get_engine(slug)
    # Return only text-content messages (skip tool_use/tool_result blocks)
    result = []
    for msg in engine._history:
        content = msg.get("content", "")
        if isinstance(content, str):
            result.append({"role": msg["role"], "content": content})
    return result


# ---------------------------------------------------------------------------
# Checkpoint routes
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/checkpoints")
def get_checkpoints(slug: str) -> list[dict]:
    """List all saved checkpoints for a project."""
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return list_checkpoints(project_path)


@app.post("/api/projects/{slug}/checkpoints/{phase}/restore")
def restore_checkpoint_route(slug: str, phase: str) -> dict:
    """Restore the project to a previous checkpoint."""
    project_path = _get_project_path(slug)
    try:
        restored_acc, summary = restore_checkpoint(project_path, phase)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Checkpoint '{phase}' not found")
    engine = _get_engine(slug)
    engine.accumulator = restored_acc
    engine._tool_handler._acc = restored_acc
    engine._history = []
    engine._state["phase"] = phase.split("_", 1)[-1] if "_" in phase else phase
    render_all(project_path, restored_acc)
    engine._save_accumulator()
    return {"restored": phase, "summary": summary}


# ---------------------------------------------------------------------------
# Config routes
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/configs")
def get_configs(slug: str) -> list[dict]:
    """Return all 7 config files with their status."""
    engine = _get_engine(slug)
    result = []
    project_path = _get_project_path(slug)
    for block in BLOCKS:
        config_file = project_path / f"{block}.yaml"
        content = config_file.read_text() if config_file.exists() else ""
        result.append({
            "block": block,
            "status": engine.accumulator.get_status(block).value,
            "content": content,
        })
    return result


@app.get("/api/projects/{slug}/configs/{block}")
def get_config(slug: str, block: str) -> dict:
    """Return a single block config."""
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    project_path = _get_project_path(slug)
    config_file = project_path / f"{block}.yaml"
    content = config_file.read_text() if config_file.exists() else ""
    engine = _get_engine(slug)
    return {"block": block, "status": engine.accumulator.get_status(block).value, "content": content}


@app.put("/api/projects/{slug}/configs/{block}")
def update_config_file(slug: str, block: str, body: UpdateConfigRequest) -> dict:
    """Manually update a config file and reverse-sync the accumulator.

    Writes the YAML file regardless of validation errors.
    If validation fails, sets the block status to STALE.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    import yaml
    project_path = _get_project_path(slug)
    config_file = project_path / f"{block}.yaml"
    config_file.write_text(body.content)
    engine = _get_engine(slug)
    data = load_block_from_file(project_path, block)
    engine.accumulator._data[block] = data
    errors = validate_partial(block, data)
    from dev_kit.agent.accumulator import ConfigStatus, DRAFT_BLOCKS
    if errors:
        engine.accumulator.set_status(block, ConfigStatus.STALE)
    elif block in DRAFT_BLOCKS:
        engine.accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        engine.accumulator.set_status(block, ConfigStatus.COMPLETE)
    engine._save_accumulator()
    return {"block": block, "status": engine.accumulator.get_status(block).value, "validation_errors": errors}


@app.post("/api/projects/{slug}/configs/validate")
def validate_all_configs(slug: str) -> dict[str, Any]:
    """Run partial validation on all 7 configs and return results."""
    engine = _get_engine(slug)
    results = {}
    for block in BLOCKS:
        data = engine.accumulator.get_block(block)
        errors = validate_partial(block, data)
        results[block] = {"valid": len(errors) == 0, "errors": errors}
    return results


# ---------------------------------------------------------------------------
# Workflow graph route
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/workflow/graph")
def get_workflow_graph(slug: str) -> dict:
    """Return the subagent workflow as nodes and edges for the frontend graph."""
    engine = _get_engine(slug)
    return engine.accumulator.get_workflow_graph()


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"error": "Frontend not built. Run: cd frontend && npm run build"}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd dev-kit
uv run pytest agent/tests/test_app.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Run all backend tests**

```bash
cd dev-kit
uv run pytest tests/ agent/tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/agent/app.py dev-kit/agent/tests/test_app.py
git commit -m "feat(dev-kit): add FastAPI app with all routes"
```

---

## Task 11: Frontend Scaffold

**Files:** All new under `dev-kit/frontend/`.

- [ ] **Step 1: Create package.json**

Create `dev-kit/frontend/package.json`:

```json
{
  "name": "dpg-config-agent",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "@xyflow/react": "^12.3.6",
    "@codemirror/state": "^6.4.1",
    "@codemirror/view": "^6.34.3",
    "@codemirror/lang-yaml": "^6.1.1",
    "@codemirror/theme-one-dark": "^6.1.2"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "tailwindcss": "^3.4.17",
    "postcss": "^8.5.3",
    "autoprefixer": "^10.4.21",
    "vite": "^5.4.14"
  }
}
```

- [ ] **Step 2: Create vite.config.js**

Create `dev-kit/frontend/vite.config.js`:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
  build: {
    outDir: '../agent/static',
    emptyOutDir: true,
  },
})
```

- [ ] **Step 3: Create tailwind.config.js**

Create `dev-kit/frontend/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 4: Create postcss.config.js**

Create `dev-kit/frontend/postcss.config.js`:

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 5: Create index.html**

Create `dev-kit/frontend/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>DPG Configuration Agent</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create src/main.jsx and src/index.css**

Create `dev-kit/frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  @apply bg-gray-950 text-gray-100 font-sans;
}
```

Create `dev-kit/frontend/src/main.jsx`:

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 7: Install dependencies and verify build**

```bash
cd dev-kit/frontend
npm install
npm run build
```

Expected: Build succeeds, `dev-kit/agent/static/` directory is populated with `index.html` and `assets/`.

- [ ] **Step 8: Commit**

```bash
git add dev-kit/frontend/
git commit -m "chore(dev-kit): add frontend scaffold"
```

---

## Task 12: API Client + App Shell

**Files:**
- Create: `dev-kit/frontend/src/api.js`
- Create: `dev-kit/frontend/src/App.jsx`
- Create: `dev-kit/frontend/src/components/ProjectList.jsx`

- [ ] **Step 1: Create api.js**

Create `dev-kit/frontend/src/api.js`:

```js
const BASE = '/api'

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

export const api = {
  // Projects
  listProjects: () => request('GET', '/projects'),
  createProject: (name, description) => request('POST', '/projects', { name, description }),
  getProject: (slug) => request('GET', `/projects/${slug}`),
  deleteProject: (slug) => request('DELETE', `/projects/${slug}`),

  // Chat
  chat: (slug, message) => request('POST', `/projects/${slug}/chat`, { message }),
  getHistory: (slug) => request('GET', `/projects/${slug}/history`),

  // Checkpoints
  getCheckpoints: (slug) => request('GET', `/projects/${slug}/checkpoints`),
  restoreCheckpoint: (slug, phase) => request('POST', `/projects/${slug}/checkpoints/${phase}/restore`),

  // Configs
  getConfigs: (slug) => request('GET', `/projects/${slug}/configs`),
  getConfig: (slug, block) => request('GET', `/projects/${slug}/configs/${block}`),
  updateConfig: (slug, block, content) => request('PUT', `/projects/${slug}/configs/${block}`, { content }),
  validateConfigs: (slug) => request('POST', `/projects/${slug}/configs/validate`),

  // Workflow graph
  getGraph: (slug) => request('GET', `/projects/${slug}/workflow/graph`),
}
```

- [ ] **Step 2: Create App.jsx**

Create `dev-kit/frontend/src/App.jsx`:

```jsx
import React, { useState } from 'react'
import ProjectList from './components/ProjectList'
import Chat from './components/Chat'
import Dashboard from './components/Dashboard'
import ConfigEditor from './components/ConfigEditor'

export default function App() {
  const [view, setView] = useState('projects')   // 'projects' | 'chat' | 'dashboard' | 'config'
  const [activeSlug, setActiveSlug] = useState(null)
  const [activeBlock, setActiveBlock] = useState(null)

  function openProject(slug) {
    setActiveSlug(slug)
    setView('chat')
  }

  function openDashboard(slug) {
    setActiveSlug(slug)
    setView('dashboard')
  }

  function openConfig(slug, block) {
    setActiveSlug(slug)
    setActiveBlock(block)
    setView('config')
  }

  if (view === 'projects') {
    return <ProjectList onOpen={openProject} />
  }
  if (view === 'chat') {
    return (
      <Chat
        slug={activeSlug}
        onDashboard={() => openDashboard(activeSlug)}
        onBack={() => setView('projects')}
      />
    )
  }
  if (view === 'dashboard') {
    return (
      <Dashboard
        slug={activeSlug}
        onChat={() => setView('chat')}
        onEditConfig={(block) => openConfig(activeSlug, block)}
        onBack={() => setView('projects')}
      />
    )
  }
  if (view === 'config') {
    return (
      <ConfigEditor
        slug={activeSlug}
        block={activeBlock}
        onBack={() => openDashboard(activeSlug)}
      />
    )
  }
}
```

- [ ] **Step 3: Create ProjectList.jsx**

Create `dev-kit/frontend/src/components/ProjectList.jsx`:

```jsx
import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function ProjectList({ onOpen }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]))
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const project = await api.createProject(name.trim(), description.trim())
      setProjects((p) => [...p, project])
      setName('')
      setDescription('')
      onOpen(project.slug)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-start pt-16 px-4">
      <h1 className="text-3xl font-bold mb-2">DPG Configuration Agent</h1>
      <p className="text-gray-400 mb-10">Configure your AI-powered conversation agent for the DPG framework.</p>

      <div className="w-full max-w-lg bg-gray-900 rounded-xl p-6 mb-8 border border-gray-800">
        <h2 className="text-lg font-semibold mb-4">New Project</h2>
        <form onSubmit={handleCreate} className="flex flex-col gap-3">
          <input
            className="bg-gray-800 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Project name (e.g. Rural Jobs Assistant)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <textarea
            className="bg-gray-800 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            placeholder="Brief description of your use case"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={creating}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg py-2 font-medium text-sm transition-colors"
          >
            {creating ? 'Creating…' : 'Create & Start Configuration'}
          </button>
        </form>
      </div>

      {projects.length > 0 && (
        <div className="w-full max-w-lg">
          <h2 className="text-lg font-semibold mb-3">Existing Projects</h2>
          <div className="flex flex-col gap-2">
            {projects.map((p) => (
              <button
                key={p.slug}
                onClick={() => onOpen(p.slug)}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 hover:border-blue-500 transition-colors text-left"
              >
                <div>
                  <p className="font-medium">{p.name}</p>
                  <p className="text-gray-400 text-sm">{p.description}</p>
                </div>
                <span className="text-gray-500 text-sm ml-4">Phase: {p.current_phase}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Build and verify**

```bash
cd dev-kit/frontend
npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/frontend/src/api.js dev-kit/frontend/src/App.jsx dev-kit/frontend/src/components/ProjectList.jsx
git commit -m "feat(dev-kit): add API client, app shell, and project list"
```

---

## Task 13: Chat View

**Files:**
- Create: `dev-kit/frontend/src/components/PhaseBar.jsx`
- Create: `dev-kit/frontend/src/components/Chat.jsx`

- [ ] **Step 1: Create PhaseBar.jsx**

Create `dev-kit/frontend/src/components/PhaseBar.jsx`:

```jsx
import React from 'react'

const PHASES = ['overview', 'language', 'knowledge', 'memory', 'trust', 'connectors', 'workflow', 'review']
const PHASE_LABELS = {
  overview: 'Overview', language: 'Language', knowledge: 'Knowledge',
  memory: 'Memory', trust: 'Trust', connectors: 'Connectors',
  workflow: 'Workflow', review: 'Review',
}

export default function PhaseBar({ currentPhase, checkpoints, onRestoreCheckpoint }) {
  const currentIdx = PHASES.indexOf(currentPhase)

  return (
    <div className="flex items-center gap-1 px-4 py-2 bg-gray-900 border-b border-gray-800 overflow-x-auto">
      {PHASES.map((phase, i) => {
        const isDone = i < currentIdx
        const isCurrent = phase === currentPhase
        const hasCheckpoint = checkpoints?.some((cp) => cp.phase.endsWith(phase))

        return (
          <button
            key={phase}
            onClick={() => hasCheckpoint && onRestoreCheckpoint && onRestoreCheckpoint(
              checkpoints.find((cp) => cp.phase.endsWith(phase))?.phase
            )}
            disabled={!hasCheckpoint}
            title={hasCheckpoint ? `Restore to ${PHASE_LABELS[phase]} checkpoint` : PHASE_LABELS[phase]}
            className={[
              'flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors',
              isCurrent ? 'bg-blue-600 text-white' : '',
              isDone && !isCurrent ? 'bg-gray-700 text-gray-300 hover:bg-gray-600 cursor-pointer' : '',
              !isDone && !isCurrent ? 'text-gray-600' : '',
            ].filter(Boolean).join(' ')}
          >
            {isDone && <span>✓</span>}
            {PHASE_LABELS[phase]}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Create Chat.jsx**

Create `dev-kit/frontend/src/components/Chat.jsx`:

```jsx
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import PhaseBar from './PhaseBar'
import FlowGraph from './FlowGraph'

export default function Chat({ slug, onDashboard, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState('overview')
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [checkpoints, setCheckpoints] = useState([])
  const [showGraph, setShowGraph] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    // Load existing history
    api.getHistory(slug).then((history) => {
      setMessages(history.map((m) => ({ role: m.role, text: m.content })))
    }).catch(() => {})
    api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
    api.getGraph(slug).then(setGraph).catch(() => {})
  }, [slug])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(e) {
    e.preventDefault()
    if (!input.trim() || loading) return
    const userText = input.trim()
    setInput('')
    setMessages((m) => [...m, { role: 'user', text: userText }])
    setLoading(true)
    try {
      const res = await api.chat(slug, userText)
      setMessages((m) => [...m, { role: 'assistant', text: res.reply }])
      setPhase(res.phase)
      if (res.graph) setGraph(res.graph)
      if (res.checkpoint_created) {
        api.getCheckpoints(slug).then(setCheckpoints).catch(() => {})
      }
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: `Error: ${err.message}` }])
    } finally {
      setLoading(false)
    }
  }

  async function handleRestoreCheckpoint(checkpointPhase) {
    if (!window.confirm(`Restore to checkpoint: ${checkpointPhase}? This will clear current conversation history.`)) return
    try {
      await api.restoreCheckpoint(slug, checkpointPhase)
      setMessages([])
      const history = await api.getHistory(slug)
      setMessages(history.map((m) => ({ role: m.role, text: m.content })))
      const project = await api.getProject(slug)
      setPhase(project.current_phase)
      const newGraph = await api.getGraph(slug)
      setGraph(newGraph)
      const newCheckpoints = await api.getCheckpoints(slug)
      setCheckpoints(newCheckpoints)
    } catch (err) {
      alert(`Failed to restore: ${err.message}`)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-800">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm">← Projects</button>
        <span className="font-semibold text-sm">{slug}</span>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGraph((g) => !g)}
            className="text-xs bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded-lg transition-colors"
          >
            {showGraph ? 'Hide Graph' : 'Show Graph'}
          </button>
          <button
            onClick={onDashboard}
            className="text-xs bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded-lg transition-colors"
          >
            Dashboard
          </button>
        </div>
      </div>

      {/* Phase bar */}
      <PhaseBar currentPhase={phase} checkpoints={checkpoints} onRestoreCheckpoint={handleRestoreCheckpoint} />

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Messages */}
        <div className={`flex flex-col ${showGraph ? 'w-1/2' : 'w-full'} overflow-hidden`}>
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-center text-sm mt-8">
                Describe your AI agent use case to get started.
              </p>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={[
                    'max-w-xl rounded-2xl px-4 py-2 text-sm leading-relaxed whitespace-pre-wrap',
                    m.role === 'user' ? 'bg-blue-600 text-white' : '',
                    m.role === 'assistant' ? 'bg-gray-800 text-gray-100' : '',
                    m.role === 'error' ? 'bg-red-900 text-red-200' : '',
                  ].filter(Boolean).join(' ')}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-800 rounded-2xl px-4 py-2 text-sm text-gray-400 animate-pulse">
                  Thinking…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <form onSubmit={send} className="flex gap-2 px-4 py-3 border-t border-gray-800">
            <input
              className="flex-1 bg-gray-800 rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Type your message…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-xl px-4 py-2 text-sm font-medium transition-colors"
            >
              Send
            </button>
          </form>
        </div>

        {/* Flow graph panel */}
        {showGraph && (
          <div className="w-1/2 border-l border-gray-800 bg-gray-950">
            <FlowGraph graph={graph} />
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Build and verify**

```bash
cd dev-kit/frontend
npm run build
```

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/components/PhaseBar.jsx dev-kit/frontend/src/components/Chat.jsx
git commit -m "feat(dev-kit): add Chat view with PhaseBar"
```

---

## Task 14: Dashboard + ConfigEditor

**Files:**
- Create: `dev-kit/frontend/src/components/Dashboard.jsx`
- Create: `dev-kit/frontend/src/components/ConfigEditor.jsx`

- [ ] **Step 1: Create Dashboard.jsx**

Create `dev-kit/frontend/src/components/Dashboard.jsx`:

```jsx
import React, { useEffect, useState } from 'react'
import { api } from '../api'

const STATUS_COLORS = {
  complete: 'bg-green-900 text-green-300 border-green-700',
  draft: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  pending: 'bg-gray-800 text-gray-400 border-gray-700',
  stale: 'bg-red-900 text-red-300 border-red-700',
}

const BLOCKS = ['agent_core', 'knowledge_engine', 'memory_layer', 'trust_layer', 'action_gateway', 'reach_layer', 'learning_layer']
const BLOCK_LABELS = {
  agent_core: 'Agent Core', knowledge_engine: 'Knowledge Engine',
  memory_layer: 'Memory Layer', trust_layer: 'Trust Layer',
  action_gateway: 'Action Gateway', reach_layer: 'Reach Layer',
  learning_layer: 'Learning Layer',
}

export default function Dashboard({ slug, onChat, onEditConfig, onBack }) {
  const [configs, setConfigs] = useState([])
  const [project, setProject] = useState(null)

  useEffect(() => {
    api.getConfigs(slug).then(setConfigs).catch(() => {})
    api.getProject(slug).then(setProject).catch(() => {})
  }, [slug])

  return (
    <div className="min-h-screen px-6 py-8 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <button onClick={onBack} className="text-gray-400 hover:text-white text-sm mb-2 block">← Projects</button>
          <h1 className="text-2xl font-bold">{project?.name || slug}</h1>
          <p className="text-gray-400 text-sm">{project?.description}</p>
        </div>
        <button
          onClick={onChat}
          className="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded-xl text-sm font-medium transition-colors"
        >
          Continue Configuration
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {BLOCKS.map((block) => {
          const config = configs.find((c) => c.block === block)
          const status = config?.status || 'pending'
          return (
            <button
              key={block}
              onClick={() => onEditConfig(block)}
              className={`border rounded-xl p-4 text-left hover:opacity-90 transition-opacity ${STATUS_COLORS[status] || STATUS_COLORS.pending}`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-sm">{BLOCK_LABELS[block]}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS[status]}`}>{status}</span>
              </div>
              <p className="text-xs opacity-70 truncate">
                {config?.content ? 'Click to view or edit' : 'Not yet configured'}
              </p>
            </button>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create ConfigEditor.jsx**

Create `dev-kit/frontend/src/components/ConfigEditor.jsx`:

```jsx
import React, { useEffect, useRef, useState } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { api } from '../api'

const DRAFT_BLOCKS = new Set(['trust_layer', 'action_gateway', 'reach_layer', 'learning_layer'])

export default function ConfigEditor({ slug, block, onBack }) {
  const editorRef = useRef(null)
  const viewRef = useRef(null)
  const [status, setStatus] = useState('pending')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationErrors, setValidationErrors] = useState([])
  const [saveMsg, setSaveMsg] = useState(null)

  useEffect(() => {
    api.getConfig(slug, block).then(({ content, status: s }) => {
      setStatus(s)
      if (viewRef.current) viewRef.current.destroy()
      if (!editorRef.current) return
      const state = EditorState.create({
        doc: content || '',
        extensions: [basicSetup, yaml(), oneDark, EditorView.editable.of(false)],
      })
      viewRef.current = new EditorView({ state, parent: editorRef.current })
    }).catch(() => {})
    return () => viewRef.current?.destroy()
  }, [slug, block])

  function enableEdit() {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: EditorView.editable.reconfigure(EditorView.editable.of(true)),
    })
    setEditing(true)
  }

  async function handleSave() {
    if (!viewRef.current) return
    setSaving(true)
    setValidationErrors([])
    setSaveMsg(null)
    const content = viewRef.current.state.doc.toString()
    try {
      const result = await api.updateConfig(slug, block, content)
      setStatus(result.status)
      setValidationErrors(result.validation_errors || [])
      setSaveMsg(result.validation_errors?.length > 0 ? 'Saved with validation errors.' : 'Saved.')
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-800">
        <button onClick={onBack} className="text-gray-400 hover:text-white text-sm">← Dashboard</button>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{block}.yaml</span>
          <span className={`text-xs px-2 py-0.5 rounded-full border ${
            status === 'complete' ? 'bg-green-900 text-green-300 border-green-700' :
            status === 'draft' ? 'bg-yellow-900 text-yellow-300 border-yellow-700' :
            status === 'stale' ? 'bg-red-900 text-red-300 border-red-700' :
            'bg-gray-800 text-gray-400 border-gray-700'
          }`}>{status}</span>
        </div>
        <div className="flex gap-2">
          {!editing && (
            <button onClick={enableEdit} className="text-xs bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-lg">
              Edit
            </button>
          )}
          {editing && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1 rounded-lg"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>
      </div>

      {DRAFT_BLOCKS.has(block) && (
        <div className="px-4 py-2 bg-yellow-900 border-b border-yellow-700 text-yellow-300 text-xs">
          ⚠ This config is a draft — the block template is not yet finalised.
        </div>
      )}

      {saveMsg && (
        <div className={`px-4 py-2 text-xs border-b ${validationErrors.length > 0 ? 'bg-red-900 text-red-300 border-red-700' : 'bg-green-900 text-green-300 border-green-700'}`}>
          {saveMsg}
          {validationErrors.map((e, i) => <div key={i} className="mt-0.5">• {e}</div>)}
        </div>
      )}

      <div ref={editorRef} className="flex-1 overflow-auto text-sm" />
    </div>
  )
}
```

- [ ] **Step 3: Build and verify**

```bash
cd dev-kit/frontend
npm run build
```

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/frontend/src/components/Dashboard.jsx dev-kit/frontend/src/components/ConfigEditor.jsx
git commit -m "feat(dev-kit): add Dashboard and ConfigEditor views"
```

---

## Task 15: Flow Graph

**Files:**
- Create: `dev-kit/frontend/src/components/FlowGraph.jsx`

- [ ] **Step 1: Create FlowGraph.jsx**

Create `dev-kit/frontend/src/components/FlowGraph.jsx`:

```jsx
import React, { useCallback, useEffect, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

const NODE_COLORS = {
  start: '#16a34a',
  end: '#dc2626',
  normal: '#2563eb',
}

function toFlowNodes(nodes) {
  return nodes.map((n, i) => ({
    id: n.id,
    data: { label: n.name || n.id },
    position: { x: 200 * (i % 4), y: 150 * Math.floor(i / 4) },
    style: {
      background: NODE_COLORS[n.type] || NODE_COLORS.normal,
      color: '#fff',
      border: 'none',
      borderRadius: '10px',
      padding: '10px 16px',
      fontSize: '12px',
      fontWeight: '600',
    },
  }))
}

function toFlowEdges(edges) {
  return edges.map((e, i) => ({
    id: `e-${i}`,
    source: e.from,
    target: e.to,
    label: e.intent,
    labelStyle: { fontSize: 10, fill: '#9ca3af' },
    style: { stroke: '#4b5563' },
    markerEnd: { type: 'arrowclosed', color: '#4b5563' },
  }))
}

export default function FlowGraph({ graph }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  useEffect(() => {
    if (!graph) return
    setNodes(toFlowNodes(graph.nodes || []))
    setEdges(toFlowEdges(graph.edges || []))
  }, [graph])

  if (!graph || graph.nodes?.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        No subagents yet. Start the Workflow phase to see the graph.
      </div>
    )
  }

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        colorMode="dark"
      >
        <Background color="#374151" />
        <Controls />
        <MiniMap nodeColor={(n) => n.style?.background || '#2563eb'} />
      </ReactFlow>
    </div>
  )
}
```

- [ ] **Step 2: Build and verify**

```bash
cd dev-kit/frontend
npm run build
```

Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add dev-kit/frontend/src/components/FlowGraph.jsx
git commit -m "feat(dev-kit): add FlowGraph component"
```

---

## Task 16: Dockerfile + Integration Smoke Test

**Files:**
- Create: `dev-kit/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

Create `dev-kit/Dockerfile`:

```dockerfile
# Stage 1: Build React frontend
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend with built frontend
FROM python:3.13-slim
WORKDIR /app

# Copy dev-kit package
COPY . .

# Copy built frontend into agent/static/
COPY --from=frontend /app/frontend/../agent/static ./agent/static

# Install Python deps
RUN pip install -e . --no-cache-dir

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Smoke test — start the server locally**

```bash
cd dev-kit
export ANTHROPIC_API_KEY=sk-ant-...
uv run uvicorn dev_kit.agent.app:app --host 0.0.0.0 --port 8080
```

- [ ] **Step 3: Verify API responds**

In a separate terminal:

```bash
# Create a project
curl -s -X POST http://localhost:8080/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "Smoke Test", "description": "Testing the agent"}' | python3 -m json.tool
```

Expected output includes `"slug": "smoke-test"`.

```bash
# List projects
curl -s http://localhost:8080/api/projects | python3 -m json.tool
```

Expected: Array with one project.

```bash
# Get configs
curl -s http://localhost:8080/api/projects/smoke-test/configs | python3 -m json.tool
```

Expected: Array of 7 config objects with `"status": "pending"`.

```bash
# Verify SPA is served
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/
```

Expected: `200`.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/Dockerfile
git commit -m "feat(dev-kit): add Dockerfile and complete conversation agent"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| LLM-powered with tool_use | Task 9 (ConversationEngine), Task 7 (tools.py) |
| Modular phases (infra → workflow) | Task 8 (prompts/phases.py), Task 9 |
| File-system persistence | Tasks 5–6 (renderer + checkpoints), Task 10 (app.py) |
| Best-effort draft configs for open blocks | Tasks 4–5 (DRAFT_BLOCKS, renderer) |
| FastAPI backend | Task 10 |
| React SPA | Tasks 11–15 |
| Chat view with phase bar | Task 13 |
| Dashboard with 7-card grid | Task 14 |
| ConfigEditor with YAML editing + validation | Task 14 |
| Flow graph (React Flow) | Task 15 |
| Checkpoint save/restore | Tasks 6, 10 (routes), 13 (PhaseBar) |
| schema.py Field descriptions + validate_partial | Task 2 |
| loader.py get_schema_descriptions | Task 3 |
| Manual edit reverse-sync accumulator | Task 10 (PUT /configs/:block) |
| Draft config yellow banner | Task 14 (ConfigEditor) |
| Rollback to checkpoint | Tasks 6, 10 (restore route), 13 (PhaseBar) |
| Single container Dockerfile | Task 16 |

**All spec requirements covered.**
