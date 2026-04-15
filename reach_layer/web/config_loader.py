"""
reach_layer/config_loader.py

Config loading utilities for the DPG Reach Layer block.
Extracted from main.py so both the CLI entry point and the FastAPI server
can share the same YAML loading and deep-merge logic.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: str) -> dict:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Relative or absolute path to the YAML file.

    Returns:
        Parsed YAML contents as a dict, or empty dict if file is empty.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, with override values winning on conflicts.

    Dicts at matching keys are merged recursively. All other types are replaced.

    Args:
        base: The base configuration dict.
        override: Values to overlay on top of base.

    Returns:
        New dict with override applied on top of base.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(dpg_path: str, domain_path: str) -> dict:
    """Load and merge DPG framework defaults with domain overrides.

    DPG config is required; domain config is optional (missing file is silently
    skipped so the server can start with framework defaults alone).

    Args:
        dpg_path: Path to the DPG framework YAML (e.g. "config/dpg.yaml").
        domain_path: Path to the domain override YAML (e.g. "config/domain.yaml").

    Returns:
        Merged config dict with domain values overriding DPG defaults.

    Raises:
        FileNotFoundError: If the DPG config file does not exist.
    """
    dpg_config = load_yaml(dpg_path)
    try:
        domain_config = load_yaml(domain_path)
    except FileNotFoundError:
        domain_config = {}
    return deep_merge(dpg_config, domain_config)
