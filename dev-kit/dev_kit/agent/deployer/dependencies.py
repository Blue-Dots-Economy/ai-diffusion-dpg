"""Deployer dependencies module — infrastructure service definitions and configuration.

Part of the dev-kit deployer backend within the DPG framework. Manages the 7
infrastructure services required to run the DPG stack (Redis, Memgraph, OTel
Collector, Jaeger, Prometheus, Loki, Grafana).
"""

import copy
import logging
from typing import Dict

import yaml

logger = logging.getLogger(__name__)

# Canonical definitions for all 7 infrastructure services.
# Each entry contains image spec, port mappings, resource defaults, and
# service-specific configuration fields.
INFRA_SERVICES: Dict[str, Dict] = {
    "redis": {
        "image": {
            "repository": "redis",
            "tag": "7-alpine",
        },
        "service": {
            "port": 6379,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "256Mi"},
        },
        "password": "",
        "persistence": {
            "enabled": True,
            "size": "1Gi",
        },
    },
    "memgraph": {
        "image": {
            "repository": "memgraph/memgraph",
            "tag": "2.14.0",
        },
        "service": {
            "boltPort": 7687,
            "httpPort": 7444,
        },
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "persistence": {
            "enabled": True,
            "size": "2Gi",
        },
    },
    "otel_collector": {
        "image": {
            "repository": "otel/opentelemetry-collector-contrib",
            "tag": "0.96.0",
        },
        "service": {
            "grpcPort": 4317,
            "httpPort": 4318,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "256Mi"},
        },
    },
    "jaeger": {
        "image": {
            "repository": "jaegertracing/all-in-one",
            "tag": "1.55",
        },
        "service": {
            "uiPort": 16686,
            "collectorPort": 14268,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "200m", "memory": "512Mi"},
        },
        "flags": {
            "spanStorageType": "memory",
        },
    },
    "prometheus": {
        "image": {
            "repository": "prom/prometheus",
            "tag": "v2.50.1",
        },
        "service": {
            "port": 9090,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "persistence": {
            "enabled": True,
            "size": "5Gi",
        },
    },
    "loki": {
        "image": {
            "repository": "grafana/loki",
            "tag": "2.9.4",
        },
        "service": {
            "port": 3100,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "persistence": {
            "enabled": True,
            "size": "5Gi",
        },
    },
    "grafana": {
        "image": {
            "repository": "grafana/grafana",
            "tag": "10.3.3",
        },
        "service": {
            "port": 3000,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "adminPassword": "admin",
        "persistence": {
            "enabled": True,
            "size": "1Gi",
        },
    },
}

# Runtime overrides applied by update_service_config(). Keyed by service name.
_overrides: Dict[str, Dict] = {}


def get_defaults() -> Dict[str, Dict]:
    """Return a deep copy of all infrastructure service default configurations.

    Returns:
        Dict mapping each service name to its full default configuration dict.
    """
    return copy.deepcopy(INFRA_SERVICES)


def get_service_config(name: str) -> str:
    """Return the current YAML configuration string for a named infrastructure service.

    Merges the canonical default with any runtime overrides applied via
    update_service_config().

    Args:
        name: Infrastructure service name (e.g. "redis", "memgraph").

    Returns:
        YAML-encoded string of the merged service configuration.

    Raises:
        ValueError: If name does not match any known infrastructure service.
    """
    if name not in INFRA_SERVICES:
        raise ValueError(f"Unknown infrastructure service: '{name}'. Known services: {sorted(INFRA_SERVICES.keys())}")

    config = copy.deepcopy(INFRA_SERVICES[name])
    if name in _overrides:
        config.update(_overrides[name])

    return yaml.dump(config, default_flow_style=False)


def update_service_config(name: str, yaml_str: str) -> None:
    """Apply a YAML configuration override for a named infrastructure service.

    The override is stored in memory and takes precedence when get_service_config()
    is called. Does not persist across process restarts.

    Args:
        name: Infrastructure service name to override.
        yaml_str: YAML string containing the new configuration values.

    Raises:
        ValueError: If name is not a known infrastructure service.
        ValueError: If yaml_str cannot be parsed as valid YAML.
    """
    if name not in INFRA_SERVICES:
        raise ValueError(f"Unknown infrastructure service: '{name}'. Known services: {sorted(INFRA_SERVICES.keys())}")

    try:
        parsed = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML for service '{name}': {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"Service config for '{name}' must be a YAML mapping, got {type(parsed).__name__}")

    _overrides[name] = parsed
    logger.info(
        "update_service_config",
        extra={"operation": "update_service_config", "status": "success", "service": name},
    )
