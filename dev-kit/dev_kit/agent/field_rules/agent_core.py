"""FIELD_RULES for agent_core. See catalogue §7.1 for the source of truth.

Path syntax: dotted, with ``[name=X]``/``[id=X]`` for list-of-objects.
Categories per design §5: predetermined | chat | deploy | derived |
framework_default_only.

This module is part of the dev-kit deterministic wizard for the DPG framework.
It encodes the domain-half field disposition for the agent_core runtime block.
"""
from __future__ import annotations

from dev_kit.agent.field_rules import FieldRule, register_block_rules

FIELD_RULES: dict[str, FieldRule] = {
    # ── Always-asked chat: agent.* (catalogue §3.1 + §7.1) ───────────────────

    "agent.primary_model": FieldRule(
        category="chat",
        phase="language",
        description="LLM model for the main loop. Must match provider; cannot equal fallback_model.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),
    "agent.fallback_model": FieldRule(
        category="chat",
        phase="language",
        description="Fallback LLM model. Must match provider; cannot equal primary_model.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),
    "agent.provider": FieldRule(
        category="chat",
        phase="language",
        default="anthropic",
        description="LLM provider. Switching invalidates the two model fields.",
        deploy_overridable=True,
        pydantic_class="AgentSection",
    ),

    # ── Gated chat: agent.* ───────────────────────────────────────────────────

    "agent.consent_prompt": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_consent",
        invalidated_by=["needs_consent", "default_language", "supported_languages"],
        description="Consent prompt shown to the user.",
        pydantic_class="AgentSection",
    ),

    # ── Predetermined: agent.* ────────────────────────────────────────────────

    "agent.ask_for_consent": FieldRule(
        category="predetermined",
        rule="set: needs_consent",
        invalidated_by=["needs_consent"],
        pydantic_class="AgentSection",
    ),

    # ── Always-asked chat: conversation.* (catalogue §7.1) ───────────────────

    "conversation.blocked_message": FieldRule(
        category="chat",
        phase="language",
        description="Message shown when input is blocked by trust layer.",
        invalidated_by=["default_language", "supported_languages"],
        pydantic_class="ConversationSection",
    ),
    "conversation.escalation_message": FieldRule(
        category="chat",
        phase="language",
        description="Message shown when escalating to HiTL. Re-phrased if has_hitl.",
        invalidated_by=["default_language", "supported_languages", "has_hitl"],
        pydantic_class="ConversationSection",
    ),
    "conversation.output_blocked_message": FieldRule(
        category="chat",
        phase="language",
        description="Message shown when output is blocked by trust layer.",
        invalidated_by=["default_language", "supported_languages"],
        pydantic_class="ConversationSection",
    ),
    "conversation.unknown_intent_message": FieldRule(
        category="chat",
        phase="language",
        description="Message shown when intent cannot be determined.",
        invalidated_by=["default_language", "supported_languages"],
        pydantic_class="ConversationSection",
    ),
    "conversation.unsupported_language_message": FieldRule(
        category="chat",
        phase="language",
        description="Message shown when user language is not in supported_languages. Enumerates supported_languages.",
        invalidated_by=["default_language", "supported_languages"],
        pydantic_class="ConversationSection",
    ),

    # ── Gated chat: conversation.* ────────────────────────────────────────────

    "conversation.termination_message": FieldRule(
        category="chat",
        phase="language",
        applies_if="is_multi_turn",
        invalidated_by=["default_language", "supported_languages"],
        description="Message shown when session terminates. Used by termination_short_circuit.",
        pydantic_class="ConversationSection",
    ),
    # NOTE: `conversation.consent_message` is a legacy alt path on the
    # runtime schema (used to be read by the orchestrator; now reads
    # `agent.consent_prompt` exclusively at orchestrator.py:535 / 2814).
    # Keeping it as a chat field caused the LLM to ask for / write the
    # consent text twice (once at agent.consent_prompt in language phase,
    # once at conversation.consent_message in the same phase), polluting
    # the rendered YAML with duplicate copy. Removed from FIELD_RULES so
    # the wizard surfaces it only once. The mirror still accepts the
    # field if a future runtime change starts reading it.
    "conversation.consent_decline_ack": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_consent",
        invalidated_by=["needs_consent", "default_language", "supported_languages"],
        description="Acknowledgement shown when user declines consent.",
        pydantic_class="ConversationSection",
    ),
    "conversation.profile_complete_message": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data", "default_language", "supported_languages"],
        description="Message shown when the persistent user profile is complete.",
        pydantic_class="ConversationSection",
    ),
    "conversation.returning_user_greeting": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data", "default_language", "supported_languages"],
        description="Greeting shown to returning users with a known profile.",
        pydantic_class="ConversationSection",
    ),

    # ── Predetermined: conversation.user_state_model.* ───────────────────────

    "conversation.user_state_model.enabled": FieldRule(
        category="predetermined",
        rule="set: is_companion_style",
        invalidated_by=["is_companion_style"],
        pydantic_class="ConversationSection",
    ),

    # ── Gated chat: conversation.user_state_model.* ───────────────────────────

    "conversation.user_state_model.default_state": FieldRule(
        category="chat",
        phase="user_state",
        applies_if="is_companion_style",
        invalidated_by=["is_companion_style"],
        description="Initial user state ID. Must be in states[].id.",
        pydantic_class="ConversationSection",
    ),
    "conversation.user_state_model.states": FieldRule(
        category="chat",
        phase="user_state",
        applies_if="is_companion_style",
        invalidated_by=["is_companion_style", "default_language"],
        description="List of user state definitions (id, signals, guidance).",
        pydantic_class="ConversationSection",
    ),

    # ── Predetermined: conversation.session_end_eval.* ────────────────────────

    "conversation.session_end_eval.enabled": FieldRule(
        category="predetermined",
        rule='set: "voice" in selected_channels',
        invalidated_by=["selected_channels"],
        pydantic_class="ConversationSection",
    ),

    # ── Gated chat: conversation.session_end_eval.* ───────────────────────────

    "conversation.session_end_eval.prompt": FieldRule(
        category="chat",
        phase="language",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Prompt for LLM-based session-end signal evaluation.",
        pydantic_class="ConversationSection",
    ),

    # ── Gated chat: connectors.* ──────────────────────────────────────────────

    # Category-level defaults: write/identity stay `default=[]` because
    # most projects don't need every category, and `add_tool` flips the
    # answered status when a tool of that category is registered.
    # `connectors.read` keeps `default=[]` for the same reason — but
    # paired with `action_gateway.tools` (no default), the tools phase
    # stays open until the LLM actually registers a tool. Without that
    # pairing the wizard would skip past tools entirely on the strength
    # of skeleton defaults alone (verified in the Akashvani Concierge
    # E2E: dispatch rejected the same-turn add_tool, but `tools` had
    # default=[] so the phase advanced with zero tools registered).
    "connectors.read": FieldRule(
        category="chat",
        phase="tools",
        applies_if="has_external_tools",
        invalidated_by=["has_external_tools", "default_language"],
        default=[],
        description="List of read connectors exposed to the LLM.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.write": FieldRule(
        category="chat",
        phase="tools",
        applies_if="has_external_tools",
        invalidated_by=["has_external_tools", "default_language"],
        default=[],
        description="List of write connectors exposed to the LLM. Consent gate at runtime.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.identity": FieldRule(
        category="chat",
        phase="tools",
        applies_if="has_external_tools",
        invalidated_by=["has_external_tools", "default_language"],
        default=[],
        description="List of identity connectors exposed to the LLM. Consent gate at runtime.",
        pydantic_class="ConnectorsSection",
    ),

    # ── Predetermined: connectors.internal[name=knowledge_retrieval] ──────────

    "connectors.internal[name=knowledge_retrieval]": FieldRule(
        category="predetermined",
        rule="set: InternalConnectorDef(name='knowledge_retrieval', route='knowledge_engine', ...) if has_kb else None",
        invalidated_by=["has_kb"],
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].name": FieldRule(
        category="predetermined",
        rule="set: 'knowledge_retrieval'",
        applies_if="has_kb",
        invalidated_by=["has_kb"],
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].route": FieldRule(
        category="predetermined",
        rule="set: 'knowledge_engine'",
        applies_if="has_kb",
        invalidated_by=["has_kb"],
        pydantic_class="ConnectorsSection",
    ),
    # MUST match runtime InputSchema shape exactly:
    # `{type: "object", properties: {<param>: {type: ...}}, required: [...]}`.
    # Earlier the rule wrote `{'query': {'type': 'string'}}` directly, which
    # the lenient mirror (`dict[str, Any]`) accepted but the runtime's
    # strict InputSchema(extra="forbid") rejected at boot:
    #   "connectors.internal.0.input_schema.query
    #    Extra inputs are not permitted"
    # The mirror has since been tightened to use the strict InputSchema
    # class (see dev_kit/schemas/domain/agent_core.py), so future shape
    # drift is caught at chat time, not at deploy.
    "connectors.internal[name=knowledge_retrieval].input_schema": FieldRule(
        category="predetermined",
        rule=(
            "set: {"
            "'type': 'object', "
            "'properties': {'query': {'type': 'string'}}, "
            "'required': ['query']"
            "}"
        ),
        applies_if="has_kb",
        invalidated_by=["has_kb"],
        pydantic_class="ConnectorsSection",
    ),

    # ── Gated chat: connectors.internal[name=knowledge_retrieval].* ──────────

    "connectors.internal[name=knowledge_retrieval].description": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "domain_description"],
        description="LLM-visible description for the knowledge_retrieval tool.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.call_when": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="Instruction for when the LLM must call knowledge_retrieval.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="Information the LLM must gather before calling knowledge_retrieval.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="What the LLM must not substitute for knowledge_retrieval results.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="LLM behaviour when knowledge_retrieval returns empty results.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="LLM behaviour when knowledge_retrieval fails.",
        pydantic_class="ConnectorsSection",
    ),
    "connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line": FieldRule(
        category="chat",
        phase="knowledge",
        applies_if="has_kb",
        invalidated_by=["has_kb", "default_language"],
        description="Bridging line the LLM uses when transitioning to knowledge content.",
        pydantic_class="ConnectorsSection",
    ),

    # ── Always-asked chat: preprocessing.language_normalisation.* ────────────

    "preprocessing.language_normalisation.enabled": FieldRule(
        category="chat",
        phase="language",
        default=True,
        description="Toggle the leading language-normalisation LLM call.",
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.language_normalisation.provider": FieldRule(
        category="chat",
        phase="language",
        default=None,
        description="Provider for language normalisation helper; None inherits agent.provider.",
        invalidated_by=["agent.provider"],
        pydantic_class="PreprocessingSection",
        # Same as nlu_processor.provider — `None` IS the inherit signal,
        # so skeleton marks answered without writing a value.
        auto_answer=True,
    ),
    "preprocessing.language_normalisation.model": FieldRule(
        category="chat",
        phase="language",
        default="",
        description="Model for language normalisation; empty inherits agent.primary_model.",
        invalidated_by=["preprocessing.language_normalisation.provider", "agent.provider"],
        pydantic_class="PreprocessingSection",
    ),

    # ── Predetermined: preprocessing.language_normalisation.* ────────────────

    "preprocessing.language_normalisation.default_language": FieldRule(
        category="predetermined",
        rule="set: default_language",
        invalidated_by=["default_language"],
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.language_normalisation.supported_languages": FieldRule(
        category="predetermined",
        rule="set: supported_languages",
        invalidated_by=["supported_languages"],
        pydantic_class="PreprocessingSection",
    ),

    # ── Always-asked chat: preprocessing.nlu_processor.* ─────────────────────

    "preprocessing.nlu_processor.provider": FieldRule(
        category="chat",
        phase="language",
        default=None,
        description="Provider for NLU classifier helper; None inherits agent.provider.",
        invalidated_by=["agent.provider"],
        pydantic_class="PreprocessingSection",
        # `None` (absent) is the meaningful "inherit agent.provider" signal.
        # Skeleton marks this answered without writing — nothing for the
        # LLM or user to add.
        auto_answer=True,
    ),
    "preprocessing.nlu_processor.model": FieldRule(
        category="chat",
        phase="language",
        default="",
        description="Model for NLU classifier; empty inherits agent.primary_model.",
        invalidated_by=["preprocessing.nlu_processor.provider", "agent.provider"],
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.domain_instruction": FieldRule(
        category="chat",
        phase="language",
        description="Multi-paragraph NLU classifier domain instruction.",
        invalidated_by=["domain_description", "project_name", "default_language"],
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.intents": FieldRule(
        category="chat",
        phase="language",
        description="List of NLU intent names. Required (min_length=1).",
        invalidated_by=["has_kb", "has_external_tools", "is_multi_turn", "needs_consent", "domain_description"],
        pydantic_class="PreprocessingSection",
    ),
    "preprocessing.nlu_processor.entities": FieldRule(
        category="chat",
        phase="language",
        description="List of entity names. Co-domain with entity_to_profile_field.",
        invalidated_by=["domain_description", "needs_persistent_user_data"],
        pydantic_class="PreprocessingSection",
    ),

    # ── Gated chat: preprocessing.nlu_processor.signal_intents ───────────────

    "preprocessing.nlu_processor.signal_intents": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data", "preprocessing.nlu_processor.intents"],
        default={},
        description="Open map of intent → profile-signal. Keys must subset intents.",
        pydantic_class="PreprocessingSection",
    ),

    # ── Gated chat: entity_to_profile_field ───────────────────────────────────

    "entity_to_profile_field": FieldRule(
        category="chat",
        phase="language",
        applies_if="needs_persistent_user_data",
        invalidated_by=["needs_persistent_user_data", "preprocessing.nlu_processor.entities"],
        default={},
        description="Open map: NLU entity → Memory profile field. Bridges NLU → Memory.",
        pydantic_class="EntityToProfileFieldSection",
    ),

    # ── Gated chat: hitl.response_message ────────────────────────────────────

    "hitl.response_message": FieldRule(
        category="chat",
        phase="language",
        applies_if="has_hitl",
        invalidated_by=["has_hitl", "default_language", "supported_languages"],
        description="Message the agent speaks when escalating to human. Required (min_length=1).",
        pydantic_class="HitlSection",
    ),

    # ── Derived: agent_workflow.workflow_id ──────────────────────────────────

    "agent_workflow.workflow_id": FieldRule(
        category="derived",
        compute='f"{project_slug}_workflow"',
        pydantic_class="AgentWorkflowSection",
    ),

    # `agent_workflow.version` is required by the runtime MergedConfig
    # (defaults to "1.0.0"). Without a FIELD_RULE entry the skeleton
    # never writes it, deploy-validate reports "version: Field required",
    # and the LLM scrambled to ask the user for a version number in the
    # review phase — confusing for end users. Default to "1.0.0" and
    # auto_answer so the field doesn't block phase advancement.
    "agent_workflow.version": FieldRule(
        category="chat",
        phase="workflow",
        default="1.0.0",
        description="Workflow version tag — used for cross-deployment auditing. Default '1.0.0'.",
        pydantic_class="AgentWorkflowSection",
    ),

    # ── Always-asked chat: agent_workflow.* ──────────────────────────────────

    "agent_workflow.agent_system_prompt": FieldRule(
        category="chat",
        phase="workflow",
        description="Persona prompt for the agent. Required (min_length=1).",
        invalidated_by=["domain_description", "default_language", "supported_languages", "is_companion_style"],
        pydantic_class="AgentWorkflowSection",
    ),
    "agent_workflow.global_intents": FieldRule(
        category="chat",
        phase="workflow",
        default=[],
        description="Global intent list. Subset of nlu_processor.intents; disjoint with subagent valid_intents.",
        invalidated_by=["preprocessing.nlu_processor.intents", "is_multi_turn"],
        pydantic_class="AgentWorkflowSection",
    ),
    "agent_workflow.global_routing": FieldRule(
        category="chat",
        phase="workflow",
        default=[],
        description="Global routing rules (intent → next_subagent_id). Per rule: intent, next_subagent_id, conditions, session_writes.",
        invalidated_by=["agent_workflow.global_intents", "agent_workflow.subagents"],
        pydantic_class="AgentWorkflowSection",
    ),
    "agent_workflow.default_fallback_subagent_id": FieldRule(
        category="chat",
        phase="workflow",
        description="ID of the fallback subagent. Must reference a declared subagent.",
        invalidated_by=["agent_workflow.subagents"],
        pydantic_class="AgentWorkflowSection",
    ),
    "agent_workflow.global_tools": FieldRule(
        category="chat",
        phase="workflow",
        default=[],
        description="Global tool names. Must subset connector names + MCP tools. Includes knowledge_retrieval iff has_kb.",
        invalidated_by=["has_kb", "has_external_tools", "connectors.read", "connectors.internal"],
        pydantic_class="AgentWorkflowSection",
    ),
    "agent_workflow.subagents": FieldRule(
        category="chat",
        phase="workflow",
        description="List of subagents. Required (min_length=1). Exactly one is_start=true.",
        invalidated_by=["has_kb", "has_external_tools", "is_multi_turn", "is_companion_style", "needs_persistent_user_data", "domain_description"],
        pydantic_class="AgentWorkflowSection",
    ),

    # ── Always-asked chat: channels.web.* ────────────────────────────────────

    "channels.web.system_prompt_suffix": FieldRule(
        category="chat",
        phase="language",
        description="System prompt suffix for web channel.",
        invalidated_by=["default_language", "supported_languages"],
        pydantic_class="ChannelsSection",
    ),
    "channels.web.turn_assembler.silence_trigger.silence_ms": FieldRule(
        category="chat",
        phase="reach",
        default=0,
        description="Silence trigger threshold in ms for web channel TurnAssembler. Web is direct mode.",
        pydantic_class="ChannelsSection",
    ),
    "channels.web.turn_assembler.max_wait_ceiling.max_wait_ms": FieldRule(
        category="chat",
        phase="reach",
        description="Max wait ceiling in ms for web channel TurnAssembler.",
        pydantic_class="ChannelsSection",
    ),

    # ── Gated chat: channels.voice.* ─────────────────────────────────────────

    "channels.voice.system_prompt_suffix": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="System prompt suffix for voice channel.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.numbers": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for numbers.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.money": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for monetary amounts.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.dates": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for dates.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.time": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for time expressions.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.phone": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for phone numbers.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.abbreviations": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for abbreviations.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.output_script": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for output script/transliteration.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.english_loanwords": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for English loanwords in other-language output.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.email": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for email addresses.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.tts_rules.named_entities": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="TTS rendering rule for named entities.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.terminal_word": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels", "default_language"],
        description="Voice terminal word that signals end of agent turn.",
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.turn_assembler.semantic_gate": FieldRule(
        category="chat",
        phase="reach",
        applies_if='"voice" in selected_channels',
        invalidated_by=["selected_channels"],
        # Must be a structured SemanticGateConfig dict — bare strings or
        # free-form maps are rejected by the strict mirror class:
        #   `{"enabled": true, "confidence_threshold": 0.75}`
        # See dev_kit/schemas/domain/agent_core.py SemanticGateConfig.
        description=(
            "Semantic gate for voice TurnAssembler. Shape: "
            '{"enabled": bool, "confidence_threshold": 0.0-1.0}.'
        ),
        pydantic_class="SemanticGateConfig",
    ),

    # ── Predetermined: channels.voice.turn_assembler.* ────────────────────────

    "channels.voice.turn_assembler.silence_trigger.silence_ms": FieldRule(
        category="predetermined",
        rule='set: 600 if "voice" in selected_channels else None',
        invalidated_by=["selected_channels"],
        pydantic_class="ChannelsSection",
    ),
    "channels.voice.turn_assembler.max_wait_ceiling.max_wait_ms": FieldRule(
        category="predetermined",
        rule='set: 8000 if "voice" in selected_channels else None',
        invalidated_by=["selected_channels"],
        pydantic_class="ChannelsSection",
    ),

    # ── Derived: observability.domain ────────────────────────────────────────

    "observability.domain": FieldRule(
        category="derived",
        compute="slug(project_name)",
        pydantic_class="ObservabilitySection",
    ),
}

register_block_rules("agent_core", FIELD_RULES)
