import json
import socket

import httpx

from eval.voice_bench.tap import Tap


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_tap_routes_records_and_strips_secrets():
    seen = []

    def upstream(req: httpx.Request):
        seen.append((str(req.url), req.headers.get("x-api-key")))
        return httpx.Response(200, json={"ok": True, "path": req.url.path})

    port = _free_port()
    tap = Tap(port, "http://signals.local:2742", "http://search.local:3100", transport=httpx.MockTransport(upstream))
    tap.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as c:
            r1 = c.post("/signals-search/v1/search", json={"q": "बिजली"}, headers={"x-api-key": "sk_secret"})
            r2 = c.get("/api/v1/admin/participant", params={"phone_number": "919900001000"},
                       headers={"x-api-key": "sk_secret"})
        assert r1.json()["path"] == "/v1/search" and r2.status_code == 200
        assert seen[0] == ("http://search.local:3100/v1/search", "sk_secret")
        assert seen[1][0].startswith("http://signals.local:2742/api/v1/admin/participant?phone_number=")
        entries = tap.take()
        assert [e.tool for e in entries] == ["fetch_jobs", "fetch_profile"]
        assert entries[0].req_body == {"q": "बिजली"} and entries[0].upstream == "search"
        assert "sk_secret" not in json.dumps([e.__dict__ for e in entries])
        assert tap.take() == []
    finally:
        tap.stop()


def test_tap_records_upstream_failure_as_502():
    def boom(req):
        raise httpx.ConnectError("down")

    port = _free_port()
    tap = Tap(port, "http://signals.local:2742", "http://search.local:3100", transport=httpx.MockTransport(boom))
    tap.start()
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/api/v1/action/perform", json={})
        assert r.status_code == 502
        (e,) = tap.take()
        assert e.status == 502 and e.tool == "apply_job"
    finally:
        tap.stop()
