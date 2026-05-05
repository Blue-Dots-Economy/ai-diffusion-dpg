"""Domain schemas for memory_layer block.

Sections written by the LLM during the memory phase. Includes typed
sub-models (ChildNodeConfig, AdhocNodeConfig) for the persistent graph
hierarchy — earlier loose `dict` types let typos through.
RESERVED_SESSION_FIELD_NAMES forbids re-declaring framework-injected
session fields (silent overwrite hazard).
"""
from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_kit.schemas.enums import (
    StorageMode, PersistentBackend, SessionFieldType, ReengagementChannel,
)


class SessionFieldDefinition(BaseModel):
    """One entry in state.session.schema. type='enum' requires non-empty values."""
    model_config = ConfigDict(extra="forbid")
    type: SessionFieldType
    values: Optional[list[str]] = None
    default: Any = None

    @model_validator(mode="after")
    def enum_requires_values(self) -> "SessionFieldDefinition":
        """Validate enum schema. Empty-string default is allowed — represents
        "not yet set" so the orchestrator's first-turn write can populate it
        (e.g. KKB's `income_urgency` starts as "" until user expresses urgency).
        """
        if self.type == SessionFieldType.enum:
            if not self.values:
                raise ValueError("type='enum' requires non-empty 'values' list")
            if self.default not in (None, "") and self.default not in self.values:
                raise ValueError(f"default '{self.default}' must be one of values {self.values}")
        return self


# Framework-injected and lifecycle-managed session field names.
# Declaring any of these in state.session.schema would silently overwrite
# the framework value at session init — a quiet bug that's hard to trace.
#
# Note: language_preference is intentionally NOT reserved. Domains
# (e.g. edubot-india) legitimately declare it in their session schema to
# pin a default language; the orchestrator's first-turn detection still
# overrides anything the schema initialises if the user actually sends text.
RESERVED_SESSION_FIELD_NAMES: frozenset[str] = frozenset({
    # Infrastructure injected at session init (memory_layer.py:_build_initial_session)
    "user_id",
    "journey_id",
    "is_returning",
    # Session lifecycle latches (must reset per session)
    "opening_phrase_emitted",
    # Framework routing/state managed by Agent Core orchestrator
    "current_subagent_id",
    "was_adopted",
    "last_response",
    # Intermediate buffers
    "pending_user_message",
    "pending_normalised_input",
})


class SessionStateConfig(BaseModel):
    """Session-level state config. ttl_minutes capped at 1 week."""
    model_config = ConfigDict(extra="forbid")
    ttl_minutes: int = Field(..., gt=0, le=10080)
    schema: dict[str, SessionFieldDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def schema_must_not_use_reserved_names(self) -> "SessionStateConfig":
        """Forbid re-declaring framework-injected session fields.

        If a domain declares e.g. `user_id` in schema, the framework's value
        silently overwrites the declared default at session init. Surface
        this hazard at devkit time as a hard error.
        """
        conflicts = set(self.schema.keys()) & RESERVED_SESSION_FIELD_NAMES
        if conflicts:
            raise ValueError(
                f"state.session.schema declares reserved framework fields: "
                f"{sorted(conflicts)}. These are managed by Memory Layer / Agent "
                f"Core and must not be redeclared in domain config — they would "
                f"be silently overwritten at session init."
            )
        return self


class UserNodeConfig(BaseModel):
    """The persistent graph's user (root) node."""
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)


class AdhocNodeConfig(BaseModel):
    """Ad-hoc attribute subnode (free-form key/value pairs on the graph)."""
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., min_length=1)
    rel: str = Field(..., min_length=1)
    fields: list[str] = Field(default_factory=list)


class ChildNodeConfig(BaseModel):
    """Child node under a subnode's `child` or `children`. Recursive."""
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., min_length=1)
    rel: str = Field(..., min_length=1)
    fields: list[str] = Field(default_factory=list)
    children: Optional[list["ChildNodeConfig"]] = None
    adhoc: Optional[AdhocNodeConfig] = None


class SubnodeConfig(BaseModel):
    """One subnode hanging off the user node. Mirrors runtime memory_layer schema."""
    model_config = ConfigDict(extra="forbid")
    rel: str = Field(..., min_length=1)
    grouping: bool = False
    declared_fields: list[str] = Field(default_factory=list)
    child: Optional[ChildNodeConfig] = None
    children: Optional[list[ChildNodeConfig]] = None
    adhoc: Optional[AdhocNodeConfig] = None


class GraphConfig(BaseModel):
    """The persistent graph topology. user_node is required."""
    model_config = ConfigDict(extra="forbid")
    user_node: UserNodeConfig
    subnodes: dict[str, SubnodeConfig] = Field(default_factory=dict)


class MergeRule(BaseModel):
    """Rule for merging session field → graph node at session end."""
    model_config = ConfigDict(extra="forbid")
    session_field: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)


class PersistentStateConfig(BaseModel):
    """Persistent graph state — backend + topology + merge rules."""
    model_config = ConfigDict(extra="forbid")
    backend: PersistentBackend = PersistentBackend.memgraph
    graph: GraphConfig
    merge_on_session_end: list[MergeRule] = Field(default_factory=list)


class StateSection(BaseModel):
    """memory_layer.state — session + persistent state config.

    persistent is optional — runtime memory_layer reads `.get("persistent", {})`
    and informational/stateless agents (e.g. obsrv-docs-assistant) skip the
    persistent graph entirely.
    """
    model_config = ConfigDict(extra="forbid")
    session: SessionStateConfig
    persistent: Optional[PersistentStateConfig] = None


class UserDataPersistenceSection(BaseModel):
    """memory_layer.user_data_persistence — global default storage mode."""
    model_config = ConfigDict(extra="forbid")
    default_mode: StorageMode = StorageMode.saved


class ReengagementTrigger(BaseModel):
    """One re-engagement rule. GH-168: declared, runtime not yet wired."""
    model_config = ConfigDict(extra="forbid")
    event: str = Field(..., min_length=1)
    delay_hours: Optional[int] = Field(default=None, gt=0)
    channel: Optional[ReengagementChannel] = None
    message_template: Optional[str] = Field(default=None, min_length=1)
    loop_threshold: Optional[int] = Field(default=None, gt=0)
    action: Optional[str] = Field(default=None, min_length=1)


class ReengagementSection(BaseModel):
    """memory_layer.reengagement — list of triggers (deferred until GH-168)."""
    model_config = ConfigDict(extra="forbid")
    triggers: list[ReengagementTrigger] = Field(default_factory=list)


class ObservabilitySection(BaseModel):
    """memory_layer.observability — domain identifier."""
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(..., min_length=1)
