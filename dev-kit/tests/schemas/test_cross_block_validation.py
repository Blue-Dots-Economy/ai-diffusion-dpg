"""Tests for cross-block invariants used by set_phase and pre-deploy validate."""
from dev_kit.schemas.cross_block_validation import validate_cross_block


def _empty_blocks() -> dict[str, dict]:
    return {
        "agent_core": {},
        "knowledge_engine": {},
        "memory_layer": {},
        "trust_layer": {},
        "action_gateway": {},
        "reach_layer": {},
        "observability_layer": {},
    }


def test_empty_state_passes():
    """An empty accumulator triggers no invariants — every check self-guards."""
    assert validate_cross_block(_empty_blocks(), selected_channels=[]) == []


def test_intent_filters_must_match_nlu_intents():
    """Check 5 — the user's reported case."""
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "preprocessing": {"nlu_processor": {"intents": ["greeting", "ask_packages"]}},
    }
    blocks["knowledge_engine"] = {
        "knowledge": {
            "blocks": {
                "static_knowledge_base": {
                    "intent_filters": {
                        "ask_packages": ["package_info"],
                        "ask_locations": ["site_info"],   # missing from NLU intents
                        "ask_booking": ["booking_policy"],  # missing
                    },
                },
            },
        },
    }
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any("ask_locations" in e and "not declared" in e for e in errors)
    assert any("ask_booking" in e and "not declared" in e for e in errors)
    # ask_packages is in NLU, so it should not error
    assert not any("'ask_packages'" in e and "not declared" in e for e in errors)


def test_intent_filters_pass_when_all_in_nlu():
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "preprocessing": {"nlu_processor": {"intents": ["greeting", "ask_packages", "ask_booking"]}},
    }
    blocks["knowledge_engine"] = {
        "knowledge": {
            "blocks": {
                "static_knowledge_base": {
                    "intent_filters": {"ask_packages": ["a"], "ask_booking": ["b"]},
                },
            },
        },
    }
    assert validate_cross_block(blocks, selected_channels=[]) == []


def _minimal_workflow(**overrides) -> dict:
    """Minimal valid agent_workflow shell for tests focused on tool/intent checks.

    Provides workflow_id and agent_system_prompt so Check 12 (required-field)
    doesn't fire when we only want to assert on Check 1/2/3.
    """
    base = {
        "workflow_id": "wf",
        "agent_system_prompt": "p",
    }
    base.update(overrides)
    return base


def test_global_tool_must_be_declared_connector():
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "connectors": {"read": [{"name": "weather"}]},
        "agent_workflow": _minimal_workflow(global_tools=["weather", "missing_tool"]),
    }
    errors = validate_cross_block(blocks, selected_channels=[])
    # 'missing_tool' should be flagged as not declared
    assert any("'missing_tool' is not declared" in e for e in errors)
    # 'weather' is declared, so it should not be flagged as missing
    assert not any("'weather' is not declared" in e for e in errors)


def test_mcp_namespaced_tools_skipped():
    """MCP tool names contain '__' and are not subject to the connector check."""
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "connectors": {"read": []},
        "agent_workflow": _minimal_workflow(global_tools=["docs__search"]),
    }
    assert validate_cross_block(blocks, selected_channels=[]) == []


def test_global_subagent_intent_overlap_rejected():
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "agent_workflow": _minimal_workflow(
            global_intents=["greeting", "shared_intent"],
            subagents=[{
                "id": "main",
                "is_terminal": True,   # terminal subagents skip the opening_phrase check
                "valid_intents": ["shared_intent"],
            }],
        ),
    }
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any("shared_intent" in e and "both" in e for e in errors)


def test_voice_selected_requires_voice_config():
    blocks = _empty_blocks()
    blocks["reach_layer"] = {"reach_layer": {"channels": {}}}
    errors = validate_cross_block(blocks, selected_channels=["voice"])
    assert any("reach_layer.channels.voice is not configured" in e for e in errors)


def test_channel_check_quiet_when_no_channels_selected():
    """Channel-related checks (6/7/8) only fire when the LLM has explicitly chosen channels."""
    blocks = _empty_blocks()
    # ac.channels and rl.channels are empty, but selected_channels is empty too
    assert validate_cross_block(blocks, selected_channels=[]) == []


def test_dignity_check_requires_questions_when_enabled():
    blocks = _empty_blocks()
    blocks["trust_layer"] = {"dignity_check": {"enabled": True, "questions": []}}
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any("dignity_check" in e and "questions is empty" in e for e in errors)


def test_set_phase_blocks_advance_when_intent_filters_drift():
    """End-to-end: set_phase via tool dispatch must reject phase advance on cross-block error."""
    from dev_kit.agent.accumulator import ConfigAccumulator
    from dev_kit.agent.tools import ToolHandler

    acc = ConfigAccumulator()
    # Skip strict per-block validation here so we can plant the inconsistency
    # without needing to satisfy every required field for both blocks.
    acc._strict_mode = False
    acc.update("agent_core", "preprocessing.nlu_processor", {"intents": ["greeting"]})
    acc.update(
        "knowledge_engine",
        "knowledge.blocks.static_knowledge_base",
        {"intent_filters": {"ask_packages": ["info"]}},  # not in NLU intents
    )
    state = {"phase": "knowledge", "phase_changed": None, "rollback_to": None,
             "project_meta": {"agent_type": "informational", "slug": "t"}}
    handler = ToolHandler(acc, state)
    result = handler.dispatch("set_phase", {"phase": "memory"})

    assert "PHASE_ADVANCE_BLOCKED" in result
    assert "ask_packages" in result
    assert state["phase_changed"] is None  # phase did NOT advance


def test_connector_param_renamed_from_tool_is_flagged():
    """Check 14 — renaming `name` → `city_name` in the connector breaks runtime."""
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "connectors": {
            "read": [{
                "name": "geocode",
                "input_schema": {"properties": {"city_name": {"type": "string"}}},
            }],
        },
    }
    blocks["action_gateway"] = {
        "tools": [{
            "id": "geocode",
            "type": "rest_api",
            "endpoints": [{
                "params": [{"name": "name", "source": "agent", "required": True}],
            }],
        }],
    }
    errors = validate_cross_block(blocks, selected_channels=[])
    # Connector exposes `city_name` not in the tool's agent params
    assert any("city_name" in e and "verbatim" in e for e in errors)
    # Tool requires `name` but the connector doesn't expose it
    assert any("missing required tool params" in e and "'name'" in e for e in errors)


def test_connector_matching_tool_passes():
    """Connector and tool agree on the agent-source param name → no error."""
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "connectors": {
            "read": [{
                "name": "geocode",
                "input_schema": {"properties": {"name": {"type": "string"}}},
            }],
        },
    }
    blocks["action_gateway"] = {
        "tools": [{
            "id": "geocode",
            "type": "rest_api",
            "endpoints": [{
                "params": [
                    {"name": "name", "source": "agent", "required": True},
                    {"name": "count", "source": "static", "value": 1},  # static, not in connector
                ],
            }],
        }],
    }
    assert validate_cross_block(blocks, selected_channels=[]) == []


def test_workflow_intent_not_in_nlu_is_flagged():
    """Check 15 — subagent valid_intents that aren't in NLU = silent expansion."""
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "preprocessing": {"nlu_processor": {"intents": ["unknown", "booking_inquiry"]}},
        "agent_workflow": _minimal_workflow(
            subagents=[{
                "id": "main",
                "is_terminal": True,
                "valid_intents": ["booking_inquiry", "tour_selected", "package_inquiry"],
            }],
        ),
    }
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any(
        "tour_selected" in e
        and "package_inquiry" in e
        and "silent expansion" in e
        for e in errors
    )


def test_workflow_intents_subset_of_nlu_passes():
    blocks = _empty_blocks()
    blocks["agent_core"] = {
        "preprocessing": {"nlu_processor": {"intents": ["unknown", "booking_inquiry", "tour_selected"]}},
        "agent_workflow": _minimal_workflow(
            subagents=[{
                "id": "main",
                "is_terminal": True,
                "valid_intents": ["booking_inquiry", "tour_selected"],
            }],
        ),
    }
    assert validate_cross_block(blocks, selected_channels=[]) == []


def test_channel_check_does_not_fire_before_language_phase():
    """Leaving overview with web/voice selected but channels not yet
    configured should NOT block phase advance — channels are configured
    during language/reach, not overview."""
    blocks = _empty_blocks()
    # selected_channels is set in overview, but ac.channels and rl.channels
    # haven't been touched yet — that's expected.
    assert validate_cross_block(blocks, selected_channels=["web", "voice"], current_phase="overview") == []


def test_channel_check_fires_when_leaving_language():
    """Once the LLM is leaving the language phase, missing
    agent_core.channels.<x> entries should be flagged."""
    blocks = _empty_blocks()
    errors = validate_cross_block(blocks, selected_channels=["web", "voice"], current_phase="language")
    assert any("agent_core.channels.web is missing" in e for e in errors)
    assert any("agent_core.channels.voice is missing" in e for e in errors)
    # Reach checks still gated until reach phase
    assert not any("reach_layer.channels.web" in e for e in errors)


def test_voice_raya_check_fires_only_from_reach_phase():
    blocks = _empty_blocks()
    blocks["agent_core"] = {"channels": {"web": {}, "voice": {}}}  # satisfy check #7
    blocks["reach_layer"] = {"reach_layer": {"channels": {"web": {}, "voice": {}}}}  # satisfy check #8
    errors = validate_cross_block(blocks, selected_channels=["voice"], current_phase="memory")
    # voice raya completeness shouldn't fire yet — leaving memory, not reach.
    assert not any("raya" in e for e in errors)
    errors = validate_cross_block(blocks, selected_channels=["voice"], current_phase="reach")
    assert any("raya" in e for e in errors)


def test_intent_filter_check_only_after_knowledge():
    blocks = _empty_blocks()
    blocks["agent_core"] = {"preprocessing": {"nlu_processor": {"intents": ["unknown"]}}}
    blocks["knowledge_engine"] = {
        "knowledge": {"blocks": {"static_knowledge_base": {"intent_filters": {"ask_x": ["doc"]}}}},
    }
    # Before knowledge phase: skip
    assert validate_cross_block(blocks, selected_channels=[], current_phase="language") == []
    # Knowledge phase or later: fire
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="knowledge")
    assert any("ask_x" in e and "not declared" in e for e in errors)


def test_no_phase_context_runs_every_check():
    """At deploy time (current_phase=None), every invariant runs."""
    blocks = _empty_blocks()
    errors = validate_cross_block(blocks, selected_channels=["voice"], current_phase=None)
    # Channel + voice raya checks both fire at deploy time
    assert any("agent_core.channels.voice is missing" in e for e in errors)
    assert any("reach_layer.channels.voice" in e for e in errors)


def test_set_phase_advances_when_consistent():
    """When everything is consistent, set_phase advances normally."""
    from dev_kit.agent.accumulator import ConfigAccumulator
    from dev_kit.agent.tools import ToolHandler

    acc = ConfigAccumulator()
    acc._strict_mode = False
    state = {"phase": "tier", "phase_changed": None, "rollback_to": None,
             "project_meta": {"agent_type": "informational", "slug": "t"}}
    handler = ToolHandler(acc, state)
    result = handler.dispatch("set_phase", {"phase": "overview"})

    assert "PHASE_ADVANCE_BLOCKED" not in result
    assert state["phase_changed"] == "overview"
