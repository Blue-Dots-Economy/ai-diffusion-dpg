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

import hashlib
import json
import logging
import re
import time
from typing import Any

from src.llm_wrapper.base import LLMWrapperBase
from src.models import NLUResult, UserStateClassification
from src.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

# GH-195 — prompt-cache fix:
# The NLU system prompt must be FULLY STATIC across turns so that Anthropic's
# prompt cache can be reused from turn 2 onward. Any value that varies
# per-turn (existing profile keys, previous user-state id) is moved into the
# user message (which is the natural, uncached, per-turn input).
#
# Anything inside `_NLU_SYSTEM_PROMPT_TEMPLATE` below is emitted as the
# cacheable system block and therefore MUST NOT interpolate per-turn data.
#
# `allowed_intents` is semi-static — it changes with the current subagent, not
# per turn. One cache entry per subagent is acceptable (and is the whole
# point of caching the system prompt).

_USER_STATE_SECTION_TEMPLATE = """

## User mental state classification
The user may be in one of these mental states. Classify which one best fits
their latest message, using the signals as hints (not strict rules).

If the message is ambiguous or does not clearly shift the state, keep the
user's previous state (supplied in the user message under "Previous mental
state") with a lower confidence.

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
- **Key deduplication**: If the user message lists "Existing profile fields", and your extracted entity is semantically equivalent to one of them, you MUST reuse that exact field name instead of inventing a new key. Only create a new dynamic key if no existing field covers the same concept. If no existing fields are listed, use descriptive key names for ad-hoc entities.
- Return an empty dict {{}} for entities if none are found.
- Use the current subagent and the last question asked (if provided) to resolve
  follow-up or ambiguous messages (e.g. a one-word answer like "welder" after "what trade
  do you work in?" should be classified as a profile answer, not an unknown intent).
- Never include keys outside the four specified (intent, entities, sentiment, confidence).{user_state_section}"""

# Per-turn dynamic snippet emitted in the user message, NOT the cached system prompt.
_PROFILE_KEYS_USER_LINE = "Existing profile fields: [{keys}]"


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

        # GH-195 — prompt caching. Enabled by default; can be disabled per-domain
        # (e.g. for debugging or for models that don't benefit from caching).
        self._prompt_cache_enabled: bool = bool(
            nlu_config.get("prompt_cache_enabled", True)
        )

        # GH-218 — opt-in INFO log with the parsed NLU response JSON and the
        # final composed user message. Off by default because the values can
        # carry PII; turn on per-domain for triage windows.
        self._log_raw_response: bool = bool(nlu_config.get("log_raw_response", False))
        self._log_raw_response_max_chars: int = int(
            nlu_config.get("log_raw_response_max_chars", 2000)
        )

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
            # ------------------------------------------------------------------
            # Build the STATIC system prompt (GH-195 — prompt-cache fix).
            # Everything below this comment must be derived from values fixed
            # at startup (config + allowed_intents from the current subagent).
            # Per-turn dynamic values (existing_profile_keys, previous_user_state)
            # are intentionally NOT interpolated here; they're emitted in the
            # user message so the cache prefix remains stable across turns.
            # ------------------------------------------------------------------
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
                    states_block="\n".join(state_lines),
                )
            else:
                user_state_section = ""

            system_prompt_text = _NLU_SYSTEM_PROMPT_TEMPLATE.format(
                domain_instruction=self._domain_instruction,
                intents=", ".join(intents),
                entities=", ".join(self._global_entities),
                sentiment_classes=", ".join(self._sentiment_classes),
                user_state_section=user_state_section,
            )

            # Wrap the static prompt as an Anthropic cache-control block.
            # Passing a list with an explicit cache_control marker avoids
            # depending on the wrapper's implicit size-threshold behaviour
            # and makes the intent visible at the call site.
            if self._prompt_cache_enabled:
                system_payload: str | list[dict] = [
                    {
                        "type": "text",
                        "text": system_prompt_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                system_payload = system_prompt_text

            # Build NLU context message with workflow, last-question grounding,
            # and the per-turn dynamic values that previously lived in the
            # system prompt (GH-195).
            context_parts: list[str] = []
            if current_subagent_id:
                context_parts.append(f"Current workflow step: {current_subagent_id}")
            if current_question:
                context_parts.append(f"Last question asked: {current_question}")
            if existing_profile_keys:
                context_parts.append(
                    _PROFILE_KEYS_USER_LINE.format(
                        keys=", ".join(existing_profile_keys)
                    )
                )
            if self._user_state_enabled:
                context_parts.append(
                    "Previous mental state: "
                    f"{previous_user_state or self._user_state_default}"
                )
            context_parts.append(f"User message: {normalised_input}")

            user_message_text: str = "\n".join(context_parts)
            messages: list[dict] = [
                {"role": "user", "content": user_message_text}
            ]

            llm_response = llm.call(
                messages=messages,
                tools=[],
                system=system_payload,
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

            # GH-218: emit a structured triage log alongside the existing
            # success log. PII-safe by default — entity keys only, message
            # length + hash. The full parsed response and user message are
            # included only when ``log_raw_response`` is True (opt-in).
            user_msg_bytes = user_message_text.encode("utf-8")
            user_msg_sha12 = hashlib.sha256(user_msg_bytes).hexdigest()[:12]
            user_state_id = (
                user_state_obj.id if user_state_obj is not None else None
            )
            user_state_conf = (
                user_state_obj.confidence if user_state_obj is not None else None
            )
            triage_extra: dict[str, Any] = {
                "operation": "nlu_processor.process",
                "status": "success",
                "intent": result.intent,
                "sentiment": result.sentiment,
                "confidence": result.confidence,
                "entity_keys": sorted(result.entities.keys()),
                "user_state_id": user_state_id,
                "user_state_confidence": user_state_conf,
                "user_message_chars": len(user_message_text),
                "user_message_sha256_prefix": user_msg_sha12,
                "raw_response_chars": len(llm_response.content or ""),
                "current_subagent_id": current_subagent_id or None,
            }
            if self._log_raw_response:
                cap = self._log_raw_response_max_chars
                try:
                    parsed_json = json.dumps(parsed, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    parsed_json = repr(parsed)
                triage_extra["parsed_response"] = (
                    parsed_json if cap == 0 else parsed_json[:cap]
                )
                triage_extra["user_message"] = (
                    user_message_text if cap == 0 else user_message_text[:cap]
                )
            logger.info("nlu_processor.triage", extra=triage_extra)

            logger.info(
                "nlu_processor.process",
                extra={
                    "operation": "nlu_processor.process",
                    "status": "success",
                    "intent": result.intent,
                    "sentiment": result.sentiment,
                    "confidence": result.confidence,
                    "latency_ms": int((time.time() - start) * 1000),
                    # GH-195 — surface prompt-cache usage so ops can verify the
                    # fix is effective (cache_read_input_tokens > 0 from turn 2).
                    "cache_read_input_tokens": getattr(
                        llm_response, "cache_read_input_tokens", 0
                    ),
                    "cache_creation_input_tokens": getattr(
                        llm_response, "cache_creation_input_tokens", 0
                    ),
                    "prompt_cache_enabled": self._prompt_cache_enabled,
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
