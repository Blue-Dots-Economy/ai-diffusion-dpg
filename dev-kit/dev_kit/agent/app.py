"""
dev-kit/dev_kit/agent/app.py

FastAPI application for the DPG conversation agent.

Serves the conversation API and the React SPA (built frontend output
mounted at agent/static/).
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

from dev_kit.agent.auth import verify_api_key as _verify_api_key
from dev_kit.agent.block_status import all_block_statuses, block_completion_status
from dev_kit.agent.crypto import decrypt_secrets_dict, get_public_key_spki_b64
from dev_kit.agent.field_status import load_field_status
from dev_kit.agent.history import load_history
from dev_kit.agent.intake_state import IntakeState, load_intake_state, save_intake_state
from dev_kit.agent import phase_driver
from dev_kit.agent.phase_driver import (
    LLMResponse,
    ToolCall,
    save_accumulator,
    save_current_phase,
)
from dev_kit.agent.project_state import (
    BLOCKS,
    empty_accumulator,
    load_accumulator,
    # Aliased to disambiguate from phase_driver.save_accumulator (line 45) which
    # takes a slug_root rather than an explicit file path. Both are imported.
    save_accumulator as _save_accumulator_path,
)
from dev_kit.agent.tools import DEVKIT_TOOL_SCHEMAS
from dev_kit.agent.renderer import load_block_from_file, render_all
from dev_kit.config.loader import load_devkit_config as _load_devkit_config
from dev_kit.schemas.validation import validate_partial

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

logger = logging.getLogger(__name__)


# Per-slug locks for serialising concurrent chat turns on the same
# project. ``phase_driver.run_turn`` loads/mutates/saves five
# ``_meta/*.json`` files plus appends to ``history.jsonl``; two
# concurrent ``POST /api/projects/{slug}/chat`` requests (two browser
# tabs, a stale-refresh retry, etc.) would race and produce
# last-write-wins corruption. The lock is per-slug so cross-project
# parallelism is preserved. ``_slug_locks_guard`` serialises the
# get-or-create itself.
_slug_locks: dict[str, "asyncio.Lock"] = {}
_slug_locks_guard: "asyncio.Lock | None" = None


async def _acquire_slug_lock(slug: str) -> "asyncio.Lock":
    """Return the per-slug ``asyncio.Lock``, creating it if needed.

    Lazy-creates the guard lock on first use so module import doesn't
    require an active event loop.
    """
    global _slug_locks_guard
    if _slug_locks_guard is None:
        _slug_locks_guard = asyncio.Lock()
    async with _slug_locks_guard:
        return _slug_locks.setdefault(slug, asyncio.Lock())


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

# Load dev-kit config once at startup; configure logging immediately after so
# all subsequent logger calls honour the level from config (or DEVKIT_LOG_LEVEL env var).
_DEVKIT_CONFIG = _load_devkit_config()
logging.basicConfig(
    level=getattr(logging, _DEVKIT_CONFIG.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
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
            exc_info=True,
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
    """Request body for POST /api/projects.

    The 5 new intake fields (project_name, domain_description, selected_channels,
    default_language, supported_languages) are optional so the old form shape
    ``{name, description}`` continues to work as a fallback.
    """

    # Legacy fields — kept for backwards compatibility.
    name: str
    description: str = ""

    # New deterministic-wizard intake fields.
    project_name: Optional[str] = None
    domain_description: Optional[str] = None
    selected_channels: list[str] = ["web"]
    default_language: str = "english"
    supported_languages: list[str] = ["english"]

    def effective_project_name(self) -> str:
        """Return project_name if set, falling back to name."""
        return self.project_name or self.name

    def effective_domain_description(self) -> str:
        """Return domain_description if set, falling back to description."""
        return self.domain_description or self.description


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
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Project metadata is corrupt") from exc


# ---------------------------------------------------------------------------
# Dev-kit LLM call builder (used by the migrated /chat endpoint)
# ---------------------------------------------------------------------------

_DEVKIT_MODEL = os.environ.get("DEVKIT_MODEL", "claude-haiku-4-5-20251001")
_DEVKIT_MAX_TOKENS = int(os.environ.get("DEVKIT_MAX_TOKENS", "4096"))


def _build_devkit_llm_call():
    """Return a sync ``(system_prompt, messages) -> LLMResponse`` callable.

    Each invocation builds a fresh sync Anthropic client (cheap), so the
    callable is safe to use under ``asyncio.to_thread`` from the chat handler.

    The returned callable accepts the full Anthropic-format ``messages`` list
    (text turns plus any ``tool_use``/``tool_result`` rounds for an in-turn
    tool loop) and surfaces ``stop_reason`` and the model's raw content blocks
    on the response so ``phase_driver.run_turn`` can echo them back on the
    next loop iteration.

    Returns:
        A callable accepting ``(system_prompt, messages)`` and returning an
        ``LLMResponse`` with text, tool_calls (each carrying the provider
        ``tool_use_id``), raw_content, stop_reason, model, and token counts.

    Raises:
        anthropic.APIError: On Anthropic API failures (timeout, connection,
            status error). Propagates from the inner ``_llm_call`` through
            ``asyncio.to_thread`` to the chat handler's exception handler,
            which logs and returns HTTP 500.
    """
    def _llm_call(system_prompt: str, messages: list[dict]) -> LLMResponse:
        sync_client = anthropic.Anthropic()
        response = sync_client.messages.create(
            model=_DEVKIT_MODEL,
            max_tokens=_DEVKIT_MAX_TOKENS,
            system=system_prompt,
            messages=messages,
            tools=DEVKIT_TOOL_SCHEMAS,
            timeout=30.0,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[dict] = []
        for block in response.content:
            # Serialize each content block to the Anthropic message-format dict
            # so phase_driver can echo it back verbatim as the assistant turn
            # on the next tool-use loop iteration.
            if hasattr(block, "model_dump"):
                raw_content.append(block.model_dump())
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.name,
                        args=dict(block.input),
                        id=getattr(block, "id", None),
                    )
                )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            model=getattr(response, "model", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            stop_reason=getattr(response, "stop_reason", None),
            raw_content=raw_content,
        )

    return _llm_call


# ---------------------------------------------------------------------------
# Project-route helpers (stateless, per-request)
# ---------------------------------------------------------------------------


def _reach_channels_from_accumulator(accumulator: dict[str, dict]) -> list[str]:
    """Return the channels configured in reach_layer YAML; fall back to ['web'].

    Used as a fallback when the project predates the deterministic wizard and
    has no intake_state.json.  Mirrors the logic of
    ``ConfigAccumulator.get_reach_channel_selection_or_default``.

    Args:
        accumulator: The full accumulator dict (one entry per block).

    Returns:
        List of non-null channel names from ``reach_layer.reach_layer.channels``,
        or ``['web']`` when none are configured.
    """
    channels_cfg = (
        accumulator.get("reach_layer", {})
        .get("reach_layer", {})
        .get("channels", {})
    )
    inferred = [ch for ch, cfg in channels_cfg.items() if cfg is not None]
    return inferred if inferred else ["web"]


def _workflow_graph(accumulator: dict[str, dict]) -> dict:
    """Return the subagent workflow as nodes and edges for the frontend graph.

    Mirrors ``ConfigAccumulator.get_workflow_graph`` so the workflow endpoint
    can operate directly on the raw accumulator dict without constructing a
    ``ConfigAccumulator`` instance.

    Args:
        accumulator: The full accumulator dict (one entry per block).

    Returns:
        Dict with ``nodes`` (list of ``{id, name, type}``) and
        ``edges`` (list of ``{from, to, intent}``).
    """
    subagents = (
        accumulator.get("agent_core", {})
        .get("agent_workflow", {})
        .get("subagents", [])
    )
    nodes = []
    edges = []
    for sa in subagents:
        node_type = (
            "start" if sa.get("is_start")
            else ("end" if sa.get("is_terminal") else "normal")
        )
        nodes.append({"id": sa["id"], "name": sa.get("name", sa["id"]), "type": node_type})
        for rule in sa.get("routing", []):
            edges.append({
                "from": sa["id"],
                "to": rule.get("next_subagent_id", ""),
                "intent": rule.get("intent", ""),
            })
    return {"nodes": nodes, "edges": edges}


def _required_secrets_from_accumulator(accumulator: dict[str, dict]) -> list[dict]:
    """Return API-key secrets required by configured tools (Action Gateway).

    Scans the action_gateway block's ``tools`` list for non-empty
    ``auth.secret_env`` fields. Each entry tells the deploy wizard which
    password field to render.

    Args:
        accumulator: The full accumulator dict (one entry per block).

    Returns:
        List of ``{env_var, tool_id, description}`` dicts. Empty if no tools
        declare a ``secret_env``.
    """
    result = []
    for tool in accumulator.get("action_gateway", {}).get("tools", []):
        secret_env = (tool.get("auth") or {}).get("secret_env", "")
        if secret_env:
            result.append({
                "env_var": secret_env,
                "tool_id": tool.get("id", ""),
                "description": tool.get("description", ""),
            })
    return result


def _channel_secrets_from_intake_and_accumulator(
    intake: IntakeState | None,
    accumulator: dict[str, dict],
) -> list[dict]:
    """Return credential descriptors for channels selected in the intake state.

    Inspects ``intake.selected_channels`` and returns a structured list that
    the deploy wizard renders as credential input fields. Web channel requires
    Google OAuth client ID; voice channel requires Vobiz and Raya credentials
    plus the public service URL. Recording config is read from the accumulator's
    reach_layer block.

    Args:
        intake: The project's intake state. When ``None`` (legacy project
            without intake_state.json), returns an empty list.
        accumulator: The full accumulator dict (one entry per block).

    Returns:
        List of dicts, each with keys:
            ``env_var``     — environment variable name injected into container
            ``label``       — field label shown in the UI
            ``description`` — hint text shown below the field
            ``required``    — True for all current channel credentials
            ``section``     — ``"web"`` or ``"voice"``
            ``secret``      — ``True`` → SecretInput (masked); ``False`` → plain
        Returns an empty list when intake is ``None`` or no credential-bearing
        channel is selected.
    """
    if intake is None:
        return []
    selected = intake.selected_channels or []
    result: list[dict] = []
    if "web" in selected:
        result.append({
            "env_var": "GOOGLE_CLIENT_ID",
            "label": "Google Client ID",
            "description": (
                "Google is the only supported auth provider. Get your Client ID from "
                "the Google Cloud Console — create an OAuth 2.0 credential and add "
                "your deployment URL as an authorised origin."
            ),
            "required": True,
            "section": "web",
            "secret": False,
        })
    if "voice" in selected:
        recording_cfg = (
            accumulator.get("reach_layer", {})
            .get("reach_layer", {})
            .get("channels", {})
            .get("voice", {})
            .get("recording", {})
        )
        recording_source = recording_cfg.get("source", "disabled")
        if recording_source and recording_source != "disabled":
            result.append({
                "env_var": "RECORDING_CALLER_ID_HASH_SALT",
                "label": "Recording Caller-ID Hash Salt",
                "description": (
                    "Secret salt used to hash caller phone numbers in recording metadata. "
                    "Must be at least 32 characters (64 hex chars recommended). "
                    "Auto-generated by the wizard if left blank."
                ),
                "required": True,
                "section": "voice",
                "secret": True,
            })
            store_backend = recording_cfg.get("store", {}).get("backend", "local")
            if store_backend == "s3":
                result.append({
                    "env_var": "RECORDING_S3_KMS_KEY_ID",
                    "label": "Recording S3 KMS Key ID",
                    "description": (
                        "Optional AWS KMS key ID used to encrypt recordings in S3. "
                        "Leave blank to use the bucket's default encryption."
                    ),
                    "required": False,
                    "section": "voice",
                    "secret": True,
                })
        result.extend([
            {
                "env_var": "VOBIZ_AUTH_ID",
                "label": "Vobiz Auth ID",
                "description": "Your Vobiz account Auth ID. Found in the Vobiz dashboard under Account settings.",
                "required": True,
                "section": "voice",
                "secret": True,
            },
            {
                "env_var": "VOBIZ_AUTH_TOKEN",
                "label": "Vobiz Auth Token",
                "description": "Your Vobiz account Auth Token. Found in the Vobiz dashboard under Account settings.",
                "required": True,
                "section": "voice",
                "secret": True,
            },
            {
                "env_var": "RAYA_API_KEY",
                "label": "Raya API Key",
                "description": "API key for Raya STT/TTS. Found in your Raya dashboard.",
                "required": True,
                "section": "voice",
                "secret": True,
            },
            {
                "env_var": "PUBLIC_URL",
                "label": "Voice Public URL",
                "description": (
                    "Public HTTPS URL of the voice service "
                    "(e.g. https://voice.203-0-113-42.sslip.io). "
                    "The voice server returns this to Vobiz so it knows where to open the audio WebSocket."
                ),
                "required": True,
                "section": "voice",
                "secret": False,
            },
            {
                "env_var": "VOBIZ_FROM_NUMBER",
                "label": "Vobiz From Number",
                "description": (
                    "Vobiz-assigned phone number used as caller ID on outbound calls "
                    "(E.164 format, e.g. +919876543210). Required — the voice service will not start without it."
                ),
                "required": True,
                "section": "voice",
                "secret": False,
            },
        ])
    return result


# ---------------------------------------------------------------------------
# Project routes
# ---------------------------------------------------------------------------


@app.post("/api/projects")
def create_project(body: CreateProjectRequest) -> dict:
    """Create a new project and initialise its directory structure.

    Writes ``project.json``, empty config YAMLs, ``intake_state.json``
    (with the 5 form fields + 7 binary flags defaulted to False),
    ``current_phase.txt`` (set to ``"tier"``), and ``accumulator.json``
    (empty per-block dicts).

    Args:
        body: CreateProjectRequest with at minimum ``name``. The new intake
            fields (project_name, domain_description, selected_channels,
            default_language, supported_languages) are optional and fall back
            to the legacy ``name``/``description`` values when absent.

    Returns:
        The project metadata dict that was written to ``project.json``.
    """
    effective_name = body.effective_project_name()
    slug = _slugify(effective_name)
    project_path = _get_project_path(slug)
    project_path.mkdir(parents=True, exist_ok=True)
    meta_dir = project_path / "_meta"
    meta_dir.mkdir(exist_ok=True)
    meta = {
        "slug": slug,
        "name": effective_name,
        "description": body.effective_domain_description(),
        "current_phase": "tier",
        "phases_completed": [],
        "agent_type": "",
        "phase_decisions": {},
    }
    (meta_dir / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    # Build IntakeState first so render_all (and future calls) get the
    # source-of-truth for form fields and binary flags.
    intake = IntakeState(
        project_name=effective_name,
        domain_description=body.effective_domain_description(),
        selected_channels=body.selected_channels,
        default_language=body.default_language,
        supported_languages=body.supported_languages,
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        completed=False,
    )
    intake.touch()
    save_intake_state(meta_dir / "intake_state.json", intake)
    logger.info(
        "devkit.project.intake_state_saved",
        extra={"operation": "api.create_project", "status": "success", "slug": slug},
    )

    # Write initial current_phase.
    save_current_phase(project_path, "tier")

    # Initialise empty accumulator + render placeholder YAMLs.
    acc_dict = empty_accumulator()
    save_accumulator(project_path, acc_dict)
    render_all(project_path, acc_dict, intake)

    logger.info(
        "devkit.project.created",
        extra={"operation": "api.create_project", "status": "success", "slug": slug},
    )
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
                    exc_info=True,
                )
    return projects


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict:
    """Get project metadata and config statuses.

    Loads all state per-request from disk (accumulator, field_status,
    intake_state). Legacy projects without intake_state.json return partial
    metadata without crashing.

    Args:
        slug: Project slug.

    Returns:
        Project metadata dict augmented with config_statuses, azure_storage,
        required_secrets, channel_secrets, has_knowledge_base, and llm_provider.
        ``azure_storage["needed"]`` reflects ``intake_state.uses_azure_blob``
        — set during the knowledge phase chat when the operator confirms the
        KB documents live in Azure Blob Storage. The deploy form uses this
        flag to decide whether to surface AZURE_STORAGE_ACCOUNT /
        AZURE_STORAGE_KEY / AZURE_CONTAINER_NAME inputs.

    Raises:
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if any state file (accumulator.json, field_status.json,
            or intake_state.json) contains corrupt or invalid data.
    """
    meta = _load_project_meta(slug)
    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"

    # Load per-request state from disk.
    try:
        accumulator = load_accumulator(meta_dir / "accumulator.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_project",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt accumulator.json for '{slug}': {exc}",
        )

    try:
        field_status = load_field_status(meta_dir / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_project",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )

    # Legacy projects may not have intake_state.json — degrade gracefully.
    # A corrupt intake_state.json is treated as a real error (not a legacy project).
    try:
        intake = load_intake_state(meta_dir / "intake_state.json")
    except FileNotFoundError:
        intake = None
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_project",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt intake_state.json for '{slug}': {exc}",
        )

    meta["config_statuses"] = all_block_statuses(field_status)

    # Azure Blob Storage: surface intake_state.uses_azure_blob so the
    # deploy form knows whether to show Azure credential fields. The
    # flag is captured during the knowledge phase chat via
    # `update_intake(field="uses_azure_blob", value=...)`. Credentials
    # themselves never travel through chat — only the boolean intent.
    meta["azure_storage"] = {
        "needed": bool(intake and getattr(intake, "uses_azure_blob", False))
    }

    meta["required_secrets"] = _required_secrets_from_accumulator(accumulator)
    meta["channel_secrets"] = (
        _channel_secrets_from_intake_and_accumulator(intake, accumulator)
        if intake is not None
        else []
    )

    # Knowledge base presence — used by the deploy wizard to skip the ingest step.
    meta["has_knowledge_base"] = bool(intake and intake.has_kb)

    # LLM provider chosen during the language phase. Defaults to "anthropic"
    # when the agent block hasn't been configured yet.
    agent_cfg = accumulator.get("agent_core", {}).get("agent", {}) or {}
    meta["llm_provider"] = agent_cfg.get("provider") or "anthropic"

    return meta


@app.delete("/api/projects/{slug}")
def delete_project(slug: str) -> dict:
    """Delete a project directory and all its files.

    Args:
        slug: The project slug identifying the directory under CONFIGS_DIR.

    Returns:
        Dict with key ``deleted`` set to the slug of the removed project.

    Raises:
        HTTPException: 404 if the project directory does not exist.
    """
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    shutil.rmtree(project_path)
    logger.info(
        "devkit.project.deleted",
        extra={"operation": "api.delete_project", "status": "success", "slug": slug},
    )
    return {"deleted": slug}


# ---------------------------------------------------------------------------
# Chat routes
# ---------------------------------------------------------------------------


@app.post("/api/projects/{slug}/chat")
async def chat(slug: str, body: ChatRequest) -> dict:
    """Send a user message and receive the agent response.

    Delegates to ``phase_driver.run_turn``. The phase_driver appends
    user/assistant entries to ``_meta/history.jsonl`` and persists all state
    (intake, accumulator, field_status, current_phase) on success.

    Args:
        slug: Project slug.
        body: ChatRequest with the user message.

    Returns:
        Dict shaped to preserve the React UI contract: ``reply`` (assistant
        text), ``phase`` (the phase after the turn), ``config_updates`` ([]),
        ``checkpoint_created`` (None), ``graph`` ({}).

    Raises:
        HTTPException 404: If the project directory does not exist.
        HTTPException 400: If the project predates the deterministic wizard
            (no ``_meta/intake_state.json``).
        HTTPException 500: If ``phase_driver.run_turn`` raises.
    """
    start = time.time()
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    if not (project_path / "_meta" / "intake_state.json").exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project '{slug}' was created with an older version of the "
                "dev-kit. Please create a new project to continue."
            ),
        )
    try:
        # Serialise concurrent chat turns on the same slug. The
        # alternative (two browser tabs, a stale-refresh retry, any
        # parallel POST to this endpoint with the same slug) races on
        # five ``_meta/*.json`` files plus the ``history.jsonl`` append
        # because ``phase_driver.run_turn`` is a load-mutate-save cycle
        # against shared disk state. The lock is per-slug so different
        # projects still run in parallel.
        slug_lock = await _acquire_slug_lock(slug)
        async with slug_lock:
            response_text = await asyncio.to_thread(
                phase_driver.run_turn,
                body.message,
                slug,
                projects_root=project_path.parent,
                llm_call=_build_devkit_llm_call(),
            )
    except Exception as exc:
        # Inspect the Anthropic error type so we can return an
        # operator-friendly message for the two failure modes the
        # wizard cannot recover from on its own: an exhausted API
        # credit balance and a missing/invalid API key. Both produce
        # `anthropic.BadRequestError` and `anthropic.AuthenticationError`
        # respectively; the raw message is unfriendly and the wizard's
        # 500 surfaces as an empty chat reply in the UI, which looks
        # like a wizard freeze. Surface them as a 402 / 401 with
        # human-readable detail so the operator knows what to do.
        msg = str(exc)
        if "credit balance is too low" in msg or "credit_balance" in msg:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "anthropic_credit_exhausted",
                    "message": (
                        "The Anthropic API rejected the request because your "
                        "account credit balance is too low. Top up at "
                        "https://console.anthropic.com/settings/billing and "
                        "try the same message again — the wizard's state is "
                        "preserved."
                    ),
                },
            ) from exc
        if "authentication_error" in msg.lower() or "invalid x-api-key" in msg.lower():
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "anthropic_auth_failed",
                    "message": (
                        "The Anthropic API rejected the request because the "
                        "ANTHROPIC_API_KEY env var is missing, empty, or "
                        "invalid. Set it and restart the dev-kit; the "
                        "wizard's state is preserved."
                    ),
                },
            ) from exc
        logger.error(
            "devkit.chat.failed",
            extra={
                "operation": "api.chat",
                "status": "failure",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "latency_ms": int((time.time() - start) * 1000),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc

    current_phase = phase_driver.load_current_phase(project_path)
    logger.info(
        "devkit.chat.success",
        extra={
            "operation": "api.chat",
            "status": "success",
            "latency_ms": int((time.time() - start) * 1000),
            "slug": slug,
            "current_phase": current_phase,
        },
    )
    return {
        "reply": response_text,
        "phase": current_phase,
        "config_updates": [],
        "checkpoint_created": None,
        "graph": {},
    }


@app.get("/api/projects/{slug}/history")
def get_history(slug: str) -> list[dict]:
    """Return the chat history for the project.

    Reads ``_meta/history.jsonl`` directly.

    Args:
        slug: Project slug.

    Returns:
        Ordered list of ``{role, content}`` dicts (oldest first). Empty if
        no history has been recorded yet.

    Raises:
        HTTPException 404: If the project directory does not exist.
    """
    project_path = _get_project_path(slug)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    entries = load_history(project_path)
    return [{"role": e.role, "content": e.content} for e in entries]


# ---------------------------------------------------------------------------
# Config routes
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/configs")
def get_configs(slug: str) -> list[dict]:
    """Return all 7 config blocks with their completion status and on-disk YAML content.

    Status is derived from ``field_status.json`` via ``all_block_statuses``; no
    accumulator load is required for this endpoint.  Content is the raw
    on-disk YAML text so the UI can display exactly what will be deployed.

    Args:
        slug: Project slug.

    Returns:
        List of ``{block, status, content}`` dicts, one per block.

    Raises:
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``field_status.json`` contains corrupt data.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"
    try:
        field_status = load_field_status(meta_dir / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_configs",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )
    statuses = all_block_statuses(field_status)
    result = []
    for block in BLOCKS:
        config_file = project_path / f"{block}.yaml"
        content = config_file.read_text() if config_file.exists() else ""
        result.append({
            "block": block,
            "status": statuses[block],
            "content": content,
        })
    return result


@app.get("/api/projects/{slug}/configs/export")
def export_configs(slug: str):
    """Return all config YAML files for a project as a ZIP archive.

    Reads on-disk YAML files directly — the on-disk files are the source of
    truth that operators want to export and deploy, not the in-memory
    accumulator dict which may differ from what was last rendered.

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
    """Return a single block's on-disk YAML content and completion status.

    Args:
        slug: Project slug.
        block: Block name — must be one of the 7 standard DPG blocks.

    Returns:
        Dict with ``block``, ``status`` (``"complete"`` or ``"incomplete"``),
        and ``content`` (raw YAML text, or ``""`` if the file does not exist).

    Raises:
        HTTPException: 400 if ``block`` is not a known block name.
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``field_status.json`` contains corrupt data.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"
    config_file = project_path / f"{block}.yaml"
    content = config_file.read_text() if config_file.exists() else ""
    try:
        field_status = load_field_status(meta_dir / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_config",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )
    status = block_completion_status(block, field_status)
    return {"block": block, "status": status, "content": content}


@app.put("/api/projects/{slug}/configs/{block}")
def update_config_file(slug: str, block: str, body: UpdateConfigRequest) -> dict:
    """Write operator-supplied YAML to disk and reverse-sync the accumulator.

    Parses YAML before writing to reject malformed content early.  Does NOT
    modify ``field_status.json`` — this is an out-of-band editor action, not
    a wizard turn; field completion state is unchanged (Session 5, note #2).

    Args:
        slug: Project slug.
        block: Block name — must be one of the 7 standard DPG blocks.
        body: Request body containing the raw YAML string.

    Returns:
        Dict with ``block``, ``status`` (derived from field_status), and
        ``validation_errors`` (list of strings from the mirror-schema check).

    Raises:
        HTTPException: 400 if ``block`` is unknown or if the YAML is malformed.
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``accumulator.json`` or ``field_status.json``
            is corrupt.
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    _load_project_meta(slug)  # raises 404 if project not found

    # Parse before writing — reject invalid YAML with 400.
    try:
        parsed = yaml.safe_load(body.content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"
    config_file = project_path / f"{block}.yaml"

    # Write the raw YAML text to disk (preserves user formatting + comments).
    config_file.write_text(body.content)

    # Load, update, and persist the accumulator.
    try:
        accumulator = load_accumulator(meta_dir / "accumulator.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={"operation": "api.update_config_file", "status": "failure",
                   "error": str(exc), "slug": slug},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt accumulator.json for '{slug}': {exc}",
        )
    accumulator[block] = parsed
    _save_accumulator_path(meta_dir / "accumulator.json", accumulator)

    # Validate against the mirror schema (does NOT touch field_status).
    errors = validate_partial(block, parsed)

    # Derive status from field_status only (not from validation result).
    try:
        field_status = load_field_status(meta_dir / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.update_config_file",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )
    status = block_completion_status(block, field_status)

    return {"block": block, "status": status, "validation_errors": errors}


@app.post("/api/projects/{slug}/configs/reload")
def reload_configs(slug: str) -> dict[str, Any]:
    """Repopulate the accumulator from on-disk YAML files.

    Reads each block's ``.yaml`` file from disk via ``load_block_from_file``
    and writes a fresh ``accumulator.json``.  Useful after the operator
    hand-edits YAML files outside the UI (e.g. via an editor or ``git pull``).

    Args:
        slug: Project slug.

    Returns:
        Dict with ``reloaded`` (``True``), ``slug``, and ``block_statuses``
        (``{block_name: "complete"|"incomplete"}`` for all 7 blocks).

    Raises:
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``field_status.json`` contains corrupt data.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"

    # Rebuild the accumulator from on-disk YAML files.
    accumulator = empty_accumulator()
    for block in BLOCKS:
        accumulator[block] = load_block_from_file(project_path, block)
    _save_accumulator_path(meta_dir / "accumulator.json", accumulator)

    try:
        field_status = load_field_status(meta_dir / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.reload_configs",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )
    return {
        "reloaded": True,
        "slug": slug,
        "block_statuses": all_block_statuses(field_status),
    }


@app.post("/api/projects/{slug}/configs/validate")
def validate_all_configs(slug: str) -> dict[str, Any]:
    """Run partial mirror-schema validation on all 7 blocks.

    Loads the accumulator from disk and runs ``validate_partial`` against each
    block's dev-kit mirror schema.  Does not run the full runtime Pydantic
    validation (use ``POST /deploy/validate`` for that).

    Args:
        slug: Project slug.

    Returns:
        Dict mapping each block name to ``{"valid": bool, "errors": list[str]}``.

    Raises:
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``accumulator.json`` is corrupt.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    meta_dir = project_path / "_meta"

    try:
        accumulator = load_accumulator(meta_dir / "accumulator.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={"operation": "api.validate_all_configs", "status": "failure",
                   "error": str(exc), "slug": slug},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt accumulator.json for '{slug}': {exc}",
        )

    results = {}
    for block in BLOCKS:
        data = accumulator.get(block, {})
        errors = validate_partial(block, data)
        results[block] = {"valid": len(errors) == 0, "errors": errors}
    return results


@app.post("/api/projects/{slug}/deploy/validate")
def pre_deploy_validate(slug: str) -> dict[str, Any]:
    """Run full merged-config validation and cross-block invariant checks.

    Validation strategy (two modes):

    - **Docker (canonical):** Validates against the baked runtime
      schemas at ``/app/dpg_runtime_schemas/<block>/config.py`` —
      verbatim copies of each runtime block's ``MergedConfig``, baked
      into the image at the same ``GIT_SHA`` as the runtime services.
      A pass here means the runtime service will accept the same config
      at boot; a failure surfaces exactly the runtime error.
    - **Host (graceful fallback):** Validates against the per-block
      mirror schemas at ``dev_kit/schemas/domain/<block>.py`` via
      ``validate_full`` (strict — required-field errors not filtered).
      The mirrors do not know about DPG framework defaults, so they
      may over- or under-reject in places; the canonical answer is
      always the Docker-mode gate above. Operators running the
      wizard on the host should rebuild the dev-kit image to get
      authoritative validation before deploy.

    Cross-block invariants (tool-name integrity, intent-filter
    coverage, etc.) run in both modes — they're enforced by
    ``dev_kit.schemas.cross_block_validation``.

    Args:
        slug: Project slug.

    Returns:
        Dict with ``valid`` bool, ``block_errors`` per-block error
        list, ``invariant_errors`` list of cross-block rule violations,
        ``merged_configs`` for display, and ``validator`` indicating
        which gate ran (``"runtime_baked"`` or ``"host_mirror"``).
    """
    from dev_kit.loader import _load_and_merge
    from dev_kit.agent.renderer import RUNTIME_SCHEMAS, runtime_validate
    from dev_kit.agent.errors import RuntimeValidationError
    from pydantic import ValidationError as _VE

    _BLOCKS = (
        "agent_core", "knowledge_engine", "trust_layer", "memory_layer",
        "observability_layer", "action_gateway", "reach_layer",
    )
    docker_mode = RUNTIME_SCHEMAS is not None

    # Host fallback uses dev_kit/schema.py — a hand-maintained flat-file
    # copy that models BOTH halves (framework defaults + domain values)
    # of every block's merged config. The per-block mirror schemas at
    # dev_kit/schemas/domain/<block>.py only cover the domain half by
    # design, so they would over-reject the framework-default sections
    # (server, ke_client, redis, otel, etc.) every merged config carries.
    # Drift between dev_kit/schema.py and the actual runtime is what the
    # sync rule discipline (.claude/rules/runtime-devkit-sync.md) prevents.
    _HOST_MODELS: dict[str, Any] = {}
    if not docker_mode:
        from dev_kit.schema import (
            ActionGatewayConfig,
            AgentCoreConfig,
            KnowledgeEngineConfig,
            MemoryLayerConfig,
            ObservabilityLayerConfig,
            ReachLayerConfig,
            TrustLayerConfig,
        )
        _HOST_MODELS = {
            "agent_core": AgentCoreConfig,
            "knowledge_engine": KnowledgeEngineConfig,
            "trust_layer": TrustLayerConfig,
            "memory_layer": MemoryLayerConfig,
            "observability_layer": ObservabilityLayerConfig,
            "action_gateway": ActionGatewayConfig,
            "reach_layer": ReachLayerConfig,
        }

    import copy as _copy
    block_errors: dict[str, list[str]] = {}
    merged: dict[str, dict] = {}

    # Prefer IntakeState (new wizard); fall back to accumulator for projects that
    # pre-date the deterministic wizard and don't have intake_state.json yet.
    _intake_path = CONFIGS_DIR / slug / "_meta" / "intake_state.json"
    _intake: IntakeState | None = None
    try:
        _intake = load_intake_state(_intake_path)
        selected_channels = list(_intake.selected_channels)
    except FileNotFoundError:
        try:
            legacy_acc = load_accumulator(CONFIGS_DIR / slug / "_meta" / "accumulator.json")
        except ValueError as exc:
            logger.error(
                "devkit.deploy.accumulator_corrupt",
                extra={
                    "operation": "api.deploy_validate",
                    "status": "failure",
                    "error": str(exc),
                    "slug": slug,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Corrupt accumulator.json for '{slug}': {exc}",
            ) from exc
        selected_channels = _reach_channels_from_accumulator(legacy_acc)

    # Decide which blocks would actually be deployed. The compose
    # generator drops ``knowledge_engine`` when ``has_kb=false`` and
    # ``action_gateway`` when ``has_external_tools=false`` (see
    # ``services_to_remove`` in the deploy/preview path). Validating
    # the YAML for a block that will never be deployed produces noise
    # — e.g. ``knowledge.blocks.static_knowledge_base.collection_name:
    # Field required`` on a poem bot that never wired a KB. Mirror the
    # selective-deploy logic here. The legacy accumulator-only branch
    # has no intake flags to read; default to validating everything
    # (safe conservative behaviour).
    skipped_blocks: dict[str, str] = {}
    if _intake is not None:
        if not _intake.has_kb:
            skipped_blocks["knowledge_engine"] = (
                "has_kb=false — Knowledge Engine is not deployed for this project."
            )
        if not _intake.has_external_tools:
            skipped_blocks["action_gateway"] = (
                "has_external_tools=false — Action Gateway is not deployed for this project."
            )

    # 1. Per-block validation.
    for block in _BLOCKS:
        if block in skipped_blocks:
            # Selective-deploy: this block's service is not in the
            # compose generated for this project. The compose
            # generator drops it; validating its YAML would surface
            # spurious "required field" errors for sections the
            # runtime never sees. Mark as no-errors + leave
            # ``merged`` empty so it doesn't appear in display_merged.
            block_errors[block] = []
            merged[block] = {}
            continue
        try:
            data = _load_and_merge(slug, block)
            merged[block] = data  # store original for display in merged_configs
            if block == "reach_layer":
                # Deep-copy before patching so merged[block] retains the full
                # config (shown to the user) while validation sees the patched copy.
                # Null out channels that won't be deployed so the schema skips
                # their required fields (e.g. voice raya.stt_language).
                data = _copy.deepcopy(data)
                channels = (data.get("reach_layer") or {}).get("channels") or {}
                for ch in ("voice", "cli", "web"):
                    if ch not in selected_channels and ch in channels:
                        channels[ch] = None

            if docker_mode and block in RUNTIME_SCHEMAS:
                try:
                    runtime_validate(block, data)
                    block_errors[block] = []
                except RuntimeValidationError as exc:
                    pe = exc.pydantic_error
                    if hasattr(pe, "errors") and callable(pe.errors):
                        block_errors[block] = [
                            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                            for err in pe.errors()
                        ]
                    else:
                        block_errors[block] = [str(pe)]
            else:
                model_cls = _HOST_MODELS.get(block)
                if model_cls is None:
                    block_errors[block] = []
                    continue
                try:
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

    # 2. Cross-block invariant checks. The full ruleset lives in
    # dev_kit.schemas.cross_block_validation so the same checks run during
    # the LLM tool loop (set_phase) and at this deploy-time safety net.
    from dev_kit.schemas.cross_block_validation import validate_cross_block

    # Re-use the same selected_channels resolved above (IntakeState or accumulator fallback).
    invariant_errors: list[str] = validate_cross_block(merged, selected_channels)

    all_valid = all(len(errs) == 0 for errs in block_errors.values()) and not invariant_errors

    issue_count = sum(len(errs) for errs in block_errors.values()) + len(invariant_errors)
    validator = "runtime_baked" if docker_mode else "host_mirror"
    if all_valid:
        logger.info(
            "devkit.deploy.validation_passed",
            extra={
                "operation": "api.deploy_validate",
                "status": "success",
                "slug": slug,
                "validator": validator,
            },
        )
    else:
        logger.warning(
            "devkit.deploy.validation_failed",
            extra={
                "operation": "api.deploy_validate",
                "status": "failure",
                "slug": slug,
                "issues": issue_count,
                "validator": validator,
            },
        )

    # Build display-friendly merged configs: for reach_layer, mark unselected
    # channels as enabled: false so the viewer reflects what will actually deploy.
    display_merged = {}
    for block, data in merged.items():
        if not data:
            continue
        if block == "reach_layer":
            data = _copy.deepcopy(data)
            channels = (data.get("reach_layer") or {}).get("channels") or {}
            for ch in ("voice", "cli", "web"):
                if ch not in selected_channels and ch in channels and isinstance(channels[ch], dict):
                    channels[ch]["enabled"] = False
        display_merged[block] = yaml.dump(data, default_flow_style=False, sort_keys=False)

    return {
        "valid": all_valid,
        "block_errors": block_errors,
        "invariant_errors": invariant_errors,
        "merged_configs": display_merged,
        "validator": validator,
        # Map of {block_name: human-readable reason}. Blocks listed
        # here were skipped from validation because the selective-
        # deploy logic (see ``services_to_remove`` in deploy/preview)
        # drops them from the compose for this project. The frontend
        # can render this as "Validation skipped — service not
        # deployed" on the Config Review screen.
        "skipped_blocks": skipped_blocks,
    }


# ---------------------------------------------------------------------------
# Workflow graph route
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/workflow/graph")
def get_workflow_graph(slug: str) -> dict:
    """Return the subagent workflow as nodes and edges for the frontend graph.

    Reads the accumulator from disk and builds the workflow graph from the
    ``agent_core.agent_workflow.subagents`` list.

    Args:
        slug: Project slug.

    Returns:
        Dict with ``nodes`` (list of ``{id, name, type}``) and
        ``edges`` (list of ``{from, to, intent}``).

    Raises:
        HTTPException 404: If the project does not exist.
        HTTPException 500: If ``accumulator.json`` is corrupt.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    meta_dir = _get_project_path(slug) / "_meta"
    try:
        accumulator = load_accumulator(meta_dir / "accumulator.json")
    except ValueError as exc:
        logger.error(
            "devkit.workflow_graph.accumulator_corrupt",
            extra={
                "operation": "api.get_workflow_graph",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt accumulator.json for '{slug}': {exc}",
        ) from exc
    return _workflow_graph(accumulator)


# ---------------------------------------------------------------------------
# Deploy-fields routes (Task 11.2)
# ---------------------------------------------------------------------------


class DeploySettingsRequest(BaseModel):
    """Request body for POST /api/projects/{slug}/deploy-settings."""

    overrides: dict[str, Any] = {}


@app.get("/api/projects/{slug}/deploy-fields")
def get_deploy_fields(slug: str) -> dict:
    """Return every field with category=='deploy' or deploy_overridable==True.

    For each matching entry in AGGREGATED_FIELD_RULES, returns its path, the
    rule's default, the current value from the accumulator (if any), and
    display metadata.  The accumulator is consulted so that values already
    captured during chat are pre-filled in the deploy form.

    Args:
        slug: Project slug.

    Returns:
        Dict with ``fields`` list. Each entry has keys:
            ``path`` (dotted block-prefixed path),
            ``default`` (rule default or None),
            ``current_value`` (from accumulator or rule default),
            ``description`` (human-readable description or ``""``),
            ``advanced`` (bool).

    Raises:
        HTTPException: 404 if the project does not exist.
        HTTPException: 500 if ``accumulator.json`` is corrupt.
    """
    from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES

    _load_project_meta(slug)  # raises 404 if project not found
    meta_dir = _get_project_path(slug) / "_meta"
    try:
        accumulator = load_accumulator(meta_dir / "accumulator.json")
    except ValueError as exc:
        logger.error(
            "devkit.deploy_fields.accumulator_corrupt",
            extra={
                "operation": "api.get_deploy_fields",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt accumulator.json for '{slug}': {exc}",
        ) from exc

    fields = []
    for path, rule in AGGREGATED_FIELD_RULES.items():
        if rule.category != "deploy" and not rule.deploy_overridable:
            continue
        # Resolve current value from accumulator using the block-prefixed path.
        # Path format: "<block>.<section>.<field>"
        parts = path.split(".", 1)
        block = parts[0]
        field_path = parts[1] if len(parts) > 1 else ""
        block_data = accumulator.get(block, {}) or {}
        # Walk nested keys using dot notation.
        current_val: Any = block_data
        for key in field_path.split("."):
            if isinstance(current_val, dict):
                current_val = current_val.get(key)
            else:
                current_val = None
                break
        fields.append({
            "path": path,
            "default": rule.default,
            "current_value": current_val if current_val is not None else rule.default,
            "description": rule.description or "",
            "advanced": rule.advanced,
        })

    logger.info(
        "devkit.deploy_fields.listed",
        extra={
            "operation": "api.get_deploy_fields",
            "status": "success",
            "slug": slug,
            "count": len(fields),
        },
    )
    return {"fields": fields}


@app.post("/api/projects/{slug}/deploy-settings")
def save_deploy_settings(slug: str, body: DeploySettingsRequest) -> dict:
    """Persist operator deploy-time overrides for a project.

    Writes the overrides dict to ``<project_path>/_meta/deploy_settings.json``
    so they can be applied at deploy time without altering the generated YAML
    configs.

    Args:
        slug: Project slug.
        body: DeploySettingsRequest with ``overrides`` mapping dotted field
            paths to their operator-supplied values.

    Returns:
        Dict with ``status: "saved"`` and the number of overrides persisted.

    Raises:
        HTTPException: 404 if the project does not exist.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    settings_path = project_path / "_meta" / "deploy_settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(body.overrides, indent=2, ensure_ascii=False, sort_keys=True)
    )
    logger.info(
        "devkit.deploy_settings.saved",
        extra={
            "operation": "api.save_deploy_settings",
            "status": "success",
            "slug": slug,
            "override_count": len(body.overrides),
        },
    )
    return {"status": "saved", "override_count": len(body.overrides)}


# ---------------------------------------------------------------------------
# Field-status route (Task 11.3)
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/field-status")
def get_field_status(slug: str) -> dict:
    """Return the contents of field_status.json for phase-progress display.

    Reads ``<project_path>/_meta/field_status.json``.  Returns an empty dict
    when the file is absent (project created but wizard not yet started).

    Args:
        slug: Project slug.

    Returns:
        Dict mapping dotted field paths to status strings
        (``"pending"``, ``"answered"``, ``"needs_re_asking"``,
        ``"not_applicable"``).

    Raises:
        HTTPException: 404 if the project does not exist.
    """
    _load_project_meta(slug)  # raises 404 if project not found
    project_path = _get_project_path(slug)
    try:
        status = load_field_status(project_path / "_meta" / "field_status.json")
    except ValueError as exc:
        logger.error(
            "devkit.project.state_corrupt",
            extra={
                "operation": "api.get_field_status",
                "status": "failure",
                "error": str(exc),
                "slug": slug,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt field_status.json for '{slug}': {exc}",
        )
    logger.info(
        "devkit.field_status.read",
        extra={
            "operation": "api.get_field_status",
            "status": "success",
            "slug": slug,
            "field_count": len(status),
        },
    )
    return status


# ---------------------------------------------------------------------------
# Schema routes
# ---------------------------------------------------------------------------


@app.get("/api/schemas/{block}")
def get_schema_descriptions(block: str) -> dict:
    """Return field descriptions from a block's Pydantic schemas.

    Extracts field descriptions from the top-level section schemas
    declared in DOMAIN_SECTION_SCHEMAS. For each section, field docstrings
    are extracted from the Pydantic model's field definitions.

    If the block is unrecognised, an empty descriptions dict is returned
    instead of a 404.

    Args:
        block: DPG block name, e.g. ``reach_layer``.

    Returns:
        Dict with ``block`` and ``descriptions`` keys. ``descriptions`` maps
        field names to their docstring or field description.
    """
    from dev_kit.schemas.validation import DOMAIN_SECTION_SCHEMAS

    descriptions: dict[str, str] = {}

    # Collect all section schemas for this block
    for (b, section), schema_class in DOMAIN_SECTION_SCHEMAS.items():
        if b == block:
            # Extract field descriptions from the Pydantic model
            try:
                fields = schema_class.model_fields
                for field_name, field_info in fields.items():
                    # Use the field's description if available, otherwise empty string
                    description = field_info.description or ""
                    descriptions[field_name] = description
            except (AttributeError, TypeError):
                # If the schema doesn't have model_fields, skip it
                pass

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
    """Update a DPG framework YAML file. Validates YAML syntax + Pydantic schema.

    Args:
        slug: Project slug (unused; endpoint is project-scoped for consistency).
        block: DPG block name to update.
        body: Dict with ``content`` key containing the YAML string.

    Returns:
        Dict with ``status: ok`` on success.

    Raises:
        HTTPException: 400 if block name is not recognised, YAML is invalid, or
            Pydantic schema validation fails (when DEVKIT_DPG_SCHEMA_STRICT=1).
    """
    if block not in BLOCKS:
        raise HTTPException(status_code=400, detail=f"Unknown block: {block}")
    try:
        parsed = yaml.safe_load(body["content"]) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

    # Schema validation (gated by env flag for safe rollout).
    if os.environ.get("DEVKIT_DPG_SCHEMA_STRICT", "1") == "1":
        from dev_kit.schemas.validation import validate_dpg_block
        error = validate_dpg_block(block, parsed)
        if error:
            raise HTTPException(status_code=400, detail=f"Schema validation failed:\n{error}")

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
        #
        # Read IntakeState (new wizard). Projects created before the deterministic
        # wizard don't have intake_state.json — raise 400 so the caller knows the
        # project must be re-created rather than silently deploying a wrong compose.
        _intake_path = CONFIGS_DIR / slug / "_meta" / "intake_state.json"
        try:
            _intake = load_intake_state(_intake_path)
        except FileNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Project '{slug}' was created before the deterministic wizard "
                    "and does not have an intake_state.json. "
                    "Re-create the project through the new wizard to enable deployment."
                ),
            )
        selected_channels = list(_intake.selected_channels)
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
        # Selective service inclusion driven by IntakeState (deterministic wizard).
        # See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8.
        if not _intake.has_kb:
            services_to_remove.add("knowledge_engine")
        if not _intake.has_external_tools:
            services_to_remove.add("action_gateway")
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
            if svc_name == "reach_layer_web":
                web_mode = "full" if "web" in set(selected_channels) else "routing_only"
                svc.setdefault("environment", []).append(f"REACH_LAYER_WEB_MODE={web_mode}")
        # Strip depends_on references to removed services so docker compose doesn't
        # complain about dangling dependencies. Both list-form and map-form supported.
        for svc_name, svc in list(services.items()):
            deps = svc.get("depends_on")
            if not deps:
                continue
            if isinstance(deps, list):
                filtered = [d for d in deps if d not in services_to_remove]
                if filtered != deps:
                    if filtered:
                        svc["depends_on"] = filtered
                    else:
                        svc.pop("depends_on", None)
            elif isinstance(deps, dict):
                filtered_map = {k: v for k, v in deps.items() if k not in services_to_remove}
                if filtered_map != deps:
                    if filtered_map:
                        svc["depends_on"] = filtered_map
                    else:
                        svc.pop("depends_on", None)
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
        if secrets.get("openai_api_key"):
            set_values["openaiApiKey"] = secrets["openai_api_key"]

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

    # Load IntakeState once so we can filter the service list by the
    # selective-deploy logic the compose generator uses (see
    # ``services_to_remove`` further down in this function). Without
    # this filter, ``knowledge_engine`` / ``action_gateway`` stay in
    # ``state.services`` even when the compose drops them — the status
    # endpoint then surfaces them as ``failed`` in the UI because the
    # poll for an absent container falls through to the error path.
    # Legacy projects without intake_state.json get the full unfiltered
    # list (conservative — no flag info to drop from).
    _deploy_intake_path = CONFIGS_DIR / slug / "_meta" / "intake_state.json"
    try:
        _deploy_intake = load_intake_state(_deploy_intake_path)
    except FileNotFoundError:
        _deploy_intake = None

    # Mark all services as queued initially, filtered by selective-deploy.
    all_services = [
        "redis", "memgraph", "otel_collector", "jaeger", "prometheus", "loki", "grafana",
        "agent_core", "knowledge_engine", "memory_layer", "trust_layer",
        "action_gateway", "reach_layer", "observability_layer",
    ]
    if _deploy_intake is not None:
        if not _deploy_intake.has_kb:
            all_services = [s for s in all_services if s != "knowledge_engine"]
        if not _deploy_intake.has_external_tools:
            all_services = [s for s in all_services if s != "action_gateway"]
    for svc in all_services:
        state.set_service(svc, "queued")

    logger.info(
        "devkit.deploy.execute_triggered",
        extra={"operation": "api.deploy_execute", "status": "start", "slug": slug, "target": target},
    )
    if target == "docker":
        # Read IntakeState (new wizard). Projects created before the deterministic
        # wizard don't have intake_state.json — raise 400 so the caller knows the
        # project must be re-created rather than silently deploying a wrong compose.
        _intake_path = CONFIGS_DIR / slug / "_meta" / "intake_state.json"
        try:
            _intake = load_intake_state(_intake_path)
        except FileNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Project '{slug}' was created before the deterministic wizard "
                    "and does not have an intake_state.json. "
                    "Re-create the project through the new wizard to enable deployment."
                ),
            )
        selected_channels = list(_intake.selected_channels)
        asyncio.create_task(
            _run_docker_deploy(slug, state, secrets, resources, selected_channels, _intake)
        )
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
    intake: "IntakeState | None" = None,
) -> None:
    """Background task: apply resources, resolve domain, and run docker compose up."""
    import tempfile
    from dev_kit.agent.deployer.compose import run_compose_up
    from dev_kit.agent.deployer.helm import DEPLOY_PHASES

    logger.info(
        "devkit.docker_deploy.start",
        extra={"operation": "_run_docker_deploy", "status": "start", "slug": slug},
    )

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
        # Selective service inclusion driven by IntakeState (deterministic wizard).
        # See docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §8.
        if intake is not None and not intake.has_kb:
            services_to_remove.add("knowledge_engine")
        if intake is not None and not intake.has_external_tools:
            services_to_remove.add("action_gateway")
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
            if svc_name == "reach_layer_web":
                web_mode = "full" if "web" in set(selected_channels) else "routing_only"
                svc.setdefault("environment", []).append(f"REACH_LAYER_WEB_MODE={web_mode}")

        # Strip depends_on references to removed services so docker compose doesn't
        # complain about dangling dependencies. Both list-form and map-form supported.
        for svc_name, svc in list(services.items()):
            deps = svc.get("depends_on")
            if not deps:
                continue
            if isinstance(deps, list):
                filtered = [d for d in deps if d not in services_to_remove]
                if filtered != deps:
                    if filtered:
                        svc["depends_on"] = filtered
                    else:
                        svc.pop("depends_on", None)
            elif isinstance(deps, dict):
                filtered_map = {k: v for k, v in deps.items() if k not in services_to_remove}
                if filtered_map != deps:
                    if filtered_map:
                        svc["depends_on"] = filtered_map
                    else:
                        svc.pop("depends_on", None)

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
                "docker_deploy_failed: %s",
                result["stderr"][:500],
                extra={"operation": "_run_docker_deploy", "status": "failure"},
            )
    except Exception as exc:
        for svc_name in state.services:
            state.set_service(svc_name, "failed", str(exc)[:200])
        state.overall = "failed"
        logger.error(
            "docker_deploy_exception",
            extra={"operation": "_run_docker_deploy", "status": "failure", "error": str(exc)},
            exc_info=True,
        )


async def _run_k8s_deploy(slug: str, state, secrets: dict, resources: dict, kubeconfig_content: str, namespace: str) -> None:
    """Background task: deploy all 14 charts via helm upgrade --install in phase order."""
    import tempfile
    from dev_kit.agent.deployer.helm import DEPLOY_PHASES, build_helm_command, run_helm_command

    logger.info(
        "devkit.k8s_deploy.start",
        extra={"operation": "_run_k8s_deploy", "status": "start", "slug": slug},
    )
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
                    if secrets.get("openai_api_key"):
                        set_values["openaiApiKey"] = secrets["openai_api_key"]

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
                        "k8s_deploy_service_failed: %s",
                        result["stderr"][:500],
                        extra={
                            "operation": "_run_k8s_deploy",
                            "status": "failure",
                            "service": svc_name,
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
            exc_info=True,
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


async def _get_restart_count(container_name: str) -> int:
    """Return the restart count for a container via docker inspect.

    Args:
        container_name: Container name or ID.

    Returns:
        Number of times the container has been restarted, or 0 on error.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", container_name,
            "--format", "{{.RestartCount}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return int(stdout.decode().strip())
    except Exception:
        logger.warning("get_restart_count_failed", extra={"operation": "_get_restart_count", "container": container_name}, exc_info=True)
        return 0


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
        if "unhealthy" in c_status.lower():
            return "failed"
        if "healthy" in c_status.lower() or svc_name in _NO_HEALTHCHECK_SERVICES:
            return "healthy"
        return "running"
    if c_state in ("exited", "dead"):
        return "failed"
    if c_state == "restarting":
        # Any restart means the container crashed — surface immediately so
        # the user sees the error log and can act rather than waiting forever.
        return "failed"
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
            if state.overall == "deploying":
                # Images are still being pulled / containers not yet created.
                # Return the in-memory state so the UI keeps showing queued/starting
                # instead of falsely declaring the stack destroyed.
                return state.to_response()
            if state.overall == "failed":
                # Deploy failed before any container was created (e.g. image pull
                # rate limit, port conflict). Preserve the failure so the UI can
                # surface the real error instead of misreporting it as destroyed.
                return state.to_response()
            if state.overall == "destroying":
                # Destroy is in flight; let _run_docker_destroy clear state on
                # completion. Returning here would race the teardown task.
                return state.to_response()
            # Containers are gone after a previously-complete deploy
            # (e.g. `docker compose down` was run externally).
            from dev_kit.agent.deployer.state import clear_state
            clear_state(slug)
            return {"services": [], "overall": "idle"}
        if state.overall == "destroying":
            # Containers still exist but teardown is in progress — don't
            # recompute overall from live Docker state (services still appear
            # healthy during the shutdown window, which would flip the UI back
            # to "Deployment Complete").
            return state.to_response()
        if containers:
            for c in containers:
                compose_name = c.get("Service", c.get("Name", ""))
                svc_name = _COMPOSE_TO_STATE.get(compose_name, compose_name)
                c_state = c.get("State", "")
                c_status = c.get("Status", "")
                status = _docker_status(c_state, c_status, svc_name)
                # A container can be crash-looping while briefly showing State:
                # running between restarts (e.g. startup error before health check
                # resolves). docker compose ps doesn't expose restart count, so
                # inspect the container directly for any health-checked service.
                if (status == "running"
                        and svc_name not in _NO_HEALTHCHECK_SERVICES
                        and await _get_restart_count(c.get("Name", compose_name)) > 0):
                    status = "failed"
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
            if "failed" in statuses:
                state.overall = "failed"
            elif all(s in ("healthy", "running") for s in statuses):
                # Only mark complete when every health-checked service is healthy.
                # A service stuck in "running" may be about to crash — keep polling
                # until it either goes healthy or the restart count check catches it.
                hc_all_healthy = all(
                    state.services[svc]["status"] == "healthy"
                    for svc in state.services
                    if svc not in _NO_HEALTHCHECK_SERVICES
                )
                if hc_all_healthy:
                    state.overall = "complete"
                elif state.overall != "deploying":
                    # compose up finished but health checks haven't resolved yet —
                    # revert to deploying so the frontend keeps polling.
                    state.overall = "deploying"

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


@app.post("/api/projects/{slug}/destroy")
async def destroy_deploy(slug: str, body: dict) -> dict:
    """Tear down a running Docker Compose deployment for a project.

    Runs ``docker compose down --remove-orphans`` against the project, with an
    optional ``--volumes`` flag to also wipe named volumes (ChromaDB, Memgraph,
    kb_data). Returns immediately with ``status: started`` and runs teardown as
    a background task. Poll ``/deploy/status`` to track progress — overall
    transitions to ``destroying`` during teardown and the state is cleared
    (returning ``idle``) on success.

    Args:
        slug: Project slug identifying the deployment to destroy.
        body: Dict with optional ``remove_volumes`` (bool, default False).

    Returns:
        ``{"status": "started"}``

    Raises:
        HTTPException 400: Target is not ``docker`` or a deploy is in progress.
    """
    from dev_kit.agent.deployer.state import get_state

    remove_volumes: bool = bool(body.get("remove_volumes", False))
    project_name = f"dpg-{slug}"

    state = get_state(slug)
    if state and state.target != "docker":
        raise HTTPException(400, "Destroy is only supported for Docker deployments")
    if state and state.overall == "deploying":
        raise HTTPException(400, "Cannot destroy while a deployment is in progress")

    compose_path = state.compose_file_path if state else None
    if state:
        state.overall = "destroying"

    asyncio.create_task(_run_docker_destroy(slug, compose_path, project_name, remove_volumes))
    return {"status": "started"}


async def _run_docker_destroy(
    slug: str,
    compose_file_path: Optional[str],
    project_name: str,
    remove_volumes: bool,
) -> None:
    """Background task: run docker compose down and clean up state."""
    from dev_kit.agent.deployer.compose import run_compose_down
    from dev_kit.agent.deployer.state import get_state, clear_state

    logger.info(
        "devkit.destroy.start",
        extra={
            "operation": "_run_docker_destroy",
            "status": "start",
            "slug": slug,
            "remove_volumes": remove_volumes,
        },
    )
    try:
        result = await run_compose_down(
            project_name=project_name,
            compose_file_path=compose_file_path,
            remove_volumes=remove_volumes,
        )
        if result["success"]:
            if compose_file_path:
                try:
                    Path(compose_file_path).unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning(
                        "devkit.destroy_compose_cleanup_failed",
                        extra={"operation": "_run_docker_destroy", "error": str(exc)},
                        exc_info=True,
                    )
            clear_state(slug)
            logger.info(
                "devkit.destroy_complete",
                extra={
                    "operation": "_run_docker_destroy",
                    "status": "success",
                    "slug": slug,
                    "remove_volumes": remove_volumes,
                },
            )
        else:
            state = get_state(slug)
            if state:
                state.overall = "failed"
            logger.error(
                "devkit.destroy_failed: %s",
                result["stderr"][:500],
                extra={"operation": "_run_docker_destroy", "status": "failure"},
            )
    except Exception as exc:
        state = get_state(slug)
        if state:
            state.overall = "failed"
        logger.error(
            "devkit.destroy_exception",
            extra={"operation": "_run_docker_destroy", "status": "failure", "error": str(exc)},
            exc_info=True,
        )


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
            exc_info=True,
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.ingest_submit_timeout",
            extra={"operation": "devkit.ingest_submit", "status": "failure", "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        logger.error(
            "devkit.ingest_submit_error",
            extra={"operation": "devkit.ingest_submit", "status": "failure", "error": str(e)},
            exc_info=True,
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
            exc_info=True,
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.ingest_job_status_timeout",
            extra={"operation": "devkit.ingest_job_status", "status": "failure", "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        logger.error(
            "devkit.ingest_job_status_error",
            extra={"operation": "devkit.ingest_job_status", "status": "failure", "error": str(e)},
            exc_info=True,
        )
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
            exc_info=True,
        )
        raise HTTPException(503, "Reach Layer is unreachable") from e
    except _httpx.TimeoutException as e:
        logger.error(
            "devkit.list_ingest_jobs_timeout",
            extra={"operation": "devkit.list_ingest_jobs", "status": "failure", "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(504, "Reach Layer timed out") from e
    except _httpx.HTTPError as e:
        logger.error(
            "devkit.list_ingest_jobs_error",
            extra={"operation": "devkit.list_ingest_jobs", "status": "failure", "error": str(e)},
            exc_info=True,
        )
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
            exc_info=True,
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


@app.get("/api/enums")
async def get_enums():
    """Return the open enum values from `dev_kit/schemas/enums_config.yaml`.

    Consumed by the React frontend so dropdowns (languages, providers,
    models, voices) stay in sync with the source-of-truth YAML without
    hardcoding the lists in JS.

    Returns:
        Dict with `providers`, `languages`, `anthropic_models`, `openai_models`,
        `embedding_providers`, and `raya_voices` (list of {voice_id, language,
        name} dicts).
    """
    from dev_kit.schemas.enums import (
        ANTHROPIC_MODELS,
        EMBEDDING_PROVIDERS,
        LANGUAGES,
        OPENAI_MODELS,
        PROVIDERS,
        RAYA_VOICES,
    )

    return {
        "providers": list(PROVIDERS),
        "languages": list(LANGUAGES),
        "anthropic_models": list(ANTHROPIC_MODELS),
        "openai_models": list(OPENAI_MODELS),
        "embedding_providers": list(EMBEDDING_PROVIDERS),
        "raya_voices": list(RAYA_VOICES),
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
