import pytest
from trust_layer.src.blocks.guardrails import GuardrailsBlock


KKB_CONFIG = {
    "trust": {
        "policy_pack": "kkb_advisory_jobs",
        "policy_packs": {
            "kkb_advisory_jobs": {
                "risks": ["false_certainty", "emotional_overreach"],
                "guardrails": {
                    "false_certainty": {
                        "id": "GR-001",
                        "severity": "blocker",
                        "failure_mode": "block",
                        "prompt_constraints": [
                            "MUST NOT guarantee job outcomes",
                        ],
                        "required_disclosures": ["Hiring decisions rest with employer"],
                        "refusal_template": "Main guarantee nahi de sakta.",
                    },
                    "emotional_overreach": {
                        "id": "GR-003",
                        "severity": "warning",
                        "failure_mode": "constrain",
                        "prompt_constraints": ["MUST NOT provide counselling"],
                        "required_disclosures": [],
                        "refusal_template": None,
                    },
                },
            }
        },
    }
}


@pytest.fixture
def block():
    return GuardrailsBlock(KKB_CONFIG)


# ── normal ────────────────────────────────────────────────────────────────
def test_known_risk_returns_constraints(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty"],
        user_segment=None,
    )
    assert "MUST NOT guarantee job outcomes" in result["prompt_constraints"]
    assert "Hiring decisions rest with employer" in result["required_disclosures"]
    assert result["refusal_templates"]["false_certainty"] == "Main guarantee nahi de sakta."


def test_multiple_risks_merged(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty", "emotional_overreach"],
        user_segment=None,
    )
    assert len(result["prompt_constraints"]) == 2
    assert "MUST NOT provide counselling" in result["prompt_constraints"]


def test_refusal_template_none_excluded(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["emotional_overreach"],
        user_segment=None,
    )
    assert "emotional_overreach" not in result["refusal_templates"]


# ── edge ──────────────────────────────────────────────────────────────────
def test_empty_active_risks_returns_empty(block):
    result = block.assemble_constraints("s1", "ready", [], None)
    assert result["prompt_constraints"] == []
    assert result["required_disclosures"] == []


def test_unknown_risk_skipped(block):
    result = block.assemble_constraints("s1", "ready", ["scope_breach"], None)
    assert result["prompt_constraints"] == []


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.assemble_constraints(None, "ready", ["false_certainty"], None)


# ── failure ───────────────────────────────────────────────────────────────
def test_missing_policy_pack_returns_empty():
    block = GuardrailsBlock({})
    result = block.assemble_constraints("s1", "ready", ["false_certainty"], None)
    assert result["prompt_constraints"] == []


def test_action_gates_key_present_in_result(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty"],
        user_segment=None,
    )
    assert "action_gates" in result
    assert isinstance(result["action_gates"], dict)
