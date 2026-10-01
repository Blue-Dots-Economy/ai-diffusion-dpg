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


def test_no_phase_context_runs_every_check():
    """At deploy time (current_phase=None), every invariant runs."""
    blocks = _empty_blocks()
    errors = validate_cross_block(blocks, selected_channels=["voice"], current_phase=None)
    # Channel + voice raya checks both fire at deploy time
    assert any("agent_core.channels.voice is missing" in e for e in errors)
    assert any("reach_layer.channels.voice" in e for e in errors)


def test_no_errors_when_blocks_are_consistent():
    """When all block configs are consistent, cross-block validation returns no errors."""
    from dev_kit.agent.project_state import empty_accumulator

    blocks = empty_accumulator()
    errors = validate_cross_block(blocks, selected_channels=[])

    assert errors == [], (
        "Expected no cross-block errors for empty/default accumulator. Got: " + str(errors)
    )


# -- Recording cross-block rules ---------------------------------------------


def _blocks_with_recording(recording_override: dict) -> dict[str, dict]:
    """Return a blocks dict with the given recording config merged into voice."""
    blocks = _empty_blocks()
    blocks["reach_layer"] = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "recording": recording_override,
                },
            },
        },
    }
    return blocks


def test_recording_disabled_passes():
    """Default recording config (source=disabled) should pass validation."""
    blocks = _blocks_with_recording({"source": "disabled"})
    errors = validate_cross_block(blocks, selected_channels=[])
    assert not any("recording" in e for e in errors)


def test_recording_enabled_without_salt_fails():
    """source=vobiz without caller_id_hash_salt set must produce an error."""
    blocks = _blocks_with_recording({"source": "vobiz", "caller_id_hash_salt": ""})
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any("caller_id_hash_salt" in e for e in errors)


def test_recording_s3_backend_without_bucket_fails():
    """source=vobiz with store.backend=s3 but empty bucket must produce an error."""
    blocks = _blocks_with_recording({
        "source": "vobiz",
        "caller_id_hash_salt": "somesalt",
        "store": {"backend": "s3", "s3": {"bucket": ""}},
    })
    errors = validate_cross_block(blocks, selected_channels=[])
    assert any("bucket" in e for e in errors)


# -- tool-result persistence (agent_core <-> memory_layer) --------------------

_TR_ML = {"state": {"session": {"ttl_minutes": 60, "schema": {
    "trade": {"type": "string"}, "profile_item_id": {"type": "string"}}},
    "persistent": {"graph": {"subnodes": {"UserProfile": {"declared_fields": ["name"]}}}}}}


def _tr_ac(read=None, memory_tool=None):
    ac = {"connectors": {"read": read or [], "write": []}}
    if memory_tool:
        ac["memory_tool"] = memory_tool
    return ac


def _tr_errs(ac, ml=_TR_ML):
    return [e for e in validate_cross_block({"agent_core": ac, "memory_layer": ml}, [])
            if "tool" in e or "memory_tool" in e or "vary_on" in e or "ttl_seconds" in e]


def test_valid_tool_result_config_has_no_errors():
    ac = _tr_ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600, "vary_on": ["trade"]}}],
                {"fields": {"profile_item_id": {"scope": "session"}, "name": {"scope": "persistent"}}})
    assert _tr_errs(ac) == []


def test_session_ttl_over_session_lifetime():
    ac = _tr_ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 7200}}])
    assert any("ttl_seconds" in e for e in _tr_errs(ac))


def test_undeclared_vary_on_and_memory_fields():
    ac = _tr_ac([{"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 60, "vary_on": ["ghost"]}}],
                {"fields": {"nope": {"scope": "session"}, "nada": {"scope": "persistent"}}})
    errs = _tr_errs(ac)
    assert any("vary_on" in e for e in errs)
    assert sum("memory_tool" in e for e in errs) == 2


def _tr_agent_errs(ac):
    return [e for e in validate_cross_block({"agent_core": ac, "memory_layer": _TR_ML}, [])
            if "user-scope" in e or "collides" in e or "grounded_in" in e or "not a number" in e]


def test_user_ttl_over_cap_default_and_configured():
    read = [{"name": "r", "cache": {"scope": "user", "ttl_seconds": 90000}}]
    assert any("user-scope ttl_seconds 90000 exceeds tool_results.max_user_ttl_seconds 86400" in e
               for e in _tr_agent_errs(_tr_ac(read)))
    ac = _tr_ac(read)
    ac["tool_results"] = {"max_user_ttl_seconds": 100000}
    assert _tr_agent_errs(ac) == []


def test_memory_tool_name_collides_with_connector():
    ac = _tr_ac([{"name": "remember"}], {"fields": {"name": {"scope": "persistent"}}})
    assert any("memory_tool.name 'remember' collides with a connector" in e for e in _tr_agent_errs(ac))
    ac = _tr_ac([{"name": "r"}], {"name": "remember", "fields": {"name": {"scope": "persistent"}}})
    assert _tr_agent_errs(ac) == []


def test_grounded_in_must_name_a_connector():
    mt = {"fields": {"name": {"scope": "persistent", "grounded_in": ["ghost", "r"]}}}
    errs = _tr_agent_errs(_tr_ac([{"name": "r"}], mt))
    assert errs == ["memory_tool.fields.name.grounded_in: unknown connector 'ghost'"]


def test_non_numeric_ttl_yields_error_not_exception():
    ac = _tr_ac([{"name": "r", "cache": {"scope": "user", "ttl_seconds": "abc"}}])
    assert any("not a number" in e for e in _tr_agent_errs(ac))
    ml = {"state": {"session": {"ttl_minutes": "x"}}}
    errs = validate_cross_block({"agent_core": _tr_ac(), "memory_layer": ml}, [])
    assert any("ttl_minutes" in e and "not a number" in e for e in errs)


# -- R20: user-scope cache vs action_gateway session_mapping ------------------

def _sm_errs(scope, session_mapping):
    response = {"max_size_chars": 4000}
    if session_mapping:
        response["session_mapping"] = [{"source": "a.b", "target": "trade"}]
    blocks = {
        "agent_core": _tr_ac([{"name": "fetch_profile",
                               "cache": {"scope": scope, "ttl_seconds": 600}}]),
        "memory_layer": _TR_ML,
        "action_gateway": {"tools": [{"id": "fetch_profile", "type": "rest_api",
                                      "response": response}]},
    }
    return [e for e in validate_cross_block(blocks, []) if "session_mapping" in e]


def test_user_scope_cache_with_session_mapping_is_flagged():
    errs = _sm_errs("user", True)
    assert len(errs) == 1
    assert "fetch_profile" in errs[0] and "scope: session" in errs[0]


def test_session_scope_cache_with_session_mapping_passes():
    assert _sm_errs("session", True) == []


def test_user_scope_cache_without_session_mapping_passes():
    assert _sm_errs("user", False) == []


# -- session bootstrap / prompt_session_fields ---------------------------------

ML_B = {"state": {"session": {"ttl_minutes": 60, "schema": {
    "profile_item_id": {"type": "string"}, "stored_trade": {"type": "string"}}}}}


def _boot_errs(ac):
    return [e for e in validate_cross_block({"agent_core": ac, "memory_layer": ML_B}, [])
            if "prompt_session_fields" in e or "session_bootstrap" in e]


def test_bootstrap_cross_block_valid():
    ac = {"agent": {"prompt_session_fields": ["profile_item_id"]},
          "connectors": {"read": [{"name": "fetch_profile"}], "write": []},
          "session_bootstrap": {"steps": [{"type": "tool", "tool": "fetch_profile"}]}}
    assert _boot_errs(ac) == []


def test_bootstrap_cross_block_errors():
    ac = {"agent": {"prompt_session_fields": ["ghost"]},
          "connectors": {"read": [], "write": [{"name": "save_profile"}]},
          "session_bootstrap": {"steps": [{"type": "tool", "tool": "save_profile"}]}}
    errs = _boot_errs(ac)
    assert any("'ghost' is not a declared session field" in e for e in errs)
    assert any("'save_profile' is not a read connector" in e for e in errs)


def test_bootstrap_prompt_fields_without_session_schema():
    ac = {"agent": {"prompt_session_fields": ["x"]}}
    errs = [e for e in validate_cross_block({"agent_core": ac, "memory_layer": {}}, [])
            if "prompt_session_fields" in e]
    assert any("'x' is not a declared session field" in e for e in errs)


from dev_kit.schemas.cross_block_validation import _dialogue_act_session_mapping_rules


def _ac_da(resolves_to="selected_job_item_id"):
    return {
        "entity_to_profile_field": {"consent": "consent_response"},
        "preprocessing": {"nlu_processor": {"slots": {"consent": {}, "trade": {}}}},
        "agent_workflow": {"subagents": [{"id": "job_match", "pending": [
            {"id": "select_job", "resolves_to": resolves_to,
             "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"}}]}]},
    }


def _ag(target):
    return {"tools": [{"id": "fetch_profile", "response": {"session_mapping": [
        {"source": "items[0].x", "target": target}]}}]}


def test_session_mapping_collision_with_slot_state_key():
    errs = _dialogue_act_session_mapping_rules(_ac_da(), _ag("consent_response"))
    assert any("consent_response" in e for e in errs)


def test_session_mapping_collision_with_resolves_to():
    errs = _dialogue_act_session_mapping_rules(_ac_da(), _ag("selected_job_item_id"))
    assert any("selected_job_item_id" in e for e in errs)


def test_no_collision():
    assert _dialogue_act_session_mapping_rules(_ac_da(), _ag("stored_trade")) == []


# -- Dialogue-act intents (NLU single-mode, spec §16) -------------------------


def _kb_filters(filters: dict) -> dict:
    return {"knowledge": {"blocks": {"static_knowledge_base": {"intent_filters": filters}}}}


def _da_ac(act_intents=None, routing=None, global_routing=None, off_track=None) -> dict:
    """agent_core block with dialogue-act rows and one non-terminal subagent."""
    nlu: dict = {"act_intents": act_intents or []}
    if off_track is not None:
        nlu["off_track"] = off_track
    return {
        "preprocessing": {"nlu_processor": nlu},
        "agent_workflow": _minimal_workflow(
            subagents=[{
                "id": "main",
                "is_start": True,
                "is_terminal": True,
                "routing": routing if routing is not None else [],
            }],
            global_routing=global_routing or [],
        ),
    }


def _route(intent: str) -> dict:
    return {"intent": intent, "next_subagent_id": "main"}


def test_unrouted_act_intent_is_flagged():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(
        act_intents=[{"acts": ["affirm"], "intent": "consent_given"},
                     {"acts": ["deny"], "intent": "consent_denied"}],
        routing=[_route("consent_given"), _route("off_track")],
    )
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="workflow")
    assert (
        "agent_core.preprocessing.nlu_processor.act_intents[1]: intent 'consent_denied' "
        "is not used by any routing rule"
    ) in errors
    assert not any("'consent_given'" in e for e in errors)


def test_act_intent_routed_by_global_routing_passes():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(
        act_intents=[{"acts": ["close"], "intent": "termination"}],
        routing=[_route("off_track")],
        global_routing=[_route("termination")],
    )
    assert validate_cross_block(blocks, selected_channels=[], current_phase="workflow") == []


def test_language_switch_request_needs_no_route():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(
        act_intents=[{"acts": ["request_change"], "topic": "language", "intent": "language_switch_request"}],
        routing=[_route("off_track")],
    )
    assert validate_cross_block(blocks, selected_channels=[], current_phase="workflow") == []


def test_unrouted_off_track_intent_is_flagged_when_subagents_exist():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(routing=[_route("*")])
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="workflow")
    assert (
        "agent_core.preprocessing.nlu_processor.off_track.intent 'off_track' "
        "is not used by any routing rule"
    ) in errors


def test_custom_off_track_intent_is_checked():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(routing=[_route("off_track")], off_track={"intent": "drift"})
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="workflow")
    assert any("off_track.intent 'drift' is not used by any routing rule" in e for e in errors)


def test_off_track_check_skipped_without_subagents():
    blocks = _empty_blocks()
    blocks["agent_core"] = {"agent_workflow": _minimal_workflow(subagents=[])}
    assert validate_cross_block(blocks, selected_channels=[], current_phase="workflow") == []


def test_routing_checks_wait_for_workflow_phase():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(act_intents=[{"acts": ["affirm"], "intent": "yes"}])
    assert validate_cross_block(blocks, selected_channels=[], current_phase="language") == []


def test_intent_filters_must_name_a_derivable_intent():
    blocks = _empty_blocks()
    blocks["agent_core"] = _da_ac(
        act_intents=[{"acts": ["ask"], "intent": "faq"}],
        routing=[_route("faq"), _route("off_track")],
    )
    blocks["knowledge_engine"] = _kb_filters({
        "faq": ["a"], "any_input": ["b"], "off_track": ["c"],
        "language_switch_request": ["d"], "ask_packages": ["e"],
    })
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="knowledge")
    flagged = [e for e in errors if "intent_filters" in e]
    assert len(flagged) == 1
    assert "'ask_packages'" in flagged[0]
    assert "act_intents" in flagged[0]


def test_intent_filters_check_only_after_knowledge():
    blocks = _empty_blocks()
    blocks["agent_core"] = {"preprocessing": {"nlu_processor": {"act_intents": [
        {"acts": ["ask"], "intent": "faq"}]}}}
    blocks["knowledge_engine"] = _kb_filters({"ask_x": ["doc"]})
    assert validate_cross_block(blocks, selected_channels=[], current_phase="language") == []
    errors = validate_cross_block(blocks, selected_channels=[], current_phase="knowledge")
    assert any("'ask_x'" in e for e in errors)


def test_intent_filters_check_waits_for_hand_authored_act_intents():
    """No act_intents yet (they are hand-authored) -> nothing to check against."""
    blocks = _empty_blocks()
    blocks["knowledge_engine"] = _kb_filters({"ask_x": ["doc"]})
    assert validate_cross_block(blocks, selected_channels=[], current_phase="knowledge") == []
