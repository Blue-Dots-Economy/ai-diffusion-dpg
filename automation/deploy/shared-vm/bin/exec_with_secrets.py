"""Load the secrets file written by init_secrets, then exec the service.

Shared-VM deployment tooling, not part of any DPG block. Agent Core and
Action Gateway are built on Docker Hardened Images, whose runtime has no
shell, so the secrets file cannot be ``source``-d. This script is the
entrypoint for those two containers instead, run by the image's own Python:

    entrypoint: ["python", "/opt/dpg/exec_with_secrets.py"]
    command:    ["python", "main.py"]

It reads ``/secrets/env`` (``export NAME='value'`` lines, as init_secrets
writes them), adds each variable to the environment, and replaces itself with
the command via ``os.execvp`` so the service runs as PID 1 and receives
signals directly. Values are never printed.

Standard library only. See issue #413.
"""

from __future__ import annotations

import os
import re
import shlex
import sys

SECRETS_FILE = os.environ.get("SECRETS_FILE", "/secrets/env")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _fail(message: str) -> None:
    """Print ``message`` to stderr and exit non-zero so the container restarts.

    Args:
        message: Human-readable reason; must never contain a secret value.
    """
    print(f"exec_with_secrets: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def load(path: str) -> dict[str, str]:
    """Parse ``export NAME='value'`` lines from the secrets file.

    Args:
        path: Location of the file written by init_secrets.

    Returns:
        Variable names mapped to their values, in file order.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        _fail(f"cannot read {path}: {type(exc).__name__}")

    secrets: dict[str, str] = {}
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            _fail(f"{path}:{number}: unparseable line")
        if tokens and tokens[0] == "export":
            tokens = tokens[1:]
        if len(tokens) != 1 or "=" not in tokens[0]:
            _fail(f"{path}:{number}: expected export NAME='value'")
        name, value = tokens[0].split("=", 1)
        if not NAME.fullmatch(name):
            _fail(f"{path}:{number}: invalid variable name")
        secrets[name] = value
    if not secrets:
        _fail(f"{path} contains no variables")
    return secrets


def main() -> None:
    """Export the secrets and exec the command given as arguments."""
    command = sys.argv[1:]
    if not command:
        _fail("no command given; set compose `command:` to the image's CMD")
    secrets = load(SECRETS_FILE)
    os.environ.update(secrets)
    # Names and count only, never values.
    print(f"exec_with_secrets: loaded {len(secrets)} from {SECRETS_FILE} ({', '.join(secrets)})", flush=True)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        _fail(f"cannot exec {command[0]!r}: {type(exc).__name__}")


if __name__ == "__main__":
    main()
