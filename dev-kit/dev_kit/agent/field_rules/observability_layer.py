"""FIELD_RULES for observability_layer. See catalogue §7.7 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the observability_layer runtime block.

The skeleton seeds ``lifecycle: [{state: "started", trigger_tool: null}]`` so
the mirror's min_length=1 constraint on OutcomesConfig.lifecycle is satisfied.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

# Skeleton seed for lifecycle — satisfies OutcomesConfig.lifecycle min_length=1.
_LIFECYCLE_SEED = [{"state": "started", "trigger_tool": None}]

FIELD_RULES: dict[str, FieldRule] = {
    # ── Always-asked chat: observability.outcomes.* (catalogue §7.7) ──────────

    "observability.outcomes.lifecycle": FieldRule(
        category="chat",
        phase="observability",
        default=_LIFECYCLE_SEED,
        description="List of outcome lifecycle states. Required (min_length=1). Each entry: state, trigger_tool (Optional), trigger_condition (reserved).",
        invalidated_by=["domain_description", "has_external_tools"],
        pydantic_class="OutcomesConfig",
    ),
    "observability.outcomes.metrics": FieldRule(
        category="chat",
        phase="observability",
        default=[],
        description="List of OTel metric definitions. Each entry: name, instrument (counter/gauge/histogram), description, unit, attributes.",
        invalidated_by=["domain_description"],
        pydantic_class="OutcomesConfig",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("observability_layer", FIELD_RULES)
