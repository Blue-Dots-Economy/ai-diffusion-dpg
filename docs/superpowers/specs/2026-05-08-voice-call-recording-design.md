# Voice Call Recording for Audit — Design

**Status:** Approved (brainstorming)
**Block:** Reach Layer / Voice
**Date:** 2026-05-08

## 1. Goal

Capture and persist a per-call audio recording for the Vobiz voice channel so operators have an auditable artifact of every consented call. Recording must be:

- Privacy-compliant: only after Trust Layer consent for `purpose=recording` is granted.
- Vendor-flexible: support both telephony-native (Vobiz) and pipeline-tap capture sources behind a config switch.
- Storage-pluggable: write to local disk or S3-compatible object storage via the same interface.
- Out-of-band: failures must never break the call; finalize and upload run after the websocket closes.

## 2. Non-goals

- ASR transcript persistence (already handled by Memory/Observability layers).
- Real-time streaming of audio to a downstream consumer.
- Per-channel speaker separation. The pipeline source emits a single mixed-mono WAV; vobiz native produces a single MP3 per Vobiz's defaults.
- Built-in retention enforcement. Retention is delegated to the storage backend (S3 lifecycle rules, ops cron, etc.).
- Recording for non-voice channels (web, CLI, WhatsApp).

## 3. Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Audio source | Both Vobiz native and Pipecat pipeline tap, selected via `recording.source` config |
| Storage backend | Pluggable `RecordingStoreBase` with `LocalFileStore` and `S3Store` implementations |
| Consent model | Trust Layer consent gate, `purpose=recording`; recording starts only after grant |
| Recording window | Starts on consent grant; ends on call termination (greeting/consent prompt NOT recorded) |
| Metadata | Sidecar JSON manifest **and** Observability `recording.stored` event |
| Retention | Out-of-band (storage lifecycle policy); not enforced by app code |

## 4. Architecture

New directory `reach_layer/voice/src/recordings/` mirrors the existing `operators/` and `pipecat_services/` layout. Each layer defines its ABC first per `.claude/rules/base-class-pattern.md`.

```
reach_layer/voice/src/recordings/
  __init__.py
  manager_base.py            # RecordingManagerBase (ABC) + RecordingArtifact, RecordingPayload dataclasses
  manager.py                 # RecordingManager (default concrete) + NullRecordingManager
  factory.py                 # build_recording_manager(config, telephony=...)
  sources/
    __init__.py
    source_base.py           # RecordingSourceBase (ABC)
    vobiz_source.py          # VobizRecordingSource (Vobiz REST start/stop)
    pipeline_source.py       # PipelineRecordingSource (owns the Pipecat tap processor)
  stores/
    __init__.py
    store_base.py            # RecordingStoreBase (ABC)
    local_store.py           # LocalFileStore
    s3_store.py              # S3Store (aiobotocore)
```

Plus one new pipecat service:

```
reach_layer/voice/src/pipecat_services/recording_tap.py
  RecordingTapProcessor      # subscribes to InputAudioRawFrame + OutputAudioRawFrame
```

### TelephonyAdapterBase contract change

`TelephonyAdapterBase` (`reach_layer/voice/src/base.py`) gets a new abstract property:

```python
@property
@abstractmethod
def recording_manager(self) -> RecordingManagerBase:
    """The RecordingManagerBase instance owning this call's recording.

    Adapters that do not support recording must return a NullRecordingManager —
    never None — so callers can dispatch unconditionally.
    """
```

`VobizAdapter` constructs the manager in `__init__` via `build_recording_manager(config, telephony=self)` and exposes it through this property. `NullRecordingManager` (a real concrete that returns `None` from `finalize()` and stays in `idle`) is used when `recording.source: disabled`.

### Component hierarchy

```
VobizAdapter ── owns ──▶ RecordingManager : RecordingManagerBase
                           │
                           ├── RecordingSourceBase
                           │     ├── VobizRecordingSource
                           │     └── PipelineRecordingSource
                           │           └── RecordingTapProcessor (Pipecat FrameProcessor)
                           │
                           └── RecordingStoreBase
                                 ├── LocalFileStore
                                 └── S3Store
```

The package's public surface is the three base classes plus the factory. Concrete classes are referenced only through the factory.

## 5. Component contracts

### RecordingManagerBase

```python
class RecordingManagerBase(ABC):
    @abstractmethod
    async def start(self, *, consent_granted_ts: float) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...
    @abstractmethod
    async def finalize(self) -> RecordingArtifact | None: ...
    @property
    @abstractmethod
    def state(self) -> Literal["idle", "recording", "stopped", "finalized", "failed"]: ...
    @property
    @abstractmethod
    def pipeline_processors(self) -> list: ...   # Pipecat processors to splice; [] for vobiz/null
```

`RecordingArtifact` (dataclass): `call_sid`, `session_id`, `caller_id_hash`, `start_ts`, `end_ts`, `duration_ms`, `consent_granted_ts`, `source` (`"vobiz" | "pipeline"`), `format` (`"mp3" | "wav"`), `sha256`, `payload: RecordingPayload`.

`RecordingPayload` (dataclass): one of `bytes` (in-memory) or `fetch_url` (Vobiz remote URL). The store decides how to consume.

### RecordingSourceBase

```python
class RecordingSourceBase(ABC):
    @abstractmethod
    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None: ...
    @abstractmethod
    async def end(self) -> RecordingPayload: ...
    @property
    @abstractmethod
    def pipeline_processors(self) -> list: ...
```

- **VobizRecordingSource.begin()** — `POST https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_id}/Record/` (Plivo-compatible) with `record_session=true`, `time_limit=<config>`, `transcription=false`, `callback_url=<server>/recording-ready`. Registers a future in the call-scoped registry.
- **VobizRecordingSource.end()** — `POST .../Record/Stop/`; returns `RecordingPayload(fetch_url=<await future, with timeout>)`.
- **PipelineRecordingSource.begin()** — flips `RecordingTapProcessor._active = True` and stamps the WAV writer's start time.
- **PipelineRecordingSource.end()** — flips `_active = False`, closes the WAV, returns `RecordingPayload(bytes=<wav_bytes>)`.

### RecordingTapProcessor

A Pipecat `FrameProcessor` always present in the pipeline when `recording.source: pipeline`. Subscribes to `InputAudioRawFrame` and `OutputAudioRawFrame`. While `_active` is True, sums frames into a software-mixed mono PCM stream and writes via `wave.Wave_write` at the operator's negotiated `sample_rate`. While `_active` is False, frames pass through untouched.

Placement: between `tts` and `transport.output()` so it sees both the inbound stream (which has already passed VAD/STT) and the outbound TTS audio.

### RecordingStoreBase

```python
class RecordingStoreBase(ABC):
    @abstractmethod
    async def put(self, artifact: RecordingArtifact) -> str: ...   # returns recording_uri
```

- **LocalFileStore** — writes `{base_path}/{YYYY}/{MM}/{DD}/{call_sid}.{ext}` plus `{call_sid}.json` sidecar. Streaming sha256. Parent dirs created at `0o750`.
- **S3Store** — `aiobotocore` with explicit timeout and one retry on transient failures. Key `{prefix}/{YYYY}/{MM}/{DD}/{call_sid}.{ext}`; sidecar is a sibling object. SSE-S3 default; SSE-KMS if `recording.s3.kms_key_id` set.

### build_recording_manager(config, telephony)

Factory that:
1. Reads `config.reach_layer.channels.voice.recording`.
2. Validates required fields up-front (raises at startup, never mid-call): `caller_id_hash_salt` non-empty when source != disabled; `s3.bucket` non-empty when backend = s3; performs `head_bucket` to validate S3 credentials.
3. Returns `NullRecordingManager` if `source: disabled` (default).
4. Otherwise constructs the configured `RecordingSourceBase` + `RecordingStoreBase` and returns a `RecordingManager` wrapping them.

## 6. Configuration

Added under `reach_layer.channels.voice` in the framework defaults (`dev-kit/dpg/reach_layer.yaml`) with safe defaults; domain configs override per deployment.

```yaml
reach_layer:
  channels:
    voice:
      recording:
        source: disabled                    # disabled | vobiz | pipeline
        consent_purpose: recording          # Trust Layer purpose tag
        webhook_timeout_s: 30               # vobiz finalize timeout
        fetch_timeout_s: 60                 # MP3 fetch timeout (vobiz)
        min_duration_ms: 500                # below this, emit recording.empty instead of storing
        caller_id_hash_salt: ""             # required when source != disabled
        store:
          backend: local                    # local | s3
          local:
            base_path: /var/recordings
          s3:
            bucket: ""
            prefix: recordings/
            region: ap-south-1
            kms_key_id: ""                  # optional; SSE-S3 if empty
```

Default = `disabled` so no behaviour changes for existing deployments.

### 6.1 Schema validation (dev-kit Pydantic)

The `dev-kit` Pydantic schema is the source of truth for accepted YAML keys. Every model uses `ConfigDict(extra="forbid")`, so any unrecognised key under `reach_layer.channels.voice` would fail `MergedConfig.validate_full()` at service startup (see `dev-kit/dev_kit/schemas/dpg/reach_layer.py`). New keys MUST be added to the schema in the same change as the YAML defaults, otherwise the voice service refuses to boot.

Required additions to `dev-kit/dev_kit/schemas/dpg/reach_layer.py`:

```python
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
    model_config = ConfigDict(extra="forbid")
    source: Literal["disabled", "vobiz", "pipeline"] = "disabled"
    consent_purpose: str = "recording"
    webhook_timeout_s: float = 30.0
    fetch_timeout_s: float = 60.0
    min_duration_ms: int = 500
    caller_id_hash_salt: str = ""
    store: RecordingStoreDpg = Field(default_factory=RecordingStoreDpg)
```

`VoiceDpg` gains:

```python
recording: RecordingDpg = Field(default_factory=RecordingDpg)
```

Default-factory means existing domain configs that omit `recording:` continue to validate.

Cross-block validation hooks (`dev-kit/dev_kit/schemas/cross_block_validation.py`):

- If `voice.recording.source != "disabled"` then `voice.recording.caller_id_hash_salt` must be non-empty.
- If `voice.recording.store.backend == "s3"` then `voice.recording.store.s3.bucket` must be non-empty.
- (Optional) If `voice.recording.source != "disabled"` warn when no Trust Layer consent rule covers `purpose=recording` — keeps consent gate honest at config time, before runtime.

### 6.2 Configuration Agent (dev-kit/agent) wiring

The Configuration Agent (`dev-kit/dev_kit/agent/`) is Tier 1 of the three-tier config model (per `CLAUDE.md`) and emits the YAML that becomes the runtime source of truth. To keep the wizard usable for operators turning recording on:

- `dev_kit/agent/accumulator.py` — extend the channel-secrets accumulator to recognise `voice.recording.caller_id_hash_salt` (treat it as a secret, never echo back) and the S3 credentials bucket/region/kms_key_id (kms_key_id is a secret reference, not a value).
- `dev_kit/agent/renderer.py` — render a `recording:` block under `reach_layer.channels.voice` when the operator opts in via the wizard. Default rendering omits the block entirely (so default = disabled is the YAML default too).
- `dev_kit/agent/tools.py` — add a tool/phase handler that prompts for: source, store backend, S3 bucket/prefix/region (if backend=s3), salt (autogenerated if not supplied; minimum 32 chars). Existing phase-prompt tests (`tests/agent/test_phase_prompts_use_schemas.py`) must cover the new schema additions so wizard prompts stay in sync with `RecordingDpg`.
- Update domain config templates if any default deployment (e.g. `dev-kit/configs/kkb/reach_layer.yaml`) needs an opted-in starting point.

The dev-kit changes are part of this feature's implementation plan, not a follow-up — without them, an operator using the wizard cannot enable recording without hand-editing YAML.

## 7. Data flow

```
Caller answers (Vobiz POST /answer)
  → server.py returns <Stream> XML  (no <Record> verb — both sources start mid-call)
WS connects → VobizAdapter.handle_call
  ├─ build pipeline (RecordingTapProcessor present iff source=pipeline, _active=False)
  ├─ greeting / opening_phrase
  ├─ Trust Layer consent prompt for purpose=recording   (existing flow)
  ├─ user grants → Agent Core emits ConsentEvent(purpose=recording, granted=True)
  └─ adapter listener → recording_manager.start(consent_granted_ts=now)
       ├─ vobiz   → POST /Record/ (start)         state: recording
       └─ pipeline → tap._active = True            state: recording

…turns continue, audio captured…

Call ends (caller hangup OR DoneEvent.session_ended OR EndFrame)
  on_client_disconnected → task.cancel() → handle_call returns → run_bot.finally → teardown()
  teardown():
    asyncio.create_task( _finalize_and_store(call_sid) )    ← non-blocking
  background task:
    manager.stop()
      ├─ vobiz   → POST /Record/Stop/             state: stopped
      └─ pipeline → tap._active=False, close WAV  state: stopped
    manager.finalize()
      ├─ vobiz   → await future from /recording-ready  (timeout: webhook_timeout_s)
      │            → fetch MP3 from recordingUrl       (timeout: fetch_timeout_s, 1 retry)
      │            → bytes + sha256 → RecordingArtifact
      └─ pipeline → bytes already in memory
                  → sha256 → RecordingArtifact         state: finalized
    store.put(artifact) → recording_uri
    write sidecar JSON manifest (same fields as artifact, plus recording_uri)
    emit Observability event "recording.stored"
                  {call_sid, session_id, caller_id_hash, source, format,
                   duration_ms, bytes, sha256, recording_uri,
                   consent_granted_ts, start_ts, end_ts}
```

### server.py webhook rewire

```python
# previously: log-only
@app.post("/recording-ready")
async def recording_ready(body: RecordingWebhook) -> dict:
    fut = _recording_url_registry.pop(body.callSid, None)
    if fut and not fut.done():
        fut.set_result(body.recordingUrl)
    logger.info("server.recording_ready", extra={...})
    return {"status": "ok"}
```

`_recording_url_registry: dict[str, asyncio.Future[str]]` — module-level dict registered into the app via `create_app()`. `VobizRecordingSource.begin()` inserts; `finalize()` awaits; the webhook handler resolves. `/recording-finished` stays log-only (it just signals stop; we already know).

### Caller-id hashing

`caller_id_hash = sha256(caller_id_hash_salt + caller_id).hexdigest()[:16]`. Raw E.164 must never appear in the manifest, the `recording.stored` event, or any other persisted artifact.

## 8. Error handling

Per `.claude/rules/error-handling.md` — every external call has explicit timeout + retry + structured error. A failed recording **never breaks the call**.

| Call | Timeout | Retry | On failure |
|---|---|---|---|
| `POST /Record/` (Vobiz) | 5s | 1 (exp backoff) | `start()` returns; state→`failed`; log `recording.start_failed`; call continues |
| `POST /Record/Stop/` (Vobiz) | 5s | 1 | log `recording.stop_failed`; finalize still attempts the webhook future |
| Webhook future (`/recording-ready`) | `webhook_timeout_s` | none | state→`failed`; emit `recording.failed` |
| Fetch MP3 from `recordingUrl` | `fetch_timeout_s` | 1 on transient | state→`failed`; emit `recording.failed` |
| `LocalFileStore.put` | n/a | none | log + `recording.failed`; partial files removed |
| `S3Store.put` | 30s per part | 1 on transient (`Throttling`, `503`, network) | log + `recording.failed` |

All failures route through `_finalize_and_store`'s outer `try/except` which logs structured failure and emits `recording.failed`. Errors never re-raise into `run_bot`.

## 9. Edge cases

| Case | Behaviour |
|---|---|
| Consent never granted | `start()` never called; `finalize()` is a no-op; no event |
| Consent granted, then call drops 200ms later | `start()` may be in-flight; `stop()` always called; if vobiz REST start hadn't returned 200, finalize sees no resolvable future and short-circuits with `recording.empty` |
| Call ends before consent prompt completes | Same as "never granted" |
| Vobiz webhook never fires | Timeout → `recording.failed` |
| Pipeline source, zero audio frames captured | WAV closes 0-byte; `duration_ms < min_duration_ms` → emit `recording.empty`, don't store |
| `recording.source: disabled` | `NullRecordingManager` short-circuits; zero new I/O |
| Adapter teardown crashes mid-finalize | Background task's outer `except` logs + emits `recording.failed`; never propagated |
| Two concurrent calls writing to same path | Path includes `call_sid`; provider invariant prevents collision |
| `caller_id_hash_salt` empty when recording enabled | `build_recording_manager()` raises at startup |
| S3 credentials missing | Validated at startup via `head_bucket`; raises before any call accepted |

## 10. Logging and observability

Per `.claude/rules/logging-observability.md`. Three layers of telemetry are emitted, and all three are mandatory for the recording subsystem so audit reviewers can correlate a stored artifact end-to-end.

### 10.1 Structured logs

Every significant operation emits a structured log entry with the canonical fields: `operation`, `status` (`success` | `failure` | `skipped`), `latency_ms` (for any external call), `error` (failures only), plus recording-specific fields: `call_sid`, `session_id`, `caller_id_hash` (never raw `caller_id`), `recording_source`, `recording_state`. Required log keys per stage:

| Operation | Required extra fields |
|---|---|
| `recording.start` | `consent_granted_ts`, `recording_source` |
| `recording.stop` | `recording_source`, `duration_ms` |
| `recording.finalize` | `recording_source`, `format`, `bytes`, `sha256`, `latency_ms` |
| `recording.store_put` | `store_backend`, `recording_uri`, `bytes`, `latency_ms` |
| `recording.webhook_received` | `webhook_path`, `vobiz_call_id` |
| `recording.start_failed` / `recording.stop_failed` / `recording.finalize_failed` / `recording.store_failed` | `error`, `stage`, `recording_source` |

No logger may emit raw `caller_id`, `recordingUrl` query params, or audio bytes. The `recording_uri` returned by the store is opaque to logs (full URI is fine; pre-signed URLs are not generated here).

### 10.2 OpenTelemetry spans

Per the existing pattern in `reach_layer/voice/server.py` (`reach.inbound` parent span). The recording subsystem adds a span hierarchy nested under the per-call inbound span:

```
reach.inbound                                  (existing, in server.py)
└── recording.lifecycle                        (new; parent of all recording spans)
    ├── recording.start                        (vobiz REST start OR pipeline tap activate)
    │   └── recording.vobiz.rest_start         (vobiz only — outgoing HTTP)
    ├── recording.stop                         (REST stop OR pipeline tap deactivate)
    │   └── recording.vobiz.rest_stop          (vobiz only)
    ├── recording.finalize                     (whole finalize phase)
    │   ├── recording.vobiz.await_webhook      (vobiz only)
    │   └── recording.vobiz.fetch_mp3          (vobiz only — outgoing HTTP)
    └── recording.store_put                    (always)
        └── recording.s3.put_object            (s3 backend only)
```

Span attributes (all spans):

- `dpg.block` = `"reach_layer"`
- `dpg.channel` = `"voice"`
- `dpg.subsystem` = `"recording"`
- `recording.source` = `"vobiz" | "pipeline"`
- `recording.state` (set at span end) = `"recording" | "stopped" | "finalized" | "failed"`
- `call_sid`, `session_id`, `caller_id_hash`

Stage-specific attributes:

- `recording.start`: `consent_granted_ts`
- `recording.finalize`: `recording.format`, `recording.bytes`, `recording.duration_ms`, `recording.sha256`
- `recording.store_put`: `store.backend` (`"local" | "s3"`), `recording.uri`
- `recording.s3.put_object`: `aws.s3.bucket`, `aws.s3.key`, `aws.s3.sse` (`"AES256" | "aws:kms"`)

Errors: any failure path calls `span.record_exception(exc)` and `span.set_status(StatusCode.ERROR, description=...)`. The failure is also re-emitted as an `recording.failed` observability signal (10.3) so query paths that don't aggregate spans still see it.

The `recording.lifecycle` span runs inside the background `_finalize_and_store` task spawned from `teardown()`. Because it post-dates `reach.inbound`'s natural end, it links to the inbound span via `Link` rather than parent/child to preserve the inbound span's latency reporting:

```python
with otel_trace.get_tracer(__name__).start_as_current_span(
    "recording.lifecycle",
    links=[Link(inbound_span_context)],
    attributes={"call_sid": call_sid, ...},
) as span:
    ...
```

The `inbound_span_context` is captured by `VobizAdapter.handle_call()` and stashed on the manager before teardown.

### 10.3 Observability Layer signals

Per `ARCHITECTURE.md`, the Observability Layer is async-only; signals are emitted via `ObservabilityLayerBase.emit_signal(signal_type, data)` from `_finalize_and_store`'s background task — never in the response path.

| Signal type | When | Required `data` fields |
|---|---|---|
| `recording.started` | After `manager.start()` succeeds | `call_sid`, `session_id`, `caller_id_hash`, `source`, `consent_granted_ts`, `start_ts` |
| `recording.stored` | Terminal success | `call_sid`, `session_id`, `caller_id_hash`, `source`, `format`, `duration_ms`, `bytes`, `sha256`, `recording_uri`, `consent_granted_ts`, `start_ts`, `end_ts`, `store_backend` |
| `recording.empty` | Below `min_duration_ms` or zero frames | `call_sid`, `session_id`, `source`, `duration_ms`, `reason` |
| `recording.failed` | Any terminal failure | `call_sid`, `session_id`, `source`, `stage` (`start` \| `stop` \| `finalize` \| `store`), `error_type`, `error_message` |

The Reach Layer voice service obtains its `ObservabilityLayerBase` instance via the standard factory used elsewhere in the module (e.g. the existing telemetry path in `dpg_telemetry`). If the observability backend is unreachable, the helper logs `recording.signal_emit_failed` and continues — never re-raises — so a downstream outage cannot block call teardown.

### 10.4 Sidecar JSON manifest

The sidecar JSON written next to the audio object is the third audit channel and is independent of the OTel/Observability backends (so the artifact remains self-describing even if telemetry is lost). Schema:

```json
{
  "schema_version": "1.0",
  "call_sid": "CA...",
  "session_id": "uuid",
  "caller_id_hash": "16-hex",
  "source": "vobiz",
  "format": "mp3",
  "duration_ms": 184320,
  "bytes": 1474656,
  "sha256": "hex",
  "recording_uri": "s3://bucket/key.mp3",
  "consent_granted_ts": 1746748800.123,
  "start_ts": 1746748800.456,
  "end_ts": 1746748984.776,
  "store_backend": "s3",
  "trace_id": "32-hex",
  "schema_url": "https://.../recording-manifest/v1.json"
}
```

`trace_id` is the OTel trace ID of the `reach.inbound` span, enabling cross-reference from the artifact back to the trace.

## 11. Testing

Per `.claude/rules/testing-requirements.md`. New tests under `reach_layer/voice/tests/recordings/` mirror the source layout.

| File | Coverage |
|---|---|
| `test_manager_base.py` | ABC contract; NullRecordingManager idempotence |
| `test_manager.py` | State machine transitions; idle→stopped no-op; failure paths |
| `test_vobiz_source.py` | REST payloads (mocked aiohttp); timeout; retry; webhook future resolution; webhook timeout |
| `test_pipeline_source.py` | Frame tap byte counts; WAV header correctness; `_active=False` discards frames; zero-frame edge case |
| `test_local_store.py` | Directory creation, sha256 streaming, sidecar shape |
| `test_s3_store.py` | aiobotocore stubbed; key + content-type + SSE headers; retry on `Throttling` |
| `test_factory.py` | Config validation: missing salt; unknown source; unknown backend; disabled returns Null |
| `test_vobiz_adapter_recording.py` | Pipeline includes tap when source=pipeline; consent event triggers `start`; teardown spawns background finalize; failures don't propagate |
| `test_server_recording_webhook.py` | `/recording-ready` resolves registered future; unknown call_sid logged but returns 200 |
| `test_recording_telemetry.py` | OTel span tree (using `InMemorySpanExporter`): `recording.lifecycle` linked to inbound span; required attributes present; failure paths set `StatusCode.ERROR`. Observability `emit_signal` called with correct types/fields for `started` / `stored` / `empty` / `failed` |

Coverage target: ≥70% line coverage on the new package.

Mock strategy: aiohttp via `aioresponses`; aiobotocore via `moto` or hand-rolled stub session; no real network in unit tests.

## 12. Affected files

| File | Change |
|---|---|
| `reach_layer/voice/src/base.py` | Add `recording_manager` abstract property |
| `reach_layer/voice/src/vobiz_adapter.py` | Construct manager in `__init__`; expose property; splice pipeline processors; add ConsentEvent listener; spawn `_finalize_and_store` from teardown |
| `reach_layer/voice/server.py` | Rewire `/recording-ready` to resolve registry future; register `_recording_url_registry` on app |
| `reach_layer/voice/src/recordings/**` | New package (this design) |
| `reach_layer/voice/src/pipecat_services/recording_tap.py` | New `RecordingTapProcessor` |
| `reach_layer_base/events.py` (or equivalent) | New `ConsentEvent(purpose, granted, ts)` |
| `agent_core/src/orchestrator/consent_gate.py` (or wherever consent verifies) | Emit `ConsentEvent` over the SSE stream after `/consent/verify` succeeds for `purpose=recording` |
| `dev-kit/dpg/reach_layer.yaml` | Add `recording:` block with safe defaults (`source: disabled`) |
| `dev-kit/configs/kkb/reach_layer.yaml` | Optional override per deployment |
| `dev-kit/dev_kit/schemas/dpg/reach_layer.py` | Add `RecordingDpg` / `RecordingStoreDpg` / `RecordingLocalDpg` / `RecordingS3Dpg`; attach to `VoiceDpg` |
| `dev-kit/dev_kit/schemas/cross_block_validation.py` | Add salt + S3 bucket required-when-enabled rules; consent-rule presence warning |
| `dev-kit/dev_kit/agent/accumulator.py` | Mark salt + KMS as secrets |
| `dev-kit/dev_kit/agent/renderer.py` | Render `recording:` block when operator opts in |
| `dev-kit/dev_kit/agent/tools.py` | Wizard phase to collect recording inputs (source, store, S3, salt) |
| `dev-kit/tests/schemas/dpg/test_dpg_schemas.py` | Cover new schema models |
| `dev-kit/tests/schemas/test_cross_block_validation.py` | Cover new cross-block rules |
| `dev-kit/tests/agent/test_phase_prompts_use_schemas.py` | Cover new wizard phase |
| `reach_layer/voice/tests/recordings/**` | New test package |
| `reach_layer/voice/tests/test_server.py` | Extend webhook tests for future resolution |
| `reach_layer/voice/tests/test_vobiz_adapter.py` | Extend with recording wiring tests |

## 13. Out of scope / follow-ups

- Web/CLI/WhatsApp recording (different channels, different mechanics).
- Speaker diarisation / channel split.
- Real-time recording streaming.
- App-side retention enforcement.
- Vobiz `<Record>` verb in `/answer` XML (we use the REST start API instead, which is mid-call-friendly and consent-gated).
