"""Smoke test: the package and its test dependencies import."""

from __future__ import annotations


def test_openai_sdk_available_for_contract_tests():
    """The openai package is a test-only dependency used to validate our output."""
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    assert ChatCompletion is not None
    assert ChatCompletionChunk is not None


def test_reach_layer_base_importable():
    """reach_layer_base is on the path via pyproject pythonpath."""
    from reach_layer_base import TextChannelBase

    assert TextChannelBase is not None
