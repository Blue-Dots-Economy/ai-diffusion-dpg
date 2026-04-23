"""
dev-kit/tests/test_accumulator_connector.py

Tests for the new public accumulator methods: set_reach_channel_selection
and set_agent_core_connector.
"""
from dev_kit.agent.accumulator import ConfigAccumulator


def test_set_reach_channel_selection():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web", "cli"])
    assert acc.get_reach_channel_selection() == ["web", "cli"]


def test_set_reach_channel_selection_replaces_previous():
    acc = ConfigAccumulator()
    acc.set_reach_channel_selection(["web"])
    acc.set_reach_channel_selection(["cli", "voice"])
    assert acc.get_reach_channel_selection() == ["cli", "voice"]


def test_set_agent_core_connector_adds_new():
    acc = ConfigAccumulator()
    acc.set_agent_core_connector("read", {"name": "tool_a", "description": "desc"})
    assert acc._data["agent_core"]["connectors"]["read"][0]["name"] == "tool_a"


def test_set_agent_core_connector_replaces_existing():
    acc = ConfigAccumulator()
    acc.set_agent_core_connector("read", {"name": "tool_a", "description": "v1"})
    acc.set_agent_core_connector("read", {"name": "tool_a", "description": "v2"})
    connectors = acc._data["agent_core"]["connectors"]["read"]
    assert len(connectors) == 1
    assert connectors[0]["description"] == "v2"


def test_set_agent_core_connector_multiple_categories():
    acc = ConfigAccumulator()
    acc.set_agent_core_connector("read", {"name": "tool_r"})
    acc.set_agent_core_connector("write", {"name": "tool_w"})
    assert len(acc._data["agent_core"]["connectors"]["read"]) == 1
    assert len(acc._data["agent_core"]["connectors"]["write"]) == 1
