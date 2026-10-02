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


def test_tc08_hindi_with_loanword_passes():
    assert v("TC08", rec(([T(0, "a", "आपका profile update हो गया है")], "bot"))) == "pass"


def test_tc20_third_person_and_future():
    assert v("TC20", rec(([T(0, "a", "मैं बताऊँगा।")], "bot"))) == "fail"
    for ok in ("मैं बताती हूँ, यह काम आपको मिल सकता है", "मैं देखती हूँ कि आवेदन भेजा गया है",
               "मैं देखती हूँ, यहाँ काम चल रहा है"):
        assert v("TC20", rec(([T(0, "a", ok)], "bot"))) == "pass"


def test_tc05_midcall_thanks_and_no_goodbye():
    turns = [T(0, "a", "धन्यवाद, अब मैं आपके लिए नौकरी खोजती हूँ।"), T(1, "b", "धन्यवाद, नमस्ते।", ended=True)]
    assert v("TC05", rec((turns, "bot"))) == "pass"
    assert v("TC05", rec(([T(0, "a", "ठीक है", ended=True)], "bot"))) == "pass"


def test_tc07_latin_alias_boundaries_and_tap_only():
    tap_only = [T(0, "काम", "Meerut में एक काम है।", tap=[jobs_tap("ABC", city="Meerut")])]
    assert v("TC07", rec((tap_only, "bot"))) == "pass"
    places = {**PLACES, "Agra": ["आगरा", "agra"]}
    t = [T(0, "काम", "agra में काम है", tap=[jobs_tap("Agrawal Logistics", city="Kanpur")])]
    ctx = CheckCtx(rec((t, "bot")), P["T01"], places, False)
    assert DETERMINISTIC["TC07"](ctx).status == "fail"


def test_tc21_last_apply_entry_decides():
    turns = [T(0, "हाँ", "आपका आवेदन भेज दिया है।", tap=[apply_tap(500, {"error": "x"}), apply_tap()])]
    assert v("TC21", rec((turns, "bot"))) == "pass"


def test_goodbye_detection():
    from eval.voice_bench.checks import _is_goodbye
    for yes in ("धन्यवाद, आपका दिन शुभ हो।", "धन्यवाद, आपका दिन शुभ रहे।", "धन्यवाद। नमस्ते।",
                "आपका दिन शुभ हो, धन्यवाद।", "thank you"):
        assert _is_goodbye(yes), yes
    assert not _is_goodbye("धन्यवाद, अब मैं आपके लिए नौकरी खोजती हूँ।")


# ---- final-review fixes ---------------------------------------------------------------------------------------
from eval.voice_bench.checks import _apply_outcome  # noqa: E402


def test_tc09_confirmation_readback_is_not_a_reask():
    """M2: a reply carrying the known session value is a read-back, not "asking name again"."""
    confirm = [T(0, "रमेश", "ठीक है।", session={"name": "रमेश"}),
               T(1, "हाँ", "आपका नाम रमेश है, सही है?", session={"name": "रमेश"})]
    assert v("TC09", rec((confirm, "bot"))) == "pass"
    latin = [T(0, "Ramesh", "ठीक है।", session={"user_name": "Ramesh"}),
             T(1, "हाँ", "आपका नाम ramesh है ना?", session={"user_name": "Ramesh"})]
    assert v("TC09", rec((latin, "bot"))) == "pass"
    reask = [T(0, "रमेश", "ठीक है।", session={"name": "रमेश"}),
             T(1, "हाँ", "आपका नाम क्या है?", session={"name": "रमेश"})]
    assert v("TC09", rec((reask, "bot"))) == "fail"


def test_apply_outcome_already_only_from_409_or_error_fields():
    """M3: "already" anywhere in a 2xx body (e.g. a job title) is not an already-applied outcome."""
    assert _apply_outcome(200, {"summary": {"succeeded": 1}, "results": [{"title": "already hiring"}]}) == "success"
    assert _apply_outcome(201, {"note": "you already know this employer"}) == "success"
    assert _apply_outcome(409, {"error": "ACTION_LIMIT_REACHED"}) == "already"
    assert _apply_outcome(422, {"error": "ACTION_LIMIT_REACHED",
                                "message": "An active request already exists between these two profiles."}) == "already"
    assert _apply_outcome(400, {"error": {"code": "ALREADY_APPLIED", "message": "x"}}) == "already"
    assert _apply_outcome(500, {"error": "INTERNAL", "message": "boom", "detail": "already"}) == "error"
    assert _apply_outcome(500, "already broken") == "error"
    bulk = {"summary": {"total": 1, "succeeded": 0, "failed": 1},
            "results": [{"status": "error", "message": "Already applied to this job"}]}
    assert _apply_outcome(207, bulk) == "already"
    assert _apply_outcome(207, {**bulk, "summary": {"succeeded": 1}}) == "success"


def test_tc21_success_body_mentioning_already_needs_a_confirmation():
    body = {"summary": {"succeeded": 1}, "results": [{"job": "Already Hiring Pvt Ltd"}]}
    ok = [T(0, "हाँ", "आपका आवेदन भेज दिया है।", tap=[apply_tap(200, body)])]
    assert v("TC21", rec((ok, "bot"))) == "pass"
    wrong = [T(0, "हाँ", "आप पहले ही आवेदन कर चुके हैं।", tap=[apply_tap(200, body)])]
    assert v("TC21", rec((wrong, "bot"))) == "fail"


def _tw(i, caller, reply, word, ended=False):
    t = T(i, caller, reply, ended=ended)
    t.terminal_word = word
    return t


def test_tc05_terminal_word_counts_as_a_goodbye():
    """M9: the bridge strips the terminal word from the reply; TC05 still sees that turn as a goodbye."""
    assert v("TC05", rec(([T(0, "a", "ठीक है।"), _tw(1, "बस", "आपका दिन शुभ हो", "धन्यवाद", ended=True)], "bot"))) \
        == "pass"
    # caller had to hang up after the bot's terminal-word goodbye: the bot never released the line
    assert v("TC05", rec(([T(0, "a", "ठीक है।"), _tw(1, "बस", "", "Thank you")], "caller"))) == "fail"
    # a goodbye in the text on an earlier turn plus a terminal-word goodbye later = two goodbyes
    two = [T(0, "a", "धन्यवाद, नमस्ते।"), _tw(1, "b", "ठीक है", "धन्यवाद", ended=True)]
    assert v("TC05", rec((two, "bot"))) == "fail"


def test_tc09_placeholder_values_not_known():
    """TC09: placeholder values ("0", "false", empty string, "[]", "{}") are not treated as known."""
    # age = "0" is a placeholder, bot can ask for age again
    age_zero = [T(0, "नमस्ते", "ठीक है।", session={"age": "0"}),
                T(1, "चौबीस", "आपकी उम्र क्या है?", session={"age": "0"})]
    assert v("TC09", rec((age_zero, "bot"))) == "pass"
    
    # age = "24" is a real value, bot should not ask for age again
    age_real = [T(0, "नमस्ते", "ठीक है।", session={"age": "24"}),
                T(1, "चौबीस", "आपकी उम्र क्या है?", session={"age": "24"})]
    assert v("TC09", rec((age_real, "bot"))) == "fail"
    
    # has_age = "false" is a placeholder, bot can ask for age
    has_age_false = [T(0, "नमस्ते", "ठीक है।", session={"has_age": "false"}),
                     T(1, "चौबीस", "आपकी उम्र क्या है?", session={"has_age": "false"})]
    assert v("TC09", rec((has_age_false, "bot"))) == "pass"
    
    # empty string is a placeholder
    age_empty = [T(0, "नमस्ते", "ठीक है।", session={"age": ""}),
                 T(1, "चौबीस", "आपकी उम्र क्या है?", session={"age": ""})]
    assert v("TC09", rec((age_empty, "bot"))) == "pass"
    
    # {} is a placeholder
    age_empty_obj = [T(0, "नमस्ते", "ठीक है।", session={"age": "{}"}),
                     T(1, "चौबीस", "आपकी उम्र क्या है?", session={"age": "{}"})]
    assert v("TC09", rec((age_empty_obj, "bot"))) == "pass"
    
    # [] is a placeholder
    age_empty_array = [T(0, "नमस्ते", "ठीक है।", session={"age": "[]"}),
                       T(1, "चौबीस", "आपकी उम्र क्या है?", session={"age": "[]"})]
    assert v("TC09", rec((age_empty_array, "bot"))) == "pass"




def test_tc09_reask_after_caller_answered():
    """TC09: bot re-asks a field the caller already answered, even if the session never stored it."""
    z = {"age": "0"}
    live = [T(0, "नमस्ते", "नमस्ते।", session=z), T(1, "जी", "आपकी उम्र क्या है?", session=z),
            T(2, "मेरी उम्र 24 साल है।", "ठीक है।", session=z), T(3, "जी", "आपकी उम्र क्या है?", session=z)]
    r = DETERMINISTIC["TC09"](CheckCtx(rec((live, "bot")), P["T01"], PLACES, False))
    assert r.status == "fail" and r.reason == "asked age again after the caller answered (turn 2)"
    assert r.quote == "आपकी उम्र क्या है?"
    unsure = [live[0], live[1], T(2, "पता नहीं", "ठीक है।", session=z), live[3]]
    assert v("TC09", rec((unsure, "bot"))) == "pass"
    city = [T(0, "हाँ", "आप कहाँ रहते हैं?"), T(1, "लखनऊ", "ठीक है।"), T(2, "जी", "आपका शहर कौन सा है?")]
    assert v("TC09", rec((city, "bot"))) == "fail"
    corrected = [live[0], live[1], live[2], T(3, "गलत, उम्र पच्चीस है", "आपकी उम्र क्या है?", session=z)]
    assert v("TC09", rec((corrected, "bot"))) == "pass"
    word = [live[0], live[1], T(2, "पच्चीस", "ठीक है।", session=z), live[3]]
    assert v("TC09", rec((word, "bot"))) == "fail"
