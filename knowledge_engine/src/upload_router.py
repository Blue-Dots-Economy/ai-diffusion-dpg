"""
knowledge_engine/src/upload_router.py

FastAPI router for KB document upload ingestion.

Exposes:
  POST /upload          — accept multipart batch, validate, enqueue jobs
  GET  /upload/job/{id} — return job status from SQLite

The async queue worker (started once at KE startup) processes one job at a
time to protect ChromaDB writes. All state is durable in SQLite.

Belongs to the Knowledge Engine block of the DPG framework.
Approved architecture exception: receives calls from Reach Layer (not Agent Core).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response

from src.auth import verify_api_key
from src.db.ingestion_db import IngestionDB, IngestionRecord
from src.storage.local_pvc import LocalPVCStorageBackend
from src.storage.azure_blob import AzureBlobStorageBackend

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".docx", ".html"}


class _IngestJob:
    """In-memory job passed through the asyncio queue."""

    __slots__ = ("job_id", "batch_id", "filename", "mode", "cloud_path", "file_bytes", "doc_type")

    def __init__(
        self,
        job_id: str,
        batch_id: str,
        filename: str,
        mode: str,
        cloud_path: Optional[str],
        file_bytes: Optional[bytes],
        doc_type: Optional[str] = None,
    ) -> None:
        """Initialise an in-memory ingest job descriptor.

        Args:
            job_id: UUID for this job.
            batch_id: UUID for the parent batch.
            filename: Sanitised basename of the file.
            mode: Ingest mode ('local_write_ingest', 'cloud_upload_ingest', 'cloud_fetch_ingest').
            cloud_path: Azure Blob path (cloud_fetch_ingest only).
            file_bytes: Raw file bytes (None for cloud_fetch_ingest).
            doc_type: Optional per-file doc_type tag (e.g. 'data_protection_law').
                If None, worker falls back to sources[] lookup by filename, then
                block_cfg['default_doc_type']. Lets callers label uploads to match
                the domain's intent_filters.
        """
        self.job_id = job_id
        self.batch_id = batch_id
        self.filename = filename
        self.mode = mode
        self.cloud_path = cloud_path
        self.file_bytes = file_bytes
        self.doc_type = doc_type


def create_upload_router(
    db: IngestionDB,
    ingest_queue: asyncio.Queue,
    reach_to_ke_api_key: str,
    azure_configured: bool,
    max_queue_size: int = 20,
    devkit_callback_url: Optional[str] = None,
    ke_to_devkit_api_key: Optional[str] = None,
    ke_config: Optional[dict] = None,
    static_kb_block=None,
) -> APIRouter:
    """Create and return the upload router.

    Args:
        db: Initialised IngestionDB instance.
        ingest_queue: Singleton asyncio.Queue shared with the queue worker.
        reach_to_ke_api_key: Expected value of X-API-Key from Reach Layer.
        azure_configured: True if AZURE_STORAGE_ACCOUNT/KEY/CONTAINER are all set.
        max_queue_size: Maximum combined queue+pending jobs before returning 429.
        devkit_callback_url: Dev-kit callback URL for job completion notification.
        ke_to_devkit_api_key: API key sent to dev-kit callback endpoint.
        ke_config: Full KE config dict passed to ingest_single.
        static_kb_block: StaticKnowledgeBaseBlock instance for ingest_single calls.

    Returns:
        Configured APIRouter.
    """
    router = APIRouter()

    @router.post("/upload")
    async def upload_batch(
        request: Request,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    ):
        """Accept a multipart document batch and enqueue ingestion jobs.

        Validates API key, sanitizes filenames, checks extensions, inserts all
        records atomically into SQLite, then enqueues all jobs.

        Returns batch_id and per-file job_id list.
        """
        verify_api_key(x_api_key, reach_to_ke_api_key)

        form = await request.form()

        raw_metadata = form.get("metadata")
        if not raw_metadata:
            raise HTTPException(422, "metadata field is required")

        try:
            metadata_entries = json.loads(raw_metadata)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"Invalid metadata JSON: {e}") from e

        user_id = form.get("user_id", "unknown")

        # Build a dict of filename -> file bytes from the form
        file_parts: dict[str, bytes] = {}
        for field_name, value in form.multi_items():
            if field_name == "files" and hasattr(value, "filename") and hasattr(value, "read"):
                content = await value.read()
                file_parts[value.filename] = content

        # Validate all entries
        validated: list[tuple[str, str, Optional[str], Optional[bytes], Optional[str]]] = []
        for entry in metadata_entries:
            filename = entry.get("filename", "")
            mode = entry.get("mode", "")
            cloud_path = entry.get("cloud_path")
            raw_doc_type = entry.get("doc_type")
            doc_type = raw_doc_type.strip() if isinstance(raw_doc_type, str) and raw_doc_type.strip() else None

            # Path traversal check
            safe_name = Path(filename).name
            if safe_name != filename or "/" in filename or "\\" in filename:
                raise HTTPException(422, f"Invalid filename: {filename}")

            ext = Path(safe_name).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                raise HTTPException(422, f"Unsupported extension: {ext}")

            if mode in ("cloud_upload_ingest", "cloud_fetch_ingest") and not azure_configured:
                raise HTTPException(400, "Azure storage not configured on this deployment")

            if mode in ("cloud_upload_ingest", "local_write_ingest"):
                if safe_name not in file_parts:
                    raise HTTPException(422, f"No file part found for: {filename}")

            if mode == "cloud_fetch_ingest" and not cloud_path:
                raise HTTPException(422, f"cloud_path required for: {filename}")

            file_bytes = file_parts.get(safe_name)
            validated.append((safe_name, mode, cloud_path, file_bytes, doc_type))

        # Queue capacity check
        if ingest_queue.qsize() + len(validated) > max_queue_size:
            raise HTTPException(429, "Queue full — try again later")

        # Atomic: insert all DB rows first, then enqueue
        batch_id = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()
        source_type_map = {
            "local_write_ingest": "local",
            "cloud_upload_ingest": "cloud",
            "cloud_fetch_ingest": "cloud",
        }

        db_rows: list[IngestionRecord] = []
        jobs: list[_IngestJob] = []

        for safe_name, mode, cloud_path, file_bytes, doc_type in validated:
            job_id = str(uuid.uuid4())
            db_rows.append(IngestionRecord(
                job_id=job_id,
                batch_id=batch_id,
                filename=safe_name,
                file_size_bytes=len(file_bytes) if file_bytes else None,
                source_type=source_type_map.get(mode, "local"),
                cloud_path=cloud_path,
                mode=mode,
                status="queued",
                user_id=user_id,
                uploaded_at=now_utc,
                doc_type=doc_type,
            ))
            jobs.append(_IngestJob(job_id, batch_id, safe_name, mode, cloud_path, file_bytes, doc_type))

        try:
            db.insert_batch(db_rows)
        except Exception as e:
            logger.error(
                "ke.upload.db_insert_failed",
                extra={"operation": "ke.upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(500, f"DB insert failed: {e}") from e

        try:
            for job in jobs:
                await ingest_queue.put(job)
        except Exception as e:
            db.rollback_batch(batch_id)
            logger.error(
                "ke.upload.enqueue_failed",
                extra={"operation": "ke.upload", "status": "failure", "error": str(e)},
            )
            raise HTTPException(500, f"Queue enqueue failed: {e}") from e

        logger.info(
            "ke.upload.batch_accepted",
            extra={
                "operation": "ke.upload",
                "status": "success",
                "batch_id": batch_id,
                "job_count": len(jobs),
            },
        )

        return {
            "batch_id": batch_id,
            "jobs": [{"filename": j.filename, "job_id": j.job_id} for j in jobs],
        }

    @router.get("/upload/job/{job_id}")
    async def get_job_status(
        job_id: str,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    ):
        """Return ingestion job status from SQLite.

        Reads directly from the DB — status survives pod restarts.
        queue_position is calculated dynamically for queued jobs.

        Args:
            job_id: UUID of the job to look up.
            x_api_key: Value of X-API-Key request header.

        Returns:
            Dict with job_id, filename, status, queue_position, chunks_added,
            error, and uploaded_at.

        Raises:
            HTTPException: 401 if API key is invalid; 404 if job_id not found.
        """
        verify_api_key(x_api_key, reach_to_ke_api_key)

        record = db.get_record(job_id)
        if record is None:
            raise HTTPException(404, f"Job not found: {job_id}")

        return {
            "job_id": record.job_id,
            "filename": record.filename,
            "status": record.status,
            "queue_position": record.queue_position,
            "chunks_added": record.chunks_added,
            "error": record.error,
            "uploaded_at": record.uploaded_at,
        }

    @router.get("/upload/jobs")
    async def list_jobs(
        limit: int = 100,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    ):
        """Return the most recent ingestion records ordered by upload time descending.

        Args:
            limit: Maximum number of records to return (default 100).
            x_api_key: Value of X-API-Key request header.

        Returns:
            Dict with a ``jobs`` list. Each entry has job_id, filename, status,
            queue_position, chunks_added, error, uploaded_at, ingested_at,
            doc_type, and mode.

        Raises:
            HTTPException: 401 if API key is invalid.
        """
        verify_api_key(x_api_key, reach_to_ke_api_key)

        records = db.list_records(limit=min(limit, 500))
        return {
            "jobs": [
                {
                    "job_id": r.job_id,
                    "filename": r.filename,
                    "status": r.status,
                    "queue_position": r.queue_position,
                    "chunks_added": r.chunks_added,
                    "error": r.error,
                    "uploaded_at": r.uploaded_at,
                    "ingested_at": r.ingested_at,
                    "doc_type": r.doc_type,
                    "mode": r.mode,
                }
                for r in records
            ]
        }

    return router


async def run_queue_worker(
    db: IngestionDB,
    ingest_queue: asyncio.Queue,
    ke_config: dict,
    static_kb_block,
    devkit_callback_url: Optional[str],
    ke_to_devkit_api_key: Optional[str],
    kb_data_dir: str = "/data/kb",
) -> None:
    """Process upload jobs sequentially. One job at a time to protect ChromaDB writes.

    Args:
        db: IngestionDB instance for status updates.
        ingest_queue: Singleton asyncio.Queue containing _IngestJob items.
        ke_config: Full KE config dict passed to ingest_single.
        static_kb_block: StaticKnowledgeBaseBlock instance.
        devkit_callback_url: Dev-kit callback URL (optional).
        ke_to_devkit_api_key: API key for callback requests (optional).
        kb_data_dir: Base directory for staged files (PVC mount).
    """
    azure_acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    azure_key = os.environ.get("AZURE_STORAGE_KEY")
    azure_cont = os.environ.get("AZURE_CONTAINER_NAME")

    while True:
        job: _IngestJob = await ingest_queue.get()
        db.update_status(job.job_id, "ingesting")

        try:
            staged_path = await _stage_file(job, kb_data_dir, azure_acct, azure_key, azure_cont)
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(
                None,
                lambda: static_kb_block.ingest_single(ke_config, staged_path, doc_type=job.doc_type),
            )
            ingested_at = datetime.now(timezone.utc).isoformat()
            db.update_status(job.job_id, "ingested", chunks_added=chunks, ingested_at=ingested_at)
            await _send_callback(
                devkit_callback_url, ke_to_devkit_api_key, job.job_id, "ingested", chunks_added=chunks
            )
            logger.info(
                "ke.worker.job_complete",
                extra={
                    "operation": "ke.worker",
                    "status": "success",
                    "job_id": job.job_id,
                    "chunks_added": chunks,
                },
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(
                "ke.worker.job_failed",
                extra={
                    "operation": "ke.worker",
                    "status": "failure",
                    "job_id": job.job_id,
                    "error": error_msg,
                },
            )
            db.update_status(job.job_id, "failed", error=error_msg)
            await _send_callback(
                devkit_callback_url, ke_to_devkit_api_key, job.job_id, "failed", error=error_msg
            )
        finally:
            ingest_queue.task_done()


async def _stage_file(
    job: _IngestJob,
    kb_data_dir: str,
    azure_acct: Optional[str],
    azure_key: Optional[str],
    azure_cont: Optional[str],
) -> Path:
    """Write the file to the local PVC and optionally to Azure Blob.

    Mode behaviour:
      local_write_ingest:   write bytes to PVC
      cloud_upload_ingest:  write bytes to PVC AND upload to Azure Blob
      cloud_fetch_ingest:   download from Azure Blob -> write to PVC

    Args:
        job: The in-memory job descriptor.
        kb_data_dir: Base directory for PVC file storage.
        azure_acct: Azure storage account name (None if not configured).
        azure_key: Azure storage account key (None if not configured).
        azure_cont: Azure container name (None if not configured).

    Returns:
        Absolute Path to the staged file on the PVC.

    Raises:
        ValueError: If job.mode is unrecognised.
    """
    local_backend = LocalPVCStorageBackend(base_dir=kb_data_dir)

    if job.mode == "local_write_ingest":
        dest = local_backend.upload(job.file_bytes, job.filename)
        return Path(dest)

    if job.mode == "cloud_upload_ingest":
        dest = local_backend.upload(job.file_bytes, job.filename)
        azure = AzureBlobStorageBackend(azure_acct, azure_key, azure_cont)
        azure.upload(job.file_bytes, job.filename)
        return Path(dest)

    if job.mode == "cloud_fetch_ingest":
        azure = AzureBlobStorageBackend(azure_acct, azure_key, azure_cont)
        content = azure.download(job.cloud_path)
        dest = local_backend.upload(content, job.filename)
        return Path(dest)

    raise ValueError(f"Unknown ingest mode: {job.mode}")


async def _send_callback(
    callback_url: Optional[str],
    api_key: Optional[str],
    job_id: str,
    status: str,
    **kwargs,
) -> None:
    """Notify dev-kit of job completion. Retries 3x with exponential backoff.

    Args:
        callback_url: Dev-kit callback endpoint (None disables callback).
        api_key: API key for X-API-Key header.
        job_id: UUID of the completed job.
        status: Terminal status ('ingested' or 'failed').
        **kwargs: Optional fields: chunks_added (int), error (str).
    """
    if not callback_url:
        return

    payload = {"job_id": job_id, "status": status, **kwargs}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{callback_url}/api/ingest/callback",
                    json=payload,
                    headers={"X-API-Key": api_key or ""},
                    timeout=10.0,
                )
                if r.status_code < 500:
                    return  # 2xx or 4xx — do not retry
        except Exception as e:
            logger.debug(
                "ke.callback_attempt_failed",
                extra={
                    "operation": "ke.callback",
                    "status": "failure",
                    "attempt": attempt + 1,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
        await asyncio.sleep(2 ** attempt)

    logger.warning(
        "ke.callback_failed_all_retries",
        extra={
            "operation": "ke.callback",
            "status": "failure",
            "job_id": job_id,
        },
    )
