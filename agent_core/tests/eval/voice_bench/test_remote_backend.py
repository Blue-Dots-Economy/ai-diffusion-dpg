"""RemoteBackend, the remote half of BackendCfg, and the run-salted phone numbers.

No real network and no real cluster: a fake httpx client records calls and
replays canned responses.
"""
from __future__ import annotations

import json

import pytest

from eval.voice_bench.backend_remote import RemoteBackend, RemoteBackendError
from eval.voice_bench.config import BackendCfg, _backend
from eval.voice_bench.seed import SEED_VERSION
from eval.voice_bench.suite import phone_for, run_salt

ENV = {"BLUE_DOTS_API_KEY": "sk_test_key", "BLUE_DOTS_SEARCH_API_KEY": "sk_test_search",
       "BLUE_DOTS_ORG_ID": "org-123"}


class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeHttp:
    """Minimal httpx.Client stand-in. ``totals`` is popped per search call."""

    def __init__(self, *, totals=None, post_status=201, get_status=200):
        self.totals = list(totals or [0])
        self.post_status, self.get_status = post_status, get_status
        self.posts, self.gets = [], []

    def get(self, url, params=None, headers=None):
        self.gets.append((url, params, headers))
        if url.endswith("/health"):
            return _Resp(200, {"status": "ok"})
        return _Resp(self.get_status, {})

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json, headers))
        if url.endswith("/v1/search"):
            total = self.totals.pop(0) if len(self.totals) > 1 else self.totals[0]
            return _Resp(200, {"message": {"items": [], "meta": {"total": total}}})
        return _Resp(self.post_status, {"items": [{"item_id": "11111111-1111-4111-8111-111111111111"}]})


def _cfg(**kw) -> BackendCfg:
    base = dict(mode="remote", signals_url="https://cluster.example",
                search_url="https://cluster.example/signals-search")
    base.update(kw)
    return BackendCfg(**base)


def _backend_obj(tmp_path, http, env=None, **kw) -> RemoteBackend:
    return RemoteBackend(_cfg(), tmp_path, http=http, env=dict(ENV if env is None else env),
                         sleep=lambda _s: None, **kw)


# ---- config ------------------------------------------------------------------------------------------------
def test_remote_mode_defaults_to_uat():
    """UAT holds the real curated provider data; the test cluster is LOADTEST rows."""
    cfg = _backend({"mode": "remote"})
    assert cfg.cluster == "uat"
    assert cfg.signals_url == "https://signals.bluedotseconomy.org"
    assert cfg.search_url == "https://signals.bluedotseconomy.org/signals-search"
    assert cfg.credentials_env_prefix == "UAT_"
    assert cfg.is_remote


def test_test_cluster_is_still_selectable_and_brings_its_own_credential_names():
    cfg = _backend({"mode": "remote", "cluster": "test"})
    assert cfg.signals_url == "https://signals-services.freedynamicdns.net"
    assert cfg.credentials_env_prefix == ""        # the unprefixed Keychain names


def test_unknown_cluster_is_rejected():
    with pytest.raises(ValueError, match="backend.cluster must be one of"):
        _backend({"mode": "remote", "cluster": "prod"})


def test_cluster_can_be_switched_by_env(monkeypatch):
    monkeypatch.setenv("VOICE_BENCH_CLUSTER", "test")
    assert _backend({"mode": "remote"}).cluster == "test"


def test_credentials_are_read_under_the_cluster_prefix(tmp_path):
    """UAT keys and test-cluster keys coexist in the environment."""
    cfg = BackendCfg(mode="remote", cluster="uat", credentials_env_prefix="UAT_",
                     signals_url="https://uat.example", search_url="https://uat.example/signals-search")
    env = {"BLUE_DOTS_API_KEY": "test_cluster_key", "BLUE_DOTS_SEARCH_API_KEY": "t", "BLUE_DOTS_ORG_ID": "t",
           "UAT_BLUE_DOTS_API_KEY": "uat_key", "UAT_BLUE_DOTS_SEARCH_API_KEY": "uat_s",
           "UAT_BLUE_DOTS_ORG_ID": "uat_org"}
    b = RemoteBackend(cfg, tmp_path, http=_FakeHttp(), env=env, sleep=lambda _s: None)
    b.up()
    sent = [h for _u, _p, h in b._http.gets if h]
    assert any(h.get("x-acting-org-id") == "uat_org" for h in sent)


def test_missing_uat_keys_say_which_names_and_that_clusters_differ(tmp_path):
    """The unprefixed keys being present must not look like success."""
    cfg = BackendCfg(mode="remote", cluster="uat", credentials_env_prefix="UAT_",
                     signals_url="https://uat.example", search_url="https://uat.example/signals-search")
    b = RemoteBackend(cfg, tmp_path, http=_FakeHttp(), env=dict(ENV), sleep=lambda _s: None)
    with pytest.raises(RemoteBackendError) as e:
        b.up()
    msg = str(e.value)
    assert "UAT_BLUE_DOTS_API_KEY" in msg and "cluster uat" in msg
    assert "OWN keys" in msg
    assert "sk_test_key" not in msg


def test_a_rejected_key_names_the_cluster_and_the_variables(tmp_path):
    """401/403 is the wrong-cluster-keys symptom; the message must say so."""
    cfg = BackendCfg(mode="remote", cluster="uat", credentials_env_prefix="UAT_",
                     signals_url="https://uat.example", search_url="https://uat.example/signals-search")
    env = {f"UAT_{k}": v for k, v in ENV.items()}
    b = RemoteBackend(cfg, tmp_path, http=_FakeHttp(get_status=403), env=env, sleep=lambda _s: None)
    with pytest.raises(RemoteBackendError) as e:
        b.up()
    msg = str(e.value)
    assert "403" in msg and "UAT_BLUE_DOTS_API_KEY" in msg
    assert "own key set" in msg and "'test'" in msg       # points at the other cluster


def test_remote_instance_url_falls_back_to_signals_url():
    cfg = _backend({"mode": "remote", "signals_url": "https://c.example"})
    assert cfg.apply_instance_url == "https://c.example"


def test_remote_instance_url_is_used_when_given():
    cfg = _backend({"mode": "remote", "signals_url": "https://c.example",
                    "instance_url": "https://apply.example"})
    assert cfg.apply_instance_url == "https://apply.example"


def test_local_mode_still_requires_signals_dir():
    with pytest.raises(ValueError, match="signals_dir"):
        _backend({"mode": "local"})


def test_local_mode_unchanged_when_configured():
    cfg = _backend({"mode": "local", "signals_dir": "../Signals-DPG"})
    assert not cfg.is_remote and cfg.postgres_container == "signals-postgres"
    assert cfg.signals_url == "http://localhost:2742"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="must be 'local' or 'remote'"):
        _backend({"mode": "staging"})


def test_env_overrides_the_file(monkeypatch):
    monkeypatch.setenv("VOICE_BENCH_BACKEND_MODE", "remote")
    monkeypatch.setenv("VOICE_BENCH_SIGNALS_URL", "https://from-env.example")
    cfg = _backend({"mode": "local", "signals_dir": "../Signals-DPG"})
    assert cfg.is_remote and cfg.signals_url == "https://from-env.example"


# ---- credentials -------------------------------------------------------------------------------------------
def test_missing_credentials_name_the_variables_and_never_a_value(tmp_path):
    b = _backend_obj(tmp_path, _FakeHttp(), env={"BLUE_DOTS_API_KEY": "sk_secret_value"})
    with pytest.raises(RemoteBackendError) as e:
        b.up()
    msg = str(e.value)
    assert "BLUE_DOTS_SEARCH_API_KEY" in msg and "BLUE_DOTS_ORG_ID" in msg
    assert "sk_secret_value" not in msg


def test_up_writes_a_0600_env_file_for_the_target_stack(tmp_path):
    b = _backend_obj(tmp_path, _FakeHttp())
    b.up()
    env_path = tmp_path / "backend" / "blue_dots.env"
    assert env_path.exists()
    assert oct(env_path.stat().st_mode)[-3:] == "600"
    assert "BLUE_DOTS_ORG_ID=org-123" in env_path.read_text(encoding="utf-8")


def test_up_rejects_bad_credentials(tmp_path):
    b = _backend_obj(tmp_path, _FakeHttp(get_status=401))
    with pytest.raises(RemoteBackendError, match="rejected the credentials"):
        b.up()


def test_up_does_not_use_the_spa_health_route_as_a_probe(tmp_path):
    """The Signals host serves a SPA catch-all, so /health/ready there is always 200."""
    http = _FakeHttp()
    _backend_obj(tmp_path, http).up()
    probed = [u for u, _p, _h in http.gets]
    assert not any(u.endswith("/health/ready") for u in probed)
    assert any(u.endswith("/api/v1/admin/participant") for u in probed)


# ---- seeding -----------------------------------------------------------------------------------------------
def test_seed_posts_jobs_and_profiles_then_records_state(tmp_path):
    http = _FakeHttp(totals=[100, 100, 100_000])    # baseline, then indexed
    b = _backend_obj(tmp_path, http)
    b.seed()
    st = b.state
    assert st["seed_version"] == SEED_VERSION
    assert st["mode"] == "remote"
    assert st["provider_baseline"] == 100
    assert st["cleanup"].startswith("disabled")
    assert st["instance_url"] == "https://cluster.example"
    participant_posts = [u for u, _b, _h in http.posts if u.endswith("/api/v1/admin/participant")]
    assert len(participant_posts) > 0


def test_seed_is_idempotent(tmp_path):
    http = _FakeHttp(totals=[0, 100_000])
    b = _backend_obj(tmp_path, http)
    b.seed()
    before = len(http.posts)
    b.seed()
    assert len(http.posts) == before      # second call did nothing


def test_seed_waits_for_at_least_the_baseline_plus_seeded(tmp_path):
    """A shared cluster always has other providers: the wait is >=, never ==."""
    http = _FakeHttp(totals=[4000, 4000, 4000, 9_999_999])
    b = _backend_obj(tmp_path, http)
    b.seed()
    assert b.state["provider_baseline"] == 4000


def test_more_items_than_expected_is_not_an_error(tmp_path):
    """Someone else seeding during our wait must not fail the run."""
    http = _FakeHttp(totals=[10, 9_999_999])
    _backend_obj(tmp_path, http).seed()        # no raise


def test_index_wait_times_out_rather_than_hanging(tmp_path):
    ticks = iter([0, 1, 2, 10_000])
    b = RemoteBackend(_cfg(), tmp_path, http=_FakeHttp(totals=[0]), env=dict(ENV),
                      sleep=lambda _s: None, clock=lambda: next(ticks), index_timeout_s=5)
    with pytest.raises(TimeoutError, match="at least"):
        b.seed()


def test_failed_post_raises_without_leaking_the_key(tmp_path):
    b = _backend_obj(tmp_path, _FakeHttp(totals=[0], post_status=500))
    with pytest.raises(RemoteBackendError) as e:
        b.seed()
    assert "sk_test_key" not in str(e.value)


def test_state_file_never_contains_a_key(tmp_path):
    b = _backend_obj(tmp_path, _FakeHttp(totals=[0, 9_999_999]))
    b.seed()
    raw = (tmp_path / "backend" / "state.json").read_text(encoding="utf-8")
    assert "sk_test_key" not in raw and "sk_test_search" not in raw
    assert json.loads(raw)["seed_version"] == SEED_VERSION


# ---- cleanup -----------------------------------------------------------------------------------------------
def test_cleanup_is_a_no_op_on_a_shared_cluster(tmp_path):
    http = _FakeHttp()
    b = _backend_obj(tmp_path, http)
    b.cleanup()
    assert http.posts == [] and http.gets == []       # nothing was called at all


def test_down_is_a_no_op(tmp_path):
    http = _FakeHttp()
    _backend_obj(tmp_path, http).down(volumes=True)
    assert http.posts == [] and http.gets == []


# ---- phone salt --------------------------------------------------------------------------------------------
def test_phone_numbers_stay_twelve_digits_and_keep_the_reserved_prefix():
    for salt in ("00", "07", "99"):
        n = phone_for("9199000", "T14", 2, salt)
        assert len(n) == 12 and n.isdigit()
        assert n.startswith("9199000")


def test_local_mode_numbers_are_byte_identical_to_before_the_salt():
    """The salt lives in the trailing pad, so the default must change nothing."""
    assert phone_for("9199000", "T01", 0) == "919900001000"
    assert phone_for("9199000", "T01", 1) == "919900001100"
    assert phone_for("9199000", "T14", 0) == "919900014000"


def test_a_different_salt_gives_a_different_caller():
    a = phone_for("9199000", "T01", 0, "11")
    b = phone_for("9199000", "T01", 0, "12")
    assert a != b, "a re-run must not reuse the previous run's caller number"


def test_scenario_and_run_still_separate_callers_within_one_run():
    salt = "42"
    numbers = {phone_for("9199000", f"T{i:02d}", r, salt) for i in range(1, 15) for r in range(3)}
    assert len(numbers) == 14 * 3       # no collisions inside a run


def test_run_salt_is_two_digits_and_moves_between_runs():
    assert run_salt(now=0) == "00"
    assert len(run_salt(now=1_700_000_000)) == 2
    assert run_salt(now=1_700_000_000) != run_salt(now=1_700_000_000 + 600)


# ---- pull_images -------------------------------------------------------------------------------------------
def test_pull_images_defaults_off_so_existing_targets_are_unchanged():
    from eval.voice_bench.config import _target
    assert _target({"name": "m", "git_ref": "abc1234"}).pull_images is False


def test_pull_images_is_read_from_the_target():
    from eval.voice_bench.config import _target
    t = _target({"name": "ghcr", "git_ref": "abc1234",
                 "compose": "automation/docker/docker-compose.dev.yml", "pull_images": True})
    assert t.pull_images is True and t.compose.endswith("docker-compose.dev.yml")
