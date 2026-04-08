"""
telephony_adapter/config_loader.py

Config loading utilities for the telephony adapter.
Loads framework defaults and domain overrides using the same deep-merge
pattern shared across all DPG blocks.
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

    Args:
        base: The base configuration dict.
        override: Values to overlay on top of base.

    Returns:
        New dict with override applied on top of base. Does not mutate inputs.
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

    Args:
        dpg_path: Path to the framework YAML defaults.
        domain_path: Path to the domain override YAML.

    Returns:
        Merged config dict. Domain values override DPG defaults.

    Raises:
        FileNotFoundError: If dpg_path does not exist.
    """
    dpg_config = load_yaml(dpg_path)
    try:
        domain_config = load_yaml(domain_path)
    except FileNotFoundError:
        domain_config = {}
    return deep_merge(dpg_config, domain_config)
