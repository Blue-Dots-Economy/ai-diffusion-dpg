"""Tests for dev_kit.agent.accumulator.ConfigAccumulator."""
import pytest
from dev_kit.agent.accumulator import (
    BLOCKS,
    DRAFT_BLOCKS,
    ConfigAccumulator,
    ConfigStatus,
    PHASES,
)


class TestConfigAccumulatorUpdate:
    def test_initial_state_all_blocks_empty(self):
        acc = ConfigAccumulator()
        for block in BLOCKS:
            assert acc.get_block(block) == {}

    def test_update_top_level_section(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        assert acc.get_block("agent_core")["agent"]["primary_model"] == "claude-haiku-4-5-20251001"

    def test_update_nested_section_via_dot_notation(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "preprocessing.nlu_processor", {"confidence_threshold": 0.7})
        assert acc.get_block("agent_core")["preprocessing"]["nlu_processor"]["confidence_threshold"] == 0.7

    def test_update_merges_not_replaces(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.update("agent_core", "agent", {"fallback_model": "claude-haiku-4-5-20251001"})
        block = acc.get_block("agent_core")
        assert block["agent"]["primary_model"] == "claude-haiku-4-5-20251001"
        assert block["agent"]["fallback_model"] == "claude-haiku-4-5-20251001"

    def test_update_list_replaces_not_merges(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "preprocessing.nlu_processor", {"intents": ["greeting"]})
        acc.update("agent_core", "preprocessing.nlu_processor", {"intents": ["greeting", "apply_now"]})
        intents = acc.get_block("agent_core")["preprocessing"]["nlu_processor"]["intents"]
        assert intents == ["greeting", "apply_now"]

    def test_update_unknown_block_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="Unknown block"):
            acc.update("bogus", "section", {})

    def test_get_block_returns_deep_copy(self):
        acc = ConfigAccumulator()
        acc.update("trust_layer", "trust", {"input_rules": {"blocked_phrases": ["spam"]}})
        copy = acc.get_block("trust_layer")
        copy["trust"]["input_rules"]["blocked_phrases"].append("mutated")
        assert acc.get_block("trust_layer")["trust"]["input_rules"]["blocked_phrases"] == ["spam"]


class TestConfigAccumulatorStatus:
    def test_initial_status_all_pending(self):
        acc = ConfigAccumulator()
        for block in BLOCKS:
            assert acc.get_status(block) == ConfigStatus.PENDING

    def test_set_and_get_status(self):
        acc = ConfigAccumulator()
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        assert acc.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_set_status_unknown_block_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError):
            acc.set_status("bogus", ConfigStatus.COMPLETE)


class TestConfigAccumulatorSubagents:
    def test_set_subagent_adds_new(self):
        acc = ConfigAccumulator()
        sa = {"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []}
        acc.set_subagent(sa)
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert len(subagents) == 1
        assert subagents[0]["id"] == "greeting"

    def test_set_subagent_replaces_existing(self):
        acc = ConfigAccumulator()
        sa = {"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []}
        acc.set_subagent(sa)
        acc.set_subagent({**sa, "name": "Updated Greeting"})
        subagents = acc.get_block("agent_core")["agent_workflow"]["subagents"]
        assert len(subagents) == 1
        assert subagents[0]["name"] == "Updated Greeting"

    def test_set_subagent_missing_id_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="id"):
            acc.set_subagent({"name": "No ID"})

    def test_update_subagent_modifies_fields(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.update_subagent("greeting", {"name": "Updated"})
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["name"] == "Updated"

    def test_update_subagent_unknown_id_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="no subagent"):
            acc.update_subagent("nonexistent", {"name": "x"})

    def test_remove_subagent_removes_node(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.remove_subagent("greeting")
        assert acc.get_block("agent_core")["agent_workflow"]["subagents"] == []

    def test_add_routing_rule(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.add_routing_rule("greeting", "consent_granted", "profile", [], {})
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0] == {"intent": "consent_granted", "next_subagent_id": "profile"}

    def test_add_routing_rule_with_conditions(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "a", "name": "A", "system_prompt": "x", "is_start": False, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        conditions = [{"field": "income_urgency", "operator": "eq", "value": "immediate"}]
        acc.add_routing_rule("a", "some_intent", "b", conditions, {})
        routing = acc.get_block("agent_core")["agent_workflow"]["subagents"][0]["routing"]
        assert routing[0]["conditions"] == conditions

    def test_add_routing_rule_unknown_from_raises(self):
        acc = ConfigAccumulator()
        with pytest.raises(ValueError, match="no subagent"):
            acc.add_routing_rule("nonexistent", "intent", "target", [], {})


class TestConfigAccumulatorSerialisation:
    def test_roundtrip_to_from_dict(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        acc.set_status("agent_core", ConfigStatus.COMPLETE)
        data = acc.to_dict()
        acc2 = ConfigAccumulator.from_dict(data)
        assert acc2.get_block("agent_core") == acc.get_block("agent_core")
        assert acc2.get_status("agent_core") == ConfigStatus.COMPLETE

    def test_summary_is_string(self):
        acc = ConfigAccumulator()
        acc.update("agent_core", "agent", {"primary_model": "claude-haiku-4-5-20251001"})
        assert isinstance(acc.summary(), str)
        assert "agent_core" in acc.summary()


class TestWorkflowGraph:
    def test_empty_graph(self):
        acc = ConfigAccumulator()
        graph = acc.get_workflow_graph()
        assert graph == {"nodes": [], "edges": []}

    def test_graph_with_nodes_and_edges(self):
        acc = ConfigAccumulator()
        acc.set_subagent({"id": "greeting", "name": "Greeting", "system_prompt": "Hi", "is_start": True, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.set_subagent({"id": "profile", "name": "Profile", "system_prompt": "Tell me about yourself", "is_start": False, "is_terminal": False, "valid_intents": [], "tools": [], "routing": []})
        acc.add_routing_rule("greeting", "consent_granted", "profile", [], {})
        graph = acc.get_workflow_graph()
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "greeting", "to": "profile", "intent": "consent_granted"}


class TestPhasesOrdering:
    def test_user_state_phase_between_memory_and_trust(self):
        assert "user_state" in PHASES
        assert PHASES.index("user_state") == PHASES.index("memory") + 1
        assert PHASES.index("user_state") == PHASES.index("trust") - 1

    def test_tier_phase_is_first(self):
        """GH-137: 'tier' is the first phase (pre-phase before overview)."""
        assert PHASES[0] == "tier"

    def test_phases_count(self):
        """GH-137: PHASES has 12 entries (tier + original 11)."""
        assert len(PHASES) == 12
