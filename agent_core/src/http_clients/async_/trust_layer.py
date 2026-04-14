"""
agent_core/src/http_clients/async_trust_layer.py

Async HTTP client for the Trust Layer service (port 8003).
Mirror of TrustLayerHttpClient using httpx.AsyncClient.
Used exclusively by stream_turn(); the sync client is unchanged.

Fail-closed on all errors — same semantics as the sync client.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from src.interfaces.async_.trust_layer import AsyncTrustLayerBase
from src.models import TrustCheckResult

logger = logging.getLogger(__name__)


class AsyncTrustLayerConstraintError(RuntimeError):
    """Raised when async assemble_constraints fails; caller must block the turn."""


_ESCALATE_FAILED = {"queued": False, "ticket_id": "", "holding_message": ""}


class AsyncTrustLayerHttpClient(AsyncTrustLayerBase):
    """Async HTTP client calling the Trust Layer service. Fail-closed on all errors.

    Args:
        config: Full config dict. Reads trust_client.endpoint and
                trust_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        client_cfg = config.get("trust_client", {})
        self._endpoint: str = client_cfg.get("endpoint", "http://localhost:8003")
        self._timeout_s: float = client_cfg.get("timeout_ms", 2000) / 1000
        self._client = httpx.AsyncClient(timeout=self._timeout_s)
        logger.info(
            "async_trust_http_client.init",
            extra={
                "operation": "async_trust_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        await self._client.aclose()

    async def check_input(
        self, session_id: str, user_message: str, active_risks: Optional[list[str]] = None
    ) -> TrustCheckResult:
        """Call POST /check/input. Returns block on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._endpoint}/check/input",
                    json={"session_id": session_id, "message": user_message or "", "active_risks": active_risks},
                )
                resp.raise_for_status()
                data = resp.json()
                result = TrustCheckResult(
                    passed=data.get("passed", False),
                    action=data.get("action", "block"),
                    reason=data.get("reason"),
                )
                logger.info("async_trust_http_client.check_input", extra={
                    "operation": "async_trust_http_client.check_input", "status": "success",
                    "session_id": session_id, "action": result.action,
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return result
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("async_trust_http_client.check_input_retry", extra={
                        "operation": "async_trust_http_client.check_input", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    await asyncio.sleep(0.1)
                    continue
                logger.error("async_trust_http_client.check_input_error", extra={
                    "operation": "async_trust_http_client.check_input", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")
            except Exception as e:
                logger.error("async_trust_http_client.check_input_error", extra={
                    "operation": "async_trust_http_client.check_input", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")
        return TrustCheckResult(passed=False, action="block")

    async def check_output(self, session_id: str, llm_response: str) -> TrustCheckResult:
        """Call POST /check/output. Returns block on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._endpoint}/check/output",
                    json={"session_id": session_id, "response": llm_response or ""},
                )
                resp.raise_for_status()
                data = resp.json()
                result = TrustCheckResult(
                    passed=data.get("passed", False),
                    action=data.get("action", "block"),
                    reason=data.get("reason"),
                )
                logger.info("async_trust_http_client.check_output", extra={
                    "operation": "async_trust_http_client.check_output", "status": "success",
                    "session_id": session_id, "action": result.action,
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return result
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("async_trust_http_client.check_output_retry", extra={
                        "operation": "async_trust_http_client.check_output", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    await asyncio.sleep(0.1)
                    continue
                logger.error("async_trust_http_client.check_output_error", extra={
                    "operation": "async_trust_http_client.check_output", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")
            except Exception as e:
                logger.error("async_trust_http_client.check_output_error", extra={
                    "operation": "async_trust_http_client.check_output", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")
        return TrustCheckResult(passed=False, action="block")

    async def check_consent(self, session_id: str, connector_name: str) -> bool:
        """Call POST /check/consent. Returns False on failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._endpoint}/check/consent",
                    json={"session_id": session_id, "connector_name": connector_name or ""},
                )
                resp.raise_for_status()
                granted = bool(resp.json().get("granted", False))
                logger.info("async_trust_http_client.check_consent", extra={
                    "operation": "async_trust_http_client.check_consent", "status": "success",
                    "session_id": session_id, "granted": granted,
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return granted
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    await asyncio.sleep(0.1)
                    continue
                logger.error("async_trust_http_client.check_consent_error", extra={
                    "operation": "async_trust_http_client.check_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False
            except Exception as e:
                logger.error("async_trust_http_client.check_consent_error", extra={
                    "operation": "async_trust_http_client.check_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False
        return False

    async def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: Optional[str],
    ) -> dict:
        """Call POST /assemble_constraints. Raises on failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = await self._client.post(
                f"{self._endpoint}/assemble_constraints",
                json={
                    "session_id": session_id,
                    "workflow_step": workflow_step,
                    "active_risks": active_risks,
                    "user_segment": user_segment,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("async_trust_http_client.assemble_constraints", extra={
                "operation": "async_trust_http_client.assemble_constraints", "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return data
        except Exception as e:
            logger.error("async_trust_http_client.assemble_constraints_error", extra={
                "operation": "async_trust_http_client.assemble_constraints", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            raise AsyncTrustLayerConstraintError(
                f"assemble_constraints failed: {type(e).__name__}: {e}"
            ) from e

    async def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Call POST /consent/verify. Returns False on failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._endpoint}/consent/verify",
                    json={"session_id": session_id, "user_message": user_message or ""},
                )
                resp.raise_for_status()
                granted = bool(resp.json().get("granted", False))
                logger.info("async_trust_http_client.verify_consent", extra={
                    "operation": "async_trust_http_client.verify_consent", "status": "success",
                    "session_id": session_id, "granted": granted,
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return granted
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    await asyncio.sleep(0.1)
                    continue
                logger.error("async_trust_http_client.verify_consent_error", extra={
                    "operation": "async_trust_http_client.verify_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False
            except Exception as e:
                logger.error("async_trust_http_client.verify_consent_error", extra={
                    "operation": "async_trust_http_client.verify_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False
        return False

    async def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Call POST /escalate. Returns queued=False on failure."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._endpoint}/escalate",
                    json={
                        "session_id": session_id,
                        "escalation_reason": escalation_reason,
                        "user_message": user_message or "",
                        "workflow_step": workflow_step,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("async_trust_http_client.escalate", extra={
                    "operation": "async_trust_http_client.escalate", "status": "success",
                    "session_id": session_id, "ticket_id": data.get("ticket_id"),
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return data
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    await asyncio.sleep(0.1)
                    continue
                logger.error("async_trust_http_client.escalate_error", extra={
                    "operation": "async_trust_http_client.escalate", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return dict(_ESCALATE_FAILED)
            except Exception as e:
                logger.error("async_trust_http_client.escalate_error", extra={
                    "operation": "async_trust_http_client.escalate", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return dict(_ESCALATE_FAILED)
        return dict(_ESCALATE_FAILED)
