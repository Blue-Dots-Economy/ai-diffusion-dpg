"""Deployer dependencies module — infrastructure service configuration from Helm charts.

Part of the dev-kit deployer backend within the DPG framework. Reads and writes
infrastructure service configurations directly from the Helm chart values.yaml
files at automation/helm/infra/. All edits persist to disk.
"""

import logging
from pathlib import Path
from typing import Dict

import yaml

logger = logging.getLogger(__name__)

# Path to the Helm infra charts directory.
# Supports both local dev (../automation/helm/infra relative to repo root)
# and Docker (mounted at /app/automation/helm/infra).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_DOCKER_AUTOMATION = Path("/app/automation/helm/infra")
HELM_INFRA_DIR = _DOCKER_AUTOMATION if _DOCKER_AUTOMATION.exists() else _REPO_ROOT / "automation" / "helm" / "infra"

# Mapping from logical service name to Helm chart directory name.
SERVICE_CHART_MAP: Dict[str, str] = {
    "redis": "redis",
    "memgraph": "memgraph",
    "otel_collector": "otel-collector",
    "jaeger": "jaeger",
    "prometheus": "prometheus",
    "loki": "loki",
    "grafana": "grafana",
}


def get_service_names() -> list[str]:
    """Return the list of known infrastructure service names.

    Returns:
        Sorted list of service name strings.
    """
    return sorted(SERVICE_CHART_MAP.keys())


def get_service_config(name: str) -> str:
    """Read the current values.yaml for an infrastructure service from its Helm chart.

    Args:
        name: Infrastructure service name (e.g. "redis", "memgraph").

    Returns:
        Raw YAML string contents of the chart's values.yaml.

    Raises:
        ValueError: If name does not match any known infrastructure service.
    """
    if name not in SERVICE_CHART_MAP:
        raise ValueError(f"Unknown infrastructure service: '{name}'. Known services: {get_service_names()}")

    chart_dir = SERVICE_CHART_MAP[name]
    values_path = HELM_INFRA_DIR / chart_dir / "values.yaml"
    if not values_path.exists():
        logger.warning(
            "values_yaml_missing",
            extra={"operation": "get_service_config", "status": "skipped", "service": name, "path": str(values_path)},
        )
        return ""

    return values_path.read_text()


def get_defaults() -> Dict[str, Dict]:
    """Return parsed configuration for all infrastructure services.

    Reads each service's values.yaml and parses it. Used by the GET endpoint
    to provide both raw YAML (via get_service_config) and parsed defaults.

    Returns:
        Dict mapping each service name to its parsed YAML configuration dict.
    """
    result = {}
    for name in SERVICE_CHART_MAP:
        config_text = get_service_config(name)
        if config_text:
            try:
                result[name] = yaml.safe_load(config_text) or {}
            except yaml.YAMLError:
                result[name] = {}
        else:
            result[name] = {}
    return result


def update_service_config(name: str, yaml_str: str) -> None:
    """Write updated configuration back to the Helm chart's values.yaml.

    Args:
        name: Infrastructure service name to update.
        yaml_str: YAML string containing the new configuration values.

    Raises:
        ValueError: If name is not a known infrastructure service.
        ValueError: If yaml_str cannot be parsed as valid YAML.
    """
    if name not in SERVICE_CHART_MAP:
        raise ValueError(f"Unknown infrastructure service: '{name}'. Known services: {get_service_names()}")

    try:
        parsed = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML for service '{name}': {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"Service config for '{name}' must be a YAML mapping, got {type(parsed).__name__}")

    chart_dir = SERVICE_CHART_MAP[name]
    values_path = HELM_INFRA_DIR / chart_dir / "values.yaml"
    values_path.write_text(yaml_str)

    logger.info(
        "update_service_config",
        extra={"operation": "update_service_config", "status": "success", "service": name, "path": str(values_path)},
    )
