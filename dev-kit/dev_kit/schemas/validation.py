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


def validate_domain_section(block: str, section: str, merged_data: dict) -> Optional[str]:
    """Validate a domain section's merged data.

    Args:
        block: Block name (e.g. "agent_core").
        section: Dot-notation path. Only the first segment is used to look up
            the schema; nested writes are validated against the parent section.
        merged_data: The full top-level section dict after deep-merge.

    Returns:
        None if valid; a formatted error string if invalid.
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
        formatted = _format_pydantic_error(e)
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

    # --- Validate each section ---
    errors: list[str] = []
    for top_level, value in data.items():
        if not isinstance(value, dict):
            continue
        err = validate_domain_section(block, top_level, value)
        if err:
            # Filter out "[missing]" lines — partial data is allowed to omit fields.
            # Keep type/value constraint violations and extra-field errors.
            filtered_lines = [
                line for line in err.split("\n")
                if "[missing]" not in line
            ]
            if filtered_lines:
                errors.append("\n".join(filtered_lines))
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


def _format_pydantic_error(err: ValidationError) -> str:
    """Render a ValidationError as a single human-readable string for LLM/operator feedback.

    Includes the error type code, field path, message, and the offending input value
    so the LLM can self-correct on retry without resending the same wrong value.
    """
    lines = []
    for e in err.errors():
        path = ".".join(str(p) for p in e["loc"]) or "<root>"
        err_type = e.get("type", "unknown")
        msg = e.get("msg", "")
        offending = e.get("input")
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
        lines.append(f"- {path} [{err_type}]: {msg}{value_hint}")
    return "\n".join(lines)
