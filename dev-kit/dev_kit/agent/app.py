"""
dev-kit/dev_kit/agent/app.py

FastAPI application for the DPG conversation agent.

Serves the conversation API and the React SPA (built frontend output
mounted at agent/static/). Manages an in-memory registry of
ConversationEngine instances keyed by project slug.
"""
from __future__ import annotations

import asyncio
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
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dev_kit.agent.accumulator import BLOCKS, ConfigAccumulator
from dev_kit.agent.auth import verify_api_key as _verify_api_key
from dev_kit.agent.checkpoints import list_checkpoints, restore_checkpoint
from dev_kit.agent.conversation import ConversationEngine
from dev_kit.agent.crypto import decrypt_secrets_dict, get_public_key_spki_b64
from dev_kit.agent.errors import ConversationError
from dev_kit.agent.renderer import load_block_from_file, render_all
from dev_kit.config.loader import load_devkit_config as _load_devkit_config
from dev_kit.schema import validate_partial

load_dotenv(Path(__file__).parent.parent.parent / ".env.local")
load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs"
DPG_DIR = Path(__file__).parent.parent.parent / "dpg"
_STATIC_DIR = Path(__file__).parent / "static"
_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DOCKER_AUTOMATION = Path("/app/automation")
_AUTOMATION = _DOCKER_AUTOMATION if _DOCKER_AUTOMATION.exists() else _REPO_ROOT / "automation"
HELM_BASE = _AUTOMATION / "helm"
COMPOSE_FILE = _AUTOMATION / "docker" / "docker-compose.dev.yml"

_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not _api_key:
    raise EnvironmentError(
        "ANTHROPIC_API_KEY environment variable is not set. "
        "Set it before starting the server."
    )
_anthropic_client = anthropic.AsyncAnthropic(api_key=_api_key)
_engines: dict[str, ConversationEngine] = {}

logger = logging.getLogger(__name__)


def _rewrite_compose_bind_paths_to_host(services: dict) -> None:
    """Rewrite relative bind-mount sources to absolute host paths in-place.

    When dev_kit runs inside a container and drives ``docker compose``
    against the host daemon (via the bind-mounted /var/run/docker.sock),
    the daemon resolves bind sources against the *host* filesystem — not
    the dev_kit container's. Compose paths like ``../../dev-kit/dpg/foo.yaml``
    resolve to ``/app/dev-kit/dpg/foo.yaml`` from inside the dev_kit
    container; the host has no ``/app/dev-kit/...``, so the daemon
    auto-creates an empty directory there and the file→file mount fails.

    ``HOST_REPO_ROOT`` (passed in via the dev_kit service env) tells us
    where the repo lives on the host so we can rewrite every relative
    bind source to a path the daemon will resolve correctly. No-op when
    the env var is unset (dev_kit running directly on the host).

    Args:
        services: The ``services`` mapping from a parsed compose document.
            Mutated in place.
    """
    host_repo_root = os.environ.get("HOST_REPO_ROOT")
    if not host_repo_root:
        return
    project_dir_host = os.path.join(host_repo_root.rstrip("/"), "automation", "docker")
    for svc in services.values():
        vols = svc.get("volumes")
        if not vols:
            continue
        rewritten = []
        for vol in vols:
            if isinstance(vol, str) and ":" in vol and (vol.startswith("./") or vol.startswith("../")):
                source, sep, rest = vol.partition(":")
                absolute = os.path.normpath(os.path.join(project_dir_host, source))
                rewritten.append(f"{absolute}{sep}{rest}")
            else:
                rewritten.append(vol)
        svc["volumes"] = rewritten

# Load dev-kit config once at startup
_DEVKIT_CONFIG = _load_devkit_config()
_KE_TO_DEVKIT_API_KEY = os.environ.get("KE_TO_DEVKIT_API_KEY", "")
_DEVKIT_TO_REACH_API_KEY = os.environ.get("DEVKIT_TO_REACH_API_KEY", "")
_REACH_LAYER_URL = os.environ.get("REACH_LAYER_URL", "http://localhost:8005")

# Upload-chain API keys — generated once per dev-kit process if not pre-set via env.
# Written to secrets at deploy time so all services share the same keys.
import secrets as _secrets_module
_UPLOAD_CHAIN_KEYS: dict[str, str] = {
    "devkit_to_reach_api_key": _DEVKIT_TO_REACH_API_KEY or "",
    "reach_to_ke_api_key": os.environ.get("REACH_TO_KE_API_KEY", ""),
    "ke_to_devkit_api_key": _KE_TO_DEVKIT_API_KEY or "",
}


def _rehydrate_upload_chain_from_running_containers() -> bool:
    """Recover upload-chain API keys by inspecting a running reach_layer_web.

    Upload-chain keys are generated once during ``executeDeploy`` and stored
    only in this process's globals. After a dev_kit restart (or for a
    teammate who's auto-unlocked straight to Ingest without re-running
    deploy), the globals are empty / regenerated random — but the deployed
    reach_layer_web container still holds the *original* key in its env, so
    the proxied X-API-Key mismatches and the upstream returns 401.

    On demand we shell out to ``docker ps`` (already wired via the host
    docker socket) to find any container labelled
    ``com.docker.compose.service=reach_layer_web`` and read
    DEVKIT_TO_REACH_API_KEY / KE_TO_DEVKIT_API_KEY out of its env. The
    first match wins (typical setup runs one project at a time).

    Returns:
        True when at least one of the two keys was repopulated, False
        otherwise — used by the ingest proxy to decide whether to retry.
    """
    global _DEVKIT_TO_REACH_API_KEY, _KE_TO_DEVKIT_API_KEY
    import subprocess
    try:
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Labels}}"],
            capture_output=True, text=True, timeout=5,
        )
        if ps.returncode != 0:
            return False
        for line in ps.stdout.splitlines():
            if "\t" not in line:
                continue
            name, labels = line.split("\t", 1)
            if "com.docker.compose.service=reach_layer_web" not in labels:
                continue
            envs = subprocess.run(
                [
                    "docker", "inspect", "--format",
                    "{{range .Config.Env}}{{println .}}{{end}}", name,
                ],
                capture_output=True, text=True, timeout=5,
            )
            if envs.returncode != 0:
                continue
            recovered = False
            for env_line in envs.stdout.splitlines():
                if "=" not in env_line:
                    continue
                k, v = env_line.split("=", 1)
                if not v:
                    continue
                if k == "DEVKIT_TO_REACH_API_KEY":
                    _DEVKIT_TO_REACH_API_KEY = v
                    _UPLOAD_CHAIN_KEYS["devkit_to_reach_api_key"] = v
                    recovered = True
                elif k == "KE_TO_DEVKIT_API_KEY":
                    _KE_TO_DEVKIT_API_KEY = v
                    _UPLOAD_CHAIN_KEYS["ke_to_devkit_api_key"] = v
                    recovered = True
                elif k == "REACH_TO_KE_API_KEY":
                    _UPLOAD_CHAIN_KEYS["reach_to_ke_api_key"] = v
                    recovered = True
            if recovered:
                logger.info(
                    "devkit.upload_chain_rehydrated",
                    extra={
                        "operation": "rehydrate_upload_chain",
                        "status": "success",
                        "container": name,
                    },
                )
                return True
        return False
    except Exception as exc:
        logger.warning(
            "devkit.upload_chain_rehydrate_failed",
            extra={
                "operation": "rehydrate_upload_chain",
                "status": "failure",
                "error": str(exc),
            },
        )
        return False


def _ensure_upload_chain_keys_for_running_project() -> bool:
    """Repair an existing deploy that has empty upload-chain keys.

    When ``executeDeploy`` has never run in this dev_kit session AND the
    running ``reach_layer_web`` container was deployed earlier with empty
    DEVKIT_TO_REACH_API_KEY / KE_TO_DEVKIT_API_KEY / REACH_TO_KE_API_KEY
    env values (the previous-bug case the user just hit), both ends
    "match" as empty strings and reach_layer_web's auth check rejects
    the empty header — ingest fails with 401. ``_rehydrate_*`` only
    repopulates dev_kit's globals from whatever the container has, so
    if the container's keys are also empty, rehydrate is a no-op.

    This helper closes the loop:
    1. Find the most recent rendered compose file for any running
       ``dpg-<slug>-reach_layer_web-1`` container.
    2. Read the env on that container.
    3. If any of the three upload-chain envs are empty, generate fresh
       keys, set dev_kit globals, and run
       ``docker compose -p dpg-<slug> -f <file> up -d --force-recreate
       reach_layer_web knowledge_engine`` with the keys exported into
       the subprocess env so compose's ``${VAR:-}`` substitution picks
       them up. ``run_compose_up`` is bypassed because we don't want to
       go through the secrets/_DEVKIT_CONFIG path here — this is a
       targeted recovery, not a full deploy.

    Returns:
        True when keys were either already present or successfully
        regenerated and the affected services recreated. False on any
        unexpected error (caller falls back to current behaviour).
    """
    global _DEVKIT_TO_REACH_API_KEY, _KE_TO_DEVKIT_API_KEY
    import subprocess
    try:
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Labels}}"],
            capture_output=True, text=True, timeout=5,
        )
        if ps.returncode != 0:
            return False
        for line in ps.stdout.splitlines():
            if "\t" not in line:
                continue
            name, labels = line.split("\t", 1)
            if "com.docker.compose.service=reach_layer_web" not in labels:
                continue
            project = ""
            for kv in labels.split(","):
                if kv.startswith("com.docker.compose.project="):
                    project = kv.split("=", 1)[1]
                    break
            if not project:
                continue

            envs = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{range .Config.Env}}{{println .}}{{end}}", name],
                capture_output=True, text=True, timeout=5,
            )
            if envs.returncode != 0:
                continue
            current: dict[str, str] = {}
            for env_line in envs.stdout.splitlines():
                if "=" not in env_line:
                    continue
                k, v = env_line.split("=", 1)
                if k in ("DEVKIT_TO_REACH_API_KEY", "KE_TO_DEVKIT_API_KEY", "REACH_TO_KE_API_KEY"):
                    current[k] = v

            all_present = all(current.get(k) for k in (
                "DEVKIT_TO_REACH_API_KEY", "KE_TO_DEVKIT_API_KEY", "REACH_TO_KE_API_KEY",
            ))
            if all_present:
                # Container already has full keys — just sync our globals.
                _DEVKIT_TO_REACH_API_KEY = current["DEVKIT_TO_REACH_API_KEY"]
                _KE_TO_DEVKIT_API_KEY = current["KE_TO_DEVKIT_API_KEY"]
                _UPLOAD_CHAIN_KEYS["devkit_to_reach_api_key"] = current["DEVKIT_TO_REACH_API_KEY"]
                _UPLOAD_CHAIN_KEYS["ke_to_devkit_api_key"] = current["KE_TO_DEVKIT_API_KEY"]
                _UPLOAD_CHAIN_KEYS["reach_to_ke_api_key"] = current["REACH_TO_KE_API_KEY"]
                return True

            # At least one env is empty — generate fresh keys and recreate.
            new_keys = {
                "DEVKIT_TO_REACH_API_KEY": _secrets_module.token_urlsafe(32),
                "KE_TO_DEVKIT_API_KEY": _secrets_module.token_urlsafe(32),
                "REACH_TO_KE_API_KEY": _secrets_module.token_urlsafe(32),
            }
            _DEVKIT_TO_REACH_API_KEY = new_keys["DEVKIT_TO_REACH_API_KEY"]
            _KE_TO_DEVKIT_API_KEY = new_keys["KE_TO_DEVKIT_API_KEY"]
            _UPLOAD_CHAIN_KEYS["devkit_to_reach_api_key"] = new_keys["DEVKIT_TO_REACH_API_KEY"]
            _UPLOAD_CHAIN_KEYS["ke_to_devkit_api_key"] = new_keys["KE_TO_DEVKIT_API_KEY"]
            _UPLOAD_CHAIN_KEYS["reach_to_ke_api_key"] = new_keys["REACH_TO_KE_API_KEY"]

            # Find the rendered compose for this project (the temp file
            # _run_docker_deploy wrote into /app/automation/docker/).
            slug = project[len("dpg-"):] if project.startswith("dpg-") else project
            search_dir = COMPOSE_FILE.parent
            candidates = sorted(
                search_dir.glob(f"dpg-{slug}-*.yml"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                logger.warning(
                    "devkit.upload_chain_recreate_no_compose",
                    extra={
                        "operation": "ensure_upload_chain",
                        "status": "skipped",
                        "project": project,
                    },
                )
                return False
            compose_path = str(candidates[0])

            recreate_env = {**os.environ, **new_keys}
            recreate = subprocess.run(
                ["docker", "compose", "-p", project, "-f", compose_path,
                 "up", "-d", "--force-recreate",
                 "reach_layer_web", "knowledge_engine"],
                capture_output=True, text=True, timeout=180, env=recreate_env,
            )
            if recreate.returncode != 0:
                logger.error(
                    "devkit.upload_chain_recreate_failed",
                    extra={
                        "operation": "ensure_upload_chain",
                        "status": "failure",
                        "project": project,
                        "error": recreate.stderr[:500],
                    },
                )
                return False

            logger.info(
                "devkit.upload_chain_regenerated",
                extra={
                    "operation": "ensure_upload_chain",
                    "status": "success",
                    "project": project,
                    "services_recreated": "reach_layer_web,knowledge_engine",
                },
            )
            return True
        return False
    except Exception as exc:
        logger.warning(
            "devkit.upload_chain_ensure_failed",
            extra={
                "operation": "ensure_upload_chain",
                "status": "failure",
                "error": str(exc),
            },
        )
        return False

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
        "current_phase": "tier",
        "phases_completed": [],
        "agent_type": "",
        "phase_decisions": {},
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

    # Azure Blob Storage — expose intent flag only; all details collected at deploy time
    meta["azure_storage"] = {
        "needed": engine.accumulator.is_azure_needed(),
    }

    # Required secrets — derived from tool auth configuration
    meta["required_secrets"] = engine.accumulator.get_required_secrets()

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
    engine._state["phase"] = phase.split("_", 1)[-1] if "_" in phase else phase
    engine._history = engine._load_history_from_checkpoints()
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


@app.post("/api/projects/{slug}/configs/reload")
def reload_configs(slug: str) -> dict[str, Any]:
    """Evict the cached engine so the next request reloads all configs from disk.

    Useful after the operator hand-edits a YAML file outside the UI (e.g. in
    their editor or via git pull). The engine is recreated on the next request,
    picking up the new on-disk state.

    Args:
        slug: Project slug.

    Returns:
        Dict confirming the engine was evicted.
    """
    _engines.pop(slug, None)
    return {"reloaded": True, "slug": slug}


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


@app.post("/api/projects/{slug}/deploy/validate")
def pre_deploy_validate(slug: str) -> dict[str, Any]:
    """Run full merged-config validation and cross-block invariant checks.

    Merges DPG defaults + domain config for each of the 7 blocks, runs the
    full Pydantic model (not partial), then checks cross-block invariants
    such as tool-name integrity and intent-filter coverage.

    Args:
        slug: Project slug.

    Returns:
        Dict with ``valid`` bool, ``block_errors`` per-block Pydantic errors,
        and ``invariant_errors`` list of cross-block rule violations.
    """
    from dev_kit.loader import _load_and_merge
    from dev_kit.schema import (
        AgentCoreConfig, KnowledgeEngineConfig, TrustLayerConfig,
        MemoryLayerConfig, ObservabilityLayerConfig, ActionGatewayConfig,
        ReachLayerConfig,
    )
    from pydantic import ValidationError as _VE

    _MODELS = {
        "agent_core": AgentCoreConfig,
        "knowledge_engine": KnowledgeEngineConfig,
        "trust_layer": TrustLayerConfig,
        "memory_layer": MemoryLayerConfig,
        "observability_layer": ObservabilityLayerConfig,
        "action_gateway": ActionGatewayConfig,
        "reach_layer": ReachLayerConfig,
    }

    block_errors: dict[str, list[str]] = {}
    merged: dict[str, dict] = {}

    # 1. Full Pydantic validation per block using merged (dpg + domain) config.
    for block, model_cls in _MODELS.items():
        try:
            data = _load_and_merge(slug, block)
            merged[block] = data
            model_cls.model_validate(data)
            block_errors[block] = []
        except _VE as exc:
            block_errors[block] = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            ]
        except FileNotFoundError:
            merged[block] = {}
            block_errors[block] = []
        except Exception as exc:
            block_errors[block] = [str(exc)]

    # 2. Cross-block invariant checks.
    invariant_errors: list[str] = []
    ac = merged.get("agent_core", {})
    ke = merged.get("knowledge_engine", {})
    rl = merged.get("reach_layer", {})

    # Build connector name set from agent_core
    connectors = ac.get("connectors", {})
    declared_connectors: set[str] = set()
    for category in ("read", "write", "identity", "internal"):
        for c in connectors.get(category) or []:
            if isinstance(c, dict) and c.get("name"):
                declared_connectors.add(c["name"])
    internal_connectors: set[str] = {
        c["name"]
        for c in (connectors.get("internal") or [])
        if isinstance(c, dict) and c.get("name")
    }

    workflow = ac.get("agent_workflow", {})
    global_tools: list[str] = workflow.get("global_tools") or []
    global_intents: set[str] = set(workflow.get("global_intents") or [])

    # Check 1: tool names in global_tools exist in connectors
    for tool in global_tools:
        if tool not in declared_connectors and "__" not in tool:
            invariant_errors.append(
                f"agent_core.agent_workflow.global_tools: '{tool}' is not declared "
                f"in any connectors.* list. Declared connectors: {sorted(declared_connectors)}"
            )

    # Check 2 & 3: per-subagent tool names + global_intents overlap
    declared_subagent_ids: set[str] = {
        sa["id"]
        for sa in (workflow.get("subagents") or [])
        if isinstance(sa, dict) and sa.get("id")
    }
    all_subagent_intents: set[str] = set()
    for sa in workflow.get("subagents") or []:
        if not isinstance(sa, dict):
            continue
        sa_id = sa.get("id", "?")
        for tool in sa.get("tools") or []:
            if tool not in declared_connectors and "__" not in tool:
                invariant_errors.append(
                    f"agent_core.agent_workflow.subagents[{sa_id}].tools: '{tool}' is not "
                    f"declared in any connectors.* list. Declared connectors: {sorted(declared_connectors)}"
                )
        for intent in sa.get("valid_intents") or []:
            all_subagent_intents.add(intent)

    overlap = global_intents & all_subagent_intents
    if overlap:
        invariant_errors.append(
            f"agent_core: intents {sorted(overlap)} appear in both global_intents and a "
            f"subagent's valid_intents. Agent Core crashes at startup if there is any overlap."
        )

    # Check 4: knowledge_retrieval must be in connectors.internal, not connectors.read
    all_tool_names = set(global_tools)
    for sa in workflow.get("subagents") or []:
        if isinstance(sa, dict):
            all_tool_names.update(sa.get("tools") or [])
    if "knowledge_retrieval" in all_tool_names and "knowledge_retrieval" not in internal_connectors:
        read_names = {c["name"] for c in (connectors.get("read") or []) if isinstance(c, dict)}
        if "knowledge_retrieval" in read_names:
            invariant_errors.append(
                "agent_core: 'knowledge_retrieval' is in connectors.read but must be in "
                "connectors.internal (it routes to Knowledge Engine, not Action Gateway). "
                "Move the connector to the connectors.internal list and add a 'route: knowledge_engine' field."
            )
        else:
            invariant_errors.append(
                "agent_core: 'knowledge_retrieval' is referenced in tools but not declared "
                "in connectors.internal. Add it under connectors.internal with route: knowledge_engine."
            )

    # Check 5: intent_filters keys must be in NLU intents
    nlu_intents: set[str] = set(
        ac.get("preprocessing", {}).get("nlu_processor", {}).get("intents") or []
    )
    intent_filters: dict = (
        ke.get("knowledge", {})
        .get("blocks", {})
        .get("static_knowledge_base", {})
        .get("intent_filters") or {}
    )
    for intent_key in intent_filters:
        if intent_key not in nlu_intents:
            invariant_errors.append(
                f"knowledge_engine.intent_filters key '{intent_key}' is not declared in "
                f"agent_core.preprocessing.nlu_processor.intents. Queries for this intent "
                f"will bypass the filter. Add '{intent_key}' to the NLU intents list."
            )

    # Check 6: voice selected → reach_layer.channels.voice configured
    selected_channels = _get_engine(slug).accumulator.get_reach_channel_selection_or_default()
    if "voice" in selected_channels:
        voice_cfg = (rl.get("reach_layer", {}) or {}).get("channels", {}).get("voice")
        if not voice_cfg or not isinstance(voice_cfg, dict):
            invariant_errors.append(
                "reach_layer.channels.voice is not configured but voice is in selected_channels. "
                "Set reach_layer.channels.voice with raya.voice_id, raya.stt_language, and raya.tts_language."
            )
        else:
            raya = voice_cfg.get("raya") or {}
            for field in ("voice_id", "stt_language", "tts_language"):
                if not raya.get(field):
                    invariant_errors.append(
                        f"reach_layer.channels.voice.raya.{field} is empty but voice is in selected_channels."
                    )

    # Check 7: selected_channels[x] → agent_core.channels.<x> must exist in raw YAML.
    # DPG agent_core defaults have no channels.* entries; if the domain config also
    # omits channels.web the runtime raises ValueError: Unsupported channel.
    ac_channels = ac.get("channels", {}) or {}
    for ch in selected_channels:
        if ch not in ac_channels:
            invariant_errors.append(
                f"agent_core.channels.{ch} is missing but '{ch}' is in selected_channels. "
                f"Agent Core raises ValueError: Unsupported channel at startup. "
                f"Add a channels.{ch} block with system_prompt_suffix and turn_assembler settings."
            )

    # Check 8: selected_channels[x] → reach_layer.channels.<x> must be non-null.
    # DPG reach_layer defaults have all three channels, but domain config can nullify one.
    rl_channels = (rl.get("reach_layer", {}) or {}).get("channels", {}) or {}
    for ch in selected_channels:
        if rl_channels.get(ch) is None:
            invariant_errors.append(
                f"reach_layer.channels.{ch} is null/missing but '{ch}' is in selected_channels. "
                f"The reach layer service will fail to start. Add a reach_layer.channels.{ch} block."
            )

    # Check 9: opening_phrase must be non-empty for every non-terminal subagent.
    # Terminal subagents end the conversation and never need an opening phrase.
    for sa in (workflow.get("subagents") or []):
        if not isinstance(sa, dict):
            continue
        sa_id = sa.get("id", "?")
        if not sa.get("is_terminal") and not (sa.get("opening_phrase") or "").strip():
            invariant_errors.append(
                f"agent_core.agent_workflow.subagents[{sa_id}].opening_phrase is empty. "
                f"Every non-terminal subagent must have an opening_phrase — it is emitted "
                f"on the first turn the session enters this subagent."
            )

    # Check 10: default_fallback_subagent_id must match a declared subagent id.
    fallback_id = (workflow.get("default_fallback_subagent_id") or "").strip()
    if fallback_id and fallback_id not in declared_subagent_ids:
        invariant_errors.append(
            f"agent_core.agent_workflow.default_fallback_subagent_id: '{fallback_id}' is not "
            f"declared in subagents (declared: {sorted(declared_subagent_ids)}). "
            f"Agent Core will raise KeyError when the fallback is triggered."
        )

    # Check 11: every routing[*].next_subagent_id must match a declared subagent id.
    for rule in (workflow.get("global_routing") or []):
        if not isinstance(rule, dict):
            continue
        next_id = (rule.get("next_subagent_id") or "").strip()
        if next_id and next_id not in declared_subagent_ids:
            invariant_errors.append(
                f"agent_core.agent_workflow.global_routing: next_subagent_id '{next_id}' "
                f"is not declared in subagents (declared: {sorted(declared_subagent_ids)})."
            )
    for sa in (workflow.get("subagents") or []):
        if not isinstance(sa, dict):
            continue
        sa_id = sa.get("id", "?")
        for rule in (sa.get("routing") or []):
            if not isinstance(rule, dict):
                continue
            next_id = (rule.get("next_subagent_id") or "").strip()
            if next_id and next_id not in declared_subagent_ids:
                invariant_errors.append(
                    f"agent_core.agent_workflow.subagents[{sa_id}].routing: "
                    f"next_subagent_id '{next_id}' is not declared in subagents "
                    f"(declared: {sorted(declared_subagent_ids)})."
                )

    # Check 12: workflow top-level required fields must be non-empty.
    for field in ("workflow_id", "agent_system_prompt"):
        if not (workflow.get(field) or "").strip():
            invariant_errors.append(
                f"agent_core.agent_workflow.{field} is empty. "
                f"This is a required field — Agent Core fails Pydantic validation at startup."
            )

    # Check 13: dignity_check.questions must be non-empty if dignity_check is enabled.
    # dignity_check is a top-level key in the merged trust_layer YAML.
    tl = merged.get("trust_layer", {})
    dignity = tl.get("dignity_check") or {}
    if dignity.get("enabled") and not dignity.get("questions"):
        invariant_errors.append(
            "trust_layer.dignity_check.enabled is true but questions is empty. "
            "The dignity check will always pass with no questions — add the 5 canonical "
            "questions: ['Does this blame the user?', 'Does it over-promise?', "
            "'Does it push urgency?', 'Does it reduce their agency?', "
            "'Does it sound like a script instead of a human call?']"
        )

    all_valid = all(len(errs) == 0 for errs in block_errors.values()) and not invariant_errors
    return {
        "valid": all_valid,
        "block_errors": block_errors,
        "invariant_errors": invariant_errors,
    }


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
# Deploy endpoints
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/deploy/dpg-values")
async def get_dpg_values(slug: str) -> list:
    """Return all 7 DPG framework YAML files.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).

    Returns:
        List of dicts with ``block`` and ``content`` keys for each DPG block.
    """
    results = []
    for block in BLOCKS:
        path = DPG_DIR / f"{block}.yaml"
        content = path.read_text() if path.exists() else ""
        results.append({"block": block, "content": content})
    return results


@app.put("/api/projects/{slug}/deploy/dpg-values/{block}")
async def update_dpg_value(slug: str, block: str, body: dict) -> dict:
    """Update a DPG framework YAML file.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        block: DPG block name to update.
        body: Dict with ``content`` key containing the YAML string.

    Returns:
        Dict with ``status: ok`` on success.

    Raises:
        HTTPException: 400 if block name is not recognised.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    path = DPG_DIR / f"{block}.yaml"
    path.write_text(body["content"])
    return {"status": "ok"}


@app.get("/api/projects/{slug}/deploy/dependencies")
async def get_dependencies(slug: str) -> dict:
    """Return all infrastructure service configs.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).

    Returns:
        Dict mapping each service name to its current config YAML and defaults.
    """
    from dev_kit.agent.deployer.dependencies import get_defaults, get_service_config

    defaults = get_defaults()
    result = {}
    for name in defaults:
        result[name] = {"config": get_service_config(name), "defaults": defaults[name]}
    return result


@app.put("/api/projects/{slug}/deploy/dependencies/{service}")
async def update_dependency(slug: str, service: str, body: dict) -> dict:
    """Update an infrastructure service config.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        service: Infrastructure service name to update.
        body: Dict with ``content`` key containing the YAML override string.

    Returns:
        Dict with ``status: ok`` on success.

    Raises:
        HTTPException: 400 if service name is unknown or YAML is invalid.
    """
    from dev_kit.agent.deployer.dependencies import update_service_config

    try:
        update_service_config(service, body["content"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.get("/api/projects/{slug}/deploy/resource-presets")
async def get_resource_presets(slug: str) -> dict:
    """Return the 3 resource preset definitions.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).

    Returns:
        Dict with low/medium/high tiers, each mapping block names to resource specs.
    """
    from dev_kit.agent.deployer.presets import PRESETS

    return PRESETS


@app.post("/api/projects/{slug}/deploy/resource-presets/{tier}")
async def apply_resource_preset_endpoint(slug: str, tier: str) -> dict:
    """Apply a resource preset to all 7 DPG layers.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        tier: Preset tier name — one of ``low``, ``medium``, or ``high``.

    Returns:
        Dict mapping each DPG block to its requests/limits resource spec.

    Raises:
        HTTPException: 400 if tier is not a known preset name.
    """
    from dev_kit.agent.deployer.presets import apply_preset

    try:
        return apply_preset(tier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{slug}/deploy/validate-kubeconfig")
async def validate_kubeconfig_endpoint(slug: str, body: dict) -> dict:
    """Validate a kubeconfig and return cluster info.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        body: Dict with ``content`` key containing the kubeconfig YAML string.

    Returns:
        Dict with cluster validation details from the kubeconfig.

    Raises:
        HTTPException: 400 if the kubeconfig is invalid or cannot be parsed.
    """
    from dev_kit.agent.deployer.kubeconfig import validate_kubeconfig

    try:
        return await validate_kubeconfig(body["content"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _apply_resources_to_compose(content: str, resources: dict) -> str:
    """Apply resource presets to a docker-compose YAML string.

    Args:
        content: The raw docker-compose YAML string.
        resources: Dict mapping block names to {limits: {cpu, memory}, requests: {cpu, memory}}.

    Returns:
        Updated YAML string with deploy.resources.limits applied per service.
    """
    import yaml as _yaml

    try:
        compose = _yaml.safe_load(content)
    except _yaml.YAMLError:
        return content

    if not compose or "services" not in compose:
        return content

    for block_name, res in resources.items():
        compose_name = block_name
        if block_name == "reach_layer":
            compose_name = "reach_layer_web"
        if compose_name not in compose["services"]:
            continue
        svc = compose["services"][compose_name]
        limits = res.get("limits", {})
        if limits:
            # Convert K8s CPU (e.g. "500m") to Docker cpus (e.g. "0.5")
            cpu_str = limits.get("cpu", "100m")
            if cpu_str.endswith("m"):
                cpus = str(round(int(cpu_str[:-1]) / 1000, 2))
            else:
                cpus = cpu_str
            # Convert K8s memory (e.g. "512Mi") to Docker (e.g. "512M")
            mem_str = limits.get("memory", "512Mi")
            memory = mem_str.replace("Mi", "M").replace("Gi", "G")
            svc.setdefault("deploy", {}).setdefault("resources", {})["limits"] = {
                "cpus": cpus,
                "memory": memory,
            }

    return _yaml.dump(compose, default_flow_style=False, sort_keys=False)


@app.put("/api/projects/{slug}/deploy/compose-file")
async def update_compose_file(slug: str, body: dict) -> dict:
    """Write updated docker-compose content back to the compose file.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        body: Dict with ``content`` key containing the full YAML string.

    Returns:
        Dict with ``status: ok`` on success.
    """
    COMPOSE_FILE.write_text(body["content"])
    return {"status": "ok"}


@app.get("/api/deploy/public-key")
def get_deploy_public_key() -> dict:
    """Return the server RSA public key for browser-side secret encryption.

    The browser fetches this once and uses ``SubtleCrypto.importKey('spki', ...)``
    to import it for RSA-OAEP encryption of each secret value before sending.

    Returns:
        Dict with ``public_key`` — base64 DER/SPKI of the server's RSA-4096
        public key.
    """
    return {"public_key": get_public_key_spki_b64()}


@app.post("/api/projects/{slug}/deploy/preview")
async def get_deploy_preview(slug: str, body: dict) -> dict:
    """Read existing docker-compose file or render helm template preview.

    Args:
        slug: Project slug used to scope domain config paths.
        body: Dict with optional keys: ``target`` (``docker`` or ``kubernetes``),
            ``resources``, ``secrets``, and ``infra_configs``.

    Returns:
        Dict with ``target`` and ``preview`` keys. Preview contains rendered
        deployment manifests keyed by filename.
    """
    _CHANNEL_SERVICE: dict[str, str] = {
        "web": "reach_layer_web",
        "voice": "reach_layer_voice",
        "cli": "reach_layer_cli",
    }
    target = body.get("target", "docker")
    if target == "docker":
        # Decrypt secrets here too so preview can show what will be written to disk.
        encrypted_secrets = body.get("encrypted_secrets", {})
        secrets = decrypt_secrets_dict(encrypted_secrets) if encrypted_secrets else body.get("secrets", {})

        raw = COMPOSE_FILE.read_text() if COMPOSE_FILE.exists() else "# docker-compose.dev.yml not found"
        content = raw.replace("${DOMAIN:-kkb}", slug).replace("${DOMAIN}", slug)
        resources = body.get("resources", {})
        if resources:
            content = _apply_resources_to_compose(content, resources)

        # Apply channel selection: remove reach services for unselected channels so
        # the preview matches exactly what _run_docker_deploy will deploy.
        # web is always deployed regardless of selection so the UI and ingest proxy
        # remain accessible; voice and cli are optional.
        selected_channels = _get_engine(slug).accumulator.get_reach_channel_selection_or_default()
        effective_channels = set(selected_channels) | {"web"}
        services_to_remove = {
            svc_name
            for channel, svc_name in _CHANNEL_SERVICE.items()
            if channel not in effective_channels
        }
        # ngrok depends_on reach_layer_voice (tunnels port 8006 for Vobiz webhooks).
        # Remove it if voice is not selected.
        if "voice" not in effective_channels:
            services_to_remove.add("ngrok")
        services_to_remove.add("dev_kit")

        import yaml as _yaml
        compose_doc = _yaml.safe_load(content)
        tool_secrets = secrets.get("tool_secrets", {})
        services = compose_doc.get("services", {})
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            # Force pull_policy=missing on every service: avoids re-pulling
            # already-cached images on every redeploy and prevents the
            # `toomanyrequests: unauthenticated pull rate limit` failure on
            # demo VMs where dev_kit's CLI isn't logged into Docker Hub.
            # Operators can refresh manually with `docker pull <image>`.
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                ag_env = svc.setdefault("environment", [])
                for env_var in tool_secrets:
                    if tool_secrets[env_var]:
                        ag_env.append(f"{env_var}=<set at deploy time>")
        # Mirror the deploy-time rewrite so the preview matches what the
        # actual deploy will write (and run) on the host.
        _rewrite_compose_bind_paths_to_host(services)
        content = _yaml.dump(compose_doc, default_flow_style=False, sort_keys=False)

        return {"target": target, "preview": {"docker-compose.yml": content}}

    # Kubernetes — render all 14 charts via helm template
    from dev_kit.agent.deployer.helm import build_template_command, run_helm_command

    _helm_base = HELM_BASE
    encrypted_secrets = body.get("encrypted_secrets", {})
    secrets = decrypt_secrets_dict(encrypted_secrets) if encrypted_secrets else body.get("secrets", {})
    resources = body.get("resources", {})
    preview: dict[str, str] = {}

    from dev_kit.agent.deployer.dependencies import SERVICE_CHART_MAP, HELM_INFRA_DIR

    # Infra charts — pass edited values.yaml and secrets
    for svc_name in ["redis", "memgraph", "otel_collector", "jaeger", "prometheus", "loki", "grafana"]:
        chart_dir = svc_name.replace("_", "-")
        chart_path = str(_helm_base / "infra" / chart_dir)
        set_values: dict[str, str] = {}

        # Use the edited values.yaml from the infra Helm chart
        infra_chart_dir = SERVICE_CHART_MAP.get(svc_name, chart_dir)
        infra_values = HELM_INFRA_DIR / infra_chart_dir / "values.yaml"
        values_files: list[str] = []
        if infra_values.exists():
            values_files.append(str(infra_values))

        # Inject secrets into infra services
        if svc_name == "redis" and secrets.get("redis_password"):
            set_values["password"] = secrets["redis_password"]
        elif svc_name == "memgraph" and secrets.get("memgraph_password"):
            set_values["password"] = secrets["memgraph_password"]
        elif svc_name == "grafana" and secrets.get("grafana_admin_password"):
            set_values["adminPassword"] = secrets["grafana_admin_password"]

        cmd = build_template_command(
            chart_path, svc_name.replace("_", "-"),
            set_values=set_values or None,
            values_files=values_files or None,
        )
        result = await run_helm_command(cmd)
        preview[svc_name] = result["stdout"] if result["success"] else f"# Error: {result['stderr']}"

    # DPG charts — inject dpgConfig, domainConfig, secrets, and resources
    for block_name in BLOCKS:
        chart_dir = block_name.replace("_", "-")
        chart_path = str(_helm_base / "dpg" / chart_dir)
        set_values: dict[str, str] = {}
        set_files: dict[str, str] = {}

        dpg_file = DPG_DIR / f"{block_name}.yaml"
        domain_file = CONFIGS_DIR / slug / f"{block_name}.yaml"
        if dpg_file.exists():
            set_files["dpgConfig"] = str(dpg_file)
        if domain_file.exists():
            set_files["domainConfig"] = str(domain_file)
        if secrets.get("anthropic_api_key"):
            set_values["anthropicApiKey"] = secrets["anthropic_api_key"]

        # Inject infra secrets into DPG blocks that connect to them
        if block_name == "memory_layer":
            if secrets.get("memgraph_password"):
                set_values["memgraph.password"] = secrets["memgraph_password"]
            if secrets.get("redis_password"):
                set_values["redis.url"] = f"redis://:{secrets['redis_password']}@redis:6379/0"

        # Inject domain-specific tool API keys as extraSecrets for action-gateway
        if block_name == "action_gateway":
            for env_var, secret_value in secrets.get("tool_secrets", {}).items():
                if secret_value:
                    set_values[f"extraSecrets.{env_var}"] = secret_value

        # Inject Azure creds and upload chain auth into knowledge-engine
        if block_name == "knowledge_engine":
            if secrets.get("azure_storage_account"):
                set_values["azure.storageAccount"] = secrets["azure_storage_account"]
            if secrets.get("azure_storage_key"):
                set_values["azure.storageKey"] = secrets["azure_storage_key"]
            if secrets.get("azure_container_name"):
                set_values["azure.containerName"] = secrets["azure_container_name"]
            if secrets.get("reach_to_ke_api_key"):
                set_values["uploadAuth.reachToKeApiKey"] = secrets["reach_to_ke_api_key"]
            if secrets.get("ke_to_devkit_api_key"):
                set_values["uploadAuth.keToDevkitApiKey"] = secrets["ke_to_devkit_api_key"]
            if secrets.get("ke_devkit_callback_url"):
                set_values["uploadAuth.devkitCallbackUrl"] = secrets["ke_devkit_callback_url"]

        # Inject upload chain auth into reach-layer
        if block_name == "reach_layer":
            if secrets.get("devkit_to_reach_api_key"):
                set_values["uploadAuth.devkitToReachApiKey"] = secrets["devkit_to_reach_api_key"]
            if secrets.get("reach_to_ke_api_key"):
                set_values["uploadAuth.reachToKeApiKey"] = secrets["reach_to_ke_api_key"]
            if secrets.get("ke_internal_url"):
                set_values["uploadAuth.keInternalUrl"] = secrets["ke_internal_url"]

        block_res = resources.get(block_name, {})
        limits = block_res.get("limits", {})
        requests = block_res.get("requests", {})
        if limits.get("cpu"):
            set_values["resources.limits.cpu"] = limits["cpu"]
        if limits.get("memory"):
            set_values["resources.limits.memory"] = limits["memory"]
        if requests.get("cpu"):
            set_values["resources.requests.cpu"] = requests["cpu"]
        if requests.get("memory"):
            set_values["resources.requests.memory"] = requests["memory"]

        cmd = build_template_command(chart_path, chart_dir, set_values=set_values or None, set_files=set_files or None)
        result = await run_helm_command(cmd)
        preview[block_name] = result["stdout"] if result["success"] else f"# Error: {result['stderr']}"

    return {"target": target, "preview": preview}


@app.post("/api/projects/{slug}/deploy/execute")
async def execute_deploy(slug: str, body: dict) -> dict:
    """Trigger deployment of all 14 services.

    Starts an async background task that deploys services phase-by-phase.
    Returns immediately with ``status: started``. Poll ``/deploy/status``
    to track progress.

    Args:
        slug: Project slug identifying the deployment target.
        body: Dict with ``target``, ``secrets``, ``resources``, and
            optionally ``kubeconfig`` (for kubernetes target).

    Returns:
        Dict with ``status: started`` and the resolved target name.
    """
    from fastapi import HTTPException as _HTTPException

    # Gate: reject deploys with config errors so broken configs never reach containers.
    validation = pre_deploy_validate(slug)
    if not validation["valid"]:
        block_msgs = [
            f"[{block}] {err}"
            for block, errs in (validation.get("block_errors") or {}).items()
            for err in errs
        ]
        all_errors = block_msgs + (validation.get("invariant_errors") or [])
        raise _HTTPException(
            status_code=422,
            detail={
                "error": "Config validation failed — fix errors before deploying.",
                "errors": all_errors,
            },
        )

    import tempfile
    from dev_kit.agent.deployer.state import start_deploy

    target = body.get("target", "docker")
    state = start_deploy(slug, target)
    encrypted_secrets = body.get("encrypted_secrets", {})
    secrets = decrypt_secrets_dict(encrypted_secrets) if encrypted_secrets else body.get("secrets", {})
    resources = body.get("resources", {})

    # Auto-generate upload-chain API keys if not supplied by the caller.
    # These are internal service-to-service credentials — never entered by the user.
    # We generate them once per deploy and inject them into every service that needs them.
    # We also update the in-process globals so the devkit ingest proxy uses the same keys.
    global _DEVKIT_TO_REACH_API_KEY, _KE_TO_DEVKIT_API_KEY
    for key_name in ("devkit_to_reach_api_key", "reach_to_ke_api_key", "ke_to_devkit_api_key"):
        if not secrets.get(key_name):
            # Reuse the process-level key if already generated (survives redeploy in same session)
            existing = _UPLOAD_CHAIN_KEYS.get(key_name, "")
            if not existing:
                existing = _secrets_module.token_urlsafe(32)
                _UPLOAD_CHAIN_KEYS[key_name] = existing
            secrets[key_name] = existing

    # Sync the ingest-proxy globals so POST /api/ingest/submit authenticates correctly.
    _DEVKIT_TO_REACH_API_KEY = secrets["devkit_to_reach_api_key"]
    _KE_TO_DEVKIT_API_KEY = secrets["ke_to_devkit_api_key"]

    # For Kubernetes deployments, reach-layer is exposed as NodePort 30805 on the cluster node.
    # Update the global so the ingest proxy routes to the right host:port after deploy.
    global _REACH_LAYER_URL
    if target == "kubernetes":
        node_ip = body.get("node_ip", "")
        if not node_ip:
            # Default to the Colima VM address; operator can override via node_ip in request.
            node_ip = "192.168.5.1"
        _REACH_LAYER_URL = f"http://{node_ip}:30805"
    elif target == "docker":
        # When dev_kit runs in its own container against the host docker
        # daemon, the deployed reach_layer_web lives in a different compose
        # project / network — `localhost:8005` from inside dev_kit hits its
        # own loopback, not the host's published 8005. Resolve to
        # host.docker.internal (mapped via extra_hosts: host-gateway on the
        # dev_kit service in compose) so the ingest proxy reaches the
        # published port on the host. When dev_kit runs directly on the
        # host (no /.dockerenv), localhost is correct.
        _in_container = os.path.exists("/.dockerenv")
        _REACH_LAYER_URL = (
            "http://host.docker.internal:8005" if _in_container
            else "http://localhost:8005"
        )

    # Auto-fill ke_internal_url based on target if not already provided.
    if not secrets.get("ke_internal_url"):
        if target == "kubernetes":
            namespace = body.get("namespace", "dpg")
            secrets["ke_internal_url"] = f"http://knowledge-engine.{namespace}.svc.cluster.local:8001"
        else:
            # Docker Compose: KE is reachable on its service name within the compose network.
            secrets["ke_internal_url"] = "http://knowledge_engine:8001"

    # Auto-fill ke_devkit_callback_url so KE can hit dev_kit when an ingestion
    # job completes (ingested or failed). If left empty, KE skips the callback
    # and the frontend polls for status instead.
    #
    # IMPORTANT: pass only the base URL — KE appends `/api/ingest/callback`
    # itself. Including the path here causes KE to POST to
    # `/api/ingest/callback/api/ingest/callback`, which dev_kit answers
    # with 405 Method Not Allowed.
    #
    # Resolution order:
    #   1. Explicit value entered in the wizard.
    #   2. devkit.external_url from devkit.yaml (production / shared deploy).
    #   3. For docker target: http://host.docker.internal:8080 — KE and
    #      dev_kit live in different compose projects, so the deployed KE
    #      service uses the host-gateway alias (added to KE in compose) to
    #      reach dev_kit's published 8080 on the host.
    if not secrets.get("ke_devkit_callback_url"):
        devkit_ext = _DEVKIT_CONFIG.external_url
        if devkit_ext:
            secrets["ke_devkit_callback_url"] = devkit_ext.rstrip("/")
        elif target == "docker":
            secrets["ke_devkit_callback_url"] = "http://host.docker.internal:8080"

    # Mark all services as queued initially
    all_services = [
        "redis", "memgraph", "otel_collector", "jaeger", "prometheus", "loki", "grafana",
        "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
        "action_gateway", "reach_layer", "observability_layer",
    ]
    for svc in all_services:
        state.set_service(svc, "queued")

    if target == "docker":
        selected_channels = _get_engine(slug).accumulator.get_reach_channel_selection_or_default()
        asyncio.create_task(_run_docker_deploy(slug, state, secrets, resources, selected_channels))
    else:
        kubeconfig_content = body.get("kubeconfig", "")
        namespace = body.get("namespace", "dpg")
        asyncio.create_task(_run_k8s_deploy(slug, state, secrets, resources, kubeconfig_content, namespace))

    return {"status": "started", "target": target}


async def _run_docker_deploy(
    slug: str,
    state,
    secrets: dict,
    resources: dict,
    selected_channels: list[str] | None = None,
) -> None:
    """Background task: apply resources, resolve domain, and run docker compose up."""
    import tempfile
    from dev_kit.agent.deployer.compose import run_compose_up
    from dev_kit.agent.deployer.helm import DEPLOY_PHASES

    # Map channel name → compose service name. CLI is already profile-gated in the
    # compose file so it never starts with `docker compose up`; web/voice need explicit
    # removal when not selected.
    _CHANNEL_SERVICE: dict[str, str] = {
        "web": "reach_layer_web",
        "voice": "reach_layer_voice",
        "cli": "reach_layer_cli",
    }
    if selected_channels is None:
        selected_channels = ["web", "voice", "cli"]

    try:
        # Read compose file, apply domain and resources
        raw = COMPOSE_FILE.read_text()
        content = raw.replace("${DOMAIN:-kkb}", slug).replace("${DOMAIN}", slug)
        if resources:
            content = _apply_resources_to_compose(content, resources)

        # Patch the parsed YAML in one pass:
        #   - strip container_name (avoids conflicts across deployments)
        #   - remove reach_layer_* services for unselected channels
        #   - inject connector tool secrets directly into action_gateway environment
        import yaml as _yaml
        compose_doc = _yaml.safe_load(content)
        tool_secrets = secrets.get("tool_secrets", {})

        # Determine which reach_layer services to remove.
        # web is always deployed regardless of selected_channels so the UI and
        # ingest proxy remain accessible; voice and cli are optional.
        effective_channels = set(selected_channels) | {"web"}
        services_to_remove = {
            svc_name
            for channel, svc_name in _CHANNEL_SERVICE.items()
            if channel not in effective_channels
        }
        # ngrok depends_on reach_layer_voice (tunnels port 8006 for Vobiz webhooks).
        # Remove it if voice is not selected.
        if "voice" not in effective_channels:
            services_to_remove.add("ngrok")
        # When deploying from the local dev-kit, exclude the Docker dev-kit service
        # to avoid port 8080 conflicts. The local process handles the UI + ingest proxy.
        services_to_remove.add("dev_kit")

        services = compose_doc.get("services", {})
        for svc_name in list(services.keys()):
            if svc_name in services_to_remove:
                del services[svc_name]
                continue
            svc = services[svc_name]
            svc.pop("container_name", None)
            # Force pull_policy=missing on every service: avoids re-pulling
            # already-cached images on every redeploy and prevents the
            # `toomanyrequests: unauthenticated pull rate limit` failure on
            # demo VMs where dev_kit's CLI isn't logged into Docker Hub.
            # Operators can refresh manually with `docker pull <image>`.
            if "image" in svc:
                svc["pull_policy"] = "missing"
            if svc_name == "action_gateway" and tool_secrets:
                env_list = svc.setdefault("environment", [])
                for env_var, value in tool_secrets.items():
                    if value:
                        env_list.append(f"{env_var}={value}")

        _rewrite_compose_bind_paths_to_host(services)
        content = _yaml.dump(compose_doc, default_flow_style=False, sort_keys=False)

        # Write to a temp file next to the original so relative paths resolve
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False,
            prefix=f"dpg-{slug}-",
            dir=str(COMPOSE_FILE.parent),
        )
        tmp.write(content)
        tmp.flush()
        tmp.close()
        compose_path = tmp.name
        state.compose_file_path = compose_path

        # Mark all as starting
        for phase in DEPLOY_PHASES:
            for svc in phase["services"]:
                state.set_service(svc, "starting")

        result = await run_compose_up(compose_path, project_name=f"dpg-{slug}", secrets=secrets)
        if result["success"]:
            for svc_name in state.services:
                state.set_service(svc_name, "running")
            state.overall = "complete"
        else:
            for svc_name in state.services:
                state.set_service(svc_name, "failed", result["stderr"][:200])
            state.overall = "failed"
            logger.error(
                "docker_deploy_failed",
                extra={"operation": "_run_docker_deploy", "status": "failure", "error": result["stderr"][:500]},
            )
    except Exception as exc:
        for svc_name in state.services:
            state.set_service(svc_name, "failed", str(exc)[:200])
        state.overall = "failed"
        logger.error(
            "docker_deploy_exception",
            extra={"operation": "_run_docker_deploy", "status": "failure", "error": str(exc)},
        )


async def _run_k8s_deploy(slug: str, state, secrets: dict, resources: dict, kubeconfig_content: str, namespace: str) -> None:
    """Background task: deploy all 14 charts via helm upgrade --install in phase order."""
    import tempfile
    from dev_kit.agent.deployer.helm import DEPLOY_PHASES, build_helm_command, run_helm_command

    _helm_base = HELM_BASE
    state.namespace = namespace

    # Write kubeconfig to a temp file
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, prefix="kubeconfig-")
    tmp.write(kubeconfig_content)
    tmp.flush()
    tmp.close()
    state.kubeconfig_path = tmp.name

    infra_services = {"redis", "memgraph", "otel_collector", "jaeger", "prometheus", "loki", "grafana"}
    from dev_kit.agent.deployer.dependencies import SERVICE_CHART_MAP, HELM_INFRA_DIR

    try:
        for phase in DEPLOY_PHASES:
            for svc_name in phase["services"]:
                state.set_service(svc_name, "starting")

                chart_dir = svc_name.replace("_", "-")
                if svc_name in infra_services:
                    chart_path = str(_helm_base / "infra" / chart_dir)
                else:
                    chart_path = str(_helm_base / "dpg" / chart_dir)

                release_name = chart_dir
                set_values: dict[str, str] = {}
                set_files: dict[str, str] = {}
                values_files: list[str] = []

                if svc_name in infra_services:
                    # Use the edited values.yaml from the infra Helm chart
                    infra_chart_dir = SERVICE_CHART_MAP.get(svc_name, chart_dir)
                    infra_values = HELM_INFRA_DIR / infra_chart_dir / "values.yaml"
                    if infra_values.exists():
                        values_files.append(str(infra_values))

                    # Inject secrets into infra services that need them
                    if svc_name == "redis" and secrets.get("redis_password"):
                        set_values["password"] = secrets["redis_password"]
                    elif svc_name == "memgraph" and secrets.get("memgraph_password"):
                        set_values["password"] = secrets["memgraph_password"]
                    elif svc_name == "grafana" and secrets.get("grafana_admin_password"):
                        set_values["adminPassword"] = secrets["grafana_admin_password"]
                else:
                    # DPG charts need config injection
                    dpg_file = DPG_DIR / f"{svc_name}.yaml"
                    domain_file = CONFIGS_DIR / slug / f"{svc_name}.yaml"
                    if dpg_file.exists():
                        set_files["dpgConfig"] = str(dpg_file)
                    if domain_file.exists():
                        set_files["domainConfig"] = str(domain_file)
                    if secrets.get("anthropic_api_key"):
                        set_values["anthropicApiKey"] = secrets["anthropic_api_key"]

                    # Inject infra secrets into DPG blocks that connect to them
                    if svc_name == "memory_layer":
                        if secrets.get("memgraph_password"):
                            set_values["memgraph.password"] = secrets["memgraph_password"]
                        if secrets.get("redis_password"):
                            set_values["redis.url"] = f"redis://:{secrets['redis_password']}@redis:6379/0"

                    # Inject domain-specific tool API keys as extraSecrets for action-gateway
                    if svc_name == "action_gateway":
                        for env_var, secret_value in secrets.get("tool_secrets", {}).items():
                            if secret_value:
                                set_values[f"extraSecrets.{env_var}"] = secret_value

                    # Inject Azure creds and upload chain auth into knowledge-engine
                    if svc_name == "knowledge_engine":
                        if secrets.get("azure_storage_account"):
                            set_values["azure.storageAccount"] = secrets["azure_storage_account"]
                        if secrets.get("azure_storage_key"):
                            set_values["azure.storageKey"] = secrets["azure_storage_key"]
                        if secrets.get("azure_container_name"):
                            set_values["azure.containerName"] = secrets["azure_container_name"]
                        if secrets.get("reach_to_ke_api_key"):
                            set_values["uploadAuth.reachToKeApiKey"] = secrets["reach_to_ke_api_key"]
                        if secrets.get("ke_to_devkit_api_key"):
                            set_values["uploadAuth.keToDevkitApiKey"] = secrets["ke_to_devkit_api_key"]
                        if secrets.get("ke_devkit_callback_url"):
                            set_values["uploadAuth.devkitCallbackUrl"] = secrets["ke_devkit_callback_url"]

                    # Inject upload chain auth into reach-layer and expose as NodePort
                    # so the local dev-kit can reach it directly from outside the cluster.
                    if svc_name == "reach_layer":
                        if secrets.get("devkit_to_reach_api_key"):
                            set_values["uploadAuth.devkitToReachApiKey"] = secrets["devkit_to_reach_api_key"]
                        if secrets.get("reach_to_ke_api_key"):
                            set_values["uploadAuth.reachToKeApiKey"] = secrets["reach_to_ke_api_key"]
                        if secrets.get("ke_internal_url"):
                            set_values["uploadAuth.keInternalUrl"] = secrets["ke_internal_url"]
                        # Expose reach-layer as NodePort so local dev-kit can call it for uploads.
                        # Fixed port 30805 avoids conflicts and is predictable for the ingest proxy.
                        set_values["service.type"] = "NodePort"
                        set_values["service.nodePort"] = "30805"

                    block_res = resources.get(svc_name, {})
                    limits = block_res.get("limits", {})
                    requests = block_res.get("requests", {})
                    if limits.get("cpu"):
                        set_values["resources.limits.cpu"] = limits["cpu"]
                    if limits.get("memory"):
                        set_values["resources.limits.memory"] = limits["memory"]
                    if requests.get("cpu"):
                        set_values["resources.requests.cpu"] = requests["cpu"]
                    if requests.get("memory"):
                        set_values["resources.requests.memory"] = requests["memory"]

                cmd = build_helm_command(
                    chart_path=chart_path,
                    release_name=release_name,
                    namespace=namespace,
                    kubeconfig_path=tmp.name,
                    set_values=set_values or None,
                    set_files=set_files or None,
                    values_files=values_files or None,
                    upgrade=True,
                )

                result = await run_helm_command(cmd)
                if result["success"]:
                    state.set_service(svc_name, "running")
                else:
                    state.set_service(svc_name, "failed", result["stderr"][:200])
                    logger.error(
                        "k8s_deploy_service_failed",
                        extra={
                            "operation": "_run_k8s_deploy",
                            "status": "failure",
                            "service": svc_name,
                            "error": result["stderr"][:500],
                        },
                    )

        # Determine overall status
        statuses = {s["status"] for s in state.services.values()}
        if "failed" in statuses:
            state.overall = "failed"
        else:
            state.overall = "complete"

    except Exception as exc:
        for svc_name in state.services:
            if state.services[svc_name]["status"] == "queued":
                state.set_service(svc_name, "failed", str(exc)[:200])
        state.overall = "failed"
        logger.error(
            "k8s_deploy_exception",
            extra={"operation": "_run_k8s_deploy", "status": "failure", "error": str(exc)},
        )


# Services with no Docker healthcheck — treat "running" as "healthy"
# since that's the best signal they ever emit.
_NO_HEALTHCHECK_SERVICES = {"loki", "grafana", "prometheus", "jaeger", "otel_collector"}

# Compose service name → canonical state key
_COMPOSE_TO_STATE = {
    "reach_layer_web": "reach_layer",
    "reach_layer_voice": "reach_layer",
    "otelcol": "otel_collector",
}


def _docker_status(c_state: str, c_status: str, svc_name: str) -> str:
    """Map raw docker compose State/Status fields to a canonical service status.

    Args:
        c_state: Value of the ``State`` field from ``docker compose ps``.
        c_status: Value of the ``Status`` field (includes health text).
        svc_name: Canonical state service name (after compose→state mapping).

    Returns:
        One of: ``healthy``, ``running``, ``starting``, ``failed``, or the
        raw c_state value for unrecognised states.
    """
    if c_state == "running":
        if "healthy" in c_status.lower() or svc_name in _NO_HEALTHCHECK_SERVICES:
            return "healthy"
        return "running"
    if c_state == "exited":
        return "failed"
    if c_state == "restarting":
        # Crash-loop detection: Docker reports restart count in the Status
        # field as "Restarting (N) X seconds ago". After 3+ restarts the
        # container is unlikely to self-heal — surface it as failed so the
        # UI shows the Restart button rather than spinning forever.
        m = re.search(r"Restarting \((\d+)\)", c_status)
        if m and int(m.group(1)) >= 3:
            return "failed"
        return "starting"
    if c_state == "created":
        return "starting"
    return c_state or "starting"


@app.get("/api/projects/{slug}/deploy/status")
async def get_deploy_status(slug: str) -> dict:
    """Poll deployment status of all services.

    For Docker deployments, polls ``docker compose ps`` for live container state.
    For Kubernetes deployments, polls ``kubectl get pods`` for pod status.
    Falls back to the in-memory deployment state if no active deployment.

    Args:
        slug: Project slug identifying the deployment to query.

    Returns:
        Dict with ``services`` (list of dicts with name/status/error) and
        ``overall`` (deploying|complete|failed) keys.
    """
    from dev_kit.agent.deployer.state import get_state

    state = get_state(slug)
    if not state:
        # No in-memory state (dev_kit restarted, or a teammate just opened
        # the wizard from a fresh session). Probe the docker daemon by the
        # compose project label so an already-deployed stack still surfaces
        # as ``complete`` and the wizard can skip straight to Ingest without
        # re-collecting secrets.
        from dev_kit.agent.deployer.compose import list_project_containers

        containers = await list_project_containers(f"dpg-{slug}")
        if not containers:
            return {"services": [], "overall": "idle"}

        services_out: list[dict] = []
        all_ok = True
        for c in containers:
            compose_name = c.get("Service") or c.get("Name", "")
            svc_name = _COMPOSE_TO_STATE.get(compose_name, compose_name)
            c_state = c.get("State", "")
            c_status = c.get("Status", "")
            status = _docker_status(c_state, c_status, svc_name)
            if status == "failed":
                all_ok = False
            services_out.append({"name": svc_name, "status": status, "error": ""})
        return {
            "services": services_out,
            "overall": "complete" if all_ok else "failed",
            "target": "docker",
        }

    if state.target == "docker" and state.compose_file_path:
        from dev_kit.agent.deployer.compose import get_compose_status, get_service_logs

        containers = await get_compose_status(state.compose_file_path, project_name=f"dpg-{slug}")
        if not containers:
            # Containers are gone (e.g. docker compose down was run externally).
            # Clear stale in-memory state so next deploy starts fresh.
            from dev_kit.agent.deployer.state import clear_state
            clear_state(slug)
            return {"services": [], "overall": "idle"}
        if containers:
            for c in containers:
                compose_name = c.get("Service", c.get("Name", ""))
                svc_name = _COMPOSE_TO_STATE.get(compose_name, compose_name)
                c_state = c.get("State", "")
                c_status = c.get("Status", "")
                status = _docker_status(c_state, c_status, svc_name)
                error_text = ""
                if status == "failed":
                    # Fetch last log lines so the UI can show a readable reason.
                    # get_service_logs reads only container stdout/stderr — no secrets.
                    error_text = await get_service_logs(
                        state.compose_file_path, compose_name,
                        project_name=f"dpg-{slug}", tail=15,
                    )
                if svc_name in state.services:
                    state.set_service(svc_name, status, error=error_text)
            statuses = {s["status"] for s in state.services.values()}
            if state.overall != "deploying":
                state.overall = "failed" if "failed" in statuses else "complete"
            elif all(s in ("healthy", "running") for s in statuses):
                state.overall = "complete"

    elif state.overall in ("complete", "failed"):
        if state.target == "kubernetes" and state.kubeconfig_path:
            from dev_kit.agent.deployer.helm import get_pod_status

            pods = await get_pod_status(state.namespace, state.kubeconfig_path)
            if pods:
                for pod in pods:
                    pod_name = pod["name"]
                    matched_svc = None
                    for svc_name in state.services:
                        release = svc_name.replace("_", "-")
                        if pod_name.startswith(release):
                            matched_svc = svc_name
                            break
                    if matched_svc:
                        if pod["ready"]:
                            state.set_service(matched_svc, "healthy")
                        elif pod["status"] == "Running":
                            state.set_service(matched_svc, "running")
                        elif pod["status"] in ("Pending", "ContainerCreating"):
                            state.set_service(matched_svc, "starting")
                        else:
                            state.set_service(matched_svc, "failed", pod["status"])

                statuses = {s["status"] for s in state.services.values()}
                state.overall = "failed" if "failed" in statuses else "complete"

    return state.to_response()


@app.post("/api/projects/{slug}/deploy/services/{service}/restart")
async def restart_deploy_service(slug: str, service: str) -> dict:
    """Restart a single deployed service without redeploying the full stack.

    Translates the state service name to the compose service name, runs
    ``docker compose restart`` on that one service, and resets its in-memory
    status to ``starting`` so the polling loop shows fresh progress.

    Only supported for Docker deployments. No secrets are accepted or returned.

    Args:
        slug: Project slug.
        service: Canonical state service name (e.g. ``knowledge_engine``,
            ``reach_layer``).

    Returns:
        ``{"ok": true}`` on success.

    Raises:
        HTTPException 422: Invalid characters in service name.
        HTTPException 404: No active deployment for this project.
        HTTPException 400: Not a Docker deployment, or service name unknown.
        HTTPException 500: Docker restart command failed.
    """
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_]+$', service):
        raise HTTPException(422, "Invalid service name")

    from dev_kit.agent.deployer.state import get_state
    from dev_kit.agent.deployer.compose import restart_service

    state = get_state(slug)
    if not state:
        raise HTTPException(404, "No active deployment for this project")
    if state.target != "docker":
        raise HTTPException(400, "Per-service restart is only supported for Docker deployments")
    if not state.compose_file_path:
        raise HTTPException(400, "Compose file path not available in deployment state")
    if service not in state.services:
        raise HTTPException(400, f"Unknown service: {service}")

    # Map canonical state name → compose service name
    _STATE_TO_COMPOSE = {
        "reach_layer": "reach_layer_web",
        "otel_collector": "otelcol",
    }
    compose_service = _STATE_TO_COMPOSE.get(service, service)

    start = time.time()
    result = await restart_service(
        state.compose_file_path, compose_service, project_name=f"dpg-{slug}"
    )
    if not result["success"]:
        # Truncate stderr so docker's verbose output doesn't leak compose internals
        err_preview = result["stderr"].strip()[:300]
        raise HTTPException(500, f"Failed to restart {service}: {err_preview}")

    # Reset service in state — polling will update it to running/healthy shortly
    state.set_service(service, "starting")
    if state.overall in ("failed", "complete"):
        state.overall = "deploying"

    logger.info(
        "devkit.restart_service",
        extra={
            "operation": "devkit.restart_service",
            "status": "success",
            "slug": slug,
            "service": service,
            "latency_ms": int((time.time() - start) * 1000),
        },
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Ingest proxy endpoints (dev-kit → Reach Layer → KE)
# ---------------------------------------------------------------------------

@app.post("/api/ingest/submit")
async def ingest_submit(request: Request):
    """Accept a multipart document batch from the browser and forward to Reach Layer.

    Validates entries (extension, size, count, path traversal), injects user_id
    from devkit.yaml into the metadata, then streams the batch to Reach Layer.

    Args:
        request: Incoming FastAPI request containing the multipart form.

    Returns:
        Batch response from KE via Reach Layer (batch_id + per-file job_ids).

    Raises:
        HTTPException: 413 if a file exceeds the size limit.
        HTTPException: 422 if metadata is missing, invalid JSON, too many files, path traversal, or unsupported extension.
        HTTPException: 503 if Reach Layer is unreachable.
        HTTPException: 504 if Reach Layer times out.
        HTTPException: 502 on other upstream errors.
    """
    import httpx as _httpx
    form = await request.form()
    raw_metadata = form.get("metadata")
    if not raw_metadata:
        raise HTTPException(422, "metadata field is required")

    try:
        metadata_entries = json.loads(raw_metadata)
    except Exception as e:
        raise HTTPException(422, f"Invalid metadata JSON: {e}")

    # Validate batch size
    if len(metadata_entries) > _DEVKIT_CONFIG.upload.max_files_per_upload:
        raise HTTPException(
            422,
            f"Too many files: max {_DEVKIT_CONFIG.upload.max_files_per_upload} per batch"
        )

    # Collect file parts
    file_parts: dict[str, bytes] = {}
    for field_name, value in form.multi_items():
        if field_name == "files" and hasattr(value, "filename") and hasattr(value, "read"):
            content = await value.read()
            file_parts[value.filename] = content

    # Validate each entry
    for entry in metadata_entries:
        filename = entry.get("filename", "")
        safe_name = Path(filename).name
        if safe_name != filename or "/" in filename or "\\" in filename:
            raise HTTPException(422, f"Invalid filename: {filename}")

        ext = Path(safe_name).suffix.lower()
        if ext not in set(_DEVKIT_CONFIG.upload.supported_extensions):
            raise HTTPException(422, f"Unsupported extension: {ext}")

        mode = entry.get("mode", "")
        if mode in ("local_write_ingest", "cloud_upload_ingest"):
            file_bytes = file_parts.get(safe_name)
            if file_bytes is not None:
                size_mb = len(file_bytes) / (1024 * 1024)
                if size_mb > _DEVKIT_CONFIG.upload.max_file_size_mb:
                    raise HTTPException(
                        413,
                        f"{filename} exceeds {_DEVKIT_CONFIG.upload.max_file_size_mb} MB limit"
                    )

    # Inject user_id into each metadata entry
    for entry in metadata_entries:
        entry["user_id"] = _DEVKIT_CONFIG.user_id

    # Rebuild multipart body and forward to Reach Layer
    multipart_data = {"metadata": json.dumps(metadata_entries)}
    files_to_send = [
        ("files", (fname, fbytes, "application/octet-stream"))
        for fname, fbytes in file_parts.items()
    ]

    # If the in-process upload-chain keys are empty (dev_kit restarted, or
    # the teammate auto-unlocked into Ingest without a fresh deploy), try
    # to recover them from the still-running reach_layer_web container.
    # If the container itself was deployed with empty keys, generate fresh
    # ones and force-recreate the affected services so dev_kit and the
    # deployed stack are aligned.
    if not _DEVKIT_TO_REACH_API_KEY:
        if not _rehydrate_upload_chain_from_running_containers() or not _DEVKIT_TO_REACH_API_KEY:
            _ensure_upload_chain_keys_for_running_project()

    start = time.time()
    try:
        async with _httpx.AsyncClient(timeout=120.0) as http_client:
            ke_response = await http_client.post(
                f"{_REACH_LAYER_URL}/ingest/upload",
                data=multipart_data,
                files=files_to_send if files_to_send else None,
                headers={"X-API-Key": _DEVKIT_TO_REACH_API_KEY},
            )
        logger.info(
            "devkit.ingest_submit",
            extra={
                "operation": "devkit.ingest_submit",
                "status": "success",
                "reach_status": ke_response.status_code,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return Response(
            content=ke_response.content,
            status_code=ke_response.status_code,
            media_type=ke_response.headers.get("content-type", "application/json"),
        )
    except _httpx.ConnectError as e:
        logger.error(
            "devkit.ingest_submit_unreachable",
            extra={"operation": "devkit.ingest_submit", "status": "failure", "error": str(e)},
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.ingest_submit_timeout",
            extra={"operation": "devkit.ingest_submit", "status": "failure", "error": str(e)},
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        logger.error(
            "devkit.ingest_submit_error",
            extra={"operation": "devkit.ingest_submit", "status": "failure", "error": str(e)},
        )
        raise HTTPException(502, "Upstream error communicating with Reach Layer") from e


@app.get("/api/ingest/job/{job_id}")
async def ingest_job_status(job_id: str):
    """Return job status by proxying to Reach Layer → KE.

    Called by the frontend poller every poll_interval_seconds.

    Args:
        job_id: Unique identifier of the ingestion job to query.

    Returns:
        Job status response from KE.

    Raises:
        HTTPException: 422 if job_id contains invalid characters.
        HTTPException: 503 if Reach Layer is unreachable.
        HTTPException: 504 if Reach Layer times out.
        HTTPException: 502 on other upstream errors.
    """
    import httpx as _httpx
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_-]+$', job_id):
        raise HTTPException(422, "Invalid job_id format")
    start = time.time()
    try:
        async with _httpx.AsyncClient(timeout=10.0) as http_client:
            ke_response = await http_client.get(
                f"{_REACH_LAYER_URL}/ingest/job/{job_id}",
                headers={"X-API-Key": _DEVKIT_TO_REACH_API_KEY},
            )
        logger.info(
            "devkit.ingest_job_status",
            extra={
                "operation": "devkit.ingest_job_status",
                "status": "success",
                "reach_status": ke_response.status_code,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return Response(
            content=ke_response.content,
            status_code=ke_response.status_code,
            media_type=ke_response.headers.get("content-type", "application/json"),
        )
    except _httpx.ConnectError as e:
        logger.error(
            "devkit.ingest_job_status_unreachable",
            extra={"operation": "devkit.ingest_job_status", "status": "failure", "error": str(e)},
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.ingest_job_status_timeout",
            extra={"operation": "devkit.ingest_job_status", "status": "failure", "error": str(e)},
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        raise HTTPException(502, "Upstream error communicating with Reach Layer") from e


@app.get("/api/ingest/jobs")
async def list_ingest_jobs(limit: int = 100):
    """Return ingestion history by proxying to Reach Layer → KE.

    Called by the frontend on mount of the Ingest Documents step so previously
    ingested files are shown even after navigating away and back.

    Args:
        limit: Maximum number of records to return (default 100, max 500).

    Returns:
        Job list response from KE.

    Raises:
        HTTPException: 503 if Reach Layer is unreachable.
        HTTPException: 504 if Reach Layer times out.
        HTTPException: 502 on other upstream errors.
    """
    import httpx as _httpx
    start = time.time()
    try:
        async with _httpx.AsyncClient(timeout=10.0) as http_client:
            ke_response = await http_client.get(
                f"{_REACH_LAYER_URL}/ingest/jobs",
                params={"limit": min(limit, 500)},
                headers={"X-API-Key": _DEVKIT_TO_REACH_API_KEY},
            )
        logger.info(
            "devkit.list_ingest_jobs",
            extra={
                "operation": "devkit.list_ingest_jobs",
                "status": "success",
                "reach_status": ke_response.status_code,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return Response(
            content=ke_response.content,
            status_code=ke_response.status_code,
            media_type=ke_response.headers.get("content-type", "application/json"),
        )
    except _httpx.ConnectError as e:
        logger.error(
            "devkit.list_ingest_jobs_unreachable",
            extra={"operation": "devkit.list_ingest_jobs", "status": "failure", "error": str(e)},
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.list_ingest_jobs_timeout",
            extra={"operation": "devkit.list_ingest_jobs", "status": "failure", "error": str(e)},
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        raise HTTPException(502, "Upstream error communicating with Reach Layer") from e


class _CallbackBody(BaseModel):
    """Payload sent by KE when a job completes."""

    job_id: str
    status: str
    chunks_added: Optional[int] = None
    error: Optional[str] = None


@app.post("/api/ingest/callback")
async def ingest_callback(
    body: _CallbackBody,
    request: Request,
):
    """Receive ingestion completion callback from KE.

    Validates the KE_TO_DEVKIT_API_KEY, then appends the result to
    project.json ingest_log as an audit trail.

    Returns:
        {"ok": true} on success.
    """
    x_api_key = request.headers.get("X-API-Key")
    # Same restart-recovery path as ingest_submit: KE callbacks arrive after
    # an upload finishes, possibly long after dev_kit was restarted, so the
    # globals may be empty even though KE has the original key.
    if not _KE_TO_DEVKIT_API_KEY:
        _rehydrate_upload_chain_from_running_containers()
    _verify_api_key(x_api_key, _KE_TO_DEVKIT_API_KEY)

    logger.info(
        "devkit.ingest_callback",
        extra={
            "operation": "devkit.ingest_callback",
            "status": "success",
            "job_id": body.job_id,
            "ingest_status": body.status,
        },
    )

    _append_callback_to_ingest_log(body.job_id, body.status, body.chunks_added, body.error)
    return {"ok": True}


def _append_callback_to_ingest_log(
    job_id: str,
    status: str,
    chunks_added: Optional[int],
    error: Optional[str],
) -> None:
    """Append a callback result to the ingest_log of the relevant project.json.

    Searches CONFIGS_DIR (or PROJECTS_DIR env if set, for tests) for directories
    containing <project>/_meta/project.json and appends the callback result.
    If no project is found or any error occurs, silently skips.

    Args:
        job_id: UUID of the completed job.
        status: Terminal status ('ingested' or 'failed').
        chunks_added: Number of chunks added (if ingested).
        error: Error message (if failed).
    """
    try:
        projects_dir = Path(os.environ.get("PROJECTS_DIR", str(CONFIGS_DIR)))
        if not projects_dir.exists():
            return

        from datetime import datetime, timezone
        entry = {
            "job_id": job_id,
            "status": status,
            "chunks_added": chunks_added,
            "error": error,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            project_json = project_dir / "_meta" / "project.json"
            if not project_json.exists():
                continue
            with project_json.open("r") as f:
                data = json.load(f)
            ingest_log = data.get("ingest_log", [])
            ingest_log.append(entry)
            data["ingest_log"] = ingest_log
            with project_json.open("w") as f:
                json.dump(data, f, indent=2)
            return
    except Exception as e:
        logger.warning(
            "devkit.ingest_log_write_failed",
            extra={
                "operation": "devkit.ingest_callback",
                "status": "failure",
                "error": str(e),
            },
        )


# ---------------------------------------------------------------------------
# Dev-kit config endpoint
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/ingest/doc-types")
def get_project_doc_types(slug: str) -> dict:
    """Return the union of doc_types declared in the project's KE config.

    Scans ``knowledge.blocks.static_knowledge_base.sources[].doc_type`` and
    every value under ``knowledge.blocks.static_knowledge_base.intent_filters``
    so the frontend can render a doc_type dropdown that matches exactly
    what the domain's retrieval filters expect.

    Args:
        slug: Project slug.

    Returns:
        Dict with ``doc_types`` (sorted unique list) and ``default_doc_type``.
    """
    project_path = _get_project_path(slug)
    ke_file = project_path / "knowledge_engine.yaml"
    doc_types: set[str] = set()
    default_doc_type = "general"

    if ke_file.exists():
        try:
            data = yaml.safe_load(ke_file.read_text()) or {}
        except yaml.YAMLError:
            data = {}
        block = (
            data.get("knowledge", {})
            .get("blocks", {})
            .get("static_knowledge_base", {})
        )
        if isinstance(block, dict):
            default_doc_type = block.get("default_doc_type") or default_doc_type
            for src in block.get("sources", []) or []:
                if isinstance(src, dict):
                    dt = src.get("doc_type")
                    if isinstance(dt, str) and dt.strip():
                        doc_types.add(dt.strip())
            filters = block.get("intent_filters") or {}
            if isinstance(filters, dict):
                for values in filters.values():
                    if isinstance(values, list):
                        for v in values:
                            if isinstance(v, str) and v.strip():
                                doc_types.add(v.strip())

    return {
        "doc_types": sorted(doc_types),
        "default_doc_type": default_doc_type,
    }


@app.get("/api/devkit-config")
async def get_devkit_config():
    """Return dev-kit operational config values for the frontend.

    Used by IngestDocumentsStep to read upload limits and polling parameters
    without hardcoding them in the frontend bundle.

    Returns:
        Upload limits and polling config from devkit.yaml.
    """
    return {
        "user_id": _DEVKIT_CONFIG.user_id,
        "upload": {
            "max_files_per_upload": _DEVKIT_CONFIG.upload.max_files_per_upload,
            "max_file_size_mb": _DEVKIT_CONFIG.upload.max_file_size_mb,
            "supported_extensions": _DEVKIT_CONFIG.upload.supported_extensions,
        },
        "polling": {
            "poll_interval_seconds": _DEVKIT_CONFIG.polling.poll_interval_seconds,
            "poll_timeout_minutes": _DEVKIT_CONFIG.polling.poll_timeout_minutes,
        },
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/favicon.svg")
    def serve_favicon():
        """Serve the favicon SVG file."""
        favicon = _STATIC_DIR / "favicon.svg"
        if favicon.exists():
            return FileResponse(favicon, media_type="image/svg+xml")
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"error": "Frontend not built. Run: cd frontend && npm run build"}
