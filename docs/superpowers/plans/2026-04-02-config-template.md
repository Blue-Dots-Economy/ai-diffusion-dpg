# Config Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace block-local `config/domain.yaml` files with domain-agnostic templates, and add `CONFIG_FOLDER` env var support so local dev loads domain config from `dev-kit/configs/kkb/`.

**Architecture:** Each block's `main.py` resolves the domain config path at startup: if `CONFIG_FOLDER` is set, load `$CONFIG_FOLDER/<service>.yaml`; otherwise fall back to `config/domain.yaml`. A `.env.local` at repo root sets `CONFIG_FOLDER` to the absolute path of `dev-kit/configs/kkb/`. All blocks load `.env.local` via `python-dotenv` before building the app.

**Tech Stack:** Python, PyYAML, python-dotenv, pytest

---

## File Map

| File | Change |
|---|---|
| `.env.local.example` | Create — reference for developers |
| `agent_core/config/domain.yaml` | Replace with template |
| `knowledge_engine/config/domain.yaml` | Replace with template |
| `trust_layer/config/domain.yaml` | Replace with template |
| `action_gateway/config/domain.yaml` | Replace with template |
| `memory_layer/config/domain.yaml` | Replace with template |
| `learning_layer/config/domain.yaml` | Replace with template |
| `reach_layer/config/domain.yaml` | Replace with template |
| `agent_core/main.py` | Add `.env.local` load + `CONFIG_FOLDER`-aware domain path |
| `agent_core/tests/test_main.py` | Add tests for `_domain_config_path` |
| `knowledge_engine/main.py` | Add `.env.local` load + `CONFIG_FOLDER`-aware domain path |
| `knowledge_engine/tests/test_main.py` | Add tests for `_domain_config_path` |
| `trust_layer/main.py` | Add `load_dotenv` + `CONFIG_FOLDER`-aware domain path |
| `trust_layer/tests/test_main.py` | Add tests for `_domain_config_path` |
| `memory_layer/main.py` | Add `load_dotenv` + `CONFIG_FOLDER`-aware domain path |
| `memory_layer/tests/test_main.py` | Add tests for `_domain_config_path` |
| `learning_layer/main.py` | Add `load_dotenv` + `CONFIG_FOLDER`-aware domain path |
| `learning_layer/tests/test_main.py` | Add tests for `_domain_config_path` |
| `action_gateway/main.py` | Add `load_dotenv` + `CONFIG_FOLDER`-aware domain path |
| `action_gateway/tests/test_main.py` | Add tests for `_domain_config_path` |
| `reach_layer/main.py` | Add `load_dotenv` + `CONFIG_FOLDER`-aware domain path |
| `reach_layer/tests/test_main.py` | Add tests for `_domain_config_path` |

---

## Task 1: Repo-level files

**Files:**
- Create: `.env.local.example`

Note: `.gitignore` already contains `.env.*` which covers `.env.local`. No `.gitignore` change needed.

- [ ] **Step 1: Create `.env.local.example`**

```bash
# .env.local.example — Copy to .env.local and set CONFIG_FOLDER to the
# absolute path of your domain configs folder.
# .env.local is gitignored — never commit it.
#
# Example:
CONFIG_FOLDER=/Users/yourname/projects/ai-diffusion-dpg/dev-kit/configs/kkb
```

Save as `.env.local.example` at repo root.

- [ ] **Step 2: Commit**

```bash
git add .env.local.example
git commit -m "chore: add .env.local.example for CONFIG_FOLDER local dev setup"
```

---

## Task 2: Template `config/domain.yaml` — agent_core

**Files:**
- Modify: `agent_core/config/domain.yaml`

- [ ] **Step 1: Replace with template**

Replace the entire contents of `agent_core/config/domain.yaml` with:

```yaml
# agent_core/config/domain.yaml — DPG domain configuration template.
#
# This file is the template a domain integrator fills in.
# For local dev, set CONFIG_FOLDER in .env.local to point to your domain configs folder
# instead of editing this file.
#
# All keys are required unless marked optional.

agent:
  primary_model: ""       # Required. Claude model ID for primary inference.
  fallback_model: ""      # Required. Claude model ID used if primary fails.

conversation:
  blocked_message: ""         # Required. Shown when input is blocked by Trust Layer.
  escalation_message: ""      # Required. Shown when escalating to a human agent.
  output_blocked_message: ""  # Required. Shown when LLM output is blocked by Trust Layer.
  unknown_intent_message: ""  # Required. Shown when NLU confidence is below threshold.
  termination_message: ""     # Required. Shown when session ends. LLM translates to user language.
  consent_message: ""         # Required. Shown to request user consent to save profile data.
  consent_decline_ack: ""     # Required. Acknowledgement when user declines consent.
  profile_complete_message: ""    # Required. Shown when profile collection completes.
  returning_user_greeting: ""     # Required. Greeting for users whose profile is already saved.

connectors:
  read: []    # List of read connector tool definitions. Each entry: name, description, input_schema.
  write: []   # List of write connector tool definitions. Each entry: name, description, input_schema.
  identity: []  # List of identity connector definitions.
  internal: []  # List of internal routing tools (e.g. knowledge_retrieval).

preprocessing:
  language_normalisation:
    model: ""              # Required. Claude model ID for language normalisation.
    provider: ""           # Required. "llm_native" or "bhashini".
    default_language: ""   # Required. e.g. "hindi"
    supported_languages: []  # Required. List of supported language codes.
    transliteration: true
    code_switching: true
    bhashini:              # Required only if provider is "bhashini".
      api_key_env: ""      # Env var name holding the Bhashini API key.
      user_id_env: ""      # Env var name holding the Bhashini user ID.
      endpoint: ""         # Bhashini inference pipeline endpoint URL.

  nlu_processor:
    model: ""                  # Required. Claude model ID for NLU classification.
    confidence_threshold: 0.5  # Optional. Float 0–1. Intents below this are treated as unknown.
    domain_instruction: ""     # Required. System instruction describing the domain to the NLU.
    intents: []                # Required. List of intent string identifiers.
    entities: []               # Required. List of entity string identifiers.
    sentiment_classes: []      # Required. List of sentiment class strings.

entity_to_profile_field: {}  # Required. Map of entity name → UserProfile field name.

hitl:
  response_message: ""  # Required. Message shown when handing off to a human agent.

agent_workflow:
  workflow_id: ""    # Required. Unique identifier for this workflow.
  version: ""        # Required. Version string e.g. "1.0.0".
  agent_system_prompt: ""    # Required. System prompt used for all LLM calls in this workflow.
  global_intents: []         # Required. Intent IDs handled globally regardless of current subagent.
  global_routing: []         # Required. Routing rules for global intents. Each: intent, next_subagent_id.
  default_fallback_subagent_id: ""  # Required. Subagent to route to when no routing rule matches.
  subagents: []              # Required. List of subagent definitions. See dev-kit/configs/kkb/agent_core.yaml for full example.
```

- [ ] **Step 2: Commit**

```bash
git add agent_core/config/domain.yaml
git commit -m "chore: replace agent_core domain.yaml with DPG template"
```

---

## Task 3: Template `config/domain.yaml` — knowledge_engine

**Files:**
- Modify: `knowledge_engine/config/domain.yaml`

- [ ] **Step 1: Replace with template**

Replace the entire contents of `knowledge_engine/config/domain.yaml` with:

```yaml
# knowledge_engine/config/domain.yaml — DPG domain configuration template.
#
# This file is the template a domain integrator fills in.
# For local dev, set CONFIG_FOLDER in .env.local to point to your domain configs folder.

knowledge:
  blocks:
    glossary:
      enabled: true
      mappings: []  # Required. List of {colloquial: [...], canonical: "..."} entries.
      apply_to: []  # Required. Fields to apply glossary to. e.g. [normalised_input, entities]

    static_knowledge_base:
      enabled: true
      collection_name: ""        # Required. ChromaDB collection name for this domain.
      chroma_persist_dir: ""     # Required. Path to ChromaDB persistence directory.
      sources: []                # Required. List of {path, type, doc_type, refresh} entries.
      intent_filters: {}         # Required. Map of intent → list of doc_types to retrieve.

    multimodal_input_handler:
      image_model: ""  # Required. Claude model ID for image description.
```

- [ ] **Step 2: Commit**

```bash
git add knowledge_engine/config/domain.yaml
git commit -m "chore: replace knowledge_engine domain.yaml with DPG template"
```

---

## Task 4: Template `config/domain.yaml` — trust_layer, action_gateway, memory_layer, learning_layer, reach_layer

**Files:**
- Modify: `trust_layer/config/domain.yaml`
- Modify: `action_gateway/config/domain.yaml`
- Modify: `memory_layer/config/domain.yaml`
- Modify: `learning_layer/config/domain.yaml`
- Modify: `reach_layer/config/domain.yaml`

- [ ] **Step 1: Replace `trust_layer/config/domain.yaml`**

```yaml
# trust_layer/config/domain.yaml — DPG domain configuration template.
#
# This file is the template a domain integrator fills in.
# For local dev, set CONFIG_FOLDER in .env.local to point to your domain configs folder.

trust:
  input_rules:
    blocked_phrases: []       # Required. List of strings that trigger input blocking.
    escalation_topics: []     # Required. List of strings that trigger human escalation.
  output_rules:
    blocked_phrases: []       # Required. List of strings that must not appear in LLM output.
```

- [ ] **Step 2: Replace `action_gateway/config/domain.yaml`**

```yaml
# action_gateway/config/domain.yaml — DPG domain configuration template.
#
# This file is the template a domain integrator fills in.
# For local dev, set CONFIG_FOLDER in .env.local to point to your domain configs folder.

action_gateway:
  connectors: {}  # Required. Map of connector_name → {endpoint, timeout_ms}.
                  # Keys must match connector names declared in agent_core domain config.
                  # Example:
                  #   my_connector:
                  #     endpoint: "http://localhost:9999/my_connector"
                  #     timeout_ms: 5000
```

- [ ] **Step 3: Replace `memory_layer/config/domain.yaml`**

```yaml
# memory_layer/config/domain.yaml — DPG domain configuration template.
#
# This file is the template a domain integrator fills in.
# For local dev, set CONFIG_FOLDER in .env.local to point to your domain configs folder.

state:
  session:
    ttl_minutes: 1440  # Optional. Session TTL in minutes. Default 1440 (1 day).
    schema: {}         # Required. Map of field_name → {type, default} or {type, values, default}.
                       # Infrastructure fields (user_id, journey_id, is_returning) are injected
                       # automatically — do not declare them here.

  persistent:
    backend: neo4j
    graph:
      user_node:
        label: ""   # Required. Neo4j node label for the user. e.g. "User"
        key: ""     # Required. Property used as the unique user identifier. e.g. "user_id"
      subnodes: {}  # Required. Map of subnode config. See dev-kit/configs/kkb/memory_layer.yaml for full example.

    merge_on_session_end: []  # Required. List of {session_field, target} mappings promoted to
                               # Journey node on session close.

user_data_persistence:
  default_mode: ""  # Required. "saved" or "anonymous". Controls default profile retention.

reengagement:
  triggers: []  # Required. List of re-engagement trigger definitions.
                # Each: {event, delay_hours, channel, message_template} or {event, loop_threshold, action}.
```

- [ ] **Step 4: Replace `learning_layer/config/domain.yaml`**

```yaml
# learning_layer/config/domain.yaml — DPG domain configuration template.
#
# No domain-specific keys required. DPG defaults in config/dpg.yaml apply.
# Add domain overrides here if needed.
```

- [ ] **Step 5: Replace `reach_layer/config/domain.yaml`**

```yaml
# reach_layer/config/domain.yaml — DPG domain configuration template.
#
# No domain-specific keys required. DPG defaults in config/dpg.yaml apply.
# Add domain overrides here if needed.
```

- [ ] **Step 6: Commit**

```bash
git add trust_layer/config/domain.yaml action_gateway/config/domain.yaml \
        memory_layer/config/domain.yaml learning_layer/config/domain.yaml \
        reach_layer/config/domain.yaml
git commit -m "chore: replace remaining block domain.yaml files with DPG templates"
```

---

## Task 5: CONFIG_FOLDER support — agent_core

**Files:**
- Modify: `agent_core/main.py`
- Modify: `agent_core/tests/test_main.py`

The existing tests in `test_main.py` inline `_load_config` and `_deep_merge` to avoid triggering module-level startup. Follow the same pattern for the new `_domain_config_path` helper.

- [ ] **Step 1: Write the failing tests**

Add to `agent_core/tests/test_main.py`:

```python
import os
from pathlib import Path


# Inline implementation — mirrors what will be in main.py.
# Keeps tests independent of module-level startup side-effects.
def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        result = _domain_config_path("agent_core")
        assert result == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        result = _domain_config_path("agent_core")
        assert result == tmp_path / "agent_core.yaml"

    def test_config_folder_path_uses_service_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        result = _domain_config_path("knowledge_engine")
        assert result == tmp_path / "knowledge_engine.yaml"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent_core
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `FAILED` — `_domain_config_path` is not yet defined in `main.py`.

- [ ] **Step 3: Update `agent_core/main.py`**

3a. Update the `load_dotenv` calls (currently at line 44). Replace:

```python
load_dotenv()
```

With:

```python
load_dotenv(Path(__file__).parent.parent / ".env.local")  # repo-root local dev overrides
load_dotenv()  # .env in block dir or injected environment (Docker/prod)
```

3b. Add `_domain_config_path` helper after the `_deep_merge` function (after line ~91):

```python
def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder
            (e.g. "agent_core").

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")
```

3c. Add `import os` at the top of `main.py` imports (it is not currently imported).

3d. In `_build_app()`, replace the line:

```python
domain_config = _load_config("config/domain.yaml")
```

With:

```python
domain_config = _load_config(str(_domain_config_path("agent_core")))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent_core
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `3 passed`

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
cd agent_core
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/main.py agent_core/tests/test_main.py
git commit -m "feat: add CONFIG_FOLDER support to agent_core config loading"
```

---

## Task 6: CONFIG_FOLDER support — knowledge_engine

**Files:**
- Modify: `knowledge_engine/main.py`
- Modify: `knowledge_engine/tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

Add to `knowledge_engine/tests/test_main.py`:

```python
import os
from pathlib import Path


def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        result = _domain_config_path("knowledge_engine")
        assert result == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        result = _domain_config_path("knowledge_engine")
        assert result == tmp_path / "knowledge_engine.yaml"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd knowledge_engine
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `FAILED` — `_domain_config_path` not yet in `main.py`.

- [ ] **Step 3: Update `knowledge_engine/main.py`**

3a. `knowledge_engine/main.py` already has `load_dotenv()` at line 34. Replace it with:

```python
load_dotenv(Path(__file__).parent.parent / ".env.local")  # repo-root local dev overrides
load_dotenv()  # .env in block dir or injected environment
```

3b. Add `import os` to the imports at the top of the file (if not already present).

3c. Add `_domain_config_path` helper after the `_deep_merge` function in `main.py`:

```python
def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")
```

3d. In `_build_app()` (around line 203), replace:

```python
domain_config = _load_config("config/domain.yaml")
```

With:

```python
domain_config = _load_config(str(_domain_config_path("knowledge_engine")))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd knowledge_engine
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `2 passed`

- [ ] **Step 5: Run full test suite**

```bash
cd knowledge_engine
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add knowledge_engine/main.py knowledge_engine/tests/test_main.py
git commit -m "feat: add CONFIG_FOLDER support to knowledge_engine config loading"
```

---

## Task 7: CONFIG_FOLDER support — trust_layer, memory_layer, learning_layer

**Files:**
- Modify: `trust_layer/main.py`, `trust_layer/tests/test_main.py`
- Modify: `memory_layer/main.py`, `memory_layer/tests/test_main.py`
- Modify: `learning_layer/main.py`, `learning_layer/tests/test_main.py`

These three blocks follow the same pattern and share the same `_build_app()` + `_load_config(path)` structure. None of them currently import `load_dotenv`.

- [ ] **Step 1: Write failing tests for all three**

Add to `trust_layer/tests/test_main.py`:

```python
import os
from pathlib import Path


def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        assert _domain_config_path("trust_layer") == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        assert _domain_config_path("trust_layer") == tmp_path / "trust_layer.yaml"
```

Add the same block to `memory_layer/tests/test_main.py` (replace `"trust_layer"` with `"memory_layer"`).

Add the same block to `learning_layer/tests/test_main.py` (replace `"trust_layer"` with `"learning_layer"`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd trust_layer   && uv run pytest tests/test_main.py::TestDomainConfigPath -v
cd ../memory_layer   && uv run pytest tests/test_main.py::TestDomainConfigPath -v
cd ../learning_layer && uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `FAILED` in all three.

- [ ] **Step 3: Update each `main.py`**

For each of `trust_layer/main.py`, `memory_layer/main.py`, `learning_layer/main.py`, make these changes:

3a. Add imports after the existing imports block:

```python
import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")  # repo-root local dev overrides
load_dotenv()  # .env in block dir or injected environment
```

3b. Add `_domain_config_path` helper after `_deep_merge`:

```python
def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")
```

3c. In `_build_app()`, replace:

```python
domain_config = _load_config("config/domain.yaml")
```

With (use the correct service name per block):

- trust_layer: `domain_config = _load_config(str(_domain_config_path("trust_layer")))`
- memory_layer: `domain_config = _load_config(str(_domain_config_path("memory_layer")))`
- learning_layer: `domain_config = _load_config(str(_domain_config_path("learning_layer")))`

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd trust_layer   && uv run pytest tests/test_main.py::TestDomainConfigPath -v
cd ../memory_layer   && uv run pytest tests/test_main.py::TestDomainConfigPath -v
cd ../learning_layer && uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `2 passed` in each.

- [ ] **Step 5: Run full test suites**

```bash
cd trust_layer   && uv run pytest tests/ -v
cd ../memory_layer   && uv run pytest tests/ -v
cd ../learning_layer && uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add trust_layer/main.py trust_layer/tests/test_main.py \
        memory_layer/main.py memory_layer/tests/test_main.py \
        learning_layer/main.py learning_layer/tests/test_main.py
git commit -m "feat: add CONFIG_FOLDER support to trust_layer, memory_layer, learning_layer"
```

---

## Task 8: CONFIG_FOLDER support — action_gateway

**Files:**
- Modify: `action_gateway/main.py`
- Modify: `action_gateway/tests/test_main.py`

`action_gateway/main.py` differs from other blocks: it uses `_build_config()` (not `_build_app()`), has no `load_dotenv`, and there is no `if __name__ == "__main__"` wrapping `_build_config` at module level — it is called directly in the `if __name__` block at line 64.

- [ ] **Step 1: Write the failing test**

Add to `action_gateway/tests/test_main.py`:

```python
import os
from pathlib import Path


def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        assert _domain_config_path("action_gateway") == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        assert _domain_config_path("action_gateway") == tmp_path / "action_gateway.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd action_gateway
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `FAILED`.

- [ ] **Step 3: Update `action_gateway/main.py`**

3a. Add imports after the existing imports:

```python
import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")  # repo-root local dev overrides
load_dotenv()  # .env in block dir or injected environment
```

3b. Add `_domain_config_path` helper after `_deep_merge`:

```python
def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")
```

3c. In `_build_config()`, replace:

```python
domain_config = _load_config("config/domain.yaml")
```

With:

```python
domain_config = _load_config(str(_domain_config_path("action_gateway")))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd action_gateway
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `2 passed`.

- [ ] **Step 5: Run full test suite**

```bash
cd action_gateway
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add action_gateway/main.py action_gateway/tests/test_main.py
git commit -m "feat: add CONFIG_FOLDER support to action_gateway config loading"
```

---

## Task 9: CONFIG_FOLDER support — reach_layer

**Files:**
- Modify: `reach_layer/main.py`
- Modify: `reach_layer/tests/test_main.py`

`reach_layer/main.py` differs from other blocks: `_load_config()` takes no arguments — it has the dpg/domain path logic inside it. Update that function to check `CONFIG_FOLDER`.

- [ ] **Step 1: Write the failing test**

Add to `reach_layer/tests/test_main.py`:

```python
import os
from pathlib import Path


def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        assert _domain_config_path("reach_layer") == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        assert _domain_config_path("reach_layer") == tmp_path / "reach_layer.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd reach_layer
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `FAILED`.

- [ ] **Step 3: Update `reach_layer/main.py`**

3a. Add after the existing imports (after `import yaml`):

```python
import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")  # repo-root local dev overrides
load_dotenv()  # .env in block dir or injected environment
```

3b. Add `_domain_config_path` helper before `_load_config`:

```python
def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")
```

3c. In `_load_config()` (currently at line 75–87), replace the hardcoded domain path:

```python
# Before
domain_config = _load_yaml("config/domain.yaml")

# After
domain_config = _load_yaml(str(_domain_config_path("reach_layer")))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd reach_layer
uv run pytest tests/test_main.py::TestDomainConfigPath -v
```

Expected: `2 passed`.

- [ ] **Step 5: Run full test suite**

```bash
cd reach_layer
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/main.py reach_layer/tests/test_main.py
git commit -m "feat: add CONFIG_FOLDER support to reach_layer config loading"
```

---

## Task 10: Smoke test with CONFIG_FOLDER

Manual verification that the wiring works end-to-end before closing the issue.

- [ ] **Step 1: Create `.env.local`**

```bash
echo "CONFIG_FOLDER=$(pwd)/dev-kit/configs/kkb" > .env.local
```

Run from repo root.

- [ ] **Step 2: Verify agent_core loads the KKB config**

```bash
cd agent_core
python -c "
import os
from pathlib import Path
os.environ['CONFIG_FOLDER'] = str(Path('..') / 'dev-kit' / 'configs' / 'kkb')
from main import _domain_config_path, _load_config
p = _domain_config_path('agent_core')
cfg = _load_config(str(p))
print('primary_model:', cfg.get('agent', {}).get('primary_model'))
"
```

Expected output:
```
primary_model: claude-haiku-4-5-20251001
```

- [ ] **Step 3: Verify fallback when CONFIG_FOLDER is not set**

```bash
cd agent_core
python -c "
import os
os.environ.pop('CONFIG_FOLDER', None)
from main import _domain_config_path
print(_domain_config_path('agent_core'))
"
```

Expected output:
```
config/domain.yaml
```

- [ ] **Step 4: Final commit**

```bash
git add .env.local.example  # already committed, skip if done
git commit -m "chore: close issue #15 — CONFIG_FOLDER env var and domain.yaml templates complete" --allow-empty
```

Or just verify no uncommitted files remain:

```bash
git status
```

Expected: working tree clean.
