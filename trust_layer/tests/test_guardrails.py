"""
trust_layer/tests/test_guardrails.py

Unit tests for BasicTrustLayer.

Covers:
- Clean input → allow
- Input with blocked phrase → block with reason
- Input with escalation topic → escalate with reason
- Clean output → allow
- Output with blocked phrase → block
- Empty input → allow
- Empty output → allow
- check_consent always returns True
- Case-insensitive matching (BOMB matches bomb)
- Mixed-case escalation topic matches
- None session_id raises ValueError
- Missing config sections → safe defaults (allow everything)
"""

import pytest
from src.guardrails import BasicTrustLayer

CONFIG = {
    "trust": {
        "input_rules": {
            "blocked_phrases": ["bomb", "weapon", "kill", "threat"],
            "escalation_topics": ["arrested", "police case", "court notice", "legal action", "FIR"],
        },
        "output_rules": {
            "blocked_phrases": ["I cannot help with that", "as an AI, I"],
        },
    }
}

EMPTY_CONFIG = {"trust": {}}


@pytest.fixture
def trust():
    return BasicTrustLayer(config=CONFIG)


@pytest.fixture
def trust_empty():
    return BasicTrustLayer(config=EMPTY_CONFIG)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_none_config_raises():
    with pytest.raises(ValueError, match="config must not be None"):
        BasicTrustLayer(config=None)


def test_missing_trust_section_does_not_raise():
    """Config without 'trust' key must not raise — defaults to allow-all."""
    layer = BasicTrustLayer(config={})
    result = layer.check_input("s1", "any message")
    assert result["action"] == "allow"


# ---------------------------------------------------------------------------
# check_input — allow
# ---------------------------------------------------------------------------

def test_clean_input_is_allowed(trust):
    result = trust.check_input("s1", "Hubli mein electrician kaam chahiye")
    assert result["action"] == "allow"
    assert result["passed"] is True


def test_empty_input_is_allowed(trust):
    result = trust.check_input("s1", "")
    assert result["action"] == "allow"
    assert result["passed"] is True


def test_none_message_is_allowed(trust):
    result = trust.check_input("s1", None)
    assert result["action"] == "allow"


# ---------------------------------------------------------------------------
# check_input — block
# ---------------------------------------------------------------------------

def test_blocked_phrase_returns_block(trust):
    result = trust.check_input("s1", "I want to make a bomb")
    assert result["action"] == "block"
    assert result["passed"] is False
    assert "bomb" in result["reason"]


def test_case_insensitive_block(trust):
    result = trust.check_input("s1", "BOMB mujhe chahiye")
    assert result["action"] == "block"


def test_partial_match_blocked(trust):
    """Phrase match is substring — 'weapon' in 'illegal weapons' must block."""
    result = trust.check_input("s1", "illegal weapons sale")
    assert result["action"] == "block"


# ---------------------------------------------------------------------------
# check_input — escalate
# ---------------------------------------------------------------------------

def test_escalation_topic_returns_escalate(trust):
    result = trust.check_input("s1", "mujhe police case mein madad chahiye")
    assert result["action"] == "escalate"
    assert result["passed"] is False
    assert "police case" in result["reason"]


def test_case_insensitive_escalation(trust):
    result = trust.check_input("s1", "ARRESTED hone ke baad kya karein")
    assert result["action"] == "escalate"


def test_block_takes_priority_over_escalate(trust):
    """If both a blocked phrase and escalation topic appear, block wins."""
    result = trust.check_input("s1", "bomb aur police case")
    assert result["action"] == "block"


# ---------------------------------------------------------------------------
# check_output
# ---------------------------------------------------------------------------

def test_clean_output_is_allowed(trust):
    result = trust.check_output("s1", "Hubli mein 3 ITI centres hain jahan electrician training milti hai.")
    assert result["action"] == "allow"
    assert result["passed"] is True


def test_empty_output_is_allowed(trust):
    result = trust.check_output("s1", "")
    assert result["action"] == "allow"


def test_blocked_output_phrase_returns_block(trust):
    result = trust.check_output("s1", "I cannot help with that request.")
    assert result["action"] == "block"
    assert result["passed"] is False


def test_case_insensitive_output_block(trust):
    result = trust.check_output("s1", "AS AN AI, I cannot answer this.")
    assert result["action"] == "block"


def test_empty_output_rules_allows_everything(trust_empty):
    result = trust_empty.check_output("s1", "any output")
    assert result["action"] == "allow"


# ---------------------------------------------------------------------------
# check_consent
# ---------------------------------------------------------------------------

def test_consent_always_true(trust):
    assert trust.check_consent("s1", "onest_market_lookup") is True


def test_consent_true_for_any_connector(trust):
    assert trust.check_consent("s1", "submit_application") is True
    assert trust.check_consent("s1", "verify_identity") is True


# ---------------------------------------------------------------------------
# None session_id
# ---------------------------------------------------------------------------

def test_none_session_id_raises_on_check_input(trust):
    with pytest.raises(ValueError, match="session_id must not be None"):
        trust.check_input(None, "hello")


def test_none_session_id_raises_on_check_output(trust):
    with pytest.raises(ValueError, match="session_id must not be None"):
        trust.check_output(None, "hello")


def test_none_session_id_raises_on_check_consent(trust):
    with pytest.raises(ValueError, match="session_id must not be None"):
        trust.check_consent(None, "tool")


# ---------------------------------------------------------------------------
# Whole-word matching — regressions from live blue-dots calls
# ---------------------------------------------------------------------------

def test_employer_name_containing_a_blocked_stem_is_allowed(trust):
    """"kill" inside "SkillBridge" must not block the message.

    Observed live: the job list contained the employer "SkillBridge Network",
    so every attempt to select that job was blocked as prohibited content and
    the caller could never apply to it.
    """
    result = trust.check_input("s1", "I want to apply to SkillBridge Network")
    assert result["action"] == "allow"


@pytest.mark.parametrize("message", [
    "the first one",
    "yes confirm",
    "please confirm the first job",
    "a firm in Bengaluru",
])
def test_words_containing_an_escalation_topic_do_not_escalate(trust, message):
    """"FIR" (a police report) must not match "first", "confirm" or "firm".

    Observed live: answering "the first one" to a job list escalated the
    caller to a human agent mid-flow.
    """
    assert trust.check_input("s1", message)["action"] == "allow"


def test_blocked_phrase_still_matches_as_a_whole_word(trust):
    """The narrowing must not stop real hits from blocking."""
    assert trust.check_input("s1", "I will kill him")["action"] == "block"


def test_escalation_topic_still_matches_as_a_whole_word(trust):
    """Standalone "FIR" still escalates, case-insensitively."""
    assert trust.check_input("s1", "should I file an FIR")["action"] == "escalate"


def test_multi_word_escalation_topic_still_matches(trust):
    assert trust.check_input("s1", "there is a court notice")["action"] == "escalate"


def test_plural_of_a_term_still_matches(trust):
    """The trailing "s" allowance keeps stem rules working."""
    assert trust.check_input("s1", "he made threats")["action"] == "block"


# ---------------------------------------------------------------------------
# Inflected forms must still match — the direction whole-word matching breaks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "thinking about killing myself",
    "he killed my brother",
    "he is a killer",
    "they threatened me",
    "my employer is threatening me",
    "there was a bombing at the site",
])
def test_inflected_blocked_terms_still_block(trust, message):
    """A rule must catch the forms a caller actually types.

    Anchoring on word boundaries without allowing inflections would let all of
    these through: the stem is present but the typed word is not the stem. This
    is the failure direction that a plural-only suffix missed.
    """
    assert trust.check_input("s1", message)["action"] == "block"


def test_inflected_escalation_topic_still_escalates(trust):
    """Multi-word topics inflect too — "court notice" must catch "court notices"."""
    assert trust.check_input("s1", "i received two court notices")["action"] == "escalate"


# ---------------------------------------------------------------------------
# ...but short terms must NOT inflect — they collide with ordinary words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "i was fired from my job",
    "they are firing staff",
    "the company fires people often",
    "i am a firefighter",
    "fire safety officer role",
])
def test_short_topic_does_not_match_inflections_of_other_words(trust, message):
    """"FIR" is three characters and must match as a whole word only.

    With inflections enabled it would also match "fired", "firing" and "fires" —
    three of the most ordinary words in a jobs conversation, each of which would
    escalate the caller to a human agent mid-flow.
    """
    assert trust.check_input("s1", message)["action"] == "allow"


def test_short_topic_still_matches_on_its_own(trust):
    assert trust.check_input("s1", "should i file an FIR")["action"] == "escalate"
