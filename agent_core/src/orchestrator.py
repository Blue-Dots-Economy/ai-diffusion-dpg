"""
agent_core/orchestrator.py

Concrete implementation of AgentCoreBase.
Wires all components and executes the turn sequence.

Design rules enforced here:
- Trust Layer is called exactly twice per turn (input + output). Neither is skippable.
- Agent Core holds zero session state between turns.
- Language Normalisation and NLU Processor run directly in Agent Core (steps 3-4)
  using the primary LLM wrapper with a model_override to Haiku.
- NLU receives current_question and workflow_step (not history) for context resolution.
- Early exit at step 5 if intent is unknown or confidence is below threshold.
- HITL bypass if loop_count >= hitl.loop_count_threshold before the LLM call.
- Termination intent triggers flush_session via async daemon thread.
- Steps 11-12 (memory write, learning emit) run in a daemon thread after TurnResult is returned.
- This is the only file that imports and coordinates all DPG interfaces together.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from src.base import AgentCoreBase
from src.interfaces.action_gateway import ActionGatewayBase
from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.interfaces.learning_layer import LearningLayerBase
from src.interfaces.memory_layer import MemoryLayerBase
from src.interfaces.reach_layer import ReachLayerBase
from src.preprocessing.language_normalisation import LanguageNormaliser
from src.llm_wrapper.base import LLMWrapperBase
from src.manager_agent import ManagerAgent
from src.models import (
    ContextBundle,
    LLMResponse,
    NLUResult,
    RetrievalChunk,
    ToolCall,
    TrustCheckResult,
    TurnEvent,
    TurnInput,
    TurnResult,
)
from src.preprocessing.nlu_processor import NLUProcessor
from src.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


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
        learning:         Learning Layer interface (async emit).
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
        learning: LearningLayerBase,
    ) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._config = config
        self._llm = llm_wrapper
        self._memory = memory
        self._trust = trust
        self._knowledge_engine = knowledge_engine
        self._tool_registry = tool_registry
        self._manager_agent = manager_agent
        self._learning = learning

        # Language Normalisation and NLU run directly in Agent Core.
        # Stateless — instantiated once, reused across all sessions.
        self._language_normaliser = LanguageNormaliser()
        self._nlu_processor = NLUProcessor()

    # ------------------------------------------------------------------
    # Public interface — single entry point
    # ------------------------------------------------------------------

    def process_turn(self, turn_input: TurnInput) -> TurnResult:
        """
        Execute one full conversation turn. See AgentCoreBase for full contract.
        """
        if turn_input is None:
            raise ValueError("turn_input must not be None")
        if not turn_input.session_id:
            raise ValueError("turn_input.session_id must not be empty")
        if turn_input.user_message is None:
            raise ValueError("turn_input.user_message must not be None")

        start = time.time()
        session_id = turn_input.session_id
        # PoC fallback: use session_id as user_id if caller didn't provide one
        user_id: str = turn_input.user_id or session_id

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

        # ── Step 1: Read context bundle ───────────────────────────────
        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        logger.info(
            "  [STEP 1] Memory context_bundle  →  POST %s/context_bundle  (session=%s)",
            memory_endpoint, session_id,
        )
        t1 = time.time()
        bundle = self._memory.context_bundle(session_id, user_id)
        current_node = bundle.session.get("current_node", "")
        current_question = bundle.session.get("current_question", "")
        loop_count = int(bundle.session.get("loop_count", 0))
        logger.info(
            "  [STEP 1] Memory context_bundle  ✓  current_node=%s  loop_count=%d"
            "  is_returning=%s  latency=%dms",
            current_node, loop_count,
            bundle.session.get("is_returning", False),
            int((time.time() - t1) * 1000),
        )

        # ── Step 2: Trust check on input ─────────────────────────────
        trust_endpoint = (
            self._config.get("trust_client", {}).get("endpoint", "http://trust_layer:8003")
        )
        logger.info(
            "  [STEP 2] Trust Input Check  →  POST %s/check/input  (session=%s)",
            trust_endpoint, session_id,
        )
        t2 = time.time()
        trust_input = self._trust.check_input(session_id, turn_input.user_message)
        logger.info(
            "  [STEP 2] Trust Input Check  ✓  action=%s  passed=%s  reason=%s  latency=%dms",
            trust_input.action, trust_input.passed,
            trust_input.reason or "—", int((time.time() - t2) * 1000),
        )

        if trust_input.action == "block":
            logger.info(
                "  [STEP 2] ✗ INPUT BLOCKED — reason=%s  →  returning blocked response",
                trust_input.reason,
            )
            return self._blocked_response(session_id, trust_input, start, trust_input)

        if trust_input.action == "escalate":
            logger.info(
                "  [STEP 2] ⚠ INPUT ESCALATED — reason=%s  →  routing to human agent",
                trust_input.reason,
            )
            self._schedule_flush(session_id, user_id, "escalation_trust_input")
            return self._escalated_response(session_id, trust_input, start, trust_input)

        # ── HITL bypass: escalate if loop_count exceeds threshold ─────
        hitl_threshold = int(
            self._config.get("hitl", {}).get("loop_count_threshold", 3)
        )
        if loop_count >= hitl_threshold:
            logger.info(
                "  [HITL] loop_count=%d >= threshold=%d — escalating to human agent",
                loop_count, hitl_threshold,
            )
            hitl_trust = TrustCheckResult(
                passed=False, action="escalate", reason=f"hitl_loop_count={loop_count}"
            )
            self._schedule_flush(session_id, user_id, "hitl_loop_count")
            return self._escalated_response(session_id, hitl_trust, start, trust_input)

        # ── Step 3: Language Normalisation ───────────────────────────
        lang_model = (
            self._config.get("preprocessing", {})
            .get("language_normalisation", {})
            .get("model_override", "haiku")
        )
        logger.info(
            "  [STEP 3] Language Normalisation  →  LLM call (model_override=%s)",
            lang_model,
        )
        t3 = time.time()
        normalised_input, detected_language = self._language_normaliser.normalise(
            raw_input=turn_input.user_message,
            config=self._config,
            llm=self._llm,
        )
        logger.info(
            "  [STEP 3] Language Normalisation  ✓  detected_language=%s  normalised=%r  latency=%dms",
            detected_language or "en",
            (normalised_input or turn_input.user_message)[:100],
            int((time.time() - t3) * 1000),
        )

        # ── Step 4: NLU Processor ─────────────────────────────────────
        # Uses current_question and workflow_step from bundle (not history).
        nlu_model = (
            self._config.get("preprocessing", {})
            .get("nlu_processor", {})
            .get("model_override", "haiku")
        )
        logger.info(
            "  [STEP 4] NLU Processor  →  LLM call (model_override=%s)"
            "  workflow_step=%s  current_question=%r",
            nlu_model, current_node, current_question[:60] if current_question else "",
        )
        t4 = time.time()
        nlu_result = self._nlu_processor.process(
            normalised_input=normalised_input,
            current_question=current_question,
            workflow_step=current_node,
            config=self._config,
            llm=self._llm,
        )
        logger.info(
            "  [STEP 4] NLU Processor  ✓  intent=%s  confidence=%.2f  entities=%s"
            "  sentiment=%s  latency=%dms",
            nlu_result.intent, nlu_result.confidence,
            nlu_result.entities if nlu_result.entities else {},
            nlu_result.sentiment,
            int((time.time() - t4) * 1000),
        )

        # ── Step 5a: Termination intent check ────────────────────────
        termination_intents = self._config.get("workflow", {}).get(
            "termination_intents", ["termination_intent"]
        )
        if nlu_result.intent in termination_intents:
            logger.info(
                "  [STEP 5a] TERMINATION INTENT detected (%s) — flushing session",
                nlu_result.intent,
            )
            # Pick termination message: prefer language-specific variant if configured.
            # e.g. conversation.termination_message_english overrides conversation.termination_message
            conv_cfg = self._config.get("conversation", {})
            lang_key = f"termination_message_{detected_language}" if detected_language else ""
            termination_text = (
                (conv_cfg.get(lang_key) if lang_key else None)
                or conv_cfg.get("termination_message", "Thank you for your time. Goodbye!")
            )
            return self._build_result(
                session_id=session_id,
                user_id=user_id,
                response_text=termination_text,
                was_escalated=False,
                was_tool_used=False,
                model_used="",
                latency_ms=int((time.time() - start) * 1000),
                bundle=bundle,
                turn_input=turn_input,
                tool_calls=[],
                trust_input=trust_input,
                trust_output=TrustCheckResult(passed=True, action="allow"),
                nlu_result=nlu_result,
                do_flush=True,
                flush_reason="termination_intent",
            )

        # ── Step 5b: Early exit on unknown / low-confidence intent ────
        # BYPASS: during profile collection nodes, never do an early exit —
        # any answer (even vague) may contain entities or advance the round.
        _profile_collection_nodes = self._profile_nodes()
        confidence_threshold = (
            self._config.get("preprocessing", {})
            .get("nlu_processor", {})
            .get("confidence_threshold", 0.5)
        )
        logger.info(
            "  [STEP 5b] Intent Gate  →  intent=%s  confidence=%.2f  threshold=%.2f"
            "  current_node=%s",
            nlu_result.intent, nlu_result.confidence, confidence_threshold, current_node,
        )
        if current_node in _profile_collection_nodes:
            logger.info(
                "  [STEP 5b] Intent Gate  → BYPASSED (node=%s is in profile collection flow)",
                current_node,
            )
        elif nlu_result.confidence < confidence_threshold and nlu_result.intent == "unknown":
            logger.info(
                "  [STEP 5b] ✗ EARLY EXIT — intent=unknown and confidence=%.2f below threshold",
                nlu_result.confidence,
            )
            return self._unknown_intent_response(session_id, user_id, bundle,
                                                  turn_input, nlu_result, start)
        else:
            logger.info("  [STEP 5b] Intent Gate  ✓  proceeding")

        # ── Step 5b.1: Declarative State Machine Transitions ─────────
        transitions = self._config.get("workflow", {}).get("transitions", {})
        if current_node in transitions:
            node_transitions = transitions[current_node]
            if nlu_result.intent in node_transitions:
                next_node = node_transitions[nlu_result.intent]
                logger.info(
                    "  [STATE MACHINE] Transition: %s + intent=%s → %s",
                    current_node, nlu_result.intent, next_node
                )
                self._write_memory_sync(session_id, user_id, "session", "current_node", next_node)
                bundle.session["current_node"] = next_node
                current_node = next_node

        # ── Step 5c: Workflow gate (consent / profile collection) ─────
        gate_result = self._workflow_gate(
            session_id=session_id,
            user_id=user_id,
            bundle=bundle,
            nlu_result=nlu_result,
            turn_input=turn_input,
            start=start,
            trust_input=trust_input,
            detected_language=detected_language,
        )
        if gate_result is not None:
            logger.info(
                "  [STEP 5c] Workflow Gate  ✓  handled by gate (node=%s) — skipping LLM",
                current_node,
            )
            return gate_result
        # Re-read current_question: a gate may have updated bundle.session in-place
        # (e.g., setting a new question to ask this turn) and then returned None to fall
        # through to the LLM for language-aware delivery.
        current_question = bundle.session.get("current_question", current_question)
        logger.info("  [STEP 5c] Workflow Gate  → pass-through to LLM (node=%s)", current_node)

        # ── Step 6: Retrieve knowledge (KE) ──────────────────────────
        ke_endpoint = (
            self._config.get("ke_client", {}).get("endpoint", "http://knowledge_engine:8001/retrieve")
        )
        logger.info(
            "  [STEP 6] Knowledge Engine  →  POST %s  (intent=%s  entities=%s)",
            ke_endpoint, nlu_result.intent,
            nlu_result.entities if nlu_result.entities else {},
        )
        t6 = time.time()
        chunks: list[RetrievalChunk] = self._knowledge_engine.retrieve(
            session_id=session_id,
            user_message=turn_input.user_message,
            profile=bundle.profile,
            session=bundle.session,
            intent=nlu_result.intent,
            entities=nlu_result.entities,
            sentiment=nlu_result.sentiment,
            confidence=nlu_result.confidence,
            normalised_input=normalised_input,
            detected_language=detected_language,
        )
        logger.info(
            "  [STEP 6] Knowledge Engine  ✓  chunks=%d  latency=%dms",
            len(chunks), int((time.time() - t6) * 1000),
        )

        # ── Step 6.5: Build prompt via ManagerAgent ───────────────────
        system = self._manager_agent.build_system_prompt(
            profile=bundle.profile,
            session=bundle.session,
            detected_language=detected_language,
            config=self._config,
        )
        messages = self._manager_agent.build_messages(
            user_message=turn_input.user_message,
            chunks=chunks,
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
                bundle=bundle,
                turn_input=turn_input,
                tool_calls=[],
                trust_input=trust_input,
                trust_output=TrustCheckResult(passed=True, action="allow"),
                nlu_result=nlu_result,
            )

        # ── Step 7: LLM call #1 ──────────────────────────────────────
        tools = self._tool_registry.get_tool_definitions()
        primary_model = self._config.get("agent", {}).get("primary_model", "unknown")
        logger.info(
            "  [STEP 7] LLM Call #1  →  Anthropic API (model=%s)"
            "  tools_available=%d  message_count=%d",
            primary_model, len(tools), len(messages),
        )
        t7 = time.time()
        llm_response = self._llm.call(messages=messages, tools=tools, system=system)
        logger.info(
            "  [STEP 7] LLM Call #1  ✓  stop_reason=%s  model_used=%s"
            "  input_tokens=%d  output_tokens=%d  latency=%dms",
            llm_response.stop_reason, llm_response.model_used,
            llm_response.input_tokens, llm_response.output_tokens,
            int((time.time() - t7) * 1000),
        )
        if llm_response.stop_reason == "tool_use":
            logger.info("  [STEP 7]   → LLM requested tool use — entering tool loop")

        # ── Step 8: Tool-use loop ─────────────────────────────────────
        ag_endpoint = (
            self._config.get("action_gateway_client", {}).get("endpoint", "http://action_gateway:8005")
        )
        logger.info(
            "  [STEP 8] Tool-Use Loop  →  Action Gateway %s (if tool requested)", ag_endpoint,
        )
        t8 = time.time()
        final_text, tool_calls = self._manager_agent.run_turn(
            messages=messages,
            session_id=session_id,
            initial_llm_response=llm_response,
            system=system,
        )
        if tool_calls:
            tool_names = [tc.tool_name for tc in tool_calls]
            logger.info(
                "  [STEP 8] Tool-Use Loop  ✓  tools_called=%s  latency=%dms",
                tool_names, int((time.time() - t8) * 1000),
            )
        else:
            logger.info(
                "  [STEP 8] Tool-Use Loop  ✓  no tool used — direct LLM response  latency=%dms",
                int((time.time() - t8) * 1000),
            )

        # ── Step 9: Trust check on output ────────────────────────────
        logger.info(
            "  [STEP 9] Trust Output Check  →  POST %s/check/output  (session=%s)",
            trust_endpoint, session_id,
        )
        t9 = time.time()
        trust_output = self._trust.check_output(session_id, final_text)
        logger.info(
            "  [STEP 9] Trust Output Check  ✓  action=%s  passed=%s  latency=%dms",
            trust_output.action, trust_output.passed, int((time.time() - t9) * 1000),
        )

        if trust_output.action in ("block", "escalate"):
            logger.info(
                "  [STEP 9] ⚠ OUTPUT %s — replacing with safe fallback",
                trust_output.action.upper(),
            )
            final_text = self._safe_fallback_message()

        # ── Step 10: Build result and return ─────────────────────────
        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "  [STEP 10] Delivering response to caller  (async: memory write + learning emit follow)",
        )

        # Save the actual text the LLM generated so next turn has exact context
        self._write_memory_sync(session_id, user_id, "session", "current_question", final_text)
        bundle.session["current_question"] = final_text

        result = self._build_result(
            session_id=session_id,
            user_id=user_id,
            response_text=final_text,
            was_escalated=trust_output.action == "escalate",
            was_tool_used=bool(tool_calls),
            model_used=llm_response.model_used,
            latency_ms=latency_ms,
            bundle=bundle,
            turn_input=turn_input,
            tool_calls=tool_calls,
            trust_input=trust_input,
            trust_output=trust_output,
            nlu_result=nlu_result,
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
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
            "  model=%s  total_latency=%dms\n"
            "  response: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, nlu_result.intent, bool(tool_calls),
            llm_response.model_used, latency_ms,
            final_text[:200],
        )

        return result

    # ------------------------------------------------------------------
    # Private: blocked / escalated / unknown early exits
    # ------------------------------------------------------------------

    def _blocked_response(
        self,
        session_id: str,
        trust_result: TrustCheckResult,
        start: float,
        trust_input: TrustCheckResult,
    ) -> TurnResult:
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
        return TurnResult(
            session_id=session_id,
            response_text=blocked_text,
            was_escalated=False,
            model_used="",
            latency_ms=int((time.time() - start) * 1000),
        )

    def _escalated_response(
        self,
        session_id: str,
        trust_result: TrustCheckResult,
        start: float,
        trust_input: TrustCheckResult,
    ) -> TurnResult:
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
        return TurnResult(
            session_id=session_id,
            response_text=escalation_text,
            was_escalated=True,
            model_used="",
            latency_ms=int((time.time() - start) * 1000),
        )

    def _unknown_intent_response(
        self,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        turn_input: TurnInput,
        nlu_result: NLUResult,
        start: float,
    ) -> TurnResult:
        """
        Early exit when intent is unknown or NLU confidence is below threshold.
        Increments loop_count in memory so HITL can trigger after repeated failures.
        """
        logger.info(
            "orchestrator.unknown_intent",
            extra={
                "operation": "orchestrator.process_turn",
                "status": "skipped",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        unknown_text = self._config.get(
            "conversation", {}
        ).get(
            "unknown_intent_message",
            "I didn't quite understand that. Could you tell me more about what you need help with?",
        )
        # Increment loop_count — only on unknown/low-confidence turns.
        # This is the HITL counter: N consecutive confused turns triggers human handoff.
        # Update in-memory immediately so this turn's bundle is accurate, then
        # persist asynchronously to avoid adding memory-write latency to the response.
        new_loop_count = int(bundle.session.get("loop_count", 0)) + 1
        bundle.session["loop_count"] = new_loop_count
        logger.info(
            "  [STEP 5b] loop_count incremented to %d (HITL threshold=%d)",
            new_loop_count,
            int(self._config.get("hitl", {}).get("loop_count_threshold", 3)),
        )

        def _write_loop_count() -> None:
            try:
                self._memory.write(session_id, user_id, "session", "loop_count", new_loop_count)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "orchestrator.loop_count_write_failed",
                    extra={
                        "operation": "orchestrator._unknown_intent_response",
                        "status": "failure",
                        "session_id": session_id,
                        "error": str(exc),
                    },
                )

        threading.Thread(target=_write_loop_count, daemon=True).start()
        return self._build_result(
            session_id=session_id,
            user_id=user_id,
            response_text=unknown_text,
            was_escalated=False,
            was_tool_used=False,
            model_used="",
            latency_ms=int((time.time() - start) * 1000),
            bundle=bundle,
            turn_input=turn_input,
            tool_calls=[],
            trust_input=TrustCheckResult(passed=True, action="allow"),
            trust_output=TrustCheckResult(passed=True, action="allow"),
            nlu_result=nlu_result,
        )

    def _safe_fallback_message(self) -> str:
        return self._config.get("conversation", {}).get(
            "output_blocked_message",
            "I wasn't able to produce a safe response. Please try rephrasing your question.",
        )

    # ------------------------------------------------------------------
    # Private: schedule async flush for early exit paths
    # ------------------------------------------------------------------

    def _schedule_flush(self, session_id: str, user_id: str, reason: str) -> None:
        """Spawn a daemon thread to flush the session asynchronously."""
        thread = threading.Thread(
            target=self._do_flush,
            args=(session_id, user_id, reason),
            daemon=True,
        )
        thread.start()

    def _do_flush(self, session_id: str, user_id: str, reason: str) -> None:
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
        bundle: ContextBundle,
        turn_input: TurnInput,
        tool_calls: list[ToolCall],
        trust_input: TrustCheckResult,
        trust_output: TrustCheckResult,
        nlu_result: NLUResult,
        do_flush: bool = False,
        flush_reason: str = "",
    ) -> TurnResult:
        """
        Constructs the TurnResult and schedules the async post-turn work
        (memory write + learning emit) in a daemon thread.
        """
        result = TurnResult(
            session_id=session_id,
            response_text=response_text,
            was_escalated=was_escalated,
            was_tool_used=was_tool_used,
            model_used=model_used,
            latency_ms=latency_ms,
        )

        turn_event = TurnEvent(
            session_id=session_id,
            response_text=response_text,
            tool_calls=tool_calls,
            trust_input_result=trust_input,
            trust_output_result=trust_output,
            model_used=model_used,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            timestamp_ms=turn_input.timestamp_ms,
        )

        thread = threading.Thread(
            target=self._post_turn,
            args=(
                session_id, user_id, response_text,
                nlu_result, bundle, turn_event,
                do_flush, flush_reason,
            ),
            daemon=True,
        )
        thread.start()

        return result

    def _post_turn(
        self,
        session_id: str,
        user_id: str,
        response_text: str,
        nlu_result: NLUResult,
        bundle: ContextBundle,
        turn_event: TurnEvent,
        do_flush: bool,
        flush_reason: str,
    ) -> None:
        """
        Runs in a daemon thread after TurnResult is returned to the caller.

        Writes per-field session state and confirmed entities via Memory Layer.
        Emits turn event to Learning Layer.
        Flushes session if do_flush is True (termination or HITL).

        Any exception here is logged and swallowed — must never crash the thread.
        """
        memory_endpoint = (
            self._config.get("memory_client", {}).get("endpoint", "http://memory_layer:8002")
        )
        learning_endpoint = (
            self._config.get("learning_client", {}).get("endpoint", "http://learning_layer:8004")
        )

        # ── Step 11: Write session fields ────────────────────────────
        logger.info(
            "  [STEP 11] [async] Memory Write  →  POST %s/write  (session=%s)",
            memory_endpoint, session_id,
        )
        try:
            t11 = time.time()
            # loop_count is NOT incremented here — it is only incremented by
            # _unknown_intent_response when the intent is unknown/low-confidence.
            # Incrementing on every turn would hit the HITL threshold after 3
            # normal turns, escalating users who are having a normal conversation.
            self._memory.write(session_id, user_id, "session", "last_response", response_text)

            # Write extracted entities — scope depends on user consent.
            # consent=True → "persistent" (Neo4j). consent=False/absent → "session" (Redis only).
            consent = bundle.session.get("consent", False)
            entity_scope = "persistent" if consent else "session"
            entity_map: dict = self._config.get("entity_to_profile_field", {})
            for entity_key, entity_val in (nlu_result.entities or {}).items():
                profile_field = entity_map.get(entity_key, entity_key)
                self._memory.write(session_id, user_id, entity_scope, profile_field, entity_val)

            logger.info(
                "  [STEP 11] [async] Memory Write  ✓  loop_count=%d"
                "  entities_written=%d  latency=%dms",
                int(bundle.session.get("loop_count", 0)),
                len(nlu_result.entities or {}),
                int((time.time() - t11) * 1000),
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
                "  [STEP 11b] [async] flush_session  →  POST %s/flush_session"
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

        # ── Step 12: Emit to Learning Layer ──────────────────────────
        logger.info(
            "  [STEP 12] [async] Learning Emit  →  POST %s/emit/turn  (session=%s)",
            learning_endpoint, session_id,
        )
        try:
            t12 = time.time()
            self._learning.emit_turn(turn_event)
            logger.info(
                "  [STEP 12] [async] Learning Emit  ✓  latency=%dms",
                int((time.time() - t12) * 1000),
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
    # Private: workflow gate — consent + profile collection
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_nodes() -> frozenset:
        """Nodes where the workflow gate intercepts before the LLM."""
        return frozenset({"awaiting_consent", "profile_building", "grace_turn"})

    def _write_memory_sync(
        self,
        session_id: str,
        user_id: str,
        scope: str,
        key: str,
        value: Any,
    ) -> None:
        """
        Synchronous memory write for state transitions whose values must be
        read by the NEXT turn. Unlike _post_turn's async writes, this blocks
        until the write completes before returning the TurnResult.
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

    def _get_next_round(
        self,
        current_round: int,
        profile: dict,
        rounds_cfg: list,
    ) -> Optional[dict]:
        """
        Returns the next applicable round config dict, or None if all rounds done.
        Skips rounds whose condition field value is not in the allowed set.
        current_round is the last completed round number (0 = none done yet).
        """
        for round_cfg in rounds_cfg:
            r = int(round_cfg.get("round", 0))
            if r <= current_round:
                continue
            condition = round_cfg.get("condition")
            if condition:
                field = condition.get("field", "")
                allowed = condition.get("in", [])
                field_val = str(profile.get(field, "")).strip().lower()
                allowed_lower = [str(v).strip().lower() for v in allowed if v]
                
                matched = False
                if field_val:
                    matched = any(v in field_val or field_val in v for v in allowed_lower)

                if not matched:
                    logger.info(
                        "  [GATE] Skipping round %d — condition field=%s value=%r not in %s",
                        r, field, field_val, allowed,
                    )
                    continue

            entities = round_cfg.get("entities", [])
            missing = [e for e in entities if not profile.get(e)]
            if not missing and entities:
                logger.info("  [GATE] Skipping round %d — all entities already present", r)
                continue

            return round_cfg
        return None

    def _check_hard_min_met(
        self,
        profile: dict,
        just_extracted: dict,
    ) -> bool:
        """
        Returns True when all hard_min_fields are present in profile OR just_extracted.
        just_extracted uses profile field names (already mapped via entity_to_profile_field).
        """
        hard_min_fields: list = self._config.get("profile_collection", {}).get(
            "hard_min_fields", ["trade_or_stream", "location"]
        )
        for field in hard_min_fields:
            if profile.get(field) or just_extracted.get(field):
                continue
            return False
        return True

    def _build_dynamic_question(self, round_cfg: dict, profile: dict) -> str:
        """Formulate a dynamic instruction asking the LLM to get specific entities."""
        entities = round_cfg.get("entities", [])
        field_labels = self._config.get("profile_collection", {}).get("field_labels", {})
        missing = [e for e in entities if not profile.get(e)]
        labels = [field_labels.get(e, e) for e in missing]
        return f"Please ask the user to provide ALL of the following details in a SINGLE conversational question: {', '.join(labels)}."

    def _build_grace_question(
        self,
        profile: dict,
        just_extracted: dict,
    ) -> str:
        """Build the one-shot grace turn question targeting only missing hard_min fields."""
        hard_min_fields: list = self._config.get("profile_collection", {}).get(
            "hard_min_fields", ["trade_or_stream", "location"]
        )
        field_labels = self._config.get("profile_collection", {}).get("field_labels", {})
        missing = [
            f for f in hard_min_fields
            if not profile.get(f) and not just_extracted.get(f)
        ]
        if not missing:
            return ""
        missing_labels = [field_labels.get(m, m) for m in missing]
        return f"We must collect the following final details before proceeding: {', '.join(missing_labels)}."

    def _handle_consent_gate(
        self,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        nlu_result: NLUResult,
        turn_input: TurnInput,
        start: float,
        trust_input: TrustCheckResult,
        detected_language: Optional[str] = None,
    ) -> None:
        """
        Handles the awaiting_consent node.

        Always returns None — falls through to the LLM for language-aware delivery.
        The LLM uses node_instructions.awaiting_consent / profile_building to ask
        the appropriate question in the user's detected language.

        State transitions:
        - First visit (current_question empty): set consent question template.
        - YES → consent=True, consent_flag=True (persistent), transition to profile_building.
        - NO  → consent=False, transition to profile_building (session-only data).
        - Ambiguous → stay in awaiting_consent (current_question unchanged).

        bundle.session is updated in-place so the LLM path (called after this returns None)
        sees the updated current_question and current_node via the re-read on line ~337.
        """
        conv_cfg = self._config.get("conversation", {})
        consent_yes_intents = {"consent_granted", "consent_yes"}
        consent_no_intents  = {"consent_declined", "consent_no"}

        consent_message = conv_cfg.get(
            "consent_message",
            "Kya aap apni jaankari save karne ki anumati dete hain? (Haan/Nahi)"
        )

        # First visit: consent question not yet shown.
        consent_already_asked = bool(bundle.session.get("current_question", ""))
        if not consent_already_asked:
            self._write_memory_sync(session_id, user_id, "session", "current_question", consent_message)
            bundle.session["current_question"] = consent_message
            logger.info("  [GATE] awaiting_consent → consent question set (first visit) → LLM will ask")
            return None

        # User is answering the consent question.
        rounds_cfg = self._config.get("profile_collection", {}).get("rounds", [])

        if nlu_result.intent in consent_yes_intents:
            self._write_memory_sync(session_id, user_id, "session",    "consent",      True)
            self._write_memory_sync(session_id, user_id, "session",    "current_node", "profile_building")
            self._write_memory_sync(session_id, user_id, "persistent", "consent_flag", True)
            bundle.session["consent"] = True
            bundle.session["current_node"] = "profile_building"
            next_round = self._get_next_round(0, bundle.profile, rounds_cfg)
            if next_round:
                question = self._build_dynamic_question(next_round, bundle.profile)
                self._write_memory_sync(session_id, user_id, "session", "collection_round", next_round["round"])
                self._write_memory_sync(session_id, user_id, "session", "current_question", question)
                bundle.session["collection_round"] = next_round["round"]
                bundle.session["current_question"] = question
            else:
                self._write_memory_sync(session_id, user_id, "session", "current_node", "market_truth")
                self._write_memory_sync(session_id, user_id, "session", "current_question", "")
                bundle.session["current_node"] = "market_truth"
                bundle.session["current_question"] = ""
            logger.info("  [GATE] awaiting_consent → consent granted → profile_building → LLM will ask round 1")
            return None

        if nlu_result.intent in consent_no_intents:
            self._write_memory_sync(session_id, user_id, "session", "consent",      False)
            self._write_memory_sync(session_id, user_id, "session", "current_node", "profile_building")
            bundle.session["consent"] = False
            bundle.session["current_node"] = "profile_building"
            next_round = self._get_next_round(0, bundle.profile, rounds_cfg)
            if next_round:
                question = self._build_dynamic_question(next_round, bundle.profile)
                self._write_memory_sync(session_id, user_id, "session", "collection_round", next_round["round"])
                self._write_memory_sync(session_id, user_id, "session", "current_question", question)
                bundle.session["collection_round"] = next_round["round"]
                bundle.session["current_question"] = question
            else:
                self._write_memory_sync(session_id, user_id, "session", "current_node", "market_truth")
                self._write_memory_sync(session_id, user_id, "session", "current_question", "")
                bundle.session["current_node"] = "market_truth"
                bundle.session["current_question"] = ""
            logger.info("  [GATE] awaiting_consent → consent declined → profile_building (session-only) → LLM will ask round 1")
            return None

        # Ambiguous answer — re-ask; current_question already has the consent message.
        logger.info("  [GATE] awaiting_consent → ambiguous answer → LLM will re-ask consent")
        return None

    def _handle_profile_building(
        self,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        nlu_result: NLUResult,
        turn_input: TurnInput,
        start: float,
        trust_input: TrustCheckResult,
        detected_language: Optional[str] = None,
    ) -> None:
        """
        Handles the profile_building node.

        Always returns None — falls through to the LLM for language-aware delivery.
        bundle.session is updated in-place so the LLM path sees the new current_question.

        1. Write extracted entities synchronously (consent-aware scope).
        2. Advance to next applicable round UNCONDITIONALLY (never re-ask same round).
        3. All rounds done + hard_min met → transition to market_truth; LLM auto-calls ONEST.
        4. All rounds done + hard_min NOT met → grace_turn question.
        """
        current_round = int(bundle.session.get("collection_round", 0))
        rounds_cfg = self._config.get("profile_collection", {}).get("rounds", [])
        consent = bundle.session.get("consent", False)
        scope = "persistent" if consent else "session"
        entity_map: dict = self._config.get("entity_to_profile_field", {})

        # Write extracted entities synchronously so condition checks this turn are accurate.
        just_extracted: dict = {}
        for entity_key, entity_val in (nlu_result.entities or {}).items():
            profile_field = entity_map.get(entity_key, entity_key)
            just_extracted[profile_field] = entity_val
            self._write_memory_sync(session_id, user_id, scope, profile_field, entity_val)

        logger.info(
            "  [GATE] profile_building  round=%d  extracted=%s  scope=%s",
            current_round, list(just_extracted.keys()), scope,
        )

        # Merge just_extracted into profile for condition evaluation this turn.
        updated_profile = {**bundle.profile, **just_extracted}

        next_round = self._get_next_round(current_round, updated_profile, rounds_cfg)
        if next_round:
            question = self._build_dynamic_question(next_round, updated_profile)
            self._write_memory_sync(session_id, user_id, "session", "collection_round", next_round["round"])
            self._write_memory_sync(session_id, user_id, "session", "current_question", question)
            bundle.session["collection_round"] = next_round["round"]
            bundle.session["current_question"] = question
            logger.info("  [GATE] profile_building → next round=%d → LLM will ask in user's language", next_round["round"])
            return None

        # All rounds done — check hard_min.
        if self._check_hard_min_met(updated_profile, {}):
            self._write_memory_sync(session_id, user_id, "session", "current_node",     "market_truth")
            self._write_memory_sync(session_id, user_id, "session", "current_question", "")
            bundle.session["current_node"] = "market_truth"
            bundle.session["current_question"] = ""
            logger.info("  [GATE] profile_building → hard_min met → market_truth (LLM will auto-call ONEST)")
            return None

        # Hard min not met → grace turn.
        grace_q = self._build_grace_question(updated_profile, {})
        self._write_memory_sync(session_id, user_id, "session", "current_node",     "grace_turn")
        self._write_memory_sync(session_id, user_id, "session", "current_question", grace_q)
        bundle.session["current_node"] = "grace_turn"
        bundle.session["current_question"] = grace_q
        logger.info("  [GATE] profile_building → hard_min NOT met → grace_turn → LLM will ask")
        return None

    def _handle_grace_turn(
        self,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        nlu_result: NLUResult,
        turn_input: TurnInput,
        start: float,
        trust_input: TrustCheckResult,
        detected_language: Optional[str] = None,
    ) -> Optional[TurnResult]:
        """
        Handles the grace_turn node.

        Writes any extracted entities, then UNCONDITIONALLY advances to market_truth.
        Returns None so the LLM/tool loop auto-triggers the ONEST lookup this turn.
        """
        consent = bundle.session.get("consent", False)
        scope = "persistent" if consent else "session"
        entity_map: dict = self._config.get("entity_to_profile_field", {})

        just_extracted: dict = {}
        for entity_key, entity_val in (nlu_result.entities or {}).items():
            profile_field = entity_map.get(entity_key, entity_key)
            just_extracted[profile_field] = entity_val
            self._write_memory_sync(session_id, user_id, scope, profile_field, entity_val)

        self._write_memory_sync(session_id, user_id, "session", "current_node",     "market_truth")
        self._write_memory_sync(session_id, user_id, "session", "current_question", "")
        bundle.session["current_node"] = "market_truth"
        bundle.session["current_question"] = ""
        logger.info(
            "  [GATE] grace_turn → extracted=%s → unconditional transition to market_truth"
            " (LLM will auto-call ONEST)",
            list(just_extracted.keys()),
        )
        # Return None → fall through to LLM/tool loop so ONEST lookup happens this turn.
        return None

    def _infer_resume_node(self, bundle: ContextBundle) -> str:
        """
        For returning users starting a new session: determine where to resume.
        Called only when is_returning=True and current_node="awaiting_consent"
        (session was reset but profile exists in Neo4j).

        Returns a node name string.
        """
        profile = bundle.profile
        if not profile.get("consent_flag"):
            return "awaiting_consent"
        hard_min_fields: list = self._config.get("profile_collection", {}).get(
            "hard_min_fields", ["trade_or_stream", "location"]
        )
        missing = [f for f in hard_min_fields if not profile.get(f)]
        if not missing:
            return "market_truth"
        return "profile_building"

    def _workflow_gate(
        self,
        session_id: str,
        user_id: str,
        bundle: ContextBundle,
        nlu_result: NLUResult,
        turn_input: TurnInput,
        start: float,
        trust_input: TrustCheckResult,
        detected_language: Optional[str],
    ) -> Optional[TurnResult]:
        """
        Step 5c: Intercepts consent / profile collection nodes before the LLM.

        - awaiting_consent  → _handle_consent_gate
        - profile_building  → _handle_profile_building
        - grace_turn        → _handle_grace_turn
        - all other nodes   → returns None (fall through to LLM)

        For returning users whose new session defaults to awaiting_consent,
        infers the correct resume node first.
        """
        current_node = bundle.session.get("current_node", "awaiting_consent")
        is_returning = bundle.session.get("is_returning", False)

        # Persist detected_language to session on first turn (if not already stored).
        if detected_language and not bundle.session.get("language"):
            self._write_memory_sync(session_id, user_id, "session", "language", detected_language)
            logger.info("  [GATE] detected_language=%s → stored in session", detected_language)

        if is_returning and current_node == "awaiting_consent":
            inferred = self._infer_resume_node(bundle)
            if inferred != "awaiting_consent":
                self._write_memory_sync(session_id, user_id, "session", "current_node", inferred)
                current_node = inferred
                logger.info("  [GATE] returning user → inferred resume node=%s", current_node)

        if current_node == "awaiting_consent":
            return self._handle_consent_gate(
                session_id=session_id, user_id=user_id, bundle=bundle,
                nlu_result=nlu_result, turn_input=turn_input, start=start,
                trust_input=trust_input, detected_language=detected_language,
            )
        if current_node == "profile_building":
            return self._handle_profile_building(
                session_id=session_id, user_id=user_id, bundle=bundle,
                nlu_result=nlu_result, turn_input=turn_input, start=start,
                trust_input=trust_input, detected_language=detected_language,
            )
        if current_node == "grace_turn":
            return self._handle_grace_turn(
                session_id=session_id, user_id=user_id, bundle=bundle,
                nlu_result=nlu_result, turn_input=turn_input, start=start,
                trust_input=trust_input, detected_language=detected_language,
            )
        return None
