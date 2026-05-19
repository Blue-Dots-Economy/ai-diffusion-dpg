"""Atomic file-write helper for the wizard's per-project state files.

Plain ``Path.write_text(...)`` writes the new content directly to the
target path. If the process is killed mid-write (SIGTERM during a
container restart, OOM, host crash), the file ends up truncated. For
JSON state files (``intake_state.json``, ``accumulator.json``, etc.)
this leaves the project unloadable on the next turn — the load helper
either raises ``ValueError`` (FastAPI 500s the user's next chat turn)
or silently resets to an empty skeleton (``load_accumulator``, which is
even worse because the wizard appears to forget the conversation).

``write_atomic_text`` writes to a sibling ``.tmp`` file and uses
``os.replace`` to atomically swap it into place. The rename is atomic
on every POSIX filesystem we care about and on NTFS (Windows). If the
process is killed before the replace, the original file is untouched.

Belongs to the dev-kit deterministic wizard's persistence layer.
"""
from __future__ import annotations

import os
from pathlib import Path


def write_atomic_text(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path``.

    The write is staged in ``<path>.tmp`` and ``os.replace``'d onto the
    target. On any failure mid-write the target file is unchanged.

    Args:
        path: Final destination. Parent directory must exist (the
            caller's ``path.parent.mkdir(parents=True, exist_ok=True)``
            handles that — kept out of here so callers control
            permissions).
        content: Full file contents. ``write_atomic_text`` overwrites,
            it does NOT append.

    Raises:
        OSError: If the temp write or the rename fails (disk full,
            permission denied, etc.). The caller's existing error
            handling for ``Path.write_text`` carries over.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
