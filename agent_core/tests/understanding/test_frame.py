# agent_core/tests/understanding/test_frame.py
"""Tests for the NLU frame renderer."""
from src.understanding.frame import FrameBuilder, offered_rows
from src.workflow_loader import OptionsFrom, PendingQuestion

JOBS = PendingQuestion("select_job", "offered jobs में से एक",
                       options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"))
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan", "extra": "x"}]


def test_offered_rows_from_list_dict_or_nothing():
    assert offered_rows({"data": ROWS}) == ROWS
    assert offered_rows({"data": {"items": ROWS}}) == ROWS
    assert offered_rows({"data": {"other": 1}}) == []
    assert offered_rows(None) == []
    assert offered_rows({"data": [1, "x", {"item_id": "j"}]}) == [{"item_id": "j"}]


def test_full_frame_snapshot():
    text = FrameBuilder().build(
        step="job_match", pending=JOBS, rows=ROWS,
        known=[("consent", "granted"), ("trade", "Welder"), ("age", None)],
        recent=[{"caller": "वेल्डर", "bot": "बेंगलुरु में तीन नौकरियां हैं...", "interrupted": False}],
        segments=["पहले वाला"])
    assert text == (
        "<frame>\n"
        "step: job_match\n"
        "pending: select_job — offered jobs में से एक\n"
        "offered:\n"
        "  1. Welder · Flipkart\n"
        "  2. Welder · Titan\n"
        "known: consent=granted · trade=Welder\n"
        "</frame>\n"
        "<recent>\n"
        "caller: वेल्डर\n"
        "bot: बेंगलुरु में तीन नौकरियां हैं...\n"
        "</recent>\n"
        "<caller_now>\n"
        "पहले वाला\n"
        "</caller_now>"
    )


def test_no_pending_no_recent_no_known():
    text = FrameBuilder().build(step="opening", pending=None, rows=[], known=[], recent=[],
                                segments=["हाँ"])
    assert "pending: none" in text and "offered:" not in text and "known:" not in text
    assert "<recent>" not in text


def test_interrupted_segments_and_bot_reply_are_marked():
    text = FrameBuilder().build(
        step="job_match", pending=JOBS, rows=[], known=[],
        recent=[{"caller": "x", "bot": "आधा जवाब", "interrupted": True}],
        segments=["इलेक्ट्रीशियन", "नहीं वेल्डर"])
    assert "bot: [interrupted] आधा जवाब" in text
    assert "[interrupted] इलेक्ट्रीशियन\nनहीं वेल्डर" in text
    assert "offered:" not in text                     # options_from but no rows


def test_long_bot_reply_keeps_the_tail():
    reply = "शुरू " + ("बीच " * 400) + "क्या आप आवेदन करना चाहेंगे?"
    text = FrameBuilder(reply_cap=60).build(step="s", pending=None, rows=[], known=[],
                                            recent=[{"caller": "", "bot": reply, "interrupted": False}],
                                            segments=["हाँ"])
    bot_line = [l for l in text.splitlines() if l.startswith("bot: ")][0]
    assert bot_line.endswith("क्या आप आवेदन करना चाहेंगे?") and bot_line.startswith("bot: …")
    assert len(bot_line) <= len("bot: …") + 60
