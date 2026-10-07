"""Remote Signals cluster backend: seed over HTTP, wait on the search index, never clean up.

Same surface as ``Backend`` (``state``, ``up``, ``down``, ``seed``, ``cleanup``) so
``__main__`` can hold either. What differs is everything that needed the local
database or compose:

* no compose lifecycle — the cluster is already running;
* no service-key minting — the credentials come from the environment;
* no SQL — the index wait is a signals-search query, and the seed guard is
  ``state.json`` rather than a ``SELECT count(*)``;
* **no cleanup** — the cluster is shared, so nothing here deletes anything. The
  bench instead gives every run fresh caller numbers (see ``suite.phone_for``).

The cluster already holds other people's data, so every count is "at least",
never "exactly".

The API key is read from the environment, used for request headers, and never
printed, logged, or written to state.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx

from eval.voice_bench.config import BackendCfg
from eval.voice_bench.seed import SEED_VERSION, job_rows, load_seed, participant_body, profile_item_state

logger = logging.getLogger(__name__)

_CRED_NAMES = ("BLUE_DOTS_API_KEY", "BLUE_DOTS_SEARCH_API_KEY", "BLUE_DOTS_ORG_ID")


class RemoteBackendError(RuntimeError):
    """A remote backend precondition failed. Never carries a key value."""


class RemoteBackend:
    """Seed and verify a shared Signals cluster for the benchmark.

    Args:
        cfg: Backend config with ``mode: remote``.
        results_dir: Bench results dir; state lives in ``<results_dir>/backend/``.
        http: httpx.Client (injected in tests).
        env: Environment mapping (injected in tests).
        sleep: Poll sleep (injected in tests).
        clock: Monotonic clock for poll timeouts (injected in tests).
        index_timeout_s: Max wait for the seeded jobs to reach the search index.
    """

    def __init__(self, cfg: BackendCfg, results_dir: Path, http: httpx.Client | None = None,
                 *, env: dict | None = None, sleep=time.sleep, clock=time.monotonic,
                 index_timeout_s: float = 900):
        self.cfg = cfg
        self.dir = Path(results_dir) / "backend"
        self._http = http or httpx.Client(timeout=30)
        self._env = os.environ if env is None else env
        self._sleep, self._clock = sleep, clock
        self._index_timeout_s = index_timeout_s

    # ---- state ---------------------------------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def state(self) -> dict:
        """The persisted backend state, or {} before the first seed."""
        if not self._state_path.exists():
            return {}
        return json.loads(self._state_path.read_text(encoding="utf-8"))

    # ---- credentials ---------------------------------------------------------------------------------------
    @property
    def _cred_vars(self) -> tuple[str, str, str]:
        """The three environment variable names this cluster's keys live under."""
        pfx = self.cfg.credentials_env_prefix or ""
        return tuple(f"{pfx}{n}" for n in _CRED_NAMES)          # type: ignore[return-value]

    def _creds(self) -> tuple[str, str, str]:
        """Return (api_key, search_key, org_id) from the environment.

        Each cluster has its own keys, so the names carry the cluster's prefix:
        UAT reads ``UAT_BLUE_DOTS_API_KEY``, the test cluster the unprefixed
        name. Mixing them is the 401/403 trap this indirection exists to avoid.

        Raises:
            RemoteBackendError: A variable is missing. The message names the
                variables and the cluster — never a value.
        """
        api_n, search_n, org_n = self._cred_vars
        missing = [n for n in (api_n, search_n, org_n) if not (self._env.get(n) or "").strip()]
        if missing:
            where = f" for cluster {self.cfg.cluster}" if self.cfg.cluster else ""
            raise RemoteBackendError(
                f"remote backend needs {', '.join(missing)} in the environment{where} "
                f"({self.cfg.signals_url}). Each cluster has its OWN keys: the unprefixed "
                "BLUE_DOTS_* names are the test cluster's and are rejected by UAT. "
                'Load them with `eval "$(~/.config/kkb/bin/kkb-secrets.sh export)"`.')
        return (self._env[api_n].strip(), self._env[search_n].strip(), self._env[org_n].strip())

    def _wrong_cluster_hint(self, status: int, what: str) -> str:
        """Message for a 401/403: almost always this cluster's keys are not the ones loaded."""
        api_n, _s, org_n = self._cred_vars
        other = "test" if (self.cfg.cluster or "") == "uat" else "uat"
        return (f"{what} rejected the credentials (HTTP {status}) at {self.cfg.signals_url}. "
                f"This is cluster '{self.cfg.cluster or 'custom'}', which reads {api_n} / {org_n}. "
                f"Each cluster has its own key set — keys for '{other}' return 401/403 here, not a "
                "clear error. Check the right names are exported, or switch backend.cluster.")

    @property
    def _env_path(self) -> Path:
        return self.dir / "blue_dots.env"

    def _write_env(self, api_key: str, search_key: str, org_id: str) -> None:
        """Write the 0600 env file the target stack mounts into action_gateway.

        Local mode mints a key and writes this file; remote mode copies the
        same three variables out of the environment so everything downstream
        (TargetStack, compose_override, redact) is unchanged.
        """
        fd = os.open(self._env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)                            # also tighten a pre-existing file
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"BLUE_DOTS_API_KEY={api_key}\nBLUE_DOTS_SEARCH_API_KEY={search_key}\n"
                    f"BLUE_DOTS_ORG_ID={org_id}\n")

    # ---- lifecycle -----------------------------------------------------------------------------------------
    def up(self) -> None:
        """Verify the cluster answers and the credentials work. Starts nothing.

        Raises:
            RemoteBackendError: search is unreachable, or the admin API rejects
                the credentials.
        """
        start = time.time()
        api_key, search_key, org_id = self._creds()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write_env(api_key, search_key, org_id)

        # signals-search /health is a real JSON endpoint. The Signals API host
        # serves a single-page app with a catch-all, so GET /health/ready there
        # returns 200 and the SPA's HTML whether or not the API is alive — it is
        # useless as a probe. Read an admin route instead, which also proves the
        # credentials are accepted.
        try:
            r = self._http.get(self.cfg.search_url.rstrip("/") + "/health")
        except httpx.HTTPError as e:
            raise RemoteBackendError(f"signals-search unreachable at {self.cfg.search_url}: {type(e).__name__}")
        if r.status_code != 200:
            raise RemoteBackendError(f"signals-search /health returned HTTP {r.status_code} at {self.cfg.search_url}")
        # /health needs no key, so probe the authenticated route as well: the
        # search key is a different key and can be wrong on its own.
        sr = self._search(search_key, limit=1)
        if sr is not None and sr.status_code in (401, 403):
            raise RemoteBackendError(self._wrong_cluster_hint(sr.status_code, "signals-search"))

        probe = self.cfg.signals_url.rstrip("/") + "/api/v1/admin/participant"
        try:
            # A reserved-range number: a read, and it matches nothing real.
            rr = self._http.get(probe, params={"phone_number": "919900000000"},
                                headers={"x-api-key": api_key, "x-acting-org-id": org_id})
        except httpx.HTTPError as e:
            raise RemoteBackendError(f"Signals API unreachable at {self.cfg.signals_url}: {type(e).__name__}")
        if rr.status_code in (401, 403):
            raise RemoteBackendError(self._wrong_cluster_hint(rr.status_code, "Signals API"))
        if rr.status_code >= 500:
            raise RemoteBackendError(f"Signals API returned HTTP {rr.status_code} at {self.cfg.signals_url}")
        logger.info("remote_backend.up", extra={
            "operation": "remote_backend.up", "status": "success",
            "signals_url": self.cfg.signals_url, "search_url": self.cfg.search_url,
            "latency_ms": int((time.time() - start) * 1000)})

    def down(self, volumes: bool = False) -> None:
        """No-op: the cluster is not ours to stop.

        Args:
            volumes: Ignored. Accepted so the two backends share a signature.
        """
        return None

    # ---- seeding -------------------------------------------------------------------------------------------
    def seed(self) -> None:
        """POST the seed jobs and profiles, wait for the search index, record state.

        Idempotent: returns at once when state.json already has this
        SEED_VERSION. There is no "already has provider items" guard — the
        cluster is shared and always has some. That makes a re-seed after
        deleting state.json a *duplicating* operation, which is why the guard
        lives in state.json and the file should not be deleted casually.

        Raises:
            RemoteBackendError: Credentials missing, or a POST was non-2xx.
            TimeoutError: The seeded jobs did not reach the index in time.
        """
        if self.state.get("seed_version") == SEED_VERSION:
            logger.info("remote_backend.seed", extra={
                "operation": "remote_backend.seed", "status": "skipped", "reason": "already at SEED_VERSION"})
            return
        seed_start = time.time()
        api_key, search_key, org_id = self._creds()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write_env(api_key, search_key, org_id)
        headers = {"x-api-key": api_key, "x-acting-org-id": org_id}

        seed = load_seed()
        jobs = job_rows(seed)
        # Baseline first: the cluster already holds other providers, so the
        # index wait is "grew by at least len(jobs)", not "equals len(jobs)".
        baseline = self._provider_total(search_key)

        for row in jobs:
            self._post(participant_body("provider", row["phone"], row["name"], row["item_state"]), headers)
        profile_item_ids = []
        for p in seed["profiles"]:
            body = self._post(participant_body("seeker", p["phone"], p["name"], profile_item_state(p),
                                               age=p.get("age")), headers)
            profile_item_ids.append(str(uuid.UUID(str(body["items"][0]["item_id"]))))

        self._wait_index(search_key, baseline + len(jobs))
        state = {
            "mode": "remote",
            "seed_version": SEED_VERSION,
            "api_key_env_file": str(self._env_path),
            "signals_url": self.cfg.signals_url,
            "search_url": self.cfg.search_url,
            "instance_url": self.cfg.apply_instance_url,
            "provider_baseline": baseline,
            "profile_item_ids": profile_item_ids,
            # Present so callers that read state["watermark"] to mean "seeded"
            # keep working; remote mode has no watermark to clean up to.
            "watermark": None,
            "cleanup": "disabled: shared cluster",
        }
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("remote_backend.seed", extra={
            "operation": "remote_backend.seed", "status": "success", "jobs": len(jobs),
            "profiles": len(profile_item_ids), "provider_baseline": baseline,
            "latency_ms": int((time.time() - seed_start) * 1000)})

    def _post(self, body: dict, headers: dict) -> dict:
        url = self.cfg.signals_url.rstrip("/") + "/api/v1/admin/participant"
        r = self._http.post(url, json=body, headers=headers)
        if not 200 <= r.status_code < 300:
            raise RemoteBackendError(
                f"POST /api/v1/admin/participant ({body['domain']}) failed: HTTP {r.status_code}")
        return r.json()

    # ---- index ---------------------------------------------------------------------------------------------
    def _search(self, search_key: str, limit: int = 1):
        """POST a broad provider query. Returns the response, or None if unreachable."""
        url = self.cfg.search_url.rstrip("/") + "/v1/search"
        body = {"context": {"messageId": f"voice-bench-{uuid.uuid4()}", "networkId": "blue_dot",
                            "domain": "provider", "itemType": "job_posting_1.0"},
                "message": {"intent": {"textSearch": "jobs"}, "pagination": {"limit": limit, "offset": 0}}}
        try:
            return self._http.post(url, json=body, headers={"x-api-key": search_key})
        except httpx.HTTPError:
            return None

    def _provider_total(self, search_key: str) -> int:
        """Total live provider job postings the search index reports.

        Returns:
            ``message.meta.total`` for a broad provider query, or 0 when the
            query fails (so a transient blip reads as "not yet indexed" rather
            than crashing a seed).
        """
        r = self._search(search_key)
        if r is None or r.status_code != 200:
            return 0
        try:
            return int(((r.json().get("message") or {}).get("meta") or {}).get("total") or 0)
        except (ValueError, TypeError, AttributeError):
            return 0

    def _wait_index(self, search_key: str, at_least: int) -> None:
        """Poll until the index reports at least ``at_least`` provider items.

        Never raises on "more than expected": on a shared cluster other people's
        providers are indexed alongside ours, and someone else seeding during
        our wait is not an error.

        Raises:
            TimeoutError: The threshold was not reached in time.
        """
        deadline = self._clock() + self._index_timeout_s
        while True:
            n = self._provider_total(search_key)
            if n >= at_least:
                return
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"search index reports {n} live providers, wanted at least {at_least}, after "
                    f"{self._index_timeout_s:.0f}s")
            self._sleep(5)

    # ---- cleanup -------------------------------------------------------------------------------------------
    def cleanup(self) -> None:
        """No-op. A shared cluster is never reset from the bench.

        Per-call isolation comes from run-salted caller numbers instead
        (``suite.phone_for``), so a re-run never meets the profiles the previous
        run created.
        """
        return None
