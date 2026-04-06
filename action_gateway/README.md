# Action Gateway DPG

The framework's only interface with the external world. All external API calls go through this service.

---

## What this service does

The Action Gateway executes external API calls on behalf of the LLM. The LLM expresses intent by returning a `tool_use` block — it never calls external systems directly. Agent Core detects the tool request, routes it to the Action Gateway, and feeds the normalised result back into the conversation.

For the PoC, this is a mock FastAPI server (`mock_server.py`) that returns hardcoded fixture data. The PoC includes two connectors: `onest_market_lookup` (read) and `onest_apply` (write, requires Trust Layer consent).

**Write connector rule:** Any connector of type `write` or `identity` requires explicit user consent via Trust Layer `POST /check/consent` before Agent Core will execute it. The PoC always returns `granted: true` for non-blocked connectors.

---

## Folder structure

```
action_gateway/
├── main.py                 # Uvicorn entrypoint (port 9999)
├── pyproject.toml
├── config/
│   └── config.yaml         # Connector endpoint and timeout
├── src/
│   ├── mock_server.py      # FastAPI mock ONEST API (POST /onest/market_lookup)
│   └── mock_gateway.py     # ActionGateway implementation (ActionGatewayBase)
└── tests/
    └── test_mock_server.py
```

---

## HTTP API

The mock server runs on port **9999**.

### `POST /onest/market_lookup`

Returns labour market data (salary range, demand signal, top employers) for a given trade and location.

This endpoint simulates the real ONEST network API. In production, this call would go to the live ONEST marketplace.

**Request:**
```json
{
  "trade": "electrician",
  "location": "Hubli",
  "distance_km": 50
}
```

**Response:**
```json
{
  "trade": "electrician",
  "salary_range": "₹15k–₹28k",
  "market_signal": "steady signal 12% QoQ",
  "top_employers": ["Hubli Distribution Co", "Karnataka Power"],
  "source": "ONEST"
}
```

### `POST /onest/apply`

Submits a job application for a user to an employer on the ONEST network. Requires Trust Layer consent before Agent Core will call this endpoint.

**Request:**
```json
{
  "user_id": "user-abc123",
  "trade": "electrician",
  "employer": "Hubli Distribution Co",
  "location": "Hubli"
}
```

**Response:**
```json
{
  "applied": true,
  "application_id": "app-mock-001",
  "employer": "Hubli Distribution Co",
  "status": "submitted"
}
```

---

## Fixture data (PoC)

The mock server returns hardcoded responses for three trades. Any other trade name returns the default fixture.

| Trade | Salary Range | Market Signal | Top Employers |
|---|---|---|---|
| `electrician` | ₹15k–₹28k | steady signal 12% QoQ | Hubli Distribution Co, Karnataka Power |
| `welder` | ₹13k–₹22k | 8% QoQ | Hubli Iron Works, Dharwad Fabrication |
| `fitter` | ₹14k–₹24k | 10% QoQ | BEML Hubli, KA Manufacturing |
| *(any other)* | ₹12k–₹20k | stable | — |

Trade matching is **case-insensitive** (e.g. `Electrician`, `ELECTRICIAN`, `electrician` all match).

---

## Tool definition (as seen by the LLM)

Agent Core loads this tool definition from config and sends it to the LLM on every turn. The LLM uses it to decide when to call market lookup:

```yaml
connectors:
  read:
    - name: onest_market_lookup
      description: "Look up current salary range and demand for a trade in a location"
      parameters:
        trade:
          type: string
          description: "Occupation or trade (e.g. electrician, welder, fitter)"
        location:
          type: string
          description: "City or district"
        distance_km:
          type: integer
          description: "Search radius in kilometres (default 50)"
  write:
    - name: onest_apply
      description: "Submit a job application for a user to an employer on the ONEST network"
      parameters:
        user_id:
          type: string
          description: "Unique user identifier"
        trade:
          type: string
          description: "Trade or occupation"
        employer:
          type: string
          description: "Employer name to apply to"
        location:
          type: string
          description: "Location of the job"
```

> **Note:** Write connectors (`onest_apply`) require explicit user consent via Trust Layer `POST /check/consent` before Agent Core will execute them. The PoC always returns `granted: true` for non-blocked connectors.

---

## Configuration

| Key | Description |
|---|---|
| `connector.endpoint` | URL of the ONEST mock server (default: `http://localhost:9999/onest/market_lookup`) |
| `connector.timeout_s` | Request timeout for market lookup calls (default: 5s) |

---

## Running the service

```bash
cd action_gateway
uv run uvicorn src.mock_server:app --port 9999
```

---

## Running tests

```bash
source ../.venv/bin/activate
cd action_gateway
pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Dependencies

```
fastapi   >= 0.110
uvicorn   >= 0.29
pydantic  >= 2.0
pyyaml    >= 6.0
httpx     >= 0.27    # Used by mock_gateway.py to call the mock server
```

Requires Python 3.11+.

---

## Adding new connectors

To add a new external connector (e.g. a scheme application API):

1. Add a new endpoint to `mock_server.py` with the mock response (or point to a real API).
2. Add the connector definition to `agent_core/config/config.yaml` under `connectors.read` or `connectors.write`.
3. If it is a `write` connector, it will automatically require user consent via the Trust Layer — no code changes needed.
4. The LLM will pick up the new tool definition on next startup and use it when appropriate.
