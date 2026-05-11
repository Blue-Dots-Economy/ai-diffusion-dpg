"""build_recording_manager — config-driven manager construction.

Belongs to the Reach Layer / Voice channel in the DPG framework.
Reads the ``reach_layer.channels.voice.recording`` config section, validates
required fields at startup, and returns either a NullRecordingManager (when
source == disabled) or a fully-wired RecordingManager.
"""
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
    """Return a deterministic 16-hex-char SHA256 hash of (salt + caller_id).

    Args:
        salt: Per-deployment secret string to prevent cross-deployment linkage.
        caller_id: Raw E.164 caller identifier string.

    Returns:
        First 16 hex characters of SHA256(salt + caller_id).
    """
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
    """Construct the recording manager (or NullRecordingManager) from config.

    Validates required fields at startup:
    - ``caller_id_hash_salt`` must be non-empty when ``source != disabled``.
    - ``store.s3.bucket`` must be non-empty when ``store.backend == 's3'``.

    Raises ``ValueError`` on any validation failure so the voice service
    refuses to boot with an unsafe recording config.

    Args:
        config: Full merged YAML config dict.
        telephony: Telephony adapter instance (unused, reserved for future use).
        registry: Shared webhook future registry for VobizRecordingSource.
        call_sid: Telephony call identifier.
        session_id: Agent Core session identifier.
        caller_id: Raw caller E.164 number; hashed before storage.
        vobiz_call_id: Vobiz-side call ID (empty when using pipeline source).
        callback_url: Webhook callback URL for Vobiz recording notifications.

    Returns:
        NullRecordingManager when source is disabled, RecordingManager otherwise.

    Raises:
        ValueError: If source is unknown, salt is missing when enabled, or
            s3.bucket is missing when backend is s3.
    """
    voice = config.get("reach_layer", {}).get("channels", {}).get("voice", {})
    rec = voice.get("recording", {}) or {}
    source = rec.get("source", "disabled")

    if source == "disabled":
        return NullRecordingManager()

    if source not in ("vobiz", "pipeline"):
        raise ValueError(
            f"recording.source must be one of disabled|vobiz|pipeline, got {source!r}"
        )

    salt = rec.get("caller_id_hash_salt", "")
    if not salt:
        raise ValueError(
            "recording.caller_id_hash_salt must be set when recording is enabled"
        )

    store_cfg = rec.get("store", {}) or {}
    backend = store_cfg.get("backend", "local")

    if backend == "local":
        local_cfg = store_cfg.get("local", {}) or {}
        store = LocalFileStore(
            base_path=local_cfg.get("base_path", "/var/recordings")
        )
    elif backend == "s3":
        s3_cfg = store_cfg.get("s3", {}) or {}
        if not s3_cfg.get("bucket"):
            raise ValueError(
                "recording.store.s3.bucket must be set when backend=s3"
            )
        store = S3Store(
            bucket=s3_cfg["bucket"],
            prefix=s3_cfg.get("prefix", "recordings/"),
            region=s3_cfg.get("region", "ap-south-1"),
            kms_key_id=s3_cfg.get("kms_key_id", ""),
        )
    else:
        raise ValueError(
            f"recording.store.backend must be local|s3, got {backend!r}"
        )

    vobiz_cfg = voice.get("vobiz", {}) or {}
    sample_rate = int(vobiz_cfg.get("sample_rate", 8000))

    if source == "pipeline":
        src_obj = PipelineRecordingSource(sample_rate=sample_rate)
        fmt = "wav"
    else:
        src_obj = VobizRecordingSource(
            auth_id=vobiz_cfg.get("auth_id", ""),
            auth_token=vobiz_cfg.get("auth_token", ""),
            callback_url=callback_url,
            webhook_timeout_s=float(rec.get("webhook_timeout_s", 30.0)),
            fetch_timeout_s=float(rec.get("fetch_timeout_s", 60.0)),
            registry=registry,
        )
        fmt = "mp3"

    return RecordingManager(
        source=src_obj,
        store=store,
        call_sid=call_sid,
        session_id=session_id,
        caller_id_hash=hash_caller_id(salt, caller_id),
        source_name=source,
        fmt=fmt,
        sample_rate=sample_rate,
        min_duration_ms=int(rec.get("min_duration_ms", 500)),
        vobiz_call_id=vobiz_call_id,
    )
