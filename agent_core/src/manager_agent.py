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
from typing import Callable, Optional

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
from src.identity import render_identity
from src.interfaces.action_gateway import ActionGatewayBase
from src.interfaces.knowledge_engine import KnowledgeEngineBase
from src.interfaces.trust_layer import TrustLayerBase
from src.models import RetrievalChunk, ToolCall, ToolResult
from src.tool_registry import ToolRegistry
from src.tool_results import TurnToolCache

logger = logging.getLogger(__name__)


def zero_seed_fields(slots: dict | None) -> frozenset[str]:
    """Names of int slots whose declared minimum rules out a real answer of 0.

    Such a field is seeded with 0 when unset, so a string ``"0"`` for it is the
    seed. An int slot that accepts 0 (``min: 0``) is not included: ``"0"`` is a
    real answer there.

    Args:
        slots: ``preprocessing.nlu_processor.slots`` — name to slot config.

    Returns:
        The field names for which ``"0"`` means "not told yet".
    """
    names: set[str] = set()
    for name, slot in (slots or {}).items():
        get = slot.get if isinstance(slot, dict) else lambda k, d=None: getattr(slot, k, d)
        low = get("min", None)
        if get("type", None) == "int" and isinstance(low, (int, float)) and low > 0:
            names.add(str(name))
    return frozenset(names)


def _is_collected(
    value: object, field: str = "", zero_seeds: frozenset[str] = frozenset()
) -> bool:
    """Whether a profile value counts as something the caller has told us.

    The previous check listed the empty sentinels explicitly — ``None``,
    ``""``, ``[]``, ``"[]"`` — which covers every string field, since those
    default to ``""``. It does not cover ``age``, the one integer field,
    whose unset default is ``0``.

    A zero therefore rendered under "Already collected — do NOT ask for any
    of these fields again", so the agent never asked the caller's age and
    sent ``age=0`` to the profile API, which rejects it as under-18.

    The string ``"0"`` is the same seed for the int fields in ``zero_seeds``
    (a copy that skipped Memory Layer's int coercion still reads ``"0"``). For
    any other field it is a real value, e.g. ``experience_years`` of zero.

    Args:
        value: A profile field value.
        field: The field's name.
        zero_seeds: Fields for which ``"0"`` is the unset seed.

    Returns:
        True when the value should be shown to the LLM as already collected.
    """
    if isinstance(value, bool):
        return value
    if value in (None, "", "[]"):
        return False
    if value == "0" and field in zero_seeds:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def over_call_cap(cap: int | None, used: int) -> bool:
    """True when a tool has already run its allowed number of times this turn.

    Split out so the sync loop and both streaming loops apply one rule. A cap
    only exists for tools whose effect cannot be taken back.
    """
    # Only an int is a cap. A mocked or misconfigured registry can hand back
    # anything here, and a guard that raises is worse than no guard.
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        return False
    return used >= cap


def refusal_result(tool_name: str, tool_use_id: str, reason: str):
    """Build the ToolResult handed back when a guard refuses execution."""
    return ToolResult(
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        result={},
        success=False,
        error="REFUSED",
        result_text=reason,
    )


def ungrounded_params(
spec: dict[str, list[str]],
tool_call,
messages: list,
stored_results: dict[str, list[str]] | None = None,
strict: bool = False,
session_grounded: dict[str, list[str]] | None = None,
) -> set[str]:
    """Return the configured params whose value no tool result contains.

    A model asked for a 36-character identifier many turns after it was
    shown will sometimes emit a well-formed one it invented. The upstream
    cannot tell that apart from a stale id and answers with a generic
    "not found", which is then relayed to the user as if their request had
    simply been unnecessary. Checking the value against what upstreams
    actually returned catches it before the call is made.

    Args:
        spec: Map of param name to the tool names whose results may supply
            it. An empty source list means any tool result.
        tool_call: The pending call, carrying ``tool_name`` and
            ``input_params``.
        messages: Conversation so far; tool results are read from the
            ``ToolResultBlock`` entries inside it.
        stored_results: Tool name → serialised results that are no longer in
            the message list. Counts exactly like tool_result blocks of that
            tool.
        strict: When True, reject if nothing has been fetched at all (used by
            the ``remember`` tool). When False (default), allow any value on
            the first call.
        session_grounded: Param name → values lifted into session state by a
            producer tool's ``session_mapping``. These came FROM an upstream
            result by construction, so they ground the same way a tool_result
            does — and they outlive the tool-result cache.

            This matters because cache freshness and grounding evidence are
            different questions. ``save_profile`` correctly invalidates the
            cached ``fetch_profile``, since the profile just changed. On the
            NEXT turn the apply then had no evidence for ``profile_item_id``
            and was refused, so a returning caller could never apply at all.

    Returns:
        Names of params that were supplied but appear in no tool result.
        Empty when the tool has no configured params, when a param was not
        supplied, or when every supplied value is grounded.
    """
    if not spec:
        return set()

    # tool_use_id -> tool name, so each result can be attributed to the
    # tool that produced it.
    origin: dict[str, str] = {}
    for msg in messages or []:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "type", "") == "tool_use":
                origin[str(getattr(block, "tool_use_id", ""))] = str(
                    getattr(block, "tool_name", "")
                )

    by_tool: dict[str, list[str]] = {}
    seen_any: list[str] = []
    for msg in messages or []:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "type", "") != "tool_result":
                continue
            content = getattr(block, "content", "")
            if not isinstance(content, str) or not content:
                continue
            seen_any.append(content)
            src = origin.get(str(getattr(block, "tool_use_id", "")), "")
            by_tool.setdefault(src, []).append(content)

    for src, texts in (stored_results or {}).items():
        for text in texts or []:
            if isinstance(text, str) and text:
                seen_any.append(text)
                by_tool.setdefault(str(src), []).append(text)

    # Session values written by a producer's session_mapping count as that
    # producer's output: they were copied out of its result. Recorded against
    # every allowed source for the param, so the per-param source list still
    # decides what may supply it.
    session_values: dict[str, list[str]] = {}
    for name, values in (session_grounded or {}).items():
        for v in values or []:
            if isinstance(v, str) and v:
                session_values.setdefault(name, []).append(v)
                seen_any.append(v)

    if not seen_any:
        # Nothing has been fetched yet, so nothing can be grounded. Let the
        # call through rather than blocking a legitimate first call whose
        # value came from session state seeded outside this conversation.
        if not strict:
            return set()
        return {n for n in spec if (tool_call.input_params or {}).get(n) not in (None, "")}

    missing: set[str] = set()
    for name, sources in spec.items():
        value = (tool_call.input_params or {}).get(name)
        if value in (None, ""):
            continue
        # A session_mapping value for THIS param grounds it outright: it was
        # copied out of a producer's result, and unlike the tool-result cache
        # it survives that result being invalidated.
        if str(value) in (session_values.get(name) or []):
            continue
        if sources:
            pool = [c for src in sources for c in by_tool.get(src, [])]
            if not pool:
                # None of the naming tools has run yet — the value cannot
                # have come from one, so it was carried or invented.
                missing.add(name)
                continue
        else:
            pool = seen_any
        if str(value) not in "\n".join(pool):
            missing.add(name)
    return missing


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
        grounded_params:  Map of tool name to the params whose value must have
                          come from an earlier tool result. Either
                          ``{"apply_job": ["job_item_id"]}`` (any tool result)
                          or ``{"apply_job": {"job_item_id": ["fetch_jobs"]}}``
                          (only those tools' results). Blocks execution when the
                          model supplies an identifier it invented, or one it
                          copied out of a different tool's response.
        identity:         Use-case identity config dict; renders the tier-1 ``<identity>``
                          block. None omits the block.
    """

    def __init__(
        self,
        chat_provider: ChatProviderBase,
        tool_registry: ToolRegistry,
        action_gateway: ActionGatewayBase,
        knowledge_engine: KnowledgeEngineBase,
        trust_layer: TrustLayerBase,
        max_tool_rounds: int = 1,
        grounded_params: dict[str, list[str]] | None = None,
        tool_call_caps: dict[str, int] | None = None,
        zero_seed_fields: frozenset[str] = frozenset(),
        identity: dict | None = None,
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
        # Profile fields for which the string "0" is the unset seed.
        self._zero_seed_fields = frozenset(zero_seed_fields)
        # Use-case identity config (name/disclosure/handoff mode); None = no <identity> block.
        self._identity = identity
        # tool name -> params whose value must have appeared in an earlier tool
        # result this conversation. Guards against the model inventing an
        # identifier that is well-formed but refers to nothing.
        # Normalise both accepted shapes to {tool: {param: [source tools]}}.
        # A list means "any earlier tool result"; a mapping names the tools
        # whose results may supply that param, which is what stops one
        # identifier being copied into another's slot.
        # tool name -> max executions per turn. Guards irreversible writes
        # against a model that acts on every row of a list it was shown.
        self._tool_call_caps: dict[str, int] = {
            str(k): int(v) for k, v in (tool_call_caps or {}).items()
        }
        self._grounded_params: dict[str, dict[str, list[str]]] = {}
        for _tool, _spec in (grounded_params or {}).items():
            if isinstance(_spec, dict):
                self._grounded_params[str(_tool)] = {
                    str(k): [str(t) for t in (v or [])] for k, v in _spec.items()
                }
            else:
                self._grounded_params[str(_tool)] = {str(p): [] for p in (_spec or [])}
        # GH-137: Per-turn flag set when the LLM invokes the end_session internal tool.
        self._session_ended_flag: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ungrounded_params(self, tool_call, messages: list,
                           stored_results: dict[str, list[str]] | None = None) -> set[str]:
        """Instance wrapper around :func:`ungrounded_params` for this agent's config."""
        return ungrounded_params(
            self._grounded_params.get(tool_call.tool_name) or {}, tool_call, messages,
            stored_results=stored_results,
        )

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
        user_id: str = "",
        session_values: dict | None = None,
        tool_cache: TurnToolCache | None = None,
        remember_name: str = "",
        remember_handler: Callable[[ToolCall, list], ToolResult] | None = None,
    ) -> tuple[str, list[ToolCall], list[ToolResult]]:
        """
        Drive the tool-use loop starting from the initial LLM response.

        Routes knowledge_retrieval tool calls directly to the Knowledge Engine
        via _execute_knowledge_retrieval. All other tool calls go through the
        Action Gateway's consent gate and execution path.

        Per-turn call caps (``tool_call_caps``) are checked for every tool
        except ``remember``, which is handled first and is never capped nor
        counted. Only live Action Gateway calls increment a tool's count:
        stored-result hits, grounding refusals and knowledge retrieval do
        not, so a refused call can be retried with a valid value and a
        cached read can be served more than once. The streaming path
        (``AgentCore.stream_turn``) applies the same rules.

        Args:
            messages:         The messages list (neutral chat_provider types) that produced
                              initial_response. Extended in-place with tool_use and
                              tool_result blocks as Message objects.
            session_id:       Used for consent checks and gateway calls.
            user_id:          Stable user identity forwarded to the Action Gateway so
                              connectors can substitute ``{user_id}`` into paths and
                              body templates. Defaults to "" for callers that have no
                              user context.
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
                              entities, confidence, normalised_input,
                              detected_language. If None, knowledge_retrieval calls
                              return an empty tool_result.
            tool_cache:       Per-turn tool-result cache. When given, cacheable
                              calls are served from stored results and live
                              results are recorded for persistence. None
                              disables all of it.
            remember_name:    Name of the framework ``remember`` tool; calls to
                              it go to ``remember_handler`` and never reach the
                              Action Gateway. Empty disables.
            remember_handler: Callable ``(tool_call, messages) -> ToolResult``
                              that handles ``remember`` calls.

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
        self._reset_turn_flags(session_values)

        current_response = initial_response
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        rounds = 0
        # Per-TURN, not per-round: a capped tool must not slip through by
        # being requested again in a later tool round of the same turn.
        _turn_tool_counts: dict[str, int] = {}

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

                # The framework ``remember`` tool is a validated state write,
                # not an upstream effect: it is never capped nor counted.
                _is_remember = remember_handler is not None and tool_call.tool_name == remember_name
                _used = _turn_tool_counts.get(tool_call.tool_name, 0)
                if not _is_remember and over_call_cap(
                    self._tool_call_caps.get(tool_call.tool_name), _used,
                ):
                    logger.warning(
                        "manager_agent.tool_call_cap tool=%s used=%s",
                        tool_call.tool_name, _used,
                    )
                    tool_result = refusal_result(
                        tool_call.tool_name, tool_call.tool_use_id,
                        f"Refused: {tool_call.tool_name} has already run this turn and its "
                        f"effect cannot be undone. One per turn. If the caller meant a "
                        f"different one, ask which, and call it on the next turn.",
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
                        content=tool_result.result_text,
                    ))
                    continue

                if _is_remember:
                    tool_result = remember_handler(tool_call, messages)
                else:
                    _stored = tool_cache.stored_results_by_tool() if tool_cache else None
                    _ungrounded = self._ungrounded_params(tool_call, messages, _stored)
                    if _ungrounded:
                        # The model supplied an identifier no upstream ever returned.
                        # Refuse to execute and tell it so — a fabricated id reaches
                        # the upstream as a well-formed value and comes back as a
                        # generic "not found", which the model then reports to the
                        # caller as though the request had merely been redundant.
                        logger.warning(
                            "manager_agent.ungrounded_param tool=%s params=%s",
                            tool_call.tool_name, sorted(_ungrounded),
                        )
                        tool_result = ToolResult(
                            tool_use_id=tool_call.tool_use_id,
                            tool_name=tool_call.tool_name,
                            success=False,
                            result={},
                            error="UNGROUNDED_PARAMETER",
                            result_text=(
                                f"Refused: {', '.join(sorted(_ungrounded))} did not come from "
                                f"any tool result in this conversation, so the value was "
                                f"invented. Do not guess an identifier. Re-read the most "
                                f"recent tool result, copy the exact value for the item the "
                                f"user chose, and call this tool again."
                            ),
                        )
                    elif self._registry.get_route(tool_call.tool_name) == "knowledge_engine":
                        tool_result = self._execute_knowledge_retrieval(tool_call, ke_context)
                    else:
                        _hit = tool_cache.lookup(tool_call) if tool_cache else None
                        if _hit is not None:
                            tool_result = _hit
                        else:
                            # Only a live Action Gateway call counts toward the
                            # per-turn cap: stored results, refusals and
                            # knowledge retrieval have no upstream effect.
                            _turn_tool_counts[tool_call.tool_name] = _used + 1
                            _call = tool_cache.prepare(tool_call) if tool_cache else tool_call
                            tool_result = self._execute_tool(_call, session_id, user_id)
                            if tool_cache:
                                tool_cache.after_call(tool_call, tool_result)
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

    def _reset_turn_flags(self, session_values: dict | None = None) -> None:
        """Clear per-turn flags at the top of each ``run_turn`` invocation.

        Args:
            session_values: Turn state handed to the Action Gateway so
                connector params declared ``source: session`` resolve from
                what the framework knows rather than from what the model can
                reproduce. Replaced every turn; defaults to empty.
        """
        self._session_ended_flag = False
        self._session_values: dict = dict(session_values or {})

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
        known_facts: str = "",
        caller_turn: str = "",
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
            <known_facts>         stored tool results rendered for grounding
            <caller_turn>         NLU conclusion for this turn (dialogue-act NLU)
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
            known_facts:            Rendered stored tool results (from
                                    ``TurnToolCache.render_known_facts``); empty
                                    elides the ``<known_facts>`` section.
            caller_turn:            Rendered NLU conclusion (``render_caller_turn``);
                                    empty elides ``<caller_turn>``.

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
            xml("identity", render_identity(self._identity)),
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
                if k not in skip_keys and _is_collected(v, k, self._zero_seed_fields):
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
            xml("known_facts", known_facts),
            xml("caller_turn", caller_turn),
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

    def _execute_tool(
        self, tool_call: ToolCall, session_id: str, user_id: str = ""
    ) -> ToolResult:
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
        result = self._gateway.execute(
            tool_call, session_id, user_id,
            session_values=getattr(self, "_session_values", {}),
        )
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
