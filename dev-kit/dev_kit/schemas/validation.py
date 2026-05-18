"""Validation entry points used by the accumulator and deploy wizard endpoint.

Three main functions:
- validate_domain_section(block, section, merged_data) — for the LLM tool handler
- validate_dpg_block(block, parsed_yaml) — for operator edits in deploy wizard
- get_valid_sections(block) — returns section names for a block (replaces legacy loader)
"""
from __future__ import annotations
import logging
import time
from typing import Optional
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Known DPG block names
_VALID_BLOCKS = {
    "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
    "action_gateway", "reach_layer", "observability_layer",
}

from dev_kit.schemas.domain import (
    agent_core,
    knowledge_engine,
    memory_layer,
    trust_layer,
    action_gateway,
    reach_layer,
    observability_layer,
)
from dev_kit.schemas.dpg.agent_core import AgentCoreDpgConfig
from dev_kit.schemas.dpg.knowledge_engine import KnowledgeEngineDpgConfig
from dev_kit.schemas.dpg.memory_layer import MemoryLayerDpgConfig
from dev_kit.schemas.dpg.trust_layer import TrustLayerDpgConfig
from dev_kit.schemas.dpg.action_gateway import ActionGatewayDpgConfig
from dev_kit.schemas.dpg.reach_layer import ReachLayerDpgConfig
from dev_kit.schemas.dpg.observability_layer import ObservabilityLayerDpgConfig


# Dispatch: (block, top_level_section) → Pydantic schema class
DOMAIN_SECTION_SCHEMAS: dict[tuple[str, str], type] = {
    ("agent_core", "agent"): agent_core.AgentSection,
    ("agent_core", "preprocessing"): agent_core.PreprocessingSection,
    ("agent_core", "conversation"): agent_core.ConversationSection,
    ("agent_core", "channels"): agent_core.ChannelsSection,
    ("agent_core", "connectors"): agent_core.ConnectorsSection,
    ("agent_core", "agent_workflow"): agent_core.AgentWorkflowSection,
    ("agent_core", "entity_to_profile_field"): agent_core.EntityToProfileFieldSection,
    ("agent_core", "hitl"): agent_core.HitlSection,
    ("agent_core", "reach_layer"): agent_core.ReachLayerDefaultsSection,
    ("agent_core", "observability"): agent_core.ObservabilitySection,
    ("knowledge_engine", "knowledge"): knowledge_engine.KnowledgeSection,
    ("knowledge_engine", "observability"): knowledge_engine.ObservabilitySection,
    ("memory_layer", "state"): memory_layer.StateSection,
    ("memory_layer", "user_data_persistence"): memory_layer.UserDataPersistenceSection,
    ("memory_layer", "reengagement"): memory_layer.ReengagementSection,
    ("memory_layer", "observability"): memory_layer.ObservabilitySection,
    ("trust_layer", "trust"): trust_layer.TrustSection,
    ("trust_layer", "dignity_check"): trust_layer.DignityCheckSection,
    ("trust_layer", "observability"): trust_layer.ObservabilitySection,
    ("action_gateway", "tools"): action_gateway.ToolsSection,
    ("action_gateway", "observability"): action_gateway.ObservabilitySection,
    ("reach_layer", "reach_layer"): reach_layer.ReachLayerSection,
    ("observability_layer", "observability"): observability_layer.ObservabilitySection,
}

DPG_BLOCK_SCHEMAS: dict[str, type] = {
    "agent_core": AgentCoreDpgConfig,
    "knowledge_engine": KnowledgeEngineDpgConfig,
    "memory_layer": MemoryLayerDpgConfig,
    "trust_layer": TrustLayerDpgConfig,
    "action_gateway": ActionGatewayDpgConfig,
    "reach_layer": ReachLayerDpgConfig,
    "observability_layer": ObservabilityLayerDpgConfig,
}


def validate_domain_section(
    block: str,
    section: str,
    merged_data: dict,
    *,
    omit_missing: bool = False,
) -> Optional[str]:
    """Validate a domain section's merged data.

    Args:
        block: Block name (e.g. "agent_core").
        section: Dot-notation path. Only the first segment is used to look up
            the schema; nested writes are validated against the parent section.
        merged_data: The full top-level section dict after deep-merge.
        omit_missing: When True, drop ``missing`` (required-field) errors
            before formatting. Used by partial-validation callers
            (``validate_partial`` and the candidate-copy commit path)
            because partial data is allowed to omit fields the user
            hasn't been asked about yet.

    Returns:
        None if valid (or only ``missing`` errors remain after the
        ``omit_missing`` filter); a formatted error string otherwise.
    """
    start = time.time()
    top_level = section.split(".", 1)[0]
    schema = DOMAIN_SECTION_SCHEMAS.get((block, top_level))
    if schema is None:
        logger.warning(
            "validation_unknown_section",
            extra={
                "operation": "validate_domain_section",
                "status": "skipped",
                "block": block,
                "section": section,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return f"Unknown section '{section}' for block '{block}'"
    try:
        schema.model_validate(merged_data)
        logger.info(
            "validation_success",
            extra={
                "operation": "validate_domain_section",
                "status": "success",
                "block": block,
                "section": top_level,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return None
    except ValidationError as e:
        formatted = _format_pydantic_error(e, omit_missing=omit_missing)
        all_errors = e.errors()
        # Partial drafts naturally trigger [missing] errors (required fields
        # not yet supplied) — both validate_partial and acc.update filter
        # those before reporting. Log at DEBUG when ONLY missing-field
        # errors are present so partial-mode validation doesn't drown the
        # console in false-WARNING noise. Real errors (value_error,
        # extra_forbidden, type_error, etc.) keep the WARNING level and
        # carry a one-line summary in the message string itself, so the
        # default Python log formatter (which shows only %(message)s)
        # surfaces what failed without requiring a structured handler.
        non_missing_errors = [err for err in all_errors if err.get("type") != "missing"]
        field_errors = [
            {
                "loc": ".".join(str(p) for p in err["loc"]) or "<root>",
                "type": err.get("type", "unknown"),
                "msg": err.get("msg", ""),
                "input": _truncate_input(err.get("input")),
            }
            for err in all_errors
        ]
        latency_ms = int((time.time() - start) * 1000)

        if not non_missing_errors:
            logger.debug(
                "validation_partial_missing_only block=%s section=%s missing=%d",
                block, top_level, len(all_errors),
                extra={
                    "operation": "validate_domain_section",
                    "status": "partial_missing_only",
                    "block": block,
                    "section": top_level,
                    "error_count": len(all_errors),
                    "field_errors": field_errors,
                    "latency_ms": latency_ms,
                },
            )
        else:
            sample = " | ".join(
                f"{e['loc']}[{e['type']}]: {e['msg'][:80]}"
                for e in field_errors if e["type"] != "missing"
            )
            logger.warning(
                "validation_failed block=%s section=%s errors=%d :: %s",
                block, top_level, len(non_missing_errors), sample,
                extra={
                    "operation": "validate_domain_section",
                    "status": "failure",
                    "block": block,
                    "section": top_level,
                    "error_count": len(all_errors),
                    "non_missing_count": len(non_missing_errors),
                    "field_errors": field_errors,
                    "latency_ms": latency_ms,
                },
            )
        return formatted


def get_valid_sections(block: str) -> list[str]:
    """Return the sorted list of top-level section names declared for a block.

    Used by tools.py to render the `update_config` tool description, and by
    phases.py to list valid sections in the knowledge phase prompt.
    Replaces the legacy loader-based lookup that read YAML templates.

    Args:
        block: Block name, e.g. "agent_core" or "trust_layer".

    Returns:
        Sorted list of valid top-level section names for this block.
        Empty list if block is unknown.
    """
    return sorted(
        section for (b, section) in DOMAIN_SECTION_SCHEMAS.keys() if b == block
    )


def validate_partial(block: str, data: dict) -> list[str]:
    """Validate each top-level section of a block's data; return error messages.

    Thin wrapper around ``validate_domain_section`` that preserves the
    legacy renderer interface (returns ``list[str]`` of error messages).
    Two checks:

    1. Block existence — fails if ``block`` is not a known DPG block.
    2. For each top-level section in ``data``: delegates to
       ``validate_domain_section`` and filters out "missing field" errors
       since partial data is allowed to omit fields.

    Args:
        block: Block name, e.g. ``"agent_core"`` or ``"trust_layer"``.
        data: Partial config dict to validate.

    Returns:
        List of error strings. Empty list means valid so far.
    """
    # --- Block existence check ---
    if block not in _VALID_BLOCKS:
        return [f"Unknown block: {block!r}"]

    if not data:
        return []

    # reach_layer is the only block whose runtime YAML wraps under a
    # top-level `reach_layer:` key. FIELD_RULES paths are flat
    # (`channels.web.ui.app_name`, `common.observability.domain`) — so
    # `on_config_update` builds a candidate with top-level keys `channels`
    # and `common`, and `validate_partial` would otherwise report them as
    # "Unknown section". Auto-wrap when the wrapper is absent so
    # DOMAIN_SECTION_SCHEMAS lookups resolve. Idempotent: if the caller
    # already wrapped (e.g. tests passing wrapped data), this is a no-op.
    if block == "reach_layer" and "reach_layer" not in data:
        data = {"reach_layer": data}

    # --- Validate each section ---
    errors: list[str] = []
    for top_level, value in data.items():
        # Validate both dicts AND lists. Earlier this loop silently
        # skipped lists (`if not isinstance(value, dict): continue`),
        # which meant ``action_gateway.tools`` — a list of
        # ``ToolDefinition`` — was never validated. The wizard's
        # ``add_tool`` then committed broken specs (missing description,
        # base_url, auth, non-snake-case id, etc.) without ever raising
        # an error, and the failures only surfaced at deploy-time when
        # the full Pydantic model rejected the whole block. The new
        # behaviour: anything other than ``None`` goes through
        # ``validate_domain_section``, which knows how to handle both
        # dict-shaped sections (``ToolsSection``) and root-list sections
        # (``RootModel[list[...]]``).
        if value is None:
            continue
        if not isinstance(value, (dict, list)):
            continue
        # Pass omit_missing=True — partial drafts are allowed to omit
        # required fields the user hasn't been asked about yet. Real
        # errors (extra_forbidden, value_error, type_error, pattern
        # mismatch, …) still surface as actionable plain English.
        err = validate_domain_section(block, top_level, value, omit_missing=True)
        if err:
            errors.append(err)
    return errors


def validate_full(block: str, data: dict) -> list[str]:
    """Strict variant of ``validate_partial`` — surfaces missing required fields.

    Iterates the same top-level sections but calls
    ``validate_domain_section`` with ``omit_missing=False`` so required
    fields the user hasn't supplied are reported instead of silently
    dropped. Used by ``pre_deploy_validate`` as the host-mode fallback
    when the baked runtime schemas (the canonical Docker-mode gate) are
    not available. In Docker the baked schemas take over and this
    function is not called.

    Args:
        block: Block name, e.g. ``"agent_core"`` or ``"trust_layer"``.
        data: Full merged config dict (DPG defaults + domain values).

    Returns:
        List of error strings. Empty list means valid.
    """
    if block not in _VALID_BLOCKS:
        return [f"Unknown block: {block!r}"]
    if not data:
        return []
    if block == "reach_layer" and "reach_layer" not in data:
        data = {"reach_layer": data}

    errors: list[str] = []
    for top_level, value in data.items():
        if value is None:
            continue
        if not isinstance(value, (dict, list)):
            continue
        err = validate_domain_section(block, top_level, value, omit_missing=False)
        if err:
            errors.append(err)
    return errors


def validate_dpg_block(block: str, parsed_yaml: dict) -> Optional[str]:
    """Validate a full DPG framework YAML against its schema.

    Args:
        block: Block name.
        parsed_yaml: The full YAML parsed to dict.

    Returns:
        None if valid; a formatted error string if invalid.
    """
    start = time.time()
    schema = DPG_BLOCK_SCHEMAS.get(block)
    if schema is None:
        logger.warning(
            "dpg_validation_unknown_block",
            extra={
                "operation": "validate_dpg_block",
                "status": "skipped",
                "block": block,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return f"Unknown block '{block}'"
    try:
        schema.model_validate(parsed_yaml)
        logger.info(
            "dpg_validation_success",
            extra={
                "operation": "validate_dpg_block",
                "status": "success",
                "block": block,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return None
    except ValidationError as e:
        formatted = _format_pydantic_error(e)
        logger.warning(
            "dpg_validation_failed",
            extra={
                "operation": "validate_dpg_block",
                "status": "failure",
                "block": block,
                "error_count": len(e.errors()),
                "field_errors": [
                    {
                        "loc": ".".join(str(p) for p in err["loc"]) or "<root>",
                        "type": err.get("type", "unknown"),
                        "msg": err.get("msg", ""),
                        "input": _truncate_input(err.get("input")),
                    }
                    for err in e.errors()
                ],
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return formatted


def _truncate_input(value: object, max_len: int = 200) -> object:
    """Render an offending value compactly for log fields.

    Pydantic ValidationError.errors() includes the original input value, which
    can be a deeply nested dict or a long string. Logging it raw blows up log
    volume and can leak large payloads. This helper repr's the value and caps
    the length so structured logs stay readable.

    Args:
        value: The offending input from a Pydantic error dict (any type).
        max_len: Maximum length of the rendered repr.

    Returns:
        The original value if small/None; otherwise a truncated repr string.
    """
    if value is None:
        return None
    try:
        rendered = repr(value)
    except Exception:
        return "<unrenderable>"
    if len(rendered) > max_len:
        return rendered[:max_len] + "...<truncated>"
    return rendered


# Plain-English translations for the noisy Pydantic error codes the LLM
# routinely struggles with. The translation explains what went wrong in
# terms of the field name and an action the LLM can take next — keeping
# raw "extra_forbidden" / "string_too_short" jargon out of the LLM's
# context (and, by extension, out of the user-facing prose).
_PYDANTIC_HINTS: dict[str, str] = {
    "extra_forbidden": (
        "{path} is not a field this wizard accepts. Either the path is "
        "wrong (check the Pydantic schemas section in the system prompt "
        "for the exact field names) or this field is set automatically "
        "and is not user-configurable. Do NOT retry the same path."
    ),
    "missing": (
        "{path} is required but was not provided. Include it in your "
        "next write."
    ),
    "string_too_short": (
        "{path} cannot be an empty string. Provide a non-empty value."
    ),
    "string_too_long": (
        "{path} exceeded the maximum string length. Shorten the value."
    ),
    "value_error": (
        "{path} failed a custom validator: {msg}. Adjust the value and "
        "retry, but do NOT echo this error to the user — explain in "
        "plain English what you need from them."
    ),
    "enum": (
        "{path} must be one of the allowed values listed in the Pydantic "
        "schemas section. Pick from the allowlist."
    ),
    "literal_error": (
        "{path} must be one of the allowed literal values shown in the "
        "Pydantic schemas section. Pick from the allowlist."
    ),
    "type_error.float": "{path} must be a number (not a string).",
    "type_error.integer": "{path} must be an integer (not a string).",
    "type_error.boolean": "{path} must be true or false.",
    "type_error.list": "{path} must be a list.",
    "type_error.dict": "{path} must be a dict.",
    "type_error.string": "{path} must be a string.",
}


def _humanise_pydantic_error_line(err_type: str, path: str, msg: str) -> str:
    """Translate one Pydantic error entry into LLM-actionable plain English.

    Falls back to the original Pydantic message when no hint is registered
    for the error type, so we never silently drop information.

    Args:
        err_type: Pydantic error code (``extra_forbidden``, ``missing``, …).
        path: Dotted field path the error applies to.
        msg: Pydantic's own human-readable error message.

    Returns:
        A single-line human-actionable instruction starting with the field
        path. Suitable to surface to the LLM in a tool_result.
    """
    template = _PYDANTIC_HINTS.get(err_type)
    if template:
        return template.format(path=path, msg=msg)
    # Unknown code — fall back to Pydantic's message verbatim but keep
    # the format consistent.
    return f"{path}: {msg}"


def _format_pydantic_error(err: ValidationError, *, omit_missing: bool = False) -> str:
    """Render a ValidationError as plain English tool feedback.

    The LLM receives this string verbatim in the next tool_result, so
    the more actionable each line is, the more likely the model recovers
    on the same turn. Raw Pydantic codes (``extra_forbidden``,
    ``string_too_short``, ``type_error.float``) routinely confused the
    model in E2E runs — see Akashvani Concierge — so we translate them
    here through ``_humanise_pydantic_error_line``. The translation does
    NOT replace the path or the offending value: those are still
    surfaced so the LLM can self-correct.

    Args:
        err: The Pydantic ValidationError raised during validate_partial.
        omit_missing: When True, drop ``missing`` errors before
            formatting. Used by ``validate_partial`` because partial
            data is allowed to omit fields — the LLM shouldn't see
            "required field X missing" noise for fields it hasn't
            written yet.

    Returns:
        Newline-joined human-actionable instructions, one per error
        entry. Empty string when every entry is filtered out by
        ``omit_missing``.
    """
    lines = []
    for e in err.errors():
        err_type = e.get("type", "unknown")
        if omit_missing and err_type == "missing":
            continue
        path = ".".join(str(p) for p in e["loc"]) or "<root>"
        msg = e.get("msg", "")
        offending = e.get("input")
        action = _humanise_pydantic_error_line(err_type, path, msg)
        if offending is None:
            value_hint = ""
        else:
            try:
                rendered = repr(offending)
                if len(rendered) > 200:
                    rendered = rendered[:200] + "...<truncated>"
                value_hint = f" (you sent: {rendered})"
            except Exception:
                value_hint = ""
        lines.append(f"- {action}{value_hint}")
    return "\n".join(lines)
