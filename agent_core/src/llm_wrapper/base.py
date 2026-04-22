"""
agent_core/llm_wrapper/base.py

Abstract interface for the LLM Inferencing Wrapper.
Defines the contract that all LLM provider implementations must satisfy.
Agent Core and ManagerAgent depend only on this interface — never on a concrete class.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Optional

from src.models import LLMResponse


class LLMWrapperBase(ABC):

    @abstractmethod
    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | list[dict],
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        """
        Execute a single LLM call.

        Args:
            messages:       Conversation messages in Anthropic format.
            tools:          Tool definitions to inject. Pass empty list if no tools.
            system:         System prompt. Accepts plain string (cached as one block if
                            ≥3000 chars) or list of Anthropic content blocks with explicit
                            `cache_control` markers. Currently passed as "" because Knowledge
                            Engine embeds the system prompt inside messages. When Knowledge
                            Engine is fully implemented, pass the system prompt here instead.
            model_override: If provided, use this model instead of the active model.
                            Used internally for fallback switching.

        Returns:
            LLMResponse — always. Never raises.
            On failure, returns LLMResponse with stop_reason="error" and content=None.
        """

    @abstractmethod
    async def stream_call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | list[dict] | None = None,
        model_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream raw text tokens from the LLM.

        Uses the same retry and fallback model logic as call().
        Yields text tokens as they arrive from the provider.

        If the LLM returns a tool_use stop reason, raises
        ToolUseRequested with the accumulated tool call blocks
        so the caller can execute tools and resume.

        Args:
            messages:       Conversation messages in Anthropic format.
            tools:          Tool definitions to inject. None or empty for no tools.
            system:         System prompt. Accepts plain string (cached as one block if
                            ≥3000 chars) or list of Anthropic content blocks with explicit
                            `cache_control` markers.
            model_override: If provided, use this model instead of the active model.

        Yields:
            str: Individual text tokens from the LLM stream.

        Raises:
            ToolUseRequested: If the LLM requests tool use mid-stream.
        """
        yield  # pragma: no cover

    @abstractmethod
    def get_active_model(self) -> str:
        """
        Return the name of the currently active model.
        Reflects primary model under normal conditions;
        reflects fallback model after a primary exhaustion event.
        """
