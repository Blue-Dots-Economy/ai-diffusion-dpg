# telephony_adapter/tests/test_vobiz_serializer.py
import base64
import json
import pytest
from src.vobiz_serializer import VobizFrameSerializer, VobizCallMetadata


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_parse_start_event_extracts_metadata():
    msg = json.dumps({
        "event": "start",
        "start": {
            "callSid": "CA123",
            "streamSid": "SS456",
            "customParameters": {"caller_id": "+911234567890"},
        },
        "streamSid": "SS456",
    })
    serializer = VobizFrameSerializer()
    metadata = serializer.parse_start(msg)
    assert metadata.call_sid == "CA123"
    assert metadata.stream_sid == "SS456"
    assert metadata.caller_id == "+911234567890"


def test_parse_start_missing_caller_id_defaults_to_unknown():
    msg = json.dumps({
        "event": "start",
        "start": {"callSid": "CA999", "streamSid": "SS999", "customParameters": {}},
        "streamSid": "SS999",
    })
    serializer = VobizFrameSerializer()
    metadata = serializer.parse_start(msg)
    assert metadata.caller_id == "unknown"


def test_parse_media_returns_audio_bytes():
    audio = b"\x00\x01\x02\x03"
    msg = json.dumps({
        "event": "media",
        "media": {"payload": _b64(audio), "track": "inbound"},
        "streamSid": "SS456",
    })
    serializer = VobizFrameSerializer()
    result = serializer.parse_media(msg)
    assert result == audio


def test_parse_media_invalid_payload_raises():
    msg = json.dumps({"event": "media", "media": {"payload": "!!!not_base64"}, "streamSid": "x"})
    serializer = VobizFrameSerializer()
    with pytest.raises(ValueError, match="Invalid base64"):
        serializer.parse_media(msg)


def test_build_media_message_encodes_audio():
    audio = b"\xaa\xbb\xcc"
    serializer = VobizFrameSerializer()
    msg = serializer.build_media_message("SS456", audio)
    parsed = json.loads(msg)
    assert parsed["event"] == "media"
    decoded = base64.b64decode(parsed["media"]["payload"])
    assert decoded == audio


def test_is_stop_event():
    serializer = VobizFrameSerializer()
    stop_msg = json.dumps({"event": "stop", "streamSid": "SS1"})
    media_msg = json.dumps({"event": "media", "media": {"payload": ""}})
    assert serializer.is_stop_event(stop_msg) is True
    assert serializer.is_stop_event(media_msg) is False


def test_is_stop_event_on_invalid_json_returns_false():
    serializer = VobizFrameSerializer()
    assert serializer.is_stop_event("not json") is False


def test_parse_start_invalid_json_raises():
    serializer = VobizFrameSerializer()
    with pytest.raises(ValueError, match="Invalid JSON"):
        serializer.parse_start("not valid json {{")
