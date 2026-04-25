"""
agent_core/orchestrator.py

Concrete implementation of AgentCoreBase.
Wires all components and executes the turn sequence.

Design rules enforced here:
- Trust Layer is called exactly twice per turn (input + output). Neither is skippable.
- Agent Core holds zero session state between turns.
- Language Normalisation and NLU Processor run directly in Agent Core (steps 4-5)
  using the primary LLM wrapper with a model_override to Haiku.
- NLU receives scoped intent set: current_subagent.valid_intents + workflow.global_intents.
- SubAgent routing is deterministic: intent + optional session conditions → next_subagent_id.
- Special handlers (hitl, whatsapp_handoff) bypass LLM inference entirely.
- Steps 12-13 (memory write, learning emit) run in a daemon thread after TurnResult is returned.
- This is the only file that imports and coordinates all DPG interfaces together.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Optional

from src.base import AgentCoreBase
from src.exceptions import ToolUseRequested
from src.interfaces.action_gateway import ActionGatewayBase
from src.interfaces.async_.action_gateway import AsyncActionGatewayBase
from src.interfaces.async_.knowledge_engine import AsyncKnowledgeEngineBase
from src.interfaces.async_.memory_layer import AsyncMemoryLayerBase
from src.interfaces.async_.observability_layer import AsyncObservabilityLayerBase
from src.interfaces.async_.trust_layer import AsyncTrustLayerBase
from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.interfaces.observability_layer import ObservabilityLayerBase
from src.interfaces.memory_layer import MemoryLayerBase
from src.interfaces.reach_layer import ReachLayerBase
from src.interfaces.trust_layer import TrustLayerBase
from src.http_clients.trust_layer import TrustLayerConstraintError
from src.preprocessing.language_normalisation import LanguageNormaliser
from src.llm_wrapper.base import LLMWrapperBase
from src.manager_agent import ManagerAgent
from src.models import (
    DoneEvent,
    LLMResponse,
    NLUResult,
    SentenceEvent,
    SignalEvent,
    StreamEvent,
    ToolCall,
    ToolResult,
    TrustCheckResult,
    TurnEvent,
    TurnInput,
    TurnResult,
)
from src.preprocessing.nlu_processor import NLUProcessor
from src.tool_registry import ToolRegistry
from src.workflow_loader import AgentWorkflow, RoutingCondition, RoutingRule, SubAgent
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

logger = logging.getLogger(__name__)

# Module-level guard to prevent double-instrumentation in test environments.
_HTTPX_INSTRUMENTED = False


class AgentCore(AgentCoreBase):
    """
    Stateless orchestrator. Holds references to injected components only —
    no session-scoped data stored as instance state.

    All components are injected at construction time. The startup entrypoint
    (main.py or equivalent) is the only place that instantiates and wires them.

    Args:
        config:           Domain configuration dict.
        llm_wrapper:      LLM inferencing interface.
        memory:           Memory Layer interface.
        trust:            Trust Layer interface.
        knowledge_engine: Knowledge Engine interface.
        tool_registry:    Pre-built tool registry (initialised at startup).
        manager_agent:    Prompt assembly + tool-use loop handler.
        learning:         Observability Layer interface (async emit).
        workflow:         Pre-parsed and validated AgentWorkflow loaded at startup.
    """

    def __init__(
        self,
        config: dict,
        llm_wrapper: LLMWrapperBase,
        memory: MemoryLayerBase,
        trust: TrustLayerBase,
        knowledge_engine: KnowledgeEngineBase,
        tool_registry: ToolRegistry,
        manager_agent: ManagerAgent,
        learning: ObservabilityLayerBase,
        workflow: AgentWorkflow,
        async_memory: AsyncMemoryLayerBase | None = None,
        async_trust: AsyncTrustLayerBase | None = None,
        async_knowledge_engine: AsyncKnowledgeEngineBase | None = None,
        async_gateway: AsyncActionGatewayBase | None = None,
        async_learning: AsyncObservabilityLayerBase | None = None,
    ) -> None:
        if config is None:
            raise ValueError("config must not be None")
        if workflow is None:
            raise ValueError("workflow must not be None")

        self._config = config
        self._llm = llm_wrapper
        self._memory = memory
        self._trust = trust
        self._knowledge_engine = knowledge_engine
        self._tool_registry = tool_registry
        self._manager_agent = manager_agent
        self._learning = learning
        self._workflow = workflow

        # Session-end signal (GH-137) — optional, opt-in per domain.
        session_end_cfg = (self._config or {}).get("conversation", {}).get("session_end_eval", {}) or {}
        self._session_end_eval_enabled: bool = bool(session_end_cfg.get("enabled", False))
        self._session_end_eval_prompt: str = str(session_end_cfg.get("prompt", "") or "")

        if self._session_end_eval_enabled:
            # Register end_session as an internal tool routed to the orchestrator
            # (no external executor — intercepted by manager_agent's tool loop).
            end_session_def = {
                "name": "end_session",
                "description": (
                    "Call when the conversation has naturally concluded (user said "
                    "goodbye, task completed, user asked to stop). Emits the session-"
                    "end signal to runtime; still include your natural final response "
                    "text alongside this tool call."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "enum": [
                                "user_goodbye",
                                "task_complete",
                                "user_requested_stop",
                                "other",
                            ],
                        },
                    },
                    "required": ["reason"],
                },
            }
            try:
                self._tool_registry.register_internal(
                    name="end_session",
                    route="orchestrator",
                    description=end_session_def["description"],
                    input_schema=end_session_def["input_schema"],
                )
            except AttributeError:
                # Tolerate mock registries in tests that don't implement the method.
                pass
            # Ensure every subagent's scoped tool list includes end_session,
            # plus the shared global_tool_defs list if the domain uses it.
            try:
                tool_defs = getattr(self._workflow, "tool_defs", None)
                if isinstance(tool_defs, dict):
                    for _sa_id, _tools in list(tool_defs.items()):
                        if not isinstance(_tools, list):
                            continue
                        if not any(t.get("name") == "end_session" for t in _tools):
                            _tools.append(end_session_def)
                global_defs = getattr(self._workflow, "global_tool_defs", None)
                if isinstance(global_defs, list) and global_defs:
                    if not any(t.get("name") == "end_session" for t in global_defs):
                        global_defs.append(end_session_def)
            except Exception as _err:  # defensive — never break init
                logger.warning(
                    "orchestrator.end_session_tool_defs_extension_failed",
                    extra={
                        "operation": "orchestrator.init",
                        "status": "failure",
                        "error": f"{type(_err).__name__}: {_err}",
                    },
                )

        # Async clients for stream_turn() — optional, only needed for streaming
        self._async_memory = async_memory
        self._async_trust = async_trust
        self._async_knowledge_engine = async_knowledge_engine
        self._async_gateway = async_gateway
        self._async_learning = async_learning

        # Language Normalisation and NLU run directly in Agent Core.
        # Stateless — instantiated once, reused across all sessions.
        self._language_normaliser = LanguageNormaliser()
        self._nlu_processor = NLUProcessor(self._config)

        # User-state model (GH-139) — cached lookup for per-turn guidance injection.
        usm = (self._config or {}).get("conversation", {}).get("user_state_model", {}) or {}
        self._user_state_enabled: bool = bool(usm.get("enabled", False))
        if self._user_state_enabled:
            self._user_state_guidance_by_id: dict[str, str] = {
                (s.get("id", "")): (s.get("guidance", "") or "")
                for s in (usm.get("states") or [])
                if s.get("id")
            }
            self._user_state_default: str = usm.get("default_state", "")
        else:
            self._user_state_guidance_by_id = {}
            self._user_state_default = ""

        # Instrument HTTPX once per process so all downstream HTTP calls are
        # automatically traced as child spans of orchestrator.turn.
        global _HTTPX_INSTRUMENTED
        if not _HTTPX_INSTRUMENTED:
            try:
                HTTPXClientInstrumentor().instrument()
                _HTTPX_INSTRUMENTED = True
            except Exception as e:
                logger.warning(
                    "orchestrator.httpx_instrumentation_failed",
                    extra={
                        "operation": "orchestrator.init",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    # ------------------------------------------------------------------
    # Public interface — single entry point
    # ------------------------------------------------------------------

    def process_turn(self, turn_input: TurnInput) -> TurnResult:
        """
        Execute one full conversation turn. See AgentCoreBase for full contract.

        Implements the 13-step per-turn sequence driven by the AgentWorkflow.

        Args:
            turn_input: Normalised inbound message from the Reach Layer.

        Returns:
            TurnResult delivered to the Reach Layer. Async steps (12-13) run
            in a daemon thread after this returns.

        Raises:
            ValueError: If turn_input is None, session_id is empty, or
                        user_message is None.
        """
        if turn_input is None:
            raise ValueError("turn_input must not be None")
        if not turn_input.session_id:
            raise ValueError("turn_input.session_id must not be empty")
        if turn_input.user_message is None:
            raise ValueError("turn_input.user_message must not be None")

        _tracer = otel_trace.get_tracer(__name__)
        with _tracer.start_as_current_span("orchestrator.turn") as _span:
            return self._process_turn_inner(turn_input, _span)

    def _process_turn_inner(self, turn_input: TurnInput, _span: otel_trace.Span) -> TurnResult:
        """Execute the instrumented turn body inside the orchestrator.turn span.

        Args:
            turn_input: Validated inbound message from the Reach Layer.
            _span:      Active OTel span to attach attributes to.

        Returns:
            TurnResult delivered to the Reach Layer.
        """
        start = time.time()
        session_id = turn_input.session_id
        # PoC fallback: use session_id as user_id if caller didn't provide one
        user_id: str = turn_input.user_id or session_id
        turn_id = str(uuid.uuid4())

        # Attach span attributes and extract trace_id for TurnEvent propagation.
        _span.set_attribute("session_id", session_id)
        _span.set_attribute("turn_id", turn_id)
        _span.set_attribute("user_id", getattr(turn_input, "user_id", "") or "")
        _span.set_attribute(
            "dpg.domain",
            self._config.get("observability", {}).get("domain", "unknown"),
        )
        _trace_id: str = self._current_trace_id()

        logger.info(
            "orchestrator.turn_start",
            extra={
                "operation": "orchestrator.process_turn",
                "status": "success",
                "session_id": session_id,
                "channel": turn_input.channel,
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  TURN START  session=%s  channel=%s\n"
            "  input: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, turn_input.channel, turn_input.user_message[:120],
        )
        # Validate channel before any memory read or LLM call — unsupported
        # channels must fail fast without consuming LLM resources.
        channel_config = self._resolve_channel_config(turn_input.channel)

        # ── Step 1: Read session state ────────────────────────────────
        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        logger.info(
            "  [STEP 1] Memory context_bundle  →  POST %s/context_bundle  (session=%s)",
            memory_endpoint, session_id,
        )
        t1 = time.time()
        bundle = self._memory.context_bundle(session_id, user_id, adopt=not turn_input.fresh)
        current_subagent_id: str = (
            bundle.session.get("current_subagent_id")
            or self._workflow.start_subagent_id
        )
        current_question: str = bundle.session.get("current_question", "")
        logger.info(
            "  [STEP 1] Memory context_bundle  ✓  current_subagent_id=%s"
            "  is_returning=%s  latency=%dms",
            current_subagent_id,
            bundle.session.get("is_returning", False),
            int((time.time() - t1) * 1000),
        )

        # ── Step 4: Language Normalisation ───────────────────────────
        # Runs before the consent gate so the detected language is available
        # to translate the consent prompt on Turn 1.
        lang_model = (
            self._config.get("preprocessing", {})
            .get("language_normalisation", {})
            .get("model_override", "haiku")
        )
        logger.info(
            "  [STEP 4] Language Normalisation  →  LLM call (model_override=%s)",
            lang_model,
        )
        t4 = time.time()
        normalised_input, turn_language = self._language_normaliser.normalise(
            raw_input=turn_input.user_message,
            config=self._config,
            llm=self._llm,
        )

        # Determine language preference — lock it in if not already set
        profile_data = bundle.profile if bundle.profile is not None else {}
        session_data = bundle.session if bundle.session is not None else {}

        default_language = (
            self._config.get("preprocessing", {})
            .get("language_normalisation", {})
            .get("default_language", "hindi")
        )
        language_preference = (
            profile_data.get("language_preference") or
            session_data.get("language_preference") or
            turn_language or
            default_language
        )

        # Lock in language_preference on the first turn only.
        # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
        saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
        if not saved_preference:
            pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
            self._write_memory_sync(session_id, user_id, pref_scope, "language_preference", language_preference)
            bundle.session["language_preference"] = language_preference

        logger.info(
            "  [STEP 4] Language Normalisation  ✓  detected=%s  preference=%s  normalised=%r  latency=%dms",
            turn_language or "—",
            language_preference,
            (normalised_input or turn_input.user_message)[:100],
            int((time.time() - t4) * 1000),
        )
        # Use preference for the rest of the turn logic
        detected_language = language_preference

        # ── Consent gate (Step 1b) ────────────────────────────────────
        ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
        if ask_for_consent:
            user_storage_mode: str | None = bundle.session.get("user_storage_mode")
            turn_count: int = int(bundle.session.get("turn_count", 0) or 0)

            if user_storage_mode is None and turn_count == 0:
                # Turn 1: deliver consent prompt (translated to user's language),
                # no LLM inference, no Trust Layer call.
                # Stash the user's original message and its normalised form so the
                # next turn can replay them after consent is evaluated — otherwise
                # the user's first real input would be silently dropped.
                consent_prompt_text: str = self._config.get("agent", {}).get("consent_prompt", "")
                logger.info(
                    "orchestrator.consent_gate",
                    extra={
                        "operation": "orchestrator.consent_gate",
                        "status": "prompt_delivered",
                        "session_id": session_id,
                    },
                )
                self._write_memory_sync(session_id, user_id, "session", "turn_count", 1)
                self._write_memory_sync(
                    session_id, user_id, "session",
                    "pending_user_message", turn_input.user_message,
                )
                self._write_memory_sync(
                    session_id, user_id, "session",
                    "pending_normalised_input", normalised_input or turn_input.user_message,
                )
                consent_response_text = self._translate_consent_message(consent_prompt_text, detected_language)
                consent_latency_ms = int((time.time() - start) * 1000)
                logger.info(
                    "\n═══════════════════════════════════════════════════════════════\n"
                    "  TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
                    "  model=%s  total_latency=%dms  next_subagent=%s\n"
                    "  response: %r\n"
                    "═══════════════════════════════════════════════════════════════",
                    session_id, "consent_prompt", False,
                    "none", consent_latency_ms, "consent_gate",
                    consent_response_text.strip()[:200],
                )
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=consent_response_text,
                    latency_ms=consent_latency_ms,
                )

            if user_storage_mode is None and turn_count > 0:
                # Turn 2: evaluate response, write storage mode, continue to workflow
                granted: bool = self._trust.verify_consent(session_id, turn_input.user_message)
                new_storage_mode = "saved" if granted else "anonymous"
                logger.info(
                    "orchestrator.consent_gate",
                    extra={
                        "operation": "orchestrator.consent_gate",
                        "status": "consent_evaluated",
                        "session_id": session_id,
                        "granted": granted,
                        "user_storage_mode": new_storage_mode,
                    },
                )
                self._write_memory_sync(session_id, user_id, "session", "user_storage_mode", new_storage_mode)
                bundle.session["user_storage_mode"] = new_storage_mode

                # Replay the original first-turn message as this turn's real input
                # so downstream NLU / routing / LLM act on the user's actual intent
                # rather than on the word "yes"/"no".
                pending_msg = bundle.session.get("pending_user_message") or ""
                pending_norm = bundle.session.get("pending_normalised_input") or ""
                if pending_msg:
                    turn_input.user_message = pending_msg
                    normalised_input = pending_norm or pending_msg
                    self._write_memory_sync(session_id, user_id, "session", "pending_user_message", "")
                    self._write_memory_sync(session_id, user_id, "session", "pending_normalised_input", "")
                    logger.info(
                        "orchestrator.consent_gate",
                        extra={
                            "operation": "orchestrator.consent_gate",
                            "status": "pending_message_replayed",
                            "session_id": session_id,
                        },
                    )
            # if user_storage_mode is set → fall through, skip consent gate entirely

        # ── Opening-phrase gate (Step 1c, GH-137) ────────────────────────
        # Emit the current subagent's opening_phrase exactly once per session,
        # on the first post-consent turn. Subsequent turns skip this check.
        if not bundle.session.get("opening_phrase_emitted", False):
            current_sa = self._workflow.subagents.get(current_subagent_id)
            opening_phrase = (getattr(current_sa, "opening_phrase", "") or "").strip()

            # Always set the flag so we don't re-check every turn.
            self._write_memory_sync(session_id, user_id, "session", "opening_phrase_emitted", True)

            if opening_phrase:
                # Ensure current_subagent_id is persisted so next turn has it.
                self._write_memory_sync(session_id, user_id, "session", "current_subagent_id", current_subagent_id)
                logger.info(
                    "orchestrator.opening_phrase_emitted",
                    extra={
                        "operation": "orchestrator.opening_phrase_gate",
                        "status": "emitted",
                        "session_id": session_id,
                        "subagent_id": current_subagent_id,
                    },
                )
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=opening_phrase,
                    latency_ms=int((time.time() - start) * 1000),
                )
            # else: empty opening_phrase — flag is set; fall through to normal turn.

        # ── Step 2: Resolve current subagent + special handler ────────
        current_subagent: SubAgent = self._workflow.subagents[current_subagent_id]
        logger.info(
            "  [STEP 2] Resolved subagent=%s (%s)  special_handler=%s",
            current_subagent.id, current_subagent.name,
            current_subagent.special_handler or "none",
        )

        if current_subagent.special_handler:
            # Perform the Trust check on input before executing the special handler
            # so the Trust Layer's "exactly twice per turn" contract is honoured.
            trust_input = self._trust.check_input(session_id, turn_input.user_message)
            if trust_input.action == "block":
                return self._blocked_response(session_id, trust_input, start, trust_input, turn_id, intent="unknown", user_id=user_id, user_message=turn_input.user_message)
            if trust_input.action == "escalate":
                self._schedule_flush(session_id, user_id, "escalation_trust_input")
                return self._escalated_response(session_id, trust_input, start, trust_input, turn_id, intent="unknown", user_id=user_id, user_message=turn_input.user_message)
            return self._handle_special(
                handler=current_subagent.special_handler,
                current_subagent=current_subagent,
                session_id=session_id,
                user_id=user_id,
                bundle=bundle,
                turn_input=turn_input,
                start=start,
                trust_input=trust_input,
                turn_id=turn_id,
                intent="special_handler",
            )

        # ── Step 3: Trust check on input ─────────────────────────────
        trust_endpoint = (
            self._config.get("trust_client", {}).get("endpoint", "http://trust_layer:8003")
        )
        logger.info(
            "  [STEP 3] Trust Input Check  →  POST %s/check/input  (session=%s)",
            trust_endpoint, session_id,
        )
        t3 = time.time()
        trust_input = self._trust.check_input(session_id, turn_input.user_message)
        logger.info(
            "  [STEP 3] Trust Input Check  ✓  action=%s  passed=%s  reason=%s  latency=%dms",
            trust_input.action, trust_input.passed,
            trust_input.reason or "—", int((time.time() - t3) * 1000),
        )

        if trust_input.action == "block":
            logger.info(
                "  [STEP 3] INPUT BLOCKED — reason=%s  →  returning blocked response",
                trust_input.reason,
            )
            return self._blocked_response(session_id, trust_input, start, trust_input, turn_id, intent="unknown", user_id=user_id, user_message=turn_input.user_message)

        if trust_input.action == "escalate":
            logger.info(
                "  [STEP 3] INPUT ESCALATED — reason=%s  →  routing to human agent",
                trust_input.reason,
            )
            self._schedule_flush(session_id, user_id, "escalation_trust_input")
            return self._escalated_response(session_id, trust_input, start, trust_input, turn_id, intent="unknown", user_id=user_id, user_message=turn_input.user_message)

        # Step 4 (Language Normalisation) has been moved to run before the consent
        # gate so that detected_language is available when translating the consent
        # prompt on Turn 1.  The variables normalised_input, turn_language,
        # language_preference, and detected_language are already set above.

        # ── Step 5: NLU Processor ─────────────────────────────────────
        allowed_intents = self._workflow.nlu_intent_set.get(current_subagent_id, [])
        nlu_model = (
            self._config.get("preprocessing", {})
            .get("nlu_processor", {})
            .get("model_override", "haiku")
        )

        # Collect existing profile keys (declared + ad-hoc) so the NLU prompt
        # can instruct the LLM to reuse them instead of inventing synonyms.
        profile_data = bundle.profile or {}
        existing_profile_keys: list[str] = [
            k for k in profile_data if k != "attributes"
        ]
        for attr in profile_data.get("attributes", []):
            attr_key = attr.get("key", "") if isinstance(attr, dict) else ""
            if attr_key:
                existing_profile_keys.append(attr_key)

        previous_user_state_payload: dict | None = None
        previous_user_state_id: str | None = None
        if self._user_state_enabled:
            maybe = bundle.session.get("user_state")
            if isinstance(maybe, dict):
                previous_user_state_payload = maybe
                previous_user_state_id = maybe.get("id")
            if previous_user_state_id is None:
                previous_user_state_id = self._user_state_default

        logger.info(
            "  [STEP 5] NLU Processor  →  LLM call (model_override=%s)"
            "  current_subagent_id=%s  allowed_intents=%d  current_question=%r"
            "  existing_profile_keys=%d",
            nlu_model, current_subagent_id, len(allowed_intents),
            current_question[:60] if current_question else "",
            len(existing_profile_keys),
        )
        t5 = time.time()
        nlu_result = self._nlu_processor.process(
            normalised_input=normalised_input,
            current_question=current_question,
            current_subagent_id=current_subagent_id,
            llm=self._llm,
            allowed_intents=allowed_intents,
            existing_profile_keys=existing_profile_keys,
            previous_user_state=previous_user_state_id,
        )
        logger.info(
            "  [STEP 5] NLU Processor  ✓  intent=%s  confidence=%.2f  entities=%s"
            "  sentiment=%s  latency=%dms",
            nlu_result.intent, nlu_result.confidence,
            nlu_result.entities if nlu_result.entities else {},
            nlu_result.sentiment,
            int((time.time() - t5) * 1000),
        )

        user_state_guidance_text = self._handle_user_state_turn(
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            bundle=bundle,
            nlu_result=nlu_result,
            previous_state_id=previous_user_state_id,
            previous_payload=previous_user_state_payload,
            span=_span,
        )
        
        # After NLU: write extracted entities synchronously so routing in Step 6
        # sees current-turn values, not only last-turn state.
        # Scope is config-driven — entity_persistence.scope in domain config.
        # DPDP compliance is handled by Memory Layer at flush_session(): all entities
        # are written to Neo4j during the session; if user_storage_mode == "anonymous"
        # the Memory Layer DETACH DELETEs the user graph when the session ends.
        entity_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
        entity_map: dict = self._config.get("entity_to_profile_field", {})
        supported_langs: set[str] = {
            l.lower() for l in self._config.get("preprocessing", {})
            .get("language_normalisation", {})
            .get("supported_languages", [])
        }
        for entity_key, entity_val in (nlu_result.entities or {}).items():
            profile_field = entity_map.get(entity_key, entity_key)
            # Guard: never persist language_preference with a value outside the
            # configured supported_languages list. The language_switch_request
            # branch below (or the unsupported-language response) handles the
            # user-facing case; skipping here prevents profile pollution.
            if profile_field == "language_preference" and supported_langs:
                if str(entity_val).lower().strip() not in supported_langs:
                    continue
            self._write_memory_sync(session_id, user_id, entity_scope, profile_field, entity_val)
            bundle.session[profile_field] = entity_val

        # Write context graph signal if this intent is configured as a signal-producing intent.
        # Captures objections, emotions, and constraints for longitudinal analysis.
        signal_intents: dict = (
            self._config.get("preprocessing", {})
            .get("nlu_processor", {})
            .get("signal_intents", {})
        )
        if nlu_result.intent and nlu_result.intent in signal_intents:
            signal_type = signal_intents[nlu_result.intent]
            turn_count_for_signal = int(bundle.session.get("turn_count", 0) or 0)
            try:
                self._write_memory_sync(
                    session_id, user_id, "signal",
                    "signal",
                    {
                        "type": signal_type,
                        "turn": str(turn_count_for_signal),
                        "raw": turn_input.user_message,
                        "journey_id": session_id,
                    },
                )
            except Exception as _sig_err:
                logger.warning(
                    "orchestrator.signal_write_failed",
                    extra={
                        "operation": "orchestrator.signal_write",
                        "status": "failure",
                        "session_id": session_id,
                        "intent": nlu_result.intent,
                        "error": str(_sig_err),
                    },
                )

        # ── Language switch — handle before routing ───────────────────────
        if nlu_result.intent == "language_switch_request":
            lang_cfg = (
                self._config.get("preprocessing", {})
                .get("language_normalisation", {})
            )
            supported = [
                l.lower() for l in lang_cfg.get("supported_languages", [])
            ]
            requested_lang = (
                (nlu_result.entities or {}).get("language_preference") or ""
            ).lower().strip()

            if requested_lang and requested_lang in supported:
                # Profile write already happened in the entity loop above for
                # supported values; mirror into bundle.session and flip the
                # active detected_language for this turn's prompt.
                bundle.session["language_preference"] = requested_lang
                detected_language = requested_lang
                logger.info(
                    "orchestrator.language_switched",
                    extra={
                        "operation": "orchestrator.language_switch",
                        "status": "success",
                        "session_id": session_id,
                        "language_preference": requested_lang,
                    },
                )
            else:
                supported_names = lang_cfg.get("supported_languages", [])
                if supported_names:
                    default_msg = f"I can only respond in: {', '.join(supported_names)}."
                else:
                    default_msg = "That language is not supported."
                msg = self._config.get("conversation", {}).get(
                    "unsupported_language_message", default_msg
                )
                logger.info(
                    "orchestrator.language_switch_rejected",
                    extra={
                        "operation": "orchestrator.language_switch",
                        "status": "skipped",
                        "session_id": session_id,
                        "language_preference": requested_lang,
                        "reason": "not_in_supported_languages",
                    },
                )
                latency_ms = int((time.time() - start) * 1000)
                _trace_id = self._current_trace_id()
                turn_event = TurnEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=msg,
                    tool_calls=[],
                    trust_input_result=trust_input,
                    trust_output_result=TrustCheckResult(passed=True, action="allow"),
                    model_used="",
                    intent=nlu_result.intent,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    timestamp_ms=int(time.time() * 1000),
                    trace_id=_trace_id,
                )
                thread = threading.Thread(
                    target=self._post_turn,
                    args=(session_id, user_id, turn_id, msg, turn_input.user_message, turn_event, False, ""),
                    daemon=True,
                )
                thread.start()
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=msg,
                    was_escalated=False,
                    latency_ms=latency_ms,
                )

        # ── Step 6: Routing — determine next_subagent_id ─────────────
        logger.info(
            "  [STEP 6] Routing  →  intent=%s  current_subagent=%s",
            nlu_result.intent, current_subagent_id,
        )
        # Merge profile into session for routing evaluations
        routing_state = dict(bundle.session)
        if bundle.profile:
            routing_state.update(bundle.profile)

        next_subagent_id, matched_rule = self._resolve_next_subagent(
            current_subagent=current_subagent,
            nlu_result=nlu_result,
            session=routing_state,
        )

        # Apply session_writes from the matched routing rule, if any.
        # Allows domain.yaml rules to write arbitrary session state
        # (e.g. user_storage_mode) without any domain logic in the orchestrator.
        if matched_rule and matched_rule.session_writes:
            for field_name, field_val in matched_rule.session_writes.items():
                self._write_memory_sync(session_id, user_id, "session", field_name, field_val)
                bundle.session[field_name] = field_val

        # Increment subagent_entry_count for the destination subagent.
        raw_counts = bundle.session.get("subagent_entry_count")
        if isinstance(raw_counts, dict):
            subagent_entry_count = dict(raw_counts)
        else:
            subagent_entry_count = {}

        subagent_entry_count[next_subagent_id] = int(subagent_entry_count.get(next_subagent_id, 0)) + 1
        self._write_memory_sync(
            session_id, user_id, "session", "subagent_entry_count", subagent_entry_count
        )
        bundle.session["subagent_entry_count"] = subagent_entry_count
        bundle.session["current_subagent_id"] = next_subagent_id
        self._write_memory_sync(session_id, user_id, "session", "current_subagent_id", next_subagent_id)

        logger.info(
            "  [STEP 6] Routing  ✓  next_subagent_id=%s  entry_count=%d",
            next_subagent_id,
            subagent_entry_count[next_subagent_id],
        )

        # ── Step 7: Prompt assembly via ManagerAgent ──────────────────
        next_subagent: SubAgent = self._workflow.subagents[next_subagent_id]
        logger.info(
            "  [STEP 7] Prompt Assembly  →  subagent=%s (%s)",
            next_subagent.id, next_subagent.name,
        )
        # Merge collected session fields into profile for LLM grounding context
        profile_context = dict(bundle.profile)
        profile_field_names = set(entity_map.values())
        for k, v in bundle.session.items():
            if k in profile_field_names and v not in (None, "", "[]"):
                profile_context[k] = v

        # Ensure the prompt builder uses the most up-to-date language preference
        # (which might have been updated by NLU in Step 5).
        final_language = profile_context.get("language_preference", detected_language)

        # Check for resumption signal from Memory Layer
        is_resumption = bundle.session.get("was_adopted", False)

        # ── Step 6b: Assemble guardrail constraints (pre-LLM) ─────────
        guardrail_constraints: dict | None = None
        if nlu_result.active_risks:
            try:
                guardrail_constraints = self._trust.assemble_constraints(
                    session_id=session_id,
                    workflow_step=next_subagent_id,
                    active_risks=nlu_result.active_risks,
                    user_segment=bundle.profile.get("user_segment"),
                )
                logger.info(
                    "orchestrator.guardrails_assembled",
                    extra={
                        "operation": "orchestrator.assemble_constraints",
                        "status": "success",
                        "session_id": session_id,
                        "active_risks": nlu_result.active_risks,
                        "constraints_count": len(guardrail_constraints.get("prompt_constraints", [])),
                        "latency_ms": 0,
                    },
                )
            except Exception as e:
                logger.error(
                    "orchestrator.assemble_constraints_failed",
                    extra={
                        "operation": "orchestrator.assemble_constraints",
                        "status": "failure",
                        "session_id": session_id,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                return self._blocked_response(
                    session_id, None, start, None, turn_id,
                    intent="guardrail_unavailable",
                    user_id=user_id,
                    user_message=turn_input.user_message,
                )

        system = self._manager_agent.build_system_prompt(
            agent_system_prompt=self._workflow.agent_system_prompt,
            subagent_system_prompt=next_subagent.system_prompt,
            detected_language=final_language,
            channel=turn_input.channel,
            profile=profile_context,
            channel_config=channel_config,
            is_resumption=is_resumption,
            guardrail_constraints=guardrail_constraints,
            user_state_guidance=user_state_guidance_text,
            session_end_eval_prompt=(
                self._session_end_eval_prompt if self._session_end_eval_enabled else None
            ),
        )

        # Clear resumption flag in session so it only affects the first turn
        if is_resumption:
            bundle.session["was_adopted"] = False
            self._write_memory_sync(session_id, user_id, "session", "was_adopted", False)
        messages = self._manager_agent.build_messages(
            user_message=turn_input.user_message,
            current_question=current_question,
        )

        if not messages:
            logger.warning(
                "orchestrator.empty_messages",
                extra={
                    "operation": "orchestrator.process_turn",
                    "status": "skipped",
                    "session_id": session_id,
                },
            )
            return self._build_result(
                session_id=session_id,
                user_id=user_id,
                response_text="",
                was_escalated=False,
                was_tool_used=False,
                model_used="",
                latency_ms=int((time.time() - start) * 1000),
                turn_input=turn_input,
                turn_id=turn_id,
                intent=nlu_result.intent,
                tool_calls=[],
                trust_input=trust_input,
                trust_output=TrustCheckResult(passed=True, action="allow"),
                trace_id=_trace_id,
            )

        # ── Step 8: LLM call #1 with scoped tools ────────────────────
        active_tools = self._workflow.resolve_tools_for(next_subagent_id)
        output_format = next_subagent.output_format
        primary_model = self._config.get("agent", {}).get("primary_model", "unknown")
        logger.info(
            "  [STEP 8] LLM Call #1  →  Anthropic API (model=%s)"
            "  tools_available=%d  message_count=%d  output_format=%s",
            primary_model, len(active_tools), len(messages),
            "structured" if output_format else "free-form",
        )
        t8 = time.time()
        llm_response = self._llm.call(
            messages=messages,
            tools=active_tools,
            system=system,
            output_format=output_format,
        )
        logger.info(
            "  [STEP 8] LLM Call #1  ✓  stop_reason=%s  model_used=%s"
            "  input_tokens=%d  output_tokens=%d  latency=%dms",
            llm_response.stop_reason, llm_response.model_used,
            llm_response.input_tokens, llm_response.output_tokens,
            int((time.time() - t8) * 1000),
        )
        if llm_response.stop_reason == "tool_use":
            logger.info("  [STEP 8]   → LLM requested tool use — entering tool loop")

        # ── Step 9: Tool-use loop ─────────────────────────────────────
        logger.info(
            "  [STEP 9] Tool-Use Loop  (if tool requested)",
        )
        ke_context = {
            "session_id": session_id,
            "user_message": turn_input.user_message,
            "profile": bundle.profile,
            "session": bundle.session,
            "intent": nlu_result.intent,
            "entities": nlu_result.entities,
            "sentiment": nlu_result.sentiment,
            "confidence": nlu_result.confidence,
            "normalised_input": normalised_input,
            "detected_language": detected_language,
        }
        t9 = time.time()
        final_text, tool_calls, tool_results = self._manager_agent.run_turn(
            messages=messages,
            session_id=session_id,
            initial_llm_response=llm_response,
            system=system,
            active_tools=active_tools,
            ke_context=ke_context,
        )
        if tool_calls:
            tool_names = [tc.tool_name for tc in tool_calls]
            logger.info(
                "  [STEP 9] Tool-Use Loop  ✓  tools_called=%s  latency=%dms",
                tool_names, int((time.time() - t9) * 1000),
            )
        else:
            logger.info(
                "  [STEP 9] Tool-Use Loop  ✓  no tool used — direct LLM response  latency=%dms",
                int((time.time() - t9) * 1000),
            )

        # ── Post-tool hook: apply_job success → post_applied transition ──
        # After the tool-use loop, if any ToolResult for "apply_job" succeeded,
        # move the session to the post_applied subagent for the NEXT turn.
        # The current turn's response was already produced under the commitment
        # subagent's system prompt — that is intentional.
        # Guard ensures the framework stays domain-agnostic: other domains that
        # do not define a post_applied subagent get a no-op.
        if tool_results and "post_applied" in self._workflow.subagents:
            for tr in tool_results:
                if getattr(tr, "tool_name", None) == "apply_job" and getattr(tr, "success", False):
                    self._write_memory_sync(
                        session_id, user_id, "session",
                        "current_subagent_id", "post_applied",
                    )
                    bundle.session["current_subagent_id"] = "post_applied"
                    logger.info(
                        "orchestrator.post_applied_transition",
                        extra={
                            "operation": "orchestrator.post_tool_hook",
                            "status": "success",
                            "session_id": session_id,
                            "trigger_tool": "apply_job",
                        },
                    )
                    break

        # ── Step 10: Trust check on output ────────────────────────────
        logger.info(
            "  [STEP 10] Trust Output Check  →  POST %s/check/output  (session=%s)",
            trust_endpoint, session_id,
        )
        t10 = time.time()
        trust_output = self._trust.check_output(session_id, final_text)
        logger.info(
            "  [STEP 10] Trust Output Check  ✓  action=%s  passed=%s  latency=%dms",
            trust_output.action, trust_output.passed, int((time.time() - t10) * 1000),
        )

        if trust_output.action in ("block", "escalate"):
            # TODO(GH-hitl): When action=="escalate", call self._trust.escalate(...) to
            # queue a HiTL ticket. Currently deferred — tracked in the HiTL queue issue.
            logger.info(
                "  [STEP 10] OUTPUT %s — replacing with safe fallback",
                trust_output.action.upper(),
            )
            final_text = self._safe_fallback_message()

        # ── Step 11: Write current_question synchronously ─────────────
        # Persisted before returning so the next turn has the correct context.
        # #207: sanitize defends against accidental concatenation upstream and
        # caps the value to a sane ceiling.
        cq_value = self._sanitize_current_question(
            prev=bundle.session.get("current_question", ""),
            new=final_text,
            session_id=session_id,
        )
        self._write_memory_sync(session_id, user_id, "session", "current_question", cq_value)
        bundle.session["current_question"] = cq_value

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "  [STEP 11] Delivering response to caller  (async: memory write + learning emit follow)",
        )

        # Flush session when routing to a terminal subagent so Journey nodes get
        # ended_at, end_reason, and merge_on_session_end fields (mental_state_at_end,
        # branch_taken, Role child nodes) written to Neo4j before the session expires.
        _do_flush = next_subagent.is_terminal
        _flush_reason = next_subagent_id if _do_flush else ""

        result = self._build_result(
            session_id=session_id,
            user_id=user_id,
            response_text=final_text,
            was_escalated=trust_output.action == "escalate",
            was_tool_used=bool(tool_calls),
            model_used=llm_response.model_used,
            latency_ms=latency_ms,
            turn_input=turn_input,
            turn_id=turn_id,
            intent=nlu_result.intent,
            tool_calls=tool_calls,
            trust_input=trust_input,
            trust_output=trust_output,
            do_flush=_do_flush,
            flush_reason=_flush_reason,
            trace_id=_trace_id,
            session_ended=bool(getattr(self._manager_agent, "session_ended", False)),
        )

        logger.info(
            "orchestrator.turn_complete",
            extra={
                "operation": "orchestrator.process_turn",
                "status": "success",
                "session_id": session_id,
                "latency_ms": latency_ms,
                "model": llm_response.model_used,
                "tool_used": bool(tool_calls),
                "intent": nlu_result.intent,
                "next_subagent_id": next_subagent_id,
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
            "  model=%s  total_latency=%dms  next_subagent=%s\n"
            "  response: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, nlu_result.intent, bool(tool_calls),
            llm_response.model_used, latency_ms, next_subagent_id,
            final_text[:200],
        )

        return result

    # ------------------------------------------------------------------
    # Private: routing algorithm
    # ------------------------------------------------------------------

    def _resolve_next_subagent(
        self,
        current_subagent: SubAgent,
        nlu_result: NLUResult,
        session: dict,
    ) -> tuple[str, RoutingRule | None]:
        """
        Determine the next subagent id using the 3-pass routing algorithm.

        Pass 1: subagent-level routing rules (ordered, first match wins).
        Pass 2: workflow global_routing rules.
        Pass 3: workflow.default_fallback_subagent_id.

        Args:
            current_subagent: The subagent active at the start of this turn.
            nlu_result:       NLU result with intent and entities.
            session:          Current session state dict.

        Returns:
            Tuple of (next_subagent_id, matched_rule). matched_rule is None
            when the fallback is used (no rule matched).
        """
        intent = nlu_result.intent

        # Pass 1: subagent-level routing
        for rule in current_subagent.routing:
            if rule.intent != intent and rule.intent != "*":
                continue
            if not rule.condition and not rule.conditions:
                return rule.next_subagent_id, rule
            if rule.condition and self._evaluate_condition(rule.condition, session):
                return rule.next_subagent_id, rule
            if rule.conditions and all(
                self._evaluate_condition(c, session) for c in rule.conditions
            ):
                return rule.next_subagent_id, rule

        # Pass 2: global routing
        for rule in self._workflow.global_routing:
            if rule.intent != intent:
                continue
            if not rule.condition and not rule.conditions:
                return rule.next_subagent_id, rule
            if rule.condition and self._evaluate_condition(rule.condition, session):
                return rule.next_subagent_id, rule
            if rule.conditions and all(
                self._evaluate_condition(c, session) for c in rule.conditions
            ):
                return rule.next_subagent_id, rule

        # Pass 3: fallback
        return self._workflow.default_fallback_subagent_id, None

    def _evaluate_condition(self, condition: RoutingCondition, session: dict) -> bool:
        """
        Evaluate a single RoutingCondition against session state.

        For nested field access in subagent_entry_count:
        - "subagent_entry_count.evaluation" resolves to
          session["subagent_entry_count"].get("evaluation", 0).

        Args:
            condition: The condition to evaluate.
            session:   Current session state dict.

        Returns:
            True if the condition is satisfied, False otherwise.
        """
        field = condition.field
        if "." in field:
            parent, child = field.split(".", 1)
            value = session.get(parent, {})
            if isinstance(value, dict):
                value = value.get(child, 0)
            else:
                value = 0
        else:
            value = session.get(field)

        op = condition.operator
        cond_val = condition.value

        if op == "eq":
            return value == cond_val
        if op == "not_eq":
            return value != cond_val
        if op == "in":
            return value in (cond_val if isinstance(cond_val, list) else [cond_val])
        if op == "lt":
            try:
                return float(value or 0) < float(cond_val)
            except (TypeError, ValueError):
                return False
        if op == "gt":
            try:
                return float(value or 0) > float(cond_val)
            except (TypeError, ValueError):
                return False
        return False

    # ------------------------------------------------------------------
    # Private: special handlers
    # ------------------------------------------------------------------

    def _handle_special(
        self,
        handler: str,
        current_subagent: SubAgent,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        turn_input: TurnInput,
        start: float,
        trust_input: TrustCheckResult,
        turn_id: str,
        intent: str,
    ) -> TurnResult:
        """
        Handle subagents with special_handler set — bypasses Steps 3–9 (LLM/tools).

        Trust Layer output check is still applied before returning — CLAUDE.md guideline
        "Trust Layer runs on every I/O pass. Never skip either."

        Args:
            handler: The special_handler string from the subagent config (e.g. "hitl", "whatsapp_handoff").
            current_subagent: The resolved SubAgent with special_handler set.
            session_id: Current session identifier.
            user_id: Current user identifier.
            bundle: Memory context bundle for this turn.
            turn_input: The current turn's input data.
            start: Turn start timestamp for latency calculation.
            trust_input: The Trust Layer input check result.
            turn_id: Unique turn identifier.
            intent: Intent label used for observability logging.

        Returns:
            TurnResult with response text and latency; may have was_escalated=True for hitl/escalation handlers.
        """
        if handler == "hitl":
            hitl_msg = self._config.get("hitl", {}).get(
                "response_message",
                "I'm connecting you with a counsellor who can better assist you.",
            )
            logger.info("  [STEP 2] special_handler=hitl → flushing session")
            trust_output = self._trust.check_output(session_id, hitl_msg)
            if trust_output.action in ("block", "escalate"):
                hitl_msg = self._safe_fallback_message()
            self._schedule_flush(session_id, user_id, "hitl_special_handler")
            latency_ms = int((time.time() - start) * 1000)
            turn_event = TurnEvent(
                session_id=session_id,
                turn_id=turn_id,
                response_text=hitl_msg,
                tool_calls=[],
                trust_input_result=trust_input,
                trust_output_result=trust_output,
                model_used="",
                intent=intent,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                timestamp_ms=int(time.time() * 1000),
                trace_id=self._current_trace_id(),
            )
            # NOTE: daemon thread means audit write may be lost on abrupt process exit.
            thread = threading.Thread(
                target=self._post_turn,
                args=(session_id, user_id, turn_id, hitl_msg, turn_input.user_message, turn_event, False, ""),
                daemon=True,
            )
            thread.start()
            return TurnResult(
                session_id=session_id,
                turn_id=turn_id,
                response_text=hitl_msg,
                was_escalated=True,
                latency_ms=latency_ms,
            )

        if handler == "whatsapp_handoff":
            handoff_msg = self._config.get("messages", {}).get(
                "whatsapp_handoff",
                "We're sending you a WhatsApp message with all the details.",
            )
            logger.info("  [STEP 2] special_handler=whatsapp_handoff")
            trust_output = self._trust.check_output(session_id, handoff_msg)
            if trust_output.action in ("block", "escalate"):
                handoff_msg = self._safe_fallback_message()
            self._schedule_flush(session_id, user_id, "whatsapp_handoff")
            latency_ms = int((time.time() - start) * 1000)
            turn_event = TurnEvent(
                session_id=session_id,
                turn_id=turn_id,
                response_text=handoff_msg,
                tool_calls=[],
                trust_input_result=trust_input,
                trust_output_result=trust_output,
                model_used="",
                intent=intent,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                timestamp_ms=int(time.time() * 1000),
                trace_id=self._current_trace_id(),
            )
            # NOTE: daemon thread means audit write may be lost on abrupt process exit.
            thread = threading.Thread(
                target=self._post_turn,
                args=(session_id, user_id, turn_id, handoff_msg, turn_input.user_message, turn_event, False, ""),
                daemon=True,
            )
            thread.start()
            return TurnResult(
                session_id=session_id,
                turn_id=turn_id,
                response_text=handoff_msg,
                was_escalated=False,
                latency_ms=latency_ms,
            )

        # Unknown handler — log and return a safe fallback.
        logger.error(
            "orchestrator.unknown_special_handler",
            extra={"session_id": session_id, "handler": handler},
        )
        fallback_msg = self._config.get("conversation", {}).get(
            "unknown_intent_message",
            "I didn't quite understand that. Could you tell me more?",
        )
        latency_ms = int((time.time() - start) * 1000)
        turn_event = TurnEvent(
            session_id=session_id,
            turn_id=turn_id,
            response_text=fallback_msg,
            tool_calls=[],
            trust_input_result=trust_input,
            trust_output_result=TrustCheckResult(passed=True, action="allow"),
            model_used="",
            intent=intent,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            timestamp_ms=int(time.time() * 1000),
            trace_id=self._current_trace_id(),
        )
        # NOTE: daemon thread means audit write may be lost on abrupt process exit.
        thread = threading.Thread(
            target=self._post_turn,
            args=(session_id, user_id, turn_id, fallback_msg, turn_input.user_message, turn_event, False, ""),
            daemon=True,
        )
        thread.start()
        return TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=fallback_msg,
            was_escalated=False,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Private: blocked / escalated / unknown early exits
    # ------------------------------------------------------------------

    def _blocked_response(
        self,
        session_id: str,
        trust_result: Optional[TrustCheckResult],
        start: float,
        trust_input: TrustCheckResult,
        turn_id: str,
        intent: str,
        user_id: str = "",
        user_message: str = "",
    ) -> TurnResult:
        """
        Build a TurnResult for input that was blocked by the Trust Layer.

        Args:
            session_id:   Session identifier.
            trust_result: The blocking TrustCheckResult.
            start:        Turn start timestamp.
            trust_input:  Same as trust_result for input blocks (kept for API symmetry).
            turn_id:      Unique identifier for this turn.
            intent:       NLU intent (or "unknown" for early-exit paths).
            user_id:      User identifier for audit recording.
            user_message: Original user message for audit recording.

        Returns:
            TurnResult with the configured blocked message.
        """
        logger.warning(
            "orchestrator.input_blocked",
            extra={
                "operation": "orchestrator.process_turn",
                "status": "skipped",
                "session_id": session_id,
                "reason": trust_result.reason if trust_result else "guardrail_unavailable",
            },
        )
        blocked_text = self._config.get("conversation", {}).get(
            "blocked_message",
            "I'm unable to help with that request.",
        )
        latency_ms = int((time.time() - start) * 1000)
        _trace_id = self._current_trace_id()

        # Assemble TurnEvent for audit (Step 11b / async logging)
        turn_event = TurnEvent(
            session_id=session_id,
            turn_id=turn_id,
            response_text=blocked_text,
            tool_calls=[],
            trust_input_result=trust_input,
            trust_output_result=TrustCheckResult(passed=True, action="allow"),
            model_used="",
            intent=intent,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            timestamp_ms=int(time.time() * 1000),
            trace_id=_trace_id,
        )

        # NOTE: daemon thread means audit write may be lost on abrupt process exit.
        # Blocked turns are compliance-critical; this is a known data-loss window.
        thread = threading.Thread(
            target=self._post_turn,
            args=(
                session_id, user_id, turn_id, blocked_text,
                user_message, turn_event, False, "",
            ),
            daemon=True,
        )
        thread.start()

        return TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=blocked_text,
            was_escalated=False,
            model_used="",
            latency_ms=latency_ms,
        )

    def _escalated_response(
        self,
        session_id: str,
        trust_result: Optional[TrustCheckResult],
        start: float,
        trust_input: TrustCheckResult,
        turn_id: str,
        intent: str,
        user_id: str = "",
        user_message: str = "",
    ) -> TurnResult:
        """
        Build a TurnResult for input or output that triggered escalation.

        Args:
            session_id:   Session identifier.
            trust_result: The escalating TrustCheckResult.
            start:        Turn start timestamp.
            trust_input:  Input trust result (kept for API symmetry).
            turn_id:      Unique identifier for this turn.
            intent:       NLU intent (or "unknown" for early-exit paths).
            user_id:      User identifier for audit recording.
            user_message: Original user message for audit recording.

        Returns:
            TurnResult with the configured escalation message.
        """
        logger.warning(
            "orchestrator.input_escalated",
            extra={
                "operation": "orchestrator.process_turn",
                "status": "skipped",
                "session_id": session_id,
                "reason": trust_result.reason if trust_result else "guardrail_unavailable",
            },
        )
        escalation_text = self._config.get("conversation", {}).get(
            "escalation_message",
            "I'm connecting you to a human agent who can better assist you.",
        )
        latency_ms = int((time.time() - start) * 1000)
        _trace_id = self._current_trace_id()

        # Assemble TurnEvent for audit (Step 11b / async logging)
        turn_event = TurnEvent(
            session_id=session_id,
            turn_id=turn_id,
            response_text=escalation_text,
            tool_calls=[],
            trust_input_result=trust_input,
            trust_output_result=TrustCheckResult(passed=True, action="allow"),
            model_used="",
            intent=intent,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            timestamp_ms=int(time.time() * 1000),
            trace_id=_trace_id,
        )

        # NOTE: daemon thread means audit write may be lost on abrupt process exit.
        # Escalated turns are compliance-critical; this is a known data-loss window.
        thread = threading.Thread(
            target=self._post_turn,
            args=(
                session_id, user_id, turn_id, escalation_text,
                user_message, turn_event, False, "",
            ),
            daemon=True,
        )
        thread.start()

        return TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=escalation_text,
            was_escalated=True,
            model_used="",
            latency_ms=latency_ms,
        )


    def _current_trace_id(self) -> str:
        """Extract the W3C trace-id hex string from the active OTel span context.

        Returns:
            32-character lowercase hex trace-id string, or empty string if no
            valid span context is active.
        """
        ctx = otel_trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else ""

    def _safe_fallback_message(self) -> str:
        """
        Return the configured safe fallback message for blocked LLM output.

        Returns:
            Fallback message string from config, or a hard-coded default.
        """
        return self._config.get("conversation", {}).get(
            "output_blocked_message",
            "I wasn't able to produce a safe response. Please try rephrasing your question.",
        )

    # ------------------------------------------------------------------
    # Private: cross-turn tool_use/tool_result replay (issue #193)
    # ------------------------------------------------------------------

    def _recent_tool_exchanges_caps(self) -> tuple[int, int]:
        """Return ``(max_items, max_chars)`` for cross-turn tool replay.

        Reads ``agent.recent_tool_exchanges.max_items`` and
        ``agent.recent_tool_exchanges.max_chars`` from config, falling back
        to (3, 4000) which mirrors the action_gateway default
        ``max_size_chars``.

        Returns:
            Tuple of (max_items, max_chars). Either may be 0 to disable.
        """
        cfg = self._config.get("agent", {}).get("recent_tool_exchanges", {}) or {}
        max_items = int(cfg.get("max_items", 3))
        max_chars = int(cfg.get("max_chars", 4000))
        return max_items, max_chars

    @staticmethod
    def _build_tool_exchange_messages(
        exchanges: list[dict],
    ) -> list[dict]:
        """Convert persisted tool exchange records into Anthropic messages.

        Each exchange is rendered as one ``assistant`` message containing
        all of its ``tool_use`` blocks followed by one ``user`` message
        containing the matching ``tool_result`` blocks, exactly as the
        Anthropic tool-use protocol requires.

        Args:
            exchanges: Ordered list of exchange dicts as persisted by
                ``_capture_tool_exchange``. Malformed entries are skipped.

        Returns:
            Flat list of ``messages`` dicts ready to prepend to a turn's
            ``messages`` array.
        """
        out: list[dict] = []
        if not exchanges:
            return out
        for ex in exchanges:
            if not isinstance(ex, dict):
                continue
            uses = ex.get("tool_uses") or []
            results = ex.get("tool_results") or []
            if not uses or not results:
                continue
            out.append({"role": "assistant", "content": list(uses)})
            out.append({"role": "user", "content": list(results)})
        return out

    @staticmethod
    def _truncate_tool_result_content(content: str, max_chars: int) -> str:
        """Truncate a tool_result payload to ``max_chars`` characters.

        Args:
            content: Raw text payload (already string-form).
            max_chars: Hard cap; values <= 0 disable truncation.

        Returns:
            The original string if within the cap, otherwise a clipped
            string.
        """
        if max_chars <= 0 or not content:
            return content
        if len(content) <= max_chars:
            return content
        return content[:max_chars]

    def _capture_tool_exchange(
        self,
        tool_calls: list,
        tool_results: list,
        max_chars: int,
    ) -> dict | None:
        """Build a single persistable exchange from one tool round.

        Args:
            tool_calls: ``ToolCall`` objects executed in this round.
            tool_results: Anthropic-schema tool_result content dicts that
                were appended to ``messages`` after the round.
            max_chars: Per-result content cap.

        Returns:
            A dict with ``tool_uses`` and ``tool_results`` keys, or
            ``None`` if either side is empty.
        """
        if not tool_calls or not tool_results:
            return None
        uses: list[dict] = []
        for tc in tool_calls:
            uses.append(
                {
                    "type": "tool_use",
                    "id": tc.tool_use_id,
                    "name": tc.tool_name,
                    "input": tc.input_params or {},
                }
            )
        results: list[dict] = []
        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            content = tr.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tr.get("tool_use_id", ""),
                    "content": self._truncate_tool_result_content(content, max_chars),
                }
            )
        if not uses or not results:
            return None
        return {"tool_uses": uses, "tool_results": results}

    # ------------------------------------------------------------------
    # Private: schedule async flush for early exit paths
    # ------------------------------------------------------------------

    def _schedule_flush(self, session_id: str, user_id: str, reason: str) -> None:
        """
        Spawn a daemon thread to flush the session asynchronously.

        Args:
            session_id: Session identifier.
            user_id:    User identifier.
            reason:     Human-readable reason string for audit logging.
        """
        thread = threading.Thread(
            target=self._do_flush,
            args=(session_id, user_id, reason),
            daemon=True,
        )
        thread.start()

    def _do_flush(self, session_id: str, user_id: str, reason: str) -> None:
        """
        Flush session state via the Memory Layer.

        Runs in a daemon thread. Exceptions are logged and swallowed to avoid
        crashing the thread.

        Args:
            session_id: Session identifier.
            user_id:    User identifier.
            reason:     Flush reason passed through to the Memory Layer.
        """
        try:
            self._memory.flush_session(session_id, user_id, reason)
        except Exception as e:
            logger.error(
                "orchestrator.flush_error",
                extra={
                    "operation": "orchestrator._do_flush",
                    "status": "failure",
                    "session_id": session_id,
                    "error": str(e),
                },
            )

    # ------------------------------------------------------------------
    # Private: result construction + async post-turn
    # ------------------------------------------------------------------

    def _build_result(
        self,
        session_id: str,
        user_id: str,
        response_text: str,
        was_escalated: bool,
        was_tool_used: bool,
        model_used: str,
        latency_ms: int,
        turn_input: TurnInput,
        turn_id: str,
        intent: str,
        tool_calls: list[ToolCall],
        trust_input: TrustCheckResult,
        trust_output: TrustCheckResult,
        do_flush: bool = False,
        flush_reason: str = "",
        trace_id: str = "",
        session_ended: bool = False,
    ) -> TurnResult:
        """
        Construct the TurnResult and schedule async post-turn work.

        Spawns a daemon thread to run Step 12 (last_response write) and
        Step 13 (learning emit) after the TurnResult has been returned.
        Entity writes, subagent_entry_count, and current_subagent_id are
        written synchronously before this point and are not repeated here.

        Args:
            session_id:    Session identifier.
            user_id:       User identifier.
            response_text: Final response text to deliver.
            was_escalated: True if this turn triggered escalation.
            was_tool_used: True if at least one tool was called.
            model_used:    Model identifier from the LLM response.
            latency_ms:    Total turn latency in milliseconds.
            turn_input:    Inbound turn data, used for TurnEvent timestamp.
            tool_calls:    All tool calls executed this turn.
            trust_input:   Trust check result for the input.
            trust_output:  Trust check result for the output.
            do_flush:      If True, flush session after memory writes.
            flush_reason:  Reason string passed to flush_session.

        Returns:
            Fully constructed TurnResult.
        """
        result = TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=response_text,
            was_escalated=was_escalated,
            was_tool_used=was_tool_used,
            model_used=model_used,
            latency_ms=latency_ms,
            session_ended=session_ended,
        )

        turn_event = TurnEvent(
            session_id=session_id,
            turn_id=turn_id,
            response_text=response_text,
            tool_calls=tool_calls,
            trust_input_result=trust_input,
            trust_output_result=trust_output,
            model_used=model_used,
            intent=intent,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            timestamp_ms=turn_input.timestamp_ms,
            trace_id=trace_id,
        )

        thread = threading.Thread(
            target=self._post_turn,
            args=(
                session_id, user_id, turn_id, response_text,
                turn_input.user_message, turn_event, do_flush, flush_reason,
            ),
            daemon=True,
        )
        thread.start()

        return result

    def _post_turn(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        response_text: str,
        user_message: str,
        turn_event: TurnEvent,
        do_flush: bool,
        flush_reason: str,
    ) -> None:
        """
        Run Steps 12-13 asynchronously after the TurnResult is returned.

        Writes last_response to the Memory Layer (Step 12) and emits a turn
        event to the Observability Layer (Step 13). Entity writes, current_subagent_id,
        and subagent_entry_count are written synchronously in process_turn and
        are not repeated here.

        Flushes session if do_flush is True (termination, HITL, or handoff).

        Any exception here is logged and swallowed — must never crash the thread.

        Args:
            session_id:    Session identifier.
            user_id:       User identifier.
            response_text: Final response text delivered this turn.
            turn_event:    Pre-assembled TurnEvent to emit to Observability Layer.
            do_flush:      If True, call flush_session after memory writes.
            flush_reason:  Reason string passed to flush_session.
        """
        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        learning_endpoint = (
            self._config.get("learning_client", {}).get("endpoint", "http://observability_layer:8004")
        )

        # ── Step 11b: Record Audit Turn ─────────────────────────────
        logger.info(
            "  [STEP 11b] [async] Audit Record  →  POST %s/audit/turn  (session=%s)",
            memory_endpoint, session_id,
        )
        try:
            self._memory.record_audit_turn(
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                user_message=user_message,
                system_message=response_text,
                metadata={
                    "subagent_id": turn_event.model_used,
                    "model": turn_event.model_used,
                    "latency_ms": turn_event.latency_ms,
                    "intent": turn_event.intent,
                }
            )
        except Exception as e:
            logger.error(
                "orchestrator.audit_record_failed",
                extra={
                    "operation": "orchestrator._post_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

        # ── Step 12: Write last_response ─────────────────────────────
        # Entities, current_subagent_id, and subagent_entry_count are already
        # written synchronously in process_turn before the response is returned.
        logger.info(
            "  [STEP 12] [async] Memory Write  →  POST %s/write  (session=%s)",
            memory_endpoint, session_id,
        )
        try:
            t12 = time.time()
            self._memory.write(session_id, user_id, "session", "last_response", response_text)
            logger.info(
                "  [STEP 12] [async] Memory Write  ✓  last_response written  latency=%dms",
                int((time.time() - t12) * 1000),
            )
        except Exception as e:
            logger.error(
                "orchestrator.memory_write_failed",
                extra={
                    "operation": "orchestrator._post_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": str(e),
                },
            )

        # ── Flush if session is ending ────────────────────────────────
        if do_flush:
            logger.info(
                "  [STEP 12b] [async] flush_session  →  POST %s/flush_session"
                "  (reason=%s)",
                memory_endpoint, flush_reason,
            )
            try:
                self._memory.flush_session(session_id, user_id, flush_reason)
            except Exception as e:
                logger.error(
                    "orchestrator.flush_session_failed",
                    extra={
                        "operation": "orchestrator._post_turn",
                        "status": "failure",
                        "session_id": session_id,
                        "error": str(e),
                    },
                )

        # ── Step 13: Emit to Observability Layer ───────────────────────────
        logger.info(
            "  [STEP 13] [async] Observability Emit  →  POST %s/emit/turn  (session=%s)",
            learning_endpoint, session_id,
        )
        try:
            t13 = time.time()
            self._learning.emit_turn(turn_event)
            logger.info(
                "  [STEP 13] [async] Learning Emit  ✓  latency=%dms",
                int((time.time() - t13) * 1000),
            )
        except Exception as e:
            logger.error(
                "orchestrator.learning_emit_failed",
                extra={
                    "operation": "orchestrator._post_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": str(e),
                },
            )

    # ------------------------------------------------------------------
    # Private: consent translation helper
    # ------------------------------------------------------------------

    async def _stream_termination_short_circuit(
        self,
        *,
        session_id: str,
        user_id: str,
        turn_id: str,
        turn_input: TurnInput,
        detected_language: str,
        nlu_result: NLUResult,
        bundle: ContextBundle,
        trust_input: TrustCheckResult,
        trust_output: TrustCheckResult,
        start: float,
        stamp,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Skip the LLM and emit the canned termination_message (#204).

        Pulls ``conversation.termination_message`` from config, translates it
        to the user's detected language using the same helper as the consent
        flow, and yields a single ``SentenceEvent`` followed by a terminal
        ``DoneEvent`` with ``session_ended=True``. State updates (session_id,
        ended_at, audit, observability) still fire via ``_async_post_turn``.

        Args:
            session_id: Active session identifier.
            user_id: Caller identity for memory writes.
            turn_id: Stable id stamped on every emitted event.
            turn_input: The current turn's input — only ``user_message`` /
                ``timestamp_ms`` are read; nothing is mutated.
            detected_language: Language inferred earlier in the turn.
            nlu_result: Used for the audit-event ``intent``.
            bundle: Current memory snapshot; used to resolve the routing
                target (``ended`` subagent if present).
            trust_input: Earlier trust verdict — passed through to
                ``_async_post_turn`` for observability.
            trust_output: No second Trust call is made here; the message is
                config-controlled. The earlier verdict is reused for the audit.
            start: Wall-clock start of the turn for latency reporting.
            stamp: Helper that decorates events with ``turn_id``.

        Yields:
            Exactly one SentenceEvent followed by a terminal DoneEvent.
        """
        termination_message: str = self._config.get("conversation", {}).get(
            "termination_message", ""
        ) or ""

        # Route to the "ended" subagent if the workflow defines one. This
        # keeps reconnect / observability semantics consistent with the
        # full LLM path (where global_routing on termination_intent moves
        # the session into the terminal subagent).
        ended_subagent_id = "ended" if "ended" in self._workflow.subagents else (
            bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id
        )
        bundle.session["current_subagent_id"] = ended_subagent_id

        write_tasks: list = [
            self._async_memory.write(
                session_id, user_id, "session", "current_subagent_id", ended_subagent_id
            ),
        ]

        translated = self._translate_consent_message(termination_message, detected_language)

        # Best-effort fan-out of routing writes; parallel with the SentenceEvent
        # so the caller hears the goodbye even if Memory Layer is slow.
        await asyncio.gather(*write_tasks, return_exceptions=True)

        latency_ms = int((time.time() - start) * 1000)

        logger.info(
            "orchestrator.stream_turn_termination_short_circuit",
            extra={
                "operation": "orchestrator.stream_turn",
                "status": "success",
                "session_id": session_id,
                "intent": nlu_result.intent,
                "confidence": nlu_result.confidence,
                "latency_ms": latency_ms,
                "language": detected_language,
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  STREAM TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
            "  model=%s  total_latency=%dms  next_subagent=%s\n"
            "  response: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, nlu_result.intent, False,
            "none", latency_ms, ended_subagent_id,
            (translated or "").strip()[:200],
        )

        if translated:
            yield stamp(SentenceEvent(text=translated, sentence_index=0))

        yield stamp(DoneEvent(
            turn_id=turn_id,
            turn_status="completed",
            session_ended=True,
            was_escalated=False,
            was_tool_used=False,
            model_used="none",
            latency_ms=latency_ms,
        ))

        # Async post-turn (audit log, last_response, observability emit).
        asyncio.create_task(
            self._async_post_turn(
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                response_text=(translated or "").strip(),
                user_message=turn_input.user_message,
                trust_input=trust_input,
                trust_output=trust_output,
                model_used="none",
                intent=nlu_result.intent,
                tool_calls=[],
                latency_ms=latency_ms,
                timestamp_ms=turn_input.timestamp_ms,
            )
        )

    def _sanitize_current_question(
        self, *, prev: str, new: str, session_id: str
    ) -> str:
        """Apply the #207 guardrails to the value about to be written.

        Memory Layer's ``write`` overwrites (Redis HSET), so this helper does
        not protect against an appending store — it protects against an
        upstream caller handing us a string that already contains the prior
        ``current_question`` as a prefix (the symptom seen pre-#200 when
        cancelled and successor turn responses got glued together by the
        old pile-up path).

        Behaviour:
          * Empty ``new`` → empty result.
          * ``new`` starts with ``prev`` and is strictly longer (and ``prev``
            is long enough to be a meaningful prefix, > 8 chars) → log
            ``orchestrator.current_question_accumulation_detected`` at
            WARNING and strip the prefix so we store only the latest
            response.
          * Always cap the final value at ``agent.current_question.max_chars``.

        Args:
            prev: The value currently in memory for this session (may be "").
            new: The value the caller wants to persist.
            session_id: For log correlation only.

        Returns:
            The sanitized value to write.
        """
        max_chars: int = int(
            self._config.get("agent", {}).get("current_question", {}).get("max_chars", 500)
        )
        new = (new or "").strip()
        prev = (prev or "").strip()
        if not new:
            return ""
        # Concat detector: prev is meaningfully long, new starts with prev,
        # new is strictly longer than prev. > 8-char threshold avoids
        # warnings on trivial overlaps like "Hi." or single-word prefixes.
        if (
            prev
            and len(prev) > 8
            and new != prev
            and new.startswith(prev)
        ):
            stripped = new[len(prev):].lstrip(" \n\t।.!?")
            logger.warning(
                "orchestrator.current_question_accumulation_detected",
                extra={
                    "operation": "orchestrator._sanitize_current_question",
                    "status": "skipped",
                    "session_id": session_id,
                    "prev_len": len(prev),
                    "new_len": len(new),
                    "stored_len": len(stripped),
                },
            )
            new = stripped or prev  # if stripping leaves nothing, fall back to prev
        if len(new) > max_chars:
            new = new[:max_chars].rstrip()
        return new

    def _translate_consent_message(self, message: str, target_language: str) -> str:
        """Translate the consent prompt to the user's detected language.

        Args:
            message: The raw consent prompt string from config.
            target_language: Language detected from the user's input.

        Returns:
            Translated message, or the original if translation is unnecessary or fails.
        """
        if not message or not target_language:
            return message
        default_language = (
            self._config.get("preprocessing", {})
            .get("language_normalisation", {})
            .get("default_language", "hindi")
        )
        if target_language == default_language:
            return message
        t_translate = time.time()
        try:
            response = self._llm.call(
                messages=[{"role": "user", "content": message}],
                tools=[],
                system=(
                    f"Translate the user message to {target_language}. "
                    "Return ONLY the translated text, no explanation."
                ),
            )
            if response.stop_reason != "error" and response.content:
                logger.info(
                    "orchestrator.consent_translation_success",
                    extra={
                        "operation": "orchestrator._translate_consent_message",
                        "status": "success",
                        "target_language": target_language,
                        "latency_ms": int((time.time() - t_translate) * 1000),
                    },
                )
                return response.content.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "orchestrator.consent_translation_failed",
                extra={
                    "operation": "orchestrator._translate_consent_message",
                    "status": "failure",
                    "error": str(exc),
                    "target_language": target_language,
                    "latency_ms": int((time.time() - t_translate) * 1000),
                },
            )
        return message

    # ------------------------------------------------------------------
    # Private: channel config resolver
    # ------------------------------------------------------------------

    def _resolve_channel_config(self, channel: str) -> dict:
        """Resolve per-channel config from top-level channels.<name>.

        Args:
            channel: Channel name from the inbound TurnInput.

        Returns:
            Channel config dict (at minimum has `system_prompt_suffix` key).

        Raises:
            ValueError: If the channel is not present in the top-level channels config,
                OR if the legacy `agent.channels` path is present (hard-cut migration).
        """
        if self._config.get("agent", {}).get("channels"):
            raise ValueError(
                "agent.channels is removed — migrate to top-level channels.<name> "
                "(see docs/superpowers/specs/2026-04-21-gh137-framework-uplift-design.md)"
            )

        channels = self._config.get("channels", {})
        config = channels.get(channel)
        if config is None:
            raise ValueError(f"Unsupported channel: {channel}")
        return config

    # ------------------------------------------------------------------
    # Private: synchronous memory write helper
    # ------------------------------------------------------------------

    def _write_memory_sync(
        self,
        session_id: str,
        user_id: str,
        scope: str,
        key: str,
        value: Any,
    ) -> None:
        """
        Write a single key/value to the Memory Layer synchronously.

        Used for state transitions whose values must be visible on the NEXT
        turn. Unlike _post_turn's async writes, this blocks until the write
        completes before the TurnResult is returned.

        Args:
            session_id: Session identifier.
            user_id:    User identifier.
            scope:      Memory scope — "session" or "persistent".
            key:        Field key to write.
            value:      Value to store.
        """
        try:
            self._memory.write(session_id, user_id, scope, key, value)
        except Exception as e:
            logger.error(
                "orchestrator.sync_write_failed",
                extra={
                    "operation": "orchestrator._write_memory_sync",
                    "status": "failure",
                    "session_id": session_id,
                    "key": key,
                    "error": str(e),
                },
            )

    # ------------------------------------------------------------------
    # Private: user-state model helper (GH-139)
    # ------------------------------------------------------------------

    def _handle_user_state_turn(
        self,
        *,
        session_id: str,
        user_id: str | None,
        turn_id: str,
        bundle,
        nlu_result: NLUResult,
        previous_state_id: str | None,
        previous_payload: dict | None,
        span,
    ) -> str | None:
        """Resolve, persist, observe, and return user-state guidance for the current turn.

        No-op when the user-state model is disabled — returns None.

        Args:
            session_id:        Active session id.
            user_id:           Active user id.
            turn_id:           Active turn id (for event emission).
            bundle:            Mutated in place — bundle.session["user_state"] is set.
            nlu_result:        NLUResult containing the freshly-classified user_state.
            previous_state_id: State id read at turn start (or default on first turn).
            previous_payload:  Full previous payload from memory (None on first turn).
            span:              Active OTel span for attribute attachment.

        Returns:
            Guidance text for the current state (string) or None when the model
            is disabled. Empty guidance resolves to None.
        """
        if not self._user_state_enabled:
            return None

        from datetime import datetime, timezone
        from src.preprocessing.user_state_resolver import resolve_user_state

        new_payload, transitioned = resolve_user_state(
            classification=nlu_result.user_state,
            previous=previous_payload,
            config=self._config,
            now=datetime.now(timezone.utc),
        )
        if new_payload is None:
            return None

        # Piggy-back on the per-turn session write — same call, same scope.
        self._write_memory_sync(
            session_id, user_id, "session", "user_state", new_payload,
        )
        bundle.session["user_state"] = new_payload

        # OTel span attributes — operational telemetry on the existing turn span.
        try:
            span.set_attribute("user_state.enabled", True)
            span.set_attribute("user_state.previous", previous_state_id or "")
            span.set_attribute("user_state.current", new_payload["id"])
            span.set_attribute("user_state.transitioned", transitioned)
            span.set_attribute(
                "user_state.confidence", float(new_payload["confidence"])
            )
            span.set_attribute(
                "user_state.turn_count", int(new_payload["turn_count"])
            )
        except Exception as _otel_err:
            logger.warning(
                "orchestrator.user_state_otel_attr_failed",
                extra={
                    "operation": "orchestrator.user_state",
                    "status": "skipped",
                    "error": f"{type(_otel_err).__name__}: {_otel_err}",
                },
            )

        logger.info(
            "user_state.resolved",
            extra={
                "operation": "orchestrator.resolve_user_state",
                "status": "success",
                "transitioned": transitioned,
                "state_id": new_payload["id"],
                "previous_state_id": previous_state_id,
                "latency_ms": 0,
            },
        )

        # Observability Layer event — async, only on actual transitions.
        if transitioned:
            try:
                self._learning.emit_signal(
                    "user_state_transition",
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "timestamp_ms": int(time.time() * 1000),
                        "from_state": previous_state_id,
                        "to_state": new_payload["id"],
                        "confidence": new_payload["confidence"],
                        "trigger_intent": nlu_result.intent,
                        "turns_in_previous_state": (
                            int((previous_payload or {}).get("turn_count", 0))
                            if previous_payload else 0
                        ),
                    },
                )
            except Exception as _evt_err:
                logger.warning(
                    "orchestrator.user_state_event_emit_failed",
                    extra={
                        "operation": "orchestrator.emit_user_state_transition",
                        "status": "skipped",
                        "error": f"{type(_evt_err).__name__}: {_evt_err}",
                    },
                )

        guidance = self._user_state_guidance_by_id.get(new_payload["id"], "")
        return guidance or None

    # ------------------------------------------------------------------
    # Streaming: stream_turn() — async SSE pipeline
    # ------------------------------------------------------------------

    async def stream_turn(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute one conversation turn with streaming SSE output.

        Runs the same 13-step pipeline as process_turn() but uses async
        HTTP clients and yields StreamEvents as the pipeline progresses.

        Args:
            turn_input: Normalised inbound message from the Reach Layer.
            abort_event: Optional asyncio.Event. When set, stream_turn exits
                cleanly at the next stage boundary without yielding further
                events. Tool calls and trust checks that are already in-flight
                run to completion to preserve external-side-effect safety.
            turn_id: Optional caller-supplied identifier for this turn. When
                non-empty, it is stamped on every emitted StreamEvent. When
                empty (the default), an internal uuid4 is generated and used.

        Yields:
            SignalEvent, SentenceEvent, or DoneEvent.
        """
        if turn_input is None:
            raise ValueError("turn_input must not be None")
        if not turn_input.session_id:
            raise ValueError("turn_input.session_id must not be empty")
        if turn_input.user_message is None:
            raise ValueError("turn_input.user_message must not be None")
        if self._async_memory is None or self._async_trust is None:
            raise ValueError("Async clients must be injected to use stream_turn()")

        start = time.time()
        session_id = turn_input.session_id
        user_id: str = turn_input.user_id or session_id
        turn_id = turn_id or str(uuid.uuid4())

        def _aborted() -> bool:
            return abort_event is not None and abort_event.is_set()

        def _stamp(ev):
            """Set turn_id on the event in place and return it."""
            if hasattr(ev, "turn_id"):
                ev.turn_id = turn_id
            return ev

        was_escalated = False
        was_tool_used = False
        model_used = ""
        trust_input = TrustCheckResult(passed=True, action="allow")
        trust_output = TrustCheckResult(passed=True, action="allow")
        nlu_result = NLUResult(intent="unknown", entities={}, sentiment="neutral", confidence=0.0)
        all_tool_calls: list[ToolCall] = []
        full_response_text = ""
        # GH-191: Track end_session locally for the streaming path. The sync
        # path mutates ``manager_agent._session_ended_flag`` inside ``run_turn``;
        # the streaming tool loop bypasses that method, so we must compute the
        # signal here. Reset the manager flag too in case a prior sync turn left
        # it set on this AgentCore instance.
        session_ended = False
        try:
            self._manager_agent._reset_turn_flags()
        except AttributeError:
            # Test doubles or alternative manager implementations may omit this
            # helper; default to clearing the attribute when present.
            if hasattr(self._manager_agent, "_session_ended_flag"):
                self._manager_agent._session_ended_flag = False

        logger.info(
            "orchestrator.stream_turn_start",
            extra={
                "operation": "orchestrator.stream_turn",
                "status": "success",
                "session_id": session_id,
                "channel": turn_input.channel,
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  STREAM TURN START  session=%s  channel=%s\n"
            "  input: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, turn_input.channel, turn_input.user_message[:120],
        )

        channel_config = self._resolve_channel_config(turn_input.channel)

        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        trust_endpoint = (
            self._config.get("trust_client", {}).get("endpoint", "http://trust_layer:8003")
        )

        try:
            if _aborted():
                return
            # ── Step 1: Read session state ──────────────────────────────
            logger.info(
                "  [STEP 1] Memory context_bundle  →  POST %s/context_bundle  (session=%s)",
                memory_endpoint, session_id,
            )
            t1 = time.time()
            yield _stamp(SignalEvent(stage="memory_read", status="start"))
            bundle = await self._async_memory.context_bundle(session_id, user_id, adopt=not turn_input.fresh)
            current_subagent_id: str = (
                bundle.session.get("current_subagent_id")
                or self._workflow.start_subagent_id
            )
            current_question: str = bundle.session.get("current_question", "")
            yield _stamp(SignalEvent(stage="memory_read", status="complete"))
            if _aborted():
                return
            logger.info(
                "  [STEP 1] Memory context_bundle  ✓  current_subagent_id=%s"
                "  is_returning=%s  latency=%dms",
                current_subagent_id,
                bundle.session.get("is_returning", False),
                int((time.time() - t1) * 1000),
            )

            # ── Step 4 + Step 5 (parallel): lang-norm + NLU ─────────────
            # GH-151 #2: language_normalisation and NLU were previously run
            # back-to-back (~4 s of wall-clock on Haiku). They have no real
            # data dependency — NLU's prompt just inserts the user message
            # verbatim and Claude handles multilingual / mixed-script input
            # directly — so we fire them concurrently and await the pair.
            # The raw user message is used as NLU input so we don't need
            # to wait for the lang-norm LLM to return.
            #
            # Small cost: on the turn that hits the consent gate (first turn
            # of a new session when ask_for_consent=true), NLU's result is
            # discarded. That's one wasted LLM call per session; every
            # subsequent turn halves its lang-norm+NLU latency.

            # Pre-compute NLU arguments so both coroutines can fire immediately.
            pre_allowed_intents = self._workflow.nlu_intent_set.get(current_subagent_id, [])
            pre_profile_data = bundle.profile or {}
            pre_existing_profile_keys: list[str] = [k for k in pre_profile_data if k != "attributes"]
            for _attr in pre_profile_data.get("attributes", []):
                _attr_key = _attr.get("key", "") if isinstance(_attr, dict) else ""
                if _attr_key:
                    pre_existing_profile_keys.append(_attr_key)

            pre_previous_user_state_payload: dict | None = None
            pre_previous_user_state_id: str | None = None
            if self._user_state_enabled:
                _maybe = bundle.session.get("user_state")
                if isinstance(_maybe, dict):
                    pre_previous_user_state_payload = _maybe
                    pre_previous_user_state_id = _maybe.get("id")
                if pre_previous_user_state_id is None:
                    pre_previous_user_state_id = self._user_state_default

            logger.info(
                "  [STEP 4+5] Language Norm + NLU  →  (session=%s, parallel)", session_id
            )
            t45 = time.time()
            yield _stamp(SignalEvent(stage="nlu", status="start"))

            # asyncio.to_thread offloads each sync llm.call onto the default
            # thread pool so the two Anthropic round-trips overlap in wall
            # clock. They never race on shared state — each uses its own
            # LLMResponse.
            (normalised_input, turn_language), early_nlu_result = await asyncio.gather(
                asyncio.to_thread(
                    self._language_normaliser.normalise,
                    turn_input.user_message,
                    self._config,
                    self._llm,
                ),
                asyncio.to_thread(
                    self._nlu_processor.process,
                    turn_input.user_message,
                    current_question,
                    current_subagent_id,
                    self._llm,
                    pre_allowed_intents,
                    pre_existing_profile_keys,
                    pre_previous_user_state_id,
                ),
            )
            logger.info(
                "  [STEP 4+5] Lang-Norm + NLU (parallel)  ✓  detected=%s  intent=%s  total_latency=%dms",
                turn_language or "—",
                early_nlu_result.intent,
                int((time.time() - t45) * 1000),
            )

            profile_data = bundle.profile if bundle.profile is not None else {}
            session_data = bundle.session if bundle.session is not None else {}
            default_language = (
                self._config.get("preprocessing", {})
                .get("language_normalisation", {})
                .get("default_language", "hindi")
            )
            language_preference = (
                profile_data.get("language_preference")
                or session_data.get("language_preference")
                or turn_language
                or default_language
            )

            # Lock in language_preference on the first turn only.
            # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
            saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
            if not saved_preference:
                pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
                await self._async_memory.write(session_id, user_id, pref_scope, "language_preference", language_preference)
                bundle.session["language_preference"] = language_preference

            detected_language = language_preference

            # ── Consent gate (Step 1b) ──────────────────────────────────
            ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
            if ask_for_consent:
                user_storage_mode: str | None = bundle.session.get("user_storage_mode")
                turn_count: int = int(bundle.session.get("turn_count", 0) or 0)

                if user_storage_mode is None and turn_count == 0:
                    # Turn 1: deliver consent prompt (translated to user's language),
                    # no LLM inference, no Trust Layer call.
                    # Stash the user's original message + normalised form so the
                    # next turn can replay them after consent is evaluated — otherwise
                    # the user's first real input would be silently dropped.
                    consent_prompt_text: str = self._config.get("agent", {}).get("consent_prompt", "")
                    await self._async_memory.write(session_id, user_id, "session", "turn_count", 1)
                    await self._async_memory.write(
                        session_id, user_id, "session",
                        "pending_user_message", turn_input.user_message,
                    )
                    await self._async_memory.write(
                        session_id, user_id, "session",
                        "pending_normalised_input", normalised_input or turn_input.user_message,
                    )
                    consent_response_text = self._translate_consent_message(consent_prompt_text, detected_language)
                    consent_latency_ms = int((time.time() - start) * 1000)
                    logger.info(
                        "\n═══════════════════════════════════════════════════════════════\n"
                        "  STREAM TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
                        "  model=%s  total_latency=%dms  next_subagent=%s\n"
                        "  response: %r\n"
                        "═══════════════════════════════════════════════════════════════",
                        session_id, "consent_prompt", False,
                        "none", consent_latency_ms, "consent_gate",
                        consent_response_text.strip()[:200],
                    )
                    yield _stamp(SentenceEvent(
                        text=consent_response_text,
                        sentence_index=0,
                    ))
                    yield _stamp(DoneEvent(
                        turn_id=turn_id,
                        latency_ms=consent_latency_ms,
                    ))
                    return

                if user_storage_mode is None and turn_count > 0:
                    granted: bool = await self._async_trust.verify_consent(session_id, turn_input.user_message)
                    new_storage_mode = "saved" if granted else "anonymous"
                    await self._async_memory.write(session_id, user_id, "session", "user_storage_mode", new_storage_mode)
                    bundle.session["user_storage_mode"] = new_storage_mode

                    # Replay the stashed first-turn message as this turn's real
                    # input. The parallel NLU above ran against the consent reply
                    # ("yes"/"no"), so its result is stale — re-run NLU on the
                    # pending message and reuse the stashed normalised form so we
                    # don't pay a second lang-norm call.
                    pending_msg = bundle.session.get("pending_user_message") or ""
                    pending_norm = bundle.session.get("pending_normalised_input") or ""
                    if pending_msg:
                        turn_input.user_message = pending_msg
                        normalised_input = pending_norm or pending_msg
                        await self._async_memory.write(session_id, user_id, "session", "pending_user_message", "")
                        await self._async_memory.write(session_id, user_id, "session", "pending_normalised_input", "")
                        early_nlu_result = await asyncio.to_thread(
                            self._nlu_processor.process,
                            pending_msg,
                            current_question,
                            current_subagent_id,
                            self._llm,
                            pre_allowed_intents,
                            pre_existing_profile_keys,
                            pre_previous_user_state_id,
                        )
                        logger.info(
                            "orchestrator.consent_gate",
                            extra={
                                "operation": "orchestrator.consent_gate",
                                "status": "pending_message_replayed",
                                "session_id": session_id,
                                "replayed_intent": early_nlu_result.intent,
                            },
                        )

                    # GH-201: emit opening_phrase on the first post-consent
                    # turn. TurnAssembler suppressed it at SSE connect when
                    # ask_for_consent was on; this is the catch-up emission so
                    # the caller still gets the welcome utterance.
                    if not bundle.session.get("opening_phrase_emitted", False):
                        post_consent_sa = self._workflow.subagents.get(current_subagent_id)
                        post_consent_phrase = (
                            getattr(post_consent_sa, "opening_phrase", "") or ""
                        ).strip()
                        await self._async_memory.write(
                            session_id, user_id, "session", "opening_phrase_emitted", True
                        )
                        bundle.session["opening_phrase_emitted"] = True
                        if post_consent_phrase:
                            await self._async_memory.write(
                                session_id, user_id, "session",
                                "current_subagent_id", current_subagent_id,
                            )
                            logger.info(
                                "orchestrator.opening_phrase_emitted",
                                extra={
                                    "operation": "orchestrator.opening_phrase_gate",
                                    "status": "emitted",
                                    "session_id": session_id,
                                    "subagent_id": current_subagent_id,
                                    "trigger": "post_consent",
                                },
                            )
                            yield _stamp(SentenceEvent(
                                text=post_consent_phrase,
                                sentence_index=0,
                            ))

            # ── Step 2: Resolve current subagent ────────────────────────
            current_subagent: SubAgent = self._workflow.subagents[current_subagent_id]
            logger.info(
                "  [STEP 2] Resolved subagent=%s (%s)  special_handler=%s",
                current_subagent.id, current_subagent.name,
                current_subagent.special_handler or "none",
            )

            if current_subagent.special_handler:
                logger.info(
                    "  [STEP 3] Trust Input Check  →  POST %s/check/input  (session=%s)",
                    trust_endpoint, session_id,
                )
                t3 = time.time()
                yield _stamp(SignalEvent(stage="trust_input", status="start"))
                trust_input = await self._async_trust.check_input(session_id, turn_input.user_message)
                yield _stamp(SignalEvent(stage="trust_input", status="complete"))
                logger.info(
                    "  [STEP 3] Trust Input Check  ✓  action=%s  passed=%s  reason=%s  latency=%dms",
                    trust_input.action, trust_input.passed,
                    trust_input.reason or "—", int((time.time() - t3) * 1000),
                )

                if trust_input.action == "block":
                    blocked_text = self._config.get("conversation", {}).get(
                        "blocked_message", "I'm unable to help with that request."
                    )
                    yield _stamp(SentenceEvent(text=blocked_text, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                    return
                if trust_input.action == "escalate":
                    escalation_text = self._config.get("conversation", {}).get(
                        "escalation_message", "I'm connecting you to a human agent who can better assist you."
                    )
                    yield _stamp(SentenceEvent(text=escalation_text, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, was_escalated=True, latency_ms=int((time.time() - start) * 1000)))
                    return

                # Execute special handler inline for streaming
                if current_subagent.special_handler == "hitl":
                    hitl_msg = self._config.get("hitl", {}).get(
                        "response_message", "I'm connecting you with a counsellor who can better assist you."
                    )
                    yield _stamp(SentenceEvent(text=hitl_msg, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, was_escalated=True, latency_ms=int((time.time() - start) * 1000)))
                    return
                elif current_subagent.special_handler == "whatsapp_handoff":
                    handoff_msg = self._config.get("messages", {}).get(
                        "whatsapp_handoff", "We're sending you a WhatsApp message with all the details."
                    )
                    yield _stamp(SentenceEvent(text=handoff_msg, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                    return
                else:
                    fallback_msg = self._config.get("conversation", {}).get(
                        "unknown_intent_message", "I didn't quite understand that. Could you tell me more?"
                    )
                    yield _stamp(SentenceEvent(text=fallback_msg, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                    return

            # ── Step 3: Trust check on input ────────────────────────────
            logger.info(
                "  [STEP 3] Trust Input Check  →  POST %s/check/input  (session=%s)",
                trust_endpoint, session_id,
            )
            t3 = time.time()
            yield _stamp(SignalEvent(stage="trust_input", status="start"))
            trust_input = await self._async_trust.check_input(session_id, turn_input.user_message)
            yield _stamp(SignalEvent(stage="trust_input", status="complete"))
            logger.info(
                "  [STEP 3] Trust Input Check  ✓  action=%s  passed=%s  reason=%s  latency=%dms",
                trust_input.action, trust_input.passed,
                trust_input.reason or "—", int((time.time() - t3) * 1000),
            )

            if trust_input.action == "block":
                blocked_text = self._config.get("conversation", {}).get(
                    "blocked_message", "I'm unable to help with that request."
                )
                yield _stamp(SentenceEvent(text=blocked_text, sentence_index=0))
                yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                return

            if trust_input.action == "escalate":
                escalation_text = self._config.get("conversation", {}).get(
                    "escalation_message", "I'm connecting you to a human agent who can better assist you."
                )
                yield _stamp(SentenceEvent(text=escalation_text, sentence_index=0))
                yield _stamp(DoneEvent(turn_id=turn_id, was_escalated=True, latency_ms=int((time.time() - start) * 1000)))
                return

            # ── Step 5: NLU Processor (result from parallel gather) ─────
            # GH-151 #2: NLU already ran in parallel with lang-norm above.
            # Promote its pre-computed inputs to the names the rest of the
            # function expects, so the downstream handlers (user-state,
            # entity writes, language-switch routing) need no further change.
            allowed_intents = pre_allowed_intents
            profile_data = pre_profile_data
            existing_profile_keys = pre_existing_profile_keys
            stream_previous_user_state_payload = pre_previous_user_state_payload
            stream_previous_user_state_id = pre_previous_user_state_id
            nlu_result = early_nlu_result
            yield _stamp(SignalEvent(stage="nlu", status="complete"))
            logger.info(
                "  [STEP 5] NLU Processor  ✓  intent=%s  confidence=%.2f"
                "  entities=%s  (parallel — see STEP 4+5)",
                nlu_result.intent, nlu_result.confidence,
                list((nlu_result.entities or {}).keys()),
            )

            # ── #204: termination_intent short-circuit ──────────────────
            # When NLU is confident the user wants to end the session,
            # skip the LLM round trip entirely and speak the configured
            # termination message directly. Saves ~9 s on the goodbye
            # turn (NLU + LLM#1 + end_session tool + LLM#2 → NLU only).
            term_cfg = self._config.get("agent", {}).get("termination_short_circuit", {}) or {}
            term_enabled: bool = bool(term_cfg.get("enabled", True))
            term_threshold: float = float(term_cfg.get("confidence_threshold", 0.7))
            if (
                term_enabled
                and nlu_result.intent == "termination_intent"
                and nlu_result.confidence >= term_threshold
            ):
                async for ev in self._stream_termination_short_circuit(
                    session_id=session_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    turn_input=turn_input,
                    detected_language=detected_language,
                    nlu_result=nlu_result,
                    bundle=bundle,
                    trust_input=trust_input,
                    trust_output=trust_output,
                    start=start,
                    stamp=_stamp,
                ):
                    yield ev
                return

            stream_user_state_guidance_text = self._handle_user_state_turn(
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                bundle=bundle,
                nlu_result=nlu_result,
                previous_state_id=stream_previous_user_state_id,
                previous_payload=stream_previous_user_state_payload,
                span=otel_trace.get_current_span(),
            )

            # Write entities
            entity_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
            entity_map: dict = self._config.get("entity_to_profile_field", {})
            supported_langs: set[str] = {
                l.lower() for l in self._config.get("preprocessing", {})
                .get("language_normalisation", {})
                .get("supported_languages", [])
            }
            for entity_key, entity_val in (nlu_result.entities or {}).items():
                profile_field = entity_map.get(entity_key, entity_key)
                # Guard: never persist language_preference with a value outside
                # the configured supported_languages list (mirrors sync path).
                if profile_field == "language_preference" and supported_langs:
                    if str(entity_val).lower().strip() not in supported_langs:
                        continue
                await self._async_memory.write(session_id, user_id, entity_scope, profile_field, entity_val)
                bundle.session[profile_field] = entity_val

            # ── Language switch — handle before routing ───────────────
            if nlu_result.intent == "language_switch_request":
                lang_cfg = (
                    self._config.get("preprocessing", {})
                    .get("language_normalisation", {})
                )
                supported = [
                    l.lower() for l in lang_cfg.get("supported_languages", [])
                ]
                requested_lang = (
                    (nlu_result.entities or {}).get("language_preference") or ""
                ).lower().strip()

                if requested_lang and requested_lang in supported:
                    # Profile write already happened in the entity loop above
                    # for supported values; mirror into bundle.session and flip
                    # the active detected_language for this turn's prompt.
                    bundle.session["language_preference"] = requested_lang
                    detected_language = requested_lang
                    logger.info(
                        "orchestrator.language_switched",
                        extra={
                            "operation": "orchestrator.language_switch",
                            "status": "success",
                            "session_id": session_id,
                            "language_preference": requested_lang,
                        },
                    )
                else:
                    supported_names = lang_cfg.get("supported_languages", [])
                    if supported_names:
                        default_msg = f"I can only respond in: {', '.join(supported_names)}."
                    else:
                        default_msg = "That language is not supported."
                    msg = self._config.get("conversation", {}).get(
                        "unsupported_language_message", default_msg
                    )
                    logger.info(
                        "orchestrator.language_switch_rejected",
                        extra={
                            "operation": "orchestrator.language_switch",
                            "status": "skipped",
                            "session_id": session_id,
                            "language_preference": requested_lang,
                            "reason": "not_in_supported_languages",
                        },
                    )
                    yield _stamp(SentenceEvent(text=msg, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                    return

            # ── Step 6: Routing ────────────────────────────────────────
            logger.info(
                "  [STEP 6] Routing  →  intent=%s  current_subagent=%s",
                nlu_result.intent, current_subagent_id,
            )
            t6 = time.time()
            yield _stamp(SignalEvent(stage="routing", status="start"))
            routing_state = dict(bundle.session)
            if bundle.profile:
                routing_state.update(bundle.profile)

            next_subagent_id, matched_rule = self._resolve_next_subagent(
                current_subagent=current_subagent,
                nlu_result=nlu_result,
                session=routing_state,
            )

            # GH-151 #5: collect the routing-phase state writes and flush
            # them concurrently instead of serially. These are all session-
            # scoped (Redis-backed) but each still carries a round-trip to
            # Memory Layer; awaiting them sequentially added ~N × 5–100 ms
            # per turn. They're independent and can land in any order —
            # their in-memory shadows on ``bundle`` are updated synchronously
            # so subsequent reads in this turn still see the new values.
            routing_writes: list = []
            if matched_rule and matched_rule.session_writes:
                for field_name, field_val in matched_rule.session_writes.items():
                    routing_writes.append(
                        self._async_memory.write(
                            session_id, user_id, "session", field_name, field_val
                        )
                    )
                    bundle.session[field_name] = field_val

            raw_counts = bundle.session.get("subagent_entry_count")
            subagent_entry_count = dict(raw_counts) if isinstance(raw_counts, dict) else {}
            subagent_entry_count[next_subagent_id] = int(subagent_entry_count.get(next_subagent_id, 0)) + 1
            bundle.session["subagent_entry_count"] = subagent_entry_count
            bundle.session["current_subagent_id"] = next_subagent_id
            routing_writes.extend(
                [
                    self._async_memory.write(
                        session_id, user_id, "session", "subagent_entry_count", subagent_entry_count
                    ),
                    self._async_memory.write(
                        session_id, user_id, "session", "current_subagent_id", next_subagent_id
                    ),
                ]
            )
            if routing_writes:
                await asyncio.gather(*routing_writes, return_exceptions=True)
            yield _stamp(SignalEvent(stage="routing", status="complete"))
            logger.info(
                "  [STEP 6] Routing  ✓  next_subagent=%s  matched_rule_intent=%s  latency=%dms",
                next_subagent_id,
                matched_rule.intent if matched_rule else "—",
                int((time.time() - t6) * 1000),
            )

            # ── Step 7: Prompt assembly ────────────────────────────────
            logger.info(
                "  [STEP 7] Prompt Assembly  →  subagent=%s  language=%s",
                next_subagent_id, detected_language,
            )
            next_subagent: SubAgent = self._workflow.subagents[next_subagent_id]
            profile_context = dict(bundle.profile)
            profile_field_names = set(entity_map.values())
            for k, v in bundle.session.items():
                if k in profile_field_names and v not in (None, "", "[]"):
                    profile_context[k] = v

            final_language = profile_context.get("language_preference", detected_language)
            is_resumption = bundle.session.get("was_adopted", False)

            # Step 6b: Assemble guardrail constraints
            guardrail_constraints: dict | None = None
            if nlu_result.active_risks:
                try:
                    guardrail_constraints = await self._async_trust.assemble_constraints(
                        session_id=session_id,
                        workflow_step=next_subagent_id,
                        active_risks=nlu_result.active_risks,
                        user_segment=bundle.profile.get("user_segment"),
                    )
                except Exception:
                    blocked_text = self._config.get("conversation", {}).get(
                        "blocked_message", "I'm unable to help with that request."
                    )
                    yield _stamp(SentenceEvent(text=blocked_text, sentence_index=0))
                    yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                    return

            system = self._manager_agent.build_system_prompt(
                agent_system_prompt=self._workflow.agent_system_prompt,
                subagent_system_prompt=next_subagent.system_prompt,
                detected_language=final_language,
                channel=turn_input.channel,
                profile=profile_context,
                channel_config=channel_config,
                is_resumption=is_resumption,
                guardrail_constraints=guardrail_constraints,
                user_state_guidance=stream_user_state_guidance_text,
                session_end_eval_prompt=(
                    self._session_end_eval_prompt if self._session_end_eval_enabled else None
                ),
            )

            if is_resumption:
                bundle.session["was_adopted"] = False
                await self._async_memory.write(session_id, user_id, "session", "was_adopted", False)

            messages = self._manager_agent.build_messages(
                user_message=turn_input.user_message,
                current_question=current_question,
            )

            # ── #193: prepend prior tool_use/tool_result exchanges ──────
            # Persisted by this same path on the previous turn under the
            # session-scoped ``recent_tool_exchanges`` key. Replaying them
            # as real Anthropic tool-use messages keeps the LLM aware of
            # results it has already seen, so it does not re-invoke the
            # same tool with identical params on every follow-up turn.
            _max_items, _max_chars = self._recent_tool_exchanges_caps()
            _prior_exchanges_raw = bundle.session.get("recent_tool_exchanges") or []
            if not isinstance(_prior_exchanges_raw, list):
                _prior_exchanges_raw = []
            _prior_exchanges: list[dict] = list(_prior_exchanges_raw)
            if _max_items > 0 and _prior_exchanges:
                _replay_msgs = self._build_tool_exchange_messages(
                    _prior_exchanges[-_max_items:]
                )
                if _replay_msgs:
                    messages = _replay_msgs + messages
                    logger.info(
                        "orchestrator.stream_turn_tool_replay",
                        extra={
                            "operation": "orchestrator.stream_turn",
                            "status": "success",
                            "session_id": session_id,
                            "replayed_exchanges": len(_replay_msgs) // 2,
                        },
                    )

            # Tool exchanges captured during *this* turn's tool rounds; persisted
            # at the end of the turn so the next turn can replay them.
            _captured_exchanges_this_turn: list[dict] = []

            if not messages:
                yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                return

            if _aborted():
                return

            # ── Step 8: LLM streaming ──────────────────────────────────
            active_tools = self._workflow.resolve_tools_for(next_subagent_id)
            sentence_index = 0
            token_buffer = ""
            primary_model = self._config.get("agent", {}).get("primary_model", "unknown")

            # GH-196 — Trust /check/output batcher (config-driven).
            _batch_cfg = (
                self._config.get("trust_client", {}).get("check_output_batch", {})
                or {}
            )
            _trust_batcher = _TrustOutputBatcher(
                check_output=self._async_trust.check_output,
                session_id=session_id,
                max_sentences=int(_batch_cfg.get("max_sentences", 3)),
                max_interval_ms=int(_batch_cfg.get("max_interval_ms", 500)),
                fallback_message=self._safe_fallback_message(),
                enabled=bool(_batch_cfg.get("enabled", True)),
            )
            logger.info(
                "  [STEP 8] LLM Stream Call #1  →  Anthropic API (model=%s)"
                "  tools_available=%d  message_count=%d",
                primary_model, len(active_tools), len(messages),
            )
            t8 = time.time()

            # GH-194: per-channel response-length cap (None → wrapper default).
            channel_max_tokens = channel_config.get("max_tokens")

            try:
                async for token in self._llm.stream_call(
                    messages=messages,
                    tools=active_tools if active_tools else None,
                    system=system,
                    max_tokens=channel_max_tokens,
                    abort_event=abort_event,
                ):
                    if _aborted():
                        return
                    token_buffer += token
                    sentences, token_buffer = _split_sentences(token_buffer)
                    # Stop accepting new sentences once a batch was blocked —
                    # subsequent sentences in this turn must NOT reach TTS.
                    if _trust_batcher.was_escalated:
                        was_escalated = True
                        continue
                    pending_emit: list[str] = []
                    for sentence in sentences:
                        released = await _trust_batcher.add(sentence)
                        if released:
                            pending_emit.extend(released)
                            yield _stamp(SignalEvent(stage="trust_output", status="complete"))
                            yield _stamp(SignalEvent(stage="trust_output", status="start"))
                    # Time-based flush even if no new sentence triggered size.
                    timed = await _trust_batcher.maybe_flush_on_tick()
                    if timed:
                        pending_emit.extend(timed)
                    if _trust_batcher.was_escalated:
                        was_escalated = True
                    for emit in pending_emit:
                        if _aborted():
                            return
                        full_response_text += emit + " "
                        yield _stamp(SentenceEvent(text=emit, sentence_index=sentence_index))
                        sentence_index += 1
                        if _trust_batcher.was_escalated:
                            # Drop everything queued after the blocked batch.
                            break

                model_used = self._llm.get_active_model()
                logger.info(
                    "  [STEP 8] LLM Stream Call #1  ✓  model_used=%s"
                    "  sentences=%d  latency=%dms",
                    model_used, sentence_index, int((time.time() - t8) * 1000),
                )

            except ToolUseRequested as e:
                # ── Step 9: Tool use ───────────────────────────────────
                was_tool_used = True
                all_tool_calls = e.tool_calls
                tool_names = [tc.tool_name for tc in e.tool_calls]
                logger.info(
                    "  [STEP 8] LLM Stream Call #1  ✓  stop_reason=tool_use  tools=%s  latency=%dms",
                    tool_names, int((time.time() - t8) * 1000),
                )
                logger.info("  [STEP 9] Tool-Use Loop  →  executing tools=%s", tool_names)
                t9 = time.time()

                yield _stamp(SignalEvent(stage="tool_start", status="start"))
                tool_results_for_llm = []
                _stream_tool_results = []  # Collect ToolResult objects for post-tool hook
                # Build ke_context for knowledge_retrieval tool (same as sync path)
                _ke_context = {
                    "session_id": session_id,
                    "user_message": turn_input.user_message,
                    "profile": bundle.profile,
                    "session": bundle.session,
                    "intent": nlu_result.intent,
                    "entities": nlu_result.entities,
                    "sentiment": nlu_result.sentiment,
                    "confidence": nlu_result.confidence,
                    "normalised_input": normalised_input,
                    "detected_language": detected_language,
                }
                for tc in e.tool_calls:
                    # GH-191: ``end_session`` is an internal signal. Mirror the
                    # sync path (manager_agent.run_turn): never dispatch it to
                    # Action Gateway; just flip the flag so the DoneEvent below
                    # carries ``session_ended=True`` and the voice adapter can
                    # close the call.
                    if tc.tool_name == "end_session":
                        session_ended = True
                        self._manager_agent._session_ended_flag = True
                        logger.info(
                            "orchestrator.stream_end_session",
                            extra={
                                "operation": "orchestrator.stream_turn",
                                "status": "success",
                                "tool_name": "end_session",
                                "session_id": session_id,
                            },
                        )
                        tool_result = ToolResult(
                            tool_use_id=tc.tool_use_id,
                            tool_name="end_session",
                            result={"acknowledged": True},
                            success=True,
                            result_text="Session end acknowledged.",
                        )
                    # Route internal tools (e.g. knowledge_retrieval) to KE,
                    # not through Action Gateway.
                    elif self._tool_registry.get_route(tc.tool_name) == "knowledge_engine":
                        tool_result = await asyncio.to_thread(
                            self._manager_agent._execute_knowledge_retrieval,
                            tc, _ke_context,
                        )
                    elif self._async_gateway:
                        tool_result = await self._async_gateway.execute(tc, session_id, user_id)
                    else:
                        # Fallback: no async gateway — cannot execute tools in streaming mode
                        logger.error(
                            "orchestrator.stream_turn_no_async_gateway",
                            extra={"session_id": session_id, "tool_name": tc.tool_name},
                        )
                        break
                    _stream_tool_results.append(tool_result)
                    tool_results_for_llm.append({
                        "type": "tool_result",
                        "tool_use_id": tc.tool_use_id,
                        "content": tool_result.result_text or str(tool_result.result),
                    })
                yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                if _aborted():
                    return
                logger.info(
                    "  [STEP 9] Tool-Use Loop  ✓  tools_called=%s  latency=%dms",
                    tool_names, int((time.time() - t9) * 1000),
                )
                logger.info(
                    "  [STEP 8] LLM Stream Call #2  →  Anthropic API (model=%s)"
                    "  message_count=%d",
                    primary_model, len(messages) + 2,
                )
                t8b = time.time()

                # Resume streaming with tool results — loop handles multi-step tool chains
                _MAX_TOOL_ROUNDS: int = self._config.get("agent", {}).get("max_tool_rounds", 3)
                _current_tool_calls = e.tool_calls
                _current_tool_results = tool_results_for_llm
                _tool_round = 1

                while True:
                    messages.append({"role": "assistant", "content": [
                        {"type": "tool_use", "id": tc.tool_use_id, "name": tc.tool_name, "input": tc.input_params}
                        for tc in _current_tool_calls
                    ]})
                    messages.append({"role": "user", "content": _current_tool_results})

                    # #193: snapshot this round so it can be replayed next turn.
                    _ex = self._capture_tool_exchange(
                        _current_tool_calls, _current_tool_results, _max_chars,
                    )
                    if _ex is not None:
                        _captured_exchanges_this_turn.append(_ex)

                    if _aborted():
                        return
                    try:
                        async for token in self._llm.stream_call(
                            messages=messages,
                            tools=active_tools if active_tools else None,
                            system=system,
                            max_tokens=channel_max_tokens,
                            abort_event=abort_event,
                        ):
                            if _aborted():
                                return
                            token_buffer += token
                            sentences, token_buffer = _split_sentences(token_buffer)
                            if _trust_batcher.was_escalated:
                                was_escalated = True
                                continue
                            pending_emit = []
                            for sentence in sentences:
                                released = await _trust_batcher.add(sentence)
                                if released:
                                    pending_emit.extend(released)
                                    yield _stamp(SignalEvent(stage="trust_output", status="complete"))
                                    yield _stamp(SignalEvent(stage="trust_output", status="start"))
                            timed = await _trust_batcher.maybe_flush_on_tick()
                            if timed:
                                pending_emit.extend(timed)
                            if _trust_batcher.was_escalated:
                                was_escalated = True
                            for emit in pending_emit:
                                if _aborted():
                                    return
                                full_response_text += emit + " "
                                yield _stamp(SentenceEvent(text=emit, sentence_index=sentence_index))
                                sentence_index += 1
                                if _trust_batcher.was_escalated:
                                    break
                        break  # LLM responded with text — tool loop complete

                    except ToolUseRequested as nested_e:
                        _tool_round += 1
                        if _tool_round > _MAX_TOOL_ROUNDS:
                            logger.warning(
                                "orchestrator.stream_turn_max_tool_rounds",
                                extra={"session_id": session_id, "rounds": _tool_round},
                            )
                            break

                        _nested_tool_names = [tc.tool_name for tc in nested_e.tool_calls]
                        logger.info(
                            "  [STEP 9] Tool-Use Loop (round %d)  →  executing tools=%s",
                            _tool_round, _nested_tool_names,
                        )
                        yield _stamp(SignalEvent(stage="tool_start", status="start"))
                        _nested_results = []
                        for tc in nested_e.tool_calls:
                            # GH-191: intercept end_session in nested rounds too.
                            if tc.tool_name == "end_session":
                                session_ended = True
                                self._manager_agent._session_ended_flag = True
                                logger.info(
                                    "orchestrator.stream_end_session",
                                    extra={
                                        "operation": "orchestrator.stream_turn",
                                        "status": "success",
                                        "tool_name": "end_session",
                                        "session_id": session_id,
                                        "tool_round": _tool_round,
                                    },
                                )
                                tool_result = ToolResult(
                                    tool_use_id=tc.tool_use_id,
                                    tool_name="end_session",
                                    result={"acknowledged": True},
                                    success=True,
                                    result_text="Session end acknowledged.",
                                )
                            elif self._tool_registry.get_route(tc.tool_name) == "knowledge_engine":
                                tool_result = await asyncio.to_thread(
                                    self._manager_agent._execute_knowledge_retrieval,
                                    tc, _ke_context,
                                )
                            elif self._async_gateway:
                                tool_result = await self._async_gateway.execute(tc, session_id, user_id)
                            else:
                                break
                            _nested_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.tool_use_id,
                                "content": tool_result.result_text or str(tool_result.result),
                            })
                            _stream_tool_results.append(tool_result)
                        yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                        if _aborted():
                            return
                        logger.info(
                            "  [STEP 9] Tool-Use Loop (round %d)  ✓  tools=%s",
                            _tool_round, _nested_tool_names,
                        )
                        _current_tool_calls = nested_e.tool_calls
                        _current_tool_results = _nested_results

                model_used = self._llm.get_active_model()
                logger.info(
                    "  [STEP 8] LLM Stream Call #2  ✓  model_used=%s  latency=%dms",
                    model_used, int((time.time() - t8b) * 1000),
                )

                # Post-tool hook: apply_job success → post_applied transition
                # Mirror of the sync path hook. Guard ensures domain-agnosticism:
                # workflows without a post_applied subagent get a no-op.
                if _stream_tool_results and "post_applied" in self._workflow.subagents:
                    for tr in _stream_tool_results:
                        if getattr(tr, "tool_name", None) == "apply_job" and getattr(tr, "success", False):
                            await self._async_memory.write(
                                session_id, user_id, "session",
                                "current_subagent_id", "post_applied",
                            )
                            bundle.session["current_subagent_id"] = "post_applied"
                            logger.info(
                                "orchestrator.post_applied_transition",
                                extra={
                                    "operation": "orchestrator.post_tool_hook",
                                    "status": "success",
                                    "session_id": session_id,
                                    "trigger_tool": "apply_job",
                                },
                            )
                            break

            # Flush remaining token buffer as a final sentence into the batcher,
            # then drain the batcher in one final Trust call (turn-end flush).
            remaining = token_buffer.strip()
            if remaining and not _trust_batcher.was_escalated:
                await _trust_batcher.add(remaining)
            yield _stamp(SignalEvent(stage="trust_output", status="start"))
            final_release = await _trust_batcher.flush()
            yield _stamp(SignalEvent(stage="trust_output", status="complete"))
            if _trust_batcher.was_escalated:
                was_escalated = True
            if _aborted():
                return
            for emit in final_release:
                full_response_text += emit + " "
                yield _stamp(SentenceEvent(text=emit, sentence_index=sentence_index))
                sentence_index += 1
                if _trust_batcher.was_escalated:
                    break
            full_response_text = full_response_text.rstrip()

            # ── Step 11: Write current_question ────────────────────────
            # GH-151 #5: fire-and-forget. The next turn reads context_bundle,
            # which includes current_question; by the time the caller finishes
            # speaking and STT/TurnAssembler have produced a segment (hundreds
            # of ms later at minimum), the Redis write has landed. Awaiting
            # it synchronously here blocked the DoneEvent by ~5–100 ms on
            # every turn with no functional benefit.
            logger.info(
                "  [STEP 11] Delivering response  (async: memory write + learning emit follow)",
            )
            if _aborted():
                return
            yield _stamp(SignalEvent(stage="memory_write", status="start"))
            # #207: sanitize defends against accidental concatenation upstream
            # and caps the value to a sane ceiling.
            stream_cq_value = self._sanitize_current_question(
                prev=bundle.session.get("current_question", ""),
                new=full_response_text,
                session_id=session_id,
            )
            asyncio.create_task(
                self._async_memory.write(
                    session_id, user_id, "session", "current_question", stream_cq_value
                )
            )

            # #193: persist captured tool exchanges (capped) so the next
            # turn can replay them as real tool_use/tool_result messages.
            if _captured_exchanges_this_turn and _max_items > 0:
                _merged = list(_prior_exchanges) + _captured_exchanges_this_turn
                _capped = _merged[-_max_items:]
                bundle.session["recent_tool_exchanges"] = _capped
                asyncio.create_task(
                    self._async_memory.write(
                        session_id, user_id, "session",
                        "recent_tool_exchanges", _capped,
                    )
                )
                logger.info(
                    "orchestrator.stream_turn_tool_persist",
                    extra={
                        "operation": "orchestrator.stream_turn",
                        "status": "success",
                        "session_id": session_id,
                        "captured": len(_captured_exchanges_this_turn),
                        "stored": len(_capped),
                    },
                )
            yield _stamp(SignalEvent(stage="memory_write", status="complete"))

            latency_ms = int((time.time() - start) * 1000)
            logger.info(
                "orchestrator.stream_turn_complete",
                extra={
                    "operation": "orchestrator.stream_turn",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                    "model": model_used,
                    "tool_used": was_tool_used,
                    "intent": nlu_result.intent,
                    "next_subagent_id": next_subagent_id,
                },
            )
            logger.info(
                "\n═══════════════════════════════════════════════════════════════\n"
                "  STREAM TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
                "  model=%s  total_latency=%dms  next_subagent=%s\n"
                "  response: %r\n"
                "═══════════════════════════════════════════════════════════════",
                session_id, nlu_result.intent, was_tool_used,
                model_used, latency_ms, next_subagent_id,
                full_response_text.strip()[:200],
            )

            # ── Yield DoneEvent (terminal) ─────────────────────────────
            yield _stamp(DoneEvent(
                was_escalated=was_escalated,
                was_tool_used=was_tool_used,
                model_used=model_used,
                latency_ms=latency_ms,
                turn_id=turn_id,
                # GH-191: prefer the locally-tracked flag (streaming tool loop
                # sets it directly when end_session is invoked); fall back to
                # the manager_agent flag for parity with the sync path.
                session_ended=session_ended or bool(getattr(self._manager_agent, "session_ended", False)),
            ))

            # ── Steps 12-13: Async post-turn ───────────────────────────
            asyncio.create_task(
                self._async_post_turn(
                    session_id=session_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    response_text=full_response_text.strip(),
                    user_message=turn_input.user_message,
                    trust_input=trust_input,
                    trust_output=trust_output,
                    model_used=model_used,
                    intent=nlu_result.intent,
                    tool_calls=all_tool_calls,
                    latency_ms=latency_ms,
                    timestamp_ms=turn_input.timestamp_ms,
                )
            )

        except Exception as e:
            logger.error(
                "orchestrator.stream_turn_error",
                extra={
                    "operation": "orchestrator.stream_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            yield _stamp(DoneEvent(
                turn_id=turn_id,
                turn_status="abandoned",
                latency_ms=int((time.time() - start) * 1000),
            ))

    async def _async_post_turn(
        self,
        session_id: str,
        user_id: str,
        turn_id: str,
        response_text: str,
        user_message: str,
        trust_input: TrustCheckResult,
        trust_output: TrustCheckResult,
        model_used: str,
        intent: str,
        tool_calls: list[ToolCall],
        latency_ms: int,
        timestamp_ms: int,
    ) -> None:
        """Run Steps 12-13 asynchronously after DoneEvent is yielded.

        Writes last_response to Memory Layer and emits turn event to
        Observability Layer. Never raises.
        """
        try:
            # Step 11b: Record audit turn
            if self._async_memory:
                await self._async_memory.record_audit_turn(
                    session_id=session_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    user_message=user_message,
                    system_message=response_text,
                    metadata={"model": model_used, "intent": intent, "latency_ms": latency_ms},
                )

            # Step 12: Write last_response
            if self._async_memory:
                await self._async_memory.write(session_id, user_id, "session", "last_response", response_text)

            # Step 13: Emit to Observability Layer
            if self._async_learning:
                turn_event = TurnEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=response_text,
                    tool_calls=tool_calls,
                    trust_input_result=trust_input,
                    trust_output_result=trust_output,
                    model_used=model_used,
                    intent=intent,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    timestamp_ms=timestamp_ms,
                    trace_id=self._current_trace_id(),
                )
                await self._async_learning.emit_turn(turn_event)

        except Exception as e:
            logger.error(
                "orchestrator.async_post_turn_error",
                extra={
                    "operation": "orchestrator._async_post_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------

# Regex for sentence splitting — splits on . ? ! । (Devanagari danda U+0964)
# ？ (fullwidth question mark U+FF1F) followed by whitespace or end-of-string.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!।？])\s+")


class _TrustOutputBatcher:
    """Buffer streamed sentences and flush a single Trust Layer ``check_output``.

    GH-196 (P4-D). On a long turn, hitting ``/check/output`` once per sentence
    contributes 0.5–2 s of pure overhead. This helper batches sentences so the
    Trust Layer sees ``ceil(N_sentences / max_sentences)`` calls instead.

    Flush triggers (whichever fires first):
      * buffer length reaches ``max_sentences``
      * elapsed wall-clock since the first buffered sentence reaches
        ``max_interval_ms``
      * caller invokes ``flush()`` (turn end / pre-DoneEvent cleanup)

    Verdict semantics:
      * ``allow``  — release every buffered sentence verbatim.
      * ``block`` / ``escalate`` — release a single fallback sentence in
        place of the entire pending batch and set ``was_escalated``. The
        caller is responsible for not pushing further batches to TTS once
        ``was_escalated`` flips true (the orchestrator stops feeding new
        sentences after a blocked verdict).

    Sentences released by earlier batches that were already streamed to the
    user are NOT retracted — that is acceptable per the spec.

    Trust infra failures (network/timeout) fall back to ``allow`` to preserve
    the existing per-sentence behaviour and avoid silently dropping output.
    """

    def __init__(
        self,
        *,
        check_output: Any,
        session_id: str,
        max_sentences: int,
        max_interval_ms: int,
        fallback_message: str,
        enabled: bool = True,
        time_fn: Any = None,
    ) -> None:
        """Initialise the batcher.

        Args:
            check_output: Awaitable ``async def check_output(session_id, text)``
                returning a ``TrustCheckResult``-like object with ``passed``
                and ``action`` attributes.
            session_id: Session identifier passed to every Trust call.
            max_sentences: Flush trigger by buffer size (>= 1).
            max_interval_ms: Flush trigger by elapsed ms (>= 1).
            fallback_message: Replacement text for the entire batch on
                ``block`` / ``escalate``.
            enabled: When False, ``add()`` flushes immediately (one
                Trust call per sentence — legacy behaviour).
            time_fn: Override for ``time.monotonic`` (test seam).
        """
        if max_sentences < 1:
            raise ValueError("max_sentences must be >= 1")
        if max_interval_ms < 1:
            raise ValueError("max_interval_ms must be >= 1")
        self._check_output = check_output
        self._session_id = session_id
        self._max_sentences = 1 if not enabled else max_sentences
        self._max_interval_ms = max_interval_ms
        self._fallback = fallback_message
        self._enabled = enabled
        self._time_fn = time_fn or time.monotonic
        self._buffer: list[str] = []
        self._batch_start: float | None = None
        self.was_escalated: bool = False
        self.batch_count: int = 0

    def _should_flush(self) -> bool:
        """Return True iff size or time threshold has been crossed."""
        if not self._buffer:
            return False
        if len(self._buffer) >= self._max_sentences:
            return True
        if self._batch_start is None:
            return False
        elapsed_ms = (self._time_fn() - self._batch_start) * 1000
        return elapsed_ms >= self._max_interval_ms

    async def add(self, sentence: str) -> list[str]:
        """Buffer a sentence and flush if a threshold has been reached.

        Args:
            sentence: Sentence to enqueue. Empty / whitespace-only inputs
                are ignored.

        Returns:
            Sentences ready for TTS / SentenceEvent emission. May be empty
            if the buffer is still filling.
        """
        if not sentence or not sentence.strip():
            return []
        if self._batch_start is None:
            self._batch_start = self._time_fn()
        self._buffer.append(sentence)
        if self._should_flush():
            return await self._flush_now()
        return []

    async def maybe_flush_on_tick(self) -> list[str]:
        """Flush only if the time threshold has elapsed; never on size alone.

        Returns:
            Sentences released by the time-based flush, or empty list.
        """
        if not self._buffer or self._batch_start is None:
            return []
        elapsed_ms = (self._time_fn() - self._batch_start) * 1000
        if elapsed_ms >= self._max_interval_ms:
            return await self._flush_now()
        return []

    async def flush(self) -> list[str]:
        """Force a flush of any buffered sentences (turn end).

        Returns:
            Sentences released, or empty list if nothing was buffered.
        """
        if not self._buffer:
            return []
        return await self._flush_now()

    async def _flush_now(self) -> list[str]:
        """Submit the current buffer to Trust Layer and return release list."""
        batch = self._buffer
        self._buffer = []
        self._batch_start = None
        if not batch:
            return []
        self.batch_count += 1
        joined = " ".join(batch)
        start = time.time()
        try:
            verdict = await self._check_output(self._session_id, joined)
            latency_ms = int((time.time() - start) * 1000)
            logger.info(
                "trust_output_batcher.flush",
                extra={
                    "operation": "trust_output_batcher.flush",
                    "status": "success",
                    "session_id": self._session_id,
                    "batch_size": len(batch),
                    "batch_index": self.batch_count,
                    "latency_ms": latency_ms,
                    "passed": getattr(verdict, "passed", True),
                    "action": getattr(verdict, "action", "allow"),
                },
            )
            if not getattr(verdict, "passed", True):
                self.was_escalated = True
                return [self._fallback]
            return batch
        except Exception as exc:  # noqa: BLE001
            # Spec: trust infra failure → treat as allow, log, do not crash.
            logger.error(
                "trust_output_batcher.flush_infra_failure",
                extra={
                    "operation": "trust_output_batcher.flush",
                    "status": "failure",
                    "session_id": self._session_id,
                    "batch_size": len(batch),
                    "batch_index": self.batch_count,
                    "latency_ms": int((time.time() - start) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return batch


def _split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split accumulated text into complete sentences and a remainder.

    Args:
        buffer: Accumulated text from LLM token stream.

    Returns:
        Tuple of (complete_sentences, remaining_buffer).
        Complete sentences are stripped. Remaining buffer holds text
        after the last sentence boundary (may be empty).
    """
    parts = _SENTENCE_SPLIT_RE.split(buffer)
    if len(parts) <= 1:
        # No sentence boundary found — entire buffer is remainder
        return [], buffer

    # All parts except the last are complete sentences
    sentences = [p.strip() for p in parts[:-1] if p.strip()]
    remainder = parts[-1]
    return sentences, remainder
