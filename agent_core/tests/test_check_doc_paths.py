import subprocess, sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_doc_paths.py"  # agent_core/tests/ → repo root


def run(md_text, tmp_path):
    md = tmp_path / "x.md"; md.write_text(md_text, encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(md)], capture_output=True, text=True,
                          cwd=SCRIPT.parents[1])


def test_existing_paths_pass(tmp_path):
    assert run("See `agent_core/src/orchestrator.py` and `dev-kit/`.", tmp_path).returncode == 0


def test_missing_path_fails_and_is_named(tmp_path):
    r = run("See `agent_core/src/nope.py`.", tmp_path)
    assert r.returncode == 1 and "agent_core/src/nope.py" in r.stdout


def test_urls_and_non_paths_ignored(tmp_path):
    assert run("`https://x.org/a.md` `pnpm build` `/process_turn` `a/b`", tmp_path).returncode == 0
