"""Unit tests for the per-turn tool-result cache (spec §7)."""

from __future__ import annotations

import json

from src.models import ToolCall, ToolResult
from src.tool_results import (
    FORCE_REFRESH, ToolResultPolicies, TurnToolCache, args_hash, augment_tool_definitions,
)

CONFIG = {"connectors": {
    "read": [
        {"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 1800,
                                           "keep": ["acting_as_user_id", "items"]}},
        {"name": "fetch_jobs", "cache": {"scope": "session", "ttl_seconds": 600,
                                        "vary_on": ["trade", "location"]}},
        {"name": "uncached"},
    ],
    "write": [{"name": "save_profile", "invalidates": ["fetch_profile"]}],
}}
POL = ToolResultPolicies.from_config(CONFIG)
NOW = 10_000.0


def clock(t=NOW):
    return lambda: t


def tc(name, params=None, tid="tu1"):
    return ToolCall(tool_name=name, tool_use_id=tid, input_params=params or {})


def live(name, data, projected=True, success=True):
    return ToolResult(tool_use_id="x", tool_name=name, result={}, success=success,
                      result_text=json.dumps(data), projected=projected)


def entry(tool, data, h, fetched=NOW - 180, ttl=1800, scope="user"):
    return {"tool": tool, "args_hash": h, "data": data, "fetched_at": fetched,
            "expires_at": fetched + ttl, "origin": "turn", "scope": scope}


def test_policies_from_config():
    assert set(POL.cache) == {"fetch_profile", "fetch_jobs"}
    assert POL.invalidates == {"save_profile": ("fetch_profile",)}
    assert ToolResultPolicies.from_config(None).cache == {}


def test_args_hash_stable_sorted_and_ignores_force_refresh():
    assert args_hash({"a": 1, "b": 2}) == args_hash({"b": 2, "a": 1})
    assert args_hash({"a": 1, FORCE_REFRESH: True}) == args_hash({"a": 1})
    assert args_hash({"a": 1}, {"trade": "x"}) != args_hash({"a": 1}, {"trade": "y"})
    assert len(args_hash({})) == 32


def test_hit_returns_stored_data_labelled_with_age():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {"items": [1]}, h)], {}, clock())
    r = cache.lookup(tc("fetch_profile", tid="tu9"))
    assert r is not None and r.success and r.tool_use_id == "tu9"
    assert r.result_text.startswith("(stored result, fetched 3 min ago)")
    assert '"items": [1]' in r.result_text


def test_miss_for_uncached_tool_other_args_and_expired():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h, fetched=NOW - 2000)], {}, clock())
    assert cache.lookup(tc("fetch_profile")) is None        # expired
    assert cache.lookup(tc("uncached")) is None
    cache2 = TurnToolCache(POL, [entry("fetch_jobs", [], args_hash({}, {"trade": "a", "location": "b"}),
                                       scope="session")], {"trade": "a", "location": "c"}, clock())
    assert cache2.lookup(tc("fetch_jobs")) is None           # vary_on changed


def test_force_refresh_bypasses_and_is_stripped():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h)], {}, clock())
    call = tc("fetch_profile", {FORCE_REFRESH: True})
    assert cache.lookup(call) is None
    assert FORCE_REFRESH not in cache.prepare(call).input_params
    assert cache.prepare(tc("uncached", {"q": 1})).input_params == {"q": 1}


def test_after_call_stores_projected_json_with_keep():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("fetch_profile"), live("fetch_profile",
                     {"acting_as_user_id": "u", "items": [], "compliance": ["x"]}))
    batch = cache.drain_batch()
    assert batch["invalidate"] == []
    [put] = batch["puts"]
    assert put["data"] == {"acting_as_user_id": "u", "items": []}
    assert (put["scope"], put["ttl_seconds"], put["origin"]) == ("user", 1800, "turn")
    assert cache.lookup(tc("fetch_profile")) is not None     # same-turn overlay
    assert cache.drain_batch() == {"invalidate": [], "puts": []}


def test_after_call_skips_unprojected_invalid_and_failed():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, projected=False))
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, success=False))
    bad = ToolResult(tool_use_id="x", tool_name="fetch_profile", result={}, success=True,
                     result_text="not json", projected=True)
    cache.after_call(tc("fetch_profile"), bad)
    assert not cache.has_pending()


def test_write_call_invalidates_even_on_failure_and_drops_same_turn_puts():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h)], {}, clock())
    cache.after_call(tc("fetch_profile", {"x": 1}), live("fetch_profile", {"items": []}))
    cache.after_call(tc("save_profile"), live("save_profile", {}, success=False))
    assert cache.lookup(tc("fetch_profile")) is None
    assert cache.drain_batch() == {"invalidate": ["fetch_profile"], "puts": []}


def test_store_after_invalidate_in_same_turn_survives():
    cache = TurnToolCache(POL, [], {}, clock())
    cache.after_call(tc("save_profile"), live("save_profile", {}))
    cache.after_call(tc("fetch_profile"), live("fetch_profile", {"items": [2]}))
    batch = cache.drain_batch()
    assert batch["invalidate"] == ["fetch_profile"] and len(batch["puts"]) == 1


def test_fresh_tools_stored_results_and_ignores_unknown_or_malformed_entries():
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {"items": ["id-1"]}, h),
                                entry("not_configured", {}, h), {"junk": True}, "nope"], {}, clock())
    assert cache.fresh_tools() == {"fetch_profile"}
    assert "id-1" in cache.stored_results_by_tool()["fetch_profile"][0]


def test_render_known_facts():
    assert TurnToolCache(POL, [], {}, clock()).render_known_facts() == ""
    h = args_hash({})
    text = TurnToolCache(POL, [entry("fetch_profile", {"items": []}, h)], {}, clock()).render_known_facts()
    assert "Use them instead of calling the tool again" in text
    assert "force_refresh" in text
    assert "- fetch_profile — fetched 3 min ago, valid for 27 more min:" in text


def test_augment_adds_force_refresh_and_remember_only_when_tools_present():
    defs = [{"name": "fetch_profile", "description": "d",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "uncached", "description": "d", "input_schema": {"type": "object"}}]
    rem = {"name": "remember", "description": "r", "input_schema": {"type": "object"}}
    out = augment_tool_definitions(defs, POL, rem)
    assert FORCE_REFRESH in out[0]["input_schema"]["properties"]
    assert FORCE_REFRESH not in (defs[0]["input_schema"]["properties"])   # input not mutated
    assert "properties" not in out[1]["input_schema"] or FORCE_REFRESH not in out[1]["input_schema"]["properties"]
    assert out[-1]["name"] == "remember"
    assert augment_tool_definitions([], POL, rem) == []
    assert augment_tool_definitions(None, POL, rem) is None


def test_malformed_entries_are_ignored_and_methods_do_not_raise():
    """Entries missing data, fetched_at, with non-numeric fetched_at, or non-string tool are skipped."""
    h = args_hash({})
    bad_entries = [
        entry("fetch_profile", {"items": [1]}, h),  # good entry
        {"tool": "fetch_profile", "args_hash": h, "fetched_at": NOW - 180, "expires_at": NOW + 1800},  # missing data
        {"tool": "fetch_profile", "args_hash": h, "data": {}, "expires_at": NOW + 1800},  # missing fetched_at
        {"tool": "fetch_profile", "args_hash": h, "data": {}, "fetched_at": "not a number", "expires_at": NOW + 1800},  # non-numeric fetched_at
        {"tool": ["fetch_profile"], "args_hash": h, "data": {}, "fetched_at": NOW - 180, "expires_at": NOW + 1800},  # tool is list
        "not a dict",  # not a dict
    ]
    cache = TurnToolCache(POL, bad_entries, {}, clock())
    # Malformed entries are skipped; only the good one is loaded.
    assert cache.fresh_tools() == {"fetch_profile"}
    # Methods don't raise on the remaining good entry.
    r = cache.lookup(tc("fetch_profile"))
    assert r is not None
    facts = cache.render_known_facts()
    assert "fetch_profile" in facts
    stored = cache.stored_results_by_tool()
    assert "fetch_profile" in stored


def test_stored_expiry_capped_by_policy_ttl():
    """Entry with stored expiry > fetched_at + policy TTL is treated as expired at policy bound."""
    h = args_hash({})
    # Entry claims it expires in 9000s from fetched, but policy is 1800s.
    # It should expire 1800s after fetched_at, not 9000s.
    stored_expiry = NOW - 180 + 9000  # way in the future
    policy_expiry = NOW - 180 + 1800  # 1800s = policy TTL
    bad_entry = {"tool": "fetch_profile", "args_hash": h, "data": {"items": []},
                 "fetched_at": NOW - 180, "expires_at": stored_expiry, "origin": "turn", "scope": "user"}
    cache = TurnToolCache(POL, [bad_entry], {}, clock())
    # The entry should be present but with capped expiry.
    assert cache.fresh_tools() == {"fetch_profile"}
    # Verify effective expiry is capped.
    entry_data = cache._entries.get(("fetch_profile", h))
    assert entry_data is not None
    assert entry_data["expires_at"] == policy_expiry


def test_force_refresh_true_string_bypasses_cache():
    """force_refresh as string 'true' (case-insensitive) also bypasses cache."""
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {}, h)], {}, clock())
    # force_refresh=True still bypasses.
    assert cache.lookup(tc("fetch_profile", {FORCE_REFRESH: True})) is None
    # force_refresh="true" also bypasses.
    assert cache.lookup(tc("fetch_profile", {FORCE_REFRESH: "true"})) is None
    # force_refresh="True" (mixed case) also bypasses.
    assert cache.lookup(tc("fetch_profile", {FORCE_REFRESH: "True"})) is None
    # force_refresh="TRUE" also bypasses.
    assert cache.lookup(tc("fetch_profile", {FORCE_REFRESH: "TRUE"})) is None
    # force_refresh="false" does NOT bypass.
    r = cache.lookup(tc("fetch_profile", {FORCE_REFRESH: "false"}))
    assert r is not None
    # force_refresh without bool/true value does not bypass.
    r = cache.lookup(tc("fetch_profile", {FORCE_REFRESH: ""}))
    assert r is not None


def test_duplicate_keys_keep_latest_fetched_at():
    """When two entries share a key, keep the one with latest fetched_at."""
    h = args_hash({})
    older_entry = entry("fetch_profile", {"items": [1]}, h, fetched=NOW - 200)
    newer_entry = entry("fetch_profile", {"items": [2]}, h, fetched=NOW - 100)
    cache = TurnToolCache(POL, [older_entry, newer_entry], {}, clock())
    # The newer entry should be kept.
    r = cache.lookup(tc("fetch_profile"))
    assert r is not None
    assert "[2]" in r.result_text  # newer data
    # If we reverse the order, the newer one still wins.
    cache2 = TurnToolCache(POL, [newer_entry, older_entry], {}, clock())
    r2 = cache2.lookup(tc("fetch_profile"))
    assert r2 is not None
    assert "[2]" in r2.result_text


def test_non_finite_timestamps_are_rejected():
    """Entries whose fetched_at or expires_at is NaN or infinite are skipped."""
    h = args_hash({})
    nan, inf = float("nan"), float("inf")
    bad = [
        {**entry("fetch_profile", {"items": [1]}, h), "fetched_at": nan},
        {**entry("fetch_profile", {"items": [1]}, h), "expires_at": nan},
        {**entry("fetch_profile", {"items": [1]}, h), "fetched_at": inf},
        {**entry("fetch_profile", {"items": [1]}, h), "expires_at": inf},
        {**entry("fetch_profile", {"items": [1]}, h), "fetched_at": "-inf"},
        {**entry("fetch_profile", {"items": [1]}, h), "expires_at": "nan"},
    ]
    for e in bad:
        cache = TurnToolCache(POL, [e], {}, clock())
        assert cache.fresh_tools() == set(), e
        assert cache.lookup(tc("fetch_profile")) is None


def test_invalidated_data_stays_a_grounding_source_but_is_not_served():
    """A mid-turn invalidation stops serving the entry, but its data still grounds values."""
    h = args_hash({})
    cache = TurnToolCache(POL, [entry("fetch_profile", {"items": ["id-1"]}, h)], {}, clock())
    cache.after_call(tc("fetch_profile", {"x": 1}), live("fetch_profile", {"items": ["id-2"]}))
    cache.after_call(tc("save_profile"), live("save_profile", {}))
    assert cache.lookup(tc("fetch_profile")) is None
    assert cache.lookup(tc("fetch_profile", {"x": 1})) is None
    assert cache.fresh_tools() == set()
    assert cache.render_known_facts() == ""
    grounding = cache.stored_results_by_tool()["fetch_profile"]
    assert any("id-1" in s for s in grounding) and any("id-2" in s for s in grounding)


def test_after_call_origin_bootstrap_and_returns_entry():
    cache = TurnToolCache(POL, [], {}, clock())
    entry = cache.after_call(tc("fetch_profile"), live("fetch_profile", {"items": []}), origin="bootstrap")
    assert entry is not None
    assert entry["origin"] == "bootstrap" and entry["tool"] == "fetch_profile"
    assert entry["data"] == {"items": []} and entry["scope"] == "user"
    [put] = cache.drain_batch()["puts"]
    assert put["origin"] == "bootstrap"


def test_after_call_default_origin_turn_and_none_when_not_stored():
    cache = TurnToolCache(POL, [], {}, clock())
    assert cache.after_call(tc("fetch_profile"), live("fetch_profile", {}))["origin"] == "turn"
    assert cache.after_call(tc("fetch_profile"), live("fetch_profile", {}, success=False)) is None
    assert cache.after_call(tc("uncached"), live("uncached", {})) is None
    assert cache.after_call(tc("save_profile"), live("save_profile", {})) is None


def test_latest_entry_picks_newest_fresh_entry_for_tool():
    now = 10_000.0
    entries = [
        entry("fetch_jobs", [{"item_id": "old"}], "a", fetched=now - 300, ttl=600, scope="session"),
        entry("fetch_jobs", [{"item_id": "new"}], "b", fetched=now - 10, ttl=600, scope="session"),
        entry("fetch_jobs", [{"item_id": "expired"}], "c", fetched=now - 700, ttl=600, scope="session"),
    ]
    cache = TurnToolCache(POL, entries, {}, now=clock(now))
    assert cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "new"}]
    assert cache.latest_entry("fetch_profile") is None


def test_latest_entry_sees_entry_stored_this_turn():
    cache = TurnToolCache(POL, [], {}, now=clock(50.0))
    cache.after_call(tc("fetch_jobs", {"query_text": "welder"}, tid="t1"),
                     live("fetch_jobs", [{"item_id": "j1"}]))
    assert cache.latest_entry("fetch_jobs")["data"] == [{"item_id": "j1"}]
