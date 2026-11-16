"""Per-turn evidence read from the target's own Loki, through Grafana's datasource proxy.

A deployed target records what a local one would have exposed through the tap and
``docker logs``: ``action_gateway`` logs every tool call and ``agent_core`` logs the
turn banner, both carrying ``session_id`` = ``<phone>:<call_id>``. That key is what
makes a remote read exact rather than a guess over a time window.

Session memory has no such path — the target's Redis is not published — so
``sees_session`` is False and the checks that need it report unscored.
"""
from __future__ import annotations

import json
import logging
import os
import time
from urllib.parse import urlsplit

import httpx

from eval.voice_bench.observe import Observer, TurnObservation, parse_banner
from eval.voice_bench.records import TapEntry

logger = logging.getLogger(__name__)

TOKEN_ENV = "VOICE_BENCH_GRAFANA_TOKEN"

_REQUEST_OP = "rest_api_request"
_RESPONSE_OP = "rest_api_response"
_EXECUTE_OP = "rest_api_execute"
_TOOL_BODIES = (_REQUEST_OP, _RESPONSE_OP, _EXECUTE_OP)


class LokiError(RuntimeError):
    """A Loki read failed. Never carries the token."""


class LokiClient:
    """Range queries against one Loki datasource, proxied by Grafana.

    Args:
        grafana_url: Base URL of Grafana, e.g. ``http://10.0.0.1:3001``.
        datasource_uid: Provisioned Loki datasource uid.
        token: Grafana service-account token; read from the environment when unset.
        http: httpx.Client (injectable).
        limit: Max log lines per query.

    Raises:
        LokiError: No token available.
    """

    def __init__(self, grafana_url: str, datasource_uid: str = "dpg-loki", token: str | None = None,
                 http: httpx.Client | None = None, limit: int = 2000) -> None:
        self._base = grafana_url.rstrip("/")
        self._uid = datasource_uid
        self._token = (token or os.environ.get(TOKEN_ENV) or "").strip()
        if not self._token:
            raise LokiError(f"a remote target needs a Grafana service-account token in {TOKEN_ENV}. "
                            'Load it with `eval "$(~/.config/kkb/bin/kkb-secrets.sh export)"`.')
        self._http = http or httpx.Client(timeout=30)
        self._limit = limit

    def lines(self, query: str, start_s: float, end_s: float) -> list[tuple[int, str]]:
        """Log lines matching a LogQL query, oldest first, as (epoch_ms, line).

        Raises:
            LokiError: The request failed, or Grafana refused the credentials.
        """
        url = f"{self._base}/api/datasources/proxy/uid/{self._uid}/loki/api/v1/query_range"
        params = {"query": query, "limit": self._limit, "direction": "forward",
                  "start": f"{int(start_s * 1e9)}", "end": f"{int(end_s * 1e9)}"}
        last: Exception | None = None
        for attempt in range(2):
            try:
                r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self._token}"})
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise LokiError(f"Grafana refused the token (HTTP {r.status_code}) at {self._base}. "
                                "The service account needs the Viewer role on this instance.")
            if r.status_code >= 400:
                last = LokiError(f"Loki query failed (HTTP {r.status_code}) at {self._base}")
                time.sleep(0.5 * (attempt + 1))
                continue
            out: list[tuple[int, str]] = []
            for stream in ((r.json().get("data") or {}).get("result") or []):
                for ts_ns, line in stream.get("values") or []:
                    out.append((int(ts_ns) // 1_000_000, line))
            out.sort(key=lambda v: v[0])
            return out
        raise LokiError(f"Loki query failed at {self._base}: {type(last).__name__}: {last}")


def _record(line: str) -> dict:
    """The OTLP log record carried in a Loki line; {} when the line is not JSON."""
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _entry(group: dict) -> TapEntry:
    """One tool call assembled from its request / response / execute log records."""
    req, resp, ex = group.get(_REQUEST_OP, {}), group.get(_RESPONSE_OP, {}), group.get(_EXECUTE_OP, {})
    url = req.get("url") or ""
    split = urlsplit(url)
    status = resp.get("status_code")
    if status is None:
        status = 200 if ex.get("status") == "success" else 0
    return TapEntry(t_ms=group["t_ms"], method=str(req.get("method") or "GET"), path=split.path,
                    query=split.query, req_body=req.get("params"), status=int(status),
                    resp_body=resp.get("body"),
                    upstream="search" if "/signals-search" in url else "signals",
                    tool_name=str(ex.get("tool_name") or resp.get("tool_name") or req.get("tool_name") or "other"))


def tool_entries(lines: list[tuple[int, str]], session_id: str) -> dict[str, TapEntry]:
    """Tool calls for one session, keyed by the trace id that ties their log records together."""
    groups: dict[str, dict] = {}
    for t_ms, line in lines:
        rec = _record(line)
        attrs = rec.get("attributes") or {}
        if rec.get("body") not in _TOOL_BODIES or attrs.get("session_id") != session_id:
            continue
        key = str(rec.get("traceid") or f"t{t_ms}")
        group = groups.setdefault(key, {"t_ms": t_ms})
        group[rec["body"]] = attrs
    return {k: _entry(g) for k, g in groups.items()}


class LokiObserver(Observer):
    """Per-turn evidence for a target whose logs reach Loki.

    Args:
        client: Loki reader.
        action_gateway_job: ``job`` label of the action_gateway stream.
        agent_job: ``job`` label of the agent_core stream.
        settle_s: How long to keep re-reading before accepting that nothing more will arrive.
        sleep: Poll sleep (injectable).
        clock: Monotonic clock (injectable).
    """

    def __init__(self, client: LokiClient, action_gateway_job: str = "action_gateway",
                 agent_job: str = "agent_core", settle_s: float = 12.0,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self._client = client
        self._ag_job = action_gateway_job
        self._agent_job = agent_job
        self._settle_s = settle_s
        self._sleep, self._clock = sleep, clock
        self._seen: set[str] = set()
        self._tool_calls = 0
        self._with_body = 0

    @staticmethod
    def _session_id(phone: str, call_id: str) -> str:
        return f"{phone}:{call_id}"

    def _read(self, job: str, session_id: str, start_s: float) -> list[tuple[int, str]]:
        query = '{job="%s"} |= `%s`' % (job, session_id)
        return self._client.lines(query, start_s, time.time() + 1)

    def turn(self, phone: str, call_id: str, since_s: int) -> TurnObservation:
        """Tool calls and the turn banner for the turn that has just completed.

        Waits for the agent's end-of-turn banner before reading, so a slow log
        export cannot be mistaken for a turn that called no tools.
        """
        session_id = self._session_id(phone, call_id)
        banner: dict = {}
        deadline = self._clock() + self._settle_s
        while True:
            banner = parse_banner("\n".join(ln for _t, ln in self._read(self._agent_job, session_id, since_s)))
            if banner or self._clock() >= deadline:
                break
            self._sleep(1.0)
        if not banner:
            logger.warning("voice_bench.loki_banner_missing",
                           extra={"operation": "LokiObserver.turn", "status": "failure",
                                  "session_id": session_id})
        return TurnObservation(tap=self._new_entries(session_id, since_s), session={}, banner=banner)

    def _new_entries(self, session_id: str, start_s: float) -> list[TapEntry]:
        lines = self._read(self._ag_job, session_id, start_s)
        found = tool_entries(lines, session_id)
        fresh = [e for k, e in sorted(found.items(), key=lambda kv: kv[1].t_ms) if k not in self._seen]
        new_keys = [k for k in found if k not in self._seen]
        self._tool_calls += len(new_keys)
        self._with_body += sum(1 for e in fresh if e.resp_body is not None)
        self._seen.update(found)
        return fresh

    def leftover(self, phone: str, call_id: str) -> list[TapEntry]:
        """Tool calls that reached Loki after the last turn's stream closed."""
        self._sleep(min(self._settle_s, 6.0))
        return self._new_entries(self._session_id(phone, call_id), time.time() - 600)

    def start_attempt(self) -> None:
        self._seen.clear()

    @property
    def sees_session(self) -> bool:
        return False

    @property
    def sees_tool_bodies(self) -> bool:
        """True until a tool call is seen whose response body was never logged.

        The agent records request parameters and response bodies only at its most
        detailed level, so on a deployment left at the default these are absent and
        the checks that read them must say so rather than grade on what is left.
        """
        return self._with_body > 0 or self._tool_calls == 0
