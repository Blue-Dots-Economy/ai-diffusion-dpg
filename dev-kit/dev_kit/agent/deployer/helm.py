"""Deployer helm module — Helm command builders and async execution helpers.

Part of the dev-kit deployer backend within the DPG framework. Constructs Helm
CLI invocations for deploying DPG blocks and infrastructure services to Kubernetes.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Ordered deployment phases.  Each phase groups services that can be rolled
# out together; infra services come first so DPG blocks have their
# dependencies available on startup.
DEPLOY_PHASES: List[Dict] = [
    {
        "name": "storage",
        "services": ["redis", "memgraph"],
    },
    {
        "name": "observability_infra",
        "services": ["otel_collector", "jaeger", "prometheus", "loki", "grafana"],
    },
    {
        "name": "trust",
        "services": ["trust_layer"],
    },
    {
        "name": "memory",
        "services": ["memory_layer"],
    },
    {
        "name": "intelligence",
        "services": ["knowledge_engine", "action_gateway"],
    },
    {
        "name": "core_and_reach",
        "services": ["agent_core", "reach_layer", "observability_layer"],
    },
]


def build_helm_command(
    chart_path: str,
    release_name: str,
    namespace: str,
    kubeconfig_path: str,
    set_values: Optional[Dict[str, str]] = None,
    set_files: Optional[Dict[str, str]] = None,
    values_files: Optional[List[str]] = None,
    upgrade: bool = False,
) -> List[str]:
    """Build a helm install or upgrade command as a list of argument strings.

    Args:
        chart_path: Path to the Helm chart directory or OCI reference.
        release_name: Name of the Helm release.
        namespace: Kubernetes namespace to deploy into.
        kubeconfig_path: Path to the kubeconfig file for cluster auth.
        set_values: Dict of --set key=value pairs.
        set_files: Dict of --set-file key=filepath pairs.
        values_files: List of paths to values YAML files (-f flag).
        upgrade: If True, use 'helm upgrade --install' instead of 'helm install'.

    Returns:
        List of strings forming the complete helm command.
    """
    if upgrade:
        cmd = ["helm", "upgrade", "--install", release_name, chart_path]
    else:
        cmd = ["helm", "install", release_name, chart_path]

    cmd += [
        "--namespace", namespace,
        "--create-namespace",
        "--kubeconfig", kubeconfig_path,
        "--wait",
    ]

    if values_files:
        for vf in values_files:
            cmd += ["-f", vf]

    if set_values:
        for key, value in set_values.items():
            cmd += ["--set", f"{key}={value}"]

    if set_files:
        for key, filepath in set_files.items():
            cmd += ["--set-file", f"{key}={filepath}"]

    return cmd


def build_template_command(
    chart_path: str,
    release_name: str,
    set_values: Optional[Dict[str, str]] = None,
    set_files: Optional[Dict[str, str]] = None,
    values_files: Optional[List[str]] = None,
) -> List[str]:
    """Build a helm template command for dry-run rendering without a cluster.

    Args:
        chart_path: Path to the Helm chart directory or OCI reference.
        release_name: Name of the Helm release used for templating.
        set_values: Dict of --set key=value pairs.
        set_files: Dict of --set-file key=filepath pairs.
        values_files: List of paths to values YAML files (-f flag).

    Returns:
        List of strings forming the complete helm template command.
    """
    cmd = ["helm", "template", release_name, chart_path]

    if values_files:
        for vf in values_files:
            cmd += ["-f", vf]

    if set_values:
        for key, value in set_values.items():
            cmd += ["--set", f"{key}={value}"]

    if set_files:
        for key, filepath in set_files.items():
            cmd += ["--set-file", f"{key}={filepath}"]

    return cmd


async def run_helm_command(cmd: List[str]) -> Dict:
    """Execute a helm command asynchronously and return structured output.

    Args:
        cmd: Full helm command as a list of strings (as produced by
             build_helm_command or build_template_command).

    Returns:
        Dict with keys:
            success (bool): True if the process exited with code 0.
            stdout (str): Standard output from helm.
            stderr (str): Standard error from helm.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        result = {
            "success": success,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }
        log_extra = {
            "operation": "run_helm_command",
            "status": "success" if success else "failure",
            "release": cmd[2] if len(cmd) > 2 else "",
        }
        if success:
            logger.info("run_helm_command", extra=log_extra)
        else:
            logger.error("run_helm_command", extra={**log_extra, "error": result["stderr"]})
        return result
    except Exception as exc:
        logger.error(
            "run_helm_command_exception",
            extra={"operation": "run_helm_command", "status": "failure", "error": str(exc)},
        )
        return {"success": False, "stdout": "", "stderr": str(exc)}


async def get_pod_status(namespace: str, kubeconfig_path: str) -> List[Dict]:
    """Retrieve pod status for a Kubernetes namespace.

    Args:
        namespace: Kubernetes namespace to query.
        kubeconfig_path: Path to the kubeconfig file for cluster auth.

    Returns:
        List of dicts, each with keys: name (str), status (str), ready (bool).
        Returns an empty list if the kubectl call fails.
    """
    cmd = [
        "kubectl", "get", "pods",
        "--namespace", namespace,
        "--kubeconfig", kubeconfig_path,
        "-o", "json",
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
                "get_pod_status",
                extra={
                    "operation": "get_pod_status",
                    "status": "failure",
                    "error": stderr.decode(),
                    "namespace": namespace,
                },
            )
            return []

        data = json.loads(stdout.decode())
        pods = []
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            phase = item.get("status", {}).get("phase", "Unknown")
            conditions = item.get("status", {}).get("conditions", [])
            ready = any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in conditions
            )
            pods.append({"name": name, "status": phase, "ready": ready})
        return pods
    except Exception as exc:
        logger.error(
            "get_pod_status_exception",
            extra={"operation": "get_pod_status", "status": "failure", "error": str(exc)},
        )
        return []
