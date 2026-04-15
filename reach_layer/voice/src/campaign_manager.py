"""
telephony_adapter/src/campaign_manager.py

CampaignManager — triggers outbound calls via the Vobiz REST API.

Exposes initiate_call() which the FastAPI /campaign endpoint delegates to.
Action Gateway can also call POST /campaign as a telephony_channel_switch connector
tool when Agent Core decides to switch channels mid-session.

Retries on HTTP 429 (rate limit) with exponential backoff up to max_retries.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class CampaignManager:
    """Triggers outbound PSTN calls via the Vobiz REST API.

    Args:
        config: Full merged config dict. Reads telephony_adapter.vobiz section.

    Raises:
        ValueError: If auth_id is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        vobiz_cfg = config.get("telephony_adapter", {}).get("vobiz", {})
        auth_id = vobiz_cfg.get("auth_id", "")
        if not auth_id:
            raise ValueError("telephony_adapter.vobiz.auth_id is required")
        self._auth_id = auth_id
        auth_token = vobiz_cfg.get("auth_token", "")
        api_base = vobiz_cfg.get("api_base", "").rstrip("/")
        from_number = vobiz_cfg.get("from_number", "")
        if not api_base:
            raise ValueError("telephony_adapter.vobiz.api_base is required")
        if not from_number:
            raise ValueError("telephony_adapter.vobiz.from_number is required")
        self._auth_token = auth_token
        self._api_base = api_base
        self._from_number = from_number
        self._public_url = config.get("telephony_adapter", {}).get("public_url", "").rstrip("/")
        self._max_retries = int(vobiz_cfg.get("max_retries", 3))

    async def initiate_call(self, to_number: str) -> dict:
        """Trigger an outbound call to the given number via the Vobiz REST API.

        The answer_url points to this service's /answer endpoint so Vobiz will
        route the answered call back through the Pipecat pipeline.

        Args:
            to_number: E.164 phone number to dial (e.g., "+919148223344").

        Returns:
            Vobiz API response dict (contains callSid on success).

        Raises:
            ValueError: If to_number is empty.
            Exception: If the Vobiz API returns a non-recoverable error after
                       max_retries attempts.
        """
        if not to_number or not to_number.strip():
            raise ValueError("to_number must not be empty")

        url = f"{self._api_base}/Account/{self._auth_id}/Call/"
        payload = {
            "from": self._from_number,
            "to": to_number,
            "answer_url": f"{self._public_url}/answer",
            "answer_method": "POST",
        }
        headers = {
            "X-Auth-ID": self._auth_id,
            "X-Auth-Token": self._auth_token,
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload, headers=headers)

                if response.status_code in (200, 201):
                    logger.info(
                        "campaign_manager.call_initiated",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "success",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return response.json()

                if response.status_code == 429:
                    wait = 1.0 * (2 ** attempt)
                    logger.warning(
                        "campaign_manager.rate_limited",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "failure",
                            "attempt": attempt + 1,
                            "retry_after_s": wait,
                        },
                    )
                    last_error = Exception(f"HTTP 429 on attempt {attempt + 1}")
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(wait)
                    continue

                raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")

            except Exception as e:
                if "HTTP 429" not in str(e):
                    logger.error(
                        "campaign_manager.error",
                        extra={
                            "operation": "campaign_manager.initiate_call",
                            "status": "failure",
                            "error": f"{type(e).__name__}: {e}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    raise Exception(f"outbound call failed: {e}") from e
                last_error = e

        raise Exception(f"outbound call failed after {self._max_retries} attempts: {last_error}")
