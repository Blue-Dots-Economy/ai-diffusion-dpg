"""Authentication primitives for Reach Layer Google Sign-On.

Pure functions for verifying Google ID tokens and issuing/verifying
short-lived session JWTs. Belongs to the Reach Layer block of the DPG
framework. Has no FastAPI or HTTP coupling so it can be unit tested in
isolation and reused from any channel adapter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_SESSION_ALG = "HS256"
_USER_ID_PREFIX = "google:"


class Reason(str, Enum):
    """Stable, machine-readable failure reasons for AuthError.

    Used by callers to map to HTTP status codes and metrics labels.
    Values are stable identifiers — do not rename without a migration.
    """

    MISSING = "missing"
    INVALID = "invalid"
    EXPIRED = "expired"
    AUDIENCE = "audience"
    ISSUER = "issuer"
    UNVERIFIED_EMAIL = "unverified_email"


class AuthError(Exception):
    """Structured authentication error.

    Attributes:
        reason: Stable Reason enum value indicating why auth failed.
        message: Human-readable explanation safe to log (no PII).
    """

    def __init__(self, reason: Reason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified Google identity extracted from an ID token.

    Attributes:
        sub: Google's stable subject identifier for the user.
        email: User's email address (verified by Google).
        name: Display name from Google profile (may be empty string).
        picture: URL of profile picture (may be empty string).
    """

    sub: str
    email: str
    name: str
    picture: str

    @property
    def user_id(self) -> str:
        """Stable Reach Layer user_id string ("google:<sub>")."""
        return f"{_USER_ID_PREFIX}{self.sub}"


@dataclass(frozen=True)
class SessionClaims:
    """Decoded session JWT claims.

    Attributes:
        user_id: Reach Layer user_id (e.g. "google:117…").
        display_name: Cached display name for greetings/UI.
        exp: Unix epoch seconds at which the session expires.
        email: Cached email for UI display (may be empty for legacy tokens).
        picture: Cached profile picture URL (may be empty for legacy tokens).
    """

    user_id: str
    display_name: str
    exp: int
    email: str = ""
    picture: str = ""


def verify_google_id_token(credential: str, client_id: str) -> GoogleIdentity:
    """Verify a Google ID token and return the trusted identity.

    Verification is delegated to google-auth which checks signature
    against Google's JWKS, audience, issuer, and expiry. This function
    additionally enforces ``email_verified`` so that unverified accounts
    cannot bind to a Reach Layer user_id.

    Args:
        credential: The raw ID token string returned by Google Identity
            Services on the client.
        client_id: The OAuth 2.0 Web client_id this token was issued for.

    Returns:
        GoogleIdentity populated from the token's verified claims.

    Raises:
        AuthError: With a Reason explaining the verification failure.
    """
    if not credential:
        raise AuthError(Reason.MISSING, "credential is empty")
    if not client_id:
        raise AuthError(Reason.INVALID, "client_id not configured")

    start = time.time()
    try:
        claims = google_id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
        )
    except ValueError as exc:
        reason = _classify_google_error(str(exc))
        logger.warning(
            "google_id_token_verify",
            extra={
                "operation": "auth.verify_google_id_token",
                "status": "failure",
                "reason": reason.value,
                "error": type(exc).__name__,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        raise AuthError(reason, "google id token verification failed") from exc

    if claims.get("iss") not in _GOOGLE_ISSUERS:
        logger.warning(
            "google_id_token_verify",
            extra={
                "operation": "auth.verify_google_id_token",
                "status": "failure",
                "reason": Reason.ISSUER.value,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        raise AuthError(Reason.ISSUER, "unexpected issuer")

    if not claims.get("email_verified", False):
        logger.warning(
            "google_id_token_verify",
            extra={
                "operation": "auth.verify_google_id_token",
                "status": "failure",
                "reason": Reason.UNVERIFIED_EMAIL.value,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        raise AuthError(Reason.UNVERIFIED_EMAIL, "email not verified by google")

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise AuthError(Reason.INVALID, "token missing sub or email")

    logger.info(
        "google_id_token_verify",
        extra={
            "operation": "auth.verify_google_id_token",
            "status": "success",
            "latency_ms": int((time.time() - start) * 1000),
        },
    )

    return GoogleIdentity(
        sub=sub,
        email=email,
        name=claims.get("name", "") or "",
        picture=claims.get("picture", "") or "",
    )


def issue_session_token(
    user_id: str,
    display_name: str,
    ttl_s: int,
    secret: str,
    email: str = "",
    picture: str = "",
) -> str:
    """Issue an HS256-signed session JWT for an authenticated user.

    Args:
        user_id: Reach Layer user_id (e.g. "google:117…").
        display_name: Display name to embed for cheap UI rendering.
        ttl_s: Token lifetime in seconds (must be > 0).
        secret: HMAC secret. Must be a non-empty, high-entropy value.

    Returns:
        Compact JWT string suitable for an HttpOnly cookie.

    Raises:
        ValueError: If user_id, secret is empty, or ttl_s is non-positive.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not secret:
        raise ValueError("secret is required")
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")

    now = int(time.time())
    payload = {
        "sub": user_id,
        "name": display_name or "",
        "email": email or "",
        "picture": picture or "",
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(payload, secret, algorithm=_SESSION_ALG)


def verify_session_token(token: str, secret: str) -> SessionClaims:
    """Verify a session JWT issued by :func:`issue_session_token`.

    Args:
        token: The compact JWT from the session cookie.
        secret: HMAC secret used at issue time.

    Returns:
        SessionClaims with user_id, display_name, and exp.

    Raises:
        AuthError: With Reason.MISSING, EXPIRED, or INVALID.
    """
    if not token:
        raise AuthError(Reason.MISSING, "session token is empty")
    if not secret:
        raise AuthError(Reason.INVALID, "secret not configured")

    try:
        payload = jwt.decode(token, secret, algorithms=[_SESSION_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(Reason.EXPIRED, "session token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(Reason.INVALID, "session token invalid") from exc

    sub = payload.get("sub")
    exp = payload.get("exp")
    if not sub or not isinstance(exp, int):
        raise AuthError(Reason.INVALID, "session token missing claims")

    return SessionClaims(
        user_id=sub,
        display_name=payload.get("name", "") or "",
        exp=exp,
        email=payload.get("email", "") or "",
        picture=payload.get("picture", "") or "",
    )


def _classify_google_error(message: str) -> Reason:
    """Map google-auth ValueError messages to a Reason enum.

    google-auth raises a single ValueError for many distinct failure
    modes. We classify by substring so callers (and metrics) can see why.
    """
    msg = message.lower()
    if "expired" in msg:
        return Reason.EXPIRED
    if "audience" in msg or "aud" in msg:
        return Reason.AUDIENCE
    if "issuer" in msg or "iss" in msg:
        return Reason.ISSUER
    return Reason.INVALID


__all__ = [
    "AuthError",
    "GoogleIdentity",
    "Reason",
    "SessionClaims",
    "issue_session_token",
    "verify_google_id_token",
    "verify_session_token",
]
