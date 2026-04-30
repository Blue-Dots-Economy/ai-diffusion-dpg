"""Tests for chat_provider.base — Capabilities, errors, and ChatProviderBase."""

import pytest
from dataclasses import FrozenInstanceError

from src.chat_provider.base import (
    Capabilities,
    ChatProviderError,
    ProviderAPIError,
    ProviderConfigError,
    ToolUseRequested,
    UnsupportedFeatureError,
)
from src.chat_provider.types import ToolUseBlock


class TestCapabilities:
    def test_create(self):
        caps = Capabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_prompt_cache=True,
            supports_image_input=True,
            supports_audio_input=False,
            supports_structured_output=True,
            supports_force_tool_choice=True,
        )
        assert caps.supports_tools is True

    def test_frozen(self):
        caps = Capabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_prompt_cache=False,
            supports_image_input=False,
            supports_audio_input=False,
            supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        with pytest.raises(FrozenInstanceError):
            caps.supports_tools = False  # type: ignore[misc]


class TestErrors:
    def test_hierarchy(self):
        assert issubclass(UnsupportedFeatureError, ChatProviderError)
        assert issubclass(ProviderConfigError, ChatProviderError)
        assert issubclass(ProviderAPIError, ChatProviderError)

    def test_tool_use_requested_carries_calls(self):
        calls = [ToolUseBlock(tool_use_id="t_1", tool_name="x", input={})]
        e = ToolUseRequested(calls)
        assert e.tool_calls == calls
        # Not a subclass of ChatProviderError — it's a control-flow signal.
        assert not isinstance(e, ChatProviderError)
