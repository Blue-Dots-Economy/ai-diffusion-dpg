"""
agent_core/manager_agent.py

Owns the LLM → tool → LLM loop for a single turn.
Called by orchestrator after the first LLM call. Drives the tool-use cycle,
enforces the consent gate for write/identity connectors, and returns the
final response text once the loop is complete.

ManagerAgent never calls the LLM or external systems autonomously —
it always acts on an initial ChatResponse passed in by the orchestrator.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from src.chat_provider.base import ChatProviderBase, ProviderAPIError
from src.chat_provider.types import (
    ChatRequest,
    ChatResponse,
    Message,
    OutputFormat,
    SystemPrompt,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from src.exceptions import ConsentRequiredError
from src.interfaces.action_gateway import ActionGatewayBase
from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.interfaces.trust_layer import TrustLayerBase
from src.models import RetrievalChunk, ToolCall, ToolResult
from src.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ManagerAgent:
    """
    Drives the tool-use loop for one conversation turn.

    Args:
        chat_provider:    Used for the second (and any subsequent) LLM call after tool results.
        tool_registry:    Used to check which tools require consent.
        action_gateway:   Executes tool calls against external connectors.
        knowledge_engine: Called when the LLM invokes the knowledge_retrieval tool.
        trust_layer:      Used to verify consent before write/identity tool execution.
        max_tool_rounds:  Maximum tool → LLM cycles per turn. Default 1 for PoC.
                          Configurable so extending to multi-step chains needs only a config change.
    """

    def __init__(
        self,
        chat_provider: ChatProviderBase,
        tool_registry: ToolRegistry,
        action_gateway: ActionGatewayBase,
        knowledge_engine: KnowledgeEngineBase,
        trust_layer: TrustLayerBase,
        max_tool_rounds: int = 1,
    ) -> None:
        if chat_provider is None:
            raise ValueError("chat_provider must not be None")
        if tool_registry is None:
            raise ValueError("tool_registry must not be None")
        if action_gateway is None:
            raise ValueError("action_gateway must not be None")
        if knowledge_engine is None:
            raise ValueError("knowledge_engine must not be None")
        if trust_layer is None:
            raise ValueError("trust_layer must not be None")

        self._llm = chat_provider
        self._registry = tool_registry
        self._gateway = action_gateway
        self._ke = knowledge_engine
        self._trust = trust_layer
        self._max_tool_rounds = max(1, max_tool_rounds)
        # GH-137: Per-turn flag set when the LLM invokes the end_session internal tool.
        self._session_ended_flag: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run_turn(
        self,
        messages: list[Message],
        session_id: str,
        initial_response: ChatResponse,
        system: SystemPrompt | None = None,
        active_tools: list[dict] | None = None,
        ke_context: dict | None = None,
    ) -> tuple[str, list[ToolCall], list[ToolResult]]:
        """
        Drive the tool-use loop starting from the initial LLM response.

        Routes knowledge_retrieval tool calls directly to the Knowledge Engine
        via _execute_knowledge_retrieval. All other tool calls go through the
        Action Gateway's consent gate and execution path.

        Args:
            messages:         The messages list (neutral chat_provider types) that produced
                              initial_response. Extended in-place with tool_use and
                              tool_result blocks as Message objects.
            session_id:       Used for consent checks and gateway calls.
            initial_response: First LLM response from orchestrator's LLM call #1.
            system:           System prompt passed to follow-up LLM calls so language
                              and persona instructions are preserved after tool use.
            active_tools:     Scoped tool definitions for the current subagent (legacy
                              dict shape). Only these are passed to follow-up LLM calls.
                              If None, falls back to self._registry.get_tool_definitions()
                              for backward compatibility.
            ke_context:       Dict with context required to call the Knowledge Engine
                              when knowledge_retrieval is invoked. Expected fields:
                              session_id, user_message, profile, session, intent,
                              entities, sentiment, confidence, normalised_input,
                              detected_language. If None, knowledge_retrieval calls
                              return an empty tool_result.

        Returns:
            (final_response_text, list_of_all_tool_calls_executed, list_of_all_tool_results)
            final_response_text is an empty string if the LLM returned no content
            and no tool calls were made (edge case — orchestrator handles this).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        if initial_response is None:
            raise ValueError("initial_response must not be None")

        # GH-137: Reset per-turn flags before driving the tool loop.
        self._reset_turn_flags()

        current_response = initial_response
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        rounds = 0

        while current_response.stop_reason == "tool_use" and rounds < self._max_tool_rounds:
            response_tool_calls = [
                ToolCall(
                    tool_name=b.tool_name,
                    tool_use_id=b.tool_use_id,
                    input_params=b.input,
                )
                for b in current_response.content if b.type == "tool_use"
            ]
            response_text = next(
                (b.text for b in current_response.content if b.type == "text"),
                None,
            )

            if not response_tool_calls:
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

            # Build neutral content blocks for the assistant message and tool-result message.
            assistant_content: list = []
            if response_text:
                assistant_content.append(TextBlock(text=response_text))
            tool_results_content: list[ToolResultBlock] = []

            for tool_call in response_tool_calls:
                if tool_call.tool_name == "end_session":
                    # GH-137: internal signal — no external execution, just mark the flag.
                    self._session_ended_flag = True
                    logger.info(
                        "manager_agent.end_session",
                        extra={
                            "operation": "manager_agent.run_turn",
                            "status": "success",
                            "tool_name": "end_session",
                            "session_id": session_id,
                            "reason": (tool_call.input_params or {}).get("reason", ""),
                        },
                    )
                    tool_result = ToolResult(
                        tool_use_id=tool_call.tool_use_id,
                        tool_name="end_session",
                        result={"acknowledged": True},
                        success=True,
                        result_text="Session end acknowledged.",
                    )
                    all_tool_calls.append(tool_call)
                    all_tool_results.append(tool_result)
                    assistant_content.append(ToolUseBlock(
                        tool_use_id=tool_call.tool_use_id,
                        tool_name=tool_call.tool_name,
                        input=tool_call.input_params or {},
                    ))
                    tool_results_content.append(ToolResultBlock(
                        tool_use_id=tool_call.tool_use_id,
                        content="Session end acknowledged.",
                    ))
                    continue

                if self._registry.get_route(tool_call.tool_name) == "knowledge_engine":
                    tool_result = self._execute_knowledge_retrieval(tool_call, ke_context)
                else:
                    tool_result = self._execute_tool(tool_call, session_id)
                all_tool_calls.append(tool_call)
                all_tool_results.append(tool_result)

                assistant_content.append(ToolUseBlock(
                    tool_use_id=tool_call.tool_use_id,
                    tool_name=tool_call.tool_name,
                    input=tool_call.input_params or {},
                ))

                result_text = ""
                if not tool_result.success and tool_result.error:
                    # Prefer the structured upstream body if the adapter
                    # captured one (e.g. a 4xx response excerpt) so the LLM
                    # can recover on the next turn — falling back to the
                    # bare error tag only when no body is available.
                    result_text = tool_result.result_text or tool_result.error
                else:
                    result_text = tool_result.result_text or str(tool_result.result)

                tool_results_content.append(ToolResultBlock(
                    tool_use_id=tool_call.tool_use_id,
                    content=result_text,
                ))

            messages.append(Message(role="assistant", content=assistant_content))
            messages.append(Message(role="user", content=tool_results_content))

            rounds += 1

            follow_up_tools_legacy = active_tools if active_tools is not None else self._registry.get_tool_definitions()
            follow_up_tools = [
                ToolDefinition(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("input_schema", {}),
                )
                for t in follow_up_tools_legacy
            ]

            request = ChatRequest(
                messages=messages,
                system=system,
                tools=follow_up_tools,
            )
            start = time.time()
            current_response = self._llm.call(request)
            if current_response.stop_reason == "error":
                raise ProviderAPIError(
                    f"LLM followup call failed: {current_response.error_message}",
                    error_type=current_response.error_type,
                    error_message=current_response.error_message,
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

        # Final text: first TextBlock from the last response, or "".
        final_text = next(
            (b.text for b in current_response.content if b.type == "text"),
            "",
        )
        return final_text, all_tool_calls, all_tool_results

    @property
    def session_ended(self) -> bool:
        """True iff the LLM invoked the ``end_session`` tool during the last turn.

        Reset to False at the start of every ``run_turn`` call.
        """
        return self._session_ended_flag

    def _reset_turn_flags(self) -> None:
        """Clear per-turn flags at the top of each ``run_turn`` invocation."""
        self._session_ended_flag = False

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
        channel_config: dict | None = None,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
        user_state_guidance: str | None = None,
        session_end_eval_prompt: str | None = None,
    ) -> SystemPrompt:
        """Build a neutral SystemPrompt with TextBlock entries for one LLM call.

        Assembles three cache-volatility tiers:

        Tier 1 (session-stable — cache_hint="session"):
            <persona>             agent_system_prompt
            <channel_rules>       channel_config.system_prompt_suffix
            <session_end_policy>  session_end_eval_prompt

        Tier 2 (state-stable — cache_hint="session"):
            <subagent>            subagent_system_prompt
            <user_state_guidance> user_state_guidance

        Tier 3 (dynamic — no cache_hint):
            <channel_context>     channel + detected_language line
            <resumption>          resumption note (first turn after adoption)
            <known_profile>       profile grounding
            <active_guardrails>   guardrail constraints + required disclosures

        Empty inputs elide their section entirely; empty tiers are not
        appended to the output list. The Anthropic provider translates
        session-tier blocks into cache_control markers.

        Args:
            agent_system_prompt:    Workflow-level persona + cross-cutting safety.
            subagent_system_prompt: Active subagent's system prompt.
            detected_language:      Language detected by Language Normaliser.
            channel:                Channel type (e.g. "cli", "whatsapp", "voip").
            profile:                User profile dict for grounding injection.
            channel_config:         Optional per-channel config. When present and
                                    ``system_prompt_suffix`` is non-empty the suffix
                                    joins Tier 1 as <channel_rules>.
            is_resumption:          Whether the user is resuming an ongoing session.
            guardrail_constraints:  Optional dict with ``prompt_constraints`` and
                                    ``required_disclosures`` from the Trust Layer.
            user_state_guidance:    Optional text describing the active user state.
            session_end_eval_prompt: Optional prompt that instructs the LLM to emit
                                    the ``end_session`` tool when the user signals
                                    departure.

        Returns:
            Neutral SystemPrompt with TextBlock entries; the Anthropic provider
            translates session-tier blocks into cache_control markers.
            Contains 0–3 blocks depending on which tiers are populated.
        """

        def xml(tag: str, body: str | None) -> str:
            body_stripped = (body or "").strip()
            if not body_stripped:
                return ""
            return f"<{tag}>\n{body_stripped}\n</{tag}>"

        def join(sections: list[str]) -> str:
            return "\n\n".join(s for s in sections if s)

        # ── Tier 1: session-stable ────────────────────────────────────
        suffix = (channel_config or {}).get("system_prompt_suffix", "")
        tier1 = join([
            xml("persona", agent_system_prompt),
            xml("channel_rules", suffix),
            xml("session_end_policy", session_end_eval_prompt),
        ])

        # ── Tier 2: state-stable ──────────────────────────────────────
        tier2 = join([
            xml("subagent", subagent_system_prompt),
            xml("user_state_guidance", user_state_guidance),
        ])

        # ── Tier 3: dynamic ───────────────────────────────────────────
        channel_ctx_parts: list[str] = []
        if channel:
            channel_ctx_parts.append(f"Channel: {channel}")
        if detected_language:
            channel_ctx_parts.append(
                f"User's language: {detected_language}. Respond in {detected_language}."
            )
        else:
            channel_ctx_parts.append(
                "Detect the user's language and script from their most recent message "
                "and reply in the same language and script. If the user mixes languages "
                "or uses a romanised script (e.g. Hinglish, Kanglish), mirror their mix "
                "and script exactly."
            )
        channel_ctx = "\n".join(channel_ctx_parts)

        resumption_note = (
            "The user has returned to an ongoing session. Do not provide a "
            "starting greeting or re-introduce yourself. Resume the conversation "
            "naturally from where it left off; ask the next question required "
            "for the current stage."
        ) if is_resumption else ""

        profile_body = ""
        if profile:
            lines: list[str] = []
            skip_keys = {"attributes", "user_id"}
            for k, v in profile.items():
                if k not in skip_keys and v not in (None, "", [], "[]"):
                    lines.append(f"  {k}: {v}")
            for attr in profile.get("attributes", []) or []:
                attr_key = attr.get("key") if isinstance(attr, dict) else None
                attr_val = attr.get("value") if isinstance(attr, dict) else None
                if attr_key and attr_val:
                    lines.append(f"  {attr_key}: {attr_val}")
            if lines:
                profile_body = (
                    "Already collected — do NOT ask for any of these fields again:\n"
                    + "\n".join(lines)
                )

        guardrails_body = ""
        if guardrail_constraints:
            constraints = guardrail_constraints.get("prompt_constraints", []) or []
            disclosures = guardrail_constraints.get("required_disclosures", []) or []
            parts: list[str] = []
            if constraints:
                parts.append(
                    "Constraints:\n" + "\n".join(f"- {c}" for c in constraints)
                )
            if disclosures:
                parts.append(
                    "Required disclosures:\n" + "\n".join(f"- {d}" for d in disclosures)
                )
            guardrails_body = "\n\n".join(parts)

        tier3 = join([
            xml("channel_context", channel_ctx),
            xml("resumption", resumption_note),
            xml("known_profile", profile_body),
            xml("active_guardrails", guardrails_body),
        ])

        # ── Assemble blocks ───────────────────────────────────────────
        # cache_hint only when the active provider can honour it. OpenAI's
        # capability is False today (#304 will flip it). Without this gate,
        # _validate_request raises UnsupportedFeatureError on every turn for
        # providers that don't support prompt caching.
        cache_hint = "session" if self._llm.capabilities.supports_prompt_cache else None
        blocks: list[TextBlock] = []
        if tier1:
            blocks.append(TextBlock(text=tier1, cache_hint=cache_hint))
        if tier2:
            blocks.append(TextBlock(text=tier2, cache_hint=cache_hint))
        if tier3:
            blocks.append(TextBlock(text=tier3))
        return SystemPrompt(blocks=blocks)

    def build_messages(
        self,
        user_message: str,
        current_question: str,
    ) -> list[Message]:
        """
        Build the neutral messages list for one LLM call.

        No RAG chunks injected here — knowledge retrieval is now tool-driven.
        The LLM calls knowledge_retrieval tool when it needs context; chunks
        arrive as tool_result blocks within the same turn's tool-use loop.

        Args:
            user_message:     Raw user message text.
            current_question: The last question the agent asked (from session). Empty string if first turn.

        Returns:
            Single-turn messages list with one user Message.
        """
        # If user_message is empty (e.g. cold-start resumption), use a placeholder
        # so the LLM has a "turn" to generate the resumption prompt.
        input_text = user_message.strip() if user_message else "[Resuming session...]"

        content_parts: list[str] = []
        if current_question:
            content_parts.append(f"[Last question asked: {current_question}]")
        content_parts.append(input_text)

        full_text = "\n\n".join(content_parts)
        return [Message(role="user", content=[TextBlock(text=full_text)])]

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
            if chunk_texts:
                combined = "\n\n---\n\n".join(chunk_texts)
            else:
                # Be explicit so the LLM knows the KB itself returned nothing —
                # not a transient failure to retrieve. This avoids responses
                # like "I'm having difficulty retrieving …" that imply an
                # outage when the actual cause is an empty-result query.
                combined = (
                    "KNOWLEDGE_BASE_EMPTY_RESULT: The knowledge base was queried "
                    "successfully but returned 0 matching chunks for this query. "
                    "Do NOT apologise for a system failure — there isn't one. "
                    "Either (a) tell the user this topic isn't covered in the "
                    "available documentation and offer adjacent topics that are, "
                    "or (b) ask the user a clarifying question if their query "
                    "was ambiguous."
                )
            logger.info(
                "  [STEP 9] knowledge_retrieval  ←  chunks=%d  query=%r  latency=%dms",
                len(chunk_texts),
                (ke_context.get("normalised_input") or "")[:120],
                int((time.time() - start) * 1000),
            )
            logger.info("manager_agent.knowledge_retrieval", extra={
                "operation": "manager_agent._execute_knowledge_retrieval",
                "status": "success",
                "chunk_count": len(chunk_texts),
                "latency_ms": int((time.time() - start) * 1000),
            })
            return ToolResult(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                result={"context": combined, "chunk_count": len(chunk_texts)},
                # Plain text — what the LLM actually consumes. Without this,
                # the consumer falls back to str(result) which produces an
                # ugly Python dict-repr like "{'context': '…'}".
                result_text=combined,
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
            # Prefer the structured upstream body if the adapter captured one
            # (e.g. a 4xx response excerpt) so the LLM can recover on the
            # next turn — fall back to the bare error tag otherwise.
            result_content = tool_result.result_text or tool_result.error
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
