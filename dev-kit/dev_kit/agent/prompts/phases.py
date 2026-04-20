"""
dev-kit/dev_kit/agent/prompts/phases.py

Phase-specific additions to the system prompt. Each phase injects the
relevant YAML template sections so Claude sees the exact valid field names
and fills in values only — never inventing or renaming keys.
"""
from __future__ import annotations

from dev_kit.schemas.loader import get_valid_sections, load_template_text

_WORKFLOW_EXAMPLE = """
Example subagent (condensed from KKB reference):

  id: greeting
  name: Greeting
  is_start: true
  system_prompt: |
    Welcome the user briefly. Ask for consent to save their profile.
    Respond in the user's language.
  routing:
    - intent: consent_granted
      next_subagent_id: profile_building
      session_writes:
        user_storage_mode: "saved"
    - intent: consent_declined
      next_subagent_id: profile_building
      session_writes:
        user_storage_mode: "anonymous"
    - intent: "*"
      next_subagent_id: profile_building

  id: profile_building
  name: Profile Building
  system_prompt: |
    Collect name, location, and what the user does for work.
    Hard minimum: location + occupation must be known before proceeding.
  routing:
    - intent: profile_complete
      next_subagent_id: main_action
    - intent: "*"
      next_subagent_id: profile_building

  id: main_action
  name: Main Action
  is_terminal: false
  tools: [your_read_connector]
  system_prompt: |
    Deliver the core value of the AI based on the user's profile.
  routing:
    - intent: task_complete
      next_subagent_id: ended
    - intent: "*"
      next_subagent_id: main_action

  id: ended
  name: Ended
  is_terminal: true
  system_prompt: Thank the user and close the session.
  routing: []
"""


def get_phase_addition(phase: str, available_tools: list[str] | None = None) -> str:
    """Return schema context to append to the base system prompt for a given phase.

    Injects the YAML template for the relevant block(s) so Claude sees the
    exact field names to use. Values must be filled in; keys must never be
    renamed or invented.

    Args:
        phase: Current conversation phase name.
        available_tools: Tool IDs declared in the Tools phase (used in workflow phase).

    Returns:
        Additional system prompt text for the phase, or empty string if none.
    """
    if phase == "overview":
        return (
            "## Overview phase\n\n"
            "Your goal in this phase: understand the use case well enough to configure all 7 DPG blocks.\n\n"
            "**Required 11-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. overview  — understand the use case (current phase)\n"
            "2. language  — LLM models, language normalisation, NLU intents/entities\n"
            "3. knowledge — RAG knowledge base, persona, document sources\n"
            "4. memory    — session state fields, persistent graph, consent mode\n"
            "5. user_state — user mental-state model (Conversational agents only; skip otherwise)\n"
            "6. trust     — blocked phrases, escalation topics, safety guardrails\n"
            "7. tools     — external API / MCP tools (or confirm none needed)\n"
            "8. workflow  — subagent state machine, routing rules\n"
            "9. observability — outcome lifecycle states, metrics, domain name\n"
            "10. reach    — web UI branding (app name, icon, tagline)\n"
            "11. review   — validate, fix missing fields, finalize all blocks\n\n"
            "**CRITICAL: you may NOT skip any phase.** set_phase will return an error if you try to jump ahead.\n\n"
            "**What to collect in this phase:**\n"
            "- What problem does this agent solve? Who are the users?\n"
            "- What languages do users speak?\n"
            "- What knowledge/documents will the agent use?\n"
            "- What external APIs are needed (if any)?\n"
            "- What does a successful conversation look like?\n\n"
            "Once you have a clear picture of the use case, call `set_project_meta` to save it, "
            "then call `set_phase('language')` to begin configuration.\n"
            "Do NOT call set_phase('language') until you have asked at least 2-3 clarifying questions "
            "and understood the use case."
        )

    if phase == "language":
        return (
            "## Language & Models phase — valid fields\n\n"
            "Use `update_config` with block=`agent_core`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('agent_core'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`agent_core`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section above. Do NOT ask the user for this value.\n\n"
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- Claude model IDs: section=`agent`, keys: `primary_model`, `fallback_model`, `ask_for_consent`, `consent_prompt`\n"
            "  ❌ NEVER use: agent.name, agent.system_prompt, conversation.llm_model, conversation.language_config\n"
            "- Language normalisation: section=`preprocessing.language_normalisation`, keys: `model`, `provider`, `default_language`, `supported_languages`, `transliteration`, `code_switching`\n"
            "- NLU: section=`preprocessing.nlu_processor`, keys: `model`, `confidence_threshold`, `domain_instruction`, `intents`, `entities`, `sentiment_classes`\n"
            "- Signal intents (optional): section=`preprocessing.nlu_processor`, key: `signal_intents`\n"
            "  Ask: 'Are there intents that should write a longitudinal signal to the context graph? (e.g. pay_disappointment → objection)'\n"
            "  Only set if user confirms — it is a map of {intent_name: signal_type}.\n"
            "- Entity-to-profile map: section=`entity_to_profile_field`\n"
            "  After collecting entities, auto-generate the map (each entity_name maps to itself) and confirm with the user.\n"
            "  e.g. {name: name, location: location, trade_or_stream: trade_or_stream}\n"
            "- Conversation messages: section=`conversation`\n"
            "  Ask: 'What language should system messages be in? (e.g. Hindi, English, Hinglish)'\n"
            "  Then draft all messages in that language and confirm before calling update_config.\n"
            "  Keys: `blocked_message`, `escalation_message`, `output_blocked_message`,\n"
            "  `unknown_intent_message`, `termination_message`, `consent_message`,\n"
            "  `consent_decline_ack`, `unsupported_language_message`, `profile_complete_message`, `returning_user_greeting`\n"
            "- HITL response: section=`hitl`, key: `response_message`\n"
            "  Ask: 'What fixed message should users see when handed off to a human agent?'\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry with the correct key.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + _extract_template_sections(
                "agent_core",
                ["agent", "preprocessing", "conversation", "entity_to_profile_field", "hitl", "observability"],
            )
            + "```\n\n"
            "➡️ When agent model, language normalisation, NLU, conversation messages, entity_to_profile_field, "
            "and hitl.response_message are all set, call `set_phase('knowledge')`."
        )

    if phase == "knowledge":
        return (
            "## Knowledge phase — valid fields\n\n"
            "Use `update_config` with block=`knowledge_engine`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('knowledge_engine'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`knowledge_engine`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- RAG / vector store config: section=`knowledge.blocks.static_knowledge_base`\n"
            "  Keys: `collection_name`, `vector_store`, `top_k`, `similarity_threshold`, `sources` (list), `intent_filters` (dict)\n"
            "  ❌ NEVER write flat keys directly under knowledge: (e.g. knowledge.collection_name, knowledge.top_k)\n"
            "- Persona text: section=`conversation`, values={\"persona\": {\"text\": \"...\"}}\n"
            "  ❌ NEVER use: conversation.assistant_persona, conversation.persona_text\n"
            "- Language instruction: section=`conversation`, key: `language_instruction`\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```\n\n"
            "➡️ When collection_name, persona, and language_instruction are set, call `set_phase('memory')`."
        )

    if phase == "memory":
        return (
            "## Memory phase — valid fields\n\n"
            "Use `update_config` with block=`memory_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('memory_layer'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`memory_layer`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- Session TTL and fields: section=`state.session`, keys: `ttl_minutes` (integer), `schema` (dict of field→{type, default})\n"
            "  ❌ NEVER use: state.session_ttl_minutes, state.session_fields, state.ttl\n"
            "- Persistent graph: section=`state.persistent`, keys: `backend`, `graph` (with user_node + subnodes), `merge_on_session_end`\n"
            "  ❌ NEVER use: state.persistent_backend, state.graph\n"
            "- Merge rules: included inside `state.persistent` as `merge_on_session_end: [{session_field, target}]`\n"
            "  ❌ NEVER write state.merge_on_session_end at the top state level\n"
            "- Storage mode: section=`user_data_persistence`, only ONE valid key: `default_mode` (saved|anonymous)\n"
            "  ❌ NEVER add user_data_persistence.persistence_backend, .user_identifier, .profile_fields, .merge_on_session_end\n"
            "- Re-engagement triggers (optional): section=`reengagement`, key: `triggers`\n"
            "  Ask: 'Should the system re-engage users who dropped off? (e.g. send a WhatsApp follow-up 72 hours after drop-off)'\n"
            "  Each trigger: {event, delay_hours or loop_threshold, channel, message_template or action}\n"
            "  Common channels: outbound_call | whatsapp | sms. Use action: hitl_counsellor for escalation triggers.\n"
            "  Only set if user confirms re-engagement is needed.\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("memory_layer")
            + "```\n\n"
            "➡️ When session schema, persistent graph, and user_data_persistence are set, call `set_phase('user_state')`."
        )

    if phase == "user_state":
        return (
            "## User state phase — valid fields\n\n"
            "This phase is optional and applies to **Conversational** agents only "
            "(per the agent-type selector landing in issue #137). Transactional, "
            "Informational, and Agentic agents should call set_phase('trust') to "
            "skip this phase.\n\n"
            "For Conversational agents (e.g. KKB), you define a user-state model "
            "that describes the user's mental journey — what states they pass "
            "through emotionally and cognitively, what signals indicate each "
            "state, and how the agent should behave in each.\n\n"
            "Use `update_config` with block=`agent_core`, section=`conversation`, "
            "key=`user_state_model`. Schema:\n\n"
            "```yaml\n"
            "conversation:\n"
            "  user_state_model:\n"
            "    enabled: true                # set to true to activate\n"
            "    default_state: \"\"            # required — must match one of the state ids\n"
            "    states:                      # required — non-empty list\n"
            "      - id: \"\"                  # unique snake_case id, e.g. fog\n"
            "        signals: []              # natural-language phrases users say in this state\n"
            "        guidance: \"\"             # required — behaviour text, e.g. 'Orient gently.'\n"
            "```\n\n"
            "Elicit from the domain expert:\n"
            "- Does the agent need to distinguish user mental states? If no → call "
            "set_phase('trust') to skip.\n"
            "- List 2-5 states with short ids (e.g. fog, orientation, evaluation, "
            "commitment, follow-through for KKB).\n"
            "- For each: 2-4 natural-language signals (phrases users say) and 1-3 "
            "sentences of behavioural guidance for the agent.\n"
            "- Which state is the default for a fresh caller?\n\n"
            "Also set `preprocessing.nlu_processor.user_state_confidence_threshold` "
            "if the default (0.4) is not suitable. Use a separate `update_config` "
            "call with block=`agent_core`, section=`preprocessing.nlu_processor`.\n\n"
            "When the model is declared (or the user opts to skip), call "
            "`set_phase('trust')`."
        )

    if phase == "trust":
        return (
            "## Trust phase — valid fields\n\n"
            "Use `update_config` with block=`trust_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('trust_layer'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`trust_layer`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + load_template_text("trust_layer")
            + "```\n\n"
            "➡️ When input rules, output rules, and consent phrases are set, call `set_phase('tools')`."
        )

    if phase == "tools":
        return (
            "## Tools phase\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`action_gateway`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "In this phase you configure the external tools the agent can call via the Action Gateway.\n\n"
            "**Path A — User has an OpenAPI spec:**\n"
            "  1. Ask them to paste or upload it, then call `parse_openapi_spec` to extract candidate tools.\n"
            "  2. Present the candidates and confirm which ones to add.\n"
            "  3. Call `add_rest_api_tool` once per confirmed tool.\n"
            "  4. After adding, go to **After each path** below.\n\n"
            "**Path B — User has an MCP server:**\n"
            "  1. Ask for the MCP server URL and transport type (sse or streamable_http).\n"
            "     Use streamable_http for hosted servers like GitBook, Notion, etc.\n"
            "  2. Call `discover_mcp_tools` to fetch available tools and present the list.\n"
            "  3. Call `add_mcp_tool` ONCE for the server — NOT once per tool.\n"
            "     Choose a short snake_case namespace id (e.g. 'obsrv_docs') that will prefix\n"
            "     all discovered tool names (e.g. 'obsrv_docs.searchDocumentation').\n"
            "  4. Note the namespaced tool names — they are used in subagent tools lists.\n"
            "  5. After adding, go to **After each path** below.\n\n"
            "**Path C — Manual REST API (no spec, no MCP):**\n"
            "  Collect: tool ID, description, base URL, auth type, and at least one endpoint.\n"
            "  Then call `add_rest_api_tool`. After adding, go to **After each path** below.\n\n"
            "**After each path — ALWAYS do this:**\n"
            "  Ask: 'Are there any other tools to add? (OpenAPI spec, MCP server, or describe manually)'\n"
            "  - If yes: repeat the appropriate path above.\n"
            "  - If no: proceed to the completion step.\n\n"
            "**If no external tools are needed at all:**\n"
            "  Confirm with the user and proceed directly.\n\n"
            "REST API tools (`add_rest_api_tool`) automatically create a matching connector\n"
            "in agent_core.connectors — subagents reference these by their bare id.\n"
            "MCP tools (`add_mcp_tool`) do NOT create connectors — tool schemas come from\n"
            "the server at runtime. Subagents reference MCP tools by their namespaced names\n"
            "(e.g. 'obsrv_docs.searchDocumentation'), not the bare adapter id.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("action_gateway")
            + "```\n\n"
            "➡️ When all tools are configured (or confirmed none needed), call `set_phase('workflow')`."
        )

    if phase == "workflow":
        connector_note = ""
        if available_tools:
            connector_note = (
                "\n\nAvailable tools (configured in Tools phase): "
                + ", ".join(available_tools)
                + "\n\nIMPORTANT — tool name format per type:\n"
                "- REST API tools: use the bare id (e.g. 'onest_market_lookup') — a connector entry exists in agent_core.\n"
                "- MCP tools: use '{adapter_id}.{mcp_tool_name}' (e.g. 'obsrv_docs.searchDocumentation') — "
                "no connector entry exists; the MCP adapter discovers tool names at startup. "
                "Use the exact tool names returned by `discover_mcp_tools` prefixed with the adapter id."
            )
        return (
            "## Workflow Design phase\n\n"
            "**CRITICAL — forbidden keys that will cause validation failure:**\n"
            "❌ DO NOT use: agent.name, agent.system_prompt (these don't exist)\n"
            "❌ DO NOT use: agent_workflow.start_subagent, agent_workflow.fallback_subagent\n"
            "✅ USE: agent_workflow.default_fallback_subagent_id for the fallback subagent\n"
            "✅ USE: agent_workflow.agent_system_prompt for the top-level LLM persona\n\n"
            "Build the subagent state machine step by step:\n"
            "1. Use `create_subagent` for each node and `add_routing_rule` for each edge.\n"
            "2. After the graph is built, use `update_config` with section=`agent_workflow` to set:\n"
            "   workflow_id, version, agent_system_prompt, global_intents, global_routing, default_fallback_subagent_id\n"
            "3. If agent.primary_model was not set in the Language phase, set it now with section=`agent`.\n"
            "4. If preprocessing.nlu_processor.intents was not set, set it now with section=`preprocessing.nlu_processor`.\n"
            "5. **Subagent mental state map** — after all subagents are defined, ask:\n"
            "   'Which conversation stage does each subagent represent? (fog / orientation / evaluation / commitment / follow_through)'\n"
            "   Set via: section=`agent_workflow`, values={subagent_mental_state_map: {subagent_id: mental_state, ...}}\n"
            "   This map is used to automatically track the user's mental state in session as routing progresses.\n"
            "6. **Tool result mappings** — only needed if tools return structured lists to persist as graph nodes.\n"
            "   Ask: 'Do any tools return data you want saved to the user's context graph? (e.g. job listings → Role nodes)'\n"
            "   If yes, for each tool collect: tool name, graph node label, dot-path to the list in the result, field mappings.\n"
            "   Set via: section=`agent_workflow`, values={tool_result_mappings: {tool_name: {journey_event_label, result_list_key, field_map}}}\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below for each subagent:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent_workflow"])
            + "```"
            + connector_note
            + "\n\n"
            + _WORKFLOW_EXAMPLE
            + "\n\n➡️ When all subagents, routing rules, agent_workflow metadata, subagent_mental_state_map, "
            "and tool_result_mappings (if applicable) are set, call `set_phase('observability')`."
        )

    if phase == "observability":
        return (
            "## Observability phase — valid fields\n\n"
            "Use `update_config` with block=`observability_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('observability_layer'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`observability_layer`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user for a domain identifier.\n\n"
            "**What to collect from the user:**\n"
            "1. **Outcome lifecycle** — the ordered user journey states for this use case.\n"
            "   Ask: 'What are the key stages a user goes through? (e.g. enquiry → applied → placed)'\n"
            "   The first state has `trigger_tool: null` (set at session start).\n"
            "   Later states have `trigger_tool` = the tool name whose successful call marks that transition.\n"
            "2. **Custom metrics** — domain-specific OTel counters/gauges to track business outcomes.\n"
            "   Ask: 'What numbers do you want to track? (e.g. total applications, drop-off rate by stage)'\n"
            "3. **SLI overrides** (optional) — latency or block rate thresholds if different from defaults.\n"
            "4. **Audit retention** (optional) — how many days to keep audit logs (default 90).\n\n"
            "**CRITICAL — exact section paths:**\n"
            "- Domain: section=`observability`, values={domain: '<project_slug>'} — set automatically in STEP 0\n"
            "- Lifecycle: section=`observability.outcomes`, values={lifecycle: [...]}\n"
            "- Metrics: section=`observability.outcomes`, values={metrics: [...]}\n"
            "- SLI: section=`observability.sli`, values={turn_latency_p99_ms: N, trust_block_rate_max: N}\n"
            "- Audit: section=`observability.audit`, values={retention_days: N}\n"
            "  ❌ NEVER use: observability_layer.outcomes, observability.lifecycle directly\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("observability_layer")
            + "```\n\n"
            "➡️ When lifecycle states and metrics are set, call `set_phase('reach')`."
        )

    if phase == "reach":
        return (
            "## Reach phase — multi-channel deployment\n\n"
            "Use `update_config` with block=`reach_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('reach_layer'))}\n\n"
            "**Step 1 — Channel selection (do this first):**\n"
            "Ask the user which channels they want to deploy on: web, CLI (terminal), voice.\n"
            "Then call `set_reach_channels` with the list (e.g. `['web']` or `['web', 'voice']`).\n\n"
            "**Step 2 — Configure ONLY selected channels:**\n\n"
            "**Web channel** (if selected):\n"
            "  - UI branding: section=`reach_layer.channels.web.ui`\n"
            "    Keys: app_name, app_tagline, app_icon, agent_avatar, user_avatar,\n"
            "    setup_heading, setup_subtitle, user_id_placeholder, user_id_hint,\n"
            "    start_btn_label, new_session_msg, returning_user_msg,\n"
            "    storage_key, theme_storage_key, sign_out_confirm, switch_user_confirm,\n"
            "    delete_conversation_confirm\n"
            "  - Auth (optional): section=`reach_layer.channels.web.auth`\n"
            "    Keys: enabled (bool), google_client_id (str), cookie_secure (bool)\n\n"
            "**CLI channel** (if selected):\n"
            "  - Prompts: section=`reach_layer.channels.cli`\n"
            "    Keys: prompt (e.g. 'You: '), agent_prefix (e.g. 'Agent: ')\n\n"
            "**Voice channel** (if selected):\n"
            "  - STT/TTS: section=`reach_layer.channels.voice.raya`\n"
            "    Keys: stt_language (BCP-47, e.g. 'hi'), tts_language (BCP-47), voice_id\n"
            "  - Agent settings: section=`reach_layer.channels.voice.agent_core`\n"
            "    Keys: timeout_ms (default 15000), greeting (first spoken message), fallback_phrase\n\n"
            "**Step 3 — Configure channel response style (agent_core):**\n"
            "For each selected channel, show the user the default system_prompt_suffix\n"
            "from the agent_core schema and ask if they want to customise it. Then call:\n"
            "  update_config(block=agent_core, section=agent.channels, values={\n"
            "    '<channel>': {'system_prompt_suffix': '...'}\n"
            "  })\n"
            "Only include keys for channels selected in Step 1.\n"
            "Voice default: \"Respond in 1–2 short spoken sentences. No bullet points or markdown.\"\n"
            "Web/CLI default: \"\" (no suffix — full formatting preserved).\n"
            "The user can keep the default or write their own in their domain language.\n\n"
            "**Domain (all channels — auto-set, do NOT ask the user):**\n"
            "  Automatically call `update_config` with:\n"
            "    block=`reach_layer`, section=`reach_layer.common.observability`, values={domain: '<project_slug>'}\n"
            "  Also automatically set the agent_core reach_layer turn-assembler defaults for ONLY the channels selected above:\n"
            "    block=`agent_core`, section=`reach_layer`, values={\n"
            "      turn_assembler: {semantic_gate: {enabled: true, confidence_threshold: 0.75}},\n"
            "      channels: { (include only selected channels) }\n"
            "    }\n"
            "  Use the default silence_ms / max_wait_ms values from the agent_core template for each channel.\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("reach_layer")
            + "```\n\n"
            "➡️ When all selected channels are configured, call `set_phase('review')`."
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "All configs have been generated. Review the accumulated state above.\n"
            "Check that these required fields are set (fix with update_config if missing):\n\n"
            "**agent_core:**\n"
            "- agent.primary_model, agent.fallback_model\n"
            "- conversation.* (all message strings set)\n"
            "- preprocessing.language_normalisation.model, .supported_languages\n"
            "- preprocessing.nlu_processor.model, .intents, .entities\n"
            "- entity_to_profile_field (one entry per entity if entities were defined)\n"
            "- hitl.response_message\n"
            "- agent_workflow.workflow_id, .agent_system_prompt, .subagents (at least one with is_start: true)\n"
            "- agent_workflow.subagent_mental_state_map (if subagents were defined)\n"
            "- observability.domain = project slug\n\n"
            "**knowledge_engine:**\n"
            "- knowledge.blocks.static_knowledge_base.collection_name\n"
            "- conversation.persona.text\n"
            "- observability.domain = project slug\n\n"
            "**memory_layer:**\n"
            "- state.session (ttl_minutes + schema fields)\n"
            "- state.persistent.backend, .graph.user_node\n"
            "- observability.domain = project slug\n\n"
            "**trust_layer:**\n"
            "- trust.input_rules.blocked_phrases, .escalation_topics\n"
            "- trust.policy_pack and trust.policy_packs (at least one pack with guardrails)\n"
            "- trust.consent.consent_phrases, .decline_phrases\n"
            "- trust.hitl.holding_message\n"
            "- observability.domain = project slug\n\n"
            "**observability_layer:**\n"
            "- observability.domain = project slug\n"
            "- observability.outcomes.lifecycle (at least one state)\n"
            "- observability.outcomes.metrics (at least one metric)\n\n"
            "**action_gateway** (if tools were configured):\n"
            "- Each tool: id, type, category, base_url (for rest_api), auth, endpoints\n"
            "- observability.domain = project slug\n\n"
            "**reach_layer:**\n"
            "- Channels selected (call set_reach_channels if missing)\n"
            "- For web: reach_layer.channels.web.ui.app_name, .app_icon, .storage_key\n"
            "- For voice: reach_layer.channels.voice.raya.stt_language\n"
            "- reach_layer.common.observability.domain = project slug\n\n"
            "**Auto-set check** — verify that all `observability.domain` fields equal the project slug.\n"
            "If any are missing or wrong, call update_config with section=`observability`, values={domain: '<slug>'}.\n\n"
            "Call `finalize_config` for each block that is complete.\n"
            "The user can now view configs in the dashboard and edit them directly."
        )

    return ""


def _extract_template_sections(block: str, sections: list[str]) -> str:
    """Extract specific top-level sections from a YAML template as a string.

    Reads the template file and returns only the lines belonging to the
    requested top-level sections, preserving comments.

    Args:
        block: Block name.
        sections: List of top-level section names to extract.

    Returns:
        YAML string containing only the requested sections.
    """
    full_text = load_template_text(block)
    lines = full_text.splitlines()

    result_lines: list[str] = []
    current_section: str | None = None
    in_target = False

    for line in lines:
        # Detect top-level section headers (non-indented keys)
        if line and not line.startswith(" ") and not line.startswith("\t") and not line.startswith("#"):
            key = line.split(":")[0].strip()
            current_section = key
            in_target = key in sections

        if in_target:
            result_lines.append(line)
        elif current_section not in sections and line.startswith("#") and not result_lines:
            # Skip file-level header comments before we've entered a target section
            pass

    return "\n".join(result_lines) + "\n"
