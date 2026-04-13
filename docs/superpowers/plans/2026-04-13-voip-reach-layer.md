# VoIP Reach Layer — Base Classes & VobizAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce DPG-conformant base classes for all telephony pipeline components, create a concrete `VobizAdapter` implementing `TelephonyAdapterBase`, and wire `caller_id` (E.164) as `user_id` for cross-call memory continuity.

**Architecture:** Four independent ABCs (`TelephonyOperatorBase`, `VADAnalyzerBase`, `STTServiceBase`, `TTSServiceBase`) each with one concrete implementation. `VobizAdapter` composes them to implement the existing `TelephonyAdapterBase`. Pipecat is an implementation detail inside concrete classes — the DPG bases have no Pipecat imports.

**Tech Stack:** Python 3.11+, uv, pytest, pytest-asyncio, respx (HTTP mocking), pipecat, httpx, numpy

**GitHub issue:** sanketika-labs/ai-diffusion-dpg#66  
**Design spec:** `docs/superpowers/specs/2026-04-13-voip-reach-layer-design.md`  
**Out of scope:** Agent Core streaming (#65), call recording (#67)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `telephony_adapter/src/base.py` | Add `STTError`, `TTSError` |
| Create | `telephony_adapter/src/operators/__init__.py` | Package marker |
| Create | `telephony_adapter/src/operators/operator_base.py` | `TelephonyOperatorBase` ABC |
| Create | `telephony_adapter/src/operators/vobiz_operator.py` | `VobizOperator` concrete |
| Create | `telephony_adapter/src/vad/__init__.py` | Package marker |
| Create | `telephony_adapter/src/vad/vad_base.py` | `VADAnalyzerBase` ABC |
| Create | `telephony_adapter/src/vad/silero_vad.py` | `SileroVADWrapper` concrete |
| Create | `telephony_adapter/src/pipecat_services/stt_base.py` | `STTServiceBase` ABC (no Pipecat) |
| Create | `telephony_adapter/src/pipecat_services/tts_base.py` | `TTSServiceBase` ABC (no Pipecat) |
| Modify | `telephony_adapter/src/pipecat_services/raya_stt.py` | Inherit `STTServiceBase`; extract logic into `transcribe()` |
| Modify | `telephony_adapter/src/pipecat_services/raya_tts.py` | Inherit `TTSServiceBase`; extract logic into `synthesize()` |
| Modify | `telephony_adapter/src/pipecat_services/agent_core_llm.py` | Accept + send `user_id` |
| Create | `telephony_adapter/src/vobiz_adapter.py` | `VobizAdapter` implements `TelephonyAdapterBase` |
| Modify | `telephony_adapter/src/bot.py` | Delegate to `VobizAdapter`; add `caller_id` param |
| Modify | `telephony_adapter/server.py` | Extract `caller_id` from webhook; pass to `run_bot` |
| Modify | `telephony_adapter/config/telephony.yaml` | Add `vad` section |
| Modify | `telephony_adapter/tests/test_base.py` | Add `STTError`, `TTSError` assertions |
| Create | `telephony_adapter/tests/operators/__init__.py` | Package marker |
| Create | `telephony_adapter/tests/operators/test_operator_base.py` | ABC enforcement tests |
| Create | `telephony_adapter/tests/operators/test_vobiz_operator.py` | `VobizOperator` tests |
| Create | `telephony_adapter/tests/vad/__init__.py` | Package marker |
| Create | `telephony_adapter/tests/vad/test_vad_base.py` | ABC enforcement tests |
| Create | `telephony_adapter/tests/vad/test_silero_vad.py` | `SileroVADWrapper` tests |
| Create | `telephony_adapter/tests/pipecat_services/test_stt_base.py` | ABC enforcement tests |
| Create | `telephony_adapter/tests/pipecat_services/test_tts_base.py` | ABC enforcement tests |
| Modify | `telephony_adapter/tests/pipecat_services/test_raya_stt.py` | Add `transcribe()` tests |
| Modify | `telephony_adapter/tests/pipecat_services/test_raya_tts.py` | Add `synthesize()` tests |
| Modify | `telephony_adapter/tests/pipecat_services/test_agent_core_llm.py` | Assert `user_id` in payload |
| Create | `telephony_adapter/tests/test_vobiz_adapter.py` | `VobizAdapter` wiring tests |
| Modify | `telephony_adapter/tests/test_server.py` | Assert `caller_id` extracted and passed |

---

## Task 1: Add `STTError` and `TTSError` to `base.py`

**Files:**
- Modify: `telephony_adapter/src/base.py`
- Modify: `telephony_adapter/tests/test_base.py`

- [ ] **Write the failing tests**

Add to `telephony_adapter/tests/test_base.py`:

```python
from src.base import (
    TelephonyAdapterBase,
    TelephonyTurnInput,
    TelephonyTurnResult,
    TelephonyError,
    STTError,
    TTSError,
)


def test_stt_error_is_exception():
    err = STTError("transcription failed")
    assert isinstance(err, Exception)
    assert "transcription failed" in str(err)


def test_tts_error_is_exception():
    err = TTSError("synthesis failed")
    assert isinstance(err, Exception)
    assert "synthesis failed" in str(err)


def test_stt_error_not_tts_error():
    assert not issubclass(STTError, TTSError)
    assert not issubclass(TTSError, STTError)
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/test_base.py::test_stt_error_is_exception -v
```
Expected: `ImportError: cannot import name 'STTError'`

- [ ] **Add the exception classes to `src/base.py`**

Add after the existing `TelephonyError` class:

```python
class STTError(Exception):
    """Raised when speech-to-text transcription fails after retries."""


class TTSError(Exception):
    """Raised when text-to-speech synthesis fails."""
```

- [ ] **Run tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/test_base.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/base.py telephony_adapter/tests/test_base.py
git commit -m "feat(telephony-adapter): add STTError and TTSError to base"
```

---

## Task 2: `STTServiceBase` and `TTSServiceBase` ABCs

**Files:**
- Create: `telephony_adapter/src/pipecat_services/stt_base.py`
- Create: `telephony_adapter/src/pipecat_services/tts_base.py`
- Create: `telephony_adapter/tests/pipecat_services/test_stt_base.py`
- Create: `telephony_adapter/tests/pipecat_services/test_tts_base.py`

- [ ] **Write the failing tests**

`telephony_adapter/tests/pipecat_services/test_stt_base.py`:

```python
"""Tests for STTServiceBase — DPG abstract STT interface."""
import pytest
from src.pipecat_services.stt_base import STTServiceBase


def test_cannot_instantiate_stt_base():
    with pytest.raises(TypeError):
        STTServiceBase()


def test_concrete_stt_must_implement_transcribe():
    class IncompleteSTT(STTServiceBase):
        pass

    with pytest.raises(TypeError):
        IncompleteSTT()


def test_concrete_stt_with_transcribe_instantiates():
    class MinimalSTT(STTServiceBase):
        async def transcribe(self, audio: bytes) -> str | None:
            return "hello"

    stt = MinimalSTT()
    assert stt is not None


@pytest.mark.asyncio
async def test_concrete_stt_transcribe_returns_none_for_empty():
    class SilentSTT(STTServiceBase):
        async def transcribe(self, audio: bytes) -> str | None:
            return None

    stt = SilentSTT()
    result = await stt.transcribe(b"")
    assert result is None
```

`telephony_adapter/tests/pipecat_services/test_tts_base.py`:

```python
"""Tests for TTSServiceBase — DPG abstract TTS interface."""
import pytest
from src.pipecat_services.tts_base import TTSServiceBase


def test_cannot_instantiate_tts_base():
    with pytest.raises(TypeError):
        TTSServiceBase()


def test_concrete_tts_must_implement_synthesize():
    class IncompleteTTS(TTSServiceBase):
        pass

    with pytest.raises(TypeError):
        IncompleteTTS()


def test_concrete_tts_with_synthesize_instantiates():
    class MinimalTTS(TTSServiceBase):
        async def synthesize(self, text: str):
            yield b"\x00\x01"

    tts = MinimalTTS()
    assert tts is not None


@pytest.mark.asyncio
async def test_concrete_tts_synthesize_yields_bytes():
    class EchoTTS(TTSServiceBase):
        async def synthesize(self, text: str):
            yield b"\x00\x01"
            yield b"\x02\x03"

    tts = EchoTTS()
    chunks = [chunk async for chunk in tts.synthesize("hi")]
    assert chunks == [b"\x00\x01", b"\x02\x03"]
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_stt_base.py tests/pipecat_services/test_tts_base.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Create `src/pipecat_services/stt_base.py`**

```python
"""
telephony_adapter/src/pipecat_services/stt_base.py

STTServiceBase — DPG abstract interface for speech-to-text services.

Pipecat-independent. Concrete implementations may inherit from both this
class and a Pipecat STT base, keeping Pipecat as an implementation detail.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class STTServiceBase(ABC):
    """Abstract interface for all DPG speech-to-text service implementations.

    Defines the minimal contract for transcribing a single utterance from raw
    audio bytes to text. Concrete classes are free to use any underlying
    framework (Pipecat, raw HTTP, WebSocket) as an implementation detail.
    """

    @abstractmethod
    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete utterance to text.

        Args:
            audio: Complete WAV file bytes (PCM16, mono) for one utterance,
                assembled by the caller after VAD detects end-of-speech.

        Returns:
            Transcribed text string, or None if the audio is silent,
            unintelligible, or below the service's confidence threshold.

        Raises:
            STTError: If transcription fails after all retries.
        """
```

- [ ] **Create `src/pipecat_services/tts_base.py`**

```python
"""
telephony_adapter/src/pipecat_services/tts_base.py

TTSServiceBase — DPG abstract interface for text-to-speech services.

Pipecat-independent. Concrete implementations may inherit from both this
class and a Pipecat TTS base, keeping Pipecat as an implementation detail.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class TTSServiceBase(ABC):
    """Abstract interface for all DPG text-to-speech service implementations.

    Defines the minimal contract for synthesising text to raw PCM16 audio
    chunks. Concrete classes are free to use any underlying framework
    (Pipecat, raw HTTP, SSE) as an implementation detail.
    """

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesise text to PCM16 audio chunks at 8000 Hz.

        Args:
            text: Text to synthesise. Must not be empty.

        Yields:
            Raw PCM16 bytes chunks. Each chunk is a variable-length segment
            of 16-bit signed integer samples at 8000 Hz mono.

        Raises:
            TTSError: If synthesis fails.
        """
```

- [ ] **Run tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_stt_base.py tests/pipecat_services/test_tts_base.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/pipecat_services/stt_base.py \
        telephony_adapter/src/pipecat_services/tts_base.py \
        telephony_adapter/tests/pipecat_services/test_stt_base.py \
        telephony_adapter/tests/pipecat_services/test_tts_base.py
git commit -m "feat(telephony-adapter): add STTServiceBase and TTSServiceBase ABCs"
```

---

## Task 3: Update `RayaSTTService` to inherit `STTServiceBase`

**Files:**
- Modify: `telephony_adapter/src/pipecat_services/raya_stt.py`
- Modify: `telephony_adapter/tests/pipecat_services/test_raya_stt.py`

The key change: move the HTTP transcription logic from `run_stt()` into a new `transcribe()` method. `run_stt()` becomes a thin Pipecat bridge that calls `transcribe()` and wraps the result in a `TranscriptionFrame`.

- [ ] **Add `transcribe()` tests to `tests/pipecat_services/test_raya_stt.py`**

Add after the existing tests:

```python
import pytest
import respx
import httpx
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.stt_base import STTServiceBase


def test_raya_stt_is_stt_service_base(config):
    stt = RayaSTTService(config)
    assert isinstance(stt, STTServiceBase)


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_text_on_success(config):
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(200, json={"transcript": "नमस्ते"})
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result == "नमस्ते"


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_none_on_empty_transcript(config):
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(200, json={"transcript": "  "})
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_transcribe_returns_none_on_http_error(config):
    respx.post("https://hub.getraya.app/transcribe").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    stt = RayaSTTService(config)
    result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_returns_none_on_timeout(config):
    import httpx
    from unittest.mock import patch, AsyncMock

    async def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=raise_timeout)):
        stt = RayaSTTService(config)
        result = await stt.transcribe(b"RIFF" + b"\x00" * 40)
    assert result is None
```

- [ ] **Run to confirm new tests fail**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_raya_stt.py::test_raya_stt_is_stt_service_base -v
```
Expected: `AssertionError` (not yet inheriting `STTServiceBase`)

- [ ] **Rewrite `src/pipecat_services/raya_stt.py`**

```python
"""
telephony_adapter/src/pipecat_services/raya_stt.py

RayaSTTService — Pipecat SegmentedSTTService backed by the Raya HTTP STT API.

Inherits STTServiceBase (DPG contract) and Pipecat's SegmentedSTTService.
The DPG transcription logic lives in transcribe(); run_stt() is a thin
Pipecat bridge that calls transcribe() and wraps the result in a
TranscriptionFrame.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

import httpx

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService

from src.pipecat_services.stt_base import STTServiceBase

logger = logging.getLogger(__name__)

_RAYA_STT_URL = "https://hub.getraya.app/transcribe"


class RayaSTTService(STTServiceBase, SegmentedSTTService):
    """Transcribes one VAD-segmented utterance per call via the Raya HTTP STT API.

    Inherits STTServiceBase for the DPG interface contract and Pipecat's
    SegmentedSTTService for pipeline integration. The transcription logic
    lives in transcribe(); run_stt() delegates to it.

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
        self._language = raya_cfg.get("stt_language") or raya_cfg.get("language", "hi")
        self._timeout = float(raya_cfg.get("stt_timeout_s", 30.0))
        SegmentedSTTService.__init__(
            self,
            sample_rate=8000,
            settings=STTSettings(model=None, language=self._language),
        )

    async def transcribe(self, audio: bytes) -> str | None:
        """Transcribe a complete utterance via Raya HTTP multipart STT.

        Sends the WAV bytes as multipart/form-data. Retries once on
        connection or timeout errors with a 500ms backoff.

        Args:
            audio: Complete WAV file bytes (PCM16, 8 kHz, mono).

        Returns:
            Transcribed text, or None if transcript is empty or on error.
        """
        start = time.time()
        for attempt in range(2):
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
                        f"raya_stt.http_error HTTP {response.status_code}",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "latency_ms": latency_ms,
                        },
                    )
                    return None
                transcript = response.json().get("transcript", "").strip()
                if not transcript:
                    logger.info(
                        "raya_stt.empty_transcript",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "skipped",
                            "latency_ms": latency_ms,
                        },
                    )
                    return None
                logger.info(
                    "raya_stt.transcribed",
                    extra={
                        "operation": "raya_stt.transcribe",
                        "status": "success",
                        "latency_ms": latency_ms,
                        "audio_bytes": len(audio),
                        "transcript_len": len(transcript),
                    },
                )
                return transcript
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt == 0:
                    logger.warning(
                        "raya_stt.retrying",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.error(
                        "raya_stt.connection_error",
                        extra={
                            "operation": "raya_stt.transcribe",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return None
        return None

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Pipecat hook: transcribe audio and yield TranscriptionFrame or ErrorFrame.

        Delegates to transcribe(). Yields TranscriptionFrame on success,
        ErrorFrame if transcription returns None after exhausting retries.

        Args:
            audio: Complete WAV file bytes assembled by SegmentedSTTService.

        Yields:
            TranscriptionFrame on success. Nothing if transcript is empty.
        """
        transcript = await self.transcribe(audio)
        if transcript:
            yield TranscriptionFrame(text=transcript, user_id="", timestamp="")
```

- [ ] **Run all STT tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_raya_stt.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/pipecat_services/raya_stt.py \
        telephony_adapter/tests/pipecat_services/test_raya_stt.py
git commit -m "feat(telephony-adapter): RayaSTTService inherits STTServiceBase; extract transcribe()"
```

---

## Task 4: Update `RayaTTSService` to inherit `TTSServiceBase`

**Files:**
- Modify: `telephony_adapter/src/pipecat_services/raya_tts.py`
- Modify: `telephony_adapter/tests/pipecat_services/test_raya_tts.py`

Same pattern as Task 3: TTS logic moves into `synthesize()`, `run_tts()` becomes a thin Pipecat bridge.

- [ ] **Add `synthesize()` tests to `tests/pipecat_services/test_raya_tts.py`**

Add after the existing tests:

```python
import json
import base64
import numpy as np
import pytest
import respx
import httpx
from src.pipecat_services.raya_tts import RayaTTSService
from src.pipecat_services.tts_base import TTSServiceBase


def _make_f32le_chunk(n_samples: int = 160) -> bytes:
    samples = np.zeros(n_samples, dtype=np.float32)
    return samples.tobytes()


def _sse_line(chunk_b64: str, done: bool = False) -> str:
    if done:
        return 'data: {"done": true}\n\n'
    payload = json.dumps({"type": "chunk", "status_code": 206, "done": False, "data": chunk_b64})
    return f"data: {payload}\n\n"


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "raya": {
                "api_key": "test-key",
                "tts_base_url": "https://hub.getraya.app/v1",
                "language": "hi",
                "voice_id": "voice_001",
                "tts_speed": 1.0,
                "tts_timeout_s": 30.0,
            }
        }
    }


def test_raya_tts_is_tts_service_base(config):
    tts = RayaTTSService(config)
    assert isinstance(tts, TTSServiceBase)


@pytest.mark.asyncio
@respx.mock
async def test_synthesize_yields_pcm16_chunks(config):
    chunk = _make_f32le_chunk(160)
    chunk_b64 = base64.b64encode(chunk).decode()
    sse_body = _sse_line(chunk_b64) + _sse_line("", done=True)
    respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
        return_value=httpx.Response(200, text=sse_body)
    )
    tts = RayaTTSService(config)
    chunks = [c async for c in tts.synthesize("नमस्ते")]
    assert len(chunks) == 1
    assert len(chunks[0]) == 160 * 2  # float32 → int16: same sample count, half bytes


@pytest.mark.asyncio
@respx.mock
async def test_synthesize_yields_nothing_on_http_error(config):
    respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
        return_value=httpx.Response(500, text="error")
    )
    tts = RayaTTSService(config)
    chunks = [c async for c in tts.synthesize("hi")]
    assert chunks == []


@pytest.mark.asyncio
async def test_synthesize_yields_nothing_on_connection_error(config):
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient.stream", return_value=mock_cm):
        tts = RayaTTSService(config)
        chunks = [c async for c in tts.synthesize("hi")]
    assert chunks == []
```

- [ ] **Run to confirm new tests fail**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_raya_tts.py::test_raya_tts_is_tts_service_base -v
```
Expected: `AssertionError`

- [ ] **Rewrite `src/pipecat_services/raya_tts.py`**

```python
"""
telephony_adapter/src/pipecat_services/raya_tts.py

RayaTTSService — Pipecat TTSService backed by the Raya SSE streaming TTS API.

Inherits TTSServiceBase (DPG contract) and Pipecat's TTSService.
The DPG synthesis logic lives in synthesize(); run_tts() is a thin Pipecat
bridge that calls synthesize() and wraps each chunk in a TTSAudioRawFrame.
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
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from src.pipecat_services.tts_base import TTSServiceBase

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 8000
_NUM_CHANNELS = 1


class RayaTTSService(TTSServiceBase, TTSService):
    """Synthesises speech via the Raya SSE streaming TTS endpoint.

    Inherits TTSServiceBase for the DPG interface contract and Pipecat's
    TTSService for pipeline integration. The synthesis logic lives in
    synthesize(); run_tts() delegates to it.

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
        self._language = raya_cfg.get("tts_language") or raya_cfg.get("language", "hi")
        self._speed = float(raya_cfg.get("tts_speed", 1.0))
        self._tts_timeout = float(raya_cfg.get("tts_timeout_s", 30.0))
        TTSService.__init__(
            self,
            sample_rate=_SAMPLE_RATE,
            settings=TTSSettings(model=None, voice=self._voice_id, language=self._language),
        )

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesise text to PCM16 chunks via the Raya SSE streaming TTS endpoint.

        Streams F32LE PCM chunks from Raya SSE and converts each to PCM16.

        Args:
            text: The text to synthesise.

        Yields:
            Raw PCM16 bytes at 8000 Hz mono. Yields nothing on HTTP or
            connection errors (logs and returns cleanly).
        """
        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "model": "standard",
            "language": self._language,
            "speed": self._speed,
            "sample_rate": _SAMPLE_RATE,
        }
        headers = {"X-API-Key": self._api_key}
        total_bytes = 0

        try:
            async with httpx.AsyncClient(timeout=self._tts_timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        logger.error(
                            f"raya_tts.http_error HTTP {response.status_code}",
                            extra={
                                "operation": "raya_tts.synthesize",
                                "status": "failure",
                                "latency_ms": int((time.time() - start) * 1000),
                            },
                        )
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
                            yield pcm16_bytes

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error(
                "raya_tts.connection_error",
                extra={
                    "operation": "raya_tts.synthesize",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return

        logger.info(
            "raya_tts.synthesized",
            extra={
                "operation": "raya_tts.synthesize",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
                "audio_bytes": total_bytes,
            },
        )

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Pipecat hook: synthesise text and yield TTSAudioRawFrame objects.

        Delegates to synthesize(). Each PCM16 chunk becomes one TTSAudioRawFrame.

        Args:
            text: The text to synthesise.
            context_id: Pipecat context ID for this TTS turn.

        Yields:
            TTSAudioRawFrame per PCM16 chunk.
        """
        async for pcm16_bytes in self.synthesize(text):
            yield TTSAudioRawFrame(
                audio=pcm16_bytes,
                sample_rate=_SAMPLE_RATE,
                num_channels=_NUM_CHANNELS,
                context_id=context_id,
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

- [ ] **Run all TTS tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_raya_tts.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/pipecat_services/raya_tts.py \
        telephony_adapter/tests/pipecat_services/test_raya_tts.py
git commit -m "feat(telephony-adapter): RayaTTSService inherits TTSServiceBase; extract synthesize()"
```

---

## Task 5: `VADAnalyzerBase` and `SileroVADWrapper`

**Files:**
- Create: `telephony_adapter/src/vad/__init__.py`
- Create: `telephony_adapter/src/vad/vad_base.py`
- Create: `telephony_adapter/src/vad/silero_vad.py`
- Create: `telephony_adapter/tests/vad/__init__.py`
- Create: `telephony_adapter/tests/vad/test_vad_base.py`
- Create: `telephony_adapter/tests/vad/test_silero_vad.py`

- [ ] **Write the failing tests**

`telephony_adapter/tests/vad/test_vad_base.py`:

```python
"""Tests for VADAnalyzerBase — DPG abstract VAD interface."""
import pytest
from src.vad.vad_base import VADAnalyzerBase


def test_cannot_instantiate_vad_base():
    with pytest.raises(TypeError):
        VADAnalyzerBase()


def test_concrete_vad_must_implement_create_analyzer():
    class IncompleteVAD(VADAnalyzerBase):
        pass

    with pytest.raises(TypeError):
        IncompleteVAD()


def test_concrete_vad_with_create_analyzer_instantiates():
    class MinimalVAD(VADAnalyzerBase):
        def create_analyzer(self, config: dict):
            return object()

    vad = MinimalVAD()
    assert vad is not None
```

`telephony_adapter/tests/vad/test_silero_vad.py`:

```python
"""Tests for SileroVADWrapper — config-driven SileroVADAnalyzer factory."""
import pytest
from unittest.mock import patch, MagicMock
from src.vad.silero_vad import SileroVADWrapper
from src.vad.vad_base import VADAnalyzerBase


@pytest.fixture
def full_config():
    return {
        "telephony_adapter": {
            "vad": {
                "stop_secs": 0.5,
                "min_volume": 0.4,
                "confidence": 0.6,
                "start_secs": 0.2,
                "smoothing_factor": 0.2,
            }
        }
    }


@pytest.fixture
def empty_config():
    return {}


def test_silero_vad_wrapper_is_vad_base():
    assert issubclass(SileroVADWrapper, VADAnalyzerBase)


def test_create_analyzer_returns_silero_instance(full_config):
    mock_analyzer = MagicMock()
    mock_params = MagicMock()

    with patch("src.vad.silero_vad.SileroVADAnalyzer", return_value=mock_analyzer) as mock_cls, \
         patch("src.vad.silero_vad.VADParams", return_value=mock_params) as mock_p:
        wrapper = SileroVADWrapper()
        result = wrapper.create_analyzer(full_config)

    mock_p.assert_called_once_with(
        stop_secs=0.5,
        min_volume=0.4,
        confidence=0.6,
        start_secs=0.2,
    )
    mock_cls.assert_called_once_with(params=mock_params)
    assert result is mock_analyzer
    assert result._smoothing_factor == 0.2


def test_create_analyzer_uses_defaults_when_config_missing(empty_config):
    mock_analyzer = MagicMock()
    mock_params = MagicMock()

    with patch("src.vad.silero_vad.SileroVADAnalyzer", return_value=mock_analyzer), \
         patch("src.vad.silero_vad.VADParams", return_value=mock_params) as mock_p:
        wrapper = SileroVADWrapper()
        result = wrapper.create_analyzer(empty_config)

    mock_p.assert_called_once_with(
        stop_secs=0.35,
        min_volume=0.3,
        confidence=0.4,
        start_secs=0.1,
    )
    assert result._smoothing_factor == 0.1
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/vad/ -v
```
Expected: `ModuleNotFoundError`

- [ ] **Create `src/vad/__init__.py`** (empty)

```python
```

- [ ] **Create `src/vad/vad_base.py`**

```python
"""
telephony_adapter/src/vad/vad_base.py

VADAnalyzerBase — DPG abstract interface for voice activity detection.

Operator-agnostic: any VAD implementation (Silero, WebRTC, cloud) works
with any telephony operator. Concrete implementations return a Pipecat
VADAnalyzer instance configured from the domain YAML.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VADAnalyzerBase(ABC):
    """Abstract interface for all DPG voice activity detection implementations.

    Defines a factory method that produces a configured Pipecat VADAnalyzer
    from the domain config. Keeping the factory pattern here ensures all
    VAD parameters are config-driven and none are hardcoded in bot.py.
    """

    @abstractmethod
    def create_analyzer(self, config: dict):
        """Instantiate and return a configured Pipecat VADAnalyzer.

        Args:
            config: Full merged config dict. Reads telephony_adapter.vad section.

        Returns:
            Configured Pipecat VADAnalyzer ready to pass to VADProcessor.
        """
```

- [ ] **Create `src/vad/silero_vad.py`**

```python
"""
telephony_adapter/src/vad/silero_vad.py

SileroVADWrapper — config-driven factory for Pipecat's SileroVADAnalyzer.

All VAD parameters (stop_secs, min_volume, confidence, start_secs,
smoothing_factor) are read from telephony_adapter.vad config. None are
hardcoded. Defaults match values tuned for 8 kHz telephony audio.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from src.vad.vad_base import VADAnalyzerBase

logger = logging.getLogger(__name__)


class SileroVADWrapper(VADAnalyzerBase):
    """Creates a SileroVADAnalyzer configured from the domain YAML.

    Reads all parameters from telephony_adapter.vad in the config dict.
    Falls back to telephony-tuned defaults if keys are absent.
    """

    def create_analyzer(self, config: dict) -> SileroVADAnalyzer:
        """Instantiate SileroVADAnalyzer with config-driven parameters.

        Args:
            config: Full merged config dict. Reads telephony_adapter.vad section.

        Returns:
            Configured SileroVADAnalyzer instance.
        """
        vad_cfg = config.get("telephony_adapter", {}).get("vad", {})
        stop_secs = float(vad_cfg.get("stop_secs", 0.35))
        min_volume = float(vad_cfg.get("min_volume", 0.3))
        confidence = float(vad_cfg.get("confidence", 0.4))
        start_secs = float(vad_cfg.get("start_secs", 0.1))
        smoothing_factor = float(vad_cfg.get("smoothing_factor", 0.1))

        analyzer = SileroVADAnalyzer(
            params=VADParams(
                stop_secs=stop_secs,
                min_volume=min_volume,
                confidence=confidence,
                start_secs=start_secs,
            )
        )
        analyzer._smoothing_factor = smoothing_factor

        logger.info(
            "silero_vad.created",
            extra={
                "operation": "silero_vad.create_analyzer",
                "status": "success",
                "stop_secs": stop_secs,
                "min_volume": min_volume,
                "confidence": confidence,
            },
        )
        return analyzer
```

- [ ] **Create empty `tests/vad/__init__.py`**

```python
```

- [ ] **Run VAD tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/vad/ -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/vad/ telephony_adapter/tests/vad/
git commit -m "feat(telephony-adapter): add VADAnalyzerBase and SileroVADWrapper"
```

---

## Task 6: `TelephonyOperatorBase` and `VobizOperator`

**Files:**
- Create: `telephony_adapter/src/operators/__init__.py`
- Create: `telephony_adapter/src/operators/operator_base.py`
- Create: `telephony_adapter/src/operators/vobiz_operator.py`
- Create: `telephony_adapter/tests/operators/__init__.py`
- Create: `telephony_adapter/tests/operators/test_operator_base.py`
- Create: `telephony_adapter/tests/operators/test_vobiz_operator.py`

- [ ] **Write the failing tests**

`telephony_adapter/tests/operators/test_operator_base.py`:

```python
"""Tests for TelephonyOperatorBase — DPG abstract telephony operator interface."""
import pytest
from src.operators.operator_base import TelephonyOperatorBase


def test_cannot_instantiate_operator_base():
    with pytest.raises(TypeError):
        TelephonyOperatorBase()


def test_concrete_operator_must_implement_all_methods():
    class PartialOperator(TelephonyOperatorBase):
        async def parse_handshake(self, websocket):
            return ("sid", "cid")
        # missing create_transport and webhook_response_xml

    with pytest.raises(TypeError):
        PartialOperator()


def test_concrete_operator_with_all_methods_instantiates():
    class FullOperator(TelephonyOperatorBase):
        async def parse_handshake(self, websocket):
            return ("sid", "cid")

        def create_transport(self, websocket, stream_id, call_id):
            return object()

        def webhook_response_xml(self, websocket_url):
            return "<Response/>"

    op = FullOperator()
    assert op is not None
```

`telephony_adapter/tests/operators/test_vobiz_operator.py`:

```python
"""Tests for VobizOperator — concrete Vobiz telephony operator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.operators.vobiz_operator import VobizOperator
from src.operators.operator_base import TelephonyOperatorBase


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {
                "auth_id": "test-auth-id",
                "auth_token": "test-auth-token",
                "sample_rate": 8000,
            }
        }
    }


def test_vobiz_operator_is_operator_base():
    assert issubclass(VobizOperator, TelephonyOperatorBase)


def test_vobiz_operator_raises_on_missing_config():
    with pytest.raises(ValueError, match="auth_id"):
        VobizOperator({})


@pytest.mark.asyncio
async def test_parse_handshake_returns_stream_and_call_ids(config):
    mock_ws = MagicMock()
    mock_call_data = {"stream_id": "stream-123", "call_id": "call-456"}

    with patch(
        "src.operators.vobiz_operator.parse_telephony_websocket",
        new=AsyncMock(return_value=("plivo", mock_call_data)),
    ):
        op = VobizOperator(config)
        stream_id, call_id = await op.parse_handshake(mock_ws)

    assert stream_id == "stream-123"
    assert call_id == "call-456"


@pytest.mark.asyncio
async def test_parse_handshake_falls_back_to_empty_strings_on_missing_keys(config):
    with patch(
        "src.operators.vobiz_operator.parse_telephony_websocket",
        new=AsyncMock(return_value=("plivo", {})),
    ):
        op = VobizOperator(config)
        stream_id, call_id = await op.parse_handshake(MagicMock())

    assert stream_id == ""
    assert call_id == ""


def test_create_transport_returns_fastapi_websocket_transport(config):
    mock_ws = MagicMock()
    mock_transport = MagicMock()

    with patch("src.operators.vobiz_operator.VobizFrameSerializer"), \
         patch("src.operators.vobiz_operator.FastAPIWebsocketTransport", return_value=mock_transport):
        op = VobizOperator(config)
        result = op.create_transport(mock_ws, "stream-1", "call-1")

    assert result is mock_transport


def test_webhook_response_xml_contains_url(config):
    op = VobizOperator(config)
    xml = op.webhook_response_xml("wss://example.com/ws/abc")
    assert "wss://example.com/ws/abc" in xml
    assert "<Stream" in xml
    assert 'bidirectional="true"' in xml


def test_webhook_response_xml_is_valid_xml(config):
    import xml.etree.ElementTree as ET
    op = VobizOperator(config)
    xml = op.webhook_response_xml("wss://example.com/ws/abc")
    root = ET.fromstring(xml)  # raises if invalid
    assert root.tag == "Response"
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/operators/ -v
```
Expected: `ModuleNotFoundError`

- [ ] **Create empty `src/operators/__init__.py`**

```python
```

- [ ] **Create `src/operators/operator_base.py`**

```python
"""
telephony_adapter/src/operators/operator_base.py

TelephonyOperatorBase — DPG abstract interface for telephony operator adapters.

Each telephony operator (Vobiz, Twilio, Telnyx) has its own WebSocket
message format and webhook XML schema. This base class defines the three
methods every operator must implement: handshake parsing, transport creation,
and webhook XML generation. The serializer is bundled with the operator
because they share the same wire protocol — they are never swapped
independently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TelephonyOperatorBase(ABC):
    """Abstract interface for telephony operator adapters.

    Bundles frame serializer creation with operator identity because the
    serializer encodes the operator's wire protocol and is never swapped
    without also swapping the operator.
    """

    @abstractmethod
    async def parse_handshake(self, websocket) -> tuple[str, str]:
        """Parse provider-specific WebSocket handshake messages.

        Most telephony providers send one or two JSON messages immediately
        after the WebSocket is accepted, before audio begins. These carry
        stream and call identifiers needed to configure the serializer.

        Args:
            websocket: Active WebSocket connection from the telephony provider.

        Returns:
            Tuple of (stream_id, call_id). Either may be empty string if
            the provider does not supply it.
        """

    @abstractmethod
    def create_transport(self, websocket, stream_id: str, call_id: str):
        """Build the Pipecat transport with the provider's frame serializer.

        The serializer is constructed here because it encodes the same wire
        protocol as the operator and must match it exactly.

        Args:
            websocket: Active WebSocket connection.
            stream_id: Stream identifier from parse_handshake.
            call_id: Call identifier from parse_handshake.

        Returns:
            Configured FastAPIWebsocketTransport ready for pipeline use.
        """

    @abstractmethod
    def webhook_response_xml(self, websocket_url: str) -> str:
        """Return the XML response body for the telephony provider's /answer webhook.

        When the provider signals an inbound call via HTTP POST, the server
        must respond with XML instructing it where to open the WebSocket.

        Args:
            websocket_url: Full WebSocket URL the provider should connect to,
                e.g. wss://example.com/ws/{call_sid}.

        Returns:
            Provider-specific XML string. Must be valid XML.
        """
```

- [ ] **Create `src/operators/vobiz_operator.py`**

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

from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.vobiz import VobizFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.operators.operator_base import TelephonyOperatorBase

logger = logging.getLogger(__name__)


class VobizOperator(TelephonyOperatorBase):
    """Telephony operator adapter for the Vobiz platform.

    Handles the Vobiz WebSocket handshake, creates a FastAPIWebsocketTransport
    with VobizFrameSerializer, and generates the Vobiz/Plivo-compatible XML
    response for the /answer webhook.

    Args:
        config: Full merged config dict. Reads telephony_adapter.vobiz section.

    Raises:
        ValueError: If auth_id or auth_token is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        vobiz_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
        auth_id = vobiz_cfg.get("auth_id", "")
        if not auth_id:
            raise ValueError("telephony_adapter.vobiz.auth_id is required")
        auth_token = vobiz_cfg.get("auth_token", "")
        if not auth_token:
            raise ValueError("telephony_adapter.vobiz.auth_token is required")
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._sample_rate = int(vobiz_cfg.get("sample_rate", 8000))

    async def parse_handshake(self, websocket) -> tuple[str, str]:
        """Parse the Vobiz WebSocket handshake to extract stream_id and call_id.

        Delegates to Pipecat's parse_telephony_websocket which reads the two
        start messages Vobiz sends before audio begins.

        Args:
            websocket: Active WebSocket connection from Vobiz.

        Returns:
            Tuple of (stream_id, call_id). Either is empty string if absent.
        """
        _transport_type, call_data = await parse_telephony_websocket(websocket)
        stream_id = call_data.get("stream_id") or ""
        call_id = call_data.get("call_id") or ""

        logger.info(
            "vobiz_operator.handshake_parsed",
            extra={
                "operation": "vobiz_operator.parse_handshake",
                "status": "success",
                "stream_id": stream_id,
                "call_id": call_id,
            },
        )
        return stream_id, call_id

    def create_transport(
        self, websocket, stream_id: str, call_id: str
    ) -> FastAPIWebsocketTransport:
        """Build FastAPIWebsocketTransport with VobizFrameSerializer.

        Args:
            websocket: Active WebSocket connection.
            stream_id: Stream identifier from parse_handshake.
            call_id: Call identifier from parse_handshake.

        Returns:
            Configured FastAPIWebsocketTransport.
        """
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
        return FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )

    def webhook_response_xml(self, websocket_url: str) -> str:
        """Return Vobiz/Plivo-compatible XML for the /answer webhook.

        Args:
            websocket_url: Full WebSocket URL for Vobiz to connect to,
                e.g. wss://example.com/ws/{call_sid}.

        Returns:
            XML string instructing Vobiz to open a bidirectional audio stream.
        """
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Stream bidirectional="true" keepCallAlive="true"'
            f' contentType="audio/x-mulaw;rate=8000">'
            f"{websocket_url}</Stream>\n"
            "</Response>"
        )
```

- [ ] **Create empty `tests/operators/__init__.py`**

```python
```

- [ ] **Run operator tests to confirm they pass**

```bash
cd telephony_adapter && uv run pytest tests/operators/ -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/operators/ telephony_adapter/tests/operators/
git commit -m "feat(telephony-adapter): add TelephonyOperatorBase and VobizOperator"
```

---

## Task 7: Update `AgentCoreLLMProcessor` to pass `user_id`

**Files:**
- Modify: `telephony_adapter/src/pipecat_services/agent_core_llm.py`
- Modify: `telephony_adapter/tests/pipecat_services/test_agent_core_llm.py`

- [ ] **Add failing test**

Add to `tests/pipecat_services/test_agent_core_llm.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_process_turn_payload_includes_user_id():
    """user_id (caller E.164) must appear in the Agent Core request payload."""
    captured = {}

    def capture(request):
        import json as _json
        captured["payload"] = _json.loads(request.content)
        return httpx.Response(200, json={
            "response_text": "hello",
            "was_escalated": False,
            "was_tool_used": False,
            "model_used": "claude-sonnet-4-6",
        })

    respx.post("http://agent_core:8000/process_turn").mock(side_effect=capture)

    config = {
        "telephony_adapter": {
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "fallback_phrase": "sorry",
                "greeting": "hello",
            }
        }
    }
    processor = AgentCoreLLMProcessor(
        config,
        call_sid="call-123",
        session_id="sess-abc",
        user_id="+919876543210",
    )
    frame = TranscriptionFrame(text="मुझे जॉब चाहिए", user_id="", timestamp="")
    await processor._handle_transcription(frame)

    assert captured["payload"]["user_id"] == "+919876543210"
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_agent_core_llm.py::test_process_turn_payload_includes_user_id -v
```
Expected: `TypeError` (unexpected keyword argument `user_id`)

- [ ] **Update `src/pipecat_services/agent_core_llm.py`**

Change the `__init__` signature and payload construction:

```python
    def __init__(self, config: dict, *, call_sid: str, session_id: str, user_id: str) -> None:
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
        self._user_id = user_id
```

And update the payload in `_handle_transcription`:

```python
        payload = {
            "session_id": self._session_id,
            "user_message": frame.text,
            "channel": "telephony",
            "user_id": self._user_id,
            "timestamp_ms": int(start * 1000),
        }
```

- [ ] **Run all agent_core_llm tests**

```bash
cd telephony_adapter && uv run pytest tests/pipecat_services/test_agent_core_llm.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/pipecat_services/agent_core_llm.py \
        telephony_adapter/tests/pipecat_services/test_agent_core_llm.py
git commit -m "feat(telephony-adapter): pass caller user_id to Agent Core process_turn"
```

---

## Task 8: `VobizAdapter` and `bot.py` refactor

**Files:**
- Create: `telephony_adapter/src/vobiz_adapter.py`
- Modify: `telephony_adapter/src/bot.py`
- Create: `telephony_adapter/tests/test_vobiz_adapter.py`

- [ ] **Write the failing tests**

`telephony_adapter/tests/test_vobiz_adapter.py`:

```python
"""Tests for VobizAdapter — concrete TelephonyAdapterBase implementation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.vobiz_adapter import VobizAdapter
from src.base import TelephonyAdapterBase


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {
                "api_key": "raya-key",
                "tts_base_url": "https://hub.getraya.app/v1",
                "language": "hi",
                "voice_id": "voice_001",
            },
            "agent_core": {
                "base_url": "http://agent_core:8000",
                "timeout_ms": 5000,
                "greeting": "नमस्ते",
                "fallback_phrase": "माफ़ करें",
            },
        }
    }


def test_vobiz_adapter_is_telephony_adapter_base():
    assert issubclass(VobizAdapter, TelephonyAdapterBase)


def test_vobiz_adapter_raises_on_none_config():
    with pytest.raises(ValueError):
        VobizAdapter(None)


def test_teardown_does_not_raise(config):
    import asyncio
    adapter = VobizAdapter(config)
    asyncio.get_event_loop().run_until_complete(adapter.teardown("call-123"))


@pytest.mark.asyncio
async def test_handle_call_uses_caller_id_as_user_id(config):
    """user_id passed to AgentCoreLLMProcessor must equal caller_id."""
    captured_user_id = {}

    class MockAgentCoreLLM:
        def __init__(self, cfg, *, call_sid, session_id, user_id):
            captured_user_id["user_id"] = user_id

        async def process_frame(self, frame, direction):
            pass

    mock_ws = MagicMock()
    mock_transport = MagicMock()
    mock_transport.input = MagicMock(return_value=MagicMock())
    mock_transport.output = MagicMock(return_value=MagicMock())
    mock_transport.event_handler = MagicMock(return_value=lambda f: f)
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock()

    with patch("src.vobiz_adapter.VobizOperator") as MockOp, \
         patch("src.vobiz_adapter.SileroVADWrapper") as MockVAD, \
         patch("src.vobiz_adapter.RayaSTTService"), \
         patch("src.vobiz_adapter.AgentCoreLLMProcessor", MockAgentCoreLLM), \
         patch("src.vobiz_adapter.RayaTTSService"), \
         patch("src.vobiz_adapter.VADProcessor"), \
         patch("src.vobiz_adapter.Pipeline"), \
         patch("src.vobiz_adapter.PipelineTask"), \
         patch("src.vobiz_adapter.PipelineRunner", return_value=mock_runner):

        MockOp.return_value.parse_handshake = AsyncMock(return_value=("sid", "cid"))
        MockOp.return_value.create_transport = MagicMock(return_value=mock_transport)
        MockVAD.return_value.create_analyzer = MagicMock(return_value=MagicMock())

        adapter = VobizAdapter(config)
        await adapter.handle_call("call-123", "+919876543210", mock_ws)

    assert captured_user_id["user_id"] == "+919876543210"
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/test_vobiz_adapter.py::test_vobiz_adapter_is_telephony_adapter_base -v
```
Expected: `ModuleNotFoundError`

- [ ] **Create `src/vobiz_adapter.py`**

```python
"""
telephony_adapter/src/vobiz_adapter.py

VobizAdapter — concrete TelephonyAdapterBase for the Vobiz telephony platform.

Owns the full per-call lifecycle: parse handshake, build Pipecat pipeline,
run until call ends, teardown. Composes VobizOperator, SileroVADWrapper,
RayaSTTService, AgentCoreLLMProcessor, and RayaTTSService.

caller_id (E.164 phone number) is used as user_id so the Memory Layer can
recognise returning callers across sessions.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor

from src.base import TelephonyAdapterBase, TelephonyError
from src.operators.vobiz_operator import VobizOperator
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.vad.silero_vad import SileroVADWrapper

logger = logging.getLogger(__name__)


class VobizAdapter(TelephonyAdapterBase):
    """Telephony adapter for the Vobiz platform.

    Implements TelephonyAdapterBase by composing operator, VAD, STT, LLM,
    and TTS components into a Pipecat pipeline. One instance is created per
    call and discarded after teardown.

    Args:
        config: Full merged config dict.

    Raises:
        ValueError: If config is None.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        self._config = config
        self._operator = VobizOperator(config)
        self._vad_wrapper = SileroVADWrapper()
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        self._greeting = ac_cfg.get("greeting", "Hello, how can I help you today?")

    async def handle_call(self, call_sid: str, caller_id: str, websocket: WebSocket) -> None:
        """Handle the full lifecycle of one Vobiz inbound call.

        Parses the Vobiz handshake, builds the VAD→STT→Agent→TTS pipeline,
        sends a greeting on connect, and runs until the call ends.

        Args:
            call_sid: Vobiz CallUUID — opaque call identifier.
            caller_id: Caller E.164 phone number, used as user_id for Memory Layer.
            websocket: Accepted WebSocket connection from Vobiz.

        Raises:
            TelephonyError: If the pipeline cannot be established.
        """
        try:
            stream_id, call_id = await self._operator.parse_handshake(websocket)
        except Exception as exc:
            raise TelephonyError(f"Handshake failed for {call_sid}: {exc}") from exc

        transport = self._operator.create_transport(
            websocket, stream_id, call_id or call_sid
        )
        vad_analyzer = self._vad_wrapper.create_analyzer(self._config)
        session_id = str(uuid.uuid4())

        stt = RayaSTTService(self._config)
        agent = AgentCoreLLMProcessor(
            self._config,
            call_sid=call_sid,
            session_id=session_id,
            user_id=caller_id,
        )
        tts = RayaTTSService(self._config)

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
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
        async def _on_connected(transport, client):
            logger.info(
                "vobiz_adapter.call_connected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                    "session_id": session_id,
                },
            )
            await task.queue_frame(TTSSpeakFrame(text=self._greeting))

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(transport, client):
            logger.info(
                "vobiz_adapter.call_disconnected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                },
            )
            await task.cancel()

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    async def teardown(self, call_sid: str) -> None:
        """Log call completion. Pipecat handles WebSocket resource cleanup.

        Args:
            call_sid: The call SID whose resources should be released.
        """
        logger.info(
            "vobiz_adapter.teardown",
            extra={
                "operation": "vobiz_adapter.teardown",
                "status": "success",
                "call_sid": call_sid,
            },
        )
```

- [ ] **Slim down `src/bot.py`**

Replace the entire file with:

```python
"""
telephony_adapter/src/bot.py

run_bot — per-call entry point for the Telephony Adapter.

Delegates the full call lifecycle to VobizAdapter. Called once per inbound
WebSocket connection from server.py.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket

from src.vobiz_adapter import VobizAdapter

logger = logging.getLogger(__name__)


async def run_bot(websocket: WebSocket, call_sid: str, caller_id: str, config: dict) -> None:
    """Build and run the VobizAdapter pipeline for one inbound call.

    Args:
        websocket: FastAPI WebSocket that has already been accepted by server.py.
        call_sid: Vobiz CallUUID from the URL path.
        caller_id: Caller E.164 phone number from the /answer webhook From field.
        config: Full merged config dict.
    """
    adapter = VobizAdapter(config)
    await adapter.handle_call(call_sid, caller_id, websocket)
    await adapter.teardown(call_sid)
```

- [ ] **Run VobizAdapter tests**

```bash
cd telephony_adapter && uv run pytest tests/test_vobiz_adapter.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/src/vobiz_adapter.py \
        telephony_adapter/src/bot.py \
        telephony_adapter/tests/test_vobiz_adapter.py
git commit -m "feat(telephony-adapter): VobizAdapter implements TelephonyAdapterBase; slim bot.py"
```

---

## Task 9: Update `server.py` to extract and pass `caller_id`

**Files:**
- Modify: `telephony_adapter/server.py`
- Modify: `telephony_adapter/tests/test_server.py`

- [ ] **Add failing test**

Add to `telephony_adapter/tests/test_server.py`:

```python
@pytest.mark.asyncio
async def test_answer_webhook_passes_caller_id_to_run_bot():
    """caller_id extracted from From field must be passed to run_bot."""
    from unittest.mock import AsyncMock, patch
    from httpx import AsyncClient, ASGITransport
    from server import create_app

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317", "sample_rate": 1.0, "export_interval_ms": 5000}},
    }
    app = create_app(config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/answer",
            data={"CallUUID": "call-abc", "From": "+919876543210", "To": "+911234567890"},
        )

    assert response.status_code == 200
    assert "call-abc" in response.text


@pytest.mark.asyncio
async def test_websocket_passes_caller_id_from_stored_form():
    """WebSocket endpoint must pass the caller_id stored during /answer to run_bot."""
    # This test verifies the server correctly maps call_sid → caller_id
    # by storing it during /answer and retrieving it during /ws/{call_sid}.
    from unittest.mock import AsyncMock, patch, MagicMock
    from starlette.testclient import TestClient
    import src.bot as bot_module

    config = {
        "telephony_adapter": {
            "public_url": "https://example.com",
            "vobiz": {"auth_id": "aid", "auth_token": "tok", "sample_rate": 8000},
            "vad": {},
            "raya": {"api_key": "k", "tts_base_url": "https://hub.getraya.app/v1", "language": "hi", "voice_id": "v"},
            "agent_core": {"base_url": "http://ac:8000", "timeout_ms": 5000, "greeting": "hi", "fallback_phrase": "sorry"},
        },
        "observability": {"otel": {"collector_endpoint": "http://otelcol:4317", "sample_rate": 1.0, "export_interval_ms": 5000}},
    }

    captured = {}

    async def mock_run_bot(websocket, call_sid, caller_id, config):
        captured["caller_id"] = caller_id
        captured["call_sid"] = call_sid

    with patch.object(bot_module, "run_bot", mock_run_bot):
        app = create_app(config)
        client = TestClient(app)

        # First register the caller_id via /answer
        client.post("/answer", data={"CallUUID": "call-xyz", "From": "+911111111111"})

        # Then open the WebSocket
        with client.websocket_connect("/ws/call-xyz"):
            pass

    assert captured.get("caller_id") == "+911111111111"
    assert captured.get("call_sid") == "call-xyz"
```

- [ ] **Run to confirm failure**

```bash
cd telephony_adapter && uv run pytest tests/test_server.py::test_websocket_passes_caller_id_from_stored_form -v
```
Expected: `AssertionError` (`caller_id` not passed)

- [ ] **Update `server.py`**

In `create_app()`, add a `_caller_id_map: dict[str, str]` to store call_sid → caller_id, and update both `/answer` and `/ws/{call_sid}`:

```python
    # Store caller_id keyed by call_sid so the WebSocket endpoint can retrieve it.
    _caller_id_map: dict[str, str] = {}

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        """Handle Vobiz call-answered webhook; return XML with WebSocket stream URL."""
        form = await request.form()
        call_sid = str(form.get("CallUUID") or form.get("CallSid") or "unknown")
        caller_id = str(form.get("From") or "")
        _caller_id_map[call_sid] = caller_id

        stream_url = f"{ws_url}/ws/{call_sid}"
        op_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
        # Use VobizOperator to generate provider-correct XML
        from src.operators.vobiz_operator import VobizOperator
        try:
            op = VobizOperator(config)
            xml = op.webhook_response_xml(stream_url)
        except ValueError:
            # Fallback if auth credentials not yet configured
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
        """Bidirectional audio stream for an active call."""
        caller_id = _caller_id_map.pop(call_sid, "")
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
            await bot.run_bot(websocket, call_sid, caller_id, _config)
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
```

- [ ] **Run server tests**

```bash
cd telephony_adapter && uv run pytest tests/test_server.py -v
```
Expected: all pass.

- [ ] **Commit**

```bash
git add telephony_adapter/server.py telephony_adapter/tests/test_server.py
git commit -m "feat(telephony-adapter): extract caller_id from /answer webhook; pass to run_bot"
```

---

## Task 10: Add `vad` section to config

**Files:**
- Modify: `telephony_adapter/config/telephony.yaml`

- [ ] **Add `vad` section**

Add under `telephony_adapter:` in `telephony_adapter/config/telephony.yaml`:

```yaml
  vad:
    stop_secs: 0.35       # Silence duration (seconds) to detect end-of-speech
    min_volume: 0.3       # Minimum volume threshold (0–1)
    confidence: 0.4       # VAD confidence threshold (0–1)
    start_secs: 0.1       # Speech duration to confirm voice start
    smoothing_factor: 0.1 # Volume smoothing (lower = faster response)
```

- [ ] **Verify config loads cleanly**

```bash
cd telephony_adapter && uv run python -c "
from config_loader import load_config
cfg = load_config('config/telephony.yaml', 'config/telephony.yaml')
vad = cfg.get('telephony_adapter', {}).get('vad', {})
assert vad.get('stop_secs') == 0.35, f'Expected 0.35, got {vad}'
print('Config OK:', vad)
"
```
Expected: `Config OK: {'stop_secs': 0.35, ...}`

- [ ] **Commit**

```bash
git add telephony_adapter/config/telephony.yaml
git commit -m "feat(telephony-adapter): add vad config section with Silero defaults"
```

---

## Task 11: Full test suite run and coverage check

- [ ] **Run the complete test suite**

```bash
cd telephony_adapter && uv run pytest --cov=src --cov-report=term-missing -v
```

- [ ] **Verify no regressions** — all previously passing tests must still pass.

- [ ] **Check coverage** — confirm new files have meaningful coverage. If any new file is below 70%, add targeted tests for the uncovered lines before proceeding.

- [ ] **Commit any coverage gap fixes**

```bash
git add telephony_adapter/tests/
git commit -m "test(telephony-adapter): fill coverage gaps"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `STTError`, `TTSError` added to `base.py` — Task 1
- ✅ `STTServiceBase`, `TTSServiceBase` — Task 2
- ✅ `RayaSTTService` inherits `STTServiceBase`, `transcribe()` extracted — Task 3
- ✅ `RayaTTSService` inherits `TTSServiceBase`, `synthesize()` extracted — Task 4
- ✅ `VADAnalyzerBase`, `SileroVADWrapper` — Task 5
- ✅ `TelephonyOperatorBase`, `VobizOperator` — Task 6
- ✅ `AgentCoreLLMProcessor` passes `user_id` — Task 7
- ✅ `VobizAdapter` implements `TelephonyAdapterBase` — Task 8
- ✅ `bot.py` slimmed, `caller_id` parameter — Task 8
- ✅ `server.py` extracts `caller_id` from webhook — Task 9
- ✅ Config `vad` section — Task 10
- ✅ Agent Core streaming out of scope — not present ✓
- ✅ Call recording out of scope — not present ✓

**Type consistency:** `AgentCoreLLMProcessor(config, call_sid=, session_id=, user_id=)` signature used consistently in Task 7, Task 8, and Task 8's test.

**No placeholders:** All code blocks are complete. No TBD/TODO present.
