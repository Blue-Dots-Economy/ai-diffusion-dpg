"""
memory_layer/src/graph_journey_store.py

GraphJourneyStore — manages Journey nodes and their domain-specific child nodes.

Responsibilities:
  - Create a new Journey node under JourneyHistory (create_journey)
  - Close a Journey node on session end (close_journey)
  - Read the last journey summary for a returning user (get_last_journey_summary)
  - Create/update domain-specific journey child nodes (create_journey_child)
  - Promote session fields to Journey node properties on flush (merge_session_fields)

Journey structure (per domain.yaml):
  (JourneyHistory)-[:JOURNEY]->(Journey)
                                   └──[:OFFERED]->(Role)
                                   └──[:DROPPED_AT]->(DropOff)

All queries are parameterised. The one exception is property key names in
merge_session_fields, which must be interpolated because Neo4j does not support
parameterised property key names. Keys are validated with assert k.isidentifier()
before use — all keys are sourced from domain.yaml config, not user input.
All methods absorb exceptions — never raise.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver

logger = logging.getLogger(__name__)


class GraphJourneyStore:
    """
    Manages Journey subgraph in Neo4j.

    Args:
        driver:           Initialised neo4j.Driver instance.
        journey_children: List of journey child config dicts from domain.yaml.
                          Each: {"label": str, "rel": str, "fields": [str, ...]}
    """

    def __init__(self, driver: Driver, journey_children: list[dict]) -> None:
        if driver is None:
            raise ValueError("driver must not be None")
        if journey_children is None:
            raise ValueError("journey_children must not be None")

        self._driver = driver
        # Build lookup: label → {rel, fields} for quick dispatch in create_journey_child
        self._children_by_label: dict[str, dict] = {
            c["label"]: c for c in journey_children
        }
        # Build lookup: rel → label for reverse lookup when writing by event key
        self._children_by_rel: dict[str, dict] = {
            c["rel"]: c for c in journey_children
        }

        logger.info(
            "graph_journey_store.init",
            extra={
                "operation": "graph_journey_store.init",
                "status": "success",
                "child_types": list(self._children_by_label.keys()),
            },
        )

    # ------------------------------------------------------------------
    # Journey lifecycle
    # ------------------------------------------------------------------

    def create_journey(self, user_id: str, journey_id: str) -> None:
        """
        Create a new Journey node under JourneyHistory for this user.
        journey_id == session_id (one Journey per session).
        """
        start = time.time()
        try:
            now = _now_iso()
            with self._driver.session() as session:
                session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_JOURNEY_HISTORY]->(jh:JourneyHistory)
                    CREATE (jh)-[:JOURNEY]->(j:Journey {
                        journey_id: $journey_id,
                        started_at: $started_at,
                        ended_at: null,
                        end_reason: null
                    })
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                    started_at=now,
                )
            logger.info(
                "graph_journey_store.create_journey",
                extra={
                    "operation": "graph_journey_store.create_journey",
                    "status": "success",
                    "journey_id": journey_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_journey_store.create_journey_error",
                extra={
                    "operation": "graph_journey_store.create_journey",
                    "status": "failure",
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def close_journey(self, user_id: str, journey_id: str, end_reason: str) -> None:
        """SET ended_at and end_reason on the Journey node."""
        start = time.time()
        try:
            now = _now_iso()
            with self._driver.session() as session:
                session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_JOURNEY_HISTORY]->
                          (jh:JourneyHistory)-[:JOURNEY]->(j:Journey {journey_id: $journey_id})
                    SET j.ended_at = $ended_at, j.end_reason = $end_reason
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                    ended_at=now,
                    end_reason=end_reason,
                )
            logger.info(
                "graph_journey_store.close_journey",
                extra={
                    "operation": "graph_journey_store.close_journey",
                    "status": "success",
                    "journey_id": journey_id,
                    "end_reason": end_reason,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_journey_store.close_journey_error",
                extra={
                    "operation": "graph_journey_store.close_journey",
                    "status": "failure",
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    # ------------------------------------------------------------------
    # Journey summary (for returning users)
    # ------------------------------------------------------------------

    def get_last_journey_summary(self, user_id: str, current_journey_id: str) -> dict | None:
        """
        Read the most recently closed Journey for this user (excluding the current one).
        Returns a summary dict with outcomes and end_reason.
        Returns None if no prior journeys found.
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                # Get the most recent completed journey (has ended_at, not the current one)
                result = session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_JOURNEY_HISTORY]->
                          (jh:JourneyHistory)-[:JOURNEY]->(j:Journey)
                    WHERE j.ended_at IS NOT NULL AND j.journey_id <> $current_journey_id
                    RETURN j.journey_id AS journey_id,
                           j.started_at AS started_at,
                           j.ended_at AS ended_at,
                           j.end_reason AS end_reason
                    ORDER BY j.ended_at DESC
                    LIMIT 1
                    """,
                    user_id=user_id,
                    current_journey_id=current_journey_id,
                )
                journey_record = result.single()
                if not journey_record:
                    return None

                last_journey_id = journey_record["journey_id"]
                summary: dict = {
                    "journey_id": last_journey_id,
                    "started_at": journey_record["started_at"],
                    "ended_at": journey_record["ended_at"],
                    "end_reason": journey_record.get("end_reason", ""),
                    "outcomes": [],
                }

                # Collect all journey child nodes
                for child_cfg in self._children_by_label.values():
                    label = child_cfg["label"]
                    rel = child_cfg["rel"]
                    child_result = session.run(
                        f"""
                        MATCH (u:User {{user_id: $user_id}})-[:HAS_JOURNEY_HISTORY]->
                              (jh:JourneyHistory)-[:JOURNEY]->(j:Journey {{journey_id: $journey_id}})
                              -[:{rel}]->(child:{label})
                        RETURN properties(child) AS props
                        """,
                        user_id=user_id,
                        journey_id=last_journey_id,
                    )
                    for record in child_result:
                        summary["outcomes"].append({
                            "type": label,
                            "data": dict(record["props"]),
                        })

            logger.info(
                "graph_journey_store.get_last_journey_summary",
                extra={
                    "operation": "graph_journey_store.get_last_journey_summary",
                    "status": "success",
                    "last_journey_id": last_journey_id,
                    "outcome_count": len(summary["outcomes"]),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return summary

        except Exception as e:
            logger.error(
                "graph_journey_store.get_last_journey_summary_error",
                extra={
                    "operation": "graph_journey_store.get_last_journey_summary",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return None

    # ------------------------------------------------------------------
    # Journey child nodes (domain-specific outcomes)
    # ------------------------------------------------------------------

    def create_journey_child(
        self,
        user_id: str,
        journey_id: str,
        child_label: str,
        properties: dict[str, Any],
    ) -> None:
        """
        Create a domain-specific child node under the Journey node.

        child_label must be one of the configured journey child labels
        (e.g. "Role", "DropOff"). Unknown labels are logged and skipped.

        properties: dict of field values for the child node. Any fields not in
                    the configured field list for this child type are ignored.
        """
        start = time.time()
        child_cfg = self._children_by_label.get(child_label)
        if not child_cfg:
            logger.error(
                "graph_journey_store.create_journey_child_unknown_label",
                extra={
                    "operation": "graph_journey_store.create_journey_child",
                    "status": "failure",
                    "child_label": child_label,
                    "error": f"Unknown child label: {child_label}. "
                             f"Configured: {list(self._children_by_label.keys())}",
                },
            )
            return

        try:
            rel = child_cfg["rel"]
            allowed_fields = set(child_cfg.get("fields", []))
            # Filter properties to only allowed fields
            filtered = {k: v for k, v in properties.items() if k in allowed_fields}

            with self._driver.session() as session:
                session.run(
                    f"""
                    MATCH (u:User {{user_id: $user_id}})-[:HAS_JOURNEY_HISTORY]->
                          (jh:JourneyHistory)-[:JOURNEY]->(j:Journey {{journey_id: $journey_id}})
                    CREATE (j)-[:{rel}]->(child:{child_label} $props)
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                    props=filtered,
                )
            logger.info(
                "graph_journey_store.create_journey_child",
                extra={
                    "operation": "graph_journey_store.create_journey_child",
                    "status": "success",
                    "child_label": child_label,
                    "journey_id": journey_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_journey_store.create_journey_child_error",
                extra={
                    "operation": "graph_journey_store.create_journey_child",
                    "status": "failure",
                    "child_label": child_label,
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    # ------------------------------------------------------------------
    # merge_on_session_end — promotes session fields to Journey properties
    # ------------------------------------------------------------------

    def merge_session_fields(
        self,
        user_id: str,
        journey_id: str,
        session_state: dict,
        merge_rules: list[dict],
    ) -> None:
        """
        Apply merge_on_session_end config rules:
        For each rule {session_field, target}, read the session field value
        and write it to the Journey node property specified in target.

        target format: "Journey.<property_name>"
        (targets pointing to child node types like "Role" are handled separately
        in create_journey_child and are skipped here)
        """
        start = time.time()
        try:
            updates: dict[str, Any] = {}
            for rule in merge_rules:
                session_field = rule.get("session_field", "")
                target = rule.get("target", "")
                if not session_field or not target:
                    continue
                # Only handle Journey.<prop> targets here
                if not target.startswith("Journey."):
                    continue
                prop_name = target[len("Journey."):]
                val = session_state.get(session_field)
                if val is not None and val != "" and val != [] and val != "[]":
                    updates[prop_name] = val

            if not updates:
                return

            with self._driver.session() as session:
                # Property key names cannot be parameterised in Cypher — interpolation
                # is intentional here. Keys come from domain.yaml merge_rules config
                # (not user input) and are validated as safe identifiers before use.
                for k in updates:
                    if not k.isidentifier():
                        raise ValueError(f"Invalid property key for Cypher interpolation: {k!r}")
                set_clauses = ", ".join(f"j.{k} = ${k}" for k in updates)
                session.run(
                    f"""
                    MATCH (u:User {{user_id: $user_id}})-[:HAS_JOURNEY_HISTORY]->
                          (jh:JourneyHistory)-[:JOURNEY]->(j:Journey {{journey_id: $journey_id}})
                    SET {set_clauses}
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                    **updates,
                )

            logger.info(
                "graph_journey_store.merge_session_fields",
                extra={
                    "operation": "graph_journey_store.merge_session_fields",
                    "status": "success",
                    "journey_id": journey_id,
                    "fields_merged": list(updates.keys()),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_journey_store.merge_session_fields_error",
                extra={
                    "operation": "graph_journey_store.merge_session_fields",
                    "status": "failure",
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
