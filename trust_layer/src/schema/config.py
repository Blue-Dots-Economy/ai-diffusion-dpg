"""
MergedConfig — strict schema for the Trust Layer merged runtime config.

Merged config = dev-kit/dpg/trust_layer.yaml (framework defaults)
                deep-merged with a domain YAML
                (e.g. dev-kit/configs/kkb/trust_layer.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Open-map sub-sections — ``trust.policy_packs`` and each pack's
``guardrails`` — are intentionally modelled as ``dict[str, <inner>]``
because the top-level keys under them are operator-defined.

Belongs to the Trust Layer DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GuardrailSeverity(str, Enum):
    """Severity tier assigned to a guardrail.

    NOTE: not yet acted on at runtime — tracked in GH-170.
    """

    blocker = "blocker"
    warning = "warning"


class GuardrailFailureMode(str, Enum):
    """How a fired guardrail is enforced.

    NOTE: not yet acted on at runtime — tracked in GH-170.
    """

    block = "block"
    constrain = "constrain"


class QueueBackend(str, Enum):
    """HiTL queue backend."""

    log = "log"
    redis = "redis"
    webhook = "webhook"


class DignityFailAction(str, Enum):
    """Action taken when the dignity check fails.

    NOTE: not yet dispatched at runtime — tracked in GH-171.
    """

    rewrite = "rewrite"
    block = "block"
    flag = "flag"


# ---------------------------------------------------------------------------
# Framework / infrastructure sections
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Trust Layer entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8003, gt=0, lt=65536)


class OtelConfig(BaseModel):
    """OTel SDK exporter and sampling configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    collector_endpoint: str = "http://localhost:4317"
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    export_interval_ms: int = Field(default=5000, gt=0)


class ObservabilityConfig(BaseModel):
    """Observability settings — OTel plus domain identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = "unknown"
    otel: OtelConfig = Field(default_factory=OtelConfig)


# ---------------------------------------------------------------------------
# Trust block — input/output rules
# ---------------------------------------------------------------------------


class InputRulesConfig(BaseModel):
    """Rules applied to user input before the LLM call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocked_phrases: list[str] = Field(default_factory=list)
    escalation_topics: list[str] = Field(default_factory=list)
    blocked_input_message: str = ""


class OutputRulesConfig(BaseModel):
    """Rules applied to LLM output before it reaches the user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocked_phrases: list[str] = Field(default_factory=list)
    output_blocked_message: str = ""


# ---------------------------------------------------------------------------
# Trust block — policy packs and guardrails
# ---------------------------------------------------------------------------


class GuardrailConfig(BaseModel):
    """One guardrail rule within a policy pack.

    Attributes:
        severity: Declared severity (blocker or warning). Validated but
            not yet acted on — tracked in GH-170.
        failure_mode: Declared enforcement mode. Validated but not yet
            acted on — tracked in GH-170.
        prompt_constraints: MUST/MUST NOT instructions appended to the
            LLM system prompt when the pack is active.
        required_disclosures: Disclosure strings appended to LLM output.
        refusal_template: Fixed refusal text used when the guardrail
            fires in block mode.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Optional[GuardrailSeverity] = None         # GH-170
    failure_mode: Optional[GuardrailFailureMode] = None  # GH-170
    prompt_constraints: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    refusal_template: Optional[str] = None


class PolicyPackConfig(BaseModel):
    """One named policy pack. ``guardrails`` is keyed by risk name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    guardrails: dict[str, GuardrailConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trust block — consent, hitl, consent_store
# ---------------------------------------------------------------------------


class ConsentConfig(BaseModel):
    """Phrase lists used by the ConsentBlock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    consent_phrases: list[str] = Field(default_factory=list)
    decline_phrases: list[str] = Field(default_factory=list)


class HitlConfig(BaseModel):
    """Human-in-the-loop escalation queue settings.

    NOTE: ``notification_webhook`` is read but not yet dispatched —
    webhook and redis backends are tracked in GH-36.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    queue_backend: QueueBackend = QueueBackend.log
    holding_message: str = ""
    notification_webhook: Optional[str] = None  # GH-36


class ConsentStoreConfig(BaseModel):
    """SQLite store for consent records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    db_path: str = "/tmp/dpg_consent.db"


# ---------------------------------------------------------------------------
# Trust block — top-level
# ---------------------------------------------------------------------------


class TrustConfig(BaseModel):
    """Top-level ``trust`` section from the domain config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_pack: str = ""
    input_rules: InputRulesConfig = Field(default_factory=InputRulesConfig)
    output_rules: OutputRulesConfig = Field(default_factory=OutputRulesConfig)
    policy_packs: dict[str, PolicyPackConfig] = Field(default_factory=dict)
    consent: ConsentConfig = Field(default_factory=ConsentConfig)
    consent_store: ConsentStoreConfig = Field(default_factory=ConsentStoreConfig)
    hitl: HitlConfig = Field(default_factory=HitlConfig)


# ---------------------------------------------------------------------------
# Dignity check (GH-137 feature; fail_action tracked in GH-171)
# ---------------------------------------------------------------------------


class DignityCheckConfig(BaseModel):
    """Pre-response dignity check block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    questions: list[str] = Field(default_factory=list)
    fail_action: DignityFailAction = DignityFailAction.rewrite  # GH-171


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged trust_layer config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    trust: TrustConfig = Field(default_factory=TrustConfig)
    dignity_check: DignityCheckConfig = Field(default_factory=DignityCheckConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def validate_full(cls, config: dict) -> "MergedConfig":
        """Validate the full merged config dict against the strict schema.

        Args:
            config: Merged dict (dpg defaults + domain overrides).

        Returns:
            Validated MergedConfig instance.

        Raises:
            pydantic.ValidationError: If the config contains unknown keys,
                wrong value types, or values outside the allowed ranges at
                any nesting level.
            TypeError: If config is None.
        """
        if config is None:
            raise TypeError("config must be a dict, got None")
        return cls.model_validate(config)
