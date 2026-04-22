"""
observability_layer/main.py

Entry point for the Observability Layer FastAPI service.

Loads config from config/dpg.yaml merged with the domain YAML,
initialises OTel SDK via dpg_telemetry, constructs OtelObservabilityLayer,
creates the FastAPI app, and starts uvicorn on port 8004.

Run:
    python -m main                   (from observability_layer/ directory)
    uvicorn main:app --reload        (dev hot-reload)

Environment:
    CONFIG_FOLDER — optional path to a folder containing observability_layer.yaml.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

_env_local = Path(__file__).parent.parent / ".env.local"
_env_local_warn = _env_local.exists() and not load_dotenv(_env_local)
load_dotenv()

_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dpg_telemetry import init_otel
from otel_observability_layer import OtelObservabilityLayer
from schema.config import MergedConfig, ObservabilityConfig
from server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

if _env_local_warn:
    logger.warning(
        "config.env_local_not_loaded",
        extra={
            "operation": "load_dotenv",
            "status": "skipped",
            "error": f"{_env_local} exists but no variables were loaded.",
        },
    )


def _load_config(path: str) -> dict:
    """Load a YAML config file and return its contents as a dict.

    Args:
        path: Relative or absolute path to the YAML file.

    Returns:
        Parsed YAML dict, or empty dict if file is empty.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values win. Dicts are merged recursively.

    Args:
        base: Base config dict.
        override: Override config dict. Values here take precedence.

    Returns:
        Merged dict.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _domain_config_path(service: str) -> Path:
    """Resolve the domain config path.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Path to the domain config YAML file.

    Raises:
        ValueError: If CONFIG_FOLDER is set to a path that is not a directory.
        FileNotFoundError: If CONFIG_FOLDER is set but the resolved service YAML is missing.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        config_dir = Path(config_folder)
        if not config_dir.is_dir():
            raise ValueError(f"CONFIG_FOLDER='{config_folder}' is not a directory.")
        resolved = config_dir / f"{service}.yaml"
        if not resolved.exists():
            raise FileNotFoundError(f"CONFIG_FOLDER='{config_folder}' set but '{resolved}' missing.")
        return resolved
    return Path("config/domain.yaml")


def _build_app():
    """Load config, initialise OTel, construct app, and return (app, host, port).

    Returns:
        Tuple of (FastAPI app, host string, port int).
    """
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config(str(_domain_config_path("observability_layer")))
    config = _deep_merge(dpg_config, domain_config)

    # Strict schema check on the full merged config — unknown keys, wrong
    # types, or out-of-range values at any depth fail here at startup.
    MergedConfig.validate_full(config)
    obs_config = ObservabilityConfig.from_config(config)
    init_otel(service_name="observability_layer", config=config)

    observability = OtelObservabilityLayer(config)
    app = create_app(observability, obs_config)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8004)

    logger.info(
        "observability_layer.startup",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
            "domain": obs_config.domain,
        },
    )
    return app, host, port


app, _host, _port = _build_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host=_host, port=_port, log_level="info")
