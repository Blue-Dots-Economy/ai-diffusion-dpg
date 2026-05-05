"""Tests for memory_layer domain schemas."""
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.memory_layer import (
    AdhocNodeConfig,
    ChildNodeConfig,
    GraphConfig,
    MergeRule,
    ObservabilitySection,
    PersistentStateConfig,
    ReengagementSection,
    ReengagementTrigger,
    RESERVED_SESSION_FIELD_NAMES,
    SessionFieldDefinition,
    SessionStateConfig,
    StateSection,
    SubnodeConfig,
    UserDataPersistenceSection,
    UserNodeConfig,
)


# -- SessionFieldDefinition --------------------------------------------------

def test_session_field_string():
    f = SessionFieldDefinition(type="string")
    assert f.values is None
    assert f.default is None


def test_session_field_int():
    f = SessionFieldDefinition(type="int", default=0)
    assert f.default == 0


def test_session_field_list():
    """SessionFieldType.list_ is the 4th valid type — not validated by enum_requires_values."""
    f = SessionFieldDefinition(type="list")
    assert f.values is None
    assert f.default is None


def test_session_field_enum_requires_values():
    with pytest.raises(ValidationError, match="enum"):
        SessionFieldDefinition(type="enum", values=None)


def test_session_field_enum_default_must_be_in_values():
    SessionFieldDefinition(type="enum", values=["a", "b"], default="a")
    with pytest.raises(ValidationError, match="default"):
        SessionFieldDefinition(type="enum", values=["a", "b"], default="c")


def test_session_field_enum_no_default_allowed():
    """Enum without default is valid."""
    f = SessionFieldDefinition(type="enum", values=["a", "b"])
    assert f.default is None


def test_session_field_extra_forbidden():
    with pytest.raises(ValidationError):
        SessionFieldDefinition(type="string", unknown="x")


# -- SessionStateConfig ------------------------------------------------------

def test_session_state_ttl_required():
    with pytest.raises(ValidationError):
        SessionStateConfig()


def test_session_state_ttl_bounds():
    SessionStateConfig(ttl_minutes=1)
    SessionStateConfig(ttl_minutes=10080)  # 1 week
    with pytest.raises(ValidationError):
        SessionStateConfig(ttl_minutes=0)
    with pytest.raises(ValidationError):
        SessionStateConfig(ttl_minutes=10081)


def test_session_state_schema_optional():
    s = SessionStateConfig(ttl_minutes=1440)
    assert s.schema == {}


def test_session_state_schema_with_fields():
    s = SessionStateConfig(
        ttl_minutes=1440,
        schema={"location": SessionFieldDefinition(type="string")},
    )
    assert "location" in s.schema


def test_reserved_session_names_forbidden():
    """Schema must reject framework-managed field names."""
    for reserved in ("user_id", "journey_id", "is_returning", "opening_phrase_emitted",
                      "current_subagent_id", "last_response"):
        with pytest.raises(ValidationError, match="reserved"):
            SessionStateConfig(
                ttl_minutes=60,
                schema={reserved: SessionFieldDefinition(type="string")},
            )


def test_language_preference_can_be_declared():
    """language_preference is NOT reserved — domains may legitimately declare it.

    Edubot-india pins a default language via state.session.schema; the orchestrator's
    first-turn detection still updates it when the user actually sends text.
    """
    s = SessionStateConfig(
        ttl_minutes=60,
        schema={"language_preference": SessionFieldDefinition(
            type="enum", values=["english", "hindi"], default="english"
        )},
    )
    assert "language_preference" in s.schema


def test_reserved_field_names_constant_complete():
    """RESERVED_SESSION_FIELD_NAMES contains all known framework fields."""
    expected = {
        "user_id", "journey_id", "is_returning", "opening_phrase_emitted",
        "current_subagent_id", "was_adopted", "last_response",
        "pending_user_message", "pending_normalised_input",
    }
    assert RESERVED_SESSION_FIELD_NAMES == expected


# -- UserNodeConfig ----------------------------------------------------------

def test_user_node_required_fields():
    UserNodeConfig(label="User", key="user_id")
    with pytest.raises(ValidationError):
        UserNodeConfig(label="", key="user_id")
    with pytest.raises(ValidationError):
        UserNodeConfig(label="User", key="")


# -- AdhocNodeConfig ---------------------------------------------------------

def test_adhoc_node_minimal():
    a = AdhocNodeConfig(label="Attribute", rel="HAS_ATTR")
    assert a.fields == []


def test_adhoc_node_extra_forbidden():
    with pytest.raises(ValidationError):
        AdhocNodeConfig(label="x", rel="y", unknown="z")


# -- ChildNodeConfig (recursive) ---------------------------------------------

def test_child_node_minimal():
    c = ChildNodeConfig(label="Role", rel="HAS_ROLE")
    assert c.fields == []
    assert c.children is None
    assert c.adhoc is None


def test_child_node_recursive():
    """children list contains more ChildNodeConfig — recursive."""
    c = ChildNodeConfig(
        label="Journey",
        rel="HAS_JOURNEY",
        children=[
            ChildNodeConfig(label="Step", rel="HAS_STEP"),
        ],
    )
    assert c.children[0].label == "Step"


def test_child_node_recursive_depth_three():
    """Verify nested children at depth 3+ work — confirms forward-reference resolution."""
    c = ChildNodeConfig(
        label="Journey",
        rel="HAS_JOURNEY",
        children=[
            ChildNodeConfig(
                label="Step",
                rel="HAS_STEP",
                children=[ChildNodeConfig(label="Milestone", rel="HAS_MILESTONE")],
            ),
        ],
    )
    assert c.children[0].children[0].label == "Milestone"


def test_child_node_with_adhoc():
    c = ChildNodeConfig(
        label="Context",
        rel="HAS_CTX",
        adhoc=AdhocNodeConfig(label="ContextAttr", rel="HAS_ATTR"),
    )
    assert c.adhoc.label == "ContextAttr"


# -- SubnodeConfig -----------------------------------------------------------

def test_subnode_rel_required():
    with pytest.raises(ValidationError):
        SubnodeConfig(rel="")


def test_subnode_with_typed_child():
    """child must be ChildNodeConfig, not raw dict."""
    s = SubnodeConfig(rel="HAS_X", child=ChildNodeConfig(label="X", rel="X_REL"))
    assert s.child.label == "X"


def test_subnode_with_typed_children_list():
    s = SubnodeConfig(
        rel="HAS_X",
        children=[ChildNodeConfig(label="A", rel="A_REL"), ChildNodeConfig(label="B", rel="B_REL")],
    )
    assert len(s.children) == 2


def test_subnode_extra_forbidden_on_child():
    """Bad keys inside ChildNodeConfig are caught (was hidden by loose dict before)."""
    with pytest.raises(ValidationError):
        SubnodeConfig(
            rel="HAS_X",
            child=ChildNodeConfig(label="X", rel="X_REL", bogus_field="y"),
        )


# -- GraphConfig -------------------------------------------------------------

def test_graph_user_node_required():
    with pytest.raises(ValidationError):
        GraphConfig()


def test_graph_full():
    g = GraphConfig(
        user_node=UserNodeConfig(label="User", key="user_id"),
        subnodes={"Profile": SubnodeConfig(rel="HAS_PROFILE")},
    )
    assert "Profile" in g.subnodes


# -- MergeRule ---------------------------------------------------------------

def test_merge_rule_required_fields():
    MergeRule(session_field="mood", target="UserProfile.last_mood")
    with pytest.raises(ValidationError):
        MergeRule(session_field="", target="x")
    with pytest.raises(ValidationError):
        MergeRule(session_field="x", target="")


# -- PersistentStateConfig ---------------------------------------------------

def test_persistent_default_backend():
    p = PersistentStateConfig(graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id")))
    assert p.backend.value == "memgraph"


def test_persistent_neo4j_backend():
    p = PersistentStateConfig(
        backend="neo4j",
        graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id")),
    )
    assert p.backend.value == "neo4j"


def test_persistent_invalid_backend():
    with pytest.raises(ValidationError):
        PersistentStateConfig(
            backend="dynamodb",
            graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id")),
        )


# -- StateSection ------------------------------------------------------------

def test_state_section_full():
    s = StateSection(
        session=SessionStateConfig(ttl_minutes=1440),
        persistent=PersistentStateConfig(
            graph=GraphConfig(user_node=UserNodeConfig(label="User", key="user_id"))
        ),
    )
    assert s.session.ttl_minutes == 1440


def test_state_section_extra_forbidden():
    with pytest.raises(ValidationError):
        StateSection(
            session=SessionStateConfig(ttl_minutes=1440),
            persistent=PersistentStateConfig(
                graph=GraphConfig(user_node=UserNodeConfig(label="U", key="id"))
            ),
            unknown="x",
        )


# -- UserDataPersistenceSection ----------------------------------------------

def test_user_data_persistence_default():
    u = UserDataPersistenceSection()
    assert u.default_mode.value == "saved"


def test_user_data_persistence_anonymous():
    u = UserDataPersistenceSection(default_mode="anonymous")
    assert u.default_mode.value == "anonymous"


def test_user_data_persistence_invalid_mode():
    with pytest.raises(ValidationError):
        UserDataPersistenceSection(default_mode="cached")


# -- ReengagementTrigger -----------------------------------------------------

def test_reengagement_trigger_event_required():
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="")


def test_reengagement_delay_hours_positive():
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="x", delay_hours=0)


def test_reengagement_channel_enum():
    for c in ("outbound_call", "whatsapp", "sms"):
        ReengagementTrigger(event="x", channel=c)
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="x", channel="email")


def test_reengagement_message_template_rejects_empty():
    """Optional[str] empty rejection pattern."""
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="x", message_template="")


def test_reengagement_action_rejects_empty():
    with pytest.raises(ValidationError):
        ReengagementTrigger(event="x", action="")


# -- ReengagementSection -----------------------------------------------------

def test_reengagement_section_default_empty():
    r = ReengagementSection()
    assert r.triggers == []


# -- ObservabilitySection ----------------------------------------------------

def test_observability_section_domain_required():
    with pytest.raises(ValidationError):
        ObservabilitySection()


def test_observability_section_extra_forbidden():
    with pytest.raises(ValidationError):
        ObservabilitySection(domain="kkb", typo="x")
