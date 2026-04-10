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

import logging
import threading
import time
import uuid
from typing import Any, Optional

from src.base import AgentCoreBase
from src.interfaces.action_gateway import ActionGatewayBase
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
    LLMResponse,
    NLUResult,
    ToolCall,
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

        # Language Normalisation and NLU run directly in Agent Core.
        # Stateless — instantiated once, reused across all sessions.
        self._language_normaliser = LanguageNormaliser()
        self._nlu_processor = NLUProcessor(self._config)

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

        # ── Step 1: Read session state ────────────────────────────────
        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        logger.info(
            "  [STEP 1] Memory context_bundle  →  POST %s/context_bundle  (session=%s)",
            memory_endpoint, session_id,
        )
        t1 = time.time()
        bundle = self._memory.context_bundle(session_id, user_id)
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

        # ── Consent gate (Step 1b) ────────────────────────────────────
        ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
        if ask_for_consent:
            user_storage_mode: str | None = bundle.session.get("user_storage_mode")
            turn_count: int = int(bundle.session.get("turn_count", 0) or 0)

            if user_storage_mode is None and turn_count == 0:
                # Turn 1: deliver consent prompt, no LLM call, no Trust Layer call
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
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=consent_prompt_text,
                    latency_ms=int((time.time() - start) * 1000),
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
            # if user_storage_mode is set → fall through, skip consent gate entirely

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

        # ── Step 4: Language Normalisation ───────────────────────────
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
        
        # Save language preference if new, or update if user switched language
        saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
        if not saved_preference or (turn_language and turn_language != saved_preference):
            if turn_language and turn_language != saved_preference:
                language_preference = turn_language
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

        # ── Step 5: NLU Processor ─────────────────────────────────────
        allowed_intents = self._workflow.nlu_intent_set.get(current_subagent_id, [])
        nlu_model = (
            self._config.get("preprocessing", {})
            .get("nlu_processor", {})
            .get("model_override", "haiku")
        )
        logger.info(
            "  [STEP 5] NLU Processor  →  LLM call (model_override=%s)"
            "  current_subagent_id=%s  allowed_intents=%d  current_question=%r",
            nlu_model, current_subagent_id, len(allowed_intents),
            current_question[:60] if current_question else "",
        )
        t5 = time.time()
        nlu_result = self._nlu_processor.process(
            normalised_input=normalised_input,
            current_question=current_question,
            current_subagent_id=current_subagent_id,
            llm=self._llm,
            allowed_intents=allowed_intents,
        )
        logger.info(
            "  [STEP 5] NLU Processor  ✓  intent=%s  confidence=%.2f  entities=%s"
            "  sentiment=%s  latency=%dms",
            nlu_result.intent, nlu_result.confidence,
            nlu_result.entities if nlu_result.entities else {},
            nlu_result.sentiment,
            int((time.time() - t5) * 1000),
        )
        
        # After NLU: write extracted entities synchronously so routing in Step 6
        # sees current-turn values, not only last-turn state.
        # Scope is config-driven — entity_persistence.scope in domain config.
        # DPDP compliance is handled by Memory Layer at flush_session(): all entities
        # are written to Neo4j during the session; if user_storage_mode == "anonymous"
        # the Memory Layer DETACH DELETEs the user graph when the session ends.
        entity_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
        entity_map: dict = self._config.get("entity_to_profile_field", {})
        for entity_key, entity_val in (nlu_result.entities or {}).items():
            profile_field = entity_map.get(entity_key, entity_key)
            self._write_memory_sync(session_id, user_id, entity_scope, profile_field, entity_val)
            bundle.session[profile_field] = entity_val

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
            is_resumption=is_resumption,
            guardrail_constraints=guardrail_constraints,
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
        active_tools = self._workflow.tool_defs.get(next_subagent_id, [])
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
        final_text, tool_calls = self._manager_agent.run_turn(
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
        self._write_memory_sync(session_id, user_id, "session", "current_question", final_text)
        bundle.session["current_question"] = final_text

        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "  [STEP 11] Delivering response to caller  (async: memory write + learning emit follow)",
        )

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
            trace_id=_trace_id,
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
        trust_result: TrustCheckResult,
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
                "reason": trust_result.reason,
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
        trust_result: TrustCheckResult,
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
                "reason": trust_result.reason,
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
