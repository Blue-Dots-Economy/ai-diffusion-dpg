"""Local Signals + signals-search stack: lifecycle, service key, seeding, index wait and watermark cleanup.

The raw service key is held in memory only long enough to write the 0600 env file and send the seed POSTs.
It is never printed, logged, written to state.json, or put into an exception message.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import yaml

from eval.voice_bench.config import BackendCfg
from eval.voice_bench.seed import (SEED_VERSION, cleanup_sql, job_rows, load_seed, participant_body,
                                   profile_item_state, restore_sql, snapshot_sql)

_SERVICES = ["postgres", "redis", "signals-bootstrap", "signals-api", "tei-embeddings", "signals-search-api",
             "signals-search-worker"]
# In-container network.json paths: signals-api runs from /app, signals-bootstrap from /repo
# (NETWORK_CONFIG_LOCAL_FILE is relative to the CWD); the search services mount /networks/network.json.
_NETWORK_MOUNTS = {
    "signals-bootstrap": "/repo/examples/schemas/blue_dot/network.json",
    "signals-api": "/app/examples/schemas/blue_dot/network.json",
    "signals-search-api": "/networks/network.json",
    "signals-search-worker": "/networks/network.json",
}
_KEY_RE = re.compile(r"sk_signals_[0-9a-f]{48}")
# seed_service_users.ts prints one block per service: "<slug>:" then indented "org_id:", "user_id:", "apikey:".
_SERVICE_BLOCK_RE = re.compile(r"^aggregator-dpg:[ \t]*\n((?:[ \t]+.*(?:\n|$))*)", re.M)
_ORG_RE = re.compile(r"^\s*org_id:\s+([A-Za-z0-9_-]+)\s*$", re.M)
_USER_RE = re.compile(r"^\s*user_id:\s+([A-Za-z0-9_-]+)\s*$", re.M)
_ENV_KEY_RE = re.compile(r"^BLUE_DOTS_API_KEY=(sk_signals_[0-9a-f]{48})$", re.M)


class Backend:
    """Drive the Signals-DPG local-setup compose stack for the benchmark.

    Args:
        cfg: Backend config.
        results_dir: Bench results dir; state lives in ``<results_dir>/backend/``.
        run: subprocess.run-compatible callable (injected in tests).
        http: httpx.Client (injected in tests); a default client is created when None.
        sleep: Poll sleep (injected in tests).
        clock: Monotonic clock for poll timeouts (injected in tests).
        health_timeout_s: Max wait for both /health endpoints (TEI start is slow).
        index_timeout_s: Max wait for the seeded jobs to appear in item_search.
    """

    def __init__(self, cfg: BackendCfg, results_dir: Path, run=subprocess.run, http: httpx.Client | None = None,
                 *, sleep=time.sleep, clock=time.monotonic, health_timeout_s: float = 600,
                 index_timeout_s: float = 900):
        self.cfg = cfg
        self.dir = Path(results_dir) / "backend"
        self._run = run
        self._http = http or httpx.Client(timeout=30)
        self._sleep, self._clock = sleep, clock
        self._health_timeout_s, self._index_timeout_s = health_timeout_s, index_timeout_s

    # ---- paths ---------------------------------------------------------------------------------------------
    @property
    def _local_setup(self) -> Path:
        return Path(self.cfg.signals_dir) / "local-setup"

    @property
    def _state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def _env_path(self) -> Path:
        return self.dir / "blue_dots.env"

    @property
    def _override_path(self) -> Path:
        return self.dir / "compose.override.yml"

    def _compose(self) -> list[str]:
        return ["docker", "compose", "-f", str(self._local_setup / "docker-compose.yml"),
                "-f", str(self._override_path.resolve()), "--profile", "search"]

    @property
    def state(self) -> dict:
        """The persisted backend state, or {} before the first seed."""
        if not self._state_path.exists():
            return {}
        return json.loads(self._state_path.read_text(encoding="utf-8"))

    # ---- lifecycle -----------------------------------------------------------------------------------------
    def up(self) -> None:
        """Start the stack with the location-unmasked network.json mounted, then wait for both /health.

        Raises:
            RuntimeError: local-setup/.env or .env.search is missing, backend.network_json is missing, or
                compose fails.
            TimeoutError: a /health endpoint did not answer 200 in time.
        """
        missing = [n for n in (".env", ".env.search") if not (self._local_setup / n).exists()]
        if missing:
            raise RuntimeError(f"missing {', '.join(missing)} in {self._local_setup}: create them per "
                               "Signals-DPG local-setup/LOCAL_SETUP.md (the harness never creates secrets files)")
        if not Path(self.cfg.network_json).is_file():
            raise RuntimeError(f"backend.network_json not found: {self.cfg.network_json} (relative paths resolve "
                               "against the current directory; run from agent_core/ or set an absolute path)")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write_network_json()
        self._write_override()
        self._check(self._compose() + ["up", "-d", "--build", *_SERVICES], "docker compose up")
        # Signals-DPG serves /health/ready (probes Postgres+Redis), not /health (returns 404)
        self._wait_health(self.cfg.signals_url.rstrip("/") + "/health/ready")
        # signals-search serves /health (no probes needed)
        self._wait_health(self.cfg.search_url.rstrip("/") + "/health")

    def down(self, volumes: bool = False) -> None:
        """Stop the stack.

        Args:
            volumes: Also remove the named volumes (``down -v``): wipes the Signals DB, so the next seed is clean.
        """
        self._check(self._compose() + ["down"] + (["-v"] if volumes else []), "docker compose down")

    def _write_network_json(self) -> None:
        """Copy backend.network_json, flipping only provider job_posting_1.0 ``jobProviderLocation`` to public."""
        net = json.loads(Path(self.cfg.network_json).read_text(encoding="utf-8"))
        for d in net.get("domains") or []:
            if d.get("id") != "provider":
                continue
            prop = (((d.get("item_schemas") or {}).get("job_posting_1.0") or {}).get("properties") or {}).get(
                "jobProviderLocation")
            if prop is not None:
                prop["private"] = False                 # public on UAT; search needs it unmasked
        (self.dir / "network.json").write_text(json.dumps(net, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_override(self) -> None:
        src = str((self.dir / "network.json").resolve())
        doc = {"services": {svc: {"volumes": [f"{src}:{dst}:ro"]} for svc, dst in _NETWORK_MOUNTS.items()}}
        self._override_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def _wait_health(self, url: str) -> None:
        deadline = self._clock() + self._health_timeout_s
        while True:
            try:
                if self._http.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self._clock() >= deadline:
                raise TimeoutError(f"{url} not healthy after {self._health_timeout_s:.0f}s")
            self._sleep(5)

    # ---- seeding -------------------------------------------------------------------------------------------
    def seed(self) -> None:
        """Mint/read the service key, POST the seed, wait for the index, snapshot and record the watermark.

        Idempotent: returns at once when state.json already has this SEED_VERSION.

        Raises:
            RuntimeError: provider items already exist (a partial earlier seed); the service key was already
                minted and no env file holds it; a POST is non-2xx; item_search has more live providers than seeded.
            TimeoutError: the 60 jobs did not reach item_search in time.
        """
        if self.state.get("seed_version") == SEED_VERSION:
            return
        # POSTs without item_id always insert, so seeding over existing data would duplicate the jobs.
        if int(self.psql("SELECT count(*) FROM items WHERE item_domain='provider';").strip() or 0) > 0:
            raise RuntimeError("seed data already present: reset the backend volumes (backend down -v) before seeding")
        self.dir.mkdir(parents=True, exist_ok=True)
        org_id, service_user_id, key = self._service_identity()
        headers = {"x-api-key": key, "x-acting-org-id": org_id}
        seed = load_seed()
        jobs = job_rows(seed)
        for row in jobs:
            self._post(participant_body("provider", row["phone"], row["name"], row["item_state"]), headers)
        profile_item_ids = []
        for p in seed["profiles"]:
            body = self._post(participant_body("seeker", p["phone"], p["name"], profile_item_state(p),
                                               age=p.get("age")), headers)
            profile_item_ids.append(str(uuid.UUID(str(body["items"][0]["item_id"]))))
        self._wait_index(len(jobs))
        instance_url = self.psql(
            "SELECT item_instance_url FROM items WHERE item_domain='provider' LIMIT 1;").strip()
        seed_user_ids = self._created_by(profile_item_ids)
        snapshot = json.loads(self.psql(snapshot_sql(seed_user_ids)).strip())
        watermark = self.psql(
            "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"');").strip()
        state = {"watermark": watermark, "seed_version": SEED_VERSION, "api_key_env_file": str(self._env_path),
                 "instance_url": instance_url, "snapshot": snapshot, "seed_user_ids": seed_user_ids,
                 "service_user_id": service_user_id}
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _service_identity(self) -> tuple[str, str, str]:
        cp = self._run(self._compose() + ["run", "--rm", "signals-bootstrap", "sh", "-lc",
                                          "pnpm --filter api db:seed:services"],
                       capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            raise RuntimeError(f"db:seed:services failed (exit {cp.returncode})")
        block = _SERVICE_BLOCK_RE.search(cp.stdout or "")
        out = block.group(1) if block else ""
        org, usr = _ORG_RE.search(out), _USER_RE.search(out)
        if not org or not usr:
            raise RuntimeError("db:seed:services output has no aggregator-dpg org_id/user_id")
        key_m = _KEY_RE.search(out)
        if key_m:
            key = key_m.group(0)
            self._write_env(key, org.group(1))
        else:
            prev = _ENV_KEY_RE.search(self._env_path.read_text(encoding="utf-8")) if self._env_path.exists() else None
            if not prev:
                raise RuntimeError("service key already minted: delete the aggregator-dpg apikey row and re-run "
                                   "backend seed")
            key = prev.group(1)
            self._write_env(key, org.group(1))
        return org.group(1), usr.group(1), key

    def _write_env(self, key: str, org_id: str) -> None:
        fd = os.open(self._env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)                            # also tighten a pre-existing file
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"BLUE_DOTS_API_KEY={key}\nBLUE_DOTS_SEARCH_API_KEY={key}\nBLUE_DOTS_ORG_ID={org_id}\n")

    def _post(self, body: dict, headers: dict) -> dict:
        url = self.cfg.signals_url.rstrip("/") + "/api/v1/admin/participant"
        r = self._http.post(url, json=body, headers=headers)
        if not 200 <= r.status_code < 300:
            raise RuntimeError(f"POST /api/v1/admin/participant ({body['domain']}) failed: HTTP {r.status_code}")
        return r.json()

    def _wait_index(self, expected: int) -> None:
        sql = "SELECT count(*) FROM item_search WHERE item_domain='provider' AND lifecycle_status='live';"
        deadline = self._clock() + self._index_timeout_s
        while True:
            n = int(self.psql(sql).strip() or 0)
            if n == expected:
                return
            if n > expected:
                raise RuntimeError(f"item_search has {n} live providers, expected exactly {expected}: reset the "
                                   "backend volumes (backend down -v) and re-seed")
            if self._clock() >= deadline:
                raise TimeoutError(f"item_search has {n}/{expected} live providers after "
                                   f"{self._index_timeout_s:.0f}s")
            self._sleep(5)

    def _created_by(self, item_ids: list[str]) -> list[str]:
        quoted = ",".join(f"'{i}'" for i in item_ids)            # item_ids were validated as UUIDs
        out = self.psql(f"SELECT created_by FROM items WHERE item_id IN ({quoted});")
        by_order = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return list(dict.fromkeys(by_order))

    # ---- cleanup -------------------------------------------------------------------------------------------
    def cleanup(self) -> None:
        """Delete everything created after the seed watermark and restore the snapshotted seed rows.

        Raises:
            RuntimeError: no seed state yet, or psql fails.
        """
        st = self.state
        if not st.get("watermark"):
            raise RuntimeError("no backend state: run backend seed first")
        self.psql(cleanup_sql(st["watermark"], st["seed_user_ids"] + [st["service_user_id"]]))
        self.psql(restore_sql(st["snapshot"]))

    def psql(self, sql: str) -> str:
        """Run SQL (on stdin) in the Postgres container; return stdout.

        Raises:
            RuntimeError: psql exits non-zero.
        """
        cp = self._run(["docker", "exec", "-i", self.cfg.postgres_container, "psql", "-U", "postgres",
                        "-d", "postgresdb", "-At", "-v", "ON_ERROR_STOP=1"],
                       input=sql, capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            first = ((cp.stderr or "").strip().splitlines() or [""])[0][:200]
            raise RuntimeError(f"psql failed (exit {cp.returncode}): {first}")
        return cp.stdout or ""

    def _check(self, args: list[str], what: str) -> None:
        cp = self._run(args, check=False)
        if cp.returncode != 0:
            raise RuntimeError(f"{what} failed (exit {cp.returncode})")
