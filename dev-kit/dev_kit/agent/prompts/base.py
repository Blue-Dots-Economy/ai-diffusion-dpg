"""
dev-kit/dev_kit/agent/prompts/base.py

Builds the full system prompt for a given conversation phase.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.phases import get_phase_addition

# GH-137: agent-type taxonomy driven by the 3-question decision tree in the tier phase.
AGENT_TYPES: list[str] = ["transactional", "informational", "agentic", "conversational"]

# GH-137: per-phase requirement matrix by agent type.
# Values are 'required', 'optional', or 'skip'. Consulted by set_phase and
# skip_optional_phase to gate visits and auto-advance.
SHEET_REQUIREMENTS: dict[str, dict[str, str]] = {
    "tier":          {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "overview":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "language":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "knowledge":     {"transactional": "skip",     "informational": "required", "agentic": "optional", "conversational": "optional"},
    "memory":        {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "user_state":    {"transactional": "skip",     "informational": "skip",     "agentic": "skip",     "conversational": "required"},
    "trust":         {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "tools":         {"transactional": "required", "informational": "skip",     "agentic": "required", "conversational": "required"},
    "workflow":      {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "observability": {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "reach":         {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
    "review":        {"transactional": "required", "informational": "required", "agentic": "required", "conversational": "required"},
}

_DPG_OVERVIEW = """
You are a DPG Configuration Assistant. You help users configure AI-powered conversation agents
on the DPG (Digital Public Good) framework without needing to understand YAML or code.

The DPG has 7 building blocks:
- Agent Core: orchestrates the conversation, calls the LLM, manages the turn loop.
- Knowledge Engine: assembles LLM prompts from user intent + domain knowledge (RAG).
- Memory Layer: stores session state and persistent user profiles.
- Trust Layer: safety gate that blocks harmful input/output and enforces escalation rules.
- Action Gateway: executes external API calls requested by the LLM.
- Reach Layer: handles input channels (WhatsApp, web, voice) and delivers responses.
- Observability Layer: async observability — logs turns and quality metrics.

Your job is to interview the user, understand their use case, and call the appropriate
tools to build their domain configuration. Be conversational but efficient.

**Conversation style — batch related fields together:**
- Do NOT ask one question at a time for every config field. Instead, group related fields
  into a single block, show sensible defaults or suggestions for each, and ask the user
  to confirm or edit the whole group at once.
- If a phase has many fields, split them into 2-3 logical groups of related items.
  Present each group with defaults and ask for confirmation before moving to the next group.
- The user should be able to confirm a whole group with a single "looks good" instead
  of answering many individual questions.
- Only ask individual questions for fields that genuinely need user-specific input
  (e.g. "What crops does your agent cover?"). For standard config fields with reasonable
  defaults, present the defaults and let the user override.

**CRITICAL — never show YAML, JSON, or code to the user:**
- The user does not understand YAML or technical config syntax. NEVER show raw YAML
  blocks, JSON, or code snippets in your messages.
- Present configuration as plain-language bullet points or simple tables. Example:
  WRONG: "session:\n  ttl_minutes: 1440\n  schema:\n    topic_area: ..."
  RIGHT: "- Session timeout: 24 hours\n- Tracked fields: topic area, question count\n- User data: anonymous (deleted after session ends)"
- Keep explanations brief and non-technical. The user cares about WHAT the agent
  will do, not HOW the config is structured internally.
- You handle the YAML internally via tool calls — the user never needs to see it.

Important rules:
- Never make up connector names, API endpoints, or model IDs. Ask the user.
- When designing the workflow, propose an initial state machine based on what you know, then refine.
- Keep system prompts in subagents concise (3-8 sentences). They guide the LLM per state.
- You MUST complete every phase in order. Do NOT skip any phase. Each phase configures a different DPG block.
- Do NOT pre-empt future phases. Build the workflow only in the workflow phase, not in overview.
""".strip()


def build_system_prompt(
    project_name: str,
    project_description: str,
    accumulator: ConfigAccumulator,
    phase: str,
    checkpoint_summaries: list[str],
    project_slug: str = "",
    available_tools: list[str] | None = None,
) -> str:
    """Build the full system prompt for the given conversation phase.

    Args:
        project_name: Human-readable project name.
        project_description: Brief project description.
        accumulator: Current config accumulator.
        phase: Current phase name (e.g. "language", "workflow").
        checkpoint_summaries: List of summary strings from prior phase checkpoints.
        project_slug: URL-safe slug used as the domain identifier across all blocks.
        available_tools: Tool IDs declared in the Tools phase (for workflow prompt).

    Returns:
        Full system prompt string.
    """
    sections = [_DPG_OVERVIEW]

    # Project context
    if project_name:
        slug_line = f"\nSlug: {project_slug}" if project_slug else ""
        sections.append(f"## Project\nName: {project_name}{slug_line}\nDescription: {project_description}")

    # Prior phase summaries
    if checkpoint_summaries:
        sections.append("## Prior phase summaries\n" + "\n---\n".join(checkpoint_summaries))

    # Current config state
    sections.append(accumulator.summary())

    # Current phase
    sections.append(f"## Current phase: {phase}")

    # Phase-specific schema context
    addition = get_phase_addition(phase, available_tools=available_tools)
    if addition:
        sections.append(addition)

    return "\n\n".join(sections)
