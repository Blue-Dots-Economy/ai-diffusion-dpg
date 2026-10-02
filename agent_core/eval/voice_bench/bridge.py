"""Black-box client for the reach_layer bridge's OpenAI-compatible SSE endpoint (spec §6.4, plan rulings 3–4)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

_HANGUP_TOOL = {"type": "function", "function": {"name": "end_conversation", "description": "End the call",
                                                 "parameters": {"type": "object", "properties": {}}}}


@dataclass(frozen=True)
class BridgeTurn:
    reply: str
    status_phrase: str | None
    t_first_content_ms: int | None
    t_first_reply_ms: int | None
    t_total_ms: int | None
    session_ended: bool
    error: str | None


class BridgeClient:
    """One instance per target.

    Args:
        base_url: Bridge base URL (e.g. http://127.0.0.1:18008).
        status_phrases: Content chunks that are tool-status filler, not reply text.
        terminal_words: Final sentence that marks session end on targets without the hangup tool (M0).
        timeout_s: Per-turn read timeout.
        transport: Optional httpx transport (tests).
    """

    def __init__(self, base_url: str, status_phrases: list[str], terminal_words: list[str],
                 timeout_s: float = 60.0, transport: httpx.BaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._status = {p.strip() for p in status_phrases}
        self._terminal = {w.strip() for w in terminal_words}
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0), transport=transport)

    def health(self) -> bool:
        try:
            return self._client.get(f"{self._base}/health").status_code == 200
        except httpx.HTTPError:
            return False

    def turn(self, text: str, phone: str, call_id: str) -> BridgeTurn:
        """Send one caller line; read the stream to [DONE]. Never raises."""
        body = {"model": "blue-dots", "stream": True, "messages": [{"role": "user", "content": text}],
                "metadata": {"caller_phone": phone, "call_id": call_id}, "tools": [_HANGUP_TOOL]}
        t0 = time.perf_counter()
        ms = lambda: int((time.perf_counter() - t0) * 1000)  # noqa: E731
        parts: list[str] = []
        status = None
        t_content = t_reply = None
        hangup = done = False
        try:
            with self._client.stream("POST", f"{self._base}/v1/chat/completions", json=body) as r:
                if r.status_code != 200:
                    r.read()
                    return BridgeTurn("", None, None, None, ms(), False, f"http_{r.status_code}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    choices = json.loads(data).get("choices") or []
                    if not choices:
                        continue
                    ch = choices[0]
                    delta = ch.get("delta") or {}
                    if any((tc.get("function") or {}).get("name") == "end_conversation"
                           for tc in delta.get("tool_calls") or []) or ch.get("finish_reason") == "tool_calls":
                        hangup = True
                    content = delta.get("content") or ""
                    if not content.strip():
                        continue
                    if t_content is None:
                        t_content = ms()
                        if content.strip() in self._status and status is None:
                            status = content.strip()
                            continue
                    if t_reply is None:
                        t_reply = ms()
                    parts.append(content.strip())
        except (httpx.HTTPError, ValueError) as e:
            return BridgeTurn(" ".join(parts), status, t_content, t_reply, ms(), False, f"transport_{type(e).__name__}")
        reply = " ".join(parts)
        ended = hangup or (bool(parts) and parts[-1] in self._terminal)
        return BridgeTurn(reply, status, t_content, t_reply, ms(), ended, None if done else "stream_truncated")
