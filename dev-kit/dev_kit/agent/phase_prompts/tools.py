"""Phase prompt builder: tools.

Declares every external tool the agent can invoke via the Action Gateway,
with strict 6-field invocation contracts the LLM must follow.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def _path_of(item) -> str:
    """Extract the dotted field path from a pending_fields item.

    Args:
        item: Either a FieldRule with a ``path`` attribute, a ``(path, rule)``
            tuple, or any other object (falls back to ``str(item)``).

    Returns:
        The dotted field path string.
    """
    if hasattr(item, "path"):
        return item.path
    if isinstance(item, tuple) and len(item) == 2:
        return item[0]
    return str(item)


def _rule_of(item):
    """Extract the FieldRule from a pending_fields item.

    Args:
        item: Either a bare FieldRule, a ``(path, rule)`` tuple, or any object.

    Returns:
        The FieldRule object, or the item itself as a fallback.
    """
    if hasattr(item, "category"):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        return item[1]
    return item


def _render_fields(pending_fields: list) -> str:
    """Render pending fields as a markdown bullet list.

    Args:
        pending_fields: Items where each is either a FieldRule with a ``path``
            attribute, or a ``(path, FieldRule)`` tuple.

    Returns:
        Markdown bullet list with one line per field, or a note if empty.
    """
    if not pending_fields:
        return "_No outstanding fields for this phase._"
    lines = []
    for item in pending_fields:
        path = _path_of(item)
        rule = _rule_of(item)
        desc = getattr(rule, "description", None) or ""
        default = getattr(rule, "default", None)
        applies_if = getattr(rule, "applies_if", None)
        line = f"- `{path}`"
        if desc:
            line += f": {desc}"
        if default is not None:
            line += f" _(default: {default!r})_"
        if applies_if:
            line += f" _(applies if: {applies_if})_"
        lines.append(line)
    return "\n".join(lines)


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

    return f"""# Phase: Tools

You are now declaring every external tool the agent can invoke via the Action
Gateway. The LLM never calls APIs directly — it expresses intent via tool
definitions only; Agent Core routes to Action Gateway.

{tools_expectation}

**MANDATORY FIRST ACTION — do this BEFORE anything else, even if no external
tools are needed:**
`update_config(block=action_gateway, section=observability,
values={{domain: '<project_slug>'}})`
This ensures action_gateway has a non-empty config. Do NOT skip this step.

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

**Three paths to add tools:**

**Path A — OpenAPI spec:**
- URL: call `fetch_openapi_spec_from_url(url)` directly.
- File/paste: call `parse_openapi_spec(spec_json)` with the full spec text.
- Present returned candidates and confirm which to add.
- Call `add_rest_api_tool` once per confirmed tool.

**Path B — MCP server:**
- Ask for the MCP server URL and transport type (`sse` or `streamable_http`).
- Call `discover_mcp_tools` to fetch available tools.
- Call `add_mcp_tool` ONCE for the server (not once per tool).
- Choose a short snake_case namespace id (e.g. `obsrv_docs`).
- MCP tools do NOT auto-create connectors; subagents reference them by
  namespaced names (e.g. `obsrv_docs__searchDocumentation`).

**Path C — Manual REST API:**
- Collect: tool ID, description, base URL, auth type, at least one endpoint.
- Call `add_rest_api_tool`.

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

When all external tools are declared with all six `invocation_rules` fields
populated (or confirmed as not needed), the router advances to the workflow
phase automatically. Do NOT call set_phase.
"""
