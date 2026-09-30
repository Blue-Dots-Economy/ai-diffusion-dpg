"""Unit tests for the framework remember tool (spec §8)."""

from __future__ import annotations

import pytest

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


class Recorder:
    def __init__(self, result=(True, "")):
        self.result, self.writes, self.saved = result, [], []

    def write(self, scope, key, value):
        self.writes.append((scope, key, value))
        return self.result

    def on_saved(self, scope, key, value):
        self.saved.append((scope, key, value))


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
