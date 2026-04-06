"""
observability_layer/src/dpg_telemetry/resource.py

Builds the OTel Resource for a DPG block service.
Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

from opentelemetry.sdk.resources import Resource


def build_resource(service_name: str, config: dict) -> Resource:
    """Build an OTel Resource with DPG-standard attributes.

    Args:
        service_name: The block's service name (e.g. "trust_layer").
        config: Full merged config dict for the service.

    Returns:
        Resource with service.name, dpg.block, dpg.domain, and service.version.
    """
    obs_cfg = (config or {}).get("observability", {})
    domain = obs_cfg.get("domain", "unknown")
    version = obs_cfg.get("service_version", "0.1.0")
    return Resource.create({
        "service.name": service_name,
        "dpg.block": service_name,
        "dpg.domain": domain,
        "service.version": version,
    })
