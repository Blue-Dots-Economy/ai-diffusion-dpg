# agent_core/tests/eval/voice_bench/test_checks.py
from eval.voice_bench.checks import DETERMINISTIC, CheckCtx
from eval.voice_bench.records import CallRecord, Leg, TapEntry, TurnRecord
from eval.voice_bench.suite import load_personas

PLACES = {"Lucknow": ["लखनऊ", "lucknow"], "Meerut": ["मेरठ", "meerut"], "Kanpur": ["कानपुर", "kanpur"]}
P = load_personas()


def T(i, caller, reply, ms=900, tap=(), session=None, ended=False):
    return TurnRecord(i, caller, reply, None, ms, ms, ms + 300, session or {}, list(tap), {}, ended, None)


def jobs_tap(*employers, city="Lucknow"):
    return TapEntry(1, "POST", "/v1/search", "", {}, 200,
                    {"message": {"items": [{"jobProviderName": e, "jobProviderLocation": city} for e in employers]}}, "search")


def apply_tap(status=200, body=None):
    return TapEntry(1, "POST", "/api/v1/action/perform", "", {}, status, body or {"summary": {"succeeded": 1}}, "signals")


def rec(*legs, scenario="T01"):
    return CallRecord("M3", "c", scenario, 0, "919900001000", 1, 1, "m", "m",
                      [Leg(f"c{i}", list(turns), ended) for i, (turns, ended) in enumerate(legs)], 1, False, None)


def v(tc, r, persona="T01", idle=False):
    return DETERMINISTIC[tc](CheckCtx(r, P[persona], PLACES, idle)).status


def test_tc03_tc02_latency():
    assert v("TC03", rec(([T(0, "a", "ठीक", 900), T(1, "b", "ठीक", 5200)], "bot"))) == "fail"
    assert v("TC03", rec(([T(0, "a", "ठीक", 900), T(1, "b", "ठीक", 5200, tap=[jobs_tap("X")])], "bot"))) == "pass"
    assert v("TC02", rec(([T(0, "a", "ठीक", 900), T(1, "b", "ठीक", 1200)], "bot"))) == "pass"


def test_tc04_first_reply():
    assert v("TC04", rec(([T(0, "नमस्ते", "नमस्ते"), T(1, "हाँ", "")], "bot"))) == "fail"
    assert v("TC04", rec(([T(0, "नमस्ते", "नमस्ते"), T(1, "हाँ", "ठीक है")], "bot"))) == "pass"


def test_tc05_goodbye_and_release():
    assert v("TC05", rec(([T(0, "बस", "धन्यवाद, नमस्ते।", ended=True)], "bot"))) == "pass"
    assert v("TC05", rec(([T(0, "बस", "धन्यवाद।"), T(1, "ओके", "धन्यवाद, फिर मिलेंगे।")], "caller"))) == "fail"


def test_tc06_silence():
    turns = [T(0, "नमस्ते", "नाम?"), T(1, "...", "क्या आप वहाँ हैं?"), T(2, "...", "मैं कॉल समाप्त कर रही हूँ। धन्यवाद", ended=True)]
    assert v("TC06", rec((turns, "bot")), "T07") == "pass"
    long = [T(0, "नमस्ते", "नाम?")] + [T(i, "...", "क्या आप वहाँ हैं?") for i in range(1, 6)]
    assert v("TC06", rec((long, "max_turns")), "T07") == "fail"
    assert v("TC06", rec((long, "max_turns")), "T07", idle=True) == "n/a"


def test_tc07_places_from_tool_or_caller():
    ok = [T(0, "लखनऊ में काम", "लखनऊ में एक काम है।", tap=[jobs_tap("ABC")])]
    bad = [T(0, "काम", "मेरठ में एक काम है।", tap=[jobs_tap("ABC")])]
    assert v("TC07", rec((ok, "bot"))) == "pass" and v("TC07", rec((bad, "bot"))) == "fail"


def test_tc08_script_and_digits():
    assert v("TC08", rec(([T(0, "a", "आपकी उम्र 24 है")], "bot"))) == "fail"
    assert v("TC08", rec(([T(0, "a", "Sure, I can help you find a job")], "bot"))) == "fail"
    assert v("TC08", rec(([T(0, "a", "आपकी उम्र चौबीस है")], "bot"))) == "pass"


def test_tc09_asked_twice_and_repeats():
    turns = [T(0, "रमेश", "आपकी उम्र क्या है?", session={"name": "रमेश"}),
             T(1, "चौबीस", "आपका नाम क्या है?", session={"name": "रमेश", "age": "24"})]
    assert v("TC09", rec((turns, "bot"))) == "fail"
    fixed = [turns[0], T(1, "चौबीस", "आप कौन सा काम करते हैं?", session={"name": "रमेश"})]
    assert v("TC09", rec((fixed, "bot"))) == "pass"
    rep = "क्या आप नई प्रोफ़ाइल बनाना चाहेंगे या पुरानी अपडेट करना चाहेंगे?"
    assert v("TC09", rec(([T(0, "a", rep), T(1, "b", rep)], "bot"))) == "fail"
    assert v("TC09", rec(([T(0, "a", rep), T(1, "फिर से बोलिए", rep)], "bot"))) == "pass"


def test_tc11_replay():
    leg0 = ([T(0, "ड्राइवर", "एबीसी ट्रांसपोर्ट में काम है", tap=[jobs_tap("ABC Transport")])], "bot")
    bad = ([T(0, "नमस्ते", "पिछली बार ABC Transport की बात हुई थी")], "bot")
    good = ([T(0, "नमस्ते", "नमस्ते, बताइए")], "bot")
    assert v("TC11", rec(leg0, bad, scenario="T13"), "T13") == "fail"
    assert v("TC11", rec(leg0, good, scenario="T13"), "T13") == "pass"


def test_tc13_and_tc21_apply_claims():
    claim_first = [T(0, "हाँ", "आपका आवेदन भेज दिया है।"), T(1, "ठीक", "ठीक है", tap=[apply_tap()])]
    assert v("TC13", rec((claim_first, "bot"))) == "fail"
    proven = [T(0, "हाँ", "आपका आवेदन भेज दिया है।", tap=[apply_tap()])]
    assert v("TC13", rec((proven, "bot"))) == "pass" and v("TC21", rec((proven, "bot"))) == "pass"
    err = [T(0, "हाँ", "आपका आवेदन भेज दिया है।", tap=[apply_tap(500, {"error": "x"})])]
    assert v("TC21", rec((err, "bot"))) == "fail"
    already = [T(0, "हाँ", "आप इस नौकरी के लिए पहले ही आवेदन कर चुके हैं।", tap=[apply_tap(409, {"error": "already applied"})])]
    assert v("TC21", rec((already, "bot"))) == "pass"


def test_tc15_pin_readback():
    assert v("TC15", rec(([T(0, "मेरा पिन 201301 है", "आपका पिन दो लाख एक हज़ार तीन सौ एक है?")], "bot")), "T02") == "fail"
    assert v("TC15", rec(([T(0, "मेरा पिन 201301 है", "आपका पिन दो शून्य एक तीन शून्य एक है?")], "bot")), "T02") == "pass"


def test_tc18_consent():
    save = TapEntry(1, "POST", "/api/v1/admin/participant", "", {}, 200, {}, "signals")
    assert v("TC18", rec(([T(0, "नहीं", "ठीक है", tap=[save])], "bot"), scenario="T12"), "T12") == "fail"
    assert v("TC18", rec(([T(0, "नहीं", "ठीक है")], "bot"), scenario="T12"), "T12") == "pass"
    assert v("TC18", rec(([T(0, "हाँ", "ठीक है", tap=[save])], "bot"))) == "n/a"


def test_tc19_tc20_regex():
    assert v("TC19", rec(([T(0, "a", "मैं fetch_jobs से देखती हूँ")], "bot"))) == "fail"
    assert v("TC19", rec(([T(0, "a", "मैं देखती हूँ")], "bot"))) == "pass"
    assert v("TC20", rec(([T(0, "a", "मैं आपकी मदद कर सकता हूँ")], "bot"))) == "fail"
    assert v("TC20", rec(([T(0, "a", "मैं आपकी मदद कर सकती हूँ")], "bot"))) == "pass"
