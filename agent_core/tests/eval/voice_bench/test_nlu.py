import json
import subprocess
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


def test_adapters_import_without_understanding_package():
    import eval.nlu.adapters as ad
    assert hasattr(ad, "predict_intent") and hasattr(ad, "predict_dialogue_act")
