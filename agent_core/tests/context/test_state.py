from types import SimpleNamespace

from src.context.state import is_collected, render_recent, render_state
from src.understanding.frame import offered_entry

PENDING = SimpleNamespace(id="select_job", expects="offered jobs में से एक",
                          options_from=SimpleNamespace(tool="fetch_jobs", fields=("role", "company"), id_field="item_id"))
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan"}]


def test_render_state_all_lines():
    text = render_state(phase="job_match", pending=PENDING,
                        collected={"name": "अजय सिंह", "age": 28, "user_id": "u1", "trade": "",
                                   "attributes": [{"key": "city", "value": "Bengaluru"}]},
                        offered=ROWS, status={"applications_submitted": 0})
    assert text.splitlines() == [
        "phase: job_match",
        "waiting for: select_job — offered jobs में से एक",
        "collected (do not ask again): name=अजय सिंह · age=28 · city=Bengaluru",
        "offered (read in this order): 1. Welder · Flipkart; 2. Welder · Titan",
        "status: applications_submitted=0",
    ]


def test_render_state_omits_empty_lines():
    assert render_state(phase="opening", pending=None, collected={"age": 0}, offered=[], status={}) == \
        "phase: opening"


def test_is_collected_treats_zero_and_empty_as_missing():
    assert not any(is_collected(v) for v in (None, "", [], "[]", 0))
    assert is_collected("x") and is_collected(28)


def test_recent_renders_last_n():
    turns = [{"caller": "a", "bot": "b", "interrupted": False}, {"caller": "c", "bot": "d", "interrupted": False},
             {"caller": "e", "bot": "f", "interrupted": False}]
    assert render_recent(turns, 2) == "caller: c\nbot: d\ncaller: e\nbot: f"


def test_recent_marks_interrupted_turn():
    turns = [{"caller": "हाँ", "bot": "आपके लिए जॉब्स हैं — पहला:", "interrupted": True}]
    assert render_recent(turns, 2) == "caller: हाँ\nbot (caller heard only): आपके लिए जॉब्स हैं — पहला:"


def test_recent_zero_or_garbage_is_empty():
    assert render_recent([{"caller": "a", "bot": "b"}], 0) == ""
    assert render_recent("nope", 2) == ""
    assert render_recent([None, 3], 2) == ""


class _Cache:
    def __init__(self, entries):
        self._e = entries

    def entry(self, tool, h):
        return self._e.get((tool, h))

    def latest_entry(self, tool):
        return self._e.get((tool, "latest"))


def test_offered_entry_prefers_served_then_latest():
    cache = _Cache({("fetch_jobs", "h1"): {"data": "served"}, ("fetch_jobs", "latest"): {"data": "new"}})
    assert offered_entry({"fetch_jobs": "h1"}, cache, "fetch_jobs") == {"data": "served"}
    assert offered_entry({"fetch_jobs": "gone"}, cache, "fetch_jobs") == {"data": "new"}
    assert offered_entry(None, cache, "fetch_jobs") == {"data": "new"}
