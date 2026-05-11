"""Tests for vobiz_protocol.py: parsing inbound frames + building outbound playAudio."""
import base64
import json

import pytest

from vobiz_protocol import (
    parse_frame,
    build_play_audio,
    StartFrame,
    MediaFrame,
    StopFrame,
    UnknownFrame,
)


def test_parse_start_frame():
    """A start event yields a StartFrame with stream_id and call_id."""
    raw = json.dumps({
        "event": "start",
        "start": {
            "streamId": "stream-abc",
            "callId":   "call-xyz",
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
        },
    })
    frame = parse_frame(raw)
    assert isinstance(frame, StartFrame)
    assert frame.stream_id == "stream-abc"
    assert frame.call_id == "call-xyz"


def test_parse_media_frame():
    """A media event yields a MediaFrame with decoded audio bytes."""
    payload_bytes = b"\xff\xff\x00\x00"  # 4 bytes of fake audio
    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
    raw = json.dumps({
        "event": "media",
        "media": {
            "track":     "inbound",
            "payload":   payload_b64,
            "timestamp": "1234",
            "chunk":     "5",
        },
    })
    frame = parse_frame(raw)
    assert isinstance(frame, MediaFrame)
    assert frame.audio_bytes == payload_bytes
    assert frame.track == "inbound"


def test_parse_stop_frame():
    """A stop event yields a StopFrame."""
    raw = json.dumps({"event": "stop", "stop": {"callId": "call-xyz"}})
    frame = parse_frame(raw)
    assert isinstance(frame, StopFrame)


def test_parse_unknown_event_returns_unknown_frame():
    """An unknown event name yields UnknownFrame with the raw dict preserved."""
    raw = json.dumps({"event": "mark", "mark": {"name": "checkpoint-1"}})
    frame = parse_frame(raw)
    assert isinstance(frame, UnknownFrame)
    assert frame.event == "mark"
    assert frame.raw["mark"]["name"] == "checkpoint-1"


def test_parse_malformed_json_returns_unknown_frame():
    """Bad JSON doesn't crash — returns UnknownFrame with event='?'."""
    frame = parse_frame("not json {{")
    assert isinstance(frame, UnknownFrame)
    assert frame.event == "?"


def test_build_play_audio():
    """build_play_audio produces a JSON string Vobiz can play."""
    audio = b"\x00\x01\x02\x03"
    out = build_play_audio(stream_id="stream-abc", audio_bytes=audio)
    decoded = json.loads(out)
    assert decoded["event"] == "playAudio"
    assert decoded["streamId"] == "stream-abc"
    assert decoded["media"]["contentType"] == "audio/x-mulaw"
    assert decoded["media"]["sampleRate"] == 8000
    assert base64.b64decode(decoded["media"]["payload"]) == audio


def test_build_play_audio_empty():
    """Empty audio bytes produce a frame with empty base64 payload."""
    out = build_play_audio(stream_id="s", audio_bytes=b"")
    decoded = json.loads(out)
    assert decoded["media"]["payload"] == ""
