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


def get_phase_addition(phase: str, available_connectors: list[str] | None = None) -> str:
    """Return schema context to append to the base system prompt for a given phase.

    Injects the YAML template for the relevant block(s) so Claude sees the
    exact field names to use. Values must be filled in; keys must never be
    renamed or invented.

    Args:
        phase: Current conversation phase name.
        available_connectors: Connector names declared in agent_core (used in workflow phase).

    Returns:
        Additional system prompt text for the phase, or empty string if none.
    """
    if phase == "overview":
        return (
            "## Overview phase\n\n"
            "Your goal in this phase: understand the use case well enough to configure all 7 DPG blocks.\n\n"
            "**Required 10-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. overview  — understand the use case (current phase)\n"
            "2. language  — LLM models, language normalisation, NLU intents/entities\n"
            "3. knowledge — RAG knowledge base, persona, document sources\n"
            "4. memory    — session state fields, persistent graph, consent mode\n"
            "5. trust     — blocked phrases, escalation topics, safety guardrails\n"
            "6. connectors — external API connectors (or confirm none needed)\n"
            "7. workflow  — subagent state machine, routing rules\n"
            "8. observability — outcome lifecycle states, metrics, domain name\n"
            "9. reach     — web UI branding (app name, icon, tagline)\n"
            "10. review   — validate, fix missing fields, finalize all blocks\n\n"
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
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- Claude model IDs: section=`agent`, keys: `primary_model`, `fallback_model`, `ask_for_consent`, `consent_prompt`\n"
            "  ❌ NEVER use: agent.name, agent.system_prompt, conversation.llm_model, conversation.language_config\n"
            "- Language normalisation: section=`preprocessing.language_normalisation`, keys: `model`, `provider`, `default_language`, `supported_languages`, `transliteration`, `code_switching`\n"
            "- NLU: section=`preprocessing.nlu_processor`, keys: `model`, `confidence_threshold`, `domain_instruction`, `intents`, `entities`, `sentiment_classes`\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry with the correct key.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent", "preprocessing"])
            + "```\n\n"
            "➡️ When agent model, language normalisation, and NLU are all set, call `set_phase('knowledge')`."
        )

    if phase == "knowledge":
        return (
            "## Knowledge phase — valid fields\n\n"
            "Use `update_config` with block=`knowledge_engine`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('knowledge_engine'))}\n\n"
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
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- Session TTL and fields: section=`state.session`, keys: `ttl_minutes` (integer), `schema` (dict of field→{type, default})\n"
            "  ❌ NEVER use: state.session_ttl_minutes, state.session_fields, state.ttl\n"
            "- Persistent graph: section=`state.persistent`, keys: `backend`, `graph` (with user_node + subnodes), `merge_on_session_end`\n"
            "  ❌ NEVER use: state.persistent_backend, state.graph\n"
            "- Merge rules: included inside `state.persistent` as `merge_on_session_end: [{session_field, target}]`\n"
            "  ❌ NEVER write state.merge_on_session_end at the top state level\n"
            "- Storage mode: section=`user_data_persistence`, only ONE valid key: `default_mode` (saved|anonymous)\n"
            "  ❌ NEVER add user_data_persistence.persistence_backend, .user_identifier, .profile_fields, .merge_on_session_end\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("memory_layer")
            + "```\n\n"
            "➡️ When session schema, persistent graph, and user_data_persistence are set, call `set_phase('trust')`."
        )

    if phase == "trust":
        return (
            "## Trust phase — valid fields\n\n"
            "Use `update_config` with block=`trust_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('trust_layer'))}\n\n"
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + load_template_text("trust_layer")
            + "```\n\n"
            "➡️ When input rules, output rules, and consent phrases are set, call `set_phase('connectors')`."
        )

    if phase == "connectors":
        return (
            "## Connectors phase — valid fields\n\n"
            "There are TWO separate configs to write:\n\n"
            "**1. Agent Core connectors** (tool definitions shown to the LLM — input schema, descriptions):\n"
            "   section=`connectors`, values={read: [...], write: [...], identity: [...], internal: [...]}\n"
            "   Each item: {name, description, input_schema: {type: object, properties: {...}, required: [...]}}\n\n"
            "**2. Action Gateway** (endpoint URLs only — no input schema, no type, no params lists):\n"
            "   block=`action_gateway`, section=`action_gateway`, values={connectors: {connector_name: {endpoint, timeout_ms}}}\n"
            "   ❌ NEVER add: action_gateway.connectors.read, .write, .internal subsections\n"
            "   ❌ NEVER add: authentication, type, required_params, optional_params to action_gateway\n"
            "   The action_gateway ONLY maps connector_name → {endpoint: url, timeout_ms: number}\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "**agent_core connectors section:**\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["connectors"])
            + "```\n\n"
            "**action_gateway template:**\n"
            "```yaml\n"
            + load_template_text("action_gateway")
            + "```\n\n"
            "➡️ When connectors are defined (or confirmed empty), call `set_phase('workflow')`."
        )

    if phase == "workflow":
        connector_note = ""
        if available_connectors:
            connector_note = f"\n\nAvailable connectors (declared in Connectors phase): {', '.join(available_connectors)}"
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
            "4. If preprocessing.nlu_processor.intents was not set, set it now with section=`preprocessing.nlu_processor`.\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below for each subagent:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent_workflow"])
            + "```"
            + connector_note
            + "\n\n"
            + _WORKFLOW_EXAMPLE
            + "\n\n➡️ When all subagents are created, routing rules added, and agent_workflow metadata set, call `set_phase('observability')`."
        )

    if phase == "observability":
        return (
            "## Observability phase — valid fields\n\n"
            "Use `update_config` with block=`observability_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('observability_layer'))}\n\n"
            "**What to collect from the user:**\n"
            "1. **Domain identifier** — a short slug for this deployment, e.g. `kkb`, `fasal_doctor`\n"
            "2. **Outcome lifecycle** — the ordered user journey states for this use case.\n"
            "   Ask: 'What are the key stages a user goes through? (e.g. enquiry → applied → placed)'\n"
            "   The first state has `trigger_tool: null` (set at session start).\n"
            "   Later states have `trigger_tool` = the tool name whose call marks that transition.\n"
            "3. **Custom metrics** — domain-specific OTel counters/gauges to track business outcomes.\n"
            "   Ask: 'What numbers do you want to track? (e.g. total registrations, drop-off rate)'\n"
            "4. **SLI overrides** (optional) — latency or block rate thresholds if different from defaults.\n"
            "5. **Audit retention** (optional) — how many days to keep audit logs (default 90).\n\n"
            "**CRITICAL — exact section paths:**\n"
            "- Domain: section=`observability`, values={domain: 'your_domain_slug'}\n"
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
            "➡️ When domain, lifecycle states, and metrics are set, call `set_phase('reach')`."
        )

    if phase == "reach":
        return (
            "## Reach phase — valid fields\n\n"
            "Use `update_config` with block=`reach_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('reach_layer'))}\n\n"
            "**What to collect from the user — Web UI branding only:**\n"
            "   - `app_name`: App name shown in browser tab and chat header\n"
            "   - `app_tagline`: Short subtitle shown under the app name\n"
            "   - `app_icon`: Emoji representing the app (e.g. 💊 health, 🌾 farming, 💼 jobs)\n"
            "   - `agent_avatar`: Emoji shown on agent chat bubbles (usually same as app_icon)\n"
            "   - `user_avatar`: Emoji shown on user chat bubbles (default '👤', or '👨‍🌾' for farmers)\n"
            "   - `setup_heading`: Heading on the user ID entry screen — include both local language and English\n"
            "   - `setup_subtitle`: Subtitle under the heading — tell user what to enter\n"
            "   - `user_id_placeholder`: Hint inside the user ID field (e.g. 'e.g. ramesh_up')\n"
            "   - `user_id_hint`: Secondary hint below the field (e.g. 'Use your name and village')\n"
            "   - `start_btn_label`: Label on the start button — include local language + English\n"
            "   - `new_session_msg`: First message shown to a brand new user — include local language + English\n"
            "   - `returning_user_msg`: First message shown to a returning user — include local language + English\n"
            "   - `storage_key`: localStorage key for the user ID (snake_case + '_user_id', e.g. 'fasal_doctor_uid')\n"
            "   - `theme_storage_key`: localStorage key for theme (snake_case + '_theme', e.g. 'fasal_doctor_theme')\n\n"
            "**CRITICAL — exact section path:**\n"
            "- section=`ui`, values={all fields above as a single dict}\n"
            "  ❌ NEVER write `agent_core_client` or `memory_layer_client` — those are DPG framework defaults\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("reach_layer")
            + "```\n\n"
            "➡️ When all ui fields are set, call `set_phase('review')`."
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "All configs have been generated. Review the accumulated state above.\n"
            "Check that these required fields are set (fix with update_config if missing):\n"
            "- agent_core: agent.primary_model, agent.fallback_model, preprocessing.language_normalisation.model,\n"
            "  preprocessing.language_normalisation.supported_languages, preprocessing.nlu_processor.model,\n"
            "  preprocessing.nlu_processor.intents, preprocessing.nlu_processor.entities,\n"
            "  agent_workflow.workflow_id, agent_workflow.subagents (at least one with is_start: true)\n"
            "- knowledge_engine: knowledge.blocks.static_knowledge_base.collection_name\n"
            "- memory_layer: state.session, state.persistent.backend, state.persistent.graph.user_node\n"
            "- observability_layer: observability.domain, observability.outcomes.lifecycle (at least one state)\n"
            "- reach_layer: ui.app_name, ui.app_icon, ui.storage_key\n\n"
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
