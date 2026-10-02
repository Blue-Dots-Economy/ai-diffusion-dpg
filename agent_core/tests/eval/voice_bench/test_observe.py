import subprocess

from eval.voice_bench.observe import LogScraper, parse_banner, read_session

M3_BANNER = """
══════════
  STREAM TURN COMPLETE  session=919900001000:vb-T01-0-a  intent=any_input  tool_used=True
  model=gpt-4.1  total_latency=2140ms  next_subagent=job_match  sentences=2
  llm_ttft=812ms  first_token=820ms  first_sentence=1210ms
  llm_calls=1  predispatch_tool=fetch_jobs  predispatch_outcome=fired  predispatch_ms=640
  response: 'x'
══════════
"""
M0_BANNER = """
  STREAM TURN COMPLETE  session=919900001000  intent=any_input  tool_used=False
  model=gpt-4.1  total_latency=3010ms  next_subagent=opening  sentences=1
  response: 'y'
"""


def test_parse_banner_m3_and_m0_and_last_wins():
    b = parse_banner(M3_BANNER)
    assert b == {"total_latency_ms": 2140, "llm_calls": 1, "llm_ttft_ms": 812, "first_sentence_ms": 1210,
                 "predispatch_tool": "fetch_jobs", "predispatch_outcome": "fired", "predispatch_ms": 640}
    b0 = parse_banner(M3_BANNER + M0_BANNER)
    assert b0["total_latency_ms"] == 3010 and b0["llm_calls"] is None and b0["predispatch_tool"] is None
    assert parse_banner("") == {}


def test_parse_banner_handles_none_values():
    txt = M3_BANNER.replace("llm_ttft=812ms", "llm_ttft=Nonems").replace("predispatch_tool=fetch_jobs", "predispatch_tool=None")
    b = parse_banner(txt)
    assert b["llm_ttft_ms"] is None and b["predispatch_tool"] is None


def _fake_run(outputs):
    calls = []

    def run(args, **kw):
        calls.append(args)
        key = args[-1]
        return subprocess.CompletedProcess(args, 0, stdout=outputs.get(key, ""), stderr="")
    return run, calls


def test_read_session_falls_back_to_phone_key():
    run, calls = _fake_run({"session:919900001000": "current_subagent_id\nopening\nconsent_given\ntrue\n"})
    s = read_session("redis", "919900001000", "vb-1", run=run)
    assert s == {"current_subagent_id": "opening", "consent_given": "true"}
    assert calls[0][-1] == "session:919900001000:vb-1" and calls[1][-1] == "session:919900001000"


def test_log_scraper_without_container_is_empty():
    assert LogScraper(None).since(0) == ""
