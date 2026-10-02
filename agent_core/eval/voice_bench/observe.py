"""Read-only observers of a target: Redis session hash and agent_core turn banners (plan ruling 2)."""
from __future__ import annotations

import re
import subprocess

_INT = r"(\d+|None)"
_FIELDS = {
    "total_latency_ms": rf"total_latency={_INT}ms",
    "llm_ttft_ms": rf"llm_ttft={_INT}ms",
    "first_sentence_ms": rf"first_sentence={_INT}ms",
    "llm_calls": rf"llm_calls={_INT}",
    "predispatch_tool": r"predispatch_tool=(\S+)",
    "predispatch_outcome": r"predispatch_outcome=(\S+)",
    "predispatch_ms": rf"predispatch_ms={_INT}",
}
_NUMERIC = {"total_latency_ms", "llm_ttft_ms", "first_sentence_ms", "llm_calls", "predispatch_ms"}


def parse_banner(log_text: str) -> dict:
    """Fields of the last STREAM TURN COMPLETE banner; {} if there is none; absent/None fields → None."""
    idx = log_text.rfind("STREAM TURN COMPLETE")
    if idx < 0:
        return {}
    block = log_text[idx: idx + 2000]
    end = block.find("response:")
    block = block[: end if end > 0 else len(block)]
    out: dict = {}
    for key, pat in _FIELDS.items():
        m = re.search(pat, block)
        v = m.group(1) if m else None
        if v == "None":
            v = None
        out[key] = int(v) if (v is not None and key in _NUMERIC) else v
    return out


def _hgetall(container: str, key: str, run) -> dict:
    r = run(["docker", "exec", container, "redis-cli", "HGETALL", key], capture_output=True, text=True, timeout=10)
    lines = [ln for ln in (r.stdout or "").splitlines()]
    return {lines[i]: lines[i + 1] for i in range(0, len(lines) - 1, 2)}


def read_session(redis_container: str, phone: str, call_id: str, run=subprocess.run) -> dict:
    """Session hash for this call: ``session:<phone>:<call_id>``, else ``session:<phone>`` (M0). {} on failure."""
    try:
        for key in (f"session:{phone}:{call_id}", f"session:{phone}"):
            d = _hgetall(redis_container, key, run)
            if d:
                return d
    except (OSError, subprocess.SubprocessError):
        pass
    return {}


class LogScraper:
    """``docker logs --since`` for the agent container; '' when unavailable."""

    def __init__(self, container: str | None, run=subprocess.run) -> None:
        self._c, self._run = container, run

    def since(self, epoch_s: int) -> str:
        if not self._c:
            return ""
        try:
            r = self._run(["docker", "logs", "--since", str(epoch_s), self._c], capture_output=True, text=True, timeout=15)
            return (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.SubprocessError):
            return ""
