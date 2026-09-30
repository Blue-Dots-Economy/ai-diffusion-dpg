"""Unit tests for ToolResultStore (fakeredis-backed)."""

from __future__ import annotations

import json

import fakeredis
import pytest

from src.tool_result_store import ToolResultStore

SECRET = "test-secret"


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def store(client):
    return ToolResultStore(client, SECRET, session_ttl_seconds=3600, max_user_ttl_seconds=86400)


def test_disabled_without_secret(client):
    s = ToolResultStore(client, "", session_ttl_seconds=3600, max_user_ttl_seconds=86400)
    assert s.enabled is False
    assert s.put("user", "u1", "fetch_profile", "ab12", {"a": 1}, 60) is False
    assert s.read("s1", "u1") == []
    assert client.keys("*") == []


def test_pseudonym_is_stable_and_not_raw(store):
    p = store.pseudonym("919876543210")
    assert p == store.pseudonym("919876543210")
    assert "919876543210" not in p
    assert len(p) == 32


def test_put_then_read_returns_entry(store):
    assert store.put("user", "u1", "fetch_profile", "ab12", {"items": []}, 60, now=1000.0)
    entries = store.read("s1", "u1", now=1010.0)
    assert len(entries) == 1
    e = entries[0]
    assert (e["tool"], e["args_hash"], e["data"], e["scope"]) == ("fetch_profile", "ab12", {"items": []}, "user")
    assert e["fetched_at"] == 1000.0 and e["expires_at"] == 1060.0


def test_key_contains_no_raw_ids(store, client):
    store.put("session", "919876543210", "fetch_jobs", "ab12", {}, 60)
    assert all("919876543210" not in k for k in client.keys("*"))


def test_redis_ttl_set_from_write_and_read_does_not_extend(store, client):
    store.put("user", "u1", "fetch_profile", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    before = client.ttl(key)
    store.read("s1", "u1")
    assert 0 < client.ttl(key) <= before <= 60


def test_index_gets_expiry_and_grows_to_longest_entry(store, client):
    store.put("user", "u1", "a", "ab12", {}, 60)
    idx = client.keys("ml:tr:idx:u:*")[0]
    assert 0 < client.ttl(idx) <= 60          # NX set it; GT alone would leave -1
    store.put("user", "u1", "b", "ab12", {}, 600)
    assert client.ttl(idx) > 60               # GT extended it
    store.put("user", "u1", "c", "ab12", {}, 30)
    assert client.ttl(idx) > 60               # a shorter entry never shrinks it


def test_ttl_clamped_to_scope_cap(store, client):
    store.put("session", "s1", "t", "ab12", {}, 999999)
    key = [k for k in client.keys("ml:tr:s:*") if ":idx:" not in k][0]
    assert client.ttl(key) <= 3600


def test_expired_by_clock_is_filtered(store):
    store.put("user", "u1", "t", "ab12", {}, 60, now=1000.0)
    assert store.read("s1", "u1", now=1061.0) == []


def test_missing_entry_key_is_skipped_and_index_cleaned(store, client):
    store.put("user", "u1", "t", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    client.delete(key)
    assert store.read("s1", "u1") == []
    idx = client.keys("ml:tr:idx:u:*")
    assert idx == [] or client.smembers(idx[0]) == set()


def test_corrupt_entry_is_skipped(store, client):
    store.put("user", "u1", "t", "ab12", {}, 60)
    key = [k for k in client.keys("ml:tr:u:*") if ":idx:" not in k][0]
    client.set(key, "not-json", ex=60)
    assert store.read("s1", "u1") == []


def test_session_and_user_scopes_both_read(store):
    store.put("session", "s1", "fetch_jobs", "aa11", {"j": 1}, 60)
    store.put("user", "u1", "fetch_profile", "bb22", {"p": 1}, 60)
    tools = sorted(e["tool"] for e in store.read("s1", "u1"))
    assert tools == ["fetch_jobs", "fetch_profile"]


def test_invalidate_removes_all_entries_for_tool_only(store):
    store.put("user", "u1", "fetch_profile", "aa11", {}, 60)
    store.put("user", "u1", "fetch_profile", "bb22", {}, 60)
    store.put("user", "u1", "fetch_jobs", "cc33", {}, 60)
    assert store.invalidate("user", "u1", "fetch_profile") == 2
    assert [e["tool"] for e in store.read("s1", "u1")] == ["fetch_jobs"]


def test_delete_owner_removes_entries_and_index(store, client):
    store.put("user", "u1", "t", "aa11", {}, 60)
    store.delete_owner("user", "u1")
    assert client.keys("ml:tr:*") == []


def test_redis_errors_are_swallowed(store, client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(client, "pipeline", boom)
    assert store.put("user", "u1", "t", "aa11", {}, 60) is False
    assert store.read("s1", "u1") == []
    assert store.invalidate("user", "u1", "t") == 0
    store.delete_owner("user", "u1")  # does not raise


def test_rejects_unknown_scope_and_bad_names(store):
    assert store.put("agent", "u1", "t", "aa11", {}, 60) is False
    assert store.put("user", "u1", "bad:tool", "aa11", {}, 60) is False
    assert store.put("user", "u1", "t", "XYZ", {}, 60) is False
