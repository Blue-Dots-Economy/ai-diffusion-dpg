# agent_core/tests/eval/voice_bench/test_report_cli.py
import json
import os
import re

import pytest
import yaml

from eval.voice_bench.records import CallRecord, Leg, TapEntry, TurnRecord, Verdict
from eval.voice_bench.report import comparable, pct, render_markdown, summarise


def _rec(run, ms_list, verdicts, tool_turn=None):
    tap = [TapEntry(1, "POST", "/v1/search", "", {}, 200, {}, "search")]
    turns = [TurnRecord(i, "a", "ठीक", None, ms, ms, ms + 100, {}, tap if i == tool_turn else [], {"llm_calls": 1},
                        False, None) for i, ms in enumerate(ms_list)]
    return CallRecord("M3", "8b39427", "T01", run, "919900001000", 1, 1, "gpt-4.1", "gpt-4.1",
                      [Leg("c", turns, "bot")], 1, False, None, verdicts={k: Verdict(*v) for k, v in verdicts.items()})


def test_pct_nearest_rank():
    assert pct([], 0.5) is None and pct([1, 2, 3, 4], 0.5) in (2, 3) and pct([5], 0.95) == 5


def test_summarise_rates_exclude_na_and_count_unscored_as_not_pass():
    recs = [_rec(0, [900, 1100, 6000], {"TC12": ("pass", "q"), "TC10": ("n/a",)}, tool_turn=2),
            _rec(1, [800, 5200], {"TC12": ("unscored",), "TC10": ("fail", "q", "r", 1)})]
    s = summarise(recs, {"name": "M3", "nlu": None})
    assert s["tc"]["TC12"]["rate"] == 0.5 and s["tc"]["TC12"]["n"] == 2
    assert s["tc"]["TC10"]["n"] == 1 and s["tc"]["TC10"]["rate"] == 0.0
    assert s["latency"]["tool"]["n"] == 1 and s["latency"]["non_tool"]["n"] == 4
    assert s["latency"]["non_tool"]["over5s"] == 1 and s["latency"]["all"]["max"] == 6000
    assert s["failures"][0]["tc"] == "TC10" and s["failures"][0]["turn"] == 1


def test_comparable_and_markdown():
    a = summarise([_rec(0, [900], {"TC12": ("pass", "q")})], {"name": "M2"})
    b = summarise([_rec(0, [700], {"TC12": ("fail", "q", "r", 0)})], {"name": "M3"})
    assert comparable([a, b]) == []
    b2 = dict(b, judge_model="other")
    assert comparable([a, b2])
    md = render_markdown([a, b])
    assert "| TC12 |" in md and "100% (1/1)" in md and "-100" in md
    assert "## Latency" in md and "## NLU (TC22)" in md and "statistical significance" in md


# ---- CLI ------------------------------------------------------------------------------------------------------
import eval.voice_bench.__main__ as cli  # noqa: E402
from eval.voice_bench.stack import StackError  # noqa: E402
from eval.voice_bench.store import ResultStore  # noqa: E402

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class FakeTap:
    instances = []

    def __init__(self, port, signals_url, search_url):
        self.started = self.stopped = False
        FakeTap.instances.append(self)

    @property
    def url_for_containers(self):
        return "http://host.docker.internal:1"

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def take(self):
        return []

    def clear(self):
        pass


class FakeBackend:
    calls = []

    def __init__(self, cfg, results_dir):
        self.state = {"watermark": "w", "seed_version": 1, "api_key_env_file": "/x/blue_dots.env",
                      "instance_url": "http://signals-api:2742", "snapshot": {}, "seed_user_ids": [],
                      "service_user_id": "s"}

    def up(self):
        FakeBackend.calls.append("up")

    def down(self, volumes=False):
        FakeBackend.calls.append(("down", volumes))

    def seed(self):
        FakeBackend.calls.append("seed")

    def cleanup(self):
        FakeBackend.calls.append("cleanup")


class FakeStack:
    fail = set()
    downs = []
    commits = {}

    def __init__(self, target, repo_root, work_root, tap_url, instance_url, env_file):
        self.target = target

    @property
    def commit(self):
        c = FakeStack.commits.get(self.target.name, f"c{self.target.name}")
        if c is None:
            raise StackError("cannot resolve ref")
        return c

    @property
    def worktree(self):
        return f"/wt/{self.target.name}"

    def up(self):
        if self.target.name in FakeStack.fail:
            raise StackError("patch does not apply: rule 0: expected 3, found 2")
        return "http://127.0.0.1:18008"

    def down(self):
        FakeStack.downs.append(self.target.name)
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    FakeTap.instances, FakeBackend.calls, FakeStack.fail, FakeStack.downs = [], [], set(), []
    FakeStack.commits = {}
    driven, nlu_calls, resets = [], [], []

    def fake_drive(deps, persona, run_idx, phone, max_turns, meta):
        deps.cleanup()
        driven.append((meta["target"], persona.id, run_idx, phone))
        turns = [TurnRecord(0, "नमस्ते", "नमस्ते जी", None, 900, 900, 1000, {}, [], {}, True, None)]
        return CallRecord(meta["target"], meta["target_commit"], persona.id, run_idx, phone, meta["suite_version"],
                          meta["seed_version"], meta["caller_model"], meta["judge_model"], [Leg("c", turns, "bot")],
                          1, False, None)

    def fake_score(rec, persona, judge_llm, places, no_idle_handling):
        rec.verdicts = {"TC12": Verdict("fail", "नमस्ते जी", "कारण", 0)}
        return rec.verdicts

    def fake_nlu(worktree, cases, repeat, out, env_file=None):
        nlu_calls.append((worktree, cases, repeat))
        return {"adapter": "intent", "report": {"fields": {"intent": {"accuracy": 0.9, "n": 10}},
                                                "termination_false_positives": 0,
                                                "latency_ms": {"p50": 400, "p95": 900}}}

    monkeypatch.setattr(cli, "Tap", FakeTap)
    monkeypatch.setattr(cli, "Backend", FakeBackend)
    monkeypatch.setattr(cli, "TargetStack", FakeStack)
    monkeypatch.setattr(cli, "BridgeClient", lambda url, sp, tw: object())
    monkeypatch.setattr(cli, "OpenAIJsonLLM", lambda model, temperature: object())
    monkeypatch.setattr(cli, "LogScraper", lambda c: object())
    monkeypatch.setattr(cli, "run_nlu", fake_nlu)
    monkeypatch.setattr(cli, "reset_session", lambda c, phone, flush: resets.append((c, phone, flush)))
    monkeypatch.setattr(cli, "drive_call", fake_drive)
    monkeypatch.setattr(cli, "score_call", fake_score)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    results = tmp_path / "results"
    cfg = {"suite_version": 1, "targets": [{"name": "M0", "git_ref": "a"}, {"name": "M1", "git_ref": "b"}],
           "models": {"caller": {"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
                      "judge": {"provider": "openai", "model": "gpt-4.1", "temperature": 0}},
           "backend": {"signals_dir": str(tmp_path)}, "runs": 1, "runs_per_scenario": {"T01": 3},
           "results_dir": str(results)}
    path = tmp_path / "vb.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return {"cfg": str(path), "results": results, "driven": driven, "nlu": nlu_calls, "tmp": tmp_path,
            "resets": resets, "raw_cfg": cfg}


def test_run_skips_cached_records(env):
    argv = ["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T01,T02"]
    assert cli.main(argv) == 0
    assert [(s, r) for _, s, r, _ in env["driven"]] == [("T01", 0), ("T01", 1), ("T01", 2), ("T02", 0)]
    env["driven"].clear()
    assert cli.main(argv) == 0
    assert env["driven"] == []
    assert FakeStack.downs == ["M0", "M0"] and all(t.stopped for t in FakeTap.instances)


def test_run_records_unmeasurable_target_and_continues(env):
    FakeStack.fail = {"M0"}
    assert cli.main(["run", "--config", env["cfg"], "--scenarios", "T02"]) == 0
    assert [t for t, *_ in env["driven"]] == ["M1"]
    store = ResultStore(env["results"])
    assert "patch does not apply" in store.read_meta("cM0")["unmeasurable"]
    assert store.read_meta("cM0")["name"] == "M0"
    assert store.read_meta("cM1")["nlu"]["adapter"] == "intent"
    assert FakeStack.downs == ["M0", "M1"]
    assert len(env["nlu"]) == 1 and env["nlu"][0][2] == 1
    assert all(os.path.isabs(c) and str(c).endswith(".jsonl") for c in env["nlu"][0][1])


def test_dry_run_is_first_target_t01_r0_and_runs_nlu(env):
    assert cli.main(["run", "--config", env["cfg"], "--dry-run"]) == 0
    assert [(t, s, r) for t, s, r, _ in env["driven"]] == [("M0", "T01", 0)]
    assert ResultStore(env["results"]).has("cM0", "T01", 0)
    assert [w for w, *_ in env["nlu"]] == ["/wt/M0"]               # I5: the NLU worker path runs on a dry run too
    assert ResultStore(env["results"]).read_meta("cM0")["nlu"]["adapter"] == "intent"


def test_runs_flag_overrides_and_seeded_phone(env):
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M1", "--scenarios", "T01,T14", "--runs", "1"]) == 0
    assert [(s, r, p) for _, s, r, p in env["driven"]] == [("T01", 0, "919900001000"), ("T14", 0, "919900014000")]


def test_env_file_sets_unset_keys_only_and_is_never_printed(env, monkeypatch, capsys):
    f = env["tmp"] / "secrets.env"
    f.write_text("# comment\n\nVB_TEST_NEW='sk-secret-value'\nVB_TEST_SET=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("VB_TEST_SET", "already")
    monkeypatch.delenv("VB_TEST_NEW", raising=False)
    assert cli.main(["run", "--config", env["cfg"], "--dry-run", "--env-file", str(f)]) == 0
    assert os.environ["VB_TEST_NEW"] == "sk-secret-value" and os.environ["VB_TEST_SET"] == "already"
    monkeypatch.delenv("VB_TEST_NEW")
    assert "sk-secret-value" not in capsys.readouterr().out


def test_report_writes_md_and_json_and_stdout_has_no_devanagari(env, capsys):
    FakeStack.fail = {"M1"}
    assert cli.main(["run", "--config", env["cfg"], "--scenarios", "T01"]) == 0
    out = env["tmp"] / "report.md"
    assert cli.main(["report", "--config", env["cfg"], "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    data = json.loads((env["tmp"] / "report.md.json").read_text(encoding="utf-8"))
    assert [s["target"] for s in data["summaries"]] == ["M0", "M1"]
    assert data["summaries"][0]["tc"]["TC12"]["n"] == 3 and data["summaries"][1]["unmeasurable"]
    assert "नमस्ते जी" in md and "patch does not apply" in md
    stdout = capsys.readouterr().out
    assert "M0 T01 r0 ok 1 turns" in stdout
    assert not _DEVANAGARI.search(stdout)


def test_backend_subcommands_delegate(env):
    assert cli.main(["backend", "up", "--config", env["cfg"]]) == 0
    assert cli.main(["backend", "seed", "--config", env["cfg"]]) == 0
    assert cli.main(["backend", "down", "-v", "--config", env["cfg"]]) == 0
    assert FakeBackend.calls == ["up", "seed", ("down", True)]


def test_report_uses_the_commit_the_ref_resolves_to_and_warns_on_partial(env):
    FakeStack.commits = {"M0": "old0000"}
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T01,T02"]) == 0
    FakeStack.commits = {"M0": "new0000"}
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T02"]) == 0
    store = ResultStore(env["results"])
    assert store.read_meta("old0000")["name"] == store.read_meta("new0000")["name"] == "M0"
    FakeStack.commits = {"M0": "old0000", "M1": None}
    out = env["tmp"] / "r.md"
    assert cli.main(["report", "--config", env["cfg"], "--out", str(out)]) == 0
    data = json.loads((env["tmp"] / "r.md.json").read_text(encoding="utf-8"))
    m0, m1 = data["summaries"]
    assert m0["commit"] == "old0000" and m0["n_calls"] == 4
    md = out.read_text(encoding="utf-8")
    assert "**Warning (M0):** partial: 4 of 16 planned calls" in md
    assert "**Warning (M1):** ref 'b' does not resolve" in md
    FakeStack.commits = {"M0": "nothere"}
    assert cli.main(["report", "--config", env["cfg"], "--targets", "M0", "--out", str(out)]) == 0
    assert "**Warning (M0):** no records for commit nothere" in out.read_text(encoding="utf-8")


def test_report_planned_calls_honour_runs_override(env):
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--runs", "1"]) == 0
    out = env["tmp"] / "r.md"
    assert cli.main(["report", "--config", env["cfg"], "--targets", "M0", "--runs", "1", "--out", str(out)]) == 0
    assert "Warning (M0)" not in out.read_text(encoding="utf-8")


def test_run_refuses_targets_resolving_to_the_same_commit(env, capsys):
    FakeStack.commits = {"M0": "same000", "M1": "same000"}
    assert cli.main(["run", "--config", env["cfg"], "--scenarios", "T02"]) == 2
    assert env["driven"] == [] and FakeTap.instances == []
    assert "M0 and M1" in capsys.readouterr().out


def test_error_counts_in_denominator_rerun_ok_counts_and_none_latency_excluded():
    r0 = _rec(0, [900, 1000], {"TC12": ("pass", "q")})
    r0.legs[0].turns[1].t_first_content_ms = None
    r1 = _rec(1, [800], {"TC12": ("error", None, "bridge: x")})
    r1.void_reason, r1.voided = "rerun_ok", True
    r2 = _rec(2, [700], {"TC12": ("pass", "q")})
    r2.void_reason, r2.voided = "rerun_ok", True
    s = summarise([r0, r1, r2], {"name": "M3"})
    assert s["tc"]["TC12"]["n"] == 3 and s["tc"]["TC12"]["error"] == 1 and s["tc"]["TC12"]["rate"] == 0.6667
    assert s["latency"]["all"]["n"] == 3 and s["n_calls"] == 3


def test_delta_uses_unrounded_rates():
    def summ(name, passes, n):
        return summarise([_rec(i, [900], {"TC12": ("pass" if i < passes else "fail", "q", "r", 0)})
                          for i in range(n)], {"name": name})
    row = next(ln for ln in render_markdown([summ("A", 1, 6), summ("B", 1, 3)]).splitlines()
               if ln.startswith("| TC12 |"))
    # 16.67% -> 33.33%: unrounded delta 16.67 -> +17 (rounding each first would give 33-17 = +16)
    assert "17% (1/6)" in row and "33% (1/3)" in row and row.endswith("| +17 |")


# ---- final-review fixes ---------------------------------------------------------------------------------------
def _with_targets(env, targets):
    raw = dict(env["raw_cfg"], targets=targets)
    path = env["tmp"] / "vb2.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(path)


def test_per_call_cleanup_resets_target_session_memory(env):
    """C1: every call's cleanup = Signals DB cleanup + session reset; FLUSHDB for git_ref, keyed delete for bridge_url."""
    cfg = _with_targets(env, [{"name": "M0", "git_ref": "a"},
                              {"name": "vm", "bridge_url": "http://127.0.0.1:8008", "redis_container": "dpg_redis"}])
    assert cli.main(["run", "--config", cfg, "--scenarios", "T01,T14", "--runs", "1"]) == 0
    assert env["resets"] == [("redis", "919900001000", True), ("redis", "919900014000", True),
                             ("dpg_redis", "919900001000", False), ("dpg_redis", "919900014000", False)]
    assert FakeBackend.calls.count("cleanup") == 4


def test_cleanup_failure_exits_1_without_traceback(env, monkeypatch, capsys):
    """C1 + M8: a reset failure aborts (no call on dirty memory) with an ASCII one-liner, exit 1."""
    def boom(c, phone, flush):
        raise RuntimeError("redis-cli FLUSHDB on redis failed (exit 1)")
    monkeypatch.setattr(cli, "reset_session", boom)
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T02"]) == 1
    out = capsys.readouterr().out
    assert "error: RuntimeError: redis-cli FLUSHDB on redis failed" in out and "Traceback" not in out
    assert env["driven"] == [] and all(t.stopped for t in FakeTap.instances) and FakeStack.downs == ["M0"]


def test_http_error_exits_1_and_redacts(env, monkeypatch, capsys):
    """M8: httpx errors surface as an ASCII line with secrets scrubbed."""
    import httpx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-topsecret")

    def boom(*a, **k):
        raise httpx.ConnectError("connect failed for sk-topsecret à")
    monkeypatch.setattr(cli, "drive_call", boom)
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T02"]) == 1
    out = capsys.readouterr().out
    assert "error: ConnectError" in out and "sk-topsecret" not in out and out.isascii()


def _harness_drive(env):
    def drive(deps, persona, run_idx, phone, max_turns, meta):
        env["driven"].append((meta["target"], persona.id, run_idx, phone))
        turns = [TurnRecord(0, "नमस्ते", "नमस्ते जी", None, 900, 900, 1000, {}, [], {}, False, None)]
        return CallRecord(meta["target"], meta["target_commit"], persona.id, run_idx, phone, meta["suite_version"],
                          meta["seed_version"], meta["caller_model"], meta["judge_model"],
                          [Leg("c", turns, "error", "caller_llm_bad_json")], 2, False, None,
                          harness_error="caller_llm_bad_json")
    return drive


def test_harness_error_calls_are_rerun_and_reported_separately(env, monkeypatch, capsys):
    """I1: a harness-error record is not a cache hit, and the report counts it outside the TC denominators."""
    monkeypatch.setattr(cli, "drive_call", _harness_drive(env))
    monkeypatch.setattr(cli, "score_call", lambda rec, *a: setattr(rec, "verdicts", {
        "TC12": Verdict("error", reason=f"harness: {rec.harness_error}")}))
    argv = ["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T02"]
    assert cli.main(argv) == 0 and cli.main(argv) == 0
    assert len(env["driven"]) == 2                              # re-driven, not cached
    assert "M0 T02 r0 harness-error 1 turns" in capsys.readouterr().out
    out = env["tmp"] / "h.md"
    assert cli.main(["report", "--config", env["cfg"], "--targets", "M0", "--out", str(out)]) == 0
    s = json.loads((env["tmp"] / "h.md.json").read_text(encoding="utf-8"))["summaries"][0]
    assert s["harness_errors"] == 1 and s["tc"]["TC12"]["n"] == 0 and s["failures"] == []
    assert "harness-error calls: 1" in out.read_text(encoding="utf-8")


def test_summarise_counts_retries_by_cause():
    """I3: retried calls are visible: first-attempt errors vs persona re-runs."""
    a = _rec(0, [900], {"TC12": ("pass", "q")})
    b = _rec(1, [900], {"TC12": ("pass", "q")})
    b.attempts, b.prior_error, b.prior_legs = 2, "http_502", [Leg("p", [], "error")]
    c = _rec(2, [900], {"TC12": ("pass", "q")})
    c.attempts, c.void_reason, c.voided = 2, "rerun_ok", True
    s = summarise([a, b, c], {"name": "M3"})
    assert (s["retried"], s["retried_first_errors"], s["retried_persona_reruns"]) == (2, 1, 1)
    assert "retried calls: 2 (first-attempt errors: 1, persona re-runs: 1)" in render_markdown([s])


def test_summarise_excludes_harness_errors_from_rates_and_latency():
    ok = _rec(0, [900], {"TC12": ("pass", "q")})
    bad = _rec(1, [7000], {"TC12": ("error", None, "harness: caller_llm_x")})
    bad.harness_error = "caller_llm_x"
    s = summarise([ok, bad], {"name": "M3"})
    assert s["tc"]["TC12"]["n"] == 1 and s["tc"]["TC12"]["rate"] == 1.0
    assert s["harness_errors"] == 1 and s["n_calls"] == 2 and s["latency"]["all"]["max"] == 900


def test_delta_column_per_consecutive_pair():
    """M10: M0→M1, M1→M2, M2→M3 deltas, not just the last two."""
    def summ(name, passes, n):
        return summarise([_rec(i, [900], {"TC12": ("pass" if i < passes else "fail", "q", "r", 0)})
                          for i in range(n)], {"name": name})
    md = render_markdown([summ("M0", 0, 2), summ("M1", 1, 2), summ("M2", 2, 2), summ("M3", 1, 2)])
    head = next(ln for ln in md.splitlines() if ln.startswith("| TC |"))
    assert head.endswith("| Δ pp M0→M1 | Δ pp M1→M2 | Δ pp M2→M3 |")
    row = next(ln for ln in md.splitlines() if ln.startswith("| TC12 |"))
    assert row.endswith("| +50 | +50 | -50 |")


def test_rescore_rescores_stored_records_and_skips_harness_errors(env, monkeypatch, capsys):
    """I4: `rescore` re-runs score_call on every stored record of the resolved commit and re-saves it."""
    assert cli.main(["run", "--config", env["cfg"], "--targets", "M0", "--scenarios", "T02,T05"]) == 0
    store = ResultStore(env["results"])
    stuck = store.load("cM0", "T05", 0)
    stuck.harness_error = "caller_llm_bad_json"
    store.save(stuck)
    seen = []

    def rescore(rec, persona, judge_llm, places, no_idle_handling):
        seen.append((rec.scenario, persona.id))
        rec.verdicts = {"TC12": Verdict("pass", "नमस्ते जी", "fixed", 0)}
        return rec.verdicts
    monkeypatch.setattr(cli, "score_call", rescore)
    capsys.readouterr()
    assert cli.main(["rescore", "--config", env["cfg"], "--targets", "M0"]) == 0
    assert seen == [("T02", "T02")]
    assert store.load("cM0", "T02", 0).verdicts["TC12"].status == "pass"
    assert store.load("cM0", "T05", 0).verdicts["TC12"].status == "fail"
    out = capsys.readouterr().out
    assert "M0 T02 r0 rescored" in out and "M0 T05 r0 skipped (harness error" in out and out.isascii()
    assert env["driven"][-1][1] == "T05" and len(env["driven"]) == 2      # rescore drives nothing


def test_example_config_loads_with_plan_target_names_and_up_gzb_schema():
    """M12 + U1: the example yaml parses; target names match the plan's commands; backend uses up-gzb."""
    from pathlib import Path

    from eval.voice_bench.config import DEFAULT_NETWORK_JSON, load_config
    ex = Path(cli.__file__).with_name("voice_bench.example.yaml")
    cfg = load_config(ex)
    assert [t.name for t in cfg.targets] == ["M0-baseline", "M1", "M2", "M3-head"]
    assert cfg.backend.network_json == DEFAULT_NETWORK_JSON
    assert str(cfg.backend.network_json).endswith("bluedots-schemas/blue_dot/up-gzb/network.json")


def test_silent_replies_counted_and_rendered():
    def T(i, reply, error=None, tw=None):
        return TurnRecord(i, "a", reply, None, 100, 100, 200, {}, [], {}, False, error, tw)
    turns = [T(0, "ठीक"), T(1, "  "), T(2, "", tw="धन्यवाद"), T(3, "", error="boom"), T(4, "")]
    r = CallRecord("M3", "c", "T01", 0, "919900001000", 1, 1, "m", "m", [Leg("c", turns, "bot")], 1, False, None)
    h = CallRecord("M3", "c", "T01", 1, "919900001000", 1, 1, "m", "m", [Leg("c", [T(0, "")], "bot")], 1, False, "x")
    h.harness_error = True
    s = summarise([r, h], {"name": "M3"})
    assert s["silent_replies"] == {"n_turns": 4, "silent": 2}
    assert "silent replies: 2 of 4 turns" in render_markdown([s])
