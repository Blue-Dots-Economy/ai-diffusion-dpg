from src.understanding.history import append_recent_turn


def test_appends_and_caps():
    e = append_recent_turn(None, caller="a", bot="b", interrupted=False, history_turns=2)
    e = append_recent_turn(e, caller="c", bot="d", interrupted=True, history_turns=2)
    e = append_recent_turn(e, caller="e", bot="f", interrupted=False, history_turns=2)
    assert e == [{"caller": "c", "bot": "d", "interrupted": True},
                 {"caller": "e", "bot": "f", "interrupted": False}]


def test_zero_history_and_junk_existing():
    assert append_recent_turn([{"caller": "x"}], caller="a", bot="b", interrupted=False, history_turns=0) == []
    assert append_recent_turn("bad", caller="a", bot="b", interrupted=False, history_turns=1) == [
        {"caller": "a", "bot": "b", "interrupted": False}]
