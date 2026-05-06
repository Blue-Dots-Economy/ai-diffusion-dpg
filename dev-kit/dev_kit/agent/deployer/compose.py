"""Deployer compose module — Docker Compose execution helpers.

Part of the dev-kit deployer backend within the DPG framework. Provides
async wrappers around docker compose CLI commands for starting, stopping,
restarting, and inspecting services.
"""

import asyncio
import json
import logging
import os
import re as _re
import secrets as _secrets_module
from typing import Dict, List, Optional

_ANSI_ESCAPE = _re.compile(r'\x1b\[[0-9;]*[mGKHF]')

logger = logging.getLogger(__name__)


async def run_compose_up(
    compose_file_path: str,
    project_name: Optional[str] = None,
    domain: Optional[str] = None,
    secrets: Optional[Dict] = None,
) -> Dict:
    """Start all services defined in a docker-compose file in detached mode.

    Args:
        compose_file_path: Absolute path to the docker-compose.yml file.
        project_name: Optional Docker Compose project name override.
        domain: Domain/project slug used to resolve ``${DOMAIN}`` in the compose file.
        secrets: Optional dict of secrets to pass as environment variables.

    Returns:
        Dict with keys:
            success (bool): True if docker compose exited with code 0.
            stdout (str): Standard output.
            stderr (str): Standard error.
    """
    cmd = ["docker", "compose", "-f", compose_file_path]
    if project_name:
        cmd += ["-p", project_name]
    cmd += ["up", "-d"]

    env = {**os.environ}
    if domain:
        env["DOMAIN"] = domain
    if secrets:
        # Pass through whichever LLM provider key(s) the user supplied.
        # Agent Core only consults the one matching agent.provider at
        # runtime, so injecting both when both are set is harmless and
        # lets a config switch providers without reconfiguring secrets.
        if secrets.get("anthropic_api_key"):
            env["ANTHROPIC_API_KEY"] = secrets["anthropic_api_key"]
        if secrets.get("openai_api_key"):
            env["OPENAI_API_KEY"] = secrets["openai_api_key"]
        if secrets.get("memgraph_password"):
            env["MEMGRAPH_PASSWORD"] = secrets["memgraph_password"]
        if secrets.get("redis_password"):
            # Build password-authenticated URL; the compose file interpolates REDIS_URL.
            env["REDIS_URL"] = f"redis://:{secrets['redis_password']}@redis:6379/0"
        if secrets.get("grafana_admin_password"):
            env["GF_SECURITY_ADMIN_PASSWORD"] = secrets["grafana_admin_password"]
        # Auto-generate a session secret when not provided — Google auth requires it.
        # Falls back to the value already in os.environ (e.g. set on the VM) so
        # redeploying the same stack preserves existing login sessions.
        env.setdefault("REACH_SESSION_SECRET",
                       secrets.get("reach_session_secret") or _secrets_module.token_urlsafe(32))
        # Upload chain auth — resolves ${VAR:-} placeholders in the compose file
        _upload_chain = {
            "devkit_to_reach_api_key": "DEVKIT_TO_REACH_API_KEY",
            "ke_to_devkit_api_key": "KE_TO_DEVKIT_API_KEY",
            "reach_to_ke_api_key": "REACH_TO_KE_API_KEY",
        }
        for secret_key, env_var in _upload_chain.items():
            if secrets.get(secret_key):
                env[env_var] = secrets[secret_key]
        # Internal service URLs — allow override when services run at non-default addresses
        if secrets.get("ke_internal_url"):
            env["KE_INTERNAL_URL"] = secrets["ke_internal_url"]
        # Always set KE_DEVKIT_CALLBACK_URL so the compose default (http://dev-kit:8080)
        # is suppressed — the dev-kit service is excluded from local deploys.
        # If empty, KE skips the callback and devkit polls for status instead.
        env["KE_DEVKIT_CALLBACK_URL"] = secrets.get("ke_devkit_callback_url", "")
        # Azure Blob Storage — resolves ${VAR:-} placeholders in the compose file
        _azure = {
            "azure_storage_account": "AZURE_STORAGE_ACCOUNT",
            "azure_storage_key": "AZURE_STORAGE_KEY",
            "azure_container_name": "AZURE_CONTAINER_NAME",
        }
        for secret_key, env_var in _azure.items():
            if secrets.get(secret_key):
                env[env_var] = secrets[secret_key]
        # Channel credentials — inject each non-empty value as its env var name
        for env_var, value in secrets.get("channel_secrets", {}).items():
            if value:
                env[env_var] = value

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        result = {"success": success, "stdout": stdout.decode(), "stderr": stderr.decode()}
        logger.info(
            "run_compose_up",
            extra={
                "operation": "run_compose_up",
                "status": "success" if success else "failure",
                "compose_file": compose_file_path,
            },
        )
        return result
    except Exception as exc:
        logger.error(
            "run_compose_up_exception",
            extra={"operation": "run_compose_up", "status": "failure", "error": str(exc)},
        )
        return {"success": False, "stdout": "", "stderr": str(exc)}


async def run_compose_down(
    project_name: str,
    compose_file_path: Optional[str] = None,
    remove_volumes: bool = False,
) -> Dict:
    """Stop and remove all containers in a Docker Compose project.

    Args:
        project_name: Docker Compose project name (e.g. ``dpg-<slug>``).
        compose_file_path: Optional absolute path to the compose file. When
            provided, used with ``-f`` for precise service resolution. When
            None, falls back to project-name-only targeting via ``-p`` (used
            when dev-kit restarts and state.compose_file_path is lost).
        remove_volumes: When True, appends ``--volumes`` to also remove named
            volumes (ChromaDB, Memgraph, kb_data). Defaults to False.

    Returns:
        Dict with keys:
            success (bool): True if docker compose exited with code 0.
            stdout (str): Standard output.
            stderr (str): Standard error.
    """
    cmd = ["docker", "compose"]
    if compose_file_path:
        cmd += ["-f", compose_file_path]
    cmd += ["-p", project_name, "down", "--remove-orphans"]
    if remove_volumes:
        cmd.append("--volumes")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        result = {"success": success, "stdout": stdout.decode(), "stderr": stderr.decode()}
        logger.info(
            "run_compose_down",
            extra={
                "operation": "run_compose_down",
                "status": "success" if success else "failure",
                "project_name": project_name,
                "compose_file": compose_file_path,
                "remove_volumes": remove_volumes,
            },
        )
        return result
    except Exception as exc:
        logger.error(
            "run_compose_down_exception",
            extra={"operation": "run_compose_down", "status": "failure", "error": str(exc)},
        )
        return {"success": False, "stdout": "", "stderr": str(exc)}


async def restart_service(
    compose_file_path: str,
    service_name: str,
    project_name: Optional[str] = None,
) -> Dict:
    """Restart a single service in a Docker Compose project.

    Does not rebuild or redeploy — equivalent to ``docker compose restart <svc>``.
    Only stdout/stderr of the docker command are returned; no secrets are exposed.

    Args:
        compose_file_path: Absolute path to the docker-compose.yml file.
        service_name: Compose service name to restart (e.g. ``knowledge_engine``).
        project_name: Optional Docker Compose project name override.

    Returns:
        Dict with ``success`` (bool), ``stdout`` (str), ``stderr`` (str).
    """
    cmd = ["docker", "compose", "-f", compose_file_path]
    if project_name:
        cmd += ["-p", project_name]
    cmd += ["restart", service_name]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        logger.info(
            "restart_service",
            extra={
                "operation": "restart_service",
                "status": "success" if success else "failure",
                "service": service_name,
            },
        )
        return {"success": success, "stdout": stdout.decode(), "stderr": stderr.decode()}
    except Exception as exc:
        logger.error(
            "restart_service_exception",
            extra={"operation": "restart_service", "status": "failure", "error": str(exc)},
        )
        return {"success": False, "stdout": "", "stderr": str(exc)}


async def get_service_logs(
    compose_file_path: str,
    service_name: str,
    project_name: Optional[str] = None,
    tail: int = 15,
) -> str:
    """Return the last *tail* lines of a service's container logs, ANSI-stripped.

    Only reads container stdout/stderr — never exposes environment variables or
    secrets. Suitable for surfacing startup failure context in the deploy UI.

    Args:
        compose_file_path: Absolute path to the docker-compose.yml file.
        service_name: Compose service name.
        project_name: Optional Docker Compose project name.
        tail: Number of log lines to fetch from the end.

    Returns:
        Cleaned, trimmed log text. Empty string on error.
    """
    cmd = ["docker", "compose", "-f", compose_file_path]
    if project_name:
        cmd += ["-p", project_name]
    cmd += ["logs", "--no-color", f"--tail={tail}", service_name]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        raw = stdout.decode(errors="replace")
        cleaned = _ANSI_ESCAPE.sub("", raw).strip()
        lines = [ln for ln in cleaned.splitlines() if ln.strip()][-tail:]
        return "\n".join(lines)
    except Exception as exc:
        logger.error(
            "get_service_logs_exception",
            extra={"operation": "get_service_logs", "status": "failure", "error": str(exc)},
        )
        return ""


async def list_project_containers(project_name: str) -> List[Dict]:
    """List containers belonging to a docker compose project by label.

    Unlike :func:`get_compose_status`, this does not require a compose file
    or in-memory state — it queries the daemon directly by the standard
    ``com.docker.compose.project`` label, so it can detect a running
    deployment after dev_kit restarts (when the in-memory state is lost).

    Args:
        project_name: Compose project name (e.g. ``dpg-<slug>``).

    Returns:
        List of dicts with keys ``Service`` (compose service name),
        ``State`` (running/exited/...), and ``Status`` (full status line
        including health). Empty list when no containers match or on error.
    """
    cmd = [
        "docker", "ps", "-a",
        "--filter", f"label=com.docker.compose.project={project_name}",
        "--format", "{{json .}}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "list_project_containers",
                extra={
                    "operation": "list_project_containers",
                    "status": "failure",
                    "error": stderr.decode(),
                },
            )
            return []
        results: List[Dict] = []
        for line in stdout.decode().strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Normalise to the same shape get_compose_status returns so
            # callers can treat both sources interchangeably. `docker ps`
            # exposes the compose service name via the Labels field.
            labels = raw.get("Labels", "")
            service = ""
            for kv in labels.split(","):
                if kv.startswith("com.docker.compose.service="):
                    service = kv.split("=", 1)[1]
                    break
            results.append({
                "Service": service or raw.get("Names", ""),
                "Name": raw.get("Names", ""),
                "State": raw.get("State", ""),
                "Status": raw.get("Status", ""),
            })
        return results
    except Exception as exc:
        logger.error(
            "list_project_containers_exception",
            extra={"operation": "list_project_containers", "status": "failure", "error": str(exc)},
        )
        return []


async def get_compose_status(
    compose_file_path: str,
    project_name: Optional[str] = None,
) -> List[Dict]:
    """Retrieve the current status of all services in a Docker Compose project.

    Args:
        compose_file_path: Absolute path to the docker-compose.yml file.
        project_name: Optional Docker Compose project name override.

    Returns:
        List of dicts, each representing one service with keys from
        'docker compose ps --format json'. Returns an empty list on failure.
    """
    cmd = ["docker", "compose", "-f", compose_file_path]
    if project_name:
        cmd += ["-p", project_name]
    cmd += ["ps", "--format", "json"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "get_compose_status",
                extra={
                    "operation": "get_compose_status",
                    "status": "failure",
                    "error": stderr.decode(),
                },
            )
            return []

        raw = stdout.decode().strip()
        if not raw:
            return []

        # docker compose ps --format json may emit one JSON object per line
        results = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results
    except Exception as exc:
        logger.error(
            "get_compose_status_exception",
            extra={"operation": "get_compose_status", "status": "failure", "error": str(exc)},
        )
        return []
