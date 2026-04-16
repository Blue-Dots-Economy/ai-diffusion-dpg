"""
action_gateway/main.py

Entry point for the Action Gateway service.

Starts the FastAPI server on the host/port read from config/dpg.yaml + config/domain.yaml
(default: 0.0.0.0:9999). DPG config missing → hard failure. Domain config missing → service
runs with DPG defaults; exceptions thrown at request time when domain values are accessed.

Run from the action_gateway/ directory:
    python main.py

Or from repo root:
    python -m action_gateway.main
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import asyncio

import uvicorn
import yaml
from dotenv import load_dotenv

from dpg_telemetry import init_otel

_env_local = Path(__file__).parent.parent / ".env.local"
_env_local_warn = _env_local.exists() and not load_dotenv(_env_local)
load_dotenv()  # .env in block dir or injected environment (Docker/prod)

from src.registry.adapter_factory import AdapterFactory
from src.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)

if _env_local_warn:
    logger.warning(
        "config.env_local_not_loaded",
        extra={
            "operation": "load_dotenv",
            "status": "skipped",
            "error": f"{_env_local} exists but no variables were loaded — check for syntax errors.",
        },
    )


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values win. Dicts are merged recursively."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Returns the path from CONFIG_FOLDER env var if set, otherwise the
    block-local config/domain.yaml fallback. An empty string CONFIG_FOLDER
    is treated the same as unset.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.

    Raises:
        ValueError: If CONFIG_FOLDER is set to a path that is not a directory.
        FileNotFoundError: If CONFIG_FOLDER is set but the resolved service
            YAML does not exist.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        config_dir = Path(config_folder)
        if not config_dir.is_dir():
            raise ValueError(
                f"CONFIG_FOLDER='{config_folder}' is not a directory. "
                f"Set CONFIG_FOLDER to the folder containing service YAML files, "
                f"not a file path. Check .env.local."
            )
        resolved = config_dir / f"{service}.yaml"
        if not resolved.exists():
            raise FileNotFoundError(
                f"CONFIG_FOLDER='{config_folder}' is set but "
                f"'{resolved}' does not exist. "
                f"Check CONFIG_FOLDER in .env.local."
            )
        return resolved
    return Path("config/domain.yaml")  # relative to cwd, consistent with config/dpg.yaml loading


def _build_config() -> tuple[dict, str, int]:
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config(str(_domain_config_path("action_gateway")))
    config = _deep_merge(dpg_config, domain_config)
    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 9999)
    return config, host, port


if __name__ == "__main__":
    config, host, port = _build_config()

    init_otel(service_name="action_gateway", config=config)

    registry = asyncio.run(AdapterFactory.build_registry(config))
    app = create_app(registry)

    logger.info(
        "action_gateway.startup",
        extra={
            "operation": "main",
            "status": "success",
            "host": host,
            "port": port,
            "tools_registered": len(registry),
        },
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
