"""Tests for Trust Layer MergedConfig strict schema validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schema.config import (
    DignityFailAction,
    GuardrailFailureMode,
    GuardrailSeverity,
    MergedConfig,
    QueueBackend,
    ServerConfig,
)


def _minimal_valid_config() -> dict:
    """A minimal merged config that validates cleanly."""
    return {
        "server": {"host": "0.0.0.0", "port": 8003},
        "trust": {
            "policy_pack": "kkb_advisory_jobs",
            "input_rules": {
                "blocked_phrases": ["bomb"],
                "escalation_topics": ["suicide"],
                "blocked_input_message": "blocked",
            },
            "output_rules": {
                "blocked_phrases": ["guaranteed placement"],
                "output_blocked_message": "blocked",
            },
            "policy_packs": {
                "kkb_advisory_jobs": {
                    "guardrails": {
                        "false_certainty": {
                            "severity": "blocker",
                            "failure_mode": "block",
                            "prompt_constraints": ["MUST NOT guarantee"],
                            "refusal_template": "cannot guarantee",
                        },
                    },
                }
            },
            "consent": {
                "consent_phrases": ["yes"],
                "decline_phrases": ["no"],
            },
            "hitl": {
                "queue_backend": "log",
                "holding_message": "please wait",
            },
        },
        "dignity_check": {
            "enabled": True,
            "questions": ["q1", "q2"],
            "fail_action": "rewrite",
        },
        "observability": {"domain": "kkb"},
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.server.port == 8003
    assert cfg.trust.policy_pack == "kkb_advisory_jobs"
    assert "kkb_advisory_jobs" in cfg.trust.policy_packs
    guardrails = cfg.trust.policy_packs["kkb_advisory_jobs"].guardrails
    assert "false_certainty" in guardrails
    assert guardrails["false_certainty"].severity == GuardrailSeverity.blocker
    assert guardrails["false_certainty"].failure_mode == GuardrailFailureMode.block
    assert cfg.dignity_check.enabled is True
    assert cfg.dignity_check.fail_action == DignityFailAction.rewrite


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({})
    assert cfg.server.port == 8003
    assert cfg.trust.policy_pack == ""
    assert cfg.trust.hitl.queue_backend == QueueBackend.log
    assert cfg.dignity_check.enabled is False


def test_rejects_unknown_top_level_key():
    config = _minimal_valid_config()
    config["typo_section"] = {}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "typo_section" in str(exc.value)


def test_rejects_unknown_key_on_trust():
    config = _minimal_valid_config()
    config["trust"]["policyPack"] = "typo"  # should be policy_pack
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "policyPack" in str(exc.value)


def test_rejects_unknown_key_on_input_rules():
    config = _minimal_valid_config()
    config["trust"]["input_rules"]["block_message"] = "x"  # missing 'ed'
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "block_message" in str(exc.value)


def test_rejects_unknown_key_on_guardrail():
    config = _minimal_valid_config()
    config["trust"]["policy_packs"]["kkb_advisory_jobs"]["guardrails"]["false_certainty"]["id"] = "GR-001"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "id" in str(exc.value)


def test_rejects_unknown_key_on_policy_pack():
    config = _minimal_valid_config()
    config["trust"]["policy_packs"]["kkb_advisory_jobs"]["risks"] = ["false_certainty"]
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "risks" in str(exc.value)


def test_rejects_unknown_key_on_hitl():
    config = _minimal_valid_config()
    config["trust"]["hitl"]["timeout_s"] = 30
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "timeout_s" in str(exc.value)


def test_rejects_unknown_key_on_dignity_check():
    config = _minimal_valid_config()
    config["dignity_check"]["question_model"] = "gpt-4"  # not in schema
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "question_model" in str(exc.value)


def test_rejects_invalid_severity_enum():
    config = _minimal_valid_config()
    config["trust"]["policy_packs"]["kkb_advisory_jobs"]["guardrails"]["false_certainty"]["severity"] = "critical"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_failure_mode_enum():
    config = _minimal_valid_config()
    config["trust"]["policy_packs"]["kkb_advisory_jobs"]["guardrails"]["false_certainty"]["failure_mode"] = "ignore"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_queue_backend_enum():
    config = _minimal_valid_config()
    config["trust"]["hitl"]["queue_backend"] = "kafka"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_fail_action_enum():
    config = _minimal_valid_config()
    config["dignity_check"]["fail_action"] = "rephrase"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_guardrail_without_severity_or_failure_mode_is_valid():
    """severity and failure_mode are Optional pending GH-170 implementation."""
    config = _minimal_valid_config()
    del config["trust"]["policy_packs"]["kkb_advisory_jobs"]["guardrails"]["false_certainty"]["severity"]
    del config["trust"]["policy_packs"]["kkb_advisory_jobs"]["guardrails"]["false_certainty"]["failure_mode"]
    cfg = MergedConfig.validate_full(config)
    gr = cfg.trust.policy_packs["kkb_advisory_jobs"].guardrails["false_certainty"]
    assert gr.severity is None
    assert gr.failure_mode is None


def test_rejects_invalid_server_port():
    with pytest.raises(ValidationError):
        ServerConfig(port=70000)
    with pytest.raises(ValidationError):
        ServerConfig(port=0)


def test_rejects_out_of_range_sample_rate():
    config = _minimal_valid_config()
    config["observability"] = {"otel": {"sample_rate": 2.0}}
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_enum_exports_are_usable():
    assert GuardrailSeverity.blocker.value == "blocker"
    assert GuardrailFailureMode.block.value == "block"
    assert QueueBackend.log.value == "log"
    assert DignityFailAction.rewrite.value == "rewrite"


def test_open_map_allows_domain_defined_pack_names():
    """policy_packs is an open map keyed by domain-defined pack names."""
    cfg = MergedConfig.validate_full({
        "trust": {
            "policy_packs": {
                "fasal_doctor_advisory": {"guardrails": {}},
                "kkb_advisory_jobs": {"guardrails": {}},
            }
        }
    })
    assert set(cfg.trust.policy_packs.keys()) == {"fasal_doctor_advisory", "kkb_advisory_jobs"}


def test_consent_store_default_path():
    cfg = MergedConfig.validate_full({})
    assert cfg.trust.consent_store.db_path == "/tmp/dpg_consent.db"
