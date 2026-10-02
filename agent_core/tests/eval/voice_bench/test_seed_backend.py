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
    assert "title" not in r["item_state"] and r["phone"] == "919900081200"   # up-gzb job_posting_1.0 has no title


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


def _backend(tmp_path, run, http=None, signals_dir=None, network_json=None):
    sd = signals_dir or tmp_path / "signals"
    cfg = BackendCfg(signals_dir=sd, signals_url="http://signals", search_url="http://search",
                     network_json=network_json or tmp_path / "up-gzb" / "network.json")
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
    net = {"id": "blue_dot", "domains": [
        {"id": "seeker", "item_schemas": {"profile_1.0": {"properties": {
            "location": {"type": "string", "private": True}}}}},
        {"id": "provider", "item_schemas": {"job_posting_1.0": {"properties": {
            "jobProviderLocation": {"type": "string", "private": True}, "hiringManagerName": {"private": True}}}}}]}
    (tmp_path / "up-gzb").mkdir()
    (tmp_path / "up-gzb" / "network.json").write_text(json.dumps(net))
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
    out = json.loads((bd / "network.json").read_text())
    props = out["domains"][1]["item_schemas"]["job_posting_1.0"]["properties"]
    assert props["jobProviderLocation"]["private"] is False and props["hiringManagerName"]["private"] is True
    # U1: built from backend.network_json; nothing but provider jobProviderLocation is touched
    assert out["domains"][0]["item_schemas"]["profile_1.0"]["properties"]["location"]["private"] is True
    src_net = json.loads((tmp_path / "up-gzb" / "network.json").read_text())
    src_net["domains"][1]["item_schemas"]["job_posting_1.0"]["properties"]["jobProviderLocation"]["private"] = False
    assert out == src_net
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
    # Signals-DPG serves /health/ready (not /health which returns 404), search serves /health
    assert "http://signals/health/ready" in hits and "http://search/health" in hits
    assert "http://signals/health" not in hits


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


def test_up_polls_signals_health_ready_not_health_which_returns_404(tmp_path):
    """Verify that up() polls /health/ready for Signals (not /health which returns 404).

    If up() polled /health, the request would timeout since Signals returns 404 (only /health/ready
    probes Postgres+Redis). This test verifies the correct endpoint is polled.
    """
    sd, run = _signals_tree(tmp_path), FakeRun()
    hits = []

    def h(req):
        hits.append(str(req.url))
        url = str(req.url)
        # Signals /health returns 404; /health/ready returns 200
        if url == "http://signals/health":
            return httpx.Response(404)
        # Search /health returns 200
        elif url == "http://search/health":
            return httpx.Response(200)
        # Signals /health/ready returns 200 (the correct endpoint)
        elif url == "http://signals/health/ready":
            return httpx.Response(200)
        else:
            return httpx.Response(404)

    b = _backend(tmp_path, run, httpx.Client(transport=httpx.MockTransport(h)), signals_dir=sd)
    b.up()
    # Confirm that /health/ready was polled (not /health)
    assert "http://signals/health/ready" in hits
    assert "http://signals/health" not in hits
    assert "http://search/health" in hits


# ---- U1-U3: the Blue Dots UP-Ghaziabad schema ------------------------------------------------------------------
from eval.voice_bench.config import DEFAULT_NETWORK_JSON  # noqa: E402

_AGENT_CORE = Path(__file__).resolve().parents[3]


def test_up_fails_clearly_when_network_json_is_missing(tmp_path):
    sd, run = _signals_tree(tmp_path), FakeRun()
    with pytest.raises(RuntimeError, match="backend.network_json not found"):
        _backend(tmp_path, run, signals_dir=sd, network_json=tmp_path / "nope.json").up()
    assert run.calls == []


def _up_gzb() -> Path | None:
    """DEFAULT_NETWORK_JSON resolved from agent_core/ (as the CLI runs), or from the main checkout's agent_core/
    when this is a linked git worktree."""
    cands = [_AGENT_CORE / DEFAULT_NETWORK_JSON]
    r = subprocess.run(["git", "-C", str(_AGENT_CORE), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        cands.append(Path(r.stdout.strip()).parent / "agent_core" / DEFAULT_NETWORK_JSON)
    return next((c.resolve() for c in cands if c.is_file()), None)


_TYPES = {"string": lambda x: isinstance(x, str), "array": lambda x: isinstance(x, list),
          "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
          "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)}


def _violations(state: dict, schema: dict) -> list[str]:
    props, out = schema["properties"], []
    out += [f"undeclared {k}" for k in state if k not in props]
    out += [f"missing required {k}" for k in schema.get("required", []) if k not in state]
    for k, v in state.items():
        p = props.get(k)
        if p is None:
            continue
        if not _TYPES[p["type"]](v):
            out.append(f"{k}: {v!r} is not {p['type']}")
            continue
        if "enum" in p and v not in p["enum"]:
            out.append(f"{k}: {v!r} not in enum")
        if p["type"] == "array" and isinstance(p.get("items"), dict) and "enum" in p["items"]:
            out += [f"{k}: {x!r} not in enum" for x in v if x not in p["items"]["enum"]]
        if p["type"] == "string" and len(v) < p.get("minLength", 0):
            out.append(f"{k}: shorter than minLength")
        if p["type"] in ("integer", "number"):
            if "minimum" in p and v < p["minimum"]:
                out.append(f"{k}: below minimum")
            if "maximum" in p and v > p["maximum"]:
                out.append(f"{k}: above maximum")
    return out


def test_every_seeded_item_state_validates_against_up_gzb_network_json():
    """U3: all 60 job postings and all seed profiles fit the up-gzb schemas (declared keys, enums, types, required)."""
    path = _up_gzb()
    if path is None:
        pytest.skip(f"up-gzb network.json not found ({DEFAULT_NETWORK_JSON} from agent_core/)")
    net = json.loads(path.read_text(encoding="utf-8"))
    assert net["id"] == "blue_dot"
    domains = {d["id"]: d for d in net["domains"]}
    job_schema = domains["provider"]["item_schemas"]["job_posting_1.0"]
    profile_schema = domains["seeker"]["item_schemas"]["profile_1.0"]
    assert job_schema["additionalProperties"] is False and profile_schema["additionalProperties"] is False
    seed = load_seed()
    bad = {f"job {r['phone']}": _violations(r["item_state"], job_schema) for r in job_rows(seed)}
    bad |= {f"profile {p['scenario']}": _violations(profile_item_state(p), profile_schema) for p in seed["profiles"]}
    assert {k: v for k, v in bad.items() if v} == {}
    # the POST bodies name the same network/domains/item types the schema declares
    for row in job_rows(seed)[:1]:
        b = participant_body("provider", row["phone"], row["name"], row["item_state"])
        assert (b["network"], b["domain"], b["item_type"]) == ("blue_dot", "provider", "job_posting_1.0")
    assert "job_posting_1.0" in domains[participant_body("provider", "1", "x", {})["domain"]]["item_schemas"]


def test_job_category_only_on_trades_with_a_clean_fit():
    rows = job_rows(load_seed())
    cats = {r["item_state"]["role"]: r["item_state"].get("jobCategory") for r in rows}
    assert cats == {"Electrician": None, "Plumber": None, "Delivery Executive": None, "Driver": None,
                    "Security Guard": None, "Welder": "Manufacturing", "Fitter": "Manufacturing",
                    "Data Entry Operator": "Data Entry", "Sales Executive": "Field Sales", "Housekeeping": None}
    assert all("typeOfJob" not in r["item_state"] and "title" not in r["item_state"] for r in rows)
