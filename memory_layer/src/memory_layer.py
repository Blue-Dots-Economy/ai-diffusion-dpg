"""
memory_layer/src/memory_layer.py

MemoryLayer — the top-level orchestrator for the Memory Layer DPG.

Implements the same 5-method contract as MemoryLayerBase (agent_core interface),
using RedisSessionStore (K1), GraphUserStore (K2), GraphJourneyStore (K3),
and GraphContextStore (K4) as the backing stores.

This class is the only entry point — server.py calls this, nothing else.
All store classes are injected at construction time (testable via mocks).

Config is read once at init. No config re-reads at request time.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from neo4j import GraphDatabase

from session_store import RedisSessionStore
from graph_user_store import GraphUserStore
from graph_journey_store import GraphJourneyStore
from graph_context_store import GraphContextStore
from audit_store_base import AuditStoreBase
from audit_store import SQLiteAuditStore

logger = logging.getLogger(__name__)


# Fields that latch state for the duration of a single session and must NOT
# be carried over when a new session adopts a prior session's state. Adopting
# these would (e.g.) suppress the greeting on every callback for the same user.
_SESSION_LIFECYCLE_FIELDS: frozenset[str] = frozenset({
    "opening_phrase_emitted",
})


class MemoryLayer:
    """
    Orchestrates Redis + Neo4j for all Memory Layer operations.

    Implements the same 5-method contract as MemoryLayerBase (agent_core/interfaces/memory_layer.py):
      - context_bundle(session_id, user_id) -> dict  (ContextBundle-shaped; see method docstring)
      - write(session_id, user_id, scope, key, value) -> None
      - flush_session(session_id, user_id, end_reason) -> None
      - get_active_sessions(user_id) -> list[dict]
      - delete_user(user_id) -> None

    Direct inheritance from MemoryLayerBase is not possible — this is a separate
    deployable service that cannot import agent_core's interface module without
    creating a circular service dependency. Method signatures are kept in manual
    sync with MemoryLayerBase; any drift should be caught by integration tests.

    Args:
        config: Full merged config dict (dpg.yaml deep-merged with domain.yaml).
                Reads state.session, state.persistent, redis, neo4j sections.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._config = config
        self._session_cfg = config.get("state", {}).get("session", {})
        self._persistent_cfg = config.get("state", {}).get("persistent", {})

        # TTL in seconds
        ttl_minutes = self._session_cfg.get("ttl_minutes", 60)
        self._ttl_seconds: int = ttl_minutes * 60

        # Session schema defaults — used to build initial session state
        self._schema: dict = self._session_cfg.get("schema", {})

        # Declared profile fields — used by GraphUserStore to route writes
        subnodes = self._persistent_cfg.get("graph", {}).get("subnodes", {})
        self._declared_fields: list[str] = (
            subnodes.get("UserProfile", {}).get("declared_fields", [])
        )

        # Journey child config
        journey_children: list[dict] = (
            subnodes.get("JourneyHistory", {})
            .get("child", {})
            .get("children", [])
        )

        # merge_on_session_end rules
        self._merge_rules: list[dict] = (
            self._persistent_cfg.get("merge_on_session_end", [])
        )

        # Default storage mode — used when user_storage_mode is absent from session.
        # "saved" keeps Neo4j data. "anonymous" deletes it at flush.
        self._default_storage_mode: str = (
            config.get("user_data_persistence", {}).get("default_mode", "saved")
        )

        # Build SCOPE_MAP from config
        self._scope_map: dict[str, str] = _build_scope_map(
            self._schema, self._declared_fields, journey_children
        )

        # Initialise Redis store
        self._redis = RedisSessionStore(config, self._ttl_seconds)

        # Initialise Memgraph driver + stores (neo4j driver connects via Bolt — Apache 2.0)
        memgraph_cfg = config.get("memgraph", {})
        memgraph_uri = os.environ.get("MEMGRAPH_URI") or memgraph_cfg.get("uri", "bolt://localhost:7687")
        memgraph_user = os.environ.get("MEMGRAPH_USER") or memgraph_cfg.get("user", "memgraph")
        memgraph_password = os.environ.get("MEMGRAPH_PASSWORD") or memgraph_cfg.get("password", "")
        memgraph_timeout = memgraph_cfg.get("connection_timeout_s", 5)

        self._graph_driver = GraphDatabase.driver(
            memgraph_uri,
            auth=(memgraph_user, memgraph_password),
            connection_timeout=memgraph_timeout,
        )
        self._user_store = GraphUserStore(self._graph_driver, self._declared_fields)
        self._journey_store = GraphJourneyStore(self._graph_driver, journey_children)
        self._context_store = GraphContextStore(self._graph_driver)

        # Initialise SQLite audit store
        audit_db = config.get("audit", {}).get("db_path", "audit.db")
        self._audit = SQLiteAuditStore(audit_db)

        logger.info(
            "memory_layer.init",
            extra={
                "operation": "memory_layer.init",
                "status": "success",
                "ttl_seconds": self._ttl_seconds,
                "declared_field_count": len(self._declared_fields),
                "journey_child_types": [c["label"] for c in journey_children],
                "scope_map_keys": len(self._scope_map),
            },
        )

    # ------------------------------------------------------------------
    # Public interface — mirrors MemoryLayerBase (agent_core)
    # ------------------------------------------------------------------

    def context_bundle(self, session_id: str, user_id: str, adopt: bool = True) -> dict:
        """
        Called at the start of every turn. Returns a ContextBundle-shaped dict.

        Return type is `dict` rather than `ContextBundle` because this service
        cannot import agent_core's ContextBundle model — the two are separate
        deployable packages that communicate over HTTP. The returned dict has
        exactly the shape {session: dict, profile: dict, journey: dict|None},
        which the Agent Core HTTP client (memory_layer.py in agent_core) receives
        as JSON and deserialises into a ContextBundle via ContextBundle.from_dict().

        This intentional divergence from MemoryLayerBase's `-> ContextBundle`
        signature is a cross-service boundary artefact, not a contract violation.

        New session: initialises Redis + Neo4j (creates user graph if needed,
                     creates Journey node, seeds Redis hash with schema defaults).
        Existing session: reads Redis hash directly (hot path).
        In both cases: reads UserProfile from Neo4j.
        """
        start = time.time()
        try:
            if not session_id:
                raise ValueError("session_id must not be empty")
            if not user_id:
                raise ValueError("user_id must not be empty")

            session_exists = self._redis.session_exists(session_id)

            if session_exists:
                # Hot path — session already in Redis
                session_data = self._redis.get_session(session_id)
                session_data = self._coerce_session_types(session_data)
                
                # Reset TTL on resume
                self._redis.reset_session_ttl(session_id)
                # Update last_accessed in user index
                self._redis.update_last_accessed(user_id, session_id)
                profile = self._user_store.get_profile(user_id)
                bundle = {"session": session_data, "profile": profile, "journey": None}

            else:
                # New session — check Neo4j for returning user
                is_returning = self._user_store.user_exists(user_id)

                if not is_returning:
                    # Brand new user — create full graph structure
                    self._user_store.create_user_graph(user_id)

                # Create Journey node (one per session)
                self._journey_store.create_journey(user_id, session_id)

                # Build initial session state from schema defaults
                initial_state = _build_initial_session(
                    session_id, user_id, self._schema, is_returning
                )

                # ── Session Adoption ────────────────────────────────────────
                # If a recent session exists in Redis for this user_id,
                # "adopt" its state instead of starting from scratch.
                # This allows resumption across volatile session IDs.
                # Disabled when adopt=False (caller explicitly wants a clean
                # "New chat" — only persistent profile facts carry over).
                if adopt:
                    active_sessions = self.get_active_sessions(user_id)
                    last_session_id = active_sessions[0]["session_id"] if active_sessions else None
                    if last_session_id and last_session_id != session_id:
                        last_state = self._redis.get_session(last_session_id)
                        if last_state:
                            # Merge last session state into our defaults, skipping
                            # session-lifecycle flags that must reset on a new
                            # session (e.g. opening_phrase_emitted — a per-session
                            # latch that, if carried over, suppresses the greeting
                            # on every callback for the same user).
                            for k, v in last_state.items():
                                if k in _SESSION_LIFECYCLE_FIELDS:
                                    continue
                                initial_state[k] = v
                            initial_state = self._coerce_session_types(initial_state)
                            initial_state["was_adopted"] = True
                            # Re-assert infrastructure fields that must be fresh for each
                            # new session. Without this, adoption would carry over the
                            # previous session's is_returning=False into a returning user's
                            # session, causing the orchestrator to always log is_returning=False.
                            initial_state["user_id"] = user_id
                            initial_state["journey_id"] = session_id
                            initial_state["is_returning"] = "true" if is_returning else "false"
                            logger.info(
                                "memory_layer.session_adoption",
                                extra={"session_id": session_id, "adopted_from": last_session_id}
                            )

                # If returning user — pre-populate profile fields into session
                profile: dict = {}
                journey: dict | None = None
                if is_returning:
                    profile = self._user_store.get_profile(user_id)
                    journey = self._journey_store.get_last_journey_summary(
                        user_id, session_id
                    )
                    # Copy declared profile fields into session for fast access
                    for field_name in self._declared_fields:
                        val = profile.get(field_name)
                        if val is not None:
                            initial_state[field_name] = str(val)

                    if journey:
                        # Attach signals from last journey
                        journey["signals"] = self._context_store.get_signals_for_journey(
                            user_id, journey.get("journey_id", "")
                        )

                # Write to Redis
                self._redis.init_session(session_id, initial_state)
                # Register in user index
                self._redis.register_session(user_id, session_id)
                # Record session audit start
                self._audit.record_session_event(session_id, user_id, "start")

                bundle = {
                    "session": initial_state,
                    "profile": profile,
                    "journey": journey,
                }

            logger.info(
                "memory_layer.context_bundle",
                extra={
                    "operation": "memory_layer.context_bundle",
                    "status": "success",
                    "session_id": session_id,
                    "session_existed": session_exists,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return bundle

        except Exception as e:
            logger.error(
                "memory_layer.context_bundle_error",
                extra={
                    "operation": "memory_layer.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"session": {}, "profile": {}, "journey": None}

    def write(self, session_id: str, user_id: str, scope: str, key: str, value: Any) -> None:
        """
        Route a key/value write to the correct backing store based on scope.

        scope="session"       → Redis HSET + TTL reset + user index update
        scope="persistent"    → Neo4j UserProfile upsert
        scope="signal"        → Neo4j Signal node creation (value must be a dict)
        scope="journey_event" → Neo4j Journey child node creation (value must be a dict)
        Unknown scope         → treated as "persistent" (ad-hoc UserAttribute)
        """
        start = time.time()
        try:
            if not session_id:
                raise ValueError("session_id must not be empty")
            if not user_id:
                raise ValueError("user_id must not be empty")
            if not key:
                raise ValueError("key must not be empty")

            # Resolve scope — trust the caller (orchestrator) if a valid scope is passed,
            # otherwise fallback to the scope_map.
            valid_scopes = {"session", "persistent", "signal", "journey_event"}
            resolved_scope = scope if scope in valid_scopes else self._scope_map.get(key, "persistent")

            if resolved_scope == "session":
                self._redis.set_session_field(session_id, key, value)
                self._redis.update_last_accessed(user_id, session_id)
                # When the user's storage consent changes, persist it to SQLite immediately
                # so the audit record is durable even if the session ends abruptly.
                if key == "user_storage_mode" and value:
                    consent_given = "true" if str(value) == "saved" else "false"
                    self._audit.update_consent(session_id, consent_given)

            elif resolved_scope == "persistent":
                journey_id = session_id  # journey_id == session_id
                raw = value if isinstance(value, str) else ""
                self._user_store.upsert_profile_field(
                    user_id, key, value, raw=raw, journey_id=journey_id
                )

            elif resolved_scope == "signal":
                # value must be a dict: {type, turn, raw, attributes?}
                if not isinstance(value, dict):
                    raise ValueError(f"signal value must be a dict, got {type(value)}")
                self._context_store.create_signal(
                    user_id=user_id,
                    journey_id=session_id,
                    signal_type=value.get("type", "unknown"),
                    turn=str(value.get("turn", "")),
                    raw=value.get("raw", ""),
                    attributes=value.get("attributes"),
                )

            elif resolved_scope == "journey_event":
                # value must be a dict: {label, ...fields}
                if not isinstance(value, dict):
                    raise ValueError(f"journey_event value must be a dict, got {type(value)}")
                # Use .get() (not .pop()) so we do not mutate the caller's dict.
                # Build a copy of properties with 'label' excluded — it is a routing
                # key only and has no meaning as a journey child node property.
                child_label = value.get("label", key)
                properties = {k: v for k, v in value.items() if k != "label"}
                self._journey_store.create_journey_child(
                    user_id=user_id,
                    journey_id=session_id,
                    child_label=child_label,
                    properties=properties,
                )

            else:
                # Fallback: unknown scope → persistent as ad-hoc attribute
                self._user_store.upsert_profile_field(user_id, key, value)

            logger.info(
                "memory_layer.write",
                extra={
                    "operation": "memory_layer.write",
                    "status": "success",
                    "session_id": session_id,
                    "key": key,
                    "scope": resolved_scope,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_layer.write_error",
                extra={
                    "operation": "memory_layer.write",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "scope": scope,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def flush_session(self, session_id: str, user_id: str, end_reason: str) -> None:
        """
        End a session:
          1. Read Redis session hash
          2. Apply merge_on_session_end rules (promote fields to Journey node)
          3. Close Journey node in Neo4j
          4. If user_storage_mode == "anonymous": DETACH DELETE user graph (DPDP)
          5. Delete Redis session key
          6. Remove session from user index
          7. Delete user index if no sessions remain
        """
        start = time.time()
        try:
            if not session_id:
                raise ValueError("session_id must not be empty")
            if not user_id:
                raise ValueError("user_id must not be empty")

            # 1. Read session state
            session_state = self._redis.get_session(session_id)

            # 2. Promote session fields to Journey node
            if session_state:
                self._journey_store.merge_session_fields(
                    user_id, session_id, session_state, self._merge_rules
                )

            # 3. Close Journey node
            self._journey_store.close_journey(user_id, session_id, end_reason)

            # 4. DPDP: delete Neo4j data if user opted for anonymous storage.
            # user_storage_mode is written to session by the agent_core routing
            # rule session_writes when the user expresses a preference.
            # Falls back to self._default_storage_mode if absent.
            storage_mode = (
                (session_state.get("user_storage_mode") or self._default_storage_mode)
                if session_state else self._default_storage_mode
            )
            if storage_mode == "anonymous":
                self._user_store.delete_user(user_id)

            # 5. Delete session key
            self._redis.delete_session(session_id)

            # 6 + 7. Remove from user index (deletes user key if empty)
            self._redis.remove_session_from_user_index(user_id, session_id)

            # 8. Record audit end — include final consent_given for DPDP compliance.
            # storage_mode was already resolved above; derive consent_given from it.
            # This acts as a safety net for sessions where consent was set before flush
            # or where the in-session update_consent call was missed.
            consent_given = "true" if storage_mode == "saved" else "false"
            self._audit.record_session_event(
                session_id=session_id,
                user_id=user_id,
                action="end" if end_reason != "escalation" else "escalate",
                reason=end_reason,
                consent_given=consent_given,
            )

            logger.info(
                "memory_layer.flush_session",
                extra={
                    "operation": "memory_layer.flush_session",
                    "status": "success",
                    "session_id": session_id,
                    "end_reason": end_reason,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_layer.flush_session_error",
                extra={
                    "operation": "memory_layer.flush_session",
                    "status": "failure",
                    "session_id": session_id,
                    "end_reason": end_reason,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def get_active_sessions(self, user_id: str) -> list[dict]:
        """
        Return active sessions for this user, sorted by last_accessed descending.
        Performs lazy cleanup of expired session fields in user:{user_id} hash.
        """
        start = time.time()
        try:
            if not user_id:
                return []

            raw_sessions = self._redis.get_user_sessions(user_id)
            if not raw_sessions:
                return []

            alive = []
            for session_id, last_accessed in raw_sessions.items():
                if self._redis.session_exists(session_id):
                    alive.append({
                        "session_id": session_id,
                        "last_accessed": last_accessed,
                    })
                else:
                    # Lazy cleanup: session expired, remove stale Redis + SQLite audit
                    self._redis.remove_stale_session_field(user_id, session_id)
                    self._audit.delete_session_audit(session_id)

            # Sort by last_accessed descending (most recent first)
            alive.sort(key=lambda s: s["last_accessed"], reverse=True)

            logger.info(
                "memory_layer.get_active_sessions",
                extra={
                    "operation": "memory_layer.get_active_sessions",
                    "status": "success",
                    "user_id": user_id,
                    "active_count": len(alive),
                    "stale_removed": len(raw_sessions) - len(alive),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return alive

        except Exception as e:
            logger.error(
                "memory_layer.get_active_sessions_error",
                extra={
                    "operation": "memory_layer.get_active_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

    def delete_user(self, user_id: str) -> None:
        """DPDP right-to-erasure: delete all Neo4j graph data + Redis user index."""
        start = time.time()
        try:
            if not user_id:
                raise ValueError("user_id must not be empty")
            self._user_store.delete_user(user_id)
            self._redis.delete_user_index(user_id)
            logger.info(
                "memory_layer.delete_user",
                extra={
                    "operation": "memory_layer.delete_user",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_layer.delete_user_error",
                extra={
                    "operation": "memory_layer.delete_user",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def delete_session(self, session_id: str, user_id: str) -> None:
        """Delete a single session from Redis and SQLite audit.

        Intended for user-initiated "delete conversation" actions from the
        Reach Layer web UI. Wipes Redis session hash, removes from the user's
        session index, and deletes the SQLite audit rows (turn_audit +
        session_audit). Persistent profile data in Memgraph is untouched so
        the user's profile facts carry over to future conversations.

        Args:
            session_id: Session identifier to delete.
            user_id: Owning user identifier — used to remove from user index.
        """
        start = time.time()
        try:
            if not session_id:
                raise ValueError("session_id must not be empty")
            if not user_id:
                raise ValueError("user_id must not be empty")

            self._redis.delete_session(session_id)
            self._redis.remove_session_from_user_index(user_id, session_id)
            self._audit.delete_session_audit(session_id)

            logger.info(
                "memory_layer.delete_session",
                extra={
                    "operation": "memory_layer.delete_session",
                    "status": "success",
                    "session_id": session_id,
                    "user_id": user_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_layer.delete_session_error",
                extra={
                    "operation": "memory_layer.delete_session",
                    "status": "failure",
                    "session_id": session_id,
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            raise

    def record_audit_session(
        self,
        session_id: str,
        user_id: str,
        action: str,
        reason: str = None,
        consent_given: str = None,
    ) -> None:
        """Record session lifecycle event in SQLite audit store."""
        self._audit.record_session_event(session_id, user_id, action, reason, consent_given)

    def record_audit_turn(
        self, 
        session_id: str, 
        user_id: str, 
        turn_id: str, 
        user_message: str, 
        system_message: str,
        metadata: dict = None
    ) -> None:
        """Record a single conversation turn in SQLite audit store."""
        # Detect subagent, intent, model from metadata if available
        subagent_id = metadata.get("subagent_id", "") if metadata else ""
        intent = metadata.get("intent", "") if metadata else ""
        model = metadata.get("model", "") if metadata else ""
        latency_ms = metadata.get("latency_ms", 0) if metadata else 0
        
        self._audit.record_turn_history(
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            user_msg=user_message,
            system_msg=system_message,
            subagent_id=subagent_id,
            intent=intent,
            model=model,
            latency_ms=latency_ms,
            metadata=metadata
        )

    def get_chat_history(self, session_id: str) -> list[dict]:
        """Expose audit history retrieval for future UI use."""
        return self._audit.get_history(session_id)

    def get_history_for_active_session(self, user_id: str) -> dict:
        """Return the most recent active session and its full chat history for a user.

        Combines active session lookup with history retrieval to avoid multiple
        round-trips from callers that need both the session_id and turn history.

        Args:
            user_id: The user identifier.

        Returns:
            Dict with session_id (str or None) and turns (list[dict]).
            Returns {"session_id": None, "turns": []} if no active session exists
            or user_id is empty.
        """
        if not user_id:
            return {"session_id": None, "turns": []}
        sessions = self.get_active_sessions(user_id)
        if not sessions:
            return {"session_id": None, "turns": []}
        session_id = sessions[0]["session_id"]
        turns = self.get_chat_history(session_id)
        return {"session_id": session_id, "turns": turns}

    def _coerce_session_types(self, session_data: dict[str, Any]) -> dict[str, Any]:
        """Coerce Redis strings back to native Python types (bool, dict, list)."""
        data = dict(session_data)
        for key, val in data.items():
            if val == "true":
                data[key] = True
            elif val == "false":
                data[key] = False
            elif isinstance(val, str) and (val.startswith("{") or val.startswith("[")):
                try:
                    data[key] = json.loads(val)
                except (ValueError, TypeError):
                    pass
        return data


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_initial_session(
    session_id: str,
    user_id: str,
    schema: dict,
    is_returning: bool,
) -> dict:
    """
    Build the initial session hash from schema defaults.
    Always includes infrastructure fields: user_id, journey_id, is_returning.
    """
    state: dict[str, Any] = {
        "user_id": user_id,
        "journey_id": session_id,  # journey_id == session_id (one journey per session)
        "is_returning": "true" if is_returning else "false",
    }
    for field_name, field_cfg in schema.items():
        default = field_cfg.get("default", "")
        if isinstance(default, list):
            state[field_name] = json.dumps(default)
        elif isinstance(default, bool):
            state[field_name] = "true" if default else "false"
        else:
            state[field_name] = str(default)
    return state


def _build_scope_map(
    schema: dict,
    declared_fields: list[str],
    journey_children: list[dict],
) -> dict[str, str]:
    """
    Compile the SCOPE_MAP from config at startup.
    Called once in __init__ — never at request time.
    """
    scope_map: dict[str, str] = {
        # Infrastructure fields — always session
        "user_id": "session",
        "journey_id": "session",
        "is_returning": "session",
        # Signal — always this key
        "signal": "signal",
    }
    # Session fields from schema
    for field_name in schema:
        scope_map[field_name] = "session"
    # Persistent profile fields
    for field_name in declared_fields:
        scope_map[field_name] = "persistent"
    # Journey event keys — derived from journey child rel names (lowercased)
    for child in journey_children:
        event_key = child.get("rel", "").lower()
        if event_key:
            scope_map[event_key] = "journey_event"
    return scope_map
