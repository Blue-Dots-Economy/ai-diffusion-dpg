"""
knowledge_engine/main.py

Startup entrypoint for the Knowledge Engine service.

Responsibilities:
- Load config from config/config.yaml (or CONFIG_FOLDER/knowledge_engine.yaml if set)
- Instantiate KnowledgeEngine (no LLM proxy needed — Language Norm and NLU
  now run in Agent Core; KE runs only Glossary, Static KB, Multimodal)
- Expose FastAPI endpoints: POST /retrieve, GET /health
- Start uvicorn HTTP server on configured host:port (default 8001)

Run:
    python main.py                       (from knowledge_engine/ directory)
    uvicorn main:app --reload            (dev hot-reload)

Environment:
    OPENAI_API_KEY — required only if embedding_provider=openai in config.yaml.
    CONFIG_FOLDER  — optional path to a folder containing knowledge_engine.yaml.
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import asyncio as _asyncio
import tempfile
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

# Load .env.local first (developer overrides), then .env (shared defaults).
# Neither file is required; missing files are silently ignored.
_env_local = Path(__file__).parent.parent / ".env.local"
_env_local_warn = _env_local.exists() and not load_dotenv(_env_local)
load_dotenv()

from dpg_telemetry import init_otel
from src.engine import KnowledgeEngine
from src.models import RetrievalChunk
from src.db.ingestion_db import IngestionDB
from src.upload_router import create_upload_router, run_queue_worker

# ---------------------------------------------------------------------------
# Logging — structured output, INFO level default
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

if _env_local_warn:
    logger.warning(
        "config.env_local_not_loaded",
        extra={
            "operation": "load_dotenv",
            "status": "skipped",
            "error": f"{_env_local} exists but no variables were loaded — check for syntax errors.",
        },
    )


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


def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback. An empty string CONFIG_FOLDER
    is treated the same as unset.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.

    Raises:
        ValueError: If CONFIG_FOLDER is set to a path that is not a directory.
        FileNotFoundError: If CONFIG_FOLDER is set but the resolved service
            YAML does not exist.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        config_dir = Path(config_folder)
        if not config_dir.is_dir():
            raise ValueError(
                f"CONFIG_FOLDER='{config_folder}' is not a directory. "
                f"Set CONFIG_FOLDER to the folder containing service YAML files, "
                f"not a file path. Check .env.local."
            )
        resolved = config_dir / f"{service}.yaml"
        if not resolved.exists():
            raise FileNotFoundError(
                f"CONFIG_FOLDER='{config_folder}' is set but "
                f"'{resolved}' does not exist. "
                f"Check CONFIG_FOLDER in .env.local."
            )
        return resolved
    return Path("config/domain.yaml")  # relative to cwd, consistent with config/dpg.yaml loading


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
    FastAPIInstrumentor.instrument_app(app)
    app.state.ke = ke
    app.state.config = config

    # ------------------------------------------------------------------
    # Upload router — KB document ingestion API
    # ------------------------------------------------------------------

    _REACH_TO_KE_API_KEY = os.environ.get("REACH_TO_KE_API_KEY", "")
    _AZURE_CONFIGURED = bool(
        os.environ.get("AZURE_STORAGE_ACCOUNT")
        and os.environ.get("AZURE_STORAGE_KEY")
        and os.environ.get("AZURE_CONTAINER_NAME")
    )
    _KB_DATA_DIR = os.environ.get("KB_DATA_DIR", "/data/kb")
    _DB_PATH = Path(_KB_DATA_DIR) / "ke_metadata.db"
    _DEVKIT_CALLBACK_URL = os.environ.get("KE_DEVKIT_CALLBACK_URL", "")
    _KE_TO_DEVKIT_API_KEY = os.environ.get("KE_TO_DEVKIT_API_KEY", "")
    _MAX_QUEUE_SIZE = 20

    # Fall back to a temporary directory if the configured KB_DATA_DIR is not
    # writable (e.g. the /data PVC is absent in local dev / test environments).
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        _tmp_dir = Path(tempfile.mkdtemp(prefix="ke_data_"))
        logger.warning(
            "ke.startup.kb_data_dir_fallback",
            extra={
                "operation": "ke.startup",
                "status": "skipped",
                "error": f"{_KB_DATA_DIR} is not writable; using {_tmp_dir}",
            },
        )
        _KB_DATA_DIR = str(_tmp_dir)
        _DB_PATH = _tmp_dir / "ke_metadata.db"

    ingest_db = IngestionDB(_DB_PATH)
    ingest_queue: _asyncio.Queue = _asyncio.Queue()

    upload_router = create_upload_router(
        db=ingest_db,
        ingest_queue=ingest_queue,
        reach_to_ke_api_key=_REACH_TO_KE_API_KEY,
        azure_configured=_AZURE_CONFIGURED,
        max_queue_size=_MAX_QUEUE_SIZE,
        devkit_callback_url=_DEVKIT_CALLBACK_URL or None,
        ke_to_devkit_api_key=_KE_TO_DEVKIT_API_KEY or None,
        ke_config=config,
        static_kb_block=ke.get_static_kb_block(),
    )
    app.include_router(upload_router)
    app.state.ingest_db = ingest_db
    app.state.ingest_queue = ingest_queue

    @app.on_event("startup")
    async def _start_queue_worker():
        """Start the singleton async queue worker on app startup."""
        static_kb = ke.get_static_kb_block()
        _asyncio.create_task(
            run_queue_worker(
                db=ingest_db,
                ingest_queue=ingest_queue,
                ke_config=config,
                static_kb_block=static_kb,
                devkit_callback_url=_DEVKIT_CALLBACK_URL or None,
                ke_to_devkit_api_key=_KE_TO_DEVKIT_API_KEY or None,
                kb_data_dir=_KB_DATA_DIR,
            )
        )
        logger.info(
            "ke.queue_worker.started",
            extra={"operation": "ke.startup", "status": "success"},
        )

    # ----------------------------------------------------------------
    # POST /retrieve
    # ----------------------------------------------------------------

    _tracer = otel_trace.get_tracer(__name__)

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

        with _tracer.start_as_current_span("ke.rag_retrieve") as span:
            span.set_attribute("session_id", request.session_id)
            span.set_attribute("intent", request.intent or "")
            start = time.time()
            try:
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
                        "latency_ms": int((time.time() - start) * 1000),
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
            except Exception as e:
                span.record_exception(e)
                logger.error(
                    "ke_server.retrieve_error",
                    extra={
                        "operation": "ke_server.retrieve",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                raise

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
    domain_config = _load_config(str(_domain_config_path("knowledge_engine")))
    config = _deep_merge(dpg_config, domain_config)

    # Strict schema check on the full merged config — unknown keys, wrong
    # types, or out-of-range values at any depth fail here at startup.
    from src.schema.config import MergedConfig
    MergedConfig.validate_full(config)

    init_otel(service_name="knowledge_engine", config=config)

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
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
