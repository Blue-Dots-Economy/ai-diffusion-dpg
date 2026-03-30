"""
knowledge_engine/main.py

Startup entrypoint for the Knowledge Engine service.

Responsibilities:
- Load config from config/config.yaml
- Instantiate KnowledgeEngine (no LLM proxy needed — Language Norm and NLU
  now run in Agent Core; KE runs only Glossary, Static KB, Multimodal)
- Expose FastAPI endpoints: POST /retrieve, GET /health
- Start uvicorn HTTP server on configured host:port (default 8001)

Run:
    python main.py                       (from knowledge_engine/ directory)
    uvicorn main:app --reload            (dev hot-reload)

Environment:
    OPENAI_API_KEY — required only if embedding_provider=openai in config.yaml.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Optional

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Load .env before anything reads environment variables.
# Has no effect if .env does not exist (safe in production).
load_dotenv()

from src.engine import KnowledgeEngine
from src.models import RetrievalChunk

# ---------------------------------------------------------------------------
# Logging — structured output, INFO level default
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas for HTTP API
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str


class RetrieveRequest(BaseModel):
    session_id: str
    user_message: str
    profile: dict[str, Any] = {}
    session: dict[str, Any] = {}
    intent: str = "unknown"
    entities: dict[str, Any] = {}
    sentiment: str = "neutral"
    confidence: float = 0.0
    normalised_input: str = ""
    detected_language: str = ""


class RetrievalChunkSchema(BaseModel):
    text: str
    doc_type: str = ""
    source: str = ""
    always_include: bool = False


class RetrieveResponse(BaseModel):
    session_id: str
    chunks: list[RetrievalChunkSchema] = []


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values win. Dicts are merged recursively."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(ke: KnowledgeEngine, config: dict) -> FastAPI:
    """
    Create and return the FastAPI application with KnowledgeEngine bound to it.

    Args:
        ke:     Instantiated KnowledgeEngine.
        config: Full config dict (used for health endpoint metadata).
    """
    if ke is None:
        raise ValueError("ke must not be None")

    app = FastAPI(
        title="Knowledge Engine DPG",
        description="NLU, RAG, and prompt assembly for the AI Composition Framework.",
        version="0.1.0",
        docs_url="/docs",
    )
    app.state.ke = ke
    app.state.config = config

    # ----------------------------------------------------------------
    # POST /retrieve
    # ----------------------------------------------------------------

    @app.post("/retrieve", response_model=RetrieveResponse)
    def retrieve(request: RetrieveRequest) -> RetrieveResponse:
        """
        Run RAG retrieval for one conversation turn.

        Returns knowledge chunks only — prompt assembly is Agent Core's responsibility.
        Returns HTTP 422 if session_id is missing.
        Always returns RetrieveResponse — block failures are absorbed internally.
        """
        if not request.session_id:
            raise HTTPException(status_code=422, detail="session_id must not be empty")

        chunks = app.state.ke.retrieve(
            session_id=request.session_id,
            user_message=request.user_message,
            profile=request.profile,
            session=request.session,
            intent=request.intent,
            entities=request.entities,
            sentiment=request.sentiment,
            confidence=request.confidence,
            normalised_input=request.normalised_input,
            detected_language=request.detected_language,
        )

        logger.info(
            "ke_server.retrieve",
            extra={
                "operation": "ke_server.retrieve",
                "status": "success",
                "session_id": request.session_id,
                "chunk_count": len(chunks),
            },
        )

        return RetrieveResponse(
            session_id=request.session_id,
            chunks=[
                RetrievalChunkSchema(
                    text=c.text,
                    doc_type=c.doc_type,
                    source=c.source,
                    always_include=c.always_include,
                )
                for c in chunks
            ],
        )

    # ----------------------------------------------------------------
    # GET /health
    # ----------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Readiness probe. Confirms KE service is up."""
        return HealthResponse(status="ok")

    return app


# ---------------------------------------------------------------------------
# App construction — exposed at module level for uvicorn --reload
# ---------------------------------------------------------------------------


def _build_app():
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config("config/domain.yaml")
    config = _deep_merge(dpg_config, domain_config)

    knowledge_cfg = config.get("knowledge", {})
    if not knowledge_cfg:
        raise ValueError("Config missing required 'knowledge' section")

    # KE no longer needs an LLM proxy — Language Normalisation and NLU run in Agent Core.
    # llm=None is fine while multimodal block is disabled; pass an LLMWrapperBase
    # implementation here when multimodal is enabled.
    ke = KnowledgeEngine(config=config)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8001)

    app = create_app(ke, config)

    logger.info(
        "knowledge_engine.startup",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
        },
    )

    return app, host, port


app, _host, _port = _build_app()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=_host,
        port=_port,
        log_level="info",
    )
