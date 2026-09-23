#!/usr/bin/env python3
"""Decide which service images build-images.yaml should build on this run.

Emits three GitHub Actions outputs:
    matrix     JSON array of {name, dockerfile} entries for the build matrix.
    any        "true" when the matrix is non-empty, so the build job can skip.
    short_sha  Short SHA of the commit actually checked out (which is not
               github.sha when the workflow was dispatched with a `ref`).

Selection rules, in order:
  * Only services whose Dockerfile exists on this ref are ever eligible.
    reach-layer-bridge is not on main yet, and a matrix entry pointing at a
    missing file fails the job instead of skipping it.
  * workflow_dispatch builds everything eligible, or the one service named.
  * A tag push builds everything eligible — a release must be complete.
  * A branch push builds only the services whose inputs changed. Changing a
    shared path rebuilds every image that vendors it, which is why the
    dependency lists below are explicit rather than derived from the
    service directory alone.
  * Anything that cannot be determined (first push, force push, failed diff)
    falls back to building everything. Over-building wastes minutes;
    under-building ships a stale image, which is the worse failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# Copied into nearly every image as a vendored package.
OBSERVABILITY = "observability_layer/"
# Shared base package for every Reach Layer channel.
REACH_BASE = "reach_layer/base/"

# dev-kit vendors the runtime config schema out of each service it configures,
# so a schema-only change in any of them must rebuild dev-kit.
DEVKIT_SCHEMAS = [
    "agent_core/src/schema/",
    "trust_layer/src/schema/",
    "knowledge_engine/src/schema/",
    "action_gateway/src/schema/",
    "memory_layer/src/schema/",
    "observability_layer/src/schema/",
    "reach_layer/base/schema/",
]

# name -> (dockerfile, paths that must trigger a rebuild)
SERVICES: dict[str, tuple[str, list[str]]] = {
    "action-gateway": ("action_gateway/Dockerfile", ["action_gateway/", OBSERVABILITY]),
    "agent-core": ("agent_core/Dockerfile", ["agent_core/", OBSERVABILITY]),
    "knowledge-engine": ("knowledge_engine/Dockerfile", ["knowledge_engine/", OBSERVABILITY]),
    "memory-layer": ("memory_layer/Dockerfile", ["memory_layer/", OBSERVABILITY]),
    "observability-layer": ("observability_layer/Dockerfile", [OBSERVABILITY]),
    "trust-layer": ("trust_layer/Dockerfile", ["trust_layer/", OBSERVABILITY]),
    "reach-layer-web": (
        "reach_layer/web/Dockerfile",
        ["reach_layer/web/", REACH_BASE, OBSERVABILITY],
    ),
    "reach-layer-voice": (
        "reach_layer/voice/Dockerfile",
        ["reach_layer/voice/", REACH_BASE, OBSERVABILITY],
    ),
    "reach-layer-mcp": (
        "reach_layer/mcp/Dockerfile",
        ["reach_layer/mcp/", REACH_BASE, OBSERVABILITY],
    ),
    "reach-layer-cli": (
        "reach_layer/cli/Dockerfile",
        ["reach_layer/cli/", REACH_BASE, OBSERVABILITY],
    ),
    # No observability_layer: the bridge Dockerfile does not vendor it.
    "reach-layer-bridge": (
        "reach_layer/bridge/Dockerfile",
        ["reach_layer/bridge/", REACH_BASE],
    ),
    "dev-kit": ("dev-kit/Dockerfile", ["dev-kit/", *DEVKIT_SCHEMAS]),
}

# A change to the build definition itself invalidates every image.
BUILD_WIDE = (".github/workflows/build-images.yaml", ".github/scripts/", ".dockerignore")

NULL_SHA = "0" * 40


def emit(**outputs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:  # local run
        for key, value in outputs.items():
            print(f"{key}={value}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def changed_files(before: str, after: str) -> list[str] | None:
    """Files changed between two commits, or None when undeterminable."""
    if not before or not after or before == NULL_SHA:
        return None
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{before}", f"{after}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    return [line for line in out.stdout.splitlines() if line]


def main() -> int:
    event = os.environ.get("EVENT_NAME", "")
    service_filter = (os.environ.get("SERVICE_FILTER") or "all").strip()

    short_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    eligible = {
        name: spec for name, spec in SERVICES.items() if os.path.isfile(spec[0])
    }
    missing = sorted(set(SERVICES) - set(eligible))
    if missing:
        print(f"Not on this ref, skipping: {', '.join(missing)}")

    if event == "workflow_dispatch":
        if service_filter and service_filter != "all":
            if service_filter not in eligible:
                print(
                    f"::error::'{service_filter}' has no Dockerfile on this ref",
                    file=sys.stderr,
                )
                return 1
            selected = [service_filter]
        else:
            selected = sorted(eligible)
        print(f"Dispatch build: {', '.join(selected)}")
    elif os.environ.get("GITHUB_REF", "").startswith("refs/tags/"):
        selected = sorted(eligible)
        print(f"Tag build, all images: {', '.join(selected)}")
    else:
        files = changed_files(
            os.environ.get("BEFORE_SHA", ""), os.environ.get("AFTER_SHA", "")
        )
        if files is None:
            selected = sorted(eligible)
            print(f"Could not diff this push, building all: {', '.join(selected)}")
        elif any(f.startswith(BUILD_WIDE) for f in files):
            selected = sorted(eligible)
            print(f"Build definition changed, building all: {', '.join(selected)}")
        else:
            selected = sorted(
                name
                for name, (_, triggers) in eligible.items()
                if any(f.startswith(tuple(triggers)) for f in files)
            )
            print(
                f"{len(files)} file(s) changed -> "
                f"{', '.join(selected) if selected else 'no images affected'}"
            )

    matrix = [
        {"name": name, "dockerfile": eligible[name][0]} for name in selected
    ]
    emit(
        matrix=json.dumps(matrix),
        any="true" if matrix else "false",
        short_sha=short_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
