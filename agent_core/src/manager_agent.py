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
from src.interfaces.trust_layer import TrustLayerBase
from src.llm_wrapper.base import LLMWrapperBase
from src.models import LLMResponse, RetrievalChunk, ToolCall, ToolResult
from src.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ManagerAgent:
    """
    Drives the tool-use loop for one conversation turn.

    Args:
        llm_wrapper:    Used for the second (and any subsequent) LLM call after tool results.
        tool_registry:  Used to check which tools require consent.
        action_gateway: Executes tool calls against external connectors.
        trust_layer:    Used to verify consent before write/identity tool execution.
        max_tool_rounds:Maximum tool → LLM cycles per turn. Default 1 for PoC.
                        Configurable so extending to multi-step chains needs only a config change.
    """

    def __init__(
        self,
        llm_wrapper: LLMWrapperBase,
        tool_registry: ToolRegistry,
        action_gateway: ActionGatewayBase,
        trust_layer: TrustLayerBase,
        max_tool_rounds: int = 1,
    ) -> None:
        if llm_wrapper is None:
            raise ValueError("llm_wrapper must not be None")
        if tool_registry is None:
            raise ValueError("tool_registry must not be None")
        if action_gateway is None:
            raise ValueError("action_gateway must not be None")
        if trust_layer is None:
            raise ValueError("trust_layer must not be None")

        self._llm = llm_wrapper
        self._registry = tool_registry
        self._gateway = action_gateway
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
    ) -> tuple[str, list[ToolCall]]:
        """
        Drive the tool-use loop starting from the initial LLM response.

        Args:
            messages:             The messages list that produced initial_llm_response.
                                  Extended in-place with tool_use and tool_result blocks.
            session_id:           Used for consent checks and gateway calls.
            initial_llm_response: First LLM response from orchestrator's LLM call #1.
            system:               System prompt from KE — passed to follow-up LLM calls
                                  so language and persona instructions are preserved after
                                  tool use.

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
                tool_result = self._execute_tool(tool_call, session_id)
                all_tool_calls.append(tool_call)
                messages = self._append_tool_result(messages, tool_call, tool_result)

            rounds += 1

            start = time.time()
            current_response = self._llm.call(
                messages=messages,
                tools=self._registry.get_tool_definitions(),
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
    # Prompt assembly helpers (E1, E2)
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        profile: dict,
        session: dict,
        detected_language: str,
        config: dict,
    ) -> str:
        """
        Build the system prompt for one LLM call.

        Reads prompt_blocks from config: persona, language_instruction, guardrail_reminders.
        Appends detected language instruction and a compact profile summary if profile
        has any filled fields.

        Args:
            profile:           UserProfile dict from ContextBundle.
            session:           Session state dict from ContextBundle.
            detected_language: Language detected by Language Normaliser.
            config:            Full agent_core config dict.

        Returns:
            str: Assembled system prompt. Empty string if no persona configured.
        """
        prompt_cfg = config.get("prompt_blocks", {})
        parts: list[str] = []

        persona_text = prompt_cfg.get("persona", "").strip()
        if persona_text:
            parts.append(persona_text)

        # Inject known profile fields as context
        if profile:
            profile_lines: list[str] = []
            skip_keys = {"attributes", "user_id"}
            for k, v in profile.items():
                if k not in skip_keys and v not in (None, "", [], "[]"):
                    profile_lines.append(f"  {k}: {v}")
            if profile_lines:
                parts.append("Known user profile:\n" + "\n".join(profile_lines))

        language_instruction = prompt_cfg.get("language_instruction", "").strip()
        if language_instruction:
            parts.append(language_instruction)

        if detected_language:
            parts.append(
                f"The user's current message is in {detected_language}. "
                f"Respond in {detected_language}."
            )

        guardrails = prompt_cfg.get("guardrail_reminders", [])
        if guardrails:
            parts.append("\n--- Guidelines ---")
            if isinstance(guardrails, str):
                # YAML block scalar (|) — already formatted with dashes, append as-is
                parts.append(guardrails.strip())
            else:
                for reminder in guardrails:
                    parts.append(f"- {reminder}")

        # Inject node-specific instruction when configured.
        # node_instructions is a dict keyed by current_node value.
        # E.g. node_instructions.market_truth guides the 5 reaction branches.
        current_node = session.get("current_node", "")
        node_instructions: dict = prompt_cfg.get("node_instructions", {})
        if current_node and node_instructions:
            instruction = node_instructions.get(current_node, "")
            if instruction:
                parts.append(instruction.strip())

        return "\n\n".join(parts)

    def build_messages(
        self,
        user_message: str,
        chunks: list[RetrievalChunk],
        current_question: str,
    ) -> list[dict]:
        """
        Build the Anthropic messages array for one LLM call.

        New design: no conversation history — each turn is fresh context.
        current_question (what the agent last asked) is prepended as grounding context.
        RAG chunks are appended to the user message.

        Args:
            user_message:     Raw user message text.
            chunks:           Retrieval chunks from KE.retrieve().
            current_question: The question the agent last asked, from session["current_question"].
                              Empty string if this is the first turn.

        Returns:
            list[dict]: Single-turn Anthropic messages list.
            Empty list if user_message is empty.
        """
        if not user_message:
            return []

        content_parts: list[str] = []

        if current_question:
            content_parts.append(f"[Last question asked: {current_question}]")

        content_parts.append(user_message)

        always_include = [c for c in chunks if c.always_include]
        retrieved = [c for c in chunks if not c.always_include]

        if always_include:
            content_parts.append("\n--- Always include context ---")
            for chunk in always_include:
                content_parts.append(chunk.text)

        if retrieved:
            content_parts.append("\n--- Relevant knowledge ---")
            for chunk in retrieved:
                content_parts.append(chunk.text)

        return [{"role": "user", "content": "\n\n".join(content_parts)}]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
        result_content: str = (
            tool_result.error
            if not tool_result.success and tool_result.error
            else str(tool_result.result)
        )
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
