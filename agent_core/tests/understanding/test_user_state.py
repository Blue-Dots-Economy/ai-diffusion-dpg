"""User-state classification moved into the dialogue-act NLU (spec §16)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import build_output_schema, build_system_prompt_text
from src.understanding.frame import FrameBuilder
from src.understanding.models import DialogueActResult
from src.understanding.understander import TurnContext, TurnUnderstander

CFG = {
    "conversation": {"user_state_model": {"enabled": True, "default_state": "fog", "states": [
        {"id": "fog", "signals": ["पता नहीं"], "guidance": "Be gentle.\nMore."},
        {"id": "orientation", "signals": ["बताइए"], "guidance": "Give options."}]}},
    # "mode" is still required until Task 7 removes the mode check; Task 7 drops it here.
    "preprocessing": {"nlu_processor": {"mode": "dialogue_act", "user_state_confidence_threshold": 0.5,
                                        "slots": {"trade": {"type": "string"}}}},
}
WF = SimpleNamespace(subagents={"s": SimpleNamespace(pending=[])})


def test_config_and_schema_include_user_state_when_enabled():
    c = DialogueActConfig.from_config(CFG)
    assert [s["id"] for s in c.user_states] == ["fog", "orientation"] and c.user_state_default == "fog"
    us = build_output_schema(c)["properties"]["user_state"]
    assert us["properties"]["id"]["enum"] == ["fog", "orientation"]
    assert set(us["required"]) == {"id", "confidence"} and us["additionalProperties"] is False
    text = build_system_prompt_text(c)
    assert "fog" in text and "Be gentle." in text and "More." not in text


def test_schema_has_no_user_state_when_disabled():
    c = DialogueActConfig.from_config({"preprocessing": {"nlu_processor": {"mode": "dialogue_act"}}})
    assert c.user_states == () and "user_state" not in build_output_schema(c)["properties"]


def test_frame_shows_previous_state():
    text = FrameBuilder().build(step="s", pending=None, rows=[], known=[], recent=[], segments=["x"],
                                previous_state="fog")
    assert "previous_state: fog" in text


def _und(result, reason=None):
    nlu = MagicMock()
    nlu.classify.return_value = (result, reason, 1)
    return TurnUnderstander(DialogueActConfig.from_config(CFG), WF, nlu)


def _ctx(prev):
    return TurnContext(subagent_id="s", state={}, session={}, segments=["x"], previous_user_state=prev)


def test_confident_classification_is_used():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="orientation",
                               user_state_confidence=0.9)).understand(_ctx("fog"))
    assert (u.nlu_result.user_state.id, u.nlu_result.user_state.confidence) == ("orientation", 0.9)


def test_low_confidence_keeps_previous_state():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="orientation",
                               user_state_confidence=0.2)).understand(_ctx("fog"))
    assert u.nlu_result.user_state.id == "fog"


def test_unknown_or_missing_state_falls_back_to_previous_then_default():
    u = _und(DialogueActResult(acts=("other",), relation="unclear", user_state_id="bogus",
                               user_state_confidence=0.9)).understand(_ctx(None))
    assert u.nlu_result.user_state.id == "fog"


def test_fallback_result_carries_no_user_state():
    u = _und(DialogueActResult.fallback(), reason="provider_error:timeout").understand(_ctx("orientation"))
    assert u.nlu_result.user_state is None          # resolve_user_state then keeps the previous payload
