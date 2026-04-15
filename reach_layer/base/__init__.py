"""
reach_layer/base — shared base class hierarchy for all Reach Layer channels.

Exports:
    ReachLayerBase   — async ABC with concrete Agent Core HTTP methods
    TextChannelBase  — text-based channels (CLI, Web)
    VoiceChannelBase — voice/telephony channels
    VADEvent         — Voice Activity Detection event
    SignalEvent, SentenceEvent, DoneEvent, StreamEvent — SSE event types
"""

from .events import DoneEvent, SentenceEvent, SignalEvent, StreamEvent
from .reach_layer_base import ReachLayerBase
from .text_channel import TextChannelBase
from .voice_channel import VADEvent, VoiceChannelBase

__all__ = [
    "ReachLayerBase",
    "TextChannelBase",
    "VoiceChannelBase",
    "VADEvent",
    "SignalEvent",
    "SentenceEvent",
    "DoneEvent",
    "StreamEvent",
]
