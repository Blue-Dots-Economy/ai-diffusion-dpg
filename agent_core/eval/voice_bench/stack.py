"""Target lifecycle (spec §6.1): throwaway worktree + uncommitted patch + compose up/down."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

from eval.voice_bench.config import TargetCfg
from eval.voice_bench.redact import redact

PATCH_FILE = Path(__file__).parent / "patches" / "blue-dots-local.yaml"
OVERRIDE_NAME = "voice-bench.override.yml"
PROJECT = "vb"
BRIDGE_HOST_PORT = 18008


_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


class PatchMismatch(Exception):
    """The target's action_gateway.yaml does not match the patch's expectations."""


class StackError(Exception):
    """The target stack could not be brought up (reason becomes meta.unmeasurable)."""


def apply_patch(text: str, patch: dict, tap_url: str, instance_url: str) -> str:
    """Apply the string-replacement patch; every rule must match exactly its `count`."""
    base_lines = [ln for ln in text.splitlines() if 'base_url: "' in ln]
    hosts = [h for h in patch["upstream_hosts"] if any(h in ln for ln in base_lines)]
    if len(hosts) != 1:
        raise PatchMismatch("no single upstream host")
    host = hosts[0]
    for i, rule in enumerate(patch["rules"]):
        find = rule["find"].replace("{host}", host)
        repl = rule["replace"].replace("{instance_url}", instance_url).replace("{tap_url}", tap_url)
        n = text.count(find)
        if n != rule["count"]:
            raise PatchMismatch(f"rule {i}: expected {rule['count']}, found {n}")
        text = text.replace(find, repl)
    for ln in text.splitlines():
        code = ln.split("#", 1)[0]
        if any(h in code for h in patch["upstream_hosts"]):
            raise PatchMismatch("upstream host still present")
    return text


def compose_override(bridge_host_port: int, env_file: Path) -> str:
    """Compose override: publish the bridge, feed BLUE_DOTS_* env, cap agent_core."""
    return yaml.safe_dump({"services": {
        "reach_layer_bridge": {"ports": [f"127.0.0.1:{bridge_host_port}:8008"]},
        "action_gateway": {"env_file": [str(env_file)],
                           "extra_hosts": ["host.docker.internal:host-gateway"]},
        "agent_core": {"deploy": {"resources": {"limits": {"memory": "1g", "cpus": "1.0"}}}},
        # Milestone refs before cf794ef use memgraph/memgraph:latest, whose newer binary fails the healthcheck.
        # The repo itself pinned 2.17.0 at cf794ef.
        "memgraph": {"image": "memgraph/memgraph:2.17.0"},
    }}, sort_keys=False)


class TargetStack:
    def __init__(self, target: TargetCfg, repo_root: Path, work_root: Path, tap_url: str,
                 instance_url: str, env_file: Path, run: Callable[..., Any] = subprocess.run,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 health_timeout_s: float = 900.0, poll_interval_s: float = 3.0) -> None:
        self.target = target
        self.repo_root = Path(repo_root)
        self.work_root = Path(work_root)
        self.tap_url = tap_url
        self.instance_url = instance_url
        self.env_file = Path(env_file).resolve()
        self._run = run
        self._client = client or httpx.Client(timeout=5.0)
        self._sleep = sleep
        self._timeout = health_timeout_s
        self._interval = poll_interval_s
        self._sha: str | None = None
        self._up = False

    @property
    def commit(self) -> str:
        if self.target.bridge_url:
            return "external-" + hashlib.sha1(self.target.bridge_url.encode()).hexdigest()[:7]
        if self._sha is None:
            r = self._git("rev-parse", "--short=7", self.target.git_ref or "")
            self._sha = (r.stdout or "").strip()
            if not self._sha:
                raise StackError(f"cannot resolve ref {self.target.git_ref!r}")
        return self._sha

    @property
    def worktree(self) -> Path:
        if not _NAME_RE.fullmatch(self.target.name):
            raise StackError("invalid target name")
        wt = self.work_root / f"{self.target.name}-{self.commit}"
        if not wt.resolve().is_relative_to(self.work_root.resolve()):
            raise StackError("worktree path escapes work_root")
        return wt

    def _git(self, *args: str):
        return self._exec(["git", "-C", str(self.repo_root), *args], "git " + args[0])

    def _exec(self, argv: list[str], what: str, env: dict[str, str] | None = None):
        try:
            r = self._run(argv, capture_output=True, text=True, **({"env": env} if env else {}))
        except OSError as e:
            raise StackError(f"{what} could not run: {type(e).__name__}") from e
        if r.returncode != 0:
            tail = ((r.stderr or "").strip().splitlines() or [""])[-1]
            tail = self._redact(tail)[:300]
            raise StackError(f"{what} failed (exit {r.returncode}): {tail}")
        return r

    def _redact(self, text: str) -> str:
        return redact(text, self.env_file)

    def _compose_argv(self, *tail: str) -> list[str]:
        compose = self.worktree / self.target.compose
        return ["docker", "compose", "-p", PROJECT, "-f", str(compose),
                "-f", str(compose.parent / OVERRIDE_NAME), *tail]

    def _env(self) -> dict[str, str]:
        return {**os.environ, "DOMAIN": "blue-dots", "GIT_SHA": self.commit}

    def up(self) -> str:
        if self.target.bridge_url:
            url = self.target.bridge_url.rstrip("/")
            self._wait_healthy(url)
            return url
        wt = self.worktree
        self.work_root.mkdir(parents=True, exist_ok=True)
        if wt.exists():  # stale from a killed run
            self._best_effort(["git", "-C", str(self.repo_root), "worktree", "remove", "--force", str(wt)])
            self._best_effort(["git", "-C", str(self.repo_root), "worktree", "prune"])
        self._git("worktree", "add", "--detach", str(wt), self.target.git_ref or "")
        self._up = True
        try:
            patch = yaml.safe_load(PATCH_FILE.read_text(encoding="utf-8"))
            f = wt / patch["file"]
            try:
                f.write_text(apply_patch(f.read_text(encoding="utf-8"), patch, self.tap_url,
                                         self.instance_url), encoding="utf-8")
            except PatchMismatch as e:
                raise StackError(f"patch does not apply: {e}") from e
            compose = wt / self.target.compose
            (compose.parent / OVERRIDE_NAME).write_text(
                compose_override(BRIDGE_HOST_PORT, self.env_file), encoding="utf-8")
            self._exec(self._compose_argv("up", "-d", "--build", "reach_layer_bridge"),
                       "docker compose up", env=self._env())
            url = f"http://127.0.0.1:{BRIDGE_HOST_PORT}"
            self._wait_healthy(url)
            return url
        except BaseException:
            try:
                self.down()
            except BaseException:
                pass
            raise

    def _best_effort(self, argv: list[str], env: dict[str, str] | None = None) -> bool:
        try:
            r = self._run(argv, capture_output=True, text=True, **({"env": env} if env else {}))
        except Exception:
            return False
        return r.returncode == 0

    def _wait_healthy(self, base: str) -> None:
        waited = 0.0
        while True:
            try:
                if self._client.get(f"{base}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if waited >= self._timeout:
                raise StackError(f"bridge not healthy after {int(self._timeout)}s")
            self._sleep(self._interval)
            waited += self._interval

    def down(self) -> list[str]:
        """Best-effort teardown; returns the names of steps that failed (never raises on a step failure)."""
        if self.target.bridge_url or not self._up:
            return []
        self._up = False
        wt = self.worktree
        failed = []
        if not self._best_effort(self._compose_argv("down", "-v"), env=self._env()):
            failed.append("compose down")
        if not self._best_effort(["git", "-C", str(self.repo_root), "worktree", "remove", "--force", str(wt)]):
            failed.append("worktree remove")
        return failed
