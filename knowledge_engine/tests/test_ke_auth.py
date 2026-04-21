"""
knowledge_engine/tests/test_ke_auth.py

Tests for the API key verification helper.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.auth import verify_api_key


class TestVerifyApiKeyNormal:
    def test_matching_key_returns_none(self):
        result = verify_api_key("secret-key-123", "secret-key-123")
        assert result is None

    def test_any_valid_string_accepted(self):
        verify_api_key("abc", "abc")  # no exception


class TestVerifyApiKeyEdge:
    def test_empty_expected_key_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "")
        assert exc_info.value.status_code == 401

    def test_none_header_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, "expected")
        assert exc_info.value.status_code == 401


class TestVerifyApiKeyFailure:
    def test_wrong_key_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong-key", "right-key")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid API key"

    def test_empty_header_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("", "expected-key")
        assert exc_info.value.status_code == 401
