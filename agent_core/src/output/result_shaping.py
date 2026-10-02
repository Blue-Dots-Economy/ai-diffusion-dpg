"""
agent_core/src/output/result_shaping.py
Per-connector result shaping at tool-result ingress (Spec D §4): drop
unusable rows, stable sort (nulls last), add ready-to-speak fields, strip
PIN/plot/sector numbers. Rewrites ``ToolResult.result_text`` only — the
cache, the in-turn tool_result and the NLU resolver all read that — and
never raises. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Any

from src.models import ToolResult
from src.output.spoken_numbers import spoken_pay

logger = logging.getLogger(__name__)

_PLACE_TOKEN = re.compile(
    r"(?i)\b(?:plot|house|flat|gali|sector|sec|block|khasra)(?:\s*no\.?)?\s*[#:]?\s*[\w/-]*\d[\w/-]*"
    r"|\bno\.?\s*\d[\w/-]*")
_DIGIT_RUN = re.compile(r"(?<!\w)#?\d[\d/-]*(?!\w)")
_COMMAS = re.compile(r"\s*,(?:\s*,)*\s*")


def strip_place_numbers(text: str) -> str:
    """Remove PIN codes and plot/house/gali/sector numbers from a place string.

    Args:
        text: E.g. ``"Plot No. 12, Sector 5, Noida"``.

    Returns:
        E.g. ``"Noida"``.
    """
    out = _PLACE_TOKEN.sub("", text)
    out = _DIGIT_RUN.sub("", out)
    out = _COMMAS.sub(", ", out)
    return re.sub(r"\s{2,}", " ", out).strip(" ,")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def _matches(row: dict, cond: dict) -> bool:
    v, op, value = row.get(cond.get("field", "")), cond.get("operator"), cond.get("value")
    if op == "eq":
        return v == value
    if op == "not_eq":
        return v != value
    if op == "in":
        return v in (value if isinstance(value, list) else [value])
    if op == "contains":
        return isinstance(v, str) and isinstance(value, str) and value in v
    if op in ("lt", "gt"):
        a, b = _num(v), _num(value)
        return a is not None and b is not None and (a < b if op == "lt" else a > b)
    return False


def _sort_key(v: Any) -> tuple:
    n = _num(v)
    return (0, n, "") if n is not None else (1, 0.0, str(v))


def shape_rows(rows: list, rule: dict, language: str) -> list[dict]:
    """Shape result rows per one connector's ``result_shaping`` rule.

    Args:
        rows: Raw rows; non-dict entries are dropped.
        rule: Raw ``result_shaping`` dict.
        language: Language for ``spoken`` fields (``hindi`` | ``english``).

    Returns:
        New row dicts (inputs are not mutated): filtered, enriched, sorted.
    """
    drop = list(rule.get("drop_when") or [])
    kept = [dict(r) for r in rows if isinstance(r, dict) and not any(_matches(r, c) for c in drop)]
    for row in kept:
        for f in rule.get("strip_numbers_in") or []:
            if isinstance(row.get(f), str):
                row[f] = strip_place_numbers(row[f])
        for name, spec in (rule.get("spoken") or {}).items():
            text = spoken_pay(spec.get("format", ""), [row.get(f) for f in spec.get("from") or []],
                              spec.get("unit", "none"), language)
            if text:
                row[name] = text
            else:
                row.pop(name, None)
    for key in reversed(list(rule.get("sort") or [])):
        f = key.get("field", "")
        present = [r for r in kept if r.get(f) is not None and r.get(f) != ""]
        missing = [r for r in kept if r.get(f) is None or r.get(f) == ""]
        present.sort(key=lambda r: _sort_key(r.get(f)), reverse=key.get("order") == "desc")
        kept = present + missing
    return kept


class ResultShaper:
    """Applies each connector's ``result_shaping`` to its ToolResults.

    Args:
        config: Raw merged agent_core config dict.
    """

    def __init__(self, config: dict | None) -> None:
        cfg = config or {}
        conns = cfg.get("connectors") or {}
        self._rules: dict[str, dict] = {
            c["name"]: c["result_shaping"]
            for group in ("read", "write", "identity") for c in (conns.get(group) or [])
            if isinstance(c, dict) and c.get("name") and isinstance(c.get("result_shaping"), dict)
        }
        ln = (cfg.get("preprocessing") or {}).get("language_normalisation") or {}
        self._language = str(ln.get("default_language") or "english")

    def shape(self, result: ToolResult) -> ToolResult:
        """Return the result with shaped ``result_text``, or the same object when not applicable.

        Args:
            result: ToolResult from the Action Gateway (not a cache hit).

        Returns:
            Shaped ToolResult; the input unchanged on pass-through or error.
        """
        rule = self._rules.get(result.tool_name)
        if rule is None or not result.success or not result.projected or not result.result_text:
            return result
        try:
            payload = json.loads(result.result_text)
            list_key = rule.get("list_key") or ""
            if list_key:
                if not isinstance(payload, dict) or not isinstance(payload.get(list_key), list):
                    return result
                payload = {**payload, list_key: shape_rows(payload[list_key], rule, self._language)}
            else:
                if not isinstance(payload, list):
                    return result
                payload = shape_rows(payload, rule, self._language)
            return dataclasses.replace(result, result_text=json.dumps(payload, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("result_shaping.error", extra={"operation": "result_shaping.shape",
                                                          "status": "failure", "tool": result.tool_name,
                                                          "error": type(e).__name__})
            return result
