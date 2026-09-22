"""The schema additions accept `bridge` and change nothing else.

Agent Core's ChannelsConfig is extra="forbid" with a fixed field set, so
channels.bridge in YAML is rejected at boot until the field exists. These tests
pin both halves: the new channel is accepted, and every existing channel and
domain still validates exactly as before.

The module is loaded by explicit file path rather than a bare
``from schema.config import ...`` import. This repo has seven
``schema/config.py`` files (one per block) and observability_layer's is
installed into site-packages, so a bare import can silently resolve to the
wrong module — a test that validated the wrong schema would pass while
leaving the real extra="forbid" constraint unchecked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SPEC_PATH = _REPO / "agent_core" / "src" / "schema" / "config.py"

_spec = importlib.util.spec_from_file_location("agent_core_schema_config", _SPEC_PATH)
_mod = importlib.util.module_from_spec(_spec)
# Registered in sys.modules under its unique name before exec so pydantic can
# resolve this module's (postponed, `from __future__ import annotations`)
# forward references via sys.modules[cls.__module__] during model building.
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
assert _mod.__file__ == str(_SPEC_PATH)  # proves we loaded agent_core's, not another

ChannelsConfig = _mod.ChannelsConfig


def test_bridge_channel_is_accepted():
    cfg = ChannelsConfig.model_validate({
        "bridge": {"system_prompt_suffix": "x", "terminal_word": "Thank you"}
    })
    assert cfg.bridge.terminal_word == "Thank you"


def test_bridge_defaults_when_absent():
    """Existing domains omit it entirely and must still validate."""
    cfg = ChannelsConfig.model_validate({"web": {"system_prompt_suffix": "w"}})
    assert cfg.bridge.system_prompt_suffix == ""
    assert cfg.bridge.terminal_word is None


@pytest.mark.parametrize("name", ["voice", "web", "cli", "mcp"])
def test_existing_channels_unchanged(name):
    cfg = ChannelsConfig.model_validate({name: {"system_prompt_suffix": "kept"}})
    assert getattr(cfg, name).system_prompt_suffix == "kept"


def test_unknown_channel_is_still_rejected():
    """The additions must not loosen validation."""
    with pytest.raises(Exception):
        ChannelsConfig.model_validate({"telepathy": {"system_prompt_suffix": "x"}})


@pytest.mark.parametrize("domain", [
    "kkb", "blue-dots", "blue-dots-economy", "poem-bot", "tourism-bot",
])
def test_every_existing_domain_still_validates(domain):
    """The regression that matters: no shipped domain config may break."""
    import yaml

    path = _REPO / "dev-kit" / "configs" / domain / "agent_core.yaml"
    if not path.exists():
        pytest.skip(f"{domain} not present in this checkout")
    raw = yaml.safe_load(path.read_text())
    ChannelsConfig.model_validate(raw.get("channels") or {})
