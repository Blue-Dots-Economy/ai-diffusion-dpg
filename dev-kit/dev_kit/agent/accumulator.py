"""
dev-kit/dev_kit/agent/accumulator.py

In-memory config accumulator for the DPG conversation agent.

Holds domain config values for all 7 DPG blocks as they are collected
during the conversation. Supports dot-notation path updates, subagent
graph management, serialisation, and status tracking.
"""
from __future__ import annotations

from copy import deepcopy
from enum import Enum


BLOCKS: list[str] = [
    "agent_core",
    "knowledge_engine",
    "memory_layer",
    "trust_layer",
    "action_gateway",
    "reach_layer",
    "observability_layer",
]

DRAFT_BLOCKS: set[str] = {"trust_layer", "action_gateway"}

PHASES: list[str] = [
    "overview",
    "language",
    "knowledge",
    "memory",
    "trust",
    "connectors",
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

    # ------------------------------------------------------------------
    # Config updates
    # ------------------------------------------------------------------

    def update(self, block: str, section: str, values: dict) -> None:
        """Deep-merge values into the block config at the given dot-notation section.

        Args:
            block: One of the 7 DPG block names.
            section: Dot-notation path, e.g. "preprocessing.nlu_processor".
                     Empty string merges directly into the block root.
            values: Values to merge.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}. Must be one of {BLOCKS}")
        if not section:
            self._data[block] = _deep_merge(self._data[block], values)
            return
        keys = section.split(".")
        target = self._data[block]
        current = target
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        last = keys[-1]
        if last not in current or not isinstance(current.get(last), dict):
            current[last] = {}
        current[last] = _deep_merge(current[last], values)

    def get_block(self, block: str) -> dict:
        """Return a deep copy of the full config dict for a block.

        Args:
            block: One of the 7 DPG block names.

        Returns:
            Deep copy of the block's accumulated config.

        Raises:
            ValueError: If block is not a valid DPG block name.
        """
        if block not in BLOCKS:
            raise ValueError(f"Unknown block: {block!r}")
        return deepcopy(self._data[block])

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
        """Return a human-readable summary of current config state for system prompts."""
        lines = ["Current config state:"]
        for block in BLOCKS:
            data = self._data[block]
            status = self._statuses[block].value
            if data:
                keys = list(data.keys())[:4]
                lines.append(f"  {block} ({status}): {', '.join(keys)}")
            else:
                lines.append(f"  {block} ({status}): empty")
        return "\n".join(lines)

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
