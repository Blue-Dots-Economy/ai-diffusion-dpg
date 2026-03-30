"""
agent_core/src/http_clients/memory_layer.py

MemoryLayerHttpClient — HTTP client for the Memory Layer service (port 8002).

Implements MemoryLayerBase (5 methods). Agent Core calls this class;
it translates each method call into the appropriate HTTP request to the
Memory Layer service and deserialises the response into ContextBundle.

Config reads from:
  memory_client.endpoint   (default "http://localhost:8002")
  memory_client.timeout_ms (default 5000)

Error handling:
  - context_bundle: returns ContextBundle.empty() on any failure.
  - write / flush_session / get_active_sessions / delete_user: log and continue.
  - Never raises to the caller.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.interfaces.memory_layer import MemoryLayerBase
from src.models import ContextBundle

logger = logging.getLogger(__name__)


class MemoryLayerHttpClient(MemoryLayerBase):
    """
    HTTP client that calls the Memory Layer service.

    Implements MemoryLayerBase so it is interchangeable with any other
    Memory Layer implementation without changing the orchestrator.

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

        logger.info(
            "memory_http_client.init",
            extra={
                "operation": "memory_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
            },
        )

    # ------------------------------------------------------------------
    # Public interface — implements MemoryLayerBase
    # ------------------------------------------------------------------

    def context_bundle(self, session_id: str, user_id: str) -> ContextBundle:
        """
        POST /context_bundle -> ContextBundle.
        Returns ContextBundle.empty() on any failure. Never raises.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = httpx.post(
                f"{self._endpoint}/context_bundle",
                json={"session_id": session_id, "user_id": user_id},
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            bundle = ContextBundle(
                session=data.get("session") or {},
                profile=data.get("profile") or {},
                journey=data.get("journey"),
            )

            logger.info(
                "memory_http_client.context_bundle",
                extra={
                    "operation": "memory_http_client.context_bundle",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return bundle

        except httpx.TimeoutException as e:
            logger.error(
                "memory_http_client.context_bundle_timeout",
                extra={
                    "operation": "memory_http_client.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ContextBundle.empty()

        except httpx.HTTPStatusError as e:
            logger.error(
                "memory_http_client.context_bundle_http_error",
                extra={
                    "operation": "memory_http_client.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ContextBundle.empty()

        except Exception as e:
            logger.error(
                "memory_http_client.context_bundle_error",
                extra={
                    "operation": "memory_http_client.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ContextBundle.empty()

    def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """
        POST /write. Called asynchronously — logs and continues on failure. Never raises.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = httpx.post(
                f"{self._endpoint}/write",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "scope": scope,
                    "key": key,
                    "value": value,
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()

            logger.info(
                "memory_http_client.write",
                extra={
                    "operation": "memory_http_client.write",
                    "status": "success",
                    "session_id": session_id,
                    "key": key,
                    "scope": scope,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.TimeoutException as e:
            logger.error(
                "memory_http_client.write_timeout",
                extra={
                    "operation": "memory_http_client.write",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_http_client.write_error",
                extra={
                    "operation": "memory_http_client.write",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """
        POST /flush_session. Logs and continues on failure. Never raises.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = httpx.post(
                f"{self._endpoint}/flush_session",
                json={
                    "session_id": session_id,
                    "user_id": user_id,
                    "end_reason": end_reason or "unknown",
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()

            logger.info(
                "memory_http_client.flush_session",
                extra={
                    "operation": "memory_http_client.flush_session",
                    "status": "success",
                    "session_id": session_id,
                    "end_reason": end_reason,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.TimeoutException as e:
            logger.error(
                "memory_http_client.flush_session_timeout",
                extra={
                    "operation": "memory_http_client.flush_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_http_client.flush_session_error",
                extra={
                    "operation": "memory_http_client.flush_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def get_active_sessions(self, user_id: str) -> list[dict]:
        """
        GET /sessions/{user_id} -> list of active session dicts.
        Returns [] on any failure. Never raises.
        """
        if not user_id:
            return []

        start = time.time()
        try:
            response = httpx.get(
                f"{self._endpoint}/sessions/{user_id}",
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            sessions = response.json()

            logger.info(
                "memory_http_client.get_active_sessions",
                extra={
                    "operation": "memory_http_client.get_active_sessions",
                    "status": "success",
                    "user_id": user_id,
                    "session_count": len(sessions),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return sessions if isinstance(sessions, list) else []

        except httpx.TimeoutException as e:
            logger.error(
                "memory_http_client.get_active_sessions_timeout",
                extra={
                    "operation": "memory_http_client.get_active_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []
        except Exception as e:
            logger.error(
                "memory_http_client.get_active_sessions_error",
                extra={
                    "operation": "memory_http_client.get_active_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

    def delete_user(self, user_id: str) -> None:
        """
        DELETE /user/{user_id}. Logs and continues on failure. Never raises.
        """
        if not user_id:
            raise ValueError("user_id must not be empty")

        start = time.time()
        try:
            response = httpx.delete(
                f"{self._endpoint}/user/{user_id}",
                timeout=self._timeout_s,
            )
            response.raise_for_status()

            logger.info(
                "memory_http_client.delete_user",
                extra={
                    "operation": "memory_http_client.delete_user",
                    "status": "success",
                    "user_id": user_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except httpx.TimeoutException as e:
            logger.error(
                "memory_http_client.delete_user_timeout",
                extra={
                    "operation": "memory_http_client.delete_user",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_http_client.delete_user_error",
                extra={
                    "operation": "memory_http_client.delete_user",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
