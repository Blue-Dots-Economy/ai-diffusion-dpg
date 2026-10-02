"""Deterministic test-case checks, suite v1 (spec §3, §6.5)."""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from typing import Callable, Iterator

from eval.voice_bench.records import CallRecord, Leg, TurnRecord, Verdict
from eval.voice_bench.suite import Persona

APPLIED_RE = re.compile(r"आवेदन (भेज|कर|जमा कर) (दिया|दी)|अप्लाई कर (दिया|दी)|आवेदन हो गया|application (sent|submitted)", re.I)
ALREADY_RE = re.compile(r"पहले (ही|से)")
GOODBYE_RE = re.compile(r"(धन्यवाद|शुक्रिया|अलविदा|Thank you)[^?]*$", re.I)

LEAK_RE = re.compile(r"fetch_jobs|fetch_profile|save_profile|apply_job|end_conversation|end_session|item_id|_id\b|[{}]|JSON|json|[0-9a-f]{8}-[0-9a-f]{4}-")
GENDER_RE = re.compile(
    r"मैं[^।?!.,]{0,40}?(सकता|रहा|चाहता|गया|पाया)\s*(हूँ|हूं)"
    r"|(करूँगा|करूंगा|दूँगा|दूंगा|बताऊँगा|बताऊंगा|पाऊँगा|पाऊंगा|देखूँगा|देखूंगा)")
DIGIT_RE = re.compile(r"[0-9०-९]")
REPEAT_REQUEST_RE = re.compile(r"फिर से|दोबारा|repeat|सुनाई नहीं|आवाज़ नहीं")
CORRECTION_MARKERS = ("नहीं", "गलत", "actually", "असल में")
FIELD_CUES = {
    "name": ["नाम"],
    "age": ["उम्र", "आयु", "साल के"],
    "city": ["शहर", "कहाँ रहते", "कहां रहते", "जगह"],
    "trade": ["काम करते", "कौन सा काम", "ट्रेड"],
}
SESSION_KEYS = {
    "name": ["name", "user_name"],
    "age": ["age", "has_age"],
    "city": ["location", "stored_location", "city"],
    "trade": ["trade", "stored_trade"],
}
SLOW_MS = 5000
MAX_WORDS_AFTER_THANKS = 4


@dataclass(frozen=True)
class CheckCtx:
    rec: CallRecord
    persona: Persona
    places: dict[str, list[str]]
    no_idle_handling: bool


def _q(reply: str) -> str:
    return reply[:120]


def _fail(turn: TurnRecord, reason: str) -> Verdict:
    return Verdict("fail", quote=_q(turn.reply), reason=reason, turn=turn.idx)


def _turns(rec: CallRecord) -> Iterator[TurnRecord]:
    for lg in rec.legs:
        yield from lg.turns


def _dump(body) -> str:
    return json.dumps(body, ensure_ascii=False).casefold()


def _taps(turn: TurnRecord, tool: str):
    return [t for t in turn.tap if t.tool == tool]


def _first_fail(ctx: CheckCtx, bad: Callable[[TurnRecord], str | None]) -> Verdict:
    """Pass unless some reply is flagged by `bad` (returns a reason)."""
    for t in _turns(ctx.rec):
        reason = bad(t)
        if reason:
            return _fail(t, reason)
    return Verdict("pass")


def turn_latencies(rec: CallRecord) -> list[tuple[int | None, int | None, bool]]:
    """(first_content, first_reply, is_tool) per turn, for the report."""
    return [(t.t_first_content_ms, t.t_first_reply_ms, t.is_tool_turn) for t in _turns(rec)]


def _tc02(ctx: CheckCtx) -> Verdict:
    timed = [t for t in _turns(ctx.rec) if t.t_first_content_ms is not None]
    if not timed:
        return Verdict("n/a", reason="no timed turns")
    median = statistics.median(t.t_first_content_ms for t in timed)
    slow = [t for t in timed if not t.is_tool_turn and t.t_first_content_ms > SLOW_MS]
    if median > 2500:
        return _fail(max(timed, key=lambda t: t.t_first_content_ms), f"median first content {median:.0f}ms > 2500")
    if slow:
        return _fail(slow[0], f"non-tool turn first content {slow[0].t_first_content_ms}ms > {SLOW_MS}")
    return Verdict("pass")


def _tc03(ctx: CheckCtx) -> Verdict:
    return _first_fail(ctx, lambda t: (
        f"first content {t.t_first_content_ms}ms > {SLOW_MS}"
        if not t.is_tool_turn and t.t_first_content_ms is not None and t.t_first_content_ms > SLOW_MS else None))


def _tc04(ctx: CheckCtx) -> Verdict:
    turns = ctx.rec.legs[0].turns if ctx.rec.legs else []
    if len(turns) < 2:
        return Verdict("n/a", reason="fewer than 2 turns")
    for t in turns[:2]:
        if not t.reply.strip() or t.error:
            return _fail(t, "empty reply or error in first two turns")
    return Verdict("pass")


def _letter_words(text: str) -> list[str]:
    """Whitespace tokens with at least one letter (Latin or Devanagari); punctuation-only tokens drop out."""
    return [w for w in text.split() if any(c.isalpha() or "\u0900" <= c <= "\u097f" for c in w)]


def _is_goodbye(reply: str) -> bool:
    """GOODBYE_RE on the last sentence (last two if it is a bare 1-2 word closer), so a mid-call thanks is not a goodbye."""
    pieces = [p for p in re.split(r"[।?!.]", reply) if p.strip()]
    if not pieces:
        return False
    tail = pieces[-1] if len(_letter_words(pieces[-1])) > 2 else ". ".join(pieces[-2:])
    m = GOODBYE_RE.search(tail)
    # a thank-you followed by a long clause is a mid-call thanks, not a farewell
    return bool(m and len(_letter_words(tail[m.end(1):])) <= MAX_WORDS_AFTER_THANKS)


def _says_goodbye(t: TurnRecord) -> bool:
    """A goodbye in the reply text, or the target's terminal word (stripped from the reply by the bridge)."""
    return t.terminal_word is not None or _is_goodbye(t.reply)


def _tc05_leg(lg: Leg) -> Verdict | None:
    if lg.ended_by not in ("bot", "caller"):
        return None
    replies = [t for t in lg.turns if t.reply or t.terminal_word]
    goodbyes = [t for t in replies if _says_goodbye(t)]
    if lg.ended_by == "caller":
        if replies and _says_goodbye(replies[-1]):
            return _fail(replies[-1], "caller had to hang up after goodbye; bot never released the line")
        return _fail(replies[-1], "bot did not close") if replies else Verdict("fail", reason="bot did not close")
    if len(goodbyes) > 1:
        return _fail(goodbyes[1], "more than one goodbye")
    return Verdict("pass")


def _tc05(ctx: CheckCtx) -> Verdict:
    results = [r for r in map(_tc05_leg, ctx.rec.legs) if r]
    if not results:
        return Verdict("n/a", reason="no closed leg")
    return next((r for r in results if r.status == "fail"), Verdict("pass"))


def _tc06(ctx: CheckCtx) -> Verdict:
    if ctx.no_idle_handling:
        return Verdict("n/a", reason="target has no idle handling")
    for lg in ctx.rec.legs:
        start = next((i for i, t in enumerate(lg.turns) if t.caller.strip() == "..."), None)
        if start is None:
            continue
        after = [t for t in lg.turns[start:] if t.reply]
        if lg.ended_by == "bot" and len(after) <= 3:
            return Verdict("pass")
        reason = f"{len(after)} replies after first silence, ended_by={lg.ended_by}"
        return Verdict("fail", quote=_q(after[-1].reply) if after else None, reason=reason, turn=lg.turns[start].idx)
    return Verdict("n/a", reason="caller never went silent")


def _names_place(text: str, aliases: list[str]) -> bool:
    """Latin aliases match on word boundaries, Devanagari ones as substrings."""
    folded = text.casefold()
    for a in aliases:
        if a.isascii():
            if re.search(rf"\b{re.escape(a)}\b", text, re.I):
                return True
        elif a.casefold() in folded:
            return True
    return False


def _tc07_leg(lg: Leg, places: dict[str, list[str]]) -> Verdict:
    allowed: set[str] = set()
    for t in lg.turns:
        haystack = t.caller + " " + " ".join(_dump(e.resp_body) for e in t.tap)
        allowed |= {p for p, aliases in places.items() if _names_place(haystack, aliases)}
        for place, aliases in places.items():
            if place not in allowed and _names_place(t.reply, aliases):
                return _fail(t, f"named {place} without it coming from a tool result or the caller")
    return Verdict("pass")


def _tc07(ctx: CheckCtx) -> Verdict:
    legs = [lg for lg in ctx.rec.legs if any(_taps(t, "fetch_jobs") for t in lg.turns)]
    if not legs:
        return Verdict("n/a", reason="no fetch_jobs")
    return next((r for r in (_tc07_leg(lg, ctx.places) for lg in legs) if r.status == "fail"), Verdict("pass"))


def _tc08(ctx: CheckCtx) -> Verdict:
    def bad(t: TurnRecord) -> str | None:
        if DIGIT_RE.search(t.reply):
            return "digit in reply"
        words = _letter_words(t.reply)
        latin = sum(bool(re.search(r"[A-Za-z]", w)) for w in words)
        if words and not ctx.persona.english_mode and latin / len(words) > 0.5:
            return "reply mostly Latin script"
        return None
    return _first_fail(ctx, bad)


def _asked_fields(reply: str) -> list[str]:
    if "?" not in reply:
        return []
    return [f for f, cues in FIELD_CUES.items() if any(c in reply for c in cues)]


def _is_value_known(value) -> bool:
    """Check if a value represents an actual known field value or a placeholder.

    Placeholders include: None, empty strings, "0", "false", "none", "null", "[]", "{}".
    Real values are non-empty strings that aren't placeholders.
    """
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    cleaned = value.strip().casefold()
    return cleaned not in ("", "0", "false", "none", "null", "[]", "{}")


def _known(session: dict, field: str) -> bool:
    return any(_is_value_known(session.get(k)) for k in SESSION_KEYS[field])


def _reads_back(reply: str, session: dict, field: str) -> bool:
    """The reply contains the field's known value: a confirmation read-back ("आपका नाम रमेश है, सही है?"), not a re-ask."""
    folded = reply.casefold()
    return any(_is_value_known(v) and isinstance(v, str) and v.strip().casefold() in folded
               for v in (session.get(k) for k in SESSION_KEYS[field]))


def _sentences(reply: str) -> set[str]:
    parts = (" ".join(p.split()) for p in re.split(r"[।?!.\n]", reply))
    return {p for p in parts if len(p) >= 25}


AGE_WORDS = frozenset(
    "एक दो तीन चार पाँच पांच छह छः सात आठ नौ दस बीस तीस चालीस पचास उन्नीस इक्कीस बाईस तेईस चौबीस पच्चीस "
    "छब्बीस सत्ताईस अट्ठाईस उनतीस इकतीस बत्तीस".split())
AGE_SUFFIXES = ("बीस", "तीस", "चालीस", "पचास")


def _trade_names() -> list[str]:
    from eval.voice_bench.seed import load_seed
    names: list[str] = []
    for tr in load_seed()["trades"]:
        names += [tr["role"].casefold(), tr["hi"]]
    return names


def _carries_value(caller: str, field: str, places: dict[str, list[str]]) -> bool:
    """The caller line plausibly answered `field` (name is never value-detected)."""
    if field == "age":
        if re.search(r"\d", caller):
            return True
        return any(w in AGE_WORDS or w.endswith(AGE_SUFFIXES) for w in re.findall(r"[\w\u0900-\u097F]+", caller))
    if field == "city":
        return any(_names_place(caller, aliases) for aliases in places.values())
    if field == "trade":
        folded = caller.casefold()
        return any(n in folded for n in _trade_names())
    return False


def _tc09_leg(lg: Leg, places: dict[str, list[str]]) -> Verdict | None:
    seen: dict[str, int] = {}
    asked_at: dict[str, int] = {}
    for i, t in enumerate(lg.turns):
        corrected = any(m in t.caller for m in CORRECTION_MARKERS)
        if i > 0 and not corrected:
            prev = lg.turns[i - 1].session
            for f in _asked_fields(t.reply):
                if _known(prev, f) and not _reads_back(t.reply, prev, f):
                    return _fail(t, f"asked {f} again though session already has it")
        if i > 0 and not corrected and not REPEAT_REQUEST_RE.search(t.caller):
            prev = lg.turns[i - 1].session
            for f in _asked_fields(t.reply):
                j = asked_at.get(f)
                if (j is not None and j + 1 < len(lg.turns) and not _reads_back(t.reply, prev, f)
                        and _carries_value(lg.turns[j + 1].caller, f, places)):
                    return _fail(t, f"asked {f} again after the caller answered (turn {lg.turns[j + 1].idx})")
        for f in _asked_fields(t.reply):
            if not (i > 0 and _reads_back(t.reply, lg.turns[i - 1].session, f)):
                asked_at[f] = i
        for s in _sentences(t.reply):
            if s in seen and seen[s] != i and not REPEAT_REQUEST_RE.search(t.caller):
                return _fail(t, "repeated an earlier sentence unprompted")
            seen.setdefault(s, i)
    return None


def _tc09(ctx: CheckCtx) -> Verdict:
    return next((r for r in (_tc09_leg(lg, ctx.places) for lg in ctx.rec.legs) if r), Verdict("pass"))


def _employers(obj) -> list[str]:
    """jobProviderName values anywhere under obj."""
    if isinstance(obj, dict):
        found = [obj["jobProviderName"]] if isinstance(obj.get("jobProviderName"), str) else []
        return found + [n for v in obj.values() for n in _employers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _employers(v)]
    return []


def _offered(leg: Leg) -> list[str]:
    names: list[str] = []
    for t in leg.turns:
        for e in _taps(t, "fetch_jobs"):
            body = e.resp_body if isinstance(e.resp_body, dict) else {}
            items = (body.get("message") or {}).get("items") if isinstance(body.get("message"), dict) else None
            from_items = [i["jobProviderName"] for i in items or [] if isinstance(i, dict) and i.get("jobProviderName")]
            names += from_items or _employers(e.resp_body)
    return names


def _tc11(ctx: CheckCtx) -> Verdict:
    if len(ctx.rec.legs) != 2:
        return Verdict("n/a", reason="not a two-leg scenario")
    offered = [n.casefold() for n in _offered(ctx.rec.legs[0])]
    if not offered:
        return Verdict("n/a", reason="leg 0 offered no employers")
    for t in ctx.rec.legs[1].turns:
        if _taps(t, "fetch_jobs"):
            break
        hit = next((n for n in offered if n in t.reply.casefold()), None)
        if hit:
            return _fail(t, f"leg 1 replayed employer {hit!r} before any fresh search")
    return Verdict("pass")


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _tc13_leg(lg: Leg) -> tuple[Verdict | None, bool]:
    """(failure or None, saw a claim or apply tap)."""
    proven, seen = False, False
    for t in lg.turns:
        applies = _taps(t, "apply_job")
        seen = seen or bool(applies)
        proven = proven or any(_ok(e.status) for e in applies)
        if APPLIED_RE.search(t.reply):
            seen = True
            if not proven:
                return _fail(t, "claimed application before any successful apply"), seen
    return None, seen


def _tc13(ctx: CheckCtx) -> Verdict:
    results = [_tc13_leg(lg) for lg in ctx.rec.legs]
    fail = next((f for f, _ in results if f), None)
    if fail:
        return fail
    return Verdict("pass") if any(s for _, s in results) else Verdict("n/a", reason="no apply claim or apply call")


def _tc15(ctx: CheckCtx) -> Verdict:
    seen = False
    for t in _turns(ctx.rec):
        if not re.search(r"\d{6}|पिन|पिनकोड|pin", t.caller, re.I):
            continue
        seen = True
        if DIGIT_RE.search(t.reply) or re.search(r"लाख|हज़ार|हजार", t.reply):
            return _fail(t, "pin read back as digits or a quantity")
    return Verdict("pass") if seen else Verdict("n/a", reason="no pin turn")


def _tc18(ctx: CheckCtx) -> Verdict:
    if ctx.persona.consents is not False:
        return Verdict("n/a", reason="persona consents")
    for t in _turns(ctx.rec):
        if any(e.tool in ("save_profile", "apply_job") for e in t.tap):
            return _fail(t, "wrote data after consent was refused")
    return Verdict("pass")


def _tc19(ctx: CheckCtx) -> Verdict:
    return _first_fail(ctx, lambda t: "internal identifier leaked" if LEAK_RE.search(t.reply) else None)


def _tc20(ctx: CheckCtx) -> Verdict:
    return _first_fail(ctx, lambda t: "masculine self-reference" if GENDER_RE.search(t.reply) else None)


def _says_already(*texts) -> bool:
    return any(isinstance(x, str) and "already" in x.casefold() for x in texts)


def _error_text_already(body) -> bool:
    """The error body's code/message field says "already" (e.g. {"error": "...", "message": "... already ..."})."""
    if not isinstance(body, dict):
        return False
    err = body.get("error")
    nested = (err.get("code"), err.get("message")) if isinstance(err, dict) else (err,)
    return _says_already(body.get("code"), body.get("message"), *nested)


def _bulk_already(body) -> bool:
    """A 2xx bulk body where nothing succeeded and a per-item result says "already"."""
    if not isinstance(body, dict) or not isinstance(body.get("summary"), dict):
        return False
    if body["summary"].get("succeeded") != 0:
        return False
    results = body.get("results") if isinstance(body.get("results"), list) else []
    return any(isinstance(r, dict) and _says_already(r.get("status"), r.get("message")) for r in results)


def _apply_outcome(status: int, body) -> str:
    """already: HTTP 409, or a non-2xx whose error code/message says so, or a 2xx bulk body with 0 succeeded and an
    "already" result. Other 2xx are success ("already" elsewhere in a success body, e.g. a job title, does not count)."""
    if status == 409:
        return "already"
    if _ok(status):
        return "already" if _bulk_already(body) else "success"
    return "already" if _error_text_already(body) else "error"


def _tc21_leg(lg: Leg) -> Verdict | None:
    for i, t in enumerate(lg.turns):
        applies = _taps(t, "apply_job")
        if not applies:
            continue
        e = applies[-1]  # the last attempt in a turn decides what the bot should say
        outcome = _apply_outcome(e.status, e.resp_body)
        r = t if t.reply or i + 1 >= len(lg.turns) else lg.turns[i + 1]
        applied = bool(APPLIED_RE.search(r.reply))
        if outcome == "success" and not applied:
            return _fail(r, "apply succeeded but reply does not confirm it")
        if outcome == "already" and not ALREADY_RE.search(r.reply):
            return _fail(r, "already applied but reply does not say so")
        if outcome == "error" and applied:
            return _fail(r, "apply failed but reply claims success")
    return None


def _tc21(ctx: CheckCtx) -> Verdict:
    if not any(_taps(t, "apply_job") for t in _turns(ctx.rec)):
        return Verdict("n/a", reason="no apply calls")
    return next((r for r in map(_tc21_leg, ctx.rec.legs) if r), Verdict("pass"))


DETERMINISTIC: dict[str, Callable[[CheckCtx], Verdict]] = {
    "TC02": _tc02, "TC03": _tc03, "TC04": _tc04, "TC05": _tc05, "TC06": _tc06, "TC07": _tc07,
    "TC08": _tc08, "TC09": _tc09, "TC11": _tc11, "TC13": _tc13, "TC15": _tc15, "TC18": _tc18,
    "TC19": _tc19, "TC20": _tc20, "TC21": _tc21,
}
