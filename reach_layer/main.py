"""
reach_layer/main.py — CLI Reach Layer

Thin terminal client that talks to the Agent Core orchestration service.
Domain config loaded from CONFIG_FOLDER/reach_layer.yaml if CONFIG_FOLDER is set,
otherwise config/domain.yaml.
This is the Reach Layer for CLI channel — the entry point for developer testing.

In production, the Reach Layer is replaced by a channel adapter (WhatsApp, Web, VOIP).
This CLI variant reads from stdin and POSTs to Agent Core via HTTP.

Prerequisites (run each in a separate terminal before this):
    Terminal 1: cd memory_layer   && python main.py   (port 8002)
    Terminal 2: cd trust_layer    && python main.py   (port 8003)
    Terminal 3: cd observability_layer && python main.py   (port 8004)
    Terminal 4: cd knowledge_engine && python main.py (port 8001)
    Terminal 5: cd action_gateway && python main.py   (port 9999)
    Terminal 6: cd agent_core     && python main.py   (port 8000)

Then run from reach_layer/:
    python main.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

# Load .env.local first (developer overrides), then .env (shared defaults).
# Neither file is required; missing files are silently ignored.
_env_local = Path(__file__).parent.parent / ".env.local"
_env_local_warn = _env_local.exists() and not load_dotenv(_env_local)
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
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
            "error": f"{_env_local} exists but no variables were loaded — check for syntax errors.",
        },
    )


# ---------------------------------------------------------------------------
# Config: read Agent Core endpoint from reach_layer's own config
# ---------------------------------------------------------------------------

_DEFAULT_AC_ENDPOINT = "http://localhost:8000/process_turn"
_DEFAULT_TIMEOUT_S = 30.0


def _load_yaml(path: str) -> dict:
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


def _load_config() -> tuple[str, float]:
    """Read agent_core_client.endpoint and timeout_s from merged YAML config.

    DPG config missing raises FileNotFoundError. Domain config missing is silently
    ignored and the server runs with DPG defaults.

    Returns:
        Tuple of (endpoint URL, timeout in seconds).
    """
    dpg_config = _load_yaml("config/dpg.yaml")
    domain_config = _load_yaml(str(_domain_config_path("reach_layer")))
    config = _deep_merge(dpg_config, domain_config)
    client_cfg = config.get("agent_core_client", {})
    endpoint = client_cfg.get("endpoint", _DEFAULT_AC_ENDPOINT)
    timeout_s = float(client_cfg.get("timeout_s", _DEFAULT_TIMEOUT_S))
    return endpoint, timeout_s


# ---------------------------------------------------------------------------
# HTTP turn
# ---------------------------------------------------------------------------


def _send_turn(
    client: httpx.Client,
    endpoint: str,
    session_id: str,
    user_message: str,
    timeout_s: float,
    user_id: str | None = None,
) -> str:
    """
    POST /process_turn and return the response text.
    On failure, returns a safe error string — never raises.
    """
    try:
        payload: dict = {
            "session_id": session_id,
            "user_message": user_message,
            "channel": "cli",
        }
        if user_id:
            payload["user_id"] = user_id
        response = client.post(
            endpoint,
            json=payload,
            timeout=timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response_text", "(no response)")

    except httpx.TimeoutException:
        return "[Error: Agent Core did not respond in time. Is it running?]"

    except httpx.ConnectError:
        return f"[Error: Could not connect to Agent Core at {endpoint}. Is it running?]"

    except httpx.HTTPStatusError as e:
        return f"[Error: Agent Core returned HTTP {e.response.status_code}]"

    except Exception as e:
        return f"[Error: {type(e).__name__}: {e}]"


# ---------------------------------------------------------------------------
# CLI REPL
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Kaam Ki Baat — PoC CLI")
    parser.add_argument(
        "--user-id",
        default=None,
        help=(
            "Persistent user identifier (e.g. phone number or name). "
            "Pass the same value across restarts to simulate a returning user. "
            "If omitted, session_id is used as user_id (each restart = new user)."
        ),
    )
    args = parser.parse_args()

    ac_endpoint, timeout_s = _load_config()
    session_id = str(uuid.uuid4())
    user_id: str | None = args.user_id

    print()
    print("=" * 60)
    print("  Kaam Ki Baat — PoC CLI")
    print(f"  Agent Core:  {ac_endpoint}")
    print(f"  Session ID:  {session_id}")
    if user_id:
        print(f"  User ID:     {user_id}  (fixed — simulating returning user)")
    else:
        print("  User ID:     (not set — each restart is a new user)")
    print("  Type your message. Ctrl-C or Ctrl-D to exit.")
    print("=" * 60)
    print()

    logger.info(
        "reach_layer.startup",
        extra={
            "operation": "main.main",
            "status": "success",
            "session_id": session_id,
            "user_id": user_id or session_id,
            "endpoint": ac_endpoint,
        },
    )

    with httpx.Client() as client:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                print("\nSession ended.")
                break
            except KeyboardInterrupt:
                print("\nSession interrupted.")
                break

            if not user_input:
                continue

            response_text = _send_turn(
                client, ac_endpoint, session_id, user_input, timeout_s, user_id
            )
            print(f"\nAssistant: {response_text}\n")


if __name__ == "__main__":
    main()
