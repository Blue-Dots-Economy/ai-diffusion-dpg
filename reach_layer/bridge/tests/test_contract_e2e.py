"""Contract tests driven by the official OpenAI client.

These prove compliance with the published contract rather than with our own
expectations: if the SDK can drive the shim unmodified, so can any client
built on it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from src.server import create_app

# `openai` 3.x and `starlette.testclient.TestClient` both build on `httpx2`
# (a source-compatible fork of `httpx`), not the `httpx` package itself —
# see `httpx2._alias.alias_httpx`. Importing `httpx2 as httpx` here, rather
# than the real `httpx`, keeps the mock transport's `Response` objects the
# same class the SDK's client and the TestClient both expect; mixing the two
# packages fails with "Cannot use an async handler in a sync Client" because
# an `httpx2.Response` is not an `httpx.Response` (or vice versa).

CONFIG = {
    "agent_core_url": "http://agent-core-test:8000",
    "channel": "bridge",
    "terminal_word": "Thank you",
}
PHONE = "919900112233"


@pytest.fixture
def sdk():
    """An OpenAI client whose transport is the shim itself."""
    app_client = TestClient(create_app(CONFIG))
    transport = httpx.MockTransport(
        lambda request: app_client.request(
            request.method,
            str(request.url.path),
            content=request.content,
            headers=dict(request.headers),
        )
    )
    return OpenAI(api_key="unused", base_url="http://bridge/v1",
                  http_client=httpx.Client(transport=transport))


def _events(*sentences, session_ended=False):
    out = [{"type": "signal", "stage": "nlu", "status": "start"}]
    out += [{"type": "sentence", "text": s, "sentence_index": i}
            for i, s in enumerate(sentences)]
    out.append({"type": "done", "session_ended": session_ended,
                "error_type": None})
    return out


def _fake_stream(events):
    async def _gen(self, payload):
        for e in events:
            yield e
    return _gen


def test_sdk_parses_a_non_streaming_response(sdk):
    with patch("src.server.AgentCoreClient.process_turn", new_callable=AsyncMock) as m:
        m.return_value = {"response_text": "Hello there.", "model_used": "",
                          "error_type": None}
        completion = sdk.chat.completions.create(
            model="gpt-4.1-mini-2025-04-14",
            messages=[{"role": "user", "content": "hello"}],
            metadata={"caller_phone": PHONE},
        )
    assert completion.choices[0].message.content == "Hello there."
    assert completion.object == "chat.completion"


def test_sdk_consumes_a_streamed_response(sdk):
    events = _events("I found two jobs.", " Which one would you like?")
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        stream = sdk.chat.completions.create(
            model="gpt-4.1-mini-2025-04-14",
            messages=[{"role": "user", "content": "find jobs"}],
            metadata={"caller_phone": PHONE},
            stream=True,
        )
        text = "".join(c.choices[0].delta.content or ""
                       for c in stream if c.choices)
    assert text == "I found two jobs. Which one would you like?"


def test_sdk_sees_the_terminal_word_on_session_end(sdk):
    events = _events("Goodbye.", session_ended=True)
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        stream = sdk.chat.completions.create(
            model="gpt-4.1-mini-2025-04-14",
            messages=[{"role": "user", "content": "bye"}],
            metadata={"caller_phone": PHONE},
            stream=True,
        )
        text = "".join(c.choices[0].delta.content or ""
                       for c in stream if c.choices)
    assert "Thank you" in text


def test_sdk_reports_a_missing_phone_as_a_bad_request(sdk):
    from openai import BadRequestError

    with pytest.raises(BadRequestError):
        sdk.chat.completions.create(
            model="gpt-4.1-mini-2025-04-14",
            messages=[{"role": "user", "content": "hello"}],
        )


def test_multi_turn_uses_one_session(sdk):
    """session_id is the phone, so consecutive turns share a conversation."""
    seen = []

    async def _capture(self, payload):
        seen.append(payload["session_id"])
        return {"response_text": "ok", "model_used": "", "error_type": None}

    with patch("src.server.AgentCoreClient.process_turn", _capture):
        for text in ("hello", "electrician in Bengaluru", "yes"):
            sdk.chat.completions.create(
                model="gpt-4.1-mini-2025-04-14",
                messages=[{"role": "user", "content": text}],
                metadata={"caller_phone": PHONE},
            )
    assert seen == [PHONE, PHONE, PHONE]


def test_two_callers_never_share_a_session(sdk):
    """The cross-contamination case: concurrent callers must stay separate."""
    seen = []

    async def _capture(self, payload):
        seen.append(payload["session_id"])
        return {"response_text": "ok", "model_used": "", "error_type": None}

    with patch("src.server.AgentCoreClient.process_turn", _capture):
        for phone in ("919900112233", "919900445566", "919900112233"):
            sdk.chat.completions.create(
                model="gpt-4.1-mini-2025-04-14",
                messages=[{"role": "user", "content": "hello"}],
                metadata={"caller_phone": phone},
            )
    assert seen == ["919900112233", "919900445566", "919900112233"]
