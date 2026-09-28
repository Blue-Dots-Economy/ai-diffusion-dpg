"""reach_layer/mcp/main.py

MCP channel entry point. Loads config, initializes the channel,
and runs the FastAPI server.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# Add repository root to sys.path so ``reach_layer_base`` imports work when
# running directly from a checkout without installing the package.
_HERE = Path(__file__).resolve().parent
_BASE_DIR = _HERE.parent / "base"
if str(_BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR.parent))

from reach_layer_base import load_reach_config  # noqa: E402

# Fall back to using local import if not installed as package
try:
    from src.mcp_reach import McpReachLayer  # type: ignore
    from src.server import create_app  # type: ignore
except ImportError:
    sys.path.insert(0, str(_HERE))
    from src.mcp_reach import McpReachLayer
    from src.server import create_app

# Load dotenv configuration
# The repo root is two levels up in a checkout; in the container main.py sits
# at /app, which has no grandparent, so indexing parents[2] would raise.
_parents = Path(__file__).resolve().parents
_env_local = _parents[2] / ".env.local" if len(_parents) > 2 else None
if _env_local is not None and _env_local.exists():
    load_dotenv(_env_local)
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

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

    Returns:
        Path to domain config.
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
    """Load and scope the unified Reach Layer config to the MCP channel.

    Returns:
        The scoped config dict.
    """
    return load_reach_config(
        channel_name="mcp",
        dpg_path=str(_dpg_config_path()),
        domain_path=str(_domain_config_path()),
    )


def main() -> None:
    """Main entry point for starting the MCP channel server."""
    config = _load_config()
    mcp_reach = McpReachLayer(config)
    app = create_app(mcp_reach, config)

    # Read port from config
    mcp_cfg = config.get("reach_layer", {}).get("channels", {}).get("mcp", {})
    port = mcp_cfg.get("port", 8007)
    host = os.getenv("HOST", "0.0.0.0")

    logger.info(
        "Starting reach_layer_mcp server",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
        },
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
