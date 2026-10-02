"""voice-bench CLI (spec §6.8).

    python -m eval.voice_bench run     [--config voice_bench.yaml] [--targets ...] [--scenarios ...] [--runs N]
                                       [--dry-run] [--env-file F]
    python -m eval.voice_bench report  [--config ...] [--targets ...] --out report.md
    python -m eval.voice_bench backend up|down|seed [-v]

Console output is ASCII only (Devanagari goes to the result files); secrets are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from eval.voice_bench import SUITE_VERSION
from eval.voice_bench.backend import Backend
from eval.voice_bench.bridge import BridgeClient
from eval.voice_bench.caller import Caller
from eval.voice_bench.config import BenchConfig, load_config
from eval.voice_bench.drive import DriveDeps, drive_call
from eval.voice_bench.llm import OpenAIJsonLLM
from eval.voice_bench.nlu import run_nlu
from eval.voice_bench.observe import LogScraper
from eval.voice_bench.report import comparable, render_markdown, summarise
from eval.voice_bench.score import score_call
from eval.voice_bench.seed import SEED_VERSION, load_seed, places
from eval.voice_bench.stack import StackError, TargetStack
from eval.voice_bench.store import ResultStore
from eval.voice_bench.suite import load_personas, phone_for, runs_for
from eval.voice_bench.tap import Tap

_NLU_CASES = [Path(__file__).resolve().parents[1] / "nlu" / "cases" / n for n in ("scenarios.jsonl", "synthetic.jsonl")]


def _say(msg: str) -> None:
    """Print ASCII only: anything else is escaped so Devanagari never reaches the console."""
    print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _repo_root() -> Path:
    """Git toplevel of the harness repo (target worktrees are cut from it)."""
    r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


def load_env_file(path: Path) -> None:
    """Set KEY=VALUE lines into os.environ for keys not already set. Values are never printed."""
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def _csv(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def _pick_targets(cfg: BenchConfig, names: list[str] | None):
    if not names:
        return list(cfg.targets)
    known = {t.name: t for t in cfg.targets}
    missing = [n for n in names if n not in known]
    if missing:
        raise ValueError(f"unknown target(s): {', '.join(missing)}")
    return [known[n] for n in names]


def _turns(rec) -> int:
    return sum(len(lg.turns) for lg in rec.legs)


def _merge_meta(store: ResultStore, key: str, kv: dict, drop: tuple[str, ...] = ()) -> None:
    meta = {k: v for k, v in store.read_meta(key).items() if k not in drop}
    store.write_meta(key, {**meta, **kv})


def _safe_commit(stack, name: str) -> str:
    try:
        return stack.commit
    except StackError:
        return f"unresolved-{name}"


def _run_target(t, stack, cfg: BenchConfig, store: ResultStore, plan, personas, backend, tap, caller, judge_llm,
                seed_places, seed_version: int, dry_run: bool) -> None:
    try:
        url = stack.up()
        commit = stack.commit
    except StackError as e:
        commit = _safe_commit(stack, t.name)
        _merge_meta(store, commit, {"name": t.name, "commit": commit, "unmeasurable": str(e)})
        _say(f"{t.name} unmeasurable: {e}")
        return
    _merge_meta(store, commit, {"name": t.name, "commit": commit}, drop=("unmeasurable",))
    deps = DriveDeps(bridge=BridgeClient(url, cfg.status_phrases, cfg.terminal_words), tap=tap,
                     redis_container=t.redis_container, scraper=LogScraper(t.agent_container), caller=caller,
                     judge_llm=judge_llm, cleanup=backend.cleanup)
    drive_meta = dict(target=t.name, target_commit=commit, suite_version=SUITE_VERSION, seed_version=seed_version,
                      caller_model=cfg.caller.model, judge_model=cfg.judge.model)
    for pid, runs in plan:
        persona = personas[pid]
        for run in range(runs):
            if store.has(commit, pid, run):
                _say(f"{t.name} {pid} r{run} cached")
                continue
            phone = persona.seeded_phone or phone_for(cfg.phone_prefix, pid, run)
            rec = drive_call(deps, persona, run, phone, cfg.max_turns, drive_meta)
            score_call(rec, persona, judge_llm, seed_places, t.no_idle_handling)
            store.save(rec)
            status = "error" if rec.error else ("voided" if rec.voided else "ok")
            _say(f"{t.name} {pid} r{run} {status} {_turns(rec)} turns")
    if t.git_ref and not dry_run:
        nlu = store.read_meta(commit).get("nlu")
        if not nlu or nlu.get("unmeasurable"):
            nlu = run_nlu(stack.worktree, _NLU_CASES, 1, store.target_dir(commit) / "nlu.json")
            _merge_meta(store, commit, {"nlu": nlu})
            _say(f"{t.name} nlu " + ("unmeasurable" if nlu.get("unmeasurable") else "ok"))


def cmd_run(cfg: BenchConfig, args) -> int:
    personas = load_personas()
    targets = _pick_targets(cfg, _csv(args.targets))
    ids = _csv(args.scenarios) or sorted(personas)
    unknown = [i for i in ids if i not in personas]
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}")
    if args.dry_run:
        targets, plan = targets[:1], [("T01", 1)]
    else:
        plan = [(i, runs_for(cfg, i)) for i in ids]
    backend = Backend(cfg.backend, cfg.results_dir)
    state = backend.state
    if not state.get("watermark"):
        _say("backend not seeded: run `python -m eval.voice_bench backend up` then `backend seed`")
        return 2
    if state.get("seed_version") != SEED_VERSION:
        _say(f"backend seed v{state.get('seed_version')} != harness seed v{SEED_VERSION}: re-seed the backend")
        return 2
    store = ResultStore(cfg.results_dir)
    caller = Caller(OpenAIJsonLLM(cfg.caller.model, cfg.caller.temperature))
    judge_llm = OpenAIJsonLLM(cfg.judge.model, cfg.judge.temperature)
    seed_places = places(load_seed())
    repo_root = _repo_root()
    work_root = (Path(cfg.results_dir) / "worktrees").resolve()
    seen: dict[str, str] = {}
    for t in targets:
        c = _resolve_commit(t, repo_root, work_root)
        if c is not None and c in seen:
            _say(f"targets {seen[c]} and {t.name} resolve to the same commit {c}: results would be mislabeled")
            return 2
        if c is not None:
            seen[c] = t.name
    tap = Tap(cfg.backend.tap_port, cfg.backend.signals_url, cfg.backend.search_url)
    tap.start()
    try:
        for t in targets:
            stack = TargetStack(t, repo_root, work_root, tap.url_for_containers, state["instance_url"],
                                Path(state["api_key_env_file"]))
            try:
                _run_target(t, stack, cfg, store, plan, personas, backend, tap, caller, judge_llm, seed_places,
                            state["seed_version"], args.dry_run)
            finally:
                failed = stack.down()
                if failed:
                    _say(f"{t.name} teardown incomplete: {', '.join(failed)}")
    finally:
        tap.stop()
    return 0


def _resolve_commit(t, repo_root: Path, work_root: Path) -> str | None:
    """The target's commit id without bringing anything up (TargetStack.commit); None if the ref won't resolve."""
    try:
        return TargetStack(t, repo_root, work_root, "", "", Path(os.devnull)).commit
    except StackError:
        return None


def _planned_calls(cfg: BenchConfig, personas) -> int:
    return sum(runs_for(cfg, i) for i in personas)


def cmd_report(cfg: BenchConfig, args) -> int:
    store = ResultStore(cfg.results_dir)
    repo_root, work_root = _repo_root(), (Path(cfg.results_dir) / "worktrees").resolve()
    planned = _planned_calls(cfg, load_personas())
    summaries = []
    for t in _pick_targets(cfg, _csv(args.targets)):
        commit = _resolve_commit(t, repo_root, work_root)
        if commit is None:
            s = summarise([], {"name": t.name, "unmeasurable": f"ref {t.git_ref!r} does not resolve"})
            s["warnings"] = [f"ref {t.git_ref!r} does not resolve; no results reported"]
            summaries.append(s)
            continue
        records = store.load_target(commit) if store.target_dir(commit).is_dir() else []
        s = summarise(records, {"commit": commit, **store.read_meta(commit), "name": t.name})
        warnings = []
        if not records:
            warnings.append(f"no records for commit {commit}")
        elif len(records) < planned:
            warnings.append(f"partial: {len(records)} of {planned} planned calls")
        s["warnings"] = warnings
        summaries.append(s)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(summaries), encoding="utf-8")
    js = out.with_name(out.name + ".json")
    js.write_text(json.dumps({"suite_version": SUITE_VERSION, "comparability": comparable(summaries),
                              "summaries": summaries}, ensure_ascii=False, indent=1), encoding="utf-8")
    flags = (" - NOT COMPARABLE" if comparable(summaries) else "") + \
        (" - WARNINGS" if any(s["warnings"] for s in summaries) else "")
    _say(f"report: {out} ({len(summaries)} targets){flags}" + (", see header" if flags else ""))
    return 0


def cmd_backend(cfg: BenchConfig, args) -> int:
    b = Backend(cfg.backend, cfg.results_dir)
    if args.action == "up":
        b.up()
    elif args.action == "down":
        b.down(volumes=args.volumes)
    else:
        b.seed()
    _say(f"backend {args.action} ok")
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m eval.voice_bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="voice_bench.yaml")
    common.add_argument("--env-file", default=None, help="KEY=VALUE file loaded into the environment (never printed)")
    run = sub.add_parser("run", parents=[common])
    run.add_argument("--targets")
    run.add_argument("--scenarios")
    run.add_argument("--runs", type=int)
    run.add_argument("--dry-run", action="store_true")
    rep = sub.add_parser("report", parents=[common])
    rep.add_argument("--targets")
    rep.add_argument("--runs", type=int, help="planned-calls check: same override semantics as run --runs")
    rep.add_argument("--out", required=True)
    be = sub.add_parser("backend", parents=[common])
    be.add_argument("action", choices=["up", "down", "seed"])
    be.add_argument("-v", "--volumes", action="store_true", help="down: also remove volumes (wipes the Signals DB)")
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _parser().parse_args(argv)
    overrides = {}
    if getattr(args, "runs", None) is not None:
        overrides = {"runs": args.runs, "runs_per_scenario": {}}
    try:
        cfg = load_config(args.config, overrides)
        env_file = args.env_file or cfg.backend.env_file
        if env_file:
            load_env_file(Path(env_file))
        return {"run": cmd_run, "report": cmd_report, "backend": cmd_backend}[args.cmd](cfg, args)
    except (ValueError, OSError) as e:
        _say(f"error: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
