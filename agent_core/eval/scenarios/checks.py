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
