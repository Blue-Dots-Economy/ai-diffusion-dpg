"""Tests for web mode written by set_reach_channel_selection."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator


def _get_web_mode(acc: ConfigAccumulator) -> str:
    """Read the web mode from the accumulator's internal reach_layer config."""
    return (
        acc._data["reach_layer"]
        .get("reach_layer", {})
        .get("channels", {})
        .get("web", {})
        .get("mode", "NOT_SET")
    )


def test_voice_only_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["voice"])
    assert _get_web_mode(acc) == "routing_only"


def test_web_selected_sets_full_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web"])
    assert _get_web_mode(acc) == "full"


def test_web_and_voice_sets_full_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web", "voice"])
    assert _get_web_mode(acc) == "full"


def test_cli_only_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["cli"])
    assert _get_web_mode(acc) == "routing_only"


def test_empty_channels_sets_routing_only_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection([])
    assert _get_web_mode(acc) == "routing_only"


def test_channel_list_still_stored_correctly():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["voice", "cli"])
    assert acc.get_reach_channel_selection() == ["voice", "cli"]


def test_overwriting_selection_updates_mode():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web"])
    assert _get_web_mode(acc) == "full"
    acc.set_reach_channel_selection(["voice"])
    assert _get_web_mode(acc) == "routing_only"
