"""Deployer state module — in-memory deployment state tracking.

Part of the dev-kit deployer backend within the DPG framework. Tracks the
status of each service during an active deployment and exposes a polling
interface consumed by the frontend DeployStatusStep.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DeployState:
    """In-memory tracker for a single project deployment.

    Attributes:
        target: Deployment target — ``docker`` or ``kubernetes``.
        services: Dict mapping service name → status dict with keys
            ``status`` and optional ``error``.
        overall: One of ``deploying``, ``complete``, or ``failed``.
    """

    def __init__(self, target: str) -> None:
        self.target = target
        self.services: Dict[str, Dict] = {}
        self.overall: str = "deploying"
        self.kubeconfig_path: Optional[str] = None
        self.compose_file_path: Optional[str] = None
        self.namespace: str = "dpg"

    def set_service(self, name: str, status: str, error: str = "") -> None:
        """Update status for a single service.

        Args:
            name: Service name.
            status: One of queued, starting, running, failed, healthy.
            error: Error message if status is failed.
        """
        entry = {"status": status}
        if error:
            entry["error"] = error
        self.services[name] = entry

    def to_response(self) -> Dict:
        """Return the state as a dict matching the frontend API contract.

        Returns:
            Dict with ``services`` (list of dicts) and ``overall`` string.
        """
        svc_list = [
            {"name": name, **data}
            for name, data in self.services.items()
        ]
        return {"services": svc_list, "overall": self.overall}


# Active deployments keyed by project slug.
_active: Dict[str, DeployState] = {}


def get_state(slug: str) -> Optional[DeployState]:
    """Return the active deployment state for a project, or None."""
    return _active.get(slug)


def start_deploy(slug: str, target: str) -> DeployState:
    """Create a fresh deployment state for a project.

    Args:
        slug: Project slug.
        target: ``docker`` or ``kubernetes``.

    Returns:
        New DeployState instance registered as the active deployment.
    """
    state = DeployState(target)
    _active[slug] = state
    return state


def clear_state(slug: str) -> None:
    """Remove a project's deployment state (e.g. after cleanup)."""
    _active.pop(slug, None)
