# Action Gateway

> Status: ✅ production — generic adapter framework

The sole interface between the DPG framework and external systems. Agent Core routes every LLM-requested tool call here; the LLM never calls external APIs directly. Write and identity connectors require Trust Layer consent before execution.

---

## What this service does

When the LLM decides to use a tool, it returns a `tool_use` block to Agent Core. Agent Core calls `POST /execute` on Action Gateway with the tool name and parameters. Action Gateway looks up the registered adapter for that tool, executes it against the external system, normalises the result, and returns it to Agent Core. The LLM sees only the normalised result — never the raw API response.

Tool definitions live entirely in `action_gateway.yaml` under `tools:[]`. Agent Core fetches the assembled tool list from `GET /tools` at startup and injects it into every LLM request. Adding or removing tools requires only a YAML change and a restart — no code changes.

---

## Folder structure

```
action_gateway/
├── main.py
├── pyproject.toml
├── Dockerfile
├── config/
│   ├── dpg.yaml          # Framework defaults: server port (9999), global timeout_ms
│   └── domain.yaml       # Merged at startup with dev-kit/configs/<domain>/action_gateway.yaml
├── src/
│   ├── server.py         # FastAPI: GET /tools, POST /execute, GET /health
│   ├── models.py         # Pydantic request/response types
│   ├── adapters/
│   │   ├── base.py       # ToolAdapter ABC
│   │   ├── rest_api.py   # RestApiAdapter — HTTP connectors
│   │   └── mcp.py        # McpAdapter — Model Context Protocol servers
│   └── registry/
│       ├── adapter_registry.py   # AdapterRegistry: holds instantiated adapters by tool name
│       └── adapter_factory.py    # AdapterFactory: instantiates adapters from YAML at startup
└── tests/
    ├── test_models.py
    ├── test_rest_api_adapter.py
    ├── test_mcp_adapter.py
    ├── test_adapter_registry.py
    ├── test_adapter_factory.py
    ├── test_server.py
    └── test_main.py
```

Total: 140 tests.

---

## HTTP API

The service runs on port **9999**.

### `GET /tools`

Returns all registered tool definitions in Anthropic tool-use format. Agent Core calls this once at startup.

**Response:**
```json
[
  {
    "name": "onest_market_lookup",
    "description": "Search ONEST live job market data by trade and location.",
    "input_schema": {
      "type": "object",
      "properties": {
        "trade": { "type": "string", "description": "Trade or occupation to search." },
        "location": { "type": "string", "description": "City or district." },
        "distance_km": { "type": "integer", "description": "Search radius in km." }
      },
      "required": ["trade"]
    }
  }
]
```

---

### `POST /execute`

Executes a single tool call. Never raises an HTTP error — always returns 200 with `success: false` and a structured error code on failure.

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

`session_id` is optional.

**Response (success):**
```json
{
  "tool_use_id": "toolu_01abc",
  "success": true,
  "result": {
    "trade": "welder",
    "salary_range": "₹13k–₹22k",
    "market_signal": "8% QoQ",
    "top_employers": ["Hubli Iron Works", "Dharwad Fabrication"]
  },
  "result_text": "Welder salary range: ₹13k–₹22k ...",
  "error": null
}
```

**Response (failure):**
```json
{
  "tool_use_id": "toolu_01abc",
  "success": false,
  "result": null,
  "result_text": null,
  "error": "unknown_tool: onest_market_lookup"
}
```

Emits OTel span `action.execute` with attributes `dpg.tool_name` and `dpg.tool_status`.

---

### `GET /health`

```json
{ "status": "ok" }
```

---

## YAML config schema

Tools are defined in `dev-kit/configs/<domain>/action_gateway.yaml` under `tools:[]`.

```yaml
action_gateway:
  timeout_ms: 5000
  tools:
    - name: onest_market_lookup
      description: "Search ONEST live job market data by trade and location."
      type: rest_api          # rest_api | mcp
      category: read          # read | write | identity
      auth:
        type: api_key         # api_key | bearer | none
        header: X-Api-Key
        env_var: ONEST_API_KEY
      endpoints:
        execute: "https://api.onest.network/v1/market_lookup"
      params:
        - name: trade
          source: agent       # agent (LLM-supplied) | static (config-supplied)
          type: string
          required: true
          description: "Trade or occupation to search."
        - name: location
          source: agent
          type: string
          required: false
          description: "City or district."
        - name: distance_km
          source: agent
          type: integer
          required: false
          description: "Search radius in km."

    - name: onest_apply
      description: "Submit a job application via ONEST."
      type: rest_api
      category: write         # write: Agent Core checks Trust Layer consent before calling /execute
      auth:
        type: bearer
        env_var: ONEST_BEARER_TOKEN
      endpoints:
        execute: "https://api.onest.network/v1/apply"
      params:
        - name: trade
          source: agent
          type: string
          required: true
        - name: employer
          source: agent
          type: string
          required: true
        - name: location
          source: agent
          type: string
          required: false
        - name: applicant_name
          source: agent
          type: string
          required: false
```

### Auth types

| Type | Behaviour |
|---|---|
| `api_key` | Sends the key in the header named by `auth.header`. Key value read from env var `auth.env_var`. |
| `bearer` | Sends `Authorization: Bearer <token>`. Token read from env var `auth.env_var`. |
| `none` | No auth header added. |

### Param sources

| Source | Behaviour |
|---|---|
| `agent` | Value supplied by the LLM in the `tool_use` block. Validated against `type` and `required`. |
| `static` | Value read from config at startup. Never exposed to the LLM. |

### Write connector rule

Tools with `category: write` or `category: identity` require Trust Layer consent before Agent Core calls `POST /execute`. The gateway itself does not enforce this — it is enforced by Agent Core before routing to the gateway.

---

## Adding new tools

1. Add a `tools[]` entry to `dev-kit/configs/<domain>/action_gateway.yaml` with the tool's `type`, `category`, `auth`, `endpoints`, and `params`.
2. Restart Action Gateway. `AdapterFactory` instantiates the adapter at startup — no code changes required.
3. Agent Core picks up the new tool definition on next startup via `GET /tools`.

---

## Adding new adapter types

1. Implement `ToolAdapter` ABC in `action_gateway/src/adapters/`:
   ```python
   from action_gateway.src.adapters.base import ToolAdapter

   class DatabaseAdapter(ToolAdapter):
       def execute(self, tool_name: str, input_params: dict) -> dict: ...
       def get_tool_definition(self, tool_name: str) -> dict: ...
   ```
2. Register the class in `ADAPTER_TYPES` in `action_gateway/src/registry/adapter_factory.py`:
   ```python
   ADAPTER_TYPES = {
       "rest_api": RestApiAdapter,
       "mcp": McpAdapter,
       "database": DatabaseAdapter,   # add here
   }
   ```
3. Add tests in `tests/test_database_adapter.py`.

---

## Running the service

```bash
cd action_gateway
uv run python main.py
```

---

## Running tests

```bash
cd action_gateway
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
fastapi                                  >= 0.110
uvicorn[standard]                        >= 0.29
httpx                                    >= 0.27
pydantic                                 >= 2.0
pyyaml                                   >= 6.0
mcp                                      >= 1.0
observability-layer                      (local path)
opentelemetry-instrumentation-fastapi
```

Requires Python 3.11+.

---

## Known gaps and roadmap

**No response mapping layer.** Tool results are returned as-is from the upstream API. A config-driven response mapping layer — field remapping, value normalisation, error-code canonicalisation — is an open design question (#93). Currently, Agent Core receives raw adapter output and the LLM is responsible for interpreting it.

**No OpenAPI spec ingestion.** Adding a new tool requires hand-authoring the YAML entry. A planned Dev-Kit feature (#94) will ingest an OpenAPI spec and emit a ready-to-use `tools[]` YAML block, removing the manual step.

**MCP adapter timeout handling.** The `McpAdapter` delegates timeout management to the underlying MCP SDK. An explicit per-call timeout matching the `action_gateway.timeout_ms` config is not yet enforced at the adapter level.

**Tool configuration phase in Dev-Kit not yet built.** The Configuration Agent (Dev-Kit Tier 1) does not yet have a tool configuration phase that walks domain experts through defining connector entries. This is tracked in #95.
