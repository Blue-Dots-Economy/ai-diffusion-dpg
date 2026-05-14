"""
dev-kit/dev_kit/agent/tools.py

Canonical 8-tool set for the deterministic wizard (design §6: "Slimmed tool
surface") plus the legacy ToolHandler/TOOL_DEFINITIONS for the old-wizard path.

The 8 new top-level functions (``update_intake``, ``update_config``,
``add_subagent``, ``update_subagent``, ``add_routing_rule``, ``add_tool``,
``parse_openapi_spec``, ``discover_mcp_tools``) are Python callables routed
by ``phase_driver.TOOL_HANDLERS`` when the LLM emits a tool call by name.
They are NOT Anthropic tool_use JSON schemas.

All 8 tools share a uniform signature::

    def tool_fn(
        args: dict[str, Any],
        intake_state: IntakeState,
        accumulator: dict[str, dict],
        field_status: dict[str, str],
    ) -> dict[str, Any]:

Return value: ``{"ok": True, ...}`` on success, ``{"ok": False, "error": "..."}``
on failure.

The legacy ``ToolHandler`` class and ``TOOL_DEFINITIONS`` list remain for
``conversation.py`` (old wizard path) until Task 12.1/12.2 ports it to
``phase_driver``.

Belongs to the dev-kit deterministic wizard.
See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §6.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from dev_kit.agent.accumulator import BLOCKS, PHASES, ConfigAccumulator, ConfigStatus
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.prompts.base import AGENT_TYPES, SHEET_REQUIREMENTS
from dev_kit.agent.router import on_config_update, on_intake_update
from dev_kit.schemas.cross_block_validation import validate_cross_block
from dev_kit.schemas.validation import get_valid_sections


# ---------------------------------------------------------------------------
# § 6 — Canonical 8-tool set (phase_driver.TOOL_HANDLERS uses these)
# ---------------------------------------------------------------------------

__all__ = [
    "update_intake",
    "update_config",
    "add_subagent",
    "update_subagent",
    "add_routing_rule",
    "add_tool",
    "parse_openapi_spec",
    "discover_mcp_tools",
]


def update_intake(
    args: dict[str, Any],
    intake_state: IntakeState,
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Mutate an IntakeState field and cascade through FIELD_RULES.

    Delegates to ``router.on_intake_update`` to apply the change and
    re-evaluate any predetermined rules that depend on the updated field.

    Args:
        args: Must contain ``field`` (str) and ``value`` (Any).
        intake_state: Current IntakeState — mutated in-place on success.
        accumulator: Per-block YAML dicts — may be mutated by cascade.
        field_status: Per-field status registry — may be mutated by cascade.

    Returns:
        ``{"ok": True, ...}`` from ``on_intake_update`` on success.
        ``{"ok": False, "error": "..."}`` if ``field`` is unknown or missing.
    """
    field = args.get("field")
    if not field:
        return {"ok": False, "error": "args.field is required"}
    if "value" not in args:
        return {"ok": False, "error": "args.value is required"}
    try:
        return on_intake_update(
            field,
            args["value"],
            intake_state,
            accumulator,
            field_status,
        )
    except AttributeError as exc:
        logger.warning(
            "update_intake.rejected",
            extra={
                "operation": "tools.update_intake",
                "status": "failure",
                "error": str(exc),
                "field": field,
            },
        )
        return {"ok": False, "error": str(exc)}


def update_config(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Apply a user chat answer to the accumulator with mirror validation.

    Accepts two calling forms:

    * **Path form** (preferred): ``args = {"path": "block.section.field", "value": ...}``
      — delegates directly to ``router.on_config_update``.
    * **Block/section/values form**: ``args = {"block": "...", "section": "...",
      "values": {...}}`` — normalises each key to path form.

    Args:
        args: Tool arguments. Must provide either ``path``+``value`` or
            ``block``+``section``+``values``.
        intake_state: Not used by this tool; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Per-field status registry — mutated on success.

    Returns:
        ``{"ok": True, "path": ..., "value": ...}`` on success (path form).
        ``{"ok": True, "results": [...]}`` on success (block/section form).
        ``{"ok": False, "error": "..."}`` on validation failure or bad args.
    """
    if "path" in args:
        if "value" not in args:
            return {"ok": False, "error": "args.value is required when args.path is set"}
        try:
            return on_config_update(args["path"], args["value"], accumulator, field_status)
        except ValueError as exc:
            logger.warning(
                "update_config.rejected",
                extra={
                    "operation": "tools.update_config",
                    "status": "failure",
                    "error": str(exc),
                    "path": args.get("path"),
                },
            )
            return {"ok": False, "error": str(exc), "path": args.get("path")}

    # Block/section/values form
    block = args.get("block")
    section = args.get("section")
    values = args.get("values") or {}
    if not block:
        return {"ok": False, "error": "args.block is required"}
    if not section:
        return {"ok": False, "error": "args.section is required"}
    if not isinstance(values, dict):
        return {
            "ok": False,
            "error": f"args.values must be a dict, got {type(values).__name__!r}",
        }

    results: list[dict[str, Any]] = []
    for key, value in values.items():
        path = f"{block}.{section}.{key}"
        try:
            result = on_config_update(path, value, accumulator, field_status)
            results.append(result)
        except ValueError as exc:
            logger.warning(
                "update_config.rejected",
                extra={
                    "operation": "tools.update_config",
                    "status": "failure",
                    "error": str(exc),
                    "path": path,
                },
            )
            return {"ok": False, "error": str(exc), "path": path}

    return {"ok": True, "results": results}


def add_subagent(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Append a new subagent definition to ``agent_core.agent_workflow.subagents``.

    The definition is validated via ``validate_partial`` against the mirror
    schema. On failure the appended item is removed (reverted) and an error
    is returned.

    Args:
        args: Must contain ``definition`` (dict) with at least an ``id`` key.
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "id": subagent_id}`` on success.
        ``{"ok": False, "error": "..."}`` on validation failure or missing id.
    """
    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415

    definition = args.get("definition")
    if not isinstance(definition, dict):
        return {"ok": False, "error": "args.definition must be a dict"}

    subagent_id = definition.get("id")
    if not subagent_id:
        return {"ok": False, "error": "definition.id is required"}

    workflow = accumulator.setdefault("agent_core", {}).setdefault("agent_workflow", {})
    subagents: list[dict] = workflow.setdefault("subagents", [])

    if any(sa.get("id") == subagent_id for sa in subagents):
        return {
            "ok": False,
            "error": f"subagent id {subagent_id!r} already exists; use update_subagent to modify it",
        }

    subagents.append(copy.deepcopy(definition))

    errors = validate_partial("agent_core", accumulator.get("agent_core", {}))
    if errors:
        subagents.pop()
        error_msg = "; ".join(errors)
        logger.warning(
            "add_subagent.validation_failed",
            extra={
                "operation": "tools.add_subagent",
                "status": "failure",
                "error": error_msg,
                "subagent_id": subagent_id,
            },
        )
        return {"ok": False, "error": f"Validation failed: {error_msg}"}

    logger.info(
        "add_subagent.success",
        extra={
            "operation": "tools.add_subagent",
            "status": "success",
            "subagent_id": subagent_id,
        },
    )
    return {"ok": True, "id": subagent_id}


def update_subagent(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Modify fields on an existing subagent in-place.

    Finds the subagent by ``id``, applies ``fields`` as a shallow update, then
    re-validates. On validation failure the update is reverted.

    Args:
        args: Must contain ``id`` (str) and ``fields`` (dict).
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "id": subagent_id}`` on success.
        ``{"ok": False, "error": "..."}`` if not found or validation fails.
    """
    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415

    subagent_id = args.get("id")
    fields = args.get("fields")
    if not subagent_id:
        return {"ok": False, "error": "args.id is required"}
    if not isinstance(fields, dict):
        return {"ok": False, "error": "args.fields must be a dict"}

    subagents = (
        accumulator.get("agent_core", {})
        .get("agent_workflow", {})
        .get("subagents", [])
    )

    for sa in subagents:
        if sa.get("id") == subagent_id:
            snapshot = copy.deepcopy(sa)
            sa.update(fields)

            errors = validate_partial("agent_core", accumulator.get("agent_core", {}))
            if errors:
                sa.clear()
                sa.update(snapshot)
                error_msg = "; ".join(errors)
                logger.warning(
                    "update_subagent.validation_failed",
                    extra={
                        "operation": "tools.update_subagent",
                        "status": "failure",
                        "error": error_msg,
                        "subagent_id": subagent_id,
                    },
                )
                return {"ok": False, "error": f"Validation failed: {error_msg}"}

            logger.info(
                "update_subagent.success",
                extra={
                    "operation": "tools.update_subagent",
                    "status": "success",
                    "subagent_id": subagent_id,
                },
            )
            return {"ok": True, "id": subagent_id}

    return {"ok": False, "error": f"subagent id {subagent_id!r} not found"}


def add_routing_rule(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Append a routing rule (transition edge) to a subagent's routing list.

    Finds ``from_subagent_id`` in the subagents list and appends a rule
    ``{"intent": ..., "to": ..., "condition": ...}``.

    Args:
        args: Must contain ``from_subagent_id`` (str), ``intent`` (str), and
            ``to_subagent_id`` (str). Optional: ``condition`` (str).
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "from": ..., "intent": ..., "to": ...}`` on success.
        ``{"ok": False, "error": "..."}`` if the source subagent is not found.
    """
    from_id = args.get("from_subagent_id")
    intent = args.get("intent")
    to_id = args.get("to_subagent_id")

    if not from_id:
        return {"ok": False, "error": "args.from_subagent_id is required"}
    if not intent:
        return {"ok": False, "error": "args.intent is required"}
    if not to_id:
        return {"ok": False, "error": "args.to_subagent_id is required"}

    subagents = (
        accumulator.get("agent_core", {})
        .get("agent_workflow", {})
        .get("subagents", [])
    )

    for sa in subagents:
        if sa.get("id") == from_id:
            rule: dict[str, Any] = {"intent": intent, "to": to_id}
            condition = args.get("condition")
            if condition:
                rule["condition"] = condition
            sa.setdefault("routing", []).append(rule)
            logger.info(
                "add_routing_rule.success",
                extra={
                    "operation": "tools.add_routing_rule",
                    "status": "success",
                    "from_subagent_id": from_id,
                    "intent": intent,
                    "to_subagent_id": to_id,
                },
            )
            return {"ok": True, "from": from_id, "intent": intent, "to": to_id}

    return {
        "ok": False,
        "error": f"source subagent id {from_id!r} not found",
    }


def add_tool(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Add an action_gateway tool and matching agent_core connector.

    Appends ``spec`` to ``accumulator["action_gateway"]["tools"]`` and syncs
    the LLM-facing connector into ``accumulator["agent_core"]["connectors"]``
    under the appropriate category (``read``, ``write``, or ``identity``).

    Both additions are validated via ``validate_partial``. On failure the
    appended items are reverted.

    Args:
        args: Must contain ``spec`` (dict) with at least ``id``, ``type``
            (``rest_api`` or ``mcp``), and ``category``
            (``read``, ``write``, or ``identity``).
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "id": tool_id}`` on success.
        ``{"ok": False, "error": "..."}`` on duplicate id or validation failure.
    """
    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415

    spec = args.get("spec")
    if not isinstance(spec, dict):
        return {"ok": False, "error": "args.spec must be a dict"}

    tool_id = spec.get("id")
    if not tool_id:
        return {"ok": False, "error": "spec.id is required"}

    # --- Action Gateway side ---
    ag = accumulator.setdefault("action_gateway", {})
    tools_list: list[dict] = ag.setdefault("tools", [])

    if any(t.get("id") == tool_id for t in tools_list):
        return {"ok": False, "error": f"tool id {tool_id!r} already exists"}

    tools_list.append(copy.deepcopy(spec))

    errors = validate_partial("action_gateway", ag)
    if errors:
        tools_list.pop()
        error_msg = "; ".join(errors)
        logger.warning(
            "add_tool.ag_validation_failed",
            extra={
                "operation": "tools.add_tool",
                "status": "failure",
                "error": error_msg,
                "tool_id": tool_id,
            },
        )
        return {"ok": False, "error": f"action_gateway validation failed: {error_msg}"}

    # --- Agent Core connector side ---
    # MCP tools — schemas come from the server at runtime; no static connector.
    if spec.get("type") != "mcp":
        category = spec.get("category", "read")
        properties: dict[str, Any] = {}
        required_list: list[str] = []
        for endpoint in spec.get("endpoints", []):
            for param in endpoint.get("params", []):
                if param.get("source") != "agent":
                    continue
                prop: dict[str, Any] = {"type": param.get("type", "string")}
                if param.get("description"):
                    prop["description"] = param["description"]
                if param.get("default") is not None:
                    prop["default"] = param["default"]
                properties[param["name"]] = prop
                if param.get("required"):
                    required_list.append(param["name"])
        input_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required_list:
            input_schema["required"] = required_list

        connector = {
            "name": tool_id,
            "description": spec.get("description", ""),
            "input_schema": input_schema,
        }
        ac = accumulator.setdefault("agent_core", {})
        connectors_block = ac.setdefault("connectors", {})
        connector_list: list[dict] = connectors_block.setdefault(category, [])

        replaced = False
        for i, c in enumerate(connector_list):
            if c.get("name") == tool_id:
                connector_list[i] = connector
                replaced = True
                break
        if not replaced:
            connector_list.append(connector)

        errors = validate_partial("agent_core", ac)
        if errors:
            if not replaced:
                connector_list.pop()
            else:
                connector_list[:] = [c for c in connector_list if c.get("name") != tool_id]
            tools_list.pop()
            error_msg = "; ".join(errors)
            logger.warning(
                "add_tool.ac_validation_failed",
                extra={
                    "operation": "tools.add_tool",
                    "status": "failure",
                    "error": error_msg,
                    "tool_id": tool_id,
                },
            )
            return {"ok": False, "error": f"agent_core validation failed: {error_msg}"}

    logger.info(
        "add_tool.success",
        extra={
            "operation": "tools.add_tool",
            "status": "success",
            "tool_id": tool_id,
            "tool_type": spec.get("type", "unknown"),
        },
    )
    return {"ok": True, "id": tool_id}


def parse_openapi_spec(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],  # unused — kept for signature uniformity
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Parse an uploaded OpenAPI JSON/YAML spec and return discovered operations.

    Does not mutate state. Returns a list of operation summaries so the caller
    can decide which endpoints to add via ``add_tool``.

    Args:
        args: Must contain ``spec`` (dict or str). If a string is provided it
            is parsed as JSON first, then YAML.
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Not used; accepted for signature uniformity.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "operations": [...]}`` where each entry has
        ``id``, ``path``, ``method``, ``summary``.
        ``{"ok": False, "error": "..."}`` on parse failure.
    """
    import json as _json  # noqa: PLC0415

    from dev_kit.agent.openapi_parser import parse_openapi_spec as _parse  # noqa: PLC0415

    raw = args.get("spec")
    if raw is None:
        return {"ok": False, "error": "args.spec is required"}

    if isinstance(raw, str):
        try:
            spec = _json.loads(raw)
        except _json.JSONDecodeError:
            try:
                import yaml as _yaml  # noqa: PLC0415
                spec = _yaml.safe_load(raw)
            except Exception as exc:
                return {"ok": False, "error": f"could not parse spec: {exc}"}
    elif isinstance(raw, dict):
        spec = raw
    else:
        return {
            "ok": False,
            "error": f"args.spec must be a dict or string, got {type(raw).__name__!r}",
        }

    if not isinstance(spec, dict):
        return {"ok": False, "error": "spec must be a JSON/YAML object"}

    try:
        tools = _parse(spec)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    operations = [
        {
            "id": t.suggested_id,
            "path": t.path,
            "method": t.method,
            "summary": t.description,
        }
        for t in tools
    ]

    logger.info(
        "parse_openapi_spec.success",
        extra={
            "operation": "tools.parse_openapi_spec",
            "status": "success",
            "operation_count": len(operations),
        },
    )
    return {"ok": True, "operations": operations}


def discover_mcp_tools(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],  # unused — kept for signature uniformity
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """List tools available on an MCP server (placeholder — not yet implemented).

    Real MCP discovery (JSON-RPC ``tools/list`` over HTTP/SSE) is deferred to a
    later phase. This stub returns an empty tool list and a note so callers can
    handle the not-implemented case gracefully.

    Args:
        args: Must contain ``server_url`` (str).
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Not used; accepted for signature uniformity.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "tools": [], "note": "MCP discovery not yet implemented"}``
        ``{"ok": False, "error": "..."}`` if ``server_url`` is missing.
    """
    server_url = args.get("server_url")
    if not server_url:
        return {"ok": False, "error": "args.server_url is required"}

    logger.warning(
        "discover_mcp_tools.not_implemented",
        extra={
            "operation": "tools.discover_mcp_tools",
            "status": "skipped",
            "server_url": server_url,
            "note": "MCP discovery not yet implemented — returning empty tool list",
        },
    )
    return {
        "ok": True,
        "tools": [],
        "note": "MCP discovery not yet implemented",
    }


# ---------------------------------------------------------------------------
# DEPRECATED LEGACY API — conversation.py (old wizard) still imports these.
# TODO(Task-12.1): port conversation.py to phase_driver; then delete this class.
# TODO(Task-12.2): delete TOOL_DEFINITIONS, ToolHandler, collect_recording_settings,
#                  and _parse_sse_json once the old wizard path is gone.
# ---------------------------------------------------------------------------

# Tool JSON schema definitions passed to the Claude API (old wizard)
TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "set_project_meta",
        "description": (
            "Set the project name, description, persona, and domain summary. "
            "Call once you understand the use case from the Domain Overview phase. "
            "NOTE: the project slug is fixed at create-time (derived from the original "
            "project name) and cannot be changed here — it is the on-disk directory key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable project name"},
                "description": {"type": "string", "description": "One-paragraph description of the use case"},
                "user_persona": {"type": "string", "description": "Who the end users are"},
                "domain_summary": {"type": "string", "description": "The domain and problem the AI agent addresses"},
            },
            "required": ["name", "description"],
        },
    },
    {
        "name": "update_config",
        "description": (
            "Update a section of a block's domain config. Values are deep-merged into the current state for that block.\n\n"
            "Valid top-level sections per block (the first segment of the dot-notation path):\n"
            + "\n".join(
                f"  - {block}: {', '.join(get_valid_sections(block))}"
                for block in BLOCKS
            )
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
                "section": {
                    "type": "string",
                    "description": (
                        "Dot-notation path to the config section. "
                        "The first segment MUST be one of the valid top-level sections listed in the tool description. "
                        "Examples: 'agent', 'preprocessing.nlu_processor', 'agent_workflow', 'trust', 'state.session'"
                    ),
                },
                "values": {"type": "object", "description": "Key-value pairs to merge into the section"},
            },
            "required": ["block", "section", "values"],
        },
    },
    {
        "name": "set_agent_type",
        "description": (
            "Sets the agent type classification for this project. Valid values: "
            "transactional, informational, agentic, conversational. Driven by the "
            "3-question decision tree in the tier phase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": AGENT_TYPES},
            },
            "required": ["type"],
        },
    },
    {
        "name": "skip_optional_phase",
        "description": (
            "Record that the user has chosen to skip an optional phase. "
            "Only allowed when SHEET_REQUIREMENTS marks the phase as 'optional' "
            "for the current agent type. Writes phase_decisions[phase] = skipped_by_user "
            "to project meta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "enum": PHASES},
            },
            "required": ["phase"],
        },
    },
    {
        "name": "set_phase",
        "description": "Advance the conversation to the next phase. Call when you have collected enough information for the current phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "enum": PHASES,
                },
            },
            "required": ["phase"],
        },
    },
    {
        "name": "create_subagent",
        "description": "Add a new subagent node to the agent_workflow. Appears as a node in the conversation flow graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case identifier"},
                "name": {"type": "string", "description": "Human-readable name"},
                "description": {"type": "string", "description": "What this subagent does"},
                "system_prompt": {"type": "string", "description": "LLM instructions for this conversation state"},
                "is_start": {"type": "boolean", "default": False},
                "is_terminal": {"type": "boolean", "default": False},
                "valid_intents": {"type": "array", "items": {"type": "string"}, "default": []},
                "tools": {"type": "array", "items": {"type": "string"}, "default": []},
                "opening_phrase": {"type": "string", "description": "Phrase emitted on the first turn only (after consent). Empty string means none.", "default": ""},
            },
            "required": ["id", "name", "description", "system_prompt"],
        },
    },
    {
        "name": "update_subagent",
        "description": "Modify an existing subagent's fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "fields": {"type": "object", "description": "Any subset of the subagent definition to update"},
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "add_routing_rule",
        "description": "Add a routing rule (transition edge) from one subagent to another, triggered by an intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string", "description": "Intent that triggers this transition. Use '*' for catch-all."},
                "next_subagent_id": {"type": "string"},
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {"type": "string", "enum": ["eq", "not_eq", "gt", "lt", "in"]},
                            "value": {},
                        },
                        "required": ["field", "operator", "value"],
                    },
                    "description": "Optional session state conditions",
                    "default": [],
                },
                "session_writes": {
                    "type": "object",
                    "description": "Optional session field writes when this rule fires",
                    "default": {},
                },
            },
            "required": ["from_subagent_id", "intent", "next_subagent_id"],
        },
    },
    {
        "name": "update_routing_rule",
        "description": "Modify an existing routing rule identified by from_subagent_id + intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string"},
                "fields": {"type": "object", "description": "Fields to update on the routing rule"},
            },
            "required": ["from_subagent_id", "intent", "fields"],
        },
    },
    {
        "name": "remove_subagent",
        "description": "Remove a subagent and all its outgoing routing rules from the workflow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "ID of the subagent to remove"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "finalize_config",
        "description": "Mark a config as complete. Use after confirming a block's config is fully specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
            },
            "required": ["block"],
        },
    },
    {
        "name": "rollback_to_checkpoint",
        "description": "Signal that the conversation should roll back to a previous checkpoint. Use only when the user explicitly requests it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Checkpoint phase identifier, e.g. '01_overview'"},
            },
            "required": ["phase"],
        },
    },
    {
        "name": "parse_openapi_spec",
        "description": (
            "Parse a raw OpenAPI 3.0/3.1 spec (JSON or YAML string) and return a list of candidate tool definitions. "
            "Use this when the user uploads or pastes an OpenAPI spec. "
            "The returned candidates help you decide which endpoints to add with add_rest_api_tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec_json": {
                    "type": "string",
                    "description": "The full OpenAPI spec as a JSON or YAML string",
                },
            },
            "required": ["spec_json"],
        },
    },
    {
        "name": "fetch_openapi_spec_from_url",
        "description": (
            "Fetch an OpenAPI 3.0/3.1 spec from a URL and return candidate tool definitions. "
            "Use this when the user pastes a URL to their API spec. "
            "Supports JSON and YAML. Returns the same candidate list as parse_openapi_spec."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the OpenAPI spec file (JSON or YAML), e.g. https://api.example.com/openapi.yaml",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "add_rest_api_tool",
        "description": (
            "Add a REST API tool to the Action Gateway config. "
            "Call this once per tool after confirming details with the user — whether from an OpenAPI spec or collected conversationally. "
            "This also auto-creates the matching connector in agent_core.connectors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case tool ID, e.g. onest_market_lookup"},
                "category": {"type": "string", "enum": ["read", "write", "identity"], "description": "read = no consent; write/identity = Trust Layer consent required"},
                "description": {"type": "string", "description": "What this tool does — shown to the LLM for routing decisions"},
                "base_url": {"type": "string", "description": "API base URL, e.g. https://api.example.com/v2"},
                "auth_type": {"type": "string", "enum": ["none", "api_key", "bearer", "oauth2"]},
                "auth_header": {"type": "string", "description": "Header name for api_key auth, e.g. X-API-KEY"},
                "auth_secret_env": {"type": "string", "description": "Env var name holding the API key"},
                "timeout_ms": {"type": "integer", "default": 5000},
                "endpoints": {
                    "type": "array",
                    "description": "One or more endpoint definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                            "path": {"type": "string"},
                            "params": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "source": {"type": "string", "enum": ["agent", "static"]},
                                        "type": {"type": "string"},
                                        "required": {"type": "boolean"},
                                        "description": {"type": "string"},
                                        "value": {"description": "Fixed value when source is static"},
                                        "default": {"description": "Default value for optional agent params"},
                                    },
                                    "required": ["name", "source", "type"],
                                },
                            },
                        },
                        "required": ["name", "method", "path"],
                    },
                },
            },
            "required": ["id", "category", "description", "base_url", "auth_type", "endpoints"],
        },
    },
    {
        "name": "set_response_transformation",
        "description": (
            "Set the response projection for a REST API tool. "
            "Call this after add_rest_api_tool, once the user tells you which fields from the API response the LLM should see. "
            "If the response wraps a list of items (e.g. search results), set list_key to the dot-path of that list "
            "and each field's source is a dot-path into one item. Without list_key, sources are dot-paths into the response root. "
            "Calling this again for the same tool replaces the previous projection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": "ID of the REST API tool to configure (must already exist via add_rest_api_tool)",
                },
                "list_key": {
                    "type": "string",
                    "description": (
                        "Optional dot-path to a list in the response (e.g. 'data.items'). "
                        "When set, each list element is projected; when empty, the response root is projected."
                    ),
                    "default": "",
                },
                "fields": {
                    "type": "array",
                    "description": "Response fields to extract and expose to the LLM",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "Dot-path into each item (or response root if no list_key), e.g. 'job.title' or 'employer.name'",
                            },
                            "target": {
                                "type": "string",
                                "description": "Field name the LLM sees in the projected result, e.g. 'job_title'",
                            },
                        },
                        "required": ["source", "target"],
                    },
                },
            },
            "required": ["tool_id", "fields"],
        },
    },
    {
        "name": "discover_mcp_tools",
        "description": (
            "Fetch the list of available tools from an MCP server by calling its tools/list endpoint. "
            "Use this when the user provides an MCP server URL. "
            "Returns the raw tools list so you can present options to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mcp_server_url": {
                    "type": "string",
                    "description": "Base URL of the MCP server, e.g. https://mcp.example.com",
                },
            },
            "required": ["mcp_server_url"],
        },
    },
    {
        "name": "add_mcp_tool",
        "description": (
            "Register an MCP server with the Action Gateway. "
            "Call this once per MCP server — the adapter auto-discovers all available tools at startup. "
            "Each discovered tool is registered as '{id}.{tool_name}' "
            "(e.g. 'obsrv_docs.searchDocumentation'). "
            "Use these namespaced names when assigning tools to subagents. "
            "Do NOT call this once per tool — one call per server is correct."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Unique snake_case namespace for this MCP server's tools "
                        "(e.g. 'obsrv_docs'). All tools discovered from the server "
                        "are prefixed with this id."
                    ),
                },
                "category": {"type": "string", "enum": ["read", "write", "identity"]},
                "description": {
                    "type": "string",
                    "description": "What this MCP server provides — used in Action Gateway config.",
                },
                "mcp_server_url": {"type": "string", "description": "Base URL of the MCP server"},
                "transport": {
                    "type": "string",
                    "enum": ["sse", "streamable_http"],
                    "default": "sse",
                    "description": (
                        "MCP transport protocol. Use 'streamable_http' for GitBook, Notion, "
                        "and other hosted servers (POST-only, MCP spec 2025-03-26). "
                        "Use 'sse' for self-hosted servers that support the older SSE transport."
                    ),
                },
                "timeout_ms": {"type": "integer", "default": 5000},
            },
            "required": ["id", "category", "description", "mcp_server_url"],
        },
    },
    {
        "name": "set_reach_channels",
        "description": (
            "Record which deployment channels the user wants (web, cli, voice). "
            "Call this in the overview phase after understanding the use case. "
            "Later phases (language, reach) use the selection to skip irrelevant config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["web", "cli", "voice"]},
                    "description": "One or more of: web, cli, voice",
                    "minItems": 1,
                },
            },
            "required": ["channels"],
        },
    },
    {
        "name": "declare_azure_storage",
        "description": (
            "Record that this domain uses Azure Blob Storage for KB document ingestion. "
            "Call only if the operator confirms they have Azure Blob Storage. "
            "Takes no parameters — all Azure credentials and config (account name, "
            "account key, container name) are entered securely in the Deployment "
            "Inputs step, never in chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def collect_recording_settings(existing: dict) -> dict:
    """Assemble a recording configuration dict for the voice channel.

    Accepts an ``existing`` dict containing whatever the operator has already
    provided (e.g. from a previous wizard turn or from a pre-filled form).
    Missing or blank values are filled in with safe defaults. When ``source``
    is ``"disabled"`` (the default) only the ``source`` key is kept — no
    further recording fields are needed and the block is effectively a no-op.

    The function never prompts interactively; it is designed to be called
    from both the schema-driven Configuration Agent wizard (which collects
    values via the React UI and passes them as ``existing``) and from unit
    tests.

    Caller-ID hash salt auto-generation: if ``caller_id_hash_salt`` is absent
    or blank in ``existing``, a 64-character hex token is generated via
    ``secrets.token_hex(32)``.  The minimum required length is 32 chars.

    Args:
        existing: Dict of recording settings already collected.  Any key may
                  be absent or set to ``None`` / ``""``; all are handled
                  gracefully.

    Returns:
        Fully-populated recording config dict ready to be merged into the
        ``reach_layer.channels.voice.recording`` path via ``update_config``.

    Raises:
        ValueError: If ``existing`` is not a dict.
    """
    import secrets as _secrets

    if not isinstance(existing, dict):
        raise ValueError(f"existing must be a dict, got {type(existing).__name__!r}")

    source = existing.get("source") or "disabled"

    if source == "disabled":
        return {"source": "disabled"}

    salt = existing.get("caller_id_hash_salt") or ""
    if not salt or len(salt) < 32:
        salt = _secrets.token_hex(32)

    store_in = existing.get("store") or {}
    if not isinstance(store_in, dict):
        store_in = {}

    backend = store_in.get("backend") or "local"

    local_cfg = store_in.get("local") or {}
    if not isinstance(local_cfg, dict):
        local_cfg = {}
    local_section = {
        "base_path": local_cfg.get("base_path") or "/var/recordings",
    }

    s3_in = store_in.get("s3") or {}
    if not isinstance(s3_in, dict):
        s3_in = {}
    s3_section = {
        "bucket": s3_in.get("bucket") or "",
        "prefix": s3_in.get("prefix") or "recordings/",
        "region": s3_in.get("region") or "ap-south-1",
        "kms_key_id": s3_in.get("kms_key_id") or "",
    }

    store_out: dict = {
        "backend": backend,
        "local": local_section,
        "s3": s3_section,
    }

    return {
        "source": source,
        "consent_purpose": existing.get("consent_purpose") or "recording",
        "webhook_timeout_s": existing.get("webhook_timeout_s") or 30,
        "fetch_timeout_s": existing.get("fetch_timeout_s") or 60,
        "min_duration_ms": existing.get("min_duration_ms") or 500,
        "caller_id_hash_salt": salt,
        "store": store_out,
    }


def _parse_sse_json(text: str) -> dict | None:
    """Extract the first JSON-RPC payload from an SSE response body.

    SSE lines have the form ``data: <json>``.  This function scans the
    response text for the first such line and returns the parsed dict, or
    ``None`` if no ``data:`` line is found or the payload is not valid JSON.

    Args:
        text: Raw response body string.

    Returns:
        Parsed dict from the first ``data:`` line, or None.
    """
    import json

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[len("data:"):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return None


class ToolHandler:
    """Dispatches Claude tool calls to their handler methods.

    DEPRECATED: use ``phase_driver.TOOL_HANDLERS`` for new code.
    Kept until Task 12.1 ports ``conversation.py`` to the phase_driver path.

    Handlers modify the ConfigAccumulator and/or the shared mutable state dict.

    Args:
        accumulator: The project's config accumulator.
        state: Mutable dict with keys ``phase`` (str) and
               ``phase_changed`` (str | None). Handlers set
               ``state['phase_changed']`` to the new phase name when
               ``set_phase`` is called, so ConversationEngine can trigger
               a checkpoint.
    """

    def __init__(
        self,
        accumulator: ConfigAccumulator,
        state: dict,
        project_path: "Path | None" = None,
    ) -> None:
        self._acc = accumulator
        self._state = state
        self._project_path = project_path

    def _read_project_meta(self) -> dict:
        """Read the persisted project meta dict from disk.

        Falls back to ``state['project_meta']`` when no ``project_path`` is
        configured (unit tests that do not provide disk state).

        Returns:
            Parsed project meta dict, or an empty dict if nothing is available.
        """
        import json

        if self._project_path is not None:
            meta_file = self._project_path / "_meta" / "project.json"
            if meta_file.exists():
                try:
                    return json.loads(meta_file.read_text())
                except json.JSONDecodeError:
                    return {}
        return dict(self._state.get("project_meta") or {})

    def _update_project_meta(self, updates: dict) -> None:
        """Merge ``updates`` into the project meta on disk and in state.

        Args:
            updates: Partial meta dict to merge into the stored metadata.
        """
        import json

        meta = self._read_project_meta()
        meta.update(updates)
        state_meta = self._state.setdefault("project_meta", {})
        state_meta.update(updates)
        if self._project_path is not None:
            meta_dir = self._project_path / "_meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "project.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2)
            )

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call to the appropriate handler.

        Args:
            tool_name: Tool name matching one of TOOL_DEFINITIONS.
            tool_input: Tool input values from the LLM.

        Returns:
            Result string to send back as tool_result content.

        Raises:
            ValueError: If tool_name is not recognised.
        """
        handlers = {
            "set_project_meta": self._handle_set_project_meta,
            "set_agent_type": self._handle_set_agent_type,
            "skip_optional_phase": self._handle_skip_optional_phase,
            "update_config": self._handle_update_config,
            "set_phase": self._handle_set_phase,
            "create_subagent": self._handle_create_subagent,
            "update_subagent": self._handle_update_subagent,
            "add_routing_rule": self._handle_add_routing_rule,
            "update_routing_rule": self._handle_update_routing_rule,
            "remove_subagent": self._handle_remove_subagent,
            "finalize_config": self._handle_finalize_config,
            "rollback_to_checkpoint": self._handle_rollback_to_checkpoint,
            "parse_openapi_spec": self._handle_parse_openapi_spec,
            "fetch_openapi_spec_from_url": self._handle_fetch_openapi_spec_from_url,
            "add_rest_api_tool": self._handle_add_rest_api_tool,
            "set_response_transformation": self._handle_set_response_transformation,
            "discover_mcp_tools": self._handle_discover_mcp_tools,
            "add_mcp_tool": self._handle_add_mcp_tool,
            "set_reach_channels": self._handle_set_reach_channels,
            "declare_azure_storage": self._handle_declare_azure_storage,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name!r}")
        slug = self._state.get("project_meta", {}).get("slug", "")
        logger.info(
            "devkit.tool.dispatch",
            extra={"operation": f"tool.{tool_name}", "status": "start", "slug": slug},
        )
        try:
            result = handler(tool_input)
            logger.info(
                "devkit.tool.dispatch",
                extra={"operation": f"tool.{tool_name}", "status": "success", "slug": slug},
            )
            return result
        except Exception as e:
            logger.error(
                "devkit.tool.dispatch_failed",
                extra={"operation": f"tool.{tool_name}", "status": "failure", "slug": slug, "error": str(e)},
                exc_info=True,
            )
            raise

    def _handle_set_project_meta(self, inputs: dict) -> str:
        protected = {"slug", "current_phase", "phases_completed", "agent_type", "phase_decisions"}
        clean = {k: v for k, v in inputs.items() if k not in protected}
        self._update_project_meta(clean)
        slug = self._read_project_meta().get("slug", "")
        return f"Project meta updated: {clean.get('name', '')} ({slug})"

    def _handle_set_agent_type(self, inputs: dict) -> str:
        """Record the project's agent type in ``_meta/project.json``.

        Args:
            inputs: Dict with ``type`` key — one of the AGENT_TYPES values.

        Returns:
            Confirmation string, or an ERROR string for an invalid type.
        """
        agent_type = inputs.get("type", "")
        if agent_type not in AGENT_TYPES:
            return f"ERROR — invalid agent type: {agent_type!r}. Must be one of: {AGENT_TYPES}"
        self._update_project_meta({"agent_type": agent_type})
        return f"ok: agent_type set to {agent_type}"

    def _handle_skip_optional_phase(self, inputs: dict) -> str:
        """Record a user-initiated skip of an optional phase.

        Args:
            inputs: Dict with ``phase`` key naming the phase to skip.

        Returns:
            Confirmation string, or an ERROR if the phase is not optional.
        """
        from datetime import datetime, timezone

        phase = inputs.get("phase", "")
        meta = self._read_project_meta()
        agent_type = meta.get("agent_type", "")
        status = SHEET_REQUIREMENTS.get(phase, {}).get(agent_type, "required")
        if status != "optional":
            return (
                f"ERROR — phase {phase!r} is {status!r} for {agent_type!r} agents; "
                "cannot skip."
            )
        phase_decisions = dict(meta.get("phase_decisions", {}))
        phase_decisions[phase] = {
            "status": "skipped_by_user",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._update_project_meta({"phase_decisions": phase_decisions})
        return f"ok: {phase} skipped by user"

    def _handle_update_config(self, inputs: dict) -> str:
        block = inputs["block"]
        section = inputs["section"]
        values = inputs["values"]

        if not isinstance(values, dict):
            return (
                f"ERROR — `values` must be an object (dict), got {type(values).__name__!r}. "
                f"Pass a JSON object with key-value pairs, e.g. {{\"key\": \"value\"}}. "
                f"Do not pass a string, list, or other scalar."
            )

        if block == "agent_core":
            if section == "agent.channels" or section.startswith("agent.channels."):
                return (
                    "ERROR — agent.channels is removed (GH-137). "
                    "Use section=`channels` at the top level instead "
                    "(e.g. section=`channels`, values={voice: {...}})."
                )
            if section == "reach_layer.channels" or section.startswith("reach_layer.channels."):
                return (
                    "ERROR — reach_layer.channels inside agent_core is removed (GH-137). "
                    "Use section=`channels.<name>.turn_assembler` at the top level for "
                    "turn_assembler policy overrides."
                )

        result = self._acc.update(block, section, values)
        if result == "OK":
            return f"ok: updated {block}.{section}"
        logger.warning(
            "devkit.tool.update_config.rejected",
            extra={
                "operation": "tool.update_config",
                "status": "rejected",
                "block": block,
                "section": section,
                "verdict": result.split(":", 1)[0] if ":" in result else result.split("\n", 1)[0],
                "response_to_llm": result[:500],
            },
        )
        return result

    def _handle_set_phase(self, inputs: dict) -> str:
        """Advance the conversation to ``inputs['phase']``.

        Args:
            inputs: Dict with a ``phase`` key naming a member of PHASES.

        Returns:
            Human-readable advance/skip message, or an ERROR string when
            the requested phase is unknown or sequencing is invalid.
        """
        from datetime import datetime, timezone

        requested = inputs["phase"]
        current = self._state.get("phase", PHASES[0])

        if requested not in PHASES:
            return f"ERROR — unknown phase: {requested!r}"

        current_idx = PHASES.index(current) if current in PHASES else 0
        requested_idx = PHASES.index(requested)

        if requested_idx < current_idx:
            return (
                f"ERROR — cannot go back from '{current}' to '{requested}'. "
                "Use rollback_to_checkpoint if you need to revisit an earlier phase."
            )
        if requested_idx > current_idx + 1:
            next_phase = PHASES[current_idx + 1]
            return (
                f"ERROR — cannot skip from '{current}' to '{requested}'. "
                f"You must complete '{next_phase}' next. "
                f"Call set_phase('{next_phase}') when you are ready."
            )

        meta = self._read_project_meta()
        agent_type = meta.get("agent_type", "")
        phase_decisions = dict(meta.get("phase_decisions", {}))
        status = (
            SHEET_REQUIREMENTS.get(requested, {}).get(agent_type, "optional")
            if agent_type else "required"
        )

        blocks_state = {b: self._acc.get_block(b) for b in BLOCKS}
        selected_channels = self._acc.get_reach_channel_selection()
        cross_errors = validate_cross_block(blocks_state, selected_channels, current_phase=current)
        if cross_errors:
            error_lines = "\n".join(f"  - {e}" for e in cross_errors)
            logger.warning(
                "devkit.tool.set_phase.cross_block_blocked",
                extra={
                    "operation": "tool.set_phase",
                    "status": "phase_advance_blocked",
                    "current_phase": current,
                    "requested_phase": requested,
                    "violation_count": len(cross_errors),
                },
            )
            stale_blocks = sorted(
                {b for b in BLOCKS if self._acc.get_status(b) == ConfigStatus.STALE}
            )
            stale_hint = ""
            if stale_blocks:
                stale_hint = (
                    f"\n\n⚠️ The following block(s) are STALE — they already exhausted "
                    f"the per-section retry budget this turn: {stale_blocks}. "
                    f"update_config will return VALIDATION_SECTION_STALE on those "
                    f"sections, so further tool calls cannot resolve this. STOP "
                    f"calling tools and reply to the user as text: explain the "
                    f"violations above and ask them how to proceed (correct a value, "
                    f"skip the section, or rollback to a checkpoint)."
                )
            return (
                f"PHASE_ADVANCE_BLOCKED — cross-block consistency check failed "
                f"for the leaving phase '{current}'. Fix these before advancing "
                f"to '{requested}':\n{error_lines}\n\n"
                f"Each violation spans two or more blocks (e.g. an intent "
                f"declared in one block but missing from another). Make the "
                f"corresponding update_config calls to bring the blocks back "
                f"in sync, then call set_phase('{requested}') again."
                f"{stale_hint}"
            )

        if status == "skip":
            phase_decisions[requested] = {
                "status": "not_applicable_for_type",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._update_project_meta({"phase_decisions": phase_decisions})
            if requested == "tools":
                self._acc.set_status("action_gateway", ConfigStatus.COMPLETE)
            self._state["phase_changed"] = requested
            next_idx = requested_idx + 1
            if next_idx < len(PHASES):
                return (
                    f"Phase '{requested}' skipped ({agent_type} agents). "
                    f"Advancing directly past it. Call set_phase('{PHASES[next_idx]}') next."
                )
            return f"Phase '{requested}' skipped ({agent_type} agents)."

        self._state["phase_changed"] = requested
        if current in PHASES and agent_type:
            if SHEET_REQUIREMENTS.get(current, {}).get(agent_type) == "optional":
                existing = phase_decisions.get(current, {})
                if existing.get("status") != "skipped_by_user":
                    phase_decisions[current] = {
                        "status": "answered",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._update_project_meta({"phase_decisions": phase_decisions})
        return f"Phase advancing to: {requested}"

    def _handle_create_subagent(self, inputs: dict) -> str:
        existing = [
            sa for sa in self._acc.get_block("agent_core")
            .get("agent_workflow", {})
            .get("subagents", [])
            if sa.get("id") == inputs["id"]
        ]
        if existing:
            return f"Subagent '{inputs['id']}' already exists — use update_subagent to modify it."
        sa = {
            "id": inputs["id"],
            "name": inputs["name"],
            "description": inputs["description"],
            "is_start": inputs.get("is_start", False),
            "is_terminal": inputs.get("is_terminal", False),
            "special_handler": None,
            "valid_intents": inputs.get("valid_intents", []),
            "tools": inputs.get("tools", []),
            "system_prompt": inputs["system_prompt"],
            "opening_phrase": inputs.get("opening_phrase", ""),
            "routing": [],
        }
        self._acc.set_subagent(sa)
        if sa["is_terminal"]:
            return f"Subagent '{inputs['id']}' created (terminal — no routing required)."
        return (
            f"Subagent '{inputs['id']}' created. "
            f"REMINDER: non-terminal subagents must have at least one routing rule. "
            f"Call `add_routing_rule` for every intent in valid_intents that should "
            f"transition to another subagent, plus a catch-all "
            f"`add_routing_rule(from_subagent_id='{inputs['id']}', intent='*', "
            f"next_subagent_id='<target>')` so unmatched intents have a destination. "
            f"If you do nothing, the renderer will insert a self-loop catch-all to "
            f"prevent a startup crash, but this is a safety net — design the routing "
            f"explicitly."
        )

    def _handle_update_subagent(self, inputs: dict) -> str:
        try:
            self._acc.update_subagent(inputs["id"], inputs["fields"])
            return f"Subagent '{inputs['id']}' updated."
        except ValueError as exc:
            return str(exc)

    def _handle_add_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.add_routing_rule(
                inputs["from_subagent_id"],
                inputs["intent"],
                inputs["next_subagent_id"],
                inputs.get("conditions", []),
                inputs.get("session_writes", {}),
            )
            return (
                f"Routing rule added: {inputs['from_subagent_id']}"
                f" --[{inputs['intent']}]--> {inputs['next_subagent_id']}"
            )
        except ValueError as exc:
            return str(exc)

    def _handle_update_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.update_routing_rule(inputs["from_subagent_id"], inputs["intent"], inputs["fields"])
            return f"Routing rule updated: {inputs['from_subagent_id']} --[{inputs['intent']}]-->"
        except ValueError as exc:
            return str(exc)

    def _handle_remove_subagent(self, inputs: dict) -> str:
        removed = self._acc.remove_subagent(inputs["id"])
        if not removed:
            return f"error: subagent '{inputs['id']}' not found — nothing removed."
        return f"Subagent '{inputs['id']}' removed."

    def _handle_finalize_config(self, inputs: dict) -> str:
        self._acc.set_status(inputs["block"], ConfigStatus.COMPLETE)
        return f"Config '{inputs['block']}' marked complete."

    def _handle_rollback_to_checkpoint(self, inputs: dict) -> str:
        self._state["rollback_to"] = inputs["phase"]
        return f"Rollback to checkpoint '{inputs['phase']}' requested."

    def _handle_parse_openapi_spec(self, inputs: dict) -> str:
        """Parse an OpenAPI spec string and return candidate tool definitions as JSON.

        Args:
            inputs: Dict with 'spec_json' key containing a JSON or YAML string.

        Returns:
            JSON array of candidate tool dicts, or an ERROR string on failure.
        """
        import json
        import yaml as _yaml
        from dev_kit.agent.openapi_parser import parse_openapi_spec as _parse_spec

        spec_json = inputs.get("spec_json", "")
        try:
            try:
                spec = json.loads(spec_json)
            except json.JSONDecodeError:
                spec = _yaml.safe_load(spec_json)
            if not isinstance(spec, dict):
                return "ERROR: spec must be a JSON or YAML object"
        except Exception as exc:
            return f"ERROR: could not parse spec — {exc}"

        try:
            tools = _parse_spec(spec)
        except ValueError as exc:
            return f"ERROR: {exc}"

        candidates = [
            {
                "suggested_id": t.suggested_id,
                "path": t.path,
                "method": t.method,
                "description": t.description,
                "base_url": t.base_url,
                "param_names": [p.name for p in t.params],
                "auth_type": t.auth_type,
                "auth_header": t.auth_header,
            }
            for t in tools
        ]
        logger.info(
            "devkit.tool.openapi_parsed",
            extra={
                "operation": "tool.parse_openapi_spec",
                "status": "success",
                "endpoint_count": len(candidates),
            },
        )
        return json.dumps(candidates, ensure_ascii=False, indent=2)

    def _handle_fetch_openapi_spec_from_url(self, inputs: dict) -> str:
        """Fetch an OpenAPI spec from a URL and return candidate tool definitions as JSON.

        Args:
            inputs: Dict with 'url' key containing the spec URL.

        Returns:
            JSON array of candidate tool dicts, or an ERROR string on failure.
        """
        import json
        import yaml as _yaml
        import httpx
        import time

        from dev_kit.agent.openapi_parser import parse_openapi_spec as _parse_spec

        url = inputs.get("url", "").strip()
        if not url:
            logger.warning(
                "fetch_openapi_spec_from_url.failure",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": "url is required",
                    "latency_ms": 0,
                },
            )
            return "ERROR: url is required"

        start = time.time()
        try:
            transport = httpx.HTTPTransport(retries=1)
            with httpx.Client(transport=transport, timeout=15.0, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "fetch_openapi_spec_from_url.failure",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": f"HTTP {exc.response.status_code}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
                exc_info=True,
            )
            return f"ERROR: HTTP {exc.response.status_code} fetching {url}"
        except httpx.HTTPError as exc:
            logger.warning(
                "fetch_openapi_spec_from_url.failure",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": str(exc),
                    "latency_ms": int((time.time() - start) * 1000),
                },
                exc_info=True,
            )
            return f"ERROR: could not fetch spec from {url} — {exc}"

        content = response.text
        try:
            try:
                spec = json.loads(content)
            except json.JSONDecodeError:
                spec = _yaml.safe_load(content)
            if not isinstance(spec, dict):
                logger.warning(
                    "fetch_openapi_spec_from_url.failure",
                    extra={
                        "operation": "tools.fetch_openapi_spec_from_url",
                        "status": "failure",
                        "url": url,
                        "error": "fetched content is not a JSON/YAML object",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return "ERROR: fetched content is not a JSON/YAML object"
        except Exception as exc:
            logger.warning(
                "fetch_openapi_spec_from_url.failure",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": f"could not parse fetched content — {exc}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
                exc_info=True,
            )
            return f"ERROR: could not parse fetched content — {exc}"

        try:
            tools = _parse_spec(spec)
        except ValueError as exc:
            logger.warning(
                "fetch_openapi_spec_from_url.failure",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": str(exc),
                    "latency_ms": int((time.time() - start) * 1000),
                },
                exc_info=True,
            )
            return f"ERROR: {exc}"

        candidates = [
            {
                "suggested_id": t.suggested_id,
                "path": t.path,
                "method": t.method,
                "description": t.description,
                "base_url": t.base_url,
                "param_names": [p.name for p in t.params],
                "auth_type": t.auth_type,
                "auth_header": t.auth_header,
            }
            for t in tools
        ]
        logger.info(
            "fetch_openapi_spec_from_url",
            extra={
                "operation": "tools.fetch_openapi_spec_from_url",
                "status": "success",
                "url": url,
                "endpoint_count": len(candidates),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return json.dumps(candidates, ensure_ascii=False, indent=2)

    def _handle_add_rest_api_tool(self, inputs: dict) -> str:
        """Add a REST API tool to action_gateway and auto-sync agent_core connector.

        Args:
            inputs: Dict containing id, category, description, base_url, auth_type,
                    and endpoints. Optional: auth_header, auth_secret_env, timeout_ms.

        Returns:
            Confirmation string, or an ERROR string if the tool id is duplicate.
        """
        auth: dict = {"type": inputs["auth_type"]}
        if inputs.get("auth_header"):
            auth["header"] = inputs["auth_header"]
        if inputs.get("auth_secret_env"):
            auth["secret_env"] = inputs["auth_secret_env"]

        tool = {
            "id": inputs["id"],
            "type": "rest_api",
            "category": inputs["category"],
            "description": inputs["description"],
            "base_url": inputs["base_url"],
            "auth": auth,
            "timeout_ms": inputs.get("timeout_ms", 5000),
            "endpoints": inputs.get("endpoints", []),
            "response": {"max_size_chars": 4000},
        }
        try:
            self._acc.add_action_gateway_tool(tool)
        except ValueError as exc:
            return f"ERROR: {exc}"

        self._sync_connector_from_tool(tool)
        return f"Tool '{inputs['id']}' added to Action Gateway config."

    def _handle_set_response_transformation(self, inputs: dict) -> str:
        """Write response projection for a REST API tool into the accumulator.

        Args:
            inputs: Dict with 'tool_id', 'fields', and optional 'list_key'.

        Returns:
            Confirmation string, or an ERROR string if the tool does not exist.
        """
        import time

        tool_id = inputs.get("tool_id", "")
        fields = inputs.get("fields", [])
        list_key = inputs.get("list_key", "") or ""

        start = time.time()
        try:
            self._acc.update_tool_response_mapping(tool_id, fields, list_key=list_key)
        except ValueError as exc:
            logger.warning(
                "set_response_transformation.failure",
                extra={
                    "operation": "tools.set_response_transformation",
                    "status": "failure",
                    "tool_id": tool_id,
                    "error": str(exc),
                    "latency_ms": int((time.time() - start) * 1000),
                },
                exc_info=True,
            )
            return f"ERROR: {exc}"

        logger.info(
            "set_response_transformation",
            extra={
                "operation": "tools.set_response_transformation",
                "status": "success",
                "tool_id": tool_id,
                "field_count": len(fields),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        field_names = ", ".join(f.get("target", "?") for f in fields[:5])
        if len(fields) > 5:
            field_names += "…"
        return (
            f"Response mapping set for tool '{tool_id}': "
            f"{len(fields)} field(s)"
            + (f" — {field_names}" if field_names else "")
        )

    def _handle_discover_mcp_tools(self, inputs: dict) -> str:
        """Fetch tools/list from an MCP server and return the tool list as JSON.

        Args:
            inputs: Dict with 'mcp_server_url' key.

        Returns:
            JSON array of tool summaries, or an ERROR string on connection failure.
        """
        import json
        import httpx

        url = inputs["mcp_server_url"].rstrip("/")
        payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"ERROR: could not reach MCP server at {url} — {exc}"
        except Exception as exc:
            return f"ERROR: unexpected error contacting MCP server — {exc}"

        try:
            data = response.json()
        except Exception:
            data = _parse_sse_json(response.text)
            if data is None:
                return (
                    f"ERROR: MCP server at {url} returned an unrecognised response format. "
                    f"Expected JSON-RPC or SSE. Response preview: {response.text[:200]!r}"
                )

        tools = data.get("result", {}).get("tools", [])
        if not tools:
            return f"No tools found at {url}. Verify the URL and that the server supports JSON-RPC tools/list."

        summary = [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {}),
            }
            for t in tools
        ]
        logger.info(
            "devkit.tool.mcp_discovered",
            extra={
                "operation": "tool.discover_mcp_tools",
                "status": "success",
                "server_url": url,
                "tool_count": len(tools),
            },
        )
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def _handle_add_mcp_tool(self, inputs: dict) -> str:
        """Register an MCP server with the Action Gateway.

        Args:
            inputs: Dict containing id, category, description, mcp_server_url.
                    Optional: transport (default 'sse'), timeout_ms (default 5000).

        Returns:
            Confirmation string with namespace hint, or an ERROR string if the
            tool id is duplicate.
        """
        tool = {
            "id": inputs["id"],
            "type": "mcp",
            "category": inputs["category"],
            "description": inputs["description"],
            "server_url": inputs["mcp_server_url"],
            "transport": inputs.get("transport", "sse"),
            "timeout_ms": inputs.get("timeout_ms", 5000),
        }
        try:
            self._acc.add_action_gateway_tool(tool)
        except ValueError as exc:
            return f"ERROR: {exc}"
        logger.info(
            "devkit.tool.mcp_registered",
            extra={
                "operation": "tool.add_mcp_tool",
                "status": "success",
                "server_name": inputs["id"],
                "transport": tool["transport"],
            },
        )
        return (
            f"MCP server '{inputs['id']}' registered with Action Gateway (transport: {tool['transport']}). "
            f"Tools discovered at startup will be available as '{inputs['id']}__<tool_name>'. "
            f"Assign tools to subagents using these namespaced names."
        )

    def _handle_set_reach_channels(self, inputs: dict) -> str:
        """Store the user's selected deployment channels in reach_layer config.

        Args:
            inputs: Dict with 'channels' key containing a list of channel names.

        Returns:
            Confirmation string, or an ERROR string for unknown/empty channel list.
        """
        channels = inputs.get("channels", [])
        valid = {"web", "cli", "voice"}
        invalid = [c for c in channels if c not in valid]
        if invalid:
            return f"ERROR: unknown channel(s): {invalid}. Valid channels: {sorted(valid)}"
        if not channels:
            return "ERROR: at least one channel must be selected."
        self._acc.set_reach_channel_selection(channels)
        return f"Channels selected: {', '.join(channels)}. Now configure each selected channel."

    def _handle_declare_azure_storage(self, tool_input: dict) -> str:
        """Record that Azure Blob Storage is needed for this domain.

        Args:
            tool_input: Ignored — this tool accepts no parameters.

        Returns:
            Confirmation string prompting the user to have Azure details ready.
        """
        import time

        start = time.time()
        self._acc.declare_azure_needed()
        logger.info(
            "declare_azure_storage",
            extra={
                "operation": "tools.declare_azure_storage",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return (
            "Azure Blob Storage noted. In the Deployment Inputs step you will be "
            "asked for your Azure account name, account key, and container name — "
            "keep all three ready."
        )

    def _sync_connector_from_tool(self, tool: dict) -> None:
        """Auto-create or update agent_core connector from a tool definition.

        Args:
            tool: Tool dict from action_gateway.tools with at minimum:
                  id, category, description, type. Plus endpoints (rest_api) or
                  input_schema (mcp).
        """
        category = tool.get("category", "read")
        tool_id = tool["id"]

        if tool.get("type") == "mcp":
            return
        else:
            properties: dict = {}
            required_list: list = []
            for endpoint in tool.get("endpoints", []):
                for param in endpoint.get("params", []):
                    if param.get("source") != "agent":
                        continue
                    prop: dict = {"type": param.get("type", "string")}
                    if param.get("description"):
                        prop["description"] = param["description"]
                    if param.get("default") is not None:
                        prop["default"] = param["default"]
                    properties[param["name"]] = prop
                    if param.get("required"):
                        required_list.append(param["name"])
            input_schema = {"type": "object", "properties": properties}
            if required_list:
                input_schema["required"] = required_list

        connector = {
            "name": tool_id,
            "description": tool.get("description", ""),
            "input_schema": input_schema,
        }

        self._acc.set_agent_core_connector(category, connector)
