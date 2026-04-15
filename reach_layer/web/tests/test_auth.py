"""Unit tests for src.auth."""

from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from src.auth import (
    AuthError,
    GoogleIdentity,
    Reason,
    SessionClaims,
    issue_session_token,
    verify_google_id_token,
    verify_session_token,
)


CLIENT_ID = "test-client-id.apps.googleusercontent.com"
SECRET = "x" * 48


# ---------------------------------------------------------------------------
# verify_google_id_token
# ---------------------------------------------------------------------------


def _valid_claims(**overrides):
    base = {
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice",
        "picture": "https://example.com/a.png",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 600,
    }
    base.update(overrides)
    return base


def test_verify_google_id_token_success():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        return_value=_valid_claims(),
    ):
        identity = verify_google_id_token("credential", CLIENT_ID)

    assert isinstance(identity, GoogleIdentity)
    assert identity.sub == "1234567890"
    assert identity.email == "alice@example.com"
    assert identity.name == "Alice"
    assert identity.user_id == "google:1234567890"


def test_verify_google_id_token_missing_credential():
    with pytest.raises(AuthError) as exc:
        verify_google_id_token("", CLIENT_ID)
    assert exc.value.reason is Reason.MISSING


def test_verify_google_id_token_missing_client_id():
    with pytest.raises(AuthError) as exc:
        verify_google_id_token("credential", "")
    assert exc.value.reason is Reason.INVALID


def test_verify_google_id_token_expired():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        side_effect=ValueError("Token expired, 12345"),
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.EXPIRED


def test_verify_google_id_token_wrong_audience():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        side_effect=ValueError("Could not verify token audience"),
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.AUDIENCE


def test_verify_google_id_token_malformed():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        side_effect=ValueError("Wrong number of segments"),
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.INVALID


def test_verify_google_id_token_unverified_email():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        return_value=_valid_claims(email_verified=False),
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.UNVERIFIED_EMAIL


def test_verify_google_id_token_wrong_issuer():
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        return_value=_valid_claims(iss="https://evil.example.com"),
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.ISSUER


def test_verify_google_id_token_missing_sub():
    claims = _valid_claims()
    claims.pop("sub")
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        return_value=claims,
    ):
        with pytest.raises(AuthError) as exc:
            verify_google_id_token("credential", CLIENT_ID)
    assert exc.value.reason is Reason.INVALID


def test_verify_google_id_token_defaults_optional_fields():
    claims = _valid_claims()
    claims.pop("name")
    claims.pop("picture")
    with patch(
        "src.auth.google_id_token.verify_oauth2_token",
        return_value=claims,
    ):
        identity = verify_google_id_token("credential", CLIENT_ID)
    assert identity.name == ""
    assert identity.picture == ""


# ---------------------------------------------------------------------------
# issue_session_token / verify_session_token
# ---------------------------------------------------------------------------


def test_session_token_roundtrip():
    token = issue_session_token("google:abc", "Alice", 3600, SECRET)
    claims = verify_session_token(token, SECRET)
    assert isinstance(claims, SessionClaims)
    assert claims.user_id == "google:abc"
    assert claims.display_name == "Alice"
    assert claims.exp > int(time.time())


def test_issue_session_token_validates_inputs():
    with pytest.raises(ValueError):
        issue_session_token("", "Alice", 3600, SECRET)
    with pytest.raises(ValueError):
        issue_session_token("google:abc", "Alice", 3600, "")
    with pytest.raises(ValueError):
        issue_session_token("google:abc", "Alice", 0, SECRET)


def test_verify_session_token_missing():
    with pytest.raises(AuthError) as exc:
        verify_session_token("", SECRET)
    assert exc.value.reason is Reason.MISSING


def test_verify_session_token_missing_secret():
    token = issue_session_token("google:abc", "Alice", 3600, SECRET)
    with pytest.raises(AuthError) as exc:
        verify_session_token(token, "")
    assert exc.value.reason is Reason.INVALID


def test_verify_session_token_expired():
    now = int(time.time())
    payload = {"sub": "google:abc", "name": "A", "iat": now - 10, "exp": now - 1}
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(AuthError) as exc:
        verify_session_token(token, SECRET)
    assert exc.value.reason is Reason.EXPIRED


def test_verify_session_token_wrong_secret():
    token = issue_session_token("google:abc", "Alice", 3600, SECRET)
    with pytest.raises(AuthError) as exc:
        verify_session_token(token, "y" * 48)
    assert exc.value.reason is Reason.INVALID


def test_verify_session_token_tampered_payload():
    token = issue_session_token("google:abc", "Alice", 3600, SECRET)
    header, payload, sig = token.split(".")
    tampered = ".".join([header, payload[:-2] + "AA", sig])
    with pytest.raises(AuthError) as exc:
        verify_session_token(tampered, SECRET)
    assert exc.value.reason is Reason.INVALID


def test_verify_session_token_missing_claims():
    payload = {"name": "A", "exp": int(time.time()) + 60}
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(AuthError) as exc:
        verify_session_token(token, SECRET)
    assert exc.value.reason is Reason.INVALID


def test_verify_session_token_non_int_exp():
    payload = {"sub": "google:abc", "name": "A", "exp": "not-an-int"}
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_session_token(token, SECRET)


def test_auth_error_carries_reason_and_message():
    err = AuthError(Reason.EXPIRED, "expired")
    assert err.reason is Reason.EXPIRED
    assert err.message == "expired"
    assert str(err) == "expired"
