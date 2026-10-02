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


# ---- reset_session (C1) ----
import pytest  # noqa: E402

from eval.voice_bench.observe import reset_session  # noqa: E402


def _redis_run(responses, rc=0):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, rc, stdout=responses.get(args[4], ""), stderr="")
    return run, calls


def test_reset_session_flushes_a_private_stack_redis():
    run, calls = _redis_run({"FLUSHDB": "OK\n"})
    reset_session("redis", "919900001000", flush=True, run=run)
    assert calls == [["docker", "exec", "redis", "redis-cli", "FLUSHDB"]]


def test_reset_session_external_redis_deletes_only_this_phones_keys():
    keys = "session:919900001000\nsession:919900001000:vb-1\n"

    def run(args, **kw):
        calls.append(args)
        out = keys if args[-1] == "session:919900001000*" else ("user:919900001000\n" if "--scan" in args else "3\n")
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
    calls = []
    reset_session("dpg_redis", "919900001000", flush=False, run=run)
    assert [c[4:] for c in calls] == [["--scan", "--pattern", "session:919900001000*"],
                                      ["--scan", "--pattern", "user:919900001000*"],
                                      ["DEL", "session:919900001000", "session:919900001000:vb-1",
                                       "user:919900001000"]]
    assert not any("FLUSHDB" in c for c in calls)


def test_reset_session_no_keys_sends_no_del():
    run, calls = _redis_run({})
    reset_session("dpg_redis", "919900001000", flush=False, run=run)
    assert [c[4] for c in calls] == ["--scan", "--scan"]


def test_reset_session_failures_raise():
    with pytest.raises(RuntimeError, match="FLUSHDB"):
        reset_session("redis", "919900001000", flush=True, run=_redis_run({"FLUSHDB": ""}, rc=1)[0])
    with pytest.raises(RuntimeError, match="FLUSHDB on redis failed"):
        reset_session("redis", "919900001000", flush=True, run=_redis_run({"FLUSHDB": "ERR unknown"})[0])
    with pytest.raises(RuntimeError, match="did not answer OK"):
        reset_session("redis", "919900001000", flush=True, run=_redis_run({"FLUSHDB": "QUEUED"})[0])
    with pytest.raises(RuntimeError, match="digits"):
        reset_session("dpg_redis", "9199*", flush=False, run=_redis_run({})[0])

    def missing(args, **kw):
        raise FileNotFoundError("docker")
    with pytest.raises(RuntimeError, match="could not run"):
        reset_session("redis", "919900001000", flush=True, run=missing)
