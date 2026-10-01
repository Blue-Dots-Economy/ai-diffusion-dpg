"""Tests for the dialogue-act NLU call."""
from unittest.mock import MagicMock

from src.chat_provider.types import ChatResponse, TextBlock, TokenUsage
from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import DialogueActNLU, build_output_schema, build_system_prompt_text
from src.understanding.models import ACTS


def _cfg():
    return DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {
        "mode": "dialogue_act", "topics": ["salary"], "signals": ["pay_disappointment"],
        "slots": {"age": {"type": "int", "min": 14, "max": 80, "description": "उम्र"},
                  "consent": {"type": "enum", "values": ["granted", "declined"]},
                  "trade": {"type": "string", "normalise": "title"}},
        "examples": [{"pending": "closing_offer", "caller": "ठीक है धन्यवाद",
                      "out": {"acts": ["acknowledge"], "relation": "answers_pending"}}],
    }}})


def _resp(parsed=None, stop="end_turn", error_type=None):
    return ChatResponse(content=[TextBlock(text="{}")], parsed_output=parsed, stop_reason=stop,
                        error_type=error_type, model_used="m", usage=TokenUsage())


GOOD = {"acts": ["acknowledge"], "relation": "answers_pending", "topic": None,
        "slots": {"age": None, "consent": None, "trade": None},
        "reference": {"option": None, "spoken": None}, "signals": [], "extras": []}


def _walk(node):
    yield node
    for v in (node.get("properties") or {}).values():
        yield from _walk(v)
    if isinstance(node.get("items"), dict):
        yield from _walk(node["items"])


def test_schema_is_strict_compatible():
    schema = build_output_schema(_cfg())
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
    props = schema["properties"]
    assert props["acts"]["items"]["enum"] == list(ACTS)
    assert props["slots"]["properties"]["age"]["type"] == ["integer", "null"]
    assert props["slots"]["properties"]["consent"]["enum"] == ["granted", "declined", None]
    assert props["topic"]["enum"] == ["salary", None]


def test_schema_with_no_topics_or_signals():
    cfg = DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act"}}})
    schema = build_output_schema(cfg)
    assert schema["properties"]["topic"] == {"type": "null"}
    assert schema["properties"]["signals"]["items"] == {"type": "string"}


def test_system_prompt_is_static_and_mentions_traps():
    text = build_system_prompt_text(_cfg())
    assert text == build_system_prompt_text(_cfg())
    for needle in ("acknowledge", "answers_pending", "age", "ठीक है धन्यवाद", "null"):
        assert needle in text


def test_classify_success_sends_strict_output_format():
    provider = MagicMock()
    provider.capabilities.supports_prompt_cache = True
    provider.call.return_value = _resp(GOOD)
    nlu = DialogueActNLU(_cfg(), provider)
    result, reason, ms = nlu.classify("<frame>...</frame>")
    assert reason is None and result.acts == ("acknowledge",) and ms >= 0
    req = provider.call.call_args.args[0]
    assert req.output_format.strict is True and req.output_format.schema == build_output_schema(_cfg())
    assert req.messages[0].content[0].text == "<frame>...</frame>"
    assert req.system.blocks[0].cache_hint == "session"


def test_classify_provider_error_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp(None, stop="error", error_type="timeout")
    result, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "provider_error:timeout" and result.acts == ("other",)


def test_classify_schema_violation_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp({"acts": ["shout"], "relation": "unclear"})
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "schema_violation"


def test_classify_missing_parsed_output_falls_back():
    provider = MagicMock()
    provider.call.return_value = _resp(None)
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "schema_violation"


def test_classify_exception_falls_back():
    provider = MagicMock()
    provider.call.side_effect = RuntimeError("boom")
    _, reason, _ = DialogueActNLU(_cfg(), provider).classify("x")
    assert reason == "exception"


def test_classify_empty_message_skips_llm():
    provider = MagicMock()
    result, reason, _ = DialogueActNLU(_cfg(), provider).classify("")
    assert reason == "empty_input" and provider.call.call_count == 0
