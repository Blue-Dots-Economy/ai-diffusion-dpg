"""S3Store — uploads the audio + sidecar JSON to an S3-compatible bucket.

Belongs to the Reach Layer / Voice channel in the DPG framework.
Uses aiobotocore for async S3 access; supports SSE-S3 (default) and SSE-KMS.
"""
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
    """Return a fresh aiobotocore session (factored out for monkeypatching in tests)."""
    return get_session()


class S3Store(RecordingStoreBase):
    """Recording store backend that persists audio and sidecar JSON to S3.

    Uploads two objects per call:
    - ``{prefix}/YYYY/MM/DD/{call_sid}.{format}`` — raw audio bytes.
    - ``{prefix}/YYYY/MM/DD/{call_sid}.json``   — sidecar audit manifest.

    Server-side encryption defaults to AES256; pass a non-empty ``kms_key_id``
    to switch to SSE-KMS.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str,
        kms_key_id: str = "",
    ) -> None:
        """Initialise the S3Store.

        Args:
            bucket: S3 bucket name. Must be non-empty.
            prefix: Key prefix (e.g. ``"recordings/"``). A trailing slash is
                added automatically if absent.
            region: AWS region name (e.g. ``"ap-south-1"``).
            kms_key_id: KMS key ID/ARN for SSE-KMS. Empty string uses SSE-S3.

        Raises:
            ValueError: If ``bucket`` is empty.
        """
        if not bucket:
            raise ValueError("S3Store requires bucket to be a non-empty string")
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._region = region
        self._kms = kms_key_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sse_kwargs(self) -> dict:
        """Return SSE kwargs appropriate for the configured encryption mode."""
        if self._kms:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        return {"ServerSideEncryption": "AES256"}

    def _key(self, artifact: RecordingArtifact, ext: str) -> str:
        """Build the S3 object key for a given file extension."""
        ts = datetime.fromtimestamp(artifact.start_ts, tz=timezone.utc)
        return (
            f"{self._prefix}{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/"
            f"{artifact.call_sid}.{ext}"
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def put(self, artifact: RecordingArtifact) -> str:
        """Upload audio bytes and sidecar JSON to S3.

        Args:
            artifact: Fully populated ``RecordingArtifact``. ``payload.bytes_data``
                must be set; fetch-URL payloads are not supported by this backend.

        Returns:
            ``s3://{bucket}/{audio_key}`` URI of the stored audio file.

        Raises:
            ValueError: If ``artifact.payload.bytes_data`` is ``None``.
        """
        if artifact.payload.bytes_data is None:
            raise ValueError(
                "S3Store requires payload.bytes_data; fetch_url payloads are not supported"
            )

        data = artifact.payload.bytes_data
        sha = hashlib.sha256(data).hexdigest()
        artifact.sha256 = sha

        audio_key = self._key(artifact, artifact.format)
        sidecar_key = self._key(artifact, "json")
        uri = f"s3://{self._bucket}/{audio_key}"

        content_type = "audio/mpeg" if artifact.format == "mp3" else "audio/wav"
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
                Bucket=self._bucket,
                Key=audio_key,
                Body=data,
                ContentType=content_type,
                **self._sse_kwargs(),
            )
            await s3.put_object(
                Bucket=self._bucket,
                Key=sidecar_key,
                Body=json.dumps(sidecar, indent=2).encode(),
                ContentType="application/json",
                **self._sse_kwargs(),
            )

        logger.info(
            "s3_store.put",
            extra={
                "operation": "s3_store.put",
                "status": "success",
                "call_sid": artifact.call_sid,
                "bytes": len(data),
                "recording_uri": uri,
                "kms": bool(self._kms),
            },
        )
        return uri
