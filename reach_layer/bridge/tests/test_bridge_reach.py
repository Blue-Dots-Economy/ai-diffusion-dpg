"""BridgeReachLayer lifecycle."""

from __future__ import annotations

import pytest

from reach_layer_base import TextChannelBase
from src.bridge_reach import BridgeReachLayer

CONFIG = {"agent_core_url": "http://agent-core-test:8000", "channel": "bridge"}


def test_is_a_text_channel():
    assert issubclass(BridgeReachLayer, TextChannelBase)


def test_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        BridgeReachLayer(config=None)


def test_exposes_the_channel_name():
    assert BridgeReachLayer(config=CONFIG).channel_name == "bridge"


async def test_run_loop_is_a_no_op():
    """Inbound requests arrive over HTTP, so there is no read loop to run."""
    assert await BridgeReachLayer(config=CONFIG).run_loop() is None
