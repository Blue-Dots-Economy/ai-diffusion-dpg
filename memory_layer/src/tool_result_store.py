"""
memory_layer/src/tool_result_store.py

ToolResultStore — Redis storage for tool-result entries (spec §6).

One key per entry, TTL fixed at write time:
  ml:tr:{s|u}:{owner}:{tool}:{args_hash}   entry JSON, SET ... EX ttl
  ml:tr:idx:{s|u}:{owner}                  SET of that owner's entry keys
owner = HMAC-SHA256(secret, session_id|user_id)[:32] — never the raw id.

Disabled (reads empty, writes no-op) when no secret is configured. Never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "ml:tr"
_SCOPE_LETTER = {"session": "s", "user": "u"}
_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HASH_RE = re.compile(r"^[a-f0-9]{4,64}$")


class ToolResultStore:
    """Stores projected tool results per session or per user.

    Args:
        client: A redis-py client created with ``decode_responses=True``.
        secret: HMAC key for owner pseudonyms. Empty disables the store.
        session_ttl_seconds: Upper bound for session-scope TTLs.
        max_user_ttl_seconds: Upper bound for user-scope TTLs.
    """

    def __init__(self, client, secret: str, session_ttl_seconds: int,
                 max_user_ttl_seconds: int) -> None:
        self._client = client
        self._secret = (secret or "").encode()
        self._max_ttl = {"session": int(session_ttl_seconds), "user": int(max_user_ttl_seconds)}
        # Reported once per process on FIRST USE, not only here: a startup line
        # is one among thousands and was missed entirely. A disabled store makes
        # every cached tool result vanish between turns, which downstream looks
        # like a model or routing fault, not a missing secret. See the F53
        # write-up in poc-reports — it cost three wrong root causes.
        self._disabled_reported = False
        if not self._secret:
            logger.error(
                "tool_result_store.disabled",
                extra={"operation": "tool_result_store.init", "status": "disabled",
                       "reason": "TOOL_RESULT_KEY_SECRET not set",
                       "impact": "tool results are not persisted between turns"},
            )

    def _report_disabled(self, operation: str) -> None:
        """Log once, at the first point the store is actually needed."""
        if self._disabled_reported:
            return
        self._disabled_reported = True
        logger.error(
            "tool_result_store.unavailable",
            extra={"operation": operation, "status": "failure",
                   "reason": "TOOL_RESULT_KEY_SECRET not set",
                   "impact": "tool results are dropped; anything that reads a previous "
                             "turn's results (e.g. selecting an offered option) will fail"},
        )

    @property
    def enabled(self) -> bool:
        """True when a secret is configured."""
        return bool(self._secret)

    def pseudonym(self, value: str) -> str:
        """Return the 32-hex-char HMAC pseudonym for an owner id."""
        return hmac.new(self._secret, str(value).encode(), hashlib.sha256).hexdigest()[:32]

    def _idx(self, scope: str, owner_id: str) -> str:
        return f"{_PREFIX}:idx:{_SCOPE_LETTER[scope]}:{self.pseudonym(owner_id)}"

    def _key_prefix(self, scope: str, owner_id: str) -> str:
        return f"{_PREFIX}:{_SCOPE_LETTER[scope]}:{self.pseudonym(owner_id)}:"

    def put(self, scope: str, owner_id: str, tool: str, args_hash: str, data: Any,
            ttl_seconds: int, origin: str = "turn", now: float | None = None) -> bool:
        """Store one entry with a TTL fixed now. Returns True when stored."""
        if not self.enabled:
            self._report_disabled("tool_result_store.put")
            return False
        if (scope not in _SCOPE_LETTER or not owner_id
                or not _TOOL_RE.match(tool or "") or not _HASH_RE.match(args_hash or "")):
            return False
        now = time.time() if now is None else now
        start = time.time()
        try:
            ttl = min(int(ttl_seconds), self._max_ttl[scope])
            if ttl <= 0:
                return False
            key = f"{self._key_prefix(scope, owner_id)}{tool}:{args_hash}"
            idx = self._idx(scope, owner_id)
            value = json.dumps({
                "tool": tool, "args_hash": args_hash, "data": data, "fetched_at": now,
                "expires_at": now + ttl, "origin": origin, "scope": scope,
            })
            pipe = self._client.pipeline()
            pipe.set(key, value, ex=ttl)
            pipe.sadd(idx, key)
            pipe.expire(idx, ttl, nx=True)   # first expiry for a new index
            pipe.expire(idx, ttl, gt=True)   # only ever extends it
            pipe.execute()
            logger.info("tool_result_store.put", extra={
                "operation": "tool_result_store.put", "status": "success", "tool": tool,
                "scope": scope, "ttl_s": ttl, "latency_ms": int((time.time() - start) * 1000)})
            return True
        except Exception as e:
            logger.error("tool_result_store.put_error", extra={
                "operation": "tool_result_store.put", "status": "failure", "tool": tool,
                "error": type(e).__name__})
            return False

    def read(self, session_id: str, user_id: str, now: float | None = None) -> list[dict]:
        """Return unexpired entries for the session and the user. Cleans dead index members."""
        if not self.enabled:
            self._report_disabled("tool_result_store.read")
            return []
        now = time.time() if now is None else now
        idxs = [self._idx("session", session_id), self._idx("user", user_id)]
        start = time.time()
        try:
            pipe = self._client.pipeline()
            for idx in idxs:
                pipe.smembers(idx)
            members = pipe.execute()
            owner_of: dict[str, str] = {}
            for idx, keys in zip(idxs, members):
                for k in sorted(keys or ()):
                    owner_of[k] = idx
            if not owner_of:
                return []
            keys = list(owner_of)
            raws = self._client.mget(keys)
            out: list[dict] = []
            dead: dict[str, list[str]] = {}
            for key, raw in zip(keys, raws):
                try:
                    entry = json.loads(raw) if raw is not None else None
                except (TypeError, ValueError):
                    entry = None
                if not isinstance(entry, dict):
                    dead.setdefault(owner_of[key], []).append(key)
                    continue
                if float(entry.get("expires_at", 0)) <= now:
                    continue
                out.append(entry)
            if dead:
                pipe = self._client.pipeline()
                for idx, ks in dead.items():
                    pipe.srem(idx, *ks)
                pipe.execute()
            logger.info("tool_result_store.read", extra={
                "operation": "tool_result_store.read", "status": "success", "entries": len(out),
                "latency_ms": int((time.time() - start) * 1000)})
            return out
        except Exception as e:
            logger.error("tool_result_store.read_error", extra={
                "operation": "tool_result_store.read", "status": "failure", "error": type(e).__name__})
            return []

    def invalidate(self, scope: str, owner_id: str, tool: str) -> int:
        """Delete every entry of ``tool`` for this owner. Returns the count deleted."""
        if not self.enabled:
            self._report_disabled("tool_result_store.invalidate")
            return 0
        if scope not in _SCOPE_LETTER or not owner_id:
            return 0
        idx = self._idx(scope, owner_id)
        prefix = f"{self._key_prefix(scope, owner_id)}{tool}:"
        try:
            doomed = [k for k in (self._client.smembers(idx) or ()) if k.startswith(prefix)]
            if doomed:
                pipe = self._client.pipeline()
                pipe.delete(*doomed)
                pipe.srem(idx, *doomed)
                pipe.execute()
            logger.info("tool_result_store.invalidate", extra={
                "operation": "tool_result_store.invalidate", "status": "success",
                "tool": tool, "scope": scope, "deleted": len(doomed)})
            return len(doomed)
        except Exception as e:
            logger.error("tool_result_store.invalidate_error", extra={
                "operation": "tool_result_store.invalidate", "status": "failure",
                "error": type(e).__name__})
            return 0

    def delete_owner(self, scope: str, owner_id: str) -> None:
        """Delete all of an owner's entries and its index (session end, erasure)."""
        if not self.enabled or scope not in _SCOPE_LETTER or not owner_id:
            return
        idx = self._idx(scope, owner_id)
        try:
            keys = list(self._client.smembers(idx) or ())
            pipe = self._client.pipeline()
            if keys:
                pipe.delete(*keys)
            pipe.delete(idx)
            pipe.execute()
        except Exception as e:
            logger.error("tool_result_store.delete_owner_error", extra={
                "operation": "tool_result_store.delete_owner", "status": "failure",
                "error": type(e).__name__})
