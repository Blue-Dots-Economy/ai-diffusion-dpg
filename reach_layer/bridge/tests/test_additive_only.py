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


# ---------------------------------------------------------------------------
# Round-1 fix-up: dev-kit mirrors (`.claude/rules/runtime-devkit-sync.md` #4).
#
# `dev-kit/dev_kit/schema.py` is loaded by explicit file path too, for the
# same collision reason as agent_core's above -- and additionally because
# `dev-kit` is not an installed dependency of this package; its directory is
# put on `sys.path` only for the one transitive import
# (`dev_kit.schemas.domain.reach_layer` -> `from dev_kit.schemas.enums import
# ...`) that needs `dev_kit` to resolve as a real package rather than a
# loose module.
# ---------------------------------------------------------------------------

_DEV_KIT_SCHEMA_PATH = _REPO / "dev-kit" / "dev_kit" / "schema.py"

_devkit_spec = importlib.util.spec_from_file_location(
    "devkit_schema", _DEV_KIT_SCHEMA_PATH
)
_devkit_mod = importlib.util.module_from_spec(_devkit_spec)
sys.modules[_devkit_spec.name] = _devkit_mod
_devkit_spec.loader.exec_module(_devkit_mod)
assert _devkit_mod.__file__ == str(_DEV_KIT_SCHEMA_PATH)

ChannelsTopLevelConfig = _devkit_mod.ChannelsTopLevelConfig  # agent_core host-mode gate
DevKitReachChannelsConfig = _devkit_mod.ChannelsConfig  # reach_layer/base mirror
DevKitBridgeChannelConfig = _devkit_mod.BridgeChannelConfig

if str(_REPO / "dev-kit") not in sys.path:
    sys.path.insert(0, str(_REPO / "dev-kit"))
from dev_kit.schemas.domain.reach_layer import ChannelsSection as DomainReachChannelsSection  # noqa: E402


def test_channels_top_level_config_preserves_bridge():
    """Item 1 (Critical): this is the class AgentCoreConfig.channels actually
    uses -- the host-mode deploy gate. Round 1 wrongly edited a different,
    non-forbid ChannelsConfig further down the same file, which silently
    dropped ``bridge`` instead of validating it. Assert the keys, not just
    that validation doesn't raise, since silent-drop looks identical to
    success if you only check for an exception.
    """
    cfg = ChannelsTopLevelConfig.model_validate({
        "bridge": {"system_prompt_suffix": "x"},
        "web": {"system_prompt_suffix": "w"},
    })
    dumped = cfg.model_dump()
    assert "bridge" in dumped
    assert dumped["bridge"]["system_prompt_suffix"] == "x"


def test_channels_top_level_config_bridge_defaults_when_absent():
    cfg = ChannelsTopLevelConfig.model_validate({"web": {"system_prompt_suffix": "w"}})
    assert cfg.bridge.system_prompt_suffix == ""


def test_devkit_reach_channels_config_bridge_is_typed_and_optional():
    """Item 2 (Important): the reach_layer/base mirror's ``bridge`` field
    must be ``BridgeChannelConfig | None = None`` like every sibling in that
    class, not a bare non-Optional ``ChannelConfig`` (which discarded every
    real bridge key into an empty object and made bridge the only channel
    always emitted by ``ChannelsConfig().model_dump()``).
    """
    cfg = DevKitReachChannelsConfig.model_validate({})
    assert cfg.bridge is None  # not deployed by default, like cli/web/voice/mcp

    cfg = DevKitReachChannelsConfig.model_validate({
        "bridge": {"agent_core_url": "http://agent_core:8000", "timeout_s": 30}
    })
    assert isinstance(cfg.bridge, DevKitBridgeChannelConfig)
    assert cfg.bridge.agent_core_url == "http://agent_core:8000"
    assert cfg.bridge.timeout_s == 30


def test_domain_reach_layer_channels_section_accepts_bridge():
    """Item 3 (Important): dev-kit/dev_kit/schemas/domain/reach_layer.py's
    ChannelsSection is extra="forbid" with no bridge field before this fix,
    so reach_layer.channels.bridge was rejected at the wizard's per-write
    gate. This is a different file from domain/agent_core.py (whose
    pre-existing missing ``mcp`` is a separate, out-of-scope drift).
    """
    section = DomainReachChannelsSection.model_validate({
        "bridge": {"enabled": True, "terminal_word": "Thank you"}
    })
    assert section.bridge.terminal_word == "Thank you"

    # Still rejects genuinely unknown channels -- not loosened.
    with pytest.raises(Exception):
        DomainReachChannelsSection.model_validate({"telepathy": {"enabled": True}})
