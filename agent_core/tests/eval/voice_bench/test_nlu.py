import json
import subprocess
import sys
from pathlib import Path

from eval.voice_bench.nlu import pick_adapter, run_nlu


def test_pick_adapter(tmp_path):
    (tmp_path / "src").mkdir()
    assert pick_adapter(tmp_path) == "intent"
    (tmp_path / "src" / "understanding").mkdir()
    assert pick_adapter(tmp_path) == "dialogue_act"


def test_run_nlu_builds_isolated_command_and_reads_output(tmp_path):
    wt = tmp_path / "wt"
    (wt / "agent_core").mkdir(parents=True)
    seen = {}

    def run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        out = Path(args[args.index("--out") + 1])
        out.write_text(json.dumps({"adapter": "intent", "n_cases": 2, "report": {"fields": {}}}), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    res = run_nlu(wt, [Path("a.jsonl")], 1, tmp_path / "nlu.json", run=run)
    assert res["adapter"] == "intent"
    assert seen["args"][:4] == ["uv", "run", "--project", str(wt / "agent_core")]
    assert seen["kw"]["cwd"] == str(wt / ".vb_harness")
    assert seen["kw"]["env"]["PYTHONPATH"].split(":")[0] == str(wt / ".vb_harness")
    assert (wt / ".vb_harness" / "eval" / "voice_bench" / "nlu_worker.py").exists()


def test_run_nlu_failure_is_unmeasurable(tmp_path):
    wt = tmp_path / "wt"
    (wt / "agent_core").mkdir(parents=True)
    res = run_nlu(wt, [], 1, tmp_path / "o.json",
                  run=lambda a, **k: subprocess.CompletedProcess(a, 1, "", "ImportError: no module src.preprocessing"))
    assert "ImportError" in res["unmeasurable"]


def test_run_nlu_resolves_relative_paths(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "agent_core").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        Path(args[args.index("--out") + 1]).write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    run_nlu(wt, [Path("a.jsonl")], 1, Path("rel.json"), run=run)
    a = seen["args"]
    assert a[a.index("--out") + 1] == str(tmp_path / "rel.json")
    assert a[a.index("--cases") + 1] == str(tmp_path / "a.jsonl")


def test_run_nlu_zero_exit_without_valid_output_is_unmeasurable(tmp_path):
    wt = tmp_path / "wt"
    (wt / "agent_core").mkdir(parents=True)
    ok = lambda a, **k: subprocess.CompletedProcess(a, 0, "", "")
    assert "no valid output" in run_nlu(wt, [], 1, tmp_path / "missing.json", run=ok)["unmeasurable"]

    def bad(args, **kw):
        Path(args[args.index("--out") + 1]).write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    assert "no valid output" in run_nlu(wt, [], 1, tmp_path / "bad.json", run=bad)["unmeasurable"]


def test_adapters_import_without_understanding_package():
    code = ("import sys\n"
            "sys.modules['src.understanding'] = None\n"
            "sys.modules['src.understanding.understander'] = None\n"
            "import eval.nlu.adapters as ad\n"
            "assert hasattr(ad, 'predict_intent') and hasattr(ad, 'predict_dialogue_act')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[3]))
    assert r.returncode == 0, r.stderr


def test_run_nlu_unmeasurable_tail_is_redacted(tmp_path, monkeypatch):
    """M7: the stderr tail goes into meta/report, so secrets are scrubbed like StackError's."""
    wt = tmp_path / "wt"
    (wt / "agent_core").mkdir(parents=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    env = tmp_path / "bd.env"
    env.write_text("BLUE_DOTS_API_KEY=sk_signals_" + "a" * 48 + "\n", encoding="utf-8")
    err = "Traceback\nAuthError: key sk-openai-secret rejected; signals sk_signals_" + "a" * 48
    res = run_nlu(wt, [], 1, tmp_path / "o.json", env_file=env,
                  run=lambda a, **k: subprocess.CompletedProcess(a, 1, "", err))
    assert "sk-openai-secret" not in res["unmeasurable"] and "sk_signals_" not in res["unmeasurable"]
    assert res["unmeasurable"].count("***") == 2
    ok = lambda a, **k: subprocess.CompletedProcess(a, 0, "", err)  # noqa: E731
    res = run_nlu(wt, [], 1, tmp_path / "missing.json", env_file=env, run=ok)
    assert "no valid output" in res["unmeasurable"] and "sk-openai-secret" not in res["unmeasurable"]
