"""The path evaluator is generic; the paths are the domain's business.

Every case below is written against a made-up shape on purpose. If a test
needed a real blue-dots key to pass, the evaluator would have domain knowledge
baked into it, which is the thing this module must not have.
"""

from __future__ import annotations

from src.adapters.response_path import resolve


DOC = {
    "top": "T",
    "nested": {"inner": {"leaf": 7}},
    "rows": [
        {"kind": "alpha", "state": "retired", "id": "id-1"},
        {"kind": "beta", "state": "live", "id": "id-2"},
        {"kind": "gamma", "state": "live", "id": "id-3"},
    ],
    "pairs": [{"k": "one", "v": True}, {"k": "two", "v": False}],
}


def test_plain_key():
    assert resolve(DOC, "top") == "T"


def test_nested_keys():
    assert resolve(DOC, "nested.inner.leaf") == 7


def test_index():
    assert resolve(DOC, "rows[0].id") == "id-1"


def test_filter_takes_the_first_match():
    assert resolve(DOC, "rows[state=live].id") == "id-2"


def test_filter_then_field():
    """The shape a key/value pair list needs: find the row, read its value."""
    assert resolve(DOC, "pairs[k=one].v") is True
    assert resolve(DOC, "pairs[k=two].v") is False


def test_comparison_is_on_the_string_form():
    """A connector path should not have to know how a backend spells booleans."""
    doc = {"flags": [{"name": "x", "on": True}]}
    assert resolve(doc, "flags[on=True].name") == "x"


def test_a_missing_key_resolves_to_none():
    assert resolve(DOC, "nope") is None
    assert resolve(DOC, "nested.nope.leaf") is None


def test_a_filter_matching_nothing_resolves_to_none():
    assert resolve(DOC, "rows[state=draft].id") is None


def test_an_out_of_range_index_resolves_to_none():
    assert resolve(DOC, "rows[99].id") is None


def test_indexing_a_non_list_resolves_to_none():
    assert resolve(DOC, "top[0]") is None


def test_an_empty_path_resolves_to_none():
    assert resolve(DOC, "") is None


def test_a_stale_path_never_raises():
    """A connector path that no longer matches must degrade, not break."""
    assert resolve({"rows": "not-a-list"}, "rows[state=live].id") is None
    assert resolve(None, "anything") is None


# ---------------------------------------------------------------------------
# End to end through the adapter
# ---------------------------------------------------------------------------


import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402

import httpx  # noqa: E402

from src.adapters.rest_api import RestApiAdapter  # noqa: E402


@pytest.mark.asyncio
async def test_declared_values_are_lifted_out_of_the_response():
    """Config declares the paths; the adapter only walks them."""
    config = {
        "id": "thing_fetch",
        "type": "rest_api",
        "base_url": "https://upstream.test",
        "endpoints": [{"name": "get", "method": "GET", "path": "/thing"}],
        "response": {
            "session_mapping": [
                {"source": "pairs[k=alpha].v", "target": "alpha_flag"},
                # `[k=v]` already yields the FIRST match, so no [0] is needed.
                {"source": "rows[state=live].id", "target": "live_id"},
                {"source": "owner", "target": "owner_id"},
                {"source": "rows[state=nothing].id", "target": "absent"},
            ],
        },
    }
    body = {
        "owner": "owner-9",
        "pairs": [{"k": "alpha", "v": True}],
        "rows": [{"state": "retired", "id": "r1"}, {"state": "live", "id": "r2"}],
    }
    resp = httpx.Response(200, json=body,
                          request=httpx.Request("GET", "https://upstream.test/thing"))
    with patch("httpx.AsyncClient.request", return_value=resp), \
         patch("httpx.Client.request", return_value=resp):
        res = await RestApiAdapter(config).execute("thing_fetch", {}, "s1", "u1")

    assert res.success is True
    assert res.session_values == {
        "alpha_flag": True,
        "live_id": "r2",      # the first LIVE row, not rows[0]
        "owner_id": "owner-9",
    }
    # a path that resolves to nothing is omitted, never written as None
    assert "absent" not in res.session_values


@pytest.mark.asyncio
async def test_no_mapping_declared_means_no_session_values():
    config = {
        "id": "plain",
        "type": "rest_api",
        "base_url": "https://upstream.test",
        "endpoints": [{"name": "get", "method": "GET", "path": "/x"}],
    }
    resp = httpx.Response(200, json={"a": 1},
                          request=httpx.Request("GET", "https://upstream.test/x"))
    with patch("httpx.AsyncClient.request", return_value=resp), \
         patch("httpx.Client.request", return_value=resp):
        res = await RestApiAdapter(config).execute("plain", {}, "s1", "u1")
    assert res.session_values == {}
