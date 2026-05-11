"""Default system prompt for the realtime voice test.

The transcription language is set independently via `LANGUAGE` env var
(passed to OpenAI as `input_audio_transcription.language`). The model is
expected to mirror the user's input language naturally. If on the first
real call we observe drift to English, add a one-line "reply in the
user's language" instruction here.
"""
from __future__ import annotations


DEFAULT_PROMPT = (
    "You are a helpful voice assistant. "
    "Keep responses short and natural — this is a phone call."
)
