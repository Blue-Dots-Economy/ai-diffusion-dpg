"""
reach_layer/base/text_channel.py

TextChannelBase — abstract base for text-based channel adapters (CLI, Web).

Extends ReachLayerBase with a run_loop() method for channels that read input
from a text source (stdin, HTTP body, WebSocket) and render responses to a
text surface (stdout, JSON response).
"""

from __future__ import annotations

from abc import abstractmethod

from .reach_layer_base import ReachLayerBase


class TextChannelBase(ReachLayerBase):
    """Abstract base for text-based channels.

    Text channels read user input from a source (stdin, HTTP body) and
    render Agent Core responses to a text surface (stdout, JSON response).

    Subclasses must implement run_loop() and the lifecycle methods from
    ReachLayerBase.
    """

    @abstractmethod
    async def run_loop(self) -> None:
        """Read input from the text channel, submit to Agent Core, render events.

        Implementations read from their input source (stdin, HTTP body, WebSocket),
        call submit_input(), subscribe to events (if session mode), and render
        SentenceEvents to their output surface. Runs until channel signals end
        of input.
        """
