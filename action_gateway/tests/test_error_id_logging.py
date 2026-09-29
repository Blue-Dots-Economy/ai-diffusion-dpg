"""A rejected call must name the ids it was rejected for.

A 422 ``TARGET_ITEM_NOT_FOUND`` from Signals says only that the target is not
an item on this instance. The caller's profile id, a service provider's id and
an invented-but-well-formed UUID all produce exactly that error, so without the
argument in the log the failure cannot be attributed to any of them.
"""

from __future__ import annotations

import logging
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
        "path": "/api/v1/action/perform",
        "params": [
            {"name": "profile_item_id", "source": "agent", "type": "string"},
            {"name": "job_item_id", "source": "agent", "type": "string"},
            {"name": "name", "source": "agent", "type": "string"},
        ],
    }],
}

PROFILE = "ec792304-c2c6-4eb3-8237-6c8aa01960ed"
JOB = "0913fe07-ea54-4d3e-80ce-46b44ac2031a"


async def _run(caplog, params):
    adapter = RestApiAdapter(CONFIG)
    resp = httpx.Response(
        422,
        json={"results": [{"status": "error", "error": "TARGET_ITEM_NOT_FOUND"}]},
        request=httpx.Request("POST", "https://upstream.test/api/v1/action/perform"),
    )
    with patch("httpx.AsyncClient.request", return_value=resp), \
         patch("httpx.Client.request", return_value=resp), \
         caplog.at_level(logging.WARNING):
        await adapter.execute("apply_job", params, "s1", "u1")
    return [r for r in caplog.records if r.message == "rest_api_http_error"]


@pytest.mark.asyncio
async def test_uuid_arguments_are_logged_on_a_rejected_call(caplog):
    rec = await _run(caplog, {"profile_item_id": PROFILE, "job_item_id": JOB})
    assert rec, "the failure should have been logged"
    ids = getattr(rec[0], "id_params", {})
    assert ids.get("job_item_id") == JOB
    assert ids.get("profile_item_id") == PROFILE


@pytest.mark.asyncio
async def test_non_uuid_arguments_are_never_logged(caplog):
    """Names and phones identify a person; ids identify a row."""
    rec = await _run(caplog, {"job_item_id": JOB, "name": "सुनील राव"})
    ids = getattr(rec[0], "id_params", {})
    assert "name" not in ids
    assert ids == {"job_item_id": JOB}


@pytest.mark.asyncio
async def test_an_ordinal_sent_as_a_job_id_is_not_mistaken_for_one(caplog):
    """The 400 case: 'तीसरा' is not a UUID and must not appear as an id."""
    rec = await _run(caplog, {"job_item_id": "तीसरा"})
    assert getattr(rec[0], "id_params", {}) == {}
