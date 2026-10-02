from src.output.contract import contract_language, render_output_contract

CONTRACT = {
    "default_language": "hindi",
    "languages": {
        "hindi": {"script": "devanagari", "numbers": "words", "rules": ["Devanagari only.", "Times as सुबह / शाम."]},
        "english": {"script": "latin", "numbers": "words", "rules": ["Plain spoken English."]},
    },
    "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True},
}


def test_render_default_language_first_then_groups():
    text = render_output_contract(CONTRACT)
    assert text.splitlines()[:4] == [
        "- Write in Devanagari script.",
        "- Write every number in words, never as digits.",
        "- Devanagari only.",
        "- Times as सुबह / शाम.",
    ]
    assert "If the conversation is in english:\n- Write in Latin script." in text
    assert text.endswith("- Plain spoken English.")


def test_render_empty_for_missing_contract():
    assert render_output_contract(None) == ""
    assert render_output_contract({}) == ""


def test_contract_language_prefers_supported_preference():
    assert contract_language(CONTRACT, "english") == "english"
    assert contract_language(CONTRACT, "tamil") == "hindi"
    assert contract_language(CONTRACT, None) == "hindi"
    assert contract_language(None, "english") == ""
