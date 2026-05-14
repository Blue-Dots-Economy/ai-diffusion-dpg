"""FIELD_RULES for action_gateway. See catalogue §7.4 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the action_gateway runtime block.

FIELD_RULES tracks the whole ``tools`` list as one chat field. Per-entry editing
(``tools[id=X].*``) is enforced by the Pydantic mirror schema (ToolDefinition)
and handled by the ``add_tool`` / OpenAPI-parser tools (Phase 6 of the plan).
Do NOT add ``tools[id=X].*`` entries here.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ── Gated chat: tools list (catalogue §7.4) ───────────────────────────────

    "tools": FieldRule(
        category="chat",
        phase="tools",
        applies_if="has_external_tools",
        invalidated_by=["has_external_tools"],
        default=[],
        description="List of tool definitions (REST or MCP). Mirror max_length=50. Per-entry shape enforced by ToolDefinition.",
        pydantic_class="ToolsSection",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("action_gateway", FIELD_RULES)
