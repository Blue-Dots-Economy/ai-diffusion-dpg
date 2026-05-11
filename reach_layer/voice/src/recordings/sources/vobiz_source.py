"""VobizRecordingSource — Vobiz REST start/stop with synchronous URL capture.

Per the Vobiz docs (/call/record-calls/start-recording and /stop-recording):

- ``POST /Account/{auth_id}/Call/{call_uuid}/Record/`` returns
  ``{"api_id": ..., "message": ..., "recording_id": ..., "url": "..."}``.
  The ``url`` is the canonical recording URL — it's the same URL the callback
  will eventually deliver, captured here without waiting for the webhook.
- ``DELETE /Account/{auth_id}/Call/{call_uuid}/Record/`` stops recording.
  Returns HTTP 204 No Content on success.
- The ``callback_url`` we register receives a Plivo-style form POST when the
  recording finalises. We use it purely as a "ready to download" signal —
  the URL we use was already captured from the start response.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
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
    """Recording source that uses the Vobiz server-side recording API."""

    def __init__(
        self,
        *,
        auth_id: str,
        auth_token: str,
        callback_url: str,
        webhook_timeout_s: float,
        fetch_timeout_s: float,
        registry: Dict[str, "asyncio.Future[str]"],
        file_format: str = "mp3",
        time_limit_s: int = 3600,
    ) -> None:
        """Initialise the source with Vobiz credentials and shared registry.

        Args:
            auth_id: Vobiz account auth ID.
            auth_token: Vobiz account auth token.
            callback_url: URL Vobiz will POST to when the recording finalises.
                          Used as a readiness signal only.
            webhook_timeout_s: Seconds to wait for the readiness webhook before
                               attempting to download anyway.
            fetch_timeout_s: Seconds allowed to download the MP3 bytes.
            registry: Shared dict mapping vobiz_call_id → asyncio.Future[str].
                      Populated by begin(); resolved by the /recording-ready handler.
            file_format: Vobiz file_format parameter — "mp3" (default) or "wav".
            time_limit_s: Vobiz time_limit parameter, in seconds. Vobiz defaults
                          to 60s; this default of 3600s prevents short calls
                          from being truncated. Capped at 4 hours per docs.
        """
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._callback_url = callback_url
        self._webhook_timeout_s = webhook_timeout_s
        self._fetch_timeout_s = fetch_timeout_s
        self._registry = registry
        self._file_format = file_format
        self._time_limit_s = max(1, min(int(time_limit_s), 4 * 3600))
        self._vobiz_call_id: str = ""
        # Canonical recording URL captured from POST /Record/ response body.
        self._recording_url: str = ""

    @property
    def pipeline_processors(self) -> list:
        """Empty — this source uses server-side recording, not a pipeline tap."""
        return []

    def _headers(self) -> dict:
        """Build Vobiz auth headers."""
        return {"X-Auth-ID": self._auth_id, "X-Auth-Token": self._auth_token}

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        """Start server-side recording and capture the canonical URL.

        Args:
            call_sid: Telephony platform call SID (used for logging).
            vobiz_call_id: Vobiz internal call ID used in the REST endpoint path.

        Raises:
            aiohttp.ClientError: If the start request fails.
            RuntimeError: If Vobiz returns a non-success status or no URL.
        """
        self._vobiz_call_id = vobiz_call_id
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{vobiz_call_id}/Record/"
        )
        loop = asyncio.get_running_loop()
        # The webhook future signals "ready to download"; it does NOT carry the
        # URL we use (the URL came back synchronously in the start response).
        self._registry[vobiz_call_id] = loop.create_future()
        body = {
            "time_limit": self._time_limit_s,
            "file_format": self._file_format,
            "callback_url": self._callback_url,
            "callback_method": "POST",
        }
        start = time.time()
        timeout = aiohttp.ClientTimeout(total=5)
        resp_json: dict = {}
        status = 0
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    endpoint, headers=self._headers(), json=body
                ) as resp:
                    status = resp.status
                    if resp.content_type and "json" in resp.content_type.lower():
                        try:
                            resp_json = await resp.json()
                        except Exception:
                            resp_json = {}
        except Exception as exc:
            logger.error(
                "vobiz_source.begin_failed",
                extra={
                    "operation": "vobiz_source.begin",
                    "status": "failure",
                    "call_sid": call_sid,
                    "vobiz_call_id": vobiz_call_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

        if status not in (200, 201, 202):
            logger.error(
                "vobiz_source.begin_bad_status",
                extra={
                    "operation": "vobiz_source.begin",
                    "status": "failure",
                    "call_sid": call_sid,
                    "vobiz_call_id": vobiz_call_id,
                    "http_status": status,
                    "response_keys": sorted(resp_json.keys()),
                },
            )
            raise RuntimeError(
                f"vobiz Record/ start returned HTTP {status} for "
                f"call_id={vobiz_call_id!r}"
            )

        self._recording_url = str(resp_json.get("url") or "")
        if not self._recording_url:
            logger.error(
                "vobiz_source.begin_no_url",
                extra={
                    "operation": "vobiz_source.begin",
                    "status": "failure",
                    "call_sid": call_sid,
                    "vobiz_call_id": vobiz_call_id,
                    "response_keys": sorted(resp_json.keys()),
                },
            )
            raise RuntimeError(
                f"vobiz Record/ start did not return a url field; "
                f"got keys {sorted(resp_json.keys())}"
            )

        logger.info(
            "vobiz_source.begin",
            extra={
                "operation": "vobiz_source.begin",
                "status": "success",
                "call_sid": call_sid,
                "vobiz_call_id": vobiz_call_id,
                "latency_ms": int((time.time() - start) * 1000),
                "recording_id": resp_json.get("recording_id", ""),
                "recording_url": self._recording_url,
                "file_format": self._file_format,
                "time_limit_s": self._time_limit_s,
            },
        )

    async def _stop_record(self) -> None:
        """DELETE the recording on Vobiz to finalise it.

        Per the docs: ``DELETE /Account/{auth_id}/Call/{call_uuid}/Record/``
        returns 204 No Content on success. With no body, all ongoing recordings
        on the call are stopped.
        """
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{self._vobiz_call_id}/Record/"
        )
        start = time.time()
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.delete(endpoint, headers=self._headers()) as resp:
                    logger.info(
                        "vobiz_source.stop",
                        extra={
                            "operation": "vobiz_source.stop",
                            "status": "success"
                            if resp.status in (200, 204)
                            else "failure",
                            "vobiz_call_id": self._vobiz_call_id,
                            "http_status": resp.status,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
        except Exception as exc:
            logger.warning(
                "vobiz_source.stop_failed",
                extra={
                    "operation": "vobiz_source.stop",
                    "status": "failure",
                    "vobiz_call_id": self._vobiz_call_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    async def end(self) -> RecordingPayload:
        """Stop recording, wait briefly for finalisation, fetch the MP3.

        We DELETE the recording, then await the readiness webhook (resolved
        via the registry future by ``/recording-ready``) so Vobiz has time to
        finalise the file in storage. If the webhook never fires within
        ``webhook_timeout_s`` we proceed anyway — the URL was captured from
        the start response and the MP3 may already be downloadable.

        Returns:
            RecordingPayload with bytes_data populated.

        Raises:
            RuntimeError: If begin() was not called before end(), or if the
                          start response carried no URL.
            aiohttp.ClientError: If the MP3 download fails.
        """
        if not self._recording_url:
            raise RuntimeError(
                "vobiz_source.end called before a successful begin "
                "(no recording_url captured)"
            )
        await self._stop_record()

        # Best-effort wait for the readiness webhook. Timeouts are downgraded
        # to a logged warning — the start-response URL is canonical, so we
        # still attempt the download.
        fut = self._registry.get(self._vobiz_call_id)
        if fut is not None:
            try:
                await asyncio.wait_for(fut, timeout=self._webhook_timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "vobiz_source.webhook_timeout",
                    extra={
                        "operation": "vobiz_source.end",
                        "status": "skipped",
                        "vobiz_call_id": self._vobiz_call_id,
                        "webhook_timeout_s": self._webhook_timeout_s,
                        "reason": "proceeding with start-response URL",
                    },
                )

        timeout = aiohttp.ClientTimeout(total=self._fetch_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(self._recording_url, headers=self._headers()) as resp:
                resp.raise_for_status()
                data = await resp.read()
        self._registry.pop(self._vobiz_call_id, None)
        logger.info(
            "vobiz_source.end",
            extra={
                "operation": "vobiz_source.end",
                "status": "success",
                "vobiz_call_id": self._vobiz_call_id,
                "recording_url": self._recording_url,
                "bytes": len(data),
            },
        )
        return RecordingPayload(bytes_data=data)
