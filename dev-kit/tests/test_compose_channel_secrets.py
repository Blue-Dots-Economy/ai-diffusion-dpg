"""Tests for channel_secrets injection in run_compose_up."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_channel_secrets_injected_as_env_vars():
    """channel_secrets values are forwarded as environment variables."""
    from dev_kit.agent.deployer.compose import run_compose_up

    captured = {}

    async def mock_exec(*args, **kwargs):
        captured.update(kwargs.get("env", {}))
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        await run_compose_up(
            compose_file_path="/tmp/fake.yml",
            secrets={
                "anthropic_api_key": "sk-ant-test",
                "channel_secrets": {
                    "GOOGLE_CLIENT_ID": "google-abc",
                    "VOBIZ_AUTH_ID": "vobiz-auth",
                    "PUBLIC_URL": "https://voice.example.com",
                },
            },
        )

    assert captured.get("GOOGLE_CLIENT_ID") == "google-abc"
    assert captured.get("VOBIZ_AUTH_ID") == "vobiz-auth"
    assert captured.get("PUBLIC_URL") == "https://voice.example.com"


@pytest.mark.asyncio
async def test_empty_channel_secret_values_are_skipped():
    """Empty string values inside channel_secrets are not forwarded."""
    from dev_kit.agent.deployer.compose import run_compose_up

    captured = {}

    async def mock_exec(*args, **kwargs):
        captured.update(kwargs.get("env", {}))
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        await run_compose_up(
            compose_file_path="/tmp/fake.yml",
            secrets={
                "channel_secrets": {
                    "GOOGLE_CLIENT_ID": "",
                    "VOBIZ_AUTH_ID": "vobiz-auth",
                },
            },
        )

    assert "GOOGLE_CLIENT_ID" not in captured
    assert captured.get("VOBIZ_AUTH_ID") == "vobiz-auth"


@pytest.mark.asyncio
async def test_no_channel_secrets_key_does_not_error():
    """run_compose_up works normally when secrets has no channel_secrets key."""
    from dev_kit.agent.deployer.compose import run_compose_up

    async def mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await run_compose_up(
            compose_file_path="/tmp/fake.yml",
            secrets={"anthropic_api_key": "sk-ant-test"},
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_old_flat_google_client_id_key_no_longer_sets_env_var():
    """Old secrets.google_client_id flat key no longer sets GOOGLE_CLIENT_ID."""
    from dev_kit.agent.deployer.compose import run_compose_up

    captured = {}

    async def mock_exec(*args, **kwargs):
        captured.update(kwargs.get("env", {}))
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        await run_compose_up(
            compose_file_path="/tmp/fake.yml",
            secrets={"google_client_id": "legacy-value"},
        )

    assert "GOOGLE_CLIENT_ID" not in captured
