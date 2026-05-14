"""
dev-kit/dev_kit/agent/errors.py

Typed exceptions for the dev-kit agent.
"""


class ConversationError(Exception):
    """Raised when the chat loop or phase_driver fails unrecoverably."""


class ConfigurationError(Exception):
    """Raised when a required configuration value is missing at startup."""


class RuntimeValidationError(ValueError):
    """Raised when rendered YAML fails validation against a runtime MergedConfig schema.

    Wraps a Pydantic validation error (or any exception from the schema class)
    together with the name of the block that failed, so callers can surface a
    meaningful message without unwrapping the underlying exception themselves.

    Attributes:
        block: Name of the runtime block, e.g. ``"agent_core"``.
        pydantic_error: The original exception raised by ``model_validate``.
    """

    def __init__(self, block: str, pydantic_error: Exception) -> None:
        """Initialise the error with the failing block name and root cause.

        Args:
            block: Name of the runtime block that failed validation.
            pydantic_error: The original exception raised during schema validation.
        """
        self.block = block
        self.pydantic_error = pydantic_error
        super().__init__(str(self))

    def __str__(self) -> str:
        """Return a human-readable error string combining block name and cause."""
        return f"{self.block}: {self.pydantic_error}"
