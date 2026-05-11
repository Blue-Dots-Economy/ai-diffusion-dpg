# Voice Call Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-call audio recording to the Reach Layer voice channel (Vobiz), gated by Trust Layer consent, with pluggable sources (Vobiz native + Pipecat tap) and stores (local + S3), full OTel/Observability telemetry, and dev-kit schema/wizard support.

**Architecture:** A new `recordings/` subpackage under `reach_layer/voice/src/` hosts three ABCs (`RecordingManagerBase`, `RecordingSourceBase`, `RecordingStoreBase`) plus a factory. `TelephonyAdapterBase` exposes the manager via an abstract property. `VobizAdapter` constructs the manager, splices any required Pipecat processors into the pipeline, listens for a new `ConsentEvent(purpose="recording")` over the existing SSE stream, and runs `finalize()` in a background task from `teardown()`. dev-kit Pydantic schemas and the Configuration Agent gain matching support so the new `recording:` YAML block validates and is reachable from the wizard.

**Tech Stack:** Python 3.12, Pipecat, FastAPI, aiohttp, aiobotocore, OpenTelemetry, Pydantic v2, pytest + aioresponses + InMemorySpanExporter.

**Spec:** `docs/superpowers/specs/2026-05-08-voice-call-recording-design.md`. Issue: #322. Branch: `322-voice-call-recording`.

**Working directory for python tests:** each module owns its own `uv` env. Run tests inside that module's directory using `uv run pytest`.

---

## File Structure

| File | Responsibility |
|---|---|
| `reach_layer/base/events.py` | + `ConsentEvent` dataclass |
| `reach_layer/base/reach_layer_base.py` | `_parse_sse_event` recognises `type="consent"` |
| `agent_core/src/orchestrator.py` | Emit `ConsentEvent(purpose, granted, ts)` after `/consent/verify` succeeds for recording purpose |
| `dev-kit/dev_kit/schemas/dpg/reach_layer.py` | + `RecordingDpg` family attached to `VoiceDpg` |
| `dev-kit/dev_kit/schemas/cross_block_validation.py` | + salt and S3 bucket required-when-enabled rules |
| `reach_layer/voice/src/recordings/manager_base.py` | `RecordingManagerBase`, `RecordingArtifact`, `RecordingPayload` |
| `reach_layer/voice/src/recordings/manager.py` | `RecordingManager`, `NullRecordingManager` |
| `reach_layer/voice/src/recordings/factory.py` | `build_recording_manager(config, telephony=...)` |
| `reach_layer/voice/src/recordings/sources/source_base.py` | `RecordingSourceBase` |
| `reach_layer/voice/src/recordings/sources/pipeline_source.py` | `PipelineRecordingSource` (owns the tap processor) |
| `reach_layer/voice/src/recordings/sources/vobiz_source.py` | `VobizRecordingSource` (REST start/stop, fetches MP3) |
| `reach_layer/voice/src/recordings/stores/store_base.py` | `RecordingStoreBase` |
| `reach_layer/voice/src/recordings/stores/local_store.py` | `LocalFileStore` |
| `reach_layer/voice/src/recordings/stores/s3_store.py` | `S3Store` (aiobotocore) |
| `reach_layer/voice/src/recordings/telemetry.py` | OTel span helpers + Observability signal emitter |
| `reach_layer/voice/src/pipecat_services/recording_tap.py` | `RecordingTapProcessor` |
| `reach_layer/voice/src/base.py` | + `recording_manager` abstract property |
| `reach_layer/voice/src/vobiz_adapter.py` | Construct manager; splice processors; listen for ConsentEvent; spawn `_finalize_and_store` in `teardown` |
| `reach_layer/voice/server.py` | Rewire `/recording-ready` to resolve registered future; expose `_recording_url_registry` |
| `dev-kit/dpg/reach_layer.yaml` | + `recording:` block (defaults: `source: disabled`) |
| `dev-kit/dev_kit/agent/{accumulator.py, renderer.py, tools.py}` | Wizard wiring for recording |

Tests mirror source paths under each module's `tests/` directory.

---

## Conventions used in this plan

- All commits include `Refs #322` and the standard `Co-Authored-By` trailer (use HEREDOC).
- All commits target branch `322-voice-call-recording`. Verify branch with `git branch --show-current` before each commit.
- Run tests from the module directory: `cd reach_layer/voice && uv run pytest tests/...`. Never run pytest from repo root.
- Each task ends with a passing test suite and one focused commit.
- File paths in this plan are repo-relative.

---

### Task 1: Add `ConsentEvent` to reach_layer_base

**Files:**
- Modify: `reach_layer/base/events.py`
- Modify: `reach_layer/base/reach_layer_base.py` (`_parse_sse_event` around line 393)
- Modify: `reach_layer/base/__init__.py` (export `ConsentEvent`)
- Test: `reach_layer/base/tests/test_events_consent.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# reach_layer/base/tests/test_events_consent.py
"""Tests for ConsentEvent SSE parsing."""
from __future__ import annotations

import json

from reach_layer_base import ConsentEvent
from reach_layer_base.reach_layer_base import ReachLayerBase


def _parse(payload: dict) -> object | None:
    return ReachLayerBase._parse_sse_event(f"data: {json.dumps(payload)}")


def test_consent_event_parsed_with_required_fields():
    evt = _parse({
        "type": "consent",
        "purpose": "recording",
        "granted": True,
        "consent_granted_ts": 1746748800.123,
        "turn_id": "t-1",
    })
    assert isinstance(evt, ConsentEvent)
    assert evt.purpose == "recording"
    assert evt.granted is True
    assert evt.consent_granted_ts == 1746748800.123
    assert evt.turn_id == "t-1"


def test_consent_event_defaults_when_optional_fields_missing():
    evt = _parse({"type": "consent", "purpose": "recording", "granted": False})
    assert isinstance(evt, ConsentEvent)
    assert evt.granted is False
    assert evt.consent_granted_ts == 0.0
    assert evt.turn_id == ""


def test_unknown_event_type_returns_none():
    assert _parse({"type": "mystery", "purpose": "x"}) is None
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd reach_layer/base && uv run pytest tests/test_events_consent.py -v
```
Expected: FAIL — `ImportError: cannot import name 'ConsentEvent'`.

- [ ] **Step 3: Add `ConsentEvent` dataclass**

In `reach_layer/base/events.py`, append:

```python
@dataclass
class ConsentEvent:
    """Trust Layer consent decision streamed back to Reach Layer.

    Emitted by Agent Core after ``/consent/verify`` succeeds for a tracked
    purpose. Reach Layer adapters use it to gate channel-side behaviour
    (e.g. start the call recorder).
    """

    purpose: str
    granted: bool
    consent_granted_ts: float = 0.0
    turn_id: str = ""
    type: str = "consent"


# Update the StreamEvent union
StreamEvent = Union[SignalEvent, SentenceEvent, DoneEvent, ConsentEvent]
```

In `reach_layer/base/__init__.py` add `ConsentEvent` to imports and `__all__`.

- [ ] **Step 4: Extend `_parse_sse_event`**

In `reach_layer/base/reach_layer_base.py`, locate the `if event_type == "signal":` block in `_parse_sse_event` and add a sibling branch:

```python
if event_type == "consent":
    return ConsentEvent(
        purpose=str(data.get("purpose", "")),
        granted=bool(data.get("granted", False)),
        consent_granted_ts=float(data.get("consent_granted_ts", 0.0)),
        turn_id=str(data.get("turn_id", "")),
    )
```

Add `ConsentEvent` to the `from .events import …` line at the top of the file.

- [ ] **Step 5: Run tests, verify passing**

```bash
cd reach_layer/base && uv run pytest tests/test_events_consent.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add reach_layer/base/events.py reach_layer/base/reach_layer_base.py reach_layer/base/__init__.py reach_layer/base/tests/test_events_consent.py
git commit -m "$(cat <<'EOF'
feat(reach-base): add ConsentEvent SSE type (#322)

Adds ConsentEvent(purpose, granted, consent_granted_ts, turn_id) to the
StreamEvent union and wires _parse_sse_event to recognise type=consent.
Channel adapters use this to react to Trust Layer consent decisions
(e.g. start the recorder when purpose=recording is granted).

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Emit `ConsentEvent` from Agent Core consent gate

**Files:**
- Modify: `agent_core/src/orchestrator.py` (consent gate paths around lines 505 and 2790)
- Test: `agent_core/tests/test_orchestrator_consent_event.py` (create)

The orchestrator already has two consent-gate paths (sync + streaming). Both must emit a `consent` SSE event after `verify_consent` returns granted, only when the purpose matches the configured `recording.consent_purpose` (default `"recording"`). For other purposes, no event is emitted (avoids leaking unrelated consent decisions).

- [ ] **Step 1: Write the failing test**

```python
# agent_core/tests/test_orchestrator_consent_event.py
"""Tests that Agent Core emits a 'consent' SSE event after recording consent."""
from __future__ import annotations

import time
import pytest

from src.orchestrator import emit_consent_event_if_recording


def test_emits_event_when_purpose_matches_and_granted():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="recording",
        granted=True,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert len(queue) == 1
    evt = queue[0]
    assert evt["type"] == "consent"
    assert evt["purpose"] == "recording"
    assert evt["granted"] is True
    assert evt["turn_id"] == "t-1"
    assert evt["consent_granted_ts"] > 0


def test_does_not_emit_for_other_purpose():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="data_share",
        granted=True,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert queue == []


def test_does_not_emit_when_not_granted():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="recording",
        granted=False,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert queue == []
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd agent_core && uv run pytest tests/test_orchestrator_consent_event.py -v
```
Expected: FAIL — `ImportError: cannot import name 'emit_consent_event_if_recording'`.

- [ ] **Step 3: Add helper to `orchestrator.py`**

Add at module level (top of `agent_core/src/orchestrator.py`, after imports):

```python
import time as _time_for_consent

def emit_consent_event_if_recording(
    *,
    queue: list,
    purpose: str,
    granted: bool,
    configured_purpose: str,
    turn_id: str,
) -> None:
    """Append a 'consent' SSE event to ``queue`` iff this is the configured
    recording purpose and consent was granted.

    Args:
        queue: Event queue (list of dicts) the SSE writer drains.
        purpose: The consent purpose just verified.
        granted: True iff Trust Layer returned granted.
        configured_purpose: Value of ``reach_layer.channels.voice.recording.consent_purpose``.
        turn_id: Current turn identifier for correlation.
    """
    if not granted:
        return
    if purpose != configured_purpose:
        return
    queue.append({
        "type": "consent",
        "purpose": purpose,
        "granted": True,
        "consent_granted_ts": _time_for_consent.time(),
        "turn_id": turn_id,
    })
```

- [ ] **Step 4: Wire helper into the two consent-gate paths**

Locate each `orchestrator.consent_gate` log site (around lines 505 and 2790). Right after Trust Layer's `verify_consent` returns and **before** logging completion, call:

```python
emit_consent_event_if_recording(
    queue=session_event_queue,           # the SSE queue you already push SignalEvent to
    purpose=consent_purpose_being_checked,
    granted=consent_granted,
    configured_purpose=self._config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("recording", {}).get("consent_purpose", "recording"),
    turn_id=turn_id,
)
```

The exact variable names (`session_event_queue`, `consent_purpose_being_checked`, `consent_granted`, `turn_id`) must match the surrounding code; substitute as appropriate. If your consent gate iterates multiple purposes per turn, wrap the call in the same loop.

- [ ] **Step 5: Run tests, verify passing**

```bash
cd agent_core && uv run pytest tests/test_orchestrator_consent_event.py tests/test_orchestrator.py -v
```
Expected: all passing (existing orchestrator tests must still pass — the helper is additive).

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator_consent_event.py
git commit -m "$(cat <<'EOF'
feat(agent-core): emit ConsentEvent after recording consent granted (#322)

Adds emit_consent_event_if_recording helper and wires it into both
consent-gate paths so a 'consent' SSE event is streamed to the channel
when purpose matches reach_layer.channels.voice.recording.consent_purpose
and the user grants. No event for other purposes or denials.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: dev-kit Pydantic schemas for `recording`

**Files:**
- Modify: `dev-kit/dev_kit/schemas/dpg/reach_layer.py`
- Modify: `dev-kit/dev_kit/schemas/cross_block_validation.py`
- Test: `dev-kit/tests/schemas/dpg/test_dpg_schemas.py`
- Test: `dev-kit/tests/schemas/test_cross_block_validation.py`

- [ ] **Step 1: Write failing schema tests**

Append to `dev-kit/tests/schemas/dpg/test_dpg_schemas.py`:

```python
import pytest
from pydantic import ValidationError
from dev_kit.schemas.dpg.reach_layer import RecordingDpg, VoiceDpg


def test_recording_dpg_defaults_to_disabled():
    rec = RecordingDpg()
    assert rec.source == "disabled"
    assert rec.consent_purpose == "recording"
    assert rec.store.backend == "local"
    assert rec.store.local.base_path == "/var/recordings"


def test_recording_dpg_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RecordingDpg(source="disabled", surprise="x")


def test_recording_source_literal_enforced():
    with pytest.raises(ValidationError):
        RecordingDpg(source="ftp")


def test_voice_dpg_includes_recording_with_default():
    voice_yaml = {
        "agent_core": {
            "endpoint": "http://agent-core:8000",
            "submit_path": "/process_turn",
            "events_path": "/sessions/{session_id}/events",
            "cancel_path": "/sessions/{session_id}/cancel",
            "request_timeout_s": 30,
        },
        "vobiz": {"auth_id": "x", "auth_token": "y", "sample_rate": 8000},
        "raya": {"endpoint": "http://raya:9090"},
        "vad": {"start_secs": 0.2, "stop_secs": 0.6, "min_volume": 0.6},
    }
    voice = VoiceDpg(**voice_yaml)
    assert voice.recording.source == "disabled"
```

Append to `dev-kit/tests/schemas/test_cross_block_validation.py`:

```python
import pytest
from dev_kit.schemas.validation import MergedConfig


BASE_REACH = {
    # minimal valid reach_layer dict — copy from a passing existing test
    # to keep this isolated to the recording rule.
}  # The test author should reuse the helper used by neighbouring tests.


def _reach_with_recording(**overrides):
    rec = {
        "source": "vobiz",
        "consent_purpose": "recording",
        "caller_id_hash_salt": "",
        "store": {
            "backend": "s3",
            "s3": {"bucket": "", "prefix": "rec/", "region": "ap-south-1", "kms_key_id": ""},
        },
    }
    rec.update(overrides)
    # plug into BASE_REACH — exact insertion path:
    # base_reach["channels"]["voice"]["recording"] = rec
    ...


def test_recording_enabled_requires_caller_id_hash_salt(monkeypatch):
    # Build a full MergedConfig with recording enabled and salt empty.
    # Expect MergedConfig.validate_full() to raise ValueError mentioning salt.
    ...


def test_recording_s3_backend_requires_bucket():
    # Build a MergedConfig with backend=s3 and bucket empty.
    # Expect ValueError mentioning bucket.
    ...


def test_recording_disabled_skips_salt_check():
    # source=disabled, salt=empty → must validate OK.
    ...
```

(The test author should fill `BASE_REACH` and `_reach_with_recording` by copying the existing helper used in the neighbouring tests in this file. The pattern is already established.)

- [ ] **Step 2: Run, verify failure**

```bash
cd dev-kit && uv run pytest tests/schemas/dpg/test_dpg_schemas.py tests/schemas/test_cross_block_validation.py -v
```
Expected: FAIL — `ImportError: cannot import name 'RecordingDpg'`.

- [ ] **Step 3: Add Pydantic models**

In `dev-kit/dev_kit/schemas/dpg/reach_layer.py`, after the existing `VoiceObservabilityDpg` class:

```python
from typing import Literal


class RecordingLocalDpg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_path: str = "/var/recordings"


class RecordingS3Dpg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: str = ""
    prefix: str = "recordings/"
    region: str = "ap-south-1"
    kms_key_id: str = ""


class RecordingStoreDpg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["local", "s3"] = "local"
    local: RecordingLocalDpg = Field(default_factory=RecordingLocalDpg)
    s3: RecordingS3Dpg = Field(default_factory=RecordingS3Dpg)


class RecordingDpg(BaseModel):
    """Voice channel recording defaults. Source=disabled means no behaviour change."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["disabled", "vobiz", "pipeline"] = "disabled"
    consent_purpose: str = "recording"
    webhook_timeout_s: float = 30.0
    fetch_timeout_s: float = 60.0
    min_duration_ms: int = 500
    caller_id_hash_salt: str = ""
    store: RecordingStoreDpg = Field(default_factory=RecordingStoreDpg)
```

In `VoiceDpg`, add the field (preserving existing fields):

```python
recording: RecordingDpg = Field(default_factory=RecordingDpg)
```

- [ ] **Step 4: Add cross-block rules**

In `dev-kit/dev_kit/schemas/cross_block_validation.py`, find the existing per-block rule structure and add (preserve existing rules):

```python
def _validate_recording(merged: dict) -> list[str]:
    errors: list[str] = []
    rec = (merged.get("reach_layer", {})
                 .get("channels", {})
                 .get("voice", {})
                 .get("recording", {}))
    if rec.get("source", "disabled") == "disabled":
        return errors
    if not rec.get("caller_id_hash_salt"):
        errors.append(
            "reach_layer.channels.voice.recording.caller_id_hash_salt must be set "
            "when recording.source is enabled"
        )
    store = rec.get("store", {})
    if store.get("backend") == "s3" and not store.get("s3", {}).get("bucket"):
        errors.append(
            "reach_layer.channels.voice.recording.store.s3.bucket must be set "
            "when store.backend == 's3'"
        )
    return errors
```

Wire `_validate_recording` into the existing `validate_cross_block` aggregator (follow the pattern of neighbouring `_validate_*` calls).

- [ ] **Step 5: Run tests, verify passing**

```bash
cd dev-kit && uv run pytest tests/schemas/ -v
```
Expected: all passing, including pre-existing schema tests (no regression).

- [ ] **Step 6: Commit**

```bash
git add dev-kit/dev_kit/schemas/ dev-kit/tests/schemas/
git commit -m "$(cat <<'EOF'
feat(devkit): pydantic schema for voice recording config (#322)

Adds RecordingDpg / RecordingStoreDpg / RecordingLocalDpg / RecordingS3Dpg
under VoiceDpg with extra=forbid and safe defaults (source=disabled).
Adds cross-block rules requiring caller_id_hash_salt when enabled and
s3.bucket when backend=s3.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `RecordingManagerBase` ABC + dataclasses + `NullRecordingManager`

**Files:**
- Create: `reach_layer/voice/src/recordings/__init__.py` (empty)
- Create: `reach_layer/voice/src/recordings/manager_base.py`
- Create: `reach_layer/voice/src/recordings/manager.py` (only `NullRecordingManager` for now; full `RecordingManager` lands in Task 9)
- Test: `reach_layer/voice/tests/recordings/__init__.py` (empty)
- Test: `reach_layer/voice/tests/recordings/test_manager_base.py`

- [ ] **Step 1: Write the failing test**

```python
# reach_layer/voice/tests/recordings/test_manager_base.py
"""Contract tests for RecordingManagerBase + NullRecordingManager."""
from __future__ import annotations

import pytest

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingPayload,
)
from src.recordings.manager import NullRecordingManager


def test_recording_manager_base_is_abstract():
    with pytest.raises(TypeError):
        RecordingManagerBase()  # type: ignore[abstract]


def test_recording_payload_carries_either_bytes_or_url():
    p1 = RecordingPayload(bytes_data=b"x")
    assert p1.bytes_data == b"x"
    assert p1.fetch_url is None
    p2 = RecordingPayload(fetch_url="https://x")
    assert p2.fetch_url == "https://x"
    assert p2.bytes_data is None


def test_recording_artifact_is_a_dataclass_with_required_fields():
    art = RecordingArtifact(
        call_sid="CA1",
        session_id="s",
        caller_id_hash="h",
        start_ts=1.0,
        end_ts=2.0,
        duration_ms=1000,
        consent_granted_ts=0.5,
        source="vobiz",
        format="mp3",
        sha256="abc",
        payload=RecordingPayload(bytes_data=b"x"),
    )
    assert art.duration_ms == 1000


@pytest.mark.asyncio
async def test_null_recording_manager_idle_forever():
    m = NullRecordingManager()
    assert m.state == "idle"
    await m.start(consent_granted_ts=1.0)
    assert m.state == "idle"
    await m.stop()
    assert m.state == "idle"
    assert await m.finalize() is None
    assert m.pipeline_processors == []
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_manager_base.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.recordings'`.

- [ ] **Step 3: Implement `manager_base.py`**

```python
# reach_layer/voice/src/recordings/manager_base.py
"""RecordingManagerBase — ABC for per-call recording lifecycle.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

RecordingState = Literal["idle", "recording", "stopped", "finalized", "failed"]


@dataclass
class RecordingPayload:
    """Carries either in-memory audio bytes or a URL the store must fetch."""

    bytes_data: Optional[bytes] = None
    fetch_url: Optional[str] = None


@dataclass
class RecordingArtifact:
    """Audit metadata + payload reference for a single recorded call."""

    call_sid: str
    session_id: str
    caller_id_hash: str
    start_ts: float
    end_ts: float
    duration_ms: int
    consent_granted_ts: float
    source: Literal["vobiz", "pipeline"]
    format: Literal["mp3", "wav"]
    sha256: str
    payload: RecordingPayload
    extra: dict = field(default_factory=dict)


class RecordingManagerBase(ABC):
    """Per-call recording lifecycle: idle → recording → stopped → finalized."""

    @abstractmethod
    async def start(self, *, consent_granted_ts: float) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...
    @abstractmethod
    async def finalize(self) -> Optional[RecordingArtifact]: ...
    @property
    @abstractmethod
    def state(self) -> RecordingState: ...
    @property
    @abstractmethod
    def pipeline_processors(self) -> list:
        """Pipecat processors to splice into the call pipeline; [] for vobiz/null."""
```

- [ ] **Step 4: Implement `NullRecordingManager`**

```python
# reach_layer/voice/src/recordings/manager.py
"""Concrete RecordingManager(s).

NullRecordingManager — used when recording is disabled. RecordingManager
(the real implementation) is added in a later task.
"""
from __future__ import annotations

from typing import Optional

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingState,
)


class NullRecordingManager(RecordingManagerBase):
    """No-op manager used when recording.source == 'disabled'."""

    async def start(self, *, consent_granted_ts: float) -> None:
        return

    async def stop(self) -> None:
        return

    async def finalize(self) -> Optional[RecordingArtifact]:
        return None

    @property
    def state(self) -> RecordingState:
        return "idle"

    @property
    def pipeline_processors(self) -> list:
        return []
```

- [ ] **Step 5: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_manager_base.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/voice/src/recordings/ reach_layer/voice/tests/recordings/
git commit -m "$(cat <<'EOF'
feat(reach-voice): RecordingManagerBase + NullRecordingManager (#322)

Adds the recording subpackage skeleton with RecordingManagerBase ABC,
RecordingArtifact / RecordingPayload dataclasses, and NullRecordingManager
used when recording.source is disabled.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `RecordingSourceBase` + `PipelineRecordingSource` + `RecordingTapProcessor`

**Files:**
- Create: `reach_layer/voice/src/recordings/sources/__init__.py` (empty)
- Create: `reach_layer/voice/src/recordings/sources/source_base.py`
- Create: `reach_layer/voice/src/recordings/sources/pipeline_source.py`
- Create: `reach_layer/voice/src/pipecat_services/recording_tap.py`
- Test: `reach_layer/voice/tests/recordings/sources/__init__.py` (empty)
- Test: `reach_layer/voice/tests/recordings/sources/test_pipeline_source.py`
- Test: `reach_layer/voice/tests/pipecat_services/test_recording_tap.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/pipecat_services/test_recording_tap.py
"""Tests for RecordingTapProcessor — frame interception and WAV write."""
from __future__ import annotations

import io
import wave

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

from src.pipecat_services.recording_tap import RecordingTapProcessor


@pytest.mark.asyncio
async def test_processor_inactive_by_default_no_writes():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    f = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1)
    await proc.process_frame(f, direction=None)
    assert buf.getvalue() == b""


@pytest.mark.asyncio
async def test_active_writes_input_and_output_frames():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    proc.activate()
    in_f = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1)
    out_f = OutputAudioRawFrame(audio=b"\x02\x03" * 80, sample_rate=8000, num_channels=1)
    await proc.process_frame(in_f, direction=None)
    await proc.process_frame(out_f, direction=None)
    proc.close()

    buf.seek(0)
    with wave.open(buf, "rb") as w:
        assert w.getframerate() == 8000
        assert w.getnchannels() == 1
        # 80 frames in + 80 frames out, mixed = 80 frames * 2 (input + output sequentially appended)
        # OR the design mixes in-place; spec says mixed-mono. Concretely: frames written as a stream;
        # exact count tied to implementation. Assert >0 frames as a smoke check.
        assert w.getnframes() > 0


@pytest.mark.asyncio
async def test_inactive_after_close_drops_frames():
    buf = io.BytesIO()
    proc = RecordingTapProcessor(sample_rate=8000, sink=buf)
    proc.activate()
    proc.close()
    f = InputAudioRawFrame(audio=b"\x05" * 160, sample_rate=8000, num_channels=1)
    await proc.process_frame(f, direction=None)
    # WAV already finalized; processor must not raise and must not corrupt the buffer.
    buf.seek(0)
    with wave.open(buf, "rb") as w:
        # readable WAV header even after extra frame
        assert w.getframerate() == 8000
```

```python
# reach_layer/voice/tests/recordings/sources/test_pipeline_source.py
"""Tests for PipelineRecordingSource."""
from __future__ import annotations

import pytest

from src.recordings.sources.pipeline_source import PipelineRecordingSource


@pytest.mark.asyncio
async def test_pipeline_source_exposes_processor():
    src = PipelineRecordingSource(sample_rate=8000)
    procs = src.pipeline_processors
    assert len(procs) == 1


@pytest.mark.asyncio
async def test_begin_activates_processor():
    src = PipelineRecordingSource(sample_rate=8000)
    proc = src.pipeline_processors[0]
    await src.begin(call_sid="CA1", vobiz_call_id="")
    assert proc._active is True


@pytest.mark.asyncio
async def test_end_returns_payload_with_bytes():
    src = PipelineRecordingSource(sample_rate=8000)
    await src.begin(call_sid="CA1", vobiz_call_id="")
    payload = await src.end()
    assert payload.bytes_data is not None
    assert payload.fetch_url is None
```

- [ ] **Step 2: Run tests, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/sources/test_pipeline_source.py tests/pipecat_services/test_recording_tap.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `RecordingTapProcessor`**

```python
# reach_layer/voice/src/pipecat_services/recording_tap.py
"""RecordingTapProcessor — passive Pipecat processor that writes audio to WAV.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import io
import logging
import wave
from typing import IO, Optional

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameProcessor

logger = logging.getLogger(__name__)


class RecordingTapProcessor(FrameProcessor):
    """Captures inbound + outbound audio frames into a WAV buffer when active."""

    def __init__(self, sample_rate: int, sink: Optional[IO[bytes]] = None) -> None:
        super().__init__()
        self._sample_rate = int(sample_rate)
        self._sink: IO[bytes] = sink if sink is not None else io.BytesIO()
        self._wav: Optional[wave.Wave_write] = None
        self._active: bool = False
        self._closed: bool = False

    def activate(self) -> None:
        if self._closed:
            return
        if self._wav is None:
            self._wav = wave.open(self._sink, "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self._sample_rate)
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def close(self) -> None:
        self.deactivate()
        if self._wav is not None and not self._closed:
            try:
                self._wav.close()
            except Exception as exc:
                logger.warning(
                    "recording_tap.close_failed",
                    extra={"operation": "recording_tap.close", "status": "failure",
                           "error": f"{type(exc).__name__}: {exc}"},
                )
        self._closed = True

    @property
    def buffer_value(self) -> bytes:
        if hasattr(self._sink, "getvalue"):
            return self._sink.getvalue()  # type: ignore[no-any-return]
        return b""

    async def process_frame(self, frame, direction) -> None:
        if self._active and self._wav is not None and not self._closed:
            if isinstance(frame, (InputAudioRawFrame, OutputAudioRawFrame)):
                try:
                    self._wav.writeframes(frame.audio)
                except Exception as exc:
                    logger.warning(
                        "recording_tap.write_failed",
                        extra={"operation": "recording_tap.write", "status": "failure",
                               "error": f"{type(exc).__name__}: {exc}"},
                    )
        await self.push_frame(frame, direction)
```

- [ ] **Step 4: Implement `RecordingSourceBase` and `PipelineRecordingSource`**

```python
# reach_layer/voice/src/recordings/sources/source_base.py
"""RecordingSourceBase — ABC for capture mechanisms."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.recordings.manager_base import RecordingPayload


class RecordingSourceBase(ABC):
    @abstractmethod
    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None: ...
    @abstractmethod
    async def end(self) -> RecordingPayload: ...
    @property
    @abstractmethod
    def pipeline_processors(self) -> list: ...
```

```python
# reach_layer/voice/src/recordings/sources/pipeline_source.py
"""PipelineRecordingSource — Pipecat-tap based audio capture."""
from __future__ import annotations

import logging
import time

from src.pipecat_services.recording_tap import RecordingTapProcessor
from src.recordings.manager_base import RecordingPayload
from src.recordings.sources.source_base import RecordingSourceBase

logger = logging.getLogger(__name__)


class PipelineRecordingSource(RecordingSourceBase):
    """Captures audio via a RecordingTapProcessor spliced into the call pipeline."""

    def __init__(self, sample_rate: int) -> None:
        self._processor = RecordingTapProcessor(sample_rate=sample_rate)
        self._sample_rate = sample_rate
        self._started_ts: float = 0.0

    @property
    def pipeline_processors(self) -> list:
        return [self._processor]

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        self._started_ts = time.time()
        self._processor.activate()
        logger.info(
            "pipeline_source.begin",
            extra={"operation": "pipeline_source.begin", "status": "success",
                   "call_sid": call_sid, "sample_rate": self._sample_rate},
        )

    async def end(self) -> RecordingPayload:
        self._processor.close()
        return RecordingPayload(bytes_data=self._processor.buffer_value)
```

- [ ] **Step 5: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/sources/test_pipeline_source.py tests/pipecat_services/test_recording_tap.py -v
```
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/voice/src/recordings/sources/ reach_layer/voice/src/pipecat_services/recording_tap.py reach_layer/voice/tests/recordings/sources/ reach_layer/voice/tests/pipecat_services/test_recording_tap.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): pipeline recording source + tap processor (#322)

Adds RecordingSourceBase and PipelineRecordingSource. The latter owns a
RecordingTapProcessor that captures InputAudioRawFrame and
OutputAudioRawFrame into an in-memory WAV at the operator's sample rate.
Inactive by default; activated by source.begin() and finalized by
source.end().

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `VobizRecordingSource` + `/recording-ready` webhook rewire

**Files:**
- Create: `reach_layer/voice/src/recordings/sources/vobiz_source.py`
- Modify: `reach_layer/voice/server.py`
- Test: `reach_layer/voice/tests/recordings/sources/test_vobiz_source.py`
- Modify test: `reach_layer/voice/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/sources/test_vobiz_source.py
"""Tests for VobizRecordingSource — REST start/stop + webhook future."""
from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses

from src.recordings.sources.vobiz_source import VobizRecordingSource


@pytest.fixture
def registry() -> dict:
    return {}


@pytest.mark.asyncio
async def test_begin_posts_record_start(registry):
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post(
            "https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/",
            status=202, payload={"ok": True},
        )
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
    assert "CALL1" in registry  # future registered


@pytest.mark.asyncio
async def test_end_posts_stop_and_awaits_future_then_fetches(registry):
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=5.0, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=202, payload={})
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/Stop/", status=204)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        # webhook arrives before end()
        registry["CALL1"].set_result("https://cdn.vobiz/CALL1.mp3")
        m.get("https://cdn.vobiz/CALL1.mp3", body=b"FAKEMP3", status=200)
        payload = await src.end()
    assert payload.bytes_data == b"FAKEMP3"


@pytest.mark.asyncio
async def test_end_times_out_when_webhook_never_arrives(registry):
    src = VobizRecordingSource(
        auth_id="A", auth_token="T", callback_url="http://x/recording-ready",
        webhook_timeout_s=0.1, fetch_timeout_s=5.0, registry=registry,
    )
    with aioresponses() as m:
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/", status=202)
        m.post("https://api.vobiz.ai/api/v1/Account/A/Call/CALL1/Record/Stop/", status=204)
        await src.begin(call_sid="CA1", vobiz_call_id="CALL1")
        with pytest.raises(asyncio.TimeoutError):
            await src.end()
```

```python
# reach_layer/voice/tests/test_server.py
# Append to existing file:
def test_recording_ready_resolves_registered_future(client, app):
    fut = asyncio.Future()
    app.state.recording_url_registry["CA9"] = fut
    response = client.post(
        "/recording-ready",
        json={"callSid": "CA9", "recordingUrl": "https://x/y.mp3"},
    )
    assert response.status_code == 200
    assert fut.done()
    assert fut.result() == "https://x/y.mp3"


def test_recording_ready_unknown_call_sid_still_200(client):
    response = client.post(
        "/recording-ready",
        json={"callSid": "UNKNOWN", "recordingUrl": "https://x/y.mp3"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/sources/test_vobiz_source.py tests/test_server.py -v
```
Expected: FAIL — module / state attribute missing.

- [ ] **Step 3: Implement `VobizRecordingSource`**

```python
# reach_layer/voice/src/recordings/sources/vobiz_source.py
"""VobizRecordingSource — Vobiz REST start/stop + webhook-fed MP3 fetch."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict

import aiohttp

from src.recordings.manager_base import RecordingPayload
from src.recordings.sources.source_base import RecordingSourceBase

logger = logging.getLogger(__name__)


class VobizRecordingSource(RecordingSourceBase):
    def __init__(
        self,
        *,
        auth_id: str,
        auth_token: str,
        callback_url: str,
        webhook_timeout_s: float,
        fetch_timeout_s: float,
        registry: Dict[str, "asyncio.Future[str]"],
    ) -> None:
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._callback_url = callback_url
        self._webhook_timeout_s = webhook_timeout_s
        self._fetch_timeout_s = fetch_timeout_s
        self._registry = registry
        self._vobiz_call_id: str = ""

    @property
    def pipeline_processors(self) -> list:
        return []

    def _headers(self) -> dict:
        return {"X-Auth-ID": self._auth_id, "X-Auth-Token": self._auth_token}

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        self._vobiz_call_id = vobiz_call_id
        endpoint = f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}/Call/{vobiz_call_id}/Record/"
        loop = asyncio.get_running_loop()
        self._registry[vobiz_call_id] = loop.create_future()
        start = time.time()
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(endpoint, headers=self._headers(), json={
                "record_session": True,
                "transcription": False,
                "callback_url": self._callback_url,
            }) as resp:
                ok = resp.status in (200, 201, 202)
        logger.info(
            "vobiz_source.begin",
            extra={"operation": "vobiz_source.begin",
                   "status": "success" if ok else "failure",
                   "call_sid": call_sid, "vobiz_call_id": vobiz_call_id,
                   "latency_ms": int((time.time() - start) * 1000)},
        )

    async def _stop_record(self) -> None:
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{self._vobiz_call_id}/Record/Stop/"
        )
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(endpoint, headers=self._headers()) as resp:
                _ = resp.status

    async def end(self) -> RecordingPayload:
        await self._stop_record()
        fut = self._registry.get(self._vobiz_call_id)
        if fut is None:
            raise RuntimeError("vobiz recording future missing")
        url = await asyncio.wait_for(fut, timeout=self._webhook_timeout_s)
        timeout = aiohttp.ClientTimeout(total=self._fetch_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
        self._registry.pop(self._vobiz_call_id, None)
        return RecordingPayload(bytes_data=data)
```

- [ ] **Step 4: Rewire server.py**

In `reach_layer/voice/server.py`:

1. In `create_app()`, after `app = FastAPI(...)`:
   ```python
   app.state.recording_url_registry = {}
   ```
2. Replace the body of `recording_ready`:
   ```python
   @app.post("/recording-ready")
   async def recording_ready(body: RecordingWebhook) -> dict:
       fut = app.state.recording_url_registry.pop(body.callSid, None)
       if fut is not None and not fut.done():
           fut.set_result(body.recordingUrl)
       logger.info(
           "server.recording_ready",
           extra={"operation": "server.recording_ready", "status": "success",
                  "call_sid": body.callSid, "had_future": fut is not None},
       )
       return {"status": "ok"}
   ```
3. Update the existing `client` test fixture in `tests/test_server.py` to expose `app` (or add a sibling `app` fixture if not already present).

- [ ] **Step 5: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/sources/test_vobiz_source.py tests/test_server.py -v
```
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add reach_layer/voice/src/recordings/sources/vobiz_source.py reach_layer/voice/server.py reach_layer/voice/tests/recordings/sources/test_vobiz_source.py reach_layer/voice/tests/test_server.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): VobizRecordingSource + webhook future registry (#322)

Adds VobizRecordingSource (REST Record/start, Record/Stop, MP3 fetch via
the registered future) and rewires /recording-ready in server.py to
resolve the registered future. Registry lives on app.state and is
populated by the source on begin().

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `RecordingStoreBase` + `LocalFileStore`

**Files:**
- Create: `reach_layer/voice/src/recordings/stores/__init__.py` (empty)
- Create: `reach_layer/voice/src/recordings/stores/store_base.py`
- Create: `reach_layer/voice/src/recordings/stores/local_store.py`
- Test: `reach_layer/voice/tests/recordings/stores/__init__.py` (empty)
- Test: `reach_layer/voice/tests/recordings/stores/test_local_store.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/stores/test_local_store.py
"""Tests for LocalFileStore."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.recordings.manager_base import RecordingArtifact, RecordingPayload
from src.recordings.stores.local_store import LocalFileStore


def _artifact(payload: RecordingPayload, source="pipeline", fmt="wav") -> RecordingArtifact:
    return RecordingArtifact(
        call_sid="CA1", session_id="s", caller_id_hash="h",
        start_ts=1.0, end_ts=2.0, duration_ms=1000, consent_granted_ts=0.5,
        source=source, format=fmt, sha256="", payload=payload,
    )


@pytest.mark.asyncio
async def test_local_store_writes_audio_and_sidecar(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    payload = RecordingPayload(bytes_data=b"AUDIO")
    art = _artifact(payload)
    uri = await store.put(art)
    assert uri.startswith("file://")
    audio_path = Path(uri.removeprefix("file://"))
    assert audio_path.read_bytes() == b"AUDIO"
    sidecar = audio_path.with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    assert meta["call_sid"] == "CA1"
    assert meta["sha256"] == hashlib.sha256(b"AUDIO").hexdigest()
    assert meta["recording_uri"] == uri


@pytest.mark.asyncio
async def test_local_store_path_uses_yyyy_mm_dd(tmp_path: Path):
    store = LocalFileStore(base_path=str(tmp_path))
    art = _artifact(RecordingPayload(bytes_data=b"x"))
    uri = await store.put(art)
    rel = uri.removeprefix("file://").replace(str(tmp_path) + "/", "")
    parts = rel.split("/")
    assert len(parts) == 4  # YYYY/MM/DD/CA1.wav
    assert parts[-1] == "CA1.wav"
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/stores/test_local_store.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# reach_layer/voice/src/recordings/stores/store_base.py
"""RecordingStoreBase — ABC for the audit storage backend."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.recordings.manager_base import RecordingArtifact


class RecordingStoreBase(ABC):
    @abstractmethod
    async def put(self, artifact: RecordingArtifact) -> str:
        """Persist artifact (audio + sidecar manifest). Returns recording URI."""
```

```python
# reach_layer/voice/src/recordings/stores/local_store.py
"""LocalFileStore — writes recording audio + sidecar JSON to a mounted volume."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.recordings.manager_base import RecordingArtifact
from src.recordings.stores.store_base import RecordingStoreBase

logger = logging.getLogger(__name__)


class LocalFileStore(RecordingStoreBase):
    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)

    async def put(self, artifact: RecordingArtifact) -> str:
        ts = datetime.fromtimestamp(artifact.start_ts, tz=timezone.utc)
        target_dir = self._base / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}"
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        audio_path = target_dir / f"{artifact.call_sid}.{artifact.format}"
        if artifact.payload.bytes_data is None:
            raise ValueError("LocalFileStore requires payload.bytes_data")
        data = artifact.payload.bytes_data
        sha = hashlib.sha256(data).hexdigest()
        artifact.sha256 = sha
        with audio_path.open("wb") as f:
            f.write(data)
        os.chmod(audio_path, 0o640)
        uri = f"file://{audio_path.resolve()}"
        sidecar = {
            "schema_version": "1.0",
            "call_sid": artifact.call_sid,
            "session_id": artifact.session_id,
            "caller_id_hash": artifact.caller_id_hash,
            "source": artifact.source,
            "format": artifact.format,
            "duration_ms": artifact.duration_ms,
            "bytes": len(data),
            "sha256": sha,
            "recording_uri": uri,
            "consent_granted_ts": artifact.consent_granted_ts,
            "start_ts": artifact.start_ts,
            "end_ts": artifact.end_ts,
            "store_backend": "local",
            "trace_id": artifact.extra.get("trace_id", ""),
        }
        sidecar_path = audio_path.with_suffix(".json")
        sidecar_path.write_text(json.dumps(sidecar, indent=2))
        os.chmod(sidecar_path, 0o640)
        logger.info(
            "local_store.put",
            extra={"operation": "local_store.put", "status": "success",
                   "call_sid": artifact.call_sid, "bytes": len(data),
                   "recording_uri": uri},
        )
        return uri
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/stores/test_local_store.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/recordings/stores/store_base.py reach_layer/voice/src/recordings/stores/local_store.py reach_layer/voice/tests/recordings/stores/
git commit -m "$(cat <<'EOF'
feat(reach-voice): RecordingStoreBase + LocalFileStore (#322)

Adds the storage ABC and a local-disk implementation that writes the
audio file plus a self-describing sidecar JSON manifest under
{base_path}/YYYY/MM/DD/. SHA256 is computed during write and recorded
in both the artifact and the sidecar.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `S3Store`

**Files:**
- Create: `reach_layer/voice/src/recordings/stores/s3_store.py`
- Modify: `reach_layer/voice/pyproject.toml` (add `aiobotocore`)
- Test: `reach_layer/voice/tests/recordings/stores/test_s3_store.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/stores/test_s3_store.py
"""Tests for S3Store using aiobotocore stubbed via aiobotocore.session.AioSession."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.recordings.manager_base import RecordingArtifact, RecordingPayload
from src.recordings.stores.s3_store import S3Store


def _artifact() -> RecordingArtifact:
    return RecordingArtifact(
        call_sid="CA1", session_id="s", caller_id_hash="h",
        start_ts=1.0, end_ts=2.0, duration_ms=1000, consent_granted_ts=0.5,
        source="vobiz", format="mp3", sha256="",
        payload=RecordingPayload(bytes_data=b"DATA"),
    )


@pytest.mark.asyncio
async def test_s3_store_uploads_audio_and_sidecar(monkeypatch):
    client = MagicMock()
    client.put_object = AsyncMock(return_value={"ETag": '"x"'})

    class _Ctx:
        async def __aenter__(self_inner):
            return client
        async def __aexit__(self_inner, *a):
            return False

    sess = MagicMock()
    sess.create_client = MagicMock(return_value=_Ctx())
    monkeypatch.setattr("src.recordings.stores.s3_store._make_session", lambda: sess)

    store = S3Store(bucket="b", prefix="rec/", region="ap-south-1", kms_key_id="")
    uri = await store.put(_artifact())
    assert uri.startswith("s3://b/")
    assert client.put_object.call_count == 2  # audio + sidecar
    args = client.put_object.call_args_list[0].kwargs
    assert args["Bucket"] == "b"
    assert args["Key"].endswith("CA1.mp3")
    assert args["ServerSideEncryption"] == "AES256"


@pytest.mark.asyncio
async def test_s3_store_uses_kms_when_configured(monkeypatch):
    client = MagicMock()
    client.put_object = AsyncMock(return_value={})

    class _Ctx:
        async def __aenter__(self_inner): return client
        async def __aexit__(self_inner, *a): return False

    sess = MagicMock()
    sess.create_client = MagicMock(return_value=_Ctx())
    monkeypatch.setattr("src.recordings.stores.s3_store._make_session", lambda: sess)

    store = S3Store(bucket="b", prefix="rec/", region="ap-south-1", kms_key_id="kms-1")
    await store.put(_artifact())
    args = client.put_object.call_args_list[0].kwargs
    assert args["ServerSideEncryption"] == "aws:kms"
    assert args["SSEKMSKeyId"] == "kms-1"
```

- [ ] **Step 2: Add dep + run, verify failure**

```bash
cd reach_layer/voice && uv add aiobotocore
uv run pytest tests/recordings/stores/test_s3_store.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# reach_layer/voice/src/recordings/stores/s3_store.py
"""S3Store — uploads the audio + sidecar JSON to an S3-compatible bucket."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from aiobotocore.session import AioSession, get_session

from src.recordings.manager_base import RecordingArtifact
from src.recordings.stores.store_base import RecordingStoreBase

logger = logging.getLogger(__name__)


def _make_session() -> AioSession:
    return get_session()


class S3Store(RecordingStoreBase):
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str,
        kms_key_id: str = "",
    ) -> None:
        if not bucket:
            raise ValueError("S3Store requires bucket")
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._region = region
        self._kms = kms_key_id

    def _sse_kwargs(self) -> dict:
        if self._kms:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        return {"ServerSideEncryption": "AES256"}

    def _key(self, artifact: RecordingArtifact, ext: str) -> str:
        ts = datetime.fromtimestamp(artifact.start_ts, tz=timezone.utc)
        return (
            f"{self._prefix}{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/"
            f"{artifact.call_sid}.{ext}"
        )

    async def put(self, artifact: RecordingArtifact) -> str:
        if artifact.payload.bytes_data is None:
            raise ValueError("S3Store requires payload.bytes_data")
        data = artifact.payload.bytes_data
        sha = hashlib.sha256(data).hexdigest()
        artifact.sha256 = sha
        audio_key = self._key(artifact, artifact.format)
        sidecar_key = self._key(artifact, "json")
        uri = f"s3://{self._bucket}/{audio_key}"
        sidecar = {
            "schema_version": "1.0",
            "call_sid": artifact.call_sid,
            "session_id": artifact.session_id,
            "caller_id_hash": artifact.caller_id_hash,
            "source": artifact.source,
            "format": artifact.format,
            "duration_ms": artifact.duration_ms,
            "bytes": len(data),
            "sha256": sha,
            "recording_uri": uri,
            "consent_granted_ts": artifact.consent_granted_ts,
            "start_ts": artifact.start_ts,
            "end_ts": artifact.end_ts,
            "store_backend": "s3",
            "trace_id": artifact.extra.get("trace_id", ""),
        }
        session = _make_session()
        async with session.create_client("s3", region_name=self._region) as s3:
            await s3.put_object(
                Bucket=self._bucket, Key=audio_key, Body=data,
                ContentType=("audio/mpeg" if artifact.format == "mp3" else "audio/wav"),
                **self._sse_kwargs(),
            )
            await s3.put_object(
                Bucket=self._bucket, Key=sidecar_key,
                Body=json.dumps(sidecar, indent=2).encode(),
                ContentType="application/json",
                **self._sse_kwargs(),
            )
        logger.info(
            "s3_store.put",
            extra={"operation": "s3_store.put", "status": "success",
                   "call_sid": artifact.call_sid, "bytes": len(data),
                   "recording_uri": uri, "kms": bool(self._kms)},
        )
        return uri
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/stores/ -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/recordings/stores/s3_store.py reach_layer/voice/tests/recordings/stores/test_s3_store.py reach_layer/voice/pyproject.toml reach_layer/voice/uv.lock
git commit -m "$(cat <<'EOF'
feat(reach-voice): S3Store backend for recordings (#322)

Adds aiobotocore-based S3 store that uploads {prefix}/YYYY/MM/DD/CA.mp3
plus the sidecar JSON. SSE-S3 by default; SSE-KMS if kms_key_id is set.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `RecordingManager` (concrete) + state machine

**Files:**
- Modify: `reach_layer/voice/src/recordings/manager.py`
- Test: `reach_layer/voice/tests/recordings/test_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/test_manager.py
"""Tests for the concrete RecordingManager state machine."""
from __future__ import annotations

import pytest

from src.recordings.manager import RecordingManager
from src.recordings.manager_base import RecordingArtifact, RecordingPayload


class _StubSource:
    def __init__(self, payload: RecordingPayload) -> None:
        self.payload = payload
        self.began = False
        self.ended = False
    @property
    def pipeline_processors(self): return []
    async def begin(self, *, call_sid, vobiz_call_id):
        self.began = True
    async def end(self) -> RecordingPayload:
        self.ended = True
        return self.payload


class _StubStore:
    def __init__(self) -> None:
        self.last: RecordingArtifact | None = None
    async def put(self, art: RecordingArtifact) -> str:
        self.last = art
        return "file:///tmp/x.wav"


def _mgr(payload=RecordingPayload(bytes_data=b"x" * 320)) -> tuple:
    src, store = _StubSource(payload), _StubStore()
    m = RecordingManager(
        source=src, store=store, call_sid="CA1", session_id="s",
        caller_id_hash="h", source_name="pipeline", fmt="wav",
        sample_rate=8000, min_duration_ms=10, vobiz_call_id="",
    )
    return m, src, store


@pytest.mark.asyncio
async def test_lifecycle_idle_recording_stopped_finalized():
    m, src, store = _mgr()
    assert m.state == "idle"
    await m.start(consent_granted_ts=1.0)
    assert m.state == "recording"
    assert src.began
    await m.stop()
    assert m.state == "stopped"
    art = await m.finalize()
    assert m.state == "finalized"
    assert art is not None
    assert store.last is art


@pytest.mark.asyncio
async def test_finalize_without_start_returns_none():
    m, _, store = _mgr()
    art = await m.finalize()
    assert art is None
    assert m.state == "idle"
    assert store.last is None


@pytest.mark.asyncio
async def test_min_duration_short_circuits_with_empty():
    m, _, store = _mgr(payload=RecordingPayload(bytes_data=b""))
    await m.start(consent_granted_ts=1.0)
    await m.stop()
    art = await m.finalize()
    assert art is None
    assert m.state == "finalized"  # finalized but no artifact stored
    assert store.last is None


@pytest.mark.asyncio
async def test_failed_source_marks_state_failed():
    class _BadSource(_StubSource):
        async def end(self):
            raise RuntimeError("boom")
    src = _BadSource(RecordingPayload(bytes_data=b"x"))
    m = RecordingManager(
        source=src, store=_StubStore(), call_sid="CA1", session_id="s",
        caller_id_hash="h", source_name="pipeline", fmt="wav",
        sample_rate=8000, min_duration_ms=10, vobiz_call_id="",
    )
    await m.start(consent_granted_ts=1.0)
    await m.stop()
    art = await m.finalize()
    assert art is None
    assert m.state == "failed"
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_manager.py -v
```
Expected: FAIL — class not implemented.

- [ ] **Step 3: Implement `RecordingManager`**

Append to `reach_layer/voice/src/recordings/manager.py`:

```python
import logging
import time
from typing import Optional

from src.recordings.manager_base import (
    RecordingArtifact,
    RecordingManagerBase,
    RecordingState,
)
from src.recordings.sources.source_base import RecordingSourceBase
from src.recordings.stores.store_base import RecordingStoreBase

logger = logging.getLogger(__name__)


class RecordingManager(RecordingManagerBase):
    def __init__(
        self,
        *,
        source: RecordingSourceBase,
        store: RecordingStoreBase,
        call_sid: str,
        session_id: str,
        caller_id_hash: str,
        source_name: str,            # "vobiz" | "pipeline"
        fmt: str,                    # "mp3" | "wav"
        sample_rate: int,
        min_duration_ms: int,
        vobiz_call_id: str,
    ) -> None:
        self._source = source
        self._store = store
        self._call_sid = call_sid
        self._session_id = session_id
        self._caller_id_hash = caller_id_hash
        self._source_name = source_name
        self._fmt = fmt
        self._sample_rate = sample_rate
        self._min_duration_ms = min_duration_ms
        self._vobiz_call_id = vobiz_call_id
        self._state: RecordingState = "idle"
        self._consent_granted_ts: float = 0.0
        self._start_ts: float = 0.0
        self._end_ts: float = 0.0
        self._extra: dict = {}

    @property
    def state(self) -> RecordingState:
        return self._state

    @property
    def pipeline_processors(self) -> list:
        return self._source.pipeline_processors

    def attach_trace_id(self, trace_id: str) -> None:
        self._extra["trace_id"] = trace_id

    async def start(self, *, consent_granted_ts: float) -> None:
        if self._state != "idle":
            return
        try:
            await self._source.begin(call_sid=self._call_sid, vobiz_call_id=self._vobiz_call_id)
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.start_failed",
                extra={"operation": "recording_manager.start", "status": "failure",
                       "call_sid": self._call_sid,
                       "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self._consent_granted_ts = consent_granted_ts
        self._start_ts = time.time()
        self._state = "recording"

    async def stop(self) -> None:
        if self._state not in ("recording",):
            return
        self._end_ts = time.time()
        self._state = "stopped"

    async def finalize(self) -> Optional[RecordingArtifact]:
        if self._state == "idle":
            return None
        if self._state == "failed":
            return None
        try:
            payload = await self._source.end()
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.finalize_failed",
                extra={"operation": "recording_manager.finalize", "status": "failure",
                       "call_sid": self._call_sid,
                       "error": f"{type(exc).__name__}: {exc}"},
            )
            return None
        if self._end_ts == 0.0:
            self._end_ts = time.time()
        duration_ms = max(0, int((self._end_ts - self._start_ts) * 1000))
        size = len(payload.bytes_data or b"")
        if duration_ms < self._min_duration_ms or size == 0:
            self._state = "finalized"
            logger.info(
                "recording_manager.empty",
                extra={"operation": "recording_manager.finalize", "status": "skipped",
                       "call_sid": self._call_sid, "duration_ms": duration_ms,
                       "bytes": size, "reason": "below_min_duration"},
            )
            return None
        artifact = RecordingArtifact(
            call_sid=self._call_sid, session_id=self._session_id,
            caller_id_hash=self._caller_id_hash,
            start_ts=self._start_ts, end_ts=self._end_ts, duration_ms=duration_ms,
            consent_granted_ts=self._consent_granted_ts,
            source=self._source_name, format=self._fmt,  # type: ignore[arg-type]
            sha256="", payload=payload, extra=dict(self._extra),
        )
        try:
            await self._store.put(artifact)
        except Exception as exc:
            self._state = "failed"
            logger.error(
                "recording_manager.store_failed",
                extra={"operation": "recording_manager.finalize", "status": "failure",
                       "call_sid": self._call_sid,
                       "error": f"{type(exc).__name__}: {exc}"},
            )
            return None
        self._state = "finalized"
        return artifact
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_manager.py tests/recordings/test_manager_base.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/recordings/manager.py reach_layer/voice/tests/recordings/test_manager.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): concrete RecordingManager with state machine (#322)

Adds RecordingManager: idle → recording → stopped → finalized | failed.
Empties (zero bytes or duration < min_duration_ms) are finalized without
a stored artifact. All source/store failures are logged and swallowed
into state=failed; the call is never affected.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Factory + config validation at startup

**Files:**
- Create: `reach_layer/voice/src/recordings/factory.py`
- Test: `reach_layer/voice/tests/recordings/test_factory.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/test_factory.py
"""Tests for build_recording_manager factory."""
from __future__ import annotations

import pytest

from src.recordings.factory import build_recording_manager
from src.recordings.manager import NullRecordingManager, RecordingManager


def _cfg(source="disabled", backend="local", salt="", bucket=""):
    return {
        "reach_layer": {"channels": {"voice": {
            "vobiz": {"auth_id": "A", "auth_token": "T", "sample_rate": 8000},
            "recording": {
                "source": source,
                "consent_purpose": "recording",
                "webhook_timeout_s": 5.0, "fetch_timeout_s": 5.0, "min_duration_ms": 10,
                "caller_id_hash_salt": salt,
                "store": {
                    "backend": backend,
                    "local": {"base_path": "/tmp/r"},
                    "s3": {"bucket": bucket, "prefix": "rec/", "region": "ap-south-1", "kms_key_id": ""},
                },
            },
        }}},
    }


def test_disabled_returns_null():
    m = build_recording_manager(_cfg(), telephony=None, registry={})
    assert isinstance(m, NullRecordingManager)


def test_pipeline_local_returns_real_manager():
    m = build_recording_manager(_cfg(source="pipeline", salt="s" * 32), telephony=None, registry={})
    assert isinstance(m, RecordingManager)
    assert m.pipeline_processors  # has the tap processor


def test_enabled_without_salt_raises():
    with pytest.raises(ValueError, match="caller_id_hash_salt"):
        build_recording_manager(_cfg(source="vobiz"), telephony=None, registry={})


def test_s3_without_bucket_raises():
    with pytest.raises(ValueError, match="bucket"):
        build_recording_manager(
            _cfg(source="vobiz", backend="s3", salt="s" * 32, bucket=""),
            telephony=None, registry={},
        )


def test_unknown_source_raises():
    cfg = _cfg()
    cfg["reach_layer"]["channels"]["voice"]["recording"]["source"] = "ftp"
    with pytest.raises(ValueError, match="recording.source"):
        build_recording_manager(cfg, telephony=None, registry={})
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_factory.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement factory**

```python
# reach_layer/voice/src/recordings/factory.py
"""build_recording_manager — config-driven manager construction."""
from __future__ import annotations

import hashlib
from typing import Any

from src.recordings.manager import NullRecordingManager, RecordingManager
from src.recordings.manager_base import RecordingManagerBase
from src.recordings.sources.pipeline_source import PipelineRecordingSource
from src.recordings.sources.vobiz_source import VobizRecordingSource
from src.recordings.stores.local_store import LocalFileStore
from src.recordings.stores.s3_store import S3Store


def hash_caller_id(salt: str, caller_id: str) -> str:
    return hashlib.sha256((salt + (caller_id or "")).encode()).hexdigest()[:16]


def build_recording_manager(
    config: dict,
    *,
    telephony: Any,
    registry: dict,
    call_sid: str = "",
    session_id: str = "",
    caller_id: str = "",
    vobiz_call_id: str = "",
    callback_url: str = "",
) -> RecordingManagerBase:
    voice = config.get("reach_layer", {}).get("channels", {}).get("voice", {})
    rec = voice.get("recording", {}) or {}
    source = rec.get("source", "disabled")
    if source == "disabled":
        return NullRecordingManager()
    if source not in ("vobiz", "pipeline"):
        raise ValueError(f"recording.source must be one of disabled|vobiz|pipeline, got {source!r}")

    salt = rec.get("caller_id_hash_salt", "")
    if not salt:
        raise ValueError("recording.caller_id_hash_salt must be set when recording is enabled")

    store_cfg = rec.get("store", {})
    backend = store_cfg.get("backend", "local")
    if backend == "local":
        store = LocalFileStore(base_path=store_cfg.get("local", {}).get("base_path", "/var/recordings"))
    elif backend == "s3":
        s3 = store_cfg.get("s3", {})
        if not s3.get("bucket"):
            raise ValueError("recording.store.s3.bucket must be set when backend=s3")
        store = S3Store(
            bucket=s3["bucket"], prefix=s3.get("prefix", "recordings/"),
            region=s3.get("region", "ap-south-1"), kms_key_id=s3.get("kms_key_id", ""),
        )
    else:
        raise ValueError(f"recording.store.backend must be local|s3, got {backend!r}")

    sample_rate = int(voice.get("vobiz", {}).get("sample_rate", 8000))

    if source == "pipeline":
        src_obj = PipelineRecordingSource(sample_rate=sample_rate)
        fmt = "wav"
    else:
        vobiz = voice.get("vobiz", {})
        src_obj = VobizRecordingSource(
            auth_id=vobiz.get("auth_id", ""), auth_token=vobiz.get("auth_token", ""),
            callback_url=callback_url,
            webhook_timeout_s=float(rec.get("webhook_timeout_s", 30.0)),
            fetch_timeout_s=float(rec.get("fetch_timeout_s", 60.0)),
            registry=registry,
        )
        fmt = "mp3"

    return RecordingManager(
        source=src_obj, store=store, call_sid=call_sid, session_id=session_id,
        caller_id_hash=hash_caller_id(salt, caller_id),
        source_name=source, fmt=fmt, sample_rate=sample_rate,
        min_duration_ms=int(rec.get("min_duration_ms", 500)),
        vobiz_call_id=vobiz_call_id,
    )
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/ -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/recordings/factory.py reach_layer/voice/tests/recordings/test_factory.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): build_recording_manager factory + startup validation (#322)

Reads reach_layer.channels.voice.recording, validates required fields
(caller_id_hash_salt when enabled; s3.bucket when backend=s3; valid
source enum), and returns NullRecordingManager when disabled. Hashes
caller_id with the configured salt for the artifact metadata.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: OTel + Observability telemetry helpers

**Files:**
- Create: `reach_layer/voice/src/recordings/telemetry.py`
- Test: `reach_layer/voice/tests/recordings/test_telemetry.py`

- [ ] **Step 1: Write failing tests**

```python
# reach_layer/voice/tests/recordings/test_telemetry.py
"""Tests for the recording telemetry helpers (OTel spans + observability signals)."""
from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.recordings.telemetry import (
    SignalEmitter, recording_lifecycle_span, recording_stage_span,
)


@pytest.fixture(autouse=True)
def _otel(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_trace, "_TRACER_PROVIDER", provider, raising=False)
    return exporter


def test_recording_lifecycle_span_sets_required_attrs(_otel):
    with recording_lifecycle_span(call_sid="CA1", session_id="s",
                                  caller_id_hash="h", source="pipeline"):
        pass
    span = _otel.get_finished_spans()[0]
    assert span.name == "recording.lifecycle"
    assert span.attributes["dpg.block"] == "reach_layer"
    assert span.attributes["dpg.subsystem"] == "recording"
    assert span.attributes["recording.source"] == "pipeline"
    assert span.attributes["call_sid"] == "CA1"


def test_stage_span_records_status_failed(_otel):
    with pytest.raises(RuntimeError):
        with recording_stage_span("recording.start", call_sid="CA1", source="vobiz"):
            raise RuntimeError("boom")
    span = next(s for s in _otel.get_finished_spans() if s.name == "recording.start")
    assert span.status.status_code.name == "ERROR"


class _FakeObs:
    def __init__(self):
        self.calls = []
    def emit_signal(self, signal_type, data):
        self.calls.append((signal_type, data))


def test_signal_emitter_emits_started(_otel):
    obs = _FakeObs()
    emitter = SignalEmitter(obs)
    emitter.started(call_sid="CA1", session_id="s", caller_id_hash="h",
                    source="pipeline", consent_granted_ts=1.0, start_ts=2.0)
    assert obs.calls[0][0] == "recording.started"
    assert obs.calls[0][1]["call_sid"] == "CA1"


def test_signal_emitter_swallows_backend_failure():
    class _Bad:
        def emit_signal(self, *_): raise RuntimeError("x")
    emitter = SignalEmitter(_Bad())
    # Must not raise.
    emitter.failed(call_sid="CA1", session_id="s", source="vobiz",
                   stage="finalize", error_type="RuntimeError", error_message="x")
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_telemetry.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# reach_layer/voice/src/recordings/telemetry.py
"""OTel span helpers + Observability Layer signal emitter for recording."""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

_TRACER = otel_trace.get_tracer("reach_layer.voice.recording")

_BASE_ATTRS = {"dpg.block": "reach_layer", "dpg.channel": "voice", "dpg.subsystem": "recording"}


@contextlib.contextmanager
def recording_lifecycle_span(*, call_sid: str, session_id: str,
                              caller_id_hash: str, source: str,
                              link=None) -> Iterator[Any]:
    attrs = {**_BASE_ATTRS, "recording.source": source,
             "call_sid": call_sid, "session_id": session_id,
             "caller_id_hash": caller_id_hash}
    links = [link] if link is not None else []
    with _TRACER.start_as_current_span("recording.lifecycle", attributes=attrs, links=links) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


@contextlib.contextmanager
def recording_stage_span(name: str, *, call_sid: str, source: str, **extra) -> Iterator[Any]:
    attrs = {**_BASE_ATTRS, "recording.source": source, "call_sid": call_sid, **extra}
    with _TRACER.start_as_current_span(name, attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


class SignalEmitter:
    """Adapter over ObservabilityLayerBase.emit_signal — never raises."""

    def __init__(self, observability) -> None:
        self._obs = observability

    def _emit(self, signal_type: str, data: dict) -> None:
        try:
            self._obs.emit_signal(signal_type, data)
        except Exception as exc:
            logger.warning(
                "recording.signal_emit_failed",
                extra={"operation": "recording.signal_emit", "status": "failure",
                       "signal_type": signal_type,
                       "error": f"{type(exc).__name__}: {exc}"},
            )

    def started(self, **fields) -> None:
        self._emit("recording.started", fields)

    def stored(self, **fields) -> None:
        self._emit("recording.stored", fields)

    def empty(self, **fields) -> None:
        self._emit("recording.empty", fields)

    def failed(self, **fields) -> None:
        self._emit("recording.failed", fields)
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/recordings/test_telemetry.py -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add reach_layer/voice/src/recordings/telemetry.py reach_layer/voice/tests/recordings/test_telemetry.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): OTel + observability telemetry for recording (#322)

Adds recording_lifecycle_span / recording_stage_span context managers
that stamp dpg.block, dpg.channel, dpg.subsystem, recording.source plus
call_sid/session_id/caller_id_hash, and a SignalEmitter that calls
ObservabilityLayerBase.emit_signal but never raises into the call path.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `TelephonyAdapterBase.recording_manager` abstract property

**Files:**
- Modify: `reach_layer/voice/src/base.py`
- Test: `reach_layer/voice/tests/test_base.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `reach_layer/voice/tests/test_base.py`:

```python
def test_telephony_adapter_base_requires_recording_manager_property():
    from src.base import TelephonyAdapterBase
    # An adapter class missing recording_manager must remain abstract.
    class _Incomplete(TelephonyAdapterBase):
        async def handle_call(self, *a): ...
        async def teardown(self, *a): ...
        async def on_session_start(self, *a): ...
        async def on_session_end(self, *a): ...
        async def handle_barge_in(self, *a): ...
        async def on_vad_event(self, *a): ...
        async def close_call(self, **kw): ...
    import pytest
    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]
```

- [ ] **Step 2: Run, verify failure**

```bash
cd reach_layer/voice && uv run pytest tests/test_base.py -v
```
Expected: FAIL — `_Incomplete` instantiates because no abstract property exists yet.

- [ ] **Step 3: Add abstract property to `base.py`**

In `reach_layer/voice/src/base.py`, inside `TelephonyAdapterBase`:

```python
from src.recordings.manager_base import RecordingManagerBase

# add inside class:
@property
@abstractmethod
def recording_manager(self) -> RecordingManagerBase:
    """The RecordingManagerBase instance owning this call's recording.

    Adapters that do not support recording must return a NullRecordingManager —
    never None — so callers can dispatch unconditionally.
    """
```

- [ ] **Step 4: Run tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/test_base.py tests/test_vobiz_adapter.py -v
```
Expected: the new test passes; **existing `test_vobiz_adapter.py` will FAIL** because `VobizAdapter` no longer satisfies the ABC. That's the bridge to Task 13. Do not commit yet — proceed to Task 13 in the same branch.

> **Note:** This task and Task 13 form a single atomic change to keep tests green between commits. Combine them when committing if tests are checked between tasks.

- [ ] **Step 5 (deferred):** commit happens at end of Task 13.

---

### Task 13: Wire `RecordingManager` into `VobizAdapter`

**Files:**
- Modify: `reach_layer/voice/src/vobiz_adapter.py`
- Test: `reach_layer/voice/tests/test_vobiz_adapter.py` (extend)

- [ ] **Step 1: Write failing tests (additions)**

Append to `tests/test_vobiz_adapter.py`:

```python
import asyncio
from unittest.mock import MagicMock

from src.recordings.manager_base import RecordingManagerBase
from reach_layer_base import ConsentEvent


def _voice_cfg(source="disabled", salt=""):
    return {"reach_layer": {"channels": {"voice": {
        "vobiz": {"auth_id": "A", "auth_token": "T", "sample_rate": 8000},
        "vad": {"start_secs": 0.2, "stop_secs": 0.6, "min_volume": 0.6},
        "raya": {"endpoint": "http://raya:9090"},
        "agent_core": {"endpoint": "http://agent:8000",
                        "submit_path": "/process_turn",
                        "events_path": "/sessions/{session_id}/events",
                        "cancel_path": "/sessions/{session_id}/cancel",
                        "request_timeout_s": 30},
        "recording": {
            "source": source, "consent_purpose": "recording",
            "webhook_timeout_s": 5.0, "fetch_timeout_s": 5.0, "min_duration_ms": 10,
            "caller_id_hash_salt": salt,
            "store": {"backend": "local",
                       "local": {"base_path": "/tmp/x"},
                       "s3": {"bucket": "", "prefix": "rec/", "region": "ap-south-1", "kms_key_id": ""}},
        },
    }}}}


def test_vobiz_adapter_exposes_null_manager_when_disabled():
    from src.vobiz_adapter import VobizAdapter
    a = VobizAdapter(_voice_cfg())
    assert isinstance(a.recording_manager, RecordingManagerBase)
    assert a.recording_manager.state == "idle"


@pytest.mark.asyncio
async def test_consent_event_triggers_manager_start():
    from src.vobiz_adapter import VobizAdapter
    a = VobizAdapter(_voice_cfg(source="pipeline", salt="s" * 32))
    a._recording_manager = MagicMock(wraps=a._recording_manager)
    a._recording_manager.start = MagicMock(side_effect=lambda **kw: asyncio.sleep(0))
    evt = ConsentEvent(purpose="recording", granted=True, consent_granted_ts=1.0, turn_id="t-1")
    await a._on_consent_event(evt)
    a._recording_manager.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_consent_event_for_other_purpose_ignored():
    from src.vobiz_adapter import VobizAdapter
    a = VobizAdapter(_voice_cfg(source="pipeline", salt="s" * 32))
    a._recording_manager = MagicMock(wraps=a._recording_manager)
    a._recording_manager.start = MagicMock()
    evt = ConsentEvent(purpose="data_share", granted=True, consent_granted_ts=1.0)
    await a._on_consent_event(evt)
    a._recording_manager.start.assert_not_called()
```

- [ ] **Step 2: Update `VobizAdapter`**

In `reach_layer/voice/src/vobiz_adapter.py`:

1. Import additions at the top:
   ```python
   from reach_layer_base import ConsentEvent
   from src.recordings.factory import build_recording_manager
   from src.recordings.manager_base import RecordingManagerBase
   from src.recordings.telemetry import (
       SignalEmitter, recording_lifecycle_span,
   )
   ```
2. In `__init__` (after `super().__init__(...)`), build the manager — use a per-instance registry passed by reference into the source:
   ```python
   self._recording_url_registry: dict = {}
   rec_cfg = (
       self._config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("recording", {})
   )
   self._recording_consent_purpose = rec_cfg.get("consent_purpose", "recording")
   # call_sid/session_id/caller_id are filled in handle_call(); construct a placeholder Null first.
   self._recording_manager: RecordingManagerBase = build_recording_manager(
       self._config, telephony=self, registry=self._recording_url_registry,
       call_sid="", session_id="", caller_id="",
   )
   ```
3. Expose property:
   ```python
   @property
   def recording_manager(self) -> RecordingManagerBase:
       return self._recording_manager
   ```
4. In `handle_call(...)`, **after** `session_id` is generated and **before** building the pipeline, replace the placeholder with a properly-keyed manager iff recording is enabled:
   ```python
   self._recording_manager = build_recording_manager(
       self._config, telephony=self, registry=self._recording_url_registry,
       call_sid=call_sid, session_id=session_id, caller_id=caller_id,
       vobiz_call_id=call_id or "",
       callback_url=self._build_recording_callback_url(),
   )
   ```
   Add helper `_build_recording_callback_url(self) -> str` that reads the public webhook base URL from `reach_layer.channels.voice.public_url` (already used by `/answer`); return `<base>/recording-ready`. Skip if disabled.

5. Splice the manager's processors into the pipeline list:
   ```python
   processors = [
       transport.input(),
       VADProcessor(vad_analyzer=vad_analyzer),
       user_turn_processor,
       stt,
       vad_observer,
       agent,
       sanitizer,
       tts,
       *self._recording_manager.pipeline_processors,
       transport.output(),
   ]
   pipeline = Pipeline(processors)
   ```

6. Add the consent-event listener — extend `_play_opening_phrase` (or add a new task spawned alongside it) to dispatch on `ConsentEvent`:
   ```python
   async def _on_consent_event(self, evt: ConsentEvent) -> None:
       if evt.purpose != self._recording_consent_purpose or not evt.granted:
           return
       await self._recording_manager.start(consent_granted_ts=evt.consent_granted_ts or time.time())
   ```
   In the per-turn SSE consumer (or in a parallel listener task started in `_on_connected`), dispatch `ConsentEvent` to `_on_consent_event`. Concrete pattern: start an `asyncio.create_task(self._consent_listener(session_id, caller_id))` from `_on_connected` that runs alongside `_play_opening_phrase`, consuming a *separate* `subscribe_events` stream filtered to ConsentEvent only — or, if SSE serialisation can't share streams, hook the dispatch into `AgentCoreLLMProcessor`'s existing event loop. The simplest correct path: add a hook on `AgentCoreLLMProcessor` (it already runs the per-turn SSE loop) and pass the adapter so it can call `await self._telephony._on_consent_event(evt)`.

7. In `teardown(call_sid)`, spawn `_finalize_and_store`:
   ```python
   asyncio.create_task(self._finalize_and_store(call_sid))
   ```
   Implement `_finalize_and_store`:
   ```python
   async def _finalize_and_store(self, call_sid: str) -> None:
       emitter = SignalEmitter(self._observability)  # acquired via the same factory used elsewhere; if not available, log-only fallback
       inbound_link = self._inbound_span_link  # captured in handle_call() before pipeline run
       try:
           with recording_lifecycle_span(
               call_sid=call_sid, session_id=self._session_id_cache,
               caller_id_hash=getattr(self._recording_manager, "_caller_id_hash", ""),
               source=getattr(self._recording_manager, "_source_name", "disabled"),
               link=inbound_link,
           ) as span:
               trace_id = format(span.get_span_context().trace_id, "032x")
               if hasattr(self._recording_manager, "attach_trace_id"):
                   self._recording_manager.attach_trace_id(trace_id)
               await self._recording_manager.stop()
               artifact = await self._recording_manager.finalize()
               if artifact is None:
                   if self._recording_manager.state == "failed":
                       emitter.failed(call_sid=call_sid, session_id=self._session_id_cache,
                                      source=getattr(self._recording_manager, "_source_name", ""),
                                      stage="finalize", error_type="", error_message="")
                   else:
                       emitter.empty(call_sid=call_sid, session_id=self._session_id_cache,
                                     source=getattr(self._recording_manager, "_source_name", ""),
                                     duration_ms=0, reason="empty_or_idle")
                   return
               emitter.stored(
                   call_sid=call_sid, session_id=artifact.session_id,
                   caller_id_hash=artifact.caller_id_hash, source=artifact.source,
                   format=artifact.format, duration_ms=artifact.duration_ms,
                   bytes=len(artifact.payload.bytes_data or b""),
                   sha256=artifact.sha256, recording_uri=artifact.extra.get("uri", ""),
                   consent_granted_ts=artifact.consent_granted_ts,
                   start_ts=artifact.start_ts, end_ts=artifact.end_ts,
                   store_backend=getattr(self._recording_manager._store, "__class__").__name__.lower(),
               )
       except Exception as exc:
           logger.error(
               "vobiz_adapter.recording_pipeline_failed",
               extra={"operation": "vobiz_adapter._finalize_and_store",
                      "status": "failure", "call_sid": call_sid,
                      "error": f"{type(exc).__name__}: {exc}"},
           )
   ```

- [ ] **Step 3: Run all voice tests, verify passing**

```bash
cd reach_layer/voice && uv run pytest tests/ -v
```
Expected: all passing including the new tests.

- [ ] **Step 4: Commit (covers Task 12 + 13 atomically)**

```bash
git add reach_layer/voice/src/base.py reach_layer/voice/src/vobiz_adapter.py reach_layer/voice/tests/test_base.py reach_layer/voice/tests/test_vobiz_adapter.py
git commit -m "$(cat <<'EOF'
feat(reach-voice): wire RecordingManager into VobizAdapter (#322)

Adds recording_manager abstract property to TelephonyAdapterBase and
implements it on VobizAdapter via build_recording_manager(). Pipeline
splices the manager's pipeline_processors (RecordingTapProcessor for
the pipeline source). A ConsentEvent listener triggers manager.start()
when purpose matches recording.consent_purpose. teardown() spawns a
background _finalize_and_store task wrapped in recording.lifecycle
OTel span and Observability signals.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: dev-kit YAML defaults + Configuration Agent wizard wiring

**Files:**
- Modify: `dev-kit/dpg/reach_layer.yaml`
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Modify: `dev-kit/dev_kit/agent/renderer.py`
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Test: `dev-kit/agent/tests/test_recording_phase.py` (create) + extend `test_renderer.py` and `test_accumulator_channel_secrets.py`

- [ ] **Step 1: Write failing tests**

```python
# dev-kit/agent/tests/test_recording_phase.py
"""Tests for the wizard's recording phase."""
import pytest

from dev_kit.agent.tools import collect_recording_settings
from dev_kit.agent.renderer import render_voice_yaml


def test_disabled_renders_no_block():
    cfg = {"source": "disabled"}
    yaml_str = render_voice_yaml({"recording": cfg})
    assert "recording:" not in yaml_str  # default omitted from output


def test_enabled_renders_full_block_with_safe_defaults():
    cfg = {"source": "vobiz",
           "store": {"backend": "local"},
           "caller_id_hash_salt": "s" * 32}
    yaml_str = render_voice_yaml({"recording": cfg})
    assert "recording:" in yaml_str
    assert "source: vobiz" in yaml_str


def test_collect_recording_settings_generates_salt_when_missing(monkeypatch):
    monkeypatch.setattr("dev_kit.agent.tools._prompt", lambda *a, **kw: "")
    out = collect_recording_settings({"source": "vobiz"})
    assert len(out["caller_id_hash_salt"]) >= 32
```

(Augment existing tests in `test_accumulator_channel_secrets.py` to assert that `voice.recording.caller_id_hash_salt` and `voice.recording.store.s3.kms_key_id` are recognised as secrets.)

- [ ] **Step 2: Run, verify failure**

```bash
cd dev-kit && uv run pytest agent/tests/ -v
```
Expected: FAIL — function/path missing.

- [ ] **Step 3: Implement**

a. `dev-kit/dpg/reach_layer.yaml` — append under `channels.voice`:

```yaml
recording:
  source: disabled
  consent_purpose: recording
  webhook_timeout_s: 30
  fetch_timeout_s: 60
  min_duration_ms: 500
  caller_id_hash_salt: ""
  store:
    backend: local
    local:
      base_path: /var/recordings
    s3:
      bucket: ""
      prefix: recordings/
      region: ap-south-1
      kms_key_id: ""
```

b. `dev-kit/dev_kit/agent/accumulator.py` — extend the existing secrets list/registry:

```python
SECRET_PATHS.update({
    "reach_layer.channels.voice.recording.caller_id_hash_salt",
    "reach_layer.channels.voice.recording.store.s3.kms_key_id",
})
```

(Use whatever pattern the existing module already uses — look for similar entries for `vobiz.auth_token`.)

c. `dev-kit/dev_kit/agent/renderer.py` — when rendering voice YAML, omit the `recording:` block iff `recording.source == "disabled"` and all sub-fields are at defaults; otherwise render the full block.

d. `dev-kit/dev_kit/agent/tools.py` — add:

```python
import secrets

def _prompt(label: str, default: str = "") -> str:  # may already exist
    ...

def collect_recording_settings(existing: dict) -> dict:
    """Wizard phase: gather recording config interactively."""
    source = existing.get("source") or _prompt(
        "Recording source (disabled/vobiz/pipeline)", "disabled",
    )
    out: dict = {"source": source}
    if source == "disabled":
        return out
    salt = existing.get("caller_id_hash_salt") or _prompt(
        "caller_id_hash_salt (leave empty to autogenerate)", "",
    )
    if not salt:
        salt = secrets.token_hex(32)
    out["caller_id_hash_salt"] = salt
    backend = existing.get("store", {}).get("backend") or _prompt(
        "Store backend (local/s3)", "local",
    )
    store: dict = {"backend": backend}
    if backend == "s3":
        store["s3"] = {
            "bucket": _prompt("S3 bucket", ""),
            "prefix": _prompt("S3 prefix", "recordings/"),
            "region": _prompt("S3 region", "ap-south-1"),
            "kms_key_id": _prompt("KMS key id (optional)", ""),
        }
    out["store"] = store
    return out
```

Wire `collect_recording_settings` into the wizard's voice phase.

- [ ] **Step 4: Run tests, verify passing**

```bash
cd dev-kit && uv run pytest agent/tests/ tests/schemas/ -v
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dpg/reach_layer.yaml dev-kit/dev_kit/agent/ dev-kit/agent/tests/
git commit -m "$(cat <<'EOF'
feat(devkit): wizard + YAML defaults for voice recording (#322)

Adds the recording: block to dpg/reach_layer.yaml with safe defaults
(source: disabled), marks caller_id_hash_salt and kms_key_id as
secrets in the accumulator, omits the block from rendered YAML when
disabled, and adds collect_recording_settings to the wizard.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: End-to-end smoke + docs

**Files:**
- Test: `reach_layer/voice/tests/test_recording_e2e.py` (create)
- Modify: `reach_layer/voice/README.md` (Recording section)
- Modify: `ARCHITECTURE.md` (note recording is now ✅ in the Reach Layer summary if applicable)

- [ ] **Step 1: Write the e2e smoke test**

```python
# reach_layer/voice/tests/test_recording_e2e.py
"""End-to-end smoke: pipeline source + LocalFileStore + ConsentEvent path."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.recordings.factory import build_recording_manager
from reach_layer_base import ConsentEvent


@pytest.mark.asyncio
async def test_pipeline_local_full_lifecycle(tmp_path: Path):
    cfg = {"reach_layer": {"channels": {"voice": {
        "vobiz": {"auth_id": "A", "auth_token": "T", "sample_rate": 8000},
        "recording": {
            "source": "pipeline", "consent_purpose": "recording",
            "webhook_timeout_s": 5.0, "fetch_timeout_s": 5.0, "min_duration_ms": 1,
            "caller_id_hash_salt": "s" * 32,
            "store": {"backend": "local",
                       "local": {"base_path": str(tmp_path)},
                       "s3": {"bucket": "", "prefix": "", "region": "", "kms_key_id": ""}},
        },
    }}}}
    manager = build_recording_manager(
        cfg, telephony=None, registry={}, call_sid="CA-E2E",
        session_id="sess", caller_id="+910000000000", vobiz_call_id="",
    )
    # Simulate the consent listener calling start on a granted ConsentEvent.
    evt = ConsentEvent(purpose="recording", granted=True, consent_granted_ts=1.0)
    assert evt.granted
    await manager.start(consent_granted_ts=evt.consent_granted_ts)

    # Feed audio frames through the tap processor.
    proc = manager.pipeline_processors[0]
    from pipecat.frames.frames import InputAudioRawFrame
    for _ in range(20):
        await proc.process_frame(
            InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1),
            direction=None,
        )
    await asyncio.sleep(0.01)
    await manager.stop()
    artifact = await manager.finalize()
    assert artifact is not None
    assert artifact.format == "wav"
    # File exists under tmp_path/YYYY/MM/DD/CA-E2E.wav
    matches = list(tmp_path.rglob("CA-E2E.wav"))
    assert len(matches) == 1
    sidecar = matches[0].with_suffix(".json")
    assert sidecar.exists()
```

- [ ] **Step 2: Run the suite, verify the e2e passes**

```bash
cd reach_layer/voice && uv run pytest tests/test_recording_e2e.py -v
cd reach_layer/voice && uv run pytest tests/ --cov=src/recordings --cov-report=term-missing
```
Expected: e2e passes; coverage on `src/recordings` ≥70%.

- [ ] **Step 3: Update docs**

`reach_layer/voice/README.md` — add a "Recording" section describing the config block, both source modes, sidecar schema, and the consent gating contract. Reference the spec at `docs/superpowers/specs/2026-05-08-voice-call-recording-design.md`.

`ARCHITECTURE.md` — under the Reach Layer voice line, add: "Call recording (audit) — ✅ behind `reach_layer.channels.voice.recording.source` config switch (default: disabled)".

- [ ] **Step 4: Commit**

```bash
git add reach_layer/voice/tests/test_recording_e2e.py reach_layer/voice/README.md ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
test+docs(reach-voice): e2e smoke for recording + README/ARCH updates (#322)

Adds an end-to-end smoke that drives the pipeline source through start,
audio frames, stop, and finalize against a LocalFileStore in tmp_path,
asserting the artifact + sidecar land on disk. README gains a Recording
section; ARCHITECTURE.md notes the new capability.

Refs #322

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Open the pull request

- [ ] **Step 1: Push the branch**

```bash
git push -u origin 322-voice-call-recording
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Voice call recording for audit (#322)" --body "$(cat <<'EOF'
## Summary
- Adds a pluggable `RecordingManagerBase` + `RecordingSourceBase` + `RecordingStoreBase` under `reach_layer/voice/src/recordings/` with concrete Vobiz REST and Pipecat-tap sources, plus local and S3 stores.
- Trust Layer consent (`purpose=recording`) gates recording start; Agent Core emits a new `ConsentEvent` SSE type that `VobizAdapter` listens for.
- Sidecar JSON manifest + Observability signals (`recording.started/stored/empty/failed`) + OTel `recording.lifecycle` span linked to the inbound call span.
- dev-kit Pydantic schemas, cross-block validation, wizard wiring, and YAML defaults so the new `recording:` block validates and is reachable from the Configuration Agent.

Default = `recording.source: disabled` — no behaviour change for existing deployments.

## Test plan
- [ ] `cd reach_layer/voice && uv run pytest`
- [ ] `cd reach_layer/base && uv run pytest`
- [ ] `cd agent_core && uv run pytest`
- [ ] `cd dev-kit && uv run pytest`
- [ ] Full-config validation passes against `dev-kit/configs/kkb/`
- [ ] Manual: run docker stack with `recording.source: pipeline` + `store.backend: local`, place a call, verify `<tmp>/YYYY/MM/DD/<call_sid>.wav` and sidecar appear after hangup
- [ ] Manual: same with `source: vobiz` after granting consent; verify `/recording-ready` resolves the future and the MP3 is fetched

Spec: `docs/superpowers/specs/2026-05-08-voice-call-recording-design.md`. Refs #322.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** §3 decisions all map to tasks (sources → 5+6, stores → 7+8, consent gate → 1+2, sidecar+signals → 7/8/11, retention out-of-band → no task, by design); §6 schemas → Task 3; §6.1 startup validation → Task 10; §6.2 wizard wiring → Task 14; §10 telemetry → Task 11 + 13; §11 testing matrix → Tasks 4–11 + 15.
- **Type consistency:** `RecordingPayload(bytes_data=...)` used everywhere; `source` literal `"vobiz"|"pipeline"`; `format` literal `"mp3"|"wav"`; signal type strings (`recording.started/stored/empty/failed`) consistent across telemetry helper and emitter calls.
- **Atomic constraint:** Task 12 + 13 must be committed together (the abstract property breaks `VobizAdapter` until 13 implements it). The plan calls this out and combines the commit.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-voice-call-recording.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
