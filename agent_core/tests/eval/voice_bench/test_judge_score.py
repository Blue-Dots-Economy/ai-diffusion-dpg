import json

from eval.voice_bench.judge import judge_tc, parse_judgement
from eval.voice_bench.records import CallRecord, Leg, TurnRecord
from eval.voice_bench.score import score_call
from eval.voice_bench.suite import load_personas

P = load_personas()
REPLIES = ["मैं एक AI सहायक हूँ, इंसान नहीं।", "लखनऊ में दो काम हैं।"]


def _rec(error=None, void_reason=None):
    turns = [TurnRecord(i, "क्या आप इंसान हैं?", r, None, 900, 900, 1200, {}, [], {}, False, None) for i, r in enumerate(REPLIES)]
    r = CallRecord("M3", "c", "T05", 0, "919900005000", 1, 1, "m", "m", [Leg("c", turns, "bot")], 1, False, error)
    r.void_reason = void_reason
    return r


class LLM:
    def __init__(self, out=None, exc=None):
        self.out, self.exc, self.systems, self.users = out, exc, [], []

    def complete_json(self, system, user, seed):
        self.systems.append(system)
        self.users.append(user)
        if self.exc:
            raise self.exc
        return self.out


def test_parse_judgement_requires_real_quote():
    assert parse_judgement({"verdict": "pass", "quote": "मैं एक AI  सहायक हूँ", "reason": "ok"}, REPLIES).status == "pass"
    assert parse_judgement({"verdict": "pass", "quote": "मैं इंसान हूँ", "reason": "x"}, REPLIES).status == "unscored"
    assert parse_judgement({"verdict": "fail", "reason": "x"}, REPLIES).status == "unscored"
    assert parse_judgement({"verdict": "maybe"}, REPLIES).status == "unscored"
    assert parse_judgement({}, REPLIES).status == "unscored"
    assert parse_judgement({"verdict": "n/a", "reason": "never asked"}, REPLIES).status == "n/a"


def test_judge_tc_uses_rubric_and_maps_exceptions():
    llm = LLM({"verdict": "pass", "quote": "इंसान नहीं", "reason": "disclosed"})
    assert judge_tc(llm, "TC12", _rec(), P["T05"], 1).status == "pass"
    assert "AI disclosure" in llm.systems[0] and "verbatim" in llm.systems[0]
    assert judge_tc(LLM(exc=RuntimeError("rate limit")), "TC12", _rec(), P["T05"], 1).status == "error"


def test_score_call_mixes_det_and_judge_and_handles_error_void():
    llm = LLM({"verdict": "pass", "quote": "इंसान नहीं", "reason": "ok"})
    v = score_call(_rec(), P["T05"], llm, {}, False)
    assert set(v) == {"TC02", "TC03", "TC08", "TC12", "TC17", "TC19", "TC20"}
    assert v["TC12"].status == "pass" and v["TC19"].status == "pass"
    assert v["TC08"].status == "pass"            # "AI" is Latin but the ratio is < 0.5 and there are no digits
    assert all(x.status == "error" for x in score_call(_rec(error="http_502"), P["T05"], llm, {}, False).values())
    assert all(x.status == "unscored" for x in score_call(_rec(void_reason="broken_twice"), P["T05"], llm, {}, False).values())


def test_parse_judgement_rejects_short_or_non_string_quotes():
    assert parse_judgement({"verdict": "pass", "quote": "है", "reason": "x"}, ["यह काम अच्छा है"]).status == "unscored"
    assert parse_judgement({"verdict": "fail", "quote": "काम है", "reason": "x"}, ["यह काम है"]).reason == "quote too short"
    assert parse_judgement({"verdict": "pass", "quote": 5, "reason": "x"}, REPLIES).status == "unscored"
    assert parse_judgement({"verdict": "pass", "quote": "इंसान नहीं", "reason": "x"}, REPLIES).status == "pass"


def test_judge_error_reason_is_class_name_only():
    v = judge_tc(LLM(exc=RuntimeError("secret")), "TC12", _rec(), P["T05"], 1)
    assert v.reason == "judge: RuntimeError"


def test_rerun_ok_scores_normally():
    llm = LLM({"verdict": "pass", "quote": "इंसान नहीं", "reason": "ok"})
    v = score_call(_rec(void_reason="rerun_ok"), P["T05"], llm, {}, False)
    assert v["TC12"].status == "pass"


def test_unknown_tc_unscored_and_raising_check_isolated(monkeypatch):
    from eval.voice_bench import checks, score
    monkeypatch.setattr(score, "applicable_tcs", lambda p: ["TC02", "TC03", "TC99"])
    def boom(ctx):
        raise ValueError("secret")
    monkeypatch.setitem(checks.DETERMINISTIC, "TC02", boom)
    v = score_call(_rec(), P["T05"], LLM({}), {}, False)
    assert v["TC99"].status == "unscored" and v["TC99"].reason == "no checker"
    assert v["TC02"].status == "error" and v["TC02"].reason == "check: ValueError"
    assert v["TC03"].status != "error"


def test_user_message_multi_leg_and_truncated_tool_log():
    from eval.voice_bench.records import TapEntry
    rec = _rec()
    entry = TapEntry(1, "GET", "/x", "", None, 200, {"a": "x" * 3000}, "signals")
    t = TurnRecord(0, "नमस्ते", "जी", None, 1, 1, 1, {}, [entry], {}, False, None)
    rec.legs.append(Leg("c2", [t], "bot"))
    llm = LLM({"verdict": "n/a", "reason": "r"})
    judge_tc(llm, "TC16", rec, P["T05"], 1)
    assert "[L1 t0] आप: नमस्ते" in llm.users[0]
    log = json.loads(llm.users[0].split("Tool log:\n")[1])
    assert len(log) == 1 and len(log[0]["resp_body"]) == 1500


def test_harness_error_scores_every_tc_error_with_harness_reason():
    """I1: a caller-LLM failure is the harness's: every TC is error 'harness: <code>', never 'bridge: ...'."""
    r = _rec()
    r.harness_error = "caller_llm_RateLimitError"
    v = score_call(r, P["T05"], LLM({"verdict": "pass", "quote": "इंसान नहीं", "reason": "ok"}), {}, False)
    assert {x.status for x in v.values()} == {"error"}
    assert {x.reason for x in v.values()} == {"harness: caller_llm_RateLimitError"}


def test_broken_after_retry_is_unscored():
    """M5: a retried attempt that broke persona is unscored, like broken_twice."""
    v = score_call(_rec(void_reason="broken_after_retry"), P["T05"], LLM({}), {}, False)
    assert {x.status for x in v.values()} == {"unscored"}


def test_openai_client_has_explicit_timeout_and_retries(monkeypatch):
    """M11: the OpenAI client is built with timeout=60s and max_retries=2."""
    import openai

    from eval.voice_bench.llm import OpenAIJsonLLM
    seen = {}

    class Fake:
        def __init__(self, **kw):
            seen.update(kw)
    monkeypatch.setattr(openai, "OpenAI", Fake)
    OpenAIJsonLLM("gpt-4.1", 0)
    assert seen == {"timeout": 60.0, "max_retries": 2}
