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

from src.llm_wrapper.base import LLMWrapperBase

logger = logging.getLogger(__name__)

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

    def normalise(
        self,
        raw_input: str,
        config: dict,
        llm: LLMWrapperBase,
    ) -> tuple[str, str]:
        """
        Detect language and normalise input text.

        Args:
            raw_input: Original user message.
            config:    Full agent_core config dict.
            llm:       LLM wrapper for direct LLM calls.

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
        model_override = block_cfg.get("model")
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

            system_prompt = _build_lang_norm_prompt(supported_languages, default_language)
            messages = [{"role": "user", "content": raw_input}]

            llm_response = llm.call(
                messages=messages,
                tools=[],
                system=system_prompt,
                model_override=model_override,
            )

            if llm_response.stop_reason == "error" or not llm_response.content:
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
                llm_response.content.strip(), raw_input
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
