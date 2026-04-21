"""
dev-kit/tests/test_devkit_config.py

Tests for DevKitConfig loader.
"""
from __future__ import annotations

import pytest
from pathlib import Path


class TestDevKitConfigNormal:
    def test_loads_defaults(self, tmp_path, monkeypatch):
        """Loader reads devkit.yaml and returns populated dataclass."""
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("""
user_id: "test-operator"
upload:
  max_files_per_upload: 5
  max_file_size_mb: 30
  supported_extensions: [".pdf", ".txt"]
polling:
  poll_interval_seconds: 5
  poll_timeout_minutes: 15
""")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        cfg = load_devkit_config()
        assert cfg.user_id == "test-operator"
        assert cfg.upload.max_files_per_upload == 5
        assert cfg.upload.max_file_size_mb == 30
        assert ".pdf" in cfg.upload.supported_extensions
        assert cfg.polling.poll_interval_seconds == 5
        assert cfg.polling.poll_timeout_minutes == 15

    def test_user_id_default(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("user_id: devkit-operator\nupload:\n  max_files_per_upload: 3\n  max_file_size_mb: 10\n  supported_extensions: ['.pdf']\npolling:\n  poll_interval_seconds: 5\n  poll_timeout_minutes: 10\n")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        cfg = load_devkit_config()
        assert cfg.user_id == "devkit-operator"


class TestDevKitConfigEdge:
    def test_missing_config_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
        from dev_kit.config.loader import load_devkit_config
        with pytest.raises(FileNotFoundError):
            load_devkit_config()


class TestDevKitConfigFailure:
    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "devkit.yaml"
        cfg_file.write_text("not: valid: yaml: [unclosed")
        monkeypatch.setenv("DEVKIT_CONFIG_PATH", str(cfg_file))
        from dev_kit.config.loader import load_devkit_config
        with pytest.raises(Exception):
            load_devkit_config()
