"""
dev-kit/dev_kit/agent/tools.py

Tool definitions (JSON schemas for Claude) and handler dispatch for the
DPG conversation agent. All 10 tools are defined here.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator, ConfigStatus

# ---------------------------------------------------------------------------
# Tool JSON schema definitions passed to the Claude API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "set_project_meta",
        "description": "Set the project name, description, and domain slug. Call once you understand the use case from the Domain Overview phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable project name"},
                "slug": {"type": "string", "description": "URL-safe identifier, lowercase with hyphens, e.g. rural-jobs-assistant"},
                "description": {"type": "string", "description": "One-paragraph description of the use case"},
                "user_persona": {"type": "string", "description": "Who the end users are"},
                "domain_summary": {"type": "string", "description": "The domain and problem the AI agent addresses"},
            },
            "required": ["name", "slug", "description"],
        },
    },
    {
        "name": "update_config",
        "description": "Update a section of a block's domain config. Values are deep-merged into the current state for that block.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
                "section": {
                    "type": "string",
                    "description": "Dot-notation path to the config section, e.g. 'preprocessing.nlu_processor' or 'conversation'",
                },
                "values": {"type": "object", "description": "Key-value pairs to merge into the section"},
            },
            "required": ["block", "section", "values"],
        },
    },
    {
        "name": "set_phase",
        "description": "Advance the conversation to the next phase. Call when you have collected enough information for the current phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "enum": ["overview", "language", "knowledge", "memory", "trust", "connectors", "workflow", "review"],
                },
            },
            "required": ["phase"],
        },
    },
    {
        "name": "create_subagent",
        "description": "Add a new subagent node to the agent_workflow. Appears as a node in the conversation flow graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case identifier"},
                "name": {"type": "string", "description": "Human-readable name"},
                "description": {"type": "string", "description": "What this subagent does"},
                "system_prompt": {"type": "string", "description": "LLM instructions for this conversation state"},
                "is_start": {"type": "boolean", "default": False},
                "is_terminal": {"type": "boolean", "default": False},
                "valid_intents": {"type": "array", "items": {"type": "string"}, "default": []},
                "tools": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["id", "name", "description", "system_prompt"],
        },
    },
    {
        "name": "update_subagent",
        "description": "Modify an existing subagent's fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "fields": {"type": "object", "description": "Any subset of the subagent definition to update"},
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "add_routing_rule",
        "description": "Add a routing rule (transition edge) from one subagent to another, triggered by an intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string", "description": "Intent that triggers this transition. Use '*' for catch-all."},
                "next_subagent_id": {"type": "string"},
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {"type": "string", "enum": ["eq", "not_eq", "gt", "lt", "gte", "lte"]},
                            "value": {},
                        },
                        "required": ["field", "operator", "value"],
                    },
                    "description": "Optional session state conditions",
                    "default": [],
                },
                "session_writes": {
                    "type": "object",
                    "description": "Optional session field writes when this rule fires",
                    "default": {},
                },
            },
            "required": ["from_subagent_id", "intent", "next_subagent_id"],
        },
    },
    {
        "name": "update_routing_rule",
        "description": "Modify an existing routing rule identified by from_subagent_id + intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_subagent_id": {"type": "string"},
                "intent": {"type": "string"},
                "fields": {"type": "object", "description": "Fields to update on the routing rule"},
            },
            "required": ["from_subagent_id", "intent", "fields"],
        },
    },
    {
        "name": "remove_subagent",
        "description": "Remove a subagent and all its outgoing routing rules from the workflow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "ID of the subagent to remove"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "finalize_config",
        "description": "Mark a config as complete. Use after confirming a block's config is fully specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
            },
            "required": ["block"],
        },
    },
    {
        "name": "rollback_to_checkpoint",
        "description": "Signal that the conversation should roll back to a previous checkpoint. Use only when the user explicitly requests it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Checkpoint phase identifier, e.g. '01_overview'"},
            },
            "required": ["phase"],
        },
    },
]

# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


class ToolHandler:
    """Dispatches Claude tool calls to their handler methods.

    Handlers modify the ConfigAccumulator and/or the shared mutable state dict.

    Args:
        accumulator: The project's config accumulator.
        state: Mutable dict with keys 'phase' (str) and 'phase_changed' (str | None).
               Handlers set state['phase_changed'] to the new phase name when set_phase
               is called, so the ConversationEngine can trigger a checkpoint.
    """

    def __init__(self, accumulator: ConfigAccumulator, state: dict) -> None:
        self._acc = accumulator
        self._state = state

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call to the appropriate handler.

        Args:
            tool_name: Tool name matching one of TOOL_DEFINITIONS.
            tool_input: Tool input values from the LLM.

        Returns:
            Result string to send back as tool_result content.

        Raises:
            ValueError: If tool_name is not recognised.
        """
        handlers = {
            "set_project_meta": self._handle_set_project_meta,
            "update_config": self._handle_update_config,
            "set_phase": self._handle_set_phase,
            "create_subagent": self._handle_create_subagent,
            "update_subagent": self._handle_update_subagent,
            "add_routing_rule": self._handle_add_routing_rule,
            "update_routing_rule": self._handle_update_routing_rule,
            "remove_subagent": self._handle_remove_subagent,
            "finalize_config": self._handle_finalize_config,
            "rollback_to_checkpoint": self._handle_rollback_to_checkpoint,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name!r}")
        return handler(tool_input)

    def _handle_set_project_meta(self, inputs: dict) -> str:
        self._state["project_meta"].update(inputs)
        return f"Project meta updated: {inputs.get('name', '')} ({inputs.get('slug', '')})"

    def _handle_update_config(self, inputs: dict) -> str:
        self._acc.update(inputs["block"], inputs["section"], inputs["values"])
        return f"ok: updated {inputs['block']}.{inputs['section']}"

    def _handle_set_phase(self, inputs: dict) -> str:
        self._state["phase_changed"] = inputs["phase"]
        return f"Phase advancing to: {inputs['phase']}"

    def _handle_create_subagent(self, inputs: dict) -> str:
        existing = [
            sa for sa in self._acc.get_block("agent_core")
            .get("agent_workflow", {})
            .get("subagents", [])
            if sa.get("id") == inputs["id"]
        ]
        if existing:
            return f"Subagent '{inputs['id']}' already exists — use update_subagent to modify it."
        sa = {
            "id": inputs["id"],
            "name": inputs["name"],
            "description": inputs["description"],
            "is_start": inputs.get("is_start", False),
            "is_terminal": inputs.get("is_terminal", False),
            "special_handler": None,
            "valid_intents": inputs.get("valid_intents", []),
            "tools": inputs.get("tools", []),
            "system_prompt": inputs["system_prompt"],
            "routing": [],
        }
        self._acc.set_subagent(sa)
        return f"Subagent '{inputs['id']}' created."

    def _handle_update_subagent(self, inputs: dict) -> str:
        try:
            self._acc.update_subagent(inputs["id"], inputs["fields"])
            return f"Subagent '{inputs['id']}' updated."
        except ValueError as exc:
            return str(exc)

    def _handle_add_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.add_routing_rule(
                inputs["from_subagent_id"],
                inputs["intent"],
                inputs["next_subagent_id"],
                inputs.get("conditions", []),
                inputs.get("session_writes", {}),
            )
            return (
                f"Routing rule added: {inputs['from_subagent_id']}"
                f" --[{inputs['intent']}]--> {inputs['next_subagent_id']}"
            )
        except ValueError as exc:
            return str(exc)

    def _handle_update_routing_rule(self, inputs: dict) -> str:
        try:
            self._acc.update_routing_rule(inputs["from_subagent_id"], inputs["intent"], inputs["fields"])
            return f"Routing rule updated: {inputs['from_subagent_id']} --[{inputs['intent']}]-->"
        except ValueError as exc:
            return str(exc)

    def _handle_remove_subagent(self, inputs: dict) -> str:
        removed = self._acc.remove_subagent(inputs["id"])
        if not removed:
            return f"error: subagent '{inputs['id']}' not found — nothing removed."
        return f"Subagent '{inputs['id']}' removed."

    def _handle_finalize_config(self, inputs: dict) -> str:
        self._acc.set_status(inputs["block"], ConfigStatus.COMPLETE)
        return f"Config '{inputs['block']}' marked complete."

    def _handle_rollback_to_checkpoint(self, inputs: dict) -> str:
        self._state["rollback_to"] = inputs["phase"]
        return f"Rollback to checkpoint '{inputs['phase']}' requested."
