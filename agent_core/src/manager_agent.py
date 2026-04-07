"""
agent_core/manager_agent.py

Owns the LLM → tool → LLM loop for a single turn.
Called by orchestrator after the first LLM call. Drives the tool-use cycle,
enforces the consent gate for write/identity connectors, and returns the
final response text once the loop is complete.

ManagerAgent never calls the LLM or external systems autonomously —
it always acts on an initial LLMResponse passed in by the orchestrator.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from src.exceptions import ConsentRequiredError
from src.interfaces.action_gateway import ActionGatewayBase
from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.interfaces.trust_layer import TrustLayerBase
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, RetrievalChunk, ToolCall, ToolResult
from src.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ManagerAgent:
    """
    Drives the tool-use loop for one conversation turn.

    Args:
        llm_wrapper:      Used for the second (and any subsequent) LLM call after tool results.
        tool_registry:    Used to check which tools require consent.
        action_gateway:   Executes tool calls against external connectors.
        knowledge_engine: Called when the LLM invokes the knowledge_retrieval tool.
        trust_layer:      Used to verify consent before write/identity tool execution.
        max_tool_rounds:  Maximum tool → LLM cycles per turn. Default 1 for PoC.
                          Configurable so extending to multi-step chains needs only a config change.
    """

    def __init__(
        self,
        llm_wrapper: LLMWrapperBase,
        tool_registry: ToolRegistry,
        action_gateway: ActionGatewayBase,
        knowledge_engine: KnowledgeEngineBase,
        trust_layer: TrustLayerBase,
        max_tool_rounds: int = 1,
    ) -> None:
        if llm_wrapper is None:
            raise ValueError("llm_wrapper must not be None")
        if tool_registry is None:
            raise ValueError("tool_registry must not be None")
        if action_gateway is None:
            raise ValueError("action_gateway must not be None")
        if knowledge_engine is None:
            raise ValueError("knowledge_engine must not be None")
        if trust_layer is None:
            raise ValueError("trust_layer must not be None")

        self._llm = llm_wrapper
        self._registry = tool_registry
        self._gateway = action_gateway
        self._ke = knowledge_engine
        self._trust = trust_layer
        self._max_tool_rounds = max(1, max_tool_rounds)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run_turn(
        self,
        messages: list[dict],
        session_id: str,
        initial_llm_response: LLMResponse,
        system: str = "",
        active_tools: list[dict] | None = None,
        ke_context: dict | None = None,
    ) -> tuple[str, list[ToolCall]]:
        """
        Drive the tool-use loop starting from the initial LLM response.

        Routes knowledge_retrieval tool calls directly to the Knowledge Engine
        via _execute_knowledge_retrieval. All other tool calls go through the
        Action Gateway's consent gate and execution path.

        Args:
            messages:             The messages list that produced initial_llm_response.
                                  Extended in-place with tool_use and tool_result blocks.
            session_id:           Used for consent checks and gateway calls.
            initial_llm_response: First LLM response from orchestrator's LLM call #1.
            system:               System prompt passed to follow-up LLM calls so language
                                  and persona instructions are preserved after tool use.
            active_tools:         Scoped tool definitions for the current subagent. Only
                                  these are passed to follow-up LLM calls. If None, falls
                                  back to self._registry.get_tool_definitions() for
                                  backward compatibility.
            ke_context:           Dict with context required to call the Knowledge Engine
                                  when knowledge_retrieval is invoked. Expected fields:
                                  session_id, user_message, profile, session, intent,
                                  entities, sentiment, confidence, normalised_input,
                                  detected_language. If None, knowledge_retrieval calls
                                  return an empty tool_result.

        Returns:
            (final_response_text, list_of_all_tool_calls_executed)
            final_response_text is an empty string if the LLM returned no content
            and no tool calls were made (edge case — orchestrator handles this).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        if initial_llm_response is None:
            raise ValueError("initial_llm_response must not be None")

        current_response = initial_llm_response
        all_tool_calls: list[ToolCall] = []
        rounds = 0

        while current_response.stop_reason == "tool_use" and rounds < self._max_tool_rounds:
            if not current_response.tool_calls:
                logger.warning(
                    "manager_agent.tool_use_no_calls",
                    extra={
                        "operation": "manager_agent.run_turn",
                        "status": "skipped",
                        "session_id": session_id,
                        "round": rounds + 1,
                    },
                )
                break

            for tool_call in current_response.tool_calls:
                if self._registry.get_route(tool_call.tool_name) == "knowledge_engine":
                    tool_result = self._execute_knowledge_retrieval(tool_call, ke_context)
                else:
                    tool_result = self._execute_tool(tool_call, session_id)
                all_tool_calls.append(tool_call)
                messages = self._append_tool_result(messages, tool_call, tool_result)

            rounds += 1

            follow_up_tools = active_tools if active_tools is not None else self._registry.get_tool_definitions()
            start = time.time()
            current_response = self._llm.call(
                messages=messages,
                tools=follow_up_tools,
                system=system,
            )
            logger.info(
                "manager_agent.llm_followup",
                extra={
                    "operation": "manager_agent.run_turn",
                    "status": "success",
                    "session_id": session_id,
                    "round": rounds,
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": current_response.model_used,
                },
            )

        final_text = current_response.content or ""
        return final_text, all_tool_calls

    # ------------------------------------------------------------------
    # Prompt assembly helpers
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        agent_system_prompt: str,
        subagent_system_prompt: str,
        detected_language: str,
        channel: str,
        profile: dict,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
    ) -> str:
        """
        Build the system prompt for one LLM call.

        Assembles three layers:
        1. agent_system_prompt — use-case level purpose and hard rules (from AgentWorkflow)
        2. Channel/language context — injected at runtime from Reach Layer / Language Normalisation
        3. subagent_system_prompt — instructions for the active subagent (from SubAgent)

        Also injects known profile fields as grounding context between layers.

        Args:
            agent_system_prompt:    Workflow-level system prompt.
            subagent_system_prompt: Active subagent's system prompt.
            detected_language:      Language detected by Language Normaliser.
            channel:                Channel type (e.g. "cli", "whatsapp", "voip").
            profile:                User profile dict for known field injection.
            is_resumption:          Whether the user is resuming an ongoing session.
            guardrail_constraints:  Optional dict with prompt_constraints and
                                    required_disclosures from the Trust Layer.
                                    When present, appended to the system prompt.

        Returns:
            Assembled system prompt string.
        """
        parts: list[str] = []

        if agent_system_prompt:
            parts.append(agent_system_prompt.strip())

        # Channel and language context injected at runtime
        context_parts: list[str] = []
        if channel:
            context_parts.append(f"Channel: {channel}")
        if detected_language:
            context_parts.append(
                f"User's language: {detected_language}. Respond in {detected_language}."
            )
        if context_parts:
            parts.append("\n".join(context_parts))

        if is_resumption:
            parts.append(
                "CONTEXT: The user has returned to an ongoing session. DO NOT provide a starting greeting "
                "or re-introduce yourself. Resume the conversation naturally from where it left off. "
                "Ask the next question required for the current stage."
            )

        # Inject known profile fields as grounding context
        if profile:
            profile_lines: list[str] = []
            skip_keys = {"attributes", "user_id"}
            for k, v in profile.items():
                if k not in skip_keys and v not in (None, "", [], "[]"):
                    profile_lines.append(f"  {k}: {v}")
            # Also inject ad-hoc attributes
            for attr in profile.get("attributes", []):
                attr_key = attr.get("key")
                attr_val = attr.get("value")
                if attr_key and attr_val:
                    profile_lines.append(f"  {attr_key}: {attr_val}")
            
            if profile_lines:
                parts.append("Known user profile:\n" + "\n".join(profile_lines))

        if subagent_system_prompt:
            parts.append(subagent_system_prompt.strip())

        if guardrail_constraints:
            constraints = guardrail_constraints.get("prompt_constraints", [])
            disclosures = guardrail_constraints.get("required_disclosures", [])

            if constraints:
                parts.append(
                    "## Guardrail Constraints\n" + "\n".join(f"- {c}" for c in constraints)
                )
            if disclosures:
                parts.append(
                    "## Required Disclosures\n" + "\n".join(f"- {d}" for d in disclosures)
                )

        return "\n\n".join(parts)

    def build_messages(
        self,
        user_message: str,
        current_question: str,
    ) -> list[dict]:
        """
        Build the Anthropic messages array for one LLM call.

        No RAG chunks injected here — knowledge retrieval is now tool-driven.
        The LLM calls knowledge_retrieval tool when it needs context; chunks
        arrive as tool_result blocks within the same turn's tool-use loop.

        Args:
            user_message:     Raw user message text.
            current_question: The last question the agent asked (from session). Empty string if first turn.

        Returns:
            Single-turn Anthropic messages list.
        """
        # If user_message is empty (e.g. cold-start resumption), use a placeholder
        # so the LLM has a "turn" to generate the resumption prompt.
        input_text = user_message.strip() if user_message else "[Resuming session...]"

        content_parts: list[str] = []
        if current_question:
            content_parts.append(f"[Last question asked: {current_question}]")
        content_parts.append(input_text)

        return [{"role": "user", "content": "\n\n".join(content_parts)}]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_knowledge_retrieval(self, tool_call: ToolCall, ke_context: dict | None) -> ToolResult:
        """
        Call Knowledge Engine for RAG retrieval and return chunks as a ToolResult.

        Args:
            tool_call:   The knowledge_retrieval tool call from the LLM.
            ke_context:  Dict with context fields for the KE retrieve call. If None
                         or missing, returns a failure ToolResult without calling KE.

        Returns:
            ToolResult with retrieved context string in result["context"] on success,
            or a failure ToolResult with an error string on failure or missing context.
        """
        if not ke_context:
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error="ke_context_not_available",
            )
        start = time.time()
        try:
            chunks = self._ke.retrieve(
                session_id=ke_context.get("session_id", ""),
                user_message=ke_context.get("user_message", ""),
                profile=ke_context.get("profile", {}),
                session=ke_context.get("session", {}),
                intent=ke_context.get("intent", ""),
                entities=ke_context.get("entities", {}),
                sentiment=ke_context.get("sentiment", "neutral"),
                confidence=ke_context.get("confidence", 0.0),
                normalised_input=ke_context.get("normalised_input", ""),
                detected_language=ke_context.get("detected_language", ""),
            )
            chunk_texts = [c.text for c in chunks] if chunks else []
            combined = "\n\n".join(chunk_texts) if chunk_texts else "No relevant context found."
            logger.info("manager_agent.knowledge_retrieval", extra={
                "operation": "manager_agent._execute_knowledge_retrieval",
                "status": "success",
                "chunk_count": len(chunk_texts),
                "latency_ms": int((time.time() - start) * 1000),
            })
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={"context": combined},
                success=True,
            )
        except Exception as e:
            logger.error("manager_agent.knowledge_retrieval_error", extra={
                "operation": "manager_agent._execute_knowledge_retrieval",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={},
                success=False,
                error=str(e),
            )

    def _execute_tool(self, tool_call: ToolCall, session_id: str) -> ToolResult:
        """
        Enforce consent gate then delegate to Action Gateway.

        Returns ToolResult regardless of success or consent failure.
        Consent failures are surfaced as ToolResult(success=False, error="consent_required")
        so the LLM can prompt the user for consent in the next turn.
        """
        if self._registry.requires_consent(tool_call.tool_name):
            consent_granted = self._trust.check_consent(session_id, tool_call.tool_name)
            if not consent_granted:
                logger.warning(
                    "manager_agent.consent_denied",
                    extra={
                        "operation": "manager_agent._execute_tool",
                        "status": "skipped",
                        "tool_name": tool_call.tool_name,
                        "session_id": session_id,
                    },
                )
                return ToolResult(
                    tool_use_id=tool_call.tool_use_id,
                    tool_name=tool_call.tool_name,
                    result={},
                    success=False,
                    error="consent_required",
                )

        start = time.time()
        result = self._gateway.execute(tool_call, session_id)
        logger.info(
            "manager_agent.tool_executed",
            extra={
                "operation": "manager_agent._execute_tool",
                "status": "success" if result.success else "failure",
                "tool_name": tool_call.tool_name,
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
                "error": result.error,
            },
        )
        logger.info(
            "  [STEP 8] Action Gateway response  ←  tool=%s  success=%s  result=%s",
            tool_call.tool_name,
            result.success,
            result.result if result.success else f"ERROR: {result.error}",
        )
        return result

    def _append_tool_result(
        self,
        messages: list[dict],
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> list[dict]:
        """
        Extend the messages list with the tool_use and tool_result blocks
        in the Anthropic message format required for multi-turn tool use.

        Anthropic format:
          - The assistant message contains the tool_use block.
          - The following user message contains the tool_result block.
        """
        # Append assistant message with the tool_use block
        messages.append({
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_call.tool_use_id,
                    "name": tool_call.tool_name,
                    "input": tool_call.input_params,
                }
            ],
        })

        # Append user message with the tool_result block
        result_content: str = ""
        if not tool_result.success and tool_result.error:
            result_content = tool_result.error
        else:
            result_content = tool_result.result_text or str(tool_result.result)

        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.tool_use_id,
                    "content": result_content,
                }
            ],
        })

        return messages
