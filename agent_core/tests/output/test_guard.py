import pytest

from src.output.guard import OutputGuard

CONTRACT = {
    "default_language": "hindi",
    "languages": {"hindi": {"script": "devanagari", "numbers": "words", "rules": []},
                  "english": {"script": "latin", "numbers": "words", "rules": []}},
    "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True},
}


@pytest.fixture
def g():
    return OutputGuard(CONTRACT)


def test_disabled_without_contract():
    guard = OutputGuard(None)
    assert guard.enabled is False
    r = guard.apply("**27** jobs", "hindi")
    assert (r.text, r.digits_rewritten, r.foreign_script_words) == ("**27** jobs", 0, 0)


def test_strips_markdown(g):
    r = g.apply("**सैलरी** `अच्छी` है, [यहाँ](http://x) देखें", "hindi")
    assert r.text == "सैलरी अच्छी है, यहाँ देखें"
    assert g.apply("- पहला विकल्प", "hindi").text == "पहला विकल्प"


def test_rewrites_plain_number_and_counts(g):
    r = g.apply("सैलरी 27620 है।", "hindi")
    assert r.text == "सैलरी सत्ताईस हज़ार छह सौ बीस है।"
    assert r.digits_rewritten == 1


def test_rewrites_range_and_currency(g):
    assert g.apply("₹20,000-30,000 महीना", "hindi").text == "बीस हज़ार से तीस हज़ार रुपये महीना"
    assert g.apply("Rs 500 रोज़", "hindi").text == "पाँच सौ रुपये रोज़"


def test_phone_is_digit_by_digit(g):
    assert g.apply("नंबर 9876543210 है", "hindi").text == \
        "नंबर नौ, आठ, सात, छह, पाँच, चार, तीन, दो, एक, शून्य है"
    assert g.apply("नंबर 98765 43210 है", "hindi").text.startswith("नंबर नौ, आठ, सात, छह, पाँच, चार")


def test_dash_number_pairs_are_ranges_not_one_number(g):
    assert g.apply("98765-43210", "hindi").text == "अट्ठानवे हज़ार सात सौ पैंसठ से तैंतालीस हज़ार दो सौ दस"


def test_devanagari_digits_are_rewritten(g):
    assert g.apply("२७ हज़ार", "hindi").text == "सत्ताईस हज़ार"


def test_decimal_survives_sentence_punctuation(g):
    assert g.apply("दूरी 2.5 किलोमीटर है।", "hindi").text == "दूरी दो दशमलव पाँच किलोमीटर है।"


def test_counts_latin_words_without_rewriting(g):
    r = g.apply("QUESS CORP में जॉब है", "hindi")
    assert r.text == "QUESS CORP में जॉब है"
    assert r.foreign_script_words == 2


def test_english_contract_uses_english_words(g):
    r = g.apply("It pays 25000 a month.", "english")
    assert r.text == "It pays twenty-five thousand a month."
    assert r.foreign_script_words == 0


def test_unknown_language_falls_back_to_default(g):
    assert g.apply("27", "tamil").text == "सत्ताईस"


def test_never_raises(g, monkeypatch):
    import src.output.guard as mod
    monkeypatch.setattr(mod, "number_words", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = g.apply("सैलरी 27 है", "hindi")
    assert r.text == "सैलरी 27 है" and r.digits_rewritten == 0
