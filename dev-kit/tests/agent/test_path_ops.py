"""Tests for path_ops: get/set/clear with [name=X] list-of-objects syntax."""
from dev_kit.agent.path_ops import get_path, set_path, clear_path


def test_get_simple_dotted_path():
    data = {"agent": {"timeout_ms": 10000}}
    assert get_path(data, "agent.timeout_ms") == 10000


def test_get_missing_returns_none():
    data = {"agent": {}}
    assert get_path(data, "agent.timeout_ms") is None


def test_get_list_of_objects_by_name():
    data = {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval", "route": "knowledge_engine"},
                {"name": "other", "route": "other"},
            ]
        }
    }
    assert get_path(data, "connectors.internal[name=knowledge_retrieval]") == {
        "name": "knowledge_retrieval",
        "route": "knowledge_engine",
    }
    assert get_path(data, "connectors.internal[name=knowledge_retrieval].route") == "knowledge_engine"


def test_get_list_of_objects_missing_match_returns_none():
    data = {"connectors": {"internal": [{"name": "other"}]}}
    assert get_path(data, "connectors.internal[name=missing]") is None


def test_set_simple_dotted_path_creates_nested():
    data = {}
    set_path(data, "agent.timeout_ms", 5000)
    assert data == {"agent": {"timeout_ms": 5000}}


def test_set_list_of_objects_appends_when_missing():
    data = {"connectors": {"internal": []}}
    set_path(data, "connectors.internal[name=knowledge_retrieval].route", "knowledge_engine")
    assert data == {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval", "route": "knowledge_engine"}
            ]
        }
    }


def test_set_list_of_objects_updates_existing():
    data = {"connectors": {"internal": [{"name": "knowledge_retrieval", "route": "old"}]}}
    set_path(data, "connectors.internal[name=knowledge_retrieval].route", "knowledge_engine")
    assert data["connectors"]["internal"][0]["route"] == "knowledge_engine"


def test_clear_simple_path_removes_key():
    data = {"agent": {"timeout_ms": 10000, "retry_attempts": 2}}
    clear_path(data, "agent.timeout_ms")
    assert data == {"agent": {"retry_attempts": 2}}


def test_clear_list_of_objects_removes_matching_element():
    data = {
        "connectors": {
            "internal": [
                {"name": "knowledge_retrieval"},
                {"name": "other"},
            ]
        }
    }
    clear_path(data, "connectors.internal[name=knowledge_retrieval]")
    assert data == {"connectors": {"internal": [{"name": "other"}]}}


def test_clear_missing_is_noop():
    data = {"agent": {}}
    clear_path(data, "agent.timeout_ms")  # should not raise
    assert data == {"agent": {}}


def test_set_list_of_objects_multiple_keys():
    """Composite key shouldn't break; only single [key=value] supported."""
    data = {}
    set_path(data, "subagents[id=enquiry].name", "Enquiry")
    assert data == {"subagents": [{"id": "enquiry", "name": "Enquiry"}]}
