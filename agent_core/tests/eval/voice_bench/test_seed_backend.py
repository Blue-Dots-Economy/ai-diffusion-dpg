import json
import stat
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

from eval.voice_bench.backend import Backend
from eval.voice_bench.config import BackendCfg
from eval.voice_bench.seed import (SEED_VERSION, cleanup_sql, job_rows, load_seed, participant_body, places,
                                   profile_item_state, restore_sql)


def test_job_rows_are_deterministic_and_reserved_range():
    s = load_seed()
    rows = job_rows(s)
    assert len(rows) == 60 and rows == job_rows(s)
    assert {r["item_state"]["jobProviderLocation"] for r in rows} == set(s["cities"])
    assert all(r["phone"].startswith("9199000") and len(r["phone"]) == 12 for r in rows)
    assert len({r["phone"] for r in rows}) == 60
    st = rows[0]["item_state"]
    assert {"jobProviderName", "role", "jobProviderLocation", "hiringManagerName", "hiringManagerPhoneNumber",
            "positions", "natureOfJob"} <= set(st)
    assert SEED_VERSION == 1


def test_job_rows_use_network_json_enum_values():
    rows = job_rows(load_seed())
    assert {r["item_state"]["candidateExperienceType"] for r in rows} == {"Fresher", "Worked before"}
    assert {r["item_state"]["workExperienceYears"] for r in rows} == {"< 1 Year"}
    assert {r["item_state"]["natureOfJob"] for r in rows} == {"Full-time", "Apprenticeship"}
    r = rows[1 * 6 + 2]                                         # trade 1 (Plumber), city 2 (Noida)
    assert r["item_state"]["jobProviderName"] == load_seed()["employers"][(1 * 3 + 2) % 8]
    assert r["item_state"]["salaryMin"] == 9000 + 1000 * ((1 + 4) % 8) and r["item_state"]["salaryMax"] == 18000
    assert r["item_state"]["title"] == "Plumber – Noida" and r["phone"] == "919900081200"


def test_places_lexicon_has_aliases():
    p = places(load_seed())
    assert "लखनऊ" in p["Lucknow"] and "meerut" in p["Meerut"]


def test_participant_body_draft_has_no_age():
    b = participant_body("seeker", "919900090000", "गीता", {"name": "गीता"})
    assert "age" not in b and b["phone_number"] == "+919900090000" and b["domain"] == "seeker"
    assert participant_body("provider", "919900080000", "X", {}, age=None)["item_type"] == "job_posting_1.0"


def test_participant_body_consent_shape():
    live = participant_body("seeker", "919900013000", "दिनेश", {}, age=28)
    assert live["compliance"] == [{"key": "user_terms", "value": True}, {"key": "user_privacy", "value": True},
                                  {"key": "profile_creation", "value": True}]
    # an age-less seeker must not send the user-level pair (Signals answers 400 AGE_REQUIRED on seeker)
    draft = participant_body("seeker", "919900090000", "गीता", {})
    assert [c["key"] for c in draft["compliance"]] == ["profile_creation"]
    provider = participant_body("provider", "919900080000", "X", {})
    assert len(provider["compliance"]) == 3


def test_profile_item_state_matches_save_profile_fields():
    p = {pr["scenario"]: pr for pr in load_seed()["profiles"]}
    st = profile_item_state(p["T14"])
    assert st == {"name": "मोहन लाल", "location": "Lucknow", "phone": "919900014000", "age": 32,
                  "nameOfJobRolesInterestedIn": "इलेक्ट्रीशियन"}
    assert "age" not in profile_item_state(p["T06"]) and "trade" not in profile_item_state(p["T06"])


def test_cleanup_sql_scopes_by_watermark_and_keeps_seed_users():
    sql = cleanup_sql("2026-10-02T10:00:00+00:00", ["u_1", "svc-2"])
    assert sql.startswith("BEGIN;") and sql.strip().endswith("COMMIT;")
    assert "id NOT IN ('u_1','svc-2')" in sql and sql.count("2026-10-02T10:00:00+00:00") == 6
    with pytest.raises(ValueError):
        cleanup_sql("2026-10-02T10:00:00+00:00", ["x'); DROP TABLE items;--"])
    with pytest.raises(ValueError):
        cleanup_sql("not-a-time", ["u_1"])


def test_restore_sql_escapes_quotes():
    snap = {"items": [{"item_id": "11111111-1111-1111-1111-111111111111", "item_state": {"name": "O'Neil"},
                       "item_private_state": "", "lifecycle_status": "live", "item_locations": [],
                       "updated_at": "2026-10-02T10:00:00+00:00"}],
            "users": [{"id": "u_1", "name": "O'Neil", "updated_at": "2026-10-02T10:00:00"}]}
    sql = restore_sql(snap)
    assert "O''Neil" in sql and "WHERE item_id='11111111-1111-1111-1111-111111111111'" in sql
    assert "UPDATE \"user\"" in sql


# ---- Backend with fake subprocess run + httpx MockTransport --------------------------------------------------

_KEY = "sk_signals_" + "ab" * 24
_WATERMARK = "2026-10-02T10:00:00.000000+00:00"
_SNAP = {"items": [{"item_id": "11111111-1111-1111-1111-111111111111", "item_state": {"name": "x"},
                    "item_private_state": "", "lifecycle_status": "live", "item_locations": [],
                    "updated_at": "2026-10-02T09:00:00+00:00"}],
         "users": [{"id": "usr_t13", "name": "x", "updated_at": "2026-10-02T09:00:00"}]}


_OTHER_KEY = "sk_signals_" + "cd" * 24


def _bootstrap_out(with_key=True):
    key_line = f"  apikey:    {_KEY}\n" if with_key else "  apikey:    (existing — capture from first-run logs)\n"
    other = ("\nmatch-engine:\n  org_id:    org_other\n  user_id:   usr_other\n  member_id: mem_0\n"
             f"  apikey:    {_OTHER_KEY}\n")                   # a service printed BEFORE aggregator-dpg
    return (other + "\naggregator-dpg:\n  org_id:    org_abc-123\n  user_id:   usr_svc-1\n  member_id: mem_1\n"
            + key_line + "\nseed complete.\n")


class FakeRun:
    def __init__(self, with_key=True, rc=0, existing_providers=0, indexed=60):
        self.calls, self.with_key, self.rc = [], with_key, rc
        self.existing_providers, self.indexed = existing_providers, indexed

    def __call__(self, args, input=None, **kw):
        self.calls.append((list(args), input))
        out = ""
        if "db:seed:services" in " ".join(args):
            out = _bootstrap_out(self.with_key)
        elif "psql" in args:
            sql = input or ""
            if "count(*) FROM item_search" in sql:
                out = f"{self.indexed}\n"
            elif "count(*) FROM items" in sql:
                out = f"{self.existing_providers}\n"
            elif "item_instance_url" in sql:
                out = "http://signals-api:2742\n"
            elif "created_by" in sql and "json_build_object" not in sql:
                out = "usr_t13\nusr_t14\nusr_t06\n"
            elif "json_build_object" in sql:
                out = json.dumps(_SNAP) + "\n"
            elif "now()" in sql:
                out = _WATERMARK + "\n"
        return subprocess.CompletedProcess(args, self.rc, stdout=out, stderr="")


def _http(posts):
    def h(req):
        posts.append(req)
        return httpx.Response(200, json={"user_id": "u", "items": [{"item_id": str(uuid.uuid4())}]})
    return httpx.Client(transport=httpx.MockTransport(h))


def _backend(tmp_path, run, http=None, signals_dir=None):
    sd = signals_dir or tmp_path / "signals"
    cfg = BackendCfg(signals_dir=sd, signals_url="http://signals", search_url="http://search")
    return Backend(cfg, tmp_path / "results", run=run, http=http, sleep=lambda s: None)


def test_seed_writes_0600_env_file_and_keeps_key_out_of_state(tmp_path):
    posts, run = [], FakeRun()
    b = _backend(tmp_path, run, _http(posts))
    b.seed()
    env = Path(b.state["api_key_env_file"])
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert env.read_text() == (f"BLUE_DOTS_API_KEY={_KEY}\nBLUE_DOTS_SEARCH_API_KEY={_KEY}\n"
                               "BLUE_DOTS_ORG_ID=org_abc-123\n")
    state_text = (tmp_path / "results" / "backend" / "state.json").read_text()
    assert _KEY not in state_text
    st = b.state
    assert st["seed_version"] == SEED_VERSION and st["watermark"] == _WATERMARK and st["snapshot"] == _SNAP
    assert st["seed_user_ids"] == ["usr_t13", "usr_t14", "usr_t06"] and st["service_user_id"] == "usr_svc-1"
    assert st["instance_url"] == "http://signals-api:2742"
    assert len(posts) == 63
    assert {p.headers["x-api-key"] for p in posts} == {_KEY}
    assert {p.headers["x-acting-org-id"] for p in posts} == {"org_abc-123"}
    assert [json.loads(p.content)["domain"] for p in posts].count("provider") == 60


def test_seed_is_idempotent_on_same_version(tmp_path):
    posts, run = [], FakeRun()
    b = _backend(tmp_path, run, _http(posts))
    b.seed()
    n_calls, n_posts = len(run.calls), len(posts)
    b.seed()
    assert len(run.calls) == n_calls and len(posts) == n_posts


def test_seed_raises_when_key_already_minted(tmp_path):
    b = _backend(tmp_path, FakeRun(with_key=False), _http([]))
    with pytest.raises(RuntimeError, match="service key already minted"):
        b.seed()


def test_seed_reuses_existing_env_file_when_key_not_printed(tmp_path):
    posts = []
    b = _backend(tmp_path, FakeRun(), _http(posts))
    b.seed()
    (tmp_path / "results" / "backend" / "state.json").unlink()       # e.g. a partial earlier seed
    b2 = _backend(tmp_path, FakeRun(with_key=False), _http(posts))
    b2.seed()
    assert {p.headers["x-api-key"] for p in posts} == {_KEY}


def test_seed_post_failure_reports_status_not_body(tmp_path):
    def h(req):
        return httpx.Response(400, json={"error": "AGE_REQUIRED", "phone": "+919900013000"})
    b = _backend(tmp_path, FakeRun(), httpx.Client(transport=httpx.MockTransport(h)))
    with pytest.raises(RuntimeError) as ei:
        b.seed()
    assert "400" in str(ei.value) and "9199" not in str(ei.value) and _KEY not in str(ei.value)


def test_seed_index_wait_times_out(tmp_path):
    t = iter(range(0, 100000, 100))
    cfg = BackendCfg(signals_dir=tmp_path, signals_url="http://signals", search_url="http://search")
    b = Backend(cfg, tmp_path / "results", run=FakeRun(indexed=12), http=_http([]), sleep=lambda s: None,
                clock=lambda: next(t))
    with pytest.raises(TimeoutError, match="item_search"):
        b.seed()


def test_cleanup_runs_cleanup_then_restore_via_psql_stdin(tmp_path):
    run = FakeRun()
    b = _backend(tmp_path, run, _http([]))
    b.seed()
    run.calls.clear()
    b.cleanup()
    assert len(run.calls) == 2
    (a1, s1), (a2, s2) = run.calls
    assert a1 == ["docker", "exec", "-i", "signals-postgres", "psql", "-U", "postgres", "-d", "postgresdb", "-At",
                  "-v", "ON_ERROR_STOP=1"] and a2 == a1
    assert s1 == cleanup_sql(_WATERMARK, ["usr_t13", "usr_t14", "usr_t06", "usr_svc-1"])
    assert s2 == restore_sql(_SNAP)


def test_psql_failure_raises(tmp_path):
    b = _backend(tmp_path, FakeRun(rc=3))
    with pytest.raises(RuntimeError, match="psql"):
        b.psql("select 1")


def test_up_requires_local_setup_env_files(tmp_path):
    (tmp_path / "signals" / "local-setup").mkdir(parents=True)
    run = FakeRun()
    with pytest.raises(RuntimeError, match="LOCAL_SETUP.md"):
        _backend(tmp_path, run).up()
    assert run.calls == []


def _signals_tree(tmp_path):
    sd = tmp_path / "signals"
    (sd / "local-setup").mkdir(parents=True)
    (sd / "local-setup" / ".env").write_text("")
    (sd / "local-setup" / ".env.search").write_text("")
    net = {"domains": [{"id": "provider", "item_schemas": {"job_posting_1.0": {"properties": {
        "jobProviderLocation": {"type": "string", "private": True}, "hiringManagerName": {"private": True}}}}}]}
    (sd / "examples" / "schemas" / "blue_dot").mkdir(parents=True)
    (sd / "examples" / "schemas" / "blue_dot" / "network.json").write_text(json.dumps(net))
    return sd


def test_up_unmasks_location_writes_override_and_waits_for_health(tmp_path):
    import yaml
    sd, run, hits = _signals_tree(tmp_path), FakeRun(), []

    def h(req):
        hits.append(str(req.url))
        return httpx.Response(503 if len(hits) == 1 else 200)
    b = _backend(tmp_path, run, httpx.Client(transport=httpx.MockTransport(h)), signals_dir=sd)
    b.up()
    bd = tmp_path / "results" / "backend"
    props = json.loads((bd / "network.json").read_text())["domains"][0]["item_schemas"]["job_posting_1.0"][
        "properties"]
    assert props["jobProviderLocation"]["private"] is False and props["hiringManagerName"]["private"] is True
    ov = yaml.safe_load((bd / "compose.override.yml").read_text())["services"]
    src = str((bd / "network.json").resolve())
    assert ov["signals-api"]["volumes"] == [f"{src}:/app/examples/schemas/blue_dot/network.json:ro"]
    assert ov["signals-bootstrap"]["volumes"] == [f"{src}:/repo/examples/schemas/blue_dot/network.json:ro"]
    assert ov["signals-search-api"]["volumes"] == [f"{src}:/networks/network.json:ro"]
    assert ov["signals-search-worker"]["volumes"] == [f"{src}:/networks/network.json:ro"]
    (args, _), = run.calls
    assert args[:2] == ["docker", "compose"] and "--profile" in args and "--build" in args
    assert args[-7:] == ["postgres", "redis", "signals-bootstrap", "signals-api", "tei-embeddings",
                         "signals-search-api", "signals-search-worker"]
    assert "http://signals/health" in hits and "http://search/health" in hits


def test_seed_refuses_when_provider_items_already_exist(tmp_path):
    posts, run = [], FakeRun(existing_providers=7)
    b = _backend(tmp_path, run, _http(posts))
    with pytest.raises(RuntimeError, match=r"seed data already present: reset the backend volumes \(backend down -v\)"):
        b.seed()
    assert posts == [] and not any("db:seed:services" in " ".join(a) for a, _ in run.calls)


def test_seed_index_wait_rejects_more_than_seeded(tmp_path):
    clock = iter(range(100000))
    cfg = BackendCfg(signals_dir=tmp_path, signals_url="http://signals", search_url="http://search")
    b = Backend(cfg, tmp_path / "results", run=FakeRun(indexed=61), http=_http([]), sleep=lambda s: None,
                clock=lambda: next(clock))
    with pytest.raises(RuntimeError, match="61 live providers, expected exactly 60"):
        b.seed()
    assert next(clock) < 5                                      # raised immediately, no polling to the timeout


def test_seed_parses_the_aggregator_dpg_block_only(tmp_path):
    posts = []
    b = _backend(tmp_path, FakeRun(), _http(posts))
    b.seed()
    assert b.state["service_user_id"] == "usr_svc-1" and _OTHER_KEY not in Path(b.state["api_key_env_file"]).read_text()
    assert {p.headers["x-acting-org-id"] for p in posts} == {"org_abc-123"}


def test_seed_already_minted_ignores_other_services_key(tmp_path):
    b = _backend(tmp_path, FakeRun(with_key=False), _http([]))
    with pytest.raises(RuntimeError, match="service key already minted"):
        b.seed()


def test_down_removes_volumes_only_on_request(tmp_path):
    run = FakeRun()
    b = _backend(tmp_path, run)
    b.down()
    b.down(volumes=True)
    (a1, _), (a2, _) = run.calls
    assert a1[:2] == ["docker", "compose"] and a1[-1] == "down" and "-v" not in a1
    assert a2[-2:] == ["down", "-v"]
