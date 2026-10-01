"""Tests for DialogueActConfig parsing."""
from src.understanding.config import DialogueActConfig


def _cfg(mode="dialogue_act"):
    return {
        "entity_to_profile_field": {"consent": "consent_response"},
        "entity_persistence": {"scope": "session"},
        "preprocessing": {"nlu_processor": {
            "mode": mode, "timeout_ms": 2000, "history_turns": 3,
            "slots": {"consent": {"type": "enum", "values": ["granted", "declined"],
                                  "accept_when_pending": ["consent"]},
                      "age": {"type": "int", "min": 14, "max": 80}},
            "known_fields": ["consent", "age", "stored_trade"],
            "act_intents": [{"acts": ["close"], "intent": "termination_intent", "gated": True}],
            "termination_gate": {"any_of": [{"pending": "closing_offer"},
                                            {"field": "applications_submitted", "operator": "gt", "value": 0}]},
            "off_track": {"threshold": 2, "intent": "off_track"},
        }},
    }


def test_from_config_none_in_intent_mode():
    assert DialogueActConfig.from_config(_cfg("intent")) is None
    assert DialogueActConfig.from_config({}) is None


def test_from_config_parses_everything():
    c = DialogueActConfig.from_config(_cfg())
    assert c.timeout_ms == 2000 and c.retry_attempts == 2 and c.history_turns == 3
    assert c.slots["consent"].values == ("granted", "declined")
    assert c.slots["age"].min == 14 and c.slots["age"].type == "int"
    assert c.act_intents[0].gated is True and c.act_intents[0].acts == ("close",)
    assert c.gate[0].pending == "closing_offer" and c.gate[0].condition is None
    assert c.gate[1].condition.field == "applications_submitted"
    assert c.off_track_threshold == 2 and c.entity_scope == "session"


def test_state_key_maps_through_entity_to_profile_field():
    c = DialogueActConfig.from_config(_cfg())
    assert c.state_key("consent") == "consent_response" and c.state_key("age") == "age"


def test_known_fields_map_slots_but_keep_raw_session_keys():
    c = DialogueActConfig.from_config(_cfg())
    assert c.known_state_keys() == (("consent", "consent_response"), ("age", "age"),
                                    ("stored_trade", "stored_trade"))
