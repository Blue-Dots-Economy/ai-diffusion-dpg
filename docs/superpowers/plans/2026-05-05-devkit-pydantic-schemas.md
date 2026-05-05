# DPG Config Schema System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace template-YAML field guides with Pydantic schemas that validate LLM tool calls in real-time and operator edits in the deploy wizard, eliminating the silent config errors that currently surface only at runtime.

**Architecture:** Two parallel schema sets in `dev-kit/dev_kit/schemas/` — `domain/` (LLM-facing, section-split per phase, validated in `accumulator.update()`) and `dpg/` (operator-facing, validates DPG framework values endpoint). Schemas use Pydantic 2 with `extra="forbid"` and proper constraints. Validation errors flow back to the LLM via the tool result, capped at 3 retries per (block, section), with a graceful fallback to user input.

**Tech Stack:** Pydantic 2 (already a dependency in `dev-kit/pyproject.toml`), pytest, FastAPI, `uv` for Python env management.

**Reference spec:** [docs/superpowers/specs/2026-05-04-devkit-pydantic-schema-design.md](../specs/2026-05-04-devkit-pydantic-schema-design.md) — contains the full Pydantic class definitions for all 14 schemas and the rationale for each design choice. Implementers should treat the spec as the source of truth for field types, constraints, and validators.

**Branch:** `feat/devkit-pydantic-schemas` (already created; spec already committed)

---

## Plan update notes (2026-05-05 — post-merge of multi-provider work)

After this plan was first written, main was merged in carrying the multi-provider chat redesign (`anthropic` + `openai`). The spec was updated accordingly. **Where this plan and the spec disagree, the spec wins.** Key changes affecting implementation:

1. **Enums are config-driven** (`dev-kit/dev_kit/schemas/enums_config.yaml`) for: `providers`, `anthropic_models`, `openai_models`, `languages`, `raya_voices`, `embedding_providers`. Adding a new model is a YAML edit, not a Python edit.

2. **New closed enums** (Python `Enum` classes in `enums.py`): `SpecialHandler`, `AuthType` (no `oauth2` — adapter doesn't support it), `HttpMethod`, `ParamSource`, `ParamType`, `McpTransport` (no `stdio` — adapter doesn't support it), `ReengagementChannel`, `RoutingOperator`, `InternalRoute`. Every value verified against runtime code.

3. **`AgentSection`** gets `provider: ProviderField`, `features: FeaturesSection` (with `prompt_cache`/`streaming`/`image_input`), and a `models_must_match_provider` cross-field validator. `primary_model`/`fallback_model` become `ChatModelField` (Annotated[str, AfterValidator]).

4. **`LanguageNormalisationSection`** and **`NLUProcessorSection`** get optional `provider`. Per-helper validation (option A) — section validates its own provider+model standalone.

5. **`SubAgent.special_handler`** → `Optional[SpecialHandler]` enum.

6. **`RoutingCondition`** is a NEW typed model with `operator: RoutingOperator`. **`RoutingRule.conditions`** changes from `list[dict]` → `list[RoutingCondition]`.

7. **`InternalConnectorDef.route`** → `InternalRoute` enum (only valid value is `knowledge_engine`).

8. **`ParamDefinition`** gets `items: Optional[dict]`. `source` → `ParamSource` enum, `type` → `ParamType` enum.

9. **`EndpointDefinition.method`** → `HttpMethod` enum.

10. **`AuthConfig.type`** → `AuthType` enum (no oauth2).

11. **`ToolDefinition.transport`** → `Optional[McpTransport]` (no stdio).

12. **`ReengagementTrigger.channel`** → `Optional[ReengagementChannel]` enum.

13. **`StaticKnowledgeBaseSection.embedding_provider`** → `EmbeddingProviderField`.

14. **`RayaVoiceConfig`**: `stt_language`/`tts_language` → `RayaLanguageField`, `voice_id` → `RayaVoiceIdField`. Adds `voice_id_matches_language` cross-field validator (the chosen voice's language must match stt/tts language).

15. **DPG `AgentDpgDefaults`** gets `provider: ProviderField` and `features: FeaturesDpg`.

16. **Phase prompts** inject **two** things now: Pydantic source AND a textual list of allowed values for fields backed by `Annotated[str, AfterValidator(...)]` (since the source code doesn't show the list). See spec section 8.3 for the helper.

**Implementation impact per task:**
- **Task 1** — substantially rewritten below. Loads from `enums_config.yaml`, adds 9 new closed enums.
- **Task 2** — `embedding_provider` uses `EmbeddingProviderField` (was `Literal[...]`).
- **Task 5** — `AuthConfig`/`ParamDefinition`/`EndpointDefinition`/`ToolDefinition` use new enums; `ParamDefinition` gets `items`. Drop `oauth2` from auth tests, drop `stdio` from transport tests.
- **Task 6** — `ReengagementTrigger.channel` uses `ReengagementChannel` enum.
- **Task 7** — `RayaVoiceConfig` uses `RayaVoiceIdField`/`RayaLanguageField`; tests need a `voice_id_matches_language` test case.
- **Task 8** — `AgentSection` adds `provider`/`features`/`models_must_match_provider` validator. Helpers add optional `provider`. `SubAgent.special_handler` enum. `RoutingCondition` typed model. `InternalConnectorDef.route` enum.
- **Task 9** — `AgentDpgDefaults` adds `provider`/`features`. DPG schema tests cover both new fields.
- **Task 16** — phase prompt injection now also calls `_format_allowed_values(...)` per phase.

Where the inline test code below references types that no longer exist (e.g., `ClaudeModel`), **read the spec for the current type and adjust the test accordingly**. The TDD discipline (write failing test, run, implement, run, commit) is unchanged.

---

## Plan update notes (2026-05-05 — runtime verification round)

After the multi-provider merge note above, a 7-agent verification round and a 4-agent code-audit round produced 16 additional spec changes (commit `9b11927`). These are reflected in the spec but not transcribed into the inline plan code below — when implementing the affected tasks, **read the spec section for the current schema and write tests accordingly**.

Per-task additions to expect:

- **Task 2 (knowledge_engine)** — `StaticKnowledgeBaseSection.collection_name` is now optional with default `"dpg_knowledge"`; `default_doc_type` is optional with default `"general"`; new field `chroma_persist_dir`. Tests should reflect this.
- **Task 4 (trust_layer)** — `TrustSection` now includes `policy_pack: str` and `policy_packs: dict[str, PolicyPackConfig]`. New supporting classes: `GuardrailConfig`, `PolicyPackConfig`. Two new enums in `enums.py`: `GuardrailSeverity`, `GuardrailFailureMode`. Tests cover guardrail construction and the `policy_pack_must_be_declared` cross-field validator.
- **Task 6 (memory_layer)** — Added `RESERVED_SESSION_FIELD_NAMES` constant + `schema_must_not_use_reserved_names` validator on `SessionStateConfig`. New typed classes `ChildNodeConfig` and `AdhocNodeConfig`; `SubnodeConfig.{child, children, adhoc}` are now properly typed (not `dict`). Tests cover both.
- **Task 7 (reach_layer)** — `VoiceChannelSection.barge_in_recency_ms` (`Optional[int]`, gt=0, le=10000). One additional test case.
- **Task 8 (agent_core)** — Substantial changes:
  - `TtsRulesConfig` adds `email` + `named_entities`.
  - `FeaturesSection` adds `_coerce_null_features` `mode="before"` validator (test: passing `features=None` to AgentSection coerces to default).
  - `NLUProcessorSection.intents` now `min_length=1` required.
  - `InvocationRules` adds 5 GH-176 fields and new `InvocationSafety` class; relaxes 4 string fields from `min_length=1` → optional empty.
  - `SubAgent.opening_phrase` is now `min_length=1` required for **ALL** subagents (terminal too); drop the `non_terminal_needs_opening_phrase` validator.
  - `RoutingRule` adds `session_writes_must_be_scalars` validator (rejects dict/list values).
- **Task 11 (round-trip tests)** — extra protection: kkb's `policy_pack`/`policy_packs`, GH-176 invocation fields, TTS email/named_entities will now validate cleanly. employ-voice-bot's sli/audit overrides will validate cleanly too.

Implementation should follow the spec verbatim — the inline code below is from before the verification round and may name fewer fields/validators than the spec ultimately requires.

---

## File structure

Files to create:

```
dev-kit/dev_kit/schemas/
├── __init__.py
├── enums.py                                    ← shared enum types
├── validation.py                               ← validate_domain_section + validate_dpg_block + dispatch tables
├── domain/
│   ├── __init__.py
│   ├── agent_core.py
│   ├── knowledge_engine.py
│   ├── memory_layer.py
│   ├── trust_layer.py
│   ├── action_gateway.py
│   ├── reach_layer.py
│   └── observability_layer.py
└── dpg/
    ├── __init__.py
    ├── _shared.py                              ← ServerConfig, OtelConfig, ClientConfig (used by multiple blocks)
    ├── agent_core.py
    ├── knowledge_engine.py
    ├── memory_layer.py
    ├── trust_layer.py
    ├── action_gateway.py
    ├── reach_layer.py
    └── observability_layer.py

dev-kit/tests/schemas/
├── __init__.py
├── test_enums.py
├── test_validation.py
├── test_existing_configs_validate.py           ← round-trip vs every dev-kit/configs/*/<block>.yaml
├── test_dpg_yamls_validate.py                  ← round-trip vs every dev-kit/dpg/<block>.yaml
├── domain/
│   ├── test_agent_core.py
│   ├── test_knowledge_engine.py
│   ├── test_memory_layer.py
│   ├── test_trust_layer.py
│   ├── test_action_gateway.py
│   ├── test_reach_layer.py
│   └── test_observability_layer.py
└── dpg/
    ├── test_agent_core_dpg.py
    ├── test_knowledge_engine_dpg.py
    ├── test_memory_layer_dpg.py
    ├── test_trust_layer_dpg.py
    ├── test_action_gateway_dpg.py
    ├── test_reach_layer_dpg.py
    └── test_observability_layer_dpg.py
```

Files to modify:

```
dev-kit/dev_kit/agent/accumulator.py            ← validation hook + retry counter
dev-kit/dev_kit/agent/conversation.py           ← reset counter on new user turn
dev-kit/dev_kit/agent/app.py                    ← DPG endpoint validation
dev-kit/dev_kit/agent/renderer.py               ← delegate to new validation
dev-kit/dev_kit/agent/prompts/phases.py         ← inject Pydantic source instead of YAML
```

Files to delete (after Task 17):

```
dev-kit/dev_kit/schema.py                       ← replaced by dev_kit/schemas/
```

---

## Tasks

### Task 1: Bootstrap — directory structure, config-driven enums, closed enums

**Files:**
- Create: `dev-kit/dev_kit/schemas/__init__.py`
- Create: `dev-kit/dev_kit/schemas/domain/__init__.py`
- Create: `dev-kit/dev_kit/schemas/dpg/__init__.py`
- Create: `dev-kit/dev_kit/schemas/enums_config.yaml`
- Create: `dev-kit/dev_kit/schemas/enums.py`
- Create: `dev-kit/tests/schemas/__init__.py`
- Create: `dev-kit/tests/schemas/test_enums.py`

- [ ] **Step 1: Write the failing test for shared enums**

Create `dev-kit/tests/schemas/test_enums.py`:

```python
"""Tests for shared enums (closed code Enums + config-driven open enums)."""
import pytest
from pydantic import BaseModel, ValidationError

from dev_kit.schemas.enums import (
    # Closed code enums
    AgentType, AuthType, DignityFailAction, HttpMethod, InstrumentType,
    InternalRoute, McpTransport, ParamSource, ParamType, PersistentBackend,
    ReengagementChannel, RoutingOperator, SessionFieldType, SpecialHandler,
    StorageMode, ToolCategory, ToolType, TrustQueueBackend,
    # Config-driven values
    ANTHROPIC_MODELS, OPENAI_MODELS, ALL_CHAT_MODELS, LANGUAGES,
    PROVIDERS, RAYA_VOICES, RAYA_VOICE_IDS, RAYA_LANGUAGES,
    RAYA_VOICE_LANGUAGE, EMBEDDING_PROVIDERS,
    # Annotated field types
    ChatModelField, EmbeddingProviderField, LanguageField,
    ProviderField, RayaLanguageField, RayaVoiceIdField,
)


# -- Closed code enums (verified against runtime code) -----------------------

def test_agent_type_values():
    assert {a.value for a in AgentType} == {
        "transactional", "informational", "agentic", "conversational"
    }

def test_trust_queue_backend_excludes_memory():
    assert {b.value for b in TrustQueueBackend} == {"log", "redis", "webhook"}

def test_dignity_fail_action_values():
    assert {a.value for a in DignityFailAction} == {"rewrite", "flag", "skip"}

def test_tool_type_values():
    assert {t.value for t in ToolType} == {"rest_api", "mcp"}

def test_tool_category_values():
    assert {c.value for c in ToolCategory} == {"read", "write", "identity"}

def test_storage_mode_values():
    assert {m.value for m in StorageMode} == {"saved", "anonymous"}

def test_persistent_backend_values():
    assert {b.value for b in PersistentBackend} == {"memgraph", "neo4j"}

def test_session_field_type_values():
    assert {t.value for t in SessionFieldType} == {"enum", "string", "int", "list"}

def test_instrument_type_values():
    assert {i.value for i in InstrumentType} == {"counter", "gauge", "histogram"}

def test_special_handler_values():
    assert {h.value for h in SpecialHandler} == {"hitl", "whatsapp_handoff"}

def test_auth_type_excludes_oauth2():
    """oauth2 deliberately excluded — adapter has no oauth2 branch."""
    assert "oauth2" not in {a.value for a in AuthType}
    assert {a.value for a in AuthType} == {"none", "api_key", "bearer"}

def test_http_method_values():
    assert {m.value for m in HttpMethod} == {"GET", "POST", "PUT", "DELETE", "PATCH"}

def test_param_source_values():
    assert {s.value for s in ParamSource} == {"agent", "static"}

def test_param_type_values():
    assert {p.value for p in ParamType} == {
        "string", "integer", "number", "boolean", "array", "object"
    }

def test_mcp_transport_excludes_stdio():
    """stdio not in _SUPPORTED_TRANSPORTS in mcp.py — must be excluded."""
    assert "stdio" not in {t.value for t in McpTransport}
    assert {t.value for t in McpTransport} == {"sse", "streamable_http"}

def test_reengagement_channel_values():
    assert {c.value for c in ReengagementChannel} == {"outbound_call", "whatsapp", "sms"}

def test_routing_operator_values():
    assert {o.value for o in RoutingOperator} == {"eq", "not_eq", "gt", "lt", "in"}

def test_internal_route_values():
    assert {r.value for r in InternalRoute} == {"knowledge_engine"}


# -- Config-driven values (loaded from enums_config.yaml) --------------------

def test_providers_loaded_from_config():
    assert "anthropic" in PROVIDERS
    assert "openai" in PROVIDERS

def test_anthropic_models_present():
    """Default config ships with at least Haiku, Sonnet, Opus."""
    assert "claude-haiku-4-5-20251001" in ANTHROPIC_MODELS
    assert "claude-sonnet-4-6" in ANTHROPIC_MODELS
    assert "claude-opus-4-7" in ANTHROPIC_MODELS

def test_openai_models_present():
    assert any(m.startswith("gpt-") for m in OPENAI_MODELS)

def test_all_chat_models_is_union():
    assert set(ALL_CHAT_MODELS) == set(ANTHROPIC_MODELS) | set(OPENAI_MODELS)

def test_raya_voices_have_required_fields():
    for v in RAYA_VOICES:
        assert "voice_id" in v and "language" in v and "name" in v

def test_raya_voice_language_map_consistent():
    """Every voice_id maps to its declared language."""
    for v in RAYA_VOICES:
        assert RAYA_VOICE_LANGUAGE[v["voice_id"]] == v["language"]

def test_raya_languages_derived_from_voices():
    """RAYA_LANGUAGES = unique languages across all voices, sorted."""
    assert RAYA_LANGUAGES == sorted({v["language"] for v in RAYA_VOICES})


# -- Annotated field types reject invalid values -----------------------------

def _wrap(t):
    """Helper: build a model with one field of the given annotated type."""
    class M(BaseModel):
        x: t
    return M

def test_provider_field_rejects_unknown():
    M = _wrap(ProviderField)
    M(x="anthropic")
    with pytest.raises(ValidationError):
        M(x="cohere")

def test_chat_model_field_rejects_unknown():
    M = _wrap(ChatModelField)
    M(x=ANTHROPIC_MODELS[0])
    with pytest.raises(ValidationError):
        M(x="not-a-real-model")

def test_language_field_rejects_unknown():
    M = _wrap(LanguageField)
    M(x="english")
    with pytest.raises(ValidationError):
        M(x="klingon")

def test_raya_voice_id_field_rejects_unknown():
    M = _wrap(RayaVoiceIdField)
    M(x=RAYA_VOICE_IDS[0])
    with pytest.raises(ValidationError):
        M(x="not-a-uuid")

def test_raya_language_field_rejects_unknown():
    M = _wrap(RayaLanguageField)
    M(x=RAYA_LANGUAGES[0])
    with pytest.raises(ValidationError):
        M(x="es")  # Spanish not in raya_voices

def test_embedding_provider_field_rejects_unknown():
    M = _wrap(EmbeddingProviderField)
    M(x="chroma_default")
    with pytest.raises(ValidationError):
        M(x="not-an-embedding")
```

- [ ] **Step 2: Run test to verify it fails**

```
cd dev-kit && uv run pytest tests/schemas/test_enums.py -v
```
Expected: `ModuleNotFoundError: No module named 'dev_kit.schemas'`

- [ ] **Step 3: Create empty `__init__.py` files**

```
touch dev-kit/dev_kit/schemas/__init__.py
touch dev-kit/dev_kit/schemas/domain/__init__.py
touch dev-kit/dev_kit/schemas/dpg/__init__.py
touch dev-kit/tests/schemas/__init__.py
```

- [ ] **Step 4: Create `dev-kit/dev_kit/schemas/enums_config.yaml`**

```yaml
# Edit this file to add a new model, voice, or language without code changes.
# enums.py reads it at import time.
providers:
  - anthropic
  - openai

# Both providers' chat_provider implementations accept any string and pass it
# to the API. The list below is "models we have tested or document as valid."
anthropic_models:
  - claude-haiku-4-5-20251001
  - claude-sonnet-4-6
  - claude-opus-4-7
  - claude-sonnet-4-5-20250929   # used by KKB NLU helper

openai_models:
  - gpt-4o-2024-08-06            # documented in openai_provider.py
  - gpt-4.1-2025-04-14           # referenced in kkb domain config
  - gpt-5.4-mini-2026-03-17      # referenced in kkb domain config

languages:
  - english
  - hindi
  - marathi
  - telugu
  - kannada
  - bengali
  - assamese
  - gujarati
  - malayalam
  - nepali
  - tamil

# Each voice tagged with its language. RAYA_LANGUAGES is derived at module load.
raya_voices:
  - {voice_id: "c849b31b-b0ba-488f-b97d-3fd12f2656f4", language: "mr",    name: "Sneha"}
  - {voice_id: "d6a002d0-230c-49b1-a137-b8a7d564b1ae", language: "hi",    name: "Priyanka"}
  - {voice_id: "25a7c7d9-57b3-488a-a880-33edf6642902", language: "te",    name: "Tanvi"}
  - {voice_id: "6a897d02-83ab-43ea-b17f-a8cc2d96a279", language: "kn",    name: "Meera"}
  - {voice_id: "a1b2c3d4-e5f6-4789-a012-b3c4d5e6f789", language: "bn",    name: "Aishwarya"}
  - {voice_id: "d4e5f6a7-b8c9-4a01-d345-e6f7a8b9c012", language: "as",    name: "Priti"}
  - {voice_id: "9a01bcde-2345-6789-abc1-123456abcdef", language: "gu",    name: "Jignesh"}
  - {voice_id: "0f24fb66-e495-4781-9e84-1224aa7dacde", language: "en-in", name: "Nayra"}
  - {voice_id: "90534e23-8bcb-4b1c-a16b-b9a4be646321", language: "en-us", name: "Solene"}
  - {voice_id: "57a1e849-8e0f-43ee-adab-b4b74a9d79e1", language: "ml",    name: "Devika"}
  - {voice_id: "5d6c7ee4-2563-4dab-9c8a-c3269e22cba9", language: "ne",    name: "Ritu"}
  - {voice_id: "fed6231c-7e35-4fbe-bbca-254f566e5dd5", language: "ta",    name: "Abirami"}

embedding_providers:
  - chroma_default
  - openai
  - sentence_transformers
```

- [ ] **Step 5: Implement `dev-kit/dev_kit/schemas/enums.py`**

```python
"""Shared enum types loaded from enums_config.yaml + closed code enums.

Open enums (provider/model/language/voice) are loaded from YAML so a new
model or voice can be added without touching Python. Closed enums are
declared as Python Enum classes — every value verified against runtime code.
"""
from __future__ import annotations
from enum import Enum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator

# ---------------------------------------------------------------------------
# Load open-enum values from config
# ---------------------------------------------------------------------------

_CFG_PATH = Path(__file__).parent / "enums_config.yaml"
_CFG: dict = yaml.safe_load(_CFG_PATH.read_text())

PROVIDERS: list[str] = _CFG["providers"]
ANTHROPIC_MODELS: list[str] = _CFG["anthropic_models"]
OPENAI_MODELS: list[str] = _CFG["openai_models"]
ALL_CHAT_MODELS: list[str] = ANTHROPIC_MODELS + OPENAI_MODELS

LANGUAGES: list[str] = _CFG["languages"]

RAYA_VOICES: list[dict] = _CFG["raya_voices"]
RAYA_VOICE_IDS: list[str] = [v["voice_id"] for v in RAYA_VOICES]
RAYA_VOICE_LANGUAGE: dict[str, str] = {v["voice_id"]: v["language"] for v in RAYA_VOICES}
RAYA_LANGUAGES: list[str] = sorted({v["language"] for v in RAYA_VOICES})

EMBEDDING_PROVIDERS: list[str] = _CFG["embedding_providers"]


def _make_validator(allowed: list[str], label: str):
    def check(v: str) -> str:
        if v not in allowed:
            raise ValueError(f"{label} must be one of {allowed}, got {v!r}")
        return v
    return check


ProviderField           = Annotated[str, AfterValidator(_make_validator(PROVIDERS, "provider"))]
ChatModelField          = Annotated[str, AfterValidator(_make_validator(ALL_CHAT_MODELS, "model"))]
LanguageField           = Annotated[str, AfterValidator(_make_validator(LANGUAGES, "language"))]
RayaVoiceIdField        = Annotated[str, AfterValidator(_make_validator(RAYA_VOICE_IDS, "voice_id"))]
RayaLanguageField       = Annotated[str, AfterValidator(_make_validator(RAYA_LANGUAGES, "raya_language"))]
EmbeddingProviderField  = Annotated[str, AfterValidator(_make_validator(EMBEDDING_PROVIDERS, "embedding_provider"))]


# ---------------------------------------------------------------------------
# Closed code enums — every value verified against runtime code support.
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    transactional = "transactional"
    informational = "informational"
    agentic = "agentic"
    conversational = "conversational"


class TrustQueueBackend(str, Enum):
    """'memory' intentionally excluded — runtime crashes on it."""
    log = "log"
    redis = "redis"
    webhook = "webhook"


class DignityFailAction(str, Enum):
    rewrite = "rewrite"
    flag = "flag"
    skip = "skip"


class ToolType(str, Enum):
    rest_api = "rest_api"
    mcp = "mcp"


class ToolCategory(str, Enum):
    read = "read"
    write = "write"
    identity = "identity"


class StorageMode(str, Enum):
    saved = "saved"
    anonymous = "anonymous"


class PersistentBackend(str, Enum):
    memgraph = "memgraph"
    neo4j = "neo4j"


class SessionFieldType(str, Enum):
    enum = "enum"
    string = "string"
    int_ = "int"
    list_ = "list"


class InstrumentType(str, Enum):
    counter = "counter"
    gauge = "gauge"
    histogram = "histogram"


class SpecialHandler(str, Enum):
    """Wired in agent_core/src/orchestrator.py."""
    hitl = "hitl"
    whatsapp_handoff = "whatsapp_handoff"


class AuthType(str, Enum):
    """'oauth2' excluded — adapter has no oauth2 branch in rest_api.py."""
    none = "none"
    api_key = "api_key"
    bearer = "bearer"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class ParamSource(str, Enum):
    agent = "agent"
    static = "static"


class ParamType(str, Enum):
    string = "string"
    integer = "integer"
    number = "number"
    boolean = "boolean"
    array = "array"
    object = "object"


class McpTransport(str, Enum):
    """'stdio' excluded — _SUPPORTED_TRANSPORTS in mcp.py is {sse, streamable_http}."""
    sse = "sse"
    streamable_http = "streamable_http"


class ReengagementChannel(str, Enum):
    """Schema-declared; runtime impl deferred (GH-168)."""
    outbound_call = "outbound_call"
    whatsapp = "whatsapp"
    sms = "sms"


class RoutingOperator(str, Enum):
    eq = "eq"
    not_eq = "not_eq"
    gt = "gt"
    lt = "lt"
    in_ = "in"


class InternalRoute(str, Enum):
    knowledge_engine = "knowledge_engine"
```

- [ ] **Step 6: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/test_enums.py -v
```
Expected: ~37 passed

- [ ] **Step 7: Commit**

```bash
git add dev-kit/dev_kit/schemas/__init__.py \
        dev-kit/dev_kit/schemas/domain/__init__.py \
        dev-kit/dev_kit/schemas/dpg/__init__.py \
        dev-kit/dev_kit/schemas/enums_config.yaml \
        dev-kit/dev_kit/schemas/enums.py \
        dev-kit/tests/schemas/__init__.py \
        dev-kit/tests/schemas/test_enums.py
git commit -m "feat(devkit): add schemas package with config-driven and closed enums"
```

---

### Task 2: Domain schema — knowledge_engine (smallest, used as the foundational pattern)

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/knowledge_engine.py`
- Create: `dev-kit/tests/schemas/domain/__init__.py`
- Create: `dev-kit/tests/schemas/domain/test_knowledge_engine.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/__init__.py` (empty).

Create `dev-kit/tests/schemas/domain/test_knowledge_engine.py`:

```python
"""Tests for knowledge_engine domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.knowledge_engine import (
    GlossaryMapping,
    GlossarySection,
    KnowledgeBlocksSection,
    KnowledgeSection,
    MetadataFiltersConfig,
    ObservabilitySection,
    StaticKnowledgeBaseSection,
)


# -- StaticKnowledgeBaseSection ----------------------------------------------

def test_static_kb_minimal_valid():
    """collection_name and default_doc_type are required; everything else has defaults."""
    s = StaticKnowledgeBaseSection(
        collection_name="kkb_docs",
        default_doc_type="general",
    )
    assert s.enabled is True
    assert s.top_k == 3
    assert s.similarity_threshold == 0.65


def test_static_kb_collection_name_pattern():
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(collection_name="Invalid Name", default_doc_type="x")


def test_static_kb_top_k_must_be_positive():
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(collection_name="x", default_doc_type="y", top_k=0)


def test_static_kb_top_k_max_50():
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(collection_name="x", default_doc_type="y", top_k=51)


def test_static_kb_similarity_threshold_range():
    StaticKnowledgeBaseSection(collection_name="x", default_doc_type="y", similarity_threshold=0.0)
    StaticKnowledgeBaseSection(collection_name="x", default_doc_type="y", similarity_threshold=1.0)
    with pytest.raises(ValidationError):
        StaticKnowledgeBaseSection(collection_name="x", default_doc_type="y", similarity_threshold=1.1)


def test_static_kb_intent_filter_requires_mappings():
    """If use_intent_filter=True, intent_filters must be non-empty."""
    with pytest.raises(ValidationError, match="intent_filters"):
        StaticKnowledgeBaseSection(
            collection_name="x",
            default_doc_type="y",
            metadata_filters=MetadataFiltersConfig(use_intent_filter=True),
            intent_filters={},
        )


def test_static_kb_intent_filter_disabled_allows_empty():
    """If use_intent_filter=False, intent_filters can be empty."""
    s = StaticKnowledgeBaseSection(
        collection_name="x",
        default_doc_type="y",
        metadata_filters=MetadataFiltersConfig(use_intent_filter=False),
        intent_filters={},
    )
    assert s.intent_filters == {}


def test_static_kb_extra_forbidden():
    with pytest.raises(ValidationError, match="Extra"):
        StaticKnowledgeBaseSection(
            collection_name="x",
            default_doc_type="y",
            vector_store="not_a_valid_field",
        )


# -- GlossarySection ---------------------------------------------------------

def test_glossary_mapping_requires_canonical():
    with pytest.raises(ValidationError):
        GlossaryMapping(colloquial=["cd"], canonical="")


def test_glossary_mapping_requires_colloquial():
    with pytest.raises(ValidationError):
        GlossaryMapping(colloquial=[], canonical="compact disc")


def test_glossary_apply_to_only_two_values():
    GlossarySection(apply_to=["normalised_input"])
    with pytest.raises(ValidationError):
        GlossarySection(apply_to=["bogus_value"])


# -- KnowledgeSection --------------------------------------------------------

def test_knowledge_section_full_valid():
    k = KnowledgeSection(
        blocks=KnowledgeBlocksSection(
            static_knowledge_base=StaticKnowledgeBaseSection(
                collection_name="x",
                default_doc_type="y",
            )
        )
    )
    assert k.blocks.static_knowledge_base.enabled is True


# -- ObservabilitySection ----------------------------------------------------

def test_observability_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_domain_must_be_non_empty():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="")


def test_observability_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", typo_field="x")
```

- [ ] **Step 2: Run test to verify it fails**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_knowledge_engine.py -v
```
Expected: `ModuleNotFoundError: No module named 'dev_kit.schemas.domain.knowledge_engine'`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/knowledge_engine.py`**

```python
"""Domain schemas for knowledge_engine block.

Sections written by the LLM during the knowledge phase.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


EmbeddingProvider = Literal["chroma_default", "openai", "sentence_transformers"]


class MetadataFiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_location_filter: bool = True
    use_intent_filter: bool = True


class StaticKnowledgeBaseSection(BaseModel):
    """RAG knowledge base configuration. Required: collection_name, default_doc_type."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    collection_name: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    top_k: int = Field(default=3, gt=0, le=50)
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    embedding_provider: EmbeddingProvider = "chroma_default"
    embedding_model: str = ""
    default_doc_type: str = Field(..., min_length=1)
    metadata_filters: MetadataFiltersConfig = Field(default_factory=MetadataFiltersConfig)
    intent_filters: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def intent_filter_requires_mappings_when_enabled(self) -> "StaticKnowledgeBaseSection":
        if self.metadata_filters.use_intent_filter and not self.intent_filters:
            raise ValueError(
                "metadata_filters.use_intent_filter=True requires intent_filters to be non-empty "
                "(or set use_intent_filter=False to allow searching all doc_types)"
            )
        return self


class GlossaryMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    colloquial: list[str] = Field(..., min_length=1)
    canonical: str = Field(..., min_length=1)


class GlossarySection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    mappings: list[GlossaryMapping] = Field(default_factory=list)
    apply_to: list[Literal["normalised_input", "entities"]] = Field(
        default_factory=lambda: ["normalised_input", "entities"]
    )


class KnowledgeBlocksSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    static_knowledge_base: Optional[StaticKnowledgeBaseSection] = None
    glossary: Optional[GlossarySection] = None


class KnowledgeSection(BaseModel):
    """Top-level knowledge_engine.knowledge section."""
    model_config = ConfigDict(extra="forbid")
    blocks: KnowledgeBlocksSection


class ObservabilitySection(BaseModel):
    """knowledge_engine.observability — auto-set by devkit to project slug."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_knowledge_engine.py -v
```
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/knowledge_engine.py \
        dev-kit/tests/schemas/domain/__init__.py \
        dev-kit/tests/schemas/domain/test_knowledge_engine.py
git commit -m "feat(devkit): add domain schema for knowledge_engine"
```

---

### Task 3: Domain schema — observability_layer

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/observability_layer.py`
- Create: `dev-kit/tests/schemas/domain/test_observability_layer.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_observability_layer.py`:

```python
"""Tests for observability_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.observability_layer import (
    LifecycleState,
    MetricDefinition,
    ObservabilitySection,
    OutcomesConfig,
)
from dev_kit.schemas.enums import InstrumentType


def test_lifecycle_state_minimal():
    s = LifecycleState(state="started")
    assert s.trigger_tool is None


def test_lifecycle_state_pattern():
    LifecycleState(state="user_consented")
    with pytest.raises(ValidationError):
        LifecycleState(state="Has Spaces")
    with pytest.raises(ValidationError):
        LifecycleState(state="123_starts_with_number")


def test_metric_definition_required_fields():
    MetricDefinition(name="turns.count", instrument="counter", description="Turn count")
    with pytest.raises(ValidationError):
        MetricDefinition(name="turns.count", instrument="counter")  # missing description


def test_metric_definition_name_pattern():
    MetricDefinition(name="placement.applications", instrument="counter", description="d")
    with pytest.raises(ValidationError):
        MetricDefinition(name="Has Spaces", instrument="counter", description="d")


def test_metric_definition_invalid_instrument():
    with pytest.raises(ValidationError):
        MetricDefinition(name="x", instrument="not_an_instrument", description="d")


def test_metric_definition_all_instruments_valid():
    for kind in ("counter", "gauge", "histogram"):
        MetricDefinition(name=f"m_{kind}", instrument=kind, description="d")


def test_outcomes_config_lifecycle_required():
    with pytest.raises(ValidationError):
        OutcomesConfig(lifecycle=[])  # min_length=1


def test_outcomes_config_metrics_optional():
    o = OutcomesConfig(lifecycle=[LifecycleState(state="started")])
    assert o.metrics == []


def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_section_full():
    o = ObservabilitySection(
        domain="kkb",
        outcomes=OutcomesConfig(
            lifecycle=[LifecycleState(state="started")],
            metrics=[MetricDefinition(name="m", instrument="counter", description="d")],
        ),
    )
    assert o.domain == "kkb"


def test_observability_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="x", unknown_field="y")
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_observability_layer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/observability_layer.py`**

```python
"""Domain schemas for observability_layer block.

Sections written by the LLM during the observability phase.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.enums import InstrumentType


class LifecycleState(BaseModel):
    """One outcome lifecycle state. trigger_tool=None means entry/initial state."""
    model_config = ConfigDict(extra="forbid")
    state: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    trigger_tool: Optional[str] = None
    trigger_condition: Optional[str] = None


class MetricDefinition(BaseModel):
    """One OTel metric instrument definition."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_.]*$")
    instrument: InstrumentType
    description: str = Field(..., min_length=1)
    unit: str = ""
    attributes: list[str] = Field(default_factory=list)


class OutcomesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle: list[LifecycleState] = Field(..., min_length=1)
    metrics: list[MetricDefinition] = Field(default_factory=list)


class ObservabilitySection(BaseModel):
    """observability_layer.observability — domain identifier + optional outcomes."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
    outcomes: Optional[OutcomesConfig] = None
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_observability_layer.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/observability_layer.py \
        dev-kit/tests/schemas/domain/test_observability_layer.py
git commit -m "feat(devkit): add domain schema for observability_layer"
```

---

### Task 4: Domain schema — trust_layer

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/trust_layer.py`
- Create: `dev-kit/tests/schemas/domain/test_trust_layer.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_trust_layer.py`:

```python
"""Tests for trust_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.trust_layer import (
    ConsentConfig,
    DignityCheckSection,
    HitlConfig,
    InputRulesConfig,
    ObservabilitySection,
    OutputRulesConfig,
    TrustSection,
)


def test_hitl_config_holding_message_required():
    with pytest.raises(ValidationError):
        HitlConfig(holding_message="")


def test_hitl_queue_backend_rejects_memory():
    """'memory' is not a valid backend — runtime crashes on it."""
    with pytest.raises(ValidationError):
        HitlConfig(holding_message="hi", queue_backend="memory")


def test_hitl_queue_backend_accepts_log_redis_webhook():
    for b in ("log", "redis", "webhook"):
        HitlConfig(holding_message="hi", queue_backend=b)


def test_input_rules_blocked_input_message_required():
    with pytest.raises(ValidationError):
        InputRulesConfig(blocked_input_message="")


def test_output_rules_output_blocked_message_required():
    with pytest.raises(ValidationError):
        OutputRulesConfig(output_blocked_message="")


def test_trust_section_full_valid():
    t = TrustSection(
        consent=ConsentConfig(consent_phrases=["yes"], decline_phrases=["no"]),
        hitl=HitlConfig(holding_message="hi"),
        input_rules=InputRulesConfig(blocked_input_message="blocked"),
        output_rules=OutputRulesConfig(output_blocked_message="out"),
    )
    assert t.hitl.queue_backend.value == "log"


def test_trust_section_extra_forbidden():
    with pytest.raises(ValidationError):
        TrustSection(
            hitl=HitlConfig(holding_message="hi"),
            input_rules=InputRulesConfig(blocked_input_message="b"),
            output_rules=OutputRulesConfig(output_blocked_message="o"),
            unknown_key="x",
        )


# -- DignityCheckSection -----------------------------------------------------

def test_dignity_check_disabled_default():
    """When disabled, questions can be empty — no enforcement."""
    d = DignityCheckSection(enabled=False, questions=[])
    assert d.enabled is False


def test_dignity_check_enabled_requires_questions():
    with pytest.raises(ValidationError, match="questions"):
        DignityCheckSection(enabled=True, questions=[])


def test_dignity_check_questions_must_be_strings():
    """Critical: questions must be plain strings, not dicts (#GH issue)."""
    with pytest.raises(ValidationError):
        DignityCheckSection(
            enabled=True,
            questions=["valid one", {"category": "hate", "severity": "high"}],
        )


def test_dignity_check_empty_string_question_rejected():
    with pytest.raises(ValidationError):
        DignityCheckSection(enabled=True, questions=["valid", ""])


def test_dignity_check_fail_action_enum():
    DignityCheckSection(enabled=False, fail_action="rewrite")
    DignityCheckSection(enabled=False, fail_action="flag")
    DignityCheckSection(enabled=False, fail_action="skip")
    with pytest.raises(ValidationError):
        DignityCheckSection(enabled=False, fail_action="bogus")


def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_trust_layer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/trust_layer.py`**

```python
"""Domain schemas for trust_layer block.

Sections written by the LLM during the trust phase.
"""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import DignityFailAction, TrustQueueBackend


class ConsentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_phrases: list[str] = Field(default_factory=list)
    decline_phrases: list[str] = Field(default_factory=list)


class HitlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holding_message: str = Field(..., min_length=1)
    queue_backend: TrustQueueBackend = TrustQueueBackend.log


class InputRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked_phrases: list[str] = Field(default_factory=list)
    blocked_input_message: str = Field(..., min_length=1)
    escalation_topics: list[str] = Field(default_factory=list)


class OutputRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked_phrases: list[str] = Field(default_factory=list)
    output_blocked_message: str = Field(..., min_length=1)


class TrustSection(BaseModel):
    """trust_layer.trust — content rules + consent + HITL + dignity wiring."""
    model_config = ConfigDict(extra="forbid")
    consent: ConsentConfig = Field(default_factory=ConsentConfig)
    hitl: HitlConfig
    input_rules: InputRulesConfig
    output_rules: OutputRulesConfig


class DignityCheckSection(BaseModel):
    """trust_layer.dignity_check — Conversational agents only."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    questions: list[str] = Field(default_factory=list)
    fail_action: DignityFailAction = DignityFailAction.rewrite

    @model_validator(mode="after")
    def enabled_requires_string_questions(self) -> "DignityCheckSection":
        if not self.enabled:
            return self
        if not self.questions:
            raise ValueError(
                "dignity_check.enabled=True requires non-empty questions list. "
                "An empty list means the check always passes (no protection)."
            )
        for i, q in enumerate(self.questions):
            if not isinstance(q, str) or not q.strip():
                raise ValueError(
                    f"dignity_check.questions[{i}] must be a non-empty plain string, "
                    f"got {type(q).__name__}: {q!r}. Do not pass dicts or empty strings."
                )
        return self


class ObservabilitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_trust_layer.py -v
```
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/trust_layer.py \
        dev-kit/tests/schemas/domain/test_trust_layer.py
git commit -m "feat(devkit): add domain schema for trust_layer"
```

---

### Task 5: Domain schema — action_gateway

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/action_gateway.py`
- Create: `dev-kit/tests/schemas/domain/test_action_gateway.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_action_gateway.py`:

```python
"""Tests for action_gateway domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.action_gateway import (
    AuthConfig,
    EndpointDefinition,
    ObservabilitySection,
    ParamDefinition,
    ResponseConfig,
    ToolDefinition,
    ToolsSection,
)


def test_tools_section_can_be_empty():
    """Critical: tools list can be empty (no external tools configured)."""
    s = ToolsSection(tools=[])
    assert s.tools == []


def test_tool_definition_id_pattern():
    ToolDefinition(
        id="my_tool", description="d",
        type="rest_api", base_url="https://x", endpoints=[
            EndpointDefinition(name="search")
        ],
    )
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="Has Spaces", description="d",
            type="rest_api", base_url="https://x",
            endpoints=[EndpointDefinition(name="s")],
        )


def test_tool_definition_description_required():
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="t", description="",
            type="rest_api", base_url="https://x",
            endpoints=[EndpointDefinition(name="s")],
        )


def test_tool_rest_requires_base_url_and_endpoints():
    with pytest.raises(ValidationError, match="REST"):
        ToolDefinition(id="t", description="d", type="rest_api")
    with pytest.raises(ValidationError, match="REST"):
        ToolDefinition(id="t", description="d", type="rest_api", base_url="https://x")


def test_tool_mcp_requires_server_url_and_transport():
    with pytest.raises(ValidationError, match="MCP"):
        ToolDefinition(id="t", description="d", type="mcp")
    with pytest.raises(ValidationError, match="MCP"):
        ToolDefinition(id="t", description="d", type="mcp", server_url="https://x")


def test_tool_mcp_valid():
    t = ToolDefinition(
        id="obsrv_docs", description="d",
        type="mcp", server_url="https://x", transport="streamable_http",
    )
    assert t.type.value == "mcp"


def test_tool_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="t", description="d", timeout_ms=0,
            type="rest_api", base_url="https://x",
            endpoints=[EndpointDefinition(name="s")],
        )


def test_tool_timeout_max_120s():
    with pytest.raises(ValidationError):
        ToolDefinition(
            id="t", description="d", timeout_ms=120001,
            type="rest_api", base_url="https://x",
            endpoints=[EndpointDefinition(name="s")],
        )


def test_param_definition_minimal():
    p = ParamDefinition(name="query")
    assert p.source == "agent"
    assert p.type == "string"


def test_param_name_required():
    with pytest.raises(ValidationError):
        ParamDefinition(name="")


def test_response_config_max_size_chars():
    ResponseConfig(max_size_chars=1)
    ResponseConfig(max_size_chars=50000)
    with pytest.raises(ValidationError):
        ResponseConfig(max_size_chars=0)
    with pytest.raises(ValidationError):
        ResponseConfig(max_size_chars=50001)


def test_tools_section_max_50_tools():
    """Domain configs cap at 50 tools."""
    many = [
        ToolDefinition(
            id=f"t{i}", description="d",
            type="rest_api", base_url="https://x",
            endpoints=[EndpointDefinition(name="s")],
        )
        for i in range(51)
    ]
    with pytest.raises(ValidationError):
        ToolsSection(tools=many)


def test_observability_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="x", typo="y")
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_action_gateway.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/action_gateway.py`**

```python
"""Domain schemas for action_gateway block.

Sections written by the LLM during the tools phase.
The tools list CAN BE EMPTY when no external tools are configured.
"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import ToolCategory, ToolType


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = "none"   # none | api_key | bearer | oauth2
    header: str = ""
    secret_env: str = ""
    token_url: str = ""


class ParamDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    source: str = "agent"   # agent | static
    type: str = "string"
    required: bool = False
    description: str = ""
    value: Optional[Any] = None
    default: Optional[Any] = None


class EndpointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    method: str = "POST"   # GET | POST | PUT | DELETE | PATCH
    path: str = ""
    params: list[ParamDefinition] = Field(default_factory=list)


class ResponseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_size_chars: int = Field(default=4000, gt=0, le=50000)
    projection: Optional[dict] = None


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    type: ToolType = ToolType.rest_api
    category: ToolCategory = ToolCategory.read
    description: str = Field(..., min_length=1)
    timeout_ms: int = Field(default=5000, gt=0, le=120000)

    # REST-only
    base_url: Optional[str] = None
    auth: Optional[AuthConfig] = None
    endpoints: Optional[list[EndpointDefinition]] = None
    response: Optional[ResponseConfig] = None

    # MCP-only
    server_url: Optional[str] = None
    transport: Optional[str] = None  # sse | streamable_http | stdio
    namespace: Optional[str] = None

    @model_validator(mode="after")
    def shape_matches_type(self) -> "ToolDefinition":
        if self.type == ToolType.rest_api:
            if not self.base_url or not self.endpoints:
                raise ValueError(
                    f"REST API tool '{self.id}' requires base_url and at least one endpoint"
                )
        elif self.type == ToolType.mcp:
            if not self.server_url or not self.transport:
                raise ValueError(
                    f"MCP tool '{self.id}' requires server_url and transport"
                )
        return self


class ToolsSection(BaseModel):
    """The tools list — CAN BE EMPTY when no external tools are configured."""
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=50)


class ObservabilitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_action_gateway.py -v
```
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/action_gateway.py \
        dev-kit/tests/schemas/domain/test_action_gateway.py
git commit -m "feat(devkit): add domain schema for action_gateway"
```

---

### Task 6: Domain schema — memory_layer

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/memory_layer.py`
- Create: `dev-kit/tests/schemas/domain/test_memory_layer.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_memory_layer.py`:

```python
"""Tests for memory_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.memory_layer import (
    GraphConfig,
    MergeRule,
    ObservabilitySection,
    PersistentStateConfig,
    ReengagementSection,
    ReengagementTrigger,
    SessionFieldDefinition,
    SessionStateConfig,
    StateSection,
    SubnodeConfig,
    UserDataPersistenceSection,
    UserNodeConfig,
)


def test_session_field_definition_enum_requires_values():
    with pytest.raises(ValidationError, match="values"):
        SessionFieldDefinition(type="enum", values=None)


def test_session_field_definition_enum_default_in_values():
    SessionFieldDefinition(type="enum", values=["a", "b"], default="a")
    with pytest.raises(ValidationError, match="default"):
        SessionFieldDefinition(type="enum", values=["a", "b"], default="c")


def test_session_field_definition_string_no_values_required():
    SessionFieldDefinition(type="string", default="")


def test_session_state_ttl_minutes_positive():
    with pytest.raises(ValidationError):
        SessionStateConfig(ttl_minutes=0)


def test_session_state_ttl_minutes_max_one_week():
    SessionStateConfig(ttl_minutes=10080)
    with pytest.raises(ValidationError):
        SessionStateConfig(ttl_minutes=10081)


def test_user_node_config_required_fields():
    UserNodeConfig(label="User", key="user_id")
    with pytest.raises(ValidationError):
        UserNodeConfig(label="", key="user_id")
    with pytest.raises(ValidationError):
        UserNodeConfig(label="User", key="")


def test_subnode_config_rel_required():
    with pytest.raises(ValidationError):
        SubnodeConfig(rel="")


def test_persistent_state_config_default_backend():
    p = PersistentStateConfig(graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id")))
    assert p.backend.value == "memgraph"


def test_persistent_state_config_neo4j_backend_valid():
    p = PersistentStateConfig(
        backend="neo4j",
        graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id")),
    )
    assert p.backend.value == "neo4j"


def test_state_section_full():
    s = StateSection(
        session=SessionStateConfig(ttl_minutes=1440),
        persistent=PersistentStateConfig(
            graph=GraphConfig(user_node=UserNodeConfig(label="User", key="user_id"))
        ),
    )
    assert s.session.ttl_minutes == 1440


def test_user_data_persistence_default_mode_default():
    u = UserDataPersistenceSection()
    assert u.default_mode.value == "saved"


def test_user_data_persistence_anonymous_valid():
    u = UserDataPersistenceSection(default_mode="anonymous")
    assert u.default_mode.value == "anonymous"


def test_reengagement_trigger_event_required():
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="")


def test_reengagement_trigger_delay_hours_positive():
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="x", delay_hours=0)


def test_merge_rule_fields_required():
    MergeRule(session_field="mood", target="UserProfile.last_mood")
    with pytest.raises(ValidationError):
        MergeRule(session_field="", target="x")


def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_state_section_extra_forbidden():
    with pytest.raises(ValidationError):
        StateSection(
            session=SessionStateConfig(ttl_minutes=60),
            persistent=PersistentStateConfig(
                graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id"))
            ),
            unknown_field="x",
        )
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_memory_layer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/memory_layer.py`**

```python
"""Domain schemas for memory_layer block.

Sections written by the LLM during the memory phase.
"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import PersistentBackend, SessionFieldType, StorageMode


class SessionFieldDefinition(BaseModel):
    """One entry in state.session.schema — domain field declaration."""
    model_config = ConfigDict(extra="forbid")
    type: SessionFieldType
    values: Optional[list[str]] = None
    default: Any = None

    @model_validator(mode="after")
    def enum_requires_values(self) -> "SessionFieldDefinition":
        if self.type == SessionFieldType.enum:
            if not self.values:
                raise ValueError("type='enum' requires non-empty 'values' list")
            if self.default is not None and self.default not in self.values:
                raise ValueError(f"default {self.default!r} must be one of values {self.values}")
        return self


class SessionStateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_minutes: int = Field(..., gt=0, le=10080)  # ≤ 1 week
    schema: dict[str, SessionFieldDefinition] = Field(default_factory=dict)


class UserNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)


class SubnodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rel: str = Field(..., min_length=1)
    grouping: bool = False
    declared_fields: list[str] = Field(default_factory=list)
    child: Optional[dict] = None
    children: Optional[list[dict]] = None
    adhoc: Optional[dict] = None


class GraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_node: UserNodeConfig
    subnodes: dict[str, SubnodeConfig] = Field(default_factory=dict)


class MergeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_field: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)


class PersistentStateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: PersistentBackend = PersistentBackend.memgraph
    graph: GraphConfig
    merge_on_session_end: list[MergeRule] = Field(default_factory=list)


class StateSection(BaseModel):
    """memory_layer.state — session + persistent storage config."""
    model_config = ConfigDict(extra="forbid")
    session: SessionStateConfig
    persistent: PersistentStateConfig


class UserDataPersistenceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_mode: StorageMode = StorageMode.saved


class ReengagementTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str = Field(..., min_length=1)
    delay_hours: Optional[int] = Field(default=None, gt=0)
    channel: Optional[str] = None
    message_template: Optional[str] = None
    loop_threshold: Optional[int] = Field(default=None, gt=0)
    action: Optional[str] = None


class ReengagementSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    triggers: list[ReengagementTrigger] = Field(default_factory=list)


class ObservabilitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_memory_layer.py -v
```
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/memory_layer.py \
        dev-kit/tests/schemas/domain/test_memory_layer.py
git commit -m "feat(devkit): add domain schema for memory_layer"
```

---

### Task 7: Domain schema — reach_layer

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/reach_layer.py`
- Create: `dev-kit/tests/schemas/domain/test_reach_layer.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_reach_layer.py`:

```python
"""Tests for reach_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.reach_layer import (
    ChannelsSection,
    CommonObservabilityConfig,
    CommonSection,
    RayaVoiceConfig,
    ReachLayerSection,
    VoiceAgentCoreClient,
    VoiceChannelSection,
    WebChannelSection,
    WebUiConfig,
)


def test_web_ui_app_name_required():
    with pytest.raises(ValidationError):
        WebUiConfig(app_name="")


def test_web_ui_minimal_valid():
    ui = WebUiConfig(app_name="KKB")
    assert ui.app_tagline == ""


def test_raya_voice_id_must_be_in_table():
    """voice_id is a Literal of 12 fixed UUIDs — anything else fails."""
    RayaVoiceConfig(
        stt_language="en-in", tts_language="en-in",
        voice_id="0f24fb66-e495-4781-9e84-1224aa7dacde",
    )
    with pytest.raises(ValidationError):
        RayaVoiceConfig(
            stt_language="en-in", tts_language="en-in",
            voice_id="not-a-real-uuid",
        )


def test_raya_voice_language_codes():
    RayaVoiceConfig(
        stt_language="hi", tts_language="hi",
        voice_id="d6a002d0-230c-49b1-a137-b8a7d564b1ae",
    )
    with pytest.raises(ValidationError):
        RayaVoiceConfig(
            stt_language="x", tts_language="hi",  # too short
            voice_id="d6a002d0-230c-49b1-a137-b8a7d564b1ae",
        )


def test_voice_agent_core_fallback_phrase_required():
    with pytest.raises(ValidationError):
        VoiceAgentCoreClient(fallback_phrase="")


def test_voice_agent_core_timeout_max_60s():
    with pytest.raises(ValidationError):
        VoiceAgentCoreClient(fallback_phrase="x", timeout_ms=60001)


def test_voice_channel_full_valid():
    v = VoiceChannelSection(
        raya=RayaVoiceConfig(
            stt_language="en-in", tts_language="en-in",
            voice_id="0f24fb66-e495-4781-9e84-1224aa7dacde",
        ),
        agent_core=VoiceAgentCoreClient(fallback_phrase="Sorry, please retry."),
    )
    assert v.terminal_word is None


def test_voice_filler_threshold_positive():
    with pytest.raises(ValidationError):
        VoiceChannelSection(
            raya=RayaVoiceConfig(
                stt_language="en-in", tts_language="en-in",
                voice_id="0f24fb66-e495-4781-9e84-1224aa7dacde",
            ),
            agent_core=VoiceAgentCoreClient(fallback_phrase="x"),
            filler_threshold_ms=0,
        )


def test_channels_section_optional_channels():
    """All channels are optional in domain config — domain only fills what it uses."""
    c = ChannelsSection()
    assert c.web is None
    assert c.voice is None


def test_reach_layer_section_full_valid():
    r = ReachLayerSection(
        channels=ChannelsSection(
            web=WebChannelSection(ui=WebUiConfig(app_name="KKB")),
        ),
        common=CommonSection(
            observability=CommonObservabilityConfig(domain="kkb"),
        ),
    )
    assert r.channels.web.ui.app_name == "KKB"


def test_reach_layer_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ReachLayerSection(unknown_field="x")
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_reach_layer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/reach_layer.py`**

```python
"""Domain schemas for reach_layer block.

Sections written by the LLM during the reach phase.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from dev_kit.schemas.enums import RayaVoiceId


class WebUiConfig(BaseModel):
    """Web channel UI branding and copy."""
    model_config = ConfigDict(extra="forbid")
    app_name: str = Field(..., min_length=1)
    app_tagline: str = ""
    app_icon: str = ""
    agent_avatar: str = ""
    user_avatar: str = ""
    setup_heading: str = ""
    setup_subtitle: str = ""
    user_id_placeholder: str = ""
    user_id_hint: str = ""
    start_btn_label: str = ""
    new_session_msg: str = ""
    returning_user_msg: str = ""
    storage_key: str = ""
    theme_storage_key: str = ""
    sign_out_confirm: str = ""
    switch_user_confirm: str = ""
    delete_conversation_confirm: str = ""


class WebChannelSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ui: WebUiConfig


class RayaVoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stt_language: str = Field(..., min_length=2, max_length=10)
    tts_language: str = Field(..., min_length=2, max_length=10)
    voice_id: RayaVoiceId


class VoiceAgentCoreClient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_ms: int = Field(default=15000, gt=0, le=60000)
    fallback_phrase: str = Field(..., min_length=1)
    barge_in_acknowledgement: str = ""


class VoiceChannelSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raya: RayaVoiceConfig
    agent_core: VoiceAgentCoreClient
    terminal_word: Optional[str] = None
    filler_phrase: Optional[str] = None
    filler_threshold_ms: Optional[int] = Field(default=None, gt=0, le=10000)


class ChannelsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web: Optional[WebChannelSection] = None
    voice: Optional[VoiceChannelSection] = None


class CommonObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)


class CommonSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observability: CommonObservabilityConfig


class ReachLayerSection(BaseModel):
    """Top-level reach_layer wrapper (matches the YAML's reach_layer: {} root key)."""
    model_config = ConfigDict(extra="forbid")
    channels: Optional[ChannelsSection] = None
    common: Optional[CommonSection] = None
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_reach_layer.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/reach_layer.py \
        dev-kit/tests/schemas/domain/test_reach_layer.py
git commit -m "feat(devkit): add domain schema for reach_layer"
```

---

### Task 8: Domain schema — agent_core (largest, most validators)

**Files:**
- Create: `dev-kit/dev_kit/schemas/domain/agent_core.py`
- Create: `dev-kit/tests/schemas/domain/test_agent_core.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/domain/test_agent_core.py`:

```python
"""Tests for agent_core domain schemas (the biggest set, most cross-field rules)."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.agent_core import (
    AgentSection,
    AgentWorkflowSection,
    ChannelsSection,
    ConnectorsSection,
    ConnectorDef,
    ConversationSection,
    EntityToProfileFieldSection,
    HitlSection,
    InternalConnectorDef,
    InvocationRules,
    LanguageNormalisationSection,
    NLUProcessorSection,
    ObservabilitySection,
    PreprocessingSection,
    RoutingRule,
    SubAgent,
    UserStateDefinition,
    UserStateModel,
)


# -- AgentSection ------------------------------------------------------------

def test_agent_section_minimal():
    a = AgentSection(primary_model="claude-sonnet-4-6", fallback_model="claude-haiku-4-5-20251001")
    assert a.max_tool_rounds == 3


def test_agent_section_primary_fallback_must_differ():
    with pytest.raises(ValidationError, match="different"):
        AgentSection(primary_model="claude-sonnet-4-6", fallback_model="claude-sonnet-4-6")


def test_agent_section_max_tool_rounds_min_1():
    """Critical: runtime crashes on max_tool_rounds=0."""
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model="claude-sonnet-4-6", fallback_model="claude-haiku-4-5-20251001",
            max_tool_rounds=0,
        )


def test_agent_section_max_tool_rounds_max_20():
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model="claude-sonnet-4-6", fallback_model="claude-haiku-4-5-20251001",
            max_tool_rounds=21,
        )


def test_agent_section_invalid_model_id():
    with pytest.raises(ValidationError):
        AgentSection(primary_model="claude-3-5-sonnet", fallback_model="claude-haiku-4-5-20251001")


def test_agent_section_extra_forbidden():
    with pytest.raises(ValidationError):
        AgentSection(
            primary_model="claude-sonnet-4-6", fallback_model="claude-haiku-4-5-20251001",
            unknown_field="x",
        )


# -- PreprocessingSection ----------------------------------------------------

def test_language_normalisation_supported_languages_min_1():
    with pytest.raises(ValidationError):
        LanguageNormalisationSection(model="claude-sonnet-4-6", default_language="english", supported_languages=[])


def test_nlu_processor_confidence_threshold_range():
    NLUProcessorSection(model="claude-sonnet-4-6", confidence_threshold=0.0)
    NLUProcessorSection(model="claude-sonnet-4-6", confidence_threshold=1.0)
    with pytest.raises(ValidationError):
        NLUProcessorSection(model="claude-sonnet-4-6", confidence_threshold=1.1)


def test_preprocessing_section_full():
    p = PreprocessingSection(
        language_normalisation=LanguageNormalisationSection(
            model="claude-sonnet-4-6", default_language="english", supported_languages=["english"],
        ),
        nlu_processor=NLUProcessorSection(model="claude-sonnet-4-6"),
    )
    assert p.nlu_processor.confidence_threshold == 0.5


# -- ConversationSection -----------------------------------------------------

def test_conversation_section_required_messages():
    with pytest.raises(ValidationError):
        ConversationSection(
            blocked_message="", escalation_message="x", output_blocked_message="y",
        )


def test_user_state_model_default_must_be_in_states():
    with pytest.raises(ValidationError, match="default_state"):
        UserStateModel(
            enabled=True,
            default_state="not_declared",
            states=[UserStateDefinition(id="fog"), UserStateDefinition(id="orientation")],
        )


def test_user_state_model_disabled_skips_default_check():
    """When disabled, default_state can be empty without error."""
    UserStateModel(enabled=False, default_state="", states=[])


# -- ChannelsSection ---------------------------------------------------------

def test_channels_section_all_optional():
    c = ChannelsSection()
    assert c.web is None and c.voice is None


# -- ConnectorsSection -------------------------------------------------------

def test_connector_def_invocation_rules_required():
    with pytest.raises(ValidationError):
        ConnectorDef(name="t", description="d")  # missing invocation_rules


def test_invocation_rules_call_when_required():
    with pytest.raises(ValidationError):
        InvocationRules(
            call_when="", must_not_substitute="x", on_empty="y", on_failure="z",
        )


def test_internal_connector_route_required():
    with pytest.raises(ValidationError):
        InternalConnectorDef(
            name="kr", description="d",
            invocation_rules=InvocationRules(
                call_when="x", must_not_substitute="y", on_empty="z", on_failure="w"
            ),
            route="",
        )


# -- AgentWorkflowSection ----------------------------------------------------

def _make_subagent(id="greeting", **kw):
    defaults = dict(
        id=id, name=id.title(), system_prompt="prompt",
        is_start=False, is_terminal=False,
        opening_phrase="hi" if not kw.get("is_terminal") else "",
    )
    defaults.update(kw)
    return SubAgent(**defaults)


def test_workflow_workflow_id_pattern():
    """workflow_id must be snake_case."""
    with pytest.raises(ValidationError):
        AgentWorkflowSection(
            workflow_id="Has Spaces", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(is_start=True)],
            default_fallback_subagent_id="greeting",
        )


def test_workflow_version_pattern():
    with pytest.raises(ValidationError):
        AgentWorkflowSection(
            workflow_id="kkb", version="not_semver",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(is_start=True)],
            default_fallback_subagent_id="greeting",
        )


def test_workflow_system_prompt_min_length():
    with pytest.raises(ValidationError):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="too short",
            subagents=[_make_subagent(is_start=True)],
            default_fallback_subagent_id="greeting",
        )


def test_workflow_fallback_must_be_declared():
    with pytest.raises(ValidationError, match="default_fallback_subagent_id"):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(is_start=True)],
            default_fallback_subagent_id="ghost_subagent",
        )


def test_workflow_routing_target_must_be_declared():
    with pytest.raises(ValidationError, match="unknown subagent"):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(
                is_start=True,
                routing=[RoutingRule(intent="next", next_subagent_id="ghost")],
            )],
            default_fallback_subagent_id="greeting",
        )


def test_workflow_global_intents_must_not_overlap():
    with pytest.raises(ValidationError, match="both global_intents"):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(is_start=True, valid_intents=["help"])],
            global_intents=["help"],
            default_fallback_subagent_id="greeting",
        )


def test_workflow_exactly_one_start():
    """Exactly one subagent must have is_start=True."""
    with pytest.raises(ValidationError, match="is_start"):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(id="a", is_start=True), _make_subagent(id="b", is_start=True)],
            default_fallback_subagent_id="a",
        )


def test_workflow_no_start_subagent_fails():
    with pytest.raises(ValidationError, match="is_start"):
        AgentWorkflowSection(
            workflow_id="kkb", version="1.0.0",
            agent_system_prompt="x" * 25,
            subagents=[_make_subagent(id="a"), _make_subagent(id="b")],
            default_fallback_subagent_id="a",
        )


def test_subagent_non_terminal_needs_opening_phrase():
    with pytest.raises(ValidationError, match="opening_phrase"):
        SubAgent(
            id="greeting", name="Greeting", system_prompt="p",
            is_start=True, is_terminal=False, opening_phrase="",
        )


def test_subagent_terminal_can_have_empty_opening_phrase():
    """Terminal subagents don't need an opening phrase."""
    SubAgent(
        id="end", name="End", system_prompt="p",
        is_start=False, is_terminal=True, opening_phrase="",
    )


def test_workflow_minimal_valid():
    w = AgentWorkflowSection(
        workflow_id="kkb_demo", version="1.0.0",
        agent_system_prompt="A demo agent for testing the workflow validators.",
        subagents=[_make_subagent(is_start=True)],
        default_fallback_subagent_id="greeting",
    )
    assert w.workflow_id == "kkb_demo"


# -- HitlSection -------------------------------------------------------------

def test_hitl_section_response_message_required():
    with pytest.raises(ValidationError):
        HitlSection(response_message="")


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_pattern():
    ObservabilitySection(domain="kkb")
    ObservabilitySection(domain="employ-voice-bot")
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="UPPERCASE")
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="123_starts_with_num")


# -- EntityToProfileFieldSection --------------------------------------------

def test_entity_to_profile_field_open_map():
    """This is an open map — accepts arbitrary string mappings."""
    e = EntityToProfileFieldSection(user_name="name", user_location="location")
    assert e.user_name == "name"
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_agent_core.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/domain/agent_core.py`**

The full source is in [the spec section 6.2](../specs/2026-05-04-devkit-pydantic-schema-design.md#62-dev-kitdev_kitschemasdomainagent_corepy). Copy the complete code from there into `dev-kit/dev_kit/schemas/domain/agent_core.py`.

Key classes (full code in spec):
- `AgentSection` — primary/fallback model + retry/timeout, with `primary_fallback_must_differ` validator
- `LanguageNormalisationSection`, `NLUProcessorSection`, `PreprocessingSection`
- `UserStateDefinition`, `UserStateModel` (with `default_must_be_in_states` validator), `ConversationSection`
- `TtsRulesConfig`, `TurnAssemblerConfig`, `ChannelEntry`, `ChannelsSection`
- `InvocationRules`, `ConnectorDef`, `InternalConnectorDef`, `ConnectorsSection`
- `RoutingRule`, `SubAgent` (with `non_terminal_needs_opening_phrase` validator)
- `AgentWorkflowSection` (with 4 validators: `fallback_must_be_declared`, `routing_targets_must_be_declared`, `global_intents_must_not_overlap_subagent_intents`, `exactly_one_start_subagent`)
- `EntityToProfileFieldSection` (uses `extra="allow"` because it's an open map)
- `HitlSection`, `ObservabilitySection`

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/domain/test_agent_core.py -v
```
Expected: 30 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/domain/agent_core.py \
        dev-kit/tests/schemas/domain/test_agent_core.py
git commit -m "feat(devkit): add domain schema for agent_core"
```

---

### Task 9: DPG schemas — shared types + 7 blocks

**Files:**
- Create: `dev-kit/dev_kit/schemas/dpg/_shared.py`
- Create: `dev-kit/dev_kit/schemas/dpg/agent_core.py`
- Create: `dev-kit/dev_kit/schemas/dpg/knowledge_engine.py`
- Create: `dev-kit/dev_kit/schemas/dpg/memory_layer.py`
- Create: `dev-kit/dev_kit/schemas/dpg/trust_layer.py`
- Create: `dev-kit/dev_kit/schemas/dpg/action_gateway.py`
- Create: `dev-kit/dev_kit/schemas/dpg/reach_layer.py`
- Create: `dev-kit/dev_kit/schemas/dpg/observability_layer.py`
- Create: `dev-kit/tests/schemas/dpg/__init__.py`
- Create: `dev-kit/tests/schemas/dpg/test_dpg_schemas.py`

- [ ] **Step 1: Write failing test**

Create `dev-kit/tests/schemas/dpg/__init__.py` (empty).

Create `dev-kit/tests/schemas/dpg/test_dpg_schemas.py`:

```python
"""Tests for DPG framework schemas — used by the deploy wizard's DPG Values endpoint."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.dpg.agent_core import AgentCoreDpgConfig
from dev_kit.schemas.dpg.knowledge_engine import KnowledgeEngineDpgConfig
from dev_kit.schemas.dpg.memory_layer import MemoryLayerDpgConfig
from dev_kit.schemas.dpg.trust_layer import TrustLayerDpgConfig
from dev_kit.schemas.dpg.action_gateway import ActionGatewayDpgConfig
from dev_kit.schemas.dpg.reach_layer import ReachLayerDpgConfig
from dev_kit.schemas.dpg.observability_layer import ObservabilityLayerDpgConfig


# -- agent_core --------------------------------------------------------------

def test_agent_core_dpg_port_range():
    base = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "agent": {},
        "ke_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "memory_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "trust_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "learning_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "action_gateway_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "reach_layer": {},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    AgentCoreDpgConfig.model_validate(base)
    base["server"]["port"] = 0
    with pytest.raises(ValidationError):
        AgentCoreDpgConfig.model_validate(base)


def test_agent_core_dpg_endpoint_must_be_url():
    base = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "agent": {},
        "ke_client": {"endpoint": "not-a-url", "timeout_ms": 100},
        "memory_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "trust_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "learning_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "action_gateway_client": {"endpoint": "http://x:1", "timeout_ms": 100},
        "reach_layer": {},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    with pytest.raises(ValidationError):
        AgentCoreDpgConfig.model_validate(base)


# -- memory_layer ------------------------------------------------------------

def test_memory_layer_dpg_redis_port_range():
    base = {
        "server": {"host": "0.0.0.0", "port": 8002},
        "redis": {"host": "redis", "port": 6379},
        "memgraph": {"uri": "bolt://memgraph:7687", "user": "memgraph"},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    MemoryLayerDpgConfig.model_validate(base)
    base["redis"]["port"] = 99999
    with pytest.raises(ValidationError):
        MemoryLayerDpgConfig.model_validate(base)


def test_memory_layer_dpg_memgraph_uri_must_be_bolt():
    base = {
        "server": {"host": "0.0.0.0", "port": 8002},
        "redis": {"host": "redis"},
        "memgraph": {"uri": "http://wrong-protocol", "user": "memgraph"},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    with pytest.raises(ValidationError):
        MemoryLayerDpgConfig.model_validate(base)


# -- knowledge_engine --------------------------------------------------------

def test_knowledge_engine_dpg_minimal():
    base = {
        "server": {"host": "0.0.0.0", "port": 8001},
        "knowledge": {"blocks": {}},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    KnowledgeEngineDpgConfig.model_validate(base)


# -- trust_layer -------------------------------------------------------------

def test_trust_layer_dpg_default_dignity_disabled():
    base = {
        "server": {"host": "0.0.0.0", "port": 8003},
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    cfg = TrustLayerDpgConfig.model_validate(base)
    assert cfg.dignity_check.enabled is False


# -- action_gateway ----------------------------------------------------------

def test_action_gateway_dpg_minimal():
    base = {
        "server": {"host": "0.0.0.0", "port": 9999},
        "tools": [],
        "observability": {"otel": {"collector_endpoint": "http://x:1"}},
    }
    ActionGatewayDpgConfig.model_validate(base)


# -- reach_layer -------------------------------------------------------------

def test_reach_layer_dpg_top_level_wrapper_required():
    """reach_layer DPG yaml has a top-level reach_layer key."""
    raw = {"reach_layer": {
        "common": {
            "agent_core_client": {"endpoint": "http://x:1", "timeout_s": 30.0},
            "memory_layer_client": {"endpoint": "http://x:1", "timeout_s": 10.0},
            "observability": {"otel": {"collector_endpoint": "http://x:1"}},
        },
        "channels": {
            "cli": {},
            "web": {},
            "voice": {
                "vobiz": {"auth_id": "x", "auth_token": "y"},
                "raya": {"api_key": "k", "stt_wss_url": "https://x", "tts_base_url": "https://x"},
                "agent_core": {"base_url": "http://agent_core:8000"},
            },
        },
    }}
    ReachLayerDpgConfig.model_validate(raw)


# -- observability_layer -----------------------------------------------------

def test_observability_layer_dpg_audit_retention_positive():
    base = {
        "server": {"host": "0.0.0.0", "port": 8004},
        "observability": {
            "otel": {"collector_endpoint": "http://x:1"},
            "audit": {"retention_days": 0},
        },
    }
    with pytest.raises(ValidationError):
        ObservabilityLayerDpgConfig.model_validate(base)


def test_observability_layer_dpg_sli_block_rate_max_1():
    base = {
        "server": {"host": "0.0.0.0", "port": 8004},
        "observability": {
            "otel": {"collector_endpoint": "http://x:1"},
            "sli": {"trust_block_rate_max": 1.5},
        },
    }
    with pytest.raises(ValidationError):
        ObservabilityLayerDpgConfig.model_validate(base)
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/dpg/test_dpg_schemas.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/dpg/_shared.py`**

```python
"""Shared DPG sub-models reused across blocks."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class ServerConfig(BaseModel):
    """Uvicorn bind settings."""
    model_config = ConfigDict(extra="forbid")
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)


class OtelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collector_endpoint: str = Field(..., pattern=r"^https?://")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0, le=300000)


class ClientConfig(BaseModel):
    """HTTP client to another DPG block."""
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(..., pattern=r"^https?://")
    timeout_ms: int = Field(..., gt=0, le=60000)


class ObservabilityDpg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    otel: OtelConfig
```

- [ ] **Step 4: Implement remaining DPG schemas**

The full source for all 7 DPG schemas is in [spec section 7](../specs/2026-05-04-devkit-pydantic-schema-design.md#7-dpg-schemas-operator-facing--deploy-wizard-validation). Copy the schema from each subsection into the matching file:

- 7.1 → `dev-kit/dev_kit/schemas/dpg/agent_core.py`
- 7.2 → `dev-kit/dev_kit/schemas/dpg/knowledge_engine.py`
- 7.3 → `dev-kit/dev_kit/schemas/dpg/memory_layer.py`
- 7.4 → `dev-kit/dev_kit/schemas/dpg/trust_layer.py`
- 7.5 → `dev-kit/dev_kit/schemas/dpg/action_gateway.py`
- 7.6 → `dev-kit/dev_kit/schemas/dpg/reach_layer.py`
- 7.7 → `dev-kit/dev_kit/schemas/dpg/observability_layer.py`

In each, replace the spec's `from dev_kit.schemas.dpg.agent_core import ServerConfig, OtelConfig` import with `from dev_kit.schemas.dpg._shared import ServerConfig, OtelConfig, ClientConfig, ObservabilityDpg` (we factored the shared types into `_shared.py`).

- [ ] **Step 5: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/dpg/test_dpg_schemas.py -v
```
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/schemas/dpg/ \
        dev-kit/tests/schemas/dpg/
git commit -m "feat(devkit): add DPG framework schemas for deploy wizard validation"
```

---

### Task 10: Validation module — section dispatch + DPG dispatch + error formatter

**Files:**
- Create: `dev-kit/dev_kit/schemas/validation.py`
- Create: `dev-kit/tests/schemas/test_validation.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/schemas/test_validation.py`:

```python
"""Tests for the central validation entry points."""
import pytest

from dev_kit.schemas.validation import (
    DOMAIN_SECTION_SCHEMAS,
    DPG_BLOCK_SCHEMAS,
    validate_domain_section,
    validate_dpg_block,
)


# -- validate_domain_section -------------------------------------------------

def test_unknown_block_returns_error():
    err = validate_domain_section("nope", "agent", {})
    assert err is not None
    assert "Unknown" in err


def test_valid_agent_section_returns_none():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert err is None


def test_invalid_agent_section_returns_error_with_type_and_value():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001",
         "max_tool_rounds": 0},
    )
    assert err is not None
    assert "max_tool_rounds" in err
    assert "[greater_than_equal]" in err  # error type code
    assert "you sent: 0" in err            # offending value


def test_extra_field_returns_extra_forbidden():
    err = validate_domain_section(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001",
         "vector_store": "bogus"},
    )
    assert err is not None
    assert "extra_forbidden" in err
    assert "vector_store" in err


def test_dotted_section_path_uses_top_level():
    """update_config(section='preprocessing.nlu_processor', ...) validates against PreprocessingSection."""
    err = validate_domain_section(
        "agent_core", "preprocessing.nlu_processor",
        {"language_normalisation": {
            "model": "claude-sonnet-4-6",
            "default_language": "english",
            "supported_languages": ["english"],
         },
         "nlu_processor": {"model": "claude-sonnet-4-6"}},
    )
    assert err is None


# -- validate_dpg_block ------------------------------------------------------

def test_dpg_unknown_block():
    err = validate_dpg_block("nope", {})
    assert err is not None
    assert "Unknown block" in err


def test_dpg_invalid_returns_formatted_error():
    err = validate_dpg_block("memory_layer", {
        "server": {"port": 99999},
        "redis": {"host": "x"},
        "memgraph": {"uri": "bolt://x", "user": "u"},
        "observability": {"otel": {"collector_endpoint": "http://x"}},
    })
    assert err is not None
    assert "server.port" in err


# -- Dispatch tables ---------------------------------------------------------

def test_all_seven_blocks_have_dpg_schemas():
    expected = {
        "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }
    assert set(DPG_BLOCK_SCHEMAS.keys()) == expected


def test_domain_dispatch_covers_critical_sections():
    """Spot-check that key sections are mapped."""
    assert ("agent_core", "agent") in DOMAIN_SECTION_SCHEMAS
    assert ("agent_core", "agent_workflow") in DOMAIN_SECTION_SCHEMAS
    assert ("knowledge_engine", "knowledge") in DOMAIN_SECTION_SCHEMAS
    assert ("memory_layer", "state") in DOMAIN_SECTION_SCHEMAS
    assert ("trust_layer", "trust") in DOMAIN_SECTION_SCHEMAS
    assert ("trust_layer", "dignity_check") in DOMAIN_SECTION_SCHEMAS
    assert ("action_gateway", "tools") in DOMAIN_SECTION_SCHEMAS
    assert ("reach_layer", "reach_layer") in DOMAIN_SECTION_SCHEMAS
    assert ("observability_layer", "observability") in DOMAIN_SECTION_SCHEMAS
```

- [ ] **Step 2: Run test to verify fail**

```
cd dev-kit && uv run pytest tests/schemas/test_validation.py -v
```
Expected: `ModuleNotFoundError: No module named 'dev_kit.schemas.validation'`

- [ ] **Step 3: Implement `dev-kit/dev_kit/schemas/validation.py`**

```python
"""Central validation entry points used by the accumulator and deploy wizard endpoint.

Two main functions:
- validate_domain_section(block, section, merged_data) — for the LLM tool handler
- validate_dpg_block(block, parsed_yaml) — for operator edits in deploy wizard
"""
from __future__ import annotations
from typing import Optional
from pydantic import ValidationError

from dev_kit.schemas.domain import (
    agent_core,
    knowledge_engine,
    memory_layer,
    trust_layer,
    action_gateway,
    reach_layer,
    observability_layer,
)
from dev_kit.schemas.dpg.agent_core import AgentCoreDpgConfig
from dev_kit.schemas.dpg.knowledge_engine import KnowledgeEngineDpgConfig
from dev_kit.schemas.dpg.memory_layer import MemoryLayerDpgConfig
from dev_kit.schemas.dpg.trust_layer import TrustLayerDpgConfig
from dev_kit.schemas.dpg.action_gateway import ActionGatewayDpgConfig
from dev_kit.schemas.dpg.reach_layer import ReachLayerDpgConfig
from dev_kit.schemas.dpg.observability_layer import ObservabilityLayerDpgConfig


# Dispatch: (block, top_level_section) → Pydantic schema class
DOMAIN_SECTION_SCHEMAS: dict[tuple[str, str], type] = {
    ("agent_core", "agent"): agent_core.AgentSection,
    ("agent_core", "preprocessing"): agent_core.PreprocessingSection,
    ("agent_core", "conversation"): agent_core.ConversationSection,
    ("agent_core", "channels"): agent_core.ChannelsSection,
    ("agent_core", "connectors"): agent_core.ConnectorsSection,
    ("agent_core", "agent_workflow"): agent_core.AgentWorkflowSection,
    ("agent_core", "entity_to_profile_field"): agent_core.EntityToProfileFieldSection,
    ("agent_core", "hitl"): agent_core.HitlSection,
    ("agent_core", "observability"): agent_core.ObservabilitySection,
    ("knowledge_engine", "knowledge"): knowledge_engine.KnowledgeSection,
    ("knowledge_engine", "observability"): knowledge_engine.ObservabilitySection,
    ("memory_layer", "state"): memory_layer.StateSection,
    ("memory_layer", "user_data_persistence"): memory_layer.UserDataPersistenceSection,
    ("memory_layer", "reengagement"): memory_layer.ReengagementSection,
    ("memory_layer", "observability"): memory_layer.ObservabilitySection,
    ("trust_layer", "trust"): trust_layer.TrustSection,
    ("trust_layer", "dignity_check"): trust_layer.DignityCheckSection,
    ("trust_layer", "observability"): trust_layer.ObservabilitySection,
    ("action_gateway", "tools"): action_gateway.ToolsSection,
    ("action_gateway", "observability"): action_gateway.ObservabilitySection,
    ("reach_layer", "reach_layer"): reach_layer.ReachLayerSection,
    ("observability_layer", "observability"): observability_layer.ObservabilitySection,
}

DPG_BLOCK_SCHEMAS: dict[str, type] = {
    "agent_core": AgentCoreDpgConfig,
    "knowledge_engine": KnowledgeEngineDpgConfig,
    "memory_layer": MemoryLayerDpgConfig,
    "trust_layer": TrustLayerDpgConfig,
    "action_gateway": ActionGatewayDpgConfig,
    "reach_layer": ReachLayerDpgConfig,
    "observability_layer": ObservabilityLayerDpgConfig,
}


def validate_domain_section(block: str, section: str, merged_data: dict) -> Optional[str]:
    """Validate a domain section's merged data.

    Args:
        block: Block name (e.g. "agent_core").
        section: Dot-notation path. Only the first segment is used to look up
            the schema; nested writes are validated against the parent section.
        merged_data: The full top-level section dict after deep-merge.

    Returns:
        None if valid; a formatted error string if invalid.
    """
    top_level = section.split(".", 1)[0]
    schema = DOMAIN_SECTION_SCHEMAS.get((block, top_level))
    if schema is None:
        return f"Unknown section '{section}' for block '{block}'"
    try:
        schema.model_validate(merged_data)
        return None
    except ValidationError as e:
        return _format_pydantic_error(e)


def validate_dpg_block(block: str, parsed_yaml: dict) -> Optional[str]:
    """Validate a full DPG framework YAML against its schema.

    Args:
        block: Block name.
        parsed_yaml: The full YAML parsed to dict.

    Returns:
        None if valid; a formatted error string if invalid.
    """
    schema = DPG_BLOCK_SCHEMAS.get(block)
    if schema is None:
        return f"Unknown block '{block}'"
    try:
        schema.model_validate(parsed_yaml)
        return None
    except ValidationError as e:
        return _format_pydantic_error(e)


def _format_pydantic_error(err: ValidationError) -> str:
    """Render a ValidationError as a single human-readable string for LLM/operator feedback.

    Includes the error type code, field path, message, and the offending input value
    so the LLM can self-correct on retry without resending the same wrong value.
    """
    lines = []
    for e in err.errors():
        path = ".".join(str(p) for p in e["loc"]) or "<root>"
        err_type = e.get("type", "unknown")
        msg = e.get("msg", "")
        offending = e.get("input")
        if offending is None:
            value_hint = ""
        else:
            try:
                rendered = repr(offending)
                if len(rendered) > 200:
                    rendered = rendered[:200] + "...<truncated>"
                value_hint = f" (you sent: {rendered})"
            except Exception:
                value_hint = ""
        lines.append(f"- {path} [{err_type}]: {msg}{value_hint}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify pass**

```
cd dev-kit && uv run pytest tests/schemas/test_validation.py -v
```
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/validation.py \
        dev-kit/tests/schemas/test_validation.py
git commit -m "feat(devkit): add central validation entry points for domain and DPG"
```

---

### Task 11: Round-trip tests against existing configs

This is the safety net: every existing project's domain config must validate, and every DPG YAML must validate. Catches schema-vs-reality mismatches before any integration touches production.

**Files:**
- Create: `dev-kit/tests/schemas/test_existing_configs_validate.py`
- Create: `dev-kit/tests/schemas/test_dpg_yamls_validate.py`

- [ ] **Step 1: Write the round-trip test for domain configs**

Create `dev-kit/tests/schemas/test_existing_configs_validate.py`:

```python
"""Round-trip: every config under dev-kit/configs/<domain>/<block>.yaml must validate.

If a domain's existing YAML rejects, either the schema is too strict or the YAML is wrong.
Either way, this test catches the mismatch before the schema is wired into the wizard.
"""
from pathlib import Path
import yaml
import pytest

from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS

CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs"
BLOCKS = [
    "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
    "action_gateway", "reach_layer", "observability_layer",
]


def _domain_dirs() -> list[Path]:
    if not CONFIGS_DIR.exists():
        return []
    return [p for p in CONFIGS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")]


def _validate_each_top_level_section(block: str, data: dict) -> list[str]:
    """Validate every top-level section of a parsed YAML against its domain schema."""
    errors = []
    for top_level, value in data.items():
        schema = DOMAIN_SECTION_SCHEMAS.get((block, top_level))
        if schema is None:
            errors.append(f"unmapped section: {top_level}")
            continue
        try:
            schema.model_validate(value)
        except Exception as exc:
            errors.append(f"{top_level}: {exc}")
    return errors


@pytest.mark.parametrize("domain_dir", _domain_dirs(), ids=lambda p: p.name)
@pytest.mark.parametrize("block", BLOCKS)
def test_domain_block_validates(domain_dir: Path, block: str):
    """Every existing domain config must validate against the new domain schemas."""
    yaml_path = domain_dir / f"{block}.yaml"
    if not yaml_path.exists():
        pytest.skip(f"{yaml_path} not present")
    raw = yaml_path.read_text()
    if not raw.strip() or raw.strip().startswith("#"):
        pytest.skip(f"{yaml_path} is empty or comment-only")
    data = yaml.safe_load(raw) or {}
    if not data:
        pytest.skip(f"{yaml_path} parses to empty dict")
    errors = _validate_each_top_level_section(block, data)
    assert not errors, f"{domain_dir.name}/{block}.yaml validation errors:\n  " + "\n  ".join(errors)
```

- [ ] **Step 2: Write the round-trip test for DPG YAMLs**

Create `dev-kit/tests/schemas/test_dpg_yamls_validate.py`:

```python
"""Round-trip: every dev-kit/dpg/<block>.yaml must validate against its DPG schema."""
from pathlib import Path
import yaml
import pytest

from dev_kit.schemas.validation import validate_dpg_block

DPG_DIR = Path(__file__).parent.parent.parent / "dpg"
BLOCKS = [
    "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
    "action_gateway", "reach_layer", "observability_layer",
]


@pytest.mark.parametrize("block", BLOCKS)
def test_dpg_yaml_validates(block: str):
    """Every framework default YAML must conform to its DPG schema."""
    yaml_path = DPG_DIR / f"{block}.yaml"
    assert yaml_path.exists(), f"missing DPG yaml: {yaml_path}"
    raw = yaml_path.read_text()
    parsed = yaml.safe_load(raw) or {}
    error = validate_dpg_block(block, parsed)
    assert error is None, f"{yaml_path} validation errors:\n{error}"
```

- [ ] **Step 3: Run tests — expect failures revealing real-world gaps**

```
cd dev-kit && uv run pytest tests/schemas/test_existing_configs_validate.py tests/schemas/test_dpg_yamls_validate.py -v
```

Expected: some failures. Each failure is a signal to either:
- **Adjust the schema** (it was too strict — e.g., a default value was missing)
- **Fix the YAML** (the schema is correct and the YAML is genuinely broken)

Read each failure and decide. Common adjustments:
- A required field needs a sensible default
- A `min_length` is too aggressive
- A pattern is too restrictive
- An optional sub-section needs to default to `None` (Optional[X])

Iterate: fix one failure at a time, re-run, repeat until green.

- [ ] **Step 4: Once green, commit**

```bash
git add dev-kit/tests/schemas/test_existing_configs_validate.py \
        dev-kit/tests/schemas/test_dpg_yamls_validate.py \
        dev-kit/dev_kit/schemas/  # any schema fixes from step 3
git commit -m "test(devkit): round-trip validation against existing configs and DPG YAMLs"
```

---

### Task 12: Accumulator integration — validation hook + retry counter

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Create: `dev-kit/tests/agent/test_accumulator_validation.py`

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/agent/test_accumulator_validation.py`:

```python
"""Tests for the new validation hook + retry counter on ConfigAccumulator."""
import os
import pytest

from dev_kit.agent.accumulator import ConfigAccumulator


@pytest.fixture(autouse=True)
def enable_strict():
    """Tests run with strict validation enabled."""
    old = os.environ.get("DEVKIT_DPG_SCHEMA_STRICT")
    os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = "1"
    yield
    if old is None:
        os.environ.pop("DEVKIT_DPG_SCHEMA_STRICT", None)
    else:
        os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = old


def test_valid_update_returns_ok():
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert result == "OK"


def test_invalid_update_returns_validation_error():
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert result.startswith("VALIDATION_ERROR")
    assert "must be different" in result
    assert "attempt 1/" in result


def test_counter_increments_on_repeated_failures():
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    r1 = acc.update("agent_core", "agent", bad)
    r2 = acc.update("agent_core", "agent", bad)
    assert "attempt 1/" in r1
    assert "attempt 2/" in r2


def test_counter_caps_at_max():
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    for _ in range(3):
        acc.update("agent_core", "agent", bad)
    final = acc.update("agent_core", "agent", bad)
    assert "VALIDATION_FAILED_AFTER" in final


def test_counter_resets_on_success():
    acc = ConfigAccumulator()
    acc.update("agent_core", "agent",
               {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"})
    # Now successful update — counter should reset
    ok = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-haiku-4-5-20251001"},
    )
    assert ok == "OK"
    # Subsequent failure starts at attempt 1, not 2
    fail = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert "attempt 1/" in fail


def test_counter_independent_per_section():
    acc = ConfigAccumulator()
    bad_agent = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    acc.update("agent_core", "agent", bad_agent)  # 1/3
    acc.update("agent_core", "agent", bad_agent)  # 2/3
    # Other section's counter is independent
    other = acc.update("knowledge_engine", "observability", {"domain": ""})
    assert "attempt 1/" in other


def test_reset_counters_on_new_turn():
    acc = ConfigAccumulator()
    bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
    for _ in range(3):
        acc.update("agent_core", "agent", bad)
    acc.reset_validation_attempts()
    fresh = acc.update("agent_core", "agent", bad)
    assert "attempt 1/" in fresh


def test_strict_mode_disabled_skips_validation():
    """With DEVKIT_DPG_SCHEMA_STRICT=0, invalid values pass through."""
    os.environ["DEVKIT_DPG_SCHEMA_STRICT"] = "0"
    acc = ConfigAccumulator()
    result = acc.update(
        "agent_core", "agent",
        {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"},
    )
    assert result == "OK"


def test_max_attempts_env_override():
    """DEVKIT_VALIDATION_MAX_ATTEMPTS overrides the default of 3."""
    os.environ["DEVKIT_VALIDATION_MAX_ATTEMPTS"] = "1"
    try:
        acc = ConfigAccumulator()
        bad = {"primary_model": "claude-sonnet-4-6", "fallback_model": "claude-sonnet-4-6"}
        first = acc.update("agent_core", "agent", bad)
        assert "VALIDATION_FAILED_AFTER" in first  # cap is 1, immediate fallback
    finally:
        os.environ.pop("DEVKIT_VALIDATION_MAX_ATTEMPTS", None)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd dev-kit && uv run pytest tests/agent/test_accumulator_validation.py -v
```
Expected: fails because `update()` doesn't return error strings yet (currently returns nothing or different shape).

- [ ] **Step 3: Modify `dev-kit/dev_kit/agent/accumulator.py`**

Read the current `accumulator.py` to find the existing `update()` method. Then modify it as follows. Add at the top of the file:

```python
import os
from dev_kit.schemas.validation import validate_domain_section
```

Add to the `ConfigAccumulator.__init__`:

```python
self._validation_attempts: dict[tuple[str, str], int] = {}

# Read once at construction; tests that override env need a fresh accumulator.
self._max_validation_attempts: int = int(
    os.environ.get("DEVKIT_VALIDATION_MAX_ATTEMPTS", "3")
)
self._strict_mode: bool = os.environ.get("DEVKIT_DPG_SCHEMA_STRICT", "1") == "1"
```

Replace the existing `update()` method body with:

```python
def update(self, block: str, section: str, values: dict) -> str:
    """Merge values into block.section; validate; return 'OK' or error string."""
    if block not in BLOCKS:
        raise ValueError(f"Unknown block '{block}'")

    # ---- existing deep-merge logic (preserve as-is from current implementation)
    self._merge_section(block, section, values)
    # ----

    if not self._strict_mode:
        return "OK"

    top_level = section.split(".", 1)[0]
    merged_top = self._data[block].get(top_level, {})
    error = validate_domain_section(block, section, merged_top)

    key = (block, top_level)
    if error:
        attempt = self._validation_attempts.get(key, 0) + 1
        if attempt >= self._max_validation_attempts:
            self._validation_attempts[key] = attempt
            self.set_status(block, ConfigStatus.STALE)
            return (
                f"VALIDATION_FAILED_AFTER_{self._max_validation_attempts}_ATTEMPTS for "
                f"{block}.{top_level}:\n{error}\n\n"
                f"Tell the user we couldn't auto-configure this and ask for guidance, "
                f"OR call set_phase to advance and fix in Review phase."
            )
        self._validation_attempts[key] = attempt
        return f"VALIDATION_ERROR (attempt {attempt}/{self._max_validation_attempts}):\n{error}"

    self._validation_attempts.pop(key, None)
    return "OK"


def reset_validation_attempts(self) -> None:
    """Clear all per-section retry counters. Called by ConversationEngine on new user turn."""
    self._validation_attempts.clear()
```

Also update any callers of `update()` that previously didn't expect a string return. Use `grep -rn "_acc.update\|self._acc.update" dev-kit/dev_kit/agent/` to find them — most likely in `dev-kit/dev_kit/agent/tools.py` (the `update_config` tool handler). Update that handler to return the string from `update()` directly to the LLM via the tool result.

- [ ] **Step 4: Run accumulator tests**

```
cd dev-kit && uv run pytest tests/agent/test_accumulator_validation.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Run full devkit test suite to check no regressions**

```
cd dev-kit && uv run pytest -x
```
Expected: all pass. If tests in `test_accumulator.py` or `test_tools.py` fail because they expected `update()` to return something different, update those tests to assert on `"OK"` or the appropriate error string.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/accumulator.py \
        dev-kit/dev_kit/agent/tools.py \
        dev-kit/tests/agent/test_accumulator_validation.py
git commit -m "feat(devkit): wire schema validation into accumulator with retry counter"
```

---

### Task 13: Reset retry counter on each new user turn

**Files:**
- Modify: `dev-kit/dev_kit/agent/conversation.py`
- Create: `dev-kit/tests/agent/test_conversation_validation_reset.py`

- [ ] **Step 1: Write failing test**

Create `dev-kit/tests/agent/test_conversation_validation_reset.py`:

```python
"""ConversationEngine must reset accumulator validation counters on each new user turn."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_chat_resets_validation_counters_at_start_of_turn(tmp_path):
    """Every call to chat() resets the counters before any tool dispatch."""
    from dev_kit.agent.conversation import ConversationEngine

    client = MagicMock()
    # Make the LLM return a final text reply with no tool_use loop
    fake_response = MagicMock()
    fake_response.stop_reason = "end_turn"
    fake_response.content = []
    fake_response.model = "test"
    fake_response.usage.input_tokens = 1
    fake_response.usage.output_tokens = 1
    client.messages.create = AsyncMock(return_value=fake_response)

    engine = ConversationEngine(tmp_path, client)

    # Pre-poison the counter
    engine.accumulator._validation_attempts[("agent_core", "agent")] = 99

    await engine.chat("hello")

    # After chat() processes the user message, counter should be reset
    assert engine.accumulator._validation_attempts == {}
```

- [ ] **Step 2: Run test — should fail because reset isn't wired yet**

```
cd dev-kit && uv run pytest tests/agent/test_conversation_validation_reset.py -v
```
Expected: counter still contains the poisoned value.

- [ ] **Step 3: Modify `dev-kit/dev_kit/agent/conversation.py`**

Find the `chat()` method (around line 226). Immediately after the line that appends the user message to history:

```python
self._history.append({"role": "user", "content": user_message})
```

Add:

```python
self.accumulator.reset_validation_attempts()
```

- [ ] **Step 4: Run test to confirm pass**

```
cd dev-kit && uv run pytest tests/agent/test_conversation_validation_reset.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/conversation.py \
        dev-kit/tests/agent/test_conversation_validation_reset.py
git commit -m "feat(devkit): reset validation retry counters on each new user turn"
```

---

### Task 14: DPG framework values endpoint validation

**Files:**
- Modify: `dev-kit/dev_kit/agent/app.py`
- Create: `dev-kit/tests/agent/test_app_dpg_validation.py`

- [ ] **Step 1: Write failing test**

Create `dev-kit/tests/agent/test_app_dpg_validation.py`:

```python
"""Tests for the deploy wizard's DPG Framework Values endpoint validation.

PUT /api/projects/{slug}/deploy/dpg-values/{block} must reject content that
fails Pydantic validation, not just YAML parse errors.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DPG_DIR", str(tmp_path))
    monkeypatch.setenv("DEVKIT_DPG_SCHEMA_STRICT", "1")
    from dev_kit.agent.app import app
    return TestClient(app)


def test_invalid_yaml_returns_400(client):
    res = client.put(
        "/api/projects/x/deploy/dpg-values/agent_core",
        json={"content": "this is: not valid: yaml: ::"},
    )
    assert res.status_code == 400
    assert "Invalid YAML" in res.json()["detail"]


def test_unknown_block_returns_400(client):
    res = client.put(
        "/api/projects/x/deploy/dpg-values/bogus_block",
        json={"content": "key: value\n"},
    )
    assert res.status_code == 400


def test_schema_violation_returns_400(client):
    """Port out of range fails Pydantic validation."""
    res = client.put(
        "/api/projects/x/deploy/dpg-values/memory_layer",
        json={"content": "server:\n  port: 99999\nredis:\n  host: r\nmemgraph:\n  uri: bolt://m\n  user: u\nobservability:\n  otel:\n    collector_endpoint: http://x\n"},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "server.port" in detail


def test_valid_yaml_accepted(client, tmp_path):
    from dev_kit.agent.app import DPG_DIR
    valid = (
        "server:\n  host: 0.0.0.0\n  port: 8002\n"
        "redis:\n  host: redis\n  port: 6379\n"
        "memgraph:\n  uri: bolt://memgraph:7687\n  user: memgraph\n"
        "observability:\n  otel:\n    collector_endpoint: http://otelcol:4317\n"
    )
    res = client.put(
        "/api/projects/x/deploy/dpg-values/memory_layer",
        json={"content": valid},
    )
    assert res.status_code == 200, res.text
```

- [ ] **Step 2: Run test to confirm fail**

```
cd dev-kit && uv run pytest tests/agent/test_app_dpg_validation.py -v
```
Expected: `test_schema_violation_returns_400` fails because the endpoint currently only checks YAML parse, not schema.

- [ ] **Step 3: Modify `dev-kit/dev_kit/agent/app.py`**

Find the `update_dpg_value` endpoint. Update it from:

```python
@app.put("/api/projects/{slug}/deploy/dpg-values/{block}")
async def update_dpg_value(slug: str, block: str, body: dict) -> dict:
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    try:
        yaml.safe_load(body["content"])
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")
    path = DPG_DIR / f"{block}.yaml"
    path.write_text(body["content"])
    return {"status": "ok"}
```

To:

```python
@app.put("/api/projects/{slug}/deploy/dpg-values/{block}")
async def update_dpg_value(slug: str, block: str, body: dict) -> dict:
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    try:
        parsed = yaml.safe_load(body["content"]) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

    if os.environ.get("DEVKIT_DPG_SCHEMA_STRICT", "1") == "1":
        from dev_kit.schemas.validation import validate_dpg_block
        error = validate_dpg_block(block, parsed)
        if error:
            raise HTTPException(status_code=400, detail=f"Schema validation failed:\n{error}")

    path = DPG_DIR / f"{block}.yaml"
    path.write_text(body["content"])
    return {"status": "ok"}
```

(Add `import os` at the top of `app.py` if not already imported.)

- [ ] **Step 4: Run tests to confirm pass**

```
cd dev-kit && uv run pytest tests/agent/test_app_dpg_validation.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/agent/app.py \
        dev-kit/tests/agent/test_app_dpg_validation.py
git commit -m "feat(devkit): validate operator DPG edits against Pydantic schemas"
```

---

### Task 15: Renderer — delegate to new validation

**Files:**
- Modify: `dev-kit/dev_kit/agent/renderer.py`

- [ ] **Step 1: Identify what needs to change**

Open `dev-kit/dev_kit/agent/renderer.py` and find the call to `validate_partial(block, data)`. We're keeping `validate_partial` as a thin shim that now delegates to the new domain-section validators, so the renderer's existing STALE/COMPLETE/PENDING flow keeps working.

- [ ] **Step 2: Replace `validate_partial` with a thin wrapper**

Open `dev-kit/dev_kit/schema.py` (the existing thin schema file we'll fully delete in Task 17). Replace the `validate_partial(block, data)` function body with:

```python
def validate_partial(block: str, data: dict) -> list[str]:
    """Validate each top-level section of a block's data; return error messages.

    Thin wrapper that delegates to the new section-split schemas. Preserves the
    existing renderer interface (returns list[str] of error messages).
    """
    from dev_kit.schemas.validation import validate_domain_section
    errors: list[str] = []
    for top_level, value in data.items():
        if not isinstance(value, dict):
            continue
        err = validate_domain_section(block, top_level, value)
        if err:
            errors.append(err)
    return errors
```

This keeps the renderer's existing logic intact while routing through the new schemas.

- [ ] **Step 3: Run renderer tests**

```
cd dev-kit && uv run pytest tests/agent/test_renderer.py -v
```
Expected: all pass. If any fail, fix the schema (most likely a too-strict constraint) until they pass.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/schema.py
git commit -m "refactor(devkit): renderer validate_partial delegates to schemas package"
```

---

### Task 16: Inject Pydantic source code into phase prompts

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Create: `dev-kit/tests/agent/test_phase_prompts_use_schemas.py`

- [ ] **Step 1: Write failing test**

Create `dev-kit/tests/agent/test_phase_prompts_use_schemas.py`:

```python
"""Each phase prompt must inject the relevant section schemas as Pydantic source code."""
from dev_kit.agent.prompts.phases import get_phase_addition


def test_language_phase_includes_agent_section_source():
    text = get_phase_addition("language")
    assert "class AgentSection" in text
    assert "primary_model" in text
    # The constraint must be visible
    assert "ge=1" in text or "le=20" in text


def test_language_phase_includes_preprocessing_section():
    text = get_phase_addition("language")
    assert "class PreprocessingSection" in text or "class NLUProcessorSection" in text


def test_language_phase_includes_conversation_section():
    text = get_phase_addition("language")
    assert "class ConversationSection" in text


def test_knowledge_phase_includes_knowledge_section():
    text = get_phase_addition("knowledge")
    assert "class KnowledgeSection" in text
    assert "intent_filters" in text


def test_workflow_phase_includes_agent_workflow_section():
    text = get_phase_addition("workflow")
    assert "class AgentWorkflowSection" in text
    assert "class SubAgent" in text


def test_trust_phase_includes_dignity_check_section():
    text = get_phase_addition("trust")
    assert "class DignityCheckSection" in text


def test_memory_phase_includes_state_section():
    text = get_phase_addition("memory")
    assert "class StateSection" in text


def test_tools_phase_includes_tools_section():
    text = get_phase_addition("tools")
    assert "class ToolsSection" in text


def test_observability_phase_includes_outcomes():
    text = get_phase_addition("observability")
    assert "class OutcomesConfig" in text or "class ObservabilitySection" in text


def test_reach_phase_includes_reach_layer_section():
    text = get_phase_addition("reach")
    assert "class ReachLayerSection" in text or "class WebChannelSection" in text
```

- [ ] **Step 2: Run test — most assertions fail**

```
cd dev-kit && uv run pytest tests/agent/test_phase_prompts_use_schemas.py -v
```
Expected: failures because phase prompts currently inject blank YAML templates.

- [ ] **Step 3: Modify `dev-kit/dev_kit/agent/prompts/phases.py`**

Add at the top of the file:

```python
import inspect
from dev_kit.schemas.domain import (
    agent_core as ac_domain,
    knowledge_engine as ke_domain,
    memory_layer as ml_domain,
    trust_layer as tl_domain,
    action_gateway as ag_domain,
    reach_layer as rl_domain,
    observability_layer as obs_domain,
)


def _schema_source(*classes) -> str:
    """Render multiple Pydantic classes as a single code block."""
    return "\n\n".join(inspect.getsource(c) for c in classes)
```

For each phase, replace the existing `load_template_text(block)` injection with the Pydantic source for the relevant section schemas. Use this mapping (from spec section 5):

| Phase | Section schemas to inject |
|---|---|
| `language` | `ac_domain.AgentSection`, `ac_domain.LanguageNormalisationSection`, `ac_domain.NLUProcessorSection`, `ac_domain.PreprocessingSection`, `ac_domain.ConversationSection`, `ac_domain.ChannelsSection`, `ac_domain.HitlSection` |
| `knowledge` | `ke_domain.StaticKnowledgeBaseSection`, `ke_domain.KnowledgeBlocksSection`, `ke_domain.KnowledgeSection`, `ac_domain.InternalConnectorDef`, `ac_domain.ConnectorsSection` |
| `memory` | `ml_domain.SessionFieldDefinition`, `ml_domain.SessionStateConfig`, `ml_domain.GraphConfig`, `ml_domain.PersistentStateConfig`, `ml_domain.StateSection`, `ml_domain.UserDataPersistenceSection`, `ml_domain.ReengagementSection` |
| `user_state` | `ac_domain.UserStateDefinition`, `ac_domain.UserStateModel` |
| `trust` | `tl_domain.TrustSection`, `tl_domain.DignityCheckSection` |
| `tools` | `ag_domain.ToolDefinition`, `ag_domain.EndpointDefinition`, `ag_domain.ParamDefinition`, `ag_domain.AuthConfig`, `ag_domain.ToolsSection`, `ac_domain.InvocationRules`, `ac_domain.ConnectorDef`, `ac_domain.ConnectorsSection` |
| `workflow` | `ac_domain.RoutingRule`, `ac_domain.SubAgent`, `ac_domain.AgentWorkflowSection` |
| `observability` | `obs_domain.LifecycleState`, `obs_domain.MetricDefinition`, `obs_domain.OutcomesConfig`, `obs_domain.ObservabilitySection` |
| `reach` | `rl_domain.WebUiConfig`, `rl_domain.WebChannelSection`, `rl_domain.RayaVoiceConfig`, `rl_domain.VoiceAgentCoreClient`, `rl_domain.VoiceChannelSection`, `rl_domain.ChannelsSection`, `rl_domain.ReachLayerSection` |
| `tier`, `overview`, `review` | (no schema injection needed) |

For each phase block in `phases.py`, find the line containing `load_template_text(block)` or `_extract_template_sections(...)` and replace with:

```python
"### Schema for sections you will configure in this phase\n\n"
"All update_config calls must produce values that conform to these Pydantic models. "
"Constraints (ge, le, enum, model_validator) are enforced by the tool handler — "
"the call will fail if you violate them.\n\n"
"```python\n"
+ _schema_source(<the relevant classes from the table above>)
+ "\n```\n"
```

- [ ] **Step 4: Run tests**

```
cd dev-kit && uv run pytest tests/agent/test_phase_prompts_use_schemas.py -v
```
Expected: 10 passed.

Then run the full devkit test suite:

```
cd dev-kit && uv run pytest -x
```
Expected: all pass.

- [ ] **Step 5: Manual smoke-check the prompts**

Run a one-off Python script to print one prompt and eyeball its quality:

```bash
cd dev-kit && uv run python -c "
from dev_kit.agent.prompts.phases import get_phase_addition
print(get_phase_addition('language')[:3000])
"
```
Expected: Pydantic class source visible, no Python syntax errors, no extra YAML mixed in.

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py \
        dev-kit/tests/agent/test_phase_prompts_use_schemas.py
git commit -m "feat(devkit): inject Pydantic schemas into phase prompts (replaces YAML templates)"
```

---

### Task 17: Cleanup — remove old schema.py and template helpers

**Files:**
- Delete: `dev-kit/dev_kit/schema.py` (old thin schema, fully superseded)
- Delete or simplify: `dev-kit/dev_kit/schemas/loader.py` (template YAML loader — only kept if still imported elsewhere)
- Modify: any file still importing from `dev_kit.schema`

- [ ] **Step 1: Find all imports of the old schema**

```
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && grep -rn "from dev_kit.schema import\|from dev_kit\.schema " dev-kit/ --include="*.py" | grep -v ".venv"
```

Note every importer.

- [ ] **Step 2: Update each importer**

For each file printed in Step 1, replace `from dev_kit.schema import X` with the equivalent import from `dev_kit.schemas`. The most likely targets:
- `validate_partial` — already kept as a shim during Task 15; if so, also rename file to keep the function importable (or move it into `dev_kit.schemas.validation` and update the shim's last importer).
- Sub-models like `AgentCoreConfig`, `ConnectorDef` — replace with `dev_kit.schemas.dpg.agent_core.AgentCoreDpgConfig` (DPG side) or `dev_kit.schemas.domain.agent_core.ConnectorDef` (domain side).

- [ ] **Step 3: Find all imports of the old template loader**

```
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && grep -rn "from dev_kit.schemas.loader import\|load_template_text\|get_valid_sections" dev-kit/ --include="*.py" | grep -v ".venv"
```

- [ ] **Step 4: Replace each `get_valid_sections(block)` call**

These are used in `tools.py` to compute the `update_config` description. Replace with a function based on `DOMAIN_SECTION_SCHEMAS`:

In `dev-kit/dev_kit/schemas/validation.py`, add:

```python
def get_valid_sections(block: str) -> list[str]:
    """Return the top-level section names defined for a block in the domain schemas."""
    return sorted(
        {section for (b, section) in DOMAIN_SECTION_SCHEMAS.keys() if b == block}
    )
```

Update `tools.py` import:

```python
from dev_kit.schemas.validation import get_valid_sections
```

- [ ] **Step 5: Delete old files**

Once nothing references them:

```bash
rm dev-kit/dev_kit/schema.py
# Only delete loader.py if Step 3 found nothing using its other functions
```

- [ ] **Step 6: Run full devkit test suite**

```
cd dev-kit && uv run pytest -x
```
Expected: all pass.

- [ ] **Step 7: Smoke-test the deploy wizard end-to-end**

Start the devkit stack and run through one project's wizard manually:

```bash
cd automation/docker && docker compose -f docker-compose.dev.yml up -d devkit
# Visit http://localhost:3030 and click through a new project's wizard
```

Verify:
- Phase prompts mention schema constraints (visible in the chat UI)
- An intentionally-bad value (e.g., `max_tool_rounds: 0`) is rejected with the typed error string
- The deploy wizard's "DPG Framework Values" step rejects invalid YAML edits with a clear message
- Existing project (e.g., kkb) still loads and deploys

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(devkit): remove old dev_kit.schema after schemas migration"
```

---

## Self-review checklist (controller responsibility before handoff)

Before invoking subagent-driven-development, verify:

- [ ] Every spec section 6.x (domain schemas) has a corresponding Task 2-8.
- [ ] Every spec section 7.x (DPG schemas) is covered by Task 9.
- [ ] Validation entry points (spec section 8.1) are in Task 10.
- [ ] Accumulator integration (spec section 8.2) is in Task 12.
- [ ] Conversation engine reset (mentioned in spec section 8.2 last paragraph) is in Task 13.
- [ ] App.py DPG endpoint integration (spec section 8.4) is in Task 14.
- [ ] Renderer integration (spec section 8.5) is in Task 15.
- [ ] Phase prompt schema injection (spec section 8.3) is in Task 16.
- [ ] Cleanup (spec section 8.6) is in Task 17.
- [ ] Round-trip tests (spec section 9, "Round-trip tests") are in Task 11.
- [ ] Retry counter env var (spec section 4) is in Task 12 step 3.
- [ ] Strict-mode env flag (spec section 10, migration) is in Task 12 step 3 + Task 14 step 3.

---

## Out of scope (deferred to follow-up plans)

- **Drift CI lint** (spec risk #2): a script that diffs `dev_kit/schemas/dpg/*.py` against runtime `<block>/src/schema/config.py` to flag schema drift. Nice to have, not required for this rollout.
- **Per-attempt LLM model escalation** (spec risk #6 follow-up): if the cap fails-fast too eagerly, a future plan could escalate to a more capable model. Current design defers to user.
