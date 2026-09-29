"""Tests for TurnRecord and the streaming-lifecycle model fields (Agent Core block)."""

import json

from src.models import DoneEvent, SegmentInput, TurnRecord


def test_turn_record_defaults_are_independent():
    a, b = TurnRecord(), TurnRecord()
    a.captured_exchanges.append({"x": 1})
    a.segments.append("hi")
    assert b.captured_exchanges == [] and b.segments == []
    assert a.max_items == 0 and a.fold_ran is False
    assert a.last_stage == "" and a.write_carryover is True
    assert a.persist_task is None


def test_done_event_interrupted_at_stage_serialises():
    ev = DoneEvent(turn_status="interrupted", interrupted_at_stage="tool_end")
    payload = json.loads(ev.to_sse()[len("data: "):])
    assert payload["interrupted_at_stage"] == "tool_end"
    assert json.loads(DoneEvent().to_sse()[len("data: "):])["interrupted_at_stage"] is None


def test_segment_input_fresh_defaults_false():
    assert SegmentInput(text="hi").fresh is False
    assert SegmentInput(text="hi", fresh=True).fresh is True
