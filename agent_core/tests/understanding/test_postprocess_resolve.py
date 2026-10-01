"""Tests for option resolution."""
from src.understanding.postprocess import resolve_reference
from src.workflow_loader import OptionsFrom, PendingQuestion

P = PendingQuestion("select_job", options_from=OptionsFrom("fetch_jobs", ("role", "company"), "item_id"),
                    resolves_to="selected_job_item_id")
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan"},
        {"role": "Welder", "company": "NoId"}]


def test_resolves_in_range():
    r, u = resolve_reference(2, P, ROWS)
    assert u is None and (r.option, r.id, r.label, r.id_field) == (2, "j2", "Welder · Titan", "item_id")


def test_out_of_range_and_zero():
    for opt in (0, 4, -1):
        r, u = resolve_reference(opt, P, ROWS)
        assert r is None and u.reason == "out_of_range" and u.offered == 3


def test_no_rows():
    r, u = resolve_reference(1, P, [])
    assert r is None and u.reason == "no_options" and u.offered == 0


def test_row_without_id():
    r, u = resolve_reference(3, P, ROWS)
    assert r is None and u.reason == "missing_id"


def test_nothing_to_resolve():
    assert resolve_reference(None, P, ROWS) == (None, None)
    assert resolve_reference(1, None, ROWS) == (None, None)
    assert resolve_reference(1, PendingQuestion("age"), ROWS) == (None, None)
