"""Automatic checks on one bot reply for the scenario runner (Spec D §9.2)."""
from __future__ import annotations

import re

_DIGIT = re.compile(r"[0-9०-९]")
_MARKDOWN = re.compile(r"\*\*|`|^\s*#|^\s*[-*•]\s", re.M)
_TOKEN = re.compile(r"\S+")
_STRIP = ".,;:!?।()[]\"'—-–"
_NOUNS = {"जॉब", "जॉब्स", "नौकरी", "नौकरियाँ", "नौकरियां", "विकल्प", "job", "jobs", "openings"}
_COUNT_WORDS = {
    "एक", "दो", "तीन", "चार", "पाँच", "पांच", "छह", "छः", "सात", "आठ", "नौ", "दस", "कुछ", "कई", "बहुत",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "few", "several",
}
_WAIT = ("एक पल रुकिए", "कृपया प्रतीक्षा", "ज़रा इंतज़ार", "please wait", "let me share what i found")
_LATIN = re.compile(r"[A-Za-z]{2,}")


def foreign_script_words(reply: str) -> int:
    """Latin-script words in a Devanagari reply."""
    return len(_LATIN.findall(reply))


def _announces_count(reply: str) -> bool:
    """True when a count word or digit sits within three tokens of a job noun."""
    toks = [t.strip(_STRIP).lower() for t in _TOKEN.findall(reply)]
    for i, t in enumerate(toks):
        if t not in _NOUNS:
            continue
        for u in toks[max(0, i - 3): i + 4]:
            if u in _COUNT_WORDS or any(c.isdigit() for c in u):
                return True
    return False


def first_job_order(reply: str, markers: list[str | None]) -> bool | None:
    """Whether the reply reads offered rows in stored order, keyed on spoken-pay markers.

    Args:
        reply: Bot reply text.
        markers: Per stored row (in order), its ``salary_spoken`` / ``stipend_spoken`` /
            ``task_rate_spoken`` text, or None when the row has no pay field.

    Returns:
        None when no marker is found (cannot judge) or row 0 has no marker; else True when
        row 0 is spoken first and the found rows keep stored order.
    """
    if not markers or not markers[0]:
        return None
    seen: set[str] = set()
    found: list[tuple[int, int]] = []  # (row index, position)
    for idx, m in enumerate(markers):
        if not m or m in seen:
            continue
        seen.add(m)
        pos = reply.find(m)
        if pos >= 0:
            found.append((idx, pos))
    if not found:
        return None
    by_pos = [idx for idx, _ in sorted(found, key=lambda f: f[1])]
    return by_pos[0] == 0 and by_pos == sorted(by_pos)


def run_checks(reply: str, session: dict) -> dict[str, bool | None]:
    """Pass/fail per automatic check.

    Args:
        reply: Full bot reply text for one turn.
        session: Facts for order checks; ``offered_markers`` is the per-row spoken-pay
            text of the stored offered rows, in order (absent when no list is stored).

    Returns:
        Check name → True/False, or None when skipped (never counted as a pass).
    """
    low = reply.lower()
    return {
        "no_digits": not _DIGIT.search(reply),
        "no_markdown": not _MARKDOWN.search(reply),
        "no_job_count": not _announces_count(reply),
        "no_wait_phrase": not any(w in low for w in _WAIT),
        "first_job_is_option_1": first_job_order(reply, session.get("offered_markers") or []),
    }
