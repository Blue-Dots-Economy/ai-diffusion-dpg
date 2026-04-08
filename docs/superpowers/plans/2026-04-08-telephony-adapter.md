# Telephony Adapter (GH-53) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `telephony_adapter/` — a standalone FastAPI service (port 8006) that handles inbound/outbound Vobiz voice calls by running a Pipecat audio pipeline: Raya WebSocket STT → Agent Core HTTP `/process_turn` → Raya SSE TTS.

**Architecture:** One Pipecat `Pipeline` spawns per call. `VobizFrameSerializer` decodes the Vobiz WebSocket envelope. `RayaSTTService` buffers inbound audio, transcribes via Raya WSS on utterance end. `AgentCoreLLMService` POSTs each transcript to Agent Core and emits the response text. `RayaTTSService` streams PCM audio back via Raya SSE. `CampaignManager` triggers outbound calls via the Vobiz REST API. All components are OTel-instrumented via `dpg_telemetry`.

**Tech Stack:** Python 3.11+, uv, FastAPI, uvicorn, pipecat-ai, httpx, websockets, pyyaml, python-dotenv, dpg_telemetry (from `../observability_layer`), pytest, pytest-asyncio, pytest-mock, respx

---

## File Map

| File | Responsibility |
|---|---|
| `telephony_adapter/pyproject.toml` | Project deps, test config, coverage |
| `telephony_adapter/config/telephony.yaml` | Framework-level config defaults |
| `telephony_adapter/config_loader.py` | `load_config()` — YAML deep-merge (same pattern as reach_layer) |
| `telephony_adapter/src/__init__.py` | Empty |
| `telephony_adapter/src/base.py` | `TelephonyAdapterBase` ABC, `TelephonyTurnInput`, `TelephonyTurnResult`, `TelephonyError` |
| `telephony_adapter/src/vobiz_serializer.py` | `VobizFrameSerializer` — encodes/decodes Vobiz WebSocket JSON envelope |
| `telephony_adapter/src/raya_stt_service.py` | `RayaSTTService` — Pipecat `FrameProcessor`, Raya WebSocket STT |
| `telephony_adapter/src/raya_tts_service.py` | `RayaTTSService` — Pipecat `FrameProcessor`, Raya SSE TTS |
| `telephony_adapter/src/agent_core_service.py` | `AgentCoreLLMService` — Pipecat `FrameProcessor`, calls Agent Core HTTP |
| `telephony_adapter/src/campaign_manager.py` | `CampaignManager` — triggers outbound Vobiz calls via REST |
| `telephony_adapter/src/telephony_adapter.py` | `VobizTelephonyAdapter` — spawns and tears down Pipecat pipeline per call |
| `telephony_adapter/server.py` | FastAPI app: `/answer`, `/ws/{call_sid}`, `/campaign`, `/recording-finished`, `/recording-ready`, `/health` |
| `telephony_adapter/Dockerfile` | Container image |
| `telephony_adapter/tests/test_base.py` | Validates ABC interface contract |
| `telephony_adapter/tests/test_config_loader.py` | Config loading and deep-merge |
| `telephony_adapter/tests/test_vobiz_serializer.py` | Vobiz envelope encode/decode |
| `telephony_adapter/tests/test_raya_stt_service.py` | Raya STT: transcription, WS failure, retry |
| `telephony_adapter/tests/test_raya_tts_service.py` | Raya TTS: SSE streaming, failure path |
| `telephony_adapter/tests/test_agent_core_service.py` | Agent Core HTTP call, escalation, timeout fallback |
| `telephony_adapter/tests/test_campaign_manager.py` | Outbound call trigger, retry on 429 |
| `telephony_adapter/tests/test_server.py` | `/answer`, `/campaign`, `/health` endpoints |

---

## Task 1: Project scaffold

**Files:**
- Create: `telephony_adapter/pyproject.toml`
- Create: `telephony_adapter/src/__init__.py`
- Create: `telephony_adapter/tests/__init__.py`
- Create: `telephony_adapter/config/telephony.yaml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p telephony_adapter/src telephony_adapter/tests telephony_adapter/config
touch telephony_adapter/src/__init__.py telephony_adapter/tests/__init__.py
```

- [ ] **Step 2: Create `telephony_adapter/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "telephony_adapter"
version = "0.1.0"
description = "Telephony channel adapter — Vobiz + Raya STT/TTS + Pipecat pipeline"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pipecat-ai[websocket]>=0.0.46",
    "httpx>=0.27.0",
    "websockets>=12.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "observability-layer",
    "opentelemetry-instrumentation-fastapi>=0.61b0",
    "opentelemetry-instrumentation-httpx>=0.61b0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "pytest-mock>=3.0",
    "httpx>=0.27.0",
    "respx>=0.22.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
asyncio_mode = "auto"

[tool.coverage.run]
source = ["src", "server.py", "config_loader.py"]
omit = ["*/tests/*", "*/__init__.py"]

[tool.coverage.report]
fail_under = 70
show_missing = true

[tool.uv.sources]
observability-layer = { path = "../observability_layer" }
```

- [ ] **Step 3: Create `telephony_adapter/config/telephony.yaml`**

```yaml
telephony_adapter:
  port: 8006
  public_url: ${PUBLIC_URL}
  vobiz:
    auth_id: ${VOBIZ_AUTH_ID}
    auth_token: ${VOBIZ_AUTH_TOKEN}
    api_base: https://api.vobiz.ai/api/v1
    from_number: ${VOBIZ_FROM_NUMBER}
  raya:
    api_key: ${RAYA_API_KEY}
    stt_wss_url: wss://hub.getraya.app/transcribe
    tts_base_url: https://hub.getraya.app/v1
    language: hi
    voice_id: voice_001
    tts_speed: 1.0
  agent_core:
    base_url: http://agent_core:8000
    timeout_ms: 5000
    fallback_phrase: "I'm sorry, I couldn't process that. Please try again."
observability:
  otel:
    collector_endpoint: http://otel-collector:4317
    sample_rate: 1.0
    export_interval_ms: 5000
```

- [ ] **Step 4: Install dependencies**

```bash
cd telephony_adapter
uv sync --extra dev
```

Expected: environment created, all packages resolved without errors.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/
git commit -m "feat(telephony): scaffold project structure and config"
```

---

## Task 2: Base ABC and dataclasses

**Files:**
- Create: `telephony_adapter/src/base.py`
- Create: `telephony_adapter/tests/test_base.py`

- [ ] **Step 1: Write the failing test**

```python
# telephony_adapter/tests/test_base.py
import pytest
from src.base import (
    TelephonyAdapterBase,
    TelephonyTurnInput,
    TelephonyTurnResult,
    TelephonyError,
)


def test_telephony_turn_input_fields():
    t = TelephonyTurnInput(
        session_id="s1",
        call_sid="c1",
        caller_id="+911234567890",
        user_message="hello",
        channel="telephony",
        timestamp_ms=1000,
    )
    assert t.session_id == "s1"
    assert t.call_sid == "c1"
    assert t.caller_id == "+911234567890"
    assert t.channel == "telephony"


def test_telephony_turn_result_fields():
    r = TelephonyTurnResult(
        session_id="s1",
        call_sid="c1",
        response_text="hi",
        was_escalated=False,
    )
    assert r.response_text == "hi"
    assert r.was_escalated is False
    assert r.latency_ms == 0


def test_abstract_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TelephonyAdapterBase()


def test_telephony_error_is_exception():
    err = TelephonyError("something failed")
    assert isinstance(err, Exception)
    assert "something failed" in str(err)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd telephony_adapter
uv run pytest tests/test_base.py -v
```

Expected: `ImportError` — `src.base` does not exist.

- [ ] **Step 3: Create `telephony_adapter/src/base.py`**

```python
"""
telephony_adapter/src/base.py

TelephonyAdapterBase — abstract interface for the telephony channel adapter.
All concrete adapter implementations inherit from this class.
Belongs to the Reach Layer channel family in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class TelephonyError(Exception):
    """Raised when a telephony adapter operation fails unrecoverably."""


@dataclass
class TelephonyTurnInput:
    """Normalised inbound turn from a telephone call.

    Extends the Reach Layer TurnInput concept with telephony-specific metadata.
    """

    session_id: str
    call_sid: str
    caller_id: str
    user_message: str
    channel: str
    timestamp_ms: int
    user_id: Optional[str] = None


@dataclass
class TelephonyTurnResult:
    """Normalised outbound response for a telephone call turn."""

    session_id: str
    call_sid: str
    response_text: str
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0


class TelephonyAdapterBase(ABC):
    """Abstract base class for telephony channel adapters.

    Defines the lifecycle interface every concrete adapter must implement:
    pipeline setup, turn processing, and teardown.
    """

    @abstractmethod
    async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
        """Handle the full lifecycle of an inbound call over a WebSocket.

        Args:
            call_sid: Unique identifier for this call from the telephony provider.
            caller_id: Caller's phone number (opaque — never log directly).
            websocket: Active WebSocket connection from the telephony provider.

        Raises:
            TelephonyError: If the pipeline cannot be established.
        """

    @abstractmethod
    async def teardown(self, call_sid: str) -> None:
        """Clean up resources for a completed or dropped call.

        Args:
            call_sid: The call SID whose resources should be released.
        """
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd telephony_adapter
uv run pytest tests/test_base.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/base.py telephony_adapter/tests/test_base.py
git commit -m "feat(telephony): add TelephonyAdapterBase ABC and dataclasses"
```

---

## Task 3: Config loader

**Files:**
- Create: `telephony_adapter/config_loader.py`
- Create: `telephony_adapter/tests/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# telephony_adapter/tests/test_config_loader.py
import pytest
import tempfile, os
from pathlib import Path
from config_loader import load_config, load_yaml, deep_merge


def _write_yaml(path: Path, content: str):
    path.write_text(content)


def test_deep_merge_overrides_scalar():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}}
    result = deep_merge(base, override)
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3


def test_deep_merge_does_not_mutate_base():
    base = {"a": {"b": 1}}
    deep_merge(base, {"a": {"b": 2}})
    assert base["a"]["b"] == 1


def test_load_config_merges_domain_over_defaults(tmp_path):
    dpg = tmp_path / "dpg.yaml"
    domain = tmp_path / "domain.yaml"
    _write_yaml(dpg, "telephony_adapter:\n  port: 8006\n  language: en\n")
    _write_yaml(domain, "telephony_adapter:\n  language: hi\n")
    cfg = load_config(str(dpg), str(domain))
    assert cfg["telephony_adapter"]["port"] == 8006
    assert cfg["telephony_adapter"]["language"] == "hi"


def test_load_config_missing_domain_uses_defaults(tmp_path):
    dpg = tmp_path / "dpg.yaml"
    _write_yaml(dpg, "telephony_adapter:\n  port: 8006\n")
    cfg = load_config(str(dpg), str(tmp_path / "missing.yaml"))
    assert cfg["telephony_adapter"]["port"] == 8006


def test_load_yaml_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_yaml("/nonexistent/path/config.yaml")
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd telephony_adapter
uv run pytest tests/test_config_loader.py -v
```

Expected: `ImportError` — `config_loader` not found.

- [ ] **Step 3: Create `telephony_adapter/config_loader.py`**

```python
"""
telephony_adapter/config_loader.py

Config loading utilities for the telephony adapter.
Loads framework defaults and domain overrides using the same deep-merge
pattern shared across all DPG blocks.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: str) -> dict:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Relative or absolute path to the YAML file.

    Returns:
        Parsed YAML contents as a dict, or empty dict if file is empty.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, with override values winning on conflicts.

    Args:
        base: The base configuration dict.
        override: Values to overlay on top of base.

    Returns:
        New dict with override applied on top of base. Does not mutate inputs.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(dpg_path: str, domain_path: str) -> dict:
    """Load and merge DPG framework defaults with domain overrides.

    Args:
        dpg_path: Path to the framework YAML defaults.
        domain_path: Path to the domain override YAML.

    Returns:
        Merged config dict. Domain values override DPG defaults.

    Raises:
        FileNotFoundError: If dpg_path does not exist.
    """
    dpg_config = load_yaml(dpg_path)
    try:
        domain_config = load_yaml(domain_path)
    except FileNotFoundError:
        domain_config = {}
    return deep_merge(dpg_config, domain_config)
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_config_loader.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/config_loader.py telephony_adapter/tests/test_config_loader.py
git commit -m "feat(telephony): add config loader with deep-merge"
```

---

## Task 4: VobizFrameSerializer

**Files:**
- Create: `telephony_adapter/src/vobiz_serializer.py`
- Create: `telephony_adapter/tests/test_vobiz_serializer.py`

Background: Vobiz sends JSON messages over WebSocket. The `start` event carries `call_sid` and `caller_id`. The `media` event carries base64-encoded µ-law 8000 Hz audio. Outbound audio is sent back as `{"event": "media", "media": {"payload": "<base64>"}}`.

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_vobiz_serializer.py
import base64
import json
import pytest
from src.vobiz_serializer import VobizFrameSerializer, VobizCallMetadata


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_parse_start_event_extracts_metadata():
    msg = json.dumps({
        "event": "start",
        "start": {
            "callSid": "CA123",
            "streamSid": "SS456",
            "customParameters": {"caller_id": "+911234567890"},
        },
        "streamSid": "SS456",
    })
    serializer = VobizFrameSerializer()
    metadata = serializer.parse_start(msg)
    assert metadata.call_sid == "CA123"
    assert metadata.stream_sid == "SS456"
    assert metadata.caller_id == "+911234567890"


def test_parse_start_missing_caller_id_defaults_to_unknown():
    msg = json.dumps({
        "event": "start",
        "start": {"callSid": "CA999", "streamSid": "SS999", "customParameters": {}},
        "streamSid": "SS999",
    })
    serializer = VobizFrameSerializer()
    metadata = serializer.parse_start(msg)
    assert metadata.caller_id == "unknown"


def test_parse_media_returns_audio_bytes():
    audio = b"\x00\x01\x02\x03"
    msg = json.dumps({
        "event": "media",
        "media": {"payload": _b64(audio), "track": "inbound"},
        "streamSid": "SS456",
    })
    serializer = VobizFrameSerializer()
    result = serializer.parse_media(msg)
    assert result == audio


def test_parse_media_invalid_payload_raises():
    msg = json.dumps({"event": "media", "media": {"payload": "!!!not_base64"}, "streamSid": "x"})
    serializer = VobizFrameSerializer()
    with pytest.raises(ValueError, match="Invalid base64"):
        serializer.parse_media(msg)


def test_build_media_message_encodes_audio():
    audio = b"\xaa\xbb\xcc"
    serializer = VobizFrameSerializer()
    msg = serializer.build_media_message("SS456", audio)
    parsed = json.loads(msg)
    assert parsed["event"] == "media"
    decoded = base64.b64decode(parsed["media"]["payload"])
    assert decoded == audio


def test_is_stop_event():
    serializer = VobizFrameSerializer()
    stop_msg = json.dumps({"event": "stop", "streamSid": "SS1"})
    media_msg = json.dumps({"event": "media", "media": {"payload": ""}})
    assert serializer.is_stop_event(stop_msg) is True
    assert serializer.is_stop_event(media_msg) is False


def test_is_stop_event_on_invalid_json_returns_false():
    serializer = VobizFrameSerializer()
    assert serializer.is_stop_event("not json") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_vobiz_serializer.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/vobiz_serializer.py`**

```python
"""
telephony_adapter/src/vobiz_serializer.py

VobizFrameSerializer — decodes and encodes the Vobiz WebSocket message envelope.

Vobiz streams audio over a WebSocket using JSON messages:
  - "start": call metadata (call SID, stream SID, caller ID)
  - "media": base64-encoded µ-law 8000 Hz audio chunk (inbound or outbound)
  - "stop": call ended

Outbound audio is sent back as {"event": "media", "media": {"payload": "<base64>"}}.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VobizCallMetadata:
    """Metadata extracted from a Vobiz "start" WebSocket event."""

    call_sid: str
    stream_sid: str
    caller_id: str


class VobizFrameSerializer:
    """Encode and decode Vobiz WebSocket JSON envelope messages.

    Stateless — one instance may be shared across calls.
    """

    def parse_start(self, raw: str) -> VobizCallMetadata:
        """Parse a Vobiz "start" event and return call metadata.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            VobizCallMetadata with call_sid, stream_sid, and caller_id.
            caller_id defaults to "unknown" if not present in customParameters.

        Raises:
            ValueError: If the message is not valid JSON or missing required fields.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in start event: {e}") from e

        start = data.get("start", {})
        call_sid = start.get("callSid") or data.get("callSid", "")
        stream_sid = start.get("streamSid") or data.get("streamSid", "")
        caller_id = start.get("customParameters", {}).get("caller_id", "unknown")

        if not call_sid:
            raise ValueError(f"Missing callSid in start event: {raw!r}")

        return VobizCallMetadata(
            call_sid=call_sid,
            stream_sid=stream_sid,
            caller_id=caller_id,
        )

    def parse_media(self, raw: str) -> bytes:
        """Decode audio bytes from a Vobiz "media" event.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            Decoded audio bytes (µ-law 8000 Hz).

        Raises:
            ValueError: If the base64 payload is missing or malformed.
        """
        try:
            data = json.loads(raw)
            payload = data.get("media", {}).get("payload", "")
        except (json.JSONDecodeError, AttributeError) as e:
            raise ValueError(f"Invalid media event: {e}") from e

        if not payload:
            return b""

        try:
            return base64.b64decode(payload)
        except Exception as e:
            raise ValueError(f"Invalid base64 in media payload: {e}") from e

    def build_media_message(self, stream_sid: str, audio: bytes) -> str:
        """Build a Vobiz "media" outbound message from raw audio bytes.

        Args:
            stream_sid: The stream SID for this call.
            audio: Raw audio bytes to send back to Vobiz.

        Returns:
            JSON string ready to send over the Vobiz WebSocket.
        """
        return json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode()},
        })

    def is_stop_event(self, raw: str) -> bool:
        """Return True if the message is a Vobiz "stop" event.

        Args:
            raw: Raw JSON string received from Vobiz WebSocket.

        Returns:
            True if event == "stop", False otherwise (including on parse error).
        """
        try:
            return json.loads(raw).get("event") == "stop"
        except (json.JSONDecodeError, AttributeError):
            return False
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_vobiz_serializer.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/vobiz_serializer.py telephony_adapter/tests/test_vobiz_serializer.py
git commit -m "feat(telephony): add VobizFrameSerializer"
```

---

## Task 5: RayaSTTService

**Files:**
- Create: `telephony_adapter/src/raya_stt_service.py`
- Create: `telephony_adapter/tests/test_raya_stt_service.py`

Background: This is a Pipecat `FrameProcessor`. It buffers inbound audio bytes, sends them as a base64 WAV over the Raya WebSocket STT endpoint when the call layer signals end-of-utterance (by calling `transcribe(audio_bytes)`), and returns the transcript string. The pipeline calls `transcribe()` directly; it is not a full Pipecat pipeline node in this implementation — `VobizTelephonyAdapter` drives it.

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_raya_stt_service.py
import asyncio
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.raya_stt_service import RayaSTTService


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
async def test_transcribe_returns_transcript(config):
    audio = b"\x00" * 100
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(return_value=json.dumps({
        "transcript": "नमस्ते",
        "status": "success",
    }))

    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = RayaSTTService(config)
        result = await svc.transcribe(audio)

    assert result == "नमस्ते"
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert "audio_base64" in sent
    assert sent["language"] == "hi"
    decoded = base64.b64decode(sent["audio_base64"])
    assert decoded == audio


@pytest.mark.asyncio
async def test_transcribe_empty_audio_returns_empty_string(config):
    svc = RayaSTTService(config)
    result = await svc.transcribe(b"")
    assert result == ""


@pytest.mark.asyncio
async def test_transcribe_ws_failure_retries_once_then_raises(config):
    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.side_effect = OSError("connection refused")
        svc = RayaSTTService(config)
        with pytest.raises(Exception, match="STT"):
            await svc.transcribe(b"\x00" * 100)
    assert mock_connect.call_count == 2


@pytest.mark.asyncio
async def test_transcribe_error_response_raises(config):
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(return_value=json.dumps({
        "error": "bad request",
        "status": "error",
    }))

    with patch("src.raya_stt_service.websockets.connect") as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = RayaSTTService(config)
        with pytest.raises(Exception, match="STT transcription failed"):
            await svc.transcribe(b"\x00" * 100)


@pytest.mark.asyncio
async def test_transcribe_missing_config_raises():
    with pytest.raises(ValueError, match="api_key"):
        RayaSTTService({})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_raya_stt_service.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/raya_stt_service.py`**

```python
"""
telephony_adapter/src/raya_stt_service.py

RayaSTTService — transcribes audio utterances via the Raya/Bakbak WebSocket STT API.

Called by VobizTelephonyAdapter once per utterance (after silence detection).
Sends base64-encoded WAV audio over WSS and returns the transcript string.
Retries once on connection failure with exponential backoff.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import websockets

logger = logging.getLogger(__name__)


class RayaSTTService:
    """Transcribes audio bytes via the Raya WebSocket STT endpoint.

    Args:
        config: Full merged config dict. Reads telephony_adapter.raya section.

    Raises:
        ValueError: If required config keys (api_key, stt_wss_url) are missing.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        raya_cfg = config.get("telephony_adapter", {}).get("raya", {})
        api_key = raya_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("telephony_adapter.raya.api_key is required")
        self._api_key = api_key
        self._wss_url = raya_cfg.get("stt_wss_url", "wss://hub.getraya.app/transcribe")
        self._language = raya_cfg.get("language", "hi")
        self._max_retries = 2

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe raw audio bytes to text via the Raya WebSocket STT API.

        Sends the audio as a single base64-encoded payload per connection.
        Retries once on transient connection failures.

        Args:
            audio: Raw audio bytes (µ-law 8000 Hz from Vobiz). Empty bytes
                   are returned immediately as an empty string.

        Returns:
            Transcript string, or empty string if audio is empty.

        Raises:
            Exception: If transcription fails after retries, or if Raya returns
                       an error status.
        """
        if not audio:
            return ""

        start = time.time()
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                transcript = await self._call_raya_wss(audio)
                logger.info(
                    "raya_stt.transcribe",
                    extra={
                        "operation": "raya_stt_service.transcribe",
                        "status": "success",
                        "latency_ms": int((time.time() - start) * 1000),
                        "language": self._language,
                    },
                )
                return transcript
            except Exception as e:
                last_error = e
                logger.warning(
                    "raya_stt.retry",
                    extra={
                        "operation": "raya_stt_service.transcribe",
                        "status": "failure",
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        logger.error(
            "raya_stt.failed",
            extra={
                "operation": "raya_stt_service.transcribe",
                "status": "failure",
                "latency_ms": int((time.time() - start) * 1000),
                "error": str(last_error),
            },
        )
        raise Exception(f"STT transcription failed after {self._max_retries} attempts: {last_error}")

    async def _call_raya_wss(self, audio: bytes) -> str:
        """Open a Raya WebSocket connection, send audio, and return transcript.

        Args:
            audio: Raw audio bytes to transcribe.

        Returns:
            Transcript string from Raya.

        Raises:
            Exception: On connection error or Raya error response.
        """
        headers = {"X-API-Key": self._api_key}
        payload = json.dumps({
            "audio_base64": base64.b64encode(audio).decode(),
            "language": self._language,
        })

        async with websockets.connect(self._wss_url, additional_headers=headers) as ws:
            await ws.send(payload)
            raw_response = await ws.recv()

        response = json.loads(raw_response)
        if response.get("status") == "error" or "error" in response:
            raise Exception(
                f"STT transcription failed: {response.get('error', response)}"
            )
        return response.get("transcript", "")
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_raya_stt_service.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/raya_stt_service.py telephony_adapter/tests/test_raya_stt_service.py
git commit -m "feat(telephony): add RayaSTTService with retry"
```

---

## Task 6: RayaTTSService

**Files:**
- Create: `telephony_adapter/src/raya_tts_service.py`
- Create: `telephony_adapter/tests/test_raya_tts_service.py`

Background: Calls `POST /v1/text-to-speech/stream` (SSE). Each `event: chunk` carries a base64 PCM F32LE audio chunk. Returns the full audio as concatenated bytes once `event: done` is received.

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_raya_tts_service.py
import base64
import pytest
import respx
import httpx
from src.raya_tts_service import RayaTTSService


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


def _sse_body(chunks: list[bytes]) -> bytes:
    lines = []
    for chunk in chunks:
        data = {
            "type": "chunk",
            "status_code": 206,
            "done": False,
            "data": base64.b64encode(chunk).decode(),
        }
        import json
        lines.append(f"event: chunk\ndata: {json.dumps(data)}\n\n")
    lines.append("event: done\ndata: {}\n\n")
    return "".join(lines).encode()


@pytest.mark.asyncio
async def test_synthesize_returns_audio_bytes(config):
    chunk1 = b"\x01\x02"
    chunk2 = b"\x03\x04"

    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_sse_body([chunk1, chunk2]))
        )
        svc = RayaTTSService(config)
        result = await svc.synthesize("hello")

    assert result == chunk1 + chunk2


@pytest.mark.asyncio
async def test_synthesize_empty_text_returns_empty_bytes(config):
    svc = RayaTTSService(config)
    result = await svc.synthesize("")
    assert result == b""


@pytest.mark.asyncio
async def test_synthesize_http_error_raises(config):
    with respx.mock:
        respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        svc = RayaTTSService(config)
        with pytest.raises(Exception, match="TTS synthesis failed"):
            await svc.synthesize("hello")


@pytest.mark.asyncio
async def test_synthesize_missing_config_raises():
    with pytest.raises(ValueError, match="api_key"):
        RayaTTSService({})


@pytest.mark.asyncio
async def test_synthesize_sends_correct_payload(config):
    with respx.mock:
        route = respx.post("https://hub.getraya.app/v1/text-to-speech/stream").mock(
            return_value=httpx.Response(200, content=_sse_body([b"\x00"]))
        )
        svc = RayaTTSService(config)
        await svc.synthesize("नमस्ते")

    import json as _json
    body = _json.loads(route.calls[0].request.content)
    assert body["text"] == "नमस्ते"
    assert body["voice_id"] == "voice_001"
    assert body["language"] == "hi"
    assert route.calls[0].request.headers["X-API-Key"] == "test-key"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_raya_tts_service.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/raya_tts_service.py`**

```python
"""
telephony_adapter/src/raya_tts_service.py

RayaTTSService — converts text to audio via the Raya/Bakbak SSE streaming TTS API.

Calls POST /v1/text-to-speech/stream and accumulates base64 PCM F32LE chunks
from Server-Sent Events until the "done" event, then returns concatenated audio bytes.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class RayaTTSService:
    """Synthesises speech audio from text via the Raya SSE streaming TTS endpoint.

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
        self._api_key = api_key
        self._base_url = raya_cfg.get("tts_base_url", "https://hub.getraya.app/v1")
        self._voice_id = raya_cfg.get("voice_id", "voice_001")
        self._language = raya_cfg.get("language", "hi")
        self._speed = float(raya_cfg.get("tts_speed", 1.0))

    async def synthesize(self, text: str) -> bytes:
        """Convert text to audio bytes via the Raya SSE streaming TTS API.

        Streams chunks from the SSE response and concatenates them into a
        single audio buffer. Returns immediately on empty text.

        Args:
            text: The text to synthesise. Empty string returns b"" immediately.

        Returns:
            Concatenated raw audio bytes (PCM F32LE) from all SSE chunks.

        Raises:
            Exception: If the HTTP request fails or Raya returns a non-200 status.
        """
        if not text or not text.strip():
            return b""

        start = time.time()
        url = f"{self._base_url}/text-to-speech/stream"
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "language": self._language,
            "speed": self._speed,
        }
        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code != 200:
                raise Exception(
                    f"TTS synthesis failed: HTTP {response.status_code} — {response.text[:200]}"
                )

            audio_chunks: list[bytes] = []
            for line in response.text.splitlines():
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
                    audio_chunks.append(base64.b64decode(chunk_data["data"]))

            result = b"".join(audio_chunks)
            logger.info(
                "raya_tts.synthesize",
                extra={
                    "operation": "raya_tts_service.synthesize",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                    "audio_bytes": len(result),
                },
            )
            return result

        except Exception as e:
            if "TTS synthesis failed" in str(e):
                raise
            logger.error(
                "raya_tts.error",
                extra={
                    "operation": "raya_tts_service.synthesize",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            raise Exception(f"TTS synthesis failed: {e}") from e
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_raya_tts_service.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/raya_tts_service.py telephony_adapter/tests/test_raya_tts_service.py
git commit -m "feat(telephony): add RayaTTSService with SSE streaming"
```

---

## Task 7: AgentCoreLLMService

**Files:**
- Create: `telephony_adapter/src/agent_core_service.py`
- Create: `telephony_adapter/tests/test_agent_core_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_agent_core_service.py
import pytest
import respx
import httpx
from src.agent_core_service import AgentCoreLLMService


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
async def test_process_turn_returns_response_text(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "मैं आपकी मदद कर सकता हूँ।",
                "was_escalated": False,
                "was_tool_used": False,
                "model_used": "claude-haiku",
                "latency_ms": 300,
            })
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn(
            session_id="s1",
            user_message="मुझे काम चाहिए",
            call_sid="CA123",
            caller_id="+911234567890",
        )

    assert result.response_text == "मैं आपकी मदद कर सकता हूँ।"
    assert result.was_escalated is False


@pytest.mark.asyncio
async def test_process_turn_sends_correct_payload(config):
    with respx.mock:
        route = respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "ok",
                "was_escalated": False,
                "was_tool_used": False,
                "model_used": "",
                "latency_ms": 0,
            })
        )
        svc = AgentCoreLLMService(config)
        await svc.process_turn("s1", "hello", "CA1", "+91999")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["session_id"] == "s1"
    assert body["user_message"] == "hello"
    assert body["channel"] == "telephony"
    assert body["user_id"] == "CA1"  # call_sid used as opaque user_id


@pytest.mark.asyncio
async def test_process_turn_timeout_returns_fallback(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "hello", "CA1", "+91999")

    assert result.response_text == "Sorry, I could not process that."
    assert result.was_escalated is False


@pytest.mark.asyncio
async def test_process_turn_http_500_returns_fallback(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(500, json={"detail": "internal error"})
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "hello", "CA1", "+91999")

    assert result.response_text == "Sorry, I could not process that."


@pytest.mark.asyncio
async def test_process_turn_escalation_flag_propagated(config):
    with respx.mock:
        respx.post("http://agent_core:8000/process_turn").mock(
            return_value=httpx.Response(200, json={
                "session_id": "s1",
                "response_text": "transferring you now",
                "was_escalated": True,
                "was_tool_used": False,
                "model_used": "",
                "latency_ms": 0,
            })
        )
        svc = AgentCoreLLMService(config)
        result = await svc.process_turn("s1", "help", "CA1", "+91999")

    assert result.was_escalated is True


@pytest.mark.asyncio
async def test_missing_config_raises():
    with pytest.raises(ValueError, match="base_url"):
        AgentCoreLLMService({})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_agent_core_service.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/agent_core_service.py`**

```python
"""
telephony_adapter/src/agent_core_service.py

AgentCoreLLMService — submits each utterance to Agent Core's /process_turn HTTP API.

Agent Core is the sole LLM orchestrator in the DPG framework. This service is the
telephony adapter's bridge to Agent Core — it translates a transcript + call metadata
into a TurnInput HTTP request and returns the TurnResult. Falls back to a configured
phrase on timeout or HTTP error so the call can continue gracefully.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AgentCoreTurnResult:
    """Normalised result from Agent Core's /process_turn endpoint."""

    session_id: str
    response_text: str
    was_escalated: bool
    was_tool_used: bool
    model_used: str
    latency_ms: int


class AgentCoreLLMService:
    """Submits transcribed utterances to Agent Core and returns the response.

    Args:
        config: Full merged config dict. Reads telephony_adapter.agent_core section.

    Raises:
        ValueError: If base_url is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        base_url = ac_cfg.get("base_url", "")
        if not base_url:
            raise ValueError("telephony_adapter.agent_core.base_url is required")
        self._base_url = base_url.rstrip("/")
        timeout_ms = int(ac_cfg.get("timeout_ms", 5000))
        self._timeout = timeout_ms / 1000.0
        self._fallback_phrase = ac_cfg.get(
            "fallback_phrase", "I'm sorry, I couldn't process that. Please try again."
        )

    async def process_turn(
        self,
        session_id: str,
        user_message: str,
        call_sid: str,
        caller_id: str,
    ) -> AgentCoreTurnResult:
        """Submit one utterance to Agent Core and return the response.

        Uses call_sid as the opaque user_id — never passes caller_id (phone number)
        to Agent Core to avoid PII in logs.

        On HTTP error or timeout, returns a fallback response so the call
        continues rather than hanging silently.

        Args:
            session_id: Stable session UUID for this call's lifetime.
            user_message: Transcribed text from the caller's utterance.
            call_sid: Opaque call identifier (used as user_id, not caller phone).
            caller_id: Caller phone number. Present only to satisfy signature;
                       never forwarded to Agent Core.

        Returns:
            AgentCoreTurnResult with response text and escalation flag.
        """
        start = time.time()
        url = f"{self._base_url}/process_turn"
        payload = {
            "session_id": session_id,
            "user_message": user_message,
            "channel": "telephony",
            "user_id": call_sid,
            "timestamp_ms": int(start * 1000),
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)

            if response.status_code != 200:
                logger.error(
                    "agent_core_service.http_error",
                    extra={
                        "operation": "agent_core_service.process_turn",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return self._fallback(session_id)

            data = response.json()
            logger.info(
                "agent_core_service.success",
                extra={
                    "operation": "agent_core_service.process_turn",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                    "was_escalated": data.get("was_escalated", False),
                },
            )
            return AgentCoreTurnResult(
                session_id=data.get("session_id", session_id),
                response_text=data.get("response_text", self._fallback_phrase),
                was_escalated=data.get("was_escalated", False),
                was_tool_used=data.get("was_tool_used", False),
                model_used=data.get("model_used", ""),
                latency_ms=data.get("latency_ms", 0),
            )

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.error(
                "agent_core_service.timeout",
                extra={
                    "operation": "agent_core_service.process_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return self._fallback(session_id)

    def _fallback(self, session_id: str) -> AgentCoreTurnResult:
        """Return the configured fallback response when Agent Core is unavailable.

        Args:
            session_id: The session ID for the current call.

        Returns:
            AgentCoreTurnResult with fallback_phrase as response_text.
        """
        return AgentCoreTurnResult(
            session_id=session_id,
            response_text=self._fallback_phrase,
            was_escalated=False,
            was_tool_used=False,
            model_used="",
            latency_ms=0,
        )
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_agent_core_service.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/agent_core_service.py telephony_adapter/tests/test_agent_core_service.py
git commit -m "feat(telephony): add AgentCoreLLMService with fallback"
```

---

## Task 8: CampaignManager

**Files:**
- Create: `telephony_adapter/src/campaign_manager.py`
- Create: `telephony_adapter/tests/test_campaign_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_campaign_manager.py
import pytest
import respx
import httpx
from src.campaign_manager import CampaignManager


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {
                "auth_id": "MA_TEST123",
                "auth_token": "token123",
                "api_base": "https://api.vobiz.ai/api/v1",
                "from_number": "+918011223344",
            },
            "public_url": "https://example.ngrok.app",
        }
    }


@pytest.mark.asyncio
async def test_initiate_call_sends_correct_payload(config):
    with respx.mock:
        route = respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(return_value=httpx.Response(200, json={"callSid": "CA999"}))

        mgr = CampaignManager(config)
        result = await mgr.initiate_call(to_number="+919148223344")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["to"] == "+919148223344"
    assert body["from"] == "+918011223344"
    assert body["answer_url"] == "https://example.ngrok.app/answer"
    assert body["answer_method"] == "POST"
    assert route.calls[0].request.headers["X-Auth-ID"] == "MA_TEST123"
    assert route.calls[0].request.headers["X-Auth-Token"] == "token123"
    assert result["callSid"] == "CA999"


@pytest.mark.asyncio
async def test_initiate_call_retries_on_429(config):
    with respx.mock:
        responses = [
            httpx.Response(429, json={"error": "rate limit"}),
            httpx.Response(200, json={"callSid": "CA888"}),
        ]
        respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(side_effect=responses)

        mgr = CampaignManager(config)
        result = await mgr.initiate_call(to_number="+919148223344")

    assert result["callSid"] == "CA888"


@pytest.mark.asyncio
async def test_initiate_call_raises_after_max_retries(config):
    with respx.mock:
        respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(return_value=httpx.Response(429, json={"error": "rate limit"}))

        mgr = CampaignManager(config)
        with pytest.raises(Exception, match="outbound call failed"):
            await mgr.initiate_call(to_number="+919148223344")


@pytest.mark.asyncio
async def test_initiate_call_empty_to_number_raises(config):
    mgr = CampaignManager(config)
    with pytest.raises(ValueError, match="to_number"):
        await mgr.initiate_call(to_number="")


@pytest.mark.asyncio
async def test_missing_config_raises():
    with pytest.raises(ValueError, match="auth_id"):
        CampaignManager({})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_campaign_manager.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/campaign_manager.py`**

```python
"""
telephony_adapter/src/campaign_manager.py

CampaignManager — triggers outbound calls via the Vobiz REST API.

Exposes initiate_call() which the FastAPI /campaign endpoint delegates to.
Action Gateway can also call POST /campaign as a telephony_channel_switch connector
tool when Agent Core decides to switch channels mid-session.

Retries on HTTP 429 (rate limit) with exponential backoff up to max_retries.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class CampaignManager:
    """Triggers outbound PSTN calls via the Vobiz REST API.

    Args:
        config: Full merged config dict. Reads telephony_adapter.vobiz section.

    Raises:
        ValueError: If auth_id is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        vobiz_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
        auth_id = vobiz_cfg.get("auth_id", "")
        if not auth_id:
            raise ValueError("telephony_adapter.vobiz.auth_id is required")
        self._auth_id = auth_id
        self._auth_token = vobiz_cfg.get("auth_token", "")
        self._api_base = vobiz_cfg.get("api_base", "https://api.vobiz.ai/api/v1").rstrip("/")
        self._from_number = vobiz_cfg.get("from_number", "")
        self._public_url = config.get("telephony_adapter", {}).get("public_url", "")
        self._max_retries = 3

    async def initiate_call(self, to_number: str) -> dict:
        """Trigger an outbound call to the given number via the Vobiz REST API.

        The answer_url points to this service's /answer endpoint so Vobiz will
        route the answered call back through the Pipecat pipeline.

        Args:
            to_number: E.164 phone number to dial (e.g., "+919148223344").

        Returns:
            Vobiz API response dict (contains callSid on success).

        Raises:
            ValueError: If to_number is empty.
            Exception: If the Vobiz API returns a non-recoverable error after
                       max_retries attempts.
        """
        if not to_number or not to_number.strip():
            raise ValueError("to_number must not be empty")

        url = f"{self._api_base}/Account/{self._auth_id}/Call/"
        payload = {
            "from": self._from_number,
            "to": to_number,
            "answer_url": f"{self._public_url}/answer",
            "answer_method": "POST",
        }
        headers = {
            "X-Auth-ID": self._auth_id,
            "X-Auth-Token": self._auth_token,
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    logger.info(
                        "campaign_manager.call_initiated",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "success",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return response.json()

                if response.status_code == 429:
                    wait = 1.0 * (2 ** attempt)
                    logger.warning(
                        "campaign_manager.rate_limited",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "failure",
                            "attempt": attempt + 1,
                            "retry_after_s": wait,
                        },
                    )
                    last_error = Exception(f"HTTP 429 on attempt {attempt + 1}")
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(wait)
                    continue

                raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")

            except Exception as e:
                if "HTTP 429" not in str(e):
                    logger.error(
                        "campaign_manager.error",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "failure",
                            "error": f"{type(e).__name__}: {e}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    raise Exception(f"outbound call failed: {e}") from e
                last_error = e

        raise Exception(f"outbound call failed after {self._max_retries} attempts: {last_error}")
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_campaign_manager.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/campaign_manager.py telephony_adapter/tests/test_campaign_manager.py
git commit -m "feat(telephony): add CampaignManager with outbound call + retry"
```

---

## Task 9: VobizTelephonyAdapter (pipeline orchestrator)

**Files:**
- Create: `telephony_adapter/src/telephony_adapter.py`
- Create: `telephony_adapter/tests/test_telephony_adapter.py`

Background: This class implements `TelephonyAdapterBase`. It drives the per-call turn loop: read WebSocket messages from Vobiz, buffer audio, detect silence (300 ms gap), call Raya STT, call Agent Core, call Raya TTS, send audio back. Does not use Pipecat's Pipeline class directly — it owns the async turn loop to keep the Vobiz WebSocket integration simple and testable.

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_telephony_adapter.py
import asyncio
import json
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from src.telephony_adapter import VobizTelephonyAdapter
from src.base import TelephonyError
from src.vobiz_serializer import VobizCallMetadata


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {"auth_id": "MA1", "auth_token": "t", "api_base": "https://api.vobiz.ai/api/v1", "from_number": "+91"},
            "raya": {"api_key": "k", "stt_wss_url": "wss://...", "tts_base_url": "https://...", "language": "hi", "voice_id": "v1", "tts_speed": 1.0},
            "agent_core": {"base_url": "http://agent_core:8000", "timeout_ms": 5000, "fallback_phrase": "sorry"},
            "public_url": "https://example.app",
        },
        "observability": {"otel": {"collector_endpoint": "http://localhost:4317"}},
    }


def _make_ws(messages: list[str]):
    """Create an async mock WebSocket that yields given messages then closes."""
    ws = AsyncMock()
    ws.__aiter__ = MagicMock(return_value=iter(messages))
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _start_msg(call_sid="CA1", caller_id="+91999"):
    return json.dumps({
        "event": "start",
        "start": {
            "callSid": call_sid,
            "streamSid": "SS1",
            "customParameters": {"caller_id": caller_id},
        },
        "streamSid": "SS1",
    })


def _media_msg(audio=b"\x00" * 20):
    return json.dumps({
        "event": "media",
        "media": {"payload": base64.b64encode(audio).decode(), "track": "inbound"},
        "streamSid": "SS1",
    })


def _stop_msg():
    return json.dumps({"event": "stop", "streamSid": "SS1"})


@pytest.mark.asyncio
async def test_handle_call_full_turn(config):
    """Inbound call: start → audio × N → stop → STT called → AC called → TTS sent."""
    ws = _make_ws([
        _start_msg(),
        _media_msg(),
        _media_msg(),
        _stop_msg(),
    ])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        mock_stt = AsyncMock()
        mock_stt.transcribe = AsyncMock(return_value="नमस्ते")
        MockSTT.return_value = mock_stt

        mock_ac = AsyncMock()
        from src.agent_core_service import AgentCoreTurnResult
        mock_ac.process_turn = AsyncMock(return_value=AgentCoreTurnResult(
            session_id="s1", response_text="hello", was_escalated=False,
            was_tool_used=False, model_used="", latency_ms=100,
        ))
        MockAC.return_value = mock_ac

        mock_tts = AsyncMock()
        mock_tts.synthesize = AsyncMock(return_value=b"\x01\x02\x03")
        MockTTS.return_value = mock_tts

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    mock_stt.transcribe.assert_called_once()
    mock_ac.process_turn.assert_called_once()
    assert mock_ac.process_turn.call_args.kwargs["user_message"] == "नमस्ते"
    mock_tts.synthesize.assert_called_once_with("hello")
    ws.send.assert_called()


@pytest.mark.asyncio
async def test_handle_call_escalation_closes_call(config):
    ws = _make_ws([_start_msg(), _media_msg(), _stop_msg()])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        MockSTT.return_value.transcribe = AsyncMock(return_value="help")
        from src.agent_core_service import AgentCoreTurnResult
        MockAC.return_value.process_turn = AsyncMock(return_value=AgentCoreTurnResult(
            session_id="s1", response_text="transferring", was_escalated=True,
            was_tool_used=False, model_used="", latency_ms=0,
        ))
        MockTTS.return_value.synthesize = AsyncMock(return_value=b"\x00")

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_teardown_removes_call_sid(config):
    adapter = VobizTelephonyAdapter(config)
    adapter._active_calls["CA1"] = {"session_id": "s1"}
    await adapter.teardown("CA1")
    assert "CA1" not in adapter._active_calls


@pytest.mark.asyncio
async def test_handle_call_empty_transcript_skips_ac(config):
    """Empty STT result skips Agent Core — no LLM call for silence."""
    ws = _make_ws([_start_msg(), _media_msg(), _stop_msg()])

    with patch("src.telephony_adapter.RayaSTTService") as MockSTT, \
         patch("src.telephony_adapter.AgentCoreLLMService") as MockAC, \
         patch("src.telephony_adapter.RayaTTSService") as MockTTS:

        MockSTT.return_value.transcribe = AsyncMock(return_value="")
        MockAC.return_value.process_turn = AsyncMock()
        MockTTS.return_value.synthesize = AsyncMock(return_value=b"")

        adapter = VobizTelephonyAdapter(config)
        await adapter.handle_call("CA1", "+91999", ws)

    MockAC.return_value.process_turn.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_telephony_adapter.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `telephony_adapter/src/telephony_adapter.py`**

```python
"""
telephony_adapter/src/telephony_adapter.py

VobizTelephonyAdapter — drives the per-call audio turn loop.

Implements TelephonyAdapterBase. Reads WebSocket messages from Vobiz,
buffers audio, calls Raya STT on utterance end (stop event), calls Agent Core
for the response, synthesises audio via Raya TTS, and sends audio back to Vobiz.
One instance is shared across all concurrent calls; per-call state is in _active_calls.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time
import uuid

from src.agent_core_service import AgentCoreLLMService
from src.base import TelephonyAdapterBase, TelephonyError
from src.raya_stt_service import RayaSTTService
from src.raya_tts_service import RayaTTSService
from src.vobiz_serializer import VobizFrameSerializer

logger = logging.getLogger(__name__)


class VobizTelephonyAdapter(TelephonyAdapterBase):
    """Handles the full lifecycle of inbound and outbound Vobiz calls.

    One instance serves all concurrent calls. Per-call state (session_id, audio buffer)
    lives in _active_calls keyed by call_sid. All service instances are created once at
    construction and are shared across calls.

    Args:
        config: Full merged config dict. Reads telephony_adapter section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        self._config = config
        self._serializer = VobizFrameSerializer()
        self._stt = RayaSTTService(config)
        self._tts = RayaTTSService(config)
        self._ac = AgentCoreLLMService(config)
        self._active_calls: dict[str, dict] = {}

    async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
        """Handle the full lifecycle of a Vobiz call over a WebSocket connection.

        Reads WebSocket messages in order:
        - "start": already parsed (call_sid + caller_id provided by server.py)
        - "media": accumulate audio bytes
        - "stop": transcribe accumulated audio, call Agent Core, synthesise TTS,
                  send audio back, close if escalated

        Empty transcripts (silence) are discarded without calling Agent Core.
        On escalation, closes the WebSocket after delivering the final response.

        Args:
            call_sid: Unique Vobiz call identifier.
            caller_id: Caller phone number (opaque — not forwarded to Agent Core).
            websocket: Active WebSocket connection from FastAPI.

        Raises:
            TelephonyError: If the WebSocket cannot be read.
        """
        session_id = str(uuid.uuid4())
        self._active_calls[call_sid] = {"session_id": session_id}
        audio_buffer: list[bytes] = []

        logger.info(
            "telephony_adapter.call_start",
            extra={
                "operation": "telephony_adapter.handle_call",
                "status": "success",
                "call_sid": call_sid,
                "session_id": session_id,
            },
        )

        try:
            async for message in websocket:
                try:
                    import json as _json
                    event = _json.loads(message).get("event", "")
                except Exception:
                    continue

                if event == "media":
                    try:
                        chunk = self._serializer.parse_media(message)
                        if chunk:
                            audio_buffer.append(chunk)
                    except ValueError:
                        pass

                elif event == "stop":
                    if not audio_buffer:
                        break

                    audio = b"".join(audio_buffer)
                    audio_buffer = []

                    transcript = await self._stt.transcribe(audio)
                    if not transcript or not transcript.strip():
                        logger.info(
                            "telephony_adapter.empty_transcript",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "skipped",
                                "call_sid": call_sid,
                            },
                        )
                        break

                    ac_result = await self._ac.process_turn(
                        session_id=session_id,
                        user_message=transcript,
                        call_sid=call_sid,
                        caller_id=caller_id,
                    )

                    audio_out = await self._tts.synthesize(ac_result.response_text)
                    if audio_out:
                        stream_sid = self._active_calls[call_sid].get("stream_sid", "")
                        out_msg = self._serializer.build_media_message(stream_sid, audio_out)
                        await websocket.send(out_msg)

                    if ac_result.was_escalated:
                        logger.info(
                            "telephony_adapter.escalated",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "success",
                                "call_sid": call_sid,
                            },
                        )
                        await websocket.close()
                        break

                elif event == "start":
                    try:
                        metadata = self._serializer.parse_start(message)
                        self._active_calls[call_sid]["stream_sid"] = metadata.stream_sid
                    except ValueError:
                        pass

        except Exception as e:
            logger.error(
                "telephony_adapter.error",
                extra={
                    "operation": "telephony_adapter.handle_call",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
        finally:
            await self.teardown(call_sid)

    async def teardown(self, call_sid: str) -> None:
        """Release resources for a completed or dropped call.

        Args:
            call_sid: The call SID whose state should be removed.
        """
        self._active_calls.pop(call_sid, None)
        logger.info(
            "telephony_adapter.teardown",
            extra={
                "operation": "telephony_adapter.teardown",
                "status": "success",
                "call_sid": call_sid,
            },
        )
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_telephony_adapter.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/src/telephony_adapter.py telephony_adapter/tests/test_telephony_adapter.py
git commit -m "feat(telephony): add VobizTelephonyAdapter turn loop"
```

---

## Task 10: FastAPI server

**Files:**
- Create: `telephony_adapter/server.py`
- Create: `telephony_adapter/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

```python
# telephony_adapter/tests/test_server.py
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("server.VobizTelephonyAdapter"), \
         patch("server.CampaignManager"), \
         patch("server.load_config", return_value={
             "telephony_adapter": {
                 "port": 8006,
                 "public_url": "https://example.app",
                 "vobiz": {"auth_id": "MA1", "auth_token": "t", "api_base": "https://api.vobiz.ai/api/v1", "from_number": "+91"},
                 "raya": {"api_key": "k", "stt_wss_url": "wss://...", "tts_base_url": "https://...", "language": "hi", "voice_id": "v1", "tts_speed": 1.0},
                 "agent_core": {"base_url": "http://agent_core:8000", "timeout_ms": 5000, "fallback_phrase": "sorry"},
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
    resp = client.post("/recording-finished", json={"callSid": "CA1", "recordingUrl": "https://..."})
    assert resp.status_code == 200


def test_recording_ready_returns_200(client):
    resp = client.post("/recording-ready", json={"callSid": "CA1", "recordingUrl": "https://..."})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd telephony_adapter
uv run pytest tests/test_server.py -v
```

Expected: `ImportError` — `server.py` not found.

- [ ] **Step 3: Create `telephony_adapter/server.py`**

```python
"""
telephony_adapter/server.py

FastAPI application for the Telephony Adapter DPG service.

Endpoints:
  POST /answer              — Vobiz webhook on call answered; returns XML with WebSocket URL.
  WebSocket /ws/{call_sid}  — Bidirectional audio stream per call.
  POST /campaign            — Trigger outbound call (also callable by Action Gateway).
  POST /recording-finished  — Vobiz webhook: recording stopped.
  POST /recording-ready     — Vobiz webhook: recording MP3 ready.
  GET  /health              — Liveness probe.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from config_loader import load_config
from dpg_telemetry import init_otel
from src.campaign_manager import CampaignManager
from src.telephony_adapter import VobizTelephonyAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons (set by create_app)
# ---------------------------------------------------------------------------
_adapter: VobizTelephonyAdapter | None = None
_campaign_manager: CampaignManager | None = None


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
    global _adapter, _campaign_manager

    if config is None:
        dpg_path = os.getenv("DPG_CONFIG_PATH", "config/telephony.yaml")
        domain_path = os.getenv("DOMAIN_CONFIG_PATH", "../dev-kit/configs/kkb/telephony_adapter.yaml")
        config = load_config(dpg_path, domain_path)

    init_otel("telephony_adapter", config)

    _adapter = VobizTelephonyAdapter(config)
    _campaign_manager = CampaignManager(config)

    public_url = config.get("telephony_adapter", {}).get("public_url", "")

    app = FastAPI(
        title="Telephony Adapter",
        description="DPG Reach Layer telephony channel adapter — Vobiz + Raya + Agent Core.",
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/answer")
    async def answer(request: Request):
        """Handle Vobiz call-answered webhook. Returns XML with WebSocket stream URL.

        Vobiz POSTs form fields: CallSid, From, To, etc.
        We return an XML response that tells Vobiz to open a WebSocket to /ws/{call_sid}.
        """
        form = await request.form()
        call_sid = form.get("CallSid", "unknown")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Stream url="{public_url}/ws/{call_sid}" bidirectional="true"/>
</Response>"""
        return Response(content=xml, media_type="application/xml")

    @app.websocket("/ws/{call_sid}")
    async def websocket_endpoint(websocket: WebSocket, call_sid: str):
        """Bidirectional audio stream for an active call.

        Vobiz connects here after receiving the XML from /answer.
        The first message over the WebSocket is the "start" event with call metadata.
        """
        await websocket.accept()
        caller_id = "unknown"

        # Peek at the first message to extract caller_id from the start event.
        try:
            first_msg = await websocket.receive_text()
            import json
            data = json.loads(first_msg)
            if data.get("event") == "start":
                caller_id = (
                    data.get("start", {})
                    .get("customParameters", {})
                    .get("caller_id", "unknown")
                )
        except Exception:
            pass

        logger.info(
            "server.ws_connected",
            extra={
                "operation": "server.websocket_endpoint",
                "status": "success",
                "call_sid": call_sid,
            },
        )

        # Re-inject the start message by wrapping websocket so the adapter sees it.
        class _PrefixedWebSocket:
            """Wraps a WebSocket to prepend one already-consumed message."""
            def __init__(self, ws, prefixed: str):
                self._ws = ws
                self._prefix = prefixed
                self._sent = False

            async def __aiter__(self):
                if not self._sent:
                    self._sent = True
                    yield self._prefix
                async for msg in self._ws.iter_text():
                    yield msg

            async def send(self, data: str):
                await self._ws.send_text(data)

            async def close(self):
                await self._ws.close()

        wrapped = _PrefixedWebSocket(websocket, first_msg)
        await _adapter.handle_call(call_sid, caller_id, wrapped)

    @app.post("/campaign")
    async def campaign(body: CampaignRequest):
        """Trigger an outbound call to the given number.

        Called directly by operators or by Action Gateway as a connector tool
        (telephony_channel_switch) when Agent Core decides to switch channels.

        Raises:
            HTTPException 400: If to_number is empty.
        """
        from fastapi import HTTPException
        if not body.to_number or not body.to_number.strip():
            raise HTTPException(status_code=400, detail="to_number must not be empty")
        result = await _campaign_manager.initiate_call(to_number=body.to_number)
        return result

    @app.post("/recording-finished")
    async def recording_finished(body: RecordingWebhook):
        """Vobiz webhook: recording has stopped."""
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
    async def recording_ready(body: RecordingWebhook):
        """Vobiz webhook: recording MP3 is ready."""
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


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    _app = create_app()
    port = int(os.getenv("PORT", "8006"))
    uvicorn.run(_app, host="0.0.0.0", port=port)
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/test_server.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add telephony_adapter/server.py telephony_adapter/tests/test_server.py
git commit -m "feat(telephony): add FastAPI server with /answer, /ws, /campaign endpoints"
```

---

## Task 11: OTel instrumentation

**Files:**
- Modify: `telephony_adapter/src/raya_stt_service.py`
- Modify: `telephony_adapter/src/raya_tts_service.py`
- Modify: `telephony_adapter/src/agent_core_service.py`
- Modify: `telephony_adapter/src/telephony_adapter.py`

Add OpenTelemetry spans and metrics to all service calls. This task adds spans around external calls and metrics for latency, active calls, and campaign counts.

- [ ] **Step 1: Add OTel spans to `raya_stt_service.py`**

At the top of `RayaSTTService.transcribe()`, wrap the operation in a span. Add these lines after the `start = time.time()` line in `transcribe()`:

```python
from opentelemetry import trace as _otel_trace
_tracer = _otel_trace.get_tracer("telephony_adapter")

# Inside transcribe(), wrap the retry loop:
with _tracer.start_as_current_span("telephony.stt") as span:
    span.set_attribute("language", self._language)
    # ... existing retry loop ...
    span.set_attribute("status", "success")
```

Full updated `transcribe()` method (replace existing):

```python
async def transcribe(self, audio: bytes) -> str:
    """Transcribe raw audio bytes to text via the Raya WebSocket STT API."""
    if not audio:
        return ""

    from opentelemetry import trace as _otel_trace
    tracer = _otel_trace.get_tracer("telephony_adapter")
    start = time.time()
    last_error: Exception | None = None

    with tracer.start_as_current_span("telephony.stt") as span:
        span.set_attribute("language", self._language)
        for attempt in range(self._max_retries):
            try:
                transcript = await self._call_raya_wss(audio)
                latency = int((time.time() - start) * 1000)
                span.set_attribute("status", "success")
                span.set_attribute("latency_ms", latency)
                logger.info(
                    "raya_stt.transcribe",
                    extra={
                        "operation": "raya_stt_service.transcribe",
                        "status": "success",
                        "latency_ms": latency,
                        "language": self._language,
                    },
                )
                return transcript
            except Exception as e:
                last_error = e
                logger.warning(
                    "raya_stt.retry",
                    extra={
                        "operation": "raya_stt_service.transcribe",
                        "status": "failure",
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        latency = int((time.time() - start) * 1000)
        span.set_attribute("status", "failure")
        span.set_attribute("latency_ms", latency)
        logger.error(
            "raya_stt.failed",
            extra={
                "operation": "raya_stt_service.transcribe",
                "status": "failure",
                "latency_ms": latency,
                "error": str(last_error),
            },
        )
        raise Exception(f"STT transcription failed after {self._max_retries} attempts: {last_error}")
```

- [ ] **Step 2: Add OTel spans to `raya_tts_service.py`**

Wrap the HTTP call in `synthesize()` in a `telephony.tts` span. Replace the `try:` block in `synthesize()`:

```python
async def synthesize(self, text: str) -> bytes:
    """Convert text to audio bytes via the Raya SSE streaming TTS API."""
    if not text or not text.strip():
        return b""

    from opentelemetry import trace as _otel_trace
    tracer = _otel_trace.get_tracer("telephony_adapter")
    start = time.time()
    url = f"{self._base_url}/text-to-speech/stream"
    payload = {
        "text": text,
        "voice_id": self._voice_id,
        "language": self._language,
        "speed": self._speed,
    }
    headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

    with tracer.start_as_current_span("telephony.tts") as span:
        span.set_attribute("voice_id", self._voice_id)
        span.set_attribute("language", self._language)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code != 200:
                span.set_attribute("status", "failure")
                raise Exception(f"TTS synthesis failed: HTTP {response.status_code} — {response.text[:200]}")

            audio_chunks: list[bytes] = []
            for line in response.text.splitlines():
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
                    audio_chunks.append(base64.b64decode(chunk_data["data"]))

            result = b"".join(audio_chunks)
            latency = int((time.time() - start) * 1000)
            span.set_attribute("status", "success")
            span.set_attribute("latency_ms", latency)
            span.set_attribute("audio_bytes", len(result))
            logger.info(
                "raya_tts.synthesize",
                extra={
                    "operation": "raya_tts_service.synthesize",
                    "status": "success",
                    "latency_ms": latency,
                    "audio_bytes": len(result),
                },
            )
            return result

        except Exception as e:
            if "TTS synthesis failed" in str(e):
                raise
            latency = int((time.time() - start) * 1000)
            span.set_attribute("status", "failure")
            logger.error(
                "raya_tts.error",
                extra={
                    "operation": "raya_tts_service.synthesize",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": latency,
                },
            )
            raise Exception(f"TTS synthesis failed: {e}") from e
```

- [ ] **Step 3: Add OTel span to `agent_core_service.py`**

Wrap the `process_turn()` HTTP call in a `telephony.agent_core_call` span. Replace the body of `process_turn()` with:

```python
async def process_turn(self, session_id, user_message, call_sid, caller_id) -> AgentCoreTurnResult:
    """Submit one utterance to Agent Core and return the response."""
    from opentelemetry import trace as _otel_trace
    tracer = _otel_trace.get_tracer("telephony_adapter")
    start = time.time()
    url = f"{self._base_url}/process_turn"
    payload = {
        "session_id": session_id,
        "user_message": user_message,
        "channel": "telephony",
        "user_id": call_sid,
        "timestamp_ms": int(start * 1000),
    }

    with tracer.start_as_current_span("telephony.agent_core_call") as span:
        span.set_attribute("session_id", session_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)

            if response.status_code != 200:
                span.set_attribute("status", "failure")
                logger.error(
                    "agent_core_service.http_error",
                    extra={
                        "operation": "agent_core_service.process_turn",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return self._fallback(session_id)

            data = response.json()
            latency = int((time.time() - start) * 1000)
            span.set_attribute("status", "success")
            span.set_attribute("latency_ms", latency)
            span.set_attribute("was_escalated", data.get("was_escalated", False))
            logger.info(
                "agent_core_service.success",
                extra={
                    "operation": "agent_core_service.process_turn",
                    "status": "success",
                    "latency_ms": latency,
                    "was_escalated": data.get("was_escalated", False),
                },
            )
            return AgentCoreTurnResult(
                session_id=data.get("session_id", session_id),
                response_text=data.get("response_text", self._fallback_phrase),
                was_escalated=data.get("was_escalated", False),
                was_tool_used=data.get("was_tool_used", False),
                model_used=data.get("model_used", ""),
                latency_ms=data.get("latency_ms", 0),
            )

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            span.set_attribute("status", "failure")
            logger.error(
                "agent_core_service.timeout",
                extra={
                    "operation": "agent_core_service.process_turn",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return self._fallback(session_id)
```

- [ ] **Step 4: Add per-call span + active_calls metric to `telephony_adapter.py`**

Add at the top of `handle_call()` (after session_id assignment):

```python
from opentelemetry import trace as _otel_trace, metrics as _otel_metrics
tracer = _otel_trace.get_tracer("telephony_adapter")
meter = _otel_metrics.get_meter("telephony_adapter")
active_calls_gauge = meter.create_up_down_counter(
    "telephony.active_calls",
    description="Number of concurrent active calls",
)
turn_latency = meter.create_histogram(
    "telephony.turn.latency_ms",
    description="End-to-end per-turn latency in milliseconds",
)
active_calls_gauge.add(1)

# Inside the "stop" event handler, wrap the turn in a span:
with tracer.start_as_current_span("telephony.turn") as span:
    span.set_attribute("session_id", session_id)
    span.set_attribute("call_sid", call_sid)
    turn_start = time.time()
    # ... STT, AC, TTS calls ...
    turn_latency.record(int((time.time() - turn_start) * 1000))

# In the finally block, decrement the gauge:
active_calls_gauge.add(-1)
```

Full updated `handle_call()` (replace existing method body):

```python
async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
    """Handle the full lifecycle of a Vobiz call over a WebSocket connection."""
    from opentelemetry import trace as _otel_trace, metrics as _otel_metrics
    tracer = _otel_trace.get_tracer("telephony_adapter")
    meter = _otel_metrics.get_meter("telephony_adapter")
    active_calls_gauge = meter.create_up_down_counter(
        "telephony.active_calls",
        description="Number of concurrent active calls",
    )
    turn_latency_hist = meter.create_histogram(
        "telephony.turn.latency_ms",
        description="End-to-end per-turn latency in milliseconds",
    )

    session_id = str(uuid.uuid4())
    self._active_calls[call_sid] = {"session_id": session_id}
    audio_buffer: list[bytes] = []
    active_calls_gauge.add(1)

    logger.info(
        "telephony_adapter.call_start",
        extra={
            "operation": "telephony_adapter.handle_call",
            "status": "success",
            "call_sid": call_sid,
            "session_id": session_id,
        },
    )

    try:
        async for message in websocket:
            try:
                import json as _json
                event = _json.loads(message).get("event", "")
            except Exception:
                continue

            if event == "media":
                try:
                    chunk = self._serializer.parse_media(message)
                    if chunk:
                        audio_buffer.append(chunk)
                except ValueError:
                    pass

            elif event == "stop":
                if not audio_buffer:
                    break

                audio = b"".join(audio_buffer)
                audio_buffer = []

                with tracer.start_as_current_span("telephony.turn") as span:
                    span.set_attribute("session_id", session_id)
                    span.set_attribute("call_sid", call_sid)
                    turn_start = time.time()

                    transcript = await self._stt.transcribe(audio)
                    if not transcript or not transcript.strip():
                        logger.info(
                            "telephony_adapter.empty_transcript",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "skipped",
                                "call_sid": call_sid,
                            },
                        )
                        span.set_attribute("status", "skipped")
                        break

                    ac_result = await self._ac.process_turn(
                        session_id=session_id,
                        user_message=transcript,
                        call_sid=call_sid,
                        caller_id=caller_id,
                    )

                    audio_out = await self._tts.synthesize(ac_result.response_text)
                    if audio_out:
                        stream_sid = self._active_calls[call_sid].get("stream_sid", "")
                        out_msg = self._serializer.build_media_message(stream_sid, audio_out)
                        await websocket.send(out_msg)

                    turn_ms = int((time.time() - turn_start) * 1000)
                    turn_latency_hist.record(turn_ms)
                    span.set_attribute("latency_ms", turn_ms)
                    span.set_attribute("was_escalated", ac_result.was_escalated)
                    span.set_attribute("status", "success")

                if ac_result.was_escalated:
                    logger.info(
                        "telephony_adapter.escalated",
                        extra={
                            "operation": "telephony_adapter.handle_call",
                            "status": "success",
                            "call_sid": call_sid,
                        },
                    )
                    await websocket.close()
                    break

            elif event == "start":
                try:
                    metadata = self._serializer.parse_start(message)
                    self._active_calls[call_sid]["stream_sid"] = metadata.stream_sid
                except ValueError:
                    pass

    except Exception as e:
        logger.error(
            "telephony_adapter.error",
            extra={
                "operation": "telephony_adapter.handle_call",
                "status": "failure",
                "call_sid": call_sid,
                "error": f"{type(e).__name__}: {e}",
            },
        )
    finally:
        active_calls_gauge.add(-1)
        await self.teardown(call_sid)
```

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

```bash
cd telephony_adapter
uv run pytest --cov=src --cov-report=term-missing -v
```

Expected: all tests PASS, coverage ≥ 70%.

- [ ] **Step 6: Commit**

```bash
git add telephony_adapter/src/
git commit -m "feat(telephony): add OTel spans and metrics to all services"
```

---

## Task 12: Dockerfile

**Files:**
- Create: `telephony_adapter/Dockerfile`

- [ ] **Step 1: Create `telephony_adapter/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy observability_layer first (local dep)
COPY observability_layer/ /observability_layer/

# Copy project files
COPY telephony_adapter/ /app/

# Install dependencies
RUN uv sync --no-dev

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
```

Note: The Dockerfile must be built from the repo root to include `observability_layer/` as a local path dep:
```bash
docker build -f telephony_adapter/Dockerfile -t telephony_adapter .
```

- [ ] **Step 2: Commit**

```bash
git add telephony_adapter/Dockerfile
git commit -m "feat(telephony): add Dockerfile"
```

---

## Task 13: Final coverage check and PR

- [ ] **Step 1: Run full test suite with coverage**

```bash
cd telephony_adapter
uv run pytest --cov=src --cov-report=term-missing -v
```

Expected: all tests pass, coverage ≥ 70% across `src/`.

- [ ] **Step 2: Check all acceptance criteria from GH-53**

Manually verify:
- [ ] `channel: "telephony"` is set in `agent_core_service.py` `process_turn()` payload ✓
- [ ] `caller_id` (phone number) is never in any `logger.*` call (only `call_sid` is) ✓
- [ ] Trust Layer runs on every turn — this is handled by Agent Core, not the adapter ✓
- [ ] Escalation closes the WebSocket (`websocket.close()` on `was_escalated=True`) ✓
- [ ] `POST /campaign` exposed for Action Gateway ✓
- [ ] Tests cover inbound flow, turn exchange, STT/TTS boundary, call-end cleanup ✓

- [ ] **Step 3: Commit any final fixups**

```bash
git add -A
git commit -m "test(telephony): final coverage pass for GH-53"
```

- [ ] **Step 4: Update ARCHITECTURE.md — add telephony_adapter port and status**

In `ARCHITECTURE.md`, under the Ports table (Section 1), add:
```
| Telephony Adapter | 8006 |
```

Under Reach Layer (Section 3), update the channel table:
```
| Voice / VOIP (Vobiz) | 🟡 | VobizTelephonyAdapter — Pipecat pipeline, Raya STT/TTS, Agent Core HTTP |
```

- [ ] **Step 5: Commit ARCHITECTURE.md**

```bash
git add ARCHITECTURE.md
git commit -m "docs: update ARCHITECTURE.md with telephony_adapter port and status"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `telephony_adapter/` separate service | Task 1 |
| `TelephonyAdapterBase` ABC before concrete | Task 2 |
| Config-driven, no hardcoded creds | Tasks 1, 3 |
| `VobizFrameSerializer` | Task 4 |
| `RayaSTTService` WebSocket STT + retry | Task 5 |
| `RayaTTSService` SSE streaming TTS | Task 6 |
| `AgentCoreLLMService` HTTP `/process_turn` + fallback | Task 7 |
| `CampaignManager` outbound + 429 retry | Task 8 |
| `VobizTelephonyAdapter` turn loop | Task 9 |
| FastAPI `/answer`, `/ws`, `/campaign`, `/recording-*`, `/health` | Task 10 |
| OTel instrumentation via `dpg_telemetry` (spans + metrics) | Task 11 |
| Port 8006 | Task 1 |
| Docker | Task 12 |
| Empty transcript skips Agent Core | Task 9 |
| Escalation closes WebSocket | Task 9 |
| PII (caller phone) never logged/forwarded | Tasks 7, 9 |
| Action Gateway can call `/campaign` | Task 10 |
| ARCHITECTURE.md update | Task 13 |

**Placeholder scan:** None found.

**Type consistency:**
- `AgentCoreTurnResult` defined in `agent_core_service.py`, imported in `test_telephony_adapter.py` ✓
- `VobizCallMetadata` defined in `vobiz_serializer.py`, referenced in test ✓
- `TelephonyAdapterBase` methods `handle_call(call_sid, caller_id, websocket)` and `teardown(call_sid)` — both implemented in `VobizTelephonyAdapter` ✓
