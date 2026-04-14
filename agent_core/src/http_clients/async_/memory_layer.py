"""
agent_core/src/http_clients/async_memory_layer.py

Async HTTP client for the Memory Layer service (port 8002).
Mirror of MemoryLayerHttpClient using httpx.AsyncClient.
Used exclusively by stream_turn(); the sync client is unchanged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.interfaces.async_.memory_layer import AsyncMemoryLayerBase
from src.models import ContextBundle

logger = logging.getLogger(__name__)


class AsyncMemoryLayerHttpClient(AsyncMemoryLayerBase):
    """Async HTTP client that calls the Memory Layer service.

    Args:
        config: Full config dict. Reads memory_client.endpoint and
                memory_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        client_cfg = config.get("memory_client", {})
        self._endpoint: str = client_cfg.get("endpoint", "http://localhost:8002")
        self._timeout_s: float = client_cfg.get("timeout_ms", 5000) / 1000
        self._client = httpx.AsyncClient(timeout=self._timeout_s)

        logger.info(
            "async_memory_http_client.init",
            extra={
                "operation": "async_memory_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        await self._client.aclose()

    async def context_bundle(self, session_id: str, user_id: str, adopt: bool = True) -> ContextBundle:
        """POST /context_bundle -> ContextBundle. Returns ContextBundle.empty() on failure."""
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = await self._client.post(
                f"{self._endpoint}/context_bundle",
                json={"session_id": session_id, "user_id": user_id, "adopt": adopt},
            )
            response.raise_for_status()
            data = response.json()

            bundle = ContextBundle(
                session=data.get("session") or {},
                profile=data.get("profile") or {},
                journey=data.get("journey"),
            )

            logger.info(
                "async_memory_http_client.context_bundle",
                extra={
                    "operation": "async_memory_http_client.context_bundle",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return bundle

        except Exception as e:
            logger.error(
                "async_memory_http_client.context_bundle_error",
                extra={
                    "operation": "async_memory_http_client.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ContextBundle.empty()

    async def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """POST /write. Logs and continues on failure. Never raises."""
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = await self._client.post(
                f"{self._endpoint}/write",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "scope": scope,
                    "key": key,
                    "value": value,
                },
            )
            response.raise_for_status()
            logger.info(
                "async_memory_http_client.write",
                extra={
                    "operation": "async_memory_http_client.write",
                    "status": "success",
                    "session_id": session_id,
                    "key": key,
                    "scope": scope,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "async_memory_http_client.write_error",
                extra={
                    "operation": "async_memory_http_client.write",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    async def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """POST /flush_session. Logs and continues on failure. Never raises."""
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = await self._client.post(
                f"{self._endpoint}/flush_session",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "end_reason": end_reason or "unknown",
                },
            )
            response.raise_for_status()
            logger.info(
                "async_memory_http_client.flush_session",
                extra={
                    "operation": "async_memory_http_client.flush_session",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "async_memory_http_client.flush_session_error",
                extra={
                    "operation": "async_memory_http_client.flush_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    async def get_active_sessions(self, user_id: str) -> list[dict]:
        """GET /sessions/{user_id}. Returns [] on failure. Never raises."""
        if not user_id:
            return []

        start = time.time()
        try:
            response = await self._client.get(
                f"{self._endpoint}/sessions/{user_id}",
            )
            response.raise_for_status()
            sessions = response.json()
            logger.info(
                "async_memory_http_client.get_active_sessions",
                extra={
                    "operation": "async_memory_http_client.get_active_sessions",
                    "status": "success",
                    "user_id": user_id,
                    "session_count": len(sessions) if isinstance(sessions, list) else 0,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return sessions if isinstance(sessions, list) else []
        except Exception as e:
            logger.error(
                "async_memory_http_client.get_active_sessions_error",
                extra={
                    "operation": "async_memory_http_client.get_active_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

    async def delete_user(self, user_id: str) -> None:
        """DELETE /user/{user_id}. Logs and continues on failure. Never raises."""
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = await self._client.delete(
                f"{self._endpoint}/user/{user_id}",
            )
            response.raise_for_status()
            logger.info(
                "async_memory_http_client.delete_user",
                extra={
                    "operation": "async_memory_http_client.delete_user",
                    "status": "success",
                    "user_id": user_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "async_memory_http_client.delete_user_error",
                extra={
                    "operation": "async_memory_http_client.delete_user",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    async def record_audit_session(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: str = None,
        consent_given: str = None,
    ) -> None:
        """POST /audit/session. Logs and continues on failure. Never raises."""
        if not session_id or not user_id or not action:
            logger.error(
                "async_memory_http_client.record_audit_session_invalid",
                extra={
                    "operation": "async_memory_http_client.record_audit_session",
                    "status": "failure",
                    "error": "session_id, user_id, and action must be non-empty",
                },
            )
            return
        try:
            response = await self._client.post(
                f"{self._endpoint}/audit/session",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "action": action,
                    "reason": reason,
                    "consent_given": consent_given,
                },
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(
                "async_memory_http_client.record_audit_session_error",
                extra={
                    "operation": "async_memory_http_client.record_audit_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    async def record_audit_turn(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_message: str,
        system_message: str,
        metadata: dict = None,
    ) -> None:
        """POST /audit/turn. Logs and continues on failure. Never raises."""
        if not session_id or not user_id or not turn_id:
            logger.error(
                "async_memory_http_client.record_audit_turn_invalid",
                extra={
                    "operation": "async_memory_http_client.record_audit_turn",
                    "status": "failure",
                    "error": "session_id, user_id, and turn_id must be non-empty",
                },
            )
            return
        try:
            response = await self._client.post(
                f"{self._endpoint}/audit/turn",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "turn_id": turn_id,
                    "user_message": user_message,
                    "system_message": system_message,
                    "metadata": metadata,
                },
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(
                "async_memory_http_client.record_audit_turn_error",
                extra={
                    "operation": "async_memory_http_client.record_audit_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    async def get_chat_history(self, session_id: str) -> list[dict]:
        """GET /audit/sessions/{session_id}/history. Returns [] on failure. Never raises."""
        if not session_id:
            return []
        start = time.time()
        try:
            response = await self._client.get(
                f"{self._endpoint}/audit/sessions/{session_id}/history",
            )
            response.raise_for_status()
            history = response.json()
            logger.info(
                "async_memory_http_client.get_chat_history",
                extra={
                    "operation": "async_memory_http_client.get_chat_history",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return history if isinstance(history, list) else []
        except Exception as e:
            logger.error(
                "async_memory_http_client.get_chat_history_error",
                extra={
                    "operation": "async_memory_http_client.get_chat_history",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []
