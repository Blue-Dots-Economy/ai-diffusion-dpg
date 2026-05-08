"""Tests for ConsentEvent SSE parsing."""
from __future__ import annotations

import json

from reach_layer_base import ConsentEvent
from reach_layer_base.reach_layer_base import ReachLayerBase


def _parse(payload: dict) -> object | None:
    return ReachLayerBase._parse_sse_event(f"data: {json.dumps(payload)}")


def test_consent_event_parsed_with_required_fields():
    evt = _parse({
        "type": "consent",
        "purpose": "recording",
        "granted": True,
        "consent_granted_ts": 1746748800.123,
        "turn_id": "t-1",
    })
    assert isinstance(evt, ConsentEvent)
    assert evt.purpose == "recording"
    assert evt.granted is True
    assert evt.consent_granted_ts == 1746748800.123
    assert evt.turn_id == "t-1"


def test_consent_event_defaults_when_optional_fields_missing():
    evt = _parse({"type": "consent", "purpose": "recording", "granted": False})
    assert isinstance(evt, ConsentEvent)
    assert evt.granted is False
    assert evt.consent_granted_ts == 0.0
    assert evt.turn_id == ""


def test_unknown_event_type_returns_none():
    assert _parse({"type": "mystery", "purpose": "x"}) is None
