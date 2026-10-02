# Spec D: Main-LLM Context, Output Contract and Blue Dots Prompts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the main LLM correct, compact context and make every spoken reply TTS-ready, without adding a model call or a network hop.

**Architecture:**
- Four new pure modules under `agent_core/src/output/` and `agent_core/src/context/`:
  - spoken numbers;
  - the output guard;
  - output-contract rendering;
  - result shaping and the state/recent renderers.
- These are wired into both turn paths (`process_turn` and `stream_turn`) at three points:
  - tool-result ingress;
  - the sentence stream, before Trust;
  - prompt assembly.
- The runtime schema gains `output_contract`, `result_shaping`, `agent.history_turns` and `agent.state_fields`, and drops `tts_rules`. The dev-kit mirrors follow, and the Blue Dots config is rewritten.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest + pytest-asyncio, `uv`. The OpenTelemetry metrics API is already a dependency.

**Spec:** `docs/superpowers/specs/2026-10-01-main-llm-context-design.md`.

## Global Constraints

- Work only in `/Users/aniket/Documents/github/aniketsaki/ai-diffusion-spec-d` on branch `spec/main-llm-context` (base `cf794ef`). Commit there; never push.
- Run tests per module:
  - `cd agent_core && uv run pytest -q`
  - `cd dev-kit && uv run pytest -q`
  - `cd reach_layer && uv run pytest -q bridge/tests/test_blue_dots_config.py`
- **Baselines (not yours to fix):**
  - agent_core: 1359 passed, 1 skipped, plus 1 UserWarning (`OutputFormat.schema`);
  - dev-kit: 1 failure, `test_dpg_yaml_validates[reach_layer]`.
- **No compatibility path.** `tts_rules` (runtime `TtsRulesConfig`, the dev-kit mirrors, FIELD_RULES, `channel_tts.py`, the wizard) is removed and becomes a rejected key. `<known_profile>`, `<active_guardrails>` and the `[Last question asked: …]` prefix are removed.
- **Runtime ↔ dev-kit sync** (`.claude/rules/runtime-devkit-sync.md`). Every runtime schema change updates all of these in the same task series:
  - `dev-kit/dpg/agent_core.yaml`;
  - `dev-kit/dev_kit/schemas/domain/agent_core.py`;
  - `dev-kit/dev_kit/agent/field_rules/agent_core.py`;
  - `dev-kit/dev_kit/schema.py`;
  - the phase prompts;
  - the dev-kit tests.
- **Runtime schemas stay self-contained.** `agent_core/src/schema/config.py` imports only from `pydantic`, `enum`, `typing` and `__future__`.
- **Read config as plain dicts.** The orchestrator and `ManagerAgent` read the raw merged config dict (`self._config`, `channel_config`), so new readers must read dict keys and must not assume Pydantic defaults have been applied.
- **Never raise into the turn.** Shaping, the guard and the renderers fall back to pass-through, or omit their block, on any error.
- **Logs never carry caller text, model text or slot values.** Log counts, keys and reasons only.
- Google-style docstrings. Coverage for `agent_core` stays ≥ 70%.
- The repo is public, so no vulnerability details go in commits. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec clarifications (rulings made while planning, from the code map)

These were settled while mapping the spec onto the code. Each one is binding for the tasks below.

1. **Shaping rewrites `ToolResult.result_text`, not `result`.**
   - The Action Gateway puts its projected rows only in `result_text`, as a JSON string; `result` stays the raw upstream dict. `TurnToolCache.after_call` stores `json.loads(result_text)` and requires `projected=True`.
   - So shaping parses `result_text`, shapes it, and re-serialises it. `projected` stays `True`, and `result` is left untouched.
   - Rows the gateway trims for `max_size_chars` are already gone, so shaping orders the surviving rows only.
2. **`spoken` fields use `preprocessing.language_normalisation.default_language`.** Connectors are channel-agnostic, so they can't use a channel's contract language. The value must be `hindi` or `english` when any connector declares `spoken`.
3. **`contains` lives only in the shaper.** Routing operators are unchanged, and the shaper has its own six-operator evaluator.
4. **Three stream guard sites.** The two `_trust_batcher.add(sentence)` sites and the tail flush `_trust_batcher.add(remaining)` all go through one helper.
5. **`<state>` uses the post-routing subagent (`next_subagent_id`),** because the prompt is built for it. The orchestrator owns its own `PendingResolver(self._workflow)`. `_offered_entry` is promoted to a public `offered_entry(served, tool_cache, tool)` in `understanding/frame.py`.
6. **Sync shaping is injected.** `ManagerAgent.run_turn` takes `result_shaper: Callable[[ToolResult], ToolResult] | None = None`, and `SessionBootstrap` takes `shape: Callable[[ToolResult], ToolResult] | None = None`.
7. **The language cross-check is a `MergedConfig` `model_validator`,** because `ChannelConfig` can't see `preprocessing`.
8. **The flat dev-kit `ChannelConfig` gets `extra="forbid"`,** after adding its missing `max_tokens` and `terminal_word` fields, so that `tts_rules` is rejected there too.
9. **`agent_core/config/domain.yaml`** (the template) migrates its `channels.voice.tts_rules` to `output_contract`, and deletes `tts_rules: null` from `web` and `cli`.
10. **The old `<!-- tts_rules:begin/end -->` suffix markers** are deleted together with `channel_tts.py`, with no one-time strip. No shipped config carries them.

## Review Focus

These five inputs aren't exercised by any task's main tests, and each is pinned by a test in the task listed:

1. **A range written with a dash that is really a phone number,** e.g. "98765-43210". It must be spoken as a range of two numbers, never as one huge number. A phone without a dash ("9876543210") is spoken digit by digit. (Task 2, `test_dash_number_pairs_are_ranges_not_one_number`.)
2. **A caller who switched to English.** Guard digits become *English* words, and the `english` contract applies, while `salary_spoken` stays in the default language. (Task 2, `test_english_contract_uses_english_words`; Task 7, `test_guard_language_follows_language_preference`.)
3. **A `fetch_jobs` page where every row is unusable.** Shaping returns an empty list, not the unshaped rows. The cache then holds `[]` and `<state>` shows no `offered` line. (Task 3, `test_all_rows_dropped_yields_empty_list`.)
4. **A cache-hit tool result.** It is already shaped and must not be re-shaped on the stream path (`tool_cache.lookup` branch). (Task 6, `test_cache_hit_is_not_reshaped`.)
5. **An interrupted previous turn.** `<recent>` shows "bot (caller heard only):" with the heard text, and the next turn's prompt still has `<state>`. (Task 5, `test_recent_marks_interrupted_turn`.)

---

### Task 1: Spoken numbers (Hindi and English)

**Files:**
- Create: `agent_core/src/output/__init__.py` (empty docstring module)
- Create: `agent_core/src/output/spoken_numbers.py`
- Test: `agent_core/tests/output/__init__.py`, `agent_core/tests/output/test_spoken_numbers.py`

**Interfaces:**
- Produces:
  - `SUPPORTED_LANGUAGES: frozenset[str]` = `{"hindi", "english"}`.
  - `integer_words(n: int, language: str) -> str`. Raises `ValueError` for an unsupported language or a negative `n`.
  - `number_words(text: str, language: str) -> str`. `text` is a digit string, with optional grouping commas and an optional decimal part.
  - `digits_one_by_one(digits: str, language: str) -> str`.
  - `spoken_pay(fmt: str, values: list, unit: str, language: str) -> str | None`. `fmt` is one of `range_thousands`, `amount`; `unit` is one of `none`, `per_month`, `per_task`, `per_day`.
  - `RANGE_JOINER: dict[str, str]` and `CURRENCY_WORD: dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/output/test_spoken_numbers.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/output/test_spoken_numbers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.output'`.

- [ ] **Step 3: Write the implementation**

```python
# agent_core/src/output/__init__.py
"""Output-side helpers: spoken numbers, output guard, output contract, result shaping (Spec D)."""
```

```python
# agent_core/src/output/spoken_numbers.py
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
```

`agent_core/tests/output/__init__.py` is an empty file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/output/test_spoken_numbers.py -q`
Expected: all pass. The round-trip test takes a few seconds.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/output agent_core/tests/output
git commit -m "feat(agent_core): deterministic Hindi/English spoken numbers (Spec D)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Output contract rendering and the output guard

**Files:**
- Create: `agent_core/src/output/contract.py`
- Create: `agent_core/src/output/guard.py`
- Test: `agent_core/tests/output/test_contract.py`, `agent_core/tests/output/test_guard.py`

**Interfaces:**
- Consumes: Task 1 `integer_words`, `number_words`, `digits_one_by_one`, `RANGE_JOINER`, `CURRENCY_WORD`, `SUPPORTED_LANGUAGES`.
- Produces:
  - `contract.render_output_contract(contract: dict | None) -> str` (empty when there is no contract).
  - `contract.contract_language(contract: dict | None, preference: str | None) -> str`.
  - `guard.GuardResult` (a frozen dataclass: `text: str`, `digits_rewritten: int`, `foreign_script_words: int`).
  - `guard.OutputGuard(contract: dict | None)`, with `.enabled: bool` and `.apply(text: str, language: str) -> GuardResult`, which never raises.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/output/test_contract.py
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
```

```python
# agent_core/tests/output/test_guard.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/output/test_contract.py tests/output/test_guard.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.output.contract'`.

- [ ] **Step 3: Write the implementation**

```python
# agent_core/src/output/contract.py
"""
agent_core/src/output/contract.py
Renders a channel's output_contract (Spec D §3) into the static prompt block
and picks the contract language for a turn. Pure. Belongs to the Agent Core
DPG block.
"""
from __future__ import annotations

_SCRIPT_LINE = {"devanagari": "Write in Devanagari script.", "latin": "Write in Latin script."}
_NUMBERS_LINE = {"words": "Write every number in words, never as digits."}


def _language_lines(entry: dict) -> list[str]:
    lines: list[str] = []
    if entry.get("script") in _SCRIPT_LINE:
        lines.append(_SCRIPT_LINE[entry["script"]])
    if entry.get("numbers") in _NUMBERS_LINE:
        lines.append(_NUMBERS_LINE[entry["numbers"]])
    lines += [str(r).strip() for r in (entry.get("rules") or []) if str(r).strip()]
    return [f"- {line}" for line in lines]


def render_output_contract(contract: dict | None) -> str:
    """Render ``<output_contract>`` text: default language first, then one group per other language.

    Args:
        contract: Raw ``channels.<name>.output_contract`` dict, or None.

    Returns:
        Block text (static per channel, prompt-cacheable); "" when no contract.
    """
    if not isinstance(contract, dict) or not isinstance(contract.get("languages"), dict):
        return ""
    langs: dict = contract["languages"]
    default = contract.get("default_language")
    parts: list[str] = []
    if isinstance(langs.get(default), dict):
        parts.append("\n".join(_language_lines(langs[default])))
    for name, entry in langs.items():
        if name == default or not isinstance(entry, dict):
            continue
        body = "\n".join(_language_lines(entry))
        if body:
            parts.append(f"If the conversation is in {name}:\n{body}")
    return "\n\n".join(p for p in parts if p)


def contract_language(contract: dict | None, preference: str | None) -> str:
    """The turn's contract language: the caller's preference when the contract has it, else the default.

    Args:
        contract: Raw output_contract dict, or None.
        preference: Session/profile ``language_preference``, or None.

    Returns:
        Language id, or "" when there is no contract.
    """
    if not isinstance(contract, dict):
        return ""
    langs = contract.get("languages") or {}
    if preference and preference in langs:
        return preference
    return str(contract.get("default_language") or "")
```

```python
# agent_core/src/output/guard.py
"""
agent_core/src/output/guard.py
Deterministic output guard on model-generated text (Spec D §5): strip
markdown, rewrite digits as words in the contract language, count
foreign-script words. Runs per sentence before the Trust output check.
Pure and never raises. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.output.spoken_numbers import (
    CURRENCY_WORD, RANGE_JOINER, SUPPORTED_LANGUAGES, digits_one_by_one, number_words,
)

logger = logging.getLogger(__name__)

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LINE_MARKERS = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)")
_EMPHASIS = re.compile(r"\*\*|__|\*|`+")
_PHONE = re.compile(r"(?<![\d.,])\d(?: ?\d){6,}(?![\d.,])")
_NUM = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"
_CUR = r"(?:₹|\bRs\.?)"
_AMOUNT = re.compile(
    rf"(?:(?P<cur>{_CUR})\s*)?(?P<a>{_NUM})(?:\s*[-–]\s*(?:(?P<cur2>{_CUR})\s*)?(?P<b>{_NUM}))?")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_DEVANAGARI_WORD = re.compile(r"[ऀ-ॿ]{2,}")


@dataclass(frozen=True)
class GuardResult:
    """One guarded sentence.

    Attributes:
        text: The text to speak.
        digits_rewritten: Numbers rewritten as words in this text.
        foreign_script_words: Words outside the contract script (counted, never rewritten).
    """

    text: str
    digits_rewritten: int = 0
    foreign_script_words: int = 0


class OutputGuard:
    """Guard configured from a channel's raw ``output_contract`` dict.

    Args:
        contract: Raw ``channels.<name>.output_contract`` dict, or None (guard disabled).
    """

    def __init__(self, contract: dict | None) -> None:
        self._contract = contract if isinstance(contract, dict) else None
        guard = (self._contract or {}).get("guard") or {}
        self._strip = bool(guard.get("strip_markdown"))
        self._rewrite = bool(guard.get("rewrite_digits"))
        self._count = bool(guard.get("count_foreign_script"))

    @property
    def enabled(self) -> bool:
        """True when the contract turns on any guard step."""
        return self._contract is not None and (self._strip or self._rewrite or self._count)

    def _language_entry(self, language: str) -> tuple[str, dict]:
        langs = (self._contract or {}).get("languages") or {}
        name = language if language in langs else str((self._contract or {}).get("default_language") or "")
        return name, langs.get(name) or {}

    def apply(self, text: str, language: str) -> GuardResult:
        """Guard one sentence. Never raises: on an internal error the input passes through.

        Args:
            text: Model-generated sentence.
            language: Contract language for this turn (see ``contract_language``).

        Returns:
            GuardResult.
        """
        if not self.enabled or not text:
            return GuardResult(text=text)
        try:
            name, entry = self._language_entry(language)
            out, rewritten = text, 0
            if self._strip:
                out = _LINK.sub(r"\1", out)
                out = _LINE_MARKERS.sub("", out)
                out = _EMPHASIS.sub("", out)
            if self._rewrite and entry.get("numbers") == "words" and name in SUPPORTED_LANGUAGES:
                out = out.translate(_DEV_DIGITS)
                out, n_phone = _PHONE.subn(lambda m: digits_one_by_one(m.group(0), name), out)
                count = [0]

                def _amount(m: re.Match) -> str:
                    count[0] += 1
                    words = number_words(m.group("a"), name)
                    if m.group("b"):
                        words = f"{words} {RANGE_JOINER[name]} {number_words(m.group('b'), name)}"
                    if m.group("cur") or m.group("cur2"):
                        words = f"{words} {CURRENCY_WORD[name]}"
                    return words

                out = _AMOUNT.sub(_amount, out)
                rewritten = n_phone + count[0]
            foreign = 0
            if self._count:
                script = entry.get("script")
                if script == "devanagari":
                    foreign = len(_LATIN_WORD.findall(out))
                elif script == "latin":
                    foreign = len(_DEVANAGARI_WORD.findall(out))
            return GuardResult(text=re.sub(r"[ \t]{2,}", " ", out).strip(), digits_rewritten=rewritten,
                               foreign_script_words=foreign)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("output_guard.error", extra={"operation": "output_guard.apply",
                                                        "status": "failure", "error": type(e).__name__})
            return GuardResult(text=text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/output -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/output agent_core/tests/output
git commit -m "feat(agent_core): output contract rendering and deterministic output guard (Spec D §3, §5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Result shaping

**Files:**
- Create: `agent_core/src/output/result_shaping.py`
- Test: `agent_core/tests/output/test_result_shaping.py`

**Interfaces:**
- Consumes:
  - Task 1 `spoken_pay`;
  - `src.models.ToolResult`, a dataclass with `tool_use_id`, `tool_name`, `result`, `success`, `result_text`, `error`, `session_values` and `projected`.
- Produces:
  - `shape_rows(rows: list, rule: dict, language: str) -> list[dict]` (pure);
  - `strip_place_numbers(text: str) -> str`;
  - `ResultShaper(config: dict | None)`, with `.shape(result: ToolResult) -> ToolResult`, which never raises and leaves `result` and `projected` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/output/test_result_shaping.py
import json

from src.models import ToolResult
from src.output.result_shaping import ResultShaper, shape_rows, strip_place_numbers

RULE = {
    "list_key": "",
    "drop_when": [
        {"field": "role", "operator": "in", "value": [None, "", "na", "NA", "Any", "Not Available"]},
        {"field": "role", "operator": "contains", "value": "|"},
    ],
    "sort": [{"field": "match_score", "order": "desc"}, {"field": "salary_max", "order": "desc"}],
    "spoken": {"salary_spoken": {"format": "range_thousands", "from": ["salary_min", "salary_max"],
                                 "unit": "per_month"}},
    "strip_numbers_in": ["location"],
}
CONFIG = {
    "preprocessing": {"language_normalisation": {"default_language": "hindi"}},
    "connectors": {"read": [{"name": "fetch_jobs", "result_shaping": RULE}]},
}


def _rows():
    return [
        {"item_id": "a", "role": "Welder", "match_score": 0.7, "salary_min": 25755, "salary_max": 31121, "location": "Dasna, 201015"},
        {"item_id": "b", "role": "na", "match_score": 0.99},
        {"item_id": "c", "role": "Welder | Fitter", "match_score": 0.95},
        {"item_id": "d", "role": "Welder", "match_score": 0.9, "salary_min": 8192, "salary_max": 14404, "location": "Plot No. 12, Sector 5, Noida"},
        {"item_id": "e", "role": "Welder", "match_score": None, "salary_min": 32000, "salary_max": 25000},
        "not-a-row",
    ]


def test_drop_sort_spoken_strip():
    out = shape_rows(_rows(), RULE, "hindi")
    assert [r["item_id"] for r in out] == ["d", "a", "e"]
    assert out[0]["salary_spoken"] == "आठ से चौदह हज़ार रुपये महीना"
    assert out[0]["location"] == "Noida"
    assert out[1]["location"] == "Dasna"
    assert "salary_spoken" not in out[2]          # impossible range omitted, never guessed


def test_sort_is_stable_and_nulls_last():
    rows = [{"k": 1, "id": "x"}, {"k": None, "id": "n"}, {"k": 1, "id": "y"}, {"k": 2, "id": "z"}]
    out = shape_rows(rows, {"sort": [{"field": "k", "order": "desc"}]}, "hindi")
    assert [r["id"] for r in out] == ["z", "x", "y", "n"]


def test_all_rows_dropped_yields_empty_list():
    assert shape_rows([{"role": "na"}, {"role": ""}], RULE, "hindi") == []


def test_input_rows_not_mutated():
    rows = _rows()
    shape_rows(rows, RULE, "hindi")
    assert rows[0]["location"] == "Dasna, 201015" and "salary_spoken" not in rows[0]


def test_strip_place_numbers():
    assert strip_place_numbers("Dasna, 201015") == "Dasna"
    assert strip_place_numbers("House No 45, Gali 3, Sarjapur") == "Sarjapur"
    assert strip_place_numbers("Bengaluru") == "Bengaluru"


def _result(rows, *, projected=True, success=True, tool="fetch_jobs"):
    return ToolResult(tool_use_id="t1", tool_name=tool, result={"raw": True}, success=success,
                      result_text=json.dumps(rows), projected=projected)


def test_shaper_rewrites_result_text_only():
    shaped = ResultShaper(CONFIG).shape(_result(_rows()))
    assert [r["item_id"] for r in json.loads(shaped.result_text)] == ["d", "a", "e"]
    assert shaped.result == {"raw": True} and shaped.projected is True


def test_shaper_passes_through_unconfigured_failed_or_unprojected():
    s = ResultShaper(CONFIG)
    for r in (_result(_rows(), tool="other"), _result(_rows(), success=False), _result(_rows(), projected=False)):
        assert s.shape(r) is r


def test_shaper_list_key_payload():
    cfg = {"connectors": {"read": [{"name": "fetch_jobs", "result_shaping": {**RULE, "list_key": "items"}}]},
           "preprocessing": {"language_normalisation": {"default_language": "hindi"}}}
    r = ToolResult(tool_use_id="t", tool_name="fetch_jobs", result={}, success=True,
                   result_text=json.dumps({"items": _rows(), "total": 6}), projected=True)
    out = json.loads(ResultShaper(cfg).shape(r).result_text)
    assert out["total"] == 6 and [x["item_id"] for x in out["items"]] == ["d", "a", "e"]


def test_shaper_never_raises_on_bad_json():
    r = ToolResult(tool_use_id="t", tool_name="fetch_jobs", result={}, success=True,
                   result_text="not json", projected=True)
    assert ResultShaper(CONFIG).shape(r) is r
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/output/test_result_shaping.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# agent_core/src/output/result_shaping.py
"""
agent_core/src/output/result_shaping.py
Per-connector result shaping at tool-result ingress (Spec D §4): drop
unusable rows, stable sort (nulls last), add ready-to-speak fields, strip
PIN/plot/sector numbers. Rewrites ``ToolResult.result_text`` only — the
cache, the in-turn tool_result and the NLU resolver all read that — and
never raises. Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Any

from src.models import ToolResult
from src.output.spoken_numbers import spoken_pay

logger = logging.getLogger(__name__)

_PLACE_TOKEN = re.compile(
    r"(?i)\b(?:plot|house|flat|gali|sector|sec|block|khasra)(?:\s*no\.?)?\s*[#:]?\s*[\w/-]*\d[\w/-]*"
    r"|\bno\.?\s*\d[\w/-]*")
_DIGIT_RUN = re.compile(r"(?<!\w)#?\d[\d/-]*(?!\w)")
_COMMAS = re.compile(r"\s*,(?:\s*,)*\s*")


def strip_place_numbers(text: str) -> str:
    """Remove PIN codes and plot/house/gali/sector numbers from a place string.

    Args:
        text: E.g. ``"Plot No. 12, Sector 5, Noida"``.

    Returns:
        E.g. ``"Noida"``.
    """
    out = _PLACE_TOKEN.sub("", text)
    out = _DIGIT_RUN.sub("", out)
    out = _COMMAS.sub(", ", out)
    return re.sub(r"\s{2,}", " ", out).strip(" ,")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def _matches(row: dict, cond: dict) -> bool:
    v, op, value = row.get(cond.get("field", "")), cond.get("operator"), cond.get("value")
    if op == "eq":
        return v == value
    if op == "not_eq":
        return v != value
    if op == "in":
        return v in (value if isinstance(value, list) else [value])
    if op == "contains":
        return isinstance(v, str) and isinstance(value, str) and value in v
    if op in ("lt", "gt"):
        a, b = _num(v), _num(value)
        return a is not None and b is not None and (a < b if op == "lt" else a > b)
    return False


def _sort_key(v: Any) -> tuple:
    n = _num(v)
    return (0, n, "") if n is not None else (1, 0.0, str(v))


def shape_rows(rows: list, rule: dict, language: str) -> list[dict]:
    """Shape result rows per one connector's ``result_shaping`` rule.

    Args:
        rows: Raw rows; non-dict entries are dropped.
        rule: Raw ``result_shaping`` dict.
        language: Language for ``spoken`` fields (``hindi`` | ``english``).

    Returns:
        New row dicts (inputs are not mutated): filtered, enriched, sorted.
    """
    drop = list(rule.get("drop_when") or [])
    kept = [dict(r) for r in rows if isinstance(r, dict) and not any(_matches(r, c) for c in drop)]
    for row in kept:
        for f in rule.get("strip_numbers_in") or []:
            if isinstance(row.get(f), str):
                row[f] = strip_place_numbers(row[f])
        for name, spec in (rule.get("spoken") or {}).items():
            text = spoken_pay(spec.get("format", ""), [row.get(f) for f in spec.get("from") or []],
                              spec.get("unit", "none"), language)
            if text:
                row[name] = text
            else:
                row.pop(name, None)
    for key in reversed(list(rule.get("sort") or [])):
        f = key.get("field", "")
        present = [r for r in kept if r.get(f) is not None and r.get(f) != ""]
        missing = [r for r in kept if r.get(f) is None or r.get(f) == ""]
        present.sort(key=lambda r: _sort_key(r.get(f)), reverse=key.get("order") == "desc")
        kept = present + missing
    return kept


class ResultShaper:
    """Applies each connector's ``result_shaping`` to its ToolResults.

    Args:
        config: Raw merged agent_core config dict.
    """

    def __init__(self, config: dict | None) -> None:
        cfg = config or {}
        conns = cfg.get("connectors") or {}
        self._rules: dict[str, dict] = {
            c["name"]: c["result_shaping"]
            for group in ("read", "write", "identity") for c in (conns.get(group) or [])
            if isinstance(c, dict) and c.get("name") and isinstance(c.get("result_shaping"), dict)
        }
        ln = (cfg.get("preprocessing") or {}).get("language_normalisation") or {}
        self._language = str(ln.get("default_language") or "english")

    def shape(self, result: ToolResult) -> ToolResult:
        """Return the result with shaped ``result_text``, or the same object when not applicable.

        Args:
            result: ToolResult from the Action Gateway (not a cache hit).

        Returns:
            Shaped ToolResult; the input unchanged on pass-through or error.
        """
        rule = self._rules.get(result.tool_name)
        if rule is None or not result.success or not result.projected or not result.result_text:
            return result
        try:
            payload = json.loads(result.result_text)
            list_key = rule.get("list_key") or ""
            if list_key:
                if not isinstance(payload, dict) or not isinstance(payload.get(list_key), list):
                    return result
                payload = {**payload, list_key: shape_rows(payload[list_key], rule, self._language)}
            else:
                if not isinstance(payload, list):
                    return result
                payload = shape_rows(payload, rule, self._language)
            return dataclasses.replace(result, result_text=json.dumps(payload, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("result_shaping.error", extra={"operation": "result_shaping.shape",
                                                          "status": "failure", "tool": result.tool_name,
                                                          "error": type(e).__name__})
            return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/output/test_result_shaping.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/output/result_shaping.py agent_core/tests/output/test_result_shaping.py
git commit -m "feat(agent_core): per-connector result shaping — drop, sort, spoken fields (Spec D §4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Runtime schema — output_contract, result_shaping, history_turns, state_fields; remove tts_rules

**Files:**
- Modify: `agent_core/src/schema/config.py`:
  - `AgentConfig` (L204-250);
  - `ToolCacheConfig` / `ConnectorDef` (L392-415);
  - `TtsRulesConfig` (L761-775, delete);
  - `ChannelConfig` (L834-856);
  - `MergedConfig` validators (after L1047).
- Modify: `agent_core/config/domain.yaml` (L51-75)
- Modify: `agent_core/tests/test_schema_config.py` (the L103 fixture, plus new tests)
- Modify: `agent_core/config/dpg.yaml`, only if it sets `tts_rules` (grep; at plan time it does not)

**Interfaces:**
- Produces these Pydantic models, all `frozen=True, extra="forbid"`:
  - `OutputLanguageContract(script: Literal["devanagari","latin","any"]="any", numbers: Literal["words","digits"]="digits", rules: list[str]=[])`;
  - `OutputGuardConfig(rewrite_digits: bool=False, strip_markdown: bool=False, count_foreign_script: bool=False)`;
  - `OutputContractConfig(default_language: str, languages: dict[str, OutputLanguageContract], guard: OutputGuardConfig)`;
  - `ShapingCondition(field: str, operator: Literal["eq","not_eq","in","lt","gt","contains"], value: Any=None)`;
  - `ShapingSort(field: str, order: Literal["asc","desc"]="asc")`;
  - `SpokenFieldConfig(format: Literal["range_thousands","amount"], from_: list[str] (alias "from", 1–2 items), unit: Literal["none","per_month","per_task","per_day"]="none")`;
  - `ResultShapingConfig(list_key: str="", drop_when: list[ShapingCondition]=[], sort: list[ShapingSort]=[], spoken: dict[str, SpokenFieldConfig]={}, strip_numbers_in: list[str]=[])`.
- New fields:
  - `ConnectorDef.result_shaping: Optional[ResultShapingConfig] = None`;
  - `ChannelConfig.output_contract: Optional[OutputContractConfig] = None` (replaces `tts_rules`);
  - `AgentConfig.history_turns: int = Field(default=2, ge=0)` and `AgentConfig.state_fields: list[str] = []`.
- New validator `MergedConfig._check_output_rules`.

- [ ] **Step 1: Write the failing tests** (append to `agent_core/tests/test_schema_config.py`, reusing its valid-config builder; the plan assumes it is `_valid_config()`, so match the actual helper name in the file)

```python
import copy

import pytest
from pydantic import ValidationError

from src.schema.config import MergedConfig

_CONTRACT = {
    "default_language": "hindi",
    "languages": {"hindi": {"script": "devanagari", "numbers": "words", "rules": ["Devanagari only."]},
                  "english": {"script": "latin", "numbers": "words"}},
    "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True},
}
_SHAPING = {
    "drop_when": [{"field": "role", "operator": "contains", "value": "|"}],
    "sort": [{"field": "match_score", "order": "desc"}],
    "spoken": {"salary_spoken": {"format": "range_thousands", "from": ["salary_min", "salary_max"], "unit": "per_month"}},
    "strip_numbers_in": ["location"],
}


def _with_language(cfg, default="hindi", supported=("english", "hindi")):
    cfg.setdefault("preprocessing", {})["language_normalisation"] = {
        "enabled": False, "default_language": default, "supported_languages": list(supported)}
    return cfg


def test_output_contract_and_shaping_accepted():
    cfg = _with_language(copy.deepcopy(_valid_config()))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": _CONTRACT}
    cfg["connectors"]["read"][0]["result_shaping"] = _SHAPING
    cfg.setdefault("agent", {}).update({"history_turns": 3, "state_fields": ["applications_submitted"]})
    MergedConfig.validate_full(cfg)


def test_tts_rules_rejected():
    cfg = copy.deepcopy(_valid_config())
    cfg.setdefault("channels", {})["voice"] = {"tts_rules": {"numbers": "words"}}
    with pytest.raises(ValidationError, match="tts_rules"):
        MergedConfig.validate_full(cfg)


def test_contract_default_language_must_be_declared():
    cfg = _with_language(copy.deepcopy(_valid_config()))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": {**_CONTRACT, "default_language": "tamil"}}
    with pytest.raises(ValidationError, match="default_language"):
        MergedConfig.validate_full(cfg)


def test_contract_must_cover_supported_languages():
    cfg = _with_language(copy.deepcopy(_valid_config()), supported=("english", "hindi", "kannada"))
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": _CONTRACT}
    with pytest.raises(ValidationError, match="kannada"):
        MergedConfig.validate_full(cfg)


def test_numbers_words_needs_a_converter():
    cfg = _with_language(copy.deepcopy(_valid_config()), default="kannada", supported=("kannada",))
    c = {"default_language": "kannada", "languages": {"kannada": {"script": "any", "numbers": "words"}}}
    cfg.setdefault("channels", {})["bridge"] = {"output_contract": c}
    with pytest.raises(ValidationError, match="spoken-number converter"):
        MergedConfig.validate_full(cfg)


def test_spoken_fields_need_a_supported_default_language():
    cfg = _with_language(copy.deepcopy(_valid_config()), default="kannada", supported=("kannada",))
    cfg["connectors"]["read"][0]["result_shaping"] = _SHAPING
    with pytest.raises(ValidationError, match="spoken"):
        MergedConfig.validate_full(cfg)


def test_history_turns_non_negative():
    cfg = copy.deepcopy(_valid_config())
    cfg.setdefault("agent", {})["history_turns"] = -1
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(cfg)
```

Also replace the L103 fixture value `"tts_rules": {"numbers": "words"}` with `"output_contract": {"default_language": "english", "languages": {"english": {"numbers": "words"}}}`. If that fixture has no `language_normalisation`, the coverage check is skipped (see Step 3).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -q`
Expected: the new tests FAIL. `output_contract` and `result_shaping` are rejected as extra fields, and `tts_rules` is still accepted.

- [ ] **Step 3: Implement**

In `config.py`, delete `class TtsRulesConfig`. Above `ChannelConfig`, add:

```python
class OutputLanguageContract(BaseModel):
    """Spoken-output rules for one language on a channel (Spec D §3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    script: Literal["devanagari", "latin", "any"] = "any"
    numbers: Literal["words", "digits"] = "digits"
    rules: list[str] = Field(default_factory=list)


class OutputGuardConfig(BaseModel):
    """Deterministic output guard switches (Spec D §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rewrite_digits: bool = False
    strip_markdown: bool = False
    count_foreign_script: bool = False


class OutputContractConfig(BaseModel):
    """What this channel's model output must look like; rendered into the prompt and enforced by the guard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_language: str = Field(min_length=1)
    languages: dict[str, OutputLanguageContract] = Field(min_length=1)
    guard: OutputGuardConfig = Field(default_factory=OutputGuardConfig)

    @model_validator(mode="after")
    def _default_declared(self) -> "OutputContractConfig":
        if self.default_language not in self.languages:
            raise ValueError(f"output_contract.default_language '{self.default_language}' "
                             f"is not in languages {sorted(self.languages)}")
        return self
```

In `ChannelConfig`, replace `tts_rules: Optional[TtsRulesConfig] = None` with `output_contract: Optional[OutputContractConfig] = None`.

Above `ConnectorDef`, add:

```python
class ShapingCondition(BaseModel):
    """A row is dropped when any drop_when condition matches (Spec D §4.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    operator: Literal["eq", "not_eq", "in", "lt", "gt", "contains"]
    value: Any = None


class ShapingSort(BaseModel):
    """One stable sort key; nulls always last."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    order: Literal["asc", "desc"] = "asc"


class SpokenFieldConfig(BaseModel):
    """A derived ready-to-speak field rendered in the default language."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    format: Literal["range_thousands", "amount"]
    from_: list[str] = Field(alias="from", min_length=1, max_length=2)
    unit: Literal["none", "per_month", "per_task", "per_day"] = "none"


class ResultShapingConfig(BaseModel):
    """Per-connector shaping applied once at tool-result ingress (Spec D §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    list_key: str = ""
    drop_when: list[ShapingCondition] = Field(default_factory=list)
    sort: list[ShapingSort] = Field(default_factory=list)
    spoken: dict[str, SpokenFieldConfig] = Field(default_factory=dict)
    strip_numbers_in: list[str] = Field(default_factory=list)
```

In `ConnectorDef`, after `cache`, add `result_shaping: Optional[ResultShapingConfig] = None`. Check that `Any` is imported from `typing`; add it if not.

In `AgentConfig`, after `prompt_session_fields`, add the following, and update the `prompt_session_fields` docstring/comment "rendered into <known_profile>" to "rendered into <state> collected":

```python
    # Spec D §6.2: how many past exchanges the main LLM sees in <recent>; 0 omits it.
    history_turns: int = Field(default=2, ge=0)
    # Spec D §6.3: session keys shown as-is on the <state> "status" line.
    state_fields: list[str] = Field(default_factory=list)
```

Add a new validator to `MergedConfig`, after `_check_dialogue_act_rules`:

```python
    @model_validator(mode="after")
    def _check_output_rules(self) -> "MergedConfig":
        """Output contracts and spoken fields need declared languages with a number converter (Spec D §3, §4)."""
        converters = {"hindi", "english"}
        ln = getattr(getattr(self, "preprocessing", None), "language_normalisation", None)
        supported = list(getattr(ln, "supported_languages", None) or [])
        default_lang = str(getattr(ln, "default_language", "") or "")
        channels = getattr(self, "channels", None)
        for name in ("voice", "web", "cli", "mcp", "bridge"):
            ch = getattr(channels, name, None) if channels is not None else None
            contract = getattr(ch, "output_contract", None)
            if contract is None:
                continue
            missing = [lang for lang in supported if lang not in contract.languages]
            if missing:
                raise ValueError(f"channels.{name}.output_contract lacks languages {missing} "
                                 f"listed in language_normalisation.supported_languages")
            for lang, entry in contract.languages.items():
                if entry.numbers == "words" and lang not in converters:
                    raise ValueError(f"channels.{name}.output_contract.languages.{lang}: numbers=words "
                                     f"needs a spoken-number converter (have {sorted(converters)})")
        conns = getattr(self, "connectors", None)
        for group in ("read", "write", "identity"):
            for c in (getattr(conns, group, None) or []):
                rs = getattr(c, "result_shaping", None)
                if rs is not None and rs.spoken and default_lang not in converters:
                    raise ValueError(f"connectors.{group}[{c.name}].result_shaping.spoken renders in "
                                     f"language_normalisation.default_language '{default_lang}', which has "
                                     f"no spoken-number converter (have {sorted(converters)})")
        return self
```

The attribute names `preprocessing`, `language_normalisation`, `channels` and `connectors` must match `MergedConfig`'s real field names. Confirm them in the file; the code map lists `LanguageNormalisationConfig` at L470 and `ChannelsConfig` at L857. If `preprocessing` is optional and absent, `supported` is `[]`, so the coverage check is skipped.

In `agent_core/config/domain.yaml`, replace the `channels.voice.tts_rules:` mapping (L51-59) with:

```yaml
    output_contract:          # Optional. Spoken-output contract rendered into the prompt and enforced by the guard (Spec D).
      default_language: english
      languages:
        english:
          script: latin
          numbers: words
          rules:
            - "Spell out dates, times and amounts the way they are spoken."
      guard:
        rewrite_digits: true
        strip_markdown: true
        count_foreign_script: false
```

Then delete the `tts_rules: null` lines under `web` (L68) and `cli` (L75). Keep the surrounding comments accurate.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/test_schema_config.py -q && uv run pytest -q`
Expected: the schema tests pass. The full suite holds at baseline (1359 passed, plus the new tests). If `tests/test_blue_dots_dialogue_act_config.py` now fails because Blue Dots still has `tts_rules`, mark it `pytest.mark.xfail(reason="Blue Dots migrates in Task 9", strict=True)` in this task; Task 9 removes the marker.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/schema/config.py agent_core/config/domain.yaml agent_core/tests
git commit -m "feat(agent_core): schema for output_contract, result_shaping, history_turns, state_fields; drop tts_rules (Spec D)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Context renderers — `<state>`, `<recent>`, and a public `offered_entry`

**Files:**
- Create: `agent_core/src/context/__init__.py`, `agent_core/src/context/state.py`
- Modify:
  - `agent_core/src/understanding/frame.py`: add `offered_entry`;
  - `agent_core/src/understanding/understander.py` (L68-75): `_offered_entry` delegates to it;
  - `agent_core/src/manager_agent.py` (L41-56): move `_is_collected` to `context/state.py` as `is_collected`, and import it back into manager_agent.
- Test: `agent_core/tests/context/__init__.py`, `agent_core/tests/context/test_state.py`

**Interfaces:**
- Consumes:
  - `src.understanding.frame.offered_rows(entry) -> list[dict]` and `option_label(row, fields) -> str`;
  - `src.workflow_loader.PendingQuestion` (`id`, `expects`, `when`, `options_from: OptionsFrom | None`, `resolves_to`).
- Produces:
  - `frame.offered_entry(served: dict | None, tool_cache, tool: str) -> dict | None`;
  - `context.state.is_collected(value: object) -> bool`;
  - `context.state.render_state(*, phase: str, pending, collected: dict, offered: list[dict], status: dict) -> str`;
  - `context.state.render_recent(recent_turns: object, n: int) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/context/test_state.py
from types import SimpleNamespace

from src.context.state import is_collected, render_recent, render_state
from src.understanding.frame import offered_entry

PENDING = SimpleNamespace(id="select_job", expects="offered jobs में से एक",
                          options_from=SimpleNamespace(tool="fetch_jobs", fields=("role", "company"), id_field="item_id"))
ROWS = [{"item_id": "j1", "role": "Welder", "company": "Flipkart"},
        {"item_id": "j2", "role": "Welder", "company": "Titan"}]


def test_render_state_all_lines():
    text = render_state(phase="job_match", pending=PENDING,
                        collected={"name": "अजय सिंह", "age": 28, "user_id": "u1", "trade": "",
                                   "attributes": [{"key": "city", "value": "Bengaluru"}]},
                        offered=ROWS, status={"applications_submitted": 0})
    assert text.splitlines() == [
        "phase: job_match",
        "waiting for: select_job — offered jobs में से एक",
        "collected (do not ask again): name=अजय सिंह · age=28 · city=Bengaluru",
        "offered (read in this order): 1. Welder · Flipkart; 2. Welder · Titan",
        "status: applications_submitted=0",
    ]


def test_render_state_omits_empty_lines():
    assert render_state(phase="opening", pending=None, collected={"age": 0}, offered=[], status={}) == \
        "phase: opening"


def test_is_collected_treats_zero_and_empty_as_missing():
    assert not any(is_collected(v) for v in (None, "", [], "[]", 0))
    assert is_collected("x") and is_collected(28)


def test_recent_renders_last_n():
    turns = [{"caller": "a", "bot": "b", "interrupted": False}, {"caller": "c", "bot": "d", "interrupted": False},
             {"caller": "e", "bot": "f", "interrupted": False}]
    assert render_recent(turns, 2) == "caller: c\nbot: d\ncaller: e\nbot: f"


def test_recent_marks_interrupted_turn():
    turns = [{"caller": "हाँ", "bot": "आपके लिए जॉब्स हैं — पहला:", "interrupted": True}]
    assert render_recent(turns, 2) == "caller: हाँ\nbot (caller heard only): आपके लिए जॉब्स हैं — पहला:"


def test_recent_zero_or_garbage_is_empty():
    assert render_recent([{"caller": "a", "bot": "b"}], 0) == ""
    assert render_recent("nope", 2) == ""
    assert render_recent([None, 3], 2) == ""


class _Cache:
    def __init__(self, entries):
        self._e = entries

    def entry(self, tool, h):
        return self._e.get((tool, h))

    def latest_entry(self, tool):
        return self._e.get((tool, "latest"))


def test_offered_entry_prefers_served_then_latest():
    cache = _Cache({("fetch_jobs", "h1"): {"data": "served"}, ("fetch_jobs", "latest"): {"data": "new"}})
    assert offered_entry({"fetch_jobs": "h1"}, cache, "fetch_jobs") == {"data": "served"}
    assert offered_entry({"fetch_jobs": "gone"}, cache, "fetch_jobs") == {"data": "new"}
    assert offered_entry(None, cache, "fetch_jobs") == {"data": "new"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/context -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.context'`.

- [ ] **Step 3: Implement**

```python
# agent_core/src/context/__init__.py
"""Main-LLM context renderers (Spec D §6)."""
```

```python
# agent_core/src/context/state.py
"""
agent_core/src/context/state.py
Renders the main LLM's <state> and <recent> blocks from session data
(Spec D §6.2–6.3). Pure; built in code, never by a model. Belongs to the
Agent Core DPG block.
"""
from __future__ import annotations

from typing import Any

from src.understanding.frame import option_label

_SKIP_KEYS = {"attributes", "user_id"}


def is_collected(value: object) -> bool:
    """Whether a profile value counts as something the caller has told us.

    ``0`` is not collected: ``age`` is the one integer field and defaults to
    0 — treating it as collected stopped the agent asking for age and sent
    age=0 upstream.

    Args:
        value: A profile field value.

    Returns:
        True when the value is present and meaningful.
    """
    if value is None or value == 0 or isinstance(value, bool):
        return bool(value) if isinstance(value, bool) else False
    if isinstance(value, (list, dict, str)):
        return bool(value) and value != "[]"
    return True


def _collected_line(collected: dict) -> str:
    items = [f"{k}={v}" for k, v in collected.items() if k not in _SKIP_KEYS and is_collected(v)]
    for attr in collected.get("attributes") or []:
        if isinstance(attr, dict) and attr.get("key") and attr.get("value"):
            items.append(f"{attr['key']}={attr['value']}")
    return " · ".join(items)


def render_state(*, phase: str, pending: Any, collected: dict, offered: list[dict], status: dict) -> str:
    """Render <state>: phase, open question, collected values, offered options in read order, status.

    Args:
        phase: Subagent id the prompt is built for.
        pending: Resolved PendingQuestion for that subagent, or None.
        collected: Profile context dict (``_build_profile_context`` output).
        offered: Rows on offer, in stored (= resolver) order; [] when none.
        status: ``agent.state_fields`` key → session value.

    Returns:
        Block text; lines with nothing to say are omitted.
    """
    lines = [f"phase: {phase}"] if phase else []
    if pending is not None:
        expects = f" — {pending.expects}" if getattr(pending, "expects", "") else ""
        lines.append(f"waiting for: {pending.id}{expects}")
    c = _collected_line(collected or {})
    if c:
        lines.append(f"collected (do not ask again): {c}")
    of = getattr(pending, "options_from", None) if pending is not None else None
    if of is not None and offered:
        labels = [f"{i}. {option_label(r, tuple(of.fields))}" for i, r in enumerate(offered, start=1)]
        lines.append("offered (read in this order): " + "; ".join(labels))
    s = " · ".join(f"{k}={v}" for k, v in (status or {}).items() if v is not None)
    if s:
        lines.append(f"status: {s}")
    return "\n".join(lines)


def render_recent(recent_turns: object, n: int) -> str:
    """Render <recent>: the last ``n`` exchanges verbatim; interrupted replies marked.

    Args:
        recent_turns: Session ``recent_turns`` list of {caller, bot, interrupted}.
        n: Exchanges to show; 0 renders nothing.

    Returns:
        Block text, or "".
    """
    if n <= 0 or not isinstance(recent_turns, list):
        return ""
    lines: list[str] = []
    for e in [e for e in recent_turns if isinstance(e, dict)][-n:]:
        if e.get("caller"):
            lines.append(f"caller: {e['caller']}")
        if e.get("bot"):
            label = "bot (caller heard only)" if e.get("interrupted") else "bot"
            lines.append(f"{label}: {e['bot']}")
    return "\n".join(lines)
```

Check `manager_agent._is_collected`'s exact behaviour (L41-~60) before replacing it. `is_collected` must be behaviour-identical, so copy its body verbatim if it differs from the above, and keep the `0`-is-missing rule. In `manager_agent.py`, delete `_is_collected` and add `from src.context.state import is_collected as _is_collected` near the imports. Task 6 removes the last use; until then this keeps L737-753 working.

In `agent_core/src/understanding/frame.py`, add:

```python
def offered_entry(served: dict | None, tool_cache: Any, tool: str) -> dict | None:
    """The entry on offer: the last-served one when still fresh, else the newest (Spec C §5.2).

    Args:
        served: Session ``served_tool_results`` (tool → args hash), or None.
        tool_cache: TurnToolCache (``entry``, ``latest_entry``).
        tool: Tool name.

    Returns:
        Cache entry dict, or None.
    """
    h = served.get(tool) if isinstance(served, dict) else None
    if isinstance(h, str) and h:
        e = tool_cache.entry(tool, h)
        if e is not None:
            return e
    return tool_cache.latest_entry(tool)
```

Make sure `Any` is imported in `frame.py`. In `understander.py`, replace the body of `_offered_entry(ctx, tool)` with `return offered_entry(ctx.served, ctx.tool_cache, tool)`, importing `offered_entry` from `src.understanding.frame`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest tests/context tests/understanding -q && uv run pytest -q`
Expected: all pass, and the full suite stays at baseline.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src/context agent_core/src/understanding/frame.py agent_core/src/understanding/understander.py agent_core/src/manager_agent.py agent_core/tests/context
git commit -m "feat(agent_core): <state>/<recent> renderers and public offered_entry (Spec D §6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Prompt assembly in ManagerAgent, plus result shaping wired on every ingress

**Files:**
- Modify: `agent_core/src/manager_agent.py`:
  - `build_system_prompt` L622-792;
  - `build_messages` L794-823;
  - `run_turn` L305-596 (add the `result_shaper` param, applied after `_execute_tool` at L524).
- Modify: `agent_core/src/session_bootstrap.py` (`__init__` L78, `from_config` L84, `_apply` ~L98-110)
- Modify: `agent_core/src/orchestrator.py`:
  - `__init__`: construct `self._result_shaper`;
  - stream gateway sites at ~L4609 and ~L4935;
  - the sync `run_turn` call;
  - the `SessionBootstrap.from_config` call.
- Test: `agent_core/tests/test_manager_agent.py` (update L398-413, L529-532, L559-628, L746-845), `agent_core/tests/test_session_bootstrap.py`, `agent_core/tests/test_stream_turn.py`

**Interfaces:**
- Consumes: Task 2 `render_output_contract`; Task 3 `ResultShaper`.
- Produces:
  - `build_system_prompt(self, agent_system_prompt: str, subagent_system_prompt: str, detected_language: str, channel: str, channel_config: dict | None = None, is_resumption: bool = False, user_state_guidance: str | None = None, session_end_eval_prompt: str | None = None, known_facts: str = "", caller_turn: str = "", state: str = "", recent: str = "") -> SystemPrompt`. The `profile` and `guardrail_constraints` parameters are removed.
  - `build_messages(self, user_message: str) -> list[Message]`. `current_question` is removed.
  - `run_turn(..., result_shaper: Callable[[ToolResult], ToolResult] | None = None)`.
  - `SessionBootstrap(steps, timeout_ms, policies, shape: Callable[[ToolResult], ToolResult] | None = None)` and `SessionBootstrap.from_config(config, policies, shape=None)`.
  - The module constant `HOW_TO_READ_CONTEXT: str` in manager_agent.

- [ ] **Step 1: Write the failing tests** (in `tests/test_manager_agent.py`; use the existing `_make_manager_for_prompt()` and `_flat(prompt)` helpers)

```python
from src.manager_agent import HOW_TO_READ_CONTEXT

_CONTRACT = {"default_language": "hindi",
             "languages": {"hindi": {"script": "devanagari", "numbers": "words", "rules": ["Devanagari only."]}},
             "guard": {"rewrite_digits": True}}


def _prompt(**kw):
    m = _make_manager_for_prompt()
    base = dict(agent_system_prompt="P", subagent_system_prompt="S", detected_language="", channel="bridge",
                channel_config={"system_prompt_suffix": "SUFFIX", "output_contract": _CONTRACT})
    base.update(kw)
    return m.build_system_prompt(**base)


def test_tier1_order_includes_contract_and_how_to_read():
    p = _prompt(session_end_eval_prompt="END")
    t1 = p.blocks[0].text
    order = [t1.index(f"<{t}>") for t in ("persona", "channel_rules", "output_contract",
                                           "how_to_read_context", "session_end_policy")]
    assert order == sorted(order)
    assert "- Write in Devanagari script." in t1 and HOW_TO_READ_CONTEXT.strip() in t1


def test_tier3_order_state_recent_facts_caller_turn():
    p = _prompt(state="phase: job_match", recent="caller: a", known_facts="F", caller_turn="acts: select")
    t3 = p.blocks[-1].text
    order = [t3.index(f"<{t}>") for t in ("channel_context", "state", "recent", "known_facts", "caller_turn")]
    assert order == sorted(order)
    assert "<known_profile>" not in t3 and "<active_guardrails>" not in t3


def test_empty_state_and_recent_are_elided():
    t3 = _prompt().blocks[-1].text
    assert "<state>" not in t3 and "<recent>" not in t3


def test_no_contract_no_block():
    p = _prompt(channel_config={"system_prompt_suffix": "SUFFIX"})
    assert "<output_contract>" not in p.blocks[0].text


def test_build_messages_is_utterance_only():
    m = _make_manager_for_prompt()
    msgs = m.build_messages("नमस्ते")
    assert len(msgs) == 1 and msgs[0].content[0].text == "नमस्ते"
    assert m.build_messages("")[0].content[0].text == "[Resuming session...]"
```

Delete or rewrite these existing tests, which assert removed behaviour:
- `test_build_messages_current_question_prepended` (L559) and `..._no_prefix` (L566);
- the `guardrail_constraints` / `<active_guardrails>` tests (L398-413, L529-532, L576-628);
- the tier-3 tuple assertions naming `known_profile` / `active_guardrails` (L746-845). Update those tuples to `("channel_context", "resumption", "state", "recent", "known_facts", "caller_turn")`.

Add a run_turn shaping test, using the file's `_make_manager(llm_responses, tool_result=...)`, `_tool_response`, `_text_response` and `_tool_call` helpers:

```python
def test_run_turn_applies_result_shaper_to_gateway_results():
    seen = []

    def shaper(tr):
        seen.append(tr.tool_name)
        return dataclasses.replace(tr, result_text='[{"shaped": true}]')

    m = _make_manager([_tool_response(_tool_call("fetch_jobs")), _text_response("ok")])
    _, _, results = m.run_turn(messages=[], session_id="s", initial_response=m._llm.call.return_value,
                               result_shaper=shaper)
    assert seen == ["fetch_jobs"] and results[0].result_text == '[{"shaped": true}]'
```

Adapt the `initial_response` and argument plumbing to how the existing `run_turn` tests in this file call it; mirror the nearest existing tool-round test. The assertion that matters is that the shaper runs on gateway results and its output is what gets returned and cached. Add `import dataclasses` at the top.

In `tests/test_session_bootstrap.py`, add `test_shape_applied_before_cache` modelled on the existing `_apply`/`run_sync` tests. It builds `SessionBootstrap(steps, 1500, policies, shape=lambda r: dataclasses.replace(r, result_text="[]"))`, runs one step, and asserts that the cache entry's `data == []`.

In `tests/test_stream_turn.py`, add `test_cache_hit_is_not_reshaped` and `test_gateway_result_is_shaped_once`:
- **Setup:** use `_make_agent_core`. Set `agent._result_shaper = MagicMock(side_effect=lambda r: r)`. Make the LLM request `fetch_jobs` twice in one turn: the first is a gateway call, and the second is served by `tool_cache.lookup` (the same args). Follow the existing two-call cache test pattern in this file or in `test_turn_path_identity_parity.py` (`_jobs_entry()` L206).
- **Assert:** `agent._result_shaper.call_count == 1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_manager_agent.py tests/test_session_bootstrap.py tests/test_stream_turn.py -q`
Expected: the new tests FAIL (no `HOW_TO_READ_CONTEXT`, unexpected kwargs `state`/`recent`/`result_shaper`/`shape`).

- [ ] **Step 3: Implement**

In `manager_agent.py`:
- Add `from src.output.contract import render_output_contract`.
- Add this module constant:

```python
HOW_TO_READ_CONTEXT = """\
- <caller_turn> is the system's reading of what the caller just did. It is
  already applied: listed updates are saved, and "resolved: option N" is the
  option the caller picked. Act on it; do not ask the caller to confirm what it
  shows, and do not re-ask a value it lists.
- "open: <question>" means that question is still waiting: answer what the
  caller asked, then return to it in the same reply.
- "off_track" means the caller has drifted several times: briefly restate what
  you need and why.
- "understanding unavailable" means rely on the caller's words and <recent>.
- If the caller's words clearly contradict <caller_turn>, act on neither: ask
  one short question to settle it.
- <recent> is the last exchanges. "(caller heard only)" marks a reply they did
  not hear in full: do not repeat what they heard; finish what they did not.
- <state> is where the call stands. Never ask for a value under "collected".
  Read offered options in the order listed and never re-rank them."""
```

Change `build_system_prompt`:
- **Signature:** use the one under Interfaces. Remove `profile` and `guardrail_constraints`; add `state: str = ""` and `recent: str = ""`. Update the docstring's section list: Tier 1 gains `<output_contract>` and `<how_to_read_context>`; Tier 3 is `<channel_context>`, `<resumption>`, `<state>`, `<recent>`, `<known_facts>`, `<caller_turn>`.
- **Tier 1:**

```python
        suffix = (channel_config or {}).get("system_prompt_suffix", "")
        contract_text = render_output_contract((channel_config or {}).get("output_contract"))
        tier1 = join([
            xml("persona", agent_system_prompt),
            xml("channel_rules", suffix),
            xml("output_contract", contract_text),
            xml("how_to_read_context", HOW_TO_READ_CONTEXT),
            xml("session_end_policy", session_end_eval_prompt),
        ])
```

- **Tier 3:** delete the `profile_body` block (L737-753) and the `guardrails_body` block (L755-768), then:

```python
        tier3 = join([
            xml("channel_context", channel_ctx),
            xml("resumption", resumption_note),
            xml("state", state),
            xml("recent", recent),
            xml("known_facts", known_facts),
            xml("caller_turn", caller_turn),
        ])
```

- Remove the now-unused `_is_collected` import if nothing else uses it.

Change `build_messages`:

```python
    def build_messages(self, user_message: str) -> list[Message]:
        """Build the per-turn user message: the caller's utterance only (Spec D §6.1).

        Question context reaches the model through <recent> and <state>.

        Args:
            user_message: The caller's utterance (raw, possibly carryover-folded).

        Returns:
            A single user Message.
        """
        input_text = user_message.strip() if user_message else "[Resuming session...]"
        return [Message(role="user", content=[TextBlock(text=input_text)])]
```

In `run_turn`:
- Add the parameter `result_shaper: Callable[[ToolResult], ToolResult] | None = None`, and import `Callable` from `typing` / `collections.abc` per the file's style.
- Immediately after the live gateway call at L524 (`tool_result = self._execute_tool(_call, session_id, user_id)`), add:

```python
                        if result_shaper is not None:
                            tool_result = result_shaper(tool_result)
```

- This must sit *before* `tool_cache.after_call(...)` (L526) and the `all_tool_results` append (L528). The cache-hit branch (`tool_cache.lookup`, L520) is not shaped.

In `session_bootstrap.py`:
- `__init__` gains `shape: Callable[[ToolResult], ToolResult] | None = None` and stores it as `self._shape`.
- `from_config(cls, config, policies, shape=None)` passes it through.
- The first line of `_apply` becomes `result = self._shape(result) if self._shape is not None else result`.

In `orchestrator.py`:
- `__init__`: after `self._config` is set, add `self._result_shaper = ResultShaper(self._config)` (import from `src.output.result_shaping`).
- Pass `shape=self._result_shaper.shape` to the `SessionBootstrap.from_config(...)` call (grep for it).
- **Stream:** at both gateway sites, round 1 (~L4609-4611) and nested (~L4935), insert immediately after `tool_result = await self._async_gateway.execute(...)`:

```python
                    tool_result = self._result_shaper.shape(tool_result)
```

  This is before `_write_mapped_session_values`, which reads `session_values` and is unaffected, and before `tool_cache.after_call`.
- **Sync:** pass `result_shaper=self._result_shaper.shape` to the `self._manager_agent.run_turn(...)` call.
- Update every `build_system_prompt(...)` call (sync L1250-1264, stream L4303-4317):
  - drop `profile=profile_context`;
  - add `state=""` and `recent=""` for now (Task 7 fills them).
- Update every `build_messages(...)` call (L1270-1273, L4323-4326) to `build_messages(user_message=turn_input.user_message)`.
- Keep `profile_context`. It is still used for `language_preference`, and Task 7 uses it for `<state>`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest -q`
Expected: all pass. Fix any remaining test that passed `profile=` / `guardrail_constraints=` / `current_question=`, by removing the argument; don't loosen assertions.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src agent_core/tests
git commit -m "feat(agent_core): prompt assembly with output contract and context guide; shape tool results on every ingress (Spec D §3, §4, §6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Orchestrator wiring — `<state>`, `<recent>`, retention and the output guard on both paths

**Files:**
- Modify: `agent_core/src/orchestrator.py`:
  - `__init__` (near L341);
  - a new `_render_state` helper;
  - the sync prompt site (~L1241-1273) and sync output (~L1397-1519);
  - the three `append_recent_turn` calls (L1545, L3370, L5074);
  - the stream prompt site (~L4298-4326);
  - the three stream `_trust_batcher.add` sites (~L4419, ~L4795, ~L4999);
  - the `stream_turn_complete` extras (~L5128-5160).
- Test:
  - `agent_core/tests/test_stream_turn.py`;
  - `agent_core/tests/test_orchestrator.py`;
  - `agent_core/tests/test_turn_path_identity_parity.py`;
  - a new `agent_core/tests/test_output_guard_wiring.py`.

**Interfaces:**
- Consumes:
  - Task 2 `OutputGuard`, `contract_language`;
  - Task 5 `render_state`, `render_recent`, `offered_entry`;
  - `src.understanding.pending.PendingResolver(workflow).resolve(subagent_id, state)`;
  - `src.understanding.frame.offered_rows`;
  - `RECENT_TURNS_KEY`;
  - `SERVED_TOOL_RESULTS_KEY`.
- Produces:
  - `AgentCore._render_state(self, bundle, subagent_id: str, tool_cache, profile_context: dict) -> str`;
  - `AgentCore._agent_history_turns: int`;
  - `AgentCore._recent_keep: int`;
  - `AgentCore._state_fields: list[str]`.
  - Per-turn guard counters in the stream extras: `digits_rewritten`, `foreign_script_words`.

- [ ] **Step 1: Write the failing tests**

```python
# agent_core/tests/test_output_guard_wiring.py
"""Spec D §5: the guard runs on model text before Trust, on all three stream sites and the sync path."""
import pytest

from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input
from src.models import SentenceEvent  # adjust import to where SentenceEvent lives (grep "class SentenceEvent")

_CONTRACT = {"default_language": "hindi",
             "languages": {"hindi": {"script": "devanagari", "numbers": "words"},
                           "english": {"script": "latin", "numbers": "words"}},
             "guard": {"rewrite_digits": True, "strip_markdown": True, "count_foreign_script": True}}


def _agent_with_contract(tokens):
    agent = _make_agent_core()
    agent._config.setdefault("channels", {})["cli"] = {"output_contract": _CONTRACT}
    checked = []

    async def check_output(session_id, text):
        checked.append(text)
        return {"allowed": True, "text": text}   # mirror the existing trust-output mock shape in test_stream_turn.py

    agent._async_trust.check_output.side_effect = check_output

    async def stream(*a, **k):
        for t in tokens:
            yield t

    agent._llm.stream = stream
    return agent, checked


@pytest.mark.asyncio
async def test_guard_rewrites_before_trust_and_in_the_tail():
    agent, checked = _agent_with_contract(["सैलरी 27620 है। ", "**कुल** 5"])   # tail has no terminator
    events = await _collect_events(agent, _make_turn_input(channel="cli"))
    spoken = " ".join(e.text for e in events if isinstance(e, SentenceEvent))
    assert "27620" not in spoken and "5" not in spoken and "**" not in spoken
    assert "सत्ताईस हज़ार छह सौ बीस" in spoken and "पाँच" in spoken
    assert all(not any(ch.isdigit() for ch in t) for t in checked)


@pytest.mark.asyncio
async def test_guard_language_follows_language_preference():
    agent, _ = _agent_with_contract(["It pays 25000. "])
    agent._async_memory.context_bundle.return_value.session["language_preference"] = "english"
    events = await _collect_events(agent, _make_turn_input(channel="cli"))
    assert "twenty-five thousand" in " ".join(e.text for e in events if isinstance(e, SentenceEvent))


@pytest.mark.asyncio
async def test_no_contract_leaves_text_alone():
    agent = _make_agent_core()

    async def stream(*a, **k):
        yield "सैलरी 27620 है। "

    agent._llm.stream = stream
    events = await _collect_events(agent, _make_turn_input(channel="cli"))
    assert "27620" in " ".join(e.text for e in events if isinstance(e, SentenceEvent))
```

Adapt the channel name, the trust mock return shape, the `context_bundle` mock access and the `SentenceEvent` import to `test_stream_turn.py`'s actual fixtures. Read `_make_agent_core` (L137) and an existing trust-batching test in `test_trust_output_batching.py` first. The assertions are what's binding.

Add a sync test in `tests/test_orchestrator.py` that runs `process_turn` with the same contract on the turn's channel and an LLM text reply of "सैलरी 27620 है।". It asserts that the returned reply, and the text passed to `self._trust.check_output`, contain "सत्ताईस हज़ार छह सौ बीस" and no digits.

Add state/recent tests in `tests/test_stream_turn.py`. They capture `agent._manager_agent.build_system_prompt.call_args.kwargs`, which is a MagicMock in `_make_agent_core`:

```python
@pytest.mark.asyncio
async def test_prompt_gets_state_and_recent():
    agent = _make_agent_core()
    sess = agent._async_memory.context_bundle.return_value.session
    sess["recent_turns"] = [{"caller": "हाँ", "bot": "आपकी उम्र?", "interrupted": False}]
    sess["applications_submitted"] = 0
    agent._config.setdefault("agent", {}).update({"history_turns": 2, "state_fields": ["applications_submitted"]})
    agent._state_fields = ["applications_submitted"]
    agent._agent_history_turns = 2
    await _collect_events(agent, _make_turn_input())
    kw = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert kw["recent"] == "caller: हाँ\nbot: आपकी उम्र?"
    assert kw["state"].startswith("phase: ") and "status: applications_submitted=0" in kw["state"]


def test_recent_retention_is_max_of_nlu_and_agent():
    agent = _make_agent_core()
    agent._config.setdefault("agent", {})["history_turns"] = 4
    # rebuild the derived value the way __init__ does
    assert max(agent._dialogue_cfg.history_turns, 4) == 4
```

The retention test above only shows the rule. Replace it with a real assertion: construct `AgentCore` with `agent.history_turns: 4` through the same factory the file uses for config-driven construction, if one exists, and assert `agent._recent_keep == 4`. If no factory takes config, assert it through the stream test, where three prior turns plus one complete turn leave four `recent_turns` entries in the persisted session.

In `tests/test_turn_path_identity_parity.py`, add `test_system_prompt_and_messages_parity` using `_sync_run` / `_stream_run`. It runs the same session (with `recent_turns`, a pending question and a cached `fetch_jobs` entry), uses a real `ManagerAgent` for prompt assembly (`_make_manager`), and asserts that the `system` and `messages` sent to the LLM are identical on both paths.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent_core && uv run pytest tests/test_output_guard_wiring.py tests/test_stream_turn.py tests/test_orchestrator.py tests/test_turn_path_identity_parity.py -q`
Expected: the new tests FAIL. Digits reach Trust, and `state` / `recent` are "".

- [ ] **Step 3: Implement**

In `orchestrator.py`, imports:

```python
from src.context.state import render_recent, render_state
from src.output.contract import contract_language
from src.output.guard import OutputGuard
from src.understanding.frame import offered_entry, offered_rows
from src.understanding.pending import PendingResolver
```

In `__init__`, after `self._dialogue_cfg = DialogueActConfig.from_config(self._config)` (L341):

```python
        agent_cfg = self._config.get("agent") or {}
        self._agent_history_turns = int(agent_cfg.get("history_turns", 2))
        self._state_fields = list(agent_cfg.get("state_fields") or [])
        # recent_turns serves both the NLU frame and <recent>; keep enough for either.
        self._recent_keep = max(self._dialogue_cfg.history_turns, self._agent_history_turns)
        self._pending_resolver = PendingResolver(self._workflow)
```

Change the three `append_recent_turn(...)` calls (L1545, L3370, L5074) from `history_turns=self._dialogue_cfg.history_turns` to `history_turns=self._recent_keep`.

Add the helper:

```python
    def _render_state(self, bundle, subagent_id: str, tool_cache, profile_context: dict) -> str:
        """<state> for the subagent the prompt is built for (Spec D §6.3). Never raises."""
        try:
            pending = self._pending_resolver.resolve(subagent_id, self._routing_state(bundle))
            offered: list[dict] = []
            of = getattr(pending, "options_from", None) if pending is not None else None
            if of is not None:
                served = bundle.session.get(SERVED_TOOL_RESULTS_KEY)
                offered = offered_rows(offered_entry(served, tool_cache, of.tool))
            status = {k: bundle.session.get(k) for k in self._state_fields}
            return render_state(phase=subagent_id, pending=pending, collected=profile_context,
                                offered=offered, status=status)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("orchestrator.state_render_failed",
                           extra={"operation": "orchestrator.render_state", "status": "failure",
                                  "error": type(e).__name__})
            return ""
```

At both prompt sites, after `profile_context = self._build_profile_context(...)`, compute:

```python
        state_text = self._render_state(bundle, next_subagent_id, tool_cache, profile_context)
        recent_text = render_recent(bundle.session.get(RECENT_TURNS_KEY), self._agent_history_turns)
```

Use whichever local names the site has for the post-routing subagent id and the cache. The sync path uses `next_subagent.id` (or `next_subagent_id`), and the stream path uses its equivalent. Pass `state=state_text, recent=recent_text` to `build_system_prompt` in place of Task 6's `""`.

Guard setup at both prompt sites (the channel and the language are known there):

```python
        _contract = (channel_config or {}).get("output_contract")
        _guard = OutputGuard(_contract)
        _guard_lang = contract_language(_contract, profile_context.get("language_preference")
                                        or bundle.session.get("language_preference"))
        _guard_counts = {"digits_rewritten": 0, "foreign_script_words": 0}

        def _guarded(text: str) -> str:
            r = _guard.apply(text, _guard_lang)
            _guard_counts["digits_rewritten"] += r.digits_rewritten
            _guard_counts["foreign_script_words"] += r.foreign_script_words
            return r.text
```

Stream path: at each of the three batcher sites, guard the text immediately before `.add`:
- L4419 becomes `released = await _trust_batcher.add(_guarded(sentence))`.
- L4795 becomes `released = await _trust_batcher.add(_guarded(sentence))`.
- At L4996-4999 (the tail), `remaining = _guarded(token_buffer.strip())` before the `if remaining and ...` check.

  If `_guarded` returns "" for a markdown-only fragment, skip the `.add`, as the existing empty check does.

Sync path: between `final_text` being finalised (~L1397, after the post-applied hook ~L1514) and `self._trust.check_output(session_id, final_text)` (L1519), add `final_text = _guarded(final_text)` when `final_text` is non-empty. The guard's regexes are sentence-agnostic, so applying it to the whole text is equivalent to per-sentence on the sync path.

Metrics and extras:
- In the `orchestrator.stream_turn_complete` extras (~L5128-5160), add `"digits_rewritten": _guard_counts["digits_rewritten"], "foreign_script_words": _guard_counts["foreign_script_words"]`.
- Emit OTel counters `agent_core.output_guard.digits_rewritten_total` and `agent_core.output_guard.foreign_script_words_total` (attributes `channel`, `language`). Use the lazy-meter pattern in `src/chat_provider/metrics.py`: add two counters there, with a `record_output_guard(channel: str, language: str, digits: int, foreign: int) -> None` helper that no-ops when OTel is unavailable, and call it once per turn on both paths.
- Also add `agent_core.result_shaping.errors_total{tool}` to the same file. Call it from `ResultShaper.shape`'s except branch: in Task 3's file, import lazily inside the except branch to avoid an import cycle.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent_core && uv run pytest -q`
Expected: all pass, coverage ≥ 70%.

- [ ] **Step 5: Commit**

```bash
git add agent_core/src agent_core/tests
git commit -m "feat(agent_core): <state>/<recent> in the prompt and the output guard before Trust on both paths (Spec D §5, §6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Dev-kit sync

**Files:**
- Modify: `dev-kit/dev_kit/schemas/domain/agent_core.py`:
  - `AgentSection` L70-82;
  - `TtsRulesConfig` L405-417 (delete);
  - `ChannelEntry` L469-475;
  - `ConnectorDef` L559-573.
- Modify: `dev-kit/dev_kit/schema.py`:
  - `ConnectorDef` L102-120;
  - `AgentConfig` L162-186;
  - `TtsRulesConfig` L636-646 (delete);
  - `ChannelConfig` L680-696;
  - `ChannelsTopLevelConfig` L704-745 (L713 default).
- Modify: `dev-kit/dev_kit/agent/field_rules/agent_core.py`:
  - delete L602-682 (the `channels.voice.tts_rules.*` rules);
  - add new rules.
- Modify: `dev-kit/dpg/agent_core.yaml` (`agent:` L17).
- Delete: `dev-kit/dev_kit/agent/channel_tts.py`, `dev-kit/tests/agent/test_channel_tts.py`.
- Modify: `dev-kit/dev_kit/agent/renderer.py` (L28, L163, L312); `dev-kit/dev_kit/agent/phase_prompts/reach.py` (L135-162); `phase_prompts/language.py` (L99-116); `phase_prompts/_helpers.py` (L405).
- Modify tests:
  - `dev-kit/tests/agent/test_wizard_flow.py` (L113-122);
  - `test_field_rules_agent_core.py` (L73-82);
  - `test_phase_prompts_reach.py` (L76-87);
  - `test_phase_prompts_language.py`;
  - `dev-kit/tests/schemas/domain/test_agent_core.py`;
  - `dev-kit/tests/test_schema.py`.

**Interfaces:**
- Consumes: Task 4's runtime models. The dev-kit mirrors must accept and reject exactly the same shapes.
- Produces:
  - `dev_kit/schemas/spoken_copy_lint.py`: `lint_spoken_copy(agent_core_cfg: dict) -> list[str]`, which returns warnings (spec §10);
  - mirror classes with the same names as Task 4, in the domain mirror;
  - plain-dict-friendly equivalents in the flat schema;
  - FIELD_RULES entries `agent.history_turns`, `agent.state_fields`, `channels.<channel>.output_contract` and `connectors.read[].result_shaping`.

- [ ] **Step 1: Write the failing tests**

In `dev-kit/tests/schemas/domain/test_agent_core.py` (use its existing valid-section helper):

```python
import pytest
from pydantic import ValidationError

from dev_kit.schemas.domain.agent_core import ChannelEntry, ConnectorDef, AgentSection

_CONTRACT = {"default_language": "hindi",
             "languages": {"hindi": {"script": "devanagari", "numbers": "words", "rules": ["x"]}},
             "guard": {"rewrite_digits": True}}


def test_channel_output_contract_accepted_and_tts_rules_rejected():
    ChannelEntry(output_contract=_CONTRACT)
    with pytest.raises(ValidationError):
        ChannelEntry(tts_rules={"numbers": "words"})
    with pytest.raises(ValidationError, match="default_language"):
        ChannelEntry(output_contract={**_CONTRACT, "default_language": "english"})


def test_connector_result_shaping_mirrors_runtime():
    ConnectorDef(name="fetch_jobs", result_shaping={
        "drop_when": [{"field": "role", "operator": "contains", "value": "|"}],
        "sort": [{"field": "match_score", "order": "desc"}],
        "spoken": {"salary_spoken": {"format": "range_thousands", "from": ["salary_min", "salary_max"]}},
        "strip_numbers_in": ["location"]})
    with pytest.raises(ValidationError):
        ConnectorDef(name="x", result_shaping={"sort": [{"field": "a", "order": "sideways"}]})


def test_agent_history_turns_and_state_fields():
    AgentSection(history_turns=2, state_fields=["applications_submitted"])
    with pytest.raises(ValidationError):
        AgentSection(history_turns=-1)
```

`ConnectorDef` / `ChannelEntry` / `AgentSection` may need more required fields; supply them as the file's existing tests do.

In `dev-kit/tests/test_schema.py`, add parametrised reject tests: the flat `AgentCoreConfig` rejects `channels.bridge.tts_rules` and `channels.voice.tts_rules`, and accepts `output_contract`, `result_shaping`, `agent.history_turns` and `agent.state_fields`. Use the blue-dots merged config the existing loader tests use as the base.

In `dev-kit/tests/agent/test_field_rules_agent_core.py`, assert that no FIELD_RULES key contains `tts_rules`, and that the four new keys exist.

```python
# dev-kit/tests/schemas/test_spoken_copy_lint.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dev-kit && uv run pytest tests/schemas/domain/test_agent_core.py tests/test_schema.py tests/agent/test_field_rules_agent_core.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

**Domain mirror (`dev_kit/schemas/domain/agent_core.py`):**
- Copy Task 4's `OutputLanguageContract`, `OutputGuardConfig`, `OutputContractConfig` (with its `_default_declared` validator), `ShapingCondition`, `ShapingSort`, `SpokenFieldConfig` and `ResultShapingConfig` verbatim, keeping the file's `ConfigDict(extra="forbid")` style.
- Delete `TtsRulesConfig`.
- `ChannelEntry`: replace `tts_rules` with `output_contract: Optional[OutputContractConfig] = None`.
- `ConnectorDef`: add `result_shaping: Optional[ResultShapingConfig] = None`.
- `AgentSection`: add `history_turns: int = Field(default=2, ge=0)` and `state_fields: list[str] = Field(default_factory=list)`.

**Flat schema (`dev_kit/schema.py`):**
- Delete `TtsRulesConfig`.
- `ChannelConfig` (L680-696):
  - add `max_tokens: Optional[int] = Field(default=None, gt=0)`, `terminal_word: Optional[str] = None` and `output_contract: Optional[dict] = None`;
  - remove `tts_rules`;
  - set `model_config = {"extra": "forbid"}`.
- `ChannelsTopLevelConfig` L713: `voice` defaults to `ChannelConfig()`.
- `ConnectorDef`: add `result_shaping: Optional[dict] = None`.
- `AgentConfig`: add `history_turns: int = Field(default=2, ge=0)` and `state_fields: list[str] = Field(default_factory=list)`.
- Do not touch the second `ChannelsConfig` at L1512, which is reach_layer's.

**FIELD_RULES (`field_rules/agent_core.py`):**
- Delete the ten `channels.voice.tts_rules.*` rules (L602-682).
- Add rules, copying the constructor shape of the neighbouring rules (`category`, `phase`, `pydantic_class`, `description`):
  - `agent.history_turns`: `framework_default_only`, matching `preprocessing.nlu_processor.history_turns` at L467;
  - `agent.state_fields`: phase `reach`, category `chat`, description "Session keys shown on the <state> status line";
  - `channels.voice.output_contract` and `channels.bridge.output_contract`: phase `reach`, category `chat`, description "Spoken-output contract: per-language script, numbers, short rules, guard switches";
  - `connectors.read.result_shaping`: phase `tools`, or whichever phase `connectors.read` uses at L202.

**Spoken-copy lint (spec §10, warning only):** create `dev_kit/schemas/spoken_copy_lint.py`:

```python
"""Warn when config-authored spoken copy contains digits or markdown (Spec D §10).

The runtime output guard covers model text only; copy written in config is
spoken verbatim, so the dev-kit flags it at authoring/deploy time.
"""
from __future__ import annotations

import re

_DIGIT = re.compile(r"[0-9०-९]")
_MARKDOWN = re.compile(r"\*\*|`|^\s*#|^\s*[-*•]\s", re.M)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_MESSAGE_KEYS = ("blocked_message", "escalation_message", "unsupported_language_message",
                 "unknown_intent_message", "termination_message")


def _check(where: str, text: object, out: list[str]) -> None:
    if not isinstance(text, str) or not text:
        return
    bare = _PLACEHOLDER.sub("", text)
    if _DIGIT.search(bare):
        out.append(f"{where}: contains a digit; spoken copy is read verbatim, write numbers in words")
    if _MARKDOWN.search(bare):
        out.append(f"{where}: contains markdown; spoken copy is read verbatim")


def lint_spoken_copy(agent_core_cfg: dict) -> list[str]:
    """Warnings for digits/markdown in config-authored spoken copy.

    Args:
        agent_core_cfg: Merged agent_core config dict.

    Returns:
        Human-readable warnings (empty when clean). Never raises.
    """
    out: list[str] = []
    conv = agent_core_cfg.get("conversation") or {}
    for key in _MESSAGE_KEYS:
        _check(f"conversation.{key}", conv.get(key), out)
    for sa in (agent_core_cfg.get("agent_workflow") or {}).get("subagents") or []:
        if isinstance(sa, dict):
            for key in ("opening_phrase", "fixed_opening"):
                _check(f"subagents[{sa.get('id')}].{key}", sa.get(key), out)
    return out
```

Call it from `pre_deploy_validate` in `dev_kit/agent/app.py`, on `merged.get("agent_core") or {}`, and add a `"warnings": [...]` key to the response dict. Warnings never affect `valid`. Add one assertion to the existing deploy-validate API test that the response has a `warnings` list.

**dpg defaults (`dev-kit/dpg/agent_core.yaml`):** under `agent:` (L17), add:

```yaml
  history_turns: 2        # Spec D: exchanges shown to the main LLM in <recent>
  state_fields: []        # Spec D: session keys shown on the <state> status line
```

**Delete the voice-only TTS merge:**
- Delete `dev_kit/agent/channel_tts.py` and `tests/agent/test_channel_tts.py`.
- In `renderer.py`, remove the import (L28) and the `merge_voice_tts_into_suffix` call (L163), and remove the `strip_voice_tts_from_suffix` call (L312).

**Wizard authoring:**
- In `phase_prompts/reach.py` L135-162, replace the TTS-rules section with an output-contract section that tells the wizard to:
  - collect, per supported language, the script (devanagari/latin/any), numbers (words/digits) and up to six short rules;
  - turn on `guard.rewrite_digits` and `guard.strip_markdown` for voice-like channels (voice, bridge);
  - write them with `update_config(block="agent_core", section="channels.<channel>.output_contract", ...)`.

  Keep the existing prompt's tone and the `update_config` example format.
- In `language.py` L99-116, change the "do not write tts_rules" note to "do not write output_contract in the language phase; the reach phase authors it".
- In `_helpers.py` L405, replace the `tts_rules` label with `output_contract`.
- Update the four wizard/prompt tests to the new section name and text. Keep their structure.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dev-kit && uv run pytest -q`
Expected: the only failure is the baseline `test_dpg_yaml_validates[reach_layer]`. Fix any test still referencing `tts_rules` by moving it to `output_contract`.

- [ ] **Step 5: Commit**

```bash
git add dev-kit
git commit -m "feat(dev-kit): mirror output_contract, result_shaping, history_turns, state_fields; remove tts_rules (Spec D)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Blue Dots config rewrite (spec §8, A1–A9)

**Files:**
- Modify: `dev-kit/configs/blue-dots/agent_core.yaml`. Line numbers refer to `cf794ef`; re-locate each one by its quoted text, since earlier edits shift them.
- Modify: `reach_layer/bridge/tests/test_blue_dots_config.py` (L44-47)
- Modify:
  - `agent_core/tests/test_blue_dots_dialogue_act_config.py` (remove the Task 4 `xfail`, if added);
  - a new test, `agent_core/tests/test_blue_dots_spec_d_config.py`.

**Interfaces:**
- Consumes: the Task 4 schema; the Task 2/3 semantics (`salary_spoken`, contract rendering).
- Produces: a Blue Dots config that passes `MergedConfig.validate_full`, the workflow loader and the dev-kit flat schema.

- [ ] **Step 1: Write the failing test**

```python
# agent_core/tests/test_blue_dots_spec_d_config.py
"""Spec D §8: the Blue Dots config carries the output contract and job shaping, and none of the removed prompt text."""
from pathlib import Path

import yaml

from eval.nlu.offline import load_merged_config
from src.schema.config import MergedConfig

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
TEXT = (BD / "agent_core.yaml").read_text(encoding="utf-8")


def test_validates():
    MergedConfig.validate_full(load_merged_config(BD))


def test_contract_and_shaping_present():
    cfg = yaml.safe_load(TEXT)
    bridge = cfg["channels"]["bridge"]
    assert "tts_rules" not in bridge and bridge["output_contract"]["default_language"] == "hindi"
    jobs = next(c for c in cfg["connectors"]["read"] if c["name"] == "fetch_jobs")
    assert "salary_spoken" in jobs["result_shaping"]["spoken"]
    assert cfg["agent"]["state_fields"] == ["applications_submitted", "selected_job_item_id"]


def test_removed_prompt_text_is_gone():
    for needle in ("नौकरियाँ मिली हैं।", "एक पल रुकिए", "Let me share what I found", "TWO-STEP FLOW",
                   "User Profile context", "onboard_prep", "Six phases", "Order the first batch",
                   "Order what survives by", "see tts_rules below", "[salary]"):
        assert needle not in TEXT, needle
    assert "[salary_spoken]" in TEXT and "never re-rank" in TEXT
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd agent_core && uv run pytest tests/test_blue_dots_spec_d_config.py -q`
Expected: FAIL. `tts_rules` is present, and so are the old needles.

- [ ] **Step 3: Edit the config**

1. **`agent:`** (after `prompt_session_fields` at L62). Add the lines below, and change the L59-61 comment from "<known_profile>" to "<state> collected".

```yaml
  history_turns: 2
  state_fields: [applications_submitted, selected_job_item_id]
```

2. **`channels.bridge`:**
   - Delete `tts_rules:` (L157-255) entirely.
   - In `system_prompt_suffix`, change L82-83 to "Write numbers, money, dates, times, phone numbers and email addresses in spoken form, never digits or symbols." (drop "(see tts_rules below)").
   - After `terminal_word`, add:

```yaml
    output_contract:
      default_language: hindi
      languages:
        hindi:
          script: devanagari
          numbers: words
          rules:
            - "Devanagari only. English loanwords in Devanagari: जॉब, स्किल, ऑप्शन, अप्लाई, लोकेशन, डेटा. Switch script only if the caller switches language."
            - "Employer, role and place names arrive in Latin script: sound each out and write it in Devanagari (SARA ENTERPRISES → सारा एंटरप्राइज़ेज़, QUESS CORP LTD. → क्वेस कॉर्प, Sarjapur → सरजापुर). Never read a Latin value out as English letters."
            - "Read pay exactly as given in salary_spoken, stipend_spoken or task_rate_spoken; never convert a number yourself. If none is present, say the job without pay."
            - "Times as सुबह / दोपहर / शाम / रात, never AM or PM. Dates in full words: उनतीस जनवरी दो हज़ार छब्बीस."
            - "Abbreviations as letters in Devanagari: ITI → आई टी आई, NCVT → एन सी वी टी."
            - "Speak a city, never a PIN, plot, house, gali or sector number."
        english:
          script: latin
          numbers: words
          rules:
            - "Plain spoken English. Read pay from the *_spoken field's meaning in English words, never digits."
      guard:
        rewrite_digits: true
        strip_markdown: true
        count_foreign_script: true
```

3. **`connectors.read` → `fetch_jobs`** (after its `cache:`, L487-489):

```yaml
      result_shaping:
        drop_when:
          - { field: role, operator: in, value: [null, "", "na", "NA", "Na", "Any", "any", "Not Available"] }
          - { field: role, operator: contains, value: "|" }
        sort:
          - { field: match_score, order: desc }
          - { field: salary_max, order: desc }
        spoken:
          salary_spoken: { format: range_thousands, from: [salary_min, salary_max], unit: per_month }
          stipend_spoken: { format: range_thousands, from: [stipend_min, stipend_max], unit: per_month }
          task_rate_spoken: { format: amount, from: [task_rate_min, task_rate_max], unit: per_task }
        strip_numbers_in: [location]
```

4. **A1** (`agent_system_prompt` ~L864-867): replace the "After fetch_jobs (some results)" sample with "आपके लिए जॉब्स हैं — <कंपनी क>, <वेतन>। <कंपनी ख>, <वेतन>। इनमें से किसके लिए आवेदन करना चाहेंगे?" Keep the "Say the city you actually searched" line that follows.
5. **A2:** delete the "If you genuinely have nothing to add, say 'एक पल रुकिए।'" sentence (~L891-892). In "Mandatory text content" (~L905-907), delete the final paragraph that names "एक पल रुकिए।" as the fallback, so the section ends at "the next-turn text is what the user hears." In job_match (~L2133-2136), replace "If you genuinely cannot formulate the listing, at minimum say: 'Let me share what I found.' — but you should always be able to formulate the listing because the tool result is in your context." with "The tool result is in your context, so you can always formulate the listing."
6. **A3:** in `agent_system_prompt` "## Tool invocation" (~L1234-1237), replace the "Stored memory and prior tool results must NEVER substitute…" sentence with "Within this call, use the job list you already have. Call fetch_jobs again only when the trade or city changes." In job_match's hard rules (~L2170-2173), delete ", NEVER pull a job from a previous tool result". Keep "NEVER invent a job" and "NEVER name a company you have not just seen returned", which now refer to `<known_facts>`.
7. **A4:** in job_match, delete the whole "## TWO-STEP FLOW — read this FIRST, follow it every time" section (~L2120-2138) and the "## On entry" section (~L2140-2143). Remove the City-name normalisation section's reference to a separate `location` parameter, only if it instructs a location-only call; keep the canonical-name map.
8. **A5:** in the file header (L5-6), keep "Five linear subagents map to the 5 phases". In "## Linear flow" (~L1016-1031), write "Five phases.", renumber `apply_confirm` to 5, and in "## Never ask twice" (~L1034) replace "name in phase 5" with "name in phase 4 (profile_setup)".
9. **A6** (`agent_system_prompt` ~L873-878): replace both "Profile choice (several entries…)" and "Profile choice (one entry)" samples with one two-sentence sample: "मुझे आपकी जानकारी मिली — <city> में <trade>। क्या इन्हीं से नौकरियाँ ढूंढूँ?" The use/create/update question was removed (finding F8) and must not come back.
10. **A7** (job_match):
    - Delete the "## Then clean the result before you name a single job" steps 1 and 3 and the "Order what survives by `match_score`…" line (~L2107-2119). Keep step 2 as: "Skip any job that is not the kind of work the caller asked for — the search returns rows whatever you ask, so this is your judgement."
    - Delete "Order the first batch by the numeric upper bound…" (~L2203-2204).
    - Replace "After the first batch, walk the array in order — do not re-rank. Best-fit ranking is for the first batch only." with "Read jobs in the order given and never re-rank; after the first batch, continue down the same list."
    - In the "complaint about location or job type" rule, replace "re-ranked on what they just said" with "skipping what they just ruled out".
11. **A8** (job_match "## On user picking a job", ~L2258-2262): replace the section body with "When the caller picks a job, <caller_turn> shows it as resolved. Say the role and company back once, then continue." In `agent_system_prompt` (~L1213-1214), replace "They are above, in your context." with "They are in <state> and <recent>." In job_match (~L2089), replace "Take them from <known_profile>, in this order:" with "Take them from <state> collected, in this order:".
12. **A9** (job_match):
    - Delete "## [salary] is spoken in ROUNDED THOUSANDS" (~L2193-2201).
    - Replace every `[salary]` in the "Presenting results" templates and their notes with `[salary_spoken]`.
    - In the "## Rules for that block" list, replace the Latin-script bullet with "[role], [company] and [location] arrive in LATIN script: convert each to Devanagari before it enters the sentence, as the output contract says."
    - Add one line under the templates: "No pay field present → say the job without pay."

`reach_layer/bridge/tests/test_blue_dots_config.py` L44-47: change the `tts_rules` assertion to check `_channels()["bridge"]["output_contract"]["languages"]["hindi"]["numbers"] == "words"`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd agent_core && uv run pytest -q
cd ../dev-kit && uv run pytest -q
cd ../reach_layer && uv run pytest -q bridge/tests/test_blue_dots_config.py
cd ../agent_core && uv run --env-file ../../blue-dots-economy/ai-diffusion-dpg/.env.local python -m eval.nlu.run --config ../dev-kit/configs/blue-dots --cases eval/nlu/cases/synthetic.jsonl --repeat 1 --out /tmp/nlu-d.json
```

Expected:
- all three suites pass at baseline;
- the NLU eval runs, intent accuracy ≥ 0.95. That's a smoke check: D doesn't touch NLU prompts. If the `.env.local` path is unavailable, skip the last command and note it in the report.

- [ ] **Step 5: Commit**

```bash
git add dev-kit/configs/blue-dots/agent_core.yaml reach_layer/bridge/tests/test_blue_dots_config.py agent_core/tests
git commit -m "feat(blue-dots): output contract, job shaping, state fields; resolve prompt contradictions A1–A9 (Spec D §8)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Scenario runner, docs and the deploy gate

**Files:**
- Create: `agent_core/eval/scenarios/__init__.py`, `agent_core/eval/scenarios/run.py`, `agent_core/eval/scenarios/checks.py`, `agent_core/eval/scenarios/blue_dots.yaml`
- Test: `agent_core/tests/eval/test_scenario_checks.py`
- Modify:
  - `ARCHITECTURE.md` (L113, the channel config list);
  - `agent_core/README.md`: the prompt-sections description, and the config table rows for `channels.*.output_contract`, `connectors.*.result_shaping`, `agent.history_turns` and `agent.state_fields`;
  - `CLAUDE.md`: the runtime sequence line, which mentions prompt blocks;
  - comment-only mentions of `tts_rules` in `reach_layer/base/reach_layer_base.py` L300, `reach_layer/voice/src/pipecat_services/agent_core_llm.py` L383 and `agent_core/src/servers/orchestration_server.py` L412.

**Interfaces:**
- Produces:
  - `checks.run_checks(reply: str, session: dict) -> dict[str, bool]`, with keys `no_digits`, `no_markdown`, `no_job_count`, `no_wait_phrase`, `first_job_is_option_1`;
  - `checks.foreign_script_words(reply: str) -> int`;
  - the CLI `python -m eval.scenarios.run --bridge http://127.0.0.1:18008 --scenarios eval/scenarios/blue_dots.yaml --out report.json [--redis-container dpg_redis]`.

- [ ] **Step 1: Write the failing test**

```python
# agent_core/tests/eval/test_scenario_checks.py
from eval.scenarios.checks import foreign_script_words, run_checks


def test_clean_reply_passes():
    r = run_checks("आपके लिए जॉब्स हैं — पहला: वेल्डर, फ्लिपकार्ट। किसके बारे में जानना चाहेंगे?",
                   {"spoken_first_label": "वेल्डर, फ्लिपकार्ट"})
    assert all(r.values()), r


def test_each_check_fails_on_its_defect():
    assert run_checks("सैलरी 25000 है", {})["no_digits"] is False
    assert run_checks("**ध्यान दें**", {})["no_markdown"] is False
    assert run_checks("पाँच नौकरियाँ मिली हैं", {})["no_job_count"] is False
    assert run_checks("एक पल रुकिए", {})["no_wait_phrase"] is False


def test_foreign_script_words():
    assert foreign_script_words("QUESS CORP में जॉब") == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd agent_core && uv run pytest tests/eval/test_scenario_checks.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# agent_core/eval/scenarios/checks.py
"""Automatic checks on one bot reply for the scenario runner (Spec D §9.2)."""
from __future__ import annotations

import re

_DIGIT = re.compile(r"[0-9०-९]")
_MARKDOWN = re.compile(r"\*\*|`|^\s*#|^\s*[-*•]\s", re.M)
_JOB_COUNT = re.compile(r"(?:\S+\s+)?(?:नौकरियाँ|नौकरियां|जॉब्स)\s+मिली\s+हैं")
_WAIT = ("एक पल रुकिए", "कृपया प्रतीक्षा", "ज़रा इंतज़ार", "please wait", "let me share what i found")
_LATIN = re.compile(r"[A-Za-z]{2,}")


def foreign_script_words(reply: str) -> int:
    """Latin-script words in a Devanagari reply."""
    return len(_LATIN.findall(reply))


def run_checks(reply: str, session: dict) -> dict[str, bool]:
    """Pass/fail per automatic check.

    Args:
        reply: Full bot reply text for one turn.
        session: Facts for order checks; ``spoken_first_label`` is the label of
            stored option 1 in Devanagari when a list was read (else absent).

    Returns:
        Check name → passed.
    """
    low = reply.lower()
    first = session.get("spoken_first_label")
    return {
        "no_digits": not _DIGIT.search(reply),
        "no_markdown": not _MARKDOWN.search(reply),
        "no_job_count": not _JOB_COUNT.search(reply),
        "no_wait_phrase": not any(w in low for w in _WAIT),
        "first_job_is_option_1": True if not first else first in reply,
    }
```

`eval/scenarios/run.py`:
- Load `blue_dots.yaml`: a list of `{id, lines: [caller lines], expect_list: bool}`.
- For each scenario, generate a fresh caller phone (`"9197" + 8 random digits`) and a `call_id`.
- POST each line to `<bridge>/v1/chat/completions` with `stream: true` and the `metadata` from `docs/blue-dots/test-scenarios.md`. Collect the streamed text, and measure `first_sentence_ms` (the first content chunk) and the total time.
- With `--redis-container`, read `served_tool_results` and the cached `fetch_jobs` rows through `docker exec <c> redis-cli HGET session:<phone>:<call_id> ...`, to derive `spoken_first_label`. Otherwise skip the order check.
- Write a JSON report with each turn's reply, timings and `run_checks` result, plus a summary of pass counts and the first-sentence p50/p95.
- Use `httpx`, which is already an agent_core dependency (grep `pyproject.toml`; if it's absent, use `urllib.request` with streaming reads).
- **Never log replies to stdout beyond the summary;** the report file holds them.

`blue_dots.yaml` covers A1, A2, B1–B4, C2–C4, D3, D4, D7, D8, D10, D13–D15 and E1–E4, with the caller lines quoted in `docs/blue-dots/test-scenarios.md`. Copy them verbatim. A2 reuses A1's phone, so model it with a `reuse_phone_of: A1` field.

Docs:
- In `ARCHITECTURE.md` L113, replace "`tts_rules` (voice only)" with "`output_contract` (all channels, rendered by the runtime)".
- In `agent_core/README.md`, add a short "Output contract and guard" subsection (§3/§5 in two paragraphs). Update the prompt-sections list to the §6.5 tier table. Add the four config rows.
- In `CLAUDE.md`'s runtime sequence, mention the output guard before the Trust output check.
- Replace the comment-only `tts_rules` mentions with `output_contract`.

- [ ] **Step 4: Verify**

```bash
cd agent_core && uv run pytest -q
cd ../dev-kit && uv run pytest -q
git grep -n "tts_rules" -- ':!docs/superpowers'      # expect only negative/reject tests
git grep -n "known_profile\|active_guardrails\|Last question asked" -- agent_core/src   # expect none
docker build -q -f dev-kit/Dockerfile -t dpg-dev-kit:spec-d .
docker run -d --rm --name dpg-dev-kit-spec-d -e OPENAI_API_KEY=placeholder-not-used -p 18082:8080 dpg-dev-kit:spec-d
curl -s -X POST http://127.0.0.1:18082/api/projects/blue-dots/deploy/validate | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['validator'], {b: not e for b, e in r['block_errors'].items()})"
docker rm -f dpg-dev-kit-spec-d
```

Expected:
- the suites are at baseline;
- the greps show only reject tests and no source hits;
- the validator prints `runtime_baked`, with every block `True`. The five pre-existing cross-block invariant warnings, recorded in PR #428, are acceptable.

The before/after scenario-runner comparison against a local bridge needs the local stack running. It is a pre-merge step for the human partner, or for the controller if the stack is up. It is not part of this task's pass criteria.

- [ ] **Step 5: Commit**

```bash
git add agent_core/eval/scenarios agent_core/tests/eval ARCHITECTURE.md agent_core/README.md CLAUDE.md reach_layer agent_core/src/servers
git commit -m "feat(eval): scripted scenario runner with output checks; docs for output contract and context blocks (Spec D §9.2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
