"""Loki-backed observation: log records in, tap entries out."""
from __future__ import annotations

import json

import httpx
import pytest

from eval.voice_bench.observe_loki import LokiClient, LokiError, LokiObserver, tool_entries

SID = "919060601102:br-n2"


def _line(body: str, attrs: dict, traceid: str = "aa11") -> str:
    return json.dumps({"body": body, "traceid": traceid, "severity": "INFO", "attributes": attrs,
                       "resources": {"service.name": "action_gateway"}})


def _execute(tool: str, sid: str = SID, traceid: str = "aa11", status: str = "success") -> str:
    return _line("rest_api_execute", {"operation": "RestApiAdapter.execute", "session_id": sid,
                                      "tool_name": tool, "status": status, "latency_ms": 81}, traceid)


def _request(tool: str, url: str, params: dict, traceid: str = "aa11") -> str:
    return _line("rest_api_request", {"operation": "RestApiAdapter.execute", "session_id": SID,
                                      "tool_name": tool, "method": "GET", "url": url, "params": params}, traceid)


def _response(tool: str, body, code: int = 200, traceid: str = "aa11") -> str:
    return _line("rest_api_response", {"operation": "RestApiAdapter.execute", "session_id": SID,
                                       "tool_name": tool, "status_code": code, "body": body}, traceid)


def test_tool_entries_joins_the_three_records_of_one_call():
    lines = [(1000, _request("fetch_jobs", "https://s.example/signals-search/v1/search?x=1", {"q": "welder"})),
             (1010, _response("fetch_jobs", '{"items":[]}')),
             (1020, _execute("fetch_jobs"))]
    [entry] = list(tool_entries(lines, SID).values())
    assert entry.tool == "fetch_jobs"
    assert entry.upstream == "search"
    assert entry.path == "/signals-search/v1/search" and entry.query == "x=1"
    assert entry.status == 200 and entry.resp_body == '{"items":[]}'
    assert entry.req_body == {"q": "welder"}


def test_tool_entries_keeps_calls_apart_by_trace():
    lines = [(1000, _execute("fetch_profile", traceid="t1")), (1100, _execute("fetch_jobs", traceid="t2"))]
    assert sorted(e.tool for e in tool_entries(lines, SID).values()) == ["fetch_jobs", "fetch_profile"]


def test_tool_entries_ignores_other_sessions():
    lines = [(1000, _execute("apply_job", sid="919900000001:other"))]
    assert tool_entries(lines, SID) == {}


def test_tool_entries_without_debug_lines_keeps_the_tool_and_marks_success():
    """INFO only: the call and its outcome are visible, the response body is not."""
    [entry] = list(tool_entries([(1000, _execute("save_profile"))], SID).values())
    assert entry.tool == "save_profile" and entry.status == 200 and entry.resp_body is None


def test_tool_entries_failed_call_is_not_reported_as_200():
    [entry] = list(tool_entries([(1000, _execute("apply_job", status="failure"))], SID).values())
    assert entry.status == 0


def test_tool_entries_skips_lines_that_are_not_json():
    assert tool_entries([(1000, "plain text line")], SID) == {}


def _client(handler, token="glsa_test") -> LokiClient:
    return LokiClient("http://grafana:3001", token=token, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_needs_a_token(monkeypatch):
    monkeypatch.delenv("VOICE_BENCH_GRAFANA_TOKEN", raising=False)
    with pytest.raises(LokiError, match="VOICE_BENCH_GRAFANA_TOKEN"):
        LokiClient("http://grafana:3001")


def test_client_returns_lines_oldest_first():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer glsa_test"
        return httpx.Response(200, json={"data": {"result": [
            {"stream": {}, "values": [["2000000000", "b"], ["1000000000", "a"]]}]}})

    assert _client(handler).lines("{job=\"x\"}", 0, 10) == [(1000, "a"), (2000, "b")]


def test_client_refused_credentials_say_so_without_the_token():
    def handler(request):
        return httpx.Response(403)

    with pytest.raises(LokiError) as e:
        _client(handler).lines("{job=\"x\"}", 0, 10)
    assert "Viewer" in str(e.value) and "glsa_test" not in str(e.value)


def test_client_retries_then_gives_up():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(LokiError):
        _client(handler).lines("{job=\"x\"}", 0, 10)
    assert len(calls) == 2


class _Lines:
    def __init__(self, batches):
        self.batches, self.queries = list(batches), []

    def lines(self, query, start_s, end_s):
        self.queries.append(query)
        return list(self.batches.pop(0)) if self.batches else []


BANNER = "STREAM TURN COMPLETE  session=%s  total_latency=2234ms llm_calls=1\nresponse: x" % SID


class _Clock:
    """Monotonic clock that advances a second every time it is read."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def _observer(batches, **kw):
    return LokiObserver(_Lines(batches), sleep=lambda s: None, clock=_Clock(), **kw)


def test_turn_reads_the_banner_then_the_tool_calls():
    obs = _observer([[(1, BANNER)], [(2, _execute("fetch_jobs"))]])
    out = obs.turn("919060601102", "br-n2", 0)
    assert out.banner["total_latency_ms"] == 2234
    assert [e.tool for e in out.tap] == ["fetch_jobs"]
    assert out.session == {}


def test_turn_does_not_report_the_same_call_twice():
    obs = _observer([[(1, BANNER)], [(2, _execute("fetch_jobs"))],
                     [(1, BANNER)], [(2, _execute("fetch_jobs"))]])
    assert len(obs.turn("919060601102", "br-n2", 0).tap) == 1
    assert obs.turn("919060601102", "br-n2", 0).tap == []


def test_turn_without_a_banner_gives_up_rather_than_waiting_forever():
    obs = _observer([[], []])
    assert obs.turn("919060601102", "br-n2", 0).banner == {}


def test_a_new_attempt_forgets_what_the_last_one_saw():
    obs = _observer([[(1, BANNER)], [(2, _execute("fetch_jobs"))],
                     [(1, BANNER)], [(2, _execute("fetch_jobs"))]])
    obs.turn("919060601102", "br-n2", 0)
    obs.start_attempt()
    assert len(obs.turn("919060601102", "br-n2", 0).tap) == 1


def test_session_memory_is_never_claimed():
    assert _observer([]).sees_session is False


def test_the_query_is_scoped_to_this_call():
    reader = _Lines([[(1, BANNER)], [(2, _execute("fetch_jobs"))]])
    LokiObserver(reader, sleep=lambda s: None, clock=_Clock()).turn("919060601102", "br-n2", 0)
    assert all(SID in q for q in reader.queries)
    assert reader.queries[0].startswith('{job="agent_core"}')
    assert reader.queries[1].startswith('{job="action_gateway"}')
