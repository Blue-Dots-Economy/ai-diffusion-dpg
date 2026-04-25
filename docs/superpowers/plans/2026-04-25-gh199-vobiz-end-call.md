# GH-199 Vobiz End-of-Call Hangup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a bot-initiated `end_session` actually drop the Vobiz telephony leg by triggering pipecat's built-in REST DELETE hangup signal, and surface its outcome in our structured logs.

**Architecture:** Pipecat's `VobizFrameSerializer` already issues an HTTP `DELETE` to the Vobiz REST API on `EndFrame`/`CancelFrame` when `auto_hang_up=True` (it is). The current `close_call()` only does `ws.close()` and never causes that `EndFrame` to flow, so the REST hangup never fires. We push `EndFrame` from `AgentCoreLLMProcessor._handle_done_event` after the terminal-word `TextFrame` (mirroring the escalation path), wrap the serializer in a thin logging subclass to surface hangup outcome, and demote `VobizAdapter.close_call` to a defensive fallback with richer structured logs.

**Tech Stack:** Python 3.13, pipecat (`VobizFrameSerializer`), `aiohttp` (mocked in tests), pytest + pytest-asyncio, `uv`.

**Spec:** `docs/superpowers/specs/2026-04-25-gh199-vobiz-end-call-design.md`

**Working directory for all `uv` commands:** `reach_layer/voice/`

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `reach_layer/voice/src/operators/vobiz_operator.py` | Modify | Add `LoggingVobizFrameSerializer`; use it in `create_transport`. |
| `reach_layer/voice/src/pipecat_services/agent_core_llm.py` | Modify | Push `EndFrame()` in `_handle_done_event` after terminal-word frame. |
| `reach_layer/voice/src/vobiz_adapter.py` | Modify | `close_call` becomes defensive fallback; structured log gains `vendor_signal` + `outcome`. |
| `reach_layer/voice/tests/operators/test_vobiz_operator.py` | Modify | Cover `LoggingVobizFrameSerializer` (mocked HTTP) + wiring assertion. |
| `reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py` | Modify | Frame-ordering assertions — `EndFrame` after `TextFrame`. |
| `reach_layer/voice/tests/test_vobiz_adapter.py` | Modify | `close_call` log + behaviour cases. |

---

## Task 1 — `LoggingVobizFrameSerializer` (subclass + tests)

**Files:**
- Modify: `reach_layer/voice/src/operators/vobiz_operator.py`
- Modify: `reach_layer/voice/tests/operators/test_vobiz_operator.py`

- [ ] **Step 1.1: Write failing test — successful hangup logs `outcome=success`**

Append to `reach_layer/voice/tests/operators/test_vobiz_operator.py`:

```python
import logging
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from pipecat.frames.frames import EndFrame, CancelFrame, StartFrame
from src.operators.vobiz_operator import LoggingVobizFrameSerializer


def _make_serializer(call_id: str = "call-456"):
    return LoggingVobizFrameSerializer(
        stream_id="stream-123",
        call_id=call_id,
        auth_id="aid",
        auth_token="tok",
    )


class _FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.delete_calls = []

    def delete(self, url, headers=None):
        self.delete_calls.append({"url": url, "headers": headers})
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _patch_aiohttp(response: _FakeResponse):
    fake = _FakeSession(response)
    return patch("aiohttp.ClientSession", return_value=fake), fake


@pytest.mark.asyncio
async def test_logging_serializer_success_outcome(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    assert len(fake.delete_calls) == 1
    call = fake.delete_calls[0]
    assert call["url"] == "https://api.vobiz.ai/api/v1/Account/aid/Call/call-456/"
    assert call["headers"] == {"X-Auth-ID": "aid", "X-Auth-Token": "tok"}
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "success"
    assert rec.call_id == "call-456"
    assert isinstance(rec.latency_ms, int)
```

- [ ] **Step 1.2: Run test — verify it fails on missing import**

Run: `cd reach_layer/voice && uv run pytest tests/operators/test_vobiz_operator.py::test_logging_serializer_success_outcome -v`
Expected: FAIL with `ImportError: cannot import name 'LoggingVobizFrameSerializer'`.

- [ ] **Step 1.3: Implement `LoggingVobizFrameSerializer`**

Edit `reach_layer/voice/src/operators/vobiz_operator.py`. Replace the existing imports + add the subclass above the `VobizOperator` class. Full file should now begin like:

```python
"""
telephony_adapter/src/operators/vobiz_operator.py

VobizOperator — concrete TelephonyOperatorBase for the Vobiz telephony platform.

Vobiz is Plivo-compatible. Uses Pipecat's VobizFrameSerializer (which extends
PlivoFrameSerializer with Vobiz-specific 16 kHz L16 support) and
parse_telephony_websocket for handshake parsing.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time

from pipecat.frames.frames import CancelFrame, EndFrame, Frame
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.operators.operator_base import TelephonyOperatorBase

logger = logging.getLogger(__name__)


class LoggingVobizFrameSerializer(VobizFrameSerializer):
    """VobizFrameSerializer that surfaces hangup outcome to structured logs.

    Wraps the upstream serializer's ``_hang_up_call`` so the adapter-side
    log stream records the Vobiz REST DELETE outcome consistently with the
    rest of the framework's ``operation`` / ``status`` / ``latency_ms``
    convention. No behavioural divergence from the upstream serializer.
    """

    async def _hang_up_call(self):
        """Call upstream hangup and emit a single structured log entry."""
        start = time.time()
        outcome = "failure"
        if not self._call_id or not self._auth_id or not self._auth_token:
            outcome = "skipped_missing_credentials"
            await super()._hang_up_call()
        else:
            import aiohttp
            try:
                endpoint = (
                    f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
                    f"/Call/{self._call_id}/"
                )
                headers = {
                    "X-Auth-ID": self._auth_id,
                    "X-Auth-Token": self._auth_token,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.delete(endpoint, headers=headers) as response:
                        if response.status == 204:
                            outcome = "success"
                        elif response.status == 404:
                            outcome = "already_terminated"
                        else:
                            outcome = "failure"
            except Exception as exc:
                logger.error(
                    "vobiz_serializer.hangup_exception",
                    extra={
                        "operation": "vobiz_serializer.hangup",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_id": self._call_id,
                    },
                )
                outcome = "failure"
        logger.info(
            "vobiz_serializer.hangup",
            extra={
                "operation": "vobiz_serializer.hangup",
                "status": "success" if outcome in ("success", "already_terminated") else "failure",
                "outcome": outcome,
                "latency_ms": int((time.time() - start) * 1000),
                "call_id": self._call_id,
            },
        )
```

Note: this overrides the network call entirely (rather than calling `super()._hang_up_call()` and trying to scrape its result) because the upstream method returns `None` and only debug-logs the status. We replicate the contract verbatim — same URL, same headers, same status-code handling.

The existing `VobizOperator` class stays as-is for now — Task 2 wires the new serializer.

- [ ] **Step 1.4: Run test — verify it passes**

Run: `cd reach_layer/voice && uv run pytest tests/operators/test_vobiz_operator.py::test_logging_serializer_success_outcome -v`
Expected: PASS.

- [ ] **Step 1.5: Add edge-case tests — already-terminated, missing call_id, CancelFrame, double-EndFrame, 5xx**

Append to `reach_layer/voice/tests/operators/test_vobiz_operator.py`:

```python
@pytest.mark.asyncio
async def test_logging_serializer_already_terminated_404(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(404))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "already_terminated"
    assert rec.status == "success"


@pytest.mark.asyncio
async def test_logging_serializer_missing_call_id(caplog):
    serializer = _make_serializer(call_id=None)
    with caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "skipped_missing_credentials"
    assert rec.status == "failure"


@pytest.mark.asyncio
async def test_logging_serializer_cancel_frame_triggers_hangup():
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm:
        await serializer.serialize(CancelFrame())
    assert len(fake.delete_calls) == 1


@pytest.mark.asyncio
async def test_logging_serializer_idempotent_on_double_end_frame():
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(204))
    with cm:
        await serializer.serialize(EndFrame())
        await serializer.serialize(EndFrame())
    assert len(fake.delete_calls) == 1


@pytest.mark.asyncio
async def test_logging_serializer_5xx_outcome_failure(caplog):
    serializer = _make_serializer()
    cm, fake = _patch_aiohttp(_FakeResponse(500, text="boom"))
    with cm, caplog.at_level(logging.INFO, logger="src.operators.vobiz_operator"):
        await serializer.serialize(EndFrame())
    rec = next(r for r in caplog.records if r.message == "vobiz_serializer.hangup")
    assert rec.outcome == "failure"
    assert rec.status == "failure"
```

- [ ] **Step 1.6: Run tests — verify all pass**

Run: `cd reach_layer/voice && uv run pytest tests/operators/test_vobiz_operator.py -v`
Expected: all PASS, including the original 5 collected tests.

- [ ] **Step 1.7: Commit**

```bash
git add reach_layer/voice/src/operators/vobiz_operator.py reach_layer/voice/tests/operators/test_vobiz_operator.py
git commit -m "$(cat <<'EOF'
feat(reach_layer/voice): add LoggingVobizFrameSerializer for structured hangup logs (#199)

Subclass of pipecat.serializers.vobiz.VobizFrameSerializer that overrides
_hang_up_call to surface the Vobiz REST DELETE outcome
(success|already_terminated|failure|skipped_missing_credentials) into the
framework's structured-log convention. Same URL, headers, and status-code
mapping as upstream — no behavioural divergence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — Wire `LoggingVobizFrameSerializer` into `VobizOperator.create_transport`

**Files:**
- Modify: `reach_layer/voice/src/operators/vobiz_operator.py:104-113`
- Modify: `reach_layer/voice/tests/operators/test_vobiz_operator.py`

- [ ] **Step 2.1: Write failing test — `create_transport` uses the logging serializer**

Append to `reach_layer/voice/tests/operators/test_vobiz_operator.py`:

```python
def test_create_transport_uses_logging_serializer(config):
    op = VobizOperator(config)
    mock_ws = MagicMock()
    with patch("src.operators.vobiz_operator.FastAPIWebsocketTransport") as mock_transport_cls:
        op.create_transport(mock_ws, "stream-x", "call-y")
    kwargs = mock_transport_cls.call_args.kwargs
    serializer = kwargs["params"].serializer
    assert isinstance(serializer, LoggingVobizFrameSerializer)
    assert serializer._params.auto_hang_up is True
```

`config` and `VobizOperator` are already imported at the top of the file.

- [ ] **Step 2.2: Run test — verify it fails**

Run: `cd reach_layer/voice && uv run pytest tests/operators/test_vobiz_operator.py::test_create_transport_uses_logging_serializer -v`
Expected: FAIL with `assert isinstance(<VobizFrameSerializer>, LoggingVobizFrameSerializer)`.

- [ ] **Step 2.3: Swap the serializer instantiation in `create_transport`**

In `reach_layer/voice/src/operators/vobiz_operator.py`, inside `VobizOperator.create_transport`, replace:

```python
        serializer = VobizFrameSerializer(
            stream_id=stream_id,
            call_id=call_id,
            auth_id=self._auth_id,
            auth_token=self._auth_token,
            params=VobizFrameSerializer.InputParams(
                vobiz_sample_rate=self._sample_rate,
                auto_hang_up=True,
            ),
        )
```

with:

```python
        serializer = LoggingVobizFrameSerializer(
            stream_id=stream_id,
            call_id=call_id,
            auth_id=self._auth_id,
            auth_token=self._auth_token,
            params=VobizFrameSerializer.InputParams(
                vobiz_sample_rate=self._sample_rate,
                auto_hang_up=True,
            ),
        )
```

- [ ] **Step 2.4: Run test — verify it passes; full operator suite still green**

Run: `cd reach_layer/voice && uv run pytest tests/operators/test_vobiz_operator.py -v`
Expected: all PASS.

- [ ] **Step 2.5: Commit**

```bash
git add reach_layer/voice/src/operators/vobiz_operator.py reach_layer/voice/tests/operators/test_vobiz_operator.py
git commit -m "$(cat <<'EOF'
feat(reach_layer/voice): wire LoggingVobizFrameSerializer into VobizOperator (#199)

VobizOperator.create_transport now instantiates the logging subclass so
every Vobiz call benefits from structured hangup-outcome logs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — Push `EndFrame` from `_handle_done_event`

**Files:**
- Modify: `reach_layer/voice/src/pipecat_services/agent_core_llm.py:453-503`
- Modify: `reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py`

- [ ] **Step 3.1: Write failing test — `EndFrame` flows after the terminal-word `TextFrame`**

Append to `reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py`:

```python
@pytest.mark.asyncio
async def test_done_event_session_ended_pushes_endframe_after_terminal_word(config):
    """GH-199: pipeline must see EndFrame downstream so the Vobiz serializer
    can issue its REST DELETE hangup. EndFrame must come after the terminal
    word so TTS can finish speaking."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame, TextFrame

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "धन्यवाद"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    text_idx = next(
        i for i, f in enumerate(pushed)
        if isinstance(f, TextFrame) and getattr(f, "text", "") == "धन्यवाद"
    )
    end_idx = next(i for i, f in enumerate(pushed) if isinstance(f, EndFrame))
    assert end_idx > text_idx, "EndFrame must be pushed after the terminal-word TextFrame"


@pytest.mark.asyncio
async def test_done_event_session_ended_empty_terminal_word_still_pushes_endframe(config):
    """GH-199: empty terminal_word still triggers EndFrame so the leg drops."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": ""},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=True, turn_status="completed"))

    assert any(isinstance(f, EndFrame) for f in pushed)


@pytest.mark.asyncio
async def test_done_event_session_ended_false_does_not_push_endframe(config):
    """GH-199: non-terminal turn must not push EndFrame (would tear down pipeline)."""
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    from reach_layer_base import DoneEvent
    from pipecat.frames.frames import EndFrame

    pushed = []
    telephony = _make_fake_telephony()
    proc = AgentCoreLLMProcessor(
        config,
        call_sid="CA1",
        session_id="s1",
        channel_config={"terminal_word": "धन्यवाद"},
        telephony=telephony,
    )
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    await proc._handle_done_event(DoneEvent(session_ended=False, turn_status="completed"))

    assert not any(isinstance(f, EndFrame) for f in pushed)
```

- [ ] **Step 3.2: Run tests — verify two failures, one pass**

Run: `cd reach_layer/voice && uv run pytest tests/pipecat_services/test_agent_core_llm.py -k "endframe" -v`
Expected: FAIL on `test_done_event_session_ended_pushes_endframe_after_terminal_word` and `test_done_event_session_ended_empty_terminal_word_still_pushes_endframe` (no `EndFrame` is pushed). PASS on `test_done_event_session_ended_false_does_not_push_endframe` (still no EndFrame).

- [ ] **Step 3.3: Push `EndFrame` after the terminal-word frame**

In `reach_layer/voice/src/pipecat_services/agent_core_llm.py`, inside `_handle_done_event`, locate the block:

```python
        terminal_word = (self._channel_config or {}).get("terminal_word", "") or ""
        if terminal_word:
            await self.push_frame(TextFrame(terminal_word))
        else:
            logger.warning(
                "agent_core_llm.session_ended_no_terminal_word",
                extra={
                    "operation": "agent_core_llm.done",
                    "status": "skipped",
                    "reason": "terminal_word empty",
                    "call_sid": self._call_sid,
                },
            )

        if self._telephony is not None:
```

Insert immediately before the `if self._telephony is not None:` line:

```python
        # GH-199: push EndFrame so the Vobiz serializer can issue its REST
        # DELETE hangup. This is the load-bearing step — ws.close() alone is
        # not enough to drop the telephony leg.
        await self.push_frame(EndFrame())
```

Confirm `EndFrame` is already imported at the top of the file. If not, add it to the existing pipecat import block. (It is used at line 451.)

- [ ] **Step 3.4: Run tests — verify all three pass and no other regressions**

Run: `cd reach_layer/voice && uv run pytest tests/pipecat_services/test_agent_core_llm.py -v`
Expected: all PASS, including the existing GH-137 Task 14 tests.

- [ ] **Step 3.5: Commit**

```bash
git add reach_layer/voice/src/pipecat_services/agent_core_llm.py reach_layer/voice/tests/pipecat_services/test_agent_core_llm.py
git commit -m "$(cat <<'EOF'
fix(reach_layer/voice): push EndFrame on session-end to trigger Vobiz hangup (#199)

The Vobiz REST DELETE hangup is wired through pipecat's serializer on
EndFrame/CancelFrame, but _handle_done_event never let an EndFrame flow.
Result: the bot said goodbye and called ws.close(), Vobiz held the leg
up until the caller hung up. Now we push EndFrame after the terminal
word so the serializer's _hang_up_call fires and the leg drops.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — `VobizAdapter.close_call` defensive fallback + structured log fields

**Files:**
- Modify: `reach_layer/voice/src/vobiz_adapter.py:312-352`
- Modify: `reach_layer/voice/tests/test_vobiz_adapter.py`

- [ ] **Step 4.1: Write failing test — `close_call` log records `vendor_signal` and `outcome`**

Append to `reach_layer/voice/tests/test_vobiz_adapter.py`:

```python
import logging


@pytest.mark.asyncio
async def test_close_call_logs_vendor_signal_and_outcome_no_active_ws(config, caplog):
    adapter = VobizAdapter(config)
    with caplog.at_level(logging.INFO, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    invoked = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call")
    assert invoked.reason == "session_end"
    assert invoked.vendor_signal == "vobiz_rest_delete"
    skipped = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_no_active_ws")
    assert skipped.outcome == "skipped"


@pytest.mark.asyncio
async def test_close_call_with_active_ws_logs_success(config, caplog):
    adapter = VobizAdapter(config)
    fake_ws = MagicMock()
    fake_ws.close = AsyncMock()
    adapter._active_websocket = fake_ws
    with caplog.at_level(logging.INFO, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    fake_ws.close.assert_awaited_once()
    rec = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_complete")
    assert rec.outcome == "ws_closed"
    assert rec.vendor_signal == "vobiz_rest_delete"
    assert isinstance(rec.latency_ms, int)


@pytest.mark.asyncio
async def test_close_call_with_active_ws_close_raises_logs_failure(config, caplog):
    adapter = VobizAdapter(config)
    fake_ws = MagicMock()
    fake_ws.close = AsyncMock(side_effect=RuntimeError("ws boom"))
    adapter._active_websocket = fake_ws
    with caplog.at_level(logging.ERROR, logger="src.vobiz_adapter"):
        await adapter.close_call(reason="session_end")
    rec = next(r for r in caplog.records if r.message == "vobiz_adapter.close_call_failed")
    assert rec.outcome == "failure"
    assert "RuntimeError" in rec.error
```

- [ ] **Step 4.2: Run tests — verify they fail**

Run: `cd reach_layer/voice && uv run pytest tests/test_vobiz_adapter.py -k "close_call" -v`
Expected: FAIL — current `close_call` does not emit `vendor_signal` / `outcome` / `close_call_complete`.

- [ ] **Step 4.3: Reshape `close_call`**

In `reach_layer/voice/src/vobiz_adapter.py`, replace the entire `close_call` method (currently lines 312-352) with:

```python
    async def close_call(self, *, reason: str = "normal") -> None:
        """Defensive fallback close for the active Vobiz call (GH-137, GH-199).

        On the happy path the bot-initiated end-of-session is driven by an
        ``EndFrame`` pushed through the pipecat pipeline from
        ``AgentCoreLLMProcessor._handle_done_event``; the
        ``LoggingVobizFrameSerializer`` then issues the Vobiz REST DELETE
        and the underlying WebSocket is closed by pipecat's pipeline
        shutdown. This method remains as a defensive fallback for paths
        that bypass that flow (e.g. errors raised before the EndFrame is
        pushed, or non-pipeline-mediated session terminations).
        ``LoggingVobizFrameSerializer`` is idempotent thanks to its
        ``_hangup_attempted`` guard, so calling this method after a clean
        EndFrame shutdown is safe.

        Args:
            reason: Free-form reason string recorded in structured logs.
        """
        start = time.time()
        logger.info(
            "vobiz_adapter.close_call",
            extra={
                "operation": "vobiz_adapter.close_call",
                "status": "invoked",
                "reason": reason,
                "vendor_signal": "vobiz_rest_delete",
            },
        )
        ws = self._active_websocket
        if ws is None:
            logger.warning(
                "vobiz_adapter.close_call_no_active_ws",
                extra={
                    "operation": "vobiz_adapter.close_call",
                    "status": "skipped",
                    "outcome": "skipped",
                    "reason": "no active websocket",
                    "vendor_signal": "vobiz_rest_delete",
                },
            )
            return
        try:
            await ws.close()
        except Exception as exc:
            logger.error(
                "vobiz_adapter.close_call_failed",
                extra={
                    "operation": "vobiz_adapter.close_call",
                    "status": "failure",
                    "outcome": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "vendor_signal": "vobiz_rest_delete",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return
        logger.info(
            "vobiz_adapter.close_call_complete",
            extra={
                "operation": "vobiz_adapter.close_call",
                "status": "success",
                "outcome": "ws_closed",
                "vendor_signal": "vobiz_rest_delete",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
```

`time` is already imported at module level in `vobiz_adapter.py`. If not, add `import time`.

- [ ] **Step 4.4: Run tests — verify all pass; whole adapter suite still green**

Run: `cd reach_layer/voice && uv run pytest tests/test_vobiz_adapter.py -v`
Expected: all PASS.

- [ ] **Step 4.5: Commit**

```bash
git add reach_layer/voice/src/vobiz_adapter.py reach_layer/voice/tests/test_vobiz_adapter.py
git commit -m "$(cat <<'EOF'
refactor(reach_layer/voice): close_call as defensive fallback with structured outcome (#199)

Now that the happy-path teardown is driven by EndFrame through
LoggingVobizFrameSerializer, close_call() is a defensive fallback. Its
structured log gains vendor_signal=vobiz_rest_delete, outcome, and
latency_ms so observability can attribute teardown source consistently.
LoggingVobizFrameSerializer's _hangup_attempted guard makes a follow-on
close_call idempotent at the API layer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — Final verification

- [ ] **Step 5.1: Run the full voice test suite**

Run: `cd reach_layer/voice && uv run pytest -q`
Expected: all PASS, no regressions.

- [ ] **Step 5.2: Coverage spot-check on touched files**

Run: `cd reach_layer/voice && uv run pytest --cov=src.operators.vobiz_operator --cov=src.vobiz_adapter --cov=src.pipecat_services.agent_core_llm --cov-report=term-missing -q`
Expected: ≥ 70 % line coverage on each touched module. If the existing baseline is already < 70 % on `agent_core_llm.py`, do **not** add unrelated tests — record the number in the PR description so the reviewer sees the change is non-regressive.

- [ ] **Step 5.3: Push branch + open PR**

```bash
git push -u origin fix/199-vobiz-end-call
gh pr create --title "fix(reach_layer/voice): trigger Vobiz REST hangup on bot-initiated end-session (#199)" --body "$(cat <<'EOF'
## Summary
- Push `EndFrame()` from `AgentCoreLLMProcessor._handle_done_event` after the terminal-word `TextFrame` so pipecat's `VobizFrameSerializer` actually issues its REST DELETE hangup. `ws.close()` alone never caused the serializer to see an `EndFrame`, which is why the 2026-04-24 KKB call only ended on caller hangup.
- Add `LoggingVobizFrameSerializer`, a thin subclass that surfaces the REST DELETE outcome (`success` / `already_terminated` / `failure` / `skipped_missing_credentials`) into the framework's structured-log convention.
- Demote `VobizAdapter.close_call()` to a defensive fallback with richer `vendor_signal` / `outcome` / `latency_ms` log fields. The serializer's `_hangup_attempted` guard makes it idempotent at the API layer.

## Test plan
- [x] `cd reach_layer/voice && uv run pytest -q` — all green.
- [x] Mocked-HTTP unit tests for `LoggingVobizFrameSerializer`: success (204), already-terminated (404), missing call_id, `CancelFrame`-triggered, double-`EndFrame` idempotence, 5xx failure.
- [x] Frame-ordering integration tests in `test_agent_core_llm.py`: `EndFrame` after terminal-word `TextFrame`; empty terminal-word still emits `EndFrame`; non-terminal turn does not.
- [x] Adapter-level structured-log tests for `close_call`.
- [ ] Empirical post-merge verification on the VM: fresh KKB voice call → `vobiz_serializer.hangup outcome=success` log present; `vobiz_adapter.call_disconnected` within ≤ 2 s of last bot SentenceEvent.

Spec: `docs/superpowers/specs/2026-04-25-gh199-vobiz-end-call-design.md`
Closes #199.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** §3.1 → Task 3; §3.2 → Task 4; §3.3 → Tasks 1+2+4; §4.1 → Task 1 (5 cases); §4.2 → Task 3 (3 cases); §4.3 → Task 4 (3 cases); §5 → out-of-scope post-merge step (acknowledged in PR body); §6 (interaction rules) → no new cross-module calls; §7 (out of scope) → no tasks; §9 → all rows have a task.
- **Placeholder scan:** no TBD/TODO; no "add appropriate error handling"; every code step shows the actual code.
- **Type/symbol consistency:** `LoggingVobizFrameSerializer` defined in Task 1 used in Task 2 + tests; `_make_fake_telephony` reused from existing test fixture; `_FakeResponse` / `_FakeSession` defined once in Task 1 and reused in Task 1 sub-tests; `vendor_signal="vobiz_rest_delete"` appears identically in adapter + tests.
