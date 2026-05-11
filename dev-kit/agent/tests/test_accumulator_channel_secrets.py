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


class TestRecordingSecretsInChannelSecrets:
    """caller_id_hash_salt (and S3 KMS key) must surface as secrets when recording is opted in."""

    def _acc_with_recording(self, source: str, backend: str = "local") -> "ConfigAccumulator":
        """Build an accumulator that has voice selected and recording configured."""
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["voice"])
        # Directly plant the recording config into the internal data structure the
        # way the wizard would via update_config (after deep-merge through reach_layer).
        acc._data["reach_layer"].setdefault("reach_layer", {}).setdefault(
            "channels", {}
        ).setdefault("voice", {})["recording"] = {
            "source": source,
            "store": {"backend": backend},
        }
        return acc

    def test_recording_salt_is_marked_as_secret(self):
        """caller_id_hash_salt under recording must be treated as a secret."""
        acc = self._acc_with_recording(source="vobiz")
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "RECORDING_CALLER_ID_HASH_SALT" in env_vars
        cred = next(d for d in result if d["env_var"] == "RECORDING_CALLER_ID_HASH_SALT")
        assert cred["secret"] is True
        assert cred["section"] == "voice"

    def test_disabled_recording_omits_salt_secret(self):
        """When source=disabled, no recording-specific secrets are returned."""
        acc = self._acc_with_recording(source="disabled")
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "RECORDING_CALLER_ID_HASH_SALT" not in env_vars

    def test_s3_backend_adds_kms_secret(self):
        """S3 store backend must add the KMS key ID secret entry."""
        acc = self._acc_with_recording(source="vobiz", backend="s3")
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "RECORDING_S3_KMS_KEY_ID" in env_vars
        cred = next(d for d in result if d["env_var"] == "RECORDING_S3_KMS_KEY_ID")
        assert cred["secret"] is True
        assert cred["required"] is False  # optional — bucket-default encryption is fine

    def test_local_backend_omits_kms_secret(self):
        """Local store backend must NOT add the KMS key ID secret entry."""
        acc = self._acc_with_recording(source="vobiz", backend="local")
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "RECORDING_S3_KMS_KEY_ID" not in env_vars

    def test_no_voice_channel_omits_recording_secrets(self):
        """Without voice channel selected, no recording secrets are added."""
        acc = ConfigAccumulator()
        acc.set_reach_channel_selection(["web"])
        result = acc.get_required_channel_secrets()
        env_vars = [d["env_var"] for d in result]
        assert "RECORDING_CALLER_ID_HASH_SALT" not in env_vars
