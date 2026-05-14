"""
dev-kit/dev_kit/agent/tools.py

Canonical 8-tool set for the deterministic wizard (design §6: "Slimmed tool
surface").

The 8 top-level functions (``update_intake``, ``update_config``,
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

Belongs to the dev-kit deterministic wizard.
See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §6.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from dev_kit.agent.intake_state import IntakeState
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
    "DEVKIT_TOOL_SCHEMAS",
]


# Anthropic tool-use JSON schemas for the canonical 8-tool set. Exposed so the
# new-wizard path in conversation.py can hand them to the Claude API.
#
# These are intentionally MINIMAL ("type": "object" with no strict properties)
# so phase_driver.run_turn dispatches by name regardless of arg shape. The 8
# Python handlers in this module perform their own arg validation and return
# structured errors on malformed input. Schema strictness can be tightened in a
# follow-up once the new wizard end-to-end flow is stable.
DEVKIT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "update_intake",
        "description": (
            "Set or update a single IntakeState field. Args: field (str — e.g. 'has_kb', "
            "'is_multi_turn'), value (any). Cascades through FIELD_RULES to invalidate "
            "dependent answers."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "update_config",
        "description": (
            "Write a user chat answer to the accumulator with mirror validation. "
            "Preferred form: {path: 'block.section.field', value: ...}. "
            "Legacy form: {block, section, values}."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "add_subagent",
        "description": (
            "Append a subagent definition to agent_core.agent_workflow.subagents. "
            "Args: definition (dict with at least an 'id' key)."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "update_subagent",
        "description": (
            "Modify fields on an existing subagent. Args: id (str), fields (dict)."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "add_routing_rule",
        "description": (
            "Append a routing rule (transition edge) to a subagent. "
            "Args: from_subagent_id, intent, to_subagent_id, optional condition."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "add_tool",
        "description": (
            "Add a tool to action_gateway.tools and the matching agent_core connector. "
            "Args: spec (dict with id, type, category, endpoints)."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "parse_openapi_spec",
        "description": (
            "Parse an OpenAPI 3.0/3.1 spec (JSON or YAML string, or dict) and return "
            "candidate tool operations. Does not mutate state. Args: spec."
        ),
        "input_schema": {"type": "object"},
    },
    {
        "name": "discover_mcp_tools",
        "description": (
            "List tools available on an MCP server. Args: server_url (str). "
            "Currently a placeholder — returns an empty list."
        ),
        "input_schema": {"type": "object"},
    },
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


