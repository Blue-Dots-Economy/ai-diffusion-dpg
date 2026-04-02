"""
memory_layer/tests/test_audit_store.py

Unit tests for SQLiteAuditStore.
"""

import os
import pytest
from src.audit_store import SQLiteAuditStore

DB_PATH = "test_audit.db"


@pytest.fixture
def audit_store():
    # Ensure clean state
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    store = SQLiteAuditStore(DB_PATH)
    yield store
    
    # Cleanup
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def test_record_session_start(audit_store):
    audit_store.record_session_event("session_1", "user_1", "start")
    
    history = audit_store.get_history("session_1")
    # No turns yet, so history should be empty, but we can check the table via direct query if we wanted.
    # For now, let's verify turn recording which also triggers session start.
    assert len(history) == 0


def test_record_turn_history(audit_store):
    audit_store.record_turn_history(
        session_id="s1",
        user_id="u1",
        turn_id="t1",
        user_msg="Hello",
        system_msg="Hi there!",
        subagent_id="agent_a",
        intent="greeting",
        model="gpt-4",
        latency_ms=100,
        metadata={"foo": "bar"}
    )
    
    history = audit_store.get_history("s1")
    assert len(history) == 1
    turn = history[0]
    assert turn["turn_id"] == "t1"
    assert turn["user_message"] == "Hello"
    assert turn["system_message"] == "Hi there!"
    assert turn["subagent_id"] == "agent_a"
    assert turn["intent"] == "greeting"
    assert turn["latency_ms"] == 100


def test_record_multiple_turns(audit_store):
    audit_store.record_turn_history("s1", "u1", "t1", "hi", "hello")
    audit_store.record_turn_history("s1", "u1", "t2", "how are you?", "I am fine")
    
    history = audit_store.get_history("s1")
    assert len(history) == 2
    assert history[0]["turn_id"] == "t1"
    assert history[1]["turn_id"] == "t2"


def test_record_session_end(audit_store):
    audit_store.record_session_event("s1", "u1", "start")
    audit_store.record_session_event("s1", "u1", "end", reason="user_completed")
    
    # Verify by checking session_audit table directly
    with audit_store._get_connection() as conn:
        row = conn.execute("SELECT * FROM session_audit WHERE session_id = 's1'").fetchone()
        assert row["status"] == "ended"
        assert row["end_reason"] == "user_completed"
        assert row["closed_at"] is not None


def test_record_session_escalate(audit_store):
    audit_store.record_session_event("s1", "u1", "start")
    audit_store.record_session_event("s1", "u1", "escalate", reason="hitl")
    
    with audit_store._get_connection() as conn:
        row = conn.execute("SELECT * FROM session_audit WHERE session_id = 's1'").fetchone()
        assert row["status"] == "escalated"
        assert row["end_reason"] == "hitl"


def test_session_resumption(audit_store):
    # 1. Start and end session
    audit_store.record_session_event("s1", "u1", "start")
    audit_store.record_session_event("s1", "u1", "end", reason="done")
    
    with audit_store._get_connection() as conn:
        row = conn.execute("SELECT * FROM session_audit WHERE session_id = 's1'").fetchone()
        assert row["status"] == "ended"
        assert row["closed_at"] is not None
        assert row["end_reason"] == "done"

    # 2. Resume session
    audit_store.record_session_event("s1", "u1", "start")
    
    with audit_store._get_connection() as conn:
        row = conn.execute("SELECT * FROM session_audit WHERE session_id = 's1'").fetchone()
        assert row["status"] == "active"
        assert row["closed_at"] is None
        assert row["end_reason"] is None
