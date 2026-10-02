"""HiTL webhook delivery: HTTPS POST of a signed handoff payload (spec §5.3)."""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping

import httpx

TIMEOUT_S = 3.0

# httpx logs every request URL at INFO (httpcore logs the host at DEBUG); the webhook URL is a
# secret-bearing value, so keep these loggers quiet.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the exact request body, as sent in X-Handoff-Signature."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def webhook_settings(environ: Mapping[str, str]) -> tuple[str | None, str | None, bool]:
    """(url, secret, allow_http) from env; url is None when missing or not HTTPS (unless allowed)."""
    allow_http = environ.get("HITL_WEBHOOK_ALLOW_HTTP") == "1"
    url = (environ.get("HITL_WEBHOOK_URL") or "").strip() or None
    if url and not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
        url = None
    secret = environ.get("HITL_WEBHOOK_SECRET") or None
    return url, secret, allow_http


def deliver_webhook(body: bytes, *, url: str, secret: str, timeout_s: float = TIMEOUT_S,
                    client: httpx.Client | None = None) -> tuple[bool, str]:
    """POST once, retry once on timeout/5xx. Returns (delivered, reason). Never raises."""
    headers = {"Content-Type": "application/json; charset=utf-8", "X-Handoff-Signature": sign(body, secret)}
    own = client is None
    cl = client or httpx.Client(timeout=timeout_s)
    reason = "error"
    try:
        for _attempt in range(2):
            try:
                r = cl.post(url, content=body, headers=headers, timeout=timeout_s)
            except httpx.TimeoutException:
                reason = "timeout"
                continue
            except httpx.HTTPError:
                return False, "error"
            if 200 <= r.status_code < 300:
                return True, "delivered"
            reason = f"http_{r.status_code}"
            if r.status_code < 500:
                return False, reason
        return False, reason
    finally:
        if own:
            cl.close()
