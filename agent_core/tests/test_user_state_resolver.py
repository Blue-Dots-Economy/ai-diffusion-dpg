"""
Unit tests for agent_core.src.preprocessing.user_state_resolver.
"""
from datetime import datetime, timezone

from src.models import UserStateClassification
from src.preprocessing.user_state_resolver import resolve_user_state


NOW = datetime(2026, 4, 20, 10, 15, 0, tzinfo=timezone.utc)
CONFIG = {
    "conversation": {
        "user_state_model": {
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "signals": [], "guidance": "g1"},
                {"id": "orientation", "signals": [], "guidance": "g2"},
            ],
        },
    },
}


def test_disabled_returns_none():
    payload, transitioned = resolve_user_state(
        classification=None, previous=None,
        config={"conversation": {"user_state_model": {"enabled": False}}},
        now=NOW,
    )
    assert payload is None
    assert transitioned is False


def test_first_turn_initialises_to_default():
    cls = UserStateClassification(id="fog", confidence=0.9)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=None, config=CONFIG, now=NOW,
    )
    assert payload is not None
    assert payload["id"] == "fog"
    assert payload["previous_id"] is None
    assert payload["turn_count"] == 1
    assert payload["confidence"] == 0.9
    assert transitioned is False


def test_sticky_increments_turn_count():
    previous = {
        "id": "fog", "confidence": 0.8, "previous_id": None,
        "turn_count": 2, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    cls = UserStateClassification(id="fog", confidence=0.75)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=previous, config=CONFIG, now=NOW,
    )
    assert payload["id"] == "fog"
    assert payload["turn_count"] == 3
    assert payload["previous_id"] is None
    assert payload["updated_at"] == previous["updated_at"]
    assert transitioned is False


def test_transition_builds_fresh_payload():
    previous = {
        "id": "fog", "confidence": 0.8, "previous_id": None,
        "turn_count": 3, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    cls = UserStateClassification(id="orientation", confidence=0.85)
    payload, transitioned = resolve_user_state(
        classification=cls, previous=previous, config=CONFIG, now=NOW,
    )
    assert payload["id"] == "orientation"
    assert payload["previous_id"] == "fog"
    assert payload["turn_count"] == 1
    assert payload["confidence"] == 0.85
    assert payload["updated_at"] == NOW.isoformat()
    assert transitioned is True


def test_classification_none_with_previous_keeps_previous():
    previous = {
        "id": "orientation", "confidence": 0.7, "previous_id": "fog",
        "turn_count": 2, "updated_at": "2026-04-20T10:14:00+00:00",
    }
    payload, transitioned = resolve_user_state(
        classification=None, previous=previous, config=CONFIG, now=NOW,
    )
    assert payload["id"] == "orientation"
    assert payload["turn_count"] == 3
    assert transitioned is False
