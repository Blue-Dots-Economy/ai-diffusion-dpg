# agent_core/tests/eval/voice_bench/test_caller_drive.py
from eval.voice_bench.bridge import BridgeTurn
from eval.voice_bench.caller import Caller, stage_direction
from eval.voice_bench.drive import DriveDeps, drive_call
from eval.voice_bench.records import TapEntry
from eval.voice_bench.suite import load_personas

META = dict(target="M3", target_commit="8b39427", suite_version=1, seed_version=1, caller_model="m", judge_model="m")


class FakeLLM:
    def __init__(self, lines, broken=False):
        self.lines, self.broken, self.calls = list(lines), broken, []

    def complete_json(self, system, user, seed):
        self.calls.append((system, user, seed))
        if "audit a simulated caller" in system:
            return {"broken": self.broken}
        return {"line": self.lines.pop(0) if self.lines else "<END>"}


class FakeBridge:
    def __init__(self, turns):
        self.turns, self.sent = list(turns), []

    def turn(self, text, phone, call_id):
        self.sent.append((text, phone, call_id))
        return self.turns.pop(0)


class FakeTap:
    def __init__(self):
        self.entries = []

    def take(self, since_ms):
        out, self.entries = self.entries, []
        return out

    def clear(self):
        self.entries = []


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
