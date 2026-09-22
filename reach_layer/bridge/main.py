"""reach_layer/bridge/main.py

Bridge channel entry point. Loads config, builds the app, runs uvicorn.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from reach_layer_base import load_reach_config

from src.server import create_app

_env_local = Path(__file__).resolve().parents[2] / ".env.local"
if _env_local.exists():
    load_dotenv(_env_local)
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
# The unified Reach Layer config is shared across cli/web/voice/mcp/bridge and
# lives one level up from any single channel's directory.
_LOCAL_REACH_CONFIG_DIR = _HERE.parent / "config"


def _dpg_config_path() -> Path:
    """Resolve the DPG framework defaults path.

    Returns:
        Path to the dpg.yaml defaults.
    """
    local = _LOCAL_REACH_CONFIG_DIR / "dpg.yaml"
    if local.exists():
        return local
    return Path("config/dpg.yaml")


def _domain_config_path() -> Path:
    """Resolve the domain overrides configuration path.

    Args:
        None.

    Returns:
        Path to domain config.

    Raises:
        FileNotFoundError: If ``CONFIG_FOLDER`` is set but the expected
            ``reach_layer.yaml`` does not exist under it.
    """
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        resolved = Path(config_folder) / "reach_layer.yaml"
        if not resolved.exists():
            raise FileNotFoundError(
                f"CONFIG_FOLDER='{config_folder}' is set but "
                f"'{resolved}' does not exist."
            )
        return resolved
    local = _LOCAL_REACH_CONFIG_DIR / "domain.yaml"
    if local.exists():
        return local
    return Path("config/domain.yaml")


def _load_config() -> dict:
    """Load and scope the unified Reach Layer config to the bridge channel.

    Returns:
        The scoped config dict, with ``reach_layer.channels.bridge``
        guaranteed to exist (possibly empty).
    """
    return load_reach_config(
        channel_name="bridge",
        dpg_path=str(_dpg_config_path()),
        domain_path=str(_domain_config_path()),
    )


def main() -> None:
    """Load config and run the bridge service."""
    reach_config = _load_config()
    bridge = reach_config.get("reach_layer", {}).get("channels", {}).get("bridge", {})
    server = bridge.get("server", {})

    config = {
        "agent_core_url": bridge.get("agent_core_url", "http://agent_core:8000"),
        "channel": "bridge",
        "terminal_word": bridge.get("terminal_word", ""),
        "timeout_s": float(bridge.get("timeout_s", 60.0)),
    }

    logger.info("bridge.startup",
                extra={"operation": "main.startup", "status": "success"})
    uvicorn.run(create_app(config),
                host=server.get("host", "0.0.0.0"),
                port=int(server.get("port", 8008)))


if __name__ == "__main__":
    main()
