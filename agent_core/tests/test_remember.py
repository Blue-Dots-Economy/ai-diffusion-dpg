"""Unit tests for the framework remember tool (spec §8)."""

from __future__ import annotations

import pytest

from src.chat_provider.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from src.models import ToolCall
from src.remember import RememberTool

CONFIG = {"memory_tool": {"name": "remember", "fields": {
    "profile_item_id": {"scope": "session", "description": "chosen profile",
                        "grounded_in": ["fetch_profile", "save_profile"]},
    "profile_action": {"scope": "session"},
}}}
TOOL = RememberTool.from_config(CONFIG)
STORED = {"fetch_profile": ['{"items":[{"item_id":"p-1"},{"item_id":"p-2"}]}']}


def call(field, value):
    return ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": field, "value": value})


def message_with_tool_result(tool_use_id: str, tool_name: str, content: str) -> list[Message]:
    """Create messages with a tool_use block and its result."""
    msg_use = Message(role="assistant", content=[
        ToolUseBlock(tool_use_id=tool_use_id, tool_name=tool_name, input={}),
    ])
    msg_result = Message(role="user", content=[
        ToolResultBlock(tool_use_id=tool_use_id, content=content),
    ])
    return [msg_use, msg_result]


class Recorder:
    def __init__(self, result=(True, "")):
        self.result, self.writes, self.saved = result, [], []

    def write(self, scope, key, value):
        self.writes.append((scope, key, value))
        return self.result

    def on_saved(self, scope, key, value):
        self.saved.append((scope, key, value))

    def write_raising(self, scope, key, value):
        """Write that raises an exception."""
        raise ValueError("write_strict failed")

    def on_saved_raising(self, scope, key, value):
        """on_saved that raises an exception."""
        raise RuntimeError("on_saved failed")


def test_from_config_none_without_section():
    assert RememberTool.from_config({}) is None


def test_definition_lists_fields_as_enum():
    d = TOOL.definition()
    assert d["name"] == "remember"
    assert d["input_schema"]["properties"]["field"]["enum"] == ["profile_action", "profile_item_id"]
    assert d["input_schema"]["required"] == ["field", "value"]


def test_grounded_value_is_written_and_reported():
    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "p-2"), [], STORED, rec.write, rec.on_saved)
    assert r.success and r.tool_use_id == "tu_r"
    assert rec.writes == [("session", "profile_item_id", "p-2")]
    assert rec.saved == [("session", "profile_item_id", "p-2")]


@pytest.mark.parametrize("field,value,stored", [
    ("profile_item_id", "invented", STORED),      # not in any result
    ("profile_item_id", "p-1", {}),              # nothing fetched: strict
    ("not_a_field", "x", STORED),
    ("profile_action", "", STORED),
])
def test_rejections_write_nothing(field, value, stored):
    rec = Recorder()
    r = TOOL.handle(call(field, value), [], stored, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_memory_layer_rejection_is_surfaced():
    rec = Recorder(result=(False, "'zzz' is not one of [...]"))
    r = TOOL.handle(call("profile_action", "zzz"), [], STORED, rec.write, rec.on_saved)
    assert r.success is False and "not one of" in r.result_text
    assert rec.saved == []


async def test_handle_async():
    rec = Recorder()

    async def awrite(scope, key, value):
        return rec.write(scope, key, value)

    r = await TOOL.handle_async(call("profile_action", "use_existing"), [], STORED, awrite, rec.on_saved)
    assert r.success and rec.saved == [("session", "profile_action", "use_existing")]


def test_write_strict_raising_returns_rejected():
    """write_strict raising returns REMEMBER_REJECTED, nothing saved."""
    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "p-2"), [], STORED, rec.write_raising, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert "could not store the value" in r.result_text
    assert rec.saved == []


def test_on_saved_raising_returns_success():
    """on_saved raising still returns success (value is persisted)."""
    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "p-2"), [], STORED, rec.write, rec.on_saved_raising)
    assert r.success is True
    assert rec.writes == [("session", "profile_item_id", "p-2")]
    # on_saved was called but raised, so saved won't reflect it in Recorder


def test_fragment_p_dash_not_grounded():
    """Fragment 'p-' when only 'p-10' exists → rejected."""
    rec = Recorder()
    stored = {"fetch_profile": ['{"items":[{"item_id":"p-10"}]}']}
    r = TOOL.handle(call("profile_item_id", "p-"), [], stored, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_fragment_p_1_not_grounded_when_p_10_exists():
    """Fragment 'p-1' when only 'p-10' exists → rejected."""
    rec = Recorder()
    stored = {"fetch_profile": ['{"items":[{"item_id":"p-10"}]}']}
    r = TOOL.handle(call("profile_item_id", "p-1"), [], stored, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_key_name_not_grounded():
    """Key name 'item_id' → rejected."""
    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "item_id"), [], STORED, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_value_in_grounded_and_non_grounded_tool_accepted():
    """Value present in a grounded tool result is accepted even if a non-grounded one also has it."""
    rec = Recorder()
    stored = {
        "fetch_profile": ['{"items":[{"item_id":"p-1"}]}'],
        "fetch_jobs": ['{"jobs":[{"id":"p-1"}]}'],  # p-1 also in non-grounded tool
    }
    r = TOOL.handle(call("profile_item_id", "p-1"), [], stored, rec.write, rec.on_saved)
    # Should still be grounded because it's in fetch_profile
    assert r.success is True


def test_value_only_in_non_grounded_stored_result_rejected():
    """Value found only in a non-grounded tool's stored result → rejected."""
    rec = Recorder()
    stored = {
        "fetch_profile": ['{"items":[{"item_id":"p-1"}]}'],
        "fetch_jobs": ['{"jobs":[{"id":"j-7"}]}'],
    }
    r = TOOL.handle(call("profile_item_id", "j-7"), [], stored, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_value_only_in_non_grounded_message_tool_result_rejected():
    """Value found only in a non-grounded tool's tool_use/tool_result pair → rejected."""
    rec = Recorder()
    messages = message_with_tool_result("tu_jobs", "fetch_jobs", '{"jobs":[{"id":"j-7"}]}')
    r = TOOL.handle(call("profile_item_id", "j-7"), messages, {}, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


def test_value_grounded_via_messages_tool_result():
    """Value in messages tool_result block → accepted."""
    rec = Recorder()
    msg = Message(role="user", content=[
        ToolResultBlock(tool_use_id="tu_fetch", content='{"items":[{"item_id":"p-99"}]}'),
    ])
    # Set up origin mapping
    msg_with_use = Message(role="assistant", content=[
        ToolUseBlock(tool_use_id="tu_fetch", tool_name="fetch_profile", input={}),
    ])
    messages = [msg_with_use, msg]

    rec = Recorder()
    r = TOOL.handle(call("profile_item_id", "p-99"), messages, {}, rec.write, rec.on_saved)
    assert r.success is True
    assert rec.writes == [("session", "profile_item_id", "p-99")]


def test_value_grounded_via_stored_result_with_prefix():
    """Value in stored result with prefix → accepted."""
    rec = Recorder()
    stored = {"fetch_profile": ['(stored result, fetched 3 min ago) {"items":[{"item_id":"p-stored"}]}']}
    r = TOOL.handle(call("profile_item_id", "p-stored"), [], stored, rec.write, rec.on_saved)
    assert r.success is True
    assert rec.writes == [("session", "profile_item_id", "p-stored")]


def test_non_str_value_int_rejected():
    """Non-str value (int) → rejected."""
    rec = Recorder()
    tc = ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": "profile_item_id", "value": 123})
    r = TOOL.handle(tc, [], STORED, rec.write, rec.on_saved)
    assert r.success is False and "must be text" in r.result_text
    assert rec.writes == [] and rec.saved == []


def test_non_str_value_list_rejected():
    """Non-str value (list) → rejected."""
    rec = Recorder()
    tc = ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": "profile_item_id", "value": ["p-1"]})
    r = TOOL.handle(tc, [], STORED, rec.write, rec.on_saved)
    assert r.success is False and "must be text" in r.result_text
    assert rec.writes == [] and rec.saved == []


def test_field_with_empty_grounded_in_saved_without_grounding():
    """Field with empty grounded_in (profile_action) saved without grounding."""
    rec = Recorder()
    r = TOOL.handle(call("profile_action", "some_action"), [], {}, rec.write, rec.on_saved)
    assert r.success is True
    assert rec.writes == [("session", "profile_action", "some_action")]


def test_value_stripped_before_empty_check():
    """Whitespace-only value is rejected after stripping."""
    rec = Recorder()
    r = TOOL.handle(call("profile_action", "   "), [], {}, rec.write, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.writes == [] and rec.saved == []


async def test_handle_async_rejection_path():
    """Async rejection path works."""
    rec = Recorder()

    async def awrite(scope, key, value):
        raise ValueError("async write failed")

    r = await TOOL.handle_async(call("profile_item_id", "p-2"), [], STORED, awrite, rec.on_saved)
    assert r.success is False and r.error == "REMEMBER_REJECTED"
    assert rec.saved == []
