"""Secret redaction for text that may end up in result files or the console (StackError, NLU stderr tails)."""
from __future__ import annotations

import os
from pathlib import Path


def secret_values(env_file: Path | None) -> list[str]:
    """OPENAI_API_KEY plus every VALUE in env_file (KEY=VALUE lines), longest first; [] entries dropped."""
    vals = [os.environ.get("OPENAI_API_KEY") or ""]
    if env_file is not None:
        try:
            for ln in Path(env_file).read_text(encoding="utf-8").splitlines():
                if "=" in ln and not ln.lstrip().startswith("#"):
                    vals.append(ln.split("=", 1)[1].strip().strip("'\""))
        except OSError:
            pass
    return sorted({v for v in vals if v}, key=len, reverse=True)


def redact(text: str, env_file: Path | None = None) -> str:
    """Replace every secret value (see secret_values) in text with ``***``."""
    for v in secret_values(env_file):
        text = text.replace(v, "***")
    return text
