"""
telephony_adapter/src/bot.py

run_bot — per-call entry point for the Telephony Adapter.

Delegates the full call lifecycle to VobizAdapter. Called once per inbound
WebSocket connection from server.py.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket

from src.vobiz_adapter import VobizAdapter

logger = logging.getLogger(__name__)


async def run_bot(websocket: WebSocket, call_sid: str, caller_id: str, config: dict) -> None:
    """Build and run the VobizAdapter pipeline for one inbound call.

    Args:
        websocket: FastAPI WebSocket that has already been accepted by server.py.
        call_sid: Vobiz CallUUID from the URL path.
        caller_id: Caller E.164 phone number from the /answer webhook From field.
        config: Full merged config dict.
    """
    adapter = VobizAdapter(config)
    try:
        await adapter.handle_call(call_sid, caller_id, websocket)
    finally:
        await adapter.teardown(call_sid)
