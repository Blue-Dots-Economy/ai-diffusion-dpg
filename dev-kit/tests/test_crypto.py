"""Tests for dev_kit.agent.crypto — RSA key pair lifecycle and decrypt_secret."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_encrypted_payload(plaintext: str) -> dict:
    """Encrypt plaintext the same way the browser would (using the server's public key)."""
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import dev_kit.agent.crypto as crypto_mod

    aes_key_bytes = os.urandom(32)
    iv = os.urandom(12)
    aesgcm = AESGCM(aes_key_bytes)
    encrypted_value = aesgcm.encrypt(iv, plaintext.encode(), None)

    encrypted_key = crypto_mod._PRIVATE_KEY.public_key().encrypt(
        aes_key_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return {
        "encrypted_key": base64.b64encode(encrypted_key).decode(),
        "iv": base64.b64encode(iv).decode(),
        "encrypted_value": base64.b64encode(encrypted_value).decode(),
    }


def test_get_public_key_spki_b64_returns_base64_string():
    import dev_kit.agent.crypto as crypto_mod
    result = crypto_mod.get_public_key_spki_b64()
    assert isinstance(result, str)
    # Must be valid base64
    decoded = base64.b64decode(result)
    assert len(decoded) > 100  # RSA-4096 SPKI is ~550 bytes


def test_decrypt_secret_round_trip():
    import dev_kit.agent.crypto as crypto_mod
    plaintext = "sk-ant-supersecretkey"
    payload = _make_encrypted_payload(plaintext)
    assert crypto_mod.decrypt_secret(payload) == plaintext


def test_decrypt_secret_with_special_characters():
    import dev_kit.agent.crypto as crypto_mod
    plaintext = "p@$$w0rd!#&*()=+{}[]"
    payload = _make_encrypted_payload(plaintext)
    assert crypto_mod.decrypt_secret(payload) == plaintext


def test_decrypt_secrets_dict_decrypts_all_values():
    import dev_kit.agent.crypto as crypto_mod
    encrypted = {
        "anthropic_api_key": _make_encrypted_payload("sk-ant-abc"),
        "redis_password": _make_encrypted_payload("redis-pass"),
        "tool_secrets": {
            "ONEST_API_KEY": _make_encrypted_payload("onest-key-xyz"),
        },
    }
    result = crypto_mod.decrypt_secrets_dict(encrypted)
    assert result["anthropic_api_key"] == "sk-ant-abc"
    assert result["redis_password"] == "redis-pass"
    assert result["tool_secrets"]["ONEST_API_KEY"] == "onest-key-xyz"


def test_decrypt_secrets_dict_skips_empty_strings():
    import dev_kit.agent.crypto as crypto_mod
    encrypted = {
        "anthropic_api_key": _make_encrypted_payload("sk-ant-abc"),
        "redis_password": "",
    }
    result = crypto_mod.decrypt_secrets_dict(encrypted)
    assert result["anthropic_api_key"] == "sk-ant-abc"
    assert result["redis_password"] == ""


def test_key_is_reloaded_from_disk_on_reimport(tmp_path):
    """The same private key is returned when the module-level constant is loaded."""
    import dev_kit.agent.crypto as crypto_mod
    key1 = crypto_mod.get_public_key_spki_b64()
    # Reimport (module caching means same object, same key)
    import importlib
    importlib.reload(crypto_mod)
    key2 = crypto_mod.get_public_key_spki_b64()
    assert key1 == key2


def test_public_key_endpoint_returns_spki_b64():
    """GET /api/deploy/public-key returns a non-empty base64 string."""
    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    from fastapi.testclient import TestClient
    from dev_kit.agent.app import app
    client = TestClient(app)

    res = client.get("/api/deploy/public-key")
    assert res.status_code == 200
    data = res.json()
    assert "public_key" in data
    decoded = base64.b64decode(data["public_key"])
    assert len(decoded) > 100
