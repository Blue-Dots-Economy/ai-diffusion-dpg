"""Deployer compose module — Docker Compose manifest generation and execution.

Part of the dev-kit deployer backend within the DPG framework. Generates a
complete docker-compose.yml for local and dev deployments of all 14 services
(7 DPG blocks + 7 infrastructure services).
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# DPG application blocks: name → {image template, internal port}
DPG_SERVICES: Dict[str, Dict] = {
    "agent_core": {"image": "sanketikahub/dpg-agent-core:latest", "port": 8000},
    "knowledge_engine": {"image": "sanketikahub/dpg-knowledge-engine:latest", "port": 8001},
    "memory_layer": {"image": "sanketikahub/dpg-memory-layer:latest", "port": 8002},
    "trust_layer": {"image": "sanketikahub/dpg-trust-layer:latest", "port": 8003},
    "observability_layer": {"image": "sanketikahub/dpg-observability-layer:latest", "port": 8004},
    "action_gateway": {"image": "sanketikahub/dpg-action-gateway:latest", "port": 9999},
    "reach_layer": {"image": "sanketikahub/dpg-reach-layer:latest", "port": 8005},
}

# Infrastructure service images
INFRA_IMAGES: Dict[str, str] = {
    "redis": "redis:7-alpine",
    "memgraph": "memgraph/memgraph:2.14.0",
    "otel_collector": "otel/opentelemetry-collector-contrib:0.96.0",
    "jaeger": "jaegertracing/all-in-one:1.55",
    "prometheus": "prom/prometheus:v2.50.1",
    "loki": "grafana/loki:2.9.4",
    "grafana": "grafana/grafana:10.3.3",
}

# Service startup dependency graph
DEPENDS_ON: Dict[str, List[str]] = {
    "agent_core": ["redis", "memgraph", "trust_layer", "knowledge_engine", "action_gateway"],
    "knowledge_engine": ["redis"],
    "memory_layer": ["redis", "memgraph"],
    "trust_layer": [],
    "observability_layer": ["otel_collector", "prometheus", "loki"],
    "action_gateway": [],
    "reach_layer": ["agent_core"],
    "redis": [],
    "memgraph": [],
    "otel_collector": [],
    "jaeger": ["otel_collector"],
    "prometheus": [],
    "loki": [],
    "grafana": ["prometheus", "loki"],
}

# Config file mount paths inside each DPG container
_DPG_CONFIG_MOUNT = "/app/config/dpg.yaml"
# Domain config is mounted as /app/config/{block_name}.yaml so each block's
# config_loader.py resolves it via CONFIG_FOLDER/{block_name}.yaml.
_DOMAIN_CONFIG_TEMPLATE = "/app/config/{block_name}.yaml"


def generate_compose(
    project_slug: str,
    dpg_dir: str,
    domain_dir: str,
    resources: Dict[str, Dict],
    secrets: Dict[str, str],
    infra_configs: Dict[str, Dict],
) -> str:
    """Generate a complete docker-compose.yml YAML string for a DPG deployment.

    Produces a manifest with all 14 services (7 DPG blocks + 7 infra services),
    volume mounts for DPG and domain config files, environment variables from
    secrets, health checks, service dependencies, resource limits, and a shared
    bridge network.

    Args:
        project_slug: Short identifier used to namespace volumes and network.
        dpg_dir: Host path containing per-block dpg.yaml config files.
        domain_dir: Host path containing per-block domain.yaml config files.
        resources: Dict mapping block name to {limits: {cpu, memory}} overrides.
        secrets: Dict of secret key→value pairs injected as env vars into DPG blocks.
        infra_configs: Dict mapping infra service name to override config values.

    Returns:
        YAML string of the complete docker-compose manifest.
    """
    services: Dict = {}

    # Build infra service definitions
    for svc_name, image in INFRA_IMAGES.items():
        override = infra_configs.get(svc_name, {})
        svc: Dict = {
            "image": override.get("image", image),
            "restart": "unless-stopped",
            "networks": ["dpg_net"],
        }

        deps = DEPENDS_ON.get(svc_name, [])
        if deps:
            svc["depends_on"] = deps

        services[svc_name] = svc

    # Build DPG block service definitions
    for block_name, block_info in DPG_SERVICES.items():
        svc_image = block_info["image"]
        svc_port = block_info["port"]

        env: List[str] = []
        # Inject secrets as environment variables
        if secrets.get("anthropic_api_key"):
            env.append(f"ANTHROPIC_API_KEY={secrets['anthropic_api_key']}")
        for key, value in secrets.items():
            if key == "anthropic_api_key":
                continue
            env.append(f"{key.upper()}={value}")

        domain_mount = _DOMAIN_CONFIG_TEMPLATE.format(block_name=block_name)
        volumes = [
            f"{dpg_dir}/{block_name}/dpg.yaml:{_DPG_CONFIG_MOUNT}:ro",
            f"{domain_dir}/{block_name}/domain.yaml:{domain_mount}:ro",
        ]

        svc: Dict = {
            "image": svc_image,
            "restart": "unless-stopped",
            "ports": [f"{svc_port}:{svc_port}"],
            "environment": env,
            "volumes": volumes,
            "networks": ["dpg_net"],
        }

        deps = DEPENDS_ON.get(block_name, [])
        if deps:
            svc["depends_on"] = deps

        # Apply resource limits if provided
        block_resources = resources.get(block_name, {})
        limits = block_resources.get("limits", {})
        if limits:
            svc["deploy"] = {
                "resources": {
                    "limits": {
                        k: v for k, v in limits.items()
                    }
                }
            }

        services[block_name] = svc

    compose_doc = {
        "version": "3.9",
        "services": services,
        "networks": {
            "dpg_net": {
                "driver": "bridge",
            }
        },
    }

    return yaml.dump(compose_doc, default_flow_style=False, sort_keys=False)


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
        if secrets.get("anthropic_api_key"):
            env["ANTHROPIC_API_KEY"] = secrets["anthropic_api_key"]
        if secrets.get("memgraph_password"):
            env["MEMGRAPH_PASSWORD"] = secrets["memgraph_password"]
        if secrets.get("redis_password"):
            # Build password-authenticated URL; the compose file interpolates REDIS_URL.
            env["REDIS_URL"] = f"redis://:{secrets['redis_password']}@redis:6379/0"
        if secrets.get("grafana_admin_password"):
            env["GF_SECURITY_ADMIN_PASSWORD"] = secrets["grafana_admin_password"]
        if secrets.get("google_client_id"):
            env["GOOGLE_CLIENT_ID"] = secrets["google_client_id"]
        if secrets.get("reach_session_secret"):
            env["REACH_SESSION_SECRET"] = secrets["reach_session_secret"]
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
        if secrets.get("ke_devkit_callback_url"):
            env["KE_DEVKIT_CALLBACK_URL"] = secrets["ke_devkit_callback_url"]
        # Azure Blob Storage — resolves ${VAR:-} placeholders in the compose file
        _azure = {
            "azure_storage_account": "AZURE_STORAGE_ACCOUNT",
            "azure_storage_key": "AZURE_STORAGE_KEY",
            "azure_container_name": "AZURE_CONTAINER_NAME",
        }
        for secret_key, env_var in _azure.items():
            if secrets.get(secret_key):
                env[env_var] = secrets[secret_key]

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
