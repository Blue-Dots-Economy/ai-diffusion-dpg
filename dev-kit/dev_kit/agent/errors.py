"""
dev-kit/dev_kit/agent/errors.py

Typed exceptions for the dev-kit agent.
"""


class ConversationError(Exception):
    """Raised when the ConversationEngine chat loop fails unrecoverably."""


class ConfigurationError(Exception):
    """Raised when a required configuration value is missing at startup."""
