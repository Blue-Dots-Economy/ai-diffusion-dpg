"""
agent_core/orchestrator.py

Concrete implementation of AgentCoreBase.
Wires all components and executes the turn sequence.

Design rules enforced here:
- Trust Layer is called exactly twice per turn (input + output). Neither is skippable.
- Agent Core holds zero session state between turns.
- Language Normalisation and dialogue-act understanding run directly in Agent
  Core (steps 4-5); understanding uses its own dedicated NLU provider.
- SubAgent routing is deterministic: intent + optional session conditions → next_subagent_id.
- Special handlers (hitl, whatsapp_handoff) bypass LLM inference entirely.
- Steps 12-13 (memory write, learning emit) run in a daemon thread after TurnResult is returned.
- This is the only file that imports and coordinates all DPG interfaces together.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any, Optional

from src.base import AgentCoreBase
from src.chat_provider import build_chat_provider
from src.chat_provider.base import ChatProviderBase, ToolUseRequested, ProviderAPIError, SAFE_MESSAGES, DEFAULT_SAFE_MESSAGE
from src.conditions import evaluate_condition
from src.handoff import build_handoff_payload, choose_handoff_line
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
from src.manager_agent import ManagerAgent, zero_seed_fields
from src.tool_guard import apply_session_only, check_tool_call
from src.predispatch.rules import Selection, select
from src.predispatch.runner import PREDISPATCH_ID, PredispatchResult, run_async, run_sync
from src.models import (
    DoneEvent,
    NLUResult,
    SentenceEvent,
    SignalEvent,
    StreamEvent,
    ToolCall,
    ToolResult,
    TrustCheckResult,
    TurnEvent,
    TurnInput,
    TurnRecord,
    TurnResult,
)
from src.tool_registry import ToolRegistry
from src.understanding.caller_turn import render_caller_turn
from src.understanding.config import DialogueActConfig
from src.understanding.dialogue_act_nlu import DialogueActNLU
from src.understanding.history import RECENT_TURNS_KEY, append_recent_turn
from src.context.state import render_recent, render_state
from src.output.contract import contract_language, sentence_language
from src.output.guard import OutputGuard
from src.understanding.frame import offered_entry, offered_rows
from src.understanding.pending import PendingResolver
from src.chat_provider.metrics import record_output_guard, record_predispatch
from src.understanding.precedence import nlu_owned_values
from src.understanding.understander import TurnContext, TurnUnderstander, TurnUnderstanderBase
from src.tool_results import ToolResultPolicies, TurnToolCache, augment_tool_definitions
from src.remember import RememberTool
from src.output.result_shaping import ResultShaper
from src.session_bootstrap import SessionBootstrap
from src.turn_policy import TurnPolicy, resolve_turn_policy
from src.workflow_loader import AgentWorkflow, RoutingCondition, RoutingRule, SubAgent
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

logger = logging.getLogger(__name__)

# Session key: tool → args_hash of the stored result the caller last heard
# (NLU dialogue-acts spec §5.2).
SERVED_TOOL_RESULTS_KEY = "served_tool_results"

# A ``turn_carryover`` is written by whichever replica ran the interrupted
# turn and read by whichever runs the next one, so their clocks can disagree.
# A ``written_at_ms`` up to this far in the future counts as age 0; beyond it
# the value is treated as malformed. A fixed tolerance, not a tunable.
_CARRYOVER_CLOCK_SKEW_MS = 5000
# A handoff marked "pending" this recently is still in flight: a second
# human_request speaks the already line instead of escalating again
# (identity/handoff spec §5.2). An older pending marker allows a new escalate.
_HANDOFF_PENDING_WINDOW_MS = 30_000
# Shared `operation` value for every handoff log record.
_OP_HANDOFF = "orchestrator.handoff"

# Module-level guard to prevent double-instrumentation in test environments.
_HTTPX_INSTRUMENTED = False


def emit_consent_event_if_recording(
    *,
    queue: list,
    purpose: str,
    granted: bool,
    configured_purpose: str,
    turn_id: str,
) -> None:
    """Append a 'consent' SSE event to ``queue`` iff this is the configured
    recording purpose and consent was granted.

    Args:
        queue: Event queue (list of dicts) the SSE writer drains.
        purpose: The consent purpose just verified.
        granted: True iff Trust Layer returned granted.
        configured_purpose: Value of
            ``reach_layer.channels.voice.recording.consent_purpose``.
        turn_id: Current turn identifier for correlation.
    """
    if not granted:
        return
    if purpose != configured_purpose:
        return
    queue.append({
        "type": "consent",
        "purpose": purpose,
        "granted": True,
        "consent_granted_ts": time.time(),
        "turn_id": turn_id,
    })


def _text_of(resp: ChatResponse) -> str | None:
    """Return the first TextBlock's text from a ChatResponse, or None."""
    for b in resp.content:
        if b.type == "text":
            return b.text
    return None


def _tool_calls_of(resp: ChatResponse) -> list:
    """Convert ChatResponse tool_use blocks to legacy ToolCall list."""
    from src.models import ToolCall  # local import avoids circular at module level
    return [
        ToolCall(
            tool_name=b.tool_name,
            tool_use_id=b.tool_use_id,
            input_params=b.input,
        )
        for b in resp.content if b.type == "tool_use"
    ]


def _legacy_tools_to_neutral(tools: list[dict]) -> list[ToolDefinition]:
    """Convert legacy Anthropic-shape tool dicts to neutral ToolDefinition."""
    return [
        ToolDefinition(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("input_schema", {}),
        )
        for t in (tools or [])
    ]


class AgentCore(AgentCoreBase):
    """
    Stateless orchestrator. Holds references to injected components only —
    no session-scoped data stored as instance state.

    All components are injected at construction time. The startup entrypoint
    (main.py or equivalent) is the only place that instantiates and wires them.

    Args:
        config:           Domain configuration dict.
        chat_provider:    ChatProviderBase implementation; the sole LLM caller.
        memory:           Memory Layer interface.
        trust:            Trust Layer interface.
        knowledge_engine: Knowledge Engine interface.
        tool_registry:    Pre-built tool registry (initialised at startup).
        manager_agent:    Prompt assembly + tool-use loop handler.
        learning:         Observability Layer interface (async emit).
        workflow:         Pre-parsed and validated AgentWorkflow loaded at startup.
        nlu_chat_provider: Provider for the dialogue-act NLU call; when None a
                          dedicated one is built (``_build_dialogue_act_provider``).
    """

    def __init__(
        self,
        config: dict,
        chat_provider: ChatProviderBase,
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
        nlu_chat_provider: ChatProviderBase | None = None,
    ) -> None:
        if config is None:
            raise ValueError("config must not be None")
        if workflow is None:
            raise ValueError("workflow must not be None")

        self._config = config
        self._result_shaper = ResultShaper(self._config)
        # Resolved per channel on first use; config is immutable after startup.
        self._turn_policies: dict[str, TurnPolicy] = {}
        self._llm = chat_provider
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
        # Optional allowlist of subagent ids permitted to call end_session.
        # Empty keeps the original behaviour (every subagent gets the tool).
        _raw_allow = session_end_cfg.get("subagents") or []
        self._session_end_subagents: set[str] = {
            str(x) for x in _raw_allow if isinstance(_raw_allow, list)
        }

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
                _allow = self._session_end_subagents
                tool_defs = getattr(self._workflow, "tool_defs", None)
                if isinstance(tool_defs, dict):
                    for _sa_id, _tools in list(tool_defs.items()):
                        if not isinstance(_tools, list):
                            continue
                        if _allow and _sa_id not in _allow:
                            continue
                        if not any(t.get("name") == "end_session" for t in _tools):
                            _tools.append(end_session_def)
                # The shared global list is visible to EVERY subagent, so it can
                # only carry end_session when no allowlist is in force.
                global_defs = getattr(self._workflow, "global_tool_defs", None)
                if not _allow and isinstance(global_defs, list) and global_defs:
                    if not any(t.get("name") == "end_session" for t in global_defs):
                        global_defs.append(end_session_def)
                logger.info(
                    "orchestrator.end_session_scoped subagents=%s",
                    sorted(_allow) if _allow else "ALL",
                )
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
        # Strong references to fire-and-forget tasks so they are not collected mid-flight.
        self._bg_tasks: set[asyncio.Task] = set()

        # Language Normalisation and NLU run directly in Agent Core.
        # Each helper gets its own ChatProvider — when the configured
        # provider+model differs from the primary, a dedicated provider is
        # built; otherwise the shared primary provider is reused to avoid
        # extra client connections. Each helper may override BOTH the
        # provider and the model independently of agent.provider, so a
        # deployment can run primary chat on OpenAI while keeping NLU on
        # Anthropic (or vice versa) — see #287 follow-up.
        agent_block = self._config.get("agent", {}) or {}
        primary_model = agent_block.get("primary_model", "")
        primary_provider = agent_block.get("provider", "anthropic")

        self._lang_chat_provider = self._build_helper_provider(
            block=(self._config.get("preprocessing", {}) or {}).get(
                "language_normalisation", {}
            ) or {},
            primary_model=primary_model,
            primary_provider=primary_provider,
        )

        self._language_normaliser = LanguageNormaliser(chat_provider=self._lang_chat_provider)
        # NLU dialogue-acts spec §16: the single understanding path, always on.
        self._dialogue_cfg: DialogueActConfig = DialogueActConfig.from_config(self._config)
        self._understander: TurnUnderstanderBase = TurnUnderstander(
            self._dialogue_cfg, self._workflow,
            DialogueActNLU(self._dialogue_cfg, nlu_chat_provider or self._build_dialogue_act_provider()))
        agent_cfg = self._config.get("agent") or {}
        self._agent_history_turns = int(agent_cfg.get("history_turns", 2))
        self._state_fields = list(agent_cfg.get("state_fields") or [])
        # Int fields whose string "0" is the unset seed (#436 D1): <state>
        # must not list them as collected.
        self._zero_seed_fields = zero_seed_fields(
            ((self._config.get("preprocessing") or {}).get("nlu_processor") or {}).get("slots"))
        # recent_turns serves both the NLU frame and <recent>; keep enough for either.
        self._recent_keep = max(self._dialogue_cfg.history_turns, self._agent_history_turns)
        self._pending_resolver = PendingResolver(self._workflow)

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

        # Tool-result persistence: per-tool cache/invalidate policies and the
        # optional framework ``remember`` tool, both derived from config.
        self._tool_policies = ToolResultPolicies.from_config(config)
        self._bootstrap = SessionBootstrap.from_config(
            config, self._tool_policies, shape=self._result_shaper.shape,
        )
        self._remember = RememberTool.from_config(config)
        self._prompt_session_fields: list[str] = list(
            ((config.get("agent") or {}).get("prompt_session_fields")) or [])
        self._init_predispatch(config)

    def _init_predispatch(self, config: dict) -> None:
        """Read the pre-dispatch inputs and check rule args are agent params (Spec E §4, plan ruling 5).

        Args:
            config: The merged domain config dict.

        Raises:
            ValueError: A rule binds an argument key that is not a property of
                the tool's input schema. Skipped when the gateway tool list is
                empty (no schema to check against, e.g. in tests).
        """
        self._predispatch_tables: dict = dict(config.get("predispatch_tables") or {})
        self._predispatch_timeout_s: float = int(
            (config.get("agent") or {}).get("predispatch_timeout_ms", 1500)) / 1000
        conns = config.get("connectors") or {}
        self._write_tools: set[str] = {
            c["name"] for g in ("write", "identity") for c in (conns.get(g) or [])
            if isinstance(c, dict) and c.get("name")}
        try:
            defs = self._tool_registry.get_tool_definitions()
        except Exception:  # noqa: BLE001 — a mocked/partial registry has no schemas
            defs = []
        self._tool_schemas: dict[str, dict] = {
            d["name"]: d.get("input_schema") or {} for d in (defs if isinstance(defs, list) else [])
            if isinstance(d, dict) and d.get("name")}
        for sa in (getattr(self._workflow, "subagents", None) or {}).values():
            rules = getattr(sa, "predispatch", None)
            for i, rule in enumerate(rules if isinstance(rules, list) else []):
                props = (self._tool_schemas.get(rule.get("tool")) or {}).get("properties") or {}
                unknown = [k for k in (rule.get("args") or {}) if k not in props]
                if props and unknown:
                    raise ValueError(
                        f"subagents[{sa.id}].predispatch[{i}]: args {unknown} are not agent "
                        f"parameters of '{rule.get('tool')}'")

    def _build_profile_context(self, bundle, entity_map: dict) -> dict:
        """Profile facts for <state> collected: profile, NLU-mapped session fields, listed session fields.

        bundle.profile is the source of truth for declared profile fields;
        persistent NLU writes update it in-place earlier in the turn. Session
        values only fill what it does not carry, supporting the
        entity_persistence.scope="session" path without letting stale session
        copies overwrite fresh persistent values (the previous unconditional
        overlay caused the age-stuck-at-0 leak: an "0" string from a
        session-init copy outranked a fresh "25" in Memgraph because the
        empty-check did not catch "0"). ``agent.prompt_session_fields`` names
        further session fields, e.g. ones written by session_mapping, that the
        prompt may read (session-bootstrap spec section 5.4). NLU-owned values
        (slot_provenance) win; see NLU dialogue-acts spec §6.5.

        Args:
            bundle: Context bundle with ``profile`` and ``session`` mappings.
            entity_map: NLU entity-to-profile-field mapping.

        Returns:
            New dict of profile context for prompt assembly.
        """
        profile_context = dict(bundle.profile)
        overlay = set(entity_map.values()) | set(self._prompt_session_fields)
        for k, v in bundle.session.items():
            if k in overlay and v not in (None, "", "[]") and not profile_context.get(k):
                profile_context[k] = v
        profile_context.update(nlu_owned_values(bundle.session))
        return profile_context

    def _render_state(self, bundle, subagent_id: str, tool_cache, profile_context: dict) -> str:
        """<state> for the subagent the prompt is built for (Spec D §6.3). Never raises."""
        try:
            pending = self._pending_resolver.resolve(subagent_id, self._routing_state(bundle))
            offered: list[dict] = []
            of = getattr(pending, "options_from", None) if pending is not None else None
            if of is not None:
                served = bundle.session.get(SERVED_TOOL_RESULTS_KEY)
                offered = offered_rows(offered_entry(served, tool_cache, of.tool))
            status = {k: bundle.session.get(k) for k in self._state_fields}
            return render_state(phase=subagent_id, pending=pending, collected=profile_context,
                                offered=offered, status=status, zero_seeds=self._zero_seed_fields)
        except Exception as e:  # noqa: BLE001 — never raise into the turn
            logger.warning("orchestrator.state_render_failed",
                           extra={"operation": "orchestrator.render_state", "status": "failure",
                                  "error": type(e).__name__})
            return ""

    @staticmethod
    def _make_output_guard(channel_config, profile_context: dict, bundle):
        """Build the per-turn output guard.

        Returns:
            ``(guarded, counts, language)``: ``guarded(text)`` applies the guard and
            accumulates ``counts``; ``language`` is the contract language for the turn.
        """
        contract = (channel_config or {}).get("output_contract")
        guard = OutputGuard(contract)
        preference = profile_context.get("language_preference") or bundle.session.get("language_preference")
        default_language = str((contract or {}).get("default_language") or "") if isinstance(contract, dict) else ""
        try:
            language = contract_language(contract, preference)
        except Exception:  # noqa: BLE001 — e.g. an unhashable preference
            language = default_language
        counts = {"digits_rewritten": 0, "foreign_script_words": 0}

        def guarded(text: str) -> str:
            try:
                lang = sentence_language(text, contract, preference)
            except Exception:  # noqa: BLE001 — never raise into the turn
                lang = default_language
            r = guard.apply(text, lang)
            counts["digits_rewritten"] += r.digits_rewritten
            counts["foreign_script_words"] += r.foreign_script_words
            return r.text

        return guarded, counts, language

    async def _stream_consent_ok(self, session_id: str, tc) -> bool | None:
        """Consent decision for a streaming tool call (pre-dispatch only).

        Not used for model-initiated calls: Blue Dots does not record consent
        in the Trust Layer ConsentStore (it records it through NLU and routing
        ``session_writes``), so enforcing ``check_consent`` there would refuse
        every live write. Model calls keep their historical behaviour and pass
        ``consent_ok=None`` to the guard.

        Args:
            session_id: Session identifier.
            tc: The pending ToolCall.

        Returns:
            None when the tool needs no consent, else whether it is granted
            (False when no trust client is available).
        """
        if not self._tool_registry.requires_consent(tc.tool_name):
            return None
        if not self._async_trust:
            return False
        return bool(await self._async_trust.check_consent(session_id, tc.tool_name))

    def _session_grounded_values(self, bundle, spec: dict) -> dict[str, list[str]]:
        """Values for grounded params that session_mapping lifted from a tool.

        A param named in ``grounded_params`` is often also a session field — it
        is written there by the producing connector's ``session_mapping``. Those
        copies came from the upstream result, so they ground the call, and they
        outlive the tool-result cache.

        That difference is the point. ``save_profile`` invalidates the cached
        ``fetch_profile`` because the profile has just changed, which is
        correct. Without this, the NEXT turn's ``apply_job`` had no evidence
        for ``profile_item_id`` and was refused — so a returning caller could
        never apply (measured on the VM against UAT).

        Args:
            bundle: This turn's context bundle.
            spec: The tool's ``grounded_params`` map.

        Returns:
            Param name → the session value for it, when present and non-empty.
        """
        out: dict[str, list[str]] = {}
        if not spec:
            return out
        session = getattr(bundle, "session", None) or {}
        for name in spec:
            v = session.get(name)
            if isinstance(v, str) and v:
                out[name] = [v]
        return out

    def _remember_on_saved(self, bundle, turn_session_values: dict | None = None):
        """Build the callback that mirrors a remembered value into the bundle.

        Args:
            bundle: This turn's context bundle.
            turn_session_values: The sync path's per-turn ``source: session``
                lookup (``ManagerAgent._session_values``). run_turn copies it
                once at the start of the turn, so without a refresh a value
                remembered mid-turn would be invisible to later tool calls in
                the same turn. The streaming path rebuilds its lookup from the
                bundle per call and passes None.

        Returns:
            Callable ``(scope, key, value) -> None`` that writes into
            ``bundle.session`` for session scope, else ``bundle.profile``,
            and for session scope refreshes ``turn_session_values``.
        """
        def _on_saved(scope: str, key: str, value) -> None:
            (bundle.session if scope == "session" else bundle.profile)[key] = value
            if scope == "session" and isinstance(turn_session_values, dict):
                # Rebuild with the same precedence and filtering the turn
                # started with (profile over session, seeded defaults dropped).
                turn_session_values.update(self._tool_session_values(bundle))
        return _on_saved

    def _persist_tool_cache_sync(self, session_id: str, user_id: str, tool_cache) -> None:
        """Send the sync turn's pending tool-result changes to Memory Layer.

        Args:
            session_id: Session the turn belongs to.
            user_id: Caller identity, for user-scoped entries.
            tool_cache: This turn's ``TurnToolCache``; drained when it has
                pending puts or invalidations, otherwise left untouched.

        Never raises: a Memory Layer failure is logged (exception class only)
        so it cannot mask the turn's own outcome or exception.
        """
        if not tool_cache.has_pending():
            return
        try:
            self._memory.apply_tool_results(session_id, user_id, tool_cache.drain_batch())
        except Exception as e:
            logger.error("orchestrator.apply_tool_results_error", extra={
                "operation": "orchestrator.process_turn", "status": "failure",
                "session_id": session_id, "error": type(e).__name__})

    async def _persist_tool_cache(self, session_id: str, user_id: str, tool_cache) -> None:
        """Send pending tool-result changes now; a streaming turn may be interrupted later.

        Args:
            session_id: Session the turn belongs to.
            user_id: Caller identity, for user-scoped entries.
            tool_cache: This turn's ``TurnToolCache``; drained when it has
                pending puts or invalidations, otherwise left untouched.

        Never raises: a Memory Layer failure is logged and the turn continues.
        """
        if not tool_cache.has_pending():
            return
        try:
            await self._async_memory.apply_tool_results(session_id, user_id, tool_cache.drain_batch())
        except Exception as e:
            logger.error("orchestrator.apply_tool_results_error", extra={
                "operation": "orchestrator.stream_turn", "status": "failure",
                "session_id": session_id, "error": type(e).__name__})

    # ------------------------------------------------------------------
    # Pre-dispatch (Spec E §5): determined tool calls before the main LLM
    # ------------------------------------------------------------------

    def _select_predispatch(self, bundle, subagent_id: str, intent: str, tool_cache) -> Selection:
        """The turn's pre-dispatch selection for the post-routing subagent (Spec E §3.3).

        Arguments bind from ``_tool_session_values``: session values with
        seeded empties dropped, profile layered over them, and values the NLU
        wrote this turn on top — so a correction made this turn wins.

        Args:
            bundle: This turn's context bundle, after this turn's writes.
            subagent_id: The routed subagent.
            intent: This turn's routing intent.
            tool_cache: This turn's TurnToolCache.

        Returns:
            Selection (never raises).
        """
        sa = self._workflow.subagents.get(subagent_id)
        rules = getattr(sa, "predispatch", None)
        return select(rules if isinstance(rules, list) else [], intent=intent,
                      state=self._routing_state(bundle), session=self._tool_session_values(bundle),
                      tables=self._predispatch_tables, tool_schemas=self._tool_schemas,
                      write_tools=self._write_tools,
                      has_fresh=lambda t, a: tool_cache.has_fresh_for(t, a))

    def _enforce_session_only(self, tc, bundle) -> None:
        """Replace or drop this call's ``session_only_params`` before it is guarded.

        Runs on every dispatch path. A field the caller never gave is removed
        rather than sent with whatever the model supplied.
        """
        spec = (getattr(self._manager_agent, "_session_only_params", None) or {}).get(
            getattr(tc, "tool_name", "")) or {}
        if not spec:
            return
        dropped = apply_session_only(tc, spec, getattr(bundle, "session", None))
        if dropped:
            logger.info("orchestrator.session_only_dropped", extra={
                "operation": "orchestrator.session_only", "status": "success",
                "tool": tc.tool_name, "dropped": sorted(dropped)})

    def _predispatch_guard(self, tc, bundle, tool_cache, counts: dict, consent_ok: bool | None,
                           pending_id: str | None = None):
        """The shared guard (consent, cap, grounding, cache) for a pre-dispatch call.

        Args:
            tc: The pre-dispatch ToolCall.
            bundle: This turn's context bundle.
            tool_cache: This turn's TurnToolCache.
            counts: The turn's per-tool live-call counts (shared with model calls).
            consent_ok: The path's consent decision (None = not required).
            pending_id: The question open this turn, for tools that declare
                ``requires_pending``.

        Returns:
            GuardVerdict.
        """
        self._enforce_session_only(tc, bundle)
        grounded = getattr(self._manager_agent, "_grounded_params", None)
        caps = getattr(self._manager_agent, "_tool_call_caps", None)
        spec = (grounded if isinstance(grounded, dict) else {}).get(tc.tool_name) or {}
        return check_tool_call(
            tc, cap=(caps if isinstance(caps, dict) else {}).get(tc.tool_name),
            used=counts.get(tc.tool_name, 0), grounded_spec=spec, messages=[],
            stored_results=tool_cache.stored_results_by_tool(),
            session_grounded=self._session_grounded_values(bundle, spec),
            consent_ok=consent_ok, cache_lookup=tool_cache.lookup,
            requires_pending=self._requires_pending_for(tc.tool_name),
            pending_id=pending_id)

    def _requires_pending_for(self, tool_name: str) -> str | None:
        """The pending question this tool declares it may only answer, if any."""
        spec = getattr(self._manager_agent, "_requires_pending", None)
        return (spec if isinstance(spec, dict) else {}).get(tool_name)

    @staticmethod
    def _predispatch_error(operation: str, session_id: str, e: Exception) -> PredispatchResult:
        """Log an internal pre-dispatch failure (type only) and treat it as did-not-fire."""
        logger.warning("orchestrator.predispatch_error", extra={
            "operation": operation, "status": "failure", "session_id": session_id,
            "error": type(e).__name__})
        return PredispatchResult("error")

    def _offered_tools(self, subagent_id: str):
        """The tool definitions this turn's main-LLM calls start from (before pre-dispatch removal)."""
        return augment_tool_definitions(
            self._workflow.resolve_tools_for(subagent_id), self._tool_policies,
            self._remember.definition() if self._remember else None,
        )

    def _count_live_call(self, tc, counts: dict):
        """Apply the per-turn count rule to a pre-dispatch's live call.

        A write counts BEFORE it leaves, so a timed-out or raising write has
        used the cap up; a read counts only once it has succeeded.

        Args:
            tc: The pre-dispatch ToolCall.
            counts: The turn's ``_turn_tool_counts`` (mutated).

        Returns:
            Callback to run with the gateway result.
        """
        def bump() -> None:
            counts[tc.tool_name] = counts.get(tc.tool_name, 0) + 1

        if tc.tool_name in self._write_tools:
            bump()
            return lambda _r: None
        return lambda r: bump() if getattr(r, "success", False) else None

    async def _predispatch_async(self, bundle, subagent_id: str, intent: str, tool_cache,
                                 session_id: str, user_id: str, counts: dict,
                                 pending_id: str | None = None) -> PredispatchResult:
        """Stream path: select, guard and execute the turn's pre-dispatch under its budget.

        The live execute mirrors the stream tool loop (gateway, shape, map
        session values, cache, persist). See :meth:`_count_live_call` for how
        it counts toward the per-turn cap.

        Args:
            bundle: This turn's context bundle.
            subagent_id: The routed subagent.
            intent: This turn's routing intent.
            tool_cache: This turn's TurnToolCache.
            session_id: Session id.
            user_id: Caller identity.
            counts: The turn's ``_turn_tool_counts``.

        Returns:
            PredispatchResult; never raises.
        """
        try:
            if not self._async_gateway:
                return PredispatchResult(None)
            sel = self._select_predispatch(bundle, subagent_id, intent, tool_cache)

            async def _guard(tc):
                return self._predispatch_guard(tc, bundle, tool_cache, counts,
                                               await self._stream_consent_ok(session_id, tc),
                                               pending_id=pending_id)

            async def _execute(tc):
                on_result = self._count_live_call(tc, counts)
                r = await self._async_gateway.execute(
                    tool_cache.prepare(tc), session_id, user_id,
                    session_values=self._tool_session_values(bundle))
                on_result(r)
                r = self._result_shaper.shape(r)
                await self._write_mapped_session_values(session_id, user_id, r, bundle)
                tool_cache.after_call(tc, r)
                await self._persist_tool_cache(session_id, user_id, tool_cache)
                return r

            pd = await run_async(sel, guard=_guard, execute=_execute,
                                 timeout_s=self._predispatch_timeout_s)
            return self._label(pd, sel)
        except Exception as e:  # noqa: BLE001 — pre-dispatch never raises into the turn
            return self._predispatch_error("orchestrator.stream_turn", session_id, e)

    def _predispatch_sync(self, bundle, subagent_id: str, intent: str, tool_cache,
                          session_id: str, user_id: str, counts: dict,
                          pending_id: str | None = None) -> PredispatchResult:
        """Sync path twin of :meth:`_predispatch_async`; budget is the gateway's own timeout.

        Consent is checked through ``trust.check_consent`` so a pre-dispatched
        write is consent-gated as ``ManagerAgent._execute_tool`` would gate it.
        Mapped session values are written at once, with the same helper the
        sync path uses for run_turn's results.

        Returns:
            PredispatchResult; never raises.
        """
        try:
            gateway = getattr(self._manager_agent, "_gateway", None)
            if gateway is None:
                return PredispatchResult(None)
            sel = self._select_predispatch(bundle, subagent_id, intent, tool_cache)

            def _guard(tc):
                consent = None
                if self._tool_registry.requires_consent(tc.tool_name):
                    # No trust client refuses, as _stream_consent_ok does.
                    consent = bool(self._trust is not None
                                   and self._trust.check_consent(session_id, tc.tool_name))
                return self._predispatch_guard(tc, bundle, tool_cache, counts, consent,
                                               pending_id=pending_id)

            def _execute(tc):
                on_result = self._count_live_call(tc, counts)
                r = gateway.execute(tool_cache.prepare(tc), session_id, user_id,
                                    session_values=self._tool_session_values(bundle))
                on_result(r)
                r = self._result_shaper.shape(r)
                self._write_tool_session_values_sync(session_id, user_id, [r], bundle)
                tool_cache.after_call(tc, r)
                self._persist_tool_cache_sync(session_id, user_id, tool_cache)
                return r

            return self._label(run_sync(sel, guard=_guard, execute=_execute), sel)
        except Exception as e:  # noqa: BLE001 — pre-dispatch never raises into the turn
            return self._predispatch_error("orchestrator.process_turn", session_id, e)

    @staticmethod
    def _label(pd: PredispatchResult, sel: Selection) -> PredispatchResult:
        """Name the skipped rule's tool on a skip outcome (metric/log label only)."""
        if pd.tool is None and getattr(sel, "considered_tool", None):
            return dataclasses.replace(pd, tool=sel.considered_tool)
        return pd

    def _write_tool_session_values_sync(self, session_id: str, user_id: str,
                                        tool_results, bundle) -> None:
        """Persist ``session_values`` a connector's session_mapping lifted (sync path).

        Args:
            session_id: Session receiving the writes.
            user_id: Owning user.
            tool_results: ToolResults whose ``session_values`` to write.
            bundle: Live context bundle, updated so the same turn sees them.
        """
        for tr in tool_results or []:
            for key, val in (getattr(tr, "session_values", None) or {}).items():
                self._write_memory_sync(session_id, user_id, "session", key, val)
                bundle.session[key] = val

    def _predispatch_ledger(self, pd: PredispatchResult, turn_id: str):
        """The turn's record of an injected pre-dispatch, built as soon as it ran.

        Built before any early exit so an exit after a fired write still keeps
        the exchange, the post-tool hook and the audit. The exchange is stored
        for replay under a turn-unique id, so a replayed pre-dispatch never
        shares ``PREDISPATCH_ID`` with the next turn's own.

        Args:
            pd: The runner's result.
            turn_id: This turn's id.

        Returns:
            ``(results, exchanges, calls)``: empty lists unless ``pd.inject``.
        """
        if not (pd.inject and pd.tool_result is not None and pd.tool_call is not None):
            return [], [], []
        replay_id = f"{PREDISPATCH_ID}-{turn_id}"
        ex = self._capture_tool_exchange(
            [dataclasses.replace(pd.tool_call, tool_use_id=replay_id)],
            [{"type": "tool_result", "tool_use_id": replay_id,
              "content": self._predispatch_content(pd.tool_result)}],
            self._recent_tool_exchanges_caps()[1])
        return [pd.tool_result], ([ex] if ex is not None else []), [pd.tool_call]

    @staticmethod
    def _predispatch_content(r) -> str:
        return r.result_text or (r.error if not r.success else "") or str(r.result)

    def _inject_predispatch(self, pd: PredispatchResult, messages: list, active_tools):
        """Add the synthetic pair after the utterance and settle this turn's tool list.

        The list ends on a user tool_result, the shape the model sees after
        calling the tool itself. A tool the outcome removes leaves the list.
        When it is the only tool offered, the definitions stay (a request
        carrying tool_use/tool_result blocks must define tools) and every
        main-LLM call this turn sends ``tool_choice="none"`` instead, when the
        provider supports forcing a choice. A provider without that keeps the
        tool offered under ``"auto"``; the per-turn cap and the turn cache are
        then what stop a repeat.

        Returns:
            ``(tools, tool_choice)`` for every main-LLM call this turn.
        """
        if pd.inject and pd.tool_result is not None and pd.tool_call is not None:
            r = pd.tool_result
            messages.append(Message(role="assistant", content=[ToolUseBlock(
                tool_use_id=PREDISPATCH_ID, tool_name=pd.tool,
                input=dict(pd.tool_call.input_params or {}))]))
            messages.append(Message(role="user", content=[ToolResultBlock(
                tool_use_id=PREDISPATCH_ID, content=AgentCore._predispatch_content(r),
                is_error=not r.success)]))
        if not (pd.remove_tool and active_tools):
            return active_tools, "auto"
        remaining = [t for t in active_tools if t.get("name") != pd.tool]
        if remaining:
            return remaining, "auto"
        caps = getattr(self._llm, "capabilities", None)
        if getattr(caps, "supports_force_tool_choice", False) is True:
            return active_tools, "none"
        return active_tools, "auto"

    def _post_applied_target(self, tool_results) -> bool:
        """apply_job succeeded and the workflow has a post_applied phase (domain-agnostic no-op otherwise)."""
        return "post_applied" in self._workflow.subagents and any(
            getattr(tr, "tool_name", None) == "apply_job" and getattr(tr, "success", False)
            for tr in tool_results or [])

    @staticmethod
    def _log_post_applied(session_id: str) -> None:
        logger.info("orchestrator.post_applied_transition", extra={
            "operation": "orchestrator.post_tool_hook", "status": "success",
            "session_id": session_id, "trigger_tool": "apply_job"})

    def _post_applied_hook_sync(self, session_id: str, user_id: str, bundle, tool_results) -> None:
        """Move the session to post_applied for the NEXT turn after a successful apply_job."""
        if self._post_applied_target(tool_results):
            self._write_memory_sync(session_id, user_id, "session", "current_subagent_id", "post_applied")
            bundle.session["current_subagent_id"] = "post_applied"
            self._log_post_applied(session_id)

    async def _post_applied_hook_async(self, session_id: str, user_id: str, bundle, tool_results) -> None:
        """Stream twin of :meth:`_post_applied_hook_sync`."""
        if self._post_applied_target(tool_results):
            await self._write_memory_async(session_id, user_id, "session", "current_subagent_id", "post_applied")
            bundle.session["current_subagent_id"] = "post_applied"
            self._log_post_applied(session_id)

    def _settle_predispatch_sync(self, session_id: str, user_id: str, bundle, tool_cache,
                                 prior: list, pd_exchanges: list, max_items: int) -> None:
        """Early-exit persistence for a sync turn that ran a pre-dispatch.

        The normal end of turn merges the exchange with the model's rounds; an
        exit before that (no messages, LLM error) still records what ran.
        """
        if not pd_exchanges:
            return
        capped = self._merge_tool_exchanges(prior, pd_exchanges, max_items)
        if capped is not None:
            bundle.session["recent_tool_exchanges"] = capped
            self._write_memory_sync(session_id, user_id, "session", "recent_tool_exchanges", capped)
        served = self._served_tool_results_update(bundle, tool_cache)
        if served is not None:
            self._write_memory_sync(session_id, user_id, "session", SERVED_TOOL_RESULTS_KEY, served)

    async def _settle_predispatch_stream(self, session_id: str, user_id: str, bundle, tool_cache,
                                         prior: list, pd_exchanges: list, max_items: int) -> None:
        """Stream twin of :meth:`_settle_predispatch_sync`, for an early exit that still completes.

        Exits that do not complete (abort, error) need nothing here: the
        exchange is already on ``record.captured_exchanges``, which the
        interrupted-turn persist writes.
        """
        if not pd_exchanges:
            return
        capped = self._merge_tool_exchanges(prior, pd_exchanges, max_items)
        if capped is not None:
            bundle.session["recent_tool_exchanges"] = capped
            await self._write_memory_async(session_id, user_id, "session", "recent_tool_exchanges", capped)
        served = self._served_tool_results_update(bundle, tool_cache)
        if served is not None:
            await self._write_memory_async(session_id, user_id, "session", SERVED_TOOL_RESULTS_KEY, served)

    @staticmethod
    def _record_predispatch(pd: PredispatchResult, operation: str, session_id: str) -> None:
        """Count and log a pre-dispatch outcome: tool, outcome, ms and arg keys only."""
        if pd.outcome is None:
            return
        record_predispatch(pd.tool, pd.outcome)
        status = ("success" if pd.outcome in ("fired", "cache_hit")
                  else "failure" if pd.outcome in ("failed", "timeout", "error") else "skipped")
        logger.info("orchestrator.predispatch", extra={
            "operation": operation, "status": status, "session_id": session_id,
            "tool": pd.tool, "outcome": pd.outcome, "ms": pd.ms,
            "arg_keys": sorted((pd.tool_call.input_params or {}) if pd.tool_call else {})})

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

    def _build_helper_provider(
        self,
        *,
        block: dict,
        primary_model: str,
        primary_provider: str,
    ) -> ChatProviderBase:
        """Build (or reuse) a ChatProviderBase for a preprocessing helper.

        Each helper (language_normalisation, nlu_processor) may declare its
        own ``provider`` and ``model`` independently of ``agent.*``. When
        either differs from the primary, a dedicated ChatProviderBase is
        constructed with those values; when both match, the main provider
        (``self._llm``) is reused.

        ProviderConfigError is intentionally not caught — a misconfigured
        helper override fails loud at startup per spec §6 Layer 2.
        """
        helper_provider = block.get("provider") or primary_provider
        helper_model = block.get("model") or ""
        if not helper_model:
            return self._llm
        if helper_provider == primary_provider and helper_model == primary_model:
            return self._llm
        return build_chat_provider({
            **self._config.get("agent", {}),
            "provider": helper_provider,
            "primary_model": helper_model,
        })

    def _build_dialogue_act_provider(self) -> ChatProviderBase:
        """Dedicated NLU provider: own timeout, no retry after a timeout, no SDK retries (spec §9.1).

        Always a new instance, even when the model matches the main agent's, so
        the main LLM's timeout/retry policy is unaffected.

        Returns:
            A ChatProviderBase for the dialogue-act NLU call.
        """
        agent_cfg = dict(self._config.get("agent", {}) or {})
        nlu = (self._config.get("preprocessing", {}) or {}).get("nlu_processor", {}) or {}
        return build_chat_provider({
            **agent_cfg,
            "provider": nlu.get("provider") or agent_cfg.get("provider", "anthropic"),
            "primary_model": nlu.get("model") or agent_cfg.get("primary_model", ""),
            "timeout_ms": self._dialogue_cfg.timeout_ms,
            "retry_attempts": self._dialogue_cfg.retry_attempts,
            "sdk_max_retries": 0,
            "retry_on_timeout": False,
        })

    def _turn_context(self, bundle, subagent_id: str, segments: list[str], tool_cache) -> TurnContext:
        """Build the understanding inputs from the bundle (both paths).

        Args:
            bundle: The turn's ContextBundle (after bootstrap).
            subagent_id: Current subagent, before routing.
            segments: Utterances this turn answers.
            tool_cache: This turn's TurnToolCache.

        Returns:
            TurnContext.
        """
        session = dict(bundle.session or {})
        served = session.get(SERVED_TOOL_RESULTS_KEY)
        us = session.get("user_state")
        prev_us = (us.get("id") if isinstance(us, dict) else None) or self._user_state_default or None
        return TurnContext(subagent_id=subagent_id, state=self._routing_state(bundle), session=session,
                           segments=[s for s in segments if s], recent=list(session.get(RECENT_TURNS_KEY) or []),
                           tool_cache=tool_cache, served=dict(served) if isinstance(served, dict) else {},
                           previous_user_state=prev_us)

    def _served_tool_results_update(self, bundle, tool_cache: TurnToolCache) -> dict | None:
        """Merge this turn's last-served tool entries into the session map (spec §5.2).


        Args:
            bundle: The turn's ContextBundle; ``bundle.session`` is updated
                when the map changes.
            tool_cache: This turn's TurnToolCache.

        Returns:
            The merged map to persist, or None when unchanged (nothing to write).
        """
        stored = bundle.session.get(SERVED_TOOL_RESULTS_KEY)
        merged = dict(stored) if isinstance(stored, dict) else {}
        merged.update(tool_cache.served())
        if merged == stored or (stored is None and not merged):
            return None
        bundle.session[SERVED_TOOL_RESULTS_KEY] = merged
        return merged

    async def _apply_understanding_async(self, session_id: str, user_id: str, bundle,
                                         understanding, raw_text: str) -> None:
        """Apply an understanding's writes and signals (stream path).

        Mirrors each write into the bundle so routing and the prompt see the
        values on the same turn.

        Args:
            session_id: Session id.
            user_id: User id.
            bundle: The turn's ContextBundle (mutated).
            understanding: This turn's TurnUnderstanding.
            raw_text: Caller text, for the Signal node payload.
        """
        for w in understanding.writes:
            await self._async_memory.write(session_id, user_id, w.scope, w.key, w.value)
            bundle.session[w.key] = w.value
            if w.scope == "persistent":
                bundle.profile[w.key] = w.value
        turn = str(int(bundle.session.get("turn_count", 0) or 0))
        for name in understanding.signals:
            try:
                await self._async_memory.write(session_id, user_id, "signal", "signal", {
                    "type": self._dialogue_cfg.signal_types.get(name, name), "turn": turn,
                    "raw": raw_text, "journey_id": session_id})
            except Exception as e:  # noqa: BLE001 — a signal never breaks the turn
                logger.warning("orchestrator.signal_write_failed", extra={
                    "operation": "orchestrator.apply_understanding", "status": "failure",
                    "session_id": session_id, "error": type(e).__name__})

    def _apply_understanding_sync(self, session_id: str, user_id: str, bundle,
                                  understanding, raw_text: str) -> None:
        """Apply an understanding's writes and signals (sync path); see the async twin.

        Args:
            session_id: Session id.
            user_id: User id.
            bundle: The turn's ContextBundle (mutated).
            understanding: This turn's TurnUnderstanding.
            raw_text: Caller text, for the Signal node payload.
        """
        for w in understanding.writes:
            self._write_memory_sync(session_id, user_id, w.scope, w.key, w.value)
            bundle.session[w.key] = w.value
            if w.scope == "persistent":
                bundle.profile[w.key] = w.value
        turn = str(int(bundle.session.get("turn_count", 0) or 0))
        for name in understanding.signals:
            try:
                self._write_memory_sync(session_id, user_id, "signal", "signal", {
                    "type": self._dialogue_cfg.signal_types.get(name, name), "turn": turn,
                    "raw": raw_text, "journey_id": session_id})
            except Exception as e:  # noqa: BLE001 — a signal never breaks the turn
                logger.warning("orchestrator.signal_write_failed", extra={
                    "operation": "orchestrator.apply_understanding", "status": "failure",
                    "session_id": session_id, "error": type(e).__name__})

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
        if getattr(turn_input, "caller_agent_id", None):
            _span.set_attribute("peer.agent_id", turn_input.caller_agent_id)
            _span.set_attribute("peer.protocol", "mcp")
            _span.set_attribute("peer.direction", "inbound")
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
        bundle = self._memory.context_bundle(
            session_id,
            user_id,
            adopt=not turn_input.fresh,
            caller_agent_id=getattr(turn_input, "caller_agent_id", None),
        )
        logger.info(
            "  [STEP 1] Memory context_bundle  ✓  current_subagent_id=%s"
            "  is_returning=%s  latency=%dms",
            bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id,
            bundle.session.get("is_returning", False),
            int((time.time() - t1) * 1000),
        )
        # Session bootstrap: on the session's first turn, before anything reads
        # bundle.session (routing, consent gate, opening phrase, pre-NLU args).
        # Logged after STEP 1 so memory-read latency excludes it ([STEP 1b]).
        self._run_session_bootstrap_sync(bundle, session_id, user_id)
        # Built before NLU so the frame can read stored results (spec §8);
        # reused at prompt assembly and in the tool loop.
        tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)
        current_subagent_id: str = (
            bundle.session.get("current_subagent_id")
            or self._workflow.start_subagent_id
        )

        # ── Step 4: Language Normalisation ───────────────────────────
        # Runs before the consent gate so the detected language is available
        # to translate the consent prompt on Turn 1.
        # When preprocessing.language_normalisation.enabled=false the LLM call
        # is skipped; the main LLM mirrors the user's language via a directive
        # injected by build_system_prompt() (#313).
        _ln_cfg = (
            self._config.get("preprocessing", {})
            .get("language_normalisation", {})
        )
        _ln_enabled = bool(_ln_cfg.get("enabled", True))
        t4 = time.time()
        if _ln_enabled:
            logger.info(
                "  [STEP 4] Language Normalisation  →  LLM call (model=%s)",
                self._lang_chat_provider.get_active_model(),
            )
            normalised_input, turn_language = self._language_normaliser.normalise(
                raw_input=turn_input.user_message,
                config=self._config,
            )
        else:
            logger.info("  [STEP 4] Language Normalisation  →  skipped (enabled=false)")
            normalised_input = turn_input.user_message
            turn_language = ""

        # Determine language preference — lock it in if not already set
        profile_data = bundle.profile if bundle.profile is not None else {}
        session_data = bundle.session if bundle.session is not None else {}

        default_language = _ln_cfg.get("default_language", "hindi")
        language_preference = (
            profile_data.get("language_preference") or
            session_data.get("language_preference") or
            turn_language or
            (default_language if _ln_enabled else "")
        )

        # Lock in language_preference on the first turn only.
        # Skip when LN is disabled and we have no real language signal yet —
        # the main LLM will infer it from the first turn via the mirror directive.
        # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
        saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
        if not saved_preference and language_preference:
            pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
            self._write_memory_sync(session_id, user_id, pref_scope, "language_preference", language_preference)
            bundle.session["language_preference"] = language_preference
            # Mirror persistent writes into bundle.profile so the same-turn
            # prompt assembly reads the fresh value directly, without relying
            # on a stale-prone session overlay. See _build_profile_context.
            if pref_scope == "persistent":
                bundle.profile["language_preference"] = language_preference

        logger.info(
            "  [STEP 4] Language Normalisation  ✓  detected=%s  preference=%s  normalised=%r  latency=%dms",
            turn_language or "—",
            language_preference or "—",
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
            caller_agent_id = getattr(turn_input, "caller_agent_id", None)

            if caller_agent_id:
                # Bypass interactive consent prompt for agent callers.
                # Auto-grant consent if pre-granted flag exists in metadata, otherwise default to anonymous.
                if user_storage_mode is None:
                    meta = getattr(turn_input, "metadata", {}) or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    consent_granted = meta.get("consent_granted", False)
                    user_storage_mode = "saved" if consent_granted else "anonymous"
                    self._write_memory_sync(session_id, user_id, "session", "user_storage_mode", user_storage_mode)
                    bundle.session["user_storage_mode"] = user_storage_mode
                    logger.info(
                        "orchestrator.consent_gate_bypass",
                        extra={
                            "operation": "orchestrator.consent_gate",
                            "status": "bypassed_for_agent",
                            "session_id": session_id,
                            "caller_agent_id": caller_agent_id,
                            "consent_granted": consent_granted,
                            "user_storage_mode": user_storage_mode,
                        },
                    )
            elif user_storage_mode is None and turn_count == 0:
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
                # Sync path has no SSE queue; voice always uses the streaming path.
                # Consent event emission is intentionally skipped here.
                logger.info(
                    "orchestrator.consent_event",
                    extra={
                        "operation": "orchestrator.consent_gate",
                        "status": "skipped",
                        "session_id": session_id,
                        "emission": "skipped_sync_path",
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

        # ── Step 5: NLU (dialogue-act understanding) ──────────────────
        logger.info(
            "  [STEP 5] NLU  →  understanding  current_subagent_id=%s",
            current_subagent_id,
        )
        t5 = time.time()
        # Raw caller text, not normalised_input, for parity with stream (spec §8).
        understanding = self._understander.understand(
            self._turn_context(bundle, current_subagent_id, [turn_input.user_message], tool_cache))
        nlu_result = understanding.nlu_result
        logger.info(
            "  [STEP 5] NLU  ✓  intent=%s  confidence=%.2f  entities=%s"
            "  latency=%dms",
            nlu_result.intent, nlu_result.confidence,
            list((nlu_result.entities or {}).keys()),  # keys only: values may be PII
            int((time.time() - t5) * 1000),
        )

        previous_user_state_payload: dict | None = None
        previous_user_state_id: str | None = None
        if self._user_state_enabled:
            maybe = bundle.session.get("user_state")
            if isinstance(maybe, dict):
                previous_user_state_payload = maybe
                previous_user_state_id = maybe.get("id")
            if previous_user_state_id is None:
                previous_user_state_id = self._user_state_default

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
        
        # After NLU: apply the understanding's writes synchronously so routing in
        # Step 6 sees current-turn values, not only last-turn state.
        # DPDP compliance is handled by Memory Layer at flush_session(): all entities
        # are written to Neo4j during the session; if user_storage_mode == "anonymous"
        # the Memory Layer DETACH DELETEs the user graph when the session ends.
        # entity_map is read again at prompt assembly (_build_profile_context).
        entity_map: dict = self._config.get("entity_to_profile_field", {})
        self._apply_understanding_sync(session_id, user_id, bundle, understanding,
                                       turn_input.user_message)

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
                # Profile write already happened via the understanding's
                # writes above (enum-normalised slot); mirror into bundle.session and flip the
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

        # ── Human handoff — a fixed line, no LLM (identity/handoff §4) ──
        if nlu_result.intent == "human_request":
            handoff_line = self._handle_human_request_sync(
                session_id, user_id, bundle, turn_input, turn_id=turn_id)
            if handoff_line is not None:
                self._write_memory_sync(session_id, user_id, "session", "current_subagent_id", "handoff")
                bundle.session["current_subagent_id"] = "handoff"
                latency_ms = int((time.time() - start) * 1000)
                turn_event = TurnEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=handoff_line,
                    tool_calls=[],
                    trust_input_result=trust_input,
                    trust_output_result=TrustCheckResult(passed=True, action="allow"),
                    model_used="",
                    intent=nlu_result.intent,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    timestamp_ms=int(time.time() * 1000),
                    trace_id=self._current_trace_id(),
                )
                threading.Thread(
                    target=self._post_turn,
                    args=(session_id, user_id, turn_id, handoff_line, turn_input.user_message,
                          turn_event, False, ""),
                    daemon=True,
                ).start()
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=handoff_line,
                    was_escalated=False,
                    latency_ms=latency_ms,
                )

        # ── Step 6: Routing — determine next_subagent_id ─────────────
        logger.info(
            "  [STEP 6] Routing  →  intent=%s  current_subagent=%s",
            nlu_result.intent, current_subagent_id,
        )
        # Merge profile into session for routing evaluations
        routing_state = self._routing_state(bundle)

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
        # Don't persist routing into a terminal subagent. The next session
        # adopts the last persisted current_subagent_id; if that's "ended",
        # callbacks immediately re-route to goodbye instead of resuming the
        # last meaningful stage.
        if not self._workflow.subagents[next_subagent_id].is_terminal:
            self._write_memory_sync(session_id, user_id, "session", "current_subagent_id", next_subagent_id)

        logger.info(
            "  [STEP 6] Routing  ✓  next_subagent_id=%s  entry_count=%d",
            next_subagent_id,
            subagent_entry_count[next_subagent_id],
        )

        # ── Step 6c: pre-dispatch (Spec E §5) ────────────────────────
        # A determined tool call runs now, after routing and before the
        # prompt, so its result is in the turn cache when <known_facts> and
        # <state> render. Per-TURN tool-call counts start here and are handed
        # to run_turn, so the pre-dispatch counts toward the caps.
        _turn_tool_counts: dict[str, int] = {}
        _offered = self._offered_tools(next_subagent_id)
        _pd = self._predispatch_sync(bundle, next_subagent_id, nlu_result.intent, tool_cache,
                                     session_id, user_id, _turn_tool_counts,
                                     pending_id=getattr(understanding, "pending_id", None))
        self._record_predispatch(_pd, "orchestrator.process_turn", session_id)
        # Recorded now, before any early exit: what ran stays recorded.
        _pd_results, _pd_exchanges, _pd_calls = self._predispatch_ledger(_pd, turn_id)
        self._post_applied_hook_sync(session_id, user_id, bundle, _pd_results)

        # ── Step 7: Prompt assembly via ManagerAgent ──────────────────
        next_subagent: SubAgent = self._workflow.subagents[next_subagent_id]
        logger.info(
            "  [STEP 7] Prompt Assembly  →  subagent=%s (%s)",
            next_subagent.id, next_subagent.name,
        )
        profile_context = self._build_profile_context(bundle, entity_map)
        state_text = self._render_state(bundle, next_subagent_id, tool_cache, profile_context)
        recent_text = render_recent(bundle.session.get(RECENT_TURNS_KEY), self._agent_history_turns)
        _guarded, _guard_counts, _guard_lang = self._make_output_guard(channel_config, profile_context, bundle)

        # Ensure the prompt builder uses the most up-to-date language preference
        # (which might have been updated by NLU in Step 5).
        final_language = profile_context.get("language_preference", detected_language)

        # Check for resumption signal from Memory Layer
        is_resumption = bundle.session.get("was_adopted", False)

        system = self._manager_agent.build_system_prompt(
            agent_system_prompt=self._workflow.agent_system_prompt,
            subagent_system_prompt=next_subagent.system_prompt,
            detected_language=final_language,
            channel=turn_input.channel,
            channel_config=channel_config,
            is_resumption=is_resumption,
            user_state_guidance=user_state_guidance_text,
            session_end_eval_prompt=(
                self._session_end_eval_prompt if self._session_end_eval_enabled else None
            ),
            known_facts=tool_cache.render_known_facts(),
            caller_turn=render_caller_turn(understanding),
            state=state_text,
            recent=recent_text,
        )

        # Clear resumption flag in session so it only affects the first turn
        if is_resumption:
            bundle.session["was_adopted"] = False
            self._write_memory_sync(session_id, user_id, "session", "was_adopted", False)
        messages = self._manager_agent.build_messages(
            user_message=turn_input.user_message,
        )

        # #193: replay the previous turn's tool exchanges, exactly as
        # stream_turn does. Without this the sync path starts every turn
        # blind to results it has already fetched.
        _prior_exchanges, _max_items, _max_chars = self._prepend_tool_replay(
            messages, bundle, session_id, "orchestrator.process_turn",
            skip_tools=tool_cache.fresh_tools(),
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
            self._settle_predispatch_sync(session_id, user_id, bundle, tool_cache,
                                          _prior_exchanges, _pd_exchanges, _max_items)
            return self._build_result(
                session_id=session_id,
                user_id=user_id,
                response_text="",
                was_escalated=False,
                was_tool_used=bool(_pd_calls),
                model_used="",
                latency_ms=int((time.time() - start) * 1000),
                turn_input=turn_input,
                turn_id=turn_id,
                intent=nlu_result.intent,
                tool_calls=list(_pd_calls),
                trust_input=trust_input,
                trust_output=TrustCheckResult(passed=True, action="allow"),
                trace_id=_trace_id,
            )

        # ── Step 8: LLM call #1 with scoped tools ────────────────────
        active_tools, _tool_choice = self._inject_predispatch(_pd, messages, _offered)
        output_format = next_subagent.output_format
        primary_model = self._llm.get_active_model()
        primary_provider = self._config.get("agent", {}).get("provider", "anthropic")
        logger.info(
            "  [STEP 8] LLM Call #1  →  provider=%s  model=%s"
            "  tools_available=%d  message_count=%d  output_format=%s",
            primary_provider, primary_model, len(active_tools), len(messages),
            "structured" if output_format else "free-form",
        )
        t8 = time.time()
        neutral_of = (
            OutputFormat(schema=output_format.get("schema", output_format))
            if output_format else None
        )
        request = ChatRequest(
            messages=messages,
            system=system,
            tools=_legacy_tools_to_neutral(active_tools),
            tool_choice=_tool_choice,
            output_format=neutral_of,
        )
        llm_response = self._llm.call(request)
        logger.info(
            "  [STEP 8] LLM Call #1  ✓  stop_reason=%s  model_used=%s"
            "  input_tokens=%d  output_tokens=%d  latency=%dms",
            llm_response.stop_reason, llm_response.model_used,
            (llm_response.usage.input_tokens or 0), (llm_response.usage.output_tokens or 0),
            int((time.time() - t8) * 1000),
        )
        if llm_response.stop_reason == "error":
            logger.error(
                "orchestrator.llm_call_error",
                extra={
                    "operation": "orchestrator.process_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error_type": llm_response.error_type,
                    "error_message": llm_response.error_message,
                },
            )
            latency_ms = int((time.time() - start) * 1000)
            self._settle_predispatch_sync(session_id, user_id, bundle, tool_cache,
                                          _prior_exchanges, _pd_exchanges, _max_items)
            return self._build_result(
                session_id=session_id,
                user_id=user_id,
                response_text="",
                was_escalated=False,
                was_tool_used=bool(_pd_calls),
                model_used=llm_response.model_used,
                latency_ms=latency_ms,
                turn_input=turn_input,
                turn_id=turn_id,
                intent=nlu_result.intent,
                tool_calls=list(_pd_calls),
                trust_input=trust_input,
                trust_output=TrustCheckResult(passed=True, action="allow"),
                trace_id=_trace_id,
                error_type=llm_response.error_type,
                error_message=llm_response.error_message,
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
            "confidence": nlu_result.confidence,
            "normalised_input": normalised_input,
            "detected_language": detected_language,
        }
        t9 = time.time()
        # A write can complete before a later step of the turn raises (e.g.
        # the follow-up LLM call). Its invalidation must still reach Memory
        # Layer, or the next turn serves the pre-write result. The finally
        # sends whatever is pending; the original exception propagates.
        try:
            final_text, tool_calls, tool_results = self._manager_agent.run_turn(
                result_shaper=self._result_shaper.shape,
                messages=messages,
                session_id=session_id,
                initial_response=llm_response,
                system=system,
                active_tools=active_tools,
                tool_choice=_tool_choice,
                ke_context=ke_context,
                # Without this the sync /process_turn path drops the caller's
                # identity, so connectors that template {user_id} into a path or
                # body (get_profile, update_profile) silently receive an empty
                # string. stream_turn already forwarded it; this brings the two
                # paths in line.
                user_id=user_id,
                # Same reasoning, for connector params declared ``source: session``:
                # the framework supplies what it already knows rather than asking
                # the model to reproduce it.
                session_values=self._tool_session_values(bundle),
                turn_tool_counts=_turn_tool_counts,
                pending_id=getattr(understanding, "pending_id", None),
                session_grounded=self._session_grounded_values(
                    bundle,
                    {
                        _p: None
                        for _spec in (getattr(self._manager_agent, "_grounded_params", {}) or {}).values()
                        for _p in (_spec or {})
                    },
                ),
                tool_cache=tool_cache,
                remember_name=self._remember.name if self._remember else "",
                remember_handler=(
                    (lambda _tc, _msgs: self._remember.handle(
                        _tc, _msgs, tool_cache.stored_results_by_tool(),
                        lambda scope, key, value: self._memory.write_strict(session_id, user_id, scope, key, value),
                        self._remember_on_saved(
                            bundle, getattr(self._manager_agent, "_session_values", None)),
                    )) if self._remember else None
                ),
            )
        finally:
            self._persist_tool_cache_sync(session_id, user_id, tool_cache)
        _served = self._served_tool_results_update(bundle, tool_cache)
        if _served is not None:
            self._write_memory_sync(session_id, user_id, "session", SERVED_TOOL_RESULTS_KEY, _served)

        # Persist anything a connector's session_mapping lifted out of a
        # response. The sync path runs its tools inside Manager Agent, which
        # holds no Memory Layer client, so the write lands here — the same
        # split the streaming path makes for its own tool loop. Without this
        # the mechanism silently does nothing on /process_turn, which is the
        # path the bridge actually uses. (A pre-dispatch's values were
        # written when it ran, so only run_turn's results are written here.)
        self._write_tool_session_values_sync(session_id, user_id, tool_results, bundle)

        # #193: persist this turn's tool exchanges so the next turn can
        # replay them. run_turn returns ToolResult objects; _capture_tool_exchange
        # expects the tool_result content dicts that go into messages, so
        # convert here rather than widening the helper.
        _captured = self._capture_tool_exchange(
            tool_calls,
            [
                {
                    "tool_use_id": tr.tool_use_id,
                    "content": tr.result_text or str(tr.result),
                }
                for tr in (tool_results or [])
            ],
            _max_chars,
        )
        _capped = self._merge_tool_exchanges(
            _prior_exchanges, _pd_exchanges + ([_captured] if _captured else []), _max_items,
        )
        # From here on the pre-dispatch counts as a call the turn made
        # (was_tool_used, audit), as if the model had called it. Its post-tool
        # hook already ran at Step 6c; the hook below sees run_turn's results.
        tool_calls = [*_pd_calls, *(tool_calls or [])]
        if _capped is not None:
            bundle.session["recent_tool_exchanges"] = _capped
            self._write_memory_sync(
                session_id, user_id, "session", "recent_tool_exchanges", _capped,
            )
            logger.info(
                "orchestrator.tool_persist",
                extra={
                    "operation": "orchestrator.process_turn",
                    "status": "success",
                    "session_id": session_id,
                    "stored": len(_capped),
                },
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
        self._post_applied_hook_sync(session_id, user_id, bundle, tool_results)

        # ── Step 10: Trust check on output ────────────────────────────
        logger.info(
            "  [STEP 10] Trust Output Check  →  POST %s/check/output  (session=%s)",
            trust_endpoint, session_id,
        )
        t10 = time.time()
        if final_text:
            final_text = _guard_per_sentence(final_text, _guarded)
        record_output_guard(turn_input.channel, _guard_lang,
                            _guard_counts["digits_rewritten"], _guard_counts["foreign_script_words"])
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
        entries = append_recent_turn(bundle.session.get(RECENT_TURNS_KEY), caller=turn_input.user_message,
                                     bot=final_text, interrupted=False,
                                     history_turns=self._recent_keep)
        self._write_memory_sync(session_id, user_id, "session", RECENT_TURNS_KEY, entries)
        bundle.session[RECENT_TURNS_KEY] = entries

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
            error_type=llm_response.error_type,
            error_message=llm_response.error_message,
        )

        # Spec E §10: main-LLM calls = the initial call + run_turn's follow-ups.
        _followups = getattr(self._manager_agent, "last_llm_calls", 0)
        _llm_calls = 1 + (_followups if isinstance(_followups, int) else 0)
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
                "llm_calls": _llm_calls,
                "predispatch_tool": _pd.tool,
                "predispatch_outcome": _pd.outcome,
                "predispatch_ms": _pd.ms,
            },
        )
        logger.info(
            "\n═══════════════════════════════════════════════════════════════\n"
            "  TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
            "  model=%s  total_latency=%dms  next_subagent=%s\n"
            "  llm_calls=%s  predispatch_tool=%s  predispatch_outcome=%s  predispatch_ms=%s\n"
            "  %s\n"
            "  response: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, nlu_result.intent, bool(tool_calls),
            llm_response.model_used, latency_ms, next_subagent_id,
            _llm_calls, _pd.tool, _pd.outcome, _pd.ms,
            self._nlu_banner(understanding),
            final_text[:200],
        )

        return result

    # ------------------------------------------------------------------
    # Private: routing algorithm
    # ------------------------------------------------------------------

    @staticmethod
    def _nlu_banner(understanding) -> str:
        """`acts`/`relation`/`pending`/`resolved` for the turn banner.

        These decide the derived intent, and therefore the routing, but they are
        logged only inside ``extra={}`` (dropped by the default formatter) and as
        OTel counters. With neither visible, a turn that routes on a resolver
        miss is indistinguishable from one the model simply got wrong — which
        cost three successive wrong diagnoses of the same bug.
        """
        d = getattr(understanding, "dialogue", None)
        acts = ",".join(getattr(d, "acts", ()) or ()) or "-"
        return (f"acts={acts}  relation={getattr(d, 'relation', None) or '-'}  "
                f"pending={getattr(understanding, 'pending_id', None) or '-'}  "
                f"resolved={getattr(understanding, 'resolved', None) is not None}")

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

    async def _write_mapped_session_values(
        self, session_id: str, user_id: str, tool_result, bundle,
    ) -> None:
        """Persist values a connector's ``session_mapping`` lifted from a response.

        Routing reads session ∪ profile, and a tool response otherwise reaches
        only the LLM — so this is the only path by which a workflow can gate on
        something a tool returned. The same gap produced three separate bugs
        before it was closed generically: ``consent_response``,
        ``profile_setup_done``, and every flag on the participant fetch.

        Tools run AFTER routing, so these land in time for the NEXT turn. That
        lag is inherent to the turn shape rather than a defect: a workflow
        needing a fetched fact must fetch on one turn and branch on the next.

        Args:
            session_id: Session receiving the write.
            user_id: Owning user, for the Memory Layer write.
            tool_result: Result whose ``session_values`` to persist.
            bundle: Live context bundle, updated so the same turn sees them.
        """
        values = getattr(tool_result, "session_values", None) or {}
        if not values:
            return
        for key, val in values.items():
            await self._async_memory.write(session_id, user_id, "session", key, val)
            bundle.session[key] = val
        logger.info(
            "orchestrator.mapped_session_values",
            extra={
                "operation": "orchestrator.stream_turn",
                "status": "success",
                "session_id": session_id,
                "tool_name": getattr(tool_result, "tool_name", ""),
                "fields": sorted(values),
            },
        )

    def _run_session_bootstrap_sync(self, bundle, session_id: str, user_id: str) -> None:
        """Run the session bootstrap on the sync path when this session needs it.

        Step args are literal config values, not LLM output, so the grounding
        guard does not apply. Never raises into the turn.

        Args:
            bundle:     This turn's context bundle; mutated in place.
            session_id: Session identifier.
            user_id:    User identifier.
        """
        if self._bootstrap is None or not self._bootstrap.needed(bundle):
            return
        try:
            gateway = getattr(self._manager_agent, "_gateway", None)
            if gateway is None:
                logger.debug("orchestrator.session_bootstrap_skipped", extra={
                    "operation": "orchestrator.process_turn", "status": "skipped",
                    "reason": "no_gateway"})
                return
            self._bootstrap.run_sync(
                bundle,
                execute=lambda tc: gateway.execute(
                    tc, session_id, user_id, session_values=self._tool_session_values(bundle)),
                check_consent=lambda tool: self._trust.check_consent(session_id, tool),
                write_session=lambda k, v: self._write_memory_sync(session_id, user_id, "session", k, v),
                apply_tool_results=lambda batch: self._memory.apply_tool_results(session_id, user_id, batch),
            )
        except Exception as e:  # never break a turn
            logger.error("orchestrator.session_bootstrap_error", extra={
                "operation": "orchestrator.process_turn", "status": "failure",
                "session_id": session_id, "error": type(e).__name__})

    async def _run_session_bootstrap_async(self, bundle, session_id: str, user_id: str) -> None:
        """Run the session bootstrap on the stream path when this session needs it.

        Step args are literal config values, not LLM output, so the grounding
        guard does not apply. Never raises into the turn.

        Args:
            bundle:     This turn's context bundle; mutated in place.
            session_id: Session identifier.
            user_id:    User identifier.
        """
        if self._bootstrap is None or not self._bootstrap.needed(bundle):
            return
        if not self._async_gateway:
            logger.debug("orchestrator.session_bootstrap_skipped", extra={
                "operation": "orchestrator.stream_turn", "status": "skipped",
                "reason": "no_gateway"})
            return
        try:
            async def _exec(tc):
                return await self._async_gateway.execute(
                    tc, session_id, user_id, session_values=self._tool_session_values(bundle))

            async def _consent(tool):
                return await self._async_trust.check_consent(session_id, tool) if self._async_trust else False

            async def _write(k, v):
                await self._async_memory.write(session_id, user_id, "session", k, v)

            async def _apply(batch):
                await self._async_memory.apply_tool_results(session_id, user_id, batch)

            await self._bootstrap.run_async(
                bundle, execute=_exec, check_consent=_consent, write_session=_write,
                apply_tool_results=_apply)
        except Exception as e:  # never break a turn
            logger.error("orchestrator.session_bootstrap_error", extra={
                "operation": "orchestrator.stream_turn", "status": "failure",
                "session_id": session_id, "error": type(e).__name__})

    @staticmethod
    def _tool_session_values(bundle) -> dict:
        """Build the state lookup handed to the Action Gateway for a tool call.

        Connector params declared ``source: session`` resolve from this instead
        of from the LLM. Profile is layered over session for the same key,
        matching how ``routing_state`` is built: the profile holds values the
        caller actually gave, while the session carries seeded defaults under
        the same names.

        That ordering is the whole point. ``age`` is seeded into session as the
        integer ``0`` and the caller's real age lands in the profile. Reading
        session first would send ``0``, which the participant API rejects as
        ``U18_NOT_ALLOWED`` — making the agent tell an adult they are a minor.

        NLU-owned values (slot_provenance) win; see NLU dialogue-acts spec §6.5.

        Args:
            bundle: The turn's ContextBundle.

        Returns:
            Flat dict of state values; empty when the bundle carries none.
        """
        values: dict = {}
        for src in (getattr(bundle, "session", None), getattr(bundle, "profile", None)):
            if not isinstance(src, dict):
                continue
            for key, val in src.items():
                if key == "attributes":
                    continue
                # A seeded default is not an answer. Session state pre-seeds
                # every profile field — strings as "" and the one integer
                # field, age, as 0 — so a falsy value here means "the caller
                # never told us", not "the caller said zero". Letting 0
                # through would send it as a real age and be rejected as
                # under-18. Same reasoning as src.context.state.is_collected,
                # which keeps a seeded 0 out of <state>'s "collected" line.
                if val in (None, "", [], 0, "0"):
                    continue
                values[key] = val
        values.update(nlu_owned_values(getattr(bundle, "session", None)))
        return values

    @staticmethod
    def _routing_state(bundle) -> dict:
        """Session, then profile, then NLU-owned session values (NLU dialogue-acts spec §6.5).

        Identical to the previous inline merge when no SlotWriter provenance
        exists.

        Args:
            bundle: The turn's ContextBundle.

        Returns:
            Merged state for routing and pending resolution.
        """
        state = dict(bundle.session or {})
        if bundle.profile:
            state.update(bundle.profile)
        state.update(nlu_owned_values(bundle.session))
        return state

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
        return evaluate_condition(condition, session)

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
        undelivered_note: str = "",
        skip_tools: frozenset | set = frozenset(),
    ) -> list[Message]:
        """Convert persisted tool exchange records into neutral Message objects.

        Each exchange is rendered as one ``assistant`` Message containing
        all of its ``ToolUseBlock`` blocks followed by one ``user`` Message
        containing the matching ``ToolResultBlock`` blocks, exactly as the
        tool-use protocol requires.

        Args:
            exchanges: Ordered list of exchange dicts as persisted by
                ``_capture_tool_exchange``. Malformed entries are skipped.
            undelivered_note: Appended on its own line to every tool result of
                an exchange marked ``delivered: false``; empty disables.
            skip_tools: Tool names whose uses and results are dropped pairwise
                (a fresh stored result supersedes the replayed one). An
                exchange left with no uses or no results is dropped entirely.

        Returns:
            Flat list of ``Message`` objects ready to prepend to a turn's
            ``messages`` list.
        """
        out: list[Message] = []
        if not exchanges:
            return out
        for ex in exchanges:
            if not isinstance(ex, dict):
                continue
            uses = ex.get("tool_uses") or []
            results = ex.get("tool_results") or []
            if skip_tools:
                names = {u.get("id", ""): u.get("name", "") for u in uses if isinstance(u, dict)}
                uses = [u for u in uses if not (isinstance(u, dict) and u.get("name") in skip_tools)]
                results = [
                    r for r in results
                    if not (isinstance(r, dict) and names.get(r.get("tool_use_id", "")) in skip_tools)
                ]
            if not uses or not results:
                continue
            use_blocks: list[ToolUseBlock] = []
            for u in uses:
                if not isinstance(u, dict):
                    continue
                try:
                    use_blocks.append(ToolUseBlock(
                        tool_use_id=u.get("id", ""),
                        tool_name=u.get("name", ""),
                        input=u.get("input") or {},
                    ))
                except Exception:
                    continue
            note = undelivered_note if (undelivered_note and ex.get("delivered") is False) else ""
            result_blocks: list[ToolResultBlock] = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                try:
                    result_blocks.append(ToolResultBlock(
                        tool_use_id=r.get("tool_use_id", ""),
                        content=(r.get("content", "") + ("\n" + note if note else "")),
                    ))
                except Exception:
                    continue
            if not use_blocks or not result_blocks:
                continue
            out.append(Message(role="assistant", content=use_blocks))
            out.append(Message(role="user", content=result_blocks))
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

    def _prepend_tool_replay(
        self,
        messages: list,
        bundle,
        session_id: str,
        operation: str,
        undelivered_note: str = "",
        skip_tools: frozenset | set = frozenset(),
    ) -> tuple[list[dict], int, int]:
        """Prepend the previous turn's tool exchanges to this turn's messages.

        Shared by ``process_turn`` and ``stream_turn`` so both transports give
        the LLM the same view of what it has already learned. Before this was
        shared, only the streaming path replayed exchanges, so a caller on
        ``/process_turn`` lost every tool result at the turn boundary — the
        model could not see the ids a previous ``fetch_jobs`` returned, and
        re-invoked tools it had already run.

        Args:
            messages: This turn's message list. Mutated in place.
            bundle: Context bundle whose ``session`` holds the persisted
                exchanges.
            session_id: For logging.
            operation: Caller name for the log entry.
            undelivered_note: Note appended to replayed tool results of
                exchanges marked ``delivered: false``; empty disables.
            skip_tools: Tool names to omit from the replay because a fresh
                stored result already covers them.

        Returns:
            ``(prior_exchanges, max_items, max_chars)`` for the caller to pass
            back to :meth:`_merge_tool_exchanges` at the end of the turn.
        """
        max_items, max_chars = self._recent_tool_exchanges_caps()
        raw = bundle.session.get("recent_tool_exchanges") or []
        if not isinstance(raw, list):
            raw = []
        prior: list[dict] = list(raw)
        if max_items > 0 and prior:
            replay = self._build_tool_exchange_messages(
                prior[-max_items:], undelivered_note, skip_tools,
            )
            if replay:
                messages[:0] = replay
                logger.info(
                    "orchestrator.tool_replay",
                    extra={
                        "operation": operation,
                        "status": "success",
                        "session_id": session_id,
                        "replayed_exchanges": len(replay) // 2,
                        "skipped_tools": sorted(skip_tools),
                    },
                )
        return prior, max_items, max_chars

    @staticmethod
    def _merge_tool_exchanges(
        prior: list[dict],
        captured: list[dict],
        max_items: int,
    ) -> list[dict] | None:
        """Combine prior and freshly captured exchanges, newest last.

        Args:
            prior: Exchanges replayed into this turn.
            captured: Exchanges recorded during this turn's tool rounds.
            max_items: Cap from ``agent.recent_tool_exchanges.max_items``.

        Returns:
            The capped list to persist, or ``None`` when there is nothing new.
        """
        if not captured or max_items <= 0:
            return None
        return (list(prior) + list(captured))[-max_items:]

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
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
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
        safe_error_message = (
            SAFE_MESSAGES.get(error_type, DEFAULT_SAFE_MESSAGE)
            if error_type
            else error_message
        )
        result = TurnResult(
            session_id=session_id,
            turn_id=turn_id,
            response_text=response_text,
            was_escalated=was_escalated,
            was_tool_used=was_tool_used,
            model_used=model_used,
            latency_ms=latency_ms,
            session_ended=session_ended,
            error_type=error_type,
            error_message=safe_error_message,
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
                getattr(turn_input, "caller_agent_id", None),
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
        caller_agent_id: Optional[str] = None,
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
                    "caller_agent_id": caller_agent_id,
                    "peer_protocol": "mcp" if caller_agent_id else None,
                    "peer_direction": "inbound" if caller_agent_id else None,
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
    # Private: human handoff (identity/handoff spec §4)
    # ------------------------------------------------------------------

    def _handoff_lines(self) -> dict | None:
        """The configured handoff lines when handoff is on and the workflow has a ``handoff`` phase."""
        identity = self._config.get("identity") or {}
        handoff = self._config.get("handoff") or {}
        if (identity.get("human_handoff") != "request" or not handoff.get("lines")
                or "handoff" not in self._workflow.subagents):
            return None
        return handoff["lines"]

    def _is_return_phase(self, subagent_id: str) -> bool:
        """A phase that sends the caller back via ``close_return_to`` (``handoff``, or one asking close_confirm)."""
        if subagent_id == "handoff":
            return True
        sa = self._workflow.subagents.get(subagent_id)
        return any(getattr(p, "id", "") == "close_confirm" for p in (getattr(sa, "pending", None) or []))

    def _handoff_prepare(self, session_id: str, user_id: str, bundle, turn_input) -> tuple[bool, dict, str]:
        """(already, payload, step) for a handoff turn; no payload when already delivered.

        ``step`` is the phase the caller was really in: inside a return phase
        it is ``close_return_to``, so the handoff never points back at itself.
        """
        current = bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id
        step = (bundle.session.get("close_return_to") or current) if self._is_return_phase(current) else current
        already = self._handoff_already(bundle)
        payload = {} if already else build_handoff_payload(
            ticket_hint="",
            use_case=self._config.get("observability", {}).get("domain", "unknown"),
            session={**bundle.session, "current_subagent_id": step}, phone=user_id, call_id=session_id,
            last_caller_turn=turn_input.user_message,
            summary_turns=int((self._config.get("handoff") or {}).get("summary_turns", 6)),
            now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        return already, payload, step

    def _register_bg_task(self, task: "asyncio.Task", name: str, session_id: str) -> None:
        """Hold a background task and log its outcome when it finishes.

        ``add_done_callback(self._bg_tasks.discard)`` on its own never retrieves
        the exception, so a failure surfaces only as asyncio's "Task exception
        was never retrieved" at garbage-collection time — with no operation,
        session or error attached, and nothing tying it to the turn.

        Args:
            task: The task to hold.
            name: Short operation name for the log record.
            session_id: Session the task belongs to.
        """
        self._bg_tasks.add(task)

        def _done(t: "asyncio.Task") -> None:
            self._bg_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "orchestrator.bg_task_failed",
                    extra={"operation": f"orchestrator.{name}", "status": "failure",
                           "session_id": session_id, "error": f"{type(exc).__name__}: {exc}"},
                )

        task.add_done_callback(_done)

    @staticmethod
    def _handoff_record_unheard(session_id: str, user_id: str, task: "asyncio.Task") -> None:
        """Log that a handoff completed for a turn the caller never heard.

        The session's ``delivered`` state is still correct — the handoff really
        did reach the team — so it is deliberately not rewritten here. What was
        missing is any record that the caller never heard the confirmation
        line, which is the part a later ``already`` reply silently assumes.

        Deliberately log-only: this runs from a cancelled turn's done-callback,
        where issuing another write would repeat the late-write problem it
        exists to surface.

        Args:
            session_id: Session the handoff belonged to.
            user_id: Caller id, for correlation.
            task: The completed escalate task.
        """
        if task.cancelled() or task.exception() is not None:
            return          # failure already logged by _register_bg_task
        logger.warning(
            "orchestrator.handoff_line_unheard",
            extra={"operation": _OP_HANDOFF, "status": "skipped",
                   "session_id": session_id, "user_id": user_id,
                   "reason": "turn_cancelled_before_delivery"},
        )

    @staticmethod
    def _handoff_already(bundle) -> bool:
        """True when this call already has a delivered handoff, or one still pending (< 30 s old).

        The pending age is clamped the same way carryover ages are: a marker up
        to ``_CARRYOVER_CLOCK_SKEW_MS`` in the future counts as age 0, and
        anything beyond that is malformed. Without the clamp a future
        ``handoff_pending_at`` (clock skew, or seconds written where ms were
        meant) makes the raw difference negative, negative is always below the
        window, and the call is stuck "pending" forever — the caller could
        never be handed off again. A malformed marker therefore allows a fresh
        escalate: a duplicate handoff is recoverable, a permanently blocked one
        is not. A genuinely ``delivered`` handoff still blocks duplicates above.
        """
        status = bundle.session.get("handoff_status")
        if status == "delivered":
            return True
        if status != "pending":
            return False
        try:
            pending_at = int(float(bundle.session.get("handoff_pending_at") or 0))
        except (TypeError, ValueError):
            return False
        age_ms = int(time.time() * 1000) - pending_at
        if -_CARRYOVER_CLOCK_SKEW_MS <= age_ms < 0:
            age_ms = 0
        if age_ms < 0:
            logger.warning(
                "orchestrator.handoff_pending_marker_discarded",
                extra={"operation": _OP_HANDOFF, "status": "skipped",
                       "reason": "malformed_pending_at"},
            )
            return False
        return age_ms < _HANDOFF_PENDING_WINDOW_MS

    @staticmethod
    def _handoff_pending_writes(bundle) -> dict:
        """The pending marker, written BEFORE escalate so a lost result can't allow a duplicate.

        ``handoff_line`` is ``pending`` too: a pending handoff is not delivered,
        so it never arms the delivered-only close_confirm question.
        """
        writes = {"handoff_status": "pending", "handoff_pending_at": int(time.time() * 1000),
                  "handoff_line": "pending"}
        bundle.session.update(writes)
        return writes

    def _handoff_writes(self, bundle, outcome: str, result: dict | None, *, session_id: str = "",
                        line: str = "", caller: str = "") -> dict:
        """Session fields a handoff turn records (mirrored into bundle.session); next-turn routing reads them.

        Includes the exchange in ``recent_turns`` and the spoken line as
        ``current_question``, like a normal turn, so the next turn sees it.
        """
        current = bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id
        writes: dict = {}
        # Inside a return phase close_return_to already names the real phase; keep it.
        if not self._is_return_phase(current):
            writes["close_return_to"] = current
        # The line spoken THIS turn (delivered | failed | already). handoff_status
        # stays "delivered" on an already turn; only the delivered line asks to end
        # the call, so the next turn's close_confirm pending keys on this marker.
        writes["handoff_line"] = outcome
        if outcome != "already":
            writes["handoff_status"] = outcome
            writes["handoff_ticket_id"] = str((result or {}).get("ticket_id") or "")
        raw_counts = bundle.session.get("subagent_entry_count")
        counts = dict(raw_counts) if isinstance(raw_counts, dict) else {}
        counts["handoff"] = int(counts.get("handoff", 0) or 0) + 1
        writes["subagent_entry_count"] = counts
        writes["current_question"] = self._sanitize_current_question(
            prev=bundle.session.get("current_question", "") or "", new=line, session_id=session_id)
        writes[RECENT_TURNS_KEY] = append_recent_turn(
            bundle.session.get(RECENT_TURNS_KEY), caller=caller, bot=line, interrupted=False,
            history_turns=self._dialogue_cfg.history_turns)
        bundle.session.update(writes)
        return writes

    @staticmethod
    def _handoff_escalate_failed(session_id: str, exc: Exception) -> None:
        """Log an escalate exception by type only (the message could carry the payload or URL)."""
        logger.warning("orchestrator.handoff_escalate_failed", extra={
            "operation": _OP_HANDOFF, "status": "failure",
            "session_id": session_id, "error": type(exc).__name__,
        })

    @staticmethod
    def _handoff_signal(session_id: str, turn_id: str, outcome: str, result: dict | None,
                        latency_ms: int) -> dict:
        """Log the outcome (never the payload) and return the ``handoff`` signal body."""
        if outcome == "already":
            reason = "already"
        else:
            reason = str((result or {}).get("reason") or ("error" if result is None else "unknown"))
        fields = {
            "ticket_id": str((result or {}).get("ticket_id") or ""),
            "outcome": outcome, "reason": reason, "latency_ms": latency_ms,
        }
        logger.info("orchestrator.handoff", extra={
            "operation": _OP_HANDOFF,
            "status": "failure" if outcome == "failed" else "success",
            "session_id": session_id, **fields,
        })
        return {"session_id": session_id, "turn_id": turn_id,
                "timestamp_ms": int(time.time() * 1000), **fields}

    async def _emit_handoff_signal(self, data: dict) -> None:
        """Fire-and-forget body for the stream path's ``handoff`` signal."""
        try:
            await self._async_learning.emit_signal("handoff", data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("orchestrator.handoff_signal_failed", extra={
                "operation": _OP_HANDOFF, "status": "skipped",
                "session_id": data.get("session_id"), "error": type(exc).__name__})

    def _handoff_will_escalate(self, bundle) -> bool:
        """True when a ``human_request`` this turn would call Trust (handoff on, nothing delivered or pending)."""
        return self._handoff_lines() is not None and not self._handoff_already(bundle)

    async def _handle_human_request_async(self, session_id: str, user_id: str, bundle, turn_input,
                                          *, turn_id: str = "", caller: str = "") -> str | None:
        """Escalate a ``human_request`` to Trust; return the line to speak, or None when handoff is off.

        At most one delivered handoff per call: after ``delivered`` (or while a
        handoff is pending, < 30 s) the ``already`` line is returned and nothing
        is sent; after ``failed`` a new request tries again. Any exception
        counts as failed. The pending marker is written before escalate, and
        escalate + its result writes run shielded and held in ``_bg_tasks``,
        so a cancelled turn still records the result.
        """
        lines = self._handoff_lines()
        if lines is None:
            return None
        already, payload, step = self._handoff_prepare(session_id, user_id, bundle, turn_input)
        caller = caller or turn_input.user_message
        if already:
            return await self._handoff_finish_async(session_id, user_id, bundle, lines, None, True, 0,
                                                    turn_id=turn_id, caller=caller)
        pending = self._handoff_pending_writes(bundle)
        await asyncio.gather(*(self._async_memory.write(session_id, user_id, "session", k, v)
                               for k, v in pending.items()), return_exceptions=True)
        task = asyncio.ensure_future(self._handoff_escalate_async(
            session_id, user_id, bundle, turn_input, lines, payload, step, turn_id=turn_id, caller=caller))
        self._register_bg_task(task, "handoff_escalate", session_id)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The turn was cancelled but the shielded escalate keeps running and
            # still records its result, so the session can end up marked
            # "delivered" for a line the caller never heard. Record that, so the
            # gap is visible rather than silent.
            task.add_done_callback(
                lambda t: self._handoff_record_unheard(session_id, user_id, t))
            raise

    async def _handoff_escalate_async(self, session_id: str, user_id: str, bundle, turn_input, lines: dict,
                                      payload: dict, step: str, *, turn_id: str, caller: str) -> str:
        """Escalate and record the result; runs under ``asyncio.shield`` so cancellation can't drop it."""
        t0 = time.time()
        result: dict | None
        try:
            result = await self._async_trust.escalate(
                session_id, "human_request", turn_input.user_message, step, handoff=payload)
        except Exception as exc:  # noqa: BLE001 — a handoff failure must never fail the turn
            self._handoff_escalate_failed(session_id, exc)
            result = None
        latency_ms = int((time.time() - t0) * 1000)
        return await self._handoff_finish_async(session_id, user_id, bundle, lines, result, False, latency_ms,
                                                turn_id=turn_id, caller=caller)

    async def _handoff_finish_async(self, session_id: str, user_id: str, bundle, lines: dict,
                                    result: dict | None, already: bool, latency_ms: int, *,
                                    turn_id: str, caller: str) -> str:
        """Choose the line, write the session fields and emit the ``handoff`` signal."""
        line, outcome = choose_handoff_line(
            result, lines, already, disclosure=(self._config.get("identity") or {}).get("disclosure", ""))
        writes = self._handoff_writes(bundle, outcome, result, session_id=session_id, line=line, caller=caller)
        await asyncio.gather(*(self._async_memory.write(session_id, user_id, "session", k, v)
                               for k, v in writes.items()), return_exceptions=True)
        data = self._handoff_signal(session_id, turn_id, outcome, result, latency_ms)
        if self._async_learning:
            task = asyncio.create_task(self._emit_handoff_signal(data))
            self._register_bg_task(task, "handoff_signal", session_id)
        return line

    def _handle_human_request_sync(self, session_id: str, user_id: str, bundle, turn_input,
                                   *, turn_id: str = "") -> str | None:
        """Sync twin of :meth:`_handle_human_request_async` (process_turn path)."""
        lines = self._handoff_lines()
        if lines is None:
            return None
        already, payload, step = self._handoff_prepare(session_id, user_id, bundle, turn_input)
        result: dict | None = None
        latency_ms = 0
        if not already:
            for k, v in self._handoff_pending_writes(bundle).items():
                self._write_memory_sync(session_id, user_id, "session", k, v)
            t0 = time.time()
            try:
                result = self._trust.escalate(
                    session_id, "human_request", turn_input.user_message, step, handoff=payload)
            except Exception as exc:  # noqa: BLE001 — a handoff failure must never fail the turn
                self._handoff_escalate_failed(session_id, exc)
                result = None
            latency_ms = int((time.time() - t0) * 1000)
        line, outcome = choose_handoff_line(
            result, lines, already, disclosure=(self._config.get("identity") or {}).get("disclosure", ""))
        for k, v in self._handoff_writes(bundle, outcome, result, session_id=session_id, line=line,
                                         caller=turn_input.user_message).items():
            self._write_memory_sync(session_id, user_id, "session", k, v)
        data = self._handoff_signal(session_id, turn_id, outcome, result, latency_ms)
        try:
            self._learning.emit_signal("handoff", data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("orchestrator.handoff_signal_failed", extra={
                "operation": _OP_HANDOFF, "status": "skipped",
                "session_id": session_id, "error": type(exc).__name__})
        return line

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
        message: str | None = None,
        subagent_id: str | None = None,
        end_session: bool = True,
        translate: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Skip the LLM and emit a canned closing line (#204).

        Args:
            message: the line to speak. Defaults to
                ``conversation.termination_message`` — #204's goodbye. A
                terminal subagent passes its own ``opening_phrase`` instead,
                so a phase that ends the call for a REASON states that reason.
            subagent_id: the phase to record for this turn. Defaults to
                ``ended``.
            end_session: whether this closes the call. True for a goodbye or a
                terminal phase. **False** when a mid-conversation phase simply
                speaks a fixed line — the caller still has to answer it, and
                hanging up on them would be the opposite of the intent.
            translate: whether to translate ``message`` into the caller's
                language. **False** for lines that must be spoken verbatim from
                config (the handoff lines), which the sync path also never
                translates.

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
        termination_message: str = message if message is not None else (
            self._config.get("conversation", {}).get("termination_message", "") or ""
        )

        # Route to the "ended" subagent if the workflow defines one. This
        # keeps reconnect / observability semantics consistent with the
        # full LLM path (where global_routing on termination_intent moves
        # the session into the terminal subagent).
        _want = subagent_id or "ended"
        ended_subagent_id = _want if _want in self._workflow.subagents else (
            bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id
        )
        bundle.session["current_subagent_id"] = ended_subagent_id

        # In-process bundle reflects the terminal routing for this turn's
        # observability/audit, but we don't persist a terminal subagent —
        # adopting "ended" on the next session would route callbacks straight
        # to goodbye instead of resuming the last meaningful stage.
        write_tasks: list = []
        if not self._workflow.subagents[ended_subagent_id].is_terminal:
            write_tasks.append(
                self._async_memory.write(
                    session_id, user_id, "session", "current_subagent_id", ended_subagent_id
                )
            )

        translated = (self._translate_consent_message(termination_message, detected_language)
                      if translate else termination_message)

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
            "  model=%s  total_latency=%dms  next_subagent=%s  sentences=%d\n"
            "  response: %r\n"
            "═══════════════════════════════════════════════════════════════",
            session_id, nlu_result.intent, False,
            "none", latency_ms, ended_subagent_id,
            1 if translated else 0,
            (translated or "").strip()[:200],
        )

        if translated:
            yield stamp(SentenceEvent(text=translated, sentence_index=0))

        yield stamp(DoneEvent(
            turn_id=turn_id,
            turn_status="completed",
            session_ended=end_session,
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
            sys_text = (
                f"Translate the user message to {target_language}. "
                "Return ONLY the translated text, no explanation."
            )
            request = ChatRequest(
                messages=[Message(role="user", content=[TextBlock(text=message)])],
                system=SystemPrompt(blocks=[TextBlock(text=sys_text)]),
            )
            response = self._llm.call(request)
            text_content = _text_of(response)
            if response.stop_reason != "error" and text_content:
                logger.info(
                    "orchestrator.consent_translation_success",
                    extra={
                        "operation": "orchestrator._translate_consent_message",
                        "status": "success",
                        "target_language": target_language,
                        "latency_ms": int((time.time() - t_translate) * 1000),
                    },
                )
                return text_content.strip()
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
                    "error_type": type(e).__name__,
                },
            )

    async def _write_memory_async(self, session_id: str, user_id: str, scope: str,
                                  key: str, value: Any) -> None:
        """Stream twin of :meth:`_write_memory_sync`: a failed write is logged (type only), never raised.

        Args:
            session_id: Session identifier.
            user_id:    User identifier.
            scope:      Memory scope — "session" or "persistent".
            key:        Field key to write.
            value:      Value to store.
        """
        try:
            await self._async_memory.write(session_id, user_id, scope, key, value)
        except Exception as e:  # noqa: BLE001 — a memory error must not end the turn
            logger.error("orchestrator.async_write_failed", extra={
                "operation": "orchestrator._write_memory_async", "status": "failure",
                "session_id": session_id, "key": key, "error_type": type(e).__name__})

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
        record: "TurnRecord | None" = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute one conversation turn with streaming SSE output.

        Wraps the pipeline body to keep the turn's ledger: the last stage
        reached, and whether the turn completed. A turn that ends without a
        completed DoneEvent (aborted, errored, or closed early) has its state
        persisted for the successor turn (spec §4.5) from ``finally``, via a
        background task, never awaited here.

        Args:
            turn_input: Normalised inbound message from the Reach Layer.
            abort_event: When set, the turn stops at its next safe point.
            turn_id: Identifier stamped on every event; uuid4 when empty.
            record: Per-turn ledger; a private one is used when None.

        Yields:
            SignalEvent, SentenceEvent, or DoneEvent.
        """
        record = record if record is not None else TurnRecord()
        completed = False
        try:
            async for event in self._stream_turn_impl(
                turn_input, abort_event=abort_event, turn_id=turn_id, record=record,
            ):
                if isinstance(event, SignalEvent) and event.stage:
                    record.last_stage = event.stage
                elif isinstance(event, DoneEvent) and event.turn_status == "completed":
                    completed = True
                yield event
        finally:
            if not completed:
                self._on_turn_not_completed(turn_input, record, turn_id)

    def _turn_policy(self, channel: "str | None") -> TurnPolicy:
        """Return the cached turn-lifecycle policy for ``channel``.

        Args:
            channel: Channel name from the TurnInput.

        Returns:
            The resolved TurnPolicy.
        """
        key = channel or ""
        policy = self._turn_policies.get(key)
        if policy is None:
            policy = resolve_turn_policy(self._config, channel)
            self._turn_policies[key] = policy
        return policy

    def _on_turn_not_completed(
        self, turn_input: TurnInput, record: TurnRecord, turn_id: str = "",
    ) -> None:
        """Schedule persistence of an interrupted or failed turn's state.

        Runs from ``stream_turn``'s ``finally`` so it must not await: it only
        builds the payloads and hands them to a background task, stored on
        ``record.persist_task`` so the TurnAssembler can wait for it.

        Args:
            turn_input: The turn's input (identity + utterance).
            record: The turn's ledger.
            turn_id: The caller-supplied turn id (may be empty).
        """
        if turn_input is None or not turn_input.session_id or self._async_memory is None:
            return
        user_id = turn_input.user_id or turn_input.session_id
        exchanges = [dict(ex, delivered=False) for ex in record.captured_exchanges]
        carry = None
        if record.write_carryover:
            segments = list(record.segments) if record.fold_ran else [turn_input.user_message]
            segments = [s for s in segments if isinstance(s, str) and s.strip()]
            if segments:
                carry = {
                    "segments": segments,
                    "stopped_at_stage": record.last_stage,
                    "turn_id": turn_id,
                    "written_at_ms": int(time.time() * 1000),
                }
        if not exchanges and carry is None and not record.spoken:
            return
        try:
            record.persist_task = asyncio.get_running_loop().create_task(
                self._persist_interrupted(turn_input.session_id, user_id, record,
                                          exchanges, carry, turn_input.channel,
                                          user_message=turn_input.user_message or "")
            )
        except RuntimeError:
            logger.warning(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted",
                       "status": "skipped", "error": "no running event loop"},
            )

    async def _persist_interrupted(
        self,
        session_id: str,
        user_id: str,
        record: TurnRecord,
        exchanges: list[dict],
        carry: "dict | None",
        channel: "str | None" = None,
        user_message: str = "",
    ) -> None:
        """Write an interrupted turn's tool rounds and utterances to Memory Layer.

        Tool rounds are merged into the **current** ``recent_tool_exchanges``
        (re-read now, not the start-of-turn snapshot, so a late persist cannot
        overwrite what a successor stored meanwhile), marked
        ``delivered: false``, deduplicated by tool_use id and capped to
        ``record.max_items``. Utterances go to ``turn_carryover``. If the turn
        was interrupted before its own fold ran, any existing carry-over is
        appended to rather than overwritten. One Memory Layer read serves both.
        Also writes ``current_question`` from the sentences actually emitted
        (``record.spoken``), and a ``recent_turns`` entry
        marked interrupted (NLU dialogue-acts spec §6.9). Never raises.

        Args:
            session_id: Session identifier.
            user_id: User identifier.
            record: The interrupted turn's ledger.
            exchanges: Captured rounds, already marked undelivered.
            carry: The ``turn_carryover`` payload, or None.
            channel: Channel, for the fold cap when appending.
            user_message: The turn's utterance; the ``recent_turns`` caller
                text when the fold has not run (``record.segments`` empty).
        """
        start = time.time()
        try:
            write_exchanges = bool(exchanges) and record.max_items > 0
            append_carry = carry is not None and not record.fold_ran
            session_state: dict = {}
            if write_exchanges or append_carry or record.spoken:
                try:
                    bundle = await self._async_memory.context_bundle(session_id, user_id)
                    session_state = bundle.session if isinstance(
                        getattr(bundle, "session", None), dict) else {}
                except Exception as e:  # noqa: BLE001 — fall back, still persist
                    # Without the current state, merge onto the start-of-turn
                    # snapshot: losing the rounds would let the model re-run
                    # tools it has already run.
                    logger.warning(
                        "orchestrator.interrupted_persist_read",
                        extra={"operation": "orchestrator.persist_interrupted",
                               "status": "failure", "session_id": session_id,
                               "error": f"{type(e).__name__}: {e}"},
                    )
                    session_state = {"recent_tool_exchanges": list(record.prior_exchanges)}
            if write_exchanges:
                merged = self._merge_undelivered_exchanges(
                    session_state.get("recent_tool_exchanges"), exchanges, record.max_items,
                )
                await self._async_memory.write(
                    session_id, user_id, "session", "recent_tool_exchanges", merged,
                )
            if carry is not None:
                if append_carry:
                    earlier = self._valid_carryover_segments(
                        session_state.get("turn_carryover"), self._turn_policy(channel),
                    )
                    carry = dict(carry, segments=earlier + carry["segments"])
                cap = self._turn_policy(channel).fold_max_segments
                if cap > 0:
                    carry["segments"] = carry["segments"][-cap:]
                await self._async_memory.write(
                    session_id, user_id, "session", "turn_carryover", carry,
                )
            if record.spoken:
                heard = " ".join(s.strip() for s in record.spoken if s.strip())
                await self._async_memory.write(
                    session_id, user_id, "session", "current_question",
                    self._sanitize_current_question(prev=session_state.get("current_question", "") or "",
                                                    new=heard, session_id=session_id))
                await self._async_memory.write(
                    session_id, user_id, "session", RECENT_TURNS_KEY,
                    append_recent_turn(session_state.get(RECENT_TURNS_KEY),
                                       caller=" ".join(s for s in record.segments if s) or user_message,
                                       bot=heard, interrupted=True,
                                       history_turns=self._recent_keep))
            logger.info(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted", "status": "success",
                       "session_id": session_id, "exchange_count": len(exchanges),
                       "segment_count": len(carry["segments"]) if carry else 0,
                       "stopped_at_stage": record.last_stage,
                       "latency_ms": int((time.time() - start) * 1000)},
            )
        except Exception as e:  # noqa: BLE001 — background task, must not raise
            logger.error(
                "orchestrator.interrupted_persist",
                extra={"operation": "orchestrator.persist_interrupted", "status": "failure",
                       "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                       "latency_ms": int((time.time() - start) * 1000)},
            )

    @staticmethod
    def _merge_undelivered_exchanges(
        current: Any, captured: list[dict], max_items: int,
    ) -> list[dict]:
        """Append an interrupted turn's rounds to the stored exchanges.

        A captured round whose tool_use ids are already stored is skipped, and
        the stored copy kept: it may already have been replayed to (and had
        its ``delivered`` flag cleared by) a turn that completed meanwhile.

        Args:
            current: ``recent_tool_exchanges`` as stored now (untrusted type).
            captured: The interrupted turn's rounds, marked undelivered.
            max_items: Cap from ``agent.recent_tool_exchanges.max_items``.

        Returns:
            The merged list, newest last, capped to ``max_items``.
        """
        stored = [ex for ex in current if isinstance(ex, dict)] if isinstance(current, list) else []

        def _ids(ex: dict) -> set:
            uses = ex.get("tool_uses")
            if not isinstance(uses, list):
                return set()
            return {u.get("id") for u in uses if isinstance(u, dict) and u.get("id")}

        seen: set = set()
        for ex in stored:
            seen |= _ids(ex)
        merged = list(stored)
        for ex in captured:
            ids = _ids(ex)
            if ids and ids & seen:
                continue
            merged.append(ex)
            seen |= ids
        return merged[-max_items:]

    def _valid_carryover_segments(self, raw: Any, policy: TurnPolicy) -> list[str]:
        """Return the usable segments of a stored ``turn_carryover`` value.

        Args:
            raw: The stored value (any type — upstream data is not trusted).
            policy: Policy supplying ``carryover_max_age_ms``.

        Returns:
            Non-empty string segments, or [] when the value is absent,
            malformed (including a missing, non-int, or far-future
            ``written_at_ms``), or older than the policy allows.
        """
        if raw is None:
            return []
        if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
            logger.warning(
                "orchestrator.carryover_discarded",
                extra={"operation": "orchestrator.fold_carryover", "status": "skipped",
                       "reason": "malformed"},
            )
            return []
        written = raw.get("written_at_ms")
        age_ms = (int(time.time() * 1000) - written
                  if isinstance(written, int) and not isinstance(written, bool) else None)
        if age_ms is not None and -_CARRYOVER_CLOCK_SKEW_MS <= age_ms < 0:
            age_ms = 0
        if age_ms is None or age_ms < 0:
            logger.warning(
                "orchestrator.carryover_discarded",
                extra={"operation": "orchestrator.fold_carryover", "status": "skipped",
                       "reason": "malformed"},
            )
            return []
        if age_ms > policy.carryover_max_age_ms:
            logger.info(
                "orchestrator.carryover_discarded",
                extra={"operation": "orchestrator.fold_carryover", "status": "skipped",
                       "reason": "stale"},
            )
            return []
        return [s for s in raw["segments"] if isinstance(s, str) and s.strip()]

    async def _fold_carryover(
        self,
        turn_input: TurnInput,
        bundle: Any,
        record: TurnRecord,
        user_id: str,
    ) -> TurnInput:
        """Fold an interrupted predecessor's utterances into this turn's input.

        Reads ``turn_carryover`` from the context bundle, clears it in Memory
        Layer (awaited, so a later write from this turn cannot land first), and
        joins the usable carried segments with this turn's utterance, capped to
        ``fold.max_segments``. Records the result in ``record.segments``.

        Args:
            turn_input: This turn's input.
            bundle: The step-1 context bundle (``bundle.session`` is mutated).
            record: This turn's ledger.
            user_id: Resolved user identifier.

        Returns:
            ``turn_input`` itself when nothing was folded, else a copy with the
            folded ``user_message``.
        """
        policy = self._turn_policy(turn_input.channel)
        session = bundle.session if isinstance(getattr(bundle, "session", None), dict) else {}
        raw = session.get("turn_carryover")
        carried: list[str] = []
        if raw is not None:
            if policy.fold_max_segments > 0:
                carried = self._valid_carryover_segments(raw, policy)
            session["turn_carryover"] = None
            await self._async_memory.write(
                turn_input.session_id, user_id, "session", "turn_carryover", None,
            )
        segments = carried + [turn_input.user_message]
        if policy.fold_max_segments > 0:
            segments = segments[-policy.fold_max_segments:]
        else:
            segments = [turn_input.user_message]
        record.segments = segments
        record.fold_ran = True
        if len(segments) == 1:
            return turn_input
        logger.info(
            "orchestrator.carryover_folded",
            extra={"operation": "orchestrator.fold_carryover", "status": "success",
                   "session_id": turn_input.session_id,
                   "folded_segment_count": len(segments) - 1},
        )
        return dataclasses.replace(
            turn_input, user_message=" ".join(s.strip() for s in segments),
        )

    async def _stream_turn_impl(
        self,
        turn_input: TurnInput,
        *,
        abort_event: "asyncio.Event | None" = None,
        turn_id: str = "",
        record: TurnRecord,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run the streaming pipeline body; see :meth:`stream_turn` for the contract.

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

        # GH-403: time to first audio. ``latency_ms`` on the completion banner
        # measures turn start to the LAST sentence, which is not what a caller
        # reacts to on a phone line — they react to the first one, and every
        # sentence after it is spoken while they are already listening.
        #
        # Two marks, because the gap between them is ours to control:
        #   llm_ttft_ms        the MODEL's own time to first token, measured
        #                      from the moment the request is issued. This is
        #                      the number to compare against another model or
        #                      provider — it excludes everything we do first.
        #   first_token_ms     the same first token, but measured from TURN
        #                      start. The difference between this and
        #                      llm_ttft_ms is our own pre-LLM cost: memory
        #                      read, NLU, trust input, routing, prompt build.
        #   first_sentence_ms  a whole sentence was assembled, trust-checked
        #                      and handed to the channel — the caller hears
        #                      audio at roughly this point
        # Their difference is the cost of buffering to sentence boundaries;
        # first_sentence_ms against total latency is the cost of everything
        # said after the caller already had an answer.
        _timings: dict[str, int | None] = {
            "llm_ttft_ms": None, "first_token_ms": None, "first_sentence_ms": None,
        }

        def _mark(key: str) -> None:
            """Record the first occurrence of a turn milestone, in ms."""
            if _timings[key] is None:
                _timings[key] = int((time.time() - start) * 1000)

        def _stamp(ev):
            """Set turn_id on the event in place and return it.

            Also marks first-sentence time. Every SentenceEvent in this method
            is yielded through here — the canned paths (consent, blocked,
            escalation, HiTL, fallback) as well as the streamed ones — so
            hooking it is what keeps the measurement honest across all of
            them rather than only the happy path.
            """
            if _timings["first_sentence_ms"] is None and isinstance(ev, SentenceEvent):
                _mark("first_sentence_ms")
            if hasattr(ev, "turn_id"):
                ev.turn_id = turn_id
            if isinstance(ev, SentenceEvent) and getattr(ev, "text", ""):
                record.spoken.append(ev.text)
            return ev

        was_escalated = False
        was_tool_used = False
        model_used = ""
        trust_input = TrustCheckResult(passed=True, action="allow")
        trust_output = TrustCheckResult(passed=True, action="allow")
        nlu_result = NLUResult(intent="unknown", entities={}, confidence=0.0)
        understanding = None
        tool_cache = None
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
            yield _stamp(SignalEvent(stage="memory_read", status="complete"))
            logger.info(
                "  [STEP 1] Memory context_bundle  ✓  current_subagent_id=%s"
                "  is_returning=%s  latency=%dms",
                bundle.session.get("current_subagent_id") or self._workflow.start_subagent_id,
                bundle.session.get("is_returning", False),
                int((time.time() - t1) * 1000),
            )
            if _aborted():
                return
            # Session bootstrap: first turn only, before routing and carry-over.
            # Runs after the STEP 1 log/signal so memory-read latency excludes
            # it; the bootstrap reports its own latency ([STEP 1b]).
            await self._run_session_bootstrap_async(bundle, session_id, user_id)
            # Built before NLU so the frame can read stored results (spec §8);
            # reused at prompt assembly and in the tool loop.
            tool_cache = TurnToolCache(self._tool_policies, bundle.tool_results, bundle.session)
            current_subagent_id: str = (
                bundle.session.get("current_subagent_id")
                or self._workflow.start_subagent_id
            )
            current_question: str = bundle.session.get("current_question", "")
            # Spec §4.6: fold an interrupted predecessor's utterances into this
            # turn before NLU and the input trust check see the message.
            turn_input = await self._fold_carryover(turn_input, bundle, record, user_id)

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

            _ln_cfg_s = (
                self._config.get("preprocessing", {})
                .get("language_normalisation", {})
            )
            _ln_enabled_s = bool(_ln_cfg_s.get("enabled", True))
            logger.info(
                "  [STEP 4+5] Language Norm + NLU  →  (session=%s, ln_enabled=%s)",
                session_id, _ln_enabled_s,
            )
            t45 = time.time()
            yield _stamp(SignalEvent(stage="nlu", status="start"))

            _ctx = self._turn_context(bundle, current_subagent_id,
                                      list(record.segments) or [turn_input.user_message], tool_cache)
            if _ln_enabled_s:
                # asyncio.to_thread offloads each sync provider.call() onto the
                # default thread pool so the two LLM round-trips overlap in wall clock.
                (normalised_input, turn_language), understanding = await asyncio.gather(
                    asyncio.to_thread(self._language_normaliser.normalise, turn_input.user_message, self._config),
                    asyncio.to_thread(self._understander.understand, _ctx),
                )
            else:
                # LN disabled (#313): skip the leading LLM call; main LLM mirrors language.
                normalised_input, turn_language = turn_input.user_message, ""
                understanding = await asyncio.to_thread(self._understander.understand, _ctx)
            early_nlu_result = understanding.nlu_result
            logger.info(
                "  [STEP 4+5] Lang-Norm + NLU  ✓  detected=%s  intent=%s  total_latency=%dms",
                turn_language or "—",
                early_nlu_result.intent,
                int((time.time() - t45) * 1000),
            )

            profile_data = bundle.profile if bundle.profile is not None else {}
            session_data = bundle.session if bundle.session is not None else {}
            default_language = _ln_cfg_s.get("default_language", "hindi")
            language_preference = (
                profile_data.get("language_preference")
                or session_data.get("language_preference")
                or turn_language
                or (_ln_cfg_s.get("default_language", "hindi") if _ln_enabled_s else "")
            )

            # Lock in language_preference on the first turn only.
            # Skip when LN is disabled and we have no real language signal yet —
            # the main LLM will infer it from the first turn via the mirror directive.
            # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
            saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
            if not saved_preference and language_preference:
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
                        "  model=%s  total_latency=%dms  next_subagent=%s  sentences=%d\n"
                        "  llm_ttft=Nonems  first_token=Nonems  first_sentence=%dms\n"
                        "  response: %r\n"
                        "═══════════════════════════════════════════════════════════════",
                        session_id, "consent_prompt", False,
                        "none", consent_latency_ms, "consent_gate",
                        1 if consent_response_text else 0,
                        # This path skips the LLM entirely — the canned consent
                        # prompt is the only sentence, so first-sentence time is
                        # the whole turn and there is no first token to report.
                        consent_latency_ms,
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
                    # Emit consent SSE event if this is the configured recording purpose.
                    _consent_evt_queue: list = []
                    _configured_consent_purpose: str = (
                        self._config.get("reach_layer", {})
                        .get("channels", {})
                        .get("voice", {})
                        .get("recording", {})
                        .get("consent_purpose", "recording")
                    )
                    emit_consent_event_if_recording(
                        queue=_consent_evt_queue,
                        purpose="storage",
                        granted=granted,
                        configured_purpose=_configured_consent_purpose,
                        turn_id=turn_id,
                    )
                    for _cevt in _consent_evt_queue:
                        yield _cevt

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
                        understanding = await asyncio.to_thread(
                            self._understander.understand,
                            self._turn_context(bundle, current_subagent_id, [pending_msg], tool_cache))
                        early_nlu_result = understanding.nlu_result
                        logger.info(
                            "orchestrator.consent_gate",
                            extra={
                                "operation": "orchestrator.consent_gate",
                                "status": "pending_message_replayed",
                                "session_id": session_id,
                                "replayed_intent": early_nlu_result.intent,
                            },
                        )

                    # GH-239: do NOT emit the canned opening_phrase on the
                    # first post-consent turn. The LLM's first reply already
                    # opens with greeting language, so emitting both produced
                    # back-to-back greetings (canned phrase + LLM greeting)
                    # to the caller. We still latch ``opening_phrase_emitted``
                    # so a future SSE reconnect on the same session won't trip
                    # the TurnAssembler emit path.
                    if not bundle.session.get("opening_phrase_emitted", False):
                        await self._async_memory.write(
                            session_id, user_id, "session", "opening_phrase_emitted", True
                        )
                        await self._async_memory.write(
                            session_id, user_id, "session",
                            "current_subagent_id", current_subagent_id,
                        )
                        bundle.session["opening_phrase_emitted"] = True
                        logger.info(
                            "orchestrator.opening_phrase_suppressed",
                            extra={
                                "operation": "orchestrator.opening_phrase_gate",
                                "status": "skipped",
                                "session_id": session_id,
                                "subagent_id": current_subagent_id,
                                "trigger": "post_consent",
                                "reason": "GH-239 — LLM's first post-consent reply serves as greeting",
                            },
                        )

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

            # ── Step 5: NLU (result from parallel gather) ───────────────
            # GH-151 #2: NLU already ran in parallel with lang-norm above.
            nlu_result = early_nlu_result
            yield _stamp(SignalEvent(stage="nlu", status="complete"))
            logger.info(
                "  [STEP 5] NLU  ✓  intent=%s  confidence=%.2f"
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
            # First-turn guard: a callback that adopted prior state will produce
            # turn_count==0 on the user's first utterance. Routing straight to
            # goodbye there means the LLM never sees the resume context — the
            # caller hangs up thinking the bot decided to end the call. Force
            # the LLM path on turn 0 so resume utterances get weighed against
            # session history before any termination decision.
            term_turn_count: int = int(bundle.session.get("turn_count", 0) or 0)
            if (
                term_enabled
                and term_turn_count > 0
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

            stream_previous_user_state_payload: dict | None = None
            stream_previous_user_state_id: str | None = None
            if self._user_state_enabled:
                _maybe = bundle.session.get("user_state")
                if isinstance(_maybe, dict):
                    stream_previous_user_state_payload = _maybe
                    stream_previous_user_state_id = _maybe.get("id")
                if stream_previous_user_state_id is None:
                    stream_previous_user_state_id = self._user_state_default

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

            # Apply the understanding's writes; entity_map is read again at
            # prompt assembly (_build_profile_context).
            entity_map: dict = self._config.get("entity_to_profile_field", {})
            await self._apply_understanding_async(session_id, user_id, bundle, understanding,
                                                  turn_input.user_message)

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
                    # Profile write already happened via the understanding's
                    # writes above (enum-normalised slot); mirror into bundle.session and flip
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

            # ── Human handoff — a fixed line, no LLM (identity/handoff §4) ──
            if nlu_result.intent == "human_request":
                # The bridge speaks tool_status_phrases["request_human"] while
                # escalate runs, so the caller does not hear silence.
                _handoff_hold = self._handoff_will_escalate(bundle)
                if _handoff_hold:
                    yield _stamp(SignalEvent(stage="tool_start", status="start", tools=["request_human"]))
                handoff_line = await self._handle_human_request_async(
                    session_id, user_id, bundle, turn_input, turn_id=turn_id,
                    caller=" ".join(record.segments or [turn_input.user_message]))
                if _handoff_hold:
                    yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                if handoff_line is not None:
                    async for ev in self._stream_termination_short_circuit(
                        session_id=session_id, user_id=user_id, turn_id=turn_id,
                        turn_input=turn_input, detected_language=detected_language,
                        nlu_result=nlu_result, bundle=bundle,
                        trust_input=trust_input, trust_output=trust_output,
                        start=start, stamp=_stamp,
                        message=handoff_line, subagent_id="handoff",
                        end_session=False, translate=False,
                    ):
                        yield ev
                    return

            # ── Step 6: Routing ────────────────────────────────────────
            logger.info(
                "  [STEP 6] Routing  →  intent=%s  current_subagent=%s",
                nlu_result.intent, current_subagent_id,
            )
            t6 = time.time()
            yield _stamp(SignalEvent(stage="routing", status="start"))
            routing_state = self._routing_state(bundle)

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
            routing_writes.append(
                self._async_memory.write(
                    session_id, user_id, "session", "subagent_entry_count", subagent_entry_count
                )
            )
            # Don't persist routing into a terminal subagent. The next session
            # adopts the last persisted current_subagent_id; if that's "ended",
            # callbacks immediately re-route to goodbye instead of resuming the
            # last meaningful stage.
            if not self._workflow.subagents[next_subagent_id].is_terminal:
                routing_writes.append(
                    self._async_memory.write(
                        session_id, user_id, "session", "current_subagent_id", next_subagent_id
                    )
                )
            # Latch ``opening_phrase_emitted`` AFTER routing, so it means the
            # same thing on both execution paths: "the opening question has
            # been put to the caller in this call, so an answer read from
            # session state belongs to this call and not a previous one."
            #
            # process_turn gets this free — its opening_phrase gate returns the
            # greeting before routing, so the flag is already set by turn 2.
            # stream_turn has no such gate (GH-239 suppresses the canned phrase
            # because the LLM's own first reply greets), and the existing latch
            # below it only runs on the post-consent branch — which a domain
            # without the consent gate never reaches. The flag then stayed
            # unset forever and every routing rule guarded by it was dead.
            #
            # After routing, not before: on turn 1 the rules must NOT yet see
            # it, or a consent answer persisted from an earlier call would fire
            # before this caller has been asked anything.
            if not bundle.session.get("opening_phrase_emitted", False):
                routing_writes.append(
                    self._async_memory.write(
                        session_id, user_id, "session", "opening_phrase_emitted", True
                    )
                )
                bundle.session["opening_phrase_emitted"] = True

            if routing_writes:
                await asyncio.gather(*routing_writes, return_exceptions=True)
            yield _stamp(SignalEvent(stage="routing", status="complete"))
            logger.info(
                "  [STEP 6] Routing  ✓  next_subagent=%s  matched_rule_intent=%s  latency=%dms",
                next_subagent_id,
                matched_rule.intent if matched_rule else "—",
                int((time.time() - t6) * 1000),
            )

            # ── Step 6a: a phase whose reply is one fixed sentence ─────
            # See SubAgent.fixed_opening. Three conditions, all required:
            # first entry to the phase, every named session field present,
            # and the caller's own turn carried no entities — if they named a
            # trade or city themselves, their words win and the model handles
            # it as before.
            _sa = self._workflow.subagents.get(next_subagent_id)
            _tmpl = (getattr(_sa, "fixed_opening", "") or "").strip()
            if _tmpl:
                _requires = list(getattr(_sa, "fixed_opening_requires", []) or [])
                _counts = bundle.session.get("subagent_entry_count") or {}
                _first_entry = int(
                    (_counts or {}).get(next_subagent_id, 0) or 0
                ) <= 1
                _vals = {
                    k: str(bundle.session.get(k) or "").strip() for k in _requires
                }
                _have_all = bool(_requires) and all(_vals.values())
                _caller_said_something = bool(nlu_result.entities or {})
                if _first_entry and _have_all and not _caller_said_something:
                    try:
                        _line = _tmpl.format(**_vals).strip()
                    except (KeyError, IndexError):
                        logger.warning(
                            "orchestrator.fixed_opening_placeholder_missing",
                            extra={"operation": "orchestrator.stream_turn",
                                   "status": "skipped",
                                   "subagent_id": next_subagent_id},
                        )
                        _line = ""
                    if _line:
                        logger.info(
                            "  [STEP 7] Prompt Assembly  ⏭  skipped — %s speaks its "
                            "fixed opening", next_subagent_id,
                        )
                        async for ev in self._stream_termination_short_circuit(
                            session_id=session_id, user_id=user_id, turn_id=turn_id,
                            turn_input=turn_input, detected_language=detected_language,
                            nlu_result=nlu_result, bundle=bundle,
                            trust_input=trust_input, trust_output=trust_output,
                            start=start, stamp=_stamp,
                            message=_line, subagent_id=next_subagent_id,
                            end_session=False,
                        ):
                            yield ev
                        return

            # ── Step 6b: terminal phases speak fixed copy, not generated text ──
            # A terminal subagent has no tools and a verified opening_phrase.
            # There is nothing for the model to decide, and asking it to
            # paraphrase fixed copy is how two real failures happened:
            #
            #   - a 16-year-old was told "applications are not possible for
            #     those under SIXTEEN" — the model echoed the caller's own age
            #     as the threshold instead of the nineteen the config states;
            #   - the consent-declined and under-19 turns came back EMPTY, so
            #     the caller heard "sorry, I didn't catch that" at the moment
            #     they were being turned away.
            #
            # Both disappear if the configured line is spoken verbatim. It is
            # also faster: no model call on the last turn of the call.
            _terminal = self._workflow.subagents.get(next_subagent_id)
            _fixed_copy = (
                (getattr(_terminal, "opening_phrase", "") or "").strip()
                if _terminal is not None and getattr(_terminal, "is_terminal", False)
                else ""
            )
            if _fixed_copy:
                logger.info(
                    "  [STEP 7] Prompt Assembly  ⏭  skipped — %s is terminal, "
                    "speaking its configured line",
                    next_subagent_id,
                )
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
                    message=_fixed_copy,
                    subagent_id=next_subagent_id,
                ):
                    yield ev
                return

            # ── Step 6c: pre-dispatch (Spec E §5) ──────────────────────
            # Runs after routing and before the prompt, so its result is in
            # the turn cache when <known_facts> and <state> render. Per-TURN
            # tool-call counts start here: a capped tool cannot slip through
            # by being requested again in a later round, and the pre-dispatch
            # itself counts toward the cap.
            _turn_tool_counts: dict[str, int] = {}
            _offered = self._offered_tools(next_subagent_id)
            _pd = await self._predispatch_async(
                bundle, next_subagent_id, nlu_result.intent, tool_cache,
                session_id, user_id, _turn_tool_counts,
                pending_id=getattr(understanding, "pending_id", None))
            self._record_predispatch(_pd, "orchestrator.stream_turn", session_id)
            # Recorded now, before any early exit. An abort or error from here
            # on persists record.captured_exchanges (interrupted-turn persist).
            _pd_results, _pd_exchanges, _pd_tool_calls = self._predispatch_ledger(_pd, turn_id)
            if _pd_exchanges:
                record.captured_exchanges.extend(_pd_exchanges)
                record.max_items = self._recent_tool_exchanges_caps()[0]
            if _pd_tool_calls:
                was_tool_used = True
            await self._post_applied_hook_async(session_id, user_id, bundle, _pd_results)

            # ── Step 7: Prompt assembly ────────────────────────────────
            logger.info(
                "  [STEP 7] Prompt Assembly  →  subagent=%s  language=%s",
                next_subagent_id, detected_language,
            )
            next_subagent: SubAgent = self._workflow.subagents[next_subagent_id]
            profile_context = self._build_profile_context(bundle, entity_map)
            state_text = self._render_state(bundle, next_subagent_id, tool_cache, profile_context)
            recent_text = render_recent(bundle.session.get(RECENT_TURNS_KEY), self._agent_history_turns)
            _guarded, _guard_counts, _guard_lang = self._make_output_guard(
                channel_config, profile_context, bundle)

            final_language = profile_context.get("language_preference", detected_language)
            is_resumption = bundle.session.get("was_adopted", False)

            system = self._manager_agent.build_system_prompt(
                agent_system_prompt=self._workflow.agent_system_prompt,
                subagent_system_prompt=next_subagent.system_prompt,
                detected_language=final_language,
                channel=turn_input.channel,
                channel_config=channel_config,
                is_resumption=is_resumption,
                user_state_guidance=stream_user_state_guidance_text,
                session_end_eval_prompt=(
                    self._session_end_eval_prompt if self._session_end_eval_enabled else None
                ),
                known_facts=tool_cache.render_known_facts(),
                caller_turn=render_caller_turn(understanding),
                state=state_text,
                recent=recent_text,
            )

            if is_resumption:
                bundle.session["was_adopted"] = False
                await self._async_memory.write(session_id, user_id, "session", "was_adopted", False)

            messages = self._manager_agent.build_messages(
                user_message=turn_input.user_message,
            )

            # ── #193: prepend prior tool_use/tool_result exchanges ──────
            _prior_exchanges, _max_items, _max_chars = self._prepend_tool_replay(
                messages, bundle, session_id, "orchestrator.stream_turn",
                undelivered_note=self._turn_policy(turn_input.channel).undelivered_note,
                skip_tools=tool_cache.fresh_tools(),
            )

            # Tool exchanges captured during *this* turn's tool rounds; persisted
            # at the end of the turn so the next turn can replay them. The list
            # lives on the record so an interrupted turn's rounds survive it.
            record.prior_exchanges = list(_prior_exchanges)
            record.max_items = _max_items
            _captured_exchanges_this_turn: list[dict] = record.captured_exchanges

            if not messages:
                # Completes, so the interrupted-turn persist will not run.
                await self._settle_predispatch_stream(
                    session_id, user_id, bundle, tool_cache, _prior_exchanges,
                    _pd_exchanges, _max_items)
                yield _stamp(DoneEvent(turn_id=turn_id, latency_ms=int((time.time() - start) * 1000)))
                return

            if _aborted():
                return

            # ── Step 8: LLM streaming ──────────────────────────────────
            # The filtered list is the one every main-LLM call of this turn uses.
            active_tools, _tool_choice = self._inject_predispatch(_pd, messages, _offered)
            _llm_calls = 0
            sentence_index = 0
            token_buffer = ""
            # Portion of ``full_response_text`` already replayed to the model.
            _spoken_sent_to_model = ""
            primary_model = self._llm.get_active_model()
            primary_provider = self._config.get("agent", {}).get("provider", "anthropic")

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
                "  [STEP 8] LLM Stream Call #1  →  provider=%s  model=%s"
                "  tools_available=%d  message_count=%d",
                primary_provider, primary_model, len(active_tools), len(messages),
            )
            t8 = time.time()

            # GH-194: per-channel response-length cap (None → wrapper default).
            channel_max_tokens = channel_config.get("max_tokens")

            try:
                request = ChatRequest(
                    messages=messages,
                    system=system,
                    tools=_legacy_tools_to_neutral(active_tools) if active_tools else [],
                    tool_choice=_tool_choice,
                    max_tokens=channel_max_tokens or 4096,
                )
                # GH-244: the model sometimes returns a COMPLETELY empty
                # completion — no text, no tool call — and the caller hears
                # silence, which on a phone line is indistinguishable from a
                # dropped call. One retry, and only when nothing has reached
                # the caller yet.
                #
                # The loop below emits each sentence as it is parsed, so a
                # PARTIAL response must never be re-requested — that would
                # speak the first half twice. The guard therefore requires
                # all three to be empty: no sentence emitted, no
                # un-terminated text still buffered, and no accumulated
                # response. A tool call leaves via ToolUseRequested and never
                # reaches the check.
                for _empty_attempt in range(2):
                    _llm_calls += 1
                    async for token in self._llm.stream(request, abort_event=abort_event):
                        if _aborted():
                            return
                        if _timings["llm_ttft_ms"] is None:
                            _timings["llm_ttft_ms"] = int((time.time() - t8) * 1000)
                        _mark("first_token_ms")
                        token_buffer += token
                        sentences, token_buffer = _split_sentences(token_buffer)
                        # Stop accepting new sentences once a batch was blocked —
                        # subsequent sentences in this turn must NOT reach TTS.
                        if _trust_batcher.was_escalated:
                            was_escalated = True
                            continue
                        pending_emit: list[str] = []
                        for sentence in sentences:
                            sentence = _guarded(sentence)
                            if not sentence:
                                continue
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

                    # Anything the model produced counts, whether or not the
                    # caller has heard it yet — including sentences still
                    # buffered in the trust batcher, which on a short turn is
                    # the whole response (it flushes at turn end, not during
                    # the stream).
                    if (sentence_index
                            or token_buffer.strip()
                            or full_response_text.strip()
                            or _trust_batcher.has_pending):
                        break
                    if _empty_attempt == 0:
                        logger.warning(
                            "orchestrator.stream_empty_completion_retry",
                            extra={
                                "operation": "orchestrator.stream_turn",
                                "status": "degraded",
                                "session_id": session_id,
                                "subagent_id": current_subagent_id,
                                "model": self._llm.get_active_model(),
                            },
                        )
                model_used = self._llm.get_active_model()
                logger.info(
                    "  [STEP 8] LLM Stream Call #1  ✓  model_used=%s"
                    "  sentences=%d  latency=%dms",
                    model_used, sentence_index, int((time.time() - t8) * 1000),
                )

            except ToolUseRequested as e:
                # ── Step 9: Tool use ───────────────────────────────────
                was_tool_used = True
                all_tool_calls = [
                    ToolCall(
                        tool_name=tu.tool_name,
                        tool_use_id=tu.tool_use_id,
                        input_params=tu.input,
                    )
                    for tu in e.tool_calls
                ]
                tool_names = [tc.tool_name for tc in all_tool_calls]
                logger.info(
                    "  [STEP 8] LLM Stream Call #1  ✓  stop_reason=tool_use  tools=%s  latency=%dms",
                    tool_names, int((time.time() - t8) * 1000),
                )
                logger.info("  [STEP 9] Tool-Use Loop  →  executing tools=%s", tool_names)
                t9 = time.time()

                yield _stamp(SignalEvent(stage="tool_start", status="start",
                                         tools=tool_names))
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
                    "confidence": nlu_result.confidence,
                    "normalised_input": normalised_input,
                    "detected_language": detected_language,
                }
                for tc in all_tool_calls:
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
                        # One decision (tool_guard.check_tool_call): consent, then
                        # the per-turn cap, then grounding, then the stored-result
                        # cache. All refuse BEFORE the call leaves us, because
                        # they protect writes that cannot be taken back.
                        #   cap        - the model acted on every row of a list
                        #                it was shown (5 applies from one pick)
                        #   grounding  - it supplied an id no tool returned
                        # stream_turn has its own tool loop and never calls
                        # ManagerAgent.run_turn, so the sync path's guards do
                        # not cover the path a voice client actually uses.
                        _caps = getattr(self._manager_agent, "_tool_call_caps", None)
                        _caps = _caps if isinstance(_caps, dict) else {}
                        _used = _turn_tool_counts.get(tc.tool_name, 0)
                        # remember is never capped nor grounding-checked.
                        _is_remember = (self._remember is not None
                                        and tc.tool_name == self._remember.name)
                        if _is_remember:
                            # Framework tool: validated state write, never
                            # capped, grounding-refused or sent to the gateway.
                            tool_result = await self._remember.handle_async(
                                tc, messages, tool_cache.stored_results_by_tool(),
                                lambda scope, key, value: self._async_memory.write_strict(
                                    session_id, user_id, scope, key, value),
                                self._remember_on_saved(bundle),
                            )
                        else:
                            self._enforce_session_only(tc, bundle)
                            _spec = (
                                getattr(self._manager_agent, "_grounded_params", {}) or {}
                            ).get(tc.tool_name) or {}
                            _verdict = check_tool_call(
                                tc,
                                cap=_caps.get(tc.tool_name),
                                used=_used,
                                grounded_spec=_spec,
                                messages=messages,
                                stored_results=tool_cache.stored_results_by_tool(),
                                session_grounded=self._session_grounded_values(bundle, _spec),
                                consent_ok=None,
                                cache_lookup=tool_cache.lookup,
                                requires_pending=self._requires_pending_for(tc.tool_name),
                                pending_id=getattr(understanding, "pending_id", None),
                            )
                            if _verdict.kind != "go":
                                tool_result = _verdict.result
                            else:
                                _turn_tool_counts[tc.tool_name] = _used + 1
                                tool_result = await self._async_gateway.execute(
                                    tool_cache.prepare(tc), session_id, user_id,
                                    session_values=self._tool_session_values(bundle),
                                )
                                tool_result = self._result_shaper.shape(tool_result)
                                await self._write_mapped_session_values(
                                    session_id, user_id, tool_result, bundle,
                                )
                                tool_cache.after_call(tc, tool_result)
                                # Persist now: a streaming turn may be stopped
                                # before it completes.
                                await self._persist_tool_cache(session_id, user_id, tool_cache)
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
                # Capture before yielding tool_end: a turn stopped at this yield
                # must still record the round it just completed (spec §4.5).
                _ex = self._capture_tool_exchange(
                    all_tool_calls, tool_results_for_llm, _max_chars,
                )
                if _ex is not None:
                    _captured_exchanges_this_turn.append(_ex)
                yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                if _aborted():
                    return
                logger.info(
                    "  [STEP 9] Tool-Use Loop  ✓  tools_called=%s  latency=%dms",
                    tool_names, int((time.time() - t9) * 1000),
                )
                # GH-204 fallback path: when NLU misses `termination_intent`,
                # the model ends the call itself by calling `end_session`. That
                # tool is internal — it resolves in ~0 ms and its result
                # ("acknowledged") carries nothing the model needs to read back,
                # while its own description already tells the model to speak the
                # closing line alongside the call. So a second pass here just
                # regenerates a goodbye the caller is about to hear anyway, at the
                # cost of a full round trip (946–1206 ms measured on live calls)
                # on the last turn of every call that ends this way.
                #
                # Guarded on text actually existing, by the same predicate the
                # empty-completion retry above uses: when the model produced none,
                # this loop is what writes the reply, and skipping it would end the
                # call in silence. Any other tool in the round still needs its
                # result read back, so the skip requires end_session to be alone.
                _only_end_session = bool(all_tool_calls) and all(
                    tc.tool_name == "end_session" for tc in all_tool_calls
                )
                _closing_text_exists = bool(
                    sentence_index
                    or token_buffer.strip()
                    or full_response_text.strip()
                    or _trust_batcher.has_pending
                )
                _skip_final_pass = _only_end_session

                # The model is told to speak its closing line alongside the
                # tool call, and in practice never does — across every
                # end_session observed on the VM it came back as
                # stop_reason=tool_use with no text at all. Rather than spend a
                # round trip letting it write a goodbye we already have in
                # config, speak the configured one. It is the same string
                # #204's short-circuit uses, so a call ends identically whether
                # NLU caught the goodbye or the model did.
                if _only_end_session and not _closing_text_exists:
                    # Prefer the subagent's own opening_phrase over the generic
                    # goodbye. A phase that ends the call for a REASON has that
                    # reason written in its opening_phrase, and the caller needs
                    # it: u18_blocked explains the portal route, consent_declined
                    # says nothing was saved. Measured — a caller giving age 16
                    # routed correctly to u18_blocked, the model called
                    # end_session with no text, and the generic
                    # termination_message was all they heard.
                    #
                    # This is also the only place opening_phrase reaches a caller
                    # on this path at all: it is emitted in process_turn only, so
                    # on the streaming path every phase's opening_phrase is
                    # otherwise dead config.
                    _canned = (
                        getattr(next_subagent, "opening_phrase", "") or ""
                    ).strip()
                    if not _canned:
                        _canned = (self._config.get("conversation", {}) or {}).get(
                            "termination_message", ""
                        ) or ""
                    _canned = self._translate_consent_message(
                        _canned, detected_language,
                    )
                    if _canned:
                        logger.info(
                            "  [STEP 8] LLM Stream Call #2  ⏭  skipped — spoke the "
                            "configured termination_message instead"
                        )
                        full_response_text += _canned + " "
                        yield _stamp(SentenceEvent(
                            text=_canned, sentence_index=sentence_index,
                        ))
                        sentence_index += 1
                    else:
                        # Nothing configured to fall back on: the second pass is
                        # the only thing that can produce a reply, so make it
                        # rather than hang up on the caller in silence.
                        _skip_final_pass = False
                        logger.warning(
                            "orchestrator.stream_end_session_no_canned_line",
                            extra={
                                "operation": "orchestrator.stream_turn",
                                "status": "fallback",
                                "session_id": session_id,
                            },
                        )

                if _skip_final_pass:
                    logger.info(
                        "  [STEP 8] LLM Stream Call #2  ⏭  skipped — end_session was "
                        "the only tool of the round"
                    )
                else:
                    # Call #2 will answer again, so drop call #1's undelivered
                    # text. Only on this branch: the skip branch above has no
                    # second pass and keeps that text as the reply.
                    _dropped = _trust_batcher.discard_pending()
                    if _dropped or token_buffer.strip():
                        logger.info(
                            "orchestrator.stream_tool_preamble_discarded",
                            extra={
                                "operation": "orchestrator.stream_turn",
                                "status": "success",
                                "session_id": session_id,
                                "sentences_discarded": _dropped,
                                "partial_chars": len(token_buffer.strip()),
                            },
                        )
                    token_buffer = ""
                    if sentence_index:
                        # Already streamed; cannot be withdrawn.
                        logger.warning(
                            "orchestrator.stream_tool_preamble_delivered",
                            extra={
                                "operation": "orchestrator.stream_turn",
                                "status": "degraded",
                                "session_id": session_id,
                                "sentences_delivered": sentence_index,
                            },
                        )
                    logger.info(
                        "  [STEP 8] LLM Stream Call #2  →  provider=%s  model=%s"
                        "  message_count=%d",
                        primary_provider, primary_model, len(messages) + 2,
                    )
                t8b = time.time()

                # Resume streaming with tool results — loop handles multi-step tool chains
                _MAX_TOOL_ROUNDS: int = self._config.get("agent", {}).get("max_tool_rounds", 3)
                _current_tool_calls = all_tool_calls
                _current_tool_results = tool_results_for_llm
                _tool_round = 1

                while True:
                    if _skip_final_pass:
                        break
                    # Replay delivered text as assistant content so the model
                    # sees what it has already said and does not repeat it.
                    _assistant_blocks: list[Any] = []
                    _spoken_delta = full_response_text[
                        len(_spoken_sent_to_model):
                    ].strip()
                    if _spoken_delta:
                        _assistant_blocks.append(TextBlock(text=_spoken_delta))
                        _spoken_sent_to_model = full_response_text
                    _assistant_blocks.extend(
                        ToolUseBlock(
                            tool_use_id=tc.tool_use_id,
                            tool_name=tc.tool_name,
                            input=tc.input_params or {},
                        )
                        for tc in _current_tool_calls
                    )
                    messages.append(Message(
                        role="assistant",
                        content=_assistant_blocks,
                    ))
                    messages.append(Message(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id=tr["tool_use_id"],
                                content=tr.get("content", ""),
                                is_error=tr.get("is_error", False),
                            )
                            for tr in _current_tool_results
                        ],
                    ))

                    if _aborted():
                        return
                    try:
                        request = ChatRequest(
                            messages=messages,
                            system=system,
                            tools=_legacy_tools_to_neutral(active_tools) if active_tools else [],
                            tool_choice=_tool_choice,
                            max_tokens=channel_max_tokens or 4096,
                        )
                        _llm_calls += 1
                        async for token in self._llm.stream(request, abort_event=abort_event):
                            if _aborted():
                                return
                            if _timings["llm_ttft_ms"] is None:
                                _timings["llm_ttft_ms"] = int((time.time() - t8b) * 1000)
                            _mark("first_token_ms")
                            token_buffer += token
                            sentences, token_buffer = _split_sentences(token_buffer)
                            if _trust_batcher.was_escalated:
                                was_escalated = True
                                continue
                            pending_emit = []
                            for sentence in sentences:
                                sentence = _guarded(sentence)
                                if not sentence:
                                    continue
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

                        # Another round follows, so drop this one's
                        # undelivered text. After the max-rounds break, where
                        # that text is the only reply there is.
                        _dropped_n = _trust_batcher.discard_pending()
                        if _dropped_n or token_buffer.strip():
                            logger.info(
                                "orchestrator.stream_tool_preamble_discarded",
                                extra={
                                    "operation": "orchestrator.stream_turn",
                                    "status": "success",
                                    "session_id": session_id,
                                    "tool_round": _tool_round,
                                    "sentences_discarded": _dropped_n,
                                    "partial_chars": len(token_buffer.strip()),
                                },
                            )
                        token_buffer = ""

                        _nested_tool_calls = [
                            ToolCall(
                                tool_name=tu.tool_name,
                                tool_use_id=tu.tool_use_id,
                                input_params=tu.input,
                            )
                            for tu in nested_e.tool_calls
                        ]
                        _nested_tool_names = [tc.tool_name for tc in _nested_tool_calls]
                        logger.info(
                            "  [STEP 9] Tool-Use Loop (round %d)  →  executing tools=%s",
                            _tool_round, _nested_tool_names,
                        )
                        yield _stamp(SignalEvent(stage="tool_start", status="start",
                                                 tools=_nested_tool_names))
                        _nested_results = []
                        for tc in _nested_tool_calls:
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
                                # Second streaming execution site (nested tool
                                # rounds). Same decision as the first, and
                                # the cap shares _turn_tool_counts with it -
                                # counted per TURN precisely so a capped tool
                                # cannot slip through by being requested again
                                # in a later round of the same turn.
                                _caps2 = getattr(self._manager_agent, "_tool_call_caps", None)
                                _caps2 = _caps2 if isinstance(_caps2, dict) else {}
                                _used2 = _turn_tool_counts.get(tc.tool_name, 0)
                                _is_remember2 = (self._remember is not None
                                                 and tc.tool_name == self._remember.name)
                                if _is_remember2:
                                    tool_result = await self._remember.handle_async(
                                        tc, messages, tool_cache.stored_results_by_tool(),
                                        lambda scope, key, value: self._async_memory.write_strict(
                                            session_id, user_id, scope, key, value),
                                        self._remember_on_saved(bundle),
                                    )
                                else:
                                    self._enforce_session_only(tc, bundle)
                                    _spec2 = (
                                        getattr(self._manager_agent, "_grounded_params", {}) or {}
                                    ).get(tc.tool_name) or {}
                                    _verdict2 = check_tool_call(
                                        tc,
                                        cap=_caps2.get(tc.tool_name),
                                        used=_used2,
                                        grounded_spec=_spec2,
                                        messages=messages,
                                        stored_results=tool_cache.stored_results_by_tool(),
                                        session_grounded=self._session_grounded_values(
                                            bundle, _spec2),
                                        consent_ok=None,
                                        cache_lookup=tool_cache.lookup,
                                        requires_pending=self._requires_pending_for(
                                            tc.tool_name),
                                        pending_id=getattr(
                                            understanding, "pending_id", None),
                                    )
                                    if _verdict2.kind != "go":
                                        tool_result = _verdict2.result
                                    else:
                                        _turn_tool_counts[tc.tool_name] = _used2 + 1
                                        tool_result = await self._async_gateway.execute(
                                            tool_cache.prepare(tc), session_id, user_id,
                                            session_values=self._tool_session_values(bundle),
                                        )
                                        tool_result = self._result_shaper.shape(tool_result)
                                        await self._write_mapped_session_values(
                                            session_id, user_id, tool_result, bundle,
                                        )
                                        tool_cache.after_call(tc, tool_result)
                                        await self._persist_tool_cache(
                                            session_id, user_id, tool_cache,
                                        )
                            else:
                                break
                            _nested_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.tool_use_id,
                                "content": tool_result.result_text or str(tool_result.result),
                            })
                            _stream_tool_results.append(tool_result)
                        _ex = self._capture_tool_exchange(
                            _nested_tool_calls, _nested_results, _max_chars,
                        )
                        if _ex is not None:
                            _captured_exchanges_this_turn.append(_ex)
                        yield _stamp(SignalEvent(stage="tool_end", status="complete"))
                        if _aborted():
                            return
                        logger.info(
                            "  [STEP 9] Tool-Use Loop (round %d)  ✓  tools=%s",
                            _tool_round, _nested_tool_names,
                        )
                        _current_tool_calls = _nested_tool_calls
                        _current_tool_results = _nested_results

                model_used = self._llm.get_active_model()
                logger.info(
                    "  [STEP 8] LLM Stream Call #2  ✓  model_used=%s  latency=%dms",
                    model_used, int((time.time() - t8b) * 1000),
                )

                # Post-tool hook: apply_job success → post_applied transition
                # Mirror of the sync path hook (a pre-dispatched apply ran it
                # at Step 6c). Workflows without post_applied get a no-op.
                await self._post_applied_hook_async(
                    session_id, user_id, bundle, _stream_tool_results)

            # Flush remaining token buffer as a final sentence into the batcher,
            # then drain the batcher in one final Trust call (turn-end flush).
            # The final add() can itself trigger a flush (size or time); its
            # release must be spoken ahead of whatever flush() returns, or a
            # reply whose length is a multiple of max_sentences loses its
            # last batch. The tail is guarded (Spec D) before it reaches the
            # batcher, so that release is guarded text too.
            remaining = _guarded(token_buffer.strip())
            final_release: list[str] = []
            if remaining and not _trust_batcher.was_escalated:
                final_release = await _trust_batcher.add(remaining)
            yield _stamp(SignalEvent(stage="trust_output", status="start"))
            final_release += await _trust_batcher.flush()
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

            # A turn that reaches here with nothing to say leaves the caller
            # listening to silence on a phone line, with no way to tell whether
            # the bot is thinking or the call is dead. The empty-completion
            # retry above already had two attempts; this is the backstop for
            # when both came back empty.
            #
            # An escalated turn is deliberately withheld content, not an empty
            # one — Trust Layer speaks its own refusal, so leave it alone.
            if not sentence_index and not was_escalated:
                _empty_line = (self._config.get("conversation", {}) or {}).get(
                    "empty_response_message", ""
                ) or ""
                _empty_line = self._translate_consent_message(
                    _empty_line, detected_language,
                )
                logger.warning(
                    "orchestrator.stream_empty_turn",
                    extra={
                        "operation": "orchestrator.stream_turn",
                        "status": "degraded",
                        "session_id": session_id,
                        "subagent_id": current_subagent_id,
                        "recovered": bool(_empty_line),
                        # Sentences the model produced that never reached
                        # the caller (count only, never the text).
                        "dropped_sentences": _trust_batcher.sentences_added,
                    },
                )
                if _empty_line:
                    full_response_text = _empty_line
                    yield _stamp(SentenceEvent(
                        text=_empty_line, sentence_index=sentence_index,
                    ))
                    sentence_index += 1

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
            asyncio.create_task(self._async_memory.write(
                session_id, user_id, "session", RECENT_TURNS_KEY,
                append_recent_turn(bundle.session.get(RECENT_TURNS_KEY),
                                   caller=" ".join(record.segments or [turn_input.user_message]),
                                   bot=full_response_text, interrupted=False,
                                   history_turns=self._recent_keep)))

            # #193: persist captured tool exchanges (capped) so the next
            # turn can replay them as real tool_use/tool_result messages.
            _capped = self._merge_tool_exchanges(
                _prior_exchanges, _captured_exchanges_this_turn, _max_items,
            )
            # Spec §4.6: this turn completed, so replayed undelivered results
            # have now been spoken about — drop the flags, even with no new round.
            if _capped is None and _max_items > 0 and any(
                isinstance(ex, dict) and ex.get("delivered") is False for ex in _prior_exchanges
            ):
                _capped = list(_prior_exchanges)[-_max_items:]
            if _capped is not None:
                _capped = [
                    {k: v for k, v in ex.items() if k != "delivered"} if isinstance(ex, dict) else ex
                    for ex in _capped
                ]
            _served = self._served_tool_results_update(bundle, tool_cache)
            if _served is not None:
                asyncio.create_task(self._async_memory.write(
                    session_id, user_id, "session", SERVED_TOOL_RESULTS_KEY, _served))
            if _capped is not None:
                bundle.session["recent_tool_exchanges"] = _capped
                asyncio.create_task(
                    self._async_memory.write(
                        session_id, user_id, "session",
                        "recent_tool_exchanges", _capped,
                    )
                )
                logger.info(
                    "orchestrator.tool_persist",
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
            # GH-240 / GH-243: turn-level canonical sentence count. The
            # per-call "[STEP 8] LLM Stream Call #1 sentences=N" log fires
            # before the turn-end trust-batcher flush at line ~3402-3417,
            # so it under-counts when the final flush emits anything (and
            # on tool turns it omits Call #2 entirely). ``sentence_index``
            # at this point equals the total number of SentenceEvents
            # actually yielded for this turn — matches what the Reach
            # Layer counts as ``sentences_pushed``.
            logger.info(
                "orchestrator.stream_turn_complete",
                extra={
                    "operation": "orchestrator.stream_turn",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                    # GH-403: what the caller actually waits for. latency_ms is
                    # time to the LAST sentence; these two are time to the model
                    # starting and to the first sentence reaching the channel.
                    "llm_ttft_ms": _timings["llm_ttft_ms"],
                    "first_token_ms": _timings["first_token_ms"],
                    "first_sentence_ms": _timings["first_sentence_ms"],
                    "model": model_used,
                    "tool_used": was_tool_used,
                    "intent": nlu_result.intent,
                    "next_subagent_id": next_subagent_id,
                    "sentences_emitted": sentence_index,
                    "digits_rewritten": _guard_counts["digits_rewritten"],
                    "foreign_script_words": _guard_counts["foreign_script_words"],
                    # Spec E §10.
                    "llm_calls": _llm_calls,
                    "predispatch_tool": _pd.tool,
                    "predispatch_outcome": _pd.outcome,
                    "predispatch_ms": _pd.ms,
                },
            )
            record_output_guard(turn_input.channel, _guard_lang,
                                _guard_counts["digits_rewritten"], _guard_counts["foreign_script_words"])
            logger.info(
                "\n═══════════════════════════════════════════════════════════════\n"
                "  STREAM TURN COMPLETE  session=%s  intent=%s  tool_used=%s\n"
                "  model=%s  total_latency=%dms  next_subagent=%s  sentences=%d\n"
                "  llm_ttft=%sms  first_token=%sms  first_sentence=%sms\n"
                "  llm_calls=%s  predispatch_tool=%s  predispatch_outcome=%s  predispatch_ms=%s\n"
                "  %s\n"
                "  response: %r\n"
                "═══════════════════════════════════════════════════════════════",
                session_id, nlu_result.intent, was_tool_used,
                model_used, latency_ms, next_subagent_id, sentence_index,
                _timings["llm_ttft_ms"], _timings["first_token_ms"],
                _timings["first_sentence_ms"],
                _llm_calls, _pd.tool, _pd.outcome, _pd.ms,
                self._nlu_banner(understanding),
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
                    tool_calls=[*_pd_tool_calls, *all_tool_calls],
                    latency_ms=latency_ms,
                    timestamp_ms=turn_input.timestamp_ms,
                )
            )

        except Exception as e:
            logger.error(
                "orchestrator.stream_turn_error",
                exc_info=True,
                extra={
                    "operation": "orchestrator.stream_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            error_type = getattr(e, "error_type", None)
            if error_type is None:
                error_type = "api_error" if isinstance(e, ProviderAPIError) else "internal_server_error"
            error_message = SAFE_MESSAGES.get(error_type, DEFAULT_SAFE_MESSAGE)
            yield _stamp(DoneEvent(
                turn_id=turn_id,
                turn_status="abandoned",
                latency_ms=int((time.time() - start) * 1000),
                error_type=error_type,
                error_message=error_message,
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
        self.sentences_added: int = 0

    @property
    def has_pending(self) -> bool:
        """True while sentences are buffered awaiting a Trust /check/output.

        The empty-completion retry in ``stream_turn`` asks whether anything
        the model produced is still in flight. Sentences held here have not
        reached the caller yet, but they exist — retrying would produce them
        a second time.
        """
        return bool(self._buffer)

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
        self.sentences_added += 1
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

    def discard_pending(self) -> int:
        """Drop buffered sentences without releasing or Trust-checking them.

        For text superseded before it reached the caller.

        Returns:
            Number of sentences dropped.
        """
        dropped = len(self._buffer)
        self._buffer = []
        self._batch_start = None
        self.sentences_added -= dropped
        return dropped

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


_SENTENCE_SPLIT_KEEP_RE = re.compile("(" + _SENTENCE_SPLIT_RE.pattern + ")")


def _guard_per_sentence(text: str, guarded) -> str:
    """Guard ``text`` one sentence at a time, as the stream path does.

    Args:
        text: Full model reply.
        guarded: Per-sentence guard callable (may return "" to drop a sentence).

    Returns:
        The guarded sentences re-joined with their original separators.
    """
    parts = _SENTENCE_SPLIT_KEEP_RE.split(text)
    out: list[str] = []
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        g = guarded(sentence) if sentence.strip() else sentence
        if not g.strip():
            continue
        if out:
            out.append(parts[i - 1] if i else " ")
        out.append(g)
    return "".join(out)


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
