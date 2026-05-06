"""
dev-kit/dev_kit/agent/accumulator.py

In-memory config accumulator for the DPG conversation agent.

Holds domain config values for all 7 DPG blocks as they are collected
during the conversation. Supports dot-notation path updates, subagent
graph management, serialisation, and status tracking.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from enum import Enum

from dev_kit.schemas.validation import validate_domain_section

logger = logging.getLogger(__name__)


BLOCKS: list[str] = [
    "agent_core",
    "knowledge_engine",
    "memory_layer",
    "trust_layer",
    "action_gateway",
    "reach_layer",
    "observability_layer",
]

DRAFT_BLOCKS: set[str] = set()

PHASES: list[str] = [
    "tier",
    "overview",
    "language",
    "knowledge",
    "memory",
    "user_state",
    "trust",
    "tools",
    "workflow",
    "observability",
    "reach",
    "review",
]


class ConfigStatus(str, Enum):
    """Status of a block's generated config file."""

    COMPLETE = "complete"
    DRAFT = "draft"
    PENDING = "pending"
    STALE = "stale"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not merged."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class ConfigAccumulator:
    """In-memory holder for domain config values across all 7 DPG blocks.

    Built up incrementally as the conversation progresses. Supports
    dot-notation section paths for nested updates and full subagent
    graph management for the agent_core workflow.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict] = {block: {} for block in BLOCKS}
        self._statuses: dict[str, ConfigStatus] = {block: ConfigStatus.PENDING for block in BLOCKS}
        self._validation_attempts: dict[tuple[str, str], int] = {}
        self._max_validation_attempts: int = int(
            os.environ.get("DEVKIT_VALIDATION_MAX_ATTEMPTS", "3")
        )
        self._strict_mode: bool = os.environ.get("DEVKIT_DPG_SCHEMA_STRICT", "1") == "1"

    # ------------------------------------------------------------------
    # Config updates
    # ------------------------------------------------------------------

    def update(self, block: str, section: str, values: dict) -> str:
        """Validate the would-be merged result, then commit only on success.

        Builds a candidate copy of the block, merges ``values`` into the
        target path, and validates the affected top-level section against
        its Pydantic schema BEFORE mutating ``self._data``. ``[missing]``
        errors are filtered so partial drafts can accumulate across turns.

        Once the per-(block, top_level) retry counter reaches the cap, the
        section is marked STALE and every subsequent call to that section
        within the same turn is hard-rejected without re-validation. This
        is the loop safety net — even if the LLM ignores the escalation
        message, the same-section dispatch returns immediately with a stop
        instruction.

        Args:
            block: One of the 7 DPG block names.
            section: Dot-notation path, e.g. "preprocessing.nlu_processor".
                     Empty string merges directly into the block root.
            values: Values to merge.

        Returns:
            "OK" — validation passed (or strict mode is off, or section is
            unschema'd, or section root). Write committed.
            "VALIDATION_ERROR (attempt N/M):..." — schema rejected the
            candidate merge; nothing was written; the LLM should retry
            with corrected values.
            "VALIDATION_FAILED_AFTER_M_ATTEMPTS..." — counter just hit the
            cap; section marked STALE; LLM should escalate or advance.
            "VALIDATION_SECTION_STALE..." — counter already at cap from a
            prior attempt this turn; this call is hard-rejected without
            validation. Returned to keep the LLM from looping on a section
            it has already failed M times.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}. Must be one of {BLOCKS}")

        # Section-root writes (empty section): skip schema validation —
        # they typically span multiple sections and per-section schemas
        # don't apply. Commit straight to data.
        if not section:
            self._data[block] = _deep_merge(self._data[block], values)
            logger.info(
                "devkit.accumulator.config_updated",
                extra={
                    "operation": "accumulator.update",
                    "status": "success",
                    "block": block,
                    "path": "(root)",
                },
            )
            return "OK"

        top_level = section.split(".", 1)[0]
        attempt_key = (block, top_level)

        # Hard-reject if this section already exhausted its retry budget
        # this turn. Skips validation entirely so the LLM cannot keep the
        # tool-loop alive with new variations of the same bad write.
        if (
            self._strict_mode
            and self._validation_attempts.get(attempt_key, 0) >= self._max_validation_attempts
        ):
            logger.warning(
                "devkit.accumulator.section_stale_rejected",
                extra={
                    "operation": "accumulator.update",
                    "status": "rejected_section_stale",
                    "block": block,
                    "section": section,
                    "top_level": top_level,
                    "attempts": self._validation_attempts[attempt_key],
                    "max_attempts": self._max_validation_attempts,
                },
            )
            return (
                f"VALIDATION_SECTION_STALE for {block}.{top_level}: "
                f"already failed {self._max_validation_attempts} times this turn. "
                f"DO NOT call update_config for this section again — repeated calls "
                f"will keep returning this same rejection. set_phase may also be "
                f"blocked because the merged state is still inconsistent. Your "
                f"correct next action is to STOP calling tools and reply to the "
                f"user as a text message: tell them which field could not be "
                f"auto-configured, what value(s) you tried, and ask them to either "
                f"provide a corrected value or instruct you to skip the section."
            )

        # Build the would-be merged result without touching self._data.
        candidate_block = deepcopy(self._data[block])
        keys = section.split(".")
        cursor = candidate_block
        for key in keys[:-1]:
            if key not in cursor or not isinstance(cursor[key], dict):
                cursor[key] = {}
            cursor = cursor[key]
        last = keys[-1]
        if last not in cursor or not isinstance(cursor.get(last), dict):
            cursor[last] = {}
        cursor[last] = _deep_merge(cursor[last], values)

        # Strict mode off: commit without validation.
        if not self._strict_mode:
            self._data[block] = candidate_block
            logger.info(
                "devkit.accumulator.config_updated",
                extra={
                    "operation": "accumulator.update",
                    "status": "success",
                    "block": block,
                    "path": section,
                },
            )
            return "OK"

        merged_top = candidate_block.get(top_level, {})
        error = validate_domain_section(block, section, merged_top)

        if error:
            # Filter [missing] errors — partial drafts are allowed during
            # config building. If only [missing] errors remain, treat as
            # valid and commit. Pre-existing [missing]-filter behaviour
            # used to live in validate_partial; consolidating it here.
            non_missing_lines = [line for line in error.split("\n") if "[missing]" not in line]
            filtered_error = "\n".join(non_missing_lines).strip()

            if filtered_error:
                attempt = self._validation_attempts.get(attempt_key, 0) + 1
                self._validation_attempts[attempt_key] = attempt

                if attempt >= self._max_validation_attempts:
                    self.set_status(block, ConfigStatus.STALE)
                    logger.warning(
                        "devkit.accumulator.validation_cap_reached",
                        extra={
                            "operation": "accumulator.update",
                            "status": "failure_cap_reached",
                            "block": block,
                            "section": section,
                            "top_level": top_level,
                            "attempts": attempt,
                            "max_attempts": self._max_validation_attempts,
                        },
                    )
                    return (
                        f"VALIDATION_FAILED_AFTER_{self._max_validation_attempts}_ATTEMPTS for "
                        f"{block}.{top_level}:\n{filtered_error}\n\n"
                        f"Tell the user we couldn't auto-configure this and ask for guidance, "
                        f"OR call set_phase to advance and fix in Review phase."
                    )

                logger.warning(
                    "devkit.accumulator.validation_retry",
                    extra={
                        "operation": "accumulator.update",
                        "status": "validation_error_returned",
                        "block": block,
                        "section": section,
                        "top_level": top_level,
                        "attempt": attempt,
                        "max_attempts": self._max_validation_attempts,
                    },
                )
                return (
                    f"VALIDATION_ERROR (attempt {attempt}/{self._max_validation_attempts}):\n"
                    f"{filtered_error}"
                )

        # Validation passed (or only [missing] errors that are tolerated
        # during partial accumulation). Commit the candidate and clear the
        # retry counter.
        self._data[block] = candidate_block
        self._validation_attempts.pop(attempt_key, None)
        logger.info(
            "devkit.accumulator.config_updated",
            extra={
                "operation": "accumulator.update",
                "status": "success",
                "block": block,
                "path": section,
            },
        )
        return "OK"

    def reset_validation_attempts(self) -> None:
        """Clear all per-section retry counters.

        Called by the ConversationEngine at the start of each new user turn
        so the retry budget resets between turns. Within a single tool-call
        loop the counters keep climbing until the LLM produces a valid value
        or hits the cap.
        """
        self._validation_attempts.clear()

    def get_block(self, block: str) -> dict:
        """Return a deep copy of the full config dict for a block.

        Internal keys (prefixed with ``_``) are stripped so callers see only
        domain config — matching what the renderer writes to YAML.

        Args:
            block: One of the 7 DPG block names.

        Returns:
            Deep copy of the block's accumulated config without internal keys.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        return {k: deepcopy(v) for k, v in self._data[block].items()
                if not k.startswith("_")}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_status(self, block: str, status: ConfigStatus) -> None:
        """Set the status of a block config.

        Args:
            block: One of the 7 DPG block names.
            status: New status.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        self._statuses[block] = status

    def get_status(self, block: str) -> ConfigStatus:
        """Return the current status of a block config.

        Args:
            block: One of the 7 DPG block names.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        return self._statuses[block]

    # ------------------------------------------------------------------
    # Subagent graph management
    # ------------------------------------------------------------------

    def set_subagent(self, subagent: dict) -> None:
        """Add or replace a subagent in the agent_core workflow.

        Args:
            subagent: Subagent dict. Must include an 'id' key.

        Raises:
            ValueError: If subagent has no 'id' key.
        """
        if "id" not in subagent:
            raise ValueError("Subagent must have an 'id' key")
        workflow = self._data["agent_core"].setdefault("agent_workflow", {})
        subagents: list[dict] = workflow.setdefault("subagents", [])
        for i, sa in enumerate(subagents):
            if sa.get("id") == subagent["id"]:
                subagents[i] = deepcopy(subagent)
                return
        subagents.append(deepcopy(subagent))

    def update_subagent(self, subagent_id: str, fields: dict) -> None:
        """Merge fields into an existing subagent.

        Args:
            subagent_id: ID of the subagent to update.
            fields: Fields to merge.

        Raises:
            ValueError: If no subagent with the given ID exists.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == subagent_id:
                sa.update(fields)
                return
        raise ValueError(f"no subagent with id {subagent_id!r}")

    def remove_subagent(self, subagent_id: str) -> bool:
        """Remove a subagent by ID.

        Args:
            subagent_id: ID of the subagent to remove.

        Returns:
            True if the subagent was found and removed, False if not found.
        """
        subagents = (
            self._data.get("agent_core", {})
            .get("agent_workflow", {})
            .get("subagents", [])
        )
        original_len = len(subagents)
        subagents[:] = [sa for sa in subagents if sa.get("id") != subagent_id]
        return len(subagents) < original_len

    def add_action_gateway_tool(self, tool: dict) -> None:
        """Add a tool definition to the action_gateway tools list.

        Args:
            tool: Tool dict. Must include 'id' and 'type' ('rest_api' or 'mcp') keys.

        Raises:
            ValueError: If tool has no 'id' key.
            ValueError: If a tool with the same id already exists.
        """
        if "id" not in tool:
            raise ValueError("Tool must have an 'id' key")
        tools: list[dict] = self._data["action_gateway"].setdefault("tools", [])
        if any(t.get("id") == tool["id"] for t in tools):
            raise ValueError(f"Tool with id {tool['id']!r} already exists")
        tools.append(deepcopy(tool))

    def get_action_gateway_tools(self) -> list[dict]:
        """Return the current list of action_gateway tool definitions.

        Returns:
            Deep copy of the tools list (empty list if none configured).
        """
        return deepcopy(self._data["action_gateway"].get("tools", []))

    def update_tool_response_mapping(
        self,
        tool_id: str,
        fields: list[dict],
        list_key: str = "",
    ) -> None:
        """Set the response projection for an existing action_gateway tool.

        Writes ``response.projection`` with ``list_key`` and a ``fields`` dict
        mapping target name → dot-path. Replaces any existing projection. An
        empty ``fields`` list removes the projection entirely.

        Args:
            tool_id: ID of the REST API tool to update.
            fields: List of dicts with at minimum 'source' (dot-path into each
                    item, or root if no list_key) and 'target' (name the LLM sees).
            list_key: Optional dot-path to a list in the response. When set,
                      each element is projected; when empty, the root is projected.

        Raises:
            ValueError: If no tool with the given id exists in action_gateway.
        """
        tools: list[dict] = self._data["action_gateway"].get("tools", [])
        for tool in tools:
            if tool.get("id") == tool_id:
                response = tool.setdefault("response", {})
                response.pop("field_mapping", None)
                if not fields:
                    response.pop("projection", None)
                    return
                response["projection"] = {
                    "list_key": list_key,
                    "fields": {f["target"]: f["source"] for f in fields},
                }
                return
        raise ValueError(f"Tool {tool_id!r} not found in action_gateway — call add_rest_api_tool first")

    def declare_azure_needed(self) -> None:
        """Record that this domain uses Azure Blob Storage for KB documents.

        Collects no configuration values — account name, account key, and
        container name are all entered securely in the Deployment Inputs UI.
        This method just sets the intent flag so the deploy wizard knows to
        show the Azure credential fields.
        """
        self._data["azure_storage"] = {
            "needed": True,
        }

    def is_azure_needed(self) -> bool:
        """Return True if Azure Blob Storage has been declared for this domain.

        Returns:
            True if ``declare_azure_needed`` has been called, False otherwise.
        """
        return bool(self._data.get("azure_storage", {}).get("needed"))

    def has_knowledge_base(self) -> bool:
        """Return True if the knowledge_engine config has a static_knowledge_base configured.

        The schema's ``StaticKnowledgeBaseSection.enabled`` defaults to ``True``,
        so a YAML with the section populated but no explicit ``enabled`` key
        still means "this agent uses a KB." Treat the section as a KB when:

        * the ``static_knowledge_base`` block is present and non-empty, AND
        * ``enabled`` is not explicitly set to ``False``.

        The previous implementation defaulted ``enabled`` to ``False`` when
        absent — opposite of the runtime schema — so projects that omitted
        the redundant flag (most of them) had the deploy wizard mark them as
        "no KB" and skip the ingest step.

        Returns:
            True when the agent uses a KB, False otherwise.
        """
        ke = self._data.get("knowledge_engine", {})
        skb = ke.get("knowledge", {}).get("blocks", {}).get("static_knowledge_base", {})
        if not skb:
            return False
        return skb.get("enabled", True) is not False

    def get_required_secrets(self) -> list[dict]:
        """Return the list of API key secrets required by configured tools.

        Scans all action_gateway tools for non-none auth with a ``secret_env``
        field. Each entry tells the deploy wizard what password fields to show.

        Returns:
            List of dicts, each with keys:
                ``env_var``     — environment variable name (e.g. ``ONEST_API_KEY``)
                ``tool_id``     — id of the tool that needs it
                ``description`` — human-readable tool description for the UI label
            Returns an empty list when no tools have auth secrets configured.
        """
        result = []
        for tool in self._data["action_gateway"].get("tools", []):
            secret_env = tool.get("auth", {}).get("secret_env", "")
            if secret_env:
                result.append({
                    "env_var": secret_env,
                    "tool_id": tool.get("id", ""),
                    "description": tool.get("description", ""),
                })
        return deepcopy(result)

    def get_required_channel_secrets(self) -> list[dict]:
        """Return credential descriptors for channels selected by the domain admin.

        Inspects the selected deployment channels and returns a structured list
        that the deploy wizard renders as credential input fields. Web channel
        requires Google OAuth client ID; voice channel requires Vobiz and Raya
        credentials plus the public service URL.

        Returns:
            List of dicts, each with keys:
                ``env_var``     — environment variable name injected into container
                ``label``       — field label shown in the UI
                ``description`` — hint text shown below the field
                ``required``    — always True for all current channel credentials
                ``section``     — "web" or "voice"
                ``secret``      — True → SecretInput (masked); False → plain input
            Returns an empty list when no credential-bearing channel is selected.
        """
        selected = self.get_reach_channel_selection()
        result = []
        if "web" in selected:
            result.append({
                "env_var": "GOOGLE_CLIENT_ID",
                "label": "Google Client ID",
                "description": (
                    "Google is the only supported auth provider. Get your Client ID from "
                    "the Google Cloud Console — create an OAuth 2.0 credential and add "
                    "your deployment URL as an authorised origin."
                ),
                "required": True,
                "section": "web",
                "secret": False,
            })
        if "voice" in selected:
            result.extend([
                {
                    "env_var": "VOBIZ_AUTH_ID",
                    "label": "Vobiz Auth ID",
                    "description": "Your Vobiz account Auth ID. Found in the Vobiz dashboard under Account settings.",
                    "required": True,
                    "section": "voice",
                    "secret": True,
                },
                {
                    "env_var": "VOBIZ_AUTH_TOKEN",
                    "label": "Vobiz Auth Token",
                    "description": "Your Vobiz account Auth Token. Found in the Vobiz dashboard under Account settings.",
                    "required": True,
                    "section": "voice",
                    "secret": True,
                },
                {
                    "env_var": "RAYA_API_KEY",
                    "label": "Raya API Key",
                    "description": "API key for Raya STT/TTS. Found in your Raya dashboard.",
                    "required": True,
                    "section": "voice",
                    "secret": True,
                },
                {
                    "env_var": "PUBLIC_URL",
                    "label": "Voice Public URL",
                    "description": (
                        "Public HTTPS URL of the voice service "
                        "(e.g. https://voice.203-0-113-42.sslip.io). "
                        "The voice server returns this to Vobiz so it knows where to open the audio WebSocket."
                    ),
                    "required": True,
                    "section": "voice",
                    "secret": False,
                },
                {
                    "env_var": "VOBIZ_FROM_NUMBER",
                    "label": "Vobiz From Number",
                    "description": (
                        "Vobiz-assigned phone number used as caller ID on outbound calls "
                        "(E.164 format, e.g. +919876543210). Required — the voice service will not start without it."
                    ),
                    "required": True,
                    "section": "voice",
                    "secret": False,
                },
            ])
        return deepcopy(result)

    def set_reach_channel_selection(self, channels: list[str]) -> None:
        """Store the selected deployment channels.

        The reach_layer_web service mode (full vs routing_only) is derived from
        this selection at deploy time and injected as the REACH_LAYER_WEB_MODE
        environment variable; it is not part of the runtime YAML.

        Args:
            channels: List of selected channel names (e.g. ['web', 'cli']).
        """
        self._data["reach_layer"]["_selected_channels"] = list(channels)
        logger.info(
            "devkit.accumulator.channels_set",
            extra={
                "operation": "accumulator.set_reach_channel_selection",
                "status": "success",
                "channels": list(channels),
            },
        )

    def get_reach_channel_selection(self) -> list[str]:
        """Return the selected deployment channels, or empty list if not yet set.

        Returns:
            List of selected channel names, or empty list if the user has not
            called set_reach_channels yet.
        """
        return list(self._data["reach_layer"].get("_selected_channels", []))

    def get_reach_channel_selection_or_default(self) -> list[str]:
        """Return selected channels, falling back to what is configured in YAML.

        Returns:
            List of selected channel names. If no explicit selection was stored
            (e.g. project configured before channel tracking was introduced),
            infers active channels from non-null entries in reach_layer.channels.
            Falls back to ['web'] only if neither source yields a result.
        """
        selection = self.get_reach_channel_selection()
        if selection:
            return selection
        # Infer from what's actually configured in the reach_layer YAML data.
        channels_cfg = (
            self._data.get("reach_layer", {})
            .get("reach_layer", {})
            .get("channels", {})
        )
        inferred = [ch for ch, cfg in channels_cfg.items() if cfg is not None]
        return inferred if inferred else ["web"]

    def set_agent_core_connector(self, category: str, connector: dict) -> None:
        """Add or replace a connector in agent_core.connectors[category].

        Args:
            category: Connector category ('read', 'write', or 'identity').
            connector: Connector dict with at minimum a 'name' key.
        """
        connectors_block = self._data["agent_core"].setdefault("connectors", {})
        connector_list: list = connectors_block.setdefault(category, [])
        for i, c in enumerate(connector_list):
            if c.get("name") == connector.get("name"):
                connector_list[i] = connector
                return
        connector_list.append(connector)

    def add_routing_rule(
        self,
        from_subagent_id: str,
        intent: str,
        next_subagent_id: str,
        conditions: list[dict],
        session_writes: dict,
    ) -> None:
        """Add a routing rule to a subagent.

        Args:
            from_subagent_id: Source subagent ID.
            intent: Intent that triggers this rule. Use "*" for catch-all.
            next_subagent_id: Destination subagent ID.
            conditions: Optional list of session state conditions.
            session_writes: Optional session fields to write when rule matches.

        Raises:
            ValueError: If no subagent with from_subagent_id exists.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == from_subagent_id:
                rule: dict = {"intent": intent, "next_subagent_id": next_subagent_id}
                if conditions:
                    rule["conditions"] = conditions
                if session_writes:
                    rule["session_writes"] = session_writes
                sa.setdefault("routing", []).append(rule)
                return
        raise ValueError(f"no subagent with id {from_subagent_id!r}")

    def update_routing_rule(self, from_subagent_id: str, intent: str, fields: dict) -> None:
        """Update an existing routing rule on a subagent.

        Args:
            from_subagent_id: Source subagent ID.
            intent: Intent that identifies the rule.
            fields: Fields to update.

        Raises:
            ValueError: If no matching subagent or routing rule is found.
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        for sa in subagents:
            if sa.get("id") == from_subagent_id:
                for rule in sa.get("routing", []):
                    if rule.get("intent") == intent:
                        rule.update(fields)
                        return
                raise ValueError(f"no routing rule for intent {intent!r} on subagent {from_subagent_id!r}")
        raise ValueError(f"no subagent with id {from_subagent_id!r}")

    def get_workflow_graph(self) -> dict:
        """Return the subagent workflow as nodes and edges for the frontend.

        Returns:
            Dict with 'nodes' (list of {id, name, type}) and
            'edges' (list of {from, to, intent}).
        """
        subagents = self._data.get("agent_core", {}).get("agent_workflow", {}).get("subagents", [])
        nodes = []
        edges = []
        for sa in subagents:
            node_type = "start" if sa.get("is_start") else ("end" if sa.get("is_terminal") else "normal")
            nodes.append({"id": sa["id"], "name": sa.get("name", sa["id"]), "type": node_type})
            for rule in sa.get("routing", []):
                edges.append({"from": sa["id"], "to": rule.get("next_subagent_id", ""), "intent": rule.get("intent", "")})
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of current config state for system prompts.

        Renders two sections:

        1. **Per-block top-level keys** — block-by-block snapshot of which
           top-level sections exist, plus the block's status. Quick visual
           index of what's been touched.
        2. **Cross-phase references** — the actual values for fields that
           later phases must read and reuse character-for-character (NLU
           intents, supported languages, connector names, intent_filters
           keys, etc.). The system prompt tells the LLM to ``read these
           directly``; this is where they become readable.

        Both sections are truncated to keep the prompt compact, but the
        cross-phase block prints values in full when set, never just keys.
        """
        lines = ["Current config state:"]
        for block in BLOCKS:
            data = self._data[block]
            status = self._statuses[block].value
            if data:
                if block == "action_gateway":
                    tool_ids = [t.get("id", "?") for t in data.get("tools", [])]
                    detail = f"tools: [{', '.join(tool_ids)}]" if tool_ids else "tools: []"
                else:
                    keys = [k for k in data.keys() if not k.startswith("_")][:4]
                    detail = ", ".join(keys)
                lines.append(f"  {block} ({status}): {detail}")
            else:
                lines.append(f"  {block} ({status}): empty")
        if self.is_azure_needed():
            lines.append("  azure_storage: declared (user confirmed Azure Blob Storage)")
        selected_channels = self.get_reach_channel_selection()
        if selected_channels:
            lines.append(f"  selected_channels: {', '.join(selected_channels)}")
        else:
            lines.append("  selected_channels: not yet set")

        # ---- Cross-phase references ----------------------------------
        # Surface the actual values for paths that later phases must
        # reference. Without this the LLM can see "preprocessing" exists
        # under agent_core but cannot read the intents inside it.
        ref_lines = self._render_cross_phase_references()
        if ref_lines:
            lines.append("")
            lines.append("Cross-phase references (signed-off values — read these directly, do NOT re-ask the user):")
            lines.extend(ref_lines)

        return "\n".join(lines)

    def _render_cross_phase_references(self) -> list[str]:
        """Build the cross-phase reference block for ``summary()``.

        Returns a list of indented strings, one per populated reference
        path. Returns an empty list when nothing has been set yet (e.g.
        in tier/overview phase before the language phase runs).

        The reference set is the closure of fields that downstream phase
        prompts tell the LLM to "use the value from <path>". If you add
        a new cross-phase reference rule in phases.py, add the path here
        too — otherwise the LLM is told to read something it can't see.
        """
        refs: list[str] = []
        ac = self._data.get("agent_core") or {}
        ke = self._data.get("knowledge_engine") or {}

        agent = ac.get("agent") or {}
        for field in ("provider", "primary_model", "fallback_model"):
            val = agent.get(field)
            if val:
                refs.append(f"  agent_core.agent.{field}: {val}")

        preprocessing = ac.get("preprocessing") or {}
        lang_norm = preprocessing.get("language_normalisation") or {}
        if lang_norm.get("default_language"):
            refs.append(f"  agent_core.preprocessing.language_normalisation.default_language: {lang_norm['default_language']}")
        supported = lang_norm.get("supported_languages")
        if supported:
            refs.append(f"  agent_core.preprocessing.language_normalisation.supported_languages: {supported}")

        nlu = preprocessing.get("nlu_processor") or {}
        intents = nlu.get("intents")
        if intents:
            refs.append(f"  agent_core.preprocessing.nlu_processor.intents: {intents}")
        entities = nlu.get("entities")
        if entities:
            refs.append(f"  agent_core.preprocessing.nlu_processor.entities: {entities}")

        connectors = ac.get("connectors") or {}
        for category in ("read", "write", "identity", "internal"):
            entries = connectors.get(category) or []
            names = [c.get("name") for c in entries if isinstance(c, dict) and c.get("name")]
            if names:
                refs.append(f"  agent_core.connectors.{category} names: {names}")

        kb = ((ke.get("knowledge") or {}).get("blocks") or {}).get("static_knowledge_base") or {}
        intent_filters = kb.get("intent_filters")
        if intent_filters:
            refs.append(f"  knowledge_engine.intent_filters keys: {sorted(intent_filters.keys())}")

        return refs

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict for checkpoint storage.

        Returns:
            Dict with 'data' and 'statuses' keys.
        """
        return {
            "data": deepcopy(self._data),
            "statuses": {block: status.value for block, status in self._statuses.items()},
        }

    @classmethod
    def from_dict(cls, snapshot: dict) -> "ConfigAccumulator":
        """Restore from a serialised snapshot.

        Args:
            snapshot: Dict previously returned by to_dict().

        Returns:
            New ConfigAccumulator with restored state.
        """
        acc = cls()
        acc._data = deepcopy(snapshot.get("data", {b: {} for b in BLOCKS}))
        for block, status_str in snapshot.get("statuses", {}).items():
            try:
                acc._statuses[block] = ConfigStatus(status_str)
            except ValueError:
                acc._statuses[block] = ConfigStatus.PENDING
        return acc
