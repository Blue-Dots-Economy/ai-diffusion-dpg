"""Shim overhead, measured separately from Agent Core's turn time.

The 800-1200ms target in #370 is dominated by Agent Core's turn time
(4-6s measured on the live stack), so what is assertable here is the
shim's own contribution — translating a request, relaying a stream, and
translating the response. A regression in this number is a regression in
our code; the turn time is not ours to fix.

Time-to-first-chunk is the figure that actually matters for a voice
caller (it is when their TTS can start speaking) and is the one #370's
"latency per turn" wording obscures. It was investigated separately here:
both `TestClient.post()` and `TestClient.stream()` (and, underneath it,
httpx's `ASGITransport` directly) run the ASGI app to completion and
buffer the full body before exposing any of it to the caller, so a
first-chunk timestamp taken through this harness is indistinguishable
from the whole-stream timestamp — there is no real concurrency between
"app produces a chunk" and "test reads a chunk" to measure. Forcing a
second measurement here would not add information, just a rediscovery of
the transport's own buffering. So a single figure is recorded: with the
mocked Agent Core stream yielding both events with no injected delay, it
is the shim's request translation + stream relay + response translation
cost, i.e. the whole time-to-first-chunk contribution this layer adds.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server import create_app

CONFIG = {"agent_core_url": "http://agent-core-test:8000", "channel": "bridge",
          "terminal_word": "Thank you"}
PHONE = "919900112233"


def _fake_stream(events, delay=0.0):
    async def _gen(self, payload):
        import asyncio
        for e in events:
            if delay:
                await asyncio.sleep(delay)
            yield e
    return _gen


def test_shim_overhead_to_first_chunk_is_small(capsys):
    """Assert the shim's own translation/relay cost stays negligible.

    Not a product-level latency assertion — Agent Core's 4-6s turn time
    dominates #370's 800-1200ms target and is out of this layer's control.
    This only catches a pathological regression in the shim's own code.
    """
    events = [
        {"type": "sentence", "text": "Hello there.", "sentence_index": 0},
        {"type": "done", "session_ended": False, "error_type": None},
    ]
    client = TestClient(create_app(CONFIG))

    with patch("src.server.AgentCoreClient.stream_turn", _fake_stream(events)):
        start = time.perf_counter()
        r = client.post("/v1/chat/completions", json={
            "model": "m", "stream": True,
            "metadata": {"caller_phone": PHONE},
            "messages": [{"role": "user", "content": "hello"}],
        })
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert r.status_code == 200
    with capsys.disabled():
        print(f"\n  shim overhead, time-to-first-chunk == whole stream "
              f"(TestClient buffers the full ASGI response before exposing "
              f"any of it, so the two are not separable here): "
              f"{elapsed_ms:.1f} ms")

    # Generous: this asserts the shim adds no pathological cost, not a
    # product-level latency target.
    assert elapsed_ms < 500, (
        f"shim overhead {elapsed_ms:.1f}ms — translation should be negligible "
        "against a 4-6s turn"
    )
