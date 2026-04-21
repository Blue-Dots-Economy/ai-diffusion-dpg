"""
dev-kit/dev_kit/agent/auth.py

Static API key verification for dev-kit service-to-service calls.

Belongs to the Dev-Kit tool of the DPG framework.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def verify_api_key(header: Optional[str], expected: str) -> None:
    """Verify that the X-API-Key header matches the expected static key.

    Args:
        header: Value of the X-API-Key header from the incoming request.
        expected: Expected API key read from env at startup.

    Raises:
        HTTPException: 401 if header is missing, empty, or does not match expected.
    """
    if not header or not expected or header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
