"""
reach_layer/base/config_loader.py

Shared configuration loader for every Reach Layer channel service (cli, web,
voice). Replaces the per-channel ``config_loader.py`` modules that used to live
alongside each service.

All three channel services read from a single pair of files at the Reach-Layer
root (``reach_layer/config/{dpg,domain}.yaml``) — or, at deploy time, from
volume-mounted copies of ``dev-kit/dpg/reach_layer.yaml`` and
``dev-kit/configs/<domain>/reach_layer.yaml``. Both files share the same
schema::

    reach_layer:
      common:
        agent_core_client: {...}
        memory_layer_client: {...}
        observability: {...}
      channels:
        cli:   { enabled, assembly_mode, ... }
        web:   { enabled, assembly_mode, server, auth, ui, sessions, ... }
        voice: { enabled, assembly_mode, port, vobiz, vad, raya, ... }

Each service calls ``load_reach_config("<channel_name>")`` and receives the
fully merged config. To keep the blast radius of the PR small, the loader also
injects a handful of legacy top-level aliases (``agent_core_client``, ``ui``,
``telephony_adapter``, …) so existing service code does not have to be rewritten
to read from the new nested paths. Over time the aliases can be removed as
call-sites migrate to the nested shape.

Design decisions not in the spec:

1. Env-var expansion (``${VAR}`` / ``${VAR:-default}``) — borrowed from the
   voice channel's previous loader. Needed for deploy-time injection of
   secrets like ``RAYA_API_KEY`` and ``PUBLIC_URL``.

2. Legacy aliases — keeps existing in-service config lookups (e.g. voice's
   28-file use of ``config["telephony_adapter"][...]``) working without a
   rename cascade. New code should prefer the nested paths.

3. ``enabled: false`` raises ``ChannelDisabledError`` — each service refuses to
   start when its own section is disabled, providing a single switch for
   selective deployment that sits alongside docker-compose profiles.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


class ChannelDisabledError(RuntimeError):
    """Raised when a channel's ``enabled`` flag is False at startup."""


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` placeholders.

    Missing variables without a default are left as-is so the caller can
    decide whether to treat them as required. Only string scalars are
    inspected; other types are returned unchanged.
    """
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            value = os.environ.get(m.group(1))
            if value is not None:
                return value
            return m.group(2) if m.group(2) is not None else m.group(0)
        return _ENV_VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(i) for i in obj]
    return obj


def load_yaml(path: str) -> dict:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Relative or absolute path to the YAML file.

    Returns:
        Parsed YAML contents with env-var placeholders expanded; empty dict
        for an empty file.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        data = yaml.safe_load(f) or {}
    return _expand_env_vars(data)


def deep_merge(base: dict, override: dict) -> dict:
    """Merge ``override`` into ``base`` without mutating either input.

    Dicts at matching keys are merged recursively. All other types are
    replaced outright by the override value.

    Args:
        base: Framework-level defaults dict.
        override: Domain-level overrides dict.

    Returns:
        A new dict containing the merged result.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(dpg_path: str, domain_path: str) -> dict:
    """Load and deep-merge DPG defaults with domain overrides.

    Preserved for the narrow use case of test fixtures and tools that want the
    raw merged config without channel-scoping. New code should call
    :func:`load_reach_config` instead.

    Args:
        dpg_path: Path to the DPG framework YAML.
        domain_path: Path to the domain override YAML. Missing file is
            silently treated as an empty override.

    Returns:
        Merged config dict.

    Raises:
        FileNotFoundError: If ``dpg_path`` does not exist.
    """
    dpg_config = load_yaml(dpg_path)
    try:
        domain_config = load_yaml(domain_path)
    except FileNotFoundError:
        domain_config = {}
    return deep_merge(dpg_config, domain_config)


def _inject_legacy_aliases(merged: dict, channel_name: str) -> None:
    """Inject backward-compatibility aliases at the top level.

    Existing service code reads from the legacy top-level keys
    (``agent_core_client``, ``ui``, ``telephony_adapter``, …). The new config
    file stores these under ``reach_layer.common`` or ``reach_layer.channels.<name>``
    for a cleaner schema. To avoid a large rename cascade, copy references
    into the legacy positions. ``setdefault`` is used so explicit top-level
    entries (if any) win over the derived alias.
    """
    reach_layer = merged.setdefault("reach_layer", {})
    common = reach_layer.get("common", {}) or {}
    channels = reach_layer.get("channels", {}) or {}
    channel_cfg = channels.get(channel_name, {}) or {}

    if isinstance(common.get("agent_core_client"), dict):
        merged.setdefault("agent_core_client", common["agent_core_client"])
    if isinstance(common.get("memory_layer_client"), dict):
        merged.setdefault("memory_layer_client", common["memory_layer_client"])
    if isinstance(common.get("observability"), dict):
        merged.setdefault("observability", common["observability"])

    if channel_name == "web":
        for key in ("auth", "ui", "server"):
            if isinstance(channel_cfg.get(key), dict):
                merged.setdefault(key, channel_cfg[key])
        if isinstance(channel_cfg.get("sessions"), dict):
            reach_layer.setdefault("sessions", channel_cfg["sessions"])
        if isinstance(channel_cfg.get("ke_internal_url"), str):
            merged.setdefault("ke_internal_url", channel_cfg["ke_internal_url"])

    if channel_name == "voice":
        # Voice's 28-file codebase reads from config["telephony_adapter"][...];
        # alias the voice channel section there for compatibility.
        merged.setdefault("telephony_adapter", channel_cfg)


def load_reach_config(
    channel_name: str,
    dpg_path: str | None = None,
    domain_path: str | None = None,
) -> dict:
    """Load the unified Reach Layer config and scope it to a single channel.

    Args:
        channel_name: Which channel the caller is — one of ``"cli"``,
            ``"web"``, ``"voice"``.
        dpg_path: Optional override for the framework defaults path. When
            omitted, uses ``config/dpg.yaml`` relative to the caller's CWD.
        domain_path: Optional override for the domain YAML path. When omitted,
            uses ``config/domain.yaml`` relative to the caller's CWD.

    Returns:
        Fully merged config dict with legacy top-level aliases injected.
        ``reach_layer.channels.<channel_name>`` is guaranteed to exist (possibly
        empty) so downstream ``.get()`` chains never fail on the missing key.

    Raises:
        ValueError: If ``channel_name`` is empty.
        FileNotFoundError: If ``dpg_path`` does not exist.
        ChannelDisabledError: If the resolved channel section has ``enabled: false``.
    """
    if not channel_name or not channel_name.strip():
        raise ValueError("channel_name must not be empty")

    dpg_path = dpg_path or "config/dpg.yaml"
    domain_path = domain_path or "config/domain.yaml"

    merged = load_config(dpg_path, domain_path)

    # Strict schema check on the full merged config — unknown keys, wrong
    # types, or out-of-range values at any depth fail here at startup.
    # Runs **before** legacy alias injection so the aliases are not
    # subject to duplicate validation.
    from .schema.config import MergedConfig
    MergedConfig.validate_full(merged)

    reach_layer = merged.setdefault("reach_layer", {})
    channels = reach_layer.setdefault("channels", {})
    channel_cfg = channels.setdefault(channel_name, {})

    if channel_cfg.get("enabled", True) is False:
        raise ChannelDisabledError(
            f"channel '{channel_name}' has enabled: false in config; "
            f"refusing to start. Remove the flag or flip to true."
        )

    _inject_legacy_aliases(merged, channel_name)
    return merged
