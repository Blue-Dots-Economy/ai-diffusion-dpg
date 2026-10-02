"""Quoted-verdict LLM judge (spec §6.5): a pass/fail only counts if its quote is real bot text."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from eval.voice_bench.checks import _letter_words
from eval.voice_bench.drive import _seed
from eval.voice_bench.llm import JsonLLM
from eval.voice_bench.records import CallRecord, Verdict, bot_replies
from eval.voice_bench.suite import Persona

_RUBRICS: dict[str, str] | None = None
_BODY_MAX = 1500
_QUOTE_MIN_WORDS, _QUOTE_MIN_CHARS = 2, 8
_VERDICTS = ("pass", "fail", "n/a")


def _rubrics() -> dict[str, str]:
    global _RUBRICS
    if _RUBRICS is None:
        _RUBRICS = yaml.safe_load((Path(__file__).parent / "rubrics.yaml").read_text(encoding="utf-8"))
    return _RUBRICS


def _norm(s: str) -> str:
    return " ".join(s.split())


def parse_judgement(raw: dict, replies: list[str]) -> Verdict:
    """Validate the judge's JSON; anything malformed or unquoted is unscored."""
    verdict = raw.get("verdict") if isinstance(raw, dict) else None
    if verdict not in _VERDICTS:
        return Verdict("unscored", reason="invalid verdict")
    reason = raw.get("reason")
    reason = reason if isinstance(reason, str) else ""
    if verdict == "n/a":
        return Verdict("n/a", reason=reason)
    quote = raw.get("quote")
    if not isinstance(quote, str) or not _norm(quote):
        return Verdict("unscored", reason="missing quote")
    q = _norm(quote)
    if len(_letter_words(q)) < _QUOTE_MIN_WORDS or len(q.replace(" ", "")) < _QUOTE_MIN_CHARS:
        return Verdict("unscored", quote=quote, reason="quote too short")
    if not any(q in _norm(r) for r in replies):
        return Verdict("unscored", quote=quote, reason="quote not found")
    return Verdict(verdict, quote=quote, reason=reason)


_SUMMARY_JOB_KEYS = ("jobProviderName", "jobProviderLocation", "role", "natureOfJob", "salaryMin", "salaryMax")
_APPLY_RESP_MAX = 300


def _summarise_search(resp_body) -> list[dict] | None:
    items = ((resp_body or {}).get("message") or {}).get("items") if isinstance(resp_body, dict) else None
    if not isinstance(items, list):
        return None
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        state = it.get("item_state") if isinstance(it.get("item_state"), dict) else it
        rows.append({"item_id": it.get("item_id", state.get("item_id")), **{k: state.get(k) for k in _SUMMARY_JOB_KEYS}})
    return rows


def _summarise_tool(e) -> str | None:
    """Compact JSON for the tools the judge must read in full; None = fall back to truncation."""
    if e.path.endswith("/v1/search"):
        rows = _summarise_search(e.resp_body)
        return None if rows is None else json.dumps(rows, ensure_ascii=False, default=str)
    if e.path.endswith("/api/v1/action/perform"):
        target = ((e.req_body or {}).get("target_item") or {}) if isinstance(e.req_body, dict) else {}
        resp = json.dumps(e.resp_body, ensure_ascii=False, default=str)[:_APPLY_RESP_MAX]
        return json.dumps({"target_item_id": target.get("item_id") if isinstance(target, dict) else None,
                           "status": e.status, "response": resp}, ensure_ascii=False, default=str)
    return None


def _user_message(rec: CallRecord) -> str:
    multi = len(rec.legs) > 1
    lines, tools = [], []
    for li, leg in enumerate(rec.legs):
        for t in leg.turns:
            tag = f"L{li} t{t.idx}" if multi else str(t.idx)
            lines.append(f"[{tag}] आप: {t.caller}")
            lines.append(f"[{tag}] बॉट: {t.reply}")
            for e in t.tap:
                body = _summarise_tool(e)
                if body is None:
                    body = json.dumps(e.resp_body, ensure_ascii=False, default=str)[:_BODY_MAX]
                tools.append({"turn": tag, "tool": e.tool, "status": e.status, "resp_body": body})
    return "\n".join(lines) + "\n\nTool log:\n" + json.dumps(tools, ensure_ascii=False)


def judge_tc(llm: JsonLLM, tc_id: str, rec: CallRecord, persona: Persona, seed: int) -> Verdict:
    """One judge request for (call, TC)."""
    r = _rubrics()
    system = r["_preamble"] + "\n" + r[tc_id]
    try:
        raw = llm.complete_json(system, _user_message(rec), seed)
    except Exception as e:  # noqa: BLE001 - class name only, never the message
        return Verdict("error", reason=f"judge: {type(e).__name__}")
    return parse_judgement(raw, bot_replies(rec))
