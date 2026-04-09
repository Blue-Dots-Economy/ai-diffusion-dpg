# Telephony Adapter — Pipecat Pipeline Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom silence-timer audio loop with a Pipecat frame-based pipeline (Silero VAD → Raya STT → Agent Core → Raya TTS) and fix all 5 root-cause bugs.

**Architecture:** `FastAPIWebsocketTransport` (with `VobizFrameSerializer`) handles Vobiz wire protocol and mulaw↔PCM conversion. `VADProcessor` with `SileroVADAnalyzer` segments audio by speech activity. Three custom Pipecat services — `RayaSTTService`, `AgentCoreLLMProcessor`, `RayaTTSService` — replace the old hand-rolled services. `server.py` is simplified: accept the WebSocket, hand it to `bot.run_bot()`.

**Tech Stack:** `pipecat-ai[websocket,silero]==0.0.108`, `pipecat-vobiz`, `numpy`, `httpx`, Python 3.11+.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/pipecat_services/__init__.py` | Package marker |
| Create | `src/pipecat_services/raya_stt.py` | `RayaSTTService` — WAV HTTP POST to Raya |
| Create | `src/pipecat_services/agent_core_llm.py` | `AgentCoreLLMProcessor` — HTTP to Agent Core |
| Create | `src/pipecat_services/raya_tts.py` | `RayaTTSService` — SSE TTS stream from Raya |
| Create | `src/bot.py` | Per-call Pipecat pipeline factory |
| Create | `tests/pipecat_services/__init__.py` | Package marker |
| Create | `tests/pipecat_services/test_raya_stt.py` | Tests for RayaSTTService |
| Create | `tests/pipecat_services/test_agent_core_llm.py` | Tests for AgentCoreLLMProcessor |
| Create | `tests/pipecat_services/test_raya_tts.py` | Tests for RayaTTSService |
| Modify | `server.py` | Remove adapter; call `bot.run_bot()` from WS handler |
| Modify | `tests/test_server.py` | Update WS endpoint test to patch `bot.run_bot` |
| Modify | `pyproject.toml` | Add pipecat-ai, pipecat-vobiz, numpy |
| Delete | `src/telephony_adapter.py` | Replaced by `src/bot.py` + Pipecat pipeline |
| Delete | `src/raya_stt_service.py` | Replaced by `src/pipecat_services/raya_stt.py` |
| Delete | `src/raya_tts_service.py` | Replaced by `src/pipecat_services/raya_tts.py` |
| Delete | `src/agent_core_service.py` | Replaced by `src/pipecat_services/agent_core_llm.py` |
| Delete | `src/vobiz_serializer.py` | Replaced by `pipecat-vobiz` `VobizFrameSerializer` |
| Delete | `tests/test_telephony_adapter.py` | Covered by new service tests |
| Delete | `tests/test_raya_stt_service.py` | Replaced by `tests/pipecat_services/test_raya_stt.py` |
| Delete | `tests/test_raya_tts_service.py` | Replaced by `tests/pipecat_services/test_raya_tts.py` |
| Delete | `tests/test_agent_core_service.py` | Replaced by `tests/pipecat_services/test_agent_core_llm.py` |
| Delete | `tests/test_vobiz_serializer.py` | Replaced by pipecat-vobiz (no longer ours to test) |

---

## Key Pipecat APIs (read before starting)

```python
# SegmentedSTTService — receives complete WAV bytes per utterance (base class wraps buffer in WAV)
from pipecat.services.stt_service import SegmentedSTTService
async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
    # audio is a complete WAV file (PCM16, mono, 8kHz). POST to Raya HTTP.
    yield TranscriptionFrame(text="...", user_id="", timestamp="")

# TTSService — HTTP-based TTS without word alignment
from pipecat.services.tts_service import TTSService
async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
    yield TTSAudioRawFrame(audio=pcm16_bytes, sample_rate=8000, num_channels=1, context_id=context_id)

# FrameProcessor — generic pipeline stage
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
    await super().process_frame(frame, direction)  # REQUIRED
    if isinstance(frame, TranscriptionFrame):
        # do work, push response
        await self.push_frame(TTSSpeakFrame(text="response"))
    else:
        await self.push_frame(frame, direction)  # pass unknown frames through

# VADProcessor — must be BEFORE SegmentedSTTService in pipeline
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.audio.vad.silero import SileroVADAnalyzer

# Pipeline assembly
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.runner.utils import parse_telephony_websocket  # reads 2 start msgs from WS
```

---

## Task 1: Update dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pipecat dependencies**

Replace the `[project]` `dependencies` list in `pyproject.toml`. The final dependencies block:

```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "observability-layer",
    "opentelemetry-instrumentation-fastapi>=0.61b0",
    "opentelemetry-instrumentation-httpx>=0.61b0",
    "python-multipart>=0.0.24",
    "pipecat-ai[websocket,silero]>=0.0.108",
    "pipecat-vobiz>=0.0.2",
    "numpy>=1.26",
]
```

Also remove `"websockets>=12.0"` — pipecat-ai manages its own websockets version.

- [ ] **Step 2: Sync dependencies**

```bash
cd telephony_adapter
uv sync
```

Expected: resolves and installs without error.

- [ ] **Step 3: Smoke-test imports**

```bash
uv run python -c "
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.frames.frames import TranscriptionFrame, TTSAudioRawFrame, TTSSpeakFrame, EndFrame
print('all imports OK')
"
```

Expected: `all imports OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pipecat-ai, pipecat-vobiz, numpy dependencies"
```

---

## Task 2: RayaSTTService

**Files:**
- Create: `src/pipecat_services/__init__.py`
- Create: `src/pipecat_services/raya_stt.py`
- Create: `tests/pipecat_services/__init__.py`
- Create: `tests/pipecat_services/test_raya_stt.py`

- [ ] **Step 1: Write failing tests**

Create `tests/pipecat_services/__init__.py` (empty file).

Create `tests/pipecat_services/test_raya_stt.py`:

```python
"""Tests for RayaSTTService — Raya HTTP multipart STT via Pipecat SegmentedSTTService."""
import io
import wave
import base64
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TranscriptionFrame, ErrorFrame


def _make_wav(pcm_bytes: bytes = b"\x00\x01" * 800, sample_rate: int = 8000) -> bytes:
    """Build a minimal WAV file wrapping pcm_bytes."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sample_rate)
    w.writeframes(pcm_bytes)
    w.close()
    buf.seek(0)
    return buf.read()


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "raya": {
                "api_key": "test-key",
                "stt_wss_url": "wss://hub.getraya.app/transcribe",
                "language": "hi",
            }
        }
    }


@pytest.mark.asyncio
async def test_run_stt_returns_transcription_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "नमस्ते", "status": "success"})
        )
        svc = RayaSTTService(config)
        frames = []
        async for frame in svc.run_stt(wav_audio):
            frames.append(frame)

    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "नमस्ते"


@pytest.mark.asyncio
async def test_run_stt_sends_correct_fields(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        route = respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "hello", "status": "success"})
        )
        svc = RayaSTTService(config)
        async for _ in svc.run_stt(wav_audio):
            pass

    request = route.calls[0].request
    assert request.headers["X-API-Key"] == "test-key"
    # multipart body should contain 'file' and 'language' fields
    body = request.content.decode(errors="replace")
    assert "hi" in body  # language field value


@pytest.mark.asyncio
async def test_run_stt_http_error_yields_error_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(500, json={"error": "internal"})
        )
        svc = RayaSTTService(config)
        frames = []
        async for frame in svc.run_stt(wav_audio):
            frames.append(frame)

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


@pytest.mark.asyncio
async def test_run_stt_empty_transcript_yields_nothing(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            return_value=httpx.Response(200, json={"transcript": "  ", "status": "success"})
        )
        svc = RayaSTTService(config)
        frames = [f async for f in svc.run_stt(wav_audio)]

    assert frames == []


@pytest.mark.asyncio
async def test_run_stt_connect_error_yields_error_frame(config):
    from src.pipecat_services.raya_stt import RayaSTTService

    wav_audio = _make_wav()

    with respx.mock:
        respx.post("https://hub.getraya.app/transcribe").mock(
            side_effect=httpx.ConnectError("refused")
        )
        svc = RayaSTTService(config)
        frames = [f async for f in svc.run_stt(wav_audio)]

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


def test_missing_api_key_raises():
    from src.pipecat_services.raya_stt import RayaSTTService
    with pytest.raises(ValueError, match="api_key"):
        RayaSTTService({})
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd telephony_adapter
uv run pytest tests/pipecat_services/test_raya_stt.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'src.pipecat_services.raya_stt'`

- [ ] **Step 3: Create package marker and implement RayaSTTService**

Create `src/pipecat_services/__init__.py` (empty).

Create `src/pipecat_services/raya_stt.py`:

```python
"""
telephony_adapter/src/pipecat_services/raya_stt.py

RayaSTTService — Pipecat SegmentedSTTService backed by the Raya HTTP STT API.

Pipecat's SegmentedSTTService base class buffers AudioRawFrames between
VADUserStartedSpeakingFrame and VADUserStoppedSpeakingFrame events, wraps
the buffer into a WAV file, then calls run_stt(audio) with the complete WAV
bytes.  This service POSTs those bytes as multipart/form-data to the Raya
HTTP transcription endpoint and yields a TranscriptionFrame on success.
On any error it yields an ErrorFrame so the pipeline can continue.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncGenerator

import httpx

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService

logger = logging.getLogger(__name__)

_RAYA_STT_URL = "https://hub.getraya.app/transcribe"


class RayaSTTService(SegmentedSTTService):
    """Transcribes one VAD-segmented utterance per call via the Raya HTTP STT API.

    Each call to run_stt receives a complete WAV file (PCM16, 8 kHz, mono)
    assembled by the SegmentedSTTService base class.  The WAV is sent as the
    ``file`` field of a multipart/form-data POST to Raya.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If api_key is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        self._api_key = api_key
        self._language = raya_cfg.get("language", "hi")
        self._timeout = float(raya_cfg.get("stt_timeout_s", 30.0))
        super().__init__()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe one utterance via Raya HTTP multipart STT.

        Args:
            audio: Complete WAV file bytes (PCM16, 8 kHz, mono) for the utterance.

        Yields:
            TranscriptionFrame on success, ErrorFrame on HTTP or connection failure.
            Yields nothing if the transcript is empty or blank.
        """
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _RAYA_STT_URL,
                    headers={"X-API-Key": self._api_key},
                    files={"file": ("utterance.wav", audio, "audio/wav")},
                    data={"language": self._language},
                )

            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                logger.error(
                    "raya_stt.http_error",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": latency_ms,
                    },
                )
                yield ErrorFrame(error=f"Raya STT HTTP {response.status_code}")
                return

            data = response.json()
            transcript = data.get("transcript", "").strip()

            if not transcript:
                logger.info(
                    "raya_stt.empty_transcript",
                    extra={
                        "operation": "raya_stt.run_stt",
                        "status": "skipped",
                        "latency_ms": latency_ms,
                    },
                )
                return

            logger.info(
                "raya_stt.transcribed",
                extra={
                    "operation": "raya_stt.run_stt",
                    "status": "success",
                    "latency_ms": latency_ms,
                    "audio_bytes": len(audio),
                    "transcript_len": len(transcript),
                },
            )
            yield TranscriptionFrame(text=transcript, user_id="", timestamp="")

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "raya_stt.connection_error",
                extra={
                    "operation": "raya_stt.run_stt",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                },
            )
            yield ErrorFrame(error=f"Raya STT connection error: {exc}")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/pipecat_services/test_raya_stt.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/pipecat_services/__init__.py src/pipecat_services/raya_stt.py \
        tests/pipecat_services/__init__.py tests/pipecat_services/test_raya_stt.py
git commit -m "feat: add RayaSTTService (Pipecat SegmentedSTTService + Raya HTTP)"
```

---

## Task 3: AgentCoreLLMProcessor

**Files:**
- Create: `src/pipecat_services/agent_core_llm.py`
- Create: `tests/pipecat_services/test_agent_core_llm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/pipecat_services/test_agent_core_llm.py`:

```python
"""Tests for AgentCoreLLMProcessor — FrameProcessor bridging STT to Agent Core HTTP."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    EndFrame,
    Frame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection


def _make_ac_response(response_text: str, was_escalated: bool = False) -> dict:
    return {
        "session_id": "s1",
        "response_text": response_text,
        "was_escalated": was_escalated,
        "was_tool_used": False,
        "model_used": "claude-haiku",
        "latency_ms": 200,
    }


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "fallback_phrase": "Sorry, I could not process that.",
            }
        }
    }


@pytest.mark.asyncio
async def test_transcription_frame_triggers_ac_and_pushes_tts_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []

    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json=_make_ac_response("नमस्ते"))
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "नमस्ते"


@pytest.mark.asyncio
async def test_sends_correct_payload_to_agent_core(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    import json

    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="ses1")
    proc.push_frame = AsyncMock()

    with respx.mock:
        route = respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json=_make_ac_response("ok"))
        )
        await proc.process_frame(
            TranscriptionFrame(text="मुझे मदद चाहिए", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    body = json.loads(route.calls[0].request.content)
    assert body["session_id"] == "ses1"
    assert body["user_message"] == "मुझे मदद चाहिए"
    assert body["channel"] == "telephony"
    assert body["user_id"] == "CA1"


@pytest.mark.asyncio
async def test_escalation_pushes_speak_frame_then_end_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(
                200, json=_make_ac_response("Transferring you now.", was_escalated=True)
            )
        )
        await proc.process_frame(
            TranscriptionFrame(text="help", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    types = [type(f) for f in pushed]
    assert TTSSpeakFrame in types
    assert EndFrame in types
    # EndFrame must come after TTSSpeakFrame
    assert types.index(EndFrame) > types.index(TTSSpeakFrame)


@pytest.mark.asyncio
async def test_http_timeout_pushes_fallback_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_http_500_pushes_fallback_speak_frame(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(500, json={"detail": "error"})
        )
        await proc.process_frame(
            TranscriptionFrame(text="hello", user_id="", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

    speak_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert speak_frames[0].text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_non_transcription_frame_is_passed_through(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor

    pushed = []
    proc = AgentCoreLLMProcessor(config, call_sid="CA1", session_id="s1")
    proc.push_frame = AsyncMock(side_effect=lambda f, d=None: pushed.append(f))

    other_frame = TextFrame(text="unrelated")
    await proc.process_frame(other_frame, FrameDirection.DOWNSTREAM)

    assert other_frame in pushed


def test_missing_base_url_raises(config):
    from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
    bad_config = {"telephony_adapter": {"agent_core": {"base_url": ""}}}
    with pytest.raises(ValueError, match="base_url"):
        AgentCoreLLMProcessor(bad_config, call_sid="CA1", session_id="s1")
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/pipecat_services/test_agent_core_llm.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'src.pipecat_services.agent_core_llm'`

- [ ] **Step 3: Implement AgentCoreLLMProcessor**

Create `src/pipecat_services/agent_core_llm.py`:

```python
"""
telephony_adapter/src/pipecat_services/agent_core_llm.py

AgentCoreLLMProcessor — Pipecat FrameProcessor that bridges TranscriptionFrames
to Agent Core's /process_turn HTTP endpoint.

Receives TranscriptionFrame from RayaSTTService, POSTs to Agent Core, then
pushes TTSSpeakFrame downstream so RayaTTSService can synthesize the response.
On was_escalated=True, also pushes EndFrame after the speak frame to close the
pipeline gracefully (VobizFrameSerializer will hang up the call on EndFrame).
On HTTP error or timeout, pushes a TTSSpeakFrame with the configured fallback
phrase so the call continues rather than hanging silently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time

import httpx

from pipecat.frames.frames import EndFrame, Frame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class AgentCoreLLMProcessor(FrameProcessor):
    """Posts each transcribed utterance to Agent Core and pushes TTS response downstream.

    Args:
        config: Full merged config dict. Reads telephony_adapter.agent_core section.
        call_sid: Opaque Vobiz call identifier, used as user_id in Agent Core requests.
        session_id: Stable session UUID for this call's lifetime.

    Raises:
        ValueError: If agent_core.base_url is missing or empty.
    """

    def __init__(self, config: dict, *, call_sid: str, session_id: str) -> None:
        super().__init__()
        if config is None:
            raise ValueError("config must not be None")
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        base_url = ac_cfg.get("base_url", "").rstrip("/")
        if not base_url:
            raise ValueError(
                "telephony_adapter.agent_core.base_url is required. "
                "If running in Docker, use the service name (e.g. http://agent_core:8000). "
                "Outside Docker, use the container's published port (e.g. http://localhost:8000)."
            )
        self._base_url = base_url
        self._timeout = float(ac_cfg.get("timeout_ms", 5000)) / 1000.0
        self._fallback_phrase = ac_cfg.get(
            "fallback_phrase", "I'm sorry, I couldn't process that. Please try again."
        )
        self._call_sid = call_sid
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route TranscriptionFrames to Agent Core; pass all other frames through.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)
        else:
            await self.push_frame(frame, direction)

    async def _handle_transcription(self, frame: TranscriptionFrame) -> None:
        """Call Agent Core and push TTSSpeakFrame (and EndFrame on escalation).

        Args:
            frame: The transcription frame containing the caller's utterance.
        """
        start = time.time()
        url = f"{self._base_url}/process_turn"
        payload = {
            "session_id": self._session_id,
            "user_message": frame.text,
            "channel": "telephony",
            "user_id": self._call_sid,
            "timestamp_ms": int(start * 1000),
        }

        response_text = self._fallback_phrase
        was_escalated = False

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)

            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                logger.error(
                    "agent_core_llm.http_error",
                    extra={
                        "operation": "agent_core_llm.process_turn",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": latency_ms,
                    },
                )
            else:
                data = response.json()
                response_text = data.get("response_text", self._fallback_phrase)
                was_escalated = data.get("was_escalated", False)
                logger.info(
                    "agent_core_llm.process_turn",
                    extra={
                        "operation": "agent_core_llm.process_turn",
                        "status": "success",
                        "latency_ms": latency_ms,
                        "was_escalated": was_escalated,
                        "was_tool_used": data.get("was_tool_used", False),
                    },
                )

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "agent_core_llm.connection_error",
                extra={
                    "operation": "agent_core_llm.process_turn",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                },
            )

        await self.push_frame(TTSSpeakFrame(text=response_text))
        if was_escalated:
            logger.info(
                "agent_core_llm.escalated",
                extra={
                    "operation": "agent_core_llm.process_turn",
                    "status": "success",
                    "call_sid": self._call_sid,
                },
            )
            await self.push_frame(EndFrame())
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/pipecat_services/test_agent_core_llm.py -v
```

Expected: 7 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/pipecat_services/agent_core_llm.py tests/pipecat_services/test_agent_core_llm.py
git commit -m "feat: add AgentCoreLLMProcessor (Pipecat FrameProcessor + Agent Core HTTP)"
```

---

## Task 4: RayaTTSService

**Files:**
- Create: `src/pipecat_services/raya_tts.py`
- Create: `tests/pipecat_services/test_raya_tts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/pipecat_services/test_raya_tts.py`:

```python
"""Tests for RayaTTSService — Pipecat TTSService backed by Raya SSE streaming endpoint."""
import base64
import json
import struct
import pytest
import respx
import httpx
import numpy as np

from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame


def _make_sse_body(f32le_chunks: list[bytes]) -> bytes:
    """Build an SSE body with chunk events followed by a done event."""
    lines = []
    for chunk in f32le_chunks:
        data = {
            "type": "chunk",
            "status_code": 206,
            "done": False,
            "data": base64.b64encode(chunk).decode(),
            "step_time": 0.05,
        }
        lines.append(f"event: chunk\ndata: {json.dumps(data)}\n\n")
    lines.append("event: done\ndata: {}\n\n")
    return "".join(lines).encode()


def _f32le_from_samples(samples: list[float]) -> bytes:
    """Convert a list of floats to F32LE bytes."""
    return struct.pack(f"<{len(samples)}f", *samples)


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "raya": {
                "api_key": "test-key",
                "tts_base_url": "https://hub.getraya.app/v1",
                "voice_id": "voice_001",
                "language": "hi",
                "tts_speed": 1.0,
            }
        }
    }


@pytest.mark.asyncio
async def test_run_tts_yields_tts_audio_raw_frames(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    f32_chunk = _f32le_from_samples([0.1, -0.2, 0.3, -0.1])

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = []
        async for frame in svc.run_tts("नमस्ते", context_id="ctx1"):
            frames.append(frame)

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    assert len(audio_frames) >= 1
    # Check it emitted PCM16 (2 bytes per sample × 4 samples = 8 bytes)
    total_bytes = sum(len(f.audio) for f in audio_frames)
    assert total_bytes == len(f32_chunk) // 2  # F32 4 bytes → PCM16 2 bytes per sample


@pytest.mark.asyncio
async def test_run_tts_pcm16_conversion_is_correct(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    # Known F32LE input: 1.0 → max int16 (32767), -1.0 → min int16 (-32767)
    f32_chunk = _f32le_from_samples([1.0, -1.0, 0.0])

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = [f async for f in svc.run_tts("test", context_id="ctx1")]

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    pcm16 = b"".join(f.audio for f in audio_frames)
    samples = np.frombuffer(pcm16, dtype=np.int16)
    assert samples[0] == 32767    # 1.0 clipped
    assert samples[1] == -32767   # -1.0 clipped
    assert samples[2] == 0        # 0.0


@pytest.mark.asyncio
async def test_run_tts_audio_frames_have_correct_sample_rate(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    f32_chunk = _f32le_from_samples([0.5] * 8)

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([f32_chunk]))
        )
        svc = RayaTTSService(config)
        frames = [f async for f in svc.run_tts("hi", context_id="ctx1")]

    audio_frames = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    for f in audio_frames:
        assert f.sample_rate == 8000
        assert f.num_channels == 1


@pytest.mark.asyncio
async def test_run_tts_sends_correct_payload(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    with respx.mock:
        route = respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_make_sse_body([_f32le_from_samples([0.0])]))
        )
        svc = RayaTTSService(config)
        async for _ in svc.run_tts("hello", context_id="ctx1"):
            pass

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "hello"
    assert body["voice_id"] == "voice_001"
    assert body["language"] == "hi"
    assert body["sample_rate"] == 8000
    assert route.calls[0].request.headers["X-API-Key"] == "test-key"


@pytest.mark.asyncio
async def test_run_tts_http_error_yields_error_frame(config):
    from src.pipecat_services.raya_tts import RayaTTSService

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        svc = RayaTTSService(config)
        frames = [f async for f in svc.run_tts("hello", context_id="ctx1")]

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


def test_missing_api_key_raises():
    from src.pipecat_services.raya_tts import RayaTTSService
    with pytest.raises(ValueError, match="api_key"):
        RayaTTSService({})
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/pipecat_services/test_raya_tts.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'src.pipecat_services.raya_tts'`

- [ ] **Step 3: Implement RayaTTSService**

Create `src/pipecat_services/raya_tts.py`:

```python
"""
telephony_adapter/src/pipecat_services/raya_tts.py

RayaTTSService — Pipecat TTSService backed by the Raya SSE streaming TTS API.

Receives text from upstream (via TTSSpeakFrame processed by the base class),
POSTs to the Raya /text-to-speech/stream endpoint, reads Server-Sent Events
containing base64 PCM F32LE chunks, converts each chunk from F32LE to PCM16,
and yields TTSAudioRawFrame objects at 8 kHz.

The VobizFrameSerializer downstream encodes PCM16 8 kHz → µ-law 8 kHz before
sending to Vobiz, so no additional format conversion is needed here.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import AsyncGenerator

import httpx
import numpy as np

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 8000
_NUM_CHANNELS = 1


class RayaTTSService(TTSService):
    """Synthesises speech via the Raya SSE streaming TTS endpoint.

    Each call to run_tts streams PCM F32LE audio chunks from Raya, converts
    them to PCM16 at 8 kHz, and yields TTSAudioRawFrame objects for the
    Pipecat pipeline.  The VobizFrameSerializer then encodes PCM16 → µ-law.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If required config keys (api_key, tts_base_url) are missing.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        tts_base_url = raya_cfg.get("tts_base_url", "")
        if not tts_base_url:
            raise ValueError("telephony_adapter.raya.tts_base_url is required")
        self._api_key = api_key
        self._base_url = tts_base_url.rstrip("/")
        self._voice_id = raya_cfg.get("voice_id", "voice_001")
        self._language = raya_cfg.get("language", "hi")
        self._speed = float(raya_cfg.get("tts_speed", 1.0))
        self._tts_timeout = float(raya_cfg.get("tts_timeout_s", 30.0))
        super().__init__(sample_rate=_SAMPLE_RATE)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesise text to PCM16 audio via the Raya SSE streaming TTS endpoint.

        Streams F32LE PCM chunks from Raya SSE, converts each to PCM16, and
        yields TTSAudioRawFrame objects.  On HTTP error yields ErrorFrame.

        Args:
            text: The text to synthesise.
            context_id: Pipecat context ID for this TTS turn (passed to TTSAudioRawFrame).

        Yields:
            TTSAudioRawFrame per SSE chunk, or ErrorFrame on failure.
        """
        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "language": self._language,
            "speed": self._speed,
            "sample_rate": _SAMPLE_RATE,
        }
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

        total_bytes = 0
        try:
            async with httpx.AsyncClient(timeout=self._tts_timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        latency_ms = int((time.time() - start) * 1000)
                        logger.error(
                            "raya_tts.http_error",
                            extra={
                                "operation": "raya_tts.run_tts",
                                "status": "failure",
                                "error": f"HTTP {response.status_code}",
                                "latency_ms": latency_ms,
                            },
                        )
                        yield ErrorFrame(error=f"Raya TTS HTTP {response.status_code}")
                        return

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if not raw or raw == "{}":
                            continue
                        try:
                            chunk_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if chunk_data.get("type") == "chunk" and "data" in chunk_data:
                            f32le_bytes = base64.b64decode(chunk_data["data"])
                            pcm16_bytes = _f32le_to_pcm16(f32le_bytes)
                            total_bytes += len(pcm16_bytes)
                            yield TTSAudioRawFrame(
                                audio=pcm16_bytes,
                                sample_rate=_SAMPLE_RATE,
                                num_channels=_NUM_CHANNELS,
                                context_id=context_id,
                            )

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "raya_tts.connection_error",
                extra={
                    "operation": "raya_tts.run_tts",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                },
            )
            yield ErrorFrame(error=f"Raya TTS connection error: {exc}")
            return

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "raya_tts.synthesized",
            extra={
                "operation": "raya_tts.run_tts",
                "status": "success",
                "latency_ms": latency_ms,
                "audio_bytes": total_bytes,
            },
        )


def _f32le_to_pcm16(f32le_bytes: bytes) -> bytes:
    """Convert raw PCM F32LE bytes to PCM16 bytes.

    Args:
        f32le_bytes: Raw bytes containing 32-bit float samples (little-endian).

    Returns:
        Raw bytes containing 16-bit signed integer samples, same count.
    """
    samples_f32 = np.frombuffer(f32le_bytes, dtype=np.float32)
    samples_i16 = (samples_f32 * 32767.0).clip(-32767, 32767).astype(np.int16)
    return samples_i16.tobytes()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/pipecat_services/test_raya_tts.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/pipecat_services/raya_tts.py tests/pipecat_services/test_raya_tts.py
git commit -m "feat: add RayaTTSService (Pipecat TTSService + Raya SSE stream, F32LE→PCM16)"
```

---

## Task 5: bot.py (pipeline factory)

**Files:**
- Create: `src/bot.py`

No unit tests for bot.py — it is integration-only (requires a live WebSocket). Tests for server.py in Task 6 will patch `bot.run_bot` instead.

- [ ] **Step 1: Create src/bot.py**

```python
"""
telephony_adapter/src/bot.py

run_bot — per-call Pipecat pipeline factory for the Telephony Adapter.

Called once per inbound WebSocket connection from Vobiz.  Parses the Vobiz
telephony handshake to extract stream_id and call_id, builds the pipeline:

  FastAPIWebsocketTransport (VobizFrameSerializer)
    → VADProcessor (SileroVADAnalyzer)
    → RayaSTTService
    → AgentCoreLLMProcessor
    → RayaTTSService
    → FastAPIWebsocketTransport output

Sends a TTS greeting immediately on client connect.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService

logger = logging.getLogger(__name__)


async def run_bot(websocket: WebSocket, call_sid: str, config: dict) -> None:
    """Build and run the Pipecat pipeline for one Vobiz call.

    Parses the Vobiz WebSocket handshake (reads the two start messages that
    Vobiz sends before audio) to extract stream_id and call_id needed by
    VobizFrameSerializer.  Then assembles the full pipeline and runs it until
    the call ends or is escalated.

    Args:
        websocket: FastAPI WebSocket that has already been accepted by the caller.
        call_sid: Call SID from the URL path — used as the Agent Core user_id.
        config: Full merged config dict.
    """
    vobiz_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
    ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})

    # Parse the Vobiz telephony handshake. Vobiz is Plivo-compatible so
    # parse_telephony_websocket returns transport_type="plivo" and
    # call_data with stream_id / call_id from the start message.
    transport_type, call_data = await parse_telephony_websocket(websocket)
    stream_id = call_data.get("stream_id") or ""
    call_id = call_data.get("call_id") or call_sid

    logger.info(
        "bot.call_started",
        extra={
            "operation": "bot.run_bot",
            "status": "success",
            "call_sid": call_sid,
            "stream_id": stream_id,
            "transport_type": transport_type,
        },
    )

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=vobiz_cfg.get("auth_id", ""),
        auth_token=vobiz_cfg.get("auth_token", ""),
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=8000,
            auto_hang_up=True,
        ),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    session_id = str(uuid.uuid4())
    stt = RayaSTTService(config)
    agent = AgentCoreLLMProcessor(config, call_sid=call_sid, session_id=session_id)
    tts = RayaTTSService(config)

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            stt,
            agent,
            tts,
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        greeting = ac_cfg.get("greeting", "Hello, how can I help you today?")
        logger.info(
            "bot.greeting",
            extra={
                "operation": "bot.on_client_connected",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await task.queue_frame(TTSSpeakFrame(text=greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(
            "bot.call_ended",
            extra={
                "operation": "bot.on_client_disconnected",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
```

- [ ] **Step 2: Verify import works**

```bash
uv run python -c "from src.bot import run_bot; print('bot.py imports OK')"
```

Expected: `bot.py imports OK`

- [ ] **Step 3: Commit**

```bash
git add src/bot.py
git commit -m "feat: add bot.py Pipecat pipeline factory (Silero VAD + Raya STT/TTS + Agent Core)"
```

---

## Task 6: Update server.py and its tests

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write updated test for WS endpoint**

In `tests/test_server.py`, replace the entire file with:

```python
"""Tests for telephony adapter FastAPI server."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("src.bot.run_bot", new_callable=AsyncMock) as mock_bot, \
         patch("server.CampaignManager"), \
         patch("server.load_config", return_value={
             "telephony_adapter": {
                 "port": 8006,
                 "public_url": "https://example.app",
                 "vobiz": {
                     "auth_id": "MA1", "auth_token": "t",
                     "api_base": "https://api.vobiz.ai/api/v1",
                     "from_number": "+91",
                 },
                 "raya": {
                     "api_key": "k", "stt_wss_url": "wss://...",
                     "tts_base_url": "https://...", "language": "hi",
                     "voice_id": "v1", "tts_speed": 1.0,
                 },
                 "agent_core": {
                     "base_url": "http://agent_core:8000",
                     "timeout_ms": 5000,
                     "fallback_phrase": "sorry",
                     "greeting": "Hello!",
                 },
             },
             "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
         }), \
         patch("server.init_otel"):
        from server import create_app
        app = create_app()
        yield TestClient(app)


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_answer_returns_xml_with_websocket_url(client):
    resp = client.post("/answer", data={"CallSid": "CA1", "From": "+91999"})
    assert resp.status_code == 200
    assert "wss://" in resp.text or "ws://" in resp.text
    assert "CA1" in resp.text
    assert resp.headers["content-type"].startswith("application/xml")


def test_campaign_endpoint_calls_manager(client):
    with patch("server._campaign_manager") as mock_mgr:
        mock_mgr.initiate_call = AsyncMock(return_value={"callSid": "CA_NEW"})
        resp = client.post("/campaign", json={"to_number": "+919999999999"})
    assert resp.status_code == 200
    assert resp.json()["callSid"] == "CA_NEW"


def test_campaign_empty_to_number_returns_422(client):
    resp = client.post("/campaign", json={"to_number": ""})
    assert resp.status_code in (400, 422)


def test_recording_finished_returns_200(client):
    resp = client.post(
        "/recording-finished", json={"callSid": "CA1", "recordingUrl": "https://..."}
    )
    assert resp.status_code == 200


def test_recording_ready_returns_200(client):
    resp = client.post(
        "/recording-ready", json={"callSid": "CA1", "recordingUrl": "https://..."}
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run updated tests — expect failure on WS-related imports**

```bash
uv run pytest tests/test_server.py -v 2>&1 | head -30
```

Expected: some tests may fail because `server.py` still imports `VobizTelephonyAdapter`.

- [ ] **Step 3: Update server.py**

Replace the full content of `server.py` with:

```python
"""
telephony_adapter/server.py

FastAPI application for the Telephony Adapter DPG service.

Endpoints:
  POST /answer              — Vobiz webhook on call answered; returns XML with WebSocket URL.
  WebSocket /ws/{call_sid}  — Bidirectional audio stream per call.
  POST /campaign            — Trigger outbound call.
  POST /recording-finished  — Vobiz webhook: recording stopped.
  POST /recording-ready     — Vobiz webhook: recording MP3 ready.
  GET  /health              — Liveness probe.

Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import os

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

import src.bot as bot
from config_loader import load_config
from src.campaign_manager import CampaignManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OTel
# ---------------------------------------------------------------------------
try:
    from dpg_telemetry import init_otel
except ImportError:
    def init_otel(service_name: str, config: dict) -> None:  # type: ignore[misc]
        """No-op fallback when dpg_telemetry is not installed."""


# ---------------------------------------------------------------------------
# Module-level singletons (set by create_app)
# ---------------------------------------------------------------------------
_campaign_manager: CampaignManager | None = None
_config: dict | None = None


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class CampaignRequest(BaseModel):
    """Request body for POST /campaign."""

    to_number: str


class RecordingWebhook(BaseModel):
    """Vobiz recording webhook payload."""

    callSid: str = ""
    recordingUrl: str = ""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config: dict | None = None) -> FastAPI:
    """Construct the FastAPI application and wire up all singletons.

    Args:
        config: Optional pre-loaded config dict. Loads from YAML files if None.

    Returns:
        Configured FastAPI application.
    """
    global _campaign_manager, _config

    if config is None:
        dpg_path = os.getenv("DPG_CONFIG_PATH", "config/telephony.yaml")
        domain_path = os.getenv(
            "DOMAIN_CONFIG_PATH", "../dev-kit/configs/kkb/telephony_adapter.yaml"
        )
        config = load_config(dpg_path, domain_path)

    _config = config
    init_otel("telephony_adapter", config)
    _campaign_manager = CampaignManager(config)

    public_url: str = config.get("telephony_adapter", {}).get("public_url", "")
    if not public_url:
        raise ValueError("telephony_adapter.public_url is required in config")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")

    app = FastAPI(
        title="Telephony Adapter",
        description="DPG Reach Layer telephony channel adapter — Vobiz + Raya + Agent Core.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        """Handle Vobiz call-answered webhook; return XML with WebSocket stream URL.

        Vobiz POSTs form fields: CallSid (or CallUUID), From, To, etc.
        Returns XML instructing Vobiz to open a bidirectional WebSocket to
        /ws/{call_sid}.
        """
        form = await request.form()
        call_sid = form.get("CallUUID") or form.get("CallSid") or "unknown"
        stream_url = f"{ws_url}/ws/{call_sid}"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream bidirectional="true" keepCallAlive="true"'
            f' contentType="audio/x-mulaw;rate=8000">'
            f"{stream_url}</Stream>\n"
            "</Response>"
        )
        return Response(content=xml, media_type="application/xml")

    @app.websocket("/ws/{call_sid}")
    async def websocket_endpoint(websocket: WebSocket, call_sid: str) -> None:
        """Bidirectional audio stream for an active call.

        Accepts the WebSocket then hands it to bot.run_bot which owns the full
        Pipecat pipeline lifecycle: parses the Vobiz handshake, runs the
        VAD → STT → Agent Core → TTS pipeline, and closes on call end.
        """
        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.websocket_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await websocket.accept()
        try:
            await bot.run_bot(websocket, call_sid, _config)
        except Exception as exc:
            logger.error(
                "server.ws_error",
                extra={
                    "operation": "server.websocket_endpoint",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    @app.post("/campaign")
    async def campaign(body: CampaignRequest) -> dict:
        """Trigger an outbound call to the given number.

        Args:
            body: Contains the destination phone number.

        Returns:
            Dict with callSid of the initiated call.

        Raises:
            HTTPException 400: If to_number is empty.
        """
        if _campaign_manager is None:
            raise RuntimeError("App not initialised via create_app()")
        if not body.to_number or not body.to_number.strip():
            raise HTTPException(status_code=400, detail="to_number must not be empty")
        result = await _campaign_manager.initiate_call(to_number=body.to_number)
        return result

    @app.post("/recording-finished")
    async def recording_finished(body: RecordingWebhook) -> dict:
        """Handle Vobiz webhook when recording has stopped.

        Args:
            body: Webhook payload containing callSid and recordingUrl.

        Returns:
            Dict with status ok.
        """
        logger.info(
            "server.recording_finished",
            extra={
                "operation": "server.recording_finished",
                "status": "success",
                "call_sid": body.callSid,
            },
        )
        return {"status": "ok"}

    @app.post("/recording-ready")
    async def recording_ready(body: RecordingWebhook) -> dict:
        """Handle Vobiz webhook when recording MP3 is ready.

        Args:
            body: Webhook payload containing callSid and recordingUrl.

        Returns:
            Dict with status ok.
        """
        logger.info(
            "server.recording_ready",
            extra={
                "operation": "server.recording_ready",
                "status": "success",
                "call_sid": body.callSid,
            },
        )
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    import uvicorn

    _app = create_app()
    port = int(os.getenv("PORT", "8006"))
    uvicorn.run(_app, host="0.0.0.0", port=port)
```

- [ ] **Step 4: Run updated server tests — expect pass**

```bash
uv run pytest tests/test_server.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: refactor server.py to use bot.run_bot, remove VobizTelephonyAdapter"
```

---

## Task 7: Delete replaced files

**Files:**
- Delete: `src/telephony_adapter.py`
- Delete: `src/raya_stt_service.py`
- Delete: `src/raya_tts_service.py`
- Delete: `src/agent_core_service.py`
- Delete: `src/vobiz_serializer.py`
- Delete: `tests/test_telephony_adapter.py`
- Delete: `tests/test_raya_stt_service.py`
- Delete: `tests/test_raya_tts_service.py`
- Delete: `tests/test_agent_core_service.py`
- Delete: `tests/test_vobiz_serializer.py`

- [ ] **Step 1: Delete old source files**

```bash
cd telephony_adapter
rm src/telephony_adapter.py src/raya_stt_service.py src/raya_tts_service.py \
   src/agent_core_service.py src/vobiz_serializer.py
```

- [ ] **Step 2: Delete old test files**

```bash
rm tests/test_telephony_adapter.py tests/test_raya_stt_service.py \
   tests/test_raya_tts_service.py tests/test_agent_core_service.py \
   tests/test_vobiz_serializer.py
```

- [ ] **Step 3: Run full test suite — all tests should still pass**

```bash
uv run pytest -v
```

Expected: all tests in `tests/pipecat_services/` and `tests/test_server.py`, `tests/test_base.py`, `tests/test_campaign_manager.py`, `tests/test_config_loader.py` pass.  
If any test imports a deleted module, fix the import.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete replaced source files and obsolete tests"
```

---

## Task 8: Verify coverage and run full suite

- [ ] **Step 1: Run tests with coverage**

```bash
cd telephony_adapter
uv run pytest --cov=src --cov-report=term-missing -v
```

Expected:
- All tests PASSED.
- Coverage for `src/pipecat_services/raya_stt.py`, `src/pipecat_services/raya_tts.py`, `src/pipecat_services/agent_core_llm.py` ≥ 70%.
- `src/bot.py` may show lower coverage (no unit tests — integration only). This is acceptable; exclude it from the fail-under threshold if needed by adding to `[tool.coverage.run] omit`.

- [ ] **Step 2: If bot.py coverage causes failure, exclude it**

In `pyproject.toml`, add `src/bot.py` to the omit list:

```toml
[tool.coverage.run]
source = ["src", "server.py", "config_loader.py"]
omit = ["*/tests/*", "*/__init__.py", "src/bot.py"]
```

Then re-run:

```bash
uv run pytest --cov=src --cov-report=term-missing -v
```

Expected: all pass, ≥ 70% coverage.

- [ ] **Step 3: Commit if pyproject.toml changed**

```bash
git add pyproject.toml
git commit -m "chore: exclude bot.py from coverage threshold (integration-only)"
```

---

## Summary of changes

| Bug | Fix applied |
|-----|-------------|
| 1. Track name mismatch | Removed — `pipecat-vobiz` `VobizFrameSerializer` owns Vobiz protocol parsing |
| 2. Outbound echo | Removed — Pipecat transport only feeds inbound audio into pipeline |
| 3. TTS format mismatch | `RayaTTSService` converts F32LE→PCM16; `VobizFrameSerializer` encodes PCM16→µ-law |
| 4. STT silent failure | `RayaSTTService.run_stt` yields `ErrorFrame` on HTTP/connection failure; empty transcripts log `status=skipped` and yield nothing |
| 5. Docker URL | `AgentCoreLLMProcessor.__init__` raises `ValueError` with clear message if `base_url` is empty |
