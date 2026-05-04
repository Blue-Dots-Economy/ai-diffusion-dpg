"""
agent_core/src/language_normalisation.py

Language detection and normalisation — executed in Agent Core before the KE call.

Runs after Trust check (step 2), before NLU Processor (step 4).

Uses a single LLM call (llm_native provider) with the configured model.

On any LLM failure or JSON parse error, degrades gracefully:
    returns (raw_input, "") — original text, no language detected.

Config section read: preprocessing.language_normalisation
"""

from __future__ import annotations

import json
import logging
import re
import time

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatRequest, Message, SystemPrompt, TextBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GH-151 #3: cheap script-based pre-screen
# ---------------------------------------------------------------------------
#
# The expensive LLM-driven lang-norm call exists so the downstream prompt
# receives input in a predictable script. When the caller is already speaking
# in the configured default language's native script, normalisation is a
# no-op — running the LLM only adds ~1.8 s of pointless latency.
#
# Each entry below maps a supported default_language (lowercased) to the
# Unicode ranges that count as "in that language's primary script". If >= 60%
# of the non-whitespace characters in the raw input fall inside those ranges,
# we skip the LLM call entirely and return (raw, default_language).
#
# Ratio keeps the check robust to common code-switched tokens (digits,
# punctuation, the occasional English word) while still catching mixed-script
# Hinglish like "merko job chahiye" → Latin script dominated → won't bypass.

_DEFAULT_SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    # Hindi — Devanagari block + Devanagari Extended
    "hindi": ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    # Kannada block
    "kannada": ((0x0C80, 0x0CFF),),
    # English is deliberately absent: Latin-script inputs could equally be
    # English, Hinglish, code-switched, or mis-transcribed. Skipping the LLM
    # call on a majority-Latin heuristic would mis-classify Hinglish traffic
    # as English. The non-English default languages use their own non-Latin
    # scripts, which is an unambiguous signal.
}

_SCRIPT_BYPASS_RATIO = 0.6


def _is_input_in_default_script(raw: str, default_language: str) -> bool:
    """Return True if the raw input is predominantly in the default language's script.

    Non-whitespace-stripped majority check — see module docstring. Returns
    False for unsupported default_language values so callers safely fall
    through to the LLM path.
    """
    ranges = _DEFAULT_SCRIPT_RANGES.get(default_language.lower())
    if not ranges:
        return False
    meaningful = [ch for ch in raw if not ch.isspace()]
    if not meaningful:
        return False
    in_script = sum(
        1 for ch in meaningful
        if any(lo <= ord(ch) <= hi for lo, hi in ranges)
    )
    return (in_script / len(meaningful)) >= _SCRIPT_BYPASS_RATIO

def _build_lang_norm_prompt(supported_languages: list[str], default_language: str) -> str:
    """Build the language normalisation system prompt from config values.

    Args:
        supported_languages: List of language names supported by the deployment.
        default_language: Preferred language to use when detection is ambiguous.

    Returns:
        System prompt string for the language normalisation LLM call.
    """
    lang_list = ", ".join(supported_languages)
    return (
        "You are a language processing assistant. Analyse the user's message and "
        "return a JSON object only — no explanation, no markdown.\n\n"
        f"Supported languages: {lang_list}\n"
        f"Default language: {default_language}\n\n"
        "Return exactly this JSON structure:\n"
        "{{\n"
        f'  "detected_language": "<one of: {lang_list}>",\n'
        '  "normalised_text": "<cleaned, normalised version of the input>"\n'
        "}}\n\n"
        "Detection rules:\n"
        f"1. When the message is short, ambiguous, or uses common words shared "
        f"across supported languages, prefer {default_language}.\n"
        "2. If the input uses Roman script for a non-Latin language (transliteration), "
        "keep it as-is — do not convert to native script.\n"
        "3. If the input mixes languages, keep the mix but clean spelling inconsistencies.\n"
        "4. Correct obvious typos only if clearly unambiguous.\n"
        "5. NEVER change the meaning or add words not present in the original."
    )


class LanguageNormaliser:
    """
    Detects language and normalises raw user input.

    Instantiated once by AgentCore at startup — stateless, reused across all sessions.
    Config section: preprocessing.language_normalisation
    """

    def __init__(self, chat_provider: ChatProviderBase) -> None:
        """Initialise with an injected chat provider.

        Args:
            chat_provider: Pre-configured ChatProviderBase instance used for
                           all language normalisation LLM calls.
        """
        self._chat_provider = chat_provider

    def normalise(
        self,
        raw_input: str,
        config: dict,
    ) -> tuple[str, str]:
        """
        Detect language and normalise input text.

        Args:
            raw_input: Original user message.
            config:    Full agent_core config dict.

        Returns:
            (normalised_input, detected_language)
            On any failure: (raw_input, "") — original text unchanged, no language detected.
            Never raises.
        """
        start = time.time()

        if not raw_input:
            return raw_input, ""

        block_cfg = (
            config.get("preprocessing", {})
            .get("language_normalisation", {})
        )
        supported_languages = block_cfg.get(
            "supported_languages", ["hindi", "kannada", "english", "hinglish"]
        )
        default_language = block_cfg.get("default_language", "")
        min_detection_tokens = int(block_cfg.get("min_detection_tokens", 3))

        try:
            # Short input: classification is unreliable — return default language directly.
            if len(raw_input.split()) < min_detection_tokens:
                logger.info(
                    "language_normalisation.short_input_bypass",
                    extra={
                        "operation": "language_normalisation.normalise",
                        "status": "skipped",
                        "reason": "below_min_detection_tokens",
                        "latency_ms": 0,
                    },
                )
                return raw_input, default_language

            # GH-151 #3: script-based bypass. When the caller is already
            # speaking the default language in its native script, the LLM
            # call has nothing meaningful to normalise — skip it and save
            # ~1.8 s per turn (Haiku). Loose ratio-based check keeps the
            # bypass robust to mixed-in digits, punctuation, and the
            # occasional code-switched word. Disabled by setting
            # script_bypass=false in domain config.
            script_bypass_enabled = bool(block_cfg.get("script_bypass", True))
            if (
                script_bypass_enabled
                and default_language
                and _is_input_in_default_script(raw_input, default_language)
            ):
                logger.info(
                    "language_normalisation.script_bypass",
                    extra={
                        "operation": "language_normalisation.normalise",
                        "status": "skipped",
                        "reason": "input_already_in_default_script",
                        "detected_language": default_language,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return raw_input, default_language

            system_prompt = _build_lang_norm_prompt(supported_languages, default_language)
            request = ChatRequest(
                messages=[Message(role="user", content=[TextBlock(text=raw_input)])],
                system=SystemPrompt(blocks=[TextBlock(text=system_prompt)]),
            )
            llm_response = self._chat_provider.call(request)

            text = next((b.text for b in llm_response.content if b.type == "text"), None)
            if llm_response.stop_reason == "error" or not text:
                logger.warning(
                    "language_normalisation.llm_failure",
                    extra={
                        "operation": "language_normalisation.normalise",
                        "status": "failure",
                        "stop_reason": llm_response.stop_reason,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return raw_input, ""

            normalised, detected = self._parse_response(
                text.strip(), raw_input
            )

            logger.info(
                "language_normalisation.normalise",
                extra={
                    "operation": "language_normalisation.normalise",
                    "status": "success",
                    "detected_language": detected,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return normalised, detected

        except Exception as e:
            logger.error(
                "language_normalisation.error",
                extra={
                    "operation": "language_normalisation.normalise",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return raw_input, ""

    def _parse_response(self, raw_content: str, raw_input: str) -> tuple[str, str]:
        """Parse LLM JSON response. Falls back to (raw_input, "") on any parse error."""
        # Try direct parse
        try:
            data = json.loads(raw_content)
            return data.get("normalised_text", raw_input), data.get("detected_language", "")
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from prose
        json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return data.get("normalised_text", raw_input), data.get("detected_language", "")
            except json.JSONDecodeError:
                pass

        logger.warning(
            "language_normalisation.parse_failure",
            extra={
                "operation": "language_normalisation._parse_response",
                "status": "failure",
            },
        )
        return raw_input, ""
