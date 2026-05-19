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
# § 6 — Canonical tool set (phase_driver.TOOL_HANDLERS uses these)
#
# Tool count: 9. Started at 8 (commit b0c2d33's trim); the tools phase later
# needed three independent entry points for external-tool registration —
# manual spec paste, OpenAPI URL fetch, and MCP discovery — so
# ``fetch_openapi_spec_from_url`` was restored as a sibling of
# ``parse_openapi_spec``. Without the URL fetcher, the LLM has to ask the
# user to paste a multi-thousand-line spec into chat, which is a non-starter
# for any production API.
# ---------------------------------------------------------------------------

__all__ = [
    "update_intake",
    "update_config",
    "add_subagent",
    "update_subagent",
    "add_routing_rule",
    "add_tool",
    "parse_openapi_spec",
    "fetch_openapi_spec_from_url",
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
            "Set or update a single IntakeState field. Use this for every yes/no "
            "answer the user gives during the tier intake phase. Cascades through "
            "FIELD_RULES to invalidate dependent answers when intake changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": (
                        "IntakeState field name. One of: has_kb, has_external_tools, "
                        "is_multi_turn, needs_persistent_user_data, is_companion_style, "
                        "needs_consent, has_hitl (booleans), or selected_channels, "
                        "supported_languages (list[str]), or default_language, "
                        "domain_description, project_name (str)."
                    ),
                },
                "value": {
                    "description": (
                        "New value for the field. Use a boolean for the 7 binary flags; "
                        "a list of strings for selected_channels/supported_languages; "
                        "a string for the rest."
                    ),
                },
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "update_config",
        "description": (
            "Write a user chat answer to the accumulator with mirror validation. "
            "Preferred form: {path: 'block.section.field', value: ...}. "
            "Legacy form: {block, section, values: {...}}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Dotted path 'block.section.field' (preferred form).",
                },
                "value": {
                    "description": "Value to write at path (preferred form).",
                },
                "block": {
                    "type": "string",
                    "description": (
                        "Block name (legacy form). One of: agent_core, trust_layer, "
                        "knowledge_engine, memory_layer, action_gateway, reach_layer, "
                        "observability_layer."
                    ),
                },
                "section": {
                    "type": "string",
                    "description": "Section name within the block (legacy form).",
                },
                "values": {
                    "type": "object",
                    "description": "Dict of field-value pairs to apply (legacy form).",
                },
            },
        },
    },
    {
        "name": "add_subagent",
        "description": (
            "Append a subagent definition to agent_core.agent_workflow.subagents. "
            "Used during the workflow phase to build the subagent graph."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "definition": {
                    "type": "object",
                    "description": (
                        "Subagent definition. Must include 'id' (str) and typically "
                        "'name', 'description', 'is_start', 'is_terminal', "
                        "'opening_phrase', 'system_prompt', 'valid_intents', 'routing'."
                    ),
                },
            },
            "required": ["definition"],
        },
    },
    {
        "name": "update_subagent",
        "description": (
            "Modify fields on an existing subagent in agent_core.agent_workflow.subagents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The subagent's id (must already exist).",
                },
                "fields": {
                    "type": "object",
                    "description": "Field-value pairs to overwrite on the subagent.",
                },
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "add_routing_rule",
        "description": (
            "Append a routing rule (transition edge) to a subagent's 'routing' list. "
            "Used to wire intent → next-subagent transitions during the workflow phase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {
                    "type": "string",
                    "description": "ID of the subagent whose routing list gets a new rule.",
                },
                "intent": {
                    "type": "string",
                    "description": "Intent name (must subset nlu_processor.intents) or '*' wildcard.",
                },
                "to_subagent_id": {
                    "type": "string",
                    "description": "ID of the target subagent (must already be declared).",
                },
                "condition": {
                    "type": ["object", "null"],
                    "description": "Optional RoutingCondition {field, operator, value}.",
                },
            },
            "required": ["from_subagent_id", "intent", "to_subagent_id"],
        },
    },
    {
        "name": "add_tool",
        "description": (
            "Add an external tool to action_gateway.tools and the matching connector "
            "entry in agent_core. Used during the tools phase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": (
                        "Tool spec. Must include id (str), type ('rest_api' or 'mcp'), "
                        "category ('read', 'write', or 'identity'), and the type-specific "
                        "fields (endpoints for rest_api, mcp_server_url for mcp)."
                    ),
                },
            },
            "required": ["spec"],
        },
    },
    {
        "name": "parse_openapi_spec",
        "description": (
            "Parse an OpenAPI 3.0/3.1 spec the user pasted into chat (JSON or YAML "
            "string, or a parsed dict) and return candidate tool operations. Does "
            "NOT mutate state — call add_tool afterwards to register the chosen "
            "operations. Use this when the user pastes the spec inline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {
                    "description": (
                        "The OpenAPI spec as a JSON/YAML string or a parsed dict."
                    ),
                },
            },
            "required": ["spec"],
        },
    },
    {
        "name": "fetch_openapi_spec_from_url",
        "description": (
            "Download an OpenAPI 3.0/3.1 spec from a URL and return candidate tool "
            "operations. Accepts JSON or YAML responses, follows redirects, and "
            "returns the same shape as parse_openapi_spec. Does NOT mutate state — "
            "call add_tool afterwards to register the chosen operations. Use this "
            "when the user gives a URL instead of pasting the spec."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Direct URL to the OpenAPI spec (JSON or YAML).",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "discover_mcp_tools",
        "description": (
            "List tools exposed by an MCP server via JSON-RPC tools/list. "
            "Auto-detects plain JSON or SSE responses. Returns each tool's name, "
            "description, and input_schema so you can decide whether to register "
            "the server via add_tool(spec={type:'mcp',...})."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "server_url": {
                    "type": "string",
                    "description": "URL of the MCP server (the JSON-RPC endpoint).",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Optional timeout in milliseconds (default 10000).",
                },
            },
            "required": ["server_url"],
        },
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
    field_status: dict[str, str],
) -> dict[str, Any]:
    """Append a new subagent definition to ``agent_core.agent_workflow.subagents``.

    The definition is validated via ``validate_partial`` against the mirror
    schema. On failure the appended item is removed (reverted) and an error
    is returned.

    Args:
        args: Must contain ``definition`` (dict) with at least an ``id`` key.
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Per-block YAML dicts — mutated in-place on success.
        field_status: Mutated on success — ``agent_core.agent_workflow.subagents``
            is marked ``"answered"`` so the workflow phase can advance once at
            least one subagent has been registered.

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

    # Candidate-copy commit. Building the would-be merged agent_core on a
    # deep copy means the live accumulator is never mutated until the
    # validate_partial gate passes — no bad subagent can leak into the
    # per-turn YAML render.
    candidate_agent_core = copy.deepcopy(accumulator.get("agent_core", {}))
    workflow = candidate_agent_core.setdefault("agent_workflow", {})
    subagents: list[dict] = workflow.setdefault("subagents", [])

    if any(sa.get("id") == subagent_id for sa in subagents):
        return {
            "ok": False,
            "error": f"subagent id {subagent_id!r} already exists; use update_subagent to modify it",
        }

    subagents.append(copy.deepcopy(definition))

    errors = validate_partial("agent_core", candidate_agent_core)
    if errors:
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

    # Commit the validated candidate.
    accumulator["agent_core"] = candidate_agent_core
    # Mark the workflow's `subagents` chat field as answered. Without this
    # the workflow phase stays incomplete forever because field_status
    # never transitions out of `pending` — the LLM has no signal that the
    # field has been populated, and `_is_phase_complete("workflow")`
    # keeps returning False even after subagents have been registered.
    field_status["agent_core.agent_workflow.subagents"] = "answered"
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

    # Candidate-copy commit. Builds the would-be merged agent_core on a
    # deep copy first so the live accumulator never carries a half-updated
    # subagent if validation fails mid-merge.
    candidate_agent_core = copy.deepcopy(accumulator.get("agent_core", {}))
    candidate_subagents = (
        candidate_agent_core.get("agent_workflow", {}).get("subagents", [])
    )

    target = next(
        (sa for sa in candidate_subagents if sa.get("id") == subagent_id),
        None,
    )
    if target is None:
        return {"ok": False, "error": f"subagent id {subagent_id!r} not found"}

    target.update(fields)

    errors = validate_partial("agent_core", candidate_agent_core)
    if errors:
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

    # Commit the validated candidate.
    accumulator["agent_core"] = candidate_agent_core
    logger.info(
        "update_subagent.success",
        extra={
            "operation": "tools.update_subagent",
            "status": "success",
            "subagent_id": subagent_id,
        },
    )
    return {"ok": True, "id": subagent_id}


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

    from dev_kit.schemas.validation import validate_partial  # noqa: PLC0415

    # Candidate-copy commit. Builds the routing addition on a deep copy
    # of agent_core, validates, and only swaps in on success — keeps
    # invalid rules (e.g. an intent that crashes startup invariants) out
    # of the live accumulator and the per-turn YAML render.
    candidate_agent_core = copy.deepcopy(accumulator.get("agent_core", {}))
    candidate_subagents = (
        candidate_agent_core.get("agent_workflow", {}).get("subagents", [])
    )

    target = next(
        (sa for sa in candidate_subagents if sa.get("id") == from_id),
        None,
    )
    if target is not None:
        # Match the mirror schema (RoutingRule in
        # dev_kit/schemas/domain/agent_core.py): the field is
        # `next_subagent_id`, not `to`, and `conditions` is a list of
        # RoutingCondition objects, not a single `condition`.
        rule: dict[str, Any] = {"intent": intent, "next_subagent_id": to_id}
        condition = args.get("condition")
        if condition:
            # Tool API accepts a single condition object for ergonomics;
            # promote to the schema's list shape.
            rule["conditions"] = condition if isinstance(condition, list) else [condition]
        # Idempotent dedupe — match sibling tools ``add_subagent``
        # (id check) and ``add_tool`` (name check). The LLM tool-loop
        # retry pattern (``_MAX_TOOL_ROUNDS=4``) regularly re-emits a
        # batch when one call in the batch fails validation, so
        # without this check the routing list grows by one each retry.
        existing_routes = target.setdefault("routing", [])
        for r in existing_routes:
            if (
                r.get("intent") == intent
                and r.get("next_subagent_id") == to_id
                and r.get("conditions") == rule.get("conditions")
            ):
                return {
                    "ok": True,
                    "noop": True,
                    "reason": "duplicate routing rule skipped",
                    "from": from_id,
                    "intent": intent,
                    "to": to_id,
                }
        existing_routes.append(rule)

        errors = validate_partial("agent_core", candidate_agent_core)
        if errors:
            error_msg = "; ".join(errors)
            logger.warning(
                "add_routing_rule.validation_failed",
                extra={
                    "operation": "tools.add_routing_rule",
                    "status": "failure",
                    "from_subagent_id": from_id,
                    "intent": intent,
                    "to_subagent_id": to_id,
                    "error": error_msg,
                },
            )
            return {"ok": False, "error": f"Validation failed: {error_msg}"}

        # Commit the validated candidate.
        accumulator["agent_core"] = candidate_agent_core
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
    field_status: dict[str, str],
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
        field_status: Mutated on success — both ``action_gateway.tools`` and
            the matching ``agent_core.connectors.<category>`` field are
            marked ``"answered"`` so the tools phase can advance once at
            least one tool has been registered.

    Returns:
        ``{"ok": True, "id": tool_id}`` on success.
        ``{"ok": False, "error": "..."}`` on duplicate id or validation failure.
    """
    from dev_kit.schemas.validation import (  # noqa: PLC0415
        validate_domain_section,
        validate_partial,
    )

    spec = args.get("spec")
    if not isinstance(spec, dict):
        return {"ok": False, "error": "args.spec must be a dict"}

    tool_id = spec.get("id")
    if not tool_id:
        return {"ok": False, "error": "spec.id is required"}

    # Auto-coerce four common LLM mistakes BEFORE validation so the
    # error feedback is about real issues, not noise the wizard can fix
    # itself:
    #
    #   1. params[i].source defaults to "agent" (the parser sets this on
    #      the dataclass but the LLM often drops it when reconstructing
    #      the spec by hand).
    #   2. params[i].type="number" / "float" → "string" (the mirror's
    #      ParamType allowlist excludes them; the REST adapter passes
    #      strings through to the HTTP query verbatim, so the user can
    #      type "12.97" without issue).
    #   3. auth defaults to {type: "none"} on REST tools when the LLM
    #      omits it (e.g. Open-Meteo, webhook.site test APIs have no
    #      auth). The legacy deploy schema requires auth (not Optional),
    #      so the dev-kit's host validation would let the spec through
    #      but /deploy/validate would reject it later.  Inject the
    #      no-auth block here so both layers agree.
    #   4. response.max_size_chars defaults are filled in by the mirror;
    #      no auto-coerce needed here.
    if spec.get("type") != "mcp":
        for endpoint in spec.get("endpoints", []) or []:
            for param in endpoint.get("params", []) or []:
                if "source" not in param:
                    param["source"] = "agent"
                if param.get("type") in ("number", "float"):
                    param["type"] = "string"
        if spec.get("auth") is None:
            spec["auth"] = {"type": "none"}

    # Candidate-copy commit. add_tool touches two blocks (action_gateway
    # for the tool spec, agent_core for the matching connector) so we
    # build both on deep copies, validate each, and only swap them into
    # the live accumulator once both pass. No half-applied state can
    # reach the per-turn YAML render.
    candidate_ag = copy.deepcopy(accumulator.get("action_gateway", {}))
    candidate_ag_tools: list[dict] = candidate_ag.setdefault("tools", [])
    if any(t.get("id") == tool_id for t in candidate_ag_tools):
        return {"ok": False, "error": f"tool id {tool_id!r} already exists"}

    # Also dedupe by (method, base_url + path) — the LLM has been known
    # to register the same OpenAPI operation TWICE under different IDs
    # (e.g. `get_v1_forecast` from parse_openapi_spec, then
    # `weather_forecast` as a custom user-facing name on a later turn).
    # Result: 6 connector entries for 3 actual APIs, all routed to the
    # same underlying URL. Subagent tool references become ambiguous.
    if spec.get("type") != "mcp":
        existing_operations: dict[tuple[str, str], str] = {}
        for existing in candidate_ag_tools:
            existing_base = existing.get("base_url", "")
            for ep in existing.get("endpoints", []) or []:
                key = (
                    str(ep.get("method", "")).upper(),
                    existing_base + str(ep.get("path", "")),
                )
                existing_operations[key] = existing.get("id", "?")
        for ep in spec.get("endpoints", []) or []:
            key = (
                str(ep.get("method", "")).upper(),
                spec.get("base_url", "") + str(ep.get("path", "")),
            )
            if key in existing_operations:
                return {
                    "ok": False,
                    "error": (
                        f"This API operation ({key[0]} {key[1]}) is already "
                        f"registered as tool {existing_operations[key]!r}. "
                        f"If you want to change its name or projection, use "
                        f"update_subagent / a different tool — do NOT call "
                        f"add_tool a second time for the same operation."
                    ),
                }

    candidate_ag_tools.append(copy.deepcopy(spec))

    # Strict validation (omit_missing=False) — a tool spec must be
    # complete before it reaches the accumulator. Earlier add_tool used
    # partial validation, which silently dropped "required field missing"
    # errors and committed broken specs that only failed at deploy-time.
    # The Akashvani Concierge E2E shipped 3 tools missing description,
    # base_url, auth, and endpoint name; deploy-validate then raised 27
    # cascading errors.
    errors = validate_domain_section(
        "action_gateway", "tools", candidate_ag_tools, omit_missing=False
    )
    if errors:
        logger.warning(
            "add_tool.ag_validation_failed",
            extra={
                "operation": "tools.add_tool",
                "status": "failure",
                "error": errors,
                "tool_id": tool_id,
            },
        )
        return {"ok": False, "error": f"action_gateway validation failed: {errors}"}

    # --- Agent Core connector side ---
    # MCP tools — schemas come from the server at runtime; no static connector.
    candidate_ac = None
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
        candidate_ac = copy.deepcopy(accumulator.get("agent_core", {}))
        connectors_block = candidate_ac.setdefault("connectors", {})
        connector_list: list[dict] = connectors_block.setdefault(category, [])

        replaced = False
        for i, c in enumerate(connector_list):
            if c.get("name") == tool_id:
                connector_list[i] = connector
                replaced = True
                break
        if not replaced:
            connector_list.append(connector)

        errors = validate_partial("agent_core", candidate_ac)
        if errors:
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

    # Both candidates passed — commit them atomically to the live accumulator.
    accumulator["action_gateway"] = candidate_ag
    if candidate_ac is not None:
        accumulator["agent_core"] = candidate_ac

    # Mark the chat fields this tool just populated. Without these flips
    # the tools phase stays incomplete — `add_tool` writes the connector
    # list directly (via the candidate-copy commit above) but never
    # transitions the matching `field_status` entries out of `pending`,
    # so `_is_phase_complete("tools")` keeps reporting "stay" even
    # after every requested tool has been registered.
    field_status["action_gateway.tools"] = "answered"
    if spec.get("type") != "mcp":
        category = spec.get("category", "read")
        # Only flip the category that was actually written. The mirror
        # schema allows `connectors.read/write/identity` to stay absent,
        # so we don't promise that the OTHER categories are answered.
        field_status[f"agent_core.connectors.{category}"] = "answered"

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
        underscore-prefixed discovery keys: ``_discovery_id``, ``_path``,
        ``_method``, ``_summary``, ``_params``, ``_response_fields``.
        The prefix is intentional — it visually distinguishes the
        discovery shape from the ``add_tool`` spec shape the LLM must
        construct on its next turn.
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

    # Surface the params + a best-effort response-field projection list so
    # the LLM can render the confirmation table the tools-phase prompt
    # mandates (operation | method+path | params | response fields). The
    # LLM can also see the raw spec text in its context, but giving it a
    # pre-extracted list avoids the model having to re-walk YAML and
    # invent JSONPaths from scratch.
    #
    # ALL DISCOVERY KEYS ARE PREFIXED WITH ``_`` to make them visually
    # distinct from the ``add_tool`` spec shape the LLM must construct on
    # the next turn. Earlier the discovery payload used identical key
    # names (``id``, ``path``, ``method``, ``summary``, ``params``,
    # ``response_fields``) which the LLM copied verbatim into
    # ``add_tool(spec.endpoints[i])`` — every one of those keys fails the
    # mirror's strict ``EndpointDefinition`` schema with
    # ``extra_forbidden``, and the wizard ate four LLM rounds of retries
    # per tool. The prefix forces the model to consciously rename when
    # building the add_tool spec.
    operations: list[dict[str, Any]] = []
    paths_spec = spec.get("paths", {})
    for t in tools:
        op_spec = paths_spec.get(t.path, {}).get(t.method.lower(), {})
        response_fields = _extract_response_fields(op_spec)
        operations.append({
            "_discovery_id": t.suggested_id,
            "_path": t.path,
            "_method": t.method,
            "_summary": t.description,
            "_params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "description": p.description,
                }
                for p in t.params
            ],
            "_response_fields": response_fields,
        })

    logger.info(
        "parse_openapi_spec.success",
        extra={
            "operation": "tools.parse_openapi_spec",
            "status": "success",
            "operation_count": len(operations),
        },
    )
    return {
        "ok": True,
        "operations": operations,
        # Belt-and-braces nudge for the LLM in case the prompt rule
        # gets ignored. The tools-phase prompt mandates a
        # parse → confirm → add_tool pacing; this echo in the tool
        # result reinforces it from the LLM's other context channel.
        "next_step": (
            "Do NOT call add_tool yet. Render the operations + their "
            "response_fields in a table for the user, then ask them to "
            "confirm or edit the projection list. Only call add_tool "
            "on the NEXT turn, after the user has confirmed."
        ),
    }


def _extract_response_fields(op_spec: dict[str, Any]) -> list[str]:
    """Return dotted JSONPaths for every leaf field in the 200 JSON response.

    Walks ``op_spec["responses"]["200"]["content"]["application/json"]["schema"]``
    and emits one path per primitive leaf (string/number/integer/boolean).
    Arrays contribute their item schema's paths prefixed with ``[].`` so
    the operator can see, e.g., ``results[].latitude``. Objects nest with
    ``.``. Returns an empty list if the operation has no JSON 200
    response or the schema is too opaque to walk (``$ref``-only, no
    ``properties``, etc.).

    Used by ``parse_openapi_spec`` to populate each operation's
    ``response_fields`` projection list so the tools-phase prompt can
    show the user which API response fields will flow into the LLM
    context. The user can then edit the projection (drop some, add
    others) before ``add_tool`` is called with the final
    ``response_filter`` list.

    Args:
        op_spec: A single operation dict from
            ``spec["paths"][<path>][<method>]``.

    Returns:
        A list of dotted JSONPath strings, deduplicated and order-preserving.
        Empty list if the response schema cannot be walked.
    """
    if not isinstance(op_spec, dict):
        return []
    responses = op_spec.get("responses") or {}
    if not isinstance(responses, dict):
        return []
    # Try 200, then any 2xx, then default.
    success = responses.get("200") or responses.get("default")
    if not success:
        for code, body in responses.items():
            if isinstance(code, str) and code.startswith("2"):
                success = body
                break
    if not isinstance(success, dict):
        return []
    content = success.get("content") or {}
    schema = (content.get("application/json") or {}).get("schema") or {}
    if not isinstance(schema, dict):
        return []

    paths: list[str] = []
    seen: set[str] = set()

    def _walk(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        # Inline $ref — we don't resolve refs; treat as opaque leaf.
        if "$ref" in node:
            if prefix and prefix not in seen:
                seen.add(prefix)
                paths.append(prefix)
            return
        node_type = node.get("type")
        if node_type == "object" or "properties" in node:
            props = node.get("properties") or {}
            for key, sub in props.items():
                _walk(sub, f"{prefix}.{key}" if prefix else key)
        elif node_type == "array":
            items = node.get("items") or {}
            _walk(items, f"{prefix}[]" if prefix else "[]")
        else:
            # Primitive leaf — emit the path.
            if prefix and prefix not in seen:
                seen.add(prefix)
                paths.append(prefix)

    _walk(schema, "")
    return paths


def fetch_openapi_spec_from_url(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],  # unused — kept for signature uniformity
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """Download an OpenAPI 3.x spec from a URL and parse it into tool candidates.

    Wraps the same parser used by ``parse_openapi_spec`` so the caller gets a
    consistent ``operations`` shape regardless of whether the spec arrived as a
    pasted string or via HTTP. Accepts JSON or YAML payloads.

    Mirrors the implementation that shipped on ``main`` before the state-layer
    migration trimmed it out. The wizard's tools phase needs this entry point
    so users with a real-world OpenAPI doc don't have to paste a multi-thousand-
    line spec into chat.

    Args:
        args: Must contain ``url`` (str). The URL may serve JSON or YAML; the
            response is auto-detected by trying JSON first.
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Not used; accepted for signature uniformity.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "operations": [...]}`` with each entry's discovery
        keys prefixed (``_discovery_id``, ``_path``, ``_method``,
        ``_summary``) so the LLM can't blindly copy them into the
        ``add_tool`` spec shape (see parse_openapi_spec docstring).
        ``{"ok": False, "error": "..."}`` on missing arg, network failure,
        or parse failure.
    """
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from dev_kit.agent.openapi_parser import parse_openapi_spec as _parse  # noqa: PLC0415

    url = args.get("url")
    if not url or not isinstance(url, str) or not url.strip():
        return {"ok": False, "error": "args.url is required and must be a non-empty string"}
    url = url.strip()

    start = _time.time()
    try:
        transport = httpx.HTTPTransport(retries=1)
        with httpx.Client(
            transport=transport,
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "fetch_openapi_spec_from_url.http_error",
            extra={
                "operation": "tools.fetch_openapi_spec_from_url",
                "status": "failure",
                "url": url,
                "http_status": exc.response.status_code,
                "latency_ms": int((_time.time() - start) * 1000),
            },
        )
        return {"ok": False, "error": f"HTTP {exc.response.status_code} fetching {url}"}
    except httpx.HTTPError as exc:
        logger.warning(
            "fetch_openapi_spec_from_url.network_error",
            extra={
                "operation": "tools.fetch_openapi_spec_from_url",
                "status": "failure",
                "url": url,
                "error": str(exc),
                "latency_ms": int((_time.time() - start) * 1000),
            },
        )
        return {"ok": False, "error": f"could not fetch spec from {url} — {exc}"}

    content = response.text
    try:
        spec = _json.loads(content)
    except _json.JSONDecodeError:
        try:
            import yaml as _yaml  # noqa: PLC0415
            spec = _yaml.safe_load(content)
        except Exception as exc:  # yaml emits a soup of exceptions; catch them all here
            logger.warning(
                "fetch_openapi_spec_from_url.parse_failed",
                extra={
                    "operation": "tools.fetch_openapi_spec_from_url",
                    "status": "failure",
                    "url": url,
                    "error": str(exc),
                },
            )
            return {"ok": False, "error": f"could not parse fetched content as JSON or YAML — {exc}"}

    if not isinstance(spec, dict):
        return {"ok": False, "error": "fetched content is not a JSON/YAML object"}

    try:
        tools = _parse(spec)
    except ValueError as exc:
        logger.warning(
            "fetch_openapi_spec_from_url.openapi_invalid",
            extra={
                "operation": "tools.fetch_openapi_spec_from_url",
                "status": "failure",
                "url": url,
                "error": str(exc),
            },
        )
        return {"ok": False, "error": str(exc)}

    operations = [
        # Discovery keys are prefixed with `_` for the same reason as
        # `parse_openapi_spec` above (see its long comment): the LLM
        # previously copied these keys verbatim into add_tool's spec
        # shape, triggering ``extra_forbidden`` mirror rejections on
        # every tool registration.
        {
            "_discovery_id": t.suggested_id,
            "_path": t.path,
            "_method": t.method,
            "_summary": t.description,
        }
        for t in tools
    ]
    logger.info(
        "fetch_openapi_spec_from_url.success",
        extra={
            "operation": "tools.fetch_openapi_spec_from_url",
            "status": "success",
            "url": url,
            "operation_count": len(operations),
            "latency_ms": int((_time.time() - start) * 1000),
        },
    )
    return {"ok": True, "operations": operations, "source_url": url}


def _parse_sse_json(text: str) -> dict | None:
    """Extract the first JSON-RPC payload from an SSE response body.

    SSE lines have the form ``data: <json>``. This function scans the
    response text for the first such line and returns the parsed dict, or
    ``None`` if no valid ``data:`` line is found.

    Args:
        text: Raw response body string.

    Returns:
        Parsed dict from the first ``data:`` line, or None.
    """
    import json as _json  # noqa: PLC0415

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[len("data:"):].strip()
            try:
                return _json.loads(payload)
            except _json.JSONDecodeError:
                continue
    return None


def discover_mcp_tools(
    args: dict[str, Any],
    intake_state: IntakeState,  # unused — kept for signature uniformity
    accumulator: dict[str, dict],  # unused — kept for signature uniformity
    field_status: dict[str, str],  # unused — kept for signature uniformity
) -> dict[str, Any]:
    """List tools available on an MCP server via JSON-RPC ``tools/list``.

    Performs the standard MCP discovery handshake:

    1. POST a JSON-RPC ``tools/list`` payload to ``server_url``.
    2. Accept both plain JSON and SSE (``data: <json>``) responses — auto-
       detects by trying JSON first and falling back to line-by-line SSE
       parsing.
    3. Extract ``result.tools`` and surface each tool's
       ``name``/``description``/``inputSchema``.

    Mirrors the implementation that shipped on ``main`` before the
    state-layer migration trimmed it out. The wizard's tools phase uses
    this output to suggest which MCP server to register via ``add_tool``.

    Args:
        args: Must contain ``server_url`` (str). May contain
            ``timeout_ms`` (int, default 10000).
        intake_state: Not used; accepted for signature uniformity.
        accumulator: Not used; accepted for signature uniformity.
        field_status: Not used; accepted for signature uniformity.

    Returns:
        ``{"ok": True, "tools": [...]}`` with each entry shaped as
        ``{"name", "description", "input_schema"}``. ``tools`` is empty
        when the server returns no tools.
        ``{"ok": False, "error": "..."}`` on missing arg, network error,
        or unparseable response.
    """
    import httpx  # noqa: PLC0415

    server_url = args.get("server_url")
    if not server_url:
        return {"ok": False, "error": "args.server_url is required"}
    if not isinstance(server_url, str):
        return {
            "ok": False,
            "error": f"args.server_url must be a string, got {type(server_url).__name__!r}",
        }

    timeout_ms = args.get("timeout_ms", 10_000)
    try:
        timeout_seconds = float(timeout_ms) / 1000.0
    except (TypeError, ValueError):
        timeout_seconds = 10.0

    url = server_url.rstrip("/")
    payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}

    try:
        # Route through a client with a 1-retry transport so a single
        # transient 503 / connection-reset on the MCP server doesn't
        # bounce the LLM into a regeneration cycle. Mirrors the retry
        # behaviour of sibling ``fetch_openapi_spec_from_url`` and
        # satisfies .claude/rules/error-handling.md.
        transport = httpx.HTTPTransport(retries=1)
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "discover_mcp_tools.http_error",
            extra={
                "operation": "tools.discover_mcp_tools",
                "status": "failure",
                "server_url": url,
                "http_status": exc.response.status_code,
                "error": str(exc),
            },
        )
        return {
            "ok": False,
            "error": f"MCP server at {url} returned HTTP {exc.response.status_code}",
        }
    except httpx.HTTPError as exc:
        logger.warning(
            "discover_mcp_tools.network_error",
            extra={
                "operation": "tools.discover_mcp_tools",
                "status": "failure",
                "server_url": url,
                "error": str(exc),
            },
        )
        return {
            "ok": False,
            "error": f"could not reach MCP server at {url} — {exc}",
        }

    # Auto-detect transport: plain JSON first, fall back to SSE parsing.
    import json as _json  # noqa: PLC0415
    try:
        data = response.json()
    except (_json.JSONDecodeError, ValueError):
        data = _parse_sse_json(response.text)
        if data is None:
            preview = response.text[:200]
            logger.warning(
                "discover_mcp_tools.unparseable_response",
                extra={
                    "operation": "tools.discover_mcp_tools",
                    "status": "failure",
                    "server_url": url,
                    "preview": preview,
                },
            )
            return {
                "ok": False,
                "error": (
                    f"MCP server at {url} returned an unrecognised response "
                    f"format (expected JSON-RPC or SSE). Preview: {preview!r}"
                ),
            }

    raw_tools = data.get("result", {}).get("tools", []) if isinstance(data, dict) else []
    tools = [
        {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema", {}),
        }
        for t in raw_tools
        if isinstance(t, dict)
    ]
    logger.info(
        "discover_mcp_tools.success",
        extra={
            "operation": "tools.discover_mcp_tools",
            "status": "success",
            "server_url": url,
            "tool_count": len(tools),
        },
    )
    return {"ok": True, "tools": tools, "server_url": url}


