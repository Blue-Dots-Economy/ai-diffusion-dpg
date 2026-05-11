"""VobizRecordingSource — Vobiz REST start/stop, webhook delivers the URL.

Per the Vobiz API (verified against a live tenant — note the published docs
disagree with observed behaviour on the start response):

- ``POST /Account/{auth_id}/Call/{call_uuid}/Record/`` returns
  ``{"api_id": ..., "message": "recording started"}``. The docs imply a
  ``url`` field exists in this response but production responses do not
  include it. The recording URL is delivered only via the callback POST
  to ``callback_url`` once the recording finalises.
- ``DELETE /Account/{auth_id}/Call/{call_uuid}/Record/`` stops recording.
  Returns HTTP 204 No Content on success.
- The callback POST is form-encoded with Plivo-style PascalCase fields
  (``CallUUID``, ``RecordUrl``, ``RecordingID``, …). The reach_layer/voice
  server.py handler resolves the registry future with that URL.

The start response's ``url`` field is captured opportunistically if Vobiz
ever starts returning one; otherwise we fall back to the webhook.

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

        # Opportunistic: capture URL if Vobiz ever returns one synchronously.
        # In practice the production response only contains `api_id` and
        # `message`, so we rely on the callback to deliver the URL.
        self._recording_url = str(resp_json.get("url") or "")
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
                "url_source": "start_response" if self._recording_url else "pending_webhook",
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
        """Stop recording, await the callback URL, fetch the MP3.

        Sequence:
          1. ``DELETE /Record/`` to finalise the Vobiz-side recording.
          2. Await the readiness future on the shared registry — the
             ``/recording-ready`` webhook handler resolves it with the URL
             (Vobiz's callback POSTs a Plivo-style form payload containing
             ``RecordUrl``).
          3. If the start response already carried a URL we use that and
             treat the webhook as a no-op signal; otherwise the webhook URL
             becomes the canonical recording_url.
          4. ``GET`` the URL and return the bytes.

        Returns:
            RecordingPayload with bytes_data populated.

        Raises:
            RuntimeError: If begin() was not called or the source never got
                          a vobiz_call_id.
            asyncio.TimeoutError: If neither start-response nor webhook
                                  delivered a URL within webhook_timeout_s.
            aiohttp.ClientError: If the MP3 download fails.
        """
        if not self._vobiz_call_id:
            raise RuntimeError(
                "vobiz_source.end called before a successful begin "
                "(no vobiz_call_id known)"
            )
        await self._stop_record()

        fut = self._registry.get(self._vobiz_call_id)
        if fut is None:
            raise RuntimeError(
                f"vobiz recording future missing for call_id={self._vobiz_call_id!r}; "
                "ensure begin() was called first"
            )

        webhook_url = ""
        try:
            webhook_url = await asyncio.wait_for(
                fut, timeout=self._webhook_timeout_s
            )
        except asyncio.TimeoutError:
            if not self._recording_url:
                # No URL anywhere — webhook never fired and start didn't
                # return one. Re-raise so the manager can record a failed
                # state with a clear reason.
                logger.error(
                    "vobiz_source.url_unavailable",
                    extra={
                        "operation": "vobiz_source.end",
                        "status": "failure",
                        "vobiz_call_id": self._vobiz_call_id,
                        "webhook_timeout_s": self._webhook_timeout_s,
                        "reason": "no url from start response and no callback received",
                    },
                )
                raise

        url = self._recording_url or webhook_url
        if not url:
            raise RuntimeError(
                "vobiz_source.end resolved webhook but the URL was empty; "
                "callback payload may use an unrecognised field name"
            )

        timeout = aiohttp.ClientTimeout(total=self._fetch_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers=self._headers()) as resp:
                resp.raise_for_status()
                data = await resp.read()
        self._registry.pop(self._vobiz_call_id, None)
        logger.info(
            "vobiz_source.end",
            extra={
                "operation": "vobiz_source.end",
                "status": "success",
                "vobiz_call_id": self._vobiz_call_id,
                "recording_url": url,
                "url_source": "start_response" if self._recording_url else "webhook",
                "bytes": len(data),
            },
        )
        return RecordingPayload(bytes_data=data)
