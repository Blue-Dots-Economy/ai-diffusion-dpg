"""
dev-kit/dev_kit/agent/prompts/base.py

Builds the full system prompt for a given conversation phase.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.prompts.phases import get_phase_addition

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
tools to build their domain configuration. Be conversational, ask one question at a time,
and confirm your understanding before moving to a new topic.

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
    available_connectors: list[str] | None = None,
) -> str:
    """Build the full system prompt for the given conversation phase.

    Args:
        project_name: Human-readable project name.
        project_description: Brief project description.
        accumulator: Current config accumulator.
        phase: Current phase name (e.g. "language", "workflow").
        checkpoint_summaries: List of summary strings from prior phase checkpoints.
        available_connectors: Connector names declared in Connectors phase (for workflow prompt).

    Returns:
        Full system prompt string.
    """
    sections = [_DPG_OVERVIEW]

    # Project context
    if project_name:
        sections.append(f"## Project\nName: {project_name}\nDescription: {project_description}")

    # Prior phase summaries
    if checkpoint_summaries:
        sections.append("## Prior phase summaries\n" + "\n---\n".join(checkpoint_summaries))

    # Current config state
    sections.append(accumulator.summary())

    # Current phase
    sections.append(f"## Current phase: {phase}")

    # Phase-specific schema context
    addition = get_phase_addition(phase, available_connectors=available_connectors)
    if addition:
        sections.append(addition)

    return "\n\n".join(sections)
