"""LocalFileStore — writes recording audio + sidecar JSON to a mounted volume.

Belongs to the Reach Layer / Voice channel in the DPG framework.
Intended for development, on-prem deployments, or any environment where a
shared filesystem mount is available.  Files are laid out as::

    {base_path}/YYYY/MM/DD/{call_sid}.{format}
    {base_path}/YYYY/MM/DD/{call_sid}.json   ← sidecar manifest

The sidecar is written atomically after the audio so that a reader
finding the sidecar can always trust the audio file is complete.
"""
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
    """Stores call recordings on the local filesystem under a base directory."""

    def __init__(self, base_path: str) -> None:
        """Initialise the store.

        Args:
            base_path: Absolute path to the root directory under which
                date-partitioned sub-directories will be created.
        """
        self._base = Path(base_path)

    async def put(self, artifact: RecordingArtifact) -> str:
        """Write audio bytes and a sidecar manifest to disk.

        Args:
            artifact: ``RecordingArtifact`` whose ``payload.bytes_data`` must
                be set.  ``fetch_url``-only payloads are not supported by this
                backend.

        Returns:
            ``file://`` URI pointing to the written audio file.

        Raises:
            ValueError: If ``artifact.payload.bytes_data`` is ``None``.
        """
        if artifact.payload.bytes_data is None:
            raise ValueError(
                "LocalFileStore requires payload.bytes_data; "
                "fetch_url-only payloads are not supported"
            )

        data: bytes = artifact.payload.bytes_data
        sha = hashlib.sha256(data).hexdigest()
        # Mutate artifact so callers/managers can read the computed hash.
        artifact.sha256 = sha

        ts = datetime.fromtimestamp(artifact.start_ts, tz=timezone.utc)
        target_dir = (
            self._base
            / f"{ts.year:04d}"
            / f"{ts.month:02d}"
            / f"{ts.day:02d}"
        )
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o750)

        audio_path = target_dir / f"{artifact.call_sid}.{artifact.format}"
        with audio_path.open("wb") as fh:
            fh.write(data)
        os.chmod(audio_path, 0o640)

        uri = f"file://{audio_path.resolve()}"

        sidecar: dict = {
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
            extra={
                "operation": "local_store.put",
                "status": "success",
                "call_sid": artifact.call_sid,
                "bytes": len(data),
                "recording_uri": uri,
            },
        )
        return uri
