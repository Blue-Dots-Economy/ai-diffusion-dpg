"""reach_layer/bridge/src/bridge_reach.py

BridgeReachLayer — the Reach Layer channel object for the bridge.

The bridge is request-driven: a client calls the HTTP endpoint and the server
handles each request. There is no input source to poll, so ``run_loop`` is a
no-op, as it is for the MCP channel.
"""

from __future__ import annotations

import logging

from reach_layer_base import TextChannelBase

logger = logging.getLogger(__name__)


class BridgeReachLayer(TextChannelBase):
    """OpenAI chat-completions channel backed by Agent Core."""

    def __init__(self, config: dict) -> None:
        """Initialise the channel.

        Args:
            config: Channel config. Must not be None.

        Raises:
            ValueError: If config is None.
        """
        if config is None:
            raise ValueError("config must not be None")
        super().__init__(config, channel_name=config.get("channel", "bridge"))
        logger.info("bridge.init",
                    extra={"operation": "bridge_reach.init", "status": "success"})

    async def run_loop(self) -> None:
        """No-op: inbound requests arrive over HTTP, not from a read loop."""
        return None

    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """No-op: each bridge request is a self-contained call, not a session.

        Args:
            session_id: Unique session identifier (unused).
            user_id: User identifier for this session (unused).
        """
        return None

    async def on_session_end(self, session_id: str) -> None:
        """No-op: each bridge request is a self-contained call, not a session.

        Args:
            session_id: Unique session identifier (unused).
        """
        return None
