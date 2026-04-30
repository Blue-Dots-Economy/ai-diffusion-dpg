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


from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import (
    ChatRequest,
    ImageBlock,
    ImageSource,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
)


class _DummyProvider(ChatProviderBase):
    """Test double: a ChatProviderBase with all capabilities False."""

    def __init__(self, caps: Capabilities) -> None:
        self.capabilities = caps

    def call(self, request):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, request, *, abort_event=None):  # pragma: no cover
        if False:
            yield ""

    def get_active_model(self) -> str:  # pragma: no cover
        return "dummy"


def _all_off() -> Capabilities:
    return Capabilities(
        supports_tools=False,
        supports_streaming=False,
        supports_prompt_cache=False,
        supports_image_input=False,
        supports_audio_input=False,
        supports_structured_output=False,
        supports_force_tool_choice=False,
    )


def _all_on() -> Capabilities:
    return Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=True,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )


def _basic_request(**overrides) -> ChatRequest:
    base = dict(messages=[Message(role="user", content=[TextBlock(text="hi")])])
    base.update(overrides)
    return ChatRequest(**base)


class TestValidateRequest:
    def test_passthrough_when_all_caps_on(self):
        p = _DummyProvider(_all_on())
        p._validate_request(_basic_request(), is_stream=False)
        p._validate_request(_basic_request(), is_stream=True)

    def test_rejects_tools_without_capability(self):
        p = _DummyProvider(_all_off())
        req = _basic_request(
            tools=[
                ToolDefinition(name="x", description="d", input_schema={"type": "object"})
            ]
        )
        with pytest.raises(UnsupportedFeatureError, match="tools"):
            p._validate_request(req, is_stream=False)

    def test_rejects_image_block_without_capability(self):
        caps = Capabilities(
            supports_tools=False, supports_streaming=False,
            supports_prompt_cache=False, supports_image_input=False,
            supports_audio_input=False, supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        p = _DummyProvider(caps)
        img = ImageBlock(source=ImageSource(kind="url", url="https://x/y.png"))
        req = ChatRequest(messages=[Message(role="user", content=[img])])
        with pytest.raises(UnsupportedFeatureError, match="image"):
            p._validate_request(req, is_stream=False)

    def test_rejects_cache_hint_in_message_without_capability(self):
        p = _DummyProvider(_all_off())
        req = ChatRequest(
            messages=[
                Message(role="user", content=[TextBlock(text="hi", cache_hint="session")])
            ]
        )
        with pytest.raises(UnsupportedFeatureError, match="prompt cach"):
            p._validate_request(req, is_stream=False)

    def test_rejects_cache_hint_in_system_without_capability(self):
        p = _DummyProvider(_all_off())
        req = ChatRequest(
            messages=[Message(role="user", content=[TextBlock(text="hi")])],
            system=SystemPrompt(
                blocks=[TextBlock(text="sys", cache_hint="session")]
            ),
        )
        with pytest.raises(UnsupportedFeatureError, match="prompt cach"):
            p._validate_request(req, is_stream=False)

    def test_rejects_output_format_without_capability(self):
        p = _DummyProvider(_all_off())
        req = _basic_request(output_format=OutputFormat(schema={}))
        with pytest.raises(UnsupportedFeatureError, match="structured"):
            p._validate_request(req, is_stream=False)

    def test_rejects_output_format_on_stream_even_with_capability(self):
        p = _DummyProvider(_all_on())
        req = _basic_request(output_format=OutputFormat(schema={}))
        with pytest.raises(UnsupportedFeatureError, match="stream"):
            p._validate_request(req, is_stream=True)

    def test_rejects_forced_tool_choice_without_capability(self):
        caps = Capabilities(
            supports_tools=True, supports_streaming=True,
            supports_prompt_cache=False, supports_image_input=False,
            supports_audio_input=False, supports_structured_output=False,
            supports_force_tool_choice=False,
        )
        p = _DummyProvider(caps)
        req = _basic_request(
            tools=[ToolDefinition(name="x", description="d", input_schema={"type": "object"})],
            tool_choice="any",
        )
        with pytest.raises(UnsupportedFeatureError, match="tool_choice"):
            p._validate_request(req, is_stream=False)

    def test_named_tool_choice_with_capability_passes(self):
        p = _DummyProvider(_all_on())
        req = _basic_request(
            tools=[ToolDefinition(name="my_tool", description="d", input_schema={"type": "object"})],
            tool_choice="my_tool",
        )
        p._validate_request(req, is_stream=False)
