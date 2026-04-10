# Design: Config-Driven Domain-Specific Metrics

**Date:** 2026-04-10
**Status:** Draft — awaiting review before implementation
**Branch:** fix/observability-e2e-pipeline (will be added to PR #61)

---

## 1. Problem Statement

Each domain (KKB, Farmer-Friendly, etc.) needs to capture **different business metrics** — KKB tracks jobs shown and applications; Farmer-Friendly tracks diseases diagnosed and escalations to Krishi Seva Kendra. Today the Observability Layer only supports lifecycle-based outcome tracking (tool name match → increment counter). It cannot:

- Extract values from tool call results (e.g. count jobs in a search response)
- Track metrics from NLU data (entity counts, sentiment distribution)
- Count intent-based events (escalations, specific user actions)
- Measure against thresholds (slow turns, low confidence)

All of these must be **config-driven** — if a metric is not declared in the domain's `observability_layer.yaml`, it is not captured. No code changes needed per domain.

---

## 2. What Data Is Available Per Turn

Every turn produces a `TurnEvent` that is sent async to the Observability Layer. Here is what is currently available vs what needs to be added:

| Data | Field in TurnEvent | Available Today? |
|------|-------------------|------------------|
| Tool names called | `tool_calls[].tool_name` | Yes |
| Tool input params | `tool_calls[].input_params` | Yes |
| **Tool result data** | — | **No (dropped in ManagerAgent)** |
| NLU intent | `intent` | Yes (but lost in HTTP serialisation — bug) |
| **NLU entities** | — | **No (not in TurnEvent)** |
| **NLU sentiment** | — | **No (not in TurnEvent)** |
| **NLU confidence** | — | **No (not in TurnEvent)** |
| **Current subagent** | — | **No (not in TurnEvent)** |
| Trust input result | `trust_input_result.action` | Yes |
| Trust output result | `trust_output_result.action` | Yes |
| Model used | `model_used` | Yes |
| Latency | `latency_ms` | Yes |
| Input/output tokens | `input_tokens`, `output_tokens` | Declared but hardcoded to 0 (bug) |
| Trace ID | `trace_id` | Yes |

---

## 3. Config Schema Design

### 3.1 New `domain_metrics` Section

Added under `observability` in the domain's `observability_layer.yaml`. Lives alongside the existing `outcomes` section (which keeps working unchanged).

```yaml
observability:
  domain: "kkb"

  domain_metrics:
    - name: "jobs_showed"
      description: "Number of jobs shown to user"
      instrument: counter
      source:
        type: tool_result                   # where to get the data
        tool_name: "onest_search"           # which tool to watch
        result_path: "jobs"                 # key path in tool result dict
        extract: "length"                   # how to extract the value
      attributes: ["intent"]                # OTel metric dimensions/labels
```

### 3.2 Source Types

Each metric declares a `source.type` that tells the MetricExtractor where to look in the TurnEvent:

| `source.type` | Watches | Required Fields | Needs Agent Core Change? |
|---------------|---------|-----------------|--------------------------|
| `tool_result` | Tool response data | `tool_name`, `result_path`, `extract` | **Yes** — ToolResult not in TurnEvent |
| `entity` | NLU extracted entities | `entity_name`, `extract` | **Yes** — entities not in TurnEvent |
| `intent` | NLU classified intent | `intent_name`, `extract` | No — intent already in TurnEvent |
| `sentiment` | NLU sentiment label | `sentiment_value`, `extract` | **Yes** — sentiment not in TurnEvent |
| `trust` | Trust Layer check result | `check` (input/output), `action`, `extract` | No — trust results already in TurnEvent |
| `metadata` | Turn-level numbers | `field`, `extract`, (optional `threshold`) | No — latency_ms etc already in TurnEvent |
| `subagent` | Which subagent handled turn | `extract` | **Yes** — subagent_id not in TurnEvent |

### 3.3 Extract Modes

The `extract` field tells the MetricExtractor how to turn the raw data into a numeric value for the OTel instrument:

| `extract` | What It Does | Input Example | Output |
|-----------|-------------|---------------|--------|
| `length` | `len(value)` — count items in a list | `[job1, job2, job3]` | `3` |
| `value` | Use the value directly as a number | `850` (latency) | `850` |
| `match` | Increment by 1 if value equals `match_value` | `"success"` == `"success"` | `1` |
| `event` | Increment by 1 whenever the source fires | (any) | `1` |
| `threshold` | Increment by 1 if value > `threshold` | `1350 > 1200` | `1` |

---

## 4. Full Config Examples

### 4.1 KKB Domain

```yaml
# dev-kit/configs/kkb/observability_layer.yaml
observability:
  domain: "kkb"

  domain_metrics:

    # ── Metrics from tool results ────────────────────────

    # Count how many jobs were shown to the user (from onest_search response)
    # Tool returns: {"jobs": [{...}, {...}, ...], "total_count": 5}
    # Extractor navigates to result["jobs"], counts items → counter += 3
    - name: "jobs_showed"
      description: "Total number of job listings shown to users"
      instrument: counter
      source:
        type: tool_result
        tool_name: "onest_search"
        result_path: "jobs"
        extract: "length"
      attributes: ["intent"]

    # Count successful job applications
    # Tool returns: {"status": "success", "application_id": "APP-123"}
    # Extractor checks result["status"] == "success" → counter += 1
    - name: "jobs_applied"
      description: "Total successful job applications submitted"
      instrument: counter
      source:
        type: tool_result
        tool_name: "onest_apply"
        result_path: "status"
        extract: "match"
        match_value: "success"
      attributes: ["intent"]

    # ── Metrics from NLU entities ────────────────────────

    # Track which locations users are searching for jobs in
    # NLU entities: {"location": "Bangalore", "skill": "data entry"}
    # Extractor sees entity "location" present → counter += 1, label location=Bangalore
    - name: "searches_by_location"
      description: "Job searches broken down by user location"
      instrument: counter
      source:
        type: entity
        entity_name: "location"
        extract: "event"
      attributes: ["location"]

    # Track which skills are most searched
    - name: "searches_by_skill"
      description: "Job searches broken down by skill category"
      instrument: counter
      source:
        type: entity
        entity_name: "skill"
        extract: "event"
      attributes: ["skill"]

    # ── Metrics from intent ──────────────────────────────

    # Count escalations to human counsellor
    # NLU intent == "request_counsellor" → counter += 1
    - name: "counsellor_escalations"
      description: "Sessions escalated to human counsellor"
      instrument: counter
      source:
        type: intent
        intent_name: "request_counsellor"
        extract: "event"

    # ── Metrics from sentiment ───────────────────────────

    # Track frustrated users for quality monitoring
    # NLU sentiment == "frustrated" → counter += 1
    - name: "frustrated_users"
      description: "Turns where user expressed frustration"
      instrument: counter
      source:
        type: sentiment
        sentiment_value: "frustrated"
        extract: "event"
      attributes: ["intent", "subagent_id"]

    # ── Metrics from turn metadata ───────────────────────

    # Count turns that exceeded latency SLI
    # latency_ms > 1200 → counter += 1
    - name: "slow_turns"
      description: "Turns exceeding 1200ms latency SLI"
      instrument: counter
      source:
        type: metadata
        field: "latency_ms"
        extract: "threshold"
        threshold: 1200

  # (existing sections remain unchanged)
  outcomes:
    lifecycle: [...]
    metrics: [...]
  sli:
    turn_latency_p99_ms: 1200
    trust_block_rate_max: 0.05
```

### 4.2 Farmer-Friendly Domain

```yaml
# dev-kit/configs/farmer-friendly/observability_layer.yaml
observability:
  domain: "farmer_friendly"

  domain_metrics:

    # Count diseases diagnosed via knowledge base
    # knowledge_retrieval returns: {"results": [{disease1}, {disease2}]}
    - name: "diseases_diagnosed"
      description: "Number of disease matches returned by knowledge base"
      instrument: counter
      source:
        type: tool_result
        tool_name: "knowledge_retrieval"
        result_path: "results"
        extract: "length"
      attributes: ["intent"]

    # Track which crops farmers are asking about
    # NLU entity "crop_name" = "ragi" → counter += 1, label crop_name=ragi
    - name: "queries_by_crop"
      description: "Farmer queries broken down by crop type"
      instrument: counter
      source:
        type: entity
        entity_name: "crop_name"
        extract: "event"
      attributes: ["crop_name"]

    # Track which locations are reporting disease issues
    - name: "queries_by_location"
      description: "Disease queries by farmer location"
      instrument: counter
      source:
        type: entity
        entity_name: "location"
        extract: "event"
      attributes: ["location"]

    # Count escalations to Krishi Seva Kendra
    - name: "ksk_escalations"
      description: "Sessions escalated to Krishi Seva Kendra"
      instrument: counter
      source:
        type: intent
        intent_name: "diagnosis_uncertain"
        extract: "event"

    # Track distressed farmers for outreach
    - name: "distressed_farmers"
      description: "Turns where farmer expressed distress"
      instrument: counter
      source:
        type: sentiment
        sentiment_value: "distressed"
        extract: "event"
      attributes: ["crop_name"]

    # Count Trust Layer blocks (safety monitoring)
    - name: "content_blocked"
      description: "Turns where output was blocked by Trust Layer"
      instrument: counter
      source:
        type: trust
        check: "output"
        action: "block"
        extract: "event"
```

### 4.3 Domain With No Custom Metrics

```yaml
# If a domain doesn't need custom metrics, simply omit the section
observability:
  domain: "some_new_domain"
  # no domain_metrics section → nothing is captured
```

---

## 5. End-to-End Data Flow

### 5.1 Current Flow (what exists)

```
User message
  → Agent Core orchestrator
    → ManagerAgent.run_turn()
      → _execute_tool() returns ToolResult
      → ToolResult.result used for LLM message, then DROPPED
      → returns (final_text, list[ToolCall])     ← only ToolCall, no result
    → _build_result() assembles TurnEvent
      → TurnEvent has: tool_calls (name + input_params only)
      → TurnEvent missing: tool results, entities, sentiment, subagent, tokens
    → _post_turn() daemon thread
      → HTTP POST /emit/turn
        → _serialise_turn_event() drops intent (bug)
    → Observability Layer receives TurnEvent
      → OutcomeTracker.process() matches tool_name only
      → No result-based extraction possible
```

### 5.2 New Flow (after changes)

```
User message
  → Agent Core orchestrator
    → ManagerAgent.run_turn()
      → _execute_tool() returns ToolResult
      → ToolResult.result used for LLM message
      → ALSO accumulates ToolResult in list          ← CHANGE 1
      → returns (final_text, list[ToolCall], list[ToolResult])
    → _build_result() assembles TurnEvent
      → TurnEvent now includes:                       ← CHANGE 2
        - tool_results: list[ToolResult]
        - entities: dict
        - sentiment: str
        - confidence: float
        - subagent_id: str
        - input_tokens: int  (fix: use actual values)
        - output_tokens: int (fix: use actual values)
    → _post_turn() daemon thread
      → HTTP POST /emit/turn
        → _serialise_turn_event() includes all new fields  ← CHANGE 3
        → also serialises intent (bug fix)
    → Observability Layer receives enriched TurnEvent
      → OutcomeTracker.process() — unchanged, still works
      → MetricExtractor.process() — NEW                    ← CHANGE 4
        → reads domain_metrics config
        → for each metric definition:
          → checks source.type
          → finds matching data in TurnEvent
          → applies extract mode
          → increments OTel instrument with attributes
        → Prometheus scrapes OTel Collector → metrics available in Grafana
```

---

## 6. Runtime Example Walkthrough

### Scenario: KKB user searches for jobs, then applies

**Turn 1:** User says "show me data entry jobs in Bangalore"

```
NLU result:
  intent: "job_search"
  entities: {location: "Bangalore", skill: "data entry"}
  sentiment: "neutral"
  confidence: 0.94

LLM calls tool: onest_search({query: "data entry", location: "Bangalore"})

Action Gateway returns ToolResult:
  result: {
    "jobs": [
      {"title": "Data Entry Clerk", "company": "TCS"},
      {"title": "Data Entry Operator", "company": "Infosys"},
      {"title": "Back Office Executive", "company": "Wipro"}
    ],
    "total_count": 3
  }
  success: true
```

**MetricExtractor processes the TurnEvent:**

```
Metric: "jobs_showed"
  source.type = tool_result
  source.tool_name = "onest_search"     → match tool_results[0] ✓
  source.result_path = "jobs"           → tool_results[0].result["jobs"] → list of 3
  source.extract = "length"             → len(3) = 3
  → counter.add(3, {intent: "job_search"})

Metric: "searches_by_location"
  source.type = entity
  source.entity_name = "location"       → entities["location"] = "Bangalore" ✓
  source.extract = "event"              → 1
  → counter.add(1, {location: "Bangalore"})

Metric: "searches_by_skill"
  source.type = entity
  source.entity_name = "skill"          → entities["skill"] = "data entry" ✓
  source.extract = "event"              → 1
  → counter.add(1, {skill: "data entry"})

Metric: "counsellor_escalations"
  source.type = intent
  source.intent_name = "request_counsellor"  → intent is "job_search" ✗ skip

Metric: "frustrated_users"
  source.type = sentiment
  source.sentiment_value = "frustrated"  → sentiment is "neutral" ✗ skip

Metric: "slow_turns"
  source.type = metadata
  source.field = "latency_ms"           → 850
  source.extract = "threshold"          → 850 > 1200? No ✗ skip
```

**Prometheus state after Turn 1:**
```
dpg_kkb_jobs_showed{intent="job_search"} = 3
dpg_kkb_searches_by_location{location="Bangalore"} = 1
dpg_kkb_searches_by_skill{skill="data entry"} = 1
```

**Turn 2:** User says "show me more options, maybe in Mysore also"

```
onest_search returns 5 more jobs.
NLU entities: {location: "Mysore"}
```

**Prometheus state after Turn 2:**
```
dpg_kkb_jobs_showed{intent="job_search"} = 8     (3 + 5)
dpg_kkb_searches_by_location{location="Bangalore"} = 1
dpg_kkb_searches_by_location{location="Mysore"} = 1
dpg_kkb_searches_by_skill{skill="data entry"} = 2
```

**Turn 3:** User says "apply for the TCS data entry job"

```
onest_apply returns: {status: "success", application_id: "APP-7721"}
NLU intent: "job_apply"
```

**MetricExtractor:**
```
Metric: "jobs_applied"
  source.type = tool_result
  source.tool_name = "onest_apply"       → match ✓
  source.result_path = "status"          → "success"
  source.extract = "match"               → "success" == "success" ✓
  → counter.add(1, {intent: "job_apply"})
```

**Prometheus state after Turn 3:**
```
dpg_kkb_jobs_showed{intent="job_search"} = 8
dpg_kkb_jobs_applied{intent="job_apply"} = 1
dpg_kkb_searches_by_location{location="Bangalore"} = 1
dpg_kkb_searches_by_location{location="Mysore"} = 1
dpg_kkb_searches_by_skill{skill="data entry"} = 2
```

---

## 7. Code Changes Required

### 7.1 Agent Core — Models (`agent_core/src/models.py`)

Add new fields to `TurnEvent`:

```python
@dataclass
class TurnEvent:
    session_id: str
    turn_id: str
    response_text: str
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]          # NEW — tool response data
    trust_input_result: TrustCheckResult
    trust_output_result: TrustCheckResult
    model_used: str
    intent: str
    entities: dict[str, Any]                # NEW — NLU entities
    sentiment: str                          # NEW — NLU sentiment
    confidence: float                       # NEW — NLU confidence
    subagent_id: str                        # NEW — which subagent handled turn
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp_ms: int
    trace_id: Optional[str] = None
```

**Lines changed:** ~6 new fields added to dataclass.

### 7.2 Agent Core — ManagerAgent (`agent_core/src/manager_agent.py`)

Change `run_turn()` to also return tool results:

```python
# Current
def run_turn(...) -> tuple[str, list[ToolCall]]:

# New
def run_turn(...) -> tuple[str, list[ToolCall], list[ToolResult]]:
```

Inside the tool loop, accumulate results:

```python
all_tool_calls.append(tool_call)
all_tool_results.append(tool_result)       # NEW — one line
```

Return:
```python
return final_text, all_tool_calls, all_tool_results    # add third element
```

**Lines changed:** ~5 lines (add list, append, update return).

### 7.3 Agent Core — Orchestrator (`agent_core/src/orchestrator.py`)

Update Step 9 to unpack the new return value:

```python
# Current (line 617)
final_text, tool_calls = self._manager_agent.run_turn(...)

# New
final_text, tool_calls, tool_results = self._manager_agent.run_turn(...)
```

Update `_build_result()` to accept and pass new fields:

```python
# Add parameters to _build_result():
#   tool_results, entities, sentiment, confidence, subagent_id,
#   input_tokens, output_tokens

# Update TurnEvent assembly (line 1253):
turn_event = TurnEvent(
    ...
    tool_results=tool_results,
    entities=nlu_result.entities or {},
    sentiment=nlu_result.sentiment or "",
    confidence=nlu_result.confidence,
    subagent_id=next_subagent_id,
    input_tokens=llm_response.input_tokens,     # fix: was hardcoded 0
    output_tokens=llm_response.output_tokens,   # fix: was hardcoded 0
    ...
)
```

**Lines changed:** ~15 lines across two methods.

### 7.4 Agent Core — HTTP Serialiser (`agent_core/src/http_clients/observability_layer.py`)

Update `_serialise_turn_event()`:

```python
def _serialise_turn_event(event: Any) -> dict:
    ...
    return {
        ...
        "intent": _get("intent", ""),              # FIX: was missing
        "tool_results": _serialise_tool_results(    # NEW
            _get("tool_results", [])
        ),
        "entities": _get("entities", {}),           # NEW
        "sentiment": _get("sentiment", ""),         # NEW
        "confidence": _get("confidence", 0.0),      # NEW
        "subagent_id": _get("subagent_id", ""),     # NEW
        ...
    }
```

Add `_serialise_tool_results()` helper:

```python
def _serialise_tool_results(tool_results: Any) -> list:
    if not tool_results:
        return []
    result = []
    for tr in tool_results:
        if isinstance(tr, dict):
            result.append({
                "tool_name": tr.get("tool_name", ""),
                "tool_use_id": tr.get("tool_use_id", ""),
                "result": tr.get("result", {}),
                "success": tr.get("success", False),
            })
        else:
            result.append({
                "tool_name": getattr(tr, "tool_name", ""),
                "tool_use_id": getattr(tr, "tool_use_id", ""),
                "result": getattr(tr, "result", {}),
                "success": getattr(tr, "success", False),
            })
    return result
```

**Lines changed:** ~30 lines.

### 7.5 Observability Layer — Config Schema (`observability_layer/src/schema/config.py`)

Add new Pydantic models for domain_metrics:

```python
class MetricSource(BaseModel):
    """Defines where a domain metric gets its data from."""
    model_config = ConfigDict(frozen=True)

    type: str                                # tool_result | entity | intent | sentiment | trust | metadata | subagent
    tool_name: Optional[str] = None          # for type=tool_result
    result_path: Optional[str] = None        # for type=tool_result — key in result dict
    entity_name: Optional[str] = None        # for type=entity
    intent_name: Optional[str] = None        # for type=intent
    sentiment_value: Optional[str] = None    # for type=sentiment
    check: Optional[str] = None              # for type=trust — "input" or "output"
    action: Optional[str] = None             # for type=trust — "block" or "escalate"
    field: Optional[str] = None              # for type=metadata — "latency_ms", etc.
    extract: str = "event"                   # length | value | match | event | threshold
    match_value: Optional[str] = None        # for extract=match
    threshold: Optional[float] = None        # for extract=threshold


class DomainMetricDefinition(BaseModel):
    """A config-driven domain metric."""
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    instrument: InstrumentType
    source: MetricSource
    unit: str = ""
    attributes: list[str] = Field(default_factory=list)
```

Add to `ObservabilityConfig`:

```python
class ObservabilityConfig(BaseModel):
    ...
    domain_metrics: list[DomainMetricDefinition] = Field(default_factory=list)
```

**Lines changed:** ~35 lines.

### 7.6 Observability Layer — Server Schema (`observability_layer/src/server.py`)

Update `TurnEventRequest` to accept new fields:

```python
class ToolResultSchema(BaseModel):
    tool_name: str = ""
    tool_use_id: str = ""
    result: dict = {}
    success: bool = False

class TurnEventRequest(BaseModel):
    ...
    tool_results: List[ToolResultSchema] = []    # NEW
    entities: dict = {}                          # NEW
    sentiment: str = ""                          # NEW
    confidence: float = 0.0                      # NEW
    subagent_id: str = ""                        # NEW
```

**Lines changed:** ~15 lines.

### 7.7 Observability Layer — MetricExtractor (NEW FILE)

New file: `observability_layer/src/metric_extractor.py`

```python
class MetricExtractor:
    """Config-driven domain metric extraction from TurnEvents.

    Reads domain_metrics config at init, creates OTel instruments.
    At runtime, process() evaluates each metric definition against
    the incoming TurnEvent data and increments matching instruments.

    Never raises. Logs errors and continues.
    """

    def __init__(self, config: ObservabilityConfig, meter: Any) -> None:
        # Create OTel instruments from config.domain_metrics
        # Store metric definitions + instruments in a list

    def process(self, event: dict) -> None:
        # For each metric definition:
        #   1. Call _resolve_source() to get raw value from event
        #   2. Call _apply_extract() to compute numeric value
        #   3. Call _build_attributes() to build label dict
        #   4. Increment the OTel instrument

    def _resolve_source(self, source: MetricSource, event: dict) -> Any:
        # Switch on source.type:
        #   tool_result → find matching tool_result, navigate result_path
        #   entity      → event["entities"].get(source.entity_name)
        #   intent      → event["intent"]
        #   sentiment   → event["sentiment"]
        #   trust       → event["trust_input_result"] or ["trust_output_result"]
        #   metadata    → event[source.field]
        #   subagent    → event["subagent_id"]

    def _apply_extract(self, extract: str, value: Any, source: MetricSource) -> Optional[float]:
        # length    → len(value)
        # value     → float(value)
        # match     → 1.0 if str(value) == source.match_value else None
        # event     → 1.0  (always fires)
        # threshold → 1.0 if float(value) > source.threshold else None
        # Returns None if metric should not fire this turn.

    def _build_attributes(self, attr_names: list[str], event: dict) -> dict:
        # Build {attr_name: value} from event fields
        # Handles entity values as attributes (e.g. location="Bangalore")
```

**Lines:** ~120-150 lines (new file).

### 7.8 Observability Layer — Wire MetricExtractor into Server

In `server.py` `emit_turn()` endpoint, after OutcomeTracker:

```python
# Existing
observability.emit_turn(event_dict)

# Inside emit_turn flow:
outcome_tracker.process(event_dict)    # existing
metric_extractor.process(event_dict)   # NEW — one line
```

**Lines changed:** ~5 lines (instantiation + call).

---

## 8. Files Changed Summary

| File | Type | Lines Changed |
|------|------|---------------|
| `agent_core/src/models.py` | Modify | ~6 |
| `agent_core/src/manager_agent.py` | Modify | ~5 |
| `agent_core/src/orchestrator.py` | Modify | ~15 |
| `agent_core/src/http_clients/observability_layer.py` | Modify | ~30 |
| `observability_layer/src/schema/config.py` | Modify | ~35 |
| `observability_layer/src/server.py` | Modify | ~15 |
| **`observability_layer/src/metric_extractor.py`** | **New** | **~150** |
| `dev-kit/configs/kkb/observability_layer.yaml` | Modify | ~40 |
| `dev-kit/configs/farmer-friendly/observability_layer.yaml` | Modify | ~30 |
| Tests (metric_extractor, serialiser, models) | New/Modify | ~200 |
| **Total** | | **~530** |

---

## 9. What Does NOT Change

- **OutcomeTracker** — continues to work exactly as before (lifecycle state machine)
- **Orchestrator turn sequence** — same 13 steps, no new steps added
- **Trust Layer, Knowledge Engine, Memory Layer, Action Gateway** — zero changes
- **Existing config sections** (outcomes, sli, audit) — untouched
- **Architecture rule**: Agent Core remains sole orchestrator, observability remains async-only

---

## 10. Risks and Considerations

| Risk | Mitigation |
|------|------------|
| Tool results may contain PII | MetricExtractor only extracts counts/values, never stores raw content. Existing `pii_fields_excluded` config applies. |
| Large tool results increase TurnEvent payload size | Tool results are sent async in daemon thread, not in response path. Payload size is bounded by Action Gateway response limits. |
| Config errors crash startup | Pydantic validates at startup. Invalid `domain_metrics` raises before any request is served. |
| MetricExtractor bug blocks turn processing | MetricExtractor.process() has top-level try/except, never raises. Same pattern as OutcomeTracker. |
| Existing OutcomeTracker tests break | OutcomeTracker is unchanged. New fields in TurnEvent have defaults, so existing test fixtures still work. |

---

## 11. Decision Point

**Can this be done without Agent Core changes?** No. The `tool_result`, `entity`, `sentiment`, and `subagent` source types all require data that is currently not in TurnEvent.

**However:** The `intent`, `trust`, and `metadata` source types work today with zero Agent Core changes. If you only need those three, it can ship without touching Agent Core.

**Recommendation:** The Agent Core changes are small (~50 lines across 4 files), low-risk (additive — new fields with defaults), and the same PR already touches these files for the observability pipeline. Ship it all together.
