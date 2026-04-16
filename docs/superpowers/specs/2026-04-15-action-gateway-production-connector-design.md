# Action Gateway: Production Connector Model — Design Spec

**Status:** Pending approval
**Date:** 2026-04-15
**Issue:** [#17](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/17)
**Related:** [#18](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/18) (caching — parked, designed separately)
**Sub-issues:**
- [#92](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/92) — Dev-Kit MCP tool discovery (blocker for MCP subagent assignment)
- [#93](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/93) — Config-driven response mapping (future)
- [#94](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/94) — Dev-Kit OpenAPI spec ingestion (future)
- [#95](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/95) — Dev-Kit Configuration Agent tool phase (future)
- [#96](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/96) — Action Gateway OTel instrumentation
**Depends on:** None — standalone change.
**Blocks:** #18 (caching requires the adapter framework to exist first)

---

## Problem

The current Action Gateway is a PoC stub (`mock_gateway.py`, `mock_server.py`) that returns hardcoded fixture data for KKB-specific endpoints. It is not a production framework component.

The DPG framework needs a generic, config-driven mechanism for connecting to external data sources. Domain experts must be able to add new data sources (REST APIs, MCP servers) without writing Python code — only by providing configuration through the dev-kit Configuration Agent, which generates YAML.

No design currently exists for what a real, generic Action Gateway should look like as a DPG building block.

---

## Architecture

### Core Abstraction: ToolAdapter ABC

Every external data source is accessed through a **ToolAdapter** — an abstract base class that normalizes the interface regardless of the underlying protocol.

```python
class ToolAdapter(ABC):
    """Base class for all Action Gateway tool adapters."""

    @abstractmethod
    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return tool schemas the LLM sees.

        RestApiAdapter returns a single-element list (one tool per instance).
        McpAdapter returns many (all tools discovered from that server).
        """

    @abstractmethod
    async def execute(self, tool_name: str, params: dict, session_id: str) -> ToolResult:
        """Execute a tool call and return normalized result."""

    @abstractmethod
    def health_check(self) -> bool:
        """Verify the adapter's backing service is reachable."""
```

### Hybrid Adapter Instantiation Model

- **RestApiAdapter** — one instance per tool config block. Each instance is self-contained with its own config, auth, and endpoints. `get_tool_definitions()` returns a single-element list.
- **McpAdapter** — one instance per MCP server. Maintains a persistent connection. `get_tool_definitions()` returns all tools discovered from that server via `tools/list`. Multiple tool names in the AdapterRegistry map to the same McpAdapter instance.

Future adapter types (database, file upload, gRPC, GraphQL) each get their own class inheriting from `ToolAdapter`. No changes to the registry, server, or Agent Core are needed to add them.

### Data Contracts

```python
@dataclass
class ToolDefinition:
    """Tool schema in Anthropic tool format, served to Agent Core via GET /tools."""
    name: str
    description: str
    input_schema: dict      # JSON Schema — only source:agent params included
    category: str           # "read" | "write" | "identity"

@dataclass
class ToolResult:
    """Normalized result returned from any adapter execution."""
    tool_use_id: str
    tool_name: str
    result: dict
    success: bool
    result_text: str = ""
    error: str | None = None
```

`ToolResult` matches the existing contract in `agent_core/src/models.py`. No changes to Agent Core's data model.

---

## Startup Sequence

```
Action Gateway starts
  |
  v
ConfigLoader reads action_gateway.yaml
  |  tools: [list of tool config blocks]
  |
  v
AdapterFactory iterates tool configs
  |
  |-- type: rest_api
  |     -> new RestApiAdapter(config)
  |     -> resolves auth secret from os.environ (fails loudly if missing)
  |     -> adapter.get_tool_definitions() -> 1 ToolDefinition
  |     -> registry.register("onest_market_lookup", adapter)
  |
  |-- type: rest_api
  |     -> new RestApiAdapter(config)
  |     -> registry.register("onest_apply", adapter)
  |
  |-- type: mcp
  |     -> new McpAdapter(config)
  |     -> connects to MCP server, calls tools/list
  |     -> adapter.get_tool_definitions() -> N ToolDefinitions
  |     -> registry.register("travel_mcp.search_hotels", adapter)
  |     -> registry.register("travel_mcp.book_room", adapter)
  |     -> registry.register("travel_mcp.cancel", adapter)
  |
  v
All tool definitions cached in AdapterRegistry
  |
  v
FastAPI server starts on port 9999
  |-- GET  /tools    -> registry.get_all_tool_definitions()
  |-- POST /execute  -> registry.resolve(tool_name).execute(...)
  |-- GET  /health   -> iterate adapters, call health_check()
```

### Startup Failure Handling

| Failure | Behavior |
|---|---|
| Missing env var for auth secret | Adapter not registered. Error logged. Gateway starts with remaining adapters. |
| MCP server unreachable | Adapter not registered. Error logged. Gateway starts with remaining adapters. |
| Invalid YAML config | Gateway fails to start entirely. |
| Zero adapters registered | Gateway starts. `GET /tools` returns empty list. Agent Core has no external tools. |

---

## AdapterRegistry

```python
class AdapterRegistry:
    """Maps tool names to adapter instances. Built once at startup, immutable after."""

    _adapters: dict[str, ToolAdapter]           # tool_name -> adapter instance
    _tool_definitions: list[ToolDefinition]     # cached, served via GET /tools

    def register(self, tool_name: str, adapter: ToolAdapter) -> None:
        """Register a tool name to an adapter. Multiple names can map to the same instance."""

    def resolve(self, tool_name: str) -> ToolAdapter:
        """Look up the adapter for a tool name. Raises KeyError if unknown."""

    def get_all_tool_definitions(self) -> list[ToolDefinition]:
        """Return all cached tool definitions for GET /tools."""
```

Multiple tool names can map to the same adapter instance (the MCP case). The registry is a flat `dict[str, ToolAdapter]` — O(1) lookup.

### AdapterFactory

```python
ADAPTER_TYPES: dict[str, type[ToolAdapter]] = {
    "rest_api": RestApiAdapter,
    "mcp": McpAdapter,
}
```

Adding a future adapter type = one new Python class + one entry in `ADAPTER_TYPES`. No changes to registry, server, or Agent Core.

---

## RestApiAdapter

One instance per tool config block. Fully self-contained.

### Construction

Receives the tool's YAML config block. At init:
1. Parses `base_url`, `auth`, `endpoints` from config.
2. Resolves `auth.secret_env` from `os.environ`. Fails loudly if missing.
3. Builds `ToolDefinition` from config — only `source: agent` params appear in `input_schema`.

### Tool Definition Generation

From this config:
```yaml
- id: onest_market_lookup
  type: rest_api
  category: read
  description: "Search job listings by trade and location"
  base_url: https://api.onest.network/v1
  endpoints:
    - name: search_jobs
      method: GET
      path: /jobs/search
      params:
        - name: trade
          source: agent
          type: string
          required: true
          description: "Trade or skill to search for"
        - name: location
          source: agent
          type: string
          required: true
          description: "City or district"
        - name: limit
          source: static
          value: 10
```

The adapter generates:
```json
{
  "name": "onest_market_lookup",
  "description": "Search job listings by trade and location",
  "category": "read",
  "input_schema": {
    "type": "object",
    "properties": {
      "trade": {"type": "string", "description": "Trade or skill to search for"},
      "location": {"type": "string", "description": "City or district"}
    },
    "required": ["trade", "location"]
  }
}
```

`limit: 10` is invisible to the LLM — the adapter injects it at execution time.

**One endpoint per tool for MVP.** The `endpoints` list exists for forward compatibility, but for MVP each tool config block should have exactly one endpoint. If a domain expert needs multiple operations from the same API (e.g., search vs detail lookup), they create separate tool config blocks with different `id`s, sharing the same `base_url` and `auth`. The adapter uses `endpoints[0]` for execution.

### Execution

1. Receives `tool_name`, `params` (from LLM), `session_id`.
2. Finds the matching endpoint config.
3. Merges LLM-provided params (`source: agent`) with static params (`source: static`).
4. Builds the HTTP request (method, URL from `base_url + path`, headers with auth).
5. Makes the HTTP call with explicit timeout from config (`timeout_ms`).
6. Truncates response body if it exceeds `response.max_size_chars` (default 4000).
7. Returns `ToolResult` with raw JSON as `result` and a text summary as `result_text`.
8. On failure: returns `ToolResult(success=False, error=<structured error>)`. Never raises.

### Auth Types

| Type | Behavior |
|---|---|
| `api_key` | Injects `auth.header: <resolved secret>` into request headers |
| `bearer` | Injects `Authorization: Bearer <resolved secret>` |
| `oauth` | Future — not MVP |
| `none` | No auth header |

All secrets are resolved from environment variables at startup via `auth.secret_env`. No secrets in YAML. No vault integration for MVP.

---

## McpAdapter

One instance per MCP server. Manages a persistent connection and exposes all tools discovered from that server.

### Construction

Receives the MCP server config block. At init:
1. Connects to the MCP server using the specified `transport` (sse, stdio, streamable-http).
2. Calls `tools/list` to discover available tools.
3. Converts each MCP tool schema to `ToolDefinition` format, namespaced with `namespace` prefix (e.g., `travel_mcp.search_hotels`).
4. Caches the discovered definitions.

### Tool Definition Generation

From this config:
```yaml
- id: travel_mcp
  type: mcp
  category: read
  description: "Travel booking tools"
  server_url: https://mcp.booking.com/sse
  transport: sse
  namespace: travel_mcp
```

The adapter connects, discovers tools, and generates definitions like:
```json
{
  "name": "travel_mcp.search_hotels",
  "description": "Search available hotels by location and dates",
  "category": "read",
  "input_schema": { ... }
}
```

Tool names are `{namespace}.{mcp_tool_name}`. The `category` from the YAML config block applies to all tools from that server. If individual tools need different categories (e.g., some read, some write), split them into separate MCP config blocks with different namespaces or use a `tool_overrides` section (future).

### Execution

1. Receives `tool_name` (e.g., `travel_mcp.search_hotels`), `params`, `session_id`.
2. Strips the namespace prefix to get the MCP tool name (`search_hotels`).
3. Calls `tools/call` on the MCP server with the tool name and params.
4. Converts the MCP response to `ToolResult` format.
5. Truncates if needed.
6. On failure or disconnect: returns `ToolResult(success=False, error=<structured error>)`. Attempts reconnect for next call.

### MCP Subagent Assignment Limitation

MCP tools are discovered at runtime, but subagent tool lists in `agent_core.yaml` reference tool names at config time. For MVP, the domain expert (or developer) must manually add MCP tool names to subagent tool lists after discovering them. Full dev-kit integration for MCP tool discovery and assignment is tracked in [#92](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/92).

---

## YAML Config Schema

Tool configuration lives in `dev-kit/configs/<domain>/action_gateway.yaml`. This is what the dev-kit Configuration Agent generates (or what a developer writes manually for MVP).

```yaml
server:
  host: "0.0.0.0"
  port: 9999

tools:
  - id: <unique tool identifier>
    type: rest_api | mcp              # selects adapter class
    category: read | write | identity # determines consent gating in Agent Core
    description: "<what this tool does — the LLM uses this for routing decisions>"

    # --- REST API fields ---
    base_url: <base URL for the API>
    auth:
      type: api_key | bearer | none
      header: <header name>           # required for api_key
      secret_env: <ENV_VAR_NAME>      # resolved from os.environ at startup
    endpoints:
      - name: <endpoint name>
        method: GET | POST | PUT | DELETE
        path: <URL path appended to base_url>
        params:
          - name: <param name>
            source: agent | static
            type: string | integer | number | boolean | array | object
            required: true | false    # default false
            description: "<param description — included in LLM tool schema>"
            value: <static value>     # required when source: static
            default: <default value>  # optional, for source: agent params
    response:
      max_size_chars: 4000            # truncate raw JSON beyond this

    # --- MCP fields ---
    server_url: <MCP server URL>
    transport: sse | stdio | streamable-http
    namespace: <prefix for discovered tool names>
```

### Schema Rules

| Field | Required | Default | Notes |
|---|---|---|---|
| `id` | yes | — | Unique across all tools. For REST, becomes the tool name. For MCP, becomes the namespace prefix. |
| `type` | yes | — | Must match a key in `ADAPTER_TYPES`. |
| `category` | yes | — | `read`, `write`, or `identity`. Agent Core uses this for consent gating. |
| `description` | yes | — | Critical for LLM routing. Must clearly describe when the tool should be used. |
| `base_url` | REST only | — | — |
| `auth` | REST only | `{type: none}` | — |
| `endpoints` | REST only | — | At least one endpoint required. |
| `response.max_size_chars` | no | 4000 | Truncation limit for raw JSON passthrough. |
| `server_url` | MCP only | — | — |
| `transport` | MCP only | `sse` | — |
| `namespace` | MCP only | value of `id` | Prefix for discovered tool names. |

---

## HTTP API (Action Gateway)

The service runs on port **9999**. Three endpoints.

### `GET /tools`

Returns all registered tool definitions. Called by Agent Core at startup to populate its ToolRegistry.

**Response:**
```json
{
  "tools": [
    {
      "name": "onest_market_lookup",
      "description": "Search job listings by trade and location",
      "input_schema": {
        "type": "object",
        "properties": {
          "trade": {"type": "string", "description": "Trade or skill to search for"},
          "location": {"type": "string", "description": "City or district"}
        },
        "required": ["trade", "location"]
      },
      "category": "read"
    }
  ]
}
```

### `POST /execute`

Executes a tool call. Contract unchanged from current implementation.

**Request:**
```json
{
  "tool_name": "onest_market_lookup",
  "tool_use_id": "toolu_01abc",
  "input_params": {
    "trade": "welder",
    "location": "Dharwad"
  },
  "session_id": "sess-xyz"
}
```

**Response:**
```json
{
  "tool_use_id": "toolu_01abc",
  "tool_name": "onest_market_lookup",
  "success": true,
  "result": { "...raw API JSON..." },
  "result_text": "",
  "error": null
}
```

**Error responses** (always HTTP 200, never raises):

| Condition | Error code |
|---|---|
| Unknown tool name | `unknown_tool: {tool_name}` |
| Adapter execution timeout | `adapter_timeout: {tool_name}` |
| HTTP error from external API | `http_error: {status_code}` |
| MCP server error | `mcp_error: {error_message}` |
| Auth secret missing at runtime | `auth_error: secret not configured` |
| Any other exception | `adapter_error: {ExceptionType}: {message}` |

### `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "adapters": {
    "onest_market_lookup": true,
    "travel_mcp.search_hotels": true,
    "travel_mcp.book_room": true
  }
}
```

Each adapter's `health_check()` is called. Individual adapter failures don't make the whole service unhealthy — the response shows per-adapter status.

---

## Agent Core Integration

### What Changes

Two files in Agent Core change. The orchestrator and manager agent are untouched.

**1. `ActionGatewayHttpClient` — fetches tool definitions from gateway**

Currently reads connector config from Agent Core's own YAML and builds tool definitions locally. New behavior: calls `GET /tools` on Action Gateway at startup.

```
Agent Core startup
  |
  v
ActionGatewayHttpClient.__init__()
  |  GET http://action_gateway:9999/tools
  |  -> receives [{name, description, input_schema, category}, ...]
  |
  v
ToolRegistry.__init__()
  |  tools from gateway -> registers as external tools
  |  tools from config connectors.internal -> registers as internal tools (knowledge_retrieval)
  |  category "write" / "identity" -> adds to consent set
  |
  v
Ready to serve turns
```

**2. `ToolRegistry` — uses `category` field for consent**

Currently determines consent from which YAML section a connector is in (`connectors.write` vs `connectors.read`). New behavior: each tool carries its own `category` field. ToolRegistry checks `category in {"write", "identity"}` to build the consent set. Same logic, different data source.

### What Stays the Same

| Component | Change? |
|---|---|
| `ManagerAgent._execute_tool()` | No — still calls `ActionGatewayHttpClient.execute(tool_call, session_id)` |
| `ManagerAgent._execute_knowledge_retrieval()` | No — still routes internal tools to KE |
| `ManagerAgent.run_turn()` tool-use loop | No |
| `ToolRegistry.get_route()` | No — still returns `"knowledge_engine"` for internal, `None` for external |
| `ToolRegistry.requires_consent()` | Same check, data source changed |
| `POST /execute` request/response format | No |
| Subagent tool lists in `agent_core.yaml` | No — still reference tool names by string |
| Orchestrator 13-step turn sequence | No |

### Agent Core YAML Changes

`dev-kit/configs/<domain>/agent_core.yaml` — external connector sections removed:

```yaml
# BEFORE
connectors:
  read:
    - name: onest_market_lookup
      description: "..."
      input_schema: {...}
  write:
    - name: onest_apply
      description: "..."
      input_schema: {...}
  internal:
    - name: knowledge_retrieval
      route: knowledge_engine
      description: "..."
      input_schema: {...}

# AFTER
connectors:
  internal:
    - name: knowledge_retrieval
      route: knowledge_engine
      description: "..."
      input_schema: {...}

action_gateway_client:
  endpoint: "http://action_gateway:9999"
```

External tool definitions are now the sole responsibility of Action Gateway. Agent Core fetches them at startup via `GET /tools`.

---

## Observability (#96)

Action Gateway uses the shared `dpg_telemetry` package. All adapter executions are instrumented.

### Spans

| Span | Parent | Attributes |
|---|---|---|
| `action.execute` | incoming request | `tool_name`, `adapter_type`, `category`, `session_id` |
| `action.rest_api.http_call` | `action.execute` | `http.method`, `http.url` (scrubbed), `http.status_code`, `latency_ms` |
| `action.mcp.tool_call` | `action.execute` | `mcp.server_url`, `mcp.tool_name`, `latency_ms` |
| `action.startup.adapter_init` | startup | `adapter_type`, `tool_id`, `success` |

### Metrics

| Metric | Type | Labels |
|---|---|---|
| `action.execute.duration_ms` | histogram | `tool_name`, `adapter_type` |
| `action.execute.success_total` | counter | `tool_name` |
| `action.execute.failure_total` | counter | `tool_name`, `error_type` |
| `action.response.size_bytes` | histogram | `tool_name` |
| `action.response.truncated_total` | counter | `tool_name` |

### Structured Logging

Per project logging rules — every adapter execution logs `operation`, `status`, `latency_ms`, `error`. Auth failures logged at ERROR. No PII in logs (no request params that may contain user data).

---

## Module Layout

```
action_gateway/
  src/
    server.py                      # FastAPI: GET /tools, POST /execute, GET /health
    config/
      loader.py                    # YAML config loader
    registry/
      adapter_registry.py          # AdapterRegistry
      adapter_factory.py           # AdapterFactory + ADAPTER_TYPES mapping
    adapters/
      base.py                      # ToolAdapter ABC
      rest_api.py                  # RestApiAdapter
      mcp.py                       # McpAdapter
    models.py                      # ToolDefinition, ToolResult, ExecuteRequest, ExecuteResponse
    interfaces/                    # ABCs for future external dependencies (KE, Memory — #18)
  tests/
    test_adapter_registry.py
    test_adapter_factory.py
    test_rest_api_adapter.py
    test_mcp_adapter.py
    test_server.py
    conftest.py                    # shared fixtures, mock HTTP responses
  pyproject.toml
  Dockerfile
  README.md
```

### What Gets Deleted

- `action_gateway/src/mock_gateway.py` — replaced by adapter framework
- `action_gateway/src/mock_server.py` — replaced by adapter framework
- KKB fixture data — removed entirely (not moved to test fixtures)

---

## Runtime Turn Flow (unchanged)

For reference — the tool-use portion of the turn sequence with the new architecture:

```
LLM responds with tool_use("onest_market_lookup", {trade: "welder", location: "Dharwad"})
  |
  v
ManagerAgent._execute_tool()
  |  ToolRegistry.requires_consent("onest_market_lookup") -> False (category: read)
  |  ToolRegistry.get_route("onest_market_lookup") -> None (external tool)
  |
  v
ActionGatewayHttpClient.execute(tool_call, session_id)
  |  POST http://action_gateway:9999/execute
  |  {tool_name: "onest_market_lookup", tool_use_id: "toolu_01abc",
  |   input_params: {trade: "welder", location: "Dharwad"}, session_id: "sess-xyz"}
  |
  v
Action Gateway server receives request
  |  adapter = registry.resolve("onest_market_lookup")  -> RestApiAdapter instance
  |
  v
RestApiAdapter.execute()
  |  merges agent params + static params (limit: 10)
  |  GET https://api.onest.network/v1/jobs/search?trade=welder&location=Dharwad&limit=10
  |  headers: {X-API-Key: <resolved from ONEST_API_KEY env>}
  |  timeout: 5000ms
  |
  v
Raw JSON response -> truncate to max_size_chars -> ToolResult
  |
  v
Returns to Agent Core -> ManagerAgent appends tool_result -> LLM call #2
```

---

## ARCHITECTURE.md Updates

The following sections of ARCHITECTURE.md need updating after implementation:

1. **Action Gateway section** — rewrite from "PoC stub" to production adapter framework. Document ToolAdapter ABC, adapter types, `GET /tools` endpoint, YAML config schema.
2. **Module Interaction Rules** — add a note for the planned #18 exception (Action Gateway -> KE/Memory for caching). For MVP, no new cross-module calls.
3. **Configuration Architecture** — document that external tool definitions now live in `action_gateway.yaml` (not `agent_core.yaml`). Agent Core fetches them at startup via `GET /tools`.
4. **Implementation Status** — Action Gateway moves from stub to implemented after this work.

---

## Out of Scope

| Item | Tracked in |
|---|---|
| Caching / freshness / persistence policies | #18 |
| MCP tool discovery in dev-kit for subagent assignment | #92 |
| Config-driven response mapping / transformation | #93 |
| OpenAPI spec ingestion in dev-kit | #94 |
| Dev-kit Configuration Agent tool configuration phase | #95 |
| Database adapter (SQLAlchemy, Postgres) | Future |
| File upload adapter | Future |
| gRPC adapter | Future |
| GraphQL adapter | Future |
| OAuth authentication flow | Future |
| Vault / secret manager integration | Future |
