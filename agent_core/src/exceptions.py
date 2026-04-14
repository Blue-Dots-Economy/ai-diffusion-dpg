"""
agent_core/exceptions.py

Typed exceptions raised within Agent Core.
Callers catch specific types — no bare Exception handling anywhere in agent_core/.
"""


class AgentCoreError(Exception):
    """Base exception for all Agent Core failures."""


class LLMCallError(AgentCoreError):
    """All retry attempts on the primary model exhausted without a response."""


class LLMFallbackError(AgentCoreError):
    """Fallback model also failed after primary model exhaustion."""


class TrustViolationError(AgentCoreError):
    """Trust Layer blocked execution — input or output did not pass safety check."""


class ToolExecutionError(AgentCoreError):
    """Action Gateway returned a failure result for a tool call."""


class ConsentRequiredError(AgentCoreError):
    """Write or identity connector was called without confirmed user consent."""


class ConfigurationError(AgentCoreError):
    """Invalid, missing, or inconsistent domain configuration detected at startup."""


class ToolUseRequested(AgentCoreError):
    """Raised by stream_call() when the LLM returns a tool_use stop reason.

    Carries the accumulated tool call blocks so stream_turn() can execute
    tools via the Action Gateway and resume streaming.

    Args:
        tool_calls: List of ToolCall dataclasses extracted from the stream.
    """

    def __init__(self, tool_calls: list) -> None:
        self.tool_calls = tool_calls
        names = ", ".join(tc.tool_name for tc in tool_calls)
        super().__init__(f"LLM requested tool use: {names}")
