"""
memory_layer/tests/test_session_store.py

Unit tests for RedisSessionStore.

Covers:
- Normal execution: all methods return correct results for valid inputs
- Edge cases: missing keys, empty inputs, user index TTL behaviour
- Failure scenarios: Redis exceptions swallowed / logged, returns safe defaults
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

from src.session_store import RedisSessionStore, _serialise_value, _serialise_mapping


# ---------------------------------------------------------------------------
# Helpers — build a store with a fully mocked redis.Redis client
# ---------------------------------------------------------------------------

CONFIG = {
    "redis": {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        "socket_timeout_ms": 1000,
        "socket_connect_timeout_ms": 1000,
    }
}
TTL = 3600


def _make_store() -> tuple[RedisSessionStore, MagicMock]:
    """Return (store, mock_client) with Redis patched out."""
    with patch("src.session_store.redis.Redis") as MockRedis:
        mock_client = MagicMock()
        MockRedis.return_value = mock_client
        store = RedisSessionStore(CONFIG, TTL)
    # Inject the mock client directly so tests can assert on it
    store._client = mock_client
    return store, mock_client


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        with patch("src.session_store.redis.Redis"):
            RedisSessionStore(None, TTL)


def test_non_positive_ttl_raises():
    with pytest.raises(ValueError, match="ttl_seconds must be a positive integer"):
        with patch("src.session_store.redis.Redis"):
            RedisSessionStore(CONFIG, 0)


def test_negative_ttl_raises():
    with pytest.raises(ValueError, match="ttl_seconds must be a positive integer"):
        with patch("src.session_store.redis.Redis"):
            RedisSessionStore(CONFIG, -1)


# ---------------------------------------------------------------------------
# session_exists
# ---------------------------------------------------------------------------

def test_session_exists_returns_true_when_key_exists():
    store, client = _make_store()
    client.exists.return_value = 1
    assert store.session_exists("sess-1") is True
    client.exists.assert_called_once_with("session:sess-1")


def test_session_exists_returns_false_when_key_missing():
    store, client = _make_store()
    client.exists.return_value = 0
    assert store.session_exists("sess-1") is False


def test_session_exists_returns_false_on_redis_error():
    store, client = _make_store()
    client.exists.side_effect = ConnectionError("redis down")
    assert store.session_exists("sess-err") is False


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

def test_get_session_returns_hash_contents():
    store, client = _make_store()
    client.hgetall.return_value = {"current_node": "greeting", "trade": "electrician"}
    result = store.get_session("sess-1")
    assert result == {"current_node": "greeting", "trade": "electrician"}
    client.hgetall.assert_called_once_with("session:sess-1")


def test_get_session_returns_empty_dict_when_key_missing():
    store, client = _make_store()
    client.hgetall.return_value = {}
    result = store.get_session("missing-sess")
    assert result == {}


def test_get_session_returns_empty_dict_on_redis_error():
    store, client = _make_store()
    client.hgetall.side_effect = ConnectionError("timeout")
    result = store.get_session("sess-err")
    assert result == {}


# ---------------------------------------------------------------------------
# init_session
# ---------------------------------------------------------------------------

def test_init_session_writes_hash_and_sets_ttl():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.init_session("sess-1", {"current_node": "greeting", "is_returning": False})

    mock_pipe.hset.assert_called_once()
    call_kwargs = mock_pipe.hset.call_args
    assert call_kwargs[0][0] == "session:sess-1"
    mock_pipe.expire.assert_called_once_with("session:sess-1", TTL)
    mock_pipe.execute.assert_called_once()


def test_init_session_serialises_bool_values():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.init_session("sess-2", {"is_returning": True, "consent": False})

    mapping_arg = mock_pipe.hset.call_args[1]["mapping"]
    assert mapping_arg["is_returning"] == "true"
    assert mapping_arg["consent"] == "false"


def test_init_session_swallows_redis_error():
    store, client = _make_store()
    client.pipeline.side_effect = RuntimeError("pipe error")
    # Must not raise
    store.init_session("sess-err", {"key": "val"})


# ---------------------------------------------------------------------------
# set_session_field
# ---------------------------------------------------------------------------

def test_set_session_field_writes_field_and_resets_ttl():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.set_session_field("sess-1", "trade", "electrician")

    mock_pipe.hset.assert_called_once_with("session:sess-1", "trade", "electrician")
    mock_pipe.expire.assert_called_once_with("session:sess-1", TTL)
    mock_pipe.execute.assert_called_once()


def test_set_session_field_serialises_list():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.set_session_field("sess-1", "options", ["opt1", "opt2"])

    hset_value = mock_pipe.hset.call_args[0][2]
    assert hset_value == '["opt1", "opt2"]'


def test_set_session_field_swallows_redis_error():
    store, client = _make_store()
    client.pipeline.side_effect = ConnectionError("connection refused")
    store.set_session_field("sess-err", "key", "val")  # must not raise


# ---------------------------------------------------------------------------
# reset_session_ttl
# ---------------------------------------------------------------------------

def test_reset_session_ttl_calls_expire():
    store, client = _make_store()
    store.reset_session_ttl("sess-1")
    client.expire.assert_called_once_with("session:sess-1", TTL)


def test_reset_session_ttl_swallows_redis_error():
    store, client = _make_store()
    client.expire.side_effect = ConnectionError("down")
    store.reset_session_ttl("sess-err")  # must not raise


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

def test_delete_session_calls_delete():
    store, client = _make_store()
    store.delete_session("sess-1")
    client.delete.assert_called_once_with("session:sess-1")


def test_delete_session_swallows_redis_error():
    store, client = _make_store()
    client.delete.side_effect = ConnectionError("down")
    store.delete_session("sess-err")  # must not raise


# ---------------------------------------------------------------------------
# register_session
# ---------------------------------------------------------------------------

def test_register_session_writes_user_index():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.register_session("user-1", "sess-1")

    mock_pipe.hset.assert_called_once()
    hset_args = mock_pipe.hset.call_args[0]
    assert hset_args[0] == "user:user-1"
    assert hset_args[1] == "sess-1"
    mock_pipe.expire.assert_called_once_with("user:user-1", TTL)
    mock_pipe.execute.assert_called_once()


def test_register_session_swallows_redis_error():
    store, client = _make_store()
    client.pipeline.side_effect = RuntimeError("pipe error")
    store.register_session("user-err", "sess-err")  # must not raise


# ---------------------------------------------------------------------------
# update_last_accessed
# ---------------------------------------------------------------------------

def test_update_last_accessed_updates_user_index():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe

    store.update_last_accessed("user-1", "sess-1")

    mock_pipe.hset.assert_called_once()
    assert mock_pipe.hset.call_args[0][0] == "user:user-1"
    mock_pipe.expire.assert_called_once_with("user:user-1", TTL)


def test_update_last_accessed_swallows_redis_error():
    store, client = _make_store()
    client.pipeline.side_effect = ConnectionError("down")
    store.update_last_accessed("user-err", "sess-err")  # must not raise


# ---------------------------------------------------------------------------
# get_user_sessions
# ---------------------------------------------------------------------------

def test_get_user_sessions_returns_hash():
    store, client = _make_store()
    client.hgetall.return_value = {"sess-1": "2024-01-01T10:00:00Z"}
    result = store.get_user_sessions("user-1")
    assert result == {"sess-1": "2024-01-01T10:00:00Z"}
    client.hgetall.assert_called_once_with("user:user-1")


def test_get_user_sessions_returns_empty_when_no_index():
    store, client = _make_store()
    client.hgetall.return_value = {}
    result = store.get_user_sessions("unknown-user")
    assert result == {}


def test_get_user_sessions_returns_empty_on_error():
    store, client = _make_store()
    client.hgetall.side_effect = ConnectionError("down")
    result = store.get_user_sessions("user-err")
    assert result == {}


# ---------------------------------------------------------------------------
# remove_session_from_user_index
# ---------------------------------------------------------------------------

def test_remove_session_from_user_index_deletes_field():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, 1]  # hdel=1, hlen=1 remaining

    store.remove_session_from_user_index("user-1", "sess-1")

    mock_pipe.hdel.assert_called_once_with("user:user-1", "sess-1")
    mock_pipe.hlen.assert_called_once_with("user:user-1")
    mock_pipe.execute.assert_called_once()
    # user key should NOT be deleted when sessions remain
    client.delete.assert_not_called()


def test_remove_session_from_user_index_deletes_user_key_when_empty():
    store, client = _make_store()
    mock_pipe = MagicMock()
    client.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, 0]  # hdel=1, hlen=0 (no sessions left)

    store.remove_session_from_user_index("user-1", "last-sess")

    client.delete.assert_called_once_with("user:user-1")


def test_remove_session_from_user_index_swallows_error():
    store, client = _make_store()
    client.pipeline.side_effect = RuntimeError("pipe fail")
    store.remove_session_from_user_index("user-err", "sess-err")  # must not raise


# ---------------------------------------------------------------------------
# remove_stale_session_field
# ---------------------------------------------------------------------------

def test_remove_stale_session_field_calls_hdel():
    store, client = _make_store()
    store.remove_stale_session_field("user-1", "stale-sess")
    client.hdel.assert_called_once_with("user:user-1", "stale-sess")


def test_remove_stale_session_field_swallows_error():
    store, client = _make_store()
    client.hdel.side_effect = ConnectionError("down")
    store.remove_stale_session_field("user-err", "sess")  # must not raise


# ---------------------------------------------------------------------------
# delete_user_index
# ---------------------------------------------------------------------------

def test_delete_user_index_calls_delete():
    store, client = _make_store()
    store.delete_user_index("user-1")
    client.delete.assert_called_once_with("user:user-1")


def test_delete_user_index_swallows_error():
    store, client = _make_store()
    client.delete.side_effect = ConnectionError("down")
    store.delete_user_index("user-err")  # must not raise


# ---------------------------------------------------------------------------
# _serialise_value helpers
# ---------------------------------------------------------------------------

def test_serialise_value_bool_true():
    assert _serialise_value(True) == "true"


def test_serialise_value_bool_false():
    assert _serialise_value(False) == "false"


def test_serialise_value_list():
    result = _serialise_value(["a", "b"])
    assert result == '["a", "b"]'


def test_serialise_value_dict():
    result = _serialise_value({"key": "val"})
    assert '"key": "val"' in result


def test_serialise_value_int():
    assert _serialise_value(42) == "42"


def test_serialise_value_none():
    assert _serialise_value(None) == "None"


def test_serialise_mapping_converts_all_values():
    result = _serialise_mapping({"a": True, "b": 1, "c": ["x"]})
    assert result == {"a": "true", "b": "1", "c": '["x"]'}
