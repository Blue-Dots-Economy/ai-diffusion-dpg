"""Tests for history: append-only jsonl chat history."""
from pathlib import Path

from dev_kit.agent.history import (
    HistoryEntry,
    append_turn,
    load_history,
)


def test_history_entry_minimal():
    e = HistoryEntry(role="user", content="Hello", phase="tier", timestamp="2026-05-14T10:00:00Z")
    assert e.role == "user"
    assert e.content == "Hello"


def test_append_creates_jsonl(tmp_path: Path):
    project = tmp_path / "proj"
    append_turn(project, HistoryEntry(role="user", content="Hi", phase="tier",
                                       timestamp="2026-05-14T10:00:00Z"))
    p = project / "_meta" / "history.jsonl"
    assert p.exists()
    assert p.read_text().strip().count("\n") == 0  # one line


def test_multiple_appends(tmp_path: Path):
    project = tmp_path / "proj"
    append_turn(project, HistoryEntry(role="user", content="A", phase="tier",
                                       timestamp="2026-05-14T10:00:00Z"))
    append_turn(project, HistoryEntry(role="assistant", content="B", phase="tier",
                                       timestamp="2026-05-14T10:00:01Z"))
    history = load_history(project)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].content == "B"


def test_load_missing_returns_empty(tmp_path: Path):
    history = load_history(tmp_path / "no_project")
    assert history == []


def test_load_skips_blank_lines(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "_meta").mkdir(parents=True)
    (project / "_meta" / "history.jsonl").write_text(
        '{"role": "user", "content": "A", "phase": "tier", "timestamp": "t"}\n'
        '\n'  # blank
        '{"role": "assistant", "content": "B", "phase": "tier", "timestamp": "t2"}\n'
    )
    history = load_history(project)
    assert len(history) == 2
