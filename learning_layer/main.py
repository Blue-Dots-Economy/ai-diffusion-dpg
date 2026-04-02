"""
learning_layer/main.py

Entry point for the Learning Layer FastAPI service.

Loads config from config/config.yaml (or CONFIG_FOLDER/learning_layer.yaml if set),
instantiates ConsoleLogger, creates the FastAPI app, and starts uvicorn on the
configured port (default 8004).

Run:
    python -m main                   (from learning_layer/ directory)
    uvicorn main:app --reload        (dev hot-reload)

Environment:
    CONFIG_FOLDER — optional path to a folder containing learning_layer.yaml.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

# Load .env.local first (developer overrides), then .env (shared defaults).
# Neither file is required; missing files are silently ignored.
load_dotenv(Path(__file__).parent.parent / ".env.local")
load_dotenv()

# Add src/ to path so imports within the package work cleanly.
_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from console_logger import ConsoleLogger
from server import create_app

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


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
    block-local config/domain.yaml fallback.

    Args:
        service: Service name matching the filename in the configs folder.

    Returns:
        Absolute or relative Path to the domain config YAML file.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")  # relative to cwd, consistent with config/dpg.yaml loading


# ---------------------------------------------------------------------------
# App construction — exposed at module level for uvicorn --reload
# ---------------------------------------------------------------------------


def _build_app():
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config(str(_domain_config_path("learning_layer")))
    config = _deep_merge(dpg_config, domain_config)

    learning = ConsoleLogger(config)
    app = create_app(learning)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8004)

    logger.info(
        "learning_layer.startup",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
        },
    )

    return app, host, port


app, _host, _port = _build_app()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=_host,
        port=_port,
        log_level="info",
    )
