"""
memory_layer/src/session_store.py

RedisSessionStore — manages all Redis operations for the Memory Layer.

Two key types:
  session:{session_id}  — Hash, TTL-bound. Full session state for one conversation.
  user:{user_id}        — Hash, TTL-bound. Index of active sessions for a user.
                          Fields: {session_id: ISO-8601 last_accessed timestamp}

All values stored as strings in Redis (Redis hash values are always strings).
Type coercion (int, bool, list) is handled on read by the caller (MemoryLayer).

Thread-safe: redis-py connection pool handles concurrent access.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import redis

logger = logging.getLogger(__name__)


class RedisSessionStore:
    """
    Thin wrapper over redis-py for session and user index operations.

    Args:
        config: Full merged config dict.
                Reads redis.host, redis.port, redis.db, redis.password,
                redis.socket_timeout_ms, redis.socket_connect_timeout_ms.
        ttl_seconds: Session TTL in seconds (from state.session.ttl_minutes * 60).
    """

    def __init__(self, config: dict, ttl_seconds: int) -> None:
        if config is None:
            raise ValueError("config must not be None")
        if ttl_seconds is None or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")

        self._ttl = ttl_seconds
        redis_cfg = config.get("redis", {})

        timeout_s = redis_cfg.get("socket_timeout_ms", 2000) / 1000
        connect_timeout_s = redis_cfg.get("socket_connect_timeout_ms", 2000) / 1000

        self._client = redis.Redis(
            host=redis_cfg.get("host", "localhost"),
            port=redis_cfg.get("port", 6379),
            db=redis_cfg.get("db", 0),
            password=redis_cfg.get("password", None),
            socket_timeout=timeout_s,
            socket_connect_timeout=connect_timeout_s,
            decode_responses=True,   # all values returned as str, not bytes
        )

        logger.info(
            "redis_session_store.init",
            extra={
                "operation": "session_store.init",
                "status": "success",
                "host": redis_cfg.get("host", "localhost"),
                "port": redis_cfg.get("port", 6379),
                "ttl_seconds": ttl_seconds,
            },
        )

    # ------------------------------------------------------------------
    # Session key — session:{session_id}
    # ------------------------------------------------------------------

    def session_exists(self, session_id: str) -> bool:
        """Return True if session:{session_id} exists in Redis."""
        try:
            return bool(self._client.exists(f"session:{session_id}"))
        except Exception as e:
            logger.error(
                "redis_session_store.session_exists_error",
                extra={
                    "operation": "session_store.session_exists",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return False

    def get_session(self, session_id: str) -> dict:
        """
        Read full session hash. Returns {} if key does not exist.
        All values are strings — caller coerces types as needed.
        """
        start = time.time()
        try:
            data = self._client.hgetall(f"session:{session_id}")
            logger.info(
                "redis_session_store.get_session",
                extra={
                    "operation": "session_store.get_session",
                    "status": "success",
                    "session_id": session_id,
                    "field_count": len(data),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return data
        except Exception as e:
            logger.error(
                "redis_session_store.get_session_error",
                extra={
                    "operation": "session_store.get_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {}

    def init_session(self, session_id: str, initial_state: dict) -> None:
        """
        Write the initial session hash and set TTL.
        All values are serialised to strings before writing.
        """
        start = time.time()
        try:
            serialised = _serialise_mapping(initial_state)
            pipe = self._client.pipeline()
            pipe.hset(f"session:{session_id}", mapping=serialised)
            pipe.expire(f"session:{session_id}", self._ttl)
            pipe.execute()
            logger.info(
                "redis_session_store.init_session",
                extra={
                    "operation": "session_store.init_session",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "redis_session_store.init_session_error",
                extra={
                    "operation": "session_store.init_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def set_session_field(self, session_id: str, key: str, value: Any) -> None:
        """
        HSET a single field on session:{session_id} and reset TTL.
        Value is serialised to string.
        """
        start = time.time()
        try:
            pipe = self._client.pipeline()
            pipe.hset(f"session:{session_id}", key, _serialise_value(value))
            pipe.expire(f"session:{session_id}", self._ttl)
            pipe.execute()
            logger.info(
                "redis_session_store.set_session_field",
                extra={
                    "operation": "session_store.set_session_field",
                    "status": "success",
                    "session_id": session_id,
                    "key": key,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "redis_session_store.set_session_field_error",
                extra={
                    "operation": "session_store.set_session_field",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def reset_session_ttl(self, session_id: str) -> None:
        """Reset TTL on session:{session_id} without changing fields."""
        try:
            self._client.expire(f"session:{session_id}", self._ttl)
        except Exception as e:
            logger.error(
                "redis_session_store.reset_session_ttl_error",
                extra={
                    "operation": "session_store.reset_session_ttl",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def delete_session(self, session_id: str) -> None:
        """Delete session:{session_id} entirely."""
        try:
            self._client.delete(f"session:{session_id}")
        except Exception as e:
            logger.error(
                "redis_session_store.delete_session_error",
                extra={
                    "operation": "session_store.delete_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    # ------------------------------------------------------------------
    # User index key — user:{user_id}
    # ------------------------------------------------------------------

    def register_session(self, user_id: str, session_id: str) -> None:
        """
        Register session_id in user:{user_id} hash with current timestamp.
        Resets user key TTL.
        """
        start = time.time()
        try:
            now = _now_iso8601()
            pipe = self._client.pipeline()
            pipe.hset(f"user:{user_id}", session_id, now)
            pipe.expire(f"user:{user_id}", self._ttl)
            pipe.execute()
            logger.info(
                "redis_session_store.register_session",
                extra={
                    "operation": "session_store.register_session",
                    "status": "success",
                    "user_id": user_id,
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "redis_session_store.register_session_error",
                extra={
                    "operation": "session_store.register_session",
                    "status": "failure",
                    "user_id": user_id,
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def update_last_accessed(self, user_id: str, session_id: str) -> None:
        """
        Update last_accessed timestamp for session_id in user:{user_id}.
        Resets user key TTL.
        Called on every turn that writes session-scoped state.
        """
        start = time.time()
        try:
            now = _now_iso8601()
            pipe = self._client.pipeline()
            pipe.hset(f"user:{user_id}", session_id, now)
            pipe.expire(f"user:{user_id}", self._ttl)
            pipe.execute()
        except Exception as e:
            logger.error(
                "redis_session_store.update_last_accessed_error",
                extra={
                    "operation": "session_store.update_last_accessed",
                    "status": "failure",
                    "user_id": user_id,
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def get_user_sessions(self, user_id: str) -> dict[str, str]:
        """
        Read user:{user_id} hash.
        Returns {session_id: last_accessed_iso8601} or {} if key not found.
        """
        try:
            return self._client.hgetall(f"user:{user_id}")
        except Exception as e:
            logger.error(
                "redis_session_store.get_user_sessions_error",
                extra={
                    "operation": "session_store.get_user_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return {}

    def remove_session_from_user_index(self, user_id: str, session_id: str) -> None:
        """
        HDEL session_id from user:{user_id}.
        If no fields remain, deletes the user key entirely.
        """
        try:
            pipe = self._client.pipeline()
            pipe.hdel(f"user:{user_id}", session_id)
            pipe.hlen(f"user:{user_id}")
            results = pipe.execute()
            remaining = results[1]
            if remaining == 0:
                self._client.delete(f"user:{user_id}")
        except Exception as e:
            logger.error(
                "redis_session_store.remove_session_from_user_index_error",
                extra={
                    "operation": "session_store.remove_session_from_user_index",
                    "status": "failure",
                    "user_id": user_id,
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def remove_stale_session_field(self, user_id: str, session_id: str) -> None:
        """Remove a single stale session_id field from user:{user_id} during lazy cleanup."""
        try:
            self._client.hdel(f"user:{user_id}", session_id)
        except Exception as e:
            logger.error(
                "redis_session_store.remove_stale_session_field_error",
                extra={
                    "operation": "session_store.remove_stale_session_field",
                    "status": "failure",
                    "user_id": user_id,
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def delete_user_index(self, user_id: str) -> None:
        """Delete user:{user_id} key entirely (used in DPDP erasure)."""
        try:
            self._client.delete(f"user:{user_id}")
        except Exception as e:
            logger.error(
                "redis_session_store.delete_user_index_error",
                extra={
                    "operation": "session_store.delete_user_index",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now_iso8601() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialise_value(value: Any) -> str:
    """Serialise a Python value to a Redis-safe string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def _serialise_mapping(mapping: dict) -> dict[str, str]:
    """Serialise all values in a dict to Redis-safe strings."""
    return {k: _serialise_value(v) for k, v in mapping.items()}
