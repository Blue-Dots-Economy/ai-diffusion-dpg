"""
agent_core/src/remember.py

The framework `remember` tool (tool-result persistence spec §8).

Lets the LLM save a value the caller chose into a declared state field.
Grounding is checked here; type/enum is checked by Memory Layer's strict write.
Never sent to Action Gateway.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from src.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RememberField:
    """One writable field.

    Attributes:
        name: Field name.
        scope: Storage scope (session or user).
        description: Human-readable field description.
        grounded_in: Tool names that produce values for this field.
    """

    name: str
    scope: str
    description: str = ""
    grounded_in: tuple[str, ...] = ()


def _collect_leaf_values(obj: Any) -> set[str]:
    """Recursively collect all scalar leaf values from a JSON-like object.

    Traverses dicts and lists recursively, extracting every scalar value
    (str, int, float, bool) at the leaves. Used to find all possible
    grounding candidates in a parsed tool result.

    Args:
        obj: A parsed JSON object (dict, list, or scalar).

    Returns:
        A set of string representations of all scalar leaves.
    """
    leaves = set()
    if isinstance(obj, dict):
        for v in obj.values():
            leaves.update(_collect_leaf_values(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            leaves.update(_collect_leaf_values(item))
    elif isinstance(obj, (str, int, float, bool)):
        leaves.add(str(obj))
    return leaves


def _ground_check(value: str, grounded_in: tuple[str, ...], messages: list,
                  stored_results: dict[str, list[str]]) -> bool:
    """Check if a value is grounded in a tool result (exact match).

    Builds a map of tool_use_id to tool_name from tool_use blocks, then
    collects candidate texts from tool_result blocks (whose tool_use_id
    maps to a tool in grounded_in) plus stored_results for those tools.
    For each text, attempts json.loads; if that fails, tries from the
    first "{" or "[" onward (for stored-result prefix). Skips the
    stored-result prefix "(stored result, fetched N min ago) ".
    Collects all scalar leaf values and accepts only if value is exactly
    one of them.

    Args:
        value: The value to ground.
        grounded_in: Tool names that may produce this value.
        messages: Message history containing tool blocks.
        stored_results: Cached tool results by tool name.

    Returns:
        True if value is grounded in one of the specified tools.
    """
    if not grounded_in:
        return True

    # Map tool_use_id -> tool_name
    origin: dict[str, str] = {}
    for msg in messages or []:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "type", "") == "tool_use":
                origin[str(getattr(block, "tool_use_id", ""))] = str(
                    getattr(block, "tool_name", "")
                )

    # Collect candidate texts from messages and stored_results
    candidates: list[str] = []
    grounded_set = set(grounded_in)

    # From messages
    for msg in messages or []:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "type", "") != "tool_result":
                continue
            tool_use_id = str(getattr(block, "tool_use_id", ""))
            if origin.get(tool_use_id) in grounded_set:
                content = getattr(block, "content", "")
                if isinstance(content, str) and content:
                    candidates.append(content)

    # From stored_results
    for tool in grounded_in:
        for text in stored_results.get(tool, []):
            if isinstance(text, str) and text:
                candidates.append(text)

    # Parse each candidate and collect leaf values
    all_leaves: set[str] = set()
    for text in candidates:
        # Try to strip stored-result prefix
        clean = text
        if clean.startswith("(stored result, fetched"):
            idx = clean.find(") ")
            if idx >= 0:
                clean = clean[idx + 2:]

        # Try json.loads
        obj = None
        try:
            obj = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            # Try from first { or [
            for start_char in ["{", "["]:
                idx = clean.find(start_char)
                if idx >= 0:
                    try:
                        obj = json.loads(clean[idx:])
                        break
                    except (json.JSONDecodeError, ValueError):
                        pass

        if obj is not None:
            all_leaves.update(_collect_leaf_values(obj))

    return value in all_leaves


def _reject(tool_call: ToolCall, name: str, message: str) -> ToolResult:
    """Reject a remember call with guidance.

    Args:
        tool_call: The remember tool call being rejected.
        name: The tool name.
        message: Guidance message for the LLM.

    Returns:
        A ToolResult with success=False and error="REMEMBER_REJECTED".
    """
    logger.info("tool_result", extra={"operation": "remember.handle", "status": "rejected",
                                      "tool": name, "outcome": "remember_reject"})
    return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=name, result={},
                      success=False, error="REMEMBER_REJECTED", result_text=message)


class RememberTool:
    """Validated writes of caller-chosen values.

    The remember tool lets the LLM save values into declared state fields,
    checking grounding (value comes from a specified tool) and delegating
    type/enum validation to the Memory Layer.

    Args:
        name: Tool name the LLM calls.
        fields: Writable fields by name.
    """

    def __init__(self, name: str, fields: dict[str, RememberField]) -> None:
        """Initialize the remember tool.

        Args:
            name: Tool name.
            fields: Mapping of field name to RememberField.
        """
        self.name = name
        self._fields = fields

    @classmethod
    def from_config(cls, config: dict | None) -> "RememberTool | None":
        """Build from agent_core config; None when ``memory_tool`` is absent.

        Args:
            config: The agent_core configuration dictionary.

        Returns:
            A RememberTool instance, or None if memory_tool is not configured.
        """
        mt = (config or {}).get("memory_tool")
        if not isinstance(mt, dict) or not mt.get("fields"):
            return None
        fields = {
            str(n): RememberField(name=str(n), scope=str(f.get("scope", "session")),
                                  description=str(f.get("description") or ""),
                                  grounded_in=tuple(f.get("grounded_in") or ()))
            for n, f in mt["fields"].items()
        }
        return cls(str(mt.get("name") or "remember"), fields)

    def definition(self) -> dict:
        """Return the tool definition in registry format.

        The definition describes the tool to the LLM, listing all writable
        fields as an enum and documenting their purposes.

        Returns:
            A dict with name, description, and input_schema.
        """
        names = sorted(self._fields)
        described = "; ".join(f"{n}: {self._fields[n].description}" for n in names
                              if self._fields[n].description)
        return {
            "name": self.name,
            "description": ("Save a value the caller chose, so later steps use it without you "
                            "repeating it. Only for the listed fields. " + described).strip(),
            "input_schema": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": names},
                               "value": {"type": "string"}},
                "required": ["field", "value"],
                "additionalProperties": False,
            },
        }

    def _check(self, tool_call: ToolCall, messages: list,
               stored_results: dict[str, list[str]]) -> Union[ToolResult, tuple[RememberField, Any]]:
        """Validate the tool call and extract the field and value.

        Checks that:
        - The field name is defined.
        - The value is a string.
        - The value is not empty after stripping.
        - If the field is grounded, the value appears in a specified tool's results.

        Args:
            tool_call: The remember tool call.
            messages: Message history for grounding checks.
            stored_results: Cached tool results for grounding.

        Returns:
            Either a rejection ToolResult, or a tuple of (field, value).
        """
        params = tool_call.input_params or {}
        f = self._fields.get(str(params.get("field")))
        if f is None:
            return _reject(tool_call, self.name,
                           f"You can only save these fields: {', '.join(sorted(self._fields))}.")
        value = params.get("value")
        if not isinstance(value, str):
            return _reject(tool_call, self.name, "value must be text.")
        value = value.strip()
        if not value:
            return _reject(tool_call, self.name, f"No value given for {f.name}.")
        if f.grounded_in:
            if not _ground_check(value, f.grounded_in, messages, stored_results or {}):
                return _reject(tool_call, self.name,
                               f"{f.name} must be copied exactly from a "
                               f"{' or '.join(f.grounded_in)} result. Re-read it and try again.")
        return f, value

    def _done(self, tool_call: ToolCall, f: RememberField, value: Any, ok: bool, reason: str,
              on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Finalize the save and return the result.

        If the Memory Layer accepted the write, calls on_saved and returns success.
        If on_saved raises, logs a warning but still returns success (value is persisted).
        Otherwise, returns a rejection.

        Args:
            tool_call: The remember tool call.
            f: The target field.
            value: The value being saved.
            ok: Whether the Memory Layer accepted the write.
            reason: Rejection reason from the Memory Layer (empty if ok=True).
            on_saved: Callback to notify the caller of a successful save.

        Returns:
            A ToolResult indicating success or rejection.
        """
        if not ok:
            return _reject(tool_call, self.name, f"Not saved: {reason}")
        try:
            on_saved(f.scope, f.name, value)
        except Exception as e:
            logger.warning("tool_result", extra={"operation": "remember.on_saved",
                                                  "status": "error",
                                                  "exception_class": type(e).__name__})
        logger.info("tool_result", extra={"operation": "remember.handle", "status": "success",
                                          "tool": self.name, "outcome": "remember_write"})
        return ToolResult(tool_use_id=tool_call.tool_use_id, tool_name=self.name,
                          result={"saved": f.name}, success=True, result_text=f"Saved {f.name}.")

    def handle(self, tool_call: ToolCall, messages: list, stored_results: dict[str, list[str]],
               write_strict: Callable[[str, str, Any], tuple[bool, str]],
               on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Validate and save (synchronous path).

        Checks the tool call for grounding and field validity, then delegates
        to write_strict for type/enum validation. If both checks pass, calls
        on_saved and returns success. Never raises; errors are surfaced as
        REMEMBER_REJECTED ToolResults.

        Args:
            tool_call: The remember tool call.
            messages: Message history for grounding.
            stored_results: Cached tool results for grounding.
            write_strict: Callable to validate and persist the value.
            on_saved: Callback invoked on successful save.

        Returns:
            A ToolResult indicating success or rejection.
        """
        checked = self._check(tool_call, messages, stored_results)
        if isinstance(checked, ToolResult):
            return checked
        f, value = checked
        try:
            ok, reason = write_strict(f.scope, f.name, value)
        except Exception as e:
            logger.warning("tool_result", extra={"operation": "remember.write_strict",
                                                  "status": "error",
                                                  "exception_class": type(e).__name__})
            return _reject(tool_call, self.name, "Not saved: could not store the value.")
        return self._done(tool_call, f, value, ok, reason, on_saved)

    async def handle_async(self, tool_call: ToolCall, messages: list,
                           stored_results: dict[str, list[str]],
                           write_strict: Callable[[str, str, Any], Awaitable[tuple[bool, str]]],
                           on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Validate and save (asynchronous path).

        The async variant of handle, used in stream contexts.
        Checks the tool call for grounding and field validity, then delegates
        to write_strict for type/enum validation. If both checks pass, calls
        on_saved and returns success. Never raises; errors are surfaced as
        REMEMBER_REJECTED ToolResults.

        Args:
            tool_call: The remember tool call.
            messages: Message history for grounding.
            stored_results: Cached tool results for grounding.
            write_strict: Async callable to validate and persist the value.
            on_saved: Callback invoked on successful save.

        Returns:
            A ToolResult indicating success or rejection.
        """
        checked = self._check(tool_call, messages, stored_results)
        if isinstance(checked, ToolResult):
            return checked
        f, value = checked
        try:
            ok, reason = await write_strict(f.scope, f.name, value)
        except Exception as e:
            logger.warning("tool_result", extra={"operation": "remember.write_strict",
                                                  "status": "error",
                                                  "exception_class": type(e).__name__})
            return _reject(tool_call, self.name, "Not saved: could not store the value.")
        return self._done(tool_call, f, value, ok, reason, on_saved)
