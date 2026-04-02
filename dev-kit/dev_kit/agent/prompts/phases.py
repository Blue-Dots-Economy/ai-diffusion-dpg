"""
dev-kit/dev_kit/agent/prompts/phases.py

Phase-specific additions to the system prompt. Each phase adds focused
schema context so the LLM knows exactly what fields to collect.
"""
from __future__ import annotations

from dev_kit.loader import get_schema_descriptions

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

    Args:
        phase: Current conversation phase name.
        available_connectors: Connector names declared in agent_core (used in workflow phase).

    Returns:
        Additional system prompt text for the phase, or empty string if none.
    """
    if phase == "overview":
        return ""

    if phase == "language":
        agent_desc = get_schema_descriptions("agent_core")
        ke_desc = get_schema_descriptions("knowledge_engine")
        relevant = {
            k: v for k, v in {**agent_desc, **ke_desc}.items()
            if any(kw in k for kw in ["primary_model", "fallback_model", "language", "model", "transliteration"])
        }
        lines = ["## Schema context for Language & Models phase", ""]
        for path, desc in relevant.items():
            lines.append(f"- `{path}`: {desc}")
        return "\n".join(lines)

    if phase == "knowledge":
        desc = get_schema_descriptions("knowledge_engine")
        lines = ["## Schema context for Knowledge phase", ""]
        for path, desc_text in desc.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "## Glossary format",
            "Each mapping: `{colloquial: [list of synonyms], canonical: standard_identifier}`",
            "",
            "## Source types",
            "- `static` — PDF/CSV/markdown ingested into the vector store",
            "- `always_include` — always retrieved regardless of intent",
            "",
            "## Intent filter format",
            "Map of intent → list of doc_types to retrieve, e.g. `{job_query: [role, employer]}`",
        ]
        return "\n".join(lines)

    if phase == "memory":
        lines = [
            "## Schema context for Memory phase",
            "",
            "## Session schema field types",
            "- `{type: enum, values: [...], default: value}` — for categorical fields",
            "- `{type: string, default: ''}` — for free-text fields",
            "- `{type: int, default: 0}` — for counters",
            "- `{type: list, default: []}` — for list fields",
            "",
            "## Persistent graph structure",
            "- `user_node.label` — Neo4j/Memgraph label for the user node (e.g. 'User')",
            "- `user_node.key` — unique user identifier property (e.g. 'user_id')",
            "- `subnodes` — map of subnode name → {rel, declared_fields, [child]}",
            "",
            "## merge_on_session_end",
            "List of {session_field, target} — promotes session values to graph nodes on close.",
        ]
        return "\n".join(lines)

    if phase == "trust":
        desc = get_schema_descriptions("trust_layer")
        lines = ["## Schema context for Trust phase", ""]
        for path, desc_text in desc.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "Note: trust_layer config is STATUS: draft — block template not yet finalised.",
            "Collect blocked phrases, escalation topics, and output restrictions.",
        ]
        return "\n".join(lines)

    if phase == "connectors":
        agent_desc = get_schema_descriptions("agent_core")
        gw_desc = get_schema_descriptions("action_gateway")
        relevant = {k: v for k, v in {**agent_desc, **gw_desc}.items() if "connector" in k.lower()}
        lines = [
            "## Schema context for Connectors phase",
            "",
            "## Connector types",
            "- `read` — retrieves data (no consent required)",
            "- `write` — modifies external state (Trust Layer consent required before call)",
            "- `identity` — identity verification connectors",
            "- `internal` — routes to another DPG block (e.g. knowledge_retrieval → knowledge_engine)",
            "",
        ]
        for path, desc_text in relevant.items():
            lines.append(f"- `{path}`: {desc_text}")
        lines += [
            "",
            "Note: action_gateway config is STATUS: draft.",
            "Collect connector names, descriptions, and input_schema for each connector.",
        ]
        return "\n".join(lines)

    if phase == "workflow":
        connector_note = ""
        if available_connectors:
            connector_note = f"\n\nAvailable connectors (declared in Connectors phase): {', '.join(available_connectors)}"
        return (
            "## Workflow Design phase\n\n"
            "Build the subagent state machine step by step:\n"
            "1. Propose an initial flow based on everything you know about this domain.\n"
            "2. Walk through each subagent: purpose, system_prompt, valid_intents, routing rules.\n"
            "3. Use `create_subagent` for each node and `add_routing_rule` for each edge.\n"
            "4. Suggest intents based on the conversation flow. Keep them specific to this domain.\n"
            "5. After the graph is built, use `update_config` to set `agent_workflow.workflow_id`,\n"
            "   `agent_workflow.version`, `agent_workflow.agent_system_prompt`, `agent_workflow.global_intents`,\n"
            "   `agent_workflow.global_routing`, and `agent_workflow.default_fallback_subagent_id`.\n"
            "6. Also set `preprocessing.nlu_processor.intents` (flat list) and `preprocessing.nlu_processor.entities`.\n\n"
            + _WORKFLOW_EXAMPLE
            + connector_note
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
