"""
tests/test_neo4j_stores.py

Unit tests for Neo4jUserStore, Neo4jContextStore, and Neo4jJourneyStore.
All tests mock the Neo4j driver — no real database connection required.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager

from src.neo4j_user_store import Neo4jUserStore
from src.neo4j_context_store import Neo4jContextStore
from src.neo4j_journey_store import Neo4jJourneyStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_driver(session_mock=None):
    """Return a mock Neo4j driver with a context-manager session()."""
    driver = MagicMock()
    if session_mock is None:
        session_mock = MagicMock()
    # driver.session() used as context manager: __enter__ returns session_mock
    driver.session.return_value.__enter__ = MagicMock(return_value=session_mock)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session_mock


# ---------------------------------------------------------------------------
# Neo4jUserStore — constructor
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreInit:
    def test_none_driver_raises(self):
        with pytest.raises(ValueError, match="driver must not be None"):
            Neo4jUserStore(driver=None, declared_fields=["trade"])

    def test_none_declared_fields_raises(self):
        driver, _ = make_driver()
        with pytest.raises(ValueError, match="declared_fields must not be None"):
            Neo4jUserStore(driver=driver, declared_fields=None)

    def test_init_success(self):
        driver, _ = make_driver()
        store = Neo4jUserStore(driver=driver, declared_fields=["trade", "location"])
        assert store is not None


# ---------------------------------------------------------------------------
# Neo4jUserStore — user_exists
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreUserExists:
    def test_user_exists_returns_true_when_count_positive(self):
        driver, session = make_driver()
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda k: 1 if k == "cnt" else None)
        session.run.return_value.single.return_value = record

        store = Neo4jUserStore(driver=driver, declared_fields=[])
        assert store.user_exists("user1") is True

    def test_user_exists_returns_false_when_count_zero(self):
        driver, session = make_driver()
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda k: 0 if k == "cnt" else None)
        session.run.return_value.single.return_value = record

        store = Neo4jUserStore(driver=driver, declared_fields=[])
        assert store.user_exists("user1") is False

    def test_user_exists_returns_false_when_no_record(self):
        driver, session = make_driver()
        session.run.return_value.single.return_value = None

        store = Neo4jUserStore(driver=driver, declared_fields=[])
        assert store.user_exists("user1") is False

    def test_user_exists_returns_false_on_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")

        store = Neo4jUserStore(driver=driver, declared_fields=[])
        assert store.user_exists("user1") is False


# ---------------------------------------------------------------------------
# Neo4jUserStore — create_user_graph
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreCreateUserGraph:
    def test_create_user_graph_runs_cypher(self):
        driver, session = make_driver()
        store = Neo4jUserStore(driver=driver, declared_fields=[])
        store.create_user_graph("user1")
        session.run.assert_called_once()

    def test_create_user_graph_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jUserStore(driver=driver, declared_fields=[])
        # Should not raise
        store.create_user_graph("user1")


# ---------------------------------------------------------------------------
# Neo4jUserStore — get_profile
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreGetProfile:
    def test_get_profile_returns_declared_fields(self):
        driver, session = make_driver()

        # First run() call: profile properties
        profile_record = MagicMock()
        profile_record.__getitem__ = MagicMock(
            side_effect=lambda k: {"user_id": "user1", "trade": "electrician"} if k == "props" else None
        )
        profile_result = MagicMock()
        profile_result.single.return_value = profile_record

        # Second run() call: attributes (empty)
        attr_result = MagicMock()
        attr_result.__iter__ = MagicMock(return_value=iter([]))

        session.run.side_effect = [profile_result, attr_result]

        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        profile = store.get_profile("user1")

        assert profile.get("trade") == "electrician"
        assert "user_id" not in profile
        assert "attributes" in profile

    def test_get_profile_returns_empty_dict_when_no_record(self):
        driver, session = make_driver()
        result = MagicMock()
        result.single.return_value = None
        session.run.return_value = result

        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        assert store.get_profile("user1") == {}

    def test_get_profile_returns_empty_on_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        assert store.get_profile("user1") == {}


# ---------------------------------------------------------------------------
# Neo4jUserStore — upsert_profile_field
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreUpsertProfileField:
    def test_upsert_declared_field_calls_set_declared(self):
        driver, session = make_driver()
        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        store.upsert_profile_field("user1", "trade", "electrician")
        session.run.assert_called_once()

    def test_upsert_undeclared_field_calls_upsert_attribute(self):
        driver, session = make_driver()
        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        store.upsert_profile_field("user1", "custom_field", "some_value")
        session.run.assert_called_once()

    def test_upsert_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jUserStore(driver=driver, declared_fields=["trade"])
        # Should not raise
        store.upsert_profile_field("user1", "trade", "electrician")


# ---------------------------------------------------------------------------
# Neo4jUserStore — delete_user
# ---------------------------------------------------------------------------


class TestNeo4jUserStoreDeleteUser:
    def test_delete_user_runs_cypher(self):
        driver, session = make_driver()
        store = Neo4jUserStore(driver=driver, declared_fields=[])
        store.delete_user("user1")
        session.run.assert_called_once()

    def test_delete_user_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jUserStore(driver=driver, declared_fields=[])
        # Should not raise
        store.delete_user("user1")


# ---------------------------------------------------------------------------
# Neo4jContextStore — constructor
# ---------------------------------------------------------------------------


class TestNeo4jContextStoreInit:
    def test_none_driver_raises(self):
        with pytest.raises(ValueError, match="driver must not be None"):
            Neo4jContextStore(driver=None)

    def test_init_success(self):
        driver, _ = make_driver()
        store = Neo4jContextStore(driver=driver)
        assert store is not None


# ---------------------------------------------------------------------------
# Neo4jContextStore — create_signal
# ---------------------------------------------------------------------------


class TestNeo4jContextStoreCreateSignal:
    def test_create_signal_calls_run(self):
        driver, session = make_driver()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=42)
        session.run.return_value.single.return_value = record

        store = Neo4jContextStore(driver=driver)
        store.create_signal(
            user_id="user1",
            journey_id="sess1",
            signal_type="objection",
            turn="1",
            raw="I don't want this",
        )
        session.run.assert_called()

    def test_create_signal_with_attributes(self):
        driver, session = make_driver()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=99)
        run_result = MagicMock()
        run_result.single.return_value = record
        session.run.return_value = run_result

        store = Neo4jContextStore(driver=driver)
        store.create_signal(
            user_id="user1",
            journey_id="sess1",
            signal_type="constraint",
            turn="2",
            raw="only in Hubli",
            attributes=[{"key": "location", "value": "Hubli", "raw": "only in Hubli"}],
        )
        # Should have called run() twice: once for signal, once for attribute
        assert session.run.call_count == 2

    def test_create_signal_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jContextStore(driver=driver)
        # Should not raise
        store.create_signal(
            user_id="user1",
            journey_id="sess1",
            signal_type="objection",
            turn="1",
            raw="raw text",
        )


# ---------------------------------------------------------------------------
# Neo4jContextStore — get_signals_for_journey
# ---------------------------------------------------------------------------


class TestNeo4jContextStoreGetSignals:
    def test_get_signals_returns_list(self):
        driver, session = make_driver()
        record1 = MagicMock()
        record1.__getitem__ = MagicMock(
            side_effect=lambda k: {"type": "objection", "turn": "1", "raw": "text"}[k]
        )
        record1.get = MagicMock(side_effect=lambda k, default="": {"raw": "text"}.get(k, default))
        session.run.return_value.__iter__ = MagicMock(return_value=iter([record1]))

        store = Neo4jContextStore(driver=driver)
        signals = store.get_signals_for_journey("user1", "sess1")
        assert isinstance(signals, list)
        assert len(signals) == 1

    def test_get_signals_returns_empty_on_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jContextStore(driver=driver)
        assert store.get_signals_for_journey("user1", "sess1") == []


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — constructor
# ---------------------------------------------------------------------------


JOURNEY_CHILDREN = [
    {"label": "Role", "rel": "OFFERED", "fields": ["title", "location", "trade"]},
    {"label": "DropOff", "rel": "DROPPED_AT", "fields": ["step", "reason"]},
]


class TestNeo4jJourneyStoreInit:
    def test_none_driver_raises(self):
        with pytest.raises(ValueError, match="driver must not be None"):
            Neo4jJourneyStore(driver=None, journey_children=[])

    def test_none_journey_children_raises(self):
        driver, _ = make_driver()
        with pytest.raises(ValueError, match="journey_children must not be None"):
            Neo4jJourneyStore(driver=driver, journey_children=None)

    def test_init_success(self):
        driver, _ = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        assert store is not None


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — create_journey
# ---------------------------------------------------------------------------


class TestNeo4jJourneyStoreCreateJourney:
    def test_create_journey_calls_run(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.create_journey("user1", "sess1")
        session.run.assert_called_once()

    def test_create_journey_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        # Should not raise
        store.create_journey("user1", "sess1")


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — close_journey
# ---------------------------------------------------------------------------


class TestNeo4jJourneyStoreCloseJourney:
    def test_close_journey_calls_run(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.close_journey("user1", "sess1", "termination_intent")
        session.run.assert_called_once()

    def test_close_journey_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        # Should not raise
        store.close_journey("user1", "sess1", "termination_intent")


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — get_last_journey_summary
# ---------------------------------------------------------------------------


class TestNeo4jJourneyStoreGetLastJourneySummary:
    def test_returns_none_when_no_prior_journey(self):
        driver, session = make_driver()
        result = MagicMock()
        result.single.return_value = None
        session.run.return_value = result

        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        assert store.get_last_journey_summary("user1", "curr_sess") is None

    def test_returns_summary_dict_with_outcomes(self):
        driver, session = make_driver()

        # First run: journey record
        journey_record = MagicMock()
        journey_record.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "journey_id": "prev_sess",
                "started_at": "2026-01-01T00:00:00Z",
                "ended_at": "2026-01-01T01:00:00Z",
                "end_reason": "termination_intent",
            }[k]
        )
        journey_record.get = MagicMock(return_value="termination_intent")

        first_result = MagicMock()
        first_result.single.return_value = journey_record

        # Subsequent runs: child node queries (empty)
        child_result = MagicMock()
        child_result.__iter__ = MagicMock(return_value=iter([]))

        session.run.side_effect = [first_result, child_result, child_result]

        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        summary = store.get_last_journey_summary("user1", "curr_sess")

        assert summary is not None
        assert summary["journey_id"] == "prev_sess"
        assert "outcomes" in summary

    def test_returns_none_on_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        assert store.get_last_journey_summary("user1", "sess1") is None


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — create_journey_child
# ---------------------------------------------------------------------------


class TestNeo4jJourneyStoreCreateJourneyChild:
    def test_create_known_child_calls_run(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.create_journey_child(
            "user1", "sess1", "Role", {"title": "Electrician", "location": "Hubli", "trade": "electrical"}
        )
        session.run.assert_called_once()

    def test_create_unknown_child_label_skips_gracefully(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.create_journey_child("user1", "sess1", "UnknownLabel", {"key": "val"})
        # Should not call run at all
        session.run.assert_not_called()

    def test_create_journey_child_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        # Should not raise
        store.create_journey_child("user1", "sess1", "Role", {"title": "Cook"})


# ---------------------------------------------------------------------------
# Neo4jJourneyStore — merge_session_fields
# ---------------------------------------------------------------------------


class TestNeo4jJourneyStoreMergeSessionFields:
    def test_merge_journey_fields_calls_run(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.merge_session_fields(
            "user1", "sess1",
            session_state={"current_node": "profile_collection", "loop_count": 2},
            merge_rules=[
                {"session_field": "current_node", "target": "Journey.last_step"},
            ],
        )
        session.run.assert_called_once()

    def test_merge_skips_non_journey_targets(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.merge_session_fields(
            "user1", "sess1",
            session_state={"trade": "electrician"},
            merge_rules=[
                {"session_field": "trade", "target": "Role.trade"},  # not Journey.*
            ],
        )
        # No Journey.* targets → no DB call
        session.run.assert_not_called()

    def test_merge_skips_empty_values(self):
        driver, session = make_driver()
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        store.merge_session_fields(
            "user1", "sess1",
            session_state={"current_node": ""},  # empty string → skip
            merge_rules=[
                {"session_field": "current_node", "target": "Journey.last_step"},
            ],
        )
        session.run.assert_not_called()

    def test_merge_session_fields_absorbs_exception(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("DB down")
        store = Neo4jJourneyStore(driver=driver, journey_children=JOURNEY_CHILDREN)
        # Should not raise
        store.merge_session_fields(
            "user1", "sess1",
            session_state={"current_node": "profile_collection"},
            merge_rules=[{"session_field": "current_node", "target": "Journey.last_step"}],
        )
