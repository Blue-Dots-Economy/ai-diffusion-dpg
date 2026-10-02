import pytest

from src.output.spoken_numbers import (
    _HI, digits_one_by_one, integer_words, number_words, spoken_pay,
)

_HI_VALUE = {w: i for i, w in enumerate(_HI)}
_HI_MULT = {"हज़ार": 1_000, "लाख": 100_000, "करोड़": 10_000_000}


def _parse_hindi(text: str) -> int:
    """Inverse of integer_words(…, 'hindi') for the round-trip test."""
    total, current = 0, 0
    for tok in text.split():
        if tok in _HI_VALUE:
            current += _HI_VALUE[tok]
        elif tok == "सौ":
            current *= 100
        elif tok in _HI_MULT:
            total += current * _HI_MULT[tok]
            current = 0
        else:
            raise AssertionError(f"unexpected token {tok!r} in {text!r}")
    return total + current


def test_hindi_table_has_one_hundred_entries():
    assert len(_HI) == 100 and len(set(_HI)) == 100


@pytest.mark.parametrize("n,words", [
    (0, "शून्य"), (24, "चौबीस"), (100, "एक सौ"), (27620, "सत्ताईस हज़ार छह सौ बीस"),
    (25755, "पच्चीस हज़ार सात सौ पचपन"), (31121, "इकतीस हज़ार एक सौ इक्कीस"),
    (8192, "आठ हज़ार एक सौ बानवे"), (100000, "एक लाख"), (2500000, "पच्चीस लाख"),
    (10000000, "एक करोड़"), (1205000000, "एक सौ बीस करोड़ पचास लाख"),
])
def test_hindi_known_values(n, words):
    assert integer_words(n, "hindi") == words


def test_hindi_round_trips_zero_to_99999():
    for n in range(100_000):
        assert _parse_hindi(integer_words(n, "hindi")) == n, n


@pytest.mark.parametrize("n,words", [
    (11, "ग्यारह"), (19, "उन्नीस"), (29, "उनतीस"), (39, "उनतालीस"), (44, "चौवालीस"),
    (49, "उनचास"), (53, "तिरपन"), (59, "उनसठ"), (63, "तिरसठ"), (67, "सड़सठ"),
    (69, "उनहत्तर"), (79, "उनासी"), (89, "नवासी"), (98, "अट्ठानवे"), (99, "निन्यानवे"),
])
def test_hindi_specific_words(n, words):
    """Verify specific Hindi words independently (not circular with round-trip test)."""
    assert integer_words(n, "hindi") == words


@pytest.mark.parametrize("n,words", [
    (0, "zero"), (21, "twenty-one"), (100, "one hundred"),
    (27620, "twenty-seven thousand six hundred twenty"), (1000000, "one million"),
])
def test_english_known_values(n, words):
    assert integer_words(n, "english") == words


def test_unsupported_language_and_negative_raise():
    with pytest.raises(ValueError):
        integer_words(5, "tamil")
    with pytest.raises(ValueError):
        integer_words(-1, "hindi")


def test_number_words_handles_grouping_and_decimals():
    assert number_words("25,755", "hindi") == "पच्चीस हज़ार सात सौ पचपन"
    assert number_words("2,50,000", "hindi") == "दो लाख पचास हज़ार"
    assert number_words("25.5", "hindi") == "पच्चीस दशमलव पाँच"
    assert number_words("3.25", "english") == "three point two five"


def test_digits_one_by_one():
    assert digits_one_by_one("9870", "hindi") == "नौ, आठ, सात, शून्य"
    assert digits_one_by_one("12", "english") == "one, two"


@pytest.mark.parametrize("values,expected", [
    ([25755, 31121], "पच्चीस से इकतीस हज़ार रुपये महीना"),
    (["24951", "32435"], "चौबीस से बत्तीस हज़ार रुपये महीना"),
    ([8192, 14404], "आठ से चौदह हज़ार रुपये महीना"),
    ([25000, 25900], "करीब पच्चीस हज़ार रुपये महीना"),
    ([None, 30000], "करीब तीस हज़ार रुपये महीना"),
    ([500, 800], "पाँच सौ से आठ सौ रुपये महीना"),
])
def test_range_thousands_hindi(values, expected):
    assert spoken_pay("range_thousands", values, "per_month", "hindi") == expected


@pytest.mark.parametrize("values", [[None, None], ["", None], [32000, 25000], ["abc", 5000], [-5, 10]])
def test_range_thousands_rejects_impossible_or_missing(values):
    assert spoken_pay("range_thousands", values, "per_month", "hindi") is None


def test_amount_and_english_units():
    assert spoken_pay("amount", [500], "per_day", "hindi") == "पाँच सौ रुपये दिन का"
    assert spoken_pay("amount", [500, 700], "per_task", "english") == "five hundred to seven hundred rupees per task"
    assert spoken_pay("range_thousands", [25755, 31121], "none", "english") == "twenty-five to thirty-one thousand"


@pytest.mark.parametrize("v", ["NaN", "Infinity", float("inf"), "1e9999999", 10**13])
def test_untrusted_values_return_none(v):
    """spoken_pay must not raise or hang on untrusted values."""
    assert spoken_pay("range_thousands", [v, 30000], "none", "hindi") is None


@pytest.mark.parametrize("values", [[0], [0, 0]])
def test_zero_pay_returns_none(values):
    """Zero pay is junk data and should return None."""
    assert spoken_pay("range_thousands", values, "per_month", "hindi") is None
    assert spoken_pay("amount", values, "per_month", "english") is None


@pytest.mark.parametrize("values", [[0, 5000], [5000, 0]])
def test_zero_bound_is_dropped_either_order(values):
    """A zero bound means 'no bound': speak the other one alone."""
    assert spoken_pay("range_thousands", values, "per_month", "hindi") == "करीब पाँच हज़ार रुपये महीना"
