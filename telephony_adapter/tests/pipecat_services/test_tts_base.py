"""Tests for TTSServiceBase — DPG abstract TTS interface."""
import pytest
from src.pipecat_services.tts_base import TTSServiceBase


def test_cannot_instantiate_tts_base():
    with pytest.raises(TypeError):
        TTSServiceBase()


def test_concrete_tts_must_implement_synthesize():
    class IncompleteTTS(TTSServiceBase):
        pass

    with pytest.raises(TypeError):
        IncompleteTTS()


def test_concrete_tts_with_synthesize_instantiates():
    class MinimalTTS(TTSServiceBase):
        async def synthesize(self, text: str):
            yield b"\x00\x01"

    tts = MinimalTTS()
    assert tts is not None


@pytest.mark.asyncio
async def test_concrete_tts_synthesize_yields_bytes():
    class EchoTTS(TTSServiceBase):
        async def synthesize(self, text: str):
            yield b"\x00\x01"
            yield b"\x02\x03"

    tts = EchoTTS()
    chunks = [chunk async for chunk in tts.synthesize("hi")]
    assert chunks == [b"\x00\x01", b"\x02\x03"]
