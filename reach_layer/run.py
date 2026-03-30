"""
reach_layer/run.py — KKB PoC CLI Reach Layer with phone-based user identity.

Entry point that accepts --phone <number> as the persistent user identifier.
The phone number is passed to Agent Core as user_id so the Memory Layer can
load the user's profile and prior journey from Neo4j.

Usage:
    python run.py --phone +919876543210

If --phone is omitted, user_id is None and the Memory Layer creates a new
anonymous user (session_id used as fallback user_id in Agent Core).

Prerequisites (run each in a separate terminal):
    Terminal 1: cd memory_layer   && python main.py   (port 8002)
    Terminal 2: cd trust_layer    && python main.py   (port 8003)
    Terminal 3: cd learning_layer && python main.py   (port 8004)
    Terminal 4: cd knowledge_engine && python main.py (port 8001)
    Terminal 5: cd action_gateway && python main.py   (port 9999)
    Terminal 6: cd agent_core     && python main.py   (port 8000)
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

import httpx
import yaml

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
# Config
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
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_config() -> tuple[str, float]:
    dpg_config = _load_yaml("config/dpg.yaml")
    domain_config = _load_yaml("config/domain.yaml")
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
    user_id: str | None,
    user_message: str,
    timeout_s: float,
) -> str:
    """
    POST /process_turn and return the response text.
    On failure, returns a safe error string — never raises.
    """
    payload: dict = {
        "session_id": session_id,
        "user_message": user_message,
        "channel": "cli",
    }
    if user_id:
        payload["user_id"] = user_id

    try:
        response = client.post(endpoint, json=payload, timeout=timeout_s)
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
    parser = argparse.ArgumentParser(
        description="Kaam Ki Baat — PoC CLI Reach Layer"
    )
    parser.add_argument(
        "--phone",
        type=str,
        default=None,
        help="User phone number (used as user_id for memory/profile lookup)",
    )
    args = parser.parse_args()

    ac_endpoint, timeout_s = _load_config()
    session_id = str(uuid.uuid4())
    user_id: str | None = args.phone

    print()
    print("=" * 60)
    print("  Kaam Ki Baat — PoC CLI")
    print(f"  Agent Core:  {ac_endpoint}")
    print(f"  Session ID:  {session_id}")
    if user_id:
        print(f"  User (phone): {user_id}")
    else:
        print("  User: anonymous (no --phone provided)")
    print("  Type your message. Ctrl-C or Ctrl-D to exit.")
    print("=" * 60)
    print()

    logger.info(
        "reach_layer.startup",
        extra={
            "operation": "run.main",
            "status": "success",
            "session_id": session_id,
            "user_id": user_id or "anonymous",
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
                client, ac_endpoint, session_id, user_id, user_input, timeout_s
            )
            print(f"\nAssistant: {response_text}\n")


if __name__ == "__main__":
    main()
