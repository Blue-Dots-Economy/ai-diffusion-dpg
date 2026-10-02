"""voice_bench.yaml loading (spec §5). Flags override file values."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from eval.voice_bench import SUITE_VERSION

_DEFAULT_COMPOSE = "automation/docker/docker-compose.yml"


@dataclass(frozen=True)
class ModelCfg:
    provider: str
    model: str
    temperature: float


@dataclass(frozen=True)
class TargetCfg:
    name: str
    git_ref: str | None = None
    bridge_url: str | None = None
    compose: str = _DEFAULT_COMPOSE
    redis_container: str = "redis"
    agent_container: str | None = "agent_core"
    no_idle_handling: bool = False


@dataclass(frozen=True)
class BackendCfg:
    signals_dir: Path
    signals_url: str = "http://localhost:2742"
    search_url: str = "http://localhost:3100"
    tap_port: int = 18742
    postgres_container: str = "signals-postgres"
    env_file: Path | None = None


@dataclass(frozen=True)
class BenchConfig:
    suite_version: int
    targets: list[TargetCfg]
    caller: ModelCfg
    judge: ModelCfg
    backend: BackendCfg
    runs: int = 1
    runs_per_scenario: dict[str, int] = field(default_factory=dict)
    max_turns: int = 14
    phone_prefix: str = "9199000"
    results_dir: Path = Path("eval_results/voice_bench")
    status_phrases: list[str] = field(default_factory=lambda: ["एक मिनट।"])
    terminal_words: list[str] = field(default_factory=lambda: ["धन्यवाद", "Thank you"])


def _target(d: dict) -> TargetCfg:
    name = d.get("name") or "?"
    if bool(d.get("git_ref")) == bool(d.get("bridge_url")):
        raise ValueError(f"target {name}: set exactly one of git_ref or bridge_url")
    return TargetCfg(name=name, git_ref=d.get("git_ref"), bridge_url=d.get("bridge_url"),
                     compose=d.get("compose") or _DEFAULT_COMPOSE,
                     redis_container=d.get("redis_container") or "redis",
                     agent_container=d.get("agent_container", "agent_core"),
                     no_idle_handling=bool(d.get("no_idle_handling", False)))


def load_config(path: str | Path, overrides: dict | None = None) -> BenchConfig:
    """Load and validate voice_bench.yaml.

    Args:
        path: Config file.
        overrides: Top-level keys that win over the file (from CLI flags).

    Returns:
        BenchConfig.

    Raises:
        ValueError: suite_version mismatch, or a target without exactly one of git_ref/bridge_url.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if int(raw.get("suite_version", SUITE_VERSION)) != SUITE_VERSION:
        raise ValueError(f"suite_version {raw.get('suite_version')} != harness SUITE_VERSION {SUITE_VERSION}")
    models = raw.get("models") or {}
    b = dict(raw.get("backend") or {})
    backend = BackendCfg(signals_dir=Path(b.pop("signals_dir", "../Signals-DPG")),
                         env_file=Path(b.pop("env_file")) if b.get("env_file") else None,
                         **{k: v for k, v in b.items() if k != "env_file"})
    extra = {k: raw[k] for k in ("status_phrases", "terminal_words") if k in raw}
    return BenchConfig(suite_version=SUITE_VERSION, targets=[_target(t) for t in raw.get("targets") or []],
                       caller=ModelCfg(**models["caller"]), judge=ModelCfg(**models["judge"]), backend=backend,
                       runs=int(raw.get("runs", 1)), runs_per_scenario=dict(raw.get("runs_per_scenario") or {}),
                       max_turns=int(raw.get("max_turns", 14)), phone_prefix=str(raw.get("phone_prefix", "9199000")),
                       results_dir=Path(raw.get("results_dir", "eval_results/voice_bench")), **extra)
