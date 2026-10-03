#!/usr/bin/env python3
"""Check that every repo path in backticks in the given markdown files exists (run from the repo root)."""
import re
import sys
from pathlib import Path

TOKEN = re.compile(r"`([^`\s]+)`")
LOOKS_LIKE_PATH = re.compile(r"^(?!https?://)(?!/)[\w.\-]+(/[\w.\-]+)*(\.[A-Za-z0-9]+|/)$")


def missing(md: Path, root: Path) -> list[str]:
    out = []
    for tok in TOKEN.findall(md.read_text(encoding="utf-8")):
        tok = tok.split(":")[0]  # allow path:line
        if "/" in tok and LOOKS_LIKE_PATH.match(tok) and not (root / tok).exists():
            out.append(tok)
    return sorted(set(out))


def main(argv: list[str]) -> int:
    root = Path.cwd()
    if not (root / "agent_core").is_dir():
        print("hint: run this from the repo root (no agent_core/ in the current directory)")
        return 1
    bad = {a: missing(Path(a), root) for a in argv}
    for a, toks in bad.items():
        for t in toks:
            print(f"{a}: missing {t}")
    return 1 if any(bad.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
