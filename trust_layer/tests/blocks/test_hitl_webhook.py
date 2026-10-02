import hashlib
import hmac
import json

import httpx

from trust_layer.src.blocks.hitl import HiTLBlock
from trust_layer.src.blocks.hitl_webhook import deliver_webhook, sign, webhook_settings

SECRET = "s3cret"
BODY = json.dumps({"caller": {"name": "रमेश"}, "summary": [{"caller": "a\nb"}]}, ensure_ascii=False).encode()


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sign_is_hmac_sha256_of_raw_body():
    assert sign(BODY, SECRET) == "sha256=" + hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()


def test_2xx_is_delivered_and_signed():
    seen = {}

    def h(req):
        seen["sig"], seen["body"] = req.headers["X-Handoff-Signature"], req.content
        return httpx.Response(202)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (True, "delivered")
    assert seen["body"] == BODY and seen["sig"] == sign(BODY, SECRET)


def test_5xx_retries_once_then_fails():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "http_503")
    assert len(calls) == 2


def test_4xx_does_not_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "http_400")
    assert len(calls) == 1


def test_timeout_then_success_on_retry_is_delivered():
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200)

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (True, "delivered")


def test_timeout_twice_is_timeout():
    def h(req):
        raise httpx.ReadTimeout("slow")

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "timeout")


def test_settings_require_https_unless_allowed():
    assert webhook_settings({"HITL_WEBHOOK_URL": "http://x", "HITL_WEBHOOK_SECRET": "s"}) == (None, "s", False)
    assert webhook_settings({"HITL_WEBHOOK_URL": "http://x", "HITL_WEBHOOK_SECRET": "s",
                             "HITL_WEBHOOK_ALLOW_HTTP": "1"}) == ("http://x", "s", True)
    assert webhook_settings({}) == (None, None, False)


def test_block_webhook_misconfigured_without_env(monkeypatch):
    monkeypatch.delenv("HITL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HITL_WEBHOOK_SECRET", raising=False)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    r = HiTLBlock(cfg).escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is False and r["delivered"] is False and r["reason"] == "misconfigured"


def test_block_webhook_delivers(monkeypatch):
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://x/hook")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    blk = HiTLBlock(cfg, http_client=_client(lambda req: httpx.Response(200)))
    r = blk.escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is True and r["delivered"] is True and r["reason"] == "delivered"


def test_webhook_path_never_logs_payload_url_or_secret(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://hooks.example.test/abc123")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    blk = HiTLBlock(cfg, http_client=_client(lambda req: httpx.Response(503)))
    blk.escalate("s1", "human_request", "m", "job_match", handoff={"caller": {"phone": "919900001000"}})
    assert caplog.records
    for needle in ("919900001000", "hooks.example.test", "abc123", SECRET):
        assert all(needle not in str(r.__dict__) for r in caplog.records)


def test_invalid_url_does_not_raise():
    assert deliver_webhook(b"{}", url="https://[::1/x", secret="s") == (False, "error")


def test_connect_error_is_error_without_retry():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ConnectError("nope")

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "error")
    assert len(calls) == 1


def test_redirect_is_not_followed():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(302, headers={"Location": "https://evil/"})

    assert deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h)) == (False, "http_302")
    assert len(calls) == 1


def test_deadline_consumed_blocks_retry():
    ticks = iter([0.0, 5.0, 5.0, 5.0])
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    out = deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h),
                          clock=lambda: next(ticks))
    assert out == (False, "timeout") and len(calls) == 1


def test_retry_happens_when_time_remains():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    out = deliver_webhook(BODY, url="https://x/hook", secret=SECRET, client=_client(h), clock=lambda: 0.0)
    assert out == (False, "timeout") and len(calls) == 2


def test_block_malformed_url_no_raise_no_url_in_logs(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://[::1/secretpath")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    r = HiTLBlock(cfg).escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["delivered"] is False and r["reason"] in ("error", "misconfigured")
    assert all("secretpath" not in str(x.__dict__) for x in caplog.records)


def test_block_receiver_500_twice(monkeypatch):
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://x/hook")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    blk = HiTLBlock(cfg, http_client=_client(lambda req: httpx.Response(500)))
    r = blk.escalate("s1", "human_request", "m", "job_match", handoff={"x": 1})
    assert r["queued"] is True and r["delivered"] is False and r["reason"] == "http_500"


def test_ticket_id_cannot_be_overridden_by_handoff(monkeypatch):
    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://x/hook")
    monkeypatch.setenv("HITL_WEBHOOK_SECRET", SECRET)
    seen = {}

    def h(req):
        seen["b"] = json.loads(req.content)
        return httpx.Response(200)

    cfg = {"trust": {"hitl": {"queue_backend": "webhook", "holding_message": "h", "notification_webhook": None}}}
    r = HiTLBlock(cfg, http_client=_client(h)).escalate("s1", "r", "m", "w", handoff={"ticket_id": "FAKE"})
    assert seen["b"]["ticket_id"] == r["ticket_id"]
