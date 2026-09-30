"""
agent_core/src/remember.py

The framework `remember` tool (tool-result persistence spec §8).

Lets the LLM save a value the caller chose into a declared state field.
Grounding is checked here; type/enum is checked by Memory Layer's strict write.
Never sent to Action Gateway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from src.manager_agent import ungrounded_params
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
        - The value is not empty.
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
        if value in (None, ""):
            return _reject(tool_call, self.name, f"No value given for {f.name}.")
        if f.grounded_in:
            probe = ToolCall(tool_name=self.name, tool_use_id=tool_call.tool_use_id,
                             input_params={"value": value})
            if ungrounded_params({"value": list(f.grounded_in)}, probe, messages,
                                 stored_results=stored_results, strict=True):
                return _reject(tool_call, self.name,
                               f"{f.name} must be copied exactly from a "
                               f"{' or '.join(f.grounded_in)} result. Re-read it and try again.")
        return f, value

    def _done(self, tool_call: ToolCall, f: RememberField, value: Any, ok: bool, reason: str,
              on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Finalize the save and return the result.

        If the Memory Layer accepted the write, call on_saved and return success.
        Otherwise, return a rejection.

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
        on_saved(f.scope, f.name, value)
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
        on_saved and returns success.

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
        ok, reason = write_strict(f.scope, f.name, value)
        return self._done(tool_call, f, value, ok, reason, on_saved)

    async def handle_async(self, tool_call: ToolCall, messages: list,
                           stored_results: dict[str, list[str]],
                           write_strict: Callable[[str, str, Any], Awaitable[tuple[bool, str]]],
                           on_saved: Callable[[str, str, Any], None]) -> ToolResult:
        """Validate and save (asynchronous path).

        The async variant of handle, used in stream contexts.
        Checks the tool call for grounding and field validity, then delegates
        to write_strict for type/enum validation. If both checks pass, calls
        on_saved and returns success.

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
        ok, reason = await write_strict(f.scope, f.name, value)
        return self._done(tool_call, f, value, ok, reason, on_saved)
