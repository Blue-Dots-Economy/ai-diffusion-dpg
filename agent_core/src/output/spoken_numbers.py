"""
agent_core/src/output/spoken_numbers.py
Deterministic number-to-words for spoken output (Spec D §4, §5). Table-driven
Hindi (irregular 0–99, then सौ / हज़ार / लाख / करोड़) and English. Pure; no I/O.
Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

SUPPORTED_LANGUAGES = frozenset({"hindi", "english"})

_HI: tuple[str, ...] = (
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरेपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरेसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
)
_EN_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
            "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

RANGE_JOINER = {"hindi": "से", "english": "to"}
CURRENCY_WORD = {"hindi": "रुपये", "english": "rupees"}
_POINT = {"hindi": "दशमलव", "english": "point"}
_THOUSAND = {"hindi": "हज़ार", "english": "thousand"}
_AROUND = {"hindi": "करीब", "english": "around"}
_UNITS = {
    "hindi": {"none": "", "per_month": "रुपये महीना", "per_task": "रुपये प्रति काम", "per_day": "रुपये दिन का"},
    "english": {"none": "", "per_month": "rupees a month", "per_task": "rupees per task", "per_day": "rupees a day"},
}


def _check(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"no spoken-number converter for language {language!r}")


def _hindi(n: int) -> str:
    if n < 100:
        return _HI[n]
    parts: list[str] = []
    crore, n = divmod(n, 10_000_000)
    if crore:
        parts.append(f"{_hindi(crore)} करोड़")
    for div, name in ((100_000, "लाख"), (1_000, "हज़ार"), (100, "सौ")):
        q, n = divmod(n, div)
        if q:
            parts.append(f"{_HI[q]} {name}")
    if n:
        parts.append(_HI[n])
    return " ".join(parts)


def _english(n: int) -> str:
    if n < 20:
        return _EN_ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _EN_TENS[t] + (f"-{_EN_ONES[o]}" if o else "")
    for div, name in ((10**9, "billion"), (10**6, "million"), (1_000, "thousand"), (100, "hundred")):
        if n >= div:
            q, r = divmod(n, div)
            return f"{_english(q)} {name}" + (f" {_english(r)}" if r else "")
    raise AssertionError("unreachable")


def integer_words(n: int, language: str) -> str:
    """Spell a non-negative integer.

    Args:
        n: The number (>= 0).
        language: ``hindi`` or ``english``.

    Returns:
        The number in words.

    Raises:
        ValueError: On an unsupported language or a negative number.
    """
    _check(language)
    if n < 0:
        raise ValueError("negative numbers are not spoken")
    return _hindi(n) if language == "hindi" else _english(n)


def digits_one_by_one(digits: str, language: str) -> str:
    """Speak a digit string one digit at a time (phone numbers, IDs).

    Args:
        digits: Digits only.
        language: ``hindi`` or ``english``.

    Returns:
        Comma-separated digit words.
    """
    return ", ".join(integer_words(int(d), language) for d in digits if d.isdigit())


def number_words(text: str, language: str) -> str:
    """Spell a number written as digits, with optional grouping commas and decimals.

    Args:
        text: E.g. ``"25,755"``, ``"2,50,000"``, ``"25.5"``.
        language: ``hindi`` or ``english``.

    Returns:
        The number in words; decimals as "<int> दशमलव <digit> <digit>".
    """
    whole, _, frac = text.replace(",", "").partition(".")
    words = integer_words(int(whole or "0"), language)
    if frac:
        frac_words = " ".join(integer_words(int(d), language) for d in frac)
        words = f"{words} {_POINT[language]} {frac_words}"
    return words


def _to_int(value: object) -> int | None:
    """Parse a pay value; None when absent. Raises ValueError when present but unusable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("bool is not a number")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as e:
        raise ValueError(f"not a number: {value!r}") from e
    if number < 0:
        raise ValueError("negative pay")
    return int(number)


def _with_unit(text: str, unit: str, language: str) -> str:
    suffix = _UNITS[language].get(unit, "")
    return f"{text} {suffix}" if suffix else text


def spoken_pay(fmt: str, values: list, unit: str, language: str) -> str | None:
    """Render a pay figure or range ready to speak (Spec D §4.1).

    Args:
        fmt: ``range_thousands`` (round each bound down to its thousand,
            ascending pair said once) or ``amount`` (exact words).
        values: One or two raw values (lower bound first).
        unit: ``none`` | ``per_month`` | ``per_task`` | ``per_day``.
        language: ``hindi`` or ``english``.

    Returns:
        The spoken text, or None when there is nothing trustworthy to say
        (no values, non-numeric, negative, or lower bound above upper).
    """
    _check(language)
    try:
        nums = [_to_int(v) for v in values[:2]]
    except ValueError:
        return None
    present = [n for n in nums if n is not None]
    if not present:
        return None
    joiner = RANGE_JOINER[language]
    if len(present) == 2 and present[0] > present[1]:
        return None
    w = lambda n: integer_words(n, language)  # noqa: E731
    if fmt == "amount":
        text = w(present[0]) if len(present) == 1 else f"{w(present[0])} {joiner} {w(present[1])}"
        return _with_unit(text, unit, language)
    if fmt != "range_thousands":
        raise ValueError(f"unknown pay format {fmt!r}")
    if len(present) == 1 or (present[0] >= 1000 and present[0] // 1000 == present[1] // 1000):
        n = present[-1] if len(present) == 1 else present[0]
        core = f"{w(n // 1000)} {_THOUSAND[language]}" if n >= 1000 else w(n)
        return _with_unit(f"{_AROUND[language]} {core}", unit, language)
    lo, hi = present
    if lo >= 1000:
        text = f"{w(lo // 1000)} {joiner} {w(hi // 1000)} {_THOUSAND[language]}"
    else:
        text = f"{w(lo)} {joiner} {w(hi)}"
    return _with_unit(text, unit, language)
