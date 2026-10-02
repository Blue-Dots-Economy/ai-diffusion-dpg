from pathlib import Path

import yaml

from eval.nlu.offline import load_merged_config
from src.predispatch.rules import select
from src.schema.config import MergedConfig

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
CFG = yaml.safe_load((BD / "agent_core.yaml").read_text(encoding="utf-8"))
SUB = {s["id"]: s for s in CFG["agent_workflow"]["subagents"]}


def test_validates():
    MergedConfig.validate_full(load_merged_config(BD))


def test_fetch_jobs_rule_on_and_writes_off():
    jm = SUB["job_match"]["predispatch"]
    assert jm[0]["tool"] == "fetch_jobs" and jm[0].get("enabled", True) is True and jm[0]["unless_fresh"] is True
    assert SUB["apply_confirm"]["predispatch"][0]["enabled"] is False
    assert SUB["profile_setup"]["predispatch"][0]["enabled"] is False


def test_fetch_jobs_query_from_session():
    s = select(SUB["job_match"]["predispatch"], intent="any_input", state={}, session={"stored_trade": "Welder",
               "location": "Bangalore"}, tables=CFG["predispatch_tables"],
               tool_schemas={"fetch_jobs": {"properties": {"query_text": {"type": "string"}}, "required": ["query_text"]}},
               write_tools={"apply_job", "save_profile"}, has_fresh=lambda t: False)
    assert (s.tool, s.args) == ("fetch_jobs", {"query_text": "Welder jobs in Bengaluru"})


def test_prompts_updated():
    for sid in ("job_match", "apply_confirm", "profile_setup"):
        assert "do not call the tool again" in SUB[sid]["system_prompt"], sid
    text = (BD / "agent_core.yaml").read_text(encoding="utf-8")
    for gone in ("re-fetch it, do not recall it", "an ordinal such as"):
        assert gone not in text, gone


def test_predispatch_tables_complete():
    tables = CFG["predispatch_tables"]
    assert tables["city_canonical"] == {
        "Bangalore": "Bengaluru", "Bombay": "Mumbai", "Madras": "Chennai",
        "Calcutta": "Kolkata", "Hubli": "Hubballi", "Mysore": "Mysuru",
        "Trivandrum": "Thiruvananthapuram", "Cochin": "Kochi",
        "Pondicherry": "Puducherry", "Gurgaon": "Gurugram",
    }
    assert tables["name_placeholders"]


def test_job_match_prompt_has_no_dangling_pair_reference():
    prompt = SUB["job_match"]["system_prompt"]
    assert "any pair above" not in prompt
    # the model still re-fetches on its own, so the canonical names stay in the prompt
    for canonical in CFG["predispatch_tables"]["city_canonical"].values():
        assert canonical in prompt, canonical
