"""A minimal in-process stand-in for ``aiohttp.ClientSession``.

Replaces ``aioresponses`` in these tests. aioresponses builds
``aiohttp.ClientResponse`` objects itself, and aiohttp 3.14.0 made
``ClientResponse.__init__`` require a new ``stream_writer`` keyword, so every
test that used it died with ``TypeError`` inside aioresponses before reaching
our code. aioresponses 0.7.9 is its latest release, so there is no version to
bump to.

This fakes one layer higher, at the session, and only what vobiz_source.py
actually calls: ``ClientSession(timeout=...)`` as an async context manager,
``post``/``get``/``delete`` as async context managers, and on the response
``status``, ``json()``, ``read()`` and ``raise_for_status()``. It therefore
never constructs an aiohttp internal and is not exposed to that kind of
signature change again.

What it deliberately does NOT exercise is aiohttp's own request building and
transport. That was already out of reach: the source hardcodes
``https://api.vobiz.ai`` inline, so a real local server cannot stand in for it.

The registration API mirrors the subset of aioresponses the tests used, so the
test bodies change by one import and one name::

    with fake_http() as m:
        m.post(url, status=202, payload={...})
        m.get(url, body=b"bytes", status=200)
        m.get(url, status=200, payload={...}, repeat=True)
"""
from __future__ import annotations

import json as _json
from contextlib import contextmanager
from typing import Any, Iterator
from unittest import mock

import aiohttp
from yarl import URL


def _key(method: str, url: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    """Normalise a request to a comparable key; query order does not matter."""
    u = URL(url)
    return (method.upper(), u.host or "", u.path, tuple(sorted(u.query.items())))


class _Response:
    def __init__(
        self, method: str, url: str, status: int, body: bytes, content_type: str
    ) -> None:
        self.method, self.url, self.status, self._body = method, url, status, body
        self.content_type = content_type

    async def json(self, **_: Any) -> Any:
        return _json.loads(self._body)

    async def read(self) -> bytes:
        return self._body

    def raise_for_status(self) -> None:
        if self.status >= 400:
            u = URL(self.url)
            info = aiohttp.RequestInfo(
                url=u, method=self.method, headers={}, real_url=u
            )
            raise aiohttp.ClientResponseError(
                info, (), status=self.status, message=f"HTTP {self.status}"
            )


class _Registry:
    def __init__(self) -> None:
        # key -> list of (status, body, content_type, repeat); consumed
        # front-to-back.
        self._routes: dict[Any, list[tuple[int, bytes, str, bool]]] = {}
        self.calls: list[tuple[str, str]] = []

    def _add(
        self,
        method: str,
        url: str,
        *,
        status: int = 200,
        payload: Any = None,
        body: bytes | str | None = None,
        repeat: bool = False,
    ) -> None:
        # vobiz_source.py branches on resp.content_type to decide whether to
        # parse a body as JSON, so it has to be right: JSON for a registered
        # payload, binary for a raw body (the MP3 download).
        if body is not None:
            raw = body.encode() if isinstance(body, str) else body
            ctype = "application/octet-stream"
        elif payload is not None:
            raw = _json.dumps(payload).encode()
            ctype = "application/json"
        else:
            raw = b""
            ctype = "application/json"
        self._routes.setdefault(_key(method, url), []).append(
            (status, raw, ctype, repeat)
        )

    def post(self, url: str, **kw: Any) -> None:
        self._add("POST", url, **kw)

    def get(self, url: str, **kw: Any) -> None:
        self._add("GET", url, **kw)

    def delete(self, url: str, **kw: Any) -> None:
        self._add("DELETE", url, **kw)

    def _respond(self, method: str, url: str) -> _Response:
        self.calls.append((method.upper(), url))
        queue = self._routes.get(_key(method, url))
        if not queue:
            # Fail loudly, as aioresponses does: an unregistered call is a test
            # bug, and silently returning an empty 200 would hide it.
            raise aiohttp.ClientConnectionError(
                f"fake_http: no response registered for {method.upper()} {url}"
            )
        status, raw, ctype, repeat = queue[0]
        if not repeat:
            queue.pop(0)
        return _Response(method.upper(), url, status, raw, ctype)


class _RequestCtx:
    def __init__(self, registry: _Registry, method: str, url: str) -> None:
        self._registry, self._method, self._url = registry, method, url

    async def __aenter__(self) -> _Response:
        return self._registry._respond(self._method, self._url)

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _session_class(registry: _Registry) -> type:
    class _Session:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, **_: Any) -> _RequestCtx:
            return _RequestCtx(registry, "POST", url)

        def get(self, url: str, **_: Any) -> _RequestCtx:
            return _RequestCtx(registry, "GET", url)

        def delete(self, url: str, **_: Any) -> _RequestCtx:
            return _RequestCtx(registry, "DELETE", url)

    return _Session


@contextmanager
def fake_http() -> Iterator[_Registry]:
    """Patch ``aiohttp.ClientSession`` for the duration of the block."""
    registry = _Registry()
    with mock.patch.object(aiohttp, "ClientSession", _session_class(registry)):
        yield registry
