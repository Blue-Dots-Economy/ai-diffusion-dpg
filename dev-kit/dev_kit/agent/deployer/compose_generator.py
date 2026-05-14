"""Compose generator — selective docker-compose YAML generation from IntakeState.

Part of the dev-kit deterministic wizard (design §6 / §8). Reads the base
docker-compose.dev.yml template, filters services based on the persisted
IntakeState (has_kb, has_external_tools, selected_channels), strips orphaned
depends_on references, and returns a YAML string ready to write to disk and
run via `docker compose -f`.

See:
  docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §6 §8
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from dev_kit.agent.intake_state import IntakeState

__all__ = ["generate_compose"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[4]
_BASE_COMPOSE: Path = _REPO_ROOT / "automation" / "docker" / "docker-compose.dev.yml"

# Maps intake channel names to their docker-compose service names.
# Only channels listed here are subject to optional removal; reach_layer_web
# is always retained (even when "web" is not selected) so the routing proxy
# and ingest endpoint remain available.
_CHANNEL_SERVICE: dict[str, str] = {
    "voice": "reach_layer_voice",
}

# Service that is never included in a deployed compose (dev-kit manages itself).
_ALWAYS_REMOVE: frozenset[str] = frozenset({"dev_kit"})


def generate_compose(
    intake_state: IntakeState,
    project_slug: str,
    *,
    base_compose_path: Path | None = None,
) -> str:
    """Render a docker-compose YAML string with services filtered by IntakeState.

    Reads the base template at ``base_compose_path`` (defaults to
    ``automation/docker/docker-compose.dev.yml``), filters services per intake,
    sets per-service environment vars per intake, and returns the YAML string.

    Filtering rules applied in order:
    1. ``dev_kit`` is always removed (it does not deploy itself).
    2. ``knowledge_engine`` is removed when ``intake_state.has_kb is False``.
    3. ``action_gateway`` is removed when ``intake_state.has_external_tools is False``.
    4. ``reach_layer_voice`` is removed when ``"voice"`` is not in
       ``intake_state.selected_channels``.
    5. ``ngrok`` is removed when voice is not selected (it tunnels port 8006).
    6. ``reach_layer_web`` is always kept; its ``REACH_LAYER_WEB_MODE`` env var
       is set to ``full`` when ``"web"`` is in ``selected_channels``, or
       ``routing_only`` otherwise.
    7. ``depends_on`` entries in every remaining service that reference a removed
       service are stripped so compose does not error on missing dependencies.
    8. ``pull_policy: missing`` is set on every service that has an ``image`` key.
    9. ``container_name`` is removed from every service.

    Args:
        intake_state: The persisted IntakeState (12 fields).
        project_slug: Substituted for ``${DOMAIN}`` / ``${DOMAIN:-kkb}`` placeholders
            in the template volume paths and env vars.
        base_compose_path: Optional override for the base compose template path.
            When ``None``, defaults to ``_BASE_COMPOSE``.

    Returns:
        A YAML string ready to be written to disk and run via ``docker compose -f``.

    Raises:
        FileNotFoundError: If the base compose template does not exist.
        ValueError: If the template YAML cannot be parsed.
    """
    resolved_path = base_compose_path if base_compose_path is not None else _BASE_COMPOSE

    if not resolved_path.exists():
        raise FileNotFoundError(
            f"Base compose template not found: {resolved_path}"
        )

    raw = resolved_path.read_text(encoding="utf-8")

    # Substitute ${DOMAIN:-kkb} and ${DOMAIN} placeholders with the project slug.
    content = raw.replace("${DOMAIN:-kkb}", project_slug).replace("${DOMAIN}", project_slug)

    try:
        compose_doc: dict = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse base compose template {resolved_path}: {exc}") from exc

    services: dict = compose_doc.get("services", {})

    # ------------------------------------------------------------------
    # 1. Determine which services to remove based on IntakeState.
    # ------------------------------------------------------------------
    services_to_remove: set[str] = set(_ALWAYS_REMOVE)

    if not intake_state.has_kb:
        services_to_remove.add("knowledge_engine")

    if not intake_state.has_external_tools:
        services_to_remove.add("action_gateway")

    effective_channels: set[str] = set(intake_state.selected_channels)

    # Remove channel-specific services for unselected channels.
    for channel, svc_name in _CHANNEL_SERVICE.items():
        if channel not in effective_channels:
            services_to_remove.add(svc_name)

    # ngrok tunnels port 8006 (reach_layer_voice); remove it when voice is absent.
    if "voice" not in effective_channels:
        services_to_remove.add("ngrok")

    # ------------------------------------------------------------------
    # 2. Remove services and apply per-service tweaks to survivors.
    # ------------------------------------------------------------------
    # Point 14: determine reason per removed service before iterating
    _exclude_reasons: dict[str, str] = {}
    if intake_state.has_kb is False and "knowledge_engine" in services_to_remove:
        _exclude_reasons["knowledge_engine"] = "has_kb=false"
    if intake_state.has_external_tools is False and "action_gateway" in services_to_remove:
        _exclude_reasons["action_gateway"] = "has_external_tools=false"
    if "voice" not in set(intake_state.selected_channels):
        if "reach_layer_voice" in services_to_remove:
            _exclude_reasons["reach_layer_voice"] = "voice_not_in_selected_channels"
        if "ngrok" in services_to_remove:
            _exclude_reasons["ngrok"] = "voice_not_in_selected_channels"
    for _always in _ALWAYS_REMOVE:
        if _always in services_to_remove:
            _exclude_reasons[_always] = "always_excluded"

    for svc_name in list(services.keys()):
        if svc_name in services_to_remove:
            # Point 14: log service excluded
            _reason = _exclude_reasons.get(svc_name, "excluded")
            logger.info(
                "compose_generator.service_decision",
                extra={
                    "operation": "compose_generator.service_decision",
                    "status": "success",
                    "service": svc_name,
                    "included": False,
                    "reason": _reason,
                },
            )
            del services[svc_name]
            continue

        svc: dict = services[svc_name]

        # Drop container_name — let compose generate per-project names.
        svc.pop("container_name", None)

        # Force pull_policy=missing on every image service to avoid re-pulling
        # cached images on redeploy and prevent Docker Hub rate-limit failures.
        if "image" in svc:
            svc["pull_policy"] = "missing"

        # Set REACH_LAYER_WEB_MODE based on whether "web" was selected.
        if svc_name == "reach_layer_web":
            web_mode = "full" if "web" in effective_channels else "routing_only"
            env_list: list = svc.setdefault("environment", [])
            # Remove any existing REACH_LAYER_WEB_MODE entry before appending.
            env_list[:] = [
                e for e in env_list
                if not (isinstance(e, str) and e.startswith("REACH_LAYER_WEB_MODE="))
            ]
            env_list.append(f"REACH_LAYER_WEB_MODE={web_mode}")

        # Point 14: log service included
        logger.info(
            "compose_generator.service_decision",
            extra={
                "operation": "compose_generator.service_decision",
                "status": "success",
                "service": svc_name,
                "included": True,
                "reason": "selected",
            },
        )

    # ------------------------------------------------------------------
    # 3. Strip depends_on references to removed services.
    # ------------------------------------------------------------------
    for svc_name, svc in services.items():
        depends_on = svc.get("depends_on")
        if depends_on is None:
            continue

        if isinstance(depends_on, list):
            # Plain list form: ["redis", "knowledge_engine"]
            filtered = [dep for dep in depends_on if dep not in services_to_remove]
            if filtered:
                svc["depends_on"] = filtered
            else:
                del svc["depends_on"]

        elif isinstance(depends_on, dict):
            # Long form: {knowledge_engine: {condition: service_healthy}, ...}
            filtered_dict = {
                dep: cfg
                for dep, cfg in depends_on.items()
                if dep not in services_to_remove
            }
            if filtered_dict:
                svc["depends_on"] = filtered_dict
            else:
                del svc["depends_on"]

    logger.info(
        "generate_compose",
        extra={
            "operation": "generate_compose",
            "status": "success",
            "project_slug": project_slug,
            "removed_services": sorted(services_to_remove & set(_services_in_raw(raw))),
            "remaining_services": sorted(services.keys()),
        },
    )

    return yaml.safe_dump(compose_doc, default_flow_style=False, sort_keys=False)


def _services_in_raw(raw: str) -> list[str]:
    """Return the list of service names present in raw compose YAML text.

    Args:
        raw: Raw YAML string of a docker-compose file.

    Returns:
        List of service names found under the ``services`` key, or an empty
        list if the YAML cannot be parsed or has no ``services`` section.
    """
    try:
        doc = yaml.safe_load(raw)
        return list((doc or {}).get("services", {}).keys())
    except yaml.YAMLError:
        return []
