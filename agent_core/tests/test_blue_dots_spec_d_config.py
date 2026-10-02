"""Spec D §8: the Blue Dots config carries the output contract and job shaping, and none of the removed prompt text."""
from pathlib import Path

import yaml

from eval.nlu.offline import OfflineGateway, load_merged_config
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.understanding.config import DialogueActConfig
from src.workflow_loader import AgentWorkflowLoader

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
TEXT = (BD / "agent_core.yaml").read_text(encoding="utf-8")


def test_validates():
    MergedConfig.validate_full(load_merged_config(BD))


def test_contract_and_shaping_present():
    cfg = yaml.safe_load(TEXT)
    bridge = cfg["channels"]["bridge"]
    assert "tts_rules" not in bridge and bridge["output_contract"]["default_language"] == "hindi"
    jobs = next(c for c in cfg["connectors"]["read"] if c["name"] == "fetch_jobs")
    assert "salary_spoken" in jobs["result_shaping"]["spoken"]
    assert cfg["agent"]["state_fields"] == ["applications_submitted", "selected_job_item_id"]


def test_removed_prompt_text_is_gone():
    for needle in ("नौकरियाँ मिली हैं।", "एक पल रुकिए", "Let me share what I found", "TWO-STEP FLOW",
                   "User Profile context", "onboard_prep", "Six phases", "Order the first batch",
                   "Order what survives by", "see tts_rules below", "[salary]", "<known_profile>", "Last question asked"):
        assert needle not in TEXT, needle
    assert "[salary_spoken]" in TEXT and "never re-rank" in TEXT


def test_nlu_dialogue_act_config_survives_rewrite():
    """The A-items must not remove what the NLU dialogue-act config relies on."""
    cfg = load_merged_config(BD)
    wf = AgentWorkflowLoader().load(config=cfg, tool_registry=ToolRegistry(cfg, OfflineGateway(cfg)))
    da = DialogueActConfig.from_config(cfg)
    assert {"consent_response", "age", "trade", "location", "name"} <= set(da.slots)
    assert wf.subagents["job_match"].pending and wf.subagents["job_match"].routing
