import pytest

from eval.voice_bench.records import CallRecord, Leg, TapEntry, TurnRecord, Verdict, bot_replies


def _turn(i, reply, tap=()):
    return TurnRecord(idx=i, caller="नमस्ते", reply=reply, status_phrase=None, t_first_content_ms=900,
                      t_first_reply_ms=900, t_total_ms=1500, session={"k": "v"}, tap=list(tap), banner={},
                      session_ended=False, error=None)


def test_tap_entry_tool_classification():
    assert TapEntry(0, "GET", "/api/v1/admin/participant", "phone_number=9199", None, 200, {}, "signals").tool == "fetch_profile"
    assert TapEntry(0, "POST", "/api/v1/admin/participant", "", {}, 200, {}, "signals").tool == "save_profile"
    assert TapEntry(0, "POST", "/v1/search", "", {}, 200, {}, "search").tool == "fetch_jobs"
    assert TapEntry(0, "POST", "/api/v1/action/perform", "", {}, 200, {}, "signals").tool == "apply_job"
    assert TapEntry(0, "GET", "/health", "", None, 200, {}, "signals").tool == "other"


def test_call_record_roundtrip_keeps_devanagari_and_verdicts():
    tap = TapEntry(5, "POST", "/v1/search", "", {"q": "x"}, 200, {"items": []}, "search")
    rec = CallRecord(target="M3", target_commit="8b39427", scenario="T01", run=0, phone="919900001000",
                     suite_version=1, seed_version=1, caller_model="gpt-4.1", judge_model="gpt-4.1",
                     legs=[Leg(call_id="vb-T01-0-a", turns=[_turn(0, "नमस्ते, मैं ब्लू डॉट्स से बोल रही हूँ।", [tap])],
                               ended_by="bot")],
                     attempts=1, voided=False, error=None, verdicts={"TC19": Verdict("pass")}, started_at="2026-10-02T10:00:00Z")
    back = CallRecord.from_json(rec.to_json())
    assert back == rec
    assert "ब्लू" in rec.to_json()                      # ensure_ascii=False
    assert back.legs[0].turns[0].is_tool_turn
    assert bot_replies(back) == ["नमस्ते, मैं ब्लू डॉट्स से बोल रही हूँ।"]


def test_verdict_status_validated():
    with pytest.raises(ValueError):
        Verdict("ok")


def test_new_fields_round_trip_and_old_records_still_load():
    t = _turn(0, "आपका दिन शुभ हो।")
    t.terminal_word = "धन्यवाद"
    rec = CallRecord(target="M3", target_commit="c", scenario="T01", run=0, phone="919900001000", suite_version=1,
                     seed_version=1, caller_model="m", judge_model="m", legs=[Leg("b", [t], "bot")], attempts=2,
                     voided=False, error=None, harness_error="caller_llm_bad_json",
                     prior_legs=[Leg("a", [_turn(0, "x")], "error")], prior_error="http_502")
    back = CallRecord.from_json(rec.to_json())
    assert back == rec and back.legs[0].turns[0].terminal_word == "धन्यवाद"
    import json
    d = json.loads(rec.to_json())
    for k in ("harness_error", "prior_legs", "prior_error"):
        d.pop(k)
    d["legs"][0]["turns"][0].pop("terminal_word")
    old = CallRecord.from_json(json.dumps(d))
    assert old.harness_error is None and old.prior_legs == [] and old.legs[0].turns[0].terminal_word is None
