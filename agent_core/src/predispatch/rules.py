"""
agent_core/src/predispatch/rules.py

Pure resolution of predispatch rules (Spec E §3): bind arguments from session,
literal or template; normalise; validate against the tool's agent schema; pick
the first eligible rule. Never raises. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from src.conditions import evaluate_condition

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*)\}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MISSING = "skipped_missing_arg"
_INVALID = "skipped_invalid_arg"
_TYPE_OK = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


class _Invalid(Exception):
    pass


def is_empty(value: Any) -> bool:
    """Seeded-empty values never count as a bound argument (same set as ``_tool_session_values``)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value in ("", "0") or not value.strip()
    return value is None or value == [] or (isinstance(value, (int, float)) and value == 0)


def _apply_table(value: Any, spec: Any, tables: dict) -> Any:
    if spec is None:
        return value
    if spec == "title":
        return str(value).title()
    if spec == "lower":
        return str(value).lower()
    table = tables.get(spec) if isinstance(spec, str) else spec
    if isinstance(table, dict):
        low = {str(k).lower(): v for k, v in table.items()}
        return low.get(str(value).lower(), value)
    return value


def _bind(arg: dict, session: dict, tables: dict) -> Any:
    """Return the bound value, or None when missing. Raises _Invalid if reject check fails."""
    if "template" in arg:
        norm = arg.get("normalise") if isinstance(arg.get("normalise"), dict) else {}
        reject = arg.get("reject")

        def sub(m: re.Match) -> str:
            keys = m.group(1).split("|")
            for k in keys:
                if not is_empty(session.get(k)):
                    val = session[k]
                    # Check reject on the chosen placeholder value
                    if reject:
                        reject_list = tables.get(reject) if isinstance(reject, str) else None
                        if reject_list is None:
                            raise _Invalid(k)  # reject table missing
                        if not isinstance(reject_list, (list, tuple)):
                            raise _Invalid(k)  # reject not a list/tuple
                        if str(val).strip().lower() in {str(r).lower() for r in reject_list}:
                            raise _Invalid(k)  # value is rejected
                    return str(_apply_table(val, norm.get(keys[0]), tables))
            raise KeyError(keys[0])

        try:
            return _PLACEHOLDER.sub(sub, str(arg["template"])).strip() or None
        except KeyError:
            return None
    if arg.get("from") == "literal":
        value = arg.get("value")
        return _apply_table(value, arg.get("normalise"), tables)
    value = session.get(arg.get("key", ""))
    if is_empty(value):
        return None
    return _apply_table(value, arg.get("normalise"), tables)


def _validate(name: str, value: Any, prop: dict, arg: dict, tables: dict) -> Any:
    reject = tables.get(arg.get("reject")) if arg.get("reject") else None
    if arg.get("reject"):
        if reject is None or not isinstance(reject, (list, tuple)):
            raise _Invalid(name)
    if isinstance(reject, (list, tuple)) and str(value).strip().lower() in {str(r).lower() for r in reject}:
        raise _Invalid(name)
    typ = prop.get("type")
    # Type coercion
    if typ == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    elif typ == "integer" and not isinstance(value, int):
        if isinstance(value, str) and value.strip().isdecimal():
            value = int(value.strip())
        else:
            raise _Invalid(name)
    # Type validation after coercion
    check = _TYPE_OK.get(typ)
    if check is not None and not check(value):
        raise _Invalid(name)
    if "enum" in prop and value not in prop["enum"]:
        raise _Invalid(name)
    if prop.get("format") == "uuid" and not (isinstance(value, str) and _UUID.fullmatch(value)):
        raise _Invalid(name)
    return value


def resolve_args(rule: dict, session: dict, tables: dict, tool_schema: dict | None) -> tuple[dict | None, str]:
    """Bind and validate one rule's arguments.

    Args:
        rule: Raw predispatch rule dict.
        session: Session values as written so far this turn.
        tables: ``predispatch_tables``.
        tool_schema: The tool's ``input_schema`` (``properties``/``required``), or None.

    Returns:
        ``(args, "")`` on success, else ``(None, "skipped_missing_arg" | "skipped_invalid_arg")``.
    """
    props = (tool_schema or {}).get("properties") or {}
    required = set((tool_schema or {}).get("required") or []) if tool_schema else None
    out: dict = {}
    for name, arg in (rule.get("args") or {}).items():
        try:
            value = _bind(arg, session, tables)
        except _Invalid:
            return None, _INVALID
        if value is None or is_empty(value):
            if required is None or name in required:
                return None, _MISSING
            continue
        try:
            out[name] = _validate(name, value, props.get(name) or {}, arg, tables)
        except _Invalid:
            return None, _INVALID
    for name in (required or set()) - set(out):
        return None, _MISSING
    return out, ""


@dataclass(frozen=True)
class Selection:
    """The turn's pre-dispatch decision.

    Attributes:
        tool: Tool to run, or None.
        args: Bound arguments (empty when tool is None).
        outcome: "fired" when a tool was chosen; otherwise the first gated rule's skip reason, or None.
        is_write: Whether ``tool`` is a write/identity tool.
        considered_tool: On a skip outcome, the tool of the first gated rule
            (the one that set ``outcome``); None when no rule's gates held.
    """

    tool: str | None
    args: dict = field(default_factory=dict)
    outcome: str | None = None
    is_write: bool = False
    considered_tool: str | None = None


def _gates_hold(rule: dict, intent: str, state: dict) -> bool:
    on = rule.get("on_intent") or []
    if on and intent not in on:
        return False
    return all(evaluate_condition(SimpleNamespace(**c), state) for c in (rule.get("when") or []))


def select(rules: list[dict], *, intent: str, state: dict, session: dict, tables: dict,
           tool_schemas: dict[str, dict], write_tools: set[str], has_fresh: Callable[[str, dict], bool]) -> Selection:
    """First rule whose gates hold, that is enabled, not fresh, and whose args resolve. Never raises.

    Args:
        rules: The subagent's raw predispatch rules, in order.
        intent: This turn's routing intent.
        state: Routing state (session ∪ profile ∪ NLU-owned) after this turn's writes.
        session: Session values for argument binding.
        tables: ``predispatch_tables``.
        tool_schemas: Tool name → input_schema.
        write_tools: Names of write/identity tools.
        has_fresh: Whether the turn cache already holds a fresh entry for a tool
            CALLED WITH the resolved args, so a changed city or trade is a miss.

    Returns:
        Selection.
    """
    first_skip: str | None = None
    first_tool: str | None = None
    for i, rule in enumerate(rules or []):
        try:
            if not _gates_hold(rule, intent, state):
                continue
            if first_skip is None:
                first_tool = rule.get("tool") if isinstance(rule.get("tool"), str) else None
            tool = rule["tool"]
            is_write = tool in write_tools
            enabled = rule.get("enabled")
            # Write tools must be strictly enabled (enabled is True)
            if is_write:
                if enabled is not True:
                    first_skip = first_skip or "disabled"
                    continue
            else:
                # Read tools: enabled is False → disabled, None or True → eligible
                if enabled is False:
                    first_skip = first_skip or "disabled"
                    continue
            # Args first, freshness second. The old order asked "has this tool
            # run this turn?" before it knew what the call would ask for, so a
            # caller who changed city or trade was answered from the previous
            # search: the rule was skipped as fresh and the stale rows were
            # handed to the model as though they answered the new question.
            # Freshness is only meaningful once the arguments are known.
            args, why = resolve_args(rule, session, tables, tool_schemas.get(tool))
            if args is None:
                first_skip = first_skip or why
                continue
            if rule.get("unless_fresh") and has_fresh(tool, args):
                first_skip = first_skip or "skipped_fresh"
                continue
            return Selection(tool=tool, args=args, outcome="fired", is_write=is_write)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("predispatch.rule_error", extra={"operation": "predispatch.select", "status": "failure", "rule_index": i, "error": type(e).__name__})
            first_skip = first_skip or "error"
    return Selection(tool=None, outcome=first_skip, considered_tool=first_tool if first_skip else None)
