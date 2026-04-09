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
    "low": {
        "agent_core": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "500m", "memory": "1Gi"},
        },
        "knowledge_engine": {
            "requests": {"cpu": "250m", "memory": "1Gi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
        "memory_layer": {
            "requests": {"cpu": "50m", "memory": "256Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "trust_layer": {
            "requests": {"cpu": "50m", "memory": "256Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "action_gateway": {
            "requests": {"cpu": "50m", "memory": "256Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "reach_layer": {
            "requests": {"cpu": "50m", "memory": "256Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
        },
        "observability_layer": {
            "requests": {"cpu": "50m", "memory": "256Mi"},
            "limits": {"cpu": "250m", "memory": "512Mi"},
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
