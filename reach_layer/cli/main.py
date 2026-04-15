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

import yaml
from dotenv import load_dotenv

# Add repository root to sys.path so ``reach_layer_base`` imports work when
# running directly from a checkout without installing the package.
_HERE = Path(__file__).resolve().parent
_BASE_DIR = _HERE.parent / "base"
if str(_BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR.parent))

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
# Config loading (mirrors the pattern used by other DPG blocks)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")
    with path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _domain_config_path() -> Path:
    """Resolve the domain config path for the reach_layer block.

    Uses CONFIG_FOLDER env var if set (points to dev-kit/configs/<domain>),
    otherwise falls back to the block-local config/domain.yaml.
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
    return _HERE / "config" / "domain.yaml"


def _load_config() -> dict:
    """Merge framework defaults (dpg.yaml) with domain overrides (domain.yaml)."""
    dpg_path = _HERE / "config" / "dpg.yaml"
    dpg_config = _load_yaml(dpg_path) if dpg_path.exists() else {}
    domain_path = _domain_config_path()
    domain_config = _load_yaml(domain_path) if domain_path.exists() else {}
    return _deep_merge(dpg_config, domain_config)


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
