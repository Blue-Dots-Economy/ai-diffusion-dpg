"""Tests for block_status: derive 'complete' | 'incomplete' from field_status."""
from dev_kit.agent.block_status import block_completion_status, all_block_statuses


def test_no_fields_returns_incomplete():
    assert block_completion_status("agent_core", {}) == "incomplete"


def test_all_answered_returns_complete():
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.fallback_model": "answered",
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_any_pending_returns_incomplete():
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.fallback_model": "pending",
    }
    assert block_completion_status("agent_core", fs) == "incomplete"


def test_needs_re_asking_is_incomplete():
    fs = {"agent_core.agent.primary_model": "needs_re_asking"}
    assert block_completion_status("agent_core", fs) == "incomplete"


def test_not_applicable_counts_as_complete_for_that_field():
    """A 'not_applicable' field doesn't block completion."""
    fs = {
        "agent_core.agent.primary_model": "answered",
        "agent_core.agent.consent_prompt": "not_applicable",
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_only_fields_for_named_block_counted():
    """Fields from other blocks don't affect this block's status."""
    fs = {
        "agent_core.agent.primary_model": "answered",
        "trust_layer.trust.policy_pack": "pending",  # other block
    }
    assert block_completion_status("agent_core", fs) == "complete"


def test_all_block_statuses_returns_one_per_block():
    fs = {"agent_core.agent.primary_model": "answered"}
    statuses = all_block_statuses(fs)
    assert set(statuses.keys()) == {
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }
    assert statuses["agent_core"] == "complete"
    assert statuses["trust_layer"] == "incomplete"  # no fields → incomplete
