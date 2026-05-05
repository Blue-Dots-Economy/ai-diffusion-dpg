"""Tests for observability_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.observability_layer import (
    AuditOverride,
    LifecycleState,
    MetricDefinition,
    ObservabilitySection,
    OutcomesConfig,
    SliOverride,
    TelemetryOverride,
)
from dev_kit.schemas.enums import InstrumentType


# -- LifecycleState ----------------------------------------------------------

def test_lifecycle_state_minimal():
    s = LifecycleState(state="started")
    assert s.trigger_tool is None
    assert s.trigger_condition is None


def test_lifecycle_state_with_trigger():
    s = LifecycleState(state="completed", trigger_tool="onest_apply")
    assert s.trigger_tool == "onest_apply"


def test_lifecycle_state_pattern():
    LifecycleState(state="user_consented")
    LifecycleState(state="state123")
    with pytest.raises(ValidationError):
        LifecycleState(state="Has Spaces")
    with pytest.raises(ValidationError):
        LifecycleState(state="123_starts_with_number")
    with pytest.raises(ValidationError):
        LifecycleState(state="")


def test_lifecycle_state_extra_forbidden():
    with pytest.raises(ValidationError):
        LifecycleState(state="x", unknown_field="y")


# -- MetricDefinition --------------------------------------------------------

def test_metric_definition_minimal():
    m = MetricDefinition(name="turns.count", instrument="counter", description="Turn count")
    assert m.unit == ""
    assert m.attributes == []


def test_metric_definition_required_fields():
    with pytest.raises(ValidationError):
        MetricDefinition(name="turns.count", instrument="counter")  # missing description


def test_metric_definition_name_pattern():
    MetricDefinition(name="placement.applications", instrument="counter", description="d")
    MetricDefinition(name="x", instrument="gauge", description="d")
    with pytest.raises(ValidationError):
        MetricDefinition(name="Has Spaces", instrument="counter", description="d")


def test_metric_definition_invalid_instrument():
    with pytest.raises(ValidationError):
        MetricDefinition(name="x", instrument="not_an_instrument", description="d")


def test_metric_definition_all_instruments_valid():
    for kind in ("counter", "gauge", "histogram"):
        MetricDefinition(name=f"m_{kind}", instrument=kind, description="d")


def test_metric_definition_with_attributes():
    m = MetricDefinition(
        name="placement.applications",
        instrument="counter",
        description="Placement applications counter",
        unit="1",
        attributes=["intent", "state"],
    )
    assert m.attributes == ["intent", "state"]


# -- OutcomesConfig ----------------------------------------------------------

def test_outcomes_config_lifecycle_required_min_one():
    with pytest.raises(ValidationError):
        OutcomesConfig(lifecycle=[])  # min_length=1


def test_outcomes_config_metrics_optional():
    o = OutcomesConfig(lifecycle=[LifecycleState(state="started")])
    assert o.metrics == []


def test_outcomes_config_full():
    o = OutcomesConfig(
        lifecycle=[LifecycleState(state="started"), LifecycleState(state="completed")],
        metrics=[MetricDefinition(name="m", instrument="counter", description="d")],
    )
    assert len(o.lifecycle) == 2
    assert len(o.metrics) == 1


# -- SliOverride -------------------------------------------------------------

def test_sli_override_all_optional():
    s = SliOverride()
    assert s.turn_latency_p99_ms is None
    assert s.trust_block_rate_max is None


def test_sli_override_turn_latency_range():
    SliOverride(turn_latency_p99_ms=1500)
    with pytest.raises(ValidationError):
        SliOverride(turn_latency_p99_ms=0)
    with pytest.raises(ValidationError):
        SliOverride(turn_latency_p99_ms=10001)


def test_sli_override_block_rate_range():
    SliOverride(trust_block_rate_max=0.05)
    SliOverride(trust_block_rate_max=0.0)
    SliOverride(trust_block_rate_max=1.0)
    with pytest.raises(ValidationError):
        SliOverride(trust_block_rate_max=1.5)


# -- AuditOverride -----------------------------------------------------------

def test_audit_override_all_optional():
    a = AuditOverride()
    assert a.retention_days is None
    assert a.pii_fields_excluded is None


def test_audit_override_retention_days_range():
    AuditOverride(retention_days=90)
    with pytest.raises(ValidationError):
        AuditOverride(retention_days=0)
    with pytest.raises(ValidationError):
        AuditOverride(retention_days=3651)


def test_audit_override_pii_fields():
    a = AuditOverride(pii_fields_excluded=["user_message", "user_id"])
    assert a.pii_fields_excluded == ["user_message", "user_id"]


def test_audit_override_pii_fields_empty_list_allowed():
    """Empty list explicitly means 'no fields excluded' — valid (overrides DPG default)."""
    a = AuditOverride(pii_fields_excluded=[])
    assert a.pii_fields_excluded == []


# -- TelemetryOverride -------------------------------------------------------

def test_telemetry_override_optional():
    t = TelemetryOverride()
    assert t.pii_fields_excluded is None


def test_telemetry_override_pii_fields():
    t = TelemetryOverride(pii_fields_excluded=["user_message"])
    assert t.pii_fields_excluded == ["user_message"]


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_section_domain_pattern_not_enforced_here():
    """In observability_layer schema, domain is just non-empty (the agent_core.observability schema enforces the slug pattern)."""
    ObservabilitySection(domain="kkb")
    ObservabilitySection(domain="employ-voice-bot")


def test_observability_section_full():
    o = ObservabilitySection(
        domain="kkb",
        outcomes=OutcomesConfig(
            lifecycle=[LifecycleState(state="started")],
            metrics=[MetricDefinition(name="m", instrument="counter", description="d")],
        ),
        sli=SliOverride(turn_latency_p99_ms=1500),
        audit=AuditOverride(retention_days=90),
        telemetry=TelemetryOverride(pii_fields_excluded=["user_message"]),
    )
    assert o.domain == "kkb"
    assert o.sli.turn_latency_p99_ms == 1500


def test_observability_section_only_domain_required_others_optional():
    o = ObservabilitySection(domain="kkb")
    assert o.outcomes is None
    assert o.sli is None
    assert o.audit is None
    assert o.telemetry is None


def test_observability_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", unknown_field="y")
