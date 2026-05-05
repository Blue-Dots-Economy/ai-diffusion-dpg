"""Tests for dev_kit.schema — Pydantic config models — and the legacy
``validate_partial`` shim (now in ``dev_kit.schemas.validation``)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dev_kit.schema import (
    AgentConfig,
    AgentCoreConfig,
    AgentWorkflowConfig,
    AuditObsConfig,
    ConsentConfig,
    ConnectorDef,
    ConnectorsConfig,
    GuardrailConfig,
    HitlConfig,
    HitlTrustConfig,
    InputRulesConfig,
    InternalConnectorDef,
    KnowledgeEngineConfig,
    MemoryLayerConfig,
    MetricConfig,
    ObservabilityLayerConfig,
    ObservabilitySettings,
    OutputRulesConfig,
    PolicyPackConfig,
    ReachLayerConfig,
    RoutingConditionSchema,
    RoutingRuleSchema,
    SubAgentSchema,
    TrustConfig,
    TrustLayerConfig,
    WebChannelConfig,
)
from dev_kit.schemas.validation import validate_partial
from dev_kit.loader import load_agent_core, load_observability_layer, load_trust_layer


# ===========================================================================
# validate_partial — structural and type checks
# ===========================================================================


class TestValidatePartialBasics:
    def test_empty_dict_returns_no_errors(self):
        assert validate_partial("trust_layer", {}) == []

    def test_unknown_block_returns_error(self):
        errors = validate_partial("bogus_block", {})
        assert len(errors) == 1
        assert "Unknown block" in errors[0]

    def test_missing_required_field_not_reported(self):
        # AgentCoreConfig has many required fields — partial must not flag them.
        data = {"agent": {"primary_model": "claude-haiku-4-5-20251001"}}
        assert validate_partial("agent_core", data) == []

    def test_wrong_type_reported(self):
        data = {"trust": {"input_rules": {"blocked_phrases": "not-a-list"}}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0

    def test_nested_wrong_type_reported(self):
        data = {"trust": {"input_rules": {"blocked_phrases": [123]}}}
        errors = validate_partial("trust_layer", data)
        assert len(errors) > 0


class TestValidatePartialKeyCheck:
    def test_renamed_top_level_key_rejected(self):
        errors = validate_partial("trust_layer", {"truuust": {"input_rules": {}}})
        assert any("truuust" in e for e in errors)

    def test_renamed_nested_key_rejected(self):
        errors = validate_partial("trust_layer", {"trust": {"input_rulz": {}}})
        assert any("input_rulz" in e for e in errors)

    def test_valid_trust_layer_partial(self):
        data = {
            "trust": {
                "policy_pack": "my_pack",
                "input_rules": {
                    "blocked_phrases": ["spam"],
                    "escalation_topics": ["crisis"],
                    "blocked_input_message": "Cannot help with that.",
                },
                "output_rules": {
                    "blocked_phrases": ["forbidden"],
                    "output_blocked_message": "Cannot produce that response.",
                },
            }
        }
        assert validate_partial("trust_layer", data) == []

    def test_valid_observability_layer_partial(self):
        data = {
            "observability": {
                "domain": "test",
                "sli": {"turn_latency_p99_ms": 1000, "trust_block_rate_max": 0.03},
            }
        }
        assert validate_partial("observability_layer", data) == []

    def test_invented_observability_key_rejected(self):
        errors = validate_partial("observability_layer", {"observability": {"bogus_key": "x"}})
        assert any("bogus_key" in e for e in errors)

    def test_open_map_policy_packs_accepts_arbitrary_pack_name(self):
        # policy_packs is an open map — any pack name should pass key check
        data = {
            "trust": {
                "policy_packs": {
                    "my_custom_pack": {
                        "guardrails": {
                            "false_certainty": {
                                "severity": "blocker",
                                "failure_mode": "block",
                                "prompt_constraints": [],
                                "required_disclosures": [],
                                "refusal_template": None,
                            }
                        },
                    }
                }
            }
        }
        assert validate_partial("trust_layer", data) == []

    def test_agent_core_renamed_connector_key_rejected(self):
        errors = validate_partial("agent_core", {"connectors": {"reead": []}})
        assert any("reead" in e for e in errors)


# ===========================================================================
# AgentConfig
# ===========================================================================


class TestAgentConfig:
    def test_required_models_must_be_present(self):
        with pytest.raises(ValidationError):
            AgentConfig()  # missing primary_model and fallback_model

    def test_consent_fields_default_false_and_empty(self):
        cfg = AgentConfig(primary_model="claude-haiku-4-5-20251001", fallback_model="claude-haiku-4-5-20251001")
        assert cfg.ask_for_consent is False
        assert cfg.consent_prompt == ""

    def test_ask_for_consent_can_be_true(self):
        cfg = AgentConfig(
            primary_model="claude-haiku-4-5-20251001",
            fallback_model="claude-haiku-4-5-20251001",
            ask_for_consent=True,
            consent_prompt="May we store your data?",
        )
        assert cfg.ask_for_consent is True
        assert cfg.consent_prompt == "May we store your data?"


# ===========================================================================
# TrustLayer — new models
# ===========================================================================


class TestInputRulesConfig:
    def test_blocked_input_message_defaults_empty(self):
        cfg = InputRulesConfig()
        assert cfg.blocked_input_message == ""

    def test_blocked_input_message_can_be_set(self):
        cfg = InputRulesConfig(blocked_input_message="Cannot help with that.")
        assert cfg.blocked_input_message == "Cannot help with that."


class TestOutputRulesConfig:
    def test_output_blocked_message_defaults_empty(self):
        cfg = OutputRulesConfig()
        assert cfg.output_blocked_message == ""

    def test_output_blocked_message_can_be_set(self):
        cfg = OutputRulesConfig(output_blocked_message="Cannot produce that.")
        assert cfg.output_blocked_message == "Cannot produce that."


class TestGuardrailConfig:
    def test_defaults_are_valid(self):
        g = GuardrailConfig()
        assert g.severity == "blocker"
        assert g.failure_mode == "block"
        assert g.prompt_constraints == []
        assert g.required_disclosures == []
        assert g.refusal_template is None

    def test_valid_guardrail_with_overrides(self):
        g = GuardrailConfig(severity="blocker", failure_mode="block")
        assert g.severity == "blocker"
        assert g.failure_mode == "block"

    def test_refusal_template_can_be_null_or_string(self):
        g1 = GuardrailConfig(severity="warning", failure_mode="constrain", refusal_template=None)
        g2 = GuardrailConfig(severity="blocker", failure_mode="block", refusal_template="I cannot help.")
        assert g1.refusal_template is None
        assert g2.refusal_template == "I cannot help."


class TestPolicyPackConfig:
    def test_empty_policy_pack_is_valid(self):
        pp = PolicyPackConfig()
        assert pp.guardrails == {}

    def test_guardrails_is_dict_of_guardrail_config(self):
        pp = PolicyPackConfig(
            guardrails={
                "false_certainty": GuardrailConfig(severity="blocker", failure_mode="block")
            },
        )
        assert isinstance(pp.guardrails["false_certainty"], GuardrailConfig)


class TestConsentConfig:
    def test_defaults_are_empty_lists(self):
        c = ConsentConfig()
        assert c.consent_phrases == []
        assert c.decline_phrases == []


class TestHitlTrustConfig:
    def test_defaults(self):
        h = HitlTrustConfig()
        assert h.queue_backend == "log"
        assert h.holding_message == ""
        assert h.notification_webhook is None


class TestTrustConfig:
    def test_policy_pack_defaults_empty(self):
        t = TrustConfig()
        assert t.policy_pack == ""

    def test_policy_packs_defaults_empty_dict(self):
        t = TrustConfig()
        assert t.policy_packs == {}

    def test_consent_and_hitl_defaults(self):
        t = TrustConfig()
        assert isinstance(t.consent, ConsentConfig)
        assert t.hitl is None

    def test_full_trust_config(self):
        t = TrustConfig(
            policy_pack="my_pack",
            input_rules=InputRulesConfig(blocked_phrases=["bomb"], blocked_input_message="Blocked."),
            output_rules=OutputRulesConfig(blocked_phrases=["guarantee"], output_blocked_message="Blocked output."),
            policy_packs={
                "my_pack": PolicyPackConfig(
                    guardrails={"false_certainty": GuardrailConfig(severity="blocker", failure_mode="block")},
                )
            },
            consent=ConsentConfig(consent_phrases=["yes"], decline_phrases=["no"]),
            hitl=HitlTrustConfig(holding_message="Please wait."),
        )
        assert t.policy_pack == "my_pack"
        assert t.input_rules.blocked_input_message == "Blocked."
        assert t.output_rules.output_blocked_message == "Blocked output."
        assert "my_pack" in t.policy_packs
        assert t.consent.consent_phrases == ["yes"]
        assert t.hitl is not None


# ===========================================================================
# ObservabilityLayer
# ===========================================================================


class TestObservabilitySettings:
    def test_all_fields_have_defaults(self):
        obs = ObservabilitySettings()
        assert obs.domain == ""
        assert obs.sli.turn_latency_p99_ms == 1200
        assert obs.sli.trust_block_rate_max == 0.05

    def test_otel_defaults(self):
        obs = ObservabilitySettings()
        assert obs.otel.collector_endpoint == "http://otelcol:4317"
        assert obs.otel.sample_rate == 1.0
        assert obs.otel.export_interval_ms == 5000

    def test_audit_defaults(self):
        obs = ObservabilitySettings()
        assert "user_message" in obs.telemetry.pii_fields_excluded
        assert obs.audit.retention_days == 90

    def test_outcomes_defaults_empty(self):
        obs = ObservabilitySettings()
        assert obs.outcomes.lifecycle == []
        assert obs.outcomes.metrics == []


class TestMetricConfig:
    def test_required_fields_enforced(self):
        with pytest.raises(ValidationError):
            MetricConfig()  # name and instrument required

    def test_valid_metric(self):
        m = MetricConfig(name="placement.rate", instrument="gauge", description="Placement rate", unit="%")
        assert m.name == "placement.rate"
        assert m.attributes == []


class TestAuditObsConfig:
    def test_defaults(self):
        a = AuditObsConfig()
        assert a.retention_days == 90
        assert "user_message" in a.pii_fields_excluded


# ===========================================================================
# AgentWorkflow — RoutingConditionSchema, RoutingRuleSchema, SubAgentSchema
# ===========================================================================


class TestRoutingConditionSchema:
    def test_all_valid_operators_accepted(self):
        for op in ("eq", "not_eq", "in", "lt", "gt"):
            rc = RoutingConditionSchema(field="some_field", operator=op, value="x")
            assert rc.operator == op

    def test_invalid_operator_rejected(self):
        with pytest.raises(ValidationError):
            RoutingConditionSchema(field="f", operator="gte", value=1)

    def test_required_fields_enforced(self):
        with pytest.raises(ValidationError):
            RoutingConditionSchema()


class TestRoutingRuleSchema:
    def test_minimal_rule(self):
        r = RoutingRuleSchema(intent="greeting", next_subagent_id="welcome")
        assert r.intent == "greeting"
        assert r.condition is None
        assert r.conditions == []
        assert r.session_writes == {}

    def test_wildcard_intent(self):
        r = RoutingRuleSchema(intent="*", next_subagent_id="fallback")
        assert r.intent == "*"

    def test_condition_and_conditions_optional(self):
        cond = RoutingConditionSchema(field="score", operator="gt", value=3)
        r = RoutingRuleSchema(intent="apply", next_subagent_id="apply_agent", condition=cond)
        assert r.condition.field == "score"

    def test_session_writes_accepts_arbitrary_keys(self):
        r = RoutingRuleSchema(
            intent="profile_done",
            next_subagent_id="main",
            session_writes={"profile_complete": True, "score": 5},
        )
        assert r.session_writes["profile_complete"] is True


class TestSubAgentSchema:
    def test_required_id_field(self):
        with pytest.raises(ValidationError):
            SubAgentSchema()  # id required

    def test_defaults(self):
        s = SubAgentSchema(id="greeting")
        assert s.is_start is False
        assert s.is_terminal is False
        assert s.special_handler is None
        assert s.valid_intents == []
        assert s.tools == []
        assert s.routing == []

    def test_valid_special_handlers(self):
        s1 = SubAgentSchema(id="hitl_node", special_handler="hitl")
        s2 = SubAgentSchema(id="wa_node", special_handler="whatsapp_handoff")
        assert s1.special_handler == "hitl"
        assert s2.special_handler == "whatsapp_handoff"

    def test_invalid_special_handler_rejected(self):
        with pytest.raises(ValidationError):
            SubAgentSchema(id="bad", special_handler="unknown_handler")

    def test_null_special_handler_accepted(self):
        s = SubAgentSchema(id="normal", special_handler=None)
        assert s.special_handler is None


class TestAgentWorkflowConfig:
    def _make_minimal_workflow(self) -> dict:
        return {
            "workflow_id": "test_flow",
            "version": "1.0.0",
            "subagents": [
                {"id": "start", "is_start": True, "routing": [{"intent": "*", "next_subagent_id": "end"}]},
                {"id": "end", "is_terminal": True},
            ],
        }

    def test_minimal_workflow_validates(self):
        wf = AgentWorkflowConfig(**self._make_minimal_workflow())
        assert wf.workflow_id == "test_flow"
        assert len(wf.subagents) == 2

    def test_empty_subagents_rejected(self):
        with pytest.raises(ValidationError):
            AgentWorkflowConfig(workflow_id="x", version="1.0.0", subagents=[])

    def test_workflow_id_required(self):
        data = self._make_minimal_workflow()
        del data["workflow_id"]
        with pytest.raises(ValidationError):
            AgentWorkflowConfig(**data)

    def test_global_intents_default_empty(self):
        wf = AgentWorkflowConfig(**self._make_minimal_workflow())
        assert wf.global_intents == []
        assert wf.global_routing == []
        assert wf.default_fallback_subagent_id == ""

    def test_multiple_subagents_accepted(self):
        data = {
            "workflow_id": "multi",
            "version": "1.0.0",
            "subagents": [
                {"id": "greeting", "is_start": True, "routing": [{"intent": "start", "next_subagent_id": "profile"}]},
                {"id": "profile", "routing": [{"intent": "*", "next_subagent_id": "goodbye"}]},
                {"id": "goodbye", "is_terminal": True},
            ],
        }
        wf = AgentWorkflowConfig(**data)
        assert len(wf.subagents) == 3


# ===========================================================================
# ConnectorsConfig — internal connectors
# ===========================================================================


class TestConnectorsConfig:
    def test_internal_connector_defaults_empty(self):
        c = ConnectorsConfig()
        assert c.internal == []

    def test_internal_connector_validates(self):
        c = ConnectorsConfig(
            internal=[
                InternalConnectorDef(
                    name="knowledge_retrieval",
                    route="knowledge_engine",
                    description="Retrieve relevant knowledge",
                )
            ]
        )
        assert c.internal[0].name == "knowledge_retrieval"

    def test_internal_connector_requires_name_and_route(self):
        with pytest.raises(ValidationError):
            InternalConnectorDef(description="missing name and route")

    def test_read_write_identity_all_default_empty(self):
        c = ConnectorsConfig()
        assert c.read == []
        assert c.write == []
        assert c.identity == []


# ===========================================================================
# HitlConfig (agent_core)
# ===========================================================================


class TestHitlConfig:
    def test_response_message_required(self):
        with pytest.raises(ValidationError):
            HitlConfig()

    def test_valid_hitl_config(self):
        h = HitlConfig(response_message="A counsellor will contact you soon.")
        assert h.response_message == "A counsellor will contact you soon."


# ===========================================================================
# MemoryLayerConfig — state, redis, memgraph, etc.
# ===========================================================================


class TestMemoryLayerConfig:
    def test_redis_defaults(self):
        from dev_kit.schema import RedisConfig
        r = RedisConfig()
        assert r.host == "redis"
        assert r.port == 6379
        assert r.db == 0
        assert r.password is None

    def test_memgraph_defaults(self):
        from dev_kit.schema import MemgraphConfig
        m = MemgraphConfig()
        assert m.uri == "bolt://memgraph:7687"
        assert m.user == "memgraph"

    def test_session_state_ttl_default(self):
        from dev_kit.schema import SessionStateConfig
        s = SessionStateConfig()
        assert s.ttl_minutes == 60

    def test_session_state_schema_alias(self):
        # YAML key is 'schema'; Python attribute is session_schema
        from dev_kit.schema import SessionStateConfig
        s = SessionStateConfig(**{"schema": {"trade": {"type": "string", "default": ""}}})
        assert "trade" in s.session_schema

    def test_merge_rule_requires_session_field_and_target(self):
        from dev_kit.schema import MergeRuleConfig
        with pytest.raises(ValidationError):
            MergeRuleConfig()

    def test_user_data_persistence_defaults_saved(self):
        from dev_kit.schema import UserDataPersistenceConfig
        u = UserDataPersistenceConfig()
        assert u.default_mode == "saved"

    def test_invalid_persistence_mode_rejected(self):
        from dev_kit.schema import UserDataPersistenceConfig
        with pytest.raises(ValidationError):
            UserDataPersistenceConfig(default_mode="unknown")


# ===========================================================================
# Integration: load KKB domain configs via loader and validate
# ===========================================================================


class TestLoaderIntegration:
    def test_load_agent_core_kkb(self):
        cfg = load_agent_core("kkb")
        assert cfg.agent.primary_model != ""
        assert cfg.agent.fallback_model != ""
        assert len(cfg.agent_workflow.subagents) >= 1

    def test_kkb_workflow_has_exactly_one_start(self):
        cfg = load_agent_core("kkb")
        start_agents = [s for s in cfg.agent_workflow.subagents if s.is_start]
        assert len(start_agents) == 1

    def test_kkb_agent_ask_for_consent_is_bool(self):
        cfg = load_agent_core("kkb")
        assert isinstance(cfg.agent.ask_for_consent, bool)

    def test_load_trust_layer_kkb(self):
        cfg = load_trust_layer("kkb")
        assert "kkb_advisory_jobs" in cfg.trust.policy_packs
        assert cfg.trust.policy_pack == "kkb_advisory_jobs"
        assert len(cfg.trust.input_rules.blocked_phrases) > 0
        assert cfg.trust.input_rules.blocked_input_message != ""
        assert cfg.trust.output_rules.output_blocked_message != ""
        assert cfg.trust.consent.consent_phrases != []
        assert cfg.trust.hitl is not None

    def test_load_observability_layer_kkb(self):
        cfg = load_observability_layer("kkb")
        assert cfg.observability.domain == "kkb"
        assert len(cfg.observability.outcomes.lifecycle) > 0
        assert len(cfg.observability.outcomes.metrics) > 0
        assert cfg.observability.sli.turn_latency_p99_ms == 1200

    def test_kkb_policy_pack_guardrails_validate(self):
        cfg = load_trust_layer("kkb")
        pack = cfg.trust.policy_packs["kkb_advisory_jobs"]
        assert "false_certainty" in pack.guardrails
        gr = pack.guardrails["false_certainty"]
        assert gr.severity == "blocker"
        assert gr.failure_mode == "block"
        assert len(gr.prompt_constraints) > 0

    def test_kkb_internal_connector_present(self):
        cfg = load_agent_core("kkb")
        internal_names = [c.name for c in cfg.connectors.internal]
        assert "knowledge_retrieval" in internal_names


class TestWebChannelConfigMode:
    def test_default_mode_is_full(self):
        cfg = WebChannelConfig()
        assert cfg.mode == "full"

    def test_routing_only_mode_accepted(self):
        cfg = WebChannelConfig(mode="routing_only")
        assert cfg.mode == "routing_only"

    def test_full_mode_accepted(self):
        cfg = WebChannelConfig(mode="full")
        assert cfg.mode == "full"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValidationError):
            WebChannelConfig(mode="partial")
