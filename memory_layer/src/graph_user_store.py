"""
memory_layer/src/graph_user_store.py

GraphUserStore — manages User, UserProfile, and UserAttribute nodes in Neo4j.

Responsibilities:
  - Check if a user exists (find_user)
  - Create the full user graph structure on first visit (create_user_graph)
  - Read UserProfile declared fields + UserAttribute nodes (get_profile)
  - Upsert a declared profile field (upsert_profile_field)
  - Create/update an ad-hoc UserAttribute node (upsert_user_attribute)
  - DETACH DELETE a user and all subnodes (delete_user)

All queries are parameterised. The one exception is property key names in
_set_declared_field, which must be interpolated into the Cypher template because
Neo4j does not support parameterised property key names. The key is validated
with assert key.isidentifier() before use — call sites are config-driven and trusted.
All methods absorb exceptions and log — never raise.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)


class GraphUserStore:
    """
    Manages User and UserProfile subgraph in Neo4j.

    Args:
        driver:          Initialised neo4j.Driver instance.
        declared_fields: List of field names declared in domain.yaml
                         UserProfile.declared_fields. Used to decide
                         whether a write goes to a node property or
                         a UserAttribute child node.
    """

    def __init__(self, driver: Driver, declared_fields: list[str]) -> None:
        if driver is None:
            raise ValueError("driver must not be None")
        if declared_fields is None:
            raise ValueError("declared_fields must not be None")

        self._driver = driver
        self._declared_fields = set(declared_fields)

        logger.info(
            "graph_user_store.init",
            extra={
                "operation": "graph_user_store.init",
                "status": "success",
                "declared_field_count": len(declared_fields),
            },
        )

    # ------------------------------------------------------------------
    # User existence
    # ------------------------------------------------------------------

    def user_exists(self, user_id: str) -> bool:
        """Return True if a User node with this user_id exists in Neo4j."""
        start = time.time()
        try:
            with self._driver.session() as session:
                result = session.run(
                    "MATCH (u:User {user_id: $user_id}) RETURN count(u) AS cnt",
                    user_id=user_id,
                )
                record = result.single()
                exists = record["cnt"] > 0 if record else False
            logger.info(
                "graph_user_store.user_exists",
                extra={
                    "operation": "graph_user_store.user_exists",
                    "status": "success",
                    "exists": exists,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return exists
        except Exception as e:
            logger.error(
                "graph_user_store.user_exists_error",
                extra={
                    "operation": "graph_user_store.user_exists",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return False

    # ------------------------------------------------------------------
    # Graph creation
    # ------------------------------------------------------------------

    def create_user_graph(self, user_id: str) -> None:
        """
        Create the full user subgraph for a new user:
          (User)-[:HAS_PROFILE]->(UserProfile)
          (User)-[:HAS_JOURNEY_HISTORY]->(JourneyHistory)
          (User)-[:HAS_CONTEXT]->(ContextGraph)

        Uses MERGE on User to avoid duplicate nodes if called twice.
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (u:User {user_id: $user_id})
                    ON CREATE SET u.created_at = localdatetime()
                    WITH u
                    MERGE (u)-[:HAS_PROFILE]->(up:UserProfile {user_id: $user_id})
                    MERGE (u)-[:HAS_JOURNEY_HISTORY]->(jh:JourneyHistory {user_id: $user_id})
                    MERGE (u)-[:HAS_CONTEXT]->(cg:ContextGraph {user_id: $user_id})
                    """,
                    user_id=user_id,
                )
            logger.info(
                "graph_user_store.create_user_graph",
                extra={
                    "operation": "graph_user_store.create_user_graph",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_user_store.create_user_graph_error",
                extra={
                    "operation": "graph_user_store.create_user_graph",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    # ------------------------------------------------------------------
    # Profile read
    # ------------------------------------------------------------------

    def get_profile(self, user_id: str) -> dict:
        """
        Read UserProfile declared fields + all UserAttribute child nodes.

        Returns:
            {
              "<declared_field_1>": "<value>",
              ...
              "attributes": [
                {"key": "...", "value": "...", "raw": "...", "turn": "...", "journey_id": "..."},
                ...
              ]
            }
        Returns {} on any failure.
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                # Read declared fields from UserProfile node
                profile_result = session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_PROFILE]->(up:UserProfile)
                    RETURN properties(up) AS props
                    """,
                    user_id=user_id,
                )
                profile_record = profile_result.single()
                if not profile_record:
                    return {}

                props = dict(profile_record["props"])
                # Remove internal neo4j / structural keys
                props.pop("user_id", None)

                # Filter to only declared fields
                profile = {k: v for k, v in props.items() if k in self._declared_fields}

                # Read ad-hoc UserAttribute nodes
                attr_result = session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_PROFILE]->(up:UserProfile)
                          -[:HAS_ATTRIBUTE]->(ua:UserAttribute)
                    RETURN ua.key AS key, ua.value AS value, ua.raw AS raw,
                           ua.turn AS turn, ua.journey_id AS journey_id
                    """,
                    user_id=user_id,
                )
                attributes = []
                for record in attr_result:
                    attributes.append({
                        "key": record["key"],
                        "value": record["value"],
                        "raw": record.get("raw", ""),
                        "turn": record.get("turn", ""),
                        "journey_id": record.get("journey_id", ""),
                    })

                profile["attributes"] = attributes

            logger.info(
                "graph_user_store.get_profile",
                extra={
                    "operation": "graph_user_store.get_profile",
                    "status": "success",
                    "field_count": len(profile) - 1,  # exclude attributes key
                    "attribute_count": len(attributes),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return profile

        except Exception as e:
            logger.error(
                "graph_user_store.get_profile_error",
                extra={
                    "operation": "graph_user_store.get_profile",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {}

    # ------------------------------------------------------------------
    # Profile write
    # ------------------------------------------------------------------

    def upsert_profile_field(
        self,
        user_id: str,
        key: str,
        value: Any,
        raw: str = "",
        turn: str = "",
        journey_id: str = "",
    ) -> None:
        """
        Write a field to UserProfile (if declared) or UserAttribute (if ad-hoc).

        Declared field: SET property directly on UserProfile node.
        Ad-hoc field: MERGE UserAttribute node keyed by (user_id + key).
        """
        start = time.time()
        try:
            if key in self._declared_fields:
                self._set_declared_field(user_id, key, value)
            else:
                self._upsert_attribute(user_id, key, value, raw, turn, journey_id)

            logger.info(
                "graph_user_store.upsert_profile_field",
                extra={
                    "operation": "graph_user_store.upsert_profile_field",
                    "status": "success",
                    "key": key,
                    "is_declared": key in self._declared_fields,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_user_store.upsert_profile_field_error",
                extra={
                    "operation": "graph_user_store.upsert_profile_field",
                    "status": "failure",
                    "key": key,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    def _set_declared_field(self, user_id: str, key: str, value: Any) -> None:
        # Property key names cannot be parameterised in Cypher — interpolation is
        # intentional here. The key is validated to be a safe identifier before use.
        if not key.isidentifier():
            raise ValueError(f"Invalid property key for Cypher interpolation: {key!r}")
        with self._driver.session() as session:
            session.run(
                f"""
                MATCH (u:User {{user_id: $user_id}})-[:HAS_PROFILE]->(up:UserProfile)
                SET up.{key} = $value
                """,
                user_id=user_id,
                value=value,
            )

    def _upsert_attribute(
        self,
        user_id: str,
        key: str,
        value: Any,
        raw: str,
        turn: str,
        journey_id: str,
    ) -> None:
        with self._driver.session() as session:
            session.run(
                """
                MATCH (u:User {user_id: $user_id})-[:HAS_PROFILE]->(up:UserProfile)
                MERGE (up)-[:HAS_ATTRIBUTE]->(ua:UserAttribute {user_id: $user_id, key: $key})
                SET ua.value = $value, ua.raw = $raw, ua.turn = $turn, ua.journey_id = $journey_id
                """,
                user_id=user_id,
                key=key,
                value=str(value),
                raw=raw,
                turn=turn,
                journey_id=journey_id,
            )

    # ------------------------------------------------------------------
    # DPDP erasure
    # ------------------------------------------------------------------

    def delete_user(self, user_id: str) -> None:
        """
        DETACH DELETE the User node and all its connected subnodes.
        This cascades through UserProfile, JourneyHistory, ContextGraph,
        and all their descendants.
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                session.run(
                    "MATCH (u:User {user_id: $user_id}) DETACH DELETE u",
                    user_id=user_id,
                )
            logger.info(
                "graph_user_store.delete_user",
                extra={
                    "operation": "graph_user_store.delete_user",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_user_store.delete_user_error",
                extra={
                    "operation": "graph_user_store.delete_user",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
