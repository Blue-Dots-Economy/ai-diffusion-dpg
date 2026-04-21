"""
dev-kit/dev_kit/agent/crypto.py

Server-side RSA-4096 key pair management and hybrid decryption for secrets
sent encrypted from the browser via the Web Crypto API.

Key lifecycle:
  - On first run, generate a 4096-bit RSA key pair and persist the private
    key to ~/.devkit/server_key.pem (mode 0o600, directory 0o700).
  - On subsequent runs, load the persisted key.
  - The public key is exposed via GET /api/deploy/public-key so the browser
    can encrypt secrets before sending them over HTTP.

Encryption scheme (mirrors browser Web Crypto):
  Browser side:
    1. Generate a random 32-byte AES-256-GCM key.
    2. Encrypt the plaintext secret with AES-256-GCM (12-byte random IV).
    3. Encrypt the AES key with RSA-OAEP (SHA-256).
    4. Send { encrypted_key, iv, encrypted_value } — all base64-encoded.

  Server side (this module):
    1. Decrypt the AES key with the RSA private key (OAEP + SHA-256).
    2. Decrypt the value with AES-256-GCM using the recovered key and IV.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_KEY_PATH = Path.home() / ".devkit" / "server_key.pem"


def _load_or_generate_key() -> rsa.RSAPrivateKey:
    """Load the server private key from disk, generating it if absent.

    The key file is created at ~/.devkit/server_key.pem with mode 0o600.
    The directory is created with mode 0o700 if it does not exist.

    Returns:
        The RSA-4096 private key instance.
    """
    if _KEY_PATH.exists():
        with _KEY_PATH.open("rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        logger.info(
            "crypto.key_loaded",
            extra={
                "operation": "crypto.load_key",
                "status": "success",
                "path": str(_KEY_PATH),
                "latency_ms": 0,
            },
        )
        return key  # type: ignore[return-value]

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    _KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _KEY_PATH.open("wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    _KEY_PATH.chmod(0o600)
    logger.info(
        "crypto.key_generated",
        extra={
            "operation": "crypto.generate_key",
            "status": "success",
            "path": str(_KEY_PATH),
            "latency_ms": 0,
        },
    )
    return key


_PRIVATE_KEY: rsa.RSAPrivateKey = _load_or_generate_key()


def get_public_key_spki_b64() -> str:
    """Return the server public key in base64-encoded DER/SPKI format.

    The browser uses this with ``SubtleCrypto.importKey('spki', ...)`` to
    import the key for RSA-OAEP encryption.

    Returns:
        Base64 string of the DER-encoded SubjectPublicKeyInfo structure.
    """
    spki = _PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(spki).decode()


def decrypt_secret(payload: dict) -> str:
    """Decrypt a single hybrid-encrypted secret from the browser.

    The payload must contain the three fields produced by the browser's
    ``encryptSecret()`` function in ``src/crypto.js``.

    Args:
        payload: Dict with keys:
            ``encrypted_key``   — base64 RSA-OAEP ciphertext of the AES key
            ``iv``              — base64 12-byte AES-GCM nonce
            ``encrypted_value`` — base64 AES-256-GCM ciphertext of the secret

    Returns:
        Plaintext secret string.

    Raises:
        KeyError: If any required key is missing from payload.
        ValueError: If decryption fails (wrong key or corrupted payload).
    """
    encrypted_key = base64.b64decode(payload["encrypted_key"])
    iv = base64.b64decode(payload["iv"])
    encrypted_value = base64.b64decode(payload["encrypted_value"])

    aes_key = _PRIVATE_KEY.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(iv, encrypted_value, None).decode()


def decrypt_secrets_dict(encrypted: dict) -> dict:
    """Recursively decrypt a dict of hybrid-encrypted secrets.

    String values that are dicts with ``encrypted_key`` are decrypted.
    Nested dicts (e.g. ``tool_secrets``) are recursed into.
    Empty strings are passed through unchanged.

    Args:
        encrypted: Nested dict where leaf values are either empty strings
            or cipher-payload dicts ``{encrypted_key, iv, encrypted_value}``.

    Returns:
        Dict with the same structure but all cipher payloads replaced by
        their plaintext strings.
    """
    result: dict = {}
    for key, value in encrypted.items():
        if isinstance(value, dict) and "encrypted_key" in value:
            result[key] = decrypt_secret(value)
        elif isinstance(value, dict):
            result[key] = decrypt_secrets_dict(value)
        else:
            result[key] = value  # empty string or non-secret passthrough
    return result
