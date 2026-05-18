"""FIELD_RULES for knowledge_engine. See catalogue §7.5 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the knowledge_engine runtime block.

Locked decision #7: knowledge.blocks.multimodal_input_handler.* stays
framework_default_only — NOT included here.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ── Gated chat: knowledge.blocks.glossary.* (catalogue §7.5) ─────────────

    "knowledge.blocks.glossary.enabled": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb"],
        default=True,
        description="Toggle the glossary block for colloquial → canonical normalisation.",
        pydantic_class="GlossarySection",
    ),
    "knowledge.blocks.glossary.mappings": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language", "supported_languages"],
        default=[],
        description="List of glossary mappings (colloquial: list[str], canonical: str).",
        pydantic_class="GlossarySection",
    ),

    # ── Predetermined: knowledge.blocks.static_knowledge_base.* ──────────────

    "knowledge.blocks.static_knowledge_base.enabled": FieldRule(
        category="predetermined",
        rule="set: has_kb",
        invalidated_by=["has_kb"],
        pydantic_class="StaticKnowledgeBaseSection",
    ),
    "knowledge.blocks.static_knowledge_base.collection_name": FieldRule(
        category="predetermined",
        # `project_slug` is the hyphen-separated slug computed from
        # `intake_state.project_name` and exposed by `skeleton.eval_rule`.
        # When no KB is needed the rule returns None and the renderer
        # skips writing the field; the schema's own default kicks in.
        rule='set: f"{project_slug}_knowledge" if has_kb else None',
        invalidated_by=["has_kb", "project_name"],
        pydantic_class="StaticKnowledgeBaseSection",
    ),

    # ── Gated chat: knowledge.blocks.static_knowledge_base.* ─────────────────

    "knowledge.blocks.static_knowledge_base.default_doc_type": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "domain_description"],
        default="general",
        description="Default document type for knowledge base entries.",
        pydantic_class="StaticKnowledgeBaseSection",
    ),
    "knowledge.blocks.static_knowledge_base.intent_filters": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "agent_core.preprocessing.nlu_processor.intents"],
        description="Open map: intent → list of doc_types. Keys must subset nlu_processor.intents.",
        pydantic_class="StaticKnowledgeBaseSection",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("knowledge_engine", FIELD_RULES)
