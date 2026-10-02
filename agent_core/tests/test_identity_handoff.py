import json

from src.handoff import build_handoff_payload, choose_handoff_line
from src.identity import render_identity

IDENT = {"name": "ब्लू डॉट्स सहायक", "kind": "ai_assistant", "operator": "Blue Dots",
         "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "human_handoff": "none",
         "no_handoff_line": "अभी इस कॉल पर कोई इंसान उपलब्ध नहीं है।"}
LINES = {"delivered": "D", "failed": "F", "already": "A"}


def test_render_identity_none_and_rules():
    assert render_identity(None) == ""
    out = render_identity(IDENT)
    assert "ब्लू डॉट्स सहायक" in out and IDENT["disclosure"] in out and IDENT["no_handoff_line"] in out
    assert "Never claim to be human" in out


def test_render_identity_none_mode_no_handoff_line_only_for_a_person_request():
    """'who are you?' / 'are you a human?' get the disclosure only; asking for a person gets no_handoff_line."""
    out = render_identity(IDENT)
    who_rule = next(line for line in out.splitlines() if "who you are" in line)
    assert IDENT["disclosure"] in who_rule and "human or a computer" in who_rule
    assert IDENT["no_handoff_line"] not in who_rule and "speak to a person" not in who_rule
    person_rule = next(line for line in out.splitlines() if IDENT["no_handoff_line"] in line)
    assert "speak to a person" in person_rule
    assert "who you are" not in person_rule and "human or a computer" not in person_rule


def test_disclosure_text_only_inside_conditional_rule():
    """The disclosure must never appear as a standalone order (it was spoken in unrelated replies)."""
    for mode in ("none", "request"):
        out = render_identity({**IDENT, "human_handoff": mode})
        with_disclosure = [line for line in out.splitlines() if IDENT["disclosure"] in line]
        assert with_disclosure and all("Only when" in line for line in with_disclosure)
        assert any(line.startswith("Otherwise never say the disclosure line") for line in out.splitlines())


def test_render_identity_request_mode_omits_no_handoff_line():
    out = render_identity({**IDENT, "human_handoff": "request"})
    assert IDENT["no_handoff_line"] not in out and "handoff" in out.lower()


def test_payload_from_session_only_and_capped():
    long = "क" * 500
    session = {"name": "रमेश", "stored_trade": "Electrician", "stored_location": "Lucknow",
               "current_subagent_id": "job_match", "detected_language": "hi",
               "recent_turns": json.dumps([{"caller": f"c{i}\n{long}", "bot": f"b{i}"} for i in range(10)]),
               "last_application_id": "a-1", "applications_submitted": 1}
    p = build_handoff_payload(ticket_hint="", use_case="blue-dots", session=session, phone="919900001000",
                              call_id="c1", last_caller_turn=long, summary_turns=6, now_iso="2026-10-02T12:00:00Z")
    assert p["caller"] == {"phone": "919900001000", "name": "रमेश", "language": "hi"}
    assert p["context"]["step"] == "job_match" and p["context"]["trade"] == "Electrician"
    assert len(p["summary"]) == 6 and p["summary"][-1]["bot"] == "b9"
    assert all(len(t["caller"]) <= 300 and len(t["bot"]) <= 300 for t in p["summary"])
    assert len(p["context"]["last_caller_turn"]) <= 300
    assert p["reason"] == "human_request" and p["use_case"] == "blue-dots" and p["call_id"] == "c1"
    json.dumps(p, ensure_ascii=False)                 # serialisable


def test_payload_accepts_list_recent_turns():
    """Memory Layer may hand back the list already decoded (history.append_recent_turn writes a list)."""
    turns = [{"caller": "hi", "bot": "hello", "interrupted": False}]
    p = build_handoff_payload(ticket_hint="", use_case="u", session={"recent_turns": turns}, phone="91",
                              call_id="", last_caller_turn="", summary_turns=6, now_iso="t")
    assert p["summary"] == [{"caller": "hi", "bot": "hello"}]


def test_payload_tolerates_missing_or_bad_recent_turns():
    p = build_handoff_payload(ticket_hint="", use_case="u", session={"recent_turns": "not json"}, phone="91",
                              call_id="", last_caller_turn="", summary_turns=6, now_iso="t")
    assert p["summary"] == [] and p["caller"]["name"] == ""


def test_choose_line():
    assert choose_handoff_line({"delivered": True}, LINES, already=False) == ("D", "delivered")
    assert choose_handoff_line({"queued": False}, LINES, already=False) == ("F", "failed")
    assert choose_handoff_line(None, LINES, already=False) == ("F", "failed")
    assert choose_handoff_line(None, LINES, already=True) == ("A", "already")


def test_payload_application_carries_application_and_job_ids():
    p = build_handoff_payload(ticket_hint="", use_case="u", phone="91", call_id="", last_caller_turn="",
                              session={"last_application_id": "a-1", "selected_job_item_id": "job-9"},
                              summary_turns=6, now_iso="t")
    assert p["context"]["applications"] == [
        {"application_id": "a-1", "job_item_id": "job-9", "status": "submitted"}]


def test_payload_summary_empty_when_no_turns_requested():
    turns = [{"caller": "hi", "bot": "hello"}]
    p = build_handoff_payload(ticket_hint="", use_case="u", session={"recent_turns": turns}, phone="91",
                              call_id="", last_caller_turn="", summary_turns=0, now_iso="t")
    assert p["summary"] == []
