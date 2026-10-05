# agent_core/eval/nlu/offline.py
"""Offline stand-ins for the eval runner: no Action Gateway, no Memory Layer."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.interfaces.action_gateway import ActionGatewayBase
from src.models import ToolCall, ToolResult


class OfflineGateway(ActionGatewayBase):
    """Lists connector names as tools so the workflow loader validates; never executes.

    Args:
        config: Merged agent_core config.
    """

    def __init__(self, config: dict) -> None:
        connectors = config.get("connectors") or {}
        self._tools = [{"name": c["name"], "description": c.get("description", ""),
                        "input_schema": {"type": "object", "properties": {}}}
                       for group in ("read", "write", "identity") for c in (connectors.get(group) or [])
                       if isinstance(c, dict) and c.get("name")]

    def list_available_tools(self) -> list[dict]:
        """Return connector names as tool definitions."""
        return list(self._tools)

    def execute(self, tool_call: ToolCall, session_id: str, user_id: str = "",
                session_values: dict | None = None) -> ToolResult:
        """Always fail: the eval never calls tools."""
        return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=tool_call.tool_name, result={},
                          success=False, error="eval: offline gateway")


class StaticToolCache:
    """Minimal TurnToolCache stand-in: ``latest_entry`` over fixed rows per tool; ``entry`` is always None."""

    def __init__(self, rows_by_tool: dict[str, list[dict]]) -> None:
        self._rows = rows_by_tool

    def latest_entry(self, tool: str) -> dict | None:
        """Return ``{"data": rows}`` for a tool with rows, else None."""
        rows = self._rows.get(tool)
        return {"data": list(rows)} if rows else None

    def entry(self, tool: str, args_hash_value: str) -> dict | None:
        """No served tracking offline: always None, so ``latest_entry`` applies."""
        return None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_merged_config(domain_dir: str | Path, agent_core_root: str | Path | None = None) -> dict:
    """agent_core/config/dpg.yaml deep-merged with ``<domain_dir>/agent_core.yaml``.

    Args:
        domain_dir: Directory holding the domain's agent_core.yaml.
        agent_core_root: agent_core checkout supplying config/dpg.yaml (default: this file's own tree).

    Returns:
        Merged config dict.
    """
    root = Path(agent_core_root) if agent_core_root else Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((root / "config" / "dpg.yaml").read_text(encoding="utf-8")) or {}
    domain = yaml.safe_load((Path(domain_dir) / "agent_core.yaml").read_text(encoding="utf-8")) or {}
    merged = _deep_merge(dpg, domain)
    return merged
