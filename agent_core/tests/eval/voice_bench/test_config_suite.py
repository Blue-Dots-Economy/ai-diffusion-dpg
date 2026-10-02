"""Config + suite loading for voice-bench (no network)."""
import pytest

from eval.voice_bench import SUITE_VERSION
from eval.voice_bench.config import load_config
from eval.voice_bench.suite import (ALWAYS_TCS, TCS, applicable_tcs, load_personas, phone_for, runs_for)

CFG = """
suite_version: 1
targets:
  - {name: M0, git_ref: edf7ec8}
  - {name: vm, bridge_url: "http://127.0.0.1:8008", redis_container: dpg_redis, agent_container: dpg_agent_core}
models:
  caller: {provider: openai, model: gpt-4.1, temperature: 0.3}
  judge:  {provider: openai, model: gpt-4.1, temperature: 0}
backend: {signals_dir: /tmp/sig, signals_url: "http://localhost:2742", search_url: "http://localhost:3100"}
runs: 1
runs_per_scenario: {T01: 3, T12: 3, T14: 3}
max_turns: 14
phone_prefix: "9199000"
results_dir: eval_results/voice_bench
"""


def test_load_config_defaults_and_overrides(tmp_path):
    p = tmp_path / "vb.yaml"
    p.write_text(CFG, encoding="utf-8")
    cfg = load_config(p, overrides={"runs": 2})
    assert cfg.runs == 2 and cfg.max_turns == 14
    m0, vm = cfg.targets
    assert m0.git_ref == "edf7ec8" and m0.bridge_url is None
    assert m0.compose == "automation/docker/docker-compose.yml" and m0.redis_container == "redis"
    assert m0.agent_container == "agent_core"
    assert vm.bridge_url == "http://127.0.0.1:8008" and vm.redis_container == "dpg_redis"
    assert cfg.judge.temperature == 0 and cfg.caller.model == "gpt-4.1"
    assert cfg.backend.tap_port == 18742 and cfg.backend.postgres_container == "signals-postgres"
    assert cfg.status_phrases == ["एक मिनट।"] and "धन्यवाद" in cfg.terminal_words
    assert runs_for(cfg, "T01") == 3 and runs_for(cfg, "T02") == 2


def test_target_needs_exactly_one_of_ref_or_url(tmp_path):
    p = tmp_path / "vb.yaml"
    p.write_text(CFG.replace("{name: M0, git_ref: edf7ec8}", "{name: M0}"), encoding="utf-8")
    with pytest.raises(ValueError, match="M0"):
        load_config(p)


def test_suite_version_mismatch_rejected(tmp_path):
    p = tmp_path / "vb.yaml"
    p.write_text(CFG.replace("suite_version: 1", "suite_version: 99"), encoding="utf-8")
    with pytest.raises(ValueError, match="suite_version"):
        load_config(p)


def test_personas_cover_t01_to_t14_and_feed_known_tcs():
    personas = load_personas()
    assert sorted(personas) == [f"T{i:02d}" for i in range(1, 15)]
    for p in personas.values():
        assert p.legs and all(leg.opening_line for leg in p.legs)
        assert set(p.feeds) <= set(TCS), p.id
    assert len(personas["T13"].legs) == 2
    assert personas["T14"].seeded_phone and personas["T12"].consents is False
    assert personas["T06"].seeded_phone          # draft-profile owner (ruling 9)
    assert SUITE_VERSION == 1


def test_applicable_tcs_adds_always_set():
    p = load_personas()["T05"]
    tcs = applicable_tcs(p)
    assert {"TC12", "TC17"} <= set(tcs) and set(ALWAYS_TCS) <= set(tcs)
    assert "TC22" not in tcs                      # TC22 is per target, not per call


def test_phone_for_is_reserved_range_and_distinct():
    a, b = phone_for("9199000", "T01", 0), phone_for("9199000", "T01", 1)
    assert a == "919900001000" and b == "919900001100" and a != b
    assert len(a) == 12 and a.isdigit()


def test_backend_network_json_default_and_override(tmp_path):
    """U1: backend.network_json defaults to the up-gzb schema path; the config key overrides it."""
    from pathlib import Path

    from eval.voice_bench.config import DEFAULT_NETWORK_JSON
    p = tmp_path / "vb.yaml"
    p.write_text(CFG, encoding="utf-8")
    assert load_config(p).backend.network_json == DEFAULT_NETWORK_JSON
    p.write_text(CFG.replace("search_url: \"http://localhost:3100\"}",
                             "search_url: \"http://localhost:3100\", network_json: /x/net.json}"), encoding="utf-8")
    assert load_config(p).backend.network_json == Path("/x/net.json")
