"""
dev-kit/dev_kit/agent/tools.py

Tool definitions (JSON schemas for Claude) and handler dispatch for the
DPG conversation agent.
"""
from __future__ import annotations

from dev_kit.agent.accumulator import BLOCKS, PHASES, ConfigAccumulator, ConfigStatus
from dev_kit.schemas.loader import get_valid_sections

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
        "description": (
            "Update a section of a block's domain config. Values are deep-merged into the current state for that block.\n\n"
            "Valid top-level sections per block (the first segment of the dot-notation path):\n"
            + "\n".join(
                f"  - {block}: {', '.join(get_valid_sections(block))}"
                for block in BLOCKS
            )
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "enum": BLOCKS},
                "section": {
                    "type": "string",
                    "description": (
                        "Dot-notation path to the config section. "
                        "The first segment MUST be one of the valid top-level sections listed in the tool description. "
                        "Examples: 'agent', 'preprocessing.nlu_processor', 'agent_workflow', 'trust', 'state.session'"
                    ),
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
                    "enum": ["overview", "language", "knowledge", "memory", "trust", "tools", "workflow", "observability", "reach", "review"],
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
                            "operator": {"type": "string", "enum": ["eq", "not_eq", "gt", "lt", "in"]},
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
    {
        "name": "parse_openapi_spec",
        "description": (
            "Parse a raw OpenAPI 3.0/3.1 spec (JSON or YAML string) and return a list of candidate tool definitions. "
            "Use this when the user uploads or pastes an OpenAPI spec. "
            "The returned candidates help you decide which endpoints to add with add_rest_api_tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec_json": {
                    "type": "string",
                    "description": "The full OpenAPI spec as a JSON or YAML string",
                },
            },
            "required": ["spec_json"],
        },
    },
    {
        "name": "add_rest_api_tool",
        "description": (
            "Add a REST API tool to the Action Gateway config. "
            "Call this once per tool after confirming details with the user — whether from an OpenAPI spec or collected conversationally. "
            "This also auto-creates the matching connector in agent_core.connectors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case tool ID, e.g. onest_market_lookup"},
                "category": {"type": "string", "enum": ["read", "write", "identity"], "description": "read = no consent; write/identity = Trust Layer consent required"},
                "description": {"type": "string", "description": "What this tool does — shown to the LLM for routing decisions"},
                "base_url": {"type": "string", "description": "API base URL, e.g. https://api.example.com/v2"},
                "auth_type": {"type": "string", "enum": ["none", "api_key", "bearer", "oauth2"]},
                "auth_header": {"type": "string", "description": "Header name for api_key auth, e.g. X-API-KEY"},
                "auth_secret_env": {"type": "string", "description": "Env var name holding the API key"},
                "timeout_ms": {"type": "integer", "default": 5000},
                "endpoints": {
                    "type": "array",
                    "description": "One or more endpoint definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                            "path": {"type": "string"},
                            "params": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "source": {"type": "string", "enum": ["agent", "static"]},
                                        "type": {"type": "string"},
                                        "required": {"type": "boolean"},
                                        "description": {"type": "string"},
                                        "value": {"description": "Fixed value when source is static"},
                                        "default": {"description": "Default value for optional agent params"},
                                    },
                                    "required": ["name", "source", "type"],
                                },
                            },
                        },
                        "required": ["name", "method", "path"],
                    },
                },
            },
            "required": ["id", "category", "description", "base_url", "auth_type", "endpoints"],
        },
    },
    {
        "name": "discover_mcp_tools",
        "description": (
            "Fetch the list of available tools from an MCP server by calling its tools/list endpoint. "
            "Use this when the user provides an MCP server URL. "
            "Returns the raw tools list so you can present options to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mcp_server_url": {
                    "type": "string",
                    "description": "Base URL of the MCP server, e.g. https://mcp.example.com",
                },
            },
            "required": ["mcp_server_url"],
        },
    },
    {
        "name": "add_mcp_tool",
        "description": (
            "Register an MCP server with the Action Gateway. "
            "Call this once per MCP server — the adapter auto-discovers all available tools at startup. "
            "Each discovered tool is registered as '{id}.{tool_name}' "
            "(e.g. 'obsrv_docs.searchDocumentation'). "
            "Use these namespaced names when assigning tools to subagents. "
            "Do NOT call this once per tool — one call per server is correct."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Unique snake_case namespace for this MCP server's tools "
                        "(e.g. 'obsrv_docs'). All tools discovered from the server "
                        "are prefixed with this id."
                    ),
                },
                "category": {"type": "string", "enum": ["read", "write", "identity"]},
                "description": {
                    "type": "string",
                    "description": "What this MCP server provides — used in Action Gateway config.",
                },
                "mcp_server_url": {"type": "string", "description": "Base URL of the MCP server"},
                "transport": {
                    "type": "string",
                    "enum": ["sse", "streamable_http"],
                    "default": "sse",
                    "description": (
                        "MCP transport protocol. Use 'streamable_http' for GitBook, Notion, "
                        "and other hosted servers (POST-only, MCP spec 2025-03-26). "
                        "Use 'sse' for self-hosted servers that support the older SSE transport."
                    ),
                },
                "timeout_ms": {"type": "integer", "default": 5000},
            },
            "required": ["id", "category", "description", "mcp_server_url"],
        },
    },
    {
        "name": "set_reach_channels",
        "description": (
            "Record which deployment channels the user wants (web, cli, voice). "
            "Call this at the start of the reach phase, before collecting per-channel config. "
            "Only the selected channels will be configured."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["web", "cli", "voice"]},
                    "description": "One or more of: web, cli, voice",
                    "minItems": 1,
                },
            },
            "required": ["channels"],
        },
    },
]

# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def _parse_sse_json(text: str) -> dict | None:
    """Extract the first JSON-RPC payload from an SSE response body.

    SSE lines have the form ``data: <json>``.  This function scans the
    response text for the first such line and returns the parsed dict, or
    ``None`` if no ``data:`` line is found or the payload is not valid JSON.

    Args:
        text: Raw response body string.

    Returns:
        Parsed dict from the first ``data:`` line, or None.
    """
    import json

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[len("data:"):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return None


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
            "parse_openapi_spec": self._handle_parse_openapi_spec,
            "add_rest_api_tool": self._handle_add_rest_api_tool,
            "discover_mcp_tools": self._handle_discover_mcp_tools,
            "add_mcp_tool": self._handle_add_mcp_tool,
            "set_reach_channels": self._handle_set_reach_channels,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name!r}")
        return handler(tool_input)

    def _handle_set_project_meta(self, inputs: dict) -> str:
        self._state["project_meta"].update(inputs)
        return f"Project meta updated: {inputs.get('name', '')} ({inputs.get('slug', '')})"

    def _handle_update_config(self, inputs: dict) -> str:
        from dev_kit.schema import validate_partial

        block = inputs["block"]
        section = inputs["section"]
        values = inputs["values"]

        # Build the nested partial and validate key names before writing.
        partial: dict = {}
        node = partial
        parts = section.split(".")
        for part in parts[:-1]:
            node[part] = {}
            node = node[part]
        node[parts[-1]] = values

        errors = validate_partial(block, partial)
        if errors:
            error_lines = "\n".join(f"  - {e}" for e in errors)
            return (
                f"ERROR — config NOT written. Invalid key names detected:\n{error_lines}\n\n"
                f"Refer to the YAML template shown in the phase prompt for the exact key names. "
                f"Correct the section path or key names and retry update_config."
            )

        self._acc.update(block, section, values)
        return f"ok: updated {block}.{section}"

    def _handle_set_phase(self, inputs: dict) -> str:
        requested = inputs["phase"]
        current = self._state.get("phase", "overview")
        current_idx = PHASES.index(current) if current in PHASES else 0
        requested_idx = PHASES.index(requested) if requested in PHASES else -1

        # Only allow moving to the immediately next phase (or staying on the same one).
        # Skipping phases is not permitted — each phase must be visited in order.
        if requested_idx > current_idx + 1:
            next_phase = PHASES[current_idx + 1]
            return (
                f"ERROR — cannot skip from '{current}' to '{requested}'. "
                f"You must complete '{next_phase}' next. "
                f"Call set_phase('{next_phase}') when you are ready."
            )
        if requested_idx < current_idx:
            return (
                f"ERROR — cannot go back from '{current}' to '{requested}'. "
                f"Use rollback_to_checkpoint if you need to revisit an earlier phase."
            )
        self._state["phase_changed"] = requested
        return f"Phase advancing to: {requested}"

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

    def _handle_parse_openapi_spec(self, inputs: dict) -> str:
        """Parse an OpenAPI spec string and return candidate tool definitions as JSON.

        Args:
            inputs: Dict with 'spec_json' key containing a JSON or YAML string.

        Returns:
            JSON array of candidate tool dicts, or an ERROR string on failure.
        """
        import json
        import yaml as _yaml
        from dev_kit.agent.openapi_parser import parse_openapi_spec

        spec_json = inputs.get("spec_json", "")
        try:
            try:
                spec = json.loads(spec_json)
            except json.JSONDecodeError:
                spec = _yaml.safe_load(spec_json)
            if not isinstance(spec, dict):
                return "ERROR: spec must be a JSON or YAML object"
        except Exception as exc:
            return f"ERROR: could not parse spec — {exc}"

        try:
            tools = parse_openapi_spec(spec)
        except ValueError as exc:
            return f"ERROR: {exc}"

        candidates = [
            {
                "suggested_id": t.suggested_id,
                "path": t.path,
                "method": t.method,
                "description": t.description,
                "base_url": t.base_url,
                "param_names": [p.name for p in t.params],
                "auth_type": t.auth_type,
                "auth_header": t.auth_header,
            }
            for t in tools
        ]
        return json.dumps(candidates, ensure_ascii=False, indent=2)

    def _handle_add_rest_api_tool(self, inputs: dict) -> str:
        """Add a REST API tool to action_gateway and auto-sync agent_core connector.

        Args:
            inputs: Dict containing id, category, description, base_url, auth_type,
                    and endpoints. Optional: auth_header, auth_secret_env, timeout_ms.

        Returns:
            Confirmation string, or an ERROR string if the tool id is duplicate.
        """
        auth: dict = {"type": inputs["auth_type"]}
        if inputs.get("auth_header"):
            auth["header"] = inputs["auth_header"]
        if inputs.get("auth_secret_env"):
            auth["secret_env"] = inputs["auth_secret_env"]

        tool = {
            "id": inputs["id"],
            "type": "rest_api",
            "category": inputs["category"],
            "description": inputs["description"],
            "base_url": inputs["base_url"],
            "auth": auth,
            "timeout_ms": inputs.get("timeout_ms", 5000),
            "endpoints": inputs.get("endpoints", []),
            "response": {"max_size_chars": 4000},
        }
        try:
            self._acc.add_action_gateway_tool(tool)
        except ValueError as exc:
            return f"ERROR: {exc}"

        self._sync_connector_from_tool(tool)
        return f"Tool '{inputs['id']}' added to Action Gateway config."

    def _handle_discover_mcp_tools(self, inputs: dict) -> str:
        """Fetch tools/list from an MCP server and return the tool list as JSON.

        Supports both plain JSON-RPC responses and SSE (Server-Sent Events)
        transport. The response format is detected automatically: plain JSON is
        tried first; if that fails, each line is scanned for a ``data:`` prefix
        and the JSON payload is extracted from the first matching line.

        Args:
            inputs: Dict with 'mcp_server_url' key.

        Returns:
            JSON array of tool summaries, or an ERROR string on connection failure.
        """
        import json
        import httpx

        url = inputs["mcp_server_url"].rstrip("/")
        payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"ERROR: could not reach MCP server at {url} — {exc}"
        except Exception as exc:
            return f"ERROR: unexpected error contacting MCP server — {exc}"

        # Auto-detect transport: try plain JSON first, fall back to SSE parsing.
        try:
            data = response.json()
        except Exception:
            data = _parse_sse_json(response.text)
            if data is None:
                return (
                    f"ERROR: MCP server at {url} returned an unrecognised response format. "
                    f"Expected JSON-RPC or SSE. Response preview: {response.text[:200]!r}"
                )

        tools = data.get("result", {}).get("tools", [])
        if not tools:
            return f"No tools found at {url}. Verify the URL and that the server supports JSON-RPC tools/list."

        summary = [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {}),
            }
            for t in tools
        ]
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def _handle_add_mcp_tool(self, inputs: dict) -> str:
        """Register an MCP server with the Action Gateway.

        One entry per MCP server. McpAdapter connects at startup, discovers all
        tools via tools/list, and registers them as '{id}.{tool_name}'. No
        agent_core connector is written — MCP tool schemas come from the server
        at runtime. Subagents reference tools by their namespaced names directly
        (e.g. 'obsrv_docs.searchDocumentation').

        Args:
            inputs: Dict containing id, category, description, mcp_server_url.
                    Optional: transport (default 'sse'), timeout_ms (default 5000).

        Returns:
            Confirmation string with namespace hint, or an ERROR string if the
            tool id is duplicate.
        """
        tool = {
            "id": inputs["id"],
            "type": "mcp",
            "category": inputs["category"],
            "description": inputs["description"],
            "server_url": inputs["mcp_server_url"],
            "transport": inputs.get("transport", "sse"),
            "timeout_ms": inputs.get("timeout_ms", 5000),
        }
        try:
            self._acc.add_action_gateway_tool(tool)
        except ValueError as exc:
            return f"ERROR: {exc}"
        return (
            f"MCP server '{inputs['id']}' registered with Action Gateway (transport: {tool['transport']}). "
            f"Tools discovered at startup will be available as '{inputs['id']}.<tool_name>'. "
            f"Assign tools to subagents using these namespaced names."
        )

    def _handle_set_reach_channels(self, inputs: dict) -> str:
        """Store the user's selected deployment channels in reach_layer config.

        Args:
            inputs: Dict with 'channels' key containing a list of channel names.

        Returns:
            Confirmation string, or an ERROR string for unknown/empty channel list.
        """
        channels = inputs.get("channels", [])
        valid = {"web", "cli", "voice"}
        invalid = [c for c in channels if c not in valid]
        if invalid:
            return f"ERROR: unknown channel(s): {invalid}. Valid channels: {sorted(valid)}"
        if not channels:
            return "ERROR: at least one channel must be selected."
        self._acc.set_reach_channel_selection(channels)
        return f"Channels selected: {', '.join(channels)}. Now configure each selected channel."

    def _sync_connector_from_tool(self, tool: dict) -> None:
        """Auto-create or update agent_core connector from a tool definition.

        Generates the LLM-facing connector (name, description, input_schema) from
        the full tool definition. For rest_api tools, only agent-sourced params are
        included (static params are hidden from the LLM). For mcp tools, the
        input_schema from the MCP server is used directly.

        Args:
            tool: Tool dict from action_gateway.tools with at minimum:
                  id, category, description, type. Plus endpoints (rest_api) or
                  input_schema (mcp).
        """
        category = tool.get("category", "read")
        tool_id = tool["id"]

        if tool.get("type") == "mcp":
            # MCP tools are external — schemas come from the server at runtime.
            # Subagents reference them by namespaced names ('{id}.{tool_name}').
            # No agent_core connector entry is created.
            return
        else:
            properties: dict = {}
            required_list: list = []
            for endpoint in tool.get("endpoints", []):
                for param in endpoint.get("params", []):
                    if param.get("source") != "agent":
                        continue
                    prop: dict = {"type": param.get("type", "string")}
                    if param.get("description"):
                        prop["description"] = param["description"]
                    if param.get("default") is not None:
                        prop["default"] = param["default"]
                    properties[param["name"]] = prop
                    if param.get("required"):
                        required_list.append(param["name"])
            input_schema = {"type": "object", "properties": properties}
            if required_list:
                input_schema["required"] = required_list

        connector = {
            "name": tool_id,
            "description": tool.get("description", ""),
            "input_schema": input_schema,
        }

        self._acc.set_agent_core_connector(category, connector)
