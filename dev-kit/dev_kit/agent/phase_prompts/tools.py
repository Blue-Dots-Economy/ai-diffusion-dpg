"""Phase prompt builder: tools.

Declares every external tool the agent can invoke via the Action Gateway,
with strict 6-field invocation contracts the LLM must follow. Part of the
dev-kit deterministic wizard's phase-prompt system.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import (
    _phase_focus_header,
    _closing_block,
    _common_rules,
    _path_of,
    _render_fields,
    _rule_of,
)

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the tools phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the tools phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference.
        intake_state: Current IntakeState. Used to determine if external tools
            are expected (has_external_tools).

    Returns:
        A non-empty string to append to the base system prompt for the tools
        phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    has_external = getattr(intake_state, "has_external_tools", False)

    tools_expectation = (
        "This project indicated it **needs external tools** (`has_external_tools=true`). "
        "At least one tool definition is expected in this phase."
        if has_external
        else "This project indicated it does **NOT** need external tools. Confirm with the "
        "user and, if confirmed, proceed directly after the mandatory first action below."
    )

    return f"""{_phase_focus_header("tools", pending_fields)}# Phase: Tools

You are declaring every external tool the agent can invoke via the Action
Gateway. At runtime, the agent never calls APIs directly — it expresses
intent via tool definitions only, and Agent Core routes to Action Gateway.

{tools_expectation}

{_common_rules()}

Do NOT write `action_gateway.observability.domain` — derived field, the
wizard computes it automatically from the project slug.

**For each tool, define 6 `invocation_rules` fields:**
1. `call_when` — exact trigger condition in plain language.
2. `required_before_calling` — data fields required before invocation; the
   tool MUST NOT be called if any are missing.
3. `must_not_substitute` — memory or prior context the LLM must never use
   as a substitute for a fresh tool call.
4. `on_empty` — exact natural line the agent says when the tool returns no
   results.
5. `on_failure` — exact natural line on tool failure or timeout.
6. `bridge_line` — short line the agent says right before the tool call
   (e.g. "Let me check that for you."). Essential for voice; optional for
   chat.

**Three independent paths to add tools — pick whichever fits the user's
input, then finish with `add_tool(spec=...)`:**

**Path A — OpenAPI spec (URL or pasted text):**

Two sub-paths depending on what the user gave you:

- *URL*: call `fetch_openapi_spec_from_url(url=<URL>)`. The wizard
  downloads the spec (JSON or YAML), parses it, and returns candidate
  operations. Do NOT ask the user to paste the spec when a URL is
  available.
- *Pasted text*: call `parse_openapi_spec(spec=<json-or-yaml-text>)` with
  the full text the user pasted. Same return shape as the URL path.

Both return `{{"ok": true, "operations": [{{id, path, method, summary,
params, response_fields}}, ...]}}` where `params` lists every query/body
parameter and `response_fields` lists candidate JSONPath fields the
wizard could project from the 200 response into the LLM's tool result.

**MANDATORY pacing — do NOT skip any of these steps:**

1. **Step 1: parse + show.** After `parse_openapi_spec` or
   `fetch_openapi_spec_from_url` succeeds, render every operation in a
   table for the user with the four columns:

   | Operation | Method + Path | Params | Response fields the bot will read |
   |---|---|---|---|
   | `getWeatherForecast` | `GET /v1/forecast` | `latitude`, `longitude`, `current`, `timezone` | `current.temperature_2m`, `current.weather_code`, `current.wind_speed_10m` |
   | … | … | … | … |

   Pick a sensible default category (`read` for GET endpoints, `write`
   for POST/PUT/DELETE that mutate state, `identity` for auth/profile
   endpoints) and list it under each operation row.

2. **Step 2: confirm with the user — STOP after this question.** Ask:

   > "Here's what I parsed. For each operation: do the response fields
   > look right (the bot only sees these from the API response), and
   > should I register all of them? Reply 'yes' to confirm everything,
   > or tell me which fields to add, drop, or rename."

   **Do NOT call `add_tool` in the same turn as the parse.** The
   wizard's dispatcher hard-rejects same-turn `add_tool` calls — if
   you ignore this rule the tool returns an error, no tool gets
   registered, and your reply must STILL render the operations table
   and the confirmation question. There is no shortcut. End your
   reply after the confirmation question — no follow-up paragraphs,
   no preview of the next phase, no claim that tools have been
   registered.

3. **Step 3: register.** Only after the user confirms in their next
   reply, call `add_tool` once per confirmed operation. Use the
   parser's snake_case `suggested_id` (NOT the operationId from the
   spec — e.g. use `get_v1_forecast`, NOT `getWeatherForecast`; the
   accumulator's `tools[i].id` must match `^[a-z][a-z0-9_]*$`).

   **Every field below is required** — REST tools without `description`,
   `base_url`, `auth`, or `endpoints[i].name` are rejected by the
   strict validator (the deploy-time runtime requires them too).
   Template:

   ```
   add_tool(spec={{
     id: "<snake_case_id, e.g. get_v1_forecast>",
     type: "rest_api",
     category: "read" | "write" | "identity",
     description: "<one-line plain English; REQUIRED, non-empty>",
     base_url: "<root URL from spec.servers[0].url; e.g. https://api.open-meteo.com>",
     timeout_ms: 5000,
     auth: {{
       type: "none" | "api_key" | "bearer" | "oauth2",
       header: "",
       secret_env: "",
       token_url: ""
     }},
     endpoints: [
       {{
         name: "<endpoint name; e.g. forecast — REQUIRED, non-empty>",
         method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH",
         path: "<path relative to base_url, e.g. /v1/forecast>",
         params: [
           {{
             name: "<param name>",
             source: "agent",
             type: "string" | "integer" | "boolean" | "array",
             required: true | false,
             description: "<what this param is>"
           }}
         ]
       }}
     ],
     response: {{
       projection: {{
         list_key: "<dot-path to a list in the response, or empty for object root>",
         fields: {{
           "<short_name_for_LLM>": "<dot-path in response>"
         }}
       }}
     }}
   }})
   ```

   - `id` MUST be snake_case (`^[a-z][a-z0-9_]*$`). Use the
     `suggested_id` from the parser output verbatim. The runtime
     `agent_core.connectors.<category>` matches by this id.
   - `description` is REQUIRED and non-empty. The LLM uses it to
     decide when to call this tool at runtime.
   - `base_url` is at the **tool level**, not nested inside each
     endpoint. Take it from `spec.servers[0].url`. Per-operation server
     overrides (e.g. the booking webhook on `webhook.site`) override
     `base_url` for that tool.
   - `auth.type: "none"` is fine when the API has no auth (Open-Meteo,
     test webhooks); otherwise set `secret_env` to a UPPER_SNAKE_CASE
     name (e.g. `OPENWEATHER_API_KEY`) — the actual key value is
     collected in the Deploy step, NEVER asked in chat.
   - `params[i].source: "agent"` is the only sensible value here (the
     LLM supplies these at call time). `static` is reserved for
     deploy-time constants and not used in chat.
   - `params[i].type`: only `string` | `integer` | `boolean` | `array`.
     **`number` and `float` are NOT valid** — use `string` for
     decimal numbers (latitude, longitude, pricing) and the REST
     adapter forwards them verbatim to the HTTP query.
   - Use `list_key=""` for endpoints that return a single object (e.g.
     a forecast wraps `{{current: {{...}}}}`); set `list_key` to the
     field name (e.g. `"results"`) for endpoints that wrap data in an
     array (e.g. a geocode response wraps `{{results: [{{...}}, ...]}}`).
   - `projection.fields` keys are the short names the LLM will see in
     its tool result; values are dot-paths into the raw response (or
     into each list item when `list_key` is set).
   - If the user edited the projection ("drop wind_speed", "add
     relative_humidity", "rename temperature_2m to temp_c"), reflect
     their edits in `fields`. **Never** silently drop or add fields
     the user did not ask for.

Skipping the confirmation step is a regression — it removes the user's
ability to audit which API response fields the agent will see, and the
wizard's audit log loses the human-in-the-loop check on tool wiring.

**Path B — MCP server:**
- Ask for the MCP server URL and transport type (`sse` or `streamable_http`).
- Call `discover_mcp_tools(server_url=<URL>)`. Returns the server's
  advertised tools as `{{"ok": true, "tools": [{{name, description,
  input_schema}}, ...]}}` — auto-handles both plain JSON-RPC and SSE
  responses. Summarise the discovered tools for the user.
- Call `add_tool(spec={{id: ..., type: "mcp",
  mcp_server_url: ..., transport: ...}})` ONCE for the server (NOT once
  per tool — the MCP adapter discovers individual operations from the
  server at runtime).
- Choose a short snake_case namespace id (e.g. `obsrv_docs`).
- MCP tools do NOT auto-create connectors; subagents reference them by
  namespaced names (e.g. `obsrv_docs__searchDocumentation`).

**Path C — Manual REST API (no spec, the user describes the endpoint):**
- Collect: tool id, description, base URL, auth type, at least one endpoint.
- Call `add_tool(spec={{id: ..., type: "rest_api", category: ..., base_url: ...,
  endpoints: [...], auth: ...}})` directly — skip the parser entirely.

**After each REST API tool — ALWAYS do this:**
1. Ask: "Can you share a sample JSON response? Or describe the key fields
   you need the AI to work with."
2. Identify the key fields the LLM needs and their JSONPaths in the response
   structure.
3. Confirm the field list with the user: "I'll extract these fields: ...
   Does that look right?"
4. Record the confirmed field list in the invocation_rules `bridge_line`
   or as a connector description note so the LLM knows what to surface.
   (Response field filtering is configured separately — ask the user if they
   want to restrict the raw response passed to the LLM.)

**Auth credentials:** When auth is required, do NOT ask for the credential
value in chat. Say: "This tool needs an API key in env var `<auth_secret_env>`.
Keep that key ready — you will enter it securely in the Deployment Inputs step."

**REST API param type rules:**
Valid `type` values: `string`, `integer`, `boolean`, `array`.
`number` and `float` are NOT valid — use `string` instead.

**Connector input_schema.properties must mirror the tool's params exactly.**
Do NOT rename, add, or remove keys. The REST adapter forwards the LLM's
params verbatim to the HTTP request.

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

{_closing_block()}
"""
