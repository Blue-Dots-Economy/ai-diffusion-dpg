"""Scenario runner scrape of the agent_core turn-complete log (Spec E §10)."""
from eval.scenarios.run import _scrape_turn_extras, _split_by_outcome, parse_turn_extras

# Real format: logging.basicConfig renders "%(asctime)s %(levelname)s %(name)s %(message)s"
# and the extra= fields are NOT rendered, so the values are read from the multi-line
# "STREAM TURN COMPLETE" message in orchestrator.py.
_BANNER = (
    "2026-10-02 10:00:00,123 INFO src.orchestrator \n"
    "═══════════════════════════════════════════════════════════════\n"
    "  STREAM TURN COMPLETE  session=s1  intent=job_search  tool_used=True\n"
    "  model=m  total_latency=1500ms  next_subagent=job_match  sentences=2\n"
    "  llm_ttft=400ms  first_token=420ms  first_sentence=900ms\n"
    "  llm_calls=%s  predispatch_tool=%s  predispatch_outcome=%s  predispatch_ms=%s\n"
    "  response: 'secret reply'\n"
    "═══════════════════════════════════════════════════════════════\n"
)


def test_parse_turn_extras_reads_fired_record():
    text = _BANNER % (1, "fetch_jobs", "fired", 212)
    assert parse_turn_extras(text) == {
        "llm_calls": 1, "predispatch_tool": "fetch_jobs", "predispatch_outcome": "fired"}


def test_parse_turn_extras_takes_last_record_and_maps_none():
    text = _BANNER % (2, "fetch_jobs", "fired", 5) + _BANNER % (1, None, None, None)
    assert parse_turn_extras(text) == {
        "llm_calls": 1, "predispatch_tool": None, "predispatch_outcome": None}


def test_parse_turn_extras_no_match_is_all_none():
    assert parse_turn_extras("nothing relevant\n") == {
        "llm_calls": None, "predispatch_tool": None, "predispatch_outcome": None}


def test_scrape_never_raises_on_docker_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no docker")
    monkeypatch.setattr("eval.scenarios.run.subprocess.run", boom)
    assert _scrape_turn_extras("c", 0.0)["llm_calls"] is None


def test_split_by_outcome():
    turns = [{"first_sentence_ms": 100, "predispatch_outcome": "fired"},
             {"first_sentence_ms": 300, "predispatch_outcome": None},
             {"first_sentence_ms": None, "predispatch_outcome": "fired"}]
    out = _split_by_outcome(turns)
    assert out["fired"]["p50"] == 100 and out["not_fired"]["p50"] == 300
