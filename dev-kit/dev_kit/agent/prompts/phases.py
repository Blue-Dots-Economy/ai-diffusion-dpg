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
    if phase == "tier":
        return (
            "## Tier phase — classify the agent type\n\n"
            "Before diving into configuration, we classify your agent into one of "
            "four types. This determines which of the subsequent phases are Required, "
            "Optional, or Skipped for your project.\n\n"
            "Ask the user these 4 questions **in order**, one at a time:\n\n"
            "**Q1.** Does the agent take any action — an API call, form submission, or "
            "system write?\n"
            "- NO → go to Q2.\n"
            "- YES → go to Q3.\n\n"
            "**Q2.** Does it answer questions from a defined knowledge source?\n"
            "- YES → Informational agent. Call `set_agent_type('informational')`.\n"
            "- NO → Reconsider scope. A passive listener is not an agent. Pause and "
            "escalate to the user.\n\n"
            "**Q3.** Is the task a single defined flow (book / check / submit) with a "
            "clear end state?\n"
            "- YES → Transactional agent. Call `set_agent_type('transactional')`.\n"
            "- NO → go to Q4.\n\n"
            "**Q4.** Does the agent need to hold context across turns, navigate "
            "trade-offs, or respond to emotional state?\n"
            "- YES → Conversational agent. Call `set_agent_type('conversational')`.\n"
            "- NO → Agentic agent. Call `set_agent_type('agentic')`.\n\n"
            "Once you call `set_agent_type`, advance with `set_phase('overview')`."
        )

    if phase == "overview":
        return (
            "## Overview phase\n\n"
            "Your goal in this phase: understand the use case well enough to configure all 7 DPG blocks.\n\n"
            "**Required 12-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. tier        — classify the agent type (already done before overview)\n"
            "2. overview    — understand the use case (current phase)\n"
            "3. language    — LLM models, language normalisation, NLU intents/entities\n"
            "4. knowledge   — RAG knowledge base, persona, document sources\n"
            "5. memory      — session state fields, persistent graph, consent mode\n"
            "6. user_state  — user mental-state model (Conversational only; skip otherwise)\n"
            "7. trust       — blocked phrases, escalation topics, safety guardrails\n"
            "8. tools       — external API / MCP tools (or confirm none needed)\n"
            "9. workflow    — subagent state machine, routing rules\n"
            "10. observability — outcome lifecycle states, metrics, domain name\n"
            "11. reach      — channels, TTS rules, terminal word\n"
            "12. review     — validate, fix missing fields, finalize all blocks\n\n"
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
            "## Language & TTS phase\n\n"
            "**What this phase is about:** Set the agent's primary + fallback LLM, "
            "configure language normalisation and NLU classification, declare "
            "conversation-level messages, and — for voice agents — TTS normalisation "
            "rules and the terminal word for call end.\n\n"
            "**Why it matters:** Every downstream phase assumes language + NLU are "
            "wired. Voice agents are especially sensitive — TTS engines do not reliably "
            "speak raw numbers, dates, or Roman-script Hindi; you must specify rules "
            "the LLM follows before responses reach TTS.\n\n"
            "### What to include (from guide §2.10 Language & TTS Rules)\n"
            "- Primary and fallback Claude model IDs (agent.primary_model, fallback_model)\n"
            "- Default language + supported languages for language normalisation\n"
            "- NLU classifier model + intents/entities/sentiment classes\n"
            "- Conversation-level messages (blocked_message, consent_message, etc.) in "
            "the target language\n"
            "- **Voice only:** TTS rules per data type (numbers, money, dates, time, "
            "phone, abbreviations, output script, English loanwords) under "
            "`channels.voice.tts_rules`\n"
            "- **Voice only:** `channels.voice.terminal_word` — the literal word that "
            "signals call end (e.g. \"Goodbye\"). Required for voice.\n\n"
            "### How the dev-kit captures this\n"
            "- Set models + consent: `update_config(block=agent_core, section=agent, "
            "values={primary_model: ..., fallback_model: ..., ask_for_consent: ..., "
            "consent_prompt: ...})`\n"
            "- Set language normalisation: `section=preprocessing.language_normalisation`\n"
            "- Set NLU: `section=preprocessing.nlu_processor`\n"
            "- Set conversation messages: `section=conversation` (all message keys)\n"
            "- Set entity-to-profile map: `section=entity_to_profile_field`\n"
            "- Set HITL response: `section=hitl, values={response_message: ...}`\n"
            "- Auto-set observability domain: `section=observability, values={domain: "
            "'<project_slug>'}`\n"
            "- **Voice only** — set TTS rules + terminal word: `section=channels, "
            "values={voice: {tts_rules: {...}, terminal_word: 'Goodbye'}}`. "
            "You may draft the TTS rules from the canonical language defaults and "
            "offer `\"draft them for me\"` to the user.\n\n"
            "### Guide gap — DPG-specific fields not in the guide\n"
            "- `signal_intents` (map of intent → signal type for longitudinal context-"
            "graph writes). Ask: 'Are there intents that should write a longitudinal "
            "signal to the context graph?'\n"
            "- `user_state_confidence_threshold` (GH-139) — set only for "
            "Conversational agents during the user_state phase; default 0.4 works.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + _extract_template_sections(
                "agent_core",
                ["agent", "preprocessing", "conversation", "entity_to_profile_field",
                 "hitl", "observability", "channels"],
            )
            + "```\n\n"
            "➡️ When models, language normalisation, NLU, conversation messages, "
            "entity_to_profile_field, hitl.response_message, and (voice only) "
            "channels.voice.{tts_rules, terminal_word} are all set, call "
            "`set_phase('knowledge')`."
        )

    if phase == "knowledge":
        return (
            "## Knowledge Base phase\n\n"
            "**What this phase is about:** Configure the RAG knowledge base that the "
            "agent queries when the LLM invokes the `knowledge_retrieval` internal "
            "tool.\n\n"
            "**Per-type requirement:** "
            "Informational = REQUIRED. Agentic / Conversational = OPTIONAL (only if the "
            "agent has a KB attached). Transactional = SKIP.\n\n"
            "### What to include (from guide §2.7 Knowledge Base Usage Rules)\n"
            "- Define the KB scope — what it contains and what it explicitly does NOT.\n"
            "- Confidence rules: what the agent does when the KB has a clear answer / "
            "partial answer / no answer / conflicting answers.\n"
            "- Citation behaviour: does the agent cite sources, or speak naturally? "
            "Formal/regulated domains cite; conversational domains speak naturally.\n"
            "- KB-to-agent boundary: the agent INTERPRETS and speaks; it must never "
            "read KB entries verbatim.\n\n"
            "### How the dev-kit captures this\n"
            "- Set RAG config: `update_config(block=knowledge_engine, "
            "section=knowledge.blocks.static_knowledge_base, values={...})`\n"
            "- Set persona + language: `section=persona`, `section=language_instruction`\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- `intent_filters` (per-intent document retrieval scoping) is DPG-specific "
            "and not covered by the guide.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```\n\n"
            "➡️ When collection_name, persona, and language_instruction are set, call "
            "`set_phase('memory')`."
        )

    if phase == "memory":
        return (
            "## Memory & Session State phase\n\n"
            "**What this phase is about:** Define what the agent remembers across "
            "turns (session scope), across sessions (persistent graph), and what "
            "contact memory fields are available at call start.\n\n"
            "### What to include (from guide §3.3 Contact Memory & Session State)\n"
            "- Session memory schema: fields and TTL.\n"
            "- Persistent graph node types and merge rules.\n"
            "- User data persistence mode: saved | anonymous.\n"
            "- **Conversational agents** must cover all 5 contact-memory states in "
            "their subagent graph later (during the workflow phase):\n"
            "    - `new` (no memory)\n"
            "    - `sparse` (location only)\n"
            "    - `rich` (location + trade/topic)\n"
            "    - `mid-journey` (options presented, decision pending)\n"
            "    - `post-application` (action taken, checking back in)\n"
            "  Use this phase to define which memory fields populate which state.\n"
            "- Re-engagement triggers (optional): if the agent should follow up with "
            "users who dropped off (WhatsApp, SMS, outbound call).\n\n"
            "### How the dev-kit captures this\n"
            "- Session schema: `update_config(block=memory_layer, section=state.session, "
            "values={ttl_minutes: ..., schema: {...}})`\n"
            "- Persistent graph: `section=state.persistent, values={...}`\n"
            "- Storage mode: `section=user_data_persistence, values={default_mode: saved|anonymous}`\n"
            "- Re-engagement: `section=reengagement, values={triggers: [...]}`\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- `merge_on_session_end`, `context_graph` node types, and re-engagement "
            "triggers are DPG-specific.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("memory_layer")
            + "```\n\n"
            "➡️ When session schema, persistent graph, user_data_persistence, and "
            "reengagement (if needed) are set, call `set_phase('user_state')`."
        )

    if phase == "user_state":
        return (
            "## User State phase\n\n"
            "**What this phase is about:** Define the user's mental journey — the "
            "cognitive/emotional states they pass through (e.g. Fog → Orientation → "
            "Evaluation → Commitment → Follow-through) and how the agent should "
            "behave in each.\n\n"
            "**Per-type requirement:** Conversational = REQUIRED. All other types = "
            "SKIP (auto-advanced by set_phase). This phase shapes the user's "
            "conversational experience, not just what data is captured.\n\n"
            "### What to include (from guide §2.5 Conversation State Model)\n"
            "- List 2-5 states with short ids (e.g. fog, orientation, evaluation, "
            "commitment, follow-through for a job-market advisor).\n"
            "- For each state: natural-language signals (phrases users say in that "
            "state) and behavioural guidance for the agent (2-3 sentences).\n"
            "- Which state is the DEFAULT for a fresh caller?\n\n"
            "### How the dev-kit captures this\n"
            "- Declare states: `update_config(block=agent_core, section=conversation, "
            "values={user_state_model: {enabled: true, default_state: ..., states: [...]}})`\n"
            "- Set threshold (GH-139): `section=preprocessing.nlu_processor, "
            "values={user_state_confidence_threshold: 0.4}` (default 0.4; usually fine).\n\n"
            "### Guide gap\n"
            "- Sticky fallback on low-confidence classification is a DPG-specific "
            "mechanism (GH-139) — the guide describes the state model but not how "
            "confidence-thresholded classification handles ambiguous turns.\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["conversation"])
            + "```\n\n"
            "➡️ When the model is declared, call `set_phase('trust')`."
        )

    if phase == "trust":
        return (
            "## Trust phase\n\n"
            "**What this phase is about:** Configure the safety gate — blocked "
            "content rules, prohibited language, topic firewall, escalation rules, "
            "and (for Conversational) the pre-response dignity check.\n\n"
            "### What to include\n"
            "- **All types:** Content rules, blocked phrases, escalation topics.\n"
            "- **Conversational:** `dignity_check` with the 5 canonical questions "
            "(auto-populated; you can override per domain). Flags `enabled: true`.\n"
            "- Prohibited language list (guide §2.11 Style & Prohibited). Include "
            "specific phrases, not just categories.\n\n"
            "### Canonical dignity check questions (Conversational only)\n"
            "1. Does this blame the user?\n"
            "2. Does it over-promise?\n"
            "3. Does it push urgency?\n"
            "4. Does it reduce their agency?\n"
            "5. Does it sound like a script instead of a human call?\n\n"
            "The dev-kit auto-emits these into `trust_layer.dignity_check.questions` "
            "when `agent_type=conversational`. Confirm with the user; author can "
            "override the list if the domain needs adjusted phrasing.\n\n"
            "### How the dev-kit captures this\n"
            "- Content/output rules: `update_config(block=trust_layer, section=rules, "
            "values={...})`\n"
            "- Consent rules (DPDP): `section=consent`.\n"
            "- Dignity check (Conversational): `section=dignity_check, values={enabled: "
            "true, questions: [...], fail_action: 'rewrite'}`. `fail_action` is schema-"
            "accepted but runtime ignores it for now — the check is self-enforced by the "
            "main LLM via prompt_constraints.\n"
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- Trust Layer's `/assemble_constraints` async call mechanism is DPG-"
            "specific — the guide describes what the check does, not how it plumbs.\n\n"
            "```yaml\n"
            + load_template_text("trust_layer")
            + "```\n\n"
            "➡️ When rules, consent, and (for Conversational) dignity_check are set, "
            "call `set_phase('tools')`."
        )

    if phase == "tools":
        return (
            "## Tools phase\n\n"
            "**What this phase is about:** Declare every external tool the agent can "
            "invoke, with strict invocation contracts the LLM must follow.\n\n"
            "**Per-type requirement:** Transactional / Agentic / Conversational = "
            "REQUIRED. Informational = SKIP (auto-advanced).\n\n"
            "### What to include (from guide §2.6 Tool Invocation Rules + §3.1)\n"
            "For each tool, define six fields in `invocation_rules`:\n"
            "1. `call_when` — exact trigger condition, in plain language.\n"
            "2. `required_before_calling` — list of data fields required before "
            "invocation. The tool MUST NOT be called if any are missing.\n"
            "3. `must_not_substitute` — memory, prior context, assumed knowledge — "
            "the LLM must never treat these as substitutes for a fresh tool call.\n"
            "4. `on_empty` — exact natural line the agent says when the tool returns "
            "empty results.\n"
            "5. `on_failure` — exact natural line on tool failure / timeout.\n"
            "6. `bridge_line` — optional single short line the agent says right before "
            "the tool call (e.g. 'ठीक है, current picture देख लेती हूँ।'). "
            "Essential for voice; optional for chat.\n\n"
            "### How the dev-kit captures this\n"
            "- Declare connectors: `update_config(block=agent_core, "
            "section=connectors.read | write | identity | internal, values=[{name, "
            "description, input_schema, invocation_rules: {...}}])`\n"
            "- If you have an OpenAPI spec for an action_gateway tool, you can upload "
            "it via `<document-extraction-tool>` (#130) — dev-kit will populate the "
            "tool schemas automatically. You still author `invocation_rules` by hand.\n\n"
            "### Guide gap\n"
            "- The guide discusses invocation contract but does not prescribe a 6-field "
            "structure; our schema formalises it.\n"
            "- The `route` field on `connectors.internal[]` (e.g. route=knowledge_engine) "
            "is DPG-specific.\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["connectors"])
            + "```\n\n"
            "➡️ When all external tools are declared with all six invocation_rules "
            "fields populated, call `set_phase('workflow')`."
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
