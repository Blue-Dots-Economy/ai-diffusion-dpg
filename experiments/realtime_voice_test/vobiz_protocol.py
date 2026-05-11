"""Pure I/O codec for Vobiz WebSocket frames (Plivo-compatible).

Vobiz uses JSON-encoded events over WebSocket. Inbound (Vobiz → us) carries
'start', 'media', 'mark', 'stop'. Outbound (us → Vobiz) typically uses
'playAudio' to send synthesized speech back to the caller. All audio
payloads are base64-encoded raw bytes; for telephony the contentType is
'audio/x-mulaw' at 8 kHz.

This module is pure data — no I/O, no network. Tested in isolation.
"""
from __future__ import annotations

import base64
import dataclasses
import json
from typing import Union


@dataclasses.dataclass
class StartFrame:
    """Vobiz 'start' event with the per-call stream identifiers."""
    stream_id: str
    call_id: str
    raw: dict


@dataclasses.dataclass
class MediaFrame:
    """Vobiz 'media' event carrying a chunk of caller audio."""
    audio_bytes: bytes
    track: str
    raw: dict


@dataclasses.dataclass
class StopFrame:
    """Vobiz 'stop' event signalling end of stream."""
    raw: dict


@dataclasses.dataclass
class UnknownFrame:
    """Any event we don't explicitly parse — logged then dropped."""
    event: str
    raw: dict


Frame = Union[StartFrame, MediaFrame, StopFrame, UnknownFrame]


def parse_frame(raw_text: str) -> Frame:
    """Parse a Vobiz WebSocket text frame into a typed Frame object.

    Args:
        raw_text: The string received over the WebSocket.

    Returns:
        A typed dataclass instance. Malformed JSON or unknown event types
        yield UnknownFrame with event='?' for malformed input.
    """
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return UnknownFrame(event="?", raw={"text": raw_text})

    event = data.get("event", "?")
    if event == "start":
        start = data.get("start", {}) or {}
        return StartFrame(
            stream_id=str(start.get("streamId", "")),
            call_id=str(start.get("callId", "")),
            raw=data,
        )
    if event == "media":
        media = data.get("media", {}) or {}
        payload_b64 = media.get("payload", "") or ""
        try:
            audio = base64.b64decode(payload_b64)
        except Exception:
            audio = b""
        return MediaFrame(
            audio_bytes=audio,
            track=str(media.get("track", "")),
            raw=data,
        )
    if event == "stop":
        return StopFrame(raw=data)
    return UnknownFrame(event=event, raw=data)


def build_play_audio(stream_id: str, audio_bytes: bytes) -> str:
    """Build a Vobiz 'playAudio' frame as a JSON string.

    Args:
        stream_id: The Vobiz stream identifier from the 'start' event.
        audio_bytes: Raw mu-law 8 kHz audio bytes to play back.

    Returns:
        A JSON string ready to send over the WebSocket.
    """
    payload_b64 = base64.b64encode(audio_bytes).decode("ascii")
    return json.dumps({
        "event": "playAudio",
        "streamId": stream_id,
        "media": {
            "contentType": "audio/x-mulaw",
            "sampleRate": 8000,
            "payload": payload_b64,
        },
    })
