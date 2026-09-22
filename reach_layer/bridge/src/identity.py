"""reach_layer/bridge/src/identity.py

Caller identity for the bridge channel (spec 11.2).

The caller's phone number is the job-seeker's identity in this domain, not an
attribute stored alongside one: Signals DPG keys profile lookup, profile
writes and job applications on it. A chat-completions request carries no field
for it, so the client sends it deliberately in ``metadata.caller_phone``.

Validation is strict because the failure is silent rather than loud. A number
without a country code is still a valid query upstream — it simply matches
nothing, so every call looks like a first-time caller, duplicate records
accumulate, and the profile written holds a number no employer can ring. A
leading "+" breaks a different way: the Action Gateway renders ``"+{user_id}"``
on the write, so a supplied "+" produces ``"++91..."``.
"""

from __future__ import annotations

import re

# Digits only. The minimum length excludes a bare national number: an Indian
# mobile is 10 digits, so 11 is the shortest value that can carry a country
# code. The upper bound is E.164's maximum.
_PHONE_RE = re.compile(r"^\d{11,15}$")

METADATA_PHONE_KEY = "caller_phone"
_PARAM_PATH = f"metadata.{METADATA_PHONE_KEY}"


class IdentityError(ValueError):
    """The caller's identity is absent or unusable.

    Attributes:
        param: Dotted path of the offending request field, for the OpenAI
            error envelope's ``param`` member.
    """

    def __init__(self, message: str, param: str = _PARAM_PATH) -> None:
        super().__init__(message)
        self.param = param


def extract_caller_phone(body: dict) -> str:
    """Pull the caller's phone number out of an OpenAI request body.

    Args:
        body: The parsed chat-completions request body.

    Returns:
        The validated phone number, digits only, country code first.

    Raises:
        IdentityError: When the value is absent, not a string, or malformed.
    """
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise IdentityError(
            "metadata.caller_phone is required: the caller's phone number "
            "identifies the job-seeker and every record is keyed on it."
        )

    raw = metadata.get(METADATA_PHONE_KEY)
    if not isinstance(raw, str):
        raise IdentityError(
            "metadata.caller_phone must be a string of digits."
        )

    phone = raw.strip()
    if not phone:
        raise IdentityError(
            "metadata.caller_phone is required and must not be empty."
        )

    if not _PHONE_RE.match(phone):
        raise IdentityError(
            "metadata.caller_phone must be digits only, country code first, "
            "with no '+', spaces or punctuation — for example 919900112233."
        )

    return phone
