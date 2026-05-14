"""FIELD_RULES for trust_layer. See catalogue §7.2 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the trust_layer runtime block.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

# Canonical dignity-check questions (English). Per locked decision #1, these
# are predetermined (English-canonical). Translation at runtime is handled
# by Trust Layer; the wizard does not re-ask for translated variants.
_CANONICAL_DIGNITY_QUESTIONS = [
    "Does this blame the user?",
    "Does it over-promise?",
    "Does it push urgency?",
    "Does it reduce their agency?",
    "Does it sound like a script instead of a human call?",
]

FIELD_RULES: dict[str, FieldRule] = {
    # ── Always-asked chat: trust.* (catalogue §7.2) ───────────────────────────

    "trust.policy_pack": FieldRule(
        category="chat",
        phase="trust",
        description="Active policy pack name. Must be a key in policy_packs.",
        invalidated_by=["is_companion_style", "project_name"],
        pydantic_class="TrustSection",
    ),
    "trust.input_rules.blocked_phrases": FieldRule(
        category="chat",
        phase="trust",
        default=[],
        description="Per-language slang/profanity phrases to block on input.",
        invalidated_by=["supported_languages", "domain_description"],
        pydantic_class="TrustSection",
    ),
    "trust.input_rules.blocked_input_message": FieldRule(
        category="chat",
        phase="trust",
        default="I can't help with that request.",
        description="Message shown when input is blocked. Translation cascade.",
        invalidated_by=["default_language"],
        pydantic_class="TrustSection",
    ),

    # ── Gated chat: trust.input_rules.escalation_topics ──────────────────────

    "trust.input_rules.escalation_topics": FieldRule(
        category="chat",
        phase="trust",
        applies_if="has_hitl",
        invalidated_by=["has_hitl", "domain_description"],
        default=[],
        description="Topics that trigger HiTL escalation before the LLM is called.",
        pydantic_class="TrustSection",
    ),

    # ── Always-asked chat: trust.output_rules.* ──────────────────────────────

    "trust.output_rules.blocked_phrases": FieldRule(
        category="chat",
        phase="trust",
        default=[],
        description="Phrases the LLM output must not contain.",
        invalidated_by=["supported_languages", "domain_description"],
        pydantic_class="TrustSection",
    ),
    "trust.output_rules.output_blocked_message": FieldRule(
        category="chat",
        phase="trust",
        description="Message shown when output is blocked. Required.",
        invalidated_by=["default_language"],
        pydantic_class="TrustSection",
    ),

    # ── Always-asked chat: trust.policy_packs ────────────────────────────────

    "trust.policy_packs": FieldRule(
        category="chat",
        phase="trust",
        default={},
        description="Open map of named policy packs. At least one required if policy_pack is set.",
        invalidated_by=["is_companion_style", "project_name", "domain_description"],
        pydantic_class="TrustSection",
    ),

    # ── Gated chat: trust.consent.* ──────────────────────────────────────────

    "trust.consent.consent_phrases": FieldRule(
        category="chat",
        phase="trust",
        applies_if="needs_consent",
        invalidated_by=["needs_consent", "default_language"],
        description="Phrases counting as opt-in consent from the user.",
        pydantic_class="TrustSection",
    ),
    "trust.consent.decline_phrases": FieldRule(
        category="chat",
        phase="trust",
        applies_if="needs_consent",
        invalidated_by=["needs_consent", "default_language"],
        description="Phrases counting as consent decline from the user.",
        pydantic_class="TrustSection",
    ),

    # ── Gated chat: trust.hitl.holding_message ────────────────────────────────

    "trust.hitl.holding_message": FieldRule(
        category="chat",
        phase="trust",
        applies_if="has_hitl",
        invalidated_by=["has_hitl", "default_language", "supported_languages"],
        default="Please hold while I connect you to an agent.",
        description="Message shown while waiting for a human agent.",
        pydantic_class="TrustSection",
    ),

    # ── Deploy: trust.hitl.* ──────────────────────────────────────────────────

    "trust.hitl.queue_backend": FieldRule(
        category="deploy",
        applies_if="has_hitl",
        invalidated_by=["has_hitl"],
        description="HiTL queue backend (log/redis/webhook). Collected at deploy time.",
        pydantic_class="TrustSection",
    ),
    "trust.hitl.notification_webhook": FieldRule(
        category="deploy",
        applies_if="has_hitl",
        invalidated_by=["has_hitl"],
        description="Webhook URL for HiTL notifications (deploy form; conditional on queue_backend).",
        pydantic_class="TrustSection",
    ),

    # ── Predetermined: dignity_check.* ────────────────────────────────────────

    "dignity_check.enabled": FieldRule(
        category="predetermined",
        rule="set: is_companion_style",
        invalidated_by=["is_companion_style"],
        pydantic_class="DignityCheckSection",
    ),
    "dignity_check.questions": FieldRule(
        category="predetermined",
        rule="set: _CANONICAL_DIGNITY_QUESTIONS if is_companion_style else []",
        invalidated_by=["is_companion_style"],
        pydantic_class="DignityCheckSection",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("trust_layer", FIELD_RULES)
