"""TC22 NLU accuracy: run the harness's NLU eval inside the target's own uv env."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from eval.voice_bench.redact import redact

_EVAL_DIR = Path(__file__).resolve().parents[1]


def pick_adapter(agent_core_root: Path) -> str:
    """Return ``dialogue_act`` if the target has ``src/understanding/``, else ``intent`` (legacy NLUProcessor)."""
    return "dialogue_act" if (Path(agent_core_root) / "src" / "understanding").is_dir() else "intent"


def _copy_harness(worktree: Path) -> Path:
    dest = worktree / ".vb_harness"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_EVAL_DIR, dest / "eval", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def run_nlu(worktree: Path, cases: list[Path], repeat: int, out: Path, run=subprocess.run,
            env_file: Path | None = None) -> dict:
    """Run the NLU worker in the target's uv env and return its report.

    Args:
        worktree: Target worktree root.
        cases: Harness-side JSONL case files (same cases for every target).
        repeat: Repeats per case.
        out: Where the worker writes its JSON.
        run: subprocess.run (injectable).
        env_file: Secrets env file whose values (plus OPENAI_API_KEY) are redacted from the stderr tail.

    Returns:
        The worker's JSON, or ``{"unmeasurable": <stderr tail>}`` on non-zero exit.
    """
    worktree = Path(worktree)
    cases = [Path(c).resolve() for c in cases]
    out = Path(out).resolve()
    harness = _copy_harness(worktree)
    agent_core = worktree / "agent_core"
    args = ["uv", "run", "--project", str(agent_core), "python", "-m", "eval.voice_bench.nlu_worker",
            "--agent-core", str(agent_core), "--config", str(worktree / "dev-kit" / "configs" / "blue-dots"),
            "--cases", *[str(c) for c in cases], "--repeat", str(repeat), "--out", str(out)]
    env = {**os.environ, "PYTHONPATH": f"{harness}:{agent_core}"}
    proc = run(args, cwd=str(harness), env=env, capture_output=True, text=True)
    tail = redact(proc.stderr or "", env_file)[-300:]    # redact before cutting: a cut secret would not match
    if proc.returncode != 0:
        return {"unmeasurable": tail}
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unmeasurable": ("worker produced no valid output: " + tail).strip()}
    if not isinstance(data, dict):
        return {"unmeasurable": "worker produced no valid output"}
    return data
