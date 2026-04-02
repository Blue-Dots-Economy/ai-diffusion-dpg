"""
memory_layer/src/graph_context_store.py

GraphContextStore — manages Signal and ContextAttribute nodes in Neo4j.

Responsibilities:
  - Create a Signal node under ContextGraph (create_signal)
  - Create ContextAttribute child nodes for structured extraction (create_context_attribute)
  - Read Signal nodes for a given journey (get_signals_for_journey)

ContextGraph structure (per domain.yaml):
  (User)-[:HAS_CONTEXT]->(ContextGraph)
                               └──[:SIGNAL]->(Signal {type, turn, raw, journey_id})
                                                 └──[:HAS_ATTRIBUTE]->(ContextAttribute {
                                                                         key, value, raw,
                                                                         turn, journey_id
                                                                      })

All queries are parameterised. All methods absorb exceptions — never raise.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from neo4j import Driver

logger = logging.getLogger(__name__)


class GraphContextStore:
    """
    Manages ContextGraph subgraph in Neo4j.

    Args:
        driver: Initialised neo4j.Driver instance.
    """

    def __init__(self, driver: Driver) -> None:
        if driver is None:
            raise ValueError("driver must not be None")
        self._driver = driver

        logger.info(
            "graph_context_store.init",
            extra={
                "operation": "graph_context_store.init",
                "status": "success",
            },
        )

    # ------------------------------------------------------------------
    # Signal creation
    # ------------------------------------------------------------------

    def create_signal(
        self,
        user_id: str,
        journey_id: str,
        signal_type: str,
        turn: str,
        raw: str,
        attributes: list[dict] | None = None,
    ) -> None:
        """
        Create a Signal node under ContextGraph.

        Args:
            user_id:     User identifier.
            journey_id:  Current session/journey identifier.
            signal_type: Signal category (e.g. "objection", "emotion", "constraint").
            turn:        Turn identifier or turn number string.
            raw:         Raw user text that triggered this signal.
            attributes:  Optional list of structured extractions from this signal.
                         Each: {"key": str, "value": str, "raw": str}
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                result = session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_CONTEXT]->(cg:ContextGraph)
                    CREATE (cg)-[:SIGNAL]->(s:Signal {
                        type:       $signal_type,
                        turn:       $turn,
                        raw:        $raw,
                        journey_id: $journey_id
                    })
                    RETURN id(s) AS signal_node_id
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                    signal_type=signal_type,
                    turn=turn,
                    raw=raw,
                )
                record = result.single()

                # Create ContextAttribute children if provided
                if record and attributes:
                    signal_node_id = record["signal_node_id"]
                    for attr in attributes:
                        session.run(
                            """
                            MATCH (s:Signal) WHERE id(s) = $signal_node_id
                            CREATE (s)-[:HAS_ATTRIBUTE]->(ca:ContextAttribute {
                                key:        $key,
                                value:      $value,
                                raw:        $raw,
                                turn:       $turn,
                                journey_id: $journey_id
                            })
                            """,
                            signal_node_id=signal_node_id,
                            key=attr.get("key", ""),
                            value=str(attr.get("value", "")),
                            raw=attr.get("raw", raw),
                            turn=turn,
                            journey_id=journey_id,
                        )

            logger.info(
                "graph_context_store.create_signal",
                extra={
                    "operation": "graph_context_store.create_signal",
                    "status": "success",
                    "signal_type": signal_type,
                    "journey_id": journey_id,
                    "attribute_count": len(attributes) if attributes else 0,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "graph_context_store.create_signal_error",
                extra={
                    "operation": "graph_context_store.create_signal",
                    "status": "failure",
                    "signal_type": signal_type,
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    # ------------------------------------------------------------------
    # Signal read (for journey summary)
    # ------------------------------------------------------------------

    def get_signals_for_journey(self, user_id: str, journey_id: str) -> list[dict]:
        """
        Read all Signal nodes for a given journey.
        Returns list of signal dicts. Returns [] on any failure.
        """
        start = time.time()
        try:
            with self._driver.session() as session:
                result = session.run(
                    """
                    MATCH (u:User {user_id: $user_id})-[:HAS_CONTEXT]->(cg:ContextGraph)
                          -[:SIGNAL]->(s:Signal {journey_id: $journey_id})
                    RETURN s.type AS type, s.turn AS turn, s.raw AS raw
                    ORDER BY s.turn
                    """,
                    user_id=user_id,
                    journey_id=journey_id,
                )
                signals = [
                    {
                        "type": record["type"],
                        "turn": record["turn"],
                        "raw": record.get("raw", ""),
                    }
                    for record in result
                ]

            logger.info(
                "graph_context_store.get_signals_for_journey",
                extra={
                    "operation": "graph_context_store.get_signals_for_journey",
                    "status": "success",
                    "journey_id": journey_id,
                    "signal_count": len(signals),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return signals

        except Exception as e:
            logger.error(
                "graph_context_store.get_signals_for_journey_error",
                extra={
                    "operation": "graph_context_store.get_signals_for_journey",
                    "status": "failure",
                    "journey_id": journey_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []
