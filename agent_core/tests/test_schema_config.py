"""Tests for Agent Core MergedConfig strict schema validation."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from src.schema.config import (
    AssemblyMode,
    MergedConfig,
    RoutingOperator,
    ServerConfig,
    SpecialHandler,
    _DIALOGUE_ACTS,
)


def _minimal_valid_config() -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 8000},
        "agent": {
            "primary_model": "claude-haiku-4-5-20251001",
            "fallback_model": "claude-sonnet-4-6-20250514",
            "timeout_ms": 10000,
            "retry_attempts": 2,
            "retry_backoff_seconds": [0, 0.5, 1.0],
            "max_tool_rounds": 3,
            "ask_for_consent": True,
            "consent_prompt": "May I store your data?",
        },
        "conversation": {
            "blocked_message": "blocked",
            "escalation_message": "escalate",
        },
        "connectors": {
            "read": [
                {
                    "name": "onest_market_lookup",
                    "description": "search jobs",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query_text": {"type": "string", "description": "x"}
                        },
                        "required": ["query_text"],
                        "additionalProperties": False,
                    },
                    "invocation_rules": {"call_when": "user asks for jobs"},
                }
            ],
            "internal": [
                {
                    "name": "knowledge_retrieval",
                    "route": "knowledge_engine",
                    "description": "RAG",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }
            ],
        },
        "preprocessing": {
            "language_normalisation": {
                "model": "claude-haiku-4-5-20251001",
                "default_language": "hindi",
                "supported_languages": ["hindi", "english"],
            },
            "nlu_processor": {
                "model": "claude-sonnet-4-6-20250514",
                "signal_intents": {"pay_disappointment": "objection"},
            },
        },
        "entity_to_profile_field": {
            "name": "name",
            "location": "location",
        },
        "hitl": {"response_message": "connecting you"},
        "agent_workflow": {
            "workflow_id": "blue-dots",
            "version": "1.0.0",
            "agent_system_prompt": "You are Blue Dots.",
            "subagents": [
                {
                    "id": "entry",
                    "is_start": True,
                    "system_prompt": "entry prompt",
                    "routing": [
                        {"intent": "off_track", "next_subagent_id": "entry"},
                        {"intent": "*", "next_subagent_id": "end"},
                    ],
                },
                {"id": "end", "is_terminal": True, "routing": []},
            ],
        },
        "channels": {
            "voice": {
                "system_prompt_suffix": "voice suffix",
                "terminal_word": "Goodbye",
                "output_contract": {
                    "default_language": "hindi",
                    "languages": {"hindi": {"numbers": "words"}, "english": {"numbers": "words"}},
                },
                "turn_assembler": {
                    "silence_trigger": {"silence_ms": 400},
                    "max_wait_ceiling": {"max_wait_ms": 8000},
                },
            }
        },
        "reach_layer": {
            "turn_assembler": {
                "silence_trigger": {"silence_ms": 400},
                "max_wait_ceiling": {"max_wait_ms": 8000},
            }
        },
        "ke_client": {"endpoint": "http://ke:8001/retrieve", "timeout_ms": 30000},
        "memory_client": {"endpoint": "http://mem:8002", "timeout_ms": 3000},
        "trust_client": {"endpoint": "http://trust:8003", "timeout_ms": 2000},
        "learning_client": {"endpoint": "http://obs:8004", "timeout_ms": 2000},
        "action_gateway_client": {"endpoint": "http://ag:9999", "timeout_ms": 5000},
        "observability": {"domain": "blue-dots"},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.agent.primary_model == "claude-haiku-4-5-20251001"
    assert cfg.agent.max_tool_rounds == 3
    assert cfg.preprocessing.nlu_processor.signal_intents["pay_disappointment"] == "objection"
    assert cfg.entity_to_profile_field["location"] == "location"
    assert cfg.hitl.response_message == "connecting you"
    assert len(cfg.agent_workflow.subagents) == 2
    assert cfg.agent_workflow.subagents[0].is_start is True
    assert cfg.channels.voice.terminal_word == "Goodbye"


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({})
    assert cfg.server.port == 8000
    assert cfg.agent.max_tool_rounds == 3
    assert cfg.connectors.read == []
    assert cfg.entity_to_profile_field == {}
    assert cfg.agent_workflow.subagents == []
    assert cfg.reach_layer.turn_assembler.max_wait_ceiling.max_wait_ms == 0


def test_rejects_chat_channel_removed_in_this_pr():
    config = _minimal_valid_config()
    config["channels"]["chat"] = {"system_prompt_suffix": ""}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "chat" in str(exc.value)


def test_rejects_conversation_max_turns_removed_in_this_pr():
    config = _minimal_valid_config()
    config["conversation"]["max_turns"] = 20
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "max_turns" in str(exc.value)


def test_rejects_language_normalisation_provider_removed_in_this_pr():
    config = _minimal_valid_config()
    config["preprocessing"]["language_normalisation"]["provider"] = "llm_native"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "provider" in str(exc.value)


def test_rejects_unknown_top_level_key():
    config = _minimal_valid_config()
    config["typo_section"] = {}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "typo_section" in str(exc.value)


def test_rejects_unknown_key_on_agent():
    config = _minimal_valid_config()
    config["agent"]["primary_mdoel"] = "x"  # typo
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "primary_mdoel" in str(exc.value)


def test_rejects_unknown_key_on_subagent():
    config = _minimal_valid_config()
    config["agent_workflow"]["subagents"][0]["prompt"] = "x"  # should be system_prompt
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "prompt" in str(exc.value)


def test_rejects_unknown_key_on_routing_rule():
    config = _minimal_valid_config()
    config["agent_workflow"]["subagents"][0]["routing"][0]["priority"] = 1
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "priority" in str(exc.value)


def test_rejects_unknown_key_on_connector():
    config = _minimal_valid_config()
    config["connectors"]["read"][0]["timeout_ms"] = 5000  # belongs in action_gateway
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "timeout_ms" in str(exc.value)


def test_rejects_invalid_routing_operator_enum():
    config = _minimal_valid_config()
    config["agent_workflow"]["subagents"][0]["routing"][0]["conditions"] = [
        {"field": "x", "operator": "equals", "value": 1}  # should be "eq"
    ]
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_special_handler_enum():
    config = _minimal_valid_config()
    config["agent_workflow"]["subagents"][0]["special_handler"] = "escalation"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_timeout():
    config = _minimal_valid_config()
    config["agent"]["timeout_ms"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_out_of_range_confidence():
    config = _minimal_valid_config()
    config["preprocessing"]["nlu_processor"]["user_state_confidence_threshold"] = 1.5
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_server_port():
    with pytest.raises(ValidationError):
        ServerConfig(port=0)
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_entity_to_profile_field_accepts_domain_keys():
    """Open-map: domain-defined entity names."""
    cfg = MergedConfig.validate_full({
        "entity_to_profile_field": {
            "trade_or_stream": "trade",
            "income_urgency": "income_urgency",
        }
    })
    assert cfg.entity_to_profile_field["trade_or_stream"] == "trade"


def test_signal_intents_accepts_domain_keys():
    """Open-map: domain-defined intent names."""
    cfg = MergedConfig.validate_full({
        "preprocessing": {
            "nlu_processor": {
                "signal_intents": {
                    "pay_disappointment": "objection",
                    "counsellor_request": "escalation_signal",
                }
            }
        }
    })
    assert cfg.preprocessing.nlu_processor.signal_intents["counsellor_request"] == "escalation_signal"


def test_routing_condition_all_operators_accepted():
    for op in ["eq", "not_eq", "gt", "lt", "in", "contains"]:
        cfg = MergedConfig.validate_full({
            "agent_workflow": {
                "global_routing": [{"intent": "off_track", "next_subagent_id": "s"}],
                "subagents": [
                    {
                        "id": "s",
                        "routing": [
                            {
                                "intent": "*",
                                "next_subagent_id": "e",
                                "conditions": [{"field": "x", "operator": op, "value": 1}],
                            }
                        ],
                    }
                ]
            }
        })
        assert cfg.agent_workflow.subagents[0].routing[0].conditions[0].operator.value == op


def test_enum_exports_are_usable():
    assert RoutingOperator.eq.value == "eq"
    assert SpecialHandler.hitl.value == "hitl"
    assert AssemblyMode.streaming.value == "streaming"


def test_agent_workflow_config_global_tools_default_empty():
    from src.schema.config import AgentWorkflowConfig
    cfg = AgentWorkflowConfig()
    assert cfg.global_tools == []


def test_agent_workflow_config_global_tools_accepts_list():
    from src.schema.config import AgentWorkflowConfig
    cfg = AgentWorkflowConfig(
        workflow_id="w",
        version="1.0.0",
        global_tools=["get_profile", "onest_market_lookup"],
    )
    assert cfg.global_tools == ["get_profile", "onest_market_lookup"]


def test_agent_workflow_config_rejects_unknown_field():
    """extra='forbid' must still reject typos."""
    from src.schema.config import AgentWorkflowConfig
    with pytest.raises(ValidationError):
        AgentWorkflowConfig(workflow_id="w", version="1.0.0", globall_tools=["x"])


class TestAgentProviderAndFeatures:
    """PR2 — agent.provider and agent.features schema additions."""

    def _base_agent(self) -> dict:
        return {"primary_model": "x", "fallback_model": "y"}

    def test_provider_defaults_to_anthropic(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate(self._base_agent())
        assert cfg.provider == "anthropic"

    def test_provider_accepts_known_values(self):
        from src.schema.config import AgentConfig
        for p in ("anthropic", "openai"):
            cfg = AgentConfig.model_validate({**self._base_agent(), "provider": p})
            assert cfg.provider == p

    def test_provider_rejects_unknown_values(self):
        from pydantic import ValidationError
        from src.schema.config import AgentConfig
        with pytest.raises(ValidationError):
            AgentConfig.model_validate({**self._base_agent(), "provider": "wat"})

    def test_features_default_all_none(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate(self._base_agent())
        assert cfg.features.prompt_cache is None
        assert cfg.features.streaming is None
        assert cfg.features.image_input is None

    def test_features_accepts_partial(self):
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate({
            **self._base_agent(),
            "features": {"prompt_cache": False},
        })
        assert cfg.features.prompt_cache is False
        assert cfg.features.streaming is None

    def test_features_rejects_unknown_keys(self):
        from pydantic import ValidationError
        from src.schema.config import AgentConfig
        with pytest.raises(ValidationError):
            AgentConfig.model_validate({
                **self._base_agent(),
                "features": {"made_up": True},
            })

    def test_features_null_coerces_to_default(self):
        """Regression: YAML parses ``features:`` with all sub-keys
        commented out as ``None``, and the schema must accept that as
        equivalent to an absent block (use provider capabilities).
        """
        from src.schema.config import AgentConfig
        cfg = AgentConfig.model_validate({**self._base_agent(), "features": None})
        assert cfg.features.prompt_cache is None
        assert cfg.features.streaming is None
        assert cfg.features.image_input is None

    def test_nlu_processor_accepts_provider_override(self):
        """Each helper may declare its own provider independently of agent.provider."""
        from src.schema.config import NLUProcessorConfig
        cfg = NLUProcessorConfig.model_validate(
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
        )
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-haiku-4-5-20251001"

    def test_language_normalisation_accepts_provider_override(self):
        from src.schema.config import LanguageNormalisationConfig
        cfg = LanguageNormalisationConfig.model_validate(
            {"provider": "openai", "model": "gpt-4o-mini-2024-07-18"}
        )
        assert cfg.provider == "openai"

    def test_helper_provider_rejects_unknown_value(self):
        from pydantic import ValidationError
        from src.schema.config import NLUProcessorConfig
        with pytest.raises(ValidationError):
            NLUProcessorConfig.model_validate({"provider": "wat", "model": "x"})

    def test_helper_provider_defaults_to_none_meaning_inherit(self):
        from src.schema.config import NLUProcessorConfig
        cfg = NLUProcessorConfig.model_validate({"model": "x"})
        assert cfg.provider is None

    def test_dpg_yaml_loads_under_full_validation(self):
        """Regression: the shipped dev-kit/dpg/agent_core.yaml must pass
        full schema validation. Caught a production-startup ValidationError
        where ``agent.features`` parsed to None from the commented-out block.
        """
        import yaml
        from pathlib import Path
        from src.schema.config import MergedConfig

        repo_root = Path(__file__).resolve().parents[2]
        dpg = yaml.safe_load(
            (repo_root / "dev-kit" / "dpg" / "agent_core.yaml").read_text()
        ) or {}
        domain = yaml.safe_load(
            (repo_root / "dev-kit" / "configs" / "blue-dots" / "agent_core.yaml").read_text()
        ) or {}
        merged: dict = {**dpg}
        for k, v in domain.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        # Should not raise.
        MergedConfig.validate_full(merged)


# ---------------------------------------------------------------------------
# entity_persistence — the orchestrator has always read this key, but it was
# absent from the strict schema, so setting it made the service fail to boot
# with "Extra inputs are not permitted". The knob was unreachable.
# ---------------------------------------------------------------------------


def test_entity_persistence_defaults_to_persistent():
    """Historical behaviour is the default: entities go to the profile store."""
    from src.schema.config import MergedConfig
    assert MergedConfig().entity_persistence.scope == "persistent"


def test_entity_persistence_accepts_session_scope():
    """A domain can keep NLU entities in session, off the profile store."""
    from src.schema.config import MergedConfig
    cfg = MergedConfig(entity_persistence={"scope": "session"})
    assert cfg.entity_persistence.scope == "session"


def test_entity_persistence_rejects_an_unknown_scope():
    from pydantic import ValidationError
    from src.schema.config import MergedConfig
    with pytest.raises(ValidationError):
        MergedConfig(entity_persistence={"scope": "memgraph"})


def test_entity_persistence_rejects_an_unknown_key():
    from pydantic import ValidationError
    from src.schema.config import MergedConfig
    with pytest.raises(ValidationError):
        MergedConfig(entity_persistence={"scope": "session", "ttl": 60})


# ---------------------------------------------------------------------------
# Tool-result persistence: cache / invalidates / tool_results / memory_tool
# ---------------------------------------------------------------------------


def _with(conn_read=None, conn_write=None, **top):
    cfg = _minimal_valid_config()
    cfg.setdefault("connectors", {})
    cfg["connectors"]["read"] = conn_read or []
    cfg["connectors"]["write"] = conn_write or []
    cfg.update(top)
    return cfg


_READ = {"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 1800, "keep": ["items"]}}
_WRITE = {"name": "save_profile", "invalidates": ["fetch_profile"]}
_MT = {"name": "remember", "fields": {"profile_item_id": {
    "scope": "session", "grounded_in": ["fetch_profile", "save_profile"]}}}


def test_valid_cache_invalidates_memory_tool():
    MergedConfig.validate_full(_with([_READ], [_WRITE], memory_tool=_MT))


_INVALID_CASES = [
    pytest.param(
        _with([], [{"name": "save_profile", "cache": {"scope": "user", "ttl_seconds": 60}}]),
        "only allowed on read connectors", id="cache-on-write"),
    pytest.param(
        {**_with([]), "connectors": {"identity": [{"name": "who", "cache": {"scope": "user", "ttl_seconds": 60}}]}},
        "only allowed on read connectors", id="cache-on-identity"),
    pytest.param(
        _with([{"name": "fetch_profile", "invalidates": ["x"]}], []),
        "only allowed on write connectors", id="invalidates-on-read"),
    pytest.param(
        {**_with([]), "connectors": {"identity": [{"name": "who", "invalidates": ["x"]}]}},
        "only allowed on write connectors", id="invalidates-on-identity"),
    pytest.param(
        _with([_READ], [{"name": "save_profile", "invalidates": ["nope"]}]),
        "invalidates unknown read connector 'nope'", id="invalidates-unknown-target"),
    pytest.param(
        _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 90000}}], []),
        "exceeds tool_results.max_user_ttl_seconds", id="user-ttl-over-cap"),
    pytest.param(
        _with([{"name": "fetch_profile", "cache": {"scope": "agent", "ttl_seconds": 60}}], []),
        "Input should be 'session' or 'user'", id="bad-cache-scope"),
    pytest.param(
        _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 0}}], []),
        "greater than 0", id="zero-ttl"),
    pytest.param(
        _with([_READ], [_WRITE], memory_tool={"fields": {"f": {"scope": "session", "grounded_in": ["ghost"]}}}),
        "unknown connector 'ghost'", id="grounded-in-unknown"),
    pytest.param(
        _with([_READ], [_WRITE], memory_tool={"name": "fetch_profile", "fields": {"f": {"scope": "session"}}}),
        "collides with a connector", id="memory-tool-name-collision"),
    pytest.param(
        _with([_READ], [_WRITE], memory_tool={"fields": {}}),
        "at least 1 item", id="memory-tool-no-fields"),
]


@pytest.mark.parametrize("cfg,match", _INVALID_CASES)
def test_invalid_tool_result_configs_rejected(cfg, match):
    with pytest.raises(ValidationError, match=match):
        MergedConfig.validate_full(cfg)


def test_user_ttl_cap_is_configurable():
    cfg = _with([{"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 90000}}], [],
                tool_results={"max_user_ttl_seconds": 100000})
    MergedConfig.validate_full(cfg)


# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------


def _boot(steps=None, read=None, write=None, identity=None, **agent):
    cfg = copy.deepcopy(_minimal_valid_config())
    cfg.setdefault("connectors", {})
    cfg["connectors"]["read"] = read if read is not None else [{"name": "fetch_profile"}]
    cfg["connectors"]["write"] = write or []
    if identity is not None:
        cfg["connectors"]["identity"] = identity
    if steps is not None:
        cfg["session_bootstrap"] = {"steps": steps}
    if agent:
        cfg.setdefault("agent", {}).update(agent)
    return cfg


def test_valid_bootstrap_and_prompt_session_fields():
    cfg = _boot([{"type": "tool", "tool": "fetch_profile"}],
                prompt_session_fields=["profile_item_id", "stored_trade"])
    m = MergedConfig.validate_full(cfg)
    assert m.session_bootstrap.timeout_ms == 1500
    assert m.session_bootstrap.steps[0].args == {} and m.session_bootstrap.steps[0].requires_consent is False
    assert m.agent.prompt_session_fields == ["profile_item_id", "stored_trade"]


def test_no_bootstrap_is_default():
    assert MergedConfig.validate_full(_boot()).session_bootstrap is None


@pytest.mark.parametrize("cfg,match", [
    pytest.param(_boot([{"type": "tool", "tool": "save_profile"}], write=[{"name": "save_profile"}]),
                 "is not a read connector", id="write-connector"),
    pytest.param(_boot([{"type": "tool", "tool": "ghost"}]), "is not a read connector", id="unknown-connector"),
    pytest.param(_boot([{"type": "tool", "tool": "verify_me"}], identity=[{"name": "verify_me"}]),
                 "is not a read connector", id="identity-connector"),
    pytest.param(_boot([{"type": "set", "tool": "fetch_profile"}]), "Input should be 'tool'", id="bad-type"),
    pytest.param(_boot([]), "at least 1", id="no-steps"),
    pytest.param({**_boot([{"type": "tool", "tool": "fetch_profile"}]),
                  "session_bootstrap": {"timeout_ms": 0, "steps": [{"type": "tool", "tool": "fetch_profile"}]}},
                 "greater than 0", id="zero-timeout"),
    pytest.param(_boot([{"type": "tool", "tool": "fetch_profile", "extra": 1}]), "Extra inputs are not permitted", id="extra-key"),
])
def test_invalid_bootstrap_rejected(cfg, match):
    with pytest.raises(ValidationError, match=match):
        MergedConfig.validate_full(cfg)


# ---------------------------------------------------------------------------
# dialogue-act NLU blocks + SubAgent.pending
# ---------------------------------------------------------------------------

def _da_base() -> dict:
    """Minimal merged config with dialogue-act NLU blocks that validates."""
    return {
        "connectors": {"read": [{"name": "fetch_jobs", "description": "jobs",
                                 "cache": {"scope": "session", "ttl_seconds": 600}}]},
        "preprocessing": {"nlu_processor": {
            "topics": ["salary", "search"],
            "slots": {"age": {"type": "int", "min": 14, "max": 80, "accept_when_pending": ["age"]},
                      "consent": {"type": "enum", "values": ["granted", "declined"],
                                  "accept_when_pending": ["consent"]}},
            "act_intents": [
                {"acts": ["affirm"], "pending": "submit_confirm", "relation": "answers_pending",
                 "intent": "apply_now"},
                {"acts": ["close"], "intent": "termination_intent", "gated": True},
            ],
            "termination_gate": {"any_of": [{"pending": "closing_offer"}]},
            "off_track": {"threshold": 3, "intent": "off_track"},
        }},
        "agent_workflow": {
            "global_routing": [{"intent": "termination_intent", "next_subagent_id": "ended"}],
            "subagents": [
                {"id": "opening", "is_start": True,
                 "pending": [{"id": "consent", "when": [{"field": "consent_response", "operator": "in",
                                                         "value": [None, ""]}]},
                             {"id": "age"}],
                 "routing": [{"intent": "off_track", "next_subagent_id": "recovery"}]},
                {"id": "apply_confirm",
                 "pending": [{"id": "submit_confirm"}, {"id": "closing_offer"}],
                 "routing": [{"intent": "apply_now", "next_subagent_id": "ended"}]},
                {"id": "job_match",
                 "pending": [{"id": "select_job",
                              "options_from": {"tool": "fetch_jobs", "fields": ["role"], "id_field": "item_id"},
                              "resolves_to": "selected_job_item_id"}]},
                {"id": "recovery"}, {"id": "ended", "is_terminal": True},
            ],
        },
    }


def test_dialogue_act_minimal_config_validates():
    cfg = MergedConfig.validate_full(_da_base())
    nlu = cfg.preprocessing.nlu_processor
    assert nlu.timeout_ms == 2500 and nlu.retry_attempts == 2
    assert cfg.agent_workflow.subagents[2].pending[0].options_from.tool == "fetch_jobs"


def test_acts_constant_is_the_framework_list():
    assert _DIALOGUE_ACTS == ("affirm", "deny", "acknowledge", "provide_info", "correct", "select",
                              "ask", "request_change", "repeat", "hold", "close", "other")


@pytest.mark.parametrize("mutate, match", [
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(acts=["shout"]),
     "unknown act"),
    (lambda c: c["preprocessing"]["nlu_processor"]["slots"]["age"].update(accept_when_pending=["nope"]),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(pending="nope"),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["termination_gate"]["any_of"].append({"pending": "nope"}),
     "undeclared pending id 'nope'"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"][0].update(intent="unrouted"),
     "intent 'unrouted' is not used by any routing rule"),
    (lambda c: c["preprocessing"]["nlu_processor"]["off_track"].update(intent="nowhere"),
     "off_track.intent 'nowhere' is not used by any routing rule"),
    (lambda c: c["connectors"]["read"][0].pop("cache"),
     "options_from.tool 'fetch_jobs' has no cache policy"),
    (lambda c: c["preprocessing"]["nlu_processor"]["act_intents"].append(
        {"acts": ["ask"], "topic": "weather", "intent": "apply_now"}),
     "topic 'weather' is not in topics"),
    (lambda c: c["preprocessing"]["nlu_processor"]["slots"].update(bad={"type": "enum"}),
     "enum slot needs values"),
    (lambda c: c["agent_workflow"]["subagents"][2]["pending"][0].pop("options_from"),
     "resolves_to requires options_from"),
])
def test_dialogue_act_rejections(mutate, match):
    cfg = copy.deepcopy(_da_base())
    mutate(cfg)
    with pytest.raises((ValidationError, ValueError), match=match):
        MergedConfig.validate_full(cfg)


def test_memory_tool_field_collision_rejected():
    cfg = copy.deepcopy(_da_base())
    cfg["connectors"]["internal"] = []
    cfg["memory_tool"] = {"name": "remember", "fields": {
        "selected_job_item_id": {"scope": "session", "description": "x"}}}
    with pytest.raises(ValueError, match="collides with memory_tool field"):
        MergedConfig.validate_full(cfg)


def test_framework_handled_intent_needs_no_routing_rule():
    cfg = copy.deepcopy(_da_base())
    nlu = cfg["preprocessing"]["nlu_processor"]
    nlu["topics"].append("language")
    nlu["act_intents"].append(
        {"acts": ["request_change"], "topic": "language", "intent": "language_switch_request"})
    MergedConfig.validate_full(cfg)


def test_rejects_semantic_gate_in_turn_assembler():
    cfg = _minimal_valid_config()
    cfg["channels"]["voice"]["turn_assembler"]["semantic_gate"] = {"enabled": False}
    with pytest.raises(ValidationError, match="semantic_gate"):
        MergedConfig.validate_full(cfg)


# ---------------------------------------------------------------------------
# Removed legacy NLU keys (spec §16): a config still carrying one fails startup
# ---------------------------------------------------------------------------

def _set_nlu(key, value):
    def _m(c):
        c["preprocessing"]["nlu_processor"][key] = value
    return _m


@pytest.mark.parametrize("mutate, key", [
    pytest.param(_set_nlu("mode", "dialogue_act"), "mode", id="nlu.mode"),
    pytest.param(_set_nlu("intents", ["greeting"]), "intents", id="nlu.intents"),
    pytest.param(_set_nlu("entities", ["name"]), "entities", id="nlu.entities"),
    pytest.param(_set_nlu("domain_instruction", "x"), "domain_instruction", id="nlu.domain_instruction"),
    pytest.param(_set_nlu("confidence_threshold", 0.5), "confidence_threshold", id="nlu.confidence_threshold"),
    pytest.param(_set_nlu("sentiment_classes", ["neutral"]), "sentiment_classes", id="nlu.sentiment_classes"),
    pytest.param(_set_nlu("log_raw_response_max_chars", 500), "log_raw_response_max_chars",
                 id="nlu.log_raw_response_max_chars"),
    pytest.param(lambda c: c["agent_workflow"]["subagents"][0].update(valid_intents=["apply_now"]),
                 "valid_intents", id="subagent.valid_intents"),
    pytest.param(lambda c: c["agent_workflow"].update(global_intents=["termination_intent"]),
                 "global_intents", id="workflow.global_intents"),
])
def test_removed_intent_mode_key_rejected(mutate, key):
    cfg = copy.deepcopy(_da_base())
    mutate(cfg)
    with pytest.raises(ValidationError, match=key):
        MergedConfig.validate_full(cfg)


def test_dialogue_act_rules_enforced_without_mode_key():
    cfg = copy.deepcopy(_da_base())
    cfg["preprocessing"]["nlu_processor"]["act_intents"][0]["intent"] = "unrouted"
    with pytest.raises((ValidationError, ValueError), match="intent 'unrouted' is not used"):
        MergedConfig.validate_full(cfg)


def test_off_track_intent_must_be_routed_when_a_workflow_exists():
    """The off-track rule now runs on every config; an empty workflow has nothing to route."""
    MergedConfig.validate_full({})
    cfg = _minimal_valid_config()
    cfg["agent_workflow"]["subagents"][0]["routing"].pop(0)
    with pytest.raises(ValidationError, match="off_track.intent 'off_track' is not used"):
        MergedConfig.validate_full(cfg)


# ---------------------------------------------------------------------------
# identity + handoff blocks
# ---------------------------------------------------------------------------

_IDENT = {"name": "ब्लू डॉट्स सहायक", "operator": "Blue Dots",
          "disclosure": "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।", "no_handoff_line": "अभी कोई इंसान उपलब्ध नहीं है।"}
_HANDOFF = {"lines": {"delivered": "d", "failed": "f", "already": "a"}}


def test_identity_optional_and_defaults():
    cfg = MergedConfig.validate_full({**_minimal_valid_config(), "identity": _IDENT})
    assert cfg.identity.human_handoff == "none" and cfg.identity.kind == "ai_assistant"
    assert MergedConfig.validate_full(_minimal_valid_config()).identity is None
    assert MergedConfig.validate_full(_minimal_valid_config()).handoff is None


def test_identity_rejects_empty_disclosure():
    with pytest.raises(ValidationError):
        MergedConfig.validate_full({**_minimal_valid_config(), "identity": {**_IDENT, "disclosure": ""}})


def test_request_requires_handoff_block_and_subagent():
    with pytest.raises(ValidationError, match="human_handoff=request"):
        MergedConfig.validate_full({**_minimal_valid_config(), "identity": {**_IDENT, "human_handoff": "request"}})


def test_request_needs_handoff_subagent_even_with_block():
    with pytest.raises(ValidationError, match="human_handoff=request"):
        MergedConfig.validate_full({**_minimal_valid_config(),
                                    "identity": {**_IDENT, "human_handoff": "request"}, "handoff": _HANDOFF})


def test_request_accepted_with_block_and_subagent():
    base = copy.deepcopy(_minimal_valid_config())
    wf = base.setdefault("agent_workflow", {})
    wf["subagents"] = [*wf.get("subagents", []), {"id": "handoff", "name": "Handoff"}]
    cfg = MergedConfig.validate_full({**base, "identity": {**_IDENT, "human_handoff": "request"},
                                      "handoff": _HANDOFF})
    assert cfg.handoff.summary_turns == 6


def test_handoff_summary_turns_bounds():
    for bad in (0, 21):
        with pytest.raises(ValidationError):
            MergedConfig.validate_full({**_minimal_valid_config(), "handoff": {**_HANDOFF, "summary_turns": bad}})


# ---------------------------------------------------------------------------
# Spec D: output_contract, result_shaping, history_turns, state_fields
# ---------------------------------------------------------------------------

_CONTRACT = {
    "default_language": "hindi",
    "languages": {"hindi": {"script": "devanagari", "numbers": "words", "rules": ["Devanagari only."]},
                  "english": {"script": "latin", "numbers": "words"}},
    "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True},
}
_SHAPING = {
    "drop_when": [{"field": "role", "operator": "contains", "value": "|"}],
    "sort": [{"field": "match_score", "order": "desc"}],
    "spoken": {"salary_spoken": {"format": "range_thousands", "from": ["salary_min", "salary_max"], "unit": "per_month"}},
    "strip_numbers_in": ["location"],
}


def _with_language(cfg, default="hindi", supported=("english", "hindi")):
    cfg.setdefault("preprocessing", {})["language_normalisation"] = {
        "enabled": False, "default_language": default, "supported_languages": list(supported)}
    return cfg


def test_output_contract_and_shaping_accepted():
    cfg = _with_language(copy.deepcopy(_minimal_valid_config()))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": _CONTRACT}
    cfg["connectors"]["read"][0]["result_shaping"] = _SHAPING
    cfg.setdefault("agent", {}).update({"history_turns": 3, "state_fields": ["applications_submitted"]})
    MergedConfig.validate_full(cfg)


def test_tts_rules_rejected():
    cfg = copy.deepcopy(_minimal_valid_config())
    cfg.setdefault("channels", {})["voice"] = {"tts_rules": {"numbers": "words"}}
    with pytest.raises(ValidationError, match="tts_rules"):
        MergedConfig.validate_full(cfg)


def test_contract_default_language_must_be_declared():
    cfg = _with_language(copy.deepcopy(_minimal_valid_config()))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": {**_CONTRACT, "default_language": "tamil"}}
    with pytest.raises(ValidationError, match="default_language"):
        MergedConfig.validate_full(cfg)


def test_contract_must_cover_supported_languages():
    cfg = _with_language(copy.deepcopy(_minimal_valid_config()), supported=("english", "hindi", "kannada"))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": _CONTRACT}
    with pytest.raises(ValidationError, match="kannada"):
        MergedConfig.validate_full(cfg)


def test_numbers_words_needs_a_converter():
    cfg = _with_language(copy.deepcopy(_minimal_valid_config()), default="kannada", supported=("kannada",))
    cfg["channels"]["voice"].pop("output_contract")  # keep the fixture's contract out of the way
    c = {"default_language": "kannada", "languages": {"kannada": {"script": "any", "numbers": "words"}}}
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": c}
    with pytest.raises(ValidationError, match="spoken-number converter"):
        MergedConfig.validate_full(cfg)


def test_spoken_fields_need_a_supported_default_language():
    cfg = _with_language(copy.deepcopy(_minimal_valid_config()), default="kannada", supported=("kannada",))
    cfg["channels"]["voice"].pop("output_contract")  # keep the fixture's contract out of the way
    cfg["connectors"]["read"][0]["result_shaping"] = _SHAPING
    with pytest.raises(ValidationError, match="spoken"):
        MergedConfig.validate_full(cfg)


def test_history_turns_non_negative():
    cfg = copy.deepcopy(_minimal_valid_config())
    cfg.setdefault("agent", {})["history_turns"] = -1
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(cfg)
