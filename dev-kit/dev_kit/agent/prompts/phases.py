"""
dev-kit/dev_kit/agent/prompts/phases.py

Phase-specific additions to the system prompt. Each phase injects the
relevant Pydantic section schemas as source code so Claude sees real
constraints (ge=1, le=20, model_validator, enums) directly in its
context — never inventing or renaming keys.
"""
from __future__ import annotations

import inspect

from dev_kit.schemas.domain import (
    action_gateway as ag_domain,
    agent_core as ac_domain,
    knowledge_engine as ke_domain,
    memory_layer as ml_domain,
    observability_layer as obs_domain,
    reach_layer as rl_domain,
    trust_layer as tl_domain,
)
from dev_kit.schemas.enums import (
    ANTHROPIC_MODELS,
    EMBEDDING_PROVIDERS,
    LANGUAGES,
    OPENAI_MODELS,
    RAYA_VOICES,
)
from dev_kit.schemas.validation import get_valid_sections


_RAYA_LANGUAGE_DISPLAY_NAMES = {
    "mr": "Marathi", "hi": "Hindi", "te": "Telugu", "kn": "Kannada",
    "bn": "Bengali", "as": "Assamese", "gu": "Gujarati",
    "en-in": "English India", "en-us": "English US",
    "ml": "Malayalam", "ne": "Nepali", "ta": "Tamil",
}


def _bullet_list(values: list[str]) -> str:
    """Render a list of allowed enum values as backtick-quoted markdown bullets.

    Generated dynamically from enums_config.yaml so adding or removing a value
    in YAML automatically updates what the LLM sees.

    Args:
        values: List of valid string values for an open enum
            (e.g. ANTHROPIC_MODELS, OPENAI_MODELS, LANGUAGES,
            EMBEDDING_PROVIDERS).

    Returns:
        Markdown bullets, one per value.
    """
    return "\n".join(f"- `{v}`" for v in values)


def _raya_voice_table() -> str:
    """Render the Raya voice lookup table from enums_config.yaml.

    Generated dynamically so adding a voice to enums_config.yaml automatically
    surfaces it in the language-phase prompt.

    Returns:
        Markdown table with one row per voice (language code + display name,
        voice name, voice_id).
    """
    rows = [
        f"| {v['language']} ({_RAYA_LANGUAGE_DISPLAY_NAMES.get(v['language'], v['language'])}) "
        f"| {v['name']} | `{v['voice_id']}` |"
        for v in RAYA_VOICES
    ]
    return (
        "| Language | Voice Name | voice_id |\n"
        "|----------|-----------|----------|\n"
        + "\n".join(rows)
    )


def _schema_source(*classes) -> str:
    """Render multiple Pydantic classes as a single code block.

    Used in phase prompts to inject real schema source (with constraints,
    validators, enums) instead of blank YAML templates.

    Args:
        *classes: Pydantic model classes to render.

    Returns:
        Concatenated Python source for each class, separated by blank lines.
    """
    return "\n\n".join(inspect.getsource(c) for c in classes)


_SCHEMA_PREAMBLE = (
    "### Schema for sections you will configure in this phase\n\n"
    "All update_config calls must produce values that conform to these "
    "Pydantic models. Constraints (ge, le, enum, model_validator) are "
    "enforced by the tool handler — the call will fail if you violate "
    "them.\n\n"
)

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
            "4. knowledge   — RAG knowledge base, doc_types, document sources\n"
            "5. memory      — session state fields, persistent graph, consent mode\n"
            "6. user_state  — user mental-state model (Conversational only; skip otherwise)\n"
            "7. trust       — blocked phrases, escalation topics, safety guardrails\n"
            "8. tools       — external API / MCP tools (or confirm none needed)\n"
            "9. workflow    — subagent state machine, routing rules\n"
            "10. observability — outcome lifecycle states, metrics, domain name\n"
            "11. reach      — channels, TTS rules, terminal word\n"
            "12. review     — validate, fix missing fields, finalize all blocks\n\n"
            "**CRITICAL: you may NOT skip any phase.** set_phase will return an error if you try to jump ahead.\n\n"
            "**Exactly 5 things to collect in this phase (no more, no less):**\n"
            "1. **Problem & users** — What problem does this agent solve? Who are the users?\n"
            "2. **Languages** — What languages do the users speak?\n"
            "3. **Knowledge domain** — What topic/domain does the agent cover? "
            "(e.g. 'crop diseases', 'general knowledge'). Just the domain — do NOT ask "
            "about document count, document size, document format, or file names. "
            "Documents are uploaded after deployment, not configured here.\n"
            "4. **External APIs** — Does the agent need to call any external APIs or "
            "services? (If no, the tools phase will be quick.)\n"
            "5. **Channels** — Which deployment channels: web, voice, or both? "
            "(Do NOT offer cli as an option — it is dev-only and never user-selectable.)\n\n"
            "**Conversation style for this phase:**\n"
            "Present what you already know from the project description, then ask ONLY "
            "about what's missing from the 5 items above. If the project description "
            "already answers some items, confirm your understanding and ask about the rest "
            "in one message. Do NOT pad with extra questions.\n\n"
            "**Do NOT ask about any of these — they are not needed in overview:**\n"
            "- Document count, size, format, or filenames (handled post-deploy)\n"
            "- User volume or scale (no config field uses this)\n"
            "- Technical infrastructure details\n"
            "- API endpoint URLs, auth details, or MCP server URLs (collected in Tools phase)\n"
            "- How voice/TTS works, whether voice means TTS or text (voice = TTS by definition)\n"
            "- Details about specific tools or APIs (just yes/no for external APIs is enough)\n"
            "- Anything not in the 5 items above\n\n"
            "**Do NOT pre-empt future phases.** For External APIs (#4), just ask whether "
            "the agent needs them — a simple yes/no with tool names is enough. Do NOT ask "
            "for API URLs, spec files, MCP endpoints, auth details, or how the API works. "
            "All that is collected in the Tools phase.\n\n"
            "**Document ingestion timing:**\n"
            "If the user mentions documents or a knowledge base, do NOT say "
            "'we will ingest those during the Knowledge phase'. Document ingestion happens "
            "AFTER deployment via the Ingest Documents step in the deploy wizard. "
            "Say: 'You will upload and ingest your documents after deployment.'\n"
            "Do NOT ask the user to upload KB documents in this chat. File uploads in chat "
            "are ONLY for OpenAPI/API spec files (in the Tools phase).\n\n"
            "**Channel selection:**\n"
            "Call `set_reach_channels(channels=[...])` before advancing. This determines "
            "what gets asked in later phases (voice TTS rules only if voice is selected, etc.).\n"
            "If unsure, suggest a sensible default based on the use case.\n\n"
            "**When done:** call `set_project_meta`, then `set_reach_channels`, "
            "then `set_phase('language')`."
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
            "- LLM provider (`anthropic` or `openai`) and primary + fallback model IDs "
            "(agent.provider, agent.primary_model, agent.fallback_model). Both models "
            "must belong to the chosen provider.\n"
            "- Default language + supported languages for language normalisation\n"
            "- NLU classifier model + intents/entities/sentiment classes\n"
            "- Conversation-level messages (blocked_message, consent_message, etc.) in "
            "the target language\n"
            "- **Voice only (skip if voice not in selected_channels):** TTS rules per "
            "data type (numbers, money, dates, time, phone, abbreviations, output script, "
            "English loanwords) under `agent_core.channels.voice.tts_rules`\n"
            "- **Voice only (skip if voice not in selected_channels):** "
            "`reach_layer.channels.voice.terminal_word` — the literal word that "
            "signals call end (e.g. \"Goodbye\", \"धन्यवाद\"). Required for voice.\n"
            "- **Voice only (skip if voice not in selected_channels):** "
            "`reach_layer.channels.voice.filler_phrase` — short utterance played if "
            "the LLM takes >1.5 s to produce the first sentence (e.g. \"एक सेकंड\", "
            "\"one moment\"). Empty string disables.\n\n"
            "### How the dev-kit captures this\n"
            "- Set provider + models + consent: `update_config(block=agent_core, "
            "section=agent, values={provider: 'anthropic' | 'openai', "
            "primary_model: ..., fallback_model: ..., ask_for_consent: ..., "
            "consent_prompt: ...})`\n"
            "- Set language normalisation: `section=preprocessing.language_normalisation`\n"
            "- Set NLU: `section=preprocessing.nlu_processor`\n"
            "- Set conversation messages: `section=conversation` (all message keys)\n"
            "- Set entity-to-profile map: `section=entity_to_profile_field`\n"
            "- Set HITL response: `section=hitl, values={response_message: ...}`\n"
            "- Auto-set observability domain: `section=observability, values={domain: "
            "'<project_slug>'}`\n"
            "- **Voice only (skip if voice not in selected_channels)** — set TTS rules "
            "in agent_core: `update_config(block=agent_core, section=channels, "
            "values={voice: {tts_rules: {...}}})`. You may draft the TTS rules from "
            "the canonical language defaults and offer `\"draft them for me\"` to "
            "the user.\n"
            "- **Voice only (skip if voice not in selected_channels)** — set "
            "terminal_word + filler in reach_layer: `update_config(block=reach_layer, "
            "section=reach_layer.channels.voice, values={terminal_word: 'धन्यवाद', "
            "filler_phrase: 'एक सेकंड', filler_threshold_ms: 1500})`.\n\n"
            "**IMPORTANT — check selected_channels in the config state above.**\n"
            "If voice is NOT in selected_channels, skip ALL voice-related config "
            "(TTS rules in agent_core.channels.voice, terminal_word + filler in "
            "reach_layer.channels.voice). Do not ask about them.\n\n"
            "**IMPORTANT — agent_core.channels MUST be configured for EVERY selected channel.**\n"
            "Agent Core crashes at startup with `ValueError: Unsupported channel` if "
            "`channels.<name>` is absent from agent_core.yaml. This is NOT optional.\n\n"
            "Walk through selected_channels (from config state above) and configure each:\n\n"
            "- **web** — ALWAYS configure this. Web is always deployed even if not explicitly "
            "selected. Set a brief `system_prompt_suffix` (e.g. 'You are responding via the "
            "web interface. Markdown is supported.') and turn_assembler timing defaults:\n"
            "  `update_config(block=agent_core, section=channels.web, values={"
            "system_prompt_suffix: 'You are responding via the web interface. Markdown is "
            "supported.', turn_assembler: {silence_trigger: {silence_ms: 1500}, "
            "max_wait_ceiling: {max_wait_ms: 15000}}})`\n\n"
            "- **cli** — only configure if the user has explicitly opted into cli "
            "for dev/testing. Never proactively offer it. Plain text, no markdown:\n"
            "  `update_config(block=agent_core, section=channels.cli, values={"
            "system_prompt_suffix: '', turn_assembler: {silence_trigger: {silence_ms: 200}, "
            "max_wait_ceiling: {max_wait_ms: 5000}}})`\n\n"
            "- **voice** — configure if voice is in selected_channels. Full TTS rules + "
            "system_prompt_suffix covered in Group 3 below. turn_assembler defaults: "
            "silence_ms: 600, max_wait_ms: 8000 (good for Hindi/regional voice cadence).\n\n"
            "Do NOT skip any channel that is in selected_channels, and always include web.\n\n"
            "### LLM provider — ASK THE USER FIRST\n"
            "Two providers are supported. Ask which one the user wants BEFORE "
            "recommending specific models, because the model list and pricing differ.\n\n"
            "| Provider | Strengths | Trade-offs |\n"
            "|----------|-----------|------------|\n"
            "| `anthropic` | Strong long-context reasoning, Claude family | Premium pricing |\n"
            "| `openai` | Lower cost on smaller tasks, GPT family | Shorter context on some models |\n\n"
            "After the user picks a provider, recommend `primary_model` and "
            "`fallback_model` from that provider's allowed list only. The schema's "
            "`models_must_match_provider` validator rejects cross-provider configs.\n\n"
            "### Anthropic models (use ONLY when provider=anthropic)\n"
            + _bullet_list(ANTHROPIC_MODELS) + "\n\n"
            "### OpenAI models (use ONLY when provider=openai)\n"
            + _bullet_list(OPENAI_MODELS) + "\n\n"
            "**Model selection guidance:**\n"
            "Use your training knowledge of each model's capability tier, context "
            "window, and price to pick `primary_model` and `fallback_model` based on "
            "the agent's use case:\n"
            "- Simple Q&A / FAQ bots → smaller/cheaper model primary, mid-tier fallback\n"
            "- Multi-step reasoning, complex domains → mid-tier primary, smaller fallback\n"
            "- High-stakes / critical accuracy → top-tier primary, mid-tier fallback\n\n"
            "⚠️ Primary and fallback MUST be different models AND from the same provider. "
            "The fallback exists to handle primary failures — using the same model for both "
            "defeats the purpose, and mixing providers is rejected by the schema.\n"
            "⚠️ Pick model IDs ONLY from the lists above. Any other ID — older Claude "
            "versions, GPT-3.5, GPT-4-turbo, hypothetical future models — will be rejected "
            "by the schema's `ChatModelField` validator.\n\n"
            "### Valid languages (use ONLY these for default_language and supported_languages)\n"
            + _bullet_list(LANGUAGES) + "\n\n"
            "These are the only values the schema's `LanguageField` accepts. Any other "
            "value — including language codes (`en`, `hi-IN`) or display names "
            "(`English`, `Hindi`) — will be rejected.\n\n"
            "**Building `supported_languages` from what the user said:**\n"
            "1. Take the user's exact list of languages.\n"
            "2. For each one the user named, check the bullet list above.\n"
            "   - If it appears verbatim → include it.\n"
            "   - If it does NOT appear → tell the user explicitly: \"`<lang>` is not "
            "in the supported list. The available options are: <bullet list>. Which of "
            "these would you like instead, or should I drop it?\" Wait for the answer "
            "before proceeding. NEVER silently drop an unsupported language and NEVER "
            "silently substitute a different one (e.g. swapping in `hinglish` for "
            "`punjabi` is wrong — `hinglish` is its own thing, not a fallback).\n"
            "3. Keep every supported language the user named. Do NOT silently drop a\n"
            "   supported language (e.g. dropping `kannada` when the user explicitly "
            "asked for it).\n"
            "4. Do NOT add languages the user did not request. If you think `hinglish` "
            "would help India-based users, ASK first; do not auto-add.\n"
            "5. The final list must equal the user-approved set, character-for-character "
            "from the bullet list above.\n\n"
            "### Conversation style for this phase\n"
            "Walk through the groups below in order, presenting each as a single block "
            "with defaults rather than asking field-by-field:\n\n"
            "**Group 1A — Provider choice (ASK FIRST, before models):**\n"
            "Ask the user: 'Which LLM provider do you want — `anthropic` (Claude) or "
            "`openai` (GPT)? Both are supported; the available models and pricing differ.'\n"
            "Wait for the user's answer before proposing any model IDs. Default lean: "
            "anthropic, but always ask explicitly.\n\n"
            "**Group 1B — Models & Language setup:**\n"
            "Once the provider is chosen, present `primary_model`, `fallback_model`, "
            "consent setting, default language, and supported languages together. Use the "
            "matching provider's model table above to suggest defaults. Ask the user to "
            "confirm or edit.\n\n"
            "**Group 2 — Conversation messages (all at once):**\n"
            "Present ALL conversation messages together with suggested defaults based on "
            "the agent's domain. Include: consent_message, consent_declined_message, "
            "termination_message, unknown_intent_message, blocked_message, "
            "escalation_message, blocked_output_message. Show all messages in a list and "
            "ask: 'Do these look good, or would you like to change any?'\n"
            "For multilingual agents, draft translations for all messages and present "
            "them together — do NOT ask for each translation one by one.\n\n"
            "**Group 3 (voice only) — TTS rules, terminal word & filler:**\n"
            "Only present this group if voice is in selected_channels. Show TTS "
            "rules (agent_core.channels.voice.tts_rules), terminal word "
            "(reach_layer.channels.voice.terminal_word), and filler phrase + "
            "threshold (reach_layer.channels.voice.filler_phrase / "
            "filler_threshold_ms) with suggested defaults. Skip entirely for "
            "non-voice agents.\n\n"
            "**Group 3 — NLU intents & entities:**\n"
            "Derive intents and entities ENTIRELY from the use case the user described "
            "in the Overview phase. Strict rules:\n"
            "- Start with ONLY `unknown` as the baseline intent. Do NOT auto-include "
            "`greeting`, `clarification`, `consent_granted`, or `consent_declined` — "
            "those are NOT generic defaults and must never appear unless the user has "
            "explicitly asked for them.\n"
            "- Generate the rest of the intents from the agent's described scope and "
            "present the full proposed list to the user for sign-off. Show entities and "
            "entity_to_profile_field in the same group.\n"
            "- After the user signs off, the intent list is FROZEN. Do not add, rename, "
            "remove, or merge intents in later phases (knowledge, workflow, etc.) without "
            "asking the user first. If a later phase needs a new intent, surface the "
            "request explicitly and get approval before calling `update_config`.\n"
            "- The renderer will union subagent `valid_intents` into "
            "`preprocessing.nlu_processor.intents` automatically. Therefore, when you "
            "design subagents in the Workflow phase, REUSE the signed-off NLU intent "
            "names verbatim — do not invent parallel names like `scheme_query` when the "
            "NLU already has `scheme_inquiry`.\n\n"
            "### Guide gap — DPG-specific fields not in the guide\n"
            "- `signal_intents` (map of intent → signal type for longitudinal context-"
            "graph writes). Ask: 'Are there intents that should write a longitudinal "
            "signal to the context graph?'\n"
            "- `user_state_confidence_threshold` (GH-139) — set only for "
            "Conversational agents during the user_state phase; default 0.4 works.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ac_domain.AgentSection,
                ac_domain.LanguageNormalisationSection,
                ac_domain.NLUProcessorSection,
                ac_domain.PreprocessingSection,
                ac_domain.ConversationSection,
                ac_domain.ChannelsSection,
                ac_domain.HitlSection,
            )
            + "\n```\n\n"
            "➡️ When models, language normalisation, NLU, conversation messages, "
            "entity_to_profile_field, hitl.response_message, and (if voice is in "
            "selected_channels) agent_core.channels.voice.tts_rules + "
            "reach_layer.channels.voice.{terminal_word, filler_phrase, "
            "filler_threshold_ms} are all set, call `set_phase('knowledge')`."
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
            "- Auto-set observability domain.\n\n"
            "### Guide gap\n"
            "- `intent_filters` (per-intent document retrieval scoping) is DPG-specific "
            "and not covered by the guide.\n\n"
            "⚠️ **Persona / language instruction do NOT go here.** The Knowledge Engine is "
            "retrieval-only and has no LLM or persona config. Persona and language instruction "
            "belong in `agent_workflow.agent_system_prompt` (set in the Workflow phase).\n\n"
            "Use `update_config` with block=`knowledge_engine`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('knowledge_engine'))}\n\n"
            "**STEP 0 — Before asking the user anything, automatically call `update_config`:**\n"
            "  block=`knowledge_engine`, section=`observability`, values={domain: '<project_slug>'}\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user.\n\n"
            "**CRITICAL — exact section paths to use, no substitutions:**\n"
            "- RAG config: section=`knowledge.blocks.static_knowledge_base`\n"
            "  Keys: `collection_name`, `top_k`, `similarity_threshold`, `default_doc_type`, "
            "`embedding_provider`, `intent_filters` (dict)\n"
            "  Valid `embedding_provider` values (schema's `EmbeddingProviderField`):\n"
            + _bullet_list(EMBEDDING_PROVIDERS) + "\n"
            "  Default `chroma_default` works for most deployments — only ask the user "
            "if they have a specific reason to override.\n"
            "  ❌ NEVER write `vector_store` — this key does not exist in the schema.\n"
            "  ❌ NEVER write `sources` — documents are uploaded post-deploy, not configured here.\n"
            "  ❌ NEVER write `conversation`, `persona`, or `language_instruction` — these do not exist in knowledge_engine.\n"
            "  ❌ NEVER write flat keys directly under knowledge: (e.g. knowledge.collection_name, knowledge.top_k)\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "**CRITICAL — knowledge_retrieval connector placement:**\n"
            "When the agent has a knowledge base, you MUST create the `knowledge_retrieval` "
            "connector under `connectors.internal` (NOT `connectors.read`). This is an "
            "internal tool routed by Agent Core directly to the Knowledge Engine — it does "
            "NOT go through Action Gateway.\n"
            "Call: `update_config(block=agent_core, section=connectors.internal, values=[{"
            "name: 'knowledge_retrieval', route: 'knowledge_engine', "
            "description: '<describe what the KB contains>', "
            "input_schema: {type: 'object', properties: {query: {type: 'string', "
            "description: 'Search query'}}, required: ['query']}, "
            "invocation_rules: {call_when: '...', required_before_calling: ['query'], "
            "must_not_substitute: '...', on_empty: '...', on_failure: '...', bridge_line: '...'}"
            "}])`\n"
            "⚠️ NEVER put knowledge_retrieval under connectors.read — that's for Action "
            "Gateway tools only. Internal connectors have `route:` not `base_url:`.\n\n"
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
            "⚠️  Document ingestion happens AFTER deployment, not during this conversation.\n"
            "    Do NOT say 'we will ingest documents in this phase' or 'during the Knowledge phase'.\n"
            "    Instead say: 'You will upload and ingest your KB documents after deployment\n"
            "    via the Ingest Documents step in the deploy wizard.'\n\n"
            "⚠️  Do NOT ask the user to upload documents in this chat. File uploads in chat\n"
            "    are ONLY for OpenAPI/API spec files (in the Tools phase). KB document uploads\n"
            "    happen exclusively in the post-deployment Ingest Documents step.\n\n"
            "Do NOT collect document filenames or a list of files — operators upload files\n"
            "directly in IngestDocumentsStep after deployment.\n\n"
            "### Conversation style for this phase\n"
            "**Step 1 — Ask about document CONTENT (not quantity):**\n"
            "Ask: 'What topics or information do your documents cover?'\n"
            "Example answer: 'I have crop disease guides, government scheme PDFs, and "
            "Obsrv platform documentation.'\n"
            "Do NOT ask how many documents, how large they are, or what format. Just the "
            "topics/content areas.\n\n"
            "**Step 2 — Generate doc_types, intent_filters, AND sync NLU intents:**\n"
            "Based on what the user says, create:\n"
            "- A `doc_type` label for each content area (short snake_case, e.g. "
            "`crop_diseases`, `govt_schemes`, `obsrv_docs`)\n"
            "- `intent_filters` mapping NLU intents to the relevant doc_types\n"
            "- Set `default_doc_type` to the most common/general doc_type\n\n"
            "**CRITICAL — keep NLU intents and intent_filters in sync (cross-block invariant):**\n"
            "Every key in `intent_filters` MUST appear in `agent_core.preprocessing."
            "nlu_processor.intents`. The NLU classifier can only produce intents that "
            "are declared there; an `intent_filters` key the NLU never produces is "
            "dead config — queries fall through to unfiltered retrieval. The deploy "
            "wizard's cross-block validator and the `set_phase` tool BOTH enforce "
            "this — if you advance with a mismatch, `set_phase` returns "
            "PHASE_ADVANCE_BLOCKED and lists every offending key.\n\n"
            "**The two writes must be paired in the SAME message — worked example:**\n"
            "If the user wants the agent to filter retrieval by booking intents:\n\n"
            "```\n"
            "1. update_config(block='knowledge_engine',\n"
            "                 section='knowledge.blocks.static_knowledge_base',\n"
            "                 values={intent_filters: {ask_packages: ['package_info'],\n"
            "                                          ask_booking:  ['booking_policy']}})\n"
            "2. update_config(block='agent_core',\n"
            "                 section='preprocessing.nlu_processor',\n"
            "                 values={intents: [<existing intents>, 'ask_packages', 'ask_booking']})\n"
            "```\n\n"
            "Always merge the new intents with the existing list — never replace. Read "
            "the current NLU intents from the config state above before constructing the "
            "second call.\n\n"
            "**Self-check before set_phase:**\n"
            "Before calling `set_phase('memory')`, verify in the config state above that "
            "every key in `knowledge_engine.knowledge.blocks.static_knowledge_base."
            "intent_filters` also appears in `agent_core.preprocessing.nlu_processor."
            "intents`. If any are missing, fix the NLU intents list first. The "
            "set_phase tool will reject the transition otherwise.\n\n"
            "These doc_types will appear as a dropdown when the user uploads documents "
            "after deployment, so they can tag each file with the correct type.\n"
            "Present the doc_types and intent_filters together and confirm with the user.\n\n"
            "**Step 3 — Present the full KB config as one block:**\n"
            "Include collection_name, RAG settings (top_k, similarity_threshold), "
            "default_doc_type, and intent_filters. Ask for confirmation.\n\n"
            "Then ask the single Azure storage question separately.\n\n"
            "**Do NOT ask about any of these:**\n"
            "- Number of documents\n"
            "- Document file sizes or formats\n"
            "- Document filenames or paths\n"
            "- Where documents are stored (handled by Azure question separately)\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ke_domain.StaticKnowledgeBaseSection,
                ke_domain.KnowledgeBlocksSection,
                ke_domain.KnowledgeSection,
                ac_domain.InternalConnectorDef,
                ac_domain.ConnectorsSection,
            )
            + "\n```\n\n"
            "➡️ When collection_name, intent_filters, and default_doc_type are set "
            "(and declare_azure_storage called if applicable), call `set_phase('memory')`."
        )

    if phase == "memory":
        return (
            "## Memory & Session State phase\n\n"
            "**What this phase is about:** Define what the agent remembers across "
            "turns (session scope), across sessions (persistent graph), and what "
            "contact memory fields are available at call start.\n\n"
            "### What to include (from guide §3.3 Contact Memory & Session State)\n"
            "- Session memory schema: domain-specific fields and TTL.\n"
            "  ⚠️ Do NOT propose fields that the DPG manages internally. In particular: "
            "current intent, last intent, current/previous subagent, turn count, language, "
            "consent state, and conversation phase are tracked by Agent Core / Memory "
            "Layer infrastructure — they are auto-injected and must NEVER appear in "
            "`state.session.schema`. Only declare fields that capture user-visible domain "
            "state (e.g. `location`, `trade`, `selected_scheme`).\n"
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
            "- Auto-set observability domain: call `update_config(block=memory_layer, section=observability, values={domain: '<project_slug>'})`.\n"
            "  ⚠️ Use `section=observability` NOT `section=observability.domain` — the latter double-nests and crashes memory_layer.\n\n"
            "### Guide gap\n"
            "- `merge_on_session_end`, `context_graph` node types, and re-engagement "
            "triggers are DPG-specific.\n\n"
            "### Conversation style for this phase\n"
            "Present the full memory configuration as **one block** with suggested defaults "
            "based on the use case. Include session schema fields, TTL, persistent graph "
            "node types, and user_data_persistence mode. Show all values together and ask: "
            "'Here is the suggested memory configuration — do these look good, or would "
            "you like to change any?' Only ask about re-engagement triggers separately if "
            "the agent type requires outbound follow-up.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ml_domain.SessionFieldDefinition,
                ml_domain.SessionStateConfig,
                # The graph node classes are referenced by type from
                # GraphConfig and SubnodeConfig — render their bodies
                # explicitly so the LLM sees that label/key/rel are
                # required (otherwise it submits empty placeholders like
                # `user_node: {}` which the runtime rejects at startup).
                ml_domain.UserNodeConfig,
                ml_domain.AdhocNodeConfig,
                ml_domain.ChildNodeConfig,
                ml_domain.SubnodeConfig,
                ml_domain.GraphConfig,
                ml_domain.PersistentStateConfig,
                ml_domain.StateSection,
                ml_domain.UserDataPersistenceSection,
                ml_domain.ReengagementSection,
            )
            + "\n```\n\n"
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
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ac_domain.UserStateDefinition,
                ac_domain.UserStateModel,
            )
            + "\n```\n\n"
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
            "- **Conversational:** `dignity_check` with the 5 canonical questions — "
            "you must set these explicitly, they are not auto-populated.\n"
            "- Prohibited language list (guide §2.11 Style & Prohibited). Include "
            "specific phrases, not just categories.\n\n"
            "### Canonical dignity check questions (Conversational only)\n"
            "For Conversational agents you MUST call `update_config` to set all five "
            "questions and `fail_action`. Do NOT leave `questions: []` — the dignity "
            "check will always pass (no questions to fail) and the protection is disabled.\n\n"
            "Default questions (adapt phrasing to the domain language):\n"
            "1. \"Does this blame the user?\"\n"
            "2. \"Does it over-promise?\"\n"
            "3. \"Does it push urgency?\"\n"
            "4. \"Does it reduce their agency?\"\n"
            "5. \"Does it sound like a script instead of a human call?\"\n\n"
            "Set them in one call — do NOT ask about each question individually:\n"
            "`update_config(block=trust_layer, section=dignity_check, values={"
            "enabled: true, "
            "questions: ['Does this blame the user?', 'Does it over-promise?', "
            "'Does it push urgency?', 'Does it reduce their agency?', "
            "'Does it sound like a script instead of a human call?'], "
            "fail_action: 'rewrite'})`\n\n"
            "Author can confirm or adjust the phrasing. If the domain is non-English, "
            "translate the questions into the domain language before setting.\n\n"
            "### How the dev-kit captures this\n"
            "- Content/output rules: `update_config(block=trust_layer, section=rules, "
            "values={...})`\n"
            "- Consent rules (DPDP): `section=consent`.\n"
            "- Dignity check (Conversational): `section=dignity_check, values={enabled: "
            "true, questions: [...], fail_action: 'rewrite'}`. `fail_action` is schema-"
            "accepted but runtime ignores it for now — the check is self-enforced by the "
            "main LLM via prompt_constraints.\n"
            "  ⚠️ `questions` MUST be a list of plain string sentences. Do NOT emit dicts "
            "like `{category: 'hate_speech', severity: 'high'}` — that's a content-"
            "moderation taxonomy, not a dignity prompt. Right shape: "
            "`questions: [\"Does this blame the user?\", \"Does it over-promise?\", ...]`. "
            "Wrong shape: `questions: [{category: ...}]` — the trust_layer container will "
            "crash at startup with a Pydantic ValidationError on each non-string element.\n"
            "- Auto-set observability domain: call `update_config(block=trust_layer, section=observability, values={domain: '<project_slug>'})`.\n"
            "  ⚠️ Use `section=observability` NOT `section=observability.domain`. "
            "Using `observability.domain` as the section creates a double-nested "
            "`observability: {domain: {domain: 'value'}}` which crashes trust_layer at startup.\n"
            "- HITL queue backend: `section=trust, values={hitl: {queue_backend: 'log'}}`. "
            "Valid values: `log` | `redis` | `webhook`. Default to `log` for dev. "
            "⚠️ NEVER use `memory` — it is not a valid backend and will crash the Trust Layer.\n\n"
            "### Guide gap\n"
            "- Trust Layer's `/assemble_constraints` async call mechanism is DPG-"
            "specific — the guide describes what the check does, not how it plumbs.\n\n"
            "### Conversation style for this phase\n"
            "Present the full trust configuration as **one or two blocks** with suggested "
            "defaults based on the agent's domain:\n\n"
            "**Block 1 — Content rules & blocked phrases:**\n"
            "Suggest domain-appropriate blocked phrases, escalation topics, and content "
            "rules. Present them all together and ask: 'Here are the suggested safety "
            "rules — do these look good, or would you like to change any?'\n\n"
            "**Block 2 (Conversational only) — Dignity check:**\n"
            "Present the 5 canonical dignity check questions together and ask for "
            "confirmation. Do NOT ask about each question individually.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                tl_domain.TrustSection,
                tl_domain.DignityCheckSection,
            )
            + "\n```\n\n"
            "➡️ Before calling `set_phase('tools')`, run this self-check:\n"
            "1. Content rules and blocked phrases are non-empty.\n"
            "2. For Conversational agents: `dignity_check.enabled: true`, `questions` has "
            "all 5 strings (not empty, not dicts), and `fail_action: 'rewrite'` is set.\n"
            "Fix any gap before advancing. Then call `set_phase('tools')`."
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
            "### REST API param types — ONLY these are valid\n"
            "When declaring endpoint params via `add_rest_api_tool`, the `type` field "
            "must be one of: `string`, `integer`, `boolean`, `array`.\n"
            "⚠️ `number` and `float` are NOT valid param types. Use `string` instead — "
            "query parameters are serialized as strings. If an OpenAPI spec says "
            "`type: number` or `type: float`, map it to `string`.\n\n"
            "### Multiple base URLs in one spec\n"
            "Each REST API tool has a single `base_url`. If an OpenAPI spec lists "
            "multiple servers with different hosts (e.g. `api.example.com` and "
            "`geocoding.example.com`), create separate tools — one per base URL. "
            "Do NOT use the wrong host for an endpoint.\n\n"
            "**MANDATORY FIRST ACTION — do this BEFORE anything else, even if no external tools are needed:**\n"
            "  Call `update_config(block=action_gateway, section=observability, values={domain: '<project_slug>'})`\n"
            "  Use the Slug shown in the '## Project' section. Do NOT ask the user. Do NOT skip this step.\n"
            "  This ensures action_gateway has a non-empty config and the Deploy wizard recognises it as configured.\n\n"
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
            "     all discovered tool names (e.g. 'obsrv_docs__searchDocumentation').\n"
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
            "  Confirm with the user and proceed directly to `set_phase('workflow')`. "
            "The MANDATORY FIRST ACTION above (writing `action_gateway.observability.domain`) "
            "is still required — do it before confirming with the user.\n\n"
            "REST API tools (`add_rest_api_tool`) automatically create a matching connector\n"
            "in agent_core.connectors — subagents reference these by their bare id.\n"
            "MCP tools (`add_mcp_tool`) do NOT create connectors — tool schemas come from\n"
            "the server at runtime. Subagents reference MCP tools by their namespaced names\n"
            "(e.g. 'obsrv_docs__searchDocumentation'), not the bare adapter id.\n\n"
            "⚠️ **CRITICAL — connector input_schema.properties must mirror the tool's**\n"
            "**agent-source params exactly. Do not rename, add, or remove keys.**\n"
            "The auto-generated connector that `add_rest_api_tool` produces already has\n"
            "exactly the right set of properties. The REST adapter forwards the LLM's\n"
            "params verbatim into the HTTP request, so any divergence between connector\n"
            "and tool causes a real runtime failure:\n"
            "- **Renamed key** — connector says `<orig>`, tool says `<renamed>`. The\n"
            "  LLM sends `?<renamed>=…` to an API expecting `?<orig>=…`. Silent empty\n"
            "  results or 400.\n"
            "- **Invented key** — connector lists a property `<extra>` that has no\n"
            "  matching agent-source param in the tool. The API receives a field it\n"
            "  never declared — either silently ignored (so any filter relying on it\n"
            "  does nothing) or rejected outright.\n"
            "- **Dropped key** — a required tool param `<missing>` is omitted from\n"
            "  the connector. The LLM never supplies it, the request is missing a\n"
            "  required field, the API fails.\n\n"
            "Rules:\n"
            "- ✅ DO edit `description`, `invocation_rules`, or any per-property\n"
            "  `description` text in the connector — these are LLM-facing hints only.\n"
            "- ❌ DO NOT rename, add, or remove keys in `input_schema.properties`.\n"
            "- ❌ DO NOT change the `required` array to a different set than the tool's\n"
            "  required agent-source params.\n"
            "- If you genuinely want a different param NAME visible to the LLM, change\n"
            "  the tool's `source: agent` param name itself — the API receives whatever\n"
            "  the tool param is named, so they must be edited together.\n"
            "- If you genuinely want to ADD a new param to the LLM contract, add it to\n"
            "  the tool's `endpoints[*].params` first (with `source: agent`), then\n"
            "  re-sync the connector. Do not edit the connector in isolation.\n"
            "The `set_phase` cross-block validator blocks phase advance if connector and\n"
            "tool param names diverge — you cannot leave this phase with a mismatch.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ag_domain.ToolDefinition,
                ag_domain.EndpointDefinition,
                ag_domain.ParamDefinition,
                ag_domain.AuthConfig,
                ag_domain.ToolsSection,
                ac_domain.InvocationRules,
                ac_domain.ConnectorDef,
                ac_domain.ConnectorsSection,
            )
            + "\n```\n\n"
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
                "- MCP tools: use '{adapter_id}__{mcp_tool_name}' (e.g. 'obsrv_docs__searchDocumentation') — "
                "no connector entry exists; the MCP adapter discovers tool names at startup. "
                "Use the exact tool names returned by `discover_mcp_tools` prefixed with the adapter id and double underscore."
            )
        return (
            "## Workflow phase\n\n"
            "**What this phase is about:** Design the subagent state machine — "
            "individual conversational sub-flows and how they route between each "
            "other based on NLU intent.\n\n"
            "### EXECUTION RULE — do NOT stall after the user confirms\n"
            "When the user confirms the subagent design (any variant of 'yes', 'looks good', "
            "'that's correct', 'proceed'), immediately call `create_subagent` for every subagent "
            "in the design. Do NOT say 'Perfect! Let me set that up…' and then ask another "
            "question. The pattern is: present design → user confirms → execute tools immediately "
            "without any intermediate message. If you catch yourself about to send a message that "
            "starts with 'Great!' or 'Perfect!' without a tool call attached, that is a stall — "
            "make the tool call instead.\n\n"
            "### Step 0 — Set top-level workflow fields FIRST (before declaring any subagents)\n"
            "These three fields are REQUIRED. Agent Core will fail Pydantic validation at "
            "startup if any is missing or empty:\n\n"
            "- `workflow_id` — unique snake_case identifier for this workflow, e.g. `kkb_iti_graduate`. "
            "Derive it from the project slug + a short domain tag.\n"
            "- `version` — semantic version string, always `\"1.0.0\"` for a new workflow.\n"
            "- `agent_system_prompt` — the top-level system prompt for the orchestrating LLM. "
            "This is the persona + overarching instruction seen on EVERY turn. Write it now "
            "based on the project description. It should define who the agent is, what it does, "
            "and any hard behavioural constraints (e.g. language, tone, scope).\n\n"
            "Set all three in one call:\n"
            "`update_config(block=agent_core, section=agent_workflow, values={"
            "workflow_id: 'my_domain_agent', version: '1.0.0', "
            "agent_system_prompt: '...full persona prompt...'})`\n\n"
            "Also set `default_fallback_subagent_id` to the id of the subagent that should "
            "handle unrecognised intents (typically a clarification or main-info subagent). "
            "You must fill this in once you have declared your subagents.\n\n"
            "### Pre-check before declaring ANY subagent\n"
            "Before calling `create_subagent` for the first time, do this:\n"
            "1. Check if this agent has a knowledge base (i.e. the Knowledge phase configured "
            "`knowledge_engine`).\n"
            "2. If yes, look at the current config state above under `connectors.internal`. "
            "If `knowledge_retrieval` is NOT already listed there, you MUST add it now BEFORE "
            "creating any subagent:\n"
            "   `update_config(block=agent_core, section=connectors.internal, values=[{name: "
            "'knowledge_retrieval', route: 'knowledge_engine', description: '<what the KB "
            "contains>', input_schema: {type: 'object', properties: {query: {type: 'string', "
            "description: 'Search query'}}, required: ['query']}, invocation_rules: {call_when: "
            "'...', required_before_calling: ['query'], must_not_substitute: '...', on_empty: "
            "'...', on_failure: '...', bridge_line: '...'}}])`\n"
            "3. Only after confirming `knowledge_retrieval` is in `connectors.internal`, "
            "proceed to `create_subagent`. Then set `global_tools: [knowledge_retrieval]` "
            "via `update_config(block=agent_core, section=agent_workflow, values={global_tools: "
            "['knowledge_retrieval']})` — do NOT list it in any subagent's `tools`.\n\n"
            "### Tool name contract — HARD RULES (Agent Core crashes at startup if violated)\n"
            "Every tool name in a subagent's `tools` list or in `global_tools` **MUST** be one of:\n"
            "- The `name` of a connector declared in `connectors.read`, `connectors.write`, or `connectors.identity`\n"
            "- The `name` of a connector declared in `connectors.internal` (e.g. `knowledge_retrieval`)\n"
            "- A namespaced MCP tool name in the form `{adapter_id}__{mcp_tool_name}`\n\n"
            "### Routing referential integrity — HARD RULES (Agent Core KeyErrors at runtime if violated)\n"
            "- `default_fallback_subagent_id` must exactly match the `id` of one of the subagents "
            "you declare in `subagents[*]`. If you write `default_fallback_subagent_id: clarification` "
            "you MUST also declare a subagent with `id: clarification`. Do not use a name you have "
            "not declared — Agent Core will KeyError every time the fallback fires.\n"
            "- Every `next_subagent_id` in every `routing` rule (including `global_routing`) must "
            "also match a declared subagent `id`. Walk each subagent's routing list and each "
            "global_routing rule before finishing this phase — a single typo causes a silent dead end.\n\n"
            "⚠️ NEVER invent tool names. Before populating any subagent's `tools` list or `global_tools`, "
            "look at the current `connectors` config (visible in the config state above) and use ONLY "
            "names that exist there. If the connector was declared in the Tools phase, its `name` is the "
            "correct identifier. Putting a non-existent tool name in a subagent's tools list will cause "
            "Agent Core to crash at startup with a KeyError.\n\n"
            "### global_tools vs subagent tools\n"
            "`global_tools` makes a tool available to ALL subagents (unless a subagent declares its own "
            "non-empty `tools` list, which then takes precedence). Use `global_tools` for tools that "
            "every subagent might need (e.g. `knowledge_retrieval` for an informational agent). "
            "Use per-subagent `tools` to restrict access (e.g. only the 'commitment' subagent can call `apply_job`).\n\n"
            "**HARD RULE — `knowledge_retrieval` always goes in `global_tools`, never in subagents:**\n"
            "When the agent has a knowledge base, ALWAYS put `knowledge_retrieval` in `global_tools`. "
            "NEVER list it in any individual subagent's `tools` field. It is a cross-cutting tool "
            "that every subagent may need — scoping it to specific subagents would prevent retrieval "
            "in other subagents.\n"
            "  Correct: `global_tools: [knowledge_retrieval]` + no subagent lists `knowledge_retrieval`\n"
            "  Wrong:   `global_tools: []` + each subagent has `tools: [knowledge_retrieval]`\n"
            "  Wrong:   `global_tools: [knowledge_retrieval]` + subagents also list `knowledge_retrieval`\n\n"
            "Other tools (external API connectors) go in specific subagents' `tools` to restrict access. "
            "Use per-subagent `tools` only when a tool should NOT be available to all subagents.\n\n"
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
            "### ⚠️ STEP 0 — Read existing NLU intents BEFORE designing any subagent\n"
            "The user signed off on a specific NLU intent list in the language phase. "
            "That list lives in `agent_core.preprocessing.nlu_processor.intents` in "
            "the config state above. **Read it directly. Use those exact strings. "
            "DO NOT ask the user to re-confirm or list them — they have already been "
            "configured and the user expects you to read the state, not re-litigate it. "
            "Asking \"do these intents look right?\" or proposing a different list and "
            "asking the user to map between is wrong.**\n\n"
            "When you design subagents, set their `valid_intents`, `routing[*].intent`, "
            "and `global_intents` using ONLY names from that existing list — character-for-"
            "character identical. The renderer will silently merge any new name into NLU "
            "at YAML write time, which means the user ends up with intents they never "
            "approved. You must not let that happen.\n\n"
            "**Common silent-expansion mistakes — do not do these:**\n"
            "- NLU has `booking_confirmation` — you write `booking_confirmed` in a subagent. "
            "Different string, treated as a new intent.\n"
            "- NLU has `package_inquiry` — you write `package_inquiry_v2` or `ask_packages`. "
            "Same problem.\n"
            "- NLU has no `pricing_question` — you add it to a subagent's `valid_intents` "
            "thinking the renderer will pick it up. It will, but the user never approved it.\n\n"
            "**If you genuinely need an intent the user hasn't approved:**\n"
            "1. STOP. Do not call `create_subagent` / `update_subagent` yet.\n"
            "2. ASK the user explicitly: \"To handle <case>, I'd like to add a new NLU "
            "intent `<intent_name>`. Is that OK?\"\n"
            "3. After the user confirms, in a SINGLE response do BOTH writes together:\n"
            "   - `update_config(block=agent_core, section=preprocessing.nlu_processor, "
            "values={intents: [<all existing intents>, '<new_intent>']})`\n"
            "   - `create_subagent` / `update_subagent` referencing the new intent.\n\n"
            "The `set_phase` tool runs a cross-block check that blocks the transition out "
            "of this phase if any subagent intent is missing from NLU intents. Do not rely "
            "on the renderer's silent auto-merge — fix it in the conversation.\n\n"
            "### global_intents vs subagent valid_intents — MUST NOT overlap\n"
            "- `global_intents` are intents handled by global routing rules — they apply "
            "across ALL subagents regardless of which subagent is active.\n"
            "- `valid_intents` on a subagent are intents that subagent handles.\n"
            "- **An intent MUST NOT appear in both.** Agent Core crashes at startup on any overlap.\n"
            "- For simple agents (greeting → main_qa → farewell), set `global_intents: []` "
            "and let each subagent's routing handle everything. Use `default_fallback_subagent_id` "
            "for unmatched intents.\n"
            "- Only use `global_intents` for intents that must fire from ANY subagent state "
            "(e.g. an emergency escalation). These intents must NOT appear in any subagent's "
            "`valid_intents`.\n\n"
            "**DEDUP RULE — apply immediately, in real time:**\n"
            "Every time you call `update_config` to add an intent to `global_intents`, "
            "immediately scan every subagent's `valid_intents` list (visible in the config "
            "state above). For each subagent that contains that intent, call `update_subagent` "
            "to remove it. Do NOT wait until the self-check at the end — fix the overlap the "
            "moment it is created.\n\n"
            "Example: if you set `global_intents: [emergency_alert]`, then immediately find "
            "every subagent whose `valid_intents` contains `emergency_alert` and remove it "
            "from those lists via `update_subagent`. One call per affected subagent.\n\n"
            "### Opening phrase guidance\n"
            "- Emitted ONCE per session, on the first post-consent turn. Subsequent "
            "turns run the subagent's normal `system_prompt`.\n"
            "- The subagent active on turn 1 is determined by Memory Layer: either the "
            "`is_start: true` subagent (new session) or a subagent restored from the "
            "previous session's `current_subagent` (returning user).\n"
            "- Tailor each subagent's `opening_phrase` to what the user knows at that "
            "point. E.g. a start subagent opens with a warm discovery question; a "
            "post-action subagent opens by acknowledging the previous action.\n"
            "- **`opening_phrase` MUST NOT be empty for any non-terminal subagent.** "
            "A blank opening_phrase means the agent says nothing on first entry to that "
            "subagent — the user gets silence or a stale response. Every subagent that "
            "is not `is_terminal: true` must have a non-empty opening_phrase. Write it "
            "in the domain language (same language as conversation messages).\n\n"
            "### Guide gap\n"
            "- The guide describes 5 'opening branches' as a single prompt-level "
            "conditional; we represent them via the subagent graph + `opening_phrase` "
            "field, because our subagent abstraction is richer than the guide assumes.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                ac_domain.RoutingRule,
                ac_domain.SubAgent,
                ac_domain.AgentWorkflowSection,
            )
            + "\n```"
            + connector_note
            + "\n\n➡️ Before calling `set_phase('observability')`, run this self-check:\n"
            "1. `workflow_id`, `version`, `agent_system_prompt` are all non-empty.\n"
            "2. Every non-terminal subagent has a non-empty `opening_phrase`.\n"
            "3. `default_fallback_subagent_id` matches an id in subagents.\n"
            "4. Every `next_subagent_id` in every routing rule matches a declared subagent id.\n"
            "5. No intent appears in both `global_intents` and any subagent's `valid_intents`.\n"
            "6. Every tool name in `global_tools` and per-subagent `tools` exists in connectors.\n"
            "7. `knowledge_retrieval` is in `global_tools` (if the agent has a KB), and NOT in "
            "any individual subagent's `tools`. If any subagent lists `knowledge_retrieval`, remove "
            "it via `update_subagent` — it belongs only in `global_tools`.\n"
            "Fix any violation before advancing. Then call `set_phase('observability')`."
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
            "- Auto-set domain tag: call `update_config(block=observability_layer, section=observability, values={domain: '<project_slug>'})`.\n"
            "  ⚠️ Use `section=observability` NOT `section=observability.domain` — the latter double-nests and crashes observability_layer.\n\n"
            "### Guide gap\n"
            "- DPG-specific: `turn_event` schema, async emit contract, OTel span "
            "attribute conventions (e.g. user_state.current, session.turn_count).\n\n"
            "### Conversation style for this phase\n"
            "Present the full observability configuration as **one block**. Suggest "
            "domain-appropriate outcome lifecycle states and quality signals based on "
            "the use case, then ask: 'Here is the suggested observability setup — do "
            "these look good, or would you like to change any?'\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                obs_domain.LifecycleState,
                obs_domain.MetricDefinition,
                obs_domain.OutcomesConfig,
                obs_domain.ObservabilitySection,
            )
            + "\n```\n\n"
            "➡️ When outcomes and quality signals are set, call `set_phase('reach')`."
        )

    if phase == "reach":
        return (
            "## Reach phase\n\n"
            "**What this phase is about:** Declare channel adapters and adapter-"
            "specific settings (voice config, web UI branding, campaign config).\n\n"
            "### What to include (from guide Appendix: Voice vs Chat)\n"
            "- Channels declared: any subset of voice / chat / web / cli.\n"
            "- **Voice** uses **Raya** as the only TTS/STT provider — do NOT ask the "
            "user which provider they want. There is no choice. Just configure Raya.\n"
            "- **Chat / web / whatsapp** require their respective adapter endpoints "
            "and webhook URLs.\n"
            "- Web UI branding (app name, icon, tagline) for web deployments.\n\n"
            "### How the dev-kit captures this\n"
            "- Auto-set observability domain: call `update_config(block=reach_layer, section=reach_layer.common.observability, values={domain: '<project_slug>'})`.\n"
            "  ⚠️ The path is `reach_layer.common.observability` (NOT `observability` or `observability.domain`).\n"
            "- Voice adapter: `update_config(block=reach_layer, "
            "section=channels.voice, values={raya: {stt_language: ..., tts_language: ..., voice_id: ...}, "
            "agent_core: {timeout_ms: 15000, fallback_phrase: ..., barge_in_acknowledgement: ''}})`\n"
            "- Web UI: `section=web, values={app_name: ..., icon: ..., tagline: ...}`\n"
            "- Campaign config (if outbound campaigns): `section=campaigns`.\n\n"
            "### Guide gap\n"
            "- Our Reach Layer is a distinct DPG block; the guide treats channels as "
            "adapter concerns without abstracting them.\n"
            "- TurnAssembler policy (semantic_gate, silence_trigger, max_wait_ceiling) "
            "lives in `agent_core.channels.<name>.turn_assembler` because TurnAssembler "
            "runs inside Agent Core — not covered by the guide.\n\n"
            "### Conversation style for this phase\n"
            "Channel selection was already done in the overview phase (check "
            "`selected_channels` in the config state above). Do NOT ask which channels "
            "again — go straight to configuring the selected channels.\n\n"
            "**IMPORTANT — reach_layer.channels must be set for every selected channel.**\n"
            "The DPG defaults provide baseline config for web, voice, and cli. You must "
            "still explicitly set the domain-specific fields for each selected channel — "
            "do not assume defaults are sufficient. Walk through selected_channels and "
            "configure each (web is always deployed; also configure voice and cli if selected):\n"
            "- **Web auth is pre-configured** — Google login (`auth.enabled: true`) is set "
            "in the DPG defaults and does NOT need to be set per-project. Do NOT set "
            "`auth.enabled`, `google_client_id`, or `cookie_secure` in the domain config.\n"
            "- Verify `reach_layer.channels.web` has `ui.*` branding fields set.\n"
            "- Verify `reach_layer.channels.voice` has `raya.voice_id`, `raya.stt_language`, "
            "`raya.tts_language`, `agent_core.fallback_phrase` set if voice is selected.\n"
            "- Verify `reach_layer.channels.cli` has `enabled: true` if cli is selected.\n\n"
            "Present ALL channel-specific config as **one block per channel** with "
            "suggested defaults:\n\n"
            "**For web channel — present all UI branding fields together:**\n"
            "Suggest app_name (from project name), app_tagline (from project description), "
            "app_icon (domain-appropriate emoji), agent_avatar, user_avatar, setup screen "
            "text (heading, subtitle, placeholder, hint, button label), session messages "
            "(new_session_msg, returning_user_msg), and confirmation dialogs — ALL in one "
            "block. Ask: 'Here is the suggested web UI configuration — do these look good, "
            "or would you like to change any?'\n"
            "Do NOT ask about app_name, then icon, then tagline separately.\n\n"
            "**For voice channel — present all voice config together:**\n"
            "The voice channel uses **Raya** as the TTS/STT provider. Voice supports "
            "**only one language at a time** — the schema's `voice_id_matches_language` "
            "validator enforces that `stt_language`, `tts_language`, and the chosen "
            "`voice_id` all belong to the same single language. This is unlike the "
            "web/text channel, which can serve multiple languages from `supported_languages`. "
            "Ask the user to pick ONE language for voice, auto-select the matching "
            "voice from the table below, and present all settings as one block for "
            "confirmation.\n\n"
            "**Raya voice lookup table (use ONLY these voice IDs):**\n"
            + _raya_voice_table() + "\n\n"
            "**How to configure voice:**\n"
            "1. Ask: 'Voice supports a single language. Which one language should the "
            "bot speak in over voice calls?' Show the available languages from the table "
            "above. Do NOT offer 'multi-language voice' — it is not supported. If the "
            "user names multiple languages, pick the most representative one and "
            "explain that voice is single-language by design.\n"
            "2. Auto-select the matching voice_id, stt_language, and tts_language.\n"
            "3. Present the full voice config block with defaults:\n"
            "   - `timeout_ms`: 15000 (default)\n"
            "   - `fallback_phrase`: Suggest a domain-appropriate phrase\n"
            "   - `barge_in_acknowledgement`: empty (silent)\n"
            "4. Ask for confirmation.\n\n"
            "⚠️ NEVER invent voice IDs. Schema validation will reject any ID not in "
            "the table above.\n\n"
            + _SCHEMA_PREAMBLE
            + "```python\n"
            + _schema_source(
                rl_domain.WebUiConfig,
                rl_domain.WebChannelSection,
                rl_domain.RayaVoiceConfig,
                rl_domain.VoiceAgentCoreClient,
                rl_domain.VoiceChannelSection,
                rl_domain.ChannelsSection,
                rl_domain.ReachLayerSection,
            )
            + "\n```\n\n"
            "➡️ Before calling `set_phase('review')`, run this self-check:\n"
            "1. `agent_core.channels.web` is configured (always required).\n"
            "2. `agent_core.channels.<X>` is configured for every channel in selected_channels.\n"
            "3. `reach_layer.channels.<X>` is non-null and has domain-specific fields set "
            "for every channel in selected_channels.\n"
            "Fix any missing channel config before advancing. Then call `set_phase('review')`."
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "**What this phase is about:** Run a full schema-coverage check across "
            "all 7 DPG blocks and report any empty required fields for correction.\n\n"
            "Use the `validate_config` tool to run the check. It reads every block's "
            "YAML template, compares against the accumulated config, and lists empty "
            "required fields with exact paths (e.g. `reach_layer.channels.voice."
            "terminal_word`, `trust_layer.dignity_check.questions[2]`).\n\n"
            "For each missing field: ask the user for the value, call the appropriate "
            "`update_config` tool, and re-run `validate_config` until the report is "
            "clean.\n\n"
            "### Cross-block invariants to verify manually\n"
            "After `validate_config` is clean, verify these cross-block rules by "
            "inspecting the config state above:\n\n"
            "1. **Tool names exist in connectors** — every name in any subagent's `tools` "
            "list or in `global_tools` must match a `name` in `connectors.read`, "
            "`connectors.write`, `connectors.identity`, or `connectors.internal`. "
            "If a tool is listed but its connector is missing, add the connector now.\n\n"
            "2. **knowledge_retrieval placement** — if `knowledge_retrieval` appears in "
            "any tool list or in `global_tools`, it must exist in `connectors.internal` "
            "(not `connectors.read`). Internal connectors have a `route` field; external "
            "connectors have a `base_url`.\n\n"
            "3. **global_intents ∩ subagent valid_intents = empty** — no intent may appear "
            "in both `agent_workflow.global_intents` and any subagent's `valid_intents`. "
            "Agent Core crashes at startup if there is any overlap.\n\n"
            "4. **NLU intents cover intent_filters** — every key in "
            "`knowledge_engine.knowledge.blocks.static_knowledge_base.intent_filters` "
            "must be present in `agent_core.preprocessing.nlu_processor.intents`. "
            "Unrecognised intents bypass the filter and return all doc_types.\n\n"
            "5. **Voice configured if voice selected** — if voice is in `selected_channels`, "
            "then `reach_layer.channels.voice` must be non-null and include `raya.voice_id`, "
            "`raya.stt_language`, and `raya.tts_language`.\n\n"
            "6. **agent_core.channels set for every selected channel** — for each channel in "
            "`selected_channels` (plus web, which is always deployed), `agent_core.channels.<name>` "
            "must exist in the YAML. Missing entries cause `ValueError: Unsupported channel` at "
            "Agent Core startup. Check web, cli, and voice explicitly.\n\n"
            "7. **reach_layer.channels set for every selected channel** — for each channel in "
            "`selected_channels`, `reach_layer.channels.<name>` must be non-null. The DPG "
            "defaults provide all three, but a domain config that nullifies one causes the "
            "reach layer service to fail to start.\n\n"
            "8. **default_fallback_subagent_id is a declared subagent id** — if non-empty, "
            "the value must exactly match one of `subagents[*].id`. A mismatch causes a "
            "KeyError at runtime whenever the fallback fires.\n\n"
            "9. **routing.next_subagent_id values are declared subagent ids** — every "
            "`next_subagent_id` in every `routing` rule (including `global_routing`) must "
            "match a declared subagent id. Walk every routing rule in every subagent.\n\n"
            "10. **opening_phrase non-empty for every non-terminal subagent** — every subagent "
            "that is not `is_terminal: true` must have a non-empty `opening_phrase`. An empty "
            "opening_phrase means the agent says nothing on first entry to that subagent.\n\n"
            "11. **workflow top-level fields set** — `agent_workflow.workflow_id`, "
            "`agent_workflow.version`, and `agent_workflow.agent_system_prompt` must all be "
            "non-empty. These are required fields; Agent Core fails Pydantic validation at "
            "startup if any is missing.\n\n"
            "12. **dignity_check questions populated** — if `trust_layer.dignity_check.enabled` "
            "is true, `questions` must be a non-empty list of strings and `fail_action` must "
            "be set. An empty `questions: []` means the dignity check always passes — no "
            "protection.\n\n"
            "13. **observability.domain is a non-empty string in every block** — inspect "
            "each block in the config state above. Every block's `observability.domain` "
            "(or `reach_layer.common.observability.domain` for reach_layer) must be a plain "
            "string matching the project slug. A dict value (e.g. `domain: {domain: 'value'}`) "
            "means the agent incorrectly used `section=observability.domain` instead of "
            "`section=observability` — this crashes each block at startup. Fix by calling "
            "`update_config(block=<block>, section=observability, values={domain: '<slug>'})` "
            "for any block where the value is a dict. "
            "Blocks to verify: agent_core, knowledge_engine, action_gateway, trust_layer, "
            "memory_layer, observability_layer, and reach_layer (check `reach_layer.common.observability.domain`).\n\n"
            "Fix any violations before announcing completion. Once everything is clean, "
            "tell the user the configuration is ready and they can move to the **Deploy** "
            "step in the wizard to push it to their DPG infrastructure. Do NOT name a "
            "tool to call — deploy is a wizard step the user clicks through, not a tool "
            "you invoke."
        )

    return ""
