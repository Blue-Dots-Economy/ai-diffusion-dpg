"""
trust_layer/tests/test_basic_escalate.py

Regression: the deployed entrypoint (main.py) wires BasicTrustLayer into create_app,
so BasicTrustLayer must expose escalate() or POST /escalate returns reason="error".
"""

import hmac
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from server import create_app
from src.guardrails import BasicTrustLayer

SECRET = "live-fix-secret"


def _cfg(backend="log"):
    return {"trust": {"hitl": {"queue_backend": backend, "holding_message": "hold", "notification_webhook": None}}}


def test_basic_trust_layer_escalate_log_backend():
    r = BasicTrustLayer(_cfg("log")).escalate("s1", "human_request", "msg", "job_match", {"a": 1})
    assert r["queued"] is True and r["delivered"] is False and r["reason"] == "log_only"
    assert r["ticket_id"].startswith("TKT-")


def test_post_escalate_through_real_wiring_delivers_signed_webhook(monkeypatch):
    received = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((body, self.headers.get("X-Handoff-Signature")))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Hook)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setenv("HITL_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}/hook")
        monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
        monkeypatch.setenv("HITL_WEBHOOK_ALLOW_HTTP", "1")
        client = TestClient(create_app(BasicTrustLayer(_cfg("webhook"))))
        resp = client.post("/escalate", json={
            "session_id": "s1", "escalation_reason": "human_request",
            "user_message": "talk to a person", "workflow_step": "job_match",
            "handoff": {"caller": {"name": "x"}},
        })
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status_code == 200
    data = resp.json()
    assert data["delivered"] is True, data
    assert len(received) == 1
    body, sig = received[0]
    json.loads(body)
    assert sig == "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
