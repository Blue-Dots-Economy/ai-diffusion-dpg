"""Deployer kubeconfig module — kubeconfig parsing and validation utilities.

Part of the dev-kit deployer backend within the DPG framework. Parses and
optionally validates Kubernetes cluster credentials before Helm deployments.
"""

import asyncio
import logging
import tempfile
from typing import Dict

import yaml

logger = logging.getLogger(__name__)

_KUBECTL_TIMEOUT_SECONDS = 10


def parse_kubeconfig(content: str) -> Dict[str, str]:
    """Parse a kubeconfig YAML string and extract essential cluster metadata.

    Args:
        content: Raw kubeconfig YAML string.

    Returns:
        Dict with keys: cluster_name, server, current_context.

    Raises:
        ValueError: If content is not valid YAML, kind is not "Config", or
                    required clusters key is absent.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid kubeconfig YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Invalid kubeconfig: top-level value must be a YAML mapping")

    kind = data.get("kind")
    if kind != "Config":
        raise ValueError(f"Invalid kubeconfig kind: expected 'Config', got '{kind}'")

    clusters = data.get("clusters")
    if not clusters:
        raise ValueError("Invalid kubeconfig: 'clusters' key is missing or empty")

    first_cluster = clusters[0]
    cluster_name = first_cluster.get("name", "")
    server = first_cluster.get("cluster", {}).get("server", "")
    current_context = data.get("current-context", "")

    return {
        "cluster_name": cluster_name,
        "server": server,
        "current_context": current_context,
    }


async def validate_kubeconfig(content: str) -> Dict:
    """Validate a kubeconfig by connecting to the cluster and checking node availability.

    Writes the kubeconfig to a temporary file, then runs kubectl commands to
    verify cluster connectivity and retrieve node count.

    Args:
        content: Raw kubeconfig YAML string.

    Returns:
        Dict with keys:
            valid (bool): True if cluster is reachable and nodes were listed.
            version (str): Server version string, or empty string on failure.
            node_count (int): Number of nodes returned by kubectl get nodes.

    Raises:
        ValueError: If content fails parse_kubeconfig validation.
    """
    parsed = parse_kubeconfig(content)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(content)
        kubeconfig_path = tmp.name

    result: Dict = {**parsed, "valid": False, "version": "", "node_count": 0}

    try:
        version_proc = await asyncio.create_subprocess_exec(
            "kubectl", "version", "--short", "--kubeconfig", kubeconfig_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            version_stdout, _ = await asyncio.wait_for(version_proc.communicate(), timeout=_KUBECTL_TIMEOUT_SECONDS)
            result["version"] = version_stdout.decode().strip()
        except asyncio.TimeoutError:
            logger.warning(
                "validate_kubeconfig_version_timeout",
                extra={"operation": "validate_kubeconfig", "status": "failure", "cluster": parsed["cluster_name"]},
            )
            return result

        nodes_proc = await asyncio.create_subprocess_exec(
            "kubectl", "get", "nodes", "--no-headers", "--kubeconfig", kubeconfig_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            nodes_stdout, _ = await asyncio.wait_for(nodes_proc.communicate(), timeout=_KUBECTL_TIMEOUT_SECONDS)
            lines = [ln for ln in nodes_stdout.decode().splitlines() if ln.strip()]
            result["node_count"] = len(lines)
            result["valid"] = True
        except asyncio.TimeoutError:
            logger.warning(
                "validate_kubeconfig_nodes_timeout",
                extra={"operation": "validate_kubeconfig", "status": "failure", "cluster": parsed["cluster_name"]},
            )

    except Exception as exc:
        logger.error(
            "validate_kubeconfig_error",
            extra={"operation": "validate_kubeconfig", "status": "failure", "error": str(exc)},
        )

    return result
