# Action Gateway

> Status: 🟡 stub — no real ONEST API calls; all responses are fixture data.

The sole interface between the DPG framework and external systems. Agent Core routes every tool call here; the LLM never calls external APIs directly. Write and identity connectors require Trust Layer consent before execution.

---

## What this service does

When the LLM decides to use a tool, it returns a `tool_use` block to Agent Core. Agent Core sends that request to Action Gateway via `POST /execute`. Action Gateway dispatches to the correct connector, normalises the result, and returns it to Agent Core. The LLM sees only the normalised result — never the raw API response.

Two connectors are implemented for the PoC:

- `onest_market_lookup` — read connector. Returns live labour market data (salary range, demand signal, top employers) for a given trade and location.
- `onest_apply` — write connector. Submits a job application. Requires Trust Layer consent before Agent Core will call this.

**Write connector rule:** Agent Core calls Trust Layer `POST /check/consent` before executing any `write` or `identity` connector. The PoC always returns `granted: true` for non-blocked connectors.

---

## Folder structure

```
action_gateway/
├── main.py
├── pyproject.toml
├── Dockerfile
├── config/
│   ├── dpg.yaml          # Server port (9999), global timeout_ms
│   └── domain.yaml       # Connector endpoint URLs, per-connector timeout
├── src/
│   ├── mock_server.py    # FastAPI mock ONEST API (POST /onest/market_lookup, POST /onest/apply, POST /execute)
│   └── mock_gateway.py   # MockActionGateway — implements list_available_tools() + execute()
└── tests/
    ├── test_mock_server.py   (31 tests)
    ├── test_mock_gateway.py  (28 tests)
    └── test_main.py          (5 tests)
```

Total: 64 tests.

---

## HTTP API

The service runs on port **9999**.

### `POST /onest/market_lookup`

Returns labour market data for a trade and location.

**Request:**
```json
{
  "trade": "electrician",
  "location": "Hubli",
  "distance_km": 50
}
```

`trade` is required. `location` defaults to `""`. `distance_km` defaults to `50`.

**Response:**
```json
{
  "trade": "electrician",
  "salary_range": "₹15k–₹28k",
  "market_signal": "steady signal 12% QoQ",
  "top_employers": ["Hubli Distribution Co", "Karnataka Power"],
  "source": "ONEST",
  "location_queried": "Hubli"
}
```

---

### `POST /onest/apply`

Submits a job application. Always returns success in the PoC.

**Request:**
```json
{
  "trade": "electrician",
  "employer": "Hubli Distribution Co",
  "location": "Hubli",
  "applicant_name": "Rahul"
}
```

`trade` and `employer` are required. `location` and `applicant_name` default to `""`.

**Response:**
```json
{
  "status": "success",
  "reference_number": "APP-X7K2M9",
  "message": "Application submitted successfully",
  "employer": "Hubli Distribution Co",
  "trade": "electrician"
}
```

`reference_number` is 6 random alphanumeric characters prefixed with `APP-`.

---

### `POST /execute`

Generic router used by Agent Core. Dispatches to the correct connector by `tool_name`.

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

**Response:**
```json
{
  "tool_use_id": "toolu_01abc",
  "success": true,
  "result": { ... },
  "result_text": "Welder salary range: ₹13k–₹22k ...",
  "error": null
}
```

On failure, `success` is `false`, `error` contains a structured error code (see error codes below), and the endpoint never raises — it always returns 200.

Emits OTel span `action.execute` with attributes `dpg.tool_name` and `dpg.tool_status`.

---

### `GET /health`

```json
{ "status": "ok" }
```

---

## Fixture data

Trade matching is case-insensitive (`Electrician`, `ELECTRICIAN`, and `electrician` all match).

| Trade | Salary Range | Market Signal | Top Employers |
|-------|-------------|---------------|---------------|
| electrician | ₹15k–₹28k | steady signal 12% QoQ | Hubli Distribution Co, Karnataka Power |
| welder | ₹13k–₹22k | 8% QoQ | Hubli Iron Works, Dharwad Fabrication |
| fitter | ₹14k–₹24k | 10% QoQ | BEML Hubli, KA Manufacturing |
| plumber / plumbing | ₹12k–₹22k | growing 9% QoQ | Hubli Municipal Corp, KA Infrastructure Projects |
| carpenter | ₹13k–₹24k | stable 6% QoQ | Dharwad Furniture Hub, Urban Interiors Hubli |
| mason | ₹14k–₹25k | growing 11% QoQ | KA Construction Co, Hubli Builders Association |
| driver | ₹14k–₹26k | high demand 15% QoQ | Ola Fleet Hubli, Karnataka Road Transport |
| tailor | ₹10k–₹18k | stable 5% QoQ | Dharwad Garments, KA Textile Mills |
| *(any other trade)* | ₹12k–₹20k | stable | Local Contractor Network, District Employment Exchange |

---

## Tool definitions (as seen by the LLM)

Agent Core loads these from config and includes them in every LLM request:

```yaml
connectors:
  read:
    - name: onest_market_lookup
      description: "Search ONEST live job market data by trade and location"
      parameters:
        trade:
          type: string
          required: true
        location:
          type: string
        distance_km:
          type: integer

  write:
    - name: onest_apply
      description: "Submit a job application via ONEST"
      parameters:
        trade:
          type: string
          required: true
        employer:
          type: string
          required: true
        location:
          type: string
        applicant_name:
          type: string
```

`onest_apply` is a write connector. Agent Core checks Trust Layer `POST /check/consent` before executing it.

---

## Error codes

`MockActionGateway.execute()` never raises. On failure it returns `success: false` with one of these error codes:

| Condition | Error code |
|-----------|-----------|
| Unknown tool name | `unknown_tool: {tool_name}` |
| Lookup request timed out | `onest_lookup_timeout` |
| Apply request timed out | `apply_timeout` |
| HTTP error from mock server | `onest_http_error: {status_code}` |
| Any other exception | `onest_error: {ExceptionType}` |

---

## Configuration

| Key | Description |
|-----|-------------|
| `server.port` | HTTP port (default: `9999`) |
| `action_gateway.timeout_ms` | Global request timeout in milliseconds (default: `5000`) |
| `action_gateway.connectors.{tool_name}.endpoint` | Endpoint URL for the connector |
| `action_gateway.connectors.{tool_name}.timeout_ms` | Per-connector timeout override |

Config is loaded once at startup by deep-merging `config/dpg.yaml` (framework defaults) with `config/domain.yaml` (domain overrides).

---

## Running the service

```bash
cd action_gateway
uv run uvicorn src.mock_server:app --port 9999
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
observability-layer                      (local path)
opentelemetry-instrumentation-fastapi
```

Requires Python 3.11+.

---

## Adding new connectors

1. Add a new endpoint to `src/mock_server.py` with the mock response (or point to a real API endpoint).
2. Add the tool definition to the domain config under `connectors.read` or `connectors.write`.
3. Add the connector's `endpoint` and `timeout_ms` under `action_gateway.connectors.{tool_name}` in `config/domain.yaml`.
4. If it is a `write` connector, consent gating is automatic — Agent Core checks Trust Layer before calling `POST /execute`.
5. The LLM picks up the new tool definition on next startup.
