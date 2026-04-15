"""
reach_layer/cli/main.py — CLI channel entry point.

Loads the merged reach_layer config, instantiates CLIReachLayer, and runs
its async REPL loop. Replaces the old reach_layer/main.py and run.py.

Usage:
    python main.py                      # anonymous session
    python main.py --user-id rahul      # persistent user identifier
    python main.py --verbose            # show pipeline signal events
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Add repository root to sys.path so ``reach_layer_base`` imports work when
# running directly from a checkout without installing the package.
_HERE = Path(__file__).resolve().parent
_BASE_DIR = _HERE.parent / "base"
if str(_BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR.parent))

from reach_layer_base import load_reach_config  # noqa: E402

# Fall back to using the flat ``base`` package if ``reach_layer_base`` is
# not installed (e.g. dev environment running from the repo without uv sync).
try:
    from src.cli_reach import CLIReachLayer  # type: ignore
except ImportError:  # pragma: no cover — dev fallback
    sys.path.insert(0, str(_HERE))
    from src.cli_reach import CLIReachLayer

# Load .env.local first (developer overrides), then .env (shared defaults).
_env_local = Path(__file__).resolve().parents[2] / ".env.local"
if _env_local.exists():
    load_dotenv(_env_local)
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,  # keep stdout clean for the REPL
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loading — delegates to the shared reach_layer loader.
# ---------------------------------------------------------------------------

# Resolve the DPG defaults path. Two layouts are supported:
#   - Docker runtime: `/app/config/dpg.yaml` (cwd=/app, Dockerfile COPYs
#     reach_layer/config/ → ./config/).
#   - Local checkout: `reach_layer/config/dpg.yaml` (one level above this file).
# The former is picked automatically because the loader accepts a relative
# path and cwd=/app inside the container. The latter is used when running
# from the repo without installing.
_LOCAL_REACH_CONFIG_DIR = _HERE.parent / "config"


def _dpg_config_path() -> Path:
    """Resolve the DPG framework defaults path.

    Prefers the checked-in ``reach_layer/config/dpg.yaml`` when it exists
    (local dev); otherwise falls back to ``config/dpg.yaml`` relative to
    cwd (container runtime).
    """
    local = _LOCAL_REACH_CONFIG_DIR / "dpg.yaml"
    if local.exists():
        return local
    return Path("config/dpg.yaml")


def _domain_config_path() -> Path:
    """Resolve the domain-overrides path.

    Uses ``CONFIG_FOLDER`` env var if set (points at
    ``dev-kit/configs/<domain>``), otherwise falls back to the checked-in
    ``reach_layer/config/domain.yaml`` for local dev, or
    ``config/domain.yaml`` relative to cwd inside containers.
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
    """Load and scope the unified Reach Layer config to the CLI channel."""
    return load_reach_config(
        channel_name="cli",
        dpg_path=str(_dpg_config_path()),
        domain_path=str(_domain_config_path()),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run(user_id: str | None, verbose: bool) -> None:
    config = _load_config()
    session_id = str(uuid.uuid4())

    layer = CLIReachLayer(
        config=config,
        session_id=session_id,
        user_id=user_id,
        verbose=verbose,
    )

    _print_banner(session_id, user_id, layer.assembly_mode, config)
    await layer.run_loop()


def _print_banner(
    session_id: str, user_id: str | None, assembly_mode: str, config: dict
) -> None:
    ac_endpoint = config.get("agent_core_client", {}).get(
        "endpoint", "http://localhost:8000/process_turn"
    )
    print()
    print("=" * 60)
    print("  Reach Layer — CLI")
    print(f"  Agent Core:    {ac_endpoint}")
    print(f"  Session ID:    {session_id}")
    print(f"  Assembly mode: {assembly_mode}")
    if user_id:
        print(f"  User ID:       {user_id}")
    else:
        print("  User ID:       anonymous")
    print("  Type your message. Ctrl-C or Ctrl-D to exit.")
    print("=" * 60)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="DPG Reach Layer — CLI channel")
    parser.add_argument(
        "--user-id",
        default=None,
        help="Persistent user identifier. If omitted, the session is anonymous.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print pipeline signal events as status lines.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.user_id, args.verbose))
    except KeyboardInterrupt:
        print("\nSession interrupted.")


if __name__ == "__main__":
    main()
