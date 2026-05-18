"""FIELD_RULES for memory_layer. See catalogue §7.6 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the memory_layer runtime block.

Locked decision #3: state.session.ttl_minutes is gated by is_multi_turn.
Memgraph selective deployment (dropping Memgraph when needs_persistent_user_data=false)
is a deferred enhancement — handled by compose changes only, not FIELD_RULES.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ── Gated chat: state.session.* (catalogue §7.6) ──────────────────────────

    "state.session.ttl_minutes": FieldRule(
        category="chat",
        phase="memory",
        applies_if="is_multi_turn",
        invalidated_by=["is_multi_turn"],
        default=1440,
        description="Session TTL in minutes. Mirror: gt=0, le=10080. KKB uses 2880.",
        pydantic_class="SessionStateConfig",
    ),
    # No default — the LLM proposes a domain-specific session schema in
    # the memory phase (e.g. `location`, `selected_package`, `group_size`)
    # and must call `update_config` to write it. Earlier `default={}`
    # let the skeleton mark the field "answered" with an empty schema,
    # so when the LLM forgot to write its proposal (verified in the
    # Akashvani Concierge edit-style E2E) the phase auto-advanced with
    # nothing useful in the session state.
    "state.session.schema": FieldRule(
        category="chat",
        phase="memory",
        applies_if="is_multi_turn",
        invalidated_by=["is_multi_turn", "is_companion_style", "domain_description"],
        description="Open map of session field definitions (type, values?, default?). Reserved names forbidden.",
        pydantic_class="SessionStateConfig",
    ),

    # ── Predetermined: state.persistent (structural) ──────────────────────────

    "state.persistent": FieldRule(
        category="predetermined",
        rule="set: PersistentStateConfig(...) if needs_persistent_user_data else None",
        invalidated_by=["needs_persistent_user_data"],
        pydantic_class="StateSection",
    ),

    # ── Gated chat: state.persistent.graph.* ──────────────────────────────────

    "state.persistent.graph.user_node.label": FieldRule(
        category="chat",
        phase="memory",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data"],
        default="User",
        description="Memgraph label for the root user node.",
        pydantic_class="PersistentStateConfig",
    ),
    "state.persistent.graph.user_node.key": FieldRule(
        category="chat",
        phase="memory",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data"],
        default="user_id",
        description="Graph node key attribute name for the user node.",
        pydantic_class="PersistentStateConfig",
    ),
    "state.persistent.graph.subnodes": FieldRule(
        category="chat",
        phase="memory",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data", "is_companion_style"],
        default={},
        description="Open map of subnodes hanging off the user node (recursive graph topology).",
        pydantic_class="PersistentStateConfig",
    ),
    # No default — the LLM proposes which session fields should persist
    # into the user's profile at session end. With `default=[]` the
    # skeleton marked it "answered" with empty merge rules, so the
    # proposed profile carry-over was silently lost.
    "state.persistent.merge_on_session_end": FieldRule(
        category="chat",
        phase="memory",
        applies_if="needs_persistent_user_data and is_multi_turn",
        invalidated_by=["needs_persistent_user_data", "is_multi_turn"],
        description="Rules for merging session fields → graph node at session end.",
        pydantic_class="PersistentStateConfig",
    ),

    # ── Predetermined: user_data_persistence.* ────────────────────────────────

    "user_data_persistence.default_mode": FieldRule(
        category="predetermined",
        rule='set: "saved" if needs_persistent_user_data else "anonymous"',
        invalidated_by=["needs_persistent_user_data"],
        pydantic_class="UserDataPersistenceSection",
    ),

    # ── Gated chat: reengagement.triggers ────────────────────────────────────

    "reengagement.triggers": FieldRule(
        category="chat",
        phase="memory",
        applies_if="is_multi_turn and has_external_tools",
        invalidated_by=["is_multi_turn", "selected_channels"],
        default=[],
        description="List of re-engagement triggers (event, delay_hours, channel, message_template, etc.). GH-168: scheduler not yet wired.",
        pydantic_class="ReengagementSection",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("memory_layer", FIELD_RULES)
