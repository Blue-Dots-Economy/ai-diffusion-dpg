"""Default system prompt for the realtime voice test.

Real-call testing confirmed OpenAI Realtime's mirroring behaviour is
unreliable — short or accented openings can land the model on Urdu /
Punjabi / English / etc. instead of Hindi, even with
`input_audio_transcription.language="hi"` set (that field only governs
STT, not output language). To pin the bot's reply language we have to
say so explicitly in the system prompt.
"""
from __future__ import annotations


DEFAULT_PROMPT = (
    "You are a helpful voice assistant. "
    "Keep responses short and natural — this is a phone call. "
    "Always reply in Hindi (Devanagari script). "
    "If the user speaks English or any other language, still reply in Hindi."
)
