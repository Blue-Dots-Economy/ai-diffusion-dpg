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
        self.out, self.exc, self.systems = out, exc, []

    def complete_json(self, system, user, seed):
        self.systems.append(system)
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
