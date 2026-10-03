"""${VAR} / ${VAR:-default} expansion for Action Gateway config (same contract as reach_layer)."""
import pytest

from src.config.env_expand import (UnresolvedEnvPlaceholderError, check_no_unresolved_urls,
                                   expand_env_vars)


def test_set_var_wins(monkeypatch):
    monkeypatch.setenv("SIGNALS_BASE_URL", "http://host.docker.internal:2742")
    assert expand_env_vars({"base_url": "${SIGNALS_BASE_URL:-https://x}"}) == {
        "base_url": "http://host.docker.internal:2742"}


def test_unset_var_uses_default(monkeypatch):
    monkeypatch.delenv("SIGNALS_BASE_URL", raising=False)
    assert expand_env_vars("${SIGNALS_BASE_URL:-https://signals.bluedotseconomy.org}") == \
        "https://signals.bluedotseconomy.org"


def test_unset_var_without_default_is_left_as_written(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    assert expand_env_vars("${NOPE_X}") == "${NOPE_X}"


def test_nested_lists_and_dicts(monkeypatch):
    monkeypatch.setenv("A_X", "1")
    assert expand_env_vars({"l": [{"v": "${A_X}"}, 3, None]}) == {"l": [{"v": "1"}, 3, None]}


def test_expansion_leaves_non_placeholders():
    raw = {"body": {"item_instance_url": "{instance_url}"}, "response_path": "$.items[0].item_id",
           "price": "$5"}
    assert expand_env_vars(raw) == raw


def test_unresolved_placeholder_in_url_fails_startup(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    cfg = {"tools": [{"id": "t1", "base_url": "${NOPE_X}/api"}]}
    with pytest.raises(UnresolvedEnvPlaceholderError, match="t1.*NOPE_X"):
        check_no_unresolved_urls(cfg)


def test_unresolved_placeholder_in_static_param_fails_startup(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    cfg = {"tools": [{"id": "apply_job", "base_url": "https://a",
                      "params": [{"name": "instance_url", "source": "static", "value": "${NOPE_X}"}]}]}
    with pytest.raises(UnresolvedEnvPlaceholderError, match="apply_job.*NOPE_X"):
        check_no_unresolved_urls(cfg)


def test_resolved_config_passes():
    check_no_unresolved_urls({"tools": [{"id": "t", "base_url": "https://a",
                                         "params": [{"name": "x", "source": "static", "value": "v"}]}]})


def test_unresolved_placeholder_in_endpoint_static_param_fails_startup(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    cfg = {"tools": [{"id": "apply_job", "base_url": "https://a", "endpoints": [
        {"params": [{"name": "instance_url", "source": "static", "value": "${NOPE_X}"}]}]}]}
    with pytest.raises(UnresolvedEnvPlaceholderError, match="apply_job.*NOPE_X"):
        check_no_unresolved_urls(cfg)
