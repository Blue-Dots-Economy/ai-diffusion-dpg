"""Tests for SileroVADWrapper — config-driven SileroVADAnalyzer factory."""
import pytest
from unittest.mock import patch, MagicMock
from src.vad.silero_vad import SileroVADWrapper
from src.vad.vad_base import VADAnalyzerBase


@pytest.fixture
def full_config():
    return {
        "telephony_adapter": {
            "vad": {
                "stop_secs": 0.5,
                "min_volume": 0.4,
                "confidence": 0.6,
                "start_secs": 0.2,
                "smoothing_factor": 0.2,
            }
        }
    }


@pytest.fixture
def empty_config():
    return {}


def test_create_analyzer_raises_on_none_config():
    wrapper = SileroVADWrapper()
    with pytest.raises(ValueError, match="None"):
        wrapper.create_analyzer(None)


def test_silero_vad_wrapper_is_vad_base():
    assert issubclass(SileroVADWrapper, VADAnalyzerBase)


def test_create_analyzer_returns_silero_instance(full_config):
    mock_analyzer = MagicMock()
    mock_params = MagicMock()

    with patch("src.vad.silero_vad.SileroVADAnalyzer", return_value=mock_analyzer) as mock_cls, \
         patch("src.vad.silero_vad.VADParams", return_value=mock_params) as mock_p:
        wrapper = SileroVADWrapper()
        result = wrapper.create_analyzer(full_config)

    mock_p.assert_called_once_with(
        stop_secs=0.5,
        min_volume=0.4,
        confidence=0.6,
        start_secs=0.2,
    )
    mock_cls.assert_called_once_with(params=mock_params)
    assert result is mock_analyzer
    assert result._smoothing_factor == 0.2


def test_create_analyzer_uses_defaults_when_config_missing(empty_config):
    mock_analyzer = MagicMock()
    mock_params = MagicMock()

    with patch("src.vad.silero_vad.SileroVADAnalyzer", return_value=mock_analyzer), \
         patch("src.vad.silero_vad.VADParams", return_value=mock_params) as mock_p:
        wrapper = SileroVADWrapper()
        result = wrapper.create_analyzer(empty_config)

    mock_p.assert_called_once_with(
        stop_secs=0.35,
        min_volume=0.3,
        confidence=0.4,
        start_secs=0.1,
    )
    assert result._smoothing_factor == 0.1
