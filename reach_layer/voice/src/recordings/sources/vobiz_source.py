"""VobizRecordingSource — Vobiz REST start/stop, recording-list polling for URL.

Vobiz's behaviour (observed against a live tenant):

- ``POST /Account/{auth_id}/Call/{call_uuid}/Record/`` starts recording. The
  production response is ``{"api_id": ..., "message": "recording started"}``
  — no ``url`` field, despite what the published docs imply.
- ``DELETE /Account/{auth_id}/Call/{call_uuid}/Record/`` stops recording.
  Returns 204 No Content.
- The promised ``callback_url`` POST is unreliable in practice (it fires for
  naturally-ended calls but not always for DELETE-stopped ones, and can take
  minutes to arrive when it does).
- ``GET /Account/{auth_id}/Recording/`` returns the recordings list. Each
  entry includes ``call_uuid``, ``recording_id``, ``recording_url`` (an
  ``https://media.vobiz.ai/...`` MP3 URL the API can authenticate against)
  and timing metadata. This is the authoritative source of the URL.

Strategy: after stopping, poll the recording list, filtering by our known
``call_uuid``, with brief backoff until the entry appears or timeout. The
inbound webhook is still wired (``server.py`` resolves the registry future)
so when it does arrive it short-circuits the poll loop.

Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

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
        poll_interval_s: float = 2.0,
        poll_max_s: Optional[float] = None,
    ) -> None:
        """Initialise the source with Vobiz credentials and shared registry.

        Args:
            auth_id: Vobiz account auth ID.
            auth_token: Vobiz account auth token.
            callback_url: URL Vobiz will POST to when the recording finalises.
                          Fast-path signal only; the recording-list endpoint is
                          the authoritative URL source.
            webhook_timeout_s: Total budget (seconds) for the poll loop and
                               webhook race. ``poll_max_s`` defaults to this.
            fetch_timeout_s: Seconds allowed to download the MP3 bytes.
            registry: Shared dict mapping vobiz_call_id → asyncio.Future[str].
                      Populated by begin(); resolved by the /recording-ready handler.
            file_format: Vobiz file_format parameter — "mp3" (default) or "wav".
            time_limit_s: Vobiz time_limit parameter, in seconds. Capped at 4 h.
            poll_interval_s: Seconds between recording-list polls.
            poll_max_s: Maximum total polling time. Defaults to webhook_timeout_s.
        """
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._callback_url = callback_url
        self._webhook_timeout_s = webhook_timeout_s
        self._fetch_timeout_s = fetch_timeout_s
        self._registry = registry
        self._file_format = file_format
        self._time_limit_s = max(1, min(int(time_limit_s), 4 * 3600))
        self._poll_interval_s = max(0.5, float(poll_interval_s))
        self._poll_max_s = float(
            poll_max_s if poll_max_s is not None else webhook_timeout_s
        )
        self._vobiz_call_id: str = ""
        # Canonical recording URL: from list-API or webhook, whichever arrives first.
        self._recording_url: str = ""

    @property
    def pipeline_processors(self) -> list:
        """Empty — this source uses server-side recording, not a pipeline tap."""
        return []

    def _headers(self) -> dict:
        """Build Vobiz auth headers."""
        return {"X-Auth-ID": self._auth_id, "X-Auth-Token": self._auth_token}

    async def begin(self, *, call_sid: str, vobiz_call_id: str) -> None:
        """Start server-side recording.

        Args:
            call_sid: Telephony platform call SID (used for logging).
            vobiz_call_id: Vobiz internal call ID used in the REST endpoint path.

        Raises:
            aiohttp.ClientError: If the start request fails.
            RuntimeError: If Vobiz returns a non-success status.
        """
        self._vobiz_call_id = vobiz_call_id
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}"
            f"/Call/{vobiz_call_id}/Record/"
        )
        loop = asyncio.get_running_loop()
        # Webhook future: fast-path signal when (if) Vobiz fires the callback.
        # If it never fires, the poll loop will discover the URL itself.
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

        # Opportunistic: if Vobiz ever returns a url synchronously, capture it.
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
                "url_source": "start_response" if self._recording_url else "pending",
                "file_format": self._file_format,
                "time_limit_s": self._time_limit_s,
            },
        )

    async def _stop_record(self) -> None:
        """DELETE /Account/{auth_id}/Call/{call_uuid}/Record/ — finalise recording."""
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

    async def _list_recording_for_call(self) -> Optional[dict]:
        """Return the recording-list entry whose call_uuid matches this call.

        Calls ``GET /Account/{auth_id}/Recording/?call_uuid=<uuid>&limit=5``.
        Vobiz supports server-side ``call_uuid`` filtering on the list endpoint
        per the published Recording API docs ("List all recordings with
        extensive filtering options"). If a future tenant ignores the filter
        and returns unrelated entries, the client-side match below remains as
        a safety net.

        Returns:
            The matching recording dict (with ``recording_url`` populated), or
            None if no match was found.
        """
        endpoint = (
            f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}/Recording/"
            f"?call_uuid={self._vobiz_call_id}&limit=5"
        )
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(endpoint, headers=self._headers()) as resp:
                if resp.status != 200:
                    logger.warning(
                        "vobiz_source.list_bad_status",
                        extra={
                            "operation": "vobiz_source._list_recording_for_call",
                            "status": "failure",
                            "http_status": resp.status,
                            "vobiz_call_id": self._vobiz_call_id,
                        },
                    )
                    return None
                payload = await resp.json()
        objects = payload.get("objects") or []
        for entry in objects:
            if str(entry.get("call_uuid", "")) == self._vobiz_call_id:
                return entry
        return None

    async def _poll_for_recording_url(self) -> Optional[str]:
        """Poll the recording-list endpoint until our recording appears.

        Honours ``self._poll_max_s`` as the overall budget; sleeps
        ``self._poll_interval_s`` between attempts. Races against the webhook
        future so the first signal wins.

        Returns:
            The recording URL if found, otherwise None on timeout.
        """
        deadline = time.time() + self._poll_max_s
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            entry = await self._list_recording_for_call()
            if entry:
                url = str(entry.get("recording_url", ""))
                logger.info(
                    "vobiz_source.list_hit",
                    extra={
                        "operation": "vobiz_source._poll_for_recording_url",
                        "status": "success",
                        "vobiz_call_id": self._vobiz_call_id,
                        "recording_id": entry.get("recording_id", ""),
                        "recording_url": url,
                        "duration_ms": entry.get("recording_duration_ms", ""),
                        "attempt": attempt,
                    },
                )
                if url:
                    return url
            await asyncio.sleep(self._poll_interval_s)
        logger.warning(
            "vobiz_source.list_timeout",
            extra={
                "operation": "vobiz_source._poll_for_recording_url",
                "status": "failure",
                "vobiz_call_id": self._vobiz_call_id,
                "poll_max_s": self._poll_max_s,
                "attempts": attempt,
            },
        )
        return None

    async def end(self) -> RecordingPayload:
        """Stop recording, find the URL via list or webhook, fetch the MP3.

        Sequence:
          1. DELETE /Record/ to ask Vobiz to finalise.
          2. Race the inbound webhook future against the recording-list poll.
          3. Whichever produces a URL first wins.
          4. GET the URL with auth headers and return the bytes.

        Returns:
            RecordingPayload with bytes_data populated.

        Raises:
            RuntimeError: If begin() was not called, or no URL is discoverable
                          within poll_max_s / webhook_timeout_s.
            aiohttp.ClientError: If the MP3 download fails.
        """
        if not self._vobiz_call_id:
            raise RuntimeError(
                "vobiz_source.end called before a successful begin "
                "(no vobiz_call_id known)"
            )
        await self._stop_record()

        url = self._recording_url  # in case start_response gave us one
        if not url:
            # Race the webhook against the polling probe. Whichever wins.
            fut = self._registry.get(self._vobiz_call_id)
            wait_tasks: list[asyncio.Task] = []
            if fut is not None:
                wait_tasks.append(asyncio.ensure_future(
                    asyncio.wait_for(fut, timeout=self._webhook_timeout_s)
                ))
            wait_tasks.append(asyncio.ensure_future(self._poll_for_recording_url()))
            try:
                done, pending = await asyncio.wait(
                    wait_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                for t in done:
                    try:
                        result = t.result()
                    except Exception:
                        result = None
                    if result:
                        url = str(result)
                        break
            finally:
                for t in wait_tasks:
                    if not t.done():
                        t.cancel()

        if not url:
            logger.error(
                "vobiz_source.url_unavailable",
                extra={
                    "operation": "vobiz_source.end",
                    "status": "failure",
                    "vobiz_call_id": self._vobiz_call_id,
                    "webhook_timeout_s": self._webhook_timeout_s,
                    "poll_max_s": self._poll_max_s,
                    "reason": "neither webhook nor recording-list produced a URL",
                },
            )
            raise RuntimeError(
                f"vobiz recording URL not discoverable for "
                f"call_id={self._vobiz_call_id!r}"
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
                "bytes": len(data),
            },
        )
        return RecordingPayload(bytes_data=data)
