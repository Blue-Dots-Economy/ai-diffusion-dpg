"""Recording reverse proxy between a target's action_gateway and the local Signals/search (plan ruling 1).

The target's action_gateway.yaml base URLs are patched to ``http://host.docker.internal:<port>``.
``/signals-search/*`` goes to search with the prefix stripped; everything else goes to Signals.
Bodies are recorded; headers are forwarded but never stored.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from eval.voice_bench.records import TapEntry

_HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}


def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


class Tap:
    """Threaded recording proxy.

    Args:
        port: Host port to listen on (0.0.0.0).
        signals_url: Upstream Signals base URL.
        search_url: Upstream signals-search base URL.
        transport: Optional httpx transport (tests).
    """

    def __init__(self, port: int, signals_url: str, search_url: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.port = port
        self._signals, self._search = signals_url.rstrip("/"), search_url.rstrip("/")
        self._client = httpx.Client(transport=transport, timeout=60.0)
        self._entries: list[TapEntry] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url_for_containers(self) -> str:
        return f"http://host.docker.internal:{self.port}"

    def _handler(self):
        tap = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):          # silence stdlib access log
                return

            def _do(self):
                t_ms = int(time.time() * 1000)
                path, _, query = self.path.partition("?")
                if path.startswith("/signals-search"):
                    base, up, path = tap._search, "search", path[len("/signals-search"):] or "/"
                else:
                    base, up = tap._signals, "signals"
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
                url = base + path + (f"?{query}" if query else "")
                try:
                    r = tap._client.request(self.command, url, content=body, headers=headers)
                    status, content, ctype = r.status_code, r.content, r.headers.get("content-type", "application/json")
                except httpx.HTTPError as e:
                    status, content, ctype = 502, json.dumps({"tap_error": type(e).__name__}).encode(), "application/json"
                with tap._lock:
                    tap._entries.append(TapEntry(t_ms=t_ms, method=self.command, path=path, query=query,
                                                 req_body=_parse(body), status=status, resp_body=_parse(content),
                                                 upstream=up))
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _do

        return H

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._client.close()

    def take(self) -> list[TapEntry]:
        """Remove and return every entry recorded so far (in arrival order).

        Draining everything (not a time window) means a request that lands between two turns is never lost:
        the driver attaches it to the next take(), or to the leg's last turn.
        """
        with self._lock:
            out, self._entries = self._entries, []
        return out

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
