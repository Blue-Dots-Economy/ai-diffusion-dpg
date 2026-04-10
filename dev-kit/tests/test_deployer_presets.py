import pytest
from dev_kit.agent.deployer.presets import PRESETS, apply_preset


def test_presets_have_three_tiers():
    assert set(PRESETS.keys()) == {"low", "medium", "high"}


def test_each_preset_has_seven_dpg_blocks():
    for tier, blocks in PRESETS.items():
        assert len(blocks) == 7, f"{tier} should have 7 blocks"
        assert "agent_core" in blocks
        assert "knowledge_engine" in blocks


def test_agent_core_gets_more_resources_than_standard():
    for tier in PRESETS:
        ac = PRESETS[tier]["agent_core"]
        tl = PRESETS[tier]["trust_layer"]
        assert int(ac["limits"]["cpu"].rstrip("m")) >= int(tl["limits"]["cpu"].rstrip("m"))


def test_apply_preset_returns_resources_per_block():
    result = apply_preset("medium")
    assert "agent_core" in result
    assert "requests" in result["agent_core"]
    assert "limits" in result["agent_core"]


def test_apply_preset_invalid_tier():
    with pytest.raises(ValueError, match="Unknown preset"):
        apply_preset("ultra")
