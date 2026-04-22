"""
MergedConfig — strict schema for the Memory Layer merged runtime config.

Merged config = dev-kit/dpg/memory_layer.yaml (framework defaults + Redis/
                Memgraph connection settings) deep-merged with a domain YAML
                (e.g. dev-kit/configs/kkb/memory_layer.yaml).

Every model sets ``extra="forbid"``: unknown keys at any nesting level
fail at startup with a pydantic ValidationError, not at first request.

Open-map sub-sections — ``state.session.schema`` (domain field names)
and ``state.persistent.graph.subnodes`` (domain graph node names) —
are intentionally modelled as ``dict[str, <inner>]`` because the top-level
keys under them are operator-defined, not fixed by the framework.

Belongs to the Memory Layer DPG block.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SessionFieldType(str, Enum):
    """Supported types for a domain session field."""

    enum = "enum"
    string = "string"
    int = "int"
    list = "list"


class PersistentBackend(str, Enum):
    """Supported persistent-state backends."""

    memgraph = "memgraph"
    neo4j = "neo4j"


class StorageMode(str, Enum):
    """User data persistence mode.

    - ``saved``: profile is retained across sessions.
    - ``anonymous``: profile is deleted on session end (DPDP-compliant).
    """

    saved = "saved"
    anonymous = "anonymous"


class ReengagementChannel(str, Enum):
    """Outbound channel used by a re-engagement trigger."""

    outbound_call = "outbound_call"
    whatsapp = "whatsapp"
    sms = "sms"


# ---------------------------------------------------------------------------
# Framework / infrastructure sections
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    """Uvicorn bind settings for the Memory Layer entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8002, gt=0, lt=65536)


class RedisConfig(BaseModel):
    """Redis connection settings used by the session store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "redis"
    port: int = Field(default=6379, gt=0, lt=65536)
    db: int = Field(default=0, ge=0)
    password: Optional[str] = None
    socket_timeout_ms: int = Field(default=2000, gt=0)
    socket_connect_timeout_ms: int = Field(default=2000, gt=0)


class MemgraphConfig(BaseModel):
    """Memgraph / Neo4j-compatible bolt connection settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = "bolt://memgraph:7687"
    user: str = "memgraph"
    password: Optional[str] = None
    connection_timeout_s: int = Field(default=5, gt=0)


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
# State: session
# ---------------------------------------------------------------------------


class SessionFieldDefinition(BaseModel):
    """One declared domain session field.

    Attributes:
        type: Field datatype. ``enum`` fields must also set ``values``.
        values: Allowed values when ``type=enum``; ignored otherwise.
        default: Default value written when a new session starts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: SessionFieldType
    values: Optional[list[str]] = None
    default: Any = None


class SessionConfig(BaseModel):
    """Session-scoped state config.

    The session ``schema`` (YAML key) is an open map keyed by domain-
    defined field names (``mental_state``, ``market_signal``, …). It is
    exposed on the model as ``fields_schema`` to avoid shadowing the v1
    ``BaseModel.schema()`` method; the YAML/dict key remains ``schema``
    via the populate_by_name alias.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    ttl_minutes: int = Field(default=1440, gt=0)
    fields_schema: dict[str, SessionFieldDefinition] = Field(
        default_factory=dict, alias="schema"
    )


# ---------------------------------------------------------------------------
# State: persistent graph
# ---------------------------------------------------------------------------


class UserNodeConfig(BaseModel):
    """Top-level user graph node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    key: str


class AdhocNodeConfig(BaseModel):
    """Ad-hoc attribute subnode for free-form key/value pairs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    rel: str
    fields: list[str] = Field(default_factory=list)


class ChildNodeConfig(BaseModel):
    """Child node under a subnode's ``child`` or ``children``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    rel: str
    fields: list[str] = Field(default_factory=list)
    children: Optional[list["ChildNodeConfig"]] = None
    adhoc: Optional[AdhocNodeConfig] = None


class SubnodeConfig(BaseModel):
    """One subnode hanging off the user node.

    Either ``declared_fields`` (flat properties) or ``grouping: true``
    (structural anchor with nested children) patterns are supported.
    ``child`` / ``children`` / ``adhoc`` model nested structure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rel: str
    grouping: bool = False
    declared_fields: list[str] = Field(default_factory=list)
    child: Optional[ChildNodeConfig] = None
    children: Optional[list[ChildNodeConfig]] = None
    adhoc: Optional[AdhocNodeConfig] = None


class GraphConfig(BaseModel):
    """Graph topology for the persistent store.

    ``subnodes`` is an open map keyed by domain-defined node names
    (``UserProfile``, ``JourneyHistory``, …).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_node: UserNodeConfig
    subnodes: dict[str, SubnodeConfig] = Field(default_factory=dict)


class MergeRule(BaseModel):
    """One session-end promotion rule.

    Promotes a final session field value to a graph node property when
    ``flush_session`` runs.

    Attributes:
        session_field: Source session field name.
        target: Graph path, e.g. ``Journey.mental_state_at_end`` or
            ``Role`` (the latter creates OFFERED edges for each value).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_field: str
    target: str


class PersistentConfig(BaseModel):
    """Persistent-state config — backend + graph topology."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: PersistentBackend = PersistentBackend.memgraph
    graph: GraphConfig
    merge_on_session_end: list[MergeRule] = Field(default_factory=list)


class StateConfig(BaseModel):
    """Combined session + persistent state config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionConfig = Field(default_factory=SessionConfig)
    persistent: Optional[PersistentConfig] = None


# ---------------------------------------------------------------------------
# User data persistence
# ---------------------------------------------------------------------------


class UserDataPersistenceConfig(BaseModel):
    """Default storage mode applied when the session has no explicit choice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_mode: StorageMode = StorageMode.saved


# ---------------------------------------------------------------------------
# Re-engagement (unimplemented — see GH-168)
# ---------------------------------------------------------------------------


class ReengagementTrigger(BaseModel):
    """One re-engagement rule fired on drop-off or loop threshold.

    NOTE: No runtime scheduler consumes these today — tracked in GH-168.
    Config is still validated at startup so typos are caught early.

    Attributes:
        event: Drop-off event code, e.g. DOP_MT, DOP_EG, DOP_RL.
        delay_hours: Delay before firing when time-based.
        channel: Outbound channel for time-based triggers.
        message_template: Template id used by the channel.
        loop_threshold: Fires when session loop_count reaches this.
        action: Action id for non-channel triggers, e.g. hitl_counsellor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: str
    delay_hours: Optional[int] = Field(default=None, gt=0)
    channel: Optional[ReengagementChannel] = None
    message_template: Optional[str] = None
    loop_threshold: Optional[int] = Field(default=None, gt=0)
    action: Optional[str] = None


class ReengagementConfig(BaseModel):
    """Re-engagement trigger rules — scheduler not yet implemented (GH-168)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    triggers: list[ReengagementTrigger] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level merged config
# ---------------------------------------------------------------------------


class MergedConfig(BaseModel):
    """Strict schema for the fully-merged memory_layer config.

    Validates the deep-merged result of dev-kit/dpg/memory_layer.yaml
    and the domain-specific YAML. Unknown keys at any nesting level fail
    at startup rather than silently passing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    memgraph: MemgraphConfig = Field(default_factory=MemgraphConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    user_data_persistence: UserDataPersistenceConfig = Field(
        default_factory=UserDataPersistenceConfig
    )
    reengagement: ReengagementConfig = Field(default_factory=ReengagementConfig)

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
