import pytest
from trust_layer.src.blocks.consent import ConsentBlock


@pytest.fixture
def block():
    return ConsentBlock({
        "trust": {
            "consent": {
                "consent_phrases": ["haan", "yes", "theek hai", "manzoor hai"],
                "decline_phrases": ["nahi", "no", "nahi chahiye"],
            }
        }
    })


# ── normal ────────────────────────────────────────────────────────────────
def test_consent_phrase_grants(block):
    assert block.verify_consent("s1", "haan, theek hai") is True


def test_decline_phrase_denies(block):
    assert block.verify_consent("s1", "nahi chahiye") is False


def test_yes_grants(block):
    assert block.verify_consent("s1", "yes please") is True


def test_no_denies(block):
    assert block.verify_consent("s1", "no") is False


# ── edge ──────────────────────────────────────────────────────────────────
def test_unclear_response_denies(block):
    assert block.verify_consent("s1", "mujhe samajh nahi aaya") is False


def test_empty_message_denies(block):
    assert block.verify_consent("s1", "") is False


def test_none_message_denies(block):
    assert block.verify_consent("s1", None) is False


def test_case_insensitive_match(block):
    assert block.verify_consent("s1", "HAAN bilkul") is True


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.verify_consent(None, "haan")


# ── failure: missing config ───────────────────────────────────────────────
def test_missing_consent_config_denies():
    block = ConsentBlock({})
    assert block.verify_consent("s1", "haan") is False
