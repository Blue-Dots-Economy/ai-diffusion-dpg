"""Deployer presets module — resource tier definitions for all 7 DPG blocks.

Part of the dev-kit deployer backend within the DPG framework.
"""

import copy
from typing import Dict

# Resource spec shape: {requests: {cpu, memory}, limits: {cpu, memory}}
# CPU values use millicores (m) notation for consistent parsing.
# Agent Core and Knowledge Engine receive elevated resources as they are
# the primary compute-intensive blocks (LLM orchestration and RAG retrieval).

PRESETS: Dict[str, Dict[str, Dict]] = {
    # "low" is the small-VM baseline. Its limits match the values in
    # ``automation/docker/docker-compose.yml`` so the deploy-time
    # overlay (``_apply_resources_to_compose``) doesn't accidentally
    # widen the envelope set by the source compose. Anyone who wants
    # larger limits picks ``medium`` or ``high`` explicitly.
    # When the source compose is tuned (e.g. PR #336), this preset
    # MUST be updated in lockstep — otherwise the per-project
    # generated compose silently keeps the old larger limits.
    "low": {
        "agent_core": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "knowledge_engine": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "memory_layer": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "256Mi"},
        },
        "trust_layer": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "256Mi"},
        },
        "action_gateway": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "reach_layer": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "250m", "memory": "256Mi"},
        },
        "observability_layer": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "100m", "memory": "256Mi"},
        },
    },
    "medium": {
        "agent_core": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "knowledge_engine": {
            "requests": {"cpu": "500m", "memory": "1.5Gi"},
            "limits": {"cpu": "1500m", "memory": "3Gi"},
        },
        "memory_layer": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "trust_layer": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "action_gateway": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "reach_layer": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "observability_layer": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
    },
    "high": {
        "agent_core": {
            "requests": {"cpu": "500m", "memory": "2Gi"},
            "limits": {"cpu": "2000m", "memory": "4Gi"},
        },
        "knowledge_engine": {
            "requests": {"cpu": "1000m", "memory": "2Gi"},
            "limits": {"cpu": "2500m", "memory": "4Gi"},
        },
        "memory_layer": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "trust_layer": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "action_gateway": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "reach_layer": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "observability_layer": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
    },
}


def apply_preset(tier: str) -> Dict[str, Dict]:
    """Return a deep copy of the resource map for the given tier.

    Args:
        tier: One of "low", "medium", or "high".

    Returns:
        Dict mapping each DPG block name to its requests/limits resource spec.

    Raises:
        ValueError: If tier is not one of the known preset names.
    """
    if tier not in PRESETS:
        raise ValueError(f"Unknown preset tier: '{tier}'. Valid options: {sorted(PRESETS.keys())}")
    return copy.deepcopy(PRESETS[tier])
