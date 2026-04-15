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
    logger.info(
        "bot.run_bot_start",
        extra={
            "operation": "bot.run_bot",
            "status": "success",
            "call_sid": call_sid,
            "caller_id": caller_id,
        },
    )
    adapter = VobizAdapter(config)
    try:
        await adapter.handle_call(call_sid, caller_id, websocket)
    except Exception as exc:
        logger.error(
            "bot.run_bot_error",
            extra={
                "operation": "bot.run_bot",
                "status": "failure",
                "call_sid": call_sid,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise
    finally:
        logger.info(
            "bot.run_bot_end",
            extra={
                "operation": "bot.run_bot",
                "status": "success",
                "call_sid": call_sid,
            },
        )
        await adapter.teardown(call_sid)
