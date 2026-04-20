# This file will be deleted after the Knowledge Engine is implemented.

# Knowledge Engine — Implementation Plan

This document is the authoritative implementation reference for the Knowledge Engine DPG.
Read this before writing any code. All decisions here are final unless explicitly revised.

---

## 1. Role in the Framework

The Knowledge Engine is called by Agent Core at **Step 3 of every turn** — after the Trust Layer
clears the input and before the LLM call is made.

```
Agent Core: read state (Memory Layer)
Agent Core: safety check on input (Trust Layer)
→ Agent Core: assemble_prompt (Knowledge Engine)   ← HERE
Agent Core: LLM call #1
...
```

Agent Core calls one method:

```python
messages = knowledge_engine.assemble_prompt(
    session_id=session_id,
    user_message=turn_input.user_message,
    session_state=state,
)
```

The return value is `list[dict]` — the complete messages array passed directly to the LLM.

---

## 2. Interface Contract (Do Not Change)

Defined in `agent_core/src/interfaces/knowledge_engine.py`:

```python
class KnowledgeEngineBase(ABC):

    @abstractmethod
    def assemble_prompt(
        self,
        session_id: str,
        user_message: str,
        session_state: SessionState,
    ) -> list[dict]:
        """
        Returns complete messages list for the LLM call.
        Returns empty list only if user_message is empty — never raises.
        """
```

The concrete `KnowledgeEngine` class must inherit from `KnowledgeEngineBase` and implement
this method with the exact same signature.

### LLM usage rule

Knowledge Engine **must not** import `anthropic` directly or instantiate any Anthropic client.

The LLM client is always `HttpLLMWrapper`, injected at **construction time**:

```python
ke = KnowledgeEngine(config, llm=HttpLLMWrapper(proxy_url=config["knowledge"]["llm_proxy_url"]))
```

All internal LLM calls (NLU, language normalisation) use:
```python
self._llm.call(messages, tools=[], system="", model_override=<haiku_model>)
```

Use `model_override` to point to the fast/cheap NLU model from YAML config — never the primary
Sonnet model.

---

## 3. LLM Proxy Architecture

KE and Agent Core always run as **separate services** — even when developed in the same repo.
KE never holds an Anthropic API key and never calls the Anthropic API directly.

Agent Core exposes an internal HTTP endpoint that KE calls for all LLM work:
```
POST /internal/llm/call
Body:     { "messages": [...], "tools": [...], "system": "", "model_override": "" }
Response: { "content": "", "stop_reason": "", "tool_calls": [...] }
```

```
KE service  →  POST /internal/llm/call  →  Agent Core  →  Anthropic API
```

KE uses `HttpLLMWrapper(LLMWrapperBase)` which makes this HTTP call.
The proxy URL comes from YAML config — changing from local dev to production is a config change only,
zero code changes:

```yaml
# Local dev:
knowledge:
  llm_proxy_url: http://localhost:8000/internal/llm/call

# Production:
knowledge:
  llm_proxy_url: http://agent-core:8000/internal/llm/call
```

`HttpLLMWrapper` lives at `knowledge_engine/src/llm_proxy_client.py`.

---

## 4. The 5 Internal Blocks

Knowledge Engine processes every user message through 5 blocks in a fixed order.
Each block enriches a shared `KEContext` object. The final prompt is assembled after all blocks complete.

### Fixed execution order (cannot be changed by config):
```
[1] Language Normalisation  →  normalise input, detect language
[2] NLU Processor           →  extract intent, entities, sentiment
[3] Glossary                →  map colloquial terms to canonical concepts
[4] Static Knowledge Base   →  RAG retrieval from ChromaDB
[5] Multimodal Handler      →  extract text from PDF/image inputs (if enabled)
```

Each block can be individually **enabled/disabled** via YAML config.
If a block is disabled, the engine skips it and passes `KEContext` unchanged to the next block.
Block order is enforced by the engine regardless of declaration order in YAML.

---

## 5. KEContext — Shared Data Object

All 5 blocks read from and write to a single `KEContext` dataclass.
It is created at the start of `assemble_prompt()` and passed through each block in sequence.

```python
@dataclass
class KEContext:
    session_id: str
    raw_input: str                      # original user_message, never modified
    normalised_input: str               # set by Language Normalisation; downstream blocks use this
    detected_language: str              # set by Language Normalisation: "hindi" | "kannada" | "english" | "hinglish"
    intent: str                         # set by NLU Processor: one of the configured intent values
    entities: dict[str, Any]           # set by NLU Processor: { "trade": "electrician", "location": "Hubli", ... }
    sentiment: str                      # set by NLU Processor: "neutral" | "positive" | "distressed" | "frustrated"
    confidence: float                   # set by NLU Processor: 0.0–1.0
    retrieval_chunks: list[dict]        # set by Static Knowledge Base: list of { "text": str, "metadata": dict }
    always_include_chunks: list[dict]   # set by Static Knowledge Base: chunks with type: always_include
    session_state: SessionState         # passed through unchanged; used by prompt builder for history
```

`KEContext` lives entirely inside the Knowledge Engine. It is never exposed to Agent Core.
Agent Core only receives the final `list[dict]` return value.

---

## 6. Block Specifications

---

### Block 1 — Language Normalisation

**File:** `knowledge_engine/src/blocks/language_normalisation.py`
**Class:** `LanguageNormalisationBlock`

**Purpose:** Normalise the raw user input to a consistent form before any retrieval or classification.
KKB users speak Hindi, Hinglish (mixed Hindi-English), Kannada, and Roman-script Hindi.

**How it works (PoC):**
- Provider `llm_native`: single `self._llm.call()` using `model_override` pointing to the NLU model
  from YAML. Prompt asks the model to detect language, transliterate Roman-script Hindi to Devanagari
  concepts, and return normalised text.
- Provider is read from `knowledge.blocks.language_normalisation.provider` in YAML.

**Writes to KEContext:**
- `context.normalised_input` — the cleaned, normalised text
- `context.entities["detected_language"]` — detected language string

**DB calls:** None

**YAML config section:**
```yaml
knowledge:
  blocks:
    language_normalisation:
      enabled: true
      supported_languages: [hindi, kannada, english, hinglish]
      provider: llm_native          # llm_native
      transliteration: true
      code_switching: true
```

**LLM call:** Yes — `model_override` = `knowledge.blocks.nlu_processor.model` from YAML config (Haiku).

**Tests to write (`test_language_normalisation.py`):**
- Pure Hindi input → `detected_language = "hindi"`, `normalised_input` is unchanged
- Pure English input → `detected_language = "english"`
- Hinglish mix ("electrician ka kaam chahiye") → `detected_language = "hinglish"`, normalised
- Kannada input → `detected_language = "kannada"`
- Roman-script Hindi ("bijli ka kaam chahiye") → transliterated concept in normalised output
- LLM call failure → returns `KEContext` with `normalised_input = raw_input` (graceful degradation)

---

### Block 2 — NLU Processor

**File:** `knowledge_engine/src/blocks/nlu_processor.py`
**Class:** `NLUProcessorBlock`

**Purpose:** Classify user intent, extract entities, and detect sentiment.
This drives what RAG retrieves (Block 4) and what tool the LLM is likely to call.

**How it works (PoC):**
Single `self._llm.call()` using `model_override` pointing to the NLU model from YAML.
Uses a structured output prompt that returns JSON:
```json
{
  "intent": "market_truth_query",
  "entities": { "trade": "electrician", "location": "Hubli" },
  "sentiment": "neutral",
  "confidence": 0.92
}
```
Parses the JSON response and writes fields to `KEContext`.
On JSON parse failure, falls back to `intent = "unknown"`, `confidence = 0.0`.

**Intent values (from YAML config — not hardcoded):**

| Intent | Meaning |
|---|---|
| `market_truth_query` | User wants job/salary market information |
| `scheme_query` | User asking about PMKVY, NAPS, or other schemes |
| `training_query` | User asking about courses or training institutes |
| `apply_now` | User wants to submit an application |
| `counsellor_request` | User wants to speak to a human counsellor |
| `pay_range_query` | User asking about salary/income |
| `unknown` | Fallback when no intent can be determined |

**Entity types (from YAML config):** `trade`, `location`, `distance_km`, `income_urgency`

**Writes to KEContext:**
- `context.intent`
- `context.entities` (merged with existing, does not overwrite `detected_language`)
- `context.sentiment`
- `context.confidence`

**DB calls:** None

**YAML config section:**
```yaml
knowledge:
  blocks:
    nlu_processor:
      enabled: true
      model: claude-haiku-4-5-20251001
      intents:
        - market_truth_query
        - scheme_query
        - training_query
        - apply_now
        - counsellor_request
        - pay_range_query
        - unknown
      entities: [trade, location, distance_km, income_urgency]
      sentiment_classes: [neutral, positive, distressed, frustrated]
```

**LLM call:** Yes — `model_override` = `knowledge.blocks.nlu_processor.model` from YAML config.

**Tests to write (`test_nlu_processor.py`):**
- "kaam chahiye Hubli mein" → `intent = market_truth_query`, `entities.location = Hubli`
- "PMKVY ke baare mein batao" → `intent = scheme_query`
- "electrician course kahan hai" → `intent = training_query`, `entities.trade = electrician`
- "apply kar do" → `intent = apply_now`
- "counsellor chahiye" → `intent = counsellor_request`
- "kitna milega" → `intent = pay_range_query`
- Distress input ("bahut mushkil hai") → `sentiment = distressed`
- LLM returns malformed JSON → falls back to `intent = unknown`, `confidence = 0.0`
- LLM call fails → falls back to `intent = unknown`, no exception raised

---

### Block 3 — Glossary & Domain Vocabulary

**File:** `knowledge_engine/src/blocks/glossary.py`
**Class:** `GlossaryBlock`

**Purpose:** Map colloquial user terms to canonical domain concepts before retrieval queries are formed.
Runs after NLU so it can also normalise entity values extracted in Block 2.

**How it works:** Pure string matching — no LLM call, no DB call.
Reads mappings from `knowledge.blocks.glossary.mappings` in YAML config at construction time.

**Applies mappings to:**
- `context.normalised_input` — substring replacement
- Entity values in `context.entities` — normalises entity strings

**KKB glossary mappings (in YAML config):**

| User Says (Colloquial) | Canonical Term |
|---|---|
| "kaam chahiye", "naukri chahiye", "job chahiye" | `market_truth_query` |
| "ITI", "tradesman", "technician" | `iti_graduate` |
| "course", "training", "sikhai" | `training_query` |
| "apply kar do", "form bharo" | `apply_now` |
| "counsellor chahiye", "kisi se baat karni hai" | `counsellor_request` |
| "kitna milega", "salary kya hai", "pay kya hai" | `pay_range_query` |
| "electrician", "bijli wala" | `trade:electrician` |
| "fitter" | `trade:fitter` |
| "welder" | `trade:welder` |
| "PMKVY", "Pradhan Mantri Kaushal" | `scheme:pmkvy` |
| "Hubli", "Dharwad", "Belgaum" | `location:karnataka_north` |

**Writes to KEContext:**
- `context.normalised_input` (with canonical substitutions applied)
- `context.entities` (normalised entity values)

**DB calls:** None
**LLM calls:** None

**YAML config section:**
```yaml
knowledge:
  blocks:
    glossary:
      enabled: true
      mappings:
        - colloquial: ["kaam chahiye", "naukri chahiye", "job chahiye"]
          canonical: "market_truth_query"
        - colloquial: ["bijli wala", "electrician"]
          canonical: "trade:electrician"
        # ... all mappings
      apply_to: [normalised_input, entities]
```

**Tests to write (`test_glossary.py`):**
- Each of the 11 KKB mappings: colloquial input → correct canonical output
- Entity normalisation: `entities.trade = "bijli wala"` → `entities.trade = "electrician"`
- No match: input unchanged
- Glossary disabled in config: `KEContext` passes through unchanged
- Empty mappings list in config: no error, input unchanged

---

### Block 4 — Static Knowledge Base (RAG)

**File:** `knowledge_engine/src/blocks/static_knowledge_base.py`
**Class:** `StaticKnowledgeBaseBlock`

**Purpose:** Retrieve the most relevant knowledge chunks for the user's normalised query.
This is the core block — it provides the grounding context the LLM uses to answer accurately.

**Two modes:**

| Mode | When | How invoked |
|---|---|---|
| `ingest()` | Once offline, before first run | `python -m knowledge_engine.scripts.ingest` |
| `process()` | Every turn at runtime | Called by engine via `block.process(context, llm, config)` |

**Vector DB:** ChromaDB (local, in-process for PoC — no separate server).
Collection name: `kkb_knowledge` (from YAML config).

**Embedding provider:** Configurable via YAML — two supported options:

| Provider | YAML value | API Key Required | Quality for Hindi/Hinglish |
|---|---|---|---|
| OpenAI `text-embedding-3-small` | `openai` | `OPENAI_API_KEY` | Best — explicitly recommended in PoC plan for multilingual support |
| `paraphrase-multilingual-MiniLM-L12-v2` | `sentence_transformers` | None (local) | Good — runs fully offline, no API cost |

The provider and model name are both read from YAML config. Switching between them is a config-only change — zero code changes required.

The original PoC plan (Task 1.2, page 7) states: *"Use Anthropic embeddings or OpenAI text-embedding-3-small. Get OPENAI_API_KEY if using OpenAI."* and the risk register (page 23) adds: *"If ChromaDB embedding quality is poor for Hindi text, switch to OpenAI text-embedding-3-small which has better multilingual support."*

**Anthropic does not offer a public embeddings API** — the "Anthropic embeddings" option in the original doc is not usable. The two viable options are OpenAI (API, better quality) or sentence-transformers (local, no key needed).

**Documents to ingest:**

| File | Content | Chunk Strategy | Doc Type | Refresh |
|---|---|---|---|---|
| `data/labour_schemes.pdf` | PMKVY, NAPS, state schemes | Semantic by scheme section, ~300 tokens | `scheme` | Manual |
| `data/trade_descriptions.pdf` | ITI trade descriptions in plain language | One chunk per trade, ~200 tokens | `trade` | Annual |
| `data/training_institutes.csv` | District-wise training institute catalogue | One row per chunk | `institute` | Monthly |
| `data/bridge_income_options.pdf` | Short-duration gig/informal work options | Semantic by option type | `bridge_income` | Manual |
| `data/onest_market_truth_framing.md` | Instructions for presenting ONEST/market data | Single chunk | `always_include` | Manual |

**`always_include` type:** Chunks with `doc_type = always_include` are **always** injected into the
system prompt. They are never retrieved by similarity — they bypass the similarity search entirely.

**Retrieval logic (runtime `process()`):**

1. Build metadata filter from `context.intent` → maps to `doc_type`
   - `market_truth_query` → filter by `doc_type in [scheme, trade, bridge_income]`
   - `scheme_query` → filter by `doc_type = scheme`
   - `training_query` → filter by `doc_type in [trade, institute]`
   - default → no filter (search all)
2. If `context.entities.location` is present, apply additional `district` metadata filter
3. Query ChromaDB: `collection.query(query_texts=[context.normalised_input], n_results=top_k, where=filters)`
4. Discard chunks below `similarity_threshold` (default 0.65 from YAML)
5. Separately fetch all `always_include` chunks (no similarity filter)

**Writes to KEContext:**
- `context.retrieval_chunks` — top-k relevant chunks: `[{ "text": str, "metadata": dict }, ...]`
- `context.always_include_chunks` — all always_include chunks

**DB calls:**
- ChromaDB `collection.query()` — one call per turn
- ChromaDB `collection.get(where={"doc_type": "always_include"})` — one call per turn

**YAML config section:**
```yaml
knowledge:
  blocks:
    static_knowledge_base:
      enabled: true
      vector_store: chromadb
      collection_name: kkb_knowledge
      embedding_provider: sentence_transformers  # options: openai | sentence_transformers (default: sentence_transformers — no API key needed)
      embedding_model: paraphrase-multilingual-MiniLM-L12-v2  # openai: text-embedding-3-small | st: paraphrase-multilingual-MiniLM-L12-v2
      top_k: 3
      similarity_threshold: 0.65
      sources:
        - path: ./data/labour_schemes.pdf
          type: static
          doc_type: scheme
          refresh: manual
        - path: ./data/trade_descriptions.pdf
          type: static
          doc_type: trade
          refresh: annual
        - path: ./data/training_institutes.csv
          type: static
          doc_type: institute
          refresh: monthly
        - path: ./data/bridge_income_options.pdf
          type: static
          doc_type: bridge_income
          refresh: manual
        - path: ./data/onest_market_truth_framing.md
          type: always_include
          doc_type: always_include
          refresh: manual
      metadata_filters:
        use_location_filter: true
        use_intent_filter: true
```

**Tests to write (`test_static_knowledge_base.py`):**
- Retrieval relevance: "electrician Hubli" → at least 1 chunk with `doc_type = trade` or `institute`
- District filter: location entity = Hubli → only Hubli/Karnataka chunks returned
- `always_include` chunk: always present regardless of query
- No results above threshold: returns empty `retrieval_chunks`, no error
- ChromaDB unavailable: logs error, returns empty chunks — does not raise to caller
- Intent filter: `scheme_query` → no `doc_type = institute` chunks returned

---

### Block 5 — Multimodal Input Handler

**File:** `knowledge_engine/src/blocks/multimodal_input_handler.py`
**Class:** `MultimodalInputHandlerBlock`

**Purpose:** Extract text from non-text inputs (PDF, image) sent mid-conversation and append to context.

**PoC status:** Built and registered but `enabled: false` for KKB (voice channel sends text only).
Demonstrates config-driven enable/disable — a different deployment (e.g. construction safety bot
receiving photos) would set `enabled: true`.

**Supported in PoC:**
- PDF → extract text using PyMuPDF (`fitz`), append to `context.raw_input`
- Image → base64 encode, call Claude vision via `self._llm.call()` with vision prompt, append description
- Audio → stub: logs that ASR pipeline is not in scope, returns context unchanged

**Writes to KEContext:**
- `context.raw_input` — appended with extracted text/description

**DB calls:** None
**LLM calls:** Image only — `self._llm.call()` with `model_override` pointing to vision-capable model from YAML.

**YAML config section:**
```yaml
knowledge:
  blocks:
    multimodal_input_handler:
      enabled: false
      supported_types: [pdf, image]
      audio_enabled: false
      image_model: claude-sonnet-4-6
      max_file_size_mb: 10
```

**Tests to write (`test_multimodal_input_handler.py`):**
- PDF extraction: 1-page test PDF → `context.raw_input` contains extracted text
- Image description: mock `self._llm.call()` → description appended to context
- Audio input: returns context unchanged, logs stub message
- `enabled: false`: block is skipped by engine, context unchanged
- File exceeds `max_file_size_mb`: logs error, returns context unchanged

---

## 7. KnowledgeEngine Orchestrator

**File:** `knowledge_engine/src/engine.py`
**Class:** `KnowledgeEngine(KnowledgeEngineBase)`

**Responsibilities:**
1. Accept `llm: LLMWrapperBase` at construction and store as `self._llm`
2. Instantiate all enabled blocks from YAML config at construction time
3. Run blocks in fixed logical order on every `assemble_prompt()` call
4. Build the final `messages` list from the enriched `KEContext`
5. Return the complete `messages` list to Agent Core

### Constructor

```python
def __init__(self, config: dict, llm: LLMWrapperBase) -> None:
    self._config = config
    self._llm = llm          # LLMWrapperBase — ClaudeLLMWrapper (PoC) or HttpLLMWrapper (prod)
    self._enabled_blocks = self._init_blocks()
```

### Block Instantiation
Blocks are instantiated once at startup. YAML config has a per-block `enabled` flag.
If `enabled: false`, the block is not instantiated and is skipped during processing.

A `BLOCK_REGISTRY` dict maps block name strings to classes:
```python
BLOCK_REGISTRY = {
    "language_normalisation": LanguageNormalisationBlock,
    "nlu_processor": NLUProcessorBlock,
    "glossary": GlossaryBlock,
    "static_knowledge_base": StaticKnowledgeBaseBlock,
    "multimodal_input_handler": MultimodalInputHandlerBlock,
}
```

This allows YAML config to drive instantiation without hardcoded imports.

### Prompt Assembly Order

After all blocks run, `assemble_prompt()` builds the messages list in this exact order:

```
[1] System prompt message (role: "user", as first message):
      a. KKB persona block          ← from YAML: conversation.persona.text
      b. Language instruction        ← "Respond in the same language the user uses."
      c. Always-include chunks       ← context.always_include_chunks (e.g. market truth framing)
      d. RAG-retrieved chunks        ← context.retrieval_chunks (top-3 by similarity)
      e. Guardrail reminders         ← blocked output phrases from YAML as negative instructions

[2] Conversation history messages   ← from session_state.history (last N turns, N from YAML)

[3] Current user message            ← role: "user", content: context.raw_input
```

> **Note on system prompt placement:** The Anthropic API accepts a `system` parameter.
> Currently `system=""` is passed by Agent Core. The persona and context are embedded in the
> first user message instead. When the Knowledge Engine is fully wired, the `system` parameter
> in `LLMWrapperBase.call()` will be used for the persona/context block.
> This is a known architectural debt — tracked in `agent_core/src/llm_wrapper/base.py` docstring.

### `assemble_prompt()` flow:

```python
def assemble_prompt(self, session_id, user_message, session_state) -> list[dict]:
    if not user_message:
        return []

    context = KEContext(
        session_id=session_id,
        raw_input=user_message,
        normalised_input=user_message,   # will be overwritten by Block 1
        detected_language="",
        intent="unknown",
        entities={},
        sentiment="neutral",
        confidence=0.0,
        retrieval_chunks=[],
        always_include_chunks=[],
        session_state=session_state,
    )

    for block in self._enabled_blocks:   # in fixed order
        context = block.process(context, self._llm, self._config)

    return self._build_messages(context)
```

---

## 8. KnowledgeBlock Base Class

**File:** `knowledge_engine/src/base.py`

Every block must inherit from `KnowledgeBlock` and implement `process()`:

```python
class KnowledgeBlock(ABC):

    @abstractmethod
    def process(
        self,
        context: KEContext,
        llm: LLMWrapperBase,
        config: dict,
    ) -> KEContext:
        """
        Enrich context with this block's output.
        Must return KEContext — the same object, modified in place or a new instance.
        Must never raise. On failure, log the error and return context unchanged.
        """
```

The `llm` parameter received here is `self._llm` from the engine — either `ClaudeLLMWrapper`
(PoC) or `HttpLLMWrapper` (production). Blocks use it the same way regardless.

---

## 9. HttpLLMWrapper — LLM Proxy Client

**File:** `knowledge_engine/src/llm_proxy_client.py`
**Class:** `HttpLLMWrapper(LLMWrapperBase)`

`HttpLLMWrapper` is the **only** LLM client used by Knowledge Engine — in local dev and in production.
Same `LLMWrapperBase` interface — backed by an HTTP call to Agent Core's `/internal/llm/call` endpoint.

```python
class HttpLLMWrapper(LLMWrapperBase):

    def __init__(self, proxy_url: str, timeout_ms: int) -> None:
        self._proxy_url = proxy_url      # e.g. "http://agent-core:8000/internal/llm/call"
        self._timeout_s = timeout_ms / 1000

    def call(self, messages, tools=None, system="", model_override=None) -> LLMResponse:
        payload = {
            "messages": messages,
            "tools": tools or [],
            "system": system,
            "model_override": model_override,
        }
        response = httpx.post(self._proxy_url, json=payload, timeout=self._timeout_s)
        response.raise_for_status()
        return LLMResponse(**response.json())

    def get_active_model(self) -> str:
        return "proxy"   # model name is resolved by Agent Core
```

**Agent Core HTTP proxy endpoint** (must be implemented in Agent Core before KE can run):
```
POST /internal/llm/call
```
Receives the request body, calls `self._llm.call(...)`, returns `LLMResponse` as JSON.
Agent Core retains the Anthropic API key — KE never holds it.

> Implementing this endpoint in Agent Core is a prerequisite for running KE end-to-end.

---

## 10. File Structure

Mirrors `agent_core/` layout exactly — same conventions, same tooling.

```
knowledge_engine/
├── config/
│   └── config.yaml                      # service config loaded once at startup (see Section 12)
├── src/
│   ├── __init__.py
│   ├── base.py                          # KnowledgeBlock ABC + KEContext dataclass
│   ├── engine.py                        # KnowledgeEngine(KnowledgeEngineBase) — orchestrator
│   ├── llm_proxy_client.py              # HttpLLMWrapper — calls agent_core /internal/llm/call
│   └── blocks/
│       ├── __init__.py
│       ├── language_normalisation.py    # Block 1 — LLM call (Haiku)
│       ├── nlu_processor.py             # Block 2 — LLM call (Haiku) + JSON parse
│       ├── glossary.py                  # Block 3 — YAML string matching, no LLM/DB
│       ├── static_knowledge_base.py     # Block 4 — ChromaDB + embeddings
│       └── multimodal_input_handler.py  # Block 5 — PDF/image extraction
├── scripts/
│   └── ingest.py                        # Offline: load documents into ChromaDB
├── data/                                # KKB knowledge documents (not committed — gitignored)
│   ├── labour_schemes.pdf
│   ├── trade_descriptions.pdf
│   ├── training_institutes.csv
│   ├── bridge_income_options.pdf
│   └── onest_market_truth_framing.md
├── tests/
│   ├── __init__.py
│   ├── test_engine.py                   # End-to-end chain test
│   ├── test_language_normalisation.py
│   ├── test_nlu_processor.py
│   ├── test_glossary.py
│   ├── test_static_knowledge_base.py
│   ├── test_multimodal_input_handler.py
│   └── test_llm_proxy_client.py
├── main.py                              # startup entrypoint — loads config, wires KE, starts server
├── pyproject.toml                       # package metadata + dependencies + pytest/coverage config
├── Dockerfile                           # multi-stage build (mirrors agent_core/Dockerfile)
└── .env.example                         # template — copy to .env; OPENAI_API_KEY only needed if embedding_provider=openai
```

---

## 11. Dependencies

`pyproject.toml` for the `knowledge_engine/` package:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "knowledge-engine-dpg"
version = "0.1.0"
description = "Knowledge Engine DPG — NLU, RAG, and prompt assembly for the AI Composition Framework"
requires-python = ">=3.11"

dependencies = [
    "chromadb>=0.4.0",                   # local vector DB, no server needed
    "langchain-text-splitters>=0.2.0",   # document chunking (semantic + recursive)
    "openai>=1.0.0",                     # required only when embedding_provider=openai
    "sentence-transformers>=2.6.0",      # required only when embedding_provider=sentence_transformers
    "pymupdf>=1.23.0",                   # PDF text extraction (multimodal block)
    "pyyaml>=6.0",                       # YAML config loading
    "httpx>=0.27.0",                     # HttpLLMWrapper — HTTP calls to agent_core proxy
    "fastapi>=0.111.0",                  # HTTP server (KE exposes its own health endpoint)
    "uvicorn[standard]>=0.29.0",         # ASGI server
    "python-dotenv>=1.0.0",              # loads .env at startup
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-mock>=3.0",
    "httpx>=0.27.0",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["src*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"

[tool.coverage.run]
source = ["src"]
omit = ["*/tests/*", "*/__init__.py"]

[tool.coverage.report]
fail_under = 70
show_missing = true
```

> **Do not add `anthropic` as a dependency of `knowledge_engine/`.** The `llm` object is
> injected from outside. Knowledge Engine never imports or instantiates an Anthropic client.
>
> **Secrets required depend on the embedding provider chosen in config:**
> - `embedding_provider: openai` → `OPENAI_API_KEY` required in `.env`
> - `embedding_provider: sentence_transformers` → no API key needed; model downloads locally on first run
>
> The `.env.example` documents both. The code must raise a clear `ValueError` at startup if
> `embedding_provider: openai` is configured but `OPENAI_API_KEY` is not set.

---

## 12. YAML Config — Full `config/config.yaml` (KKB)

Secrets (`OPENAI_API_KEY`) are never stored here — use environment variables / `.env`.
The embedding provider is the only choice that determines whether an API key is needed.

```yaml
# knowledge_engine/config/config.yaml — service configuration loaded once at startup.
# Secrets (OPENAI_API_KEY) are never stored here — use environment variables.
# To run without an API key: set embedding_provider to sentence_transformers.

server:
  host: 0.0.0.0   # 0.0.0.0 for Docker/container; 127.0.0.1 for local-only
  port: 8001       # different port from agent_core (8000)

knowledge:
  llm_proxy_url: http://localhost:8000/internal/llm/call  # agent_core LLM proxy

  blocks:
    language_normalisation:
      enabled: true
      supported_languages: [hindi, kannada, english, hinglish]
      provider: llm_native
      transliteration: true
      code_switching: true

    nlu_processor:
      enabled: true
      model: claude-haiku-4-5-20251001
      intents:
        - market_truth_query
        - scheme_query
        - training_query
        - apply_now
        - counsellor_request
        - pay_range_query
        - unknown
      entities: [trade, location, distance_km, income_urgency]
      sentiment_classes: [neutral, positive, distressed, frustrated]

    glossary:
      enabled: true
      mappings:
        - colloquial: ["kaam chahiye", "naukri chahiye", "job chahiye"]
          canonical: "market_truth_query"
        - colloquial: ["ITI", "tradesman", "technician"]
          canonical: "iti_graduate"
        - colloquial: ["course", "training", "sikhai"]
          canonical: "training_query"
        - colloquial: ["apply kar do", "form bharo"]
          canonical: "apply_now"
        - colloquial: ["counsellor chahiye", "kisi se baat karni hai"]
          canonical: "counsellor_request"
        - colloquial: ["kitna milega", "salary kya hai", "pay kya hai"]
          canonical: "pay_range_query"
        - colloquial: ["electrician", "bijli wala"]
          canonical: "trade:electrician"
        - colloquial: ["fitter"]
          canonical: "trade:fitter"
        - colloquial: ["welder"]
          canonical: "trade:welder"
        - colloquial: ["PMKVY", "Pradhan Mantri Kaushal"]
          canonical: "scheme:pmkvy"
        - colloquial: ["Hubli", "Dharwad", "Belgaum"]
          canonical: "location:karnataka_north"
      apply_to: [normalised_input, entities]

    static_knowledge_base:
      enabled: true
      vector_store: chromadb
      collection_name: kkb_knowledge
      embedding_provider: sentence_transformers  # options: openai | sentence_transformers (default: sentence_transformers — no API key needed)
      embedding_model: paraphrase-multilingual-MiniLM-L12-v2  # openai: text-embedding-3-small | st: paraphrase-multilingual-MiniLM-L12-v2
      top_k: 3
      similarity_threshold: 0.65
      sources:
        - path: ./data/labour_schemes.pdf
          type: static
          doc_type: scheme
          refresh: manual
        - path: ./data/trade_descriptions.pdf
          type: static
          doc_type: trade
          refresh: annual
        - path: ./data/training_institutes.csv
          type: static
          doc_type: institute
          refresh: monthly
        - path: ./data/bridge_income_options.pdf
          type: static
          doc_type: bridge_income
          refresh: manual
        - path: ./data/onest_market_truth_framing.md
          type: always_include
          doc_type: always_include
          refresh: manual
      metadata_filters:
        use_location_filter: true
        use_intent_filter: true

    multimodal_input_handler:
      enabled: false
      supported_types: [pdf, image]
      audio_enabled: false
      image_model: claude-sonnet-4-6
      max_file_size_mb: 10
```

---

## 13. Error Handling Rules

These apply to every block and to the engine orchestrator:

1. **Never raise to Agent Core.** `assemble_prompt()` must never propagate an exception.
   If a block fails, log the error and continue with the next block using the current `KEContext`.
2. **ChromaDB failures** (Block 4): log `status=failure`, return `retrieval_chunks=[]`.
   The LLM will answer from its base knowledge — not ideal, but better than a crash.
3. **LLM call failures** (Blocks 1, 2, 5): log `status=failure`.
   - Block 1 failure: `normalised_input = raw_input` (skip normalisation gracefully)
   - Block 2 failure: `intent = "unknown"`, `confidence = 0.0`
4. **Malformed LLM JSON** (Block 2): catch `json.JSONDecodeError`, fall back to `intent = "unknown"`.
5. **Empty retrieval results**: return empty `retrieval_chunks`, do not error.
6. **Logging**: every block must log with `operation`, `status`, `latency_ms`.
   Never log `user_message` content (PII risk).

---

## 14. Testing Requirements

Minimum coverage: **≥ 70% line coverage** across `knowledge_engine/`.

Every test file must cover:
- Normal execution: valid input → correct `KEContext` fields set
- Edge cases: empty input, missing entities, no RAG results, disabled block
- Failure scenarios: LLM call failure (mock), ChromaDB unavailable (mock), malformed JSON

All external calls (LLM, ChromaDB, OpenAI embeddings) must be **mocked** in unit tests.
No real API calls in the test suite.

End-to-end chain test (`test_engine.py`):
```
Input: "ITI electrician Hubli mein kaam chahiye"

Assert:
  context.intent == "market_truth_query"
  context.entities["trade"] == "electrician" (or "trade:electrician")
  "Hubli" in str(context.entities.get("location", ""))
  len(context.retrieval_chunks) >= 1
  len(context.always_include_chunks) >= 1
  returned messages list is non-empty and has correct role/content structure
```

---

## 15. Implementation Order

Build in this order — each step is independently testable before the next:

0. **Project scaffold** — `pyproject.toml`, `config/config.yaml`, `main.py`, `Dockerfile`,
   `.env.example`, `src/__init__.py`, `src/blocks/__init__.py`, `tests/__init__.py`
1. `src/base.py` — `KnowledgeBlock` ABC + `KEContext` dataclass
2. `src/llm_proxy_client.py` — `HttpLLMWrapper(LLMWrapperBase)` + `tests/test_llm_proxy_client.py`
3. `src/blocks/glossary.py` — no LLM/DB, simplest block; validates the block pattern + `tests/test_glossary.py`
4. `src/blocks/nlu_processor.py` — LLM call + JSON parsing + `tests/test_nlu_processor.py`
5. `src/blocks/language_normalisation.py` — LLM call + `tests/test_language_normalisation.py`
6. `src/blocks/static_knowledge_base.py` — ChromaDB + `scripts/ingest.py` + `tests/test_static_knowledge_base.py`
7. `src/blocks/multimodal_input_handler.py` — PDF/image extraction + `tests/test_multimodal_input_handler.py`
8. `src/engine.py` — wire all blocks, implement `assemble_prompt()` + `tests/test_engine.py`
