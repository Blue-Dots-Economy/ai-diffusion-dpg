"""
MergedConfig — strict schema for the Agent Core merged runtime config.

Merged config = dev-kit/dpg/agent_core.yaml (framework defaults)
                deep-merged with a domain YAML
                (e.g. dev-kit/configs/blue-dots/agent_core.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Open-map sub-sections are modelled as ``dict[str, <inner>]``:

- ``entity_to_profile_field`` — entity_name → profile_field_name
- ``preprocessing.nlu_processor.signal_intents`` — intent → signal_type
- ``connectors.*.[].input_schema.properties`` — JSON Schema property map

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RoutingOperator(str, Enum):
    """Comparison operator for a routing condition."""

    eq = "eq"
    not_eq = "not_eq"
    gt = "gt"
    lt = "lt"
    in_ = "in"
    contains = "contains"


class SpecialHandler(str, Enum):
    """Framework-level subagent handler that bypasses normal LLM flow."""

    hitl = "hitl"
    whatsapp_handoff = "whatsapp_handoff"


class AssemblyMode(str, Enum):
    """Turn assembly mode (cosmetic label, channel-specific)."""

    streaming = "streaming"
    batch = "batch"


# ---------------------------------------------------------------------------
# Framework / infrastructure sections
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Agent Core entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)


class ClientConfig(BaseModel):
    """Generic HTTP client config for an inter-service call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    timeout_ms: int = Field(default=5000, gt=0)


class CheckOutputBatchConfig(BaseModel):
    """Trust Layer ``/check/output`` batching policy for streaming turns.

    During SSE streaming, sentences are buffered and submitted to Trust Layer
    as a single concatenated check whenever the buffer reaches ``max_sentences``
    or ``max_interval_ms`` has elapsed since the first buffered sentence —
    whichever happens first. On ``block`` / ``escalate`` verdicts the entire
    pending batch is replaced with the configured fallback message; sentences
    already emitted in earlier batches are not retracted.

    Attributes:
        enabled: When False, every sentence is checked individually
            (legacy behaviour).
        max_sentences: Flush trigger by buffer size (>=1).
        max_interval_ms: Flush trigger by elapsed wall-clock ms (>=1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_sentences: int = Field(default=3, ge=1)
    max_interval_ms: int = Field(default=500, ge=1)


class TrustClientConfig(ClientConfig):
    """HTTP client config for the Trust Layer plus output-check batching policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    escalate_timeout_ms: int = Field(default=8000, gt=0)
    check_output_batch: CheckOutputBatchConfig = Field(
        default_factory=CheckOutputBatchConfig
    )


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
# agent.*
# ---------------------------------------------------------------------------


class RecentToolExchangesConfig(BaseModel):
    """Caps for cross-turn tool_use/tool_result replay (issue #193).

    Controls how many prior tool exchanges are persisted into Memory Layer
    under the session-scoped ``recent_tool_exchanges`` key and replayed as
    real ``tool_use``/``tool_result`` message pairs at the start of the
    next turn's streaming LLM call. Keeping the LLM aware of prior tool
    results avoids redundant re-invocation of the same tool with identical
    parameters across turns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_items: int = Field(default=3, ge=0)
    max_chars: int = Field(default=4000, ge=0)


class CurrentQuestionConfig(BaseModel):
    """Guardrails on the per-session ``current_question`` memory field (#207).

    The orchestrator persists every turn's bot response under ``current_question``
    so the next turn can show the LLM the previous prompt context. Pre-#200
    turn pile-ups occasionally fed concatenated multi-turn responses into this
    field, which then poisoned the next turn's prompt as
    ``[Last question asked: <bot_response_1> ... <bot_response_2> ...]``.

    The cap and the concat detector are defense-in-depth: #200's cancel-and-fold
    removes the source of the concatenation, but if it ever reappears upstream
    we want to detect and trim it rather than silently let it through.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_chars: int = Field(default=500, ge=1)


class TerminationShortCircuitConfig(BaseModel):
    """Skip the LLM round trip for high-confidence termination_intent (#204).

    When the user's intent is unambiguously to end the session, calling the
    LLM only to have it speak the configured ``conversation.termination_message``
    adds ~9 s of latency for no semantic benefit. Enabling this lets the
    orchestrator emit the canned termination message directly when NLU is
    confident enough, cutting the goodbye turn down to NLU + translation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class FeaturesConfig(BaseModel):
    """Per-deployment chat-provider feature toggles.

    None means "use the provider's intrinsic capability." A bool tightens
    the effective feature for this deployment. Cannot widen — the
    chat_provider factory rejects True against a False capability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_cache: bool | None = None
    streaming: bool | None = None
    image_input: bool | None = None


class AgentConfig(BaseModel):
    """Top-level LLM wrapper settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_model: str = ""
    fallback_model: str = ""
    provider: Literal["anthropic", "openai", "ollama", "google"] = "anthropic"
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    timeout_ms: int = Field(default=10000, gt=0)
    prompt_session_fields: list[str] = Field(default_factory=list)
    """Session fields rendered into <known_profile> (session-bootstrap spec §5.4)."""

    @field_validator("features", mode="before")
    @classmethod
    def _coerce_null_features(cls, value):
        """Treat ``features: null`` (or YAML's empty mapping) as the default.

        YAML parses an ``agent.features:`` block whose every sub-key is
        commented out as ``None`` rather than as an empty dict. Without this
        coercion startup fails with a ValidationError on a config file that
        looks correct to a domain author. The semantics are unambiguous:
        no features expressed → use the provider's intrinsic capabilities,
        which is exactly what FeaturesConfig() defaults to.
        """
        if value is None:
            return FeaturesConfig()
        return value
    retry_attempts: int = Field(default=2, ge=1)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [0, 0.5, 1.0])
    max_tool_rounds: int = Field(default=3, ge=1)
    ask_for_consent: bool = False
    consent_prompt: str = ""
    termination_short_circuit: TerminationShortCircuitConfig = Field(
        default_factory=TerminationShortCircuitConfig
    )
    current_question: CurrentQuestionConfig = Field(
        default_factory=CurrentQuestionConfig
    )
    recent_tool_exchanges: RecentToolExchangesConfig = Field(
        default_factory=RecentToolExchangesConfig
    )


# ---------------------------------------------------------------------------
# conversation.*
# ---------------------------------------------------------------------------


class UserStateDefinition(BaseModel):
    """One user-state entry (GH-139)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    signals: list[str] = Field(default_factory=list)
    guidance: str = ""


class UserStateModelConfig(BaseModel):
    """User-state classifier (opt-in, GH-139)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    default_state: str = ""
    states: list[UserStateDefinition] = Field(default_factory=list)


class SessionEndEvalConfig(BaseModel):
    """Opt-in session-end signalling (GH-137).

    ``fail_action`` is validated but not yet dispatched at runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    prompt: str = ""
    fail_action: str = "none"
    subagents: List[str] = Field(default_factory=list)
    """Subagent ids allowed to call ``end_session``.

    Empty (the default) keeps the original behaviour: every subagent gets the
    tool. Naming ids restricts it to those, which is the only reliable way to
    stop a mid-conversation hang-up — prompt rules alone do not hold, because
    the tool's own description ("task completed") invites the model to fire it
    the moment a journey succeeds.
    """


class ConversationConfig(BaseModel):
    """Static response strings plus opt-in sub-blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocked_message: str = ""
    escalation_message: str = ""
    output_blocked_message: str = ""
    unknown_intent_message: str = ""
    termination_message: str = ""
    consent_message: str = ""
    consent_decline_ack: str = ""
    unsupported_language_message: str = ""
    # Spoken when a turn produced no text at all, after the orchestrator's
    # empty-completion retry has already failed. Without it the caller gets
    # silence, which on a phone line reads as a dropped call.
    empty_response_message: str = ""
    profile_complete_message: str = ""
    returning_user_greeting: str = ""
    user_state_model: UserStateModelConfig = Field(default_factory=UserStateModelConfig)
    session_end_eval: SessionEndEvalConfig = Field(default_factory=SessionEndEvalConfig)


# ---------------------------------------------------------------------------
# connectors.*
# ---------------------------------------------------------------------------


class InvocationSafety(BaseModel):
    """Safety constraints exposed through a connector's invocation rules.

    Lists entries the agent must never present from a tool result (e.g.
    closed or inactive jobs) or speak aloud (e.g. GPS coordinates, match
    scores). Consumed by per-subagent prompts as grounding context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    never_present: list[str] = Field(default_factory=list)
    never_speak: list[str] = Field(default_factory=list)


class InvocationRules(BaseModel):
    """LLM invocation contract for a connector (GH-137, GH-176)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_when: str = ""
    required_before_calling: list[str] = Field(default_factory=list)
    must_not_substitute: str = ""
    max_calls_per_turn: Optional[int] = None
    """Hard cap on how many times this tool may execute in a single turn.

    For tools whose effect is irreversible, a prompt rule is not enough. A
    model that is shown a list will sometimes act on every row of it: measured
    on a live call, a caller picked one job and the agent emitted five
    apply_job calls in one turn, creating applications at employers they never
    chose. Extra calls beyond the cap are refused before they execute.
    """
    grounded_params: list[str] | dict[str, list[str]] = Field(default_factory=list)
    """Params whose value must have appeared in an earlier tool result.

    ``must_not_substitute`` states the same requirement in prose for the LLM to
    read; this enforces it. Identifiers the model reproduces from far back in
    context are the case that needs it — a fabricated one is well-formed, so
    only checking it against what upstreams actually returned catches it.
    """
    on_empty: str = ""
    on_failure: str = ""
    bridge_line: str = ""
    # GH-176: optional per-connector presentation contract. Consumed by the
    # subagent prompt rather than by any adapter — these fields shape how
    # the LLM talks about the tool's results, they do not change how the
    # tool is invoked.
    exception_no_call: str = ""
    ranking_order: list[str] = Field(default_factory=list)
    presentation_limit: int | None = None
    refinement_loop_max: int | None = None
    safety: InvocationSafety = Field(default_factory=InvocationSafety)


class InputSchema(BaseModel):
    """JSON-Schema-shaped description of a connector's input.

    Passed verbatim to the Anthropic tools API. ``properties`` is an
    open map keyed by parameter name; each property dict is left
    permissive (``dict[str, Any]``) since JSON Schema is large.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = "object"
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    additionalProperties: bool = False


class ToolCacheConfig(BaseModel):
    """Per-connector tool-result cache rule (tool-result persistence spec §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["session", "user"]
    ttl_seconds: int = Field(gt=0)
    # Top-level keys to store; applies only when the result is a JSON object
    # (a list or scalar result is stored whole).
    keep: list[str] = Field(default_factory=list)
    vary_on: list[str] = Field(default_factory=list)


class ConnectorDef(BaseModel):
    """External-facing connector (read / write / identity)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    input_schema: InputSchema = Field(default_factory=InputSchema)
    invocation_rules: InvocationRules = Field(default_factory=InvocationRules)
    cache: Optional[ToolCacheConfig] = None
    invalidates: list[str] = Field(default_factory=list)


class InternalConnectorDef(BaseModel):
    """Internal connector routed to another DPG block (e.g. knowledge_retrieval)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    route: str
    description: str = ""
    input_schema: InputSchema = Field(default_factory=InputSchema)
    invocation_rules: InvocationRules = Field(default_factory=InvocationRules)


class ConnectorsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    read: list[ConnectorDef] = Field(default_factory=list)
    write: list[ConnectorDef] = Field(default_factory=list)
    identity: list[ConnectorDef] = Field(default_factory=list)
    internal: list[InternalConnectorDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cache_rules(self) -> "ConnectorsConfig":
        """Reject cache on non-read and invalidates on non-write connectors.

        Returns:
            The validated config.

        Raises:
            ValueError: If cache/invalidates is misplaced or an invalidates
                target is not a read connector.
        """
        read_names = {c.name for c in self.read}
        for group_name in ("write", "identity"):
            for c in getattr(self, group_name):
                if c.cache is not None:
                    raise ValueError(f"connector '{c.name}': cache is only allowed on read connectors")
        for group_name in ("read", "identity"):
            for c in getattr(self, group_name):
                if c.invalidates:
                    raise ValueError(f"connector '{c.name}': invalidates is only allowed on write connectors")
        for c in self.write:
            for target in c.invalidates:
                if target not in read_names:
                    raise ValueError(f"connector '{c.name}': invalidates unknown read connector '{target}'")
        return self


# ---------------------------------------------------------------------------
# preprocessing.*
# ---------------------------------------------------------------------------


class LanguageNormalisationConfig(BaseModel):
    """LLM-native language normalisation block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # GH-313: when False the leading LLM call is skipped; the main LLM mirrors
    # the user's language via a directive in build_system_prompt(). Defaults to
    # True for backward compatibility; Blue Dots sets False.
    enabled: bool = True

    # Per-helper provider override (#287 follow-up). When set, build_chat_provider
    # uses this provider for the language-norm helper instead of inheriting
    # agent.provider. Lets a deployment run primary chat on OpenAI while
    # keeping language norm on Anthropic (or vice versa). None → inherit.
    provider: Literal["anthropic", "openai", "ollama", "google"] | None = None
    model: str = ""
    default_language: str = ""
    supported_languages: list[str] = Field(default_factory=list)
    min_detection_tokens: int = Field(default=3, gt=0)
    transliteration: bool = True
    code_switching: bool = True


# Intents the orchestrator acts on itself (not via a workflow routing rule).
_FRAMEWORK_HANDLED_INTENTS = frozenset({"language_switch_request", "human_request"})

_DIALOGUE_ACTS: tuple[str, ...] = (
    "affirm", "deny", "acknowledge", "provide_info", "correct", "select",
    "ask", "request_change", "repeat", "hold", "close", "other",
)
_DIALOGUE_RELATIONS: tuple[str, ...] = (
    "answers_pending", "answers_other", "new_topic", "unrelated", "unclear",
)


class NLUSlotConfig(BaseModel):
    """One caller-stated value the dialogue-act NLU extracts (NLU dialogue-acts spec §7.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["string", "int", "enum"] = "string"
    values: list[str] = Field(default_factory=list)
    min: Optional[int] = None
    max: Optional[int] = None
    normalise: Optional[Literal["title", "lower"]] = None
    accept_when_pending: list[str] = Field(default_factory=list)
    description: str = ""
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "NLUSlotConfig":
        """Enum slots need values; int bounds must be ordered."""
        if self.type == "enum" and not self.values:
            raise ValueError("enum slot needs values")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("slot min must be <= max")
        return self


class NLUExampleConfig(BaseModel):
    """A few-shot example rendered into the static NLU prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: str = ""
    caller: str
    out: dict[str, Any]


class ActIntentRuleConfig(BaseModel):
    """(acts, pending, relation, topic) → routing intent (spec §6.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acts: list[str] = Field(default_factory=list)
    pending: Optional[str] = None
    relation: Optional[Literal["answers_pending", "answers_other", "new_topic", "unrelated", "unclear"]] = None
    topic: Optional[str] = None
    intent: str
    gated: bool = False

    @field_validator("acts")
    @classmethod
    def _known_acts(cls, value: list[str]) -> list[str]:
        """Reject acts outside the framework list."""
        bad = [a for a in value if a not in _DIALOGUE_ACTS]
        if bad:
            raise ValueError(f"unknown act(s) {bad}; allowed: {list(_DIALOGUE_ACTS)}")
        return value


class TerminationGateItem(BaseModel):
    """Either a pending id or a routing condition (spec §6.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: Optional[str] = None
    field: Optional[str] = None
    operator: Optional[RoutingOperator] = None
    value: Any = None

    @model_validator(mode="after")
    def _one_form(self) -> "TerminationGateItem":
        """Exactly one of ``pending`` or ``field``+``operator``."""
        has_pending = self.pending is not None
        has_cond = self.field is not None and self.operator is not None
        if has_pending == has_cond:
            raise ValueError("termination_gate item needs exactly one of 'pending' or 'field'+'operator'")
        return self


class TerminationGateConfig(BaseModel):
    """Conditions under which a gated act-intent row may fire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    any_of: list[TerminationGateItem] = Field(default_factory=list)


class OffTrackConfig(BaseModel):
    """Consecutive off-track turns before routing to recovery (spec §6.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold: int = Field(default=3, ge=1)
    intent: str = "off_track"


class NLUProcessorConfig(BaseModel):
    """Dialogue-act NLU: acts, relation, slots and signals per caller turn.

    The routing intent is derived in code from ``act_intents``; there is no
    intent list, entity list or free-text domain instruction (spec §16).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Per-helper provider override — see LanguageNormalisationConfig.provider.
    provider: Literal["anthropic", "openai", "ollama", "google"] | None = None
    model: str = ""
    user_state_confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    signal_intents: dict[str, str] = Field(default_factory=dict)
    # GH-218: opt-in INFO log with the full parsed NLU response JSON and the
    # final composed user message. Off by default because the values can
    # carry PII (entity values, message text). Turn on for triage windows.
    log_raw_response: bool = False
    # NLU dialogue-acts spec §7.1, §9.1.
    timeout_ms: int = Field(default=2500, gt=0)
    retry_attempts: int = Field(default=2, ge=1)
    history_turns: int = Field(default=2, ge=0)
    topics: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    slots: dict[str, NLUSlotConfig] = Field(default_factory=dict)
    known_fields: list[str] = Field(default_factory=list)
    examples: list[NLUExampleConfig] = Field(default_factory=list)
    act_intents: list[ActIntentRuleConfig] = Field(default_factory=list)
    termination_gate: TerminationGateConfig = Field(default_factory=TerminationGateConfig)
    off_track: OffTrackConfig = Field(default_factory=OffTrackConfig)


class PreprocessingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language_normalisation: LanguageNormalisationConfig = Field(
        default_factory=LanguageNormalisationConfig
    )
    nlu_processor: NLUProcessorConfig = Field(default_factory=NLUProcessorConfig)


# ---------------------------------------------------------------------------
# hitl
# ---------------------------------------------------------------------------


class HitlResponseConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    response_message: str = ""


# ---------------------------------------------------------------------------
# agent_workflow.*
# ---------------------------------------------------------------------------


class RoutingCondition(BaseModel):
    """Single predicate evaluated against a session field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    operator: RoutingOperator
    value: Any = None


class RoutingRule(BaseModel):
    """Routing decision from a subagent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str
    next_subagent_id: str
    condition: Optional[RoutingCondition] = None
    conditions: list[RoutingCondition] = Field(default_factory=list)
    session_writes: dict[str, Any] = Field(default_factory=dict)


class OptionsFromConfig(BaseModel):
    """Where a pending question's offered options come from (a cached tool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    fields: list[str] = Field(min_length=1)
    id_field: str


class PendingQuestionConfig(BaseModel):
    """What a subagent may be waiting for (NLU dialogue-acts spec §7.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    expects: str = ""
    when: list[RoutingCondition] = Field(default_factory=list)
    options_from: Optional[OptionsFromConfig] = None
    resolves_to: Optional[str] = None

    @model_validator(mode="after")
    def _resolves_needs_options(self) -> "PendingQuestionConfig":
        """``resolves_to`` is only meaningful with ``options_from``."""
        if self.resolves_to and self.options_from is None:
            raise ValueError("resolves_to requires options_from")
        return self


class SubAgent(BaseModel):
    """One subagent node in the workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    description: str = ""
    is_start: bool = False
    is_terminal: bool = False
    opening_phrase: str = ""
    special_handler: Optional[SpecialHandler] = None
    tools: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    output_format: Optional[dict[str, Any]] = None
    # Speak `fixed_opening` verbatim, without calling the model, on the FIRST
    # turn that routes here when every field in `fixed_opening_requires` is
    # present in session AND the caller's own turn carried no entities.
    #
    # For a phase whose job is one fixed sentence built from values already in
    # hand, generating that sentence adds nothing and loses it intermittently:
    # blue-dots' profile_resolve is meant to offer a returning caller their
    # saved trade and city, and instead asked for the trade in 2 of 3 runs,
    # because the values arrive under the names `stored_trade` /
    # `stored_location` and the model did not connect them to the question it
    # was about to ask.
    #
    # The entity guard is what keeps the caller in charge: if they named a
    # trade or city in the same breath, their words must win, so the model
    # handles that turn as before.
    fixed_opening: str = ""
    fixed_opening_requires: list[str] = Field(default_factory=list)
    routing: list[RoutingRule] = Field(default_factory=list)
    pending: list[PendingQuestionConfig] = Field(default_factory=list)


class AgentWorkflowConfig(BaseModel):
    """Multi-subagent workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = ""
    version: str = "1.0.0"
    agent_system_prompt: str = ""
    global_routing: list[RoutingRule] = Field(default_factory=list)
    default_fallback_subagent_id: str = ""
    global_tools: list[str] = Field(default_factory=list)
    subagents: list[SubAgent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# channels.* (GH-137) — chat channel removed as unused
# ---------------------------------------------------------------------------


class TtsRulesConfig(BaseModel):
    """TTS formatting rules for a voice channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    numbers: str = ""
    money: str = ""
    dates: str = ""
    time: str = ""
    phone: str = ""
    abbreviations: str = ""
    output_script: str = ""
    english_loanwords: str = ""
    email: str = ""
    named_entities: str = ""


class SilenceTriggerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    silence_ms: int = Field(default=0, ge=0)


class MaxWaitCeilingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_wait_ms: int = Field(default=0, ge=0)


class InterruptionConfig(BaseModel):
    """What stops an in-flight streaming turn, and how long a successor waits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_new_input: Literal["abort_and_fold", "replace"] = "abort_and_fold"
    on_disconnect: Literal["abort", "continue"] = "abort"
    drain_max_ms: int = Field(default=3000, ge=0)


class FoldConfig(BaseModel):
    """How many interrupted utterances a successor turn folds into its input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_segments: int = Field(default=3, ge=0)


class CarryoverConfig(BaseModel):
    """Lifetime of carried state and the note marking unheard tool results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_age_ms: int = Field(default=60000, ge=0)
    undelivered_note: str = ""


class TurnAssemblerConfig(BaseModel):
    """Turn-assembler policy stack and streaming-turn lifecycle.

    ``session_idle_ttl_ms`` is read from ``reach_layer.turn_assembler`` only;
    a per-channel value is accepted but has no effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    silence_trigger: SilenceTriggerConfig = Field(default_factory=SilenceTriggerConfig)
    max_wait_ceiling: MaxWaitCeilingConfig = Field(default_factory=MaxWaitCeilingConfig)
    interruption: InterruptionConfig = Field(default_factory=InterruptionConfig)
    fold: FoldConfig = Field(default_factory=FoldConfig)
    carryover: CarryoverConfig = Field(default_factory=CarryoverConfig)
    session_idle_ttl_ms: int = Field(default=1_800_000, ge=0)


class ChannelConfig(BaseModel):
    """Per-channel LLM-facing configuration (GH-137).

    ``max_tokens`` (GH-194) caps the LLM response length on this channel.
    When ``None`` the wrapper falls back to its built-in default (4096).
    Voice channels typically set a tight cap (~200) so the user never
    waits through long monologues.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_prompt_suffix: str = ""
    tts_rules: Optional[TtsRulesConfig] = None
    terminal_word: Optional[str] = None
    max_tokens: Optional[int] = Field(default=None, gt=0)
    turn_assembler: TurnAssemblerConfig = Field(default_factory=TurnAssemblerConfig)
    # GH-242: filler_threshold_ms and filler_phrase used to live here, but
    # they are read by the reach_layer voice service (not agent_core), so
    # declaring them in agent_core's config block meant they never reached
    # the voice service at runtime. Moved to
    # reach_layer/base/schema/config.py: VoiceChannelConfig.


class ChannelsConfig(BaseModel):
    """Per-channel config block. ``chat`` removed as unused in this PR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    voice: ChannelConfig = Field(default_factory=ChannelConfig)
    web: ChannelConfig = Field(default_factory=ChannelConfig)
    cli: ChannelConfig = Field(default_factory=ChannelConfig)
    mcp: ChannelConfig = Field(default_factory=ChannelConfig)
    # Bridge: the OpenAI chat-completions channel (reach_layer/bridge). Added
    # as a field because ChannelsConfig is extra="forbid", so channels.bridge
    # in a domain config is rejected at boot otherwise. Defaulted, so every
    # existing domain that omits it validates unchanged.
    bridge: ChannelConfig = Field(default_factory=ChannelConfig)


# ---------------------------------------------------------------------------
# reach_layer — top-level default turn-assembler (inherited by channels)
# ---------------------------------------------------------------------------


class ReachLayerDefaultsConfig(BaseModel):
    """Default turn-assembler policy stack consumed by Agent Core.

    Lives at the top level intentionally — Agent Core reads these as
    fallbacks when a channel does not declare its own turn_assembler
    block under ``channels.<name>``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_assembler: TurnAssemblerConfig = Field(default_factory=TurnAssemblerConfig)


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


class EntityPersistenceConfig(BaseModel):
    """Where NLU-extracted entities are written.

    ``persistent`` (the default, and the historical behaviour) writes each
    entity to the caller's profile store, so it returns on a later call via
    ``bundle.profile``. ``session`` writes to session state instead, so the
    value lives for this call only.

    Session scope matters when routing conditions read these values: a
    persistent write makes ordinary turn-to-turn flow control depend on the
    profile store being reachable, and a domain whose durable identity already
    comes from an upstream system has no reason to take that dependency.

    The orchestrator has always read ``entity_persistence.scope``; without
    this class the key was rejected by the strict schema, so the knob could
    not actually be set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["session", "persistent"] = "persistent"


class ToolResultsConfig(BaseModel):
    """Global limits for tool-result persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_user_ttl_seconds: int = Field(default=86400, gt=0)


class MemoryToolField(BaseModel):
    """One field the framework ``remember`` tool may store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Literal["session", "persistent"]
    description: str = ""
    grounded_in: list[str] = Field(default_factory=list)


class MemoryToolConfig(BaseModel):
    """The framework `remember` tool (tool-result persistence spec §8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "remember"
    fields: dict[str, MemoryToolField] = Field(min_length=1)


class SessionBootstrapStep(BaseModel):
    """One deterministic step run on the first turn of a session (session-bootstrap spec §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tool"]
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    requires_consent: bool = False


class SessionBootstrapConfig(BaseModel):
    """Steps run inline on the first turn, before routing, within ``timeout_ms``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_ms: int = Field(default=1500, gt=0)
    steps: list[SessionBootstrapStep] = Field(min_length=1)


class IdentityConfig(BaseModel):
    """Who the agent is and whether callers may be handed to a human."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["ai_assistant"] = "ai_assistant"
    operator: str = Field(min_length=1)
    disclosure: str = Field(min_length=1)
    human_handoff: Literal["none", "request"] = "none"
    no_handoff_line: str = Field(min_length=1)


class HandoffLines(BaseModel):
    """Spoken lines for human handoff outcomes (never LLM-generated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivered: str = Field(min_length=1)
    failed: str = Field(min_length=1)
    already: str = Field(min_length=1)


class HandoffConfig(BaseModel):
    """Human handoff settings: spoken lines and summary size."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lines: HandoffLines
    summary_turns: int = Field(default=6, ge=1, le=20)


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged agent_core config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)

    agent: AgentConfig = Field(default_factory=AgentConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    entity_persistence: EntityPersistenceConfig = Field(
        default_factory=EntityPersistenceConfig
    )
    entity_to_profile_field: dict[str, str] = Field(default_factory=dict)
    hitl: HitlResponseConfig = Field(default_factory=HitlResponseConfig)
    agent_workflow: AgentWorkflowConfig = Field(default_factory=AgentWorkflowConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    reach_layer: ReachLayerDefaultsConfig = Field(default_factory=ReachLayerDefaultsConfig)

    # Inter-service HTTP clients
    ke_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(
            endpoint="http://knowledge_engine:8001/retrieve"
        )
    )
    memory_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://memory_layer:8002")
    )
    trust_client: TrustClientConfig = Field(
        default_factory=lambda: TrustClientConfig(endpoint="http://trust_layer:8003")
    )
    learning_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://observability_layer:8004")
    )
    action_gateway_client: ClientConfig = Field(
        default_factory=lambda: ClientConfig(endpoint="http://action_gateway:9999")
    )

    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    tool_results: ToolResultsConfig = Field(default_factory=ToolResultsConfig)
    memory_tool: Optional[MemoryToolConfig] = None
    session_bootstrap: Optional[SessionBootstrapConfig] = None
    identity: Optional[IdentityConfig] = None
    handoff: Optional[HandoffConfig] = None

    @model_validator(mode="after")
    def _check_tool_result_rules(self) -> "MergedConfig":
        """Cross-check user-scope TTLs and memory tool names against connectors.

        Returns:
            The validated config.

        Raises:
            ValueError: If a user-scope TTL exceeds the cap, the memory tool
                name collides with a connector, a ``grounded_in`` entry is
                not a connector name, or a session bootstrap step names
                a non-read connector.
        """
        c = self.connectors
        names = {x.name for x in [*c.read, *c.write, *c.identity, *c.internal]}
        cap = self.tool_results.max_user_ttl_seconds
        for x in c.read:
            if x.cache and x.cache.scope == "user" and x.cache.ttl_seconds > cap:
                raise ValueError(
                    f"connector '{x.name}': user-scope ttl_seconds {x.cache.ttl_seconds} "
                    f"exceeds tool_results.max_user_ttl_seconds {cap}"
                )
        if self.memory_tool:
            if self.memory_tool.name in names:
                raise ValueError(f"memory_tool.name '{self.memory_tool.name}' collides with a connector")
            for fname, f in self.memory_tool.fields.items():
                for src in f.grounded_in:
                    if src not in names:
                        raise ValueError(f"memory_tool.fields.{fname}.grounded_in: unknown connector '{src}'")
        if self.session_bootstrap:
            read_names = {c.name for c in self.connectors.read}
            for i, step in enumerate(self.session_bootstrap.steps):
                if step.tool not in read_names:
                    raise ValueError(f"session_bootstrap.steps[{i}]: '{step.tool}' is not a read connector")
        if (self.identity and self.identity.human_handoff == "request"
                and (self.handoff is None
                     or not any(s.id == "handoff" for s in self.agent_workflow.subagents))):
            raise ValueError(
                "identity.human_handoff=request needs a handoff block and a 'handoff' subagent")
        return self

    @model_validator(mode="after")
    def _check_dialogue_act_rules(self) -> "MergedConfig":
        """Cross-check the dialogue-act NLU config against the workflow and connectors.

        Returns:
            The validated config.

        Raises:
            ValueError: On an undeclared pending id, an unrouted intent, an
                unknown topic, an ``options_from`` tool without a cache policy,
                or a state key colliding with a ``memory_tool`` field.
        """
        nlu = self.preprocessing.nlu_processor
        wf = self.agent_workflow
        declared = {p.id for s in wf.subagents for p in s.pending}

        def _pending_ok(pid: str | None, where: str) -> None:
            if pid and pid not in declared:
                raise ValueError(f"{where}: undeclared pending id '{pid}'")

        for name, slot in nlu.slots.items():
            for pid in slot.accept_when_pending:
                _pending_ok(pid, f"preprocessing.nlu_processor.slots.{name}.accept_when_pending")
        for i, ex in enumerate(nlu.examples):
            _pending_ok(ex.pending, f"preprocessing.nlu_processor.examples[{i}]")
        for i, row in enumerate(nlu.act_intents):
            _pending_ok(row.pending, f"preprocessing.nlu_processor.act_intents[{i}]")
            if row.topic is not None and row.topic not in nlu.topics:
                raise ValueError(f"preprocessing.nlu_processor.act_intents[{i}]: topic '{row.topic}' is not in topics")
        for i, item in enumerate(nlu.termination_gate.any_of):
            _pending_ok(item.pending, f"preprocessing.nlu_processor.termination_gate.any_of[{i}]")

        routed = {r.intent for s in wf.subagents for r in s.routing} | {r.intent for r in wf.global_routing}
        for i, row in enumerate(nlu.act_intents):
            if row.intent not in routed and row.intent not in _FRAMEWORK_HANDLED_INTENTS:
                raise ValueError(
                    f"preprocessing.nlu_processor.act_intents[{i}]: intent '{row.intent}' "
                    f"is not used by any routing rule")
        if wf.subagents and nlu.off_track.intent not in routed:
            raise ValueError(
                f"preprocessing.nlu_processor.off_track.intent '{nlu.off_track.intent}' "
                f"is not used by any routing rule")

        cached = {c.name for c in self.connectors.read if c.cache is not None}
        state_keys = {self.entity_to_profile_field.get(n, n) for n in nlu.slots}
        for s in wf.subagents:
            for p in s.pending:
                if p.options_from and p.options_from.tool not in cached:
                    raise ValueError(
                        f"subagent '{s.id}' pending '{p.id}': options_from.tool "
                        f"'{p.options_from.tool}' has no cache policy")
                if p.resolves_to:
                    state_keys.add(p.resolves_to)
        if self.memory_tool:
            clash = state_keys & set(self.memory_tool.fields)
            if clash:
                raise ValueError(
                    f"dialogue_act state key '{sorted(clash)[0]}' collides with memory_tool field")
        return self

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
