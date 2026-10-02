from dev_kit.schemas.spoken_copy_lint import lint_spoken_copy


def test_flags_digits_and_markdown_in_authored_copy():
    cfg = {"conversation": {"blocked_message": "कॉल 1800 पर करें", "unsupported_language_message": "ठीक है"},
           "agent_workflow": {"subagents": [
               {"id": "ended", "opening_phrase": "**धन्यवाद**", "fixed_opening": ""},
               {"id": "profile_resolve", "opening_phrase": "नमस्ते", "fixed_opening": "{stored_trade} में 3 जॉब"}]}}
    warnings = lint_spoken_copy(cfg)
    assert len(warnings) == 3
    assert any("conversation.blocked_message" in w and "digit" in w for w in warnings)
    assert any("subagents[ended].opening_phrase" in w and "markdown" in w for w in warnings)
    assert any("subagents[profile_resolve].fixed_opening" in w for w in warnings)


def test_placeholders_are_not_digits():
    cfg = {"agent_workflow": {"subagents": [{"id": "x", "fixed_opening": "{stored_trade} में {stored_location}"}]}}
    assert lint_spoken_copy(cfg) == []


def test_malformed_config_does_not_raise():
    assert lint_spoken_copy({"conversation": "x", "agent_workflow": "y"}) == []
    assert lint_spoken_copy({"agent_workflow": {"subagents": "z"}}) == []
