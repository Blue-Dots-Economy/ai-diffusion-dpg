"""
knowledge_engine/src/auth.py

Static API key verification for Knowledge Engine service-to-service calls.

Belongs to the Knowledge Engine block of the DPG framework.
Called by upload endpoints to authenticate requests from the Reach Layer.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def verify_api_key(header: Optional[str], expected: str) -> None:
    """Verify that the X-API-Key header matches the expected key.

    Args:
        header: Value of the X-API-Key header from the incoming request.
        expected: The expected API key read from env at startup.

    Raises:
        HTTPException: 401 if header is missing, empty, or does not match expected.
    """
    if not header or not expected or header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
