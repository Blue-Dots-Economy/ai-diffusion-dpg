"""Tests for VADAnalyzerBase — DPG abstract VAD interface."""
import pytest
from src.vad.vad_base import VADAnalyzerBase


def test_cannot_instantiate_vad_base():
    with pytest.raises(TypeError):
        VADAnalyzerBase()


def test_concrete_vad_must_implement_create_analyzer():
    class IncompleteVAD(VADAnalyzerBase):
        pass

    with pytest.raises(TypeError):
        IncompleteVAD()


def test_concrete_vad_with_create_analyzer_instantiates():
    class MinimalVAD(VADAnalyzerBase):
        def create_analyzer(self, config: dict):
            return object()

    vad = MinimalVAD()
    assert vad is not None
