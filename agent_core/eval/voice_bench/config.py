"""voice_bench.yaml loading (spec §5). Flags override file values."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from eval.voice_bench import SUITE_VERSION

_DEFAULT_COMPOSE = "automation/docker/docker-compose.yml"
# The Blue Dots UP-Ghaziabad schema the local backend runs (network blue_dot; seeker/provider/service_provider).
# Like every relative path in voice_bench.yaml, it resolves against the CWD (run the CLI from agent_core/).
DEFAULT_NETWORK_JSON = Path("../../bluedots-schemas/blue_dot/up-gzb/network.json")


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


# Remote mode talks to a Signals cluster that is already running and shared with
# other people.
#
# Each cluster has its OWN credentials. Pointing the URLs at one cluster while
# the environment still holds another's keys yields 401/403, not a clear error
# (the same trap action_gateway.yaml warns about), so the URL triplet and the
# credential names move together as one preset.
#
# UAT is the default: it holds real curated provider data. The test cluster is
# saturated with LOADTEST rows (role == company, jobProviderLocation null), so
# scenarios that grade against job content score badly there for reasons that
# have nothing to do with the agent.
CLUSTERS = {
    "uat": {
        "signals_url": "https://signals.bluedotseconomy.org",
        "search_url": "https://signals.bluedotseconomy.org/signals-search",
        "credentials_env_prefix": "UAT_",
    },
    "test": {
        "signals_url": "https://signals-services.freedynamicdns.net",
        "search_url": "https://signals-services.freedynamicdns.net/signals-search",
        "credentials_env_prefix": "",
    },
}
DEFAULT_CLUSTER = "uat"


@dataclass(frozen=True)
class BackendCfg:
    """Where the Signals half of the bench lives.

    ``mode: local`` drives the Signals-DPG compose stack, seeds it and cleans up
    after every call. ``mode: remote`` points at a cluster that is already
    running: no compose, no service-key minting, and **no cleanup** — a shared
    cluster must never be mass-deleted from here. The agent half (agent_core,
    its Redis, the bridge, the tap) is identical in both modes.

    Attributes:
        mode: ``local`` or ``remote``.
        signals_dir: Signals-DPG checkout; local mode only.
        postgres_container: Signals Postgres container; local mode only.
        instance_url: ``item_instance_url`` the apply flow posts to. Remote mode
            falls back to ``signals_url`` when unset, which is what both
            clusters serve today.
        credentials_env_prefix: Prefixed onto the credential variable names, so
            each cluster's keys can live side by side in the environment.
            ``"UAT_"`` reads ``UAT_BLUE_DOTS_API_KEY``; ``""`` reads the
            unprefixed test-cluster names.
        cluster: Which preset in ``CLUSTERS`` was selected, for messages.
    """

    mode: str = "local"
    signals_dir: Path | None = None
    signals_url: str = "http://localhost:2742"
    search_url: str = "http://localhost:3100"
    instance_url: str | None = None
    credentials_env_prefix: str = ""
    cluster: str | None = None
    tap_port: int = 18742
    postgres_container: str | None = "signals-postgres"
    env_file: Path | None = None
    network_json: Path = DEFAULT_NETWORK_JSON

    @property
    def is_remote(self) -> bool:
        return self.mode == "remote"

    @property
    def apply_instance_url(self) -> str:
        """The instance_url the target stack is pointed at."""
        return self.instance_url or self.signals_url


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


def _backend(b: dict) -> BackendCfg:
    """Build BackendCfg, applying env overrides and per-mode requirements.

    Env wins over the file so a URL or a cluster can be switched for one run
    without editing voice_bench.yaml: VOICE_BENCH_BACKEND_MODE,
    VOICE_BENCH_SIGNALS_URL, VOICE_BENCH_SEARCH_URL,
    VOICE_BENCH_SIGNALS_INSTANCE_URL.

    Args:
        b: The raw ``backend`` mapping from voice_bench.yaml.

    Returns:
        BackendCfg.

    Raises:
        ValueError: An unknown mode, a local mode missing signals_dir or
            postgres_container, or a remote mode with no signals_url/search_url.
            Remote never falls back to a default URL — an unset URL is an error,
            so a typo cannot silently send a run at UAT.
    """
    # pop() unconditionally: an env override must still consume the file key,
    # or it reaches BackendCfg a second time as **b.
    file_mode = b.pop("mode", "local")
    mode = str(os.environ.get("VOICE_BENCH_BACKEND_MODE") or file_mode).strip().lower()
    if mode not in ("local", "remote"):
        raise ValueError(f"backend.mode must be 'local' or 'remote', got {mode!r}")

    signals_dir = b.pop("signals_dir", None)
    network_json = Path(b.pop("network_json", None) or DEFAULT_NETWORK_JSON)
    env_file = Path(b.pop("env_file")) if b.get("env_file") else None
    b.pop("env_file", None)
    postgres_container = b.pop("postgres_container", "signals-postgres" if mode == "local" else None)

    cluster = None
    if mode == "remote":
        # A named cluster supplies URLs *and* credential names together; either
        # can still be overridden individually.
        cluster = str(os.environ.get("VOICE_BENCH_CLUSTER")
                      or b.pop("cluster", None) or DEFAULT_CLUSTER).strip().lower()
        if cluster not in CLUSTERS:
            raise ValueError(f"backend.cluster must be one of {sorted(CLUSTERS)}, got {cluster!r}")
        preset = CLUSTERS[cluster]
        signals_url = (os.environ.get("VOICE_BENCH_SIGNALS_URL")
                       or b.pop("signals_url", None) or preset["signals_url"])
        search_url = (os.environ.get("VOICE_BENCH_SEARCH_URL")
                      or b.pop("search_url", None) or preset["search_url"])
        instance_url = (os.environ.get("VOICE_BENCH_SIGNALS_INSTANCE_URL")
                        or b.pop("instance_url", None) or signals_url)
        creds_prefix = os.environ.get("VOICE_BENCH_CREDENTIALS_PREFIX")
        if creds_prefix is None:
            creds_prefix = b.pop("credentials_env_prefix", None)
        if creds_prefix is None:
            creds_prefix = preset["credentials_env_prefix"]
        b.pop("credentials_env_prefix", None)
        if not signals_url or not search_url:
            raise ValueError("backend.mode=remote needs signals_url and search_url")
    else:
        b.pop("cluster", None)
        creds_prefix = b.pop("credentials_env_prefix", "")
        signals_url = os.environ.get("VOICE_BENCH_SIGNALS_URL") or b.pop("signals_url", "http://localhost:2742")
        search_url = os.environ.get("VOICE_BENCH_SEARCH_URL") or b.pop("search_url", "http://localhost:3100")
        instance_url = b.pop("instance_url", None)
        if not signals_dir:
            raise ValueError("backend.mode=local needs backend.signals_dir (the Signals-DPG checkout)")
        if not postgres_container:
            raise ValueError("backend.mode=local needs backend.postgres_container")

    return BackendCfg(mode=mode, signals_dir=Path(signals_dir) if signals_dir else None,
                      signals_url=signals_url, search_url=search_url, instance_url=instance_url,
                      credentials_env_prefix=str(creds_prefix or ""), cluster=cluster,
                      postgres_container=postgres_container, env_file=env_file, network_json=network_json,
                      **{k: v for k, v in b.items()})


def _target(d: dict) -> TargetCfg:
    name = d.get("name") or "?"
    if bool(d.get("git_ref")) == bool(d.get("bridge_url")):
        raise ValueError(f"target {name}: set exactly one of git_ref or bridge_url")
    # str(): YAML reads an unquoted all-digit short sha as an int.
    ref = d.get("git_ref")
    return TargetCfg(name=name, git_ref=str(ref) if ref else None, bridge_url=d.get("bridge_url"),
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
    backend = _backend(dict(raw.get("backend") or {}))
    extra = {k: raw[k] for k in ("status_phrases", "terminal_words") if k in raw}
    return BenchConfig(suite_version=SUITE_VERSION, targets=[_target(t) for t in raw.get("targets") or []],
                       caller=ModelCfg(**models["caller"]), judge=ModelCfg(**models["judge"]), backend=backend,
                       runs=int(raw.get("runs", 1)), runs_per_scenario=dict(raw.get("runs_per_scenario") or {}),
                       max_turns=int(raw.get("max_turns", 14)), phone_prefix=str(raw.get("phone_prefix", "9199000")),
                       results_dir=Path(raw.get("results_dir", "eval_results/voice_bench")), **extra)
