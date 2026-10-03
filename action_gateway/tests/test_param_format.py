"""A declared value shape is enforced before the upstream call.

The adapter knows what a uuid looks like; it never knows which parameter
carries one. That is the domain's config, exactly as the projection's paths
are. Enforcing here turns a bare upstream 400 — which the model cannot act on —
into a message naming the parameter and what was wrong with it.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from src.adapters.rest_api import RestApiAdapter


CONFIG = {
    "id": "apply_job",
    "type": "rest_api",
    "base_url": "https://upstream.test",
    "endpoints": [{
        "name": "apply",
        "method": "POST",
        "path": "/apply",
        "params": [
            {"name": "job_item_id", "source": "agent", "type": "string",
             "format": "uuid"},
            {"name": "note", "source": "agent", "type": "string"},
        ],
    }],
}

GOOD = "0913fe07-ea54-4d3e-80ce-46b44ac2031a"


async def _call(params):
    adapter = RestApiAdapter(CONFIG)
    ok = httpx.Response(200, json={"ok": True},
                        request=httpx.Request("POST", "https://upstream.test/apply"))
    with patch("httpx.AsyncClient.request", return_value=ok) as sent, \
         patch("httpx.Client.request", return_value=ok):
        res = await adapter.execute("apply_job", params, "s1", "u1")
    return res, sent


@pytest.mark.asyncio
async def test_an_ordinal_is_rejected_before_the_call():
    """'तीसरा' is what the caller said, not the id — the live 400 case."""
    res, sent = await _call({"job_item_id": "तीसरा"})
    assert res.success is False
    assert res.error == "param_format"
    assert "job_item_id" in res.result_text
    sent.assert_not_called()


@pytest.mark.asyncio
async def test_the_message_tells_the_model_what_to_do():
    res, _ = await _call({"job_item_id": "the first one"})
    assert "uuid" in res.result_text
    assert "tool result" in res.result_text


@pytest.mark.asyncio
async def test_a_real_uuid_passes_through():
    res, sent = await _call({"job_item_id": GOOD})
    assert res.success is True
    sent.assert_called_once()


@pytest.mark.asyncio
async def test_params_without_a_declared_format_are_untouched():
    """Only declared shapes are checked; everything else is the domain's business."""
    res, sent = await _call({"job_item_id": GOOD, "note": "anything at all"})
    assert res.success is True
    sent.assert_called_once()
