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
        selected_channels = _get_engine(slug).accumulator.get_reach_channel_selection_or_default()
        services_to_remove = {
            svc_name
            for channel, svc_name in _CHANNEL_SERVICE.items()
            if channel not in selected_channels
        }
        # ngrok depends_on reach_layer_voice (tunnels port 8006 for Vobiz webhooks).
        # Remove it if voice is not selected.
        if "voice" not in selected_channels:
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
            if svc_name == "action_gateway" and tool_secrets:
                ag_env = svc.setdefault("environment", [])
                for env_var in tool_secrets:
                    if tool_secrets[env_var]:
                        ag_env.append(f"{env_var}=<set at deploy time>")
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
        _REACH_LAYER_URL = "http://localhost:8005"

    # Auto-fill ke_internal_url based on target if not already provided.
    if not secrets.get("ke_internal_url"):
        if target == "kubernetes":
            namespace = body.get("namespace", "dpg")
            secrets["ke_internal_url"] = f"http://knowledge-engine.{namespace}.svc.cluster.local:8001"
        else:
            # Docker Compose: KE is reachable on its service name within the compose network.
            secrets["ke_internal_url"] = "http://knowledge_engine:8001"

    # Auto-fill ke_devkit_callback_url from devkit external_url if not provided.
    # KE calls this URL when an ingestion job completes (ingested or failed).
    # If empty, KE skips the callback and the frontend polls for status instead.
    if not secrets.get("ke_devkit_callback_url"):
        devkit_ext = _DEVKIT_CONFIG.external_url
        if devkit_ext:
            secrets["ke_devkit_callback_url"] = f"{devkit_ext.rstrip('/')}/api/ingest/callback"

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

        # Determine which reach_layer services to remove
        services_to_remove = {
            svc_name
            for channel, svc_name in _CHANNEL_SERVICE.items()
            if channel not in selected_channels
        }
        # ngrok depends_on reach_layer_voice (tunnels port 8006 for Vobiz webhooks).
        # Remove it if voice is not selected.
        if "voice" not in selected_channels:
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
            if svc_name == "action_gateway" and tool_secrets:
                env_list = svc.setdefault("environment", [])
                for env_var, value in tool_secrets.items():
                    if value:
                        env_list.append(f"{env_var}={value}")
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
        return {"services": [], "overall": "idle"}

    # For Docker deployments, always poll live container status so the UI
    # gets real-time updates (compose up -d returns quickly but containers
    # take time to become healthy).
    # Compose service names use dashes; state keys use underscores.
    _COMPOSE_TO_STATE = {
        "reach_layer_web": "reach_layer",
        "reach_layer_voice": "reach_layer",
        "otelcol": "otel_collector",
    }
    if state.target == "docker" and state.compose_file_path:
        from dev_kit.agent.deployer.compose import get_compose_status

        containers = await get_compose_status(state.compose_file_path, project_name=f"dpg-{slug}")
        if containers:
            for c in containers:
                compose_name = c.get("Service", c.get("Name", ""))
                svc_name = _COMPOSE_TO_STATE.get(compose_name, compose_name)
                c_state = c.get("State", "")
                c_status = c.get("Status", "")
                if c_state == "running":
                    status = "healthy" if "healthy" in c_status.lower() else "running"
                elif c_state == "exited":
                    status = "failed"
                else:
                    status = c_state
                if svc_name in state.services:
                    state.set_service(svc_name, status)
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
                    # Map pod name back to service (release name is prefix)
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
