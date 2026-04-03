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
        return ""

    if phase == "language":
        return (
            "## Language & Models phase — valid fields\n\n"
            "Use `update_config` with block=`agent_core`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('agent_core'))}\n\n"
            "Set `agent` and `preprocessing` sections only. "
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent", "preprocessing"])
            + "```"
        )

    if phase == "knowledge":
        return (
            "## Knowledge phase — valid fields\n\n"
            "Use `update_config` with block=`knowledge_engine`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('knowledge_engine'))}\n\n"
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + load_template_text("knowledge_engine")
            + "```"
        )

    if phase == "memory":
        return (
            "## Memory phase — valid fields\n\n"
            "Use `update_config` with block=`memory_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('memory_layer'))}\n\n"
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + load_template_text("memory_layer")
            + "```"
        )

    if phase == "trust":
        return (
            "## Trust phase — valid fields\n\n"
            "Use `update_config` with block=`trust_layer`. "
            f"Valid top-level sections: {', '.join(get_valid_sections('trust_layer'))}\n\n"
            "Use EXACTLY the key names shown in the template below — do not rename any key:\n\n"
            "```yaml\n"
            + load_template_text("trust_layer")
            + "```"
        )

    if phase == "connectors":
        return (
            "## Connectors phase — valid fields\n\n"
            "For agent_core connectors, use `update_config` with block=`agent_core`, "
            "section=`connectors`. "
            "For action_gateway endpoints, use block=`action_gateway`.\n\n"
            "Use EXACTLY the key names shown in the templates below — do not rename any key:\n\n"
            "**agent_core connectors section:**\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["connectors"])
            + "```\n\n"
            "**action_gateway template:**\n"
            "```yaml\n"
            + load_template_text("action_gateway")
            + "```"
        )

    if phase == "workflow":
        connector_note = ""
        if available_connectors:
            connector_note = f"\n\nAvailable connectors (declared in Connectors phase): {', '.join(available_connectors)}"
        return (
            "## Workflow Design phase\n\n"
            "Build the subagent state machine step by step:\n"
            "1. Use `create_subagent` for each node and `add_routing_rule` for each edge.\n"
            "2. After the graph is built, use `update_config` to set `agent_workflow.workflow_id`,\n"
            "   `agent_workflow.version`, `agent_workflow.agent_system_prompt`, `agent_workflow.global_intents`,\n"
            "   `agent_workflow.global_routing`, and `agent_workflow.default_fallback_subagent_id`.\n"
            "3. Also set `preprocessing.nlu_processor.intents` (flat list) and `preprocessing.nlu_processor.entities`.\n\n"
            "Use EXACTLY the key names shown in the template below for each subagent — do not rename any key:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent_workflow"])
            + "```"
            + connector_note
            + "\n\n"
            + _WORKFLOW_EXAMPLE
        )

    if phase == "review":
        return (
            "## Review phase\n\n"
            "All configs have been generated. Review the accumulated state above.\n"
            "If any required field is missing or incorrect, use the appropriate tool to fix it.\n"
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
