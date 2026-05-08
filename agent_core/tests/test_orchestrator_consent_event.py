"""Tests that Agent Core emits a 'consent' SSE event after recording consent."""
from __future__ import annotations

from src.orchestrator import emit_consent_event_if_recording


def test_emits_event_when_purpose_matches_and_granted():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="recording",
        granted=True,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert len(queue) == 1
    evt = queue[0]
    assert evt["type"] == "consent"
    assert evt["purpose"] == "recording"
    assert evt["granted"] is True
    assert evt["turn_id"] == "t-1"
    assert evt["consent_granted_ts"] > 0


def test_does_not_emit_for_other_purpose():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="data_share",
        granted=True,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert queue == []


def test_does_not_emit_when_not_granted():
    queue = []
    emit_consent_event_if_recording(
        queue=queue,
        purpose="recording",
        granted=False,
        configured_purpose="recording",
        turn_id="t-1",
    )
    assert queue == []
