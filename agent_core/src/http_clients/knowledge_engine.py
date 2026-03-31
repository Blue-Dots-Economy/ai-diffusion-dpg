"""
agent_core/src/http_clients/knowledge_engine.py

HttpKnowledgeEngineClient — HTTP client for the Knowledge Engine service.

Implements KnowledgeEngineBase. Makes HTTP POST calls to the Knowledge Engine
FastAPI service (default: http://localhost:8001/retrieve).

Error handling:
- Timeout: returns empty list.
- HTTP 4xx/5xx: logs and returns empty list.
- Any other exception: logs and returns empty list.
The orchestrator handles an empty chunk list gracefully (no RAG context).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.models import RetrievalChunk

logger = logging.getLogger(__name__)


class HttpKnowledgeEngineClient(KnowledgeEngineBase):
    """
    HTTP client that calls the Knowledge Engine service at POST /retrieve.

    Implements the KnowledgeEngineBase interface contract:
        retrieve(...) -> list[RetrievalChunk]

    Args:
        config: Full config dict. Reads ke_client.endpoint and ke_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")

        ke_cfg = config.get("ke_client", {})
        self._endpoint: str = ke_cfg.get("endpoint", "http://localhost:8001/retrieve")
        self._timeout_s: float = ke_cfg.get("timeout_ms", 8000) / 1000

        logger.info(
            "ke_http_client.init",
            extra={
                "operation": "ke_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
                "timeout_s": self._timeout_s,
            },
        )

    def retrieve(
        self,
        session_id: str,
        user_message: str,
        profile: dict,
        session: dict,
        intent: str = "unknown",
        entities: Optional[dict[str, Any]] = None,
        sentiment: str = "neutral",
        confidence: float = 0.0,
        normalised_input: str = "",
        detected_language: str = "",
    ) -> list[RetrievalChunk]:
        """
        Call the Knowledge Engine service to retrieve RAG chunks.

        Returns list[RetrievalChunk] — may be empty if KE finds no relevant chunks.
        Returns [] on any failure — orchestrator handles empty chunk list gracefully.
        Never raises.
        """
        if not user_message:
            return []

        start = time.time()

        payload = {
            "session_id": session_id,
            "user_message": user_message,
            "profile": profile or {},
            "session": session or {},
            "intent": intent,
            "entities": entities or {},
            "sentiment": sentiment,
            "confidence": confidence,
            "normalised_input": normalised_input,
            "detected_language": detected_language,
        }

        try:
            response = httpx.post(
                self._endpoint,
                json=payload,
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()
            raw_chunks = data.get("chunks", [])
            if not isinstance(raw_chunks, list):
                logger.error(
                    "ke_http_client.malformed_response",
                    extra={
                        "operation": "ke_http_client.retrieve",
                        "status": "failure",
                        "session_id": session_id,
                        "error": f"'chunks' field is not a list: {type(raw_chunks)}",
                    },
                )
                return []

            chunks = [
                RetrievalChunk(
                    text=c.get("text", ""),
                    doc_type=c.get("doc_type", ""),
                    source=c.get("source", ""),
                    always_include=c.get("always_include", False),
                )
                for c in raw_chunks
                if c.get("text")
            ]

            logger.info(
                "ke_http_client.retrieve",
                extra={
                    "operation": "ke_http_client.retrieve",
                    "status": "success",
                    "session_id": session_id,
                    "chunk_count": len(chunks),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return chunks

        except httpx.TimeoutException as e:
            logger.error(
                "ke_http_client.timeout",
                extra={
                    "operation": "ke_http_client.retrieve",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

        except httpx.HTTPStatusError as e:
            logger.error(
                "ke_http_client.http_error",
                extra={
                    "operation": "ke_http_client.retrieve",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

        except Exception as e:
            logger.error(
                "ke_http_client.unexpected_error",
                extra={
                    "operation": "ke_http_client.retrieve",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []
