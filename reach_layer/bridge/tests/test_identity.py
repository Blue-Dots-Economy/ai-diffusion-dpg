"""Caller identity extraction — spec 11.2.

The phone number is the job-seeker's identity in this domain: every Signals
record is keyed on it. A malformed value does not fail loudly downstream, it
silently matches nothing and writes a record no employer can ring, so this
module rejects rather than passes anything through.
"""

from __future__ import annotations

import pytest

from src.identity import IdentityError, extract_caller_phone


def test_extracts_phone_from_metadata():
    body = {"metadata": {"caller_phone": "919900112233"}}
    assert extract_caller_phone(body) == "919900112233"


def test_ignores_other_metadata_keys():
    body = {"metadata": {"caller_phone": "919900112233", "trace": "abc"}}
    assert extract_caller_phone(body) == "919900112233"


@pytest.mark.parametrize("body", [
    {},
    {"metadata": None},
    {"metadata": {}},
    {"metadata": {"caller_phone": ""}},
    {"metadata": {"caller_phone": "   "}},
])
def test_missing_phone_raises(body):
    with pytest.raises(IdentityError) as exc:
        extract_caller_phone(body)
    assert exc.value.param == "metadata.caller_phone"


@pytest.mark.parametrize("value", [
    "+919900112233",      # a "+" would render as "++91..." on the write
    "91 99001 12233",     # spaces are rejected by the upstream
    "91-99001-12233",
    "9900112233",         # no country code: silently matches nothing
    "abc",
    "666d8cbe-b297-4bda-981a-0f14a7ffe2a5",
])
def test_malformed_phone_raises(value):
    with pytest.raises(IdentityError) as exc:
        extract_caller_phone({"metadata": {"caller_phone": value}})
    assert exc.value.param == "metadata.caller_phone"


def test_non_string_phone_raises():
    with pytest.raises(IdentityError):
        extract_caller_phone({"metadata": {"caller_phone": 919900112233}})
