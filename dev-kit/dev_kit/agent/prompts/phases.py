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
            "**KB Document Sources — ask ONE question:**\n"
            "Ask: 'Do you have Azure Blob Storage for your KB documents?'\n\n"
            "  ┌─ Yes, Azure ──────────────────────────────────────────────────────────────┐\n"
            "  │  → Call declare_azure_storage() with NO arguments.                         │\n"
            "  │  → Tell the user: 'Noted. In the Deployment Inputs step you will be asked  │\n"
            "  │      for your Azure account name, account key, and container name —        │\n"
            "  │      keep all three ready.'                                                 │\n"
            "  │  → At IngestDocumentsStep (post-deploy), per file the operator chooses:    │\n"
            "  │      'Fetch from Azure', 'Upload local + push to Azure', or 'Local only'   │\n"
            "  └───────────────────────────────────────────────────────────────────────────┘\n\n"
            "  ┌─ No cloud storage ─────────────────────────────────────────────────────────┐\n"
            "  │  → Do NOT call declare_azure_storage                                        │\n"
            "  │  → At IngestDocumentsStep, only 'Upload local only' will be available       │\n"
            "  └───────────────────────────────────────────────────────────────────────────┘\n\n"
            "⚠️  NEVER ask for Azure account names, keys, container name, or any other\n"
            "    Azure details in chat. Everything is collected securely at deploy time.\n\n"
            "Do NOT collect document filenames or a list of files — operators upload files\n"
            "directly in IngestDocumentsStep after deployment.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```\n\n"
            "➡️ When collection_name, persona, and language_instruction are set "
            "(and declare_azure_storage called if applicable), call `set_phase('memory')`."
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
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`action_gateway`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "In this phase you configure the external tools the agent can call via the Action Gateway.\n\n"
            "**Path A — User has an OpenAPI spec (3 ways to provide it):**\n"
            "  1a. **URL** — User provides a URL to their spec file. Call `fetch_openapi_spec_from_url(url)` directly.\n"
            "  1b. **File upload** — User says they've uploaded a file and the spec content appears in their message. Call `parse_openapi_spec(spec_json)` with the full spec text.\n"
            "  1c. **Paste** — User pastes the YAML or JSON directly. Call `parse_openapi_spec(spec_json)` with the pasted text.\n"
            "  2. Present the returned candidates and confirm which ones to add.\n"
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
            "**Auth credentials — IMPORTANT:**\n"
            "  When a tool requires an API key or bearer token (auth_type is not 'none'),\n"
            "  do NOT ask the user for the credential value in chat.\n"
            "  Instead say: 'This tool needs an API key in env var `<auth_secret_env>`. "
            "Keep that key ready — you will enter it securely in the Deployment Inputs step.'\n\n"
            "**After adding each REST API tool — ALWAYS do this:**\n"
            "  1. Ask: 'Can you share a sample JSON response from this endpoint? Or describe the key fields you need the AI to work with.'\n"
            "  2. Based on the user's answer, identify the fields they need and their JSONPaths in the response structure.\n"
            "     - For a flat response like {\"title\": \"...\", \"company\": \"...\"}, the source is just the key name: 'title', 'company'\n"
            "     - For nested/array responses like {\"results\": [{\"title\": \"...\"}]}, use JSONPath: 'results[*].title'\n"
            "  3. Confirm the field list with the user: 'I'll extract these fields: title → job_title, company → employer'. Does that look right?\n"
            "  4. Call `set_response_transformation(tool_id=<tool_id>, fields=[...])` with the confirmed fields.\n"
            "  5. Then ask: 'Are there any other tools to add? (OpenAPI spec URL, file attachment, MCP server, or describe manually)'\n"
            "     - If yes: repeat the appropriate path above.\n"
            "     - If no: proceed to completion.\n\n"
            "**If the user does not want response transformation (no filtering needed):**\n"
            "  Skip step 4 — do NOT call set_response_transformation. The full response (up to max_size_chars) is passed to the LLM.\n\n"
            "**If no external tools are needed at all:**\n"
            "  Confirm with the user and proceed directly.\n\n"
            "REST API tools (`add_rest_api_tool`) automatically create a matching connector\n"
            "in agent_core.connectors — subagents reference these by their bare id.\n"
            "MCP tools (`add_mcp_tool`) do NOT create connectors — tool schemas come from\n"
            "the server at runtime. Subagents reference MCP tools by their namespaced names\n"
            "(e.g. 'obsrv_docs.searchDocumentation'), not the bare adapter id.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
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
            "## Workflow phase\n\n"
            "**What this phase is about:** Design the subagent state machine — "
            "individual conversational sub-flows and how they route between each "
            "other based on NLU intent.\n\n"
            "### What to include (from guide §2.3 Conversation Opening Logic)\n"
            "- One subagent per coherent conversational sub-flow.\n"
            "- Each subagent: id, description, routing rules, and **`opening_phrase`** "
            "for the first turn of a session that enters this subagent.\n"
            "- Exactly ONE subagent has `is_start: true`.\n"
            "- **Conversational agents** should structure their subagent graph so the "
            "5 contact-memory states (new, sparse, rich, mid-journey, post-application) "
            "each land in a subagent with an appropriate `opening_phrase`. The dev-kit "
            "does not schema-enforce 'exactly 5 branches' — author judgement. The "
            "guide's 5-branch rule is pedagogy, not validation.\n\n"
            "### How the dev-kit captures this\n"
            "- Declare subagents: use the `create_subagent` tool per subagent "
            "(provides id, name, description, is_start, is_terminal, valid_intents, "
            "tools, system_prompt, opening_phrase).\n"
            "- Define routing: use `update_subagent` to set routing rules.\n"
            "- Set global routing: `update_config(block=agent_core, "
            "section=agent_workflow.global_routing, values=[...])`.\n\n"
            "### Opening phrase guidance\n"
            "- Emitted ONCE per session, on the first post-consent turn. Subsequent "
            "turns run the subagent's normal `system_prompt`.\n"
            "- The subagent active on turn 1 is determined by Memory Layer: either the "
            "`is_start: true` subagent (new session) or a subagent restored from the "
            "previous session's `current_subagent` (returning user).\n"
            "- Tailor each subagent's `opening_phrase` to what the user knows at that "
            "point. E.g. a start subagent opens with a warm discovery question; a "
            "post-action subagent opens by acknowledging the previous action.\n\n"
            "### Guide gap\n"
            "- The guide describes 5 'opening branches' as a single prompt-level "
            "conditional; we represent them via the subagent graph + `opening_phrase` "
            "field, because our subagent abstraction is richer than the guide assumes.\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent_workflow"])
            + "```"
            + connector_note
            + "\n\n➡️ When all subagents are declared with routing and opening_phrases, "
            "call `set_phase('observability')`."
        )

    if phase == "observability":
        return (
            "## Observability phase\n\n"
            "**What this phase is about:** Configure outcome lifecycle states, "
            "quality metrics, and the domain tag used in all OTel spans.\n\n"
            "### What to include (from guide §3.4 Exception Handling + DPG defaults)\n"
            "- Outcome states for the domain (e.g. 'profile_gathered', 'options_shown', "
            "'applied', 'callback_pending').\n"
            "- Quality signals worth tracking (e.g. drop-off at specific subagents, "
            "low-confidence turns, consent declines).\n"
            "- Exception-handling policies: what the agent says on tool timeout, empty "
            "result, ASR misrecognition, mid-call drop.\n\n"
            "### How the dev-kit captures this\n"
            "- Outcome lifecycle: `update_config(block=observability_layer, "
            "section=outcomes.lifecycle, values=[...])`\n"
            "- Quality metrics: `section=quality.signals`.\n"
            "- Domain tag: auto-set from project slug.\n\n"
            "### Guide gap\n"
            "- DPG-specific: `turn_event` schema, async emit contract, OTel span "
            "attribute conventions (e.g. user_state.current, session.turn_count).\n\n"
            "```yaml\n"
            + load_template_text("observability_layer")
            + "```\n\n"
            "➡️ When outcomes and quality signals are set, call `set_phase('reach')`."
        )

    if phase == "reach":
        return (
            "## Reach phase\n\n"
            "**What this phase is about:** Declare channel adapters and adapter-"
            "specific settings (TTS provider endpoints, websocket URLs, campaign "
            "config, web UI branding).\n\n"
            "### What to include (from guide Appendix: Voice vs Chat)\n"
            "- Channels declared: any subset of voice / chat / web / cli.\n"
            "- **Voice** requires a TTS provider (e.g. raya_tts) and telephony adapter "
            "config; the LLM-facing voice config (prompt suffix, TTS rules, terminal "
            "word, turn_assembler) was set in the `language` phase under "
            "`agent_core.channels.voice`.\n"
            "- **Chat / web / whatsapp** require their respective adapter endpoints "
            "and webhook URLs.\n"
            "- Web UI branding (app name, icon, tagline) for web deployments.\n\n"
            "### How the dev-kit captures this\n"
            "- Voice adapter: `update_config(block=reach_layer, "
            "section=channels.voice, values={tts_provider: ..., telephony: ...})`\n"
            "- Web UI: `section=web, values={app_name: ..., icon: ..., tagline: ...}`\n"
            "- Campaign config (if outbound campaigns): `section=campaigns`.\n\n"
            "### Guide gap\n"
            "- Our Reach Layer is a distinct DPG block; the guide treats channels as "
            "adapter concerns without abstracting them.\n"
            "- TurnAssembler policy (semantic_gate, silence_trigger, max_wait_ceiling) "
            "lives in `agent_core.channels.<name>.turn_assembler` because TurnAssembler "
            "runs inside Agent Core — not covered by the guide.\n\n"
            "```yaml\n"
            + load_template_text("reach_layer")
            + "```\n\n"
            "➡️ When channel adapters and UI branding are declared, call "
            "`set_phase('review')`."
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "**What this phase is about:** Run a full schema-coverage check across "
            "all 7 DPG blocks and report any empty required fields for correction.\n\n"
            "Use the `validate_config` tool to run the check. It reads every block's "
            "YAML template, compares against the accumulated config, and lists empty "
            "required fields with exact paths (e.g. `agent_core.channels.voice."
            "terminal_word`, `trust_layer.dignity_check.questions[2]`).\n\n"
            "For each missing field: ask the user for the value, call the appropriate "
            "`update_config` tool, and re-run `validate_config` until the report is "
            "clean.\n\n"
            "Once `validate_config` reports no missing required fields, announce "
            "completion. The author can then deploy via the `deploy_project` tool or "
            "export the seven YAMLs."
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
