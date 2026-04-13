"""Tests for STTServiceBase — DPG abstract STT interface."""
import pytest
from src.pipecat_services.stt_base import STTServiceBase


def test_cannot_instantiate_stt_base():
    with pytest.raises(TypeError):
        STTServiceBase()


def test_concrete_stt_must_implement_transcribe():
    class IncompleteSTT(STTServiceBase):
        pass

    with pytest.raises(TypeError):
        IncompleteSTT()


def test_concrete_stt_with_transcribe_instantiates():
    class MinimalSTT(STTServiceBase):
        async def transcribe(self, audio: bytes) -> str | None:
            return "hello"

    stt = MinimalSTT()
    assert stt is not None


@pytest.mark.asyncio
async def test_concrete_stt_transcribe_returns_none_for_empty():
    class SilentSTT(STTServiceBase):
        async def transcribe(self, audio: bytes) -> str | None:
            return None

    stt = SilentSTT()
    result = await stt.transcribe(b"")
    assert result is None
