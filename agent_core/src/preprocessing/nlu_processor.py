"""
agent_core/src/nlu_processor.py

NLU intent classification, entity extraction, and sentiment detection —
executed in Agent Core before the KE call.

Uses a single LLM call (Haiku). Recent session history is included in the LLM
messages so the model can resolve context-dependent intents such as follow-up
questions ("tell me more", "what about plumber?").

On any LLM failure or JSON parse error, degrades gracefully:
    returns NLUResult(intent="unknown", confidence=0.0, ...).

Config section read: preprocessing.nlu_processor
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from src.llm_wrapper.base import LLMWrapperBase
from src.models import NLUResult, UserStateClassification
from src.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_USER_STATE_SECTION_TEMPLATE = """

## User mental state classification
The user may be in one of these mental states. Classify which one best fits
their latest message, using the signals as hints (not strict rules).

Previous state: {previous_state}
If the message is ambiguous or does not clearly shift the state, return the
previous state with lower confidence.

States:
{states_block}

Return an additional top-level field in your JSON:
  "user_state": {{ "id": "<one of the declared ids>", "confidence": <0.0..1.0> }}
"""

_NLU_SYSTEM_PROMPT_TEMPLATE = """{domain_instruction}

Classify the user's latest message and return a JSON object only — no explanation, no markdown.

Valid intents: {intents}
Primary entity types: {entities}
Valid sentiment classes: {sentiment_classes}

Return exactly this JSON structure:
{{
  "intent": "<one of the valid intents>",
  "entities": {{ "<entity_type>": "<value>", ... }},
  "sentiment": "<one of the valid sentiment classes>",
  "confidence": <float between 0.0 and 1.0>
}}

Rules:
- Use "unknown" intent if no intent matches or confidence is below 0.5.
- Only include entity types that are clearly present in the message.
- **Ad-hoc extraction**: If the user provides interesting personal details or preferences NOT in the primary list (e.g. hobbies, specific equipment owned, preferred brand), extract them with **dynamic keys** in the "entities" object. Choose descriptive key names on the fly.
- **Key deduplication**: {existing_profile_keys_rule}
- Return an empty dict {{}} for entities if none are found.
- Use the current subagent and the last question asked (if provided) to resolve
  follow-up or ambiguous messages (e.g. a one-word answer like "welder" after "what trade
  do you work in?" should be classified as a profile answer, not an unknown intent).
- Never include keys outside the four specified (intent, entities, sentiment, confidence).{user_state_section}"""

# Injected when the orchestrator passes existing profile keys.
_PROFILE_KEYS_RULE = (
    "The user's profile already contains these fields: [{keys}]. "
    "If your extracted entity is semantically equivalent to an existing field, "
    "you MUST reuse that exact field name instead of inventing a new key. "
    "Only create a new dynamic key if no existing field covers the same concept."
)
_PROFILE_KEYS_RULE_NONE = (
    "No existing profile fields are available. Use descriptive key names for ad-hoc entities."
)


class NLUProcessor:
    """
    Classifies intent, extracts entities, and detects sentiment via a single LLM call.

    Injects recent session history into the LLM prompt so the model can resolve
    context-dependent follow-up messages (e.g. "tell me more about that").

    Instantiated once by AgentCore at startup — config is parsed once in __init__
    and reused across all sessions. Config section: preprocessing.nlu_processor
    """

    def __init__(self, config: dict) -> None:
        """
        Parse and cache NLU config values at startup.

        Args:
            config: Full agent_core config dict. Config is read once here and
                    never re-parsed during request processing.
        """
        nlu_config = (config or {}).get("preprocessing", {}).get("nlu_processor", {})
        self._model: str = nlu_config.get("model", "")
        if not self._model:
            raise ConfigurationError(
                "preprocessing.nlu_processor.model is missing in domain configuration."
            )

        self._domain_instruction: str = nlu_config.get(
            "domain_instruction", "You are an NLU (Natural Language Understanding) classifier."
        )
        self._global_entities: list[str] = nlu_config.get("entities", [])
        self._sentiment_classes: list[str] = nlu_config.get(
            "sentiment_classes", ["neutral", "positive", "distressed", "frustrated"]
        )
        self._default_intents: list[str] = nlu_config.get("intents", ["unknown"])

        # ------------------------------------------------------------------
        # User-state model (GH-139) — optional, Conversational domains only
        # ------------------------------------------------------------------
        usm = (config or {}).get("conversation", {}).get("user_state_model", {}) or {}
        self._user_state_enabled: bool = bool(usm.get("enabled", False))
        self._user_states: list[dict] = []
        self._user_state_default: str = ""

        raw_threshold = nlu_config.get("user_state_confidence_threshold", 0.4)
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise ConfigurationError(
                f"preprocessing.nlu_processor.user_state_confidence_threshold "
                f"must be a float, got {raw_threshold!r}"
            ) from e
        if not 0.0 <= threshold <= 1.0:
            raise ConfigurationError(
                f"preprocessing.nlu_processor.user_state_confidence_threshold "
                f"must be in [0.0, 1.0], got {threshold}"
            )
        self._user_state_threshold: float = threshold

        if self._user_state_enabled:
            states = usm.get("states") or []
            if not states:
                raise ConfigurationError(
                    "conversation.user_state_model.states must be non-empty when enabled=true"
                )
            ids: list[str] = []
            for idx, s in enumerate(states):
                sid = (s or {}).get("id", "")
                guidance = (s or {}).get("guidance", "")
                if not sid:
                    raise ConfigurationError(
                        f"conversation.user_state_model.states[{idx}].id must be non-empty"
                    )
                if not guidance:
                    raise ConfigurationError(
                        f"conversation.user_state_model.states[{idx}].guidance "
                        f"must be non-empty for state {sid!r}"
                    )
                ids.append(sid)
            if len(ids) != len(set(ids)):
                raise ConfigurationError(
                    "conversation.user_state_model.states ids must be unique"
                )
            default = usm.get("default_state", "")
            if not default:
                raise ConfigurationError(
                    "conversation.user_state_model.default_state is required when enabled=true"
                )
            if default not in ids:
                raise ConfigurationError(
                    f"conversation.user_state_model.default_state {default!r} "
                    f"must match one of the declared state ids: {ids}"
                )
            self._user_states = list(states)
            self._user_state_default = default

    def process(
        self,
        normalised_input: str,
        current_question: str,
        current_subagent_id: str,
        llm: LLMWrapperBase,
        allowed_intents: list[str] | None = None,
        existing_profile_keys: list[str] | None = None,
        previous_user_state: str | None = None,
    ) -> NLUResult:
        """
        Run NLU classification with workflow context.

        Args:
            normalised_input: Cleaned text from Language Normaliser.
            current_question: The last question the agent asked this session
                              (from session["current_question"]). Used to resolve
                              short follow-up answers like "welder" or "Hubli".
            current_subagent_id: Current subagent ID (from session["current_node"]).
                                 Helps classify answers that are only meaningful in context.
            llm:              LLM wrapper for direct LLM calls.
            allowed_intents:  Optional list of allowed intents to use for the LLM system
                              prompt and validation. If provided (not None and not empty),
                              overrides the intents from config. If None or empty, falls
                              back to reading intents from config for backward compatibility.
            existing_profile_keys: Optional list of field names already stored in the user's
                              profile (declared fields + ad-hoc attribute keys). When provided,
                              the NLU prompt instructs the LLM to reuse an existing key
                              instead of inventing a semantically equivalent new one.
            previous_user_state: User-state id from the prior turn (or the configured
                                 default on first turn). Passed to the LLM as "previous
                                 state" context and used as the sticky fallback when the
                                 returned classification is below threshold or invalid.
                                 Ignored when the user-state model is disabled.

        Returns:
            NLUResult. On any failure: NLUResult(intent="unknown", confidence=0.0).
            Never raises.
        """
        start = time.time()

        if not normalised_input:
            return _fallback_nlu_result()

        # Use allowed_intents if provided and non-empty; otherwise fall back to config
        intents = (
            allowed_intents
            if allowed_intents is not None and len(allowed_intents) > 0
            else self._default_intents
        )

        try:
            if existing_profile_keys:
                keys_rule = _PROFILE_KEYS_RULE.format(keys=", ".join(existing_profile_keys))
            else:
                keys_rule = _PROFILE_KEYS_RULE_NONE

            # User-state classification section (GH-139) — empty string when disabled
            if self._user_state_enabled:
                state_lines: list[str] = []
                for s in self._user_states:
                    sid = s.get("id", "")
                    signals = s.get("signals", []) or []
                    guidance_raw = (s.get("guidance", "") or "").strip()
                    guidance_first = (
                        guidance_raw.splitlines()[0] if guidance_raw else ""
                    )
                    signals_str = (
                        " | ".join(f'"{sig}"' for sig in signals) if signals else "(none)"
                    )
                    state_lines.append(
                        f"- {sid}:\n    signals: {signals_str}\n    meaning: {guidance_first}"
                    )
                user_state_section = _USER_STATE_SECTION_TEMPLATE.format(
                    previous_state=previous_user_state or self._user_state_default,
                    states_block="\n".join(state_lines),
                )
            else:
                user_state_section = ""

            system_prompt = _NLU_SYSTEM_PROMPT_TEMPLATE.format(
                domain_instruction=self._domain_instruction,
                intents=", ".join(intents),
                entities=", ".join(self._global_entities),
                sentiment_classes=", ".join(self._sentiment_classes),
                existing_profile_keys_rule=keys_rule,
                user_state_section=user_state_section,
            )

            # Build NLU context message with workflow and last-question grounding
            context_parts: list[str] = []
            if current_subagent_id:
                context_parts.append(f"Current workflow step: {current_subagent_id}")
            if current_question:
                context_parts.append(f"Last question asked: {current_question}")
            context_parts.append(f"User message: {normalised_input}")

            messages: list[dict] = [
                {"role": "user", "content": "\n".join(context_parts)}
            ]

            llm_response = llm.call(
                messages=messages,
                tools=[],
                system=system_prompt,
                model_override=self._model,
            )

            if llm_response.stop_reason == "error" or not llm_response.content:
                logger.warning(
                    "nlu_processor.llm_failure",
                    extra={
                        "operation": "nlu_processor.process",
                        "status": "failure",
                        "stop_reason": llm_response.stop_reason,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _fallback_nlu_result()

            parsed = self._parse_nlu_json(llm_response.content)

            # Validate intent is in allowed list
            intent = parsed.get("intent", "unknown")
            if intent not in intents:
                logger.warning(
                    "nlu_processor.invalid_intent",
                    extra={
                        "operation": "nlu_processor.process",
                        "status": "failure",
                        "intent": intent,
                    },
                )
                intent = "unknown"

            extracted_entities = parsed.get("entities", {})
            if not isinstance(extracted_entities, dict):
                extracted_entities = {}

            user_state_obj: UserStateClassification | None = None
            if self._user_state_enabled:
                raw_state = parsed.get("user_state")
                valid_ids = {s.get("id") for s in self._user_states}
                fallback_id = previous_user_state or self._user_state_default
                if isinstance(raw_state, dict):
                    parsed_id = raw_state.get("id", "")
                    try:
                        parsed_conf = float(raw_state.get("confidence", 0.0))
                    except (TypeError, ValueError):
                        parsed_conf = 0.0
                    if parsed_id in valid_ids and parsed_conf >= self._user_state_threshold:
                        user_state_obj = UserStateClassification(
                            id=parsed_id, confidence=parsed_conf,
                        )
                    else:
                        if parsed_id and parsed_id not in valid_ids:
                            logger.warning(
                                "nlu_processor.user_state_invalid_id",
                                extra={
                                    "operation": "nlu_processor.process",
                                    "status": "skipped",
                                    "returned_id": parsed_id,
                                    "fallback_id": fallback_id,
                                },
                            )
                        user_state_obj = UserStateClassification(
                            id=fallback_id, confidence=parsed_conf,
                        )
                else:
                    logger.warning(
                        "nlu_processor.user_state_missing",
                        extra={
                            "operation": "nlu_processor.process",
                            "status": "skipped",
                            "fallback_id": fallback_id,
                        },
                    )
                    user_state_obj = UserStateClassification(
                        id=fallback_id, confidence=0.0,
                    )

            result = NLUResult(
                intent=intent,
                entities=extracted_entities,
                sentiment=parsed.get("sentiment", "neutral"),
                confidence=float(parsed.get("confidence", 0.0)),
                user_state=user_state_obj,
            )

            logger.info(
                "nlu_processor.process",
                extra={
                    "operation": "nlu_processor.process",
                    "status": "success",
                    "intent": result.intent,
                    "sentiment": result.sentiment,
                    "confidence": result.confidence,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return result

        except Exception as e:
            logger.error(
                "nlu_processor.error",
                extra={
                    "operation": "nlu_processor.process",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return _fallback_nlu_result()

    def _parse_nlu_json(self, raw: str) -> dict[str, Any]:
        """Parse LLM JSON response. Returns empty dict on any parse error."""
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        logger.warning(
            "nlu_processor.json_parse_failure",
            extra={
                "operation": "nlu_processor._parse_nlu_json",
                "status": "failure",
            },
        )
        return {}


def _fallback_nlu_result() -> NLUResult:
    """Default NLUResult when LLM call fails or JSON cannot be parsed."""
    return NLUResult(intent="unknown", entities={}, sentiment="neutral", confidence=0.0)
