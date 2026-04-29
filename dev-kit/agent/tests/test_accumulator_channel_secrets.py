"""Tests for ConfigAccumulator.get_required_channel_secrets()."""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator


class TestGetRequiredChannelSecrets:
    def test_no_channels_returns_empty_list(self):
        acc = ConfigAccumulator()
        assert acc.get_required_channel_secrets() == []

    def test_cli_only_returns_empty_list(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["cli"])
        assert acc.get_required_channel_secrets() == []

    def test_web_channel_returns_google_client_id(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["web"])
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "GOOGLE_CLIENT_ID" in env_vars
        assert all(d["section"] == "web" for d in result)

    def test_voice_channel_returns_all_five_voice_creds(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["voice"])
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "VOBIZ_AUTH_ID" in env_vars
        assert "VOBIZ_AUTH_TOKEN" in env_vars
        assert "RAYA_API_KEY" in env_vars
        assert "PUBLIC_URL" in env_vars
        assert "VOBIZ_FROM_NUMBER" in env_vars
        assert all(d["section"] == "voice" for d in result)

    def test_both_channels_returns_web_and_voice_creds(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["web", "voice"])
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "GOOGLE_CLIENT_ID" in env_vars
        assert "VOBIZ_AUTH_ID" in env_vars
        assert len([d for d in result if d["section"] == "web"]) == 1
        assert len([d for d in result if d["section"] == "voice"]) == 5

    def test_google_client_id_descriptor_shape(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["web"])
        result = acc.get_required_channel_secrets()
        assert len(result) == 1
        d = result[0]
        assert d["env_var"] == "GOOGLE_CLIENT_ID"
        assert d["label"] == "Google Client ID"
        assert d["required"] is True
        assert d["section"] == "web"
        assert d["secret"] is False
        assert isinstance(d["description"], str) and len(d["description"]) > 0

    def test_voice_secret_flags(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["voice"])
        result = acc.get_required_channel_secrets()
        by_env = {d["env_var"]: d for d in result}
        assert by_env["VOBIZ_AUTH_ID"]["secret"] is True
        assert by_env["VOBIZ_AUTH_TOKEN"]["secret"] is True
        assert by_env["RAYA_API_KEY"]["secret"] is True
        assert by_env["PUBLIC_URL"]["secret"] is False
        assert by_env["VOBIZ_FROM_NUMBER"]["secret"] is False

    def test_all_voice_creds_required(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["voice"])
        result = acc.get_required_channel_secrets()
        assert all(d["required"] is True for d in result)

    def test_returns_deep_copy(self):
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["web"])
        result = acc.get_required_channel_secrets()
        result[0]["env_var"] = "MUTATED"
        fresh = acc.get_required_channel_secrets()
        assert fresh[0]["env_var"] == "GOOGLE_CLIENT_ID"
