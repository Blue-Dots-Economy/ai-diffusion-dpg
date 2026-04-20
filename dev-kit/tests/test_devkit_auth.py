"""
dev-kit/tests/test_devkit_auth.py

Tests for dev-kit static API key verification helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


class TestVerifyApiKeyNormal:
    def test_matching_key_does_not_raise(self):
        from dev_kit.agent.auth import verify_api_key
        result = verify_api_key("secret", "secret")
        assert result is None


class TestVerifyApiKeyFailure:
    def test_wrong_key_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong", "right")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid API key"

    def test_missing_header_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, "expected")
        assert exc_info.value.status_code == 401

    def test_empty_header_raises_401(self):
        from dev_kit.agent.auth import verify_api_key
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "expected")
        assert exc_info.value.status_code == 401
