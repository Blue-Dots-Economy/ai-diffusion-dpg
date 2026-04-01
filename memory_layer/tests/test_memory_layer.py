"""
memory_layer/tests/test_memory_layer.py

Unit tests for MemoryLayer — the top-level orchestrator.

All store dependencies (RedisSessionStore, Neo4jUserStore, Neo4jJourneyStore,
Neo4jContextStore) are injected as mocks. No real Redis or Neo4j connections.

Covers:
- Normal execution: all 5 public methods return correct results
- Edge cases: new vs returning user, empty session state, lazy cleanup
- Failure scenarios: store exceptions are absorbed and safe defaults returned
- New: user_storage_mode field drives DPDP delete decision (replaces old consent field)
- New: is_returning preserved correctly after session adoption
- New: default_storage_mode config fallback when user_storage_mode absent
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch, call

from src.memory_layer import MemoryLayer, _build_initial_session, _build_scope_map


# ---------------------------------------------------------------------------
# Minimal config for constructing MemoryLayer in tests
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "redis": {"host": "localhost", "port": 6379, "db": 0},
    "neo4j": {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": "test",
        "connection_timeout_s": 1,
    },
    "state": {
        "session": {
            "ttl_minutes": 60,
            "schema": {
                "trade": {"default": ""},
                "loop_count": {"default": 0},
                "options_presented": {"default": []},
            },
        },
        "persistent": {
            "graph": {
                "subnodes": {
                    "UserProfile": {
                        "declared_fields": ["trade", "education"]
                    },
                    "JourneyHistory": {
                        "child": {
                            "children": [
                                {"label": "Role", "rel": "OFFERED", "fields": ["title"]},
                                {"label": "DropOff", "rel": "DROPPED_AT", "fields": ["node"]},
                            ]
                        }
                    },
                }
            },
            "merge_on_session_end": [
                {"session_field": "trade", "target": "Journey.trade_outcome"},
            ],
        },
    },
}


def _make_layer() -> tuple[MemoryLayer, dict]:
    """
    Construct a MemoryLayer with all external dependencies patched.
    Returns (layer, stores) where stores is a dict of the mock store instances.
    """
    with (
        patch("src.memory_layer.RedisSessionStore") as MockRedis,
        patch("src.memory_layer.Neo4jUserStore") as MockUser,
        patch("src.memory_layer.Neo4jJourneyStore") as MockJourney,
        patch("src.memory_layer.Neo4jContextStore") as MockContext,
        patch("src.memory_layer.GraphDatabase") as MockGDB,
    ):
        mock_redis = MagicMock()
        mock_user = MagicMock()
        mock_journey = MagicMock()
        mock_context = MagicMock()
        MockRedis.return_value = mock_redis
        MockUser.return_value = mock_user
        MockJourney.return_value = mock_journey
        MockContext.return_value = mock_context
        MockGDB.driver.return_value = MagicMock()

        layer = MemoryLayer(MINIMAL_CONFIG)

    stores = {
        "redis": mock_redis,
        "user": mock_user,
        "journey": mock_journey,
        "context": mock_context,
    }
    return layer, stores


def _make_layer_with_config(config: dict) -> tuple[MemoryLayer, dict]:
    """Construct a MemoryLayer with a custom config."""
    with (
        patch("src.memory_layer.RedisSessionStore") as MockRedis,
        patch("src.memory_layer.Neo4jUserStore") as MockUser,
        patch("src.memory_layer.Neo4jJourneyStore") as MockJourney,
        patch("src.memory_layer.Neo4jContextStore") as MockContext,
        patch("src.memory_layer.GraphDatabase") as MockGDB,
    ):
        mock_redis = MagicMock()
        mock_user = MagicMock()
        mock_journey = MagicMock()
        mock_context = MagicMock()
        MockRedis.return_value = mock_redis
        MockUser.return_value = mock_user
        MockJourney.return_value = mock_journey
        MockContext.return_value = mock_context
        MockGDB.driver.return_value = MagicMock()

        layer = MemoryLayer(config)

    stores = {
        "redis": mock_redis,
        "user": mock_user,
        "journey": mock_journey,
        "context": mock_context,
    }
    return layer, stores


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        with patch("src.memory_layer.GraphDatabase"):
            with patch("src.memory_layer.RedisSessionStore"):
                MemoryLayer(None)


def test_init_builds_correct_ttl():
    layer, _ = _make_layer()
    assert layer._ttl_seconds == 3600


def test_init_builds_scope_map_with_session_fields():
    layer, _ = _make_layer()
    assert layer._scope_map["loop_count"] == "session"
    assert layer._scope_map["options_presented"] == "session"


def test_init_builds_scope_map_with_persistent_fields():
    layer, _ = _make_layer()
    # declared_fields = ["trade", "education"]; declared_fields override schema
    assert layer._scope_map["trade"] == "persistent"
    assert layer._scope_map["education"] == "persistent"


def test_init_builds_scope_map_with_journey_events():
    layer, _ = _make_layer()
    assert layer._scope_map["offered"] == "journey_event"
    assert layer._scope_map["dropped_at"] == "journey_event"


# ---------------------------------------------------------------------------
# context_bundle — new user (cold path)
# ---------------------------------------------------------------------------

def test_context_bundle_new_user_creates_graph_and_journey():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["user"].user_exists.return_value = False
    stores["user"].get_profile.return_value = {}

    result = layer.context_bundle("sess-1", "user-1")

    stores["user"].create_user_graph.assert_called_once_with("user-1")
    stores["journey"].create_journey.assert_called_once_with("user-1", "sess-1")
    stores["redis"].init_session.assert_called_once()
    stores["redis"].register_session.assert_called_once_with("user-1", "sess-1")


def test_context_bundle_new_user_returns_empty_journey():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["user"].user_exists.return_value = False

    result = layer.context_bundle("sess-1", "user-1")

    assert "session" in result
    assert "profile" in result
    assert result["journey"] is None


def test_context_bundle_new_user_seeds_schema_defaults():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["user"].user_exists.return_value = False

    result = layer.context_bundle("sess-1", "user-1")

    session = result["session"]
    assert session["trade"] == ""
    assert session["is_returning"] == "false"
    assert session["user_id"] == "user-1"
    assert session["journey_id"] == "sess-1"


# ---------------------------------------------------------------------------
# context_bundle — returning user (cold path with profile pre-population)
# ---------------------------------------------------------------------------

def test_context_bundle_returning_user_loads_profile_and_last_journey():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["user"].user_exists.return_value = True
    stores["user"].get_profile.return_value = {"language": "hindi", "trade": "electrician", "attributes": []}
    stores["journey"].get_last_journey_summary.return_value = {
        "journey_id": "prev-sess",
        "started_at": "2024-01-01T10:00:00Z",
        "ended_at": "2024-01-01T11:00:00Z",
        "end_reason": "completed",
        "outcomes": [],
    }
    stores["context"].get_signals_for_journey.return_value = []

    result = layer.context_bundle("sess-2", "user-1")

    stores["user"].create_user_graph.assert_not_called()
    assert result["profile"]["language"] == "hindi"
    assert result["journey"]["journey_id"] == "prev-sess"


def test_context_bundle_returning_user_marks_is_returning_true():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = False
    stores["user"].user_exists.return_value = True
    stores["user"].get_profile.return_value = {}
    stores["journey"].get_last_journey_summary.return_value = None

    result = layer.context_bundle("sess-2", "user-1")
    assert result["session"]["is_returning"] == "true"


# ---------------------------------------------------------------------------
# context_bundle — session adoption preserves is_returning
# ---------------------------------------------------------------------------

def test_context_bundle_adoption_preserves_is_returning_for_returning_user():
    """Adopted stale is_returning=false must not overwrite the fresh Neo4j result."""
    layer, stores = _make_layer()
    stores["user"].user_exists.return_value = True       # returning user
    stores["user"].get_profile.return_value = {}
    stores["journey"].get_last_journey_summary.return_value = None

    # new-sess does not exist; old-sess does exist (will be adopted)
    stores["redis"].session_exists.side_effect = lambda s: s == "old-sess"
    stores["redis"].get_user_sessions.return_value = {"old-sess": "2024-01-01T10:00:00Z"}
    # old session has stale is_returning=false
    stores["redis"].get_session.return_value = {"is_returning": "false", "trade": "welder"}

    result = layer.context_bundle("new-sess", "user-1")

    # After adoption, is_returning must reflect Neo4j (True), not the stale adopted value
    assert result["session"]["is_returning"] == "true"


# ---------------------------------------------------------------------------
# context_bundle — existing session (hot path)
# ---------------------------------------------------------------------------

def test_context_bundle_existing_session_reads_from_redis():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = True
    stores["redis"].get_session.return_value = {"current_subagent_id": "jobs", "trade": "welder"}
    stores["user"].get_profile.return_value = {"language": "hindi"}

    result = layer.context_bundle("sess-1", "user-1")

    stores["redis"].reset_session_ttl.assert_called_once_with("sess-1")
    stores["redis"].update_last_accessed.assert_called_once_with("user-1", "sess-1")
    assert result["session"]["current_subagent_id"] == "jobs"
    assert result["profile"]["language"] == "hindi"


def test_context_bundle_existing_session_skips_graph_creation():
    layer, stores = _make_layer()
    stores["redis"].session_exists.return_value = True
    stores["redis"].get_session.return_value = {}
    stores["user"].get_profile.return_value = {}

    layer.context_bundle("sess-1", "user-1")

    stores["user"].create_user_graph.assert_not_called()
    stores["journey"].create_journey.assert_not_called()


# ---------------------------------------------------------------------------
# context_bundle — failure handling
# ---------------------------------------------------------------------------

def test_context_bundle_empty_session_id_returns_empty_bundle():
    layer, stores = _make_layer()
    result = layer.context_bundle("", "user-1")
    assert result == {"session": {}, "profile": {}, "journey": None}


def test_context_bundle_empty_user_id_returns_empty_bundle():
    layer, stores = _make_layer()
    result = layer.context_bundle("sess-1", "")
    assert result == {"session": {}, "profile": {}, "journey": None}


def test_context_bundle_redis_error_returns_empty_bundle():
    layer, stores = _make_layer()
    stores["redis"].session_exists.side_effect = ConnectionError("redis down")
    result = layer.context_bundle("sess-1", "user-1")
    assert result == {"session": {}, "profile": {}, "journey": None}


# ---------------------------------------------------------------------------
# write — session scope
# ---------------------------------------------------------------------------

def test_write_session_scope_calls_redis_hset():
    layer, stores = _make_layer()
    layer.write("sess-1", "user-1", "session", "current_subagent_id", "jobs")
    stores["redis"].set_session_field.assert_called_once_with("sess-1", "current_subagent_id", "jobs")
    stores["redis"].update_last_accessed.assert_called_once_with("user-1", "sess-1")


def test_write_unknown_scope_falls_back_to_scope_map():
    """An unrecognized scope string causes scope_map lookup to determine the target store."""
    layer, stores = _make_layer()
    # "education" is in declared_fields → persistent in scope_map
    layer.write("sess-1", "user-1", "UNRECOGNIZED_SCOPE", "education", "secondary")
    stores["user"].upsert_profile_field.assert_called_once()
    stores["redis"].set_session_field.assert_not_called()


# ---------------------------------------------------------------------------
# write — persistent scope
# ---------------------------------------------------------------------------

def test_write_persistent_scope_calls_user_store():
    layer, stores = _make_layer()
    layer._scope_map["custom_field"] = "persistent"
    layer.write("sess-1", "user-1", "persistent", "custom_field", "value")
    stores["user"].upsert_profile_field.assert_called_once()


# ---------------------------------------------------------------------------
# write — signal scope
# ---------------------------------------------------------------------------

def test_write_signal_scope_creates_signal():
    layer, stores = _make_layer()
    layer._scope_map["my_signal"] = "signal"
    signal_value = {"type": "objection", "turn": "3", "raw": "nahi chahiye"}
    layer.write("sess-1", "user-1", "signal", "my_signal", signal_value)
    stores["context"].create_signal.assert_called_once_with(
        user_id="user-1",
        journey_id="sess-1",
        signal_type="objection",
        turn="3",
        raw="nahi chahiye",
        attributes=None,
    )


def test_write_signal_scope_raises_for_non_dict_value():
    layer, stores = _make_layer()
    layer._scope_map["signal"] = "signal"
    # The exception is caught internally — no raise to caller
    layer.write("sess-1", "user-1", "signal", "signal", "not-a-dict")
    stores["context"].create_signal.assert_not_called()


# ---------------------------------------------------------------------------
# write — journey_event scope
# ---------------------------------------------------------------------------

def test_write_journey_event_creates_child_node():
    layer, stores = _make_layer()
    layer._scope_map["offered"] = "journey_event"
    event_value = {"label": "Role", "title": "Welder"}
    layer.write("sess-1", "user-1", "journey_event", "offered", event_value)
    stores["journey"].create_journey_child.assert_called_once()


# ---------------------------------------------------------------------------
# write — failure handling
# ---------------------------------------------------------------------------

def test_write_empty_session_id_is_absorbed():
    layer, stores = _make_layer()
    layer.write("", "user-1", "session", "key", "val")
    stores["redis"].set_session_field.assert_not_called()


def test_write_empty_key_is_absorbed():
    layer, stores = _make_layer()
    layer.write("sess-1", "user-1", "session", "", "val")
    stores["redis"].set_session_field.assert_not_called()


def test_write_redis_error_is_absorbed():
    layer, stores = _make_layer()
    stores["redis"].set_session_field.side_effect = ConnectionError("down")
    layer.write("sess-1", "user-1", "session", "current_subagent_id", "jobs")  # must not raise


# ---------------------------------------------------------------------------
# flush_session
# ---------------------------------------------------------------------------

def test_flush_session_promotes_fields_closes_journey_deletes_session():
    layer, stores = _make_layer()
    stores["redis"].get_session.return_value = {"trade": "welder", "user_storage_mode": "saved"}

    layer.flush_session("sess-1", "user-1", "completed")

    stores["journey"].merge_session_fields.assert_called_once()
    stores["journey"].close_journey.assert_called_once_with("user-1", "sess-1", "completed")
    stores["redis"].delete_session.assert_called_once_with("sess-1")
    stores["redis"].remove_session_from_user_index.assert_called_once_with("user-1", "sess-1")


def test_flush_session_user_storage_mode_anonymous_triggers_delete_user():
    """user_storage_mode='anonymous' in session → DPDP delete of user graph."""
    layer, stores = _make_layer()
    stores["redis"].get_session.return_value = {"user_storage_mode": "anonymous"}

    layer.flush_session("sess-1", "user-1", "no_consent")

    stores["user"].delete_user.assert_called_once_with("user-1")


def test_flush_session_user_storage_mode_saved_no_delete_user():
    """user_storage_mode='saved' in session → user graph is retained."""
    layer, stores = _make_layer()
    stores["redis"].get_session.return_value = {"user_storage_mode": "saved"}

    layer.flush_session("sess-1", "user-1", "completed")

    stores["user"].delete_user.assert_not_called()


def test_flush_session_absent_user_storage_mode_uses_default_saved():
    """When user_storage_mode is absent and default_mode=saved, user graph is retained."""
    layer, stores = _make_layer()
    # MINIMAL_CONFIG has no user_data_persistence → default = "saved"
    stores["redis"].get_session.return_value = {"trade": "welder"}  # no user_storage_mode

    layer.flush_session("sess-1", "user-1", "completed")

    stores["user"].delete_user.assert_not_called()


def test_flush_session_absent_user_storage_mode_uses_default_anonymous():
    """When user_storage_mode is absent and default_mode=anonymous, user graph is deleted."""
    anon_config = {
        **MINIMAL_CONFIG,
        "user_data_persistence": {"default_mode": "anonymous"},
    }
    layer, stores = _make_layer_with_config(anon_config)
    stores["redis"].get_session.return_value = {"trade": "welder"}  # no user_storage_mode

    layer.flush_session("sess-1", "user-1", "completed")

    stores["user"].delete_user.assert_called_once_with("user-1")


def test_flush_session_empty_session_state_still_closes_journey():
    """If Redis session already expired (returns {}), still close Journey."""
    layer, stores = _make_layer()
    stores["redis"].get_session.return_value = {}

    layer.flush_session("sess-1", "user-1", "timeout")

    stores["journey"].merge_session_fields.assert_not_called()
    stores["journey"].close_journey.assert_called_once()


def test_flush_session_empty_session_id_is_absorbed():
    layer, stores = _make_layer()
    layer.flush_session("", "user-1", "completed")
    stores["journey"].close_journey.assert_not_called()


def test_flush_session_redis_error_is_absorbed():
    layer, stores = _make_layer()
    stores["redis"].get_session.side_effect = ConnectionError("down")
    layer.flush_session("sess-1", "user-1", "error")  # must not raise


# ---------------------------------------------------------------------------
# get_active_sessions
# ---------------------------------------------------------------------------

def test_get_active_sessions_returns_live_sessions_sorted():
    layer, stores = _make_layer()
    stores["redis"].get_user_sessions.return_value = {
        "sess-a": "2024-01-01T10:00:00Z",
        "sess-b": "2024-01-01T11:00:00Z",
    }
    stores["redis"].session_exists.side_effect = lambda s: True  # both alive

    result = layer.get_active_sessions("user-1")

    assert len(result) == 2
    # sess-b (later timestamp) should come first
    assert result[0]["session_id"] == "sess-b"


def test_get_active_sessions_removes_stale_sessions():
    layer, stores = _make_layer()
    stores["redis"].get_user_sessions.return_value = {
        "alive-sess": "2024-01-01T11:00:00Z",
        "dead-sess": "2024-01-01T09:00:00Z",
    }
    stores["redis"].session_exists.side_effect = lambda s: s == "alive-sess"

    result = layer.get_active_sessions("user-1")

    assert len(result) == 1
    assert result[0]["session_id"] == "alive-sess"
    stores["redis"].remove_stale_session_field.assert_called_once_with("user-1", "dead-sess")


def test_get_active_sessions_empty_user_id_returns_empty():
    layer, stores = _make_layer()
    result = layer.get_active_sessions("")
    assert result == []
    stores["redis"].get_user_sessions.assert_not_called()


def test_get_active_sessions_no_sessions_returns_empty():
    layer, stores = _make_layer()
    stores["redis"].get_user_sessions.return_value = {}
    result = layer.get_active_sessions("user-1")
    assert result == []


def test_get_active_sessions_redis_error_returns_empty():
    layer, stores = _make_layer()
    stores["redis"].get_user_sessions.side_effect = ConnectionError("down")
    result = layer.get_active_sessions("user-1")
    assert result == []


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------

def test_delete_user_calls_neo4j_and_redis():
    layer, stores = _make_layer()
    layer.delete_user("user-1")
    stores["user"].delete_user.assert_called_once_with("user-1")
    stores["redis"].delete_user_index.assert_called_once_with("user-1")


def test_delete_user_empty_id_is_absorbed():
    layer, stores = _make_layer()
    layer.delete_user("")
    stores["user"].delete_user.assert_not_called()


def test_delete_user_error_is_absorbed():
    layer, stores = _make_layer()
    stores["user"].delete_user.side_effect = Exception("neo4j down")
    layer.delete_user("user-1")  # must not raise


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def test_build_initial_session_includes_infrastructure_fields():
    schema = {"trade": {"default": ""}}
    state = _build_initial_session("sess-1", "user-1", schema, False)
    assert state["user_id"] == "user-1"
    assert state["journey_id"] == "sess-1"
    assert state["is_returning"] == "false"


def test_build_initial_session_serialises_list_default():
    schema = {"options_presented": {"default": []}}
    state = _build_initial_session("s", "u", schema, False)
    assert state["options_presented"] == "[]"


def test_build_initial_session_serialises_bool_default():
    schema = {"active": {"default": True}}
    state = _build_initial_session("s", "u", schema, False)
    assert state["active"] == "true"


def test_build_scope_map_session_fields_from_schema():
    scope_map = _build_scope_map(
        {"loop_count": {}, "options_presented": {}},
        ["education"],
        [{"label": "Role", "rel": "OFFERED"}],
    )
    assert scope_map["loop_count"] == "session"
    assert scope_map["options_presented"] == "session"


def test_build_scope_map_persistent_from_declared_fields():
    scope_map = _build_scope_map({}, ["trade", "education"], [])
    assert scope_map["trade"] == "persistent"
    assert scope_map["education"] == "persistent"


def test_build_scope_map_journey_event_from_children():
    scope_map = _build_scope_map({}, [], [{"label": "Role", "rel": "OFFERED"}])
    assert scope_map["offered"] == "journey_event"


def test_build_scope_map_always_includes_infrastructure_keys():
    scope_map = _build_scope_map({}, [], [])
    assert scope_map["user_id"] == "session"
    assert scope_map["journey_id"] == "session"
    assert scope_map["is_returning"] == "session"
    assert scope_map["signal"] == "signal"
