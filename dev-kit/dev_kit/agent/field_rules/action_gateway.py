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

    # No default — when `has_external_tools=True` the LLM MUST register
    # at least one tool via `add_tool` (which both writes the spec and
    # flips field_status to "answered"). Removing the skeleton default
    # closes the loophole where the LLM could dispatch nothing and the
    # phase still advanced on the empty-list default — verified in the
    # Akashvani Concierge E2E where the dispatch correctly rejected the
    # same-turn add_tool but the phase auto-completed with zero tools
    # registered because `tools` defaulted to []. When
    # `has_external_tools=False` the applies_if check excludes this
    # field entirely, so phase-skip semantics are unchanged.
    "tools": FieldRule(
        category="chat",
        phase="tools",
        applies_if="has_external_tools",
        invalidated_by=["has_external_tools"],
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
