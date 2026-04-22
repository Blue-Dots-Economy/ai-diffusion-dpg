"""Tests for Agent Core MergedConfig strict schema validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schema.config import (
    AssemblyMode,
    MergedConfig,
    RoutingOperator,
    ServerConfig,
    SpecialHandler,
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
                "intents": ["greeting", "unknown"],
                "entities": ["name", "location"],
                "signal_intents": {"pay_disappointment": "objection"},
            },
        },
        "entity_to_profile_field": {
            "name": "name",
            "location": "location",
        },
        "hitl": {"response_message": "connecting you"},
        "agent_workflow": {
            "workflow_id": "kkb",
            "version": "1.0.0",
            "agent_system_prompt": "You are KKB.",
            "subagents": [
                {
                    "id": "entry",
                    "is_start": True,
                    "system_prompt": "entry prompt",
                    "routing": [
                        {"intent": "*", "next_subagent_id": "end"},
                    ],
                },
                {"id": "end", "is_terminal": True, "routing": []},
            ],
            "tool_result_mappings": {
                "onest_market_lookup": {
                    "journey_event_label": "Role",
                    "result_list_key": "data.items",
                    "field_map": {"role_id": "job.job_id"},
                }
            },
        },
        "channels": {
            "voice": {
                "system_prompt_suffix": "voice suffix",
                "terminal_word": "Goodbye",
                "tts_rules": {"numbers": "words"},
                "turn_assembler": {
                    "silence_trigger": {"silence_ms": 400},
                    "max_wait_ceiling": {"max_wait_ms": 8000},
                },
            }
        },
        "reach_layer": {
            "turn_assembler": {
                "semantic_gate": {"enabled": True, "confidence_threshold": 0.75},
                "silence_trigger": {"silence_ms": 400},
                "max_wait_ceiling": {"max_wait_ms": 8000},
            }
        },
        "ke_client": {"endpoint": "http://ke:8001/retrieve", "timeout_ms": 30000},
        "memory_client": {"endpoint": "http://mem:8002", "timeout_ms": 3000},
        "trust_client": {"endpoint": "http://trust:8003", "timeout_ms": 2000},
        "learning_client": {"endpoint": "http://obs:8004", "timeout_ms": 2000},
        "action_gateway_client": {"endpoint": "http://ag:9999", "timeout_ms": 5000},
        "observability": {"domain": "kkb"},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.agent.primary_model == "claude-haiku-4-5-20251001"
    assert cfg.agent.max_tool_rounds == 3
    assert len(cfg.preprocessing.nlu_processor.intents) == 2
    assert cfg.preprocessing.nlu_processor.signal_intents["pay_disappointment"] == "objection"
    assert cfg.entity_to_profile_field["location"] == "location"
    assert cfg.hitl.response_message == "connecting you"
    assert len(cfg.agent_workflow.subagents) == 2
    assert cfg.agent_workflow.subagents[0].is_start is True
    assert "onest_market_lookup" in cfg.agent_workflow.tool_result_mappings
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
    config["preprocessing"]["nlu_processor"]["confidence_threshold"] = 1.5
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
                    "hang_up": "end_session",
                }
            }
        }
    })
    assert cfg.preprocessing.nlu_processor.signal_intents["hang_up"] == "end_session"


def test_tool_result_mappings_accepts_domain_tool_names():
    """Open-map: domain-defined tool names with per-tool field maps."""
    cfg = MergedConfig.validate_full({
        "agent_workflow": {
            "tool_result_mappings": {
                "my_tool": {
                    "journey_event_label": "MyNode",
                    "result_list_key": "items",
                    "field_map": {"id_field": "x.y"},
                }
            }
        }
    })
    mapping = cfg.agent_workflow.tool_result_mappings["my_tool"]
    assert mapping.field_map["id_field"] == "x.y"


def test_routing_condition_all_operators_accepted():
    for op in ["eq", "not_eq", "gt", "lt", "in"]:
        cfg = MergedConfig.validate_full({
            "agent_workflow": {
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
