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
