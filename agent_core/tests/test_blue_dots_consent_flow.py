"""The Blue Dots consent flow: what the fetch lifts, and what the write sends."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "action_gateway"))
from src.adapters.response_path import resolve  # noqa: E402

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
AG = yaml.safe_load((BD / "action_gateway.yaml").read_text(encoding="utf-8"))
ML = yaml.safe_load((BD / "memory_layer.yaml").read_text(encoding="utf-8"))


def _seeker_mappings():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "seeker_fetch":
                return {m["target"]: m for m in t["response"]["session_mapping"]}
    raise AssertionError("seeker_fetch not found")


def test_fetch_lifts_the_per_profile_consent_flag():
    m = _seeker_mappings()["profile_consent"]
    assert m["source"] == "items[lifecycle_status=live].profile_consent_accepted"
    assert not m.get("on_error")


def test_profile_consent_resolves_from_the_same_item_as_profile_item_id():
    m = _seeker_mappings()
    assert (m["profile_consent"]["source"].rsplit(".", 1)[0]
            == m["profile_item_id"]["source"].rsplit(".", 1)[0])


def test_fetch_lifts_the_upstream_error_code():
    m = _seeker_mappings()["fetch_error"]
    assert m["source"] == "error"
    assert m["on_error"] is True


def test_consent_flags_are_declared_with_a_false_default():
    schema = ML["state"]["session"]["schema"]
    for field in ("profile_consent", "user_terms", "user_privacy", "has_age"):
        assert schema[field]["type"] == "enum", field
        assert schema[field]["values"] == ["true", "false"], field
        assert schema[field]["default"] == "false", field


def test_fetch_error_is_declared():
    assert ML["state"]["session"]["schema"]["fetch_error"] == {"type": "string", "default": ""}


def test_consent_return_to_is_declared():
    assert ML["state"]["session"]["schema"]["consent_return_to"] == {"type": "string", "default": ""}


_LIVE_BODY = {
    "user_id": "d1455336-fb14-4fc0-a8ef-547213a00831",
    "compliance": [{"key": "user_terms", "value": True},
                   {"key": "user_privacy", "value": True},
                   {"key": "has_age", "value": True}],
    "items": [
        {"item_id": "72e45bfc", "lifecycle_status": "live", "profile_consent_accepted": False},
        {"item_id": "ecf7a8a2", "lifecycle_status": "live", "profile_consent_accepted": True},
    ],
}


def test_newest_live_item_wins_and_its_consent_travels_with_it():
    m = _seeker_mappings()
    assert resolve(_LIVE_BODY, m["profile_item_id"]["source"]) == "72e45bfc"
    assert resolve(_LIVE_BODY, m["profile_consent"]["source"]) is False


def test_an_unknown_caller_yields_no_item_and_no_consent():
    body = {"user_id": None, "compliance": [{"key": "user_terms", "value": False},
                                            {"key": "user_privacy", "value": False},
                                            {"key": "has_age", "value": False}], "items": []}
    m = _seeker_mappings()
    assert resolve(body, m["profile_item_id"]["source"]) is None
    assert resolve(body, m["profile_consent"]["source"]) is None


def test_an_older_upstream_without_the_field_reads_as_absent():
    body = {"items": [{"item_id": "a", "lifecycle_status": "live"}]}
    assert resolve(body, _seeker_mappings()["profile_consent"]["source"]) is None


def _save_profile():
    for t in AG["tools"]:
        for ep in t.get("endpoints") or []:
            if ep["name"] == "save_profile":
                return ep
    raise AssertionError("save_profile not found")


def test_compliance_values_come_from_session():
    body = _save_profile()["body_template"]
    assert body["compliance"] == [
        {"key": "user_terms", "value": "{user_terms}"},
        {"key": "user_privacy", "value": "{user_privacy}"},
        {"key": "profile_creation", "value": "{profile_consent}"},
    ]


def test_the_dead_consent_fields_are_gone():
    body = _save_profile()["body_template"]
    assert "terms_accepted" not in body
    assert "privacy_accepted" not in body


def test_consent_params_are_session_sourced_booleans():
    params = {p["name"]: p for p in _save_profile()["params"]}
    for name in ("user_terms", "user_privacy", "profile_consent"):
        assert params[name]["source"] == "session", name
        assert params[name]["type"] == "boolean", name
        assert params[name].get("required") is False, name
