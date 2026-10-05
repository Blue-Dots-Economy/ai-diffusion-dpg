"""Speaking a status line while a tool runs.

Turns take 5-7 s and callers who hear nothing say "hello?", which the client
treats as barge-in and cancels the turn. Agent Core's ``tool_start`` signal
names the tools about to run; the bridge maps a configured tool to one short
caller-facing line and streams it as content, so the caller hears something
during the wait.

The line is rationed so the TTS queue cannot back up behind status chatter:
at most one per turn, only before the first real sentence, and only for tools
the domain mapped. The caller hears at most one extra line per turn.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionChunk

from src.server import create_app

PHONE = "919900112233"
STATUS = {"fetch_jobs": "नौकरियाँ देख रहा हूँ।", "apply_job": "आवेदन भेज रहा हूँ।"}
CONFIG = {
    "agent_core_url": "http://agent-core-test:8000",
    "channel": "bridge",
    "terminal_word": "",
    "tool_status_phrases": STATUS,
}


@pytest.fixture
def client():
    return TestClient(create_app(CONFIG))


def _body():
    return {
        "model": "gpt-4.1-mini-2025-04-14",
        "stream": True,
        "metadata": {"caller_phone": PHONE},
        "messages": [{"role": "user", "content": "काम है क्या"}],
    }


def _fake_stream(events):
    async def _gen(self, payload):
        for e in events:
            yield e
    return _gen


def _contents(text: str) -> list[str]:
    out = []
    for line in text.split("\n\n"):
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        ChatCompletionChunk.model_validate(chunk)
        content = chunk["choices"][0]["delta"].get("content") if chunk["choices"] else None
        if content:
            out.append(content.strip())
    return out


def _tool_start(*tools):
    return {"type": "signal", "stage": "tool_start", "status": "start",
            "tools": list(tools)}


DONE = {"type": "done", "session_ended": False, "error_type": None}
REPLY = {"type": "sentence", "text": "दो नौकरियाँ मिलीं।", "sentence_index": 0}


def _post(client, events):
    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        return client.post("/v1/chat/completions", json=_body())


def test_mapped_tool_speaks_its_status_before_the_reply(client):
    r = _post(client, [_tool_start("fetch_jobs"),
                       {"type": "signal", "stage": "tool_end", "status": "complete"},
                       REPLY, DONE])
    assert _contents(r.text) == ["नौकरियाँ देख रहा हूँ।", "दो नौकरियाँ मिलीं।"]


def test_unmapped_tool_says_nothing(client):
    r = _post(client, [_tool_start("end_session"), REPLY, DONE])
    assert _contents(r.text) == ["दो नौकरियाँ मिलीं।"]


def test_first_mapped_tool_in_a_multi_tool_call_is_used(client):
    r = _post(client, [_tool_start("end_session", "apply_job", "fetch_jobs"), REPLY, DONE])
    assert _contents(r.text)[0] == "आवेदन भेज रहा हूँ।"


def test_at_most_one_status_per_turn(client):
    """A multi-round tool chain must not queue a line per round."""
    r = _post(client, [_tool_start("fetch_jobs"), _tool_start("apply_job"), REPLY, DONE])
    assert _contents(r.text) == ["नौकरियाँ देख रहा हूँ।", "दो नौकरियाँ मिलीं।"]


def test_no_status_once_the_reply_has_started(client):
    """The caller is already hearing the answer — a status line would only
    delay the rest of it."""
    r = _post(client, [REPLY, _tool_start("fetch_jobs"), DONE])
    assert _contents(r.text) == ["दो नौकरियाँ मिलीं।"]


def test_no_status_without_configured_phrases():
    c = TestClient(create_app({**CONFIG, "tool_status_phrases": {}}))
    r = _post(c, [_tool_start("fetch_jobs"), REPLY, DONE])
    assert _contents(r.text) == ["दो नौकरियाँ मिलीं।"]


def test_tool_start_from_an_older_agent_core_without_tools_is_ignored(client):
    r = _post(client, [{"type": "signal", "stage": "tool_start", "status": "start"},
                       REPLY, DONE])
    assert _contents(r.text) == ["दो नौकरियाँ मिलीं।"]
