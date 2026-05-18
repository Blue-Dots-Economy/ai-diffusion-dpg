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

Both return `{{"ok": true, "operations": [{{id, path, method, summary}},
...]}}`. Present the operation list, confirm which ones to add, then for
each confirmed operation call:

`add_tool(spec={{id: ..., type: "rest_api", category: "read"|"write"|"identity",
base_url: ..., endpoints: [...], auth: ...}})`.

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
