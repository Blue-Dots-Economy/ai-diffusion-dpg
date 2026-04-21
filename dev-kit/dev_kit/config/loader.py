"""
dev-kit/dev_kit/config/loader.py

Dev-kit operational config loader.

Reads devkit.yaml once at startup and exposes a DevKitConfig dataclass.
Never re-reads config in request paths.

Belongs to the Dev-Kit tool of the DPG framework.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "devkit.yaml"


@dataclass
class UploadConfig:
    """Upload limits and supported file types."""

    max_files_per_upload: int = 5
    max_file_size_mb: int = 30
    supported_extensions: list[str] = field(
        default_factory=lambda: [".pdf", ".txt", ".md", ".csv", ".docx", ".html"]
    )


@dataclass
class PollingConfig:
    """Frontend polling parameters."""

    poll_interval_seconds: int = 5
    poll_timeout_minutes: int = 15


@dataclass
class DevKitConfig:
    """Top-level dev-kit operational config."""

    user_id: str = "devkit-operator"
    upload: UploadConfig = field(default_factory=UploadConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)


def load_devkit_config(path: Optional[Path] = None) -> DevKitConfig:
    """Load DevKitConfig from YAML.

    Reads from DEVKIT_CONFIG_PATH env var if set, otherwise uses the
    bundled devkit.yaml at dev-kit/dev_kit/config/devkit.yaml.

    Args:
        path: Optional explicit path override (used in tests).

    Returns:
        Populated DevKitConfig dataclass.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if path is None:
        env_path = os.environ.get("DEVKIT_CONFIG_PATH")
        path = Path(env_path) if env_path else _DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(f"Dev-kit config not found: {path}")

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    upload_raw = raw.get("upload", {})
    polling_raw = raw.get("polling", {})

    return DevKitConfig(
        user_id=raw.get("user_id", "devkit-operator"),
        upload=UploadConfig(
            max_files_per_upload=upload_raw.get("max_files_per_upload", 5),
            max_file_size_mb=upload_raw.get("max_file_size_mb", 30),
            supported_extensions=upload_raw.get(
                "supported_extensions",
                [".pdf", ".txt", ".md", ".csv", ".docx", ".html"],
            ),
        ),
        polling=PollingConfig(
            poll_interval_seconds=polling_raw.get("poll_interval_seconds", 5),
            poll_timeout_minutes=polling_raw.get("poll_timeout_minutes", 15),
        ),
    )
