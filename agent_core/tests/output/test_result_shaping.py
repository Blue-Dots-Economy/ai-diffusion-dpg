import json

from src.models import ToolResult
from src.output.result_shaping import ResultShaper, shape_rows, strip_place_numbers

RULE = {
    "list_key": "",
    "drop_when": [
        {"field": "role", "operator": "in", "value": [None, "", "na", "NA", "Any", "Not Available"]},
        {"field": "role", "operator": "contains", "value": "|"},
    ],
    "sort": [{"field": "match_score", "order": "desc"}, {"field": "salary_max", "order": "desc"}],
    "spoken": {"salary_spoken": {"format": "range_thousands", "from": ["salary_min", "salary_max"],
                                 "unit": "per_month"}},
    "strip_numbers_in": ["location"],
}
CONFIG = {
    "preprocessing": {"language_normalisation": {"default_language": "hindi"}},
    "connectors": {"read": [{"name": "fetch_jobs", "result_shaping": RULE}]},
}


def _rows():
    return [
        {"item_id": "a", "role": "Welder", "match_score": 0.7, "salary_min": 25755, "salary_max": 31121, "location": "Dasna, 201015"},
        {"item_id": "b", "role": "na", "match_score": 0.99},
        {"item_id": "c", "role": "Welder | Fitter", "match_score": 0.95},
        {"item_id": "d", "role": "Welder", "match_score": 0.9, "salary_min": 8192, "salary_max": 14404, "location": "Plot No. 12, Sector 5, Noida"},
        {"item_id": "e", "role": "Welder", "match_score": None, "salary_min": 32000, "salary_max": 25000},
        "not-a-row",
    ]


def test_drop_sort_spoken_strip():
    out = shape_rows(_rows(), RULE, "hindi")
    assert [r["item_id"] for r in out] == ["d", "a", "e"]
    assert out[0]["salary_spoken"] == "आठ से चौदह हज़ार रुपये महीना"
    assert out[0]["location"] == "Noida"
    assert out[1]["location"] == "Dasna"
    assert "salary_spoken" not in out[2]          # impossible range omitted, never guessed


def test_sort_is_stable_and_nulls_last():
    rows = [{"k": 1, "id": "x"}, {"k": None, "id": "n"}, {"k": 1, "id": "y"}, {"k": 2, "id": "z"}]
    out = shape_rows(rows, {"sort": [{"field": "k", "order": "desc"}]}, "hindi")
    assert [r["id"] for r in out] == ["z", "x", "y", "n"]


def test_all_rows_dropped_yields_empty_list():
    assert shape_rows([{"role": "na"}, {"role": ""}], RULE, "hindi") == []


def test_input_rows_not_mutated():
    rows = _rows()
    shape_rows(rows, RULE, "hindi")
    assert rows[0]["location"] == "Dasna, 201015" and "salary_spoken" not in rows[0]


def test_strip_place_numbers():
    assert strip_place_numbers("Dasna, 201015") == "Dasna"
    assert strip_place_numbers("House No 45, Gali 3, Sarjapur") == "Sarjapur"
    assert strip_place_numbers("Bengaluru") == "Bengaluru"


def _result(rows, *, projected=True, success=True, tool="fetch_jobs"):
    return ToolResult(tool_use_id="t1", tool_name=tool, result={"raw": True}, success=success,
                      result_text=json.dumps(rows), projected=projected)


def test_shaper_rewrites_result_text_only():
    shaped = ResultShaper(CONFIG).shape(_result(_rows()))
    assert [r["item_id"] for r in json.loads(shaped.result_text)] == ["d", "a", "e"]
    assert shaped.result == {"raw": True} and shaped.projected is True


def test_shaper_passes_through_unconfigured_failed_or_unprojected():
    s = ResultShaper(CONFIG)
    for r in (_result(_rows(), tool="other"), _result(_rows(), success=False), _result(_rows(), projected=False)):
        assert s.shape(r) is r


def test_shaper_list_key_payload():
    cfg = {"connectors": {"read": [{"name": "fetch_jobs", "result_shaping": {**RULE, "list_key": "items"}}]},
           "preprocessing": {"language_normalisation": {"default_language": "hindi"}}}
    r = ToolResult(tool_use_id="t", tool_name="fetch_jobs", result={}, success=True,
                   result_text=json.dumps({"items": _rows(), "total": 6}), projected=True)
    out = json.loads(ResultShaper(cfg).shape(r).result_text)
    assert out["total"] == 6 and [x["item_id"] for x in out["items"]] == ["d", "a", "e"]


def test_shaper_never_raises_on_bad_json():
    r = ToolResult(tool_use_id="t", tool_name="fetch_jobs", result={}, success=True,
                   result_text="not json", projected=True)
    assert ResultShaper(CONFIG).shape(r) is r
