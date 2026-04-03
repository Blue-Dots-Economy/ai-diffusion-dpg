import pytest
from trust_layer.src.blocks.content import ContentBlock


@pytest.fixture
def config():
    return {
        "trust": {
            "input_rules": {
                "blocked_phrases": ["bomb", "kill"],
                "escalation_topics": ["suicide", "police case"],
                "blocked_input_message": "Cannot help.",
            },
            "output_rules": {
                "blocked_phrases": ["guaranteed placement"],
                "output_blocked_message": "Bad output.",
            },
        }
    }


@pytest.fixture
def block(config):
    return ContentBlock(config)


# ── check_input normal ────────────────────────────────────────────────────
def test_check_input_allow(block):
    result = block.check_input("s1", "main electrician kaam chahiye")
    assert result["action"] == "allow"
    assert result["passed"] is True


def test_check_input_block(block):
    result = block.check_input("s1", "main bomb banana chahta hoon")
    assert result["action"] == "block"
    assert result["passed"] is False


def test_check_input_escalate(block):
    result = block.check_input("s1", "maine suicide ke baare mein socha")
    assert result["action"] == "escalate"
    assert result["passed"] is False


def test_check_input_case_insensitive(block):
    result = block.check_input("s1", "BOMB ka kya naam hai")
    assert result["action"] == "block"


# ── check_input edge ──────────────────────────────────────────────────────
def test_check_input_empty_message(block):
    result = block.check_input("s1", "")
    assert result["action"] == "allow"


def test_check_input_none_message(block):
    result = block.check_input("s1", None)
    assert result["action"] == "allow"


def test_check_input_active_risks_none_no_error(block):
    result = block.check_input("s1", "hello", active_risks=None)
    assert result["action"] == "allow"


def test_check_input_none_session_raises(block):
    with pytest.raises(ValueError):
        block.check_input(None, "hello")


# ── check_output normal ───────────────────────────────────────────────────
def test_check_output_allow(block):
    result = block.check_output("s1", "Yahan kuch jobs hain jo match karti hain.")
    assert result["action"] == "allow"


def test_check_output_block(block):
    result = block.check_output("s1", "Aapko guaranteed placement milegi.")
    assert result["action"] == "block"


# ── check_output edge ─────────────────────────────────────────────────────
def test_check_output_empty(block):
    result = block.check_output("s1", "")
    assert result["action"] == "allow"


def test_check_output_none_message(block):
    result = block.check_output("s1", None)
    assert result["action"] == "allow"


# ── failure: missing config sections ─────────────────────────────────────
def test_missing_trust_config():
    block = ContentBlock({})
    result = block.check_input("s1", "bomb")
    assert result["action"] == "allow"   # no phrases loaded → allow (no false positive)
