# agent_core/tests/eval/voice_bench/test_caller_drive.py
from eval.voice_bench.bridge import BridgeTurn
from eval.voice_bench.caller import Caller, stage_direction
from eval.voice_bench.drive import DriveDeps, drive_call
from eval.voice_bench.suite import load_personas

META = dict(target="M3", target_commit="8b39427", suite_version=1, seed_version=1, caller_model="m", judge_model="m")


class FakeLLM:
    def __init__(self, lines, broken=False):
        self.lines, self.broken, self.calls = list(lines), broken, []
        self.audit, self.raise_on_line = None, None

    def complete_json(self, system, user, seed):
        self.calls.append((system, user, seed))
        if "audit a simulated caller" in system:
            return self.audit if self.audit is not None else {"broken": self.broken}
        if self.raise_on_line:
            raise self.raise_on_line.pop(0)
        if self.lines and self.lines[0] is None:
            self.lines.pop(0)
            return {}
        return {"line": self.lines.pop(0) if self.lines else "<END>"}


class FakeBridge:
    def __init__(self, turns):
        self.turns, self.sent = list(turns), []

    def turn(self, text, phone, call_id):
        self.sent.append((text, phone, call_id))
        return self.turns.pop(0)


class FakeTap:
    """take() returns the next scripted batch (default []); clear() is counted."""

    def __init__(self, script=()):
        self.script, self.clears, self.takes = list(script), 0, 0

    def take(self):
        self.takes += 1
        return list(self.script.pop(0)) if self.script else []

    def clear(self):
        self.clears += 1


class NoLogs:
    def since(self, s):
        return ""


def _bt(reply, ended=False, error=None):
    return BridgeTurn(reply, None, 800, 800, 1200, ended, error)


def _deps(bridge, llm, cleanups):
    return DriveDeps(bridge=bridge, tap=FakeTap(), redis_container="redis", scraper=NoLogs(), caller=Caller(llm),
                     judge_llm=llm, cleanup=lambda: cleanups.append(1), read_session=lambda *a: {"k": "v"})


def test_stage_direction_regex():
    assert stage_direction("(हँसते हुए) हाँ") and stage_direction("*pause* हाँ") and stage_direction("[silence]")
    assert not stage_direction("हाँ जी, लखनऊ") and not stage_direction("...")


def test_drive_single_leg_until_bot_ends():
    p = load_personas()["T01"]
    bridge = FakeBridge([_bt("नमस्ते, आपका नाम?"), _bt("धन्यवाद, नमस्ते।", ended=True)])
    cleanups = []
    rec = drive_call(_deps(bridge, FakeLLM(["रमेश"]), cleanups), p, 0, "919900001000", 14, META)
    (leg,) = rec.legs
    assert [t.caller for t in leg.turns] == ["नमस्ते", "रमेश"] and leg.ended_by == "bot"
    assert leg.turns[1].session_ended and leg.turns[0].session == {"k": "v"}
    assert rec.attempts == 1 and not rec.voided and rec.error is None
    assert bridge.sent[0][1] == "919900001000" and bridge.sent[0][2].startswith("vb-T01-0-0-")
    assert len(cleanups) == 2                     # before the first attempt and after the final one


def test_drive_two_legs_use_two_call_ids_same_phone():
    p = load_personas()["T13"]
    bridge = FakeBridge([_bt("नमस्ते"), _bt("धन्यवाद", ended=True), _bt("नमस्ते"), _bt("धन्यवाद", ended=True)])
    rec = drive_call(_deps(bridge, FakeLLM(["बस", "बस"]), []), p, 0, "919900013000", 14, META)
    assert len(rec.legs) == 2 and rec.legs[0].call_id != rec.legs[1].call_id
    assert {s[1] for s in bridge.sent} == {"919900013000"}


def test_drive_retries_once_on_bridge_error_and_records_it():
    p = load_personas()["T01"]
    bridge = FakeBridge([_bt("", error="http_502"), _bt("नमस्ते"), _bt("धन्यवाद", ended=True)])
    rec = drive_call(_deps(bridge, FakeLLM(["रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.attempts == 2 and rec.error is None and rec.legs[0].ended_by == "bot"


def test_drive_error_twice_keeps_error():
    p = load_personas()["T01"]
    bridge = FakeBridge([_bt("", error="http_502"), _bt("", error="http_502")])
    rec = drive_call(_deps(bridge, FakeLLM([]), []), p, 0, "919900001000", 14, META)
    assert rec.attempts == 2 and rec.error == "http_502" and rec.legs[0].ended_by == "error"


def test_drive_voids_and_reruns_on_persona_break():
    p = load_personas()["T01"]
    bridge = FakeBridge([_bt("नमस्ते"), _bt("धन्यवाद", ended=True)] * 2)
    rec = drive_call(_deps(bridge, FakeLLM(["(हँसते हुए) रमेश", "रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.voided is True and rec.attempts == 2 and rec.void_reason == "rerun_ok"


def test_drive_stops_at_max_turns():
    p = load_personas()["T07"]
    bridge = FakeBridge([_bt("आपका नाम?")] * 3)
    rec = drive_call(_deps(bridge, FakeLLM(["...", "..."]), []), p, 0, "919900007000", 3, META)
    assert len(rec.legs[0].turns) == 3 and rec.legs[0].ended_by == "max_turns"


def _ok():
    return [_bt("नमस्ते"), _bt("धन्यवाद", ended=True)]


def test_cleanup_count_three_on_retry_and_void_paths():
    p = load_personas()["T01"]
    c1 = []
    drive_call(_deps(FakeBridge([_bt("", error="x")] + _ok()), FakeLLM(["रमेश"]), c1), p, 0, "919900001000", 14, META)
    c2 = []
    drive_call(_deps(FakeBridge(_ok() * 2), FakeLLM(["(x) रमेश", "रमेश"]), c2), p, 0, "919900001000", 14, META)
    assert len(c1) == 3 and len(c2) == 3


def test_broken_twice_reason():
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge(_ok() * 2), FakeLLM(["(x) रमेश", "(y) रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.voided and rec.void_reason == "broken_twice" and rec.attempts == 2


def test_caller_end_on_turn_one():
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge([_bt("नमस्ते")]), FakeLLM(["<END>"]), []), p, 0, "919900001000", 14, META)
    assert rec.legs[0].ended_by == "caller" and len(rec.legs[0].turns) == 1 and rec.error is None


def test_caller_llm_raises_retries():
    p = load_personas()["T01"]
    llm = FakeLLM(["रमेश"])
    llm.raise_on_line = [RuntimeError("boom")]
    rec = drive_call(_deps(FakeBridge([_bt("नमस्ते")] + _ok()), llm, []), p, 0, "919900001000", 14, META)
    assert rec.attempts == 2 and rec.error is None and rec.legs[0].ended_by == "bot"


def test_caller_llm_raises_twice_records_error_and_cleans_up():
    p = load_personas()["T01"]
    llm = FakeLLM([])
    llm.raise_on_line = [RuntimeError("a"), RuntimeError("b")]
    cl = []
    rec = drive_call(_deps(FakeBridge([_bt("नमस्ते")] * 2), llm, cl), p, 0, "919900001000", 14, META)
    assert rec.error is None and rec.harness_error == "caller_llm_RuntimeError" and len(cl) == 3


def test_caller_bad_json_error_after_retry():
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge([_bt("नमस्ते")] * 2), FakeLLM([None, None]), []), p, 0, "919900001000", 14, META)
    assert rec.attempts == 2 and rec.harness_error == "caller_llm_bad_json" and rec.error is None
    assert rec.legs[0].ended_by == "error"


def test_audit_bad_json_treated_as_broken():
    p = load_personas()["T01"]
    llm = FakeLLM(["रमेश", "रमेश"])
    llm.audit = {}
    rec = drive_call(_deps(FakeBridge(_ok() * 2), llm, []), p, 0, "919900001000", 14, META)
    assert rec.voided and rec.void_reason == "broken_twice"


def test_cleanup_runs_when_audit_llm_raises_out_of_drive():
    p = load_personas()["T01"]
    class Boom(FakeLLM):
        def complete_json(self, system, user, seed):
            if "audit a simulated caller" in system:
                return {"broken": False}
            return super().complete_json(system, user, seed)
    cl = []
    deps = _deps(FakeBridge(_ok()), Boom(["रमेश"]), cl)
    deps.bridge.turn = lambda *a: (_ for _ in ()).throw(RuntimeError("bridge"))
    try:
        drive_call(deps, p, 0, "919900001000", 14, META)
    except RuntimeError:
        pass
    assert len(cl) == 2


# ---- final-review fixes ---------------------------------------------------------------------------------------
from eval.voice_bench.drive import _seed  # noqa: E402
from eval.voice_bench.records import CallRecord, TapEntry  # noqa: E402


def _tap(path):
    return TapEntry(1, "POST", path, "", {}, 200, {}, "signals")


def test_tap_between_turns_and_after_the_last_turn_is_kept():
    """I2: take() drains everything; late entries land on the next turn, leftovers on the leg's last turn."""
    p = load_personas()["T01"]
    a, b, late = _tap("/a"), _tap("/b"), _tap("/late")
    tap = FakeTap([[a], [b], [late]])          # turn 0, turn 1, end-of-leg leftover
    deps = _deps(FakeBridge(_ok()), FakeLLM(["रमेश"]), [])
    deps.tap = tap
    rec = drive_call(deps, p, 0, "919900001000", 14, META)
    t0, t1 = rec.legs[0].turns
    assert t0.tap == [a] and t1.tap == [b, late]
    assert tap.clears == 1 and tap.takes == 3


def test_tap_cleared_at_the_start_of_every_attempt():
    p = load_personas()["T01"]
    deps = _deps(FakeBridge([_bt("", error="http_502")] + _ok()), FakeLLM(["रमेश"]), [])
    deps.tap = FakeTap()
    drive_call(deps, p, 0, "919900001000", 14, META)
    assert deps.tap.clears == 2


def test_retry_keeps_the_first_attempt_in_prior_legs():
    """I3: the discarded attempt and its error are kept; the record round-trips them."""
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge([_bt("", error="http_502")] + _ok()), FakeLLM(["रमेश"]), []), p, 0,
                     "919900001000", 14, META)
    assert rec.prior_error == "http_502" and len(rec.prior_legs) == 1
    assert rec.prior_legs[0].ended_by == "error" and rec.legs[0].ended_by == "bot"
    back = CallRecord.from_json(rec.to_json())
    assert back.prior_legs == rec.prior_legs and back.prior_error == "http_502"


def test_void_rerun_keeps_the_broken_attempt_in_prior_legs():
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge(_ok() * 2), FakeLLM(["(x) रमेश", "रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.void_reason == "rerun_ok" and rec.prior_error is None
    assert rec.prior_legs[0].turns[1].caller == "(x) रमेश" and rec.legs[0].turns[1].caller == "रमेश"


def test_no_retry_means_no_prior_legs():
    p = load_personas()["T01"]
    rec = drive_call(_deps(FakeBridge(_ok()), FakeLLM(["रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.prior_legs == [] and rec.prior_error is None and rec.harness_error is None


def test_retried_attempt_is_persona_audited():
    """M5: error → retry whose caller breaks persona → broken_after_retry (no third attempt)."""
    p = load_personas()["T01"]
    bridge = FakeBridge([_bt("", error="http_502")] + _ok())
    rec = drive_call(_deps(bridge, FakeLLM(["(हँसते हुए) रमेश"]), []), p, 0, "919900001000", 14, META)
    assert rec.attempts == 2 and rec.error is None
    assert rec.voided and rec.void_reason == "broken_after_retry" and bridge.turns == []


def test_reattempt_caller_seed_includes_attempt_index():
    """M6: attempt 0 keeps the paired seed; the re-run uses a different one."""
    p = load_personas()["T01"]
    llm = FakeLLM(["(x) रमेश", "रमेश"])
    drive_call(_deps(FakeBridge(_ok() * 2), llm, []), p, 0, "919900001000", 14, META)
    seeds = [seed for system, _, seed in llm.calls if "audit a simulated caller" not in system]
    assert seeds[0] == _seed("T01", 0, 0, 1) and seeds[1] != seeds[0]


def test_terminal_word_is_recorded_on_the_turn():
    p = load_personas()["T01"]
    bye = BridgeTurn("आपका दिन शुभ हो।", None, 800, 800, 1200, True, None, "Thank you")
    rec = drive_call(_deps(FakeBridge([_bt("नमस्ते"), bye]), FakeLLM(["बस"]), []), p, 0, "919900001000", 14, META)
    assert rec.legs[0].turns[1].terminal_word == "Thank you" and rec.legs[0].turns[0].terminal_word is None
