"""
dev-kit/dev_kit/agent/app.py

FastAPI application for the DPG conversation agent.

Serves the conversation API and the React SPA (built frontend output
mounted at agent/static/). Manages an in-memory registry of
ConversationEngine instances keyed by project slug.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import time
import yaml
import zipfile
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator
from dev_kit.agent.checkpoints import list_checkpoints, restore_checkpoint
from dev_kit.agent.conversation import ConversationEngine
from dev_kit.agent.errors import ConversationError
from dev_kit.agent.renderer import load_block_from_file, render_all
from dev_kit.schema import validate_partial

load_dotenv(Path(__file__).parent.parent.parent / ".env.local")
load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs"
_STATIC_DIR = Path(__file__).parent / "static"
_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not _api_key:
    raise EnvironmentError(
        "ANTHROPIC_API_KEY environment variable is not set. "
        "Set it before starting the server."
    )
_anthropic_client = anthropic.AsyncAnthropic(api_key=_api_key)
_engines: dict[str, ConversationEngine] = {}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="DPG Configuration Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str
    description: str


class ChatRequest(BaseModel):
    message: str


class UpdateConfigRequest(BaseModel):
    content: str  # Raw YAML string from the editor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _get_project_path(slug: str) -> Path:
    return CONFIGS_DIR / slug


def _load_project_meta(slug: str) -> dict:
    """Load project.json for the given slug.

    Args:
        slug: Project slug.

    Returns:
        Parsed project metadata dict.

    Raises:
        HTTPException: 404 if project not found, 500 if metadata is corrupt.
    """
    path = _get_project_path(slug) / "_meta" / "project.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "project_meta_corrupt",
            extra={"operation": "_load_project_meta", "status": "failure", "error": str(exc), "latency_ms": 0},
        )
        raise HTTPException(status_code=500, detail="Project metadata is corrupt") from exc


def _get_engine(slug: str) -> ConversationEngine:
    if slug not in _engines:
        project_path = _get_project_path(slug)
        if not project_path.exists():
            raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
        _engines[slug] = ConversationEngine(project_path, _anthropic_client)
    return _engines[slug]


# ---------------------------------------------------------------------------
# Project routes
# ---------------------------------------------------------------------------


@app.post("/api/projects")
def create_project(body: CreateProjectRequest) -> dict:
    """Create a new project and initialise its directory structure."""
    slug = _slugify(body.name)
    project_path = _get_project_path(slug)
    project_path.mkdir(parents=True, exist_ok=True)
    meta_dir = project_path / "_meta"
    meta_dir.mkdir(exist_ok=True)
    meta = {
        "slug": slug,
        "name": body.name,
        "description": body.description,
        "current_phase": "overview",
        "phases_completed": [],
    }
    (meta_dir / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    # Initialise empty config files
    acc = ConfigAccumulator()
    render_all(project_path, acc)
    _engines[slug] = ConversationEngine(project_path, _anthropic_client)
    return meta


@app.get("/api/projects")
def list_projects() -> list[dict]:
    """List all projects, skipping any with unreadable metadata."""
    projects = []
    if not CONFIGS_DIR.exists():
        return projects
    for project_path in CONFIGS_DIR.iterdir():
        if not project_path.is_dir():
            continue
        meta_file = project_path / "_meta" / "project.json"
        if meta_file.exists():
            try:
                projects.append(json.loads(meta_file.read_text()))
            except json.JSONDecodeError as exc:
                logger.error(
                    "project_meta_corrupt",
                    extra={
                        "operation": "list_projects",
                        "status": "failure",
                        "error": str(exc),
                        "latency_ms": 0,
                        "path": str(meta_file),
                    },
                )
    return projects


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict:
    """Get project metadata and config statuses."""
    meta = _load_project_meta(slug)
    engine = _get_engine(slug)
    meta["config_statuses"] = {block: engine.accumulator.get_status(block).value for block in BLOCKS}
    return meta


@app.delete("/api/projects/{slug}")
def delete_project(slug: str) -> dict:
    """Delete a project and all its files."""
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    shutil.rmtree(project_path)
    _engines.pop(slug, None)
    return {"deleted": slug}


# ---------------------------------------------------------------------------
# Chat routes
# ---------------------------------------------------------------------------


@app.post("/api/projects/{slug}/chat")
async def chat(slug: str, body: ChatRequest) -> dict:
    """Send a user message and receive the agent response."""
    engine = _get_engine(slug)
    start = time.time()
    try:
        result = await engine.chat(body.message)
        logger.info(
            "chat_turn",
            extra={
                "operation": "app.chat",
                "status": "success",
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
        )
        return result
    except ConversationError as exc:
        logger.error(
            "chat_turn_failed",
            extra={
                "operation": "app.chat",
                "status": "failure",
                "error": str(exc),
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
        )
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
    except Exception as exc:
        logger.exception(
            "chat_turn_unexpected",
            extra={
                "operation": "app.chat",
                "status": "failure",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
        )
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc


@app.get("/api/projects/{slug}/history")
def get_history(slug: str) -> list[dict]:
    """Return the conversation history for the current phase."""
    engine = _get_engine(slug)
    result = []
    for msg in engine._history:
        content = msg.get("content", "")
        if isinstance(content, str):
            result.append({"role": msg["role"], "content": content})
    return result


# ---------------------------------------------------------------------------
# Checkpoint routes
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/checkpoints")
def get_checkpoints(slug: str) -> list[dict]:
    """List all saved checkpoints for a project."""
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return list_checkpoints(project_path)


@app.post("/api/projects/{slug}/checkpoints/{phase}/restore")
def restore_checkpoint_route(slug: str, phase: str) -> dict:
    """Restore the project to a previous checkpoint."""
    project_path = _get_project_path(slug)
    try:
        restored_acc, summary = restore_checkpoint(project_path, phase)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Checkpoint '{phase}' not found")
    engine = _get_engine(slug)
    engine.accumulator = restored_acc
    engine._tool_handler._acc = restored_acc
    engine._history = []
    engine._state["phase"] = phase.split("_", 1)[-1] if "_" in phase else phase
    render_all(project_path, restored_acc)
    engine._save_accumulator()
    return {"restored": phase, "summary": summary}


@app.get("/api/projects/{slug}/checkpoints/{phase}/preview")
def preview_checkpoint(slug: str, phase: str) -> list[dict]:
    """Return what configs would look like after restoring a checkpoint, without restoring.

    Loads the checkpoint accumulator from disk and returns a list of
    ``{block, status, content}`` dicts — the same shape as
    ``GET /api/projects/{slug}/configs`` — so the frontend can diff the
    current state against the checkpoint before committing to a restore.

    Args:
        slug: Project slug.
        phase: Checkpoint phase directory name, e.g. ``01_overview``.

    Returns:
        List of dicts with ``block``, ``status``, and ``content`` keys.

    Raises:
        HTTPException: 404 if the checkpoint directory does not exist.
    """
    project_path = _get_project_path(slug)
    cp_dir = project_path / "_meta" / "checkpoints" / phase
    if not cp_dir.exists():
        raise HTTPException(status_code=404, detail=f"Checkpoint '{phase}' not found")

    acc_file = cp_dir / "accumulator.json"
    try:
        acc = ConfigAccumulator.from_dict(json.loads(acc_file.read_text()))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error(
            "checkpoint_preview_corrupt",
            extra={"operation": "preview_checkpoint", "status": "failure", "error": str(exc), "latency_ms": 0},
        )
        raise HTTPException(status_code=404, detail=f"Checkpoint '{phase}' accumulator unreadable") from exc

    result = []
    for block in BLOCKS:
        data = acc.get_block(block)
        content = yaml.dump(data, allow_unicode=True, default_flow_style=False) if data else ""
        result.append({
            "block": block,
            "status": acc.get_status(block).value,
            "content": content,
        })
    return result


# ---------------------------------------------------------------------------
# Config routes
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/configs")
def get_configs(slug: str) -> list[dict]:
    """Return all 7 config files with their status."""
    engine = _get_engine(slug)
    result = []
    project_path = _get_project_path(slug)
    for block in BLOCKS:
        config_file = project_path / f"{block}.yaml"
        content = config_file.read_text() if config_file.exists() else ""
        result.append({
            "block": block,
            "status": engine.accumulator.get_status(block).value,
            "content": content,
        })
    return result


@app.get("/api/projects/{slug}/configs/export")
def export_configs(slug: str):
    """Return all config YAML files for a project as a ZIP archive.

    Args:
        slug: Project identifier.

    Returns:
        StreamingResponse containing a ZIP file with one YAML file per block.

    Raises:
        HTTPException: 404 if the project does not exist.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for block in BLOCKS:
            config_file = project_path / f"{block}.yaml"
            content = config_file.read_text() if config_file.exists() else f"# {block}.yaml — not yet configured\n"
            zf.writestr(f"{block}.yaml", content)
    buf.seek(0)

    def _iter_and_close():
        try:
            yield from buf
        finally:
            buf.close()

    return StreamingResponse(
        _iter_and_close(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={slug}-configs.zip"},
    )


@app.get("/api/projects/{slug}/configs/{block}")
def get_config(slug: str, block: str) -> dict:
    """Return a single block config."""
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    project_path = _get_project_path(slug)
    config_file = project_path / f"{block}.yaml"
    content = config_file.read_text() if config_file.exists() else ""
    engine = _get_engine(slug)
    return {"block": block, "status": engine.accumulator.get_status(block).value, "content": content}


@app.put("/api/projects/{slug}/configs/{block}")
def update_config_file(slug: str, block: str, body: UpdateConfigRequest) -> dict:
    """Manually update a config file and reverse-sync the accumulator.

    Parses YAML before writing to prevent corrupting the stored file.
    If schema validation fails, sets the block status to STALE.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    from dev_kit.agent.accumulator import ConfigStatus, DRAFT_BLOCKS
    # Parse before writing — reject invalid YAML with 400
    try:
        parsed = yaml.safe_load(body.content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    project_path = _get_project_path(slug)
    config_file = project_path / f"{block}.yaml"
    config_file.write_text(body.content)

    engine = _get_engine(slug)
    engine.accumulator._data[block] = parsed
    errors = validate_partial(block, parsed)
    if errors:
        engine.accumulator.set_status(block, ConfigStatus.STALE)
    elif block in DRAFT_BLOCKS:
        engine.accumulator.set_status(block, ConfigStatus.DRAFT)
    else:
        engine.accumulator.set_status(block, ConfigStatus.COMPLETE)
    engine._save_accumulator()
    return {"block": block, "status": engine.accumulator.get_status(block).value, "validation_errors": errors}


@app.post("/api/projects/{slug}/configs/validate")
def validate_all_configs(slug: str) -> dict[str, Any]:
    """Run partial validation on all 7 configs and return results."""
    engine = _get_engine(slug)
    results = {}
    for block in BLOCKS:
        data = engine.accumulator.get_block(block)
        errors = validate_partial(block, data)
        results[block] = {"valid": len(errors) == 0, "errors": errors}
    return results


# ---------------------------------------------------------------------------
# Workflow graph route
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/workflow/graph")
def get_workflow_graph(slug: str) -> dict:
    """Return the subagent workflow as nodes and edges for the frontend graph."""
    engine = _get_engine(slug)
    return engine.accumulator.get_workflow_graph()


# ---------------------------------------------------------------------------
# Schema routes
# ---------------------------------------------------------------------------


@app.get("/api/schemas/{block}")
def get_schema_descriptions(block: str) -> dict:
    """Parse inline comments from a block's YAML template and return key→description map.

    Template lines are expected to follow the pattern::

        key: ""   # description text

    If the template file does not exist (e.g. an unrecognised block name),
    an empty descriptions dict is returned instead of a 404.

    Args:
        block: DPG block name, e.g. ``reach_layer``.

    Returns:
        Dict with ``block`` and ``descriptions`` keys. ``descriptions`` maps
        field names to their inline comment strings.
    """
    template_file = _SCHEMAS_DIR / f"{block}.yaml"
    descriptions: dict[str, str] = {}
    if template_file.exists():
        pattern = re.compile(r'\s+(\w+):\s+"[^"]*"\s*#\s*(.+)')
        for line in template_file.read_text().splitlines():
            match = pattern.match(line)
            if match:
                key, description = match.group(1), match.group(2).strip()
                descriptions[key] = description
    return {"block": block, "descriptions": descriptions}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"error": "Frontend not built. Run: cd frontend && npm run build"}
