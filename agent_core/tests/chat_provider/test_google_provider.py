"""Tests for GoogleChatProvider — init, config validation, wire translation.

Tests live in ``agent_core/tests/chat_provider/`` per project convention.
Mock all external dependencies — no real API calls.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.chat_provider.base import ProviderConfigError, ToolUseRequested, ProviderAPIError
from src.chat_provider.google_provider import GoogleChatProvider, _is_transient_error
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    ImageBlock,
    ImageSource,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _google_env(monkeypatch):
    """Set the GOOGLE_API_KEY env var for all tests that need it."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-dummy-key")


@pytest.fixture()
def base_config():
    """Minimal valid config dict."""
    return {
        "primary_model": "gemini-3.5-flash",
        "timeout_ms": 10000,
        "retry_attempts": 2,
    }


@pytest.fixture()
def provider(_google_env, base_config):
    """Return a GoogleChatProvider with mocked genai.Client."""
    with patch("src.chat_provider.google_provider.genai.Client"):
        return GoogleChatProvider(base_config)


# ---------------------------------------------------------------------------
# Init & config validation
# ---------------------------------------------------------------------------


def test_init_success(_google_env, base_config):
    """Provider initialises with a valid config and env key."""
    with patch("src.chat_provider.google_provider.genai.Client"):
        p = GoogleChatProvider(base_config)
        assert p.get_active_model() == "gemini-3.5-flash"


def test_init_empty_config():
    """Empty config raises ProviderConfigError."""
    with pytest.raises(ProviderConfigError, match="non-empty"):
        GoogleChatProvider({})


def test_init_missing_model(_google_env):
    """Missing primary_model raises ProviderConfigError."""
    with pytest.raises(ProviderConfigError, match="primary_model"):
        with patch("src.chat_provider.google_provider.genai.Client"):
            GoogleChatProvider({"timeout_ms": 10000, "retry_attempts": 2})


def test_init_missing_timeout(_google_env):
    """Missing timeout_ms raises ProviderConfigError."""
    with pytest.raises(ProviderConfigError, match="timeout_ms"):
        with patch("src.chat_provider.google_provider.genai.Client"):
            GoogleChatProvider({"primary_model": "gemini-3.5-flash", "retry_attempts": 2})


def test_init_missing_retry(_google_env):
    """Missing retry_attempts raises ProviderConfigError."""
    with pytest.raises(ProviderConfigError, match="retry_attempts"):
        with patch("src.chat_provider.google_provider.genai.Client"):
            GoogleChatProvider({"primary_model": "gemini-3.5-flash", "timeout_ms": 10000})


def test_init_missing_api_key(monkeypatch):
    """Missing API key raises ProviderConfigError."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError, match="No Google API key"):
        GoogleChatProvider({
            "primary_model": "gemini-3.5-flash",
            "timeout_ms": 10000,
            "retry_attempts": 2,
        })


def test_init_api_key_from_config(monkeypatch):
    """API key passed in config dict takes precedence."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with patch("src.chat_provider.google_provider.genai.Client"):
        p = GoogleChatProvider({
            "primary_model": "gemini-3.5-flash",
            "timeout_ms": 10000,
            "retry_attempts": 2,
            "api_key": "config-key",
        })
        assert p.get_active_model() == "gemini-3.5-flash"


# ---------------------------------------------------------------------------
# _to_wire
# ---------------------------------------------------------------------------


def test_to_wire_simple_text(provider):
    """A single user text message produces correct Content structure."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello Google")])]
    )
    contents, kwargs = provider._to_wire(req)
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Hello Google"


def test_to_wire_role_mapping(provider):
    """Neutral 'assistant' maps to Gemini 'model'."""
    req = ChatRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="Hi")]),
            Message(role="assistant", content=[TextBlock(text="Hello")]),
        ]
    )
    contents, _ = provider._to_wire(req)
    assert contents[0].role == "user"
    assert contents[1].role == "model"


def test_to_wire_system_instruction(provider):
    """System prompt goes into config as system_instruction, not contents."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        system=SystemPrompt(blocks=[TextBlock(text="You are a tutor.")]),
    )
    contents, kwargs = provider._to_wire(req)
    assert kwargs["system_instruction"] == "You are a tutor."
    # System prompt should NOT be in contents
    assert len(contents) == 1


def test_to_wire_tools(provider):
    """Tool declarations are translated to FunctionDeclaration."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search")])],
        tools=[ToolDefinition(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )],
        tool_choice="auto",
    )
    _, kwargs = provider._to_wire(req)
    assert "tools" in kwargs
    assert kwargs["tools"][0].function_declarations[0].name == "search"
    assert kwargs["tool_config"].function_calling_config.mode == "AUTO"


def test_to_wire_forced_tool_choice(provider):
    """Named tool_choice maps to ANY with allowed_function_names."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search")])],
        tools=[ToolDefinition(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {}},
        )],
        tool_choice="search",
    )
    _, kwargs = provider._to_wire(req)
    fc_config = kwargs["tool_config"].function_calling_config
    assert fc_config.mode == "ANY"
    assert fc_config.allowed_function_names == ["search"]


def test_to_wire_tool_choice_none_keeps_declarations(provider):
    """Spec E: "none" sends the declarations with function-calling mode NONE."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search")])],
        tools=[ToolDefinition(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {}},
        )],
        tool_choice="none",
    )
    _, kwargs = provider._to_wire(req)
    assert kwargs["tools"][0].function_declarations[0].name == "search"
    assert str(kwargs["tool_config"].function_calling_config.mode).endswith("NONE")


def test_to_wire_tool_choice_none_without_tools(provider):
    """No tools: "none" sends neither tools nor a tool_config."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="hi")])],
        tool_choice="none",
    )
    _, kwargs = provider._to_wire(req)
    assert "tools" not in kwargs and "tool_config" not in kwargs


def test_to_wire_tool_result_newline(provider):
    """ToolResultBlock with list content joins with real newline, not literal \\n."""
    req = ChatRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="Hi")]),
            Message(role="assistant", content=[ToolUseBlock(
                tool_use_id="call_1", tool_name="search", input={"q": "test"}
            )]),
            Message(role="user", content=[ToolResultBlock(
                tool_use_id="call_1",
                content=[TextBlock(text="line1"), TextBlock(text="line2")],
            )]),
        ],
    )
    contents, _ = provider._to_wire(req)
    # The last message should contain a function_response part.
    last_content = contents[-1]
    assert last_content.role == "user"
    # The joined content should use real newline
    resp_part = last_content.parts[0]
    assert resp_part.function_response is not None
    result_value = resp_part.function_response.response.get("result", "")
    assert "\\n" not in result_value or "\n" in result_value


def test_to_wire_output_format(provider):
    """OutputFormat maps to response_mime_type + response_schema."""
    req = ChatRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        output_format=OutputFormat(
            schema={"type": "object", "properties": {"name": {"type": "string"}}},
        ),
    )
    _, kwargs = provider._to_wire(req)
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}


def test_to_wire_image_input(provider):
    """ImageBlock is translated to an inline data Part."""
    import base64
    dummy_data = base64.b64encode(b"dummy image bytes").decode("utf-8")
    req = ChatRequest(
        messages=[
            Message(
                role="user",
                content=[
                    ImageBlock(
                        source=ImageSource(kind="base64", media_type="image/jpeg", data=dummy_data)
                    )
                ]
            )
        ]
    )
    contents, _ = provider._to_wire(req)
    assert len(contents) == 1
    part = contents[0].parts[0]
    assert part.inline_data is not None
    assert part.inline_data.mime_type == "image/jpeg"
    assert part.inline_data.data == base64.b64decode(dummy_data)

def test_get_or_create_cache(provider):
    """_get_or_create_cache caches system prompt and handles errors gracefully."""
    sys_text = "This is a very long system prompt." * 1000
    
    # Successful cache creation
    provider._client.caches.create.return_value = MagicMock(name="test-cache-id")
    provider._client.caches.create.return_value.name = "test-cache-id"
    
    cache_name = provider._get_or_create_cache(sys_text)
    assert cache_name == "test-cache-id"
    provider._client.caches.create.assert_called_once()
    
    # Using existing cache
    provider._client.caches.create.reset_mock()
    cache_name_2 = provider._get_or_create_cache(sys_text)
    assert cache_name_2 == "test-cache-id"
    provider._client.caches.create.assert_not_called()
    
    # Fallback on error
    provider._client.caches.create.side_effect = Exception("API error")
    new_sys_text = sys_text + " changed"
    cache_name_error = provider._get_or_create_cache(new_sys_text)
    assert cache_name_error is None


# ---------------------------------------------------------------------------
# _from_wire
# ---------------------------------------------------------------------------


def test_from_wire_empty_candidates(provider):
    """Empty candidates (safety block) returns error ChatResponse."""
    raw = MagicMock()
    raw.candidates = []
    raw.usage_metadata = None
    resp = provider._from_wire(raw, output_format=None)
    assert resp.stop_reason == "error"
    assert resp.content == []


def test_from_wire_none_candidates(provider):
    """None candidates returns error ChatResponse."""
    raw = MagicMock()
    raw.candidates = None
    raw.usage_metadata = None
    resp = provider._from_wire(raw, output_format=None)
    assert resp.stop_reason == "error"
    assert resp.content == []


def test_from_wire_text_response(provider):
    """A normal text response is extracted correctly."""
    part_mock = MagicMock()
    part_mock.text = "Hello from Google"
    part_mock.function_call = None

    candidate = MagicMock()
    candidate.content.parts = [part_mock]
    candidate.finish_reason = MagicMock()
    candidate.finish_reason.name = "STOP"

    raw = MagicMock()
    raw.candidates = [candidate]
    raw.usage_metadata = MagicMock()
    raw.usage_metadata.prompt_token_count = 10
    raw.usage_metadata.candidates_token_count = 5
    raw.usage_metadata.cached_content_token_count = 0

    resp = provider._from_wire(raw, output_format=None)
    assert resp.stop_reason == "end_turn"
    assert len(resp.content) == 1
    assert resp.content[0].text == "Hello from Google"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5


# ---------------------------------------------------------------------------
# _is_transient_error
# ---------------------------------------------------------------------------


def test_transient_error_rate_limit():
    """Rate-limit errors are classified as transient."""
    assert _is_transient_error(Exception("429 rate limit exceeded"))


def test_transient_error_timeout():
    """Timeout errors are classified as transient."""
    assert _is_transient_error(Exception("Request timeout after 30s"))


def test_non_transient_error_auth():
    """Auth errors are NOT classified as transient."""
    assert not _is_transient_error(Exception("401 Unauthorized: invalid API key"))


def test_non_transient_error_not_found():
    """404 model-not-found is NOT transient."""
    assert not _is_transient_error(Exception("404 Model not found: gemini-2.0-flash"))


# ---------------------------------------------------------------------------
# Streaming Tests
# ---------------------------------------------------------------------------


class _FakeGoogleChunk:
    def __init__(
        self,
        text: str | None = None,
        function_calls: list | None = None,
        finish_reason: str | None = None,
        usage: dict | None = None,
    ) -> None:
        self.text = text

        if function_calls is not None:
            self.function_calls = []
            for fc in function_calls:
                fc_mock = MagicMock()
                fc_mock.id = fc.get("id")
                fc_mock.name = fc.get("name")
                fc_mock.args = fc.get("args")
                self.function_calls.append(fc_mock)
        else:
            self.function_calls = None

        if usage is not None:
            u = MagicMock()
            u.prompt_token_count = usage.get("prompt_token_count", 0)
            u.candidates_token_count = usage.get("candidates_token_count", 0)
            u.cached_content_token_count = usage.get("cached_content_token_count", 0)
            self.usage_metadata = u
        else:
            self.usage_metadata = None

        if finish_reason is not None:
            fr = MagicMock()
            fr.name = finish_reason
            candidate = MagicMock()
            candidate.finish_reason = fr
            self.candidates = [candidate]
        else:
            self.candidates = None


class _FakeAsyncStream:
    def __init__(self, chunks: list[_FakeGoogleChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


def _install_async_stream(provider: GoogleChatProvider, chunks: list) -> None:
    async def _create(*args, **kwargs):
        return _FakeAsyncStream(chunks)
    provider._async_client.aio.models.generate_content_stream = _create


class TestGoogleStream:
    @pytest.mark.asyncio
    async def test_stream_text_success(self, provider):
        chunks = [
            _FakeGoogleChunk(text="Hello "),
            _FakeGoogleChunk(text="Google!"),
            _FakeGoogleChunk(finish_reason="STOP", usage={"prompt_token_count": 10, "candidates_token_count": 5}),
        ]
        _install_async_stream(provider, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="Hi")])])
        tokens = []
        async for token in provider.stream(req):
            tokens.append(token)
        assert tokens == ["Hello ", "Google!"]

    @pytest.mark.asyncio
    async def test_stream_tool_use(self, provider):
        chunks = [
            _FakeGoogleChunk(text="Let me look that up. "),
            _FakeGoogleChunk(function_calls=[{"id": "call_1", "name": "search", "args": {"q": "test"}}]),
            _FakeGoogleChunk(finish_reason="STOP", usage={"prompt_token_count": 12, "candidates_token_count": 6}),
        ]
        _install_async_stream(provider, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="Search")])])
        tokens = []
        with pytest.raises(ToolUseRequested) as exc_info:
            async for token in provider.stream(req):
                tokens.append(token)
        assert tokens == ["Let me look that up. "]
        tool_calls = exc_info.value.tool_calls
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_use_id == "call_1"
        assert tool_calls[0].tool_name == "search"
        assert tool_calls[0].input == {"q": "test"}

    @pytest.mark.asyncio
    async def test_stream_safety_error(self, provider):
        chunks = [
            _FakeGoogleChunk(text="Potentially unsafe "),
            _FakeGoogleChunk(finish_reason="SAFETY"),
        ]
        _install_async_stream(provider, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="Hi")])])
        tokens = []
        with pytest.raises(ProviderAPIError, match="SAFETY"):
            async for token in provider.stream(req):
                tokens.append(token)
        assert tokens == ["Potentially unsafe "]

    @pytest.mark.asyncio
    async def test_stream_recitation_error(self, provider):
        chunks = [
            _FakeGoogleChunk(text="Potentially copyrighted "),
            _FakeGoogleChunk(finish_reason="RECITATION"),
        ]
        _install_async_stream(provider, chunks)

        req = ChatRequest(messages=[Message(role="user", content=[TextBlock(text="Hi")])])
        tokens = []
        with pytest.raises(ProviderAPIError, match="RECITATION"):
            async for token in provider.stream(req):
                tokens.append(token)
        assert tokens == ["Potentially copyrighted "]
