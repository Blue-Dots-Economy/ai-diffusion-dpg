"""Tests for scripts/serve.py — the ingest-if-empty container entrypoint."""

import sys
from unittest.mock import patch

import pytest

from scripts import serve


@pytest.fixture
def execv():
    with patch("scripts.serve.os.execv") as m:
        yield m


@pytest.fixture
def ingest():
    with patch("scripts.serve.ingest_main") as m:
        yield m


def test_empty_store_runs_ingest_then_starts_server(tmp_path, ingest, execv):
    serve.main(["--config", "config/ke.yaml", "--chroma-dir", str(tmp_path)])

    ingest.assert_called_once_with(config_path="config/ke.yaml")
    execv.assert_called_once_with(sys.executable, [sys.executable, "main.py"])


def test_populated_store_skips_ingest(tmp_path, ingest, execv):
    (tmp_path / "chroma.sqlite3").write_bytes(b"")

    serve.main(["--config", "config/ke.yaml", "--chroma-dir", str(tmp_path)])

    ingest.assert_not_called()
    execv.assert_called_once_with(sys.executable, [sys.executable, "main.py"])


def test_missing_chroma_dir_is_treated_as_empty(tmp_path, ingest, execv):
    serve.main(["--chroma-dir", str(tmp_path / "does-not-exist")])

    ingest.assert_called_once_with(config_path=serve.DEFAULT_CONFIG)
    execv.assert_called_once()


def test_chroma_dir_defaults_to_env(tmp_path, monkeypatch, ingest, execv):
    (tmp_path / "chroma.sqlite3").write_bytes(b"")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path))

    serve.main([])

    ingest.assert_not_called()


def test_ingest_failure_exits_without_starting_server(tmp_path, ingest, execv):
    # ingest.main signals failure with sys.exit(1); the old `a && b` shell chain
    # never reached `python main.py` in that case, and neither must this.
    ingest.side_effect = SystemExit(1)

    with pytest.raises(SystemExit) as exc:
        serve.main(["--chroma-dir", str(tmp_path)])

    assert exc.value.code == 1
    execv.assert_not_called()
