"""VobizRecordingSource — Vobiz REST start/stop + webhook-fed MP3 fetch.

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
    """Recording source that uses the Vobiz server-side recording API.

    Calls the Vobiz Record/start endpoint on begin() and Record/Stop on end(),
    then waits for the /recording-ready webhook to resolve a future with the
    MP3 URL before fetching the bytes.
    """

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
        """Initialise the source with Vobiz credentials and shared registry.

        Args:
            auth_id: Vobiz account auth ID.
            auth_token: Vobiz account auth token.
            callback_url: URL Vobiz will POST to when the recording is ready.
            webhook_timeout_s: Seconds to wait for the webhook future before raising TimeoutError.
            fetch_timeout_s: Seconds allowed to download the MP3 bytes.
            registry: Shared dict mapping vobiz_call_id → asyncio.Future[str].
                      Populated by begin(); resolved by the /recording-ready handler.
        """
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._callback_url = callback_url
        self._webhook_timeout_s = webhook_timeout_s
        self._fetch_timeout_s = fetch_timeout_s
        self._registry = registry
        self._vobiz_call_id: str = ""

    @property
    def pipeline_processors(self) -> list:
        """Return empty list — this source uses server-side recording, not pipeline tap."""
        return []

    def _headers(self) -> dict:
        """Build Vobiz auth headers."""
        return {"X-Auth-ID": self._auth_id, "X-Auth-Token": self._auth_token}

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        """Start server-side recording via Vobiz REST API.

        Registers a Future in the shared registry that will be resolved by the
        /recording-ready webhook handler when the MP3 is ready.

        Args:
            call_sid: Telephony platform call SID (used for logging).
            vobiz_call_id: Vobiz internal call ID used in the REST endpoint path.

        Raises:
            aiohttp.ClientError: If the HTTP request fails.
        """
        self._vobiz_call_id = vobiz_call_id
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{vobiz_call_id}/Record/"
        )
        loop = asyncio.get_running_loop()
        self._registry[vobiz_call_id] = loop.create_future()
        start = time.time()
        ok = False
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    endpoint,
                    headers=self._headers(),
                    json={
                        "record_session": True,
                        "transcription": False,
                        "callback_url": self._callback_url,
                    },
                ) as resp:
                    ok = resp.status in (200, 201, 202)
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
        logger.info(
            "vobiz_source.begin",
            extra={
                "operation": "vobiz_source.begin",
                "status": "success" if ok else "failure",
                "call_sid": call_sid,
                "vobiz_call_id": vobiz_call_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

    async def _stop_record(self) -> None:
        """POST to Vobiz Record/Stop endpoint."""
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{self._vobiz_call_id}/Record/Stop/"
        )
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(endpoint, headers=self._headers()) as resp:
                _ = resp.status

    async def end(self) -> RecordingPayload:
        """Stop recording, await the webhook future, and fetch MP3 bytes.

        Returns:
            RecordingPayload with bytes_data populated.

        Raises:
            asyncio.TimeoutError: If the /recording-ready webhook does not arrive
                within webhook_timeout_s.
            RuntimeError: If begin() was not called before end().
            aiohttp.ClientError: If the MP3 download fails.
        """
        await self._stop_record()
        fut = self._registry.get(self._vobiz_call_id)
        if fut is None:
            raise RuntimeError(
                f"vobiz recording future missing for call_id={self._vobiz_call_id!r}; "
                "ensure begin() was called first"
            )
        url = await asyncio.wait_for(fut, timeout=self._webhook_timeout_s)
        timeout = aiohttp.ClientTimeout(total=self._fetch_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
        self._registry.pop(self._vobiz_call_id, None)
        logger.info(
            "vobiz_source.end",
            extra={
                "operation": "vobiz_source.end",
                "status": "success",
                "vobiz_call_id": self._vobiz_call_id,
                "bytes": len(data),
            },
        )
        return RecordingPayload(bytes_data=data)
