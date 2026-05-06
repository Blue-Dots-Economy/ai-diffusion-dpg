"""Cross-block invariant checks for the DPG configuration.

Per-block Pydantic schemas can only see one block's data. Many real
runtime constraints span two or more blocks (e.g. ``intent_filters``
keys must reference real NLU intents in ``agent_core``). Those rules
live here so they can run from two places:

1. Inside the LLM tool loop, on every ``set_phase`` advance, so the
   LLM sees the inconsistency in the same conversation that produced
   it and can self-correct.
2. From the Deploy Wizard's pre-deploy validate step, as a final
   safety net before ops pushes the config.

The invariants self-guard against incomplete data — checks only fire
when the source data is populated. So calling this mid-flow against
the accumulator state is safe; checks irrelevant to the current phase
silently pass.
"""
from __future__ import annotations

from typing import Iterable, Optional


# Phase ordering, mirrored from dev_kit.agent.accumulator.PHASES so the
# validator stays self-contained (no circular import). Index in this list
# determines whether a check is "applicable yet" — checks tied to a phase
# only fire when the LLM is leaving that phase or a later one. Keep this
# in sync if PHASES changes.
_PHASES: list[str] = [
    "tier",
    "overview",
    "language",
    "knowledge",
    "memory",
    "user_state",
    "trust",
    "tools",
    "workflow",
    "observability",
    "reach",
    "review",
]


def _phase_index(phase: Optional[str]) -> int:
    """Return the ordinal index of `phase` in _PHASES, or len(_PHASES) when
    None / unknown — treating "no phase context" (e.g. deploy-time validate)
    as "all phases complete" so every invariant runs."""
    if phase is None:
        return len(_PHASES)
    try:
        return _PHASES.index(phase)
    except ValueError:
        return len(_PHASES)


def validate_cross_block(
    blocks: dict[str, dict],
    selected_channels: Iterable[str],
    current_phase: Optional[str] = None,
) -> list[str]:
    """Run every cross-block invariant on the supplied block dict.

    Args:
        blocks: Mapping of block name (e.g. ``"agent_core"``) to that
            block's domain config dict. Missing blocks are treated as
            empty dicts.
        selected_channels: Channel names selected for this deployment
            (e.g. ``["web", "voice"]``). Drives channel-related checks.
        current_phase: When called from ``set_phase``, the phase the LLM
            is currently leaving. Each invariant is gated by an "earliest
            applicable phase" so checks don't fire prematurely (e.g.
            channel-shape checks must not fire while leaving overview —
            those fields are configured in the language/reach phases).
            Pass ``None`` (the default) at deploy time to run every
            invariant, regardless of phase.

    Returns:
        List of human-readable error strings, one per failed invariant
        whose earliest-applicable phase has been reached. Empty list
        means every applicable invariant passed (or had no data to check).
    """
    phase_idx = _phase_index(current_phase)

    def applicable_after(phase_name: str) -> bool:
        """True if `current_phase` >= `phase_name` (or no phase context)."""
        return phase_idx >= _phase_index(phase_name)

    errors: list[str] = []
    ac = blocks.get("agent_core") or {}
    ke = blocks.get("knowledge_engine") or {}
    rl = blocks.get("reach_layer") or {}
    tl = blocks.get("trust_layer") or {}
    selected = list(selected_channels)

    connectors = ac.get("connectors") or {}
    declared_connectors: set[str] = set()
    for category in ("read", "write", "identity", "internal"):
        for c in connectors.get(category) or []:
            if isinstance(c, dict) and c.get("name"):
                declared_connectors.add(c["name"])
    internal_connectors: set[str] = {
        c["name"]
        for c in (connectors.get("internal") or [])
        if isinstance(c, dict) and c.get("name")
    }

    workflow = ac.get("agent_workflow") or {}
    global_tools: list[str] = workflow.get("global_tools") or []
    global_intents: set[str] = set(workflow.get("global_intents") or [])

    declared_subagent_ids: set[str] = {
        sa["id"]
        for sa in (workflow.get("subagents") or [])
        if isinstance(sa, dict) and sa.get("id")
    }
    all_subagent_intents: set[str] = set()
    for sa in workflow.get("subagents") or []:
        if isinstance(sa, dict):
            for intent in sa.get("valid_intents") or []:
                all_subagent_intents.add(intent)

    # 1. Tool names in global_tools exist in connectors (skip MCP-namespaced).
    # Tied to the workflow phase — global_tools is populated there.
    if applicable_after("workflow"):
        for tool in global_tools:
            if tool not in declared_connectors and "__" not in tool:
                errors.append(
                    f"agent_core.agent_workflow.global_tools: '{tool}' is not declared "
                    f"in any connectors.* list. Declared connectors: {sorted(declared_connectors)}"
                )

    # 2 & 3. Per-subagent tool names must be declared; global vs subagent intents must not overlap.
    if applicable_after("workflow"):
        for sa in workflow.get("subagents") or []:
            if not isinstance(sa, dict):
                continue
            sa_id = sa.get("id", "?")
            for tool in sa.get("tools") or []:
                if tool not in declared_connectors and "__" not in tool:
                    errors.append(
                        f"agent_core.agent_workflow.subagents[{sa_id}].tools: '{tool}' is not "
                        f"declared in any connectors.* list. Declared connectors: {sorted(declared_connectors)}"
                    )
        overlap = global_intents & all_subagent_intents
        if overlap:
            errors.append(
                f"agent_core: intents {sorted(overlap)} appear in both global_intents and a "
                f"subagent's valid_intents. Agent Core crashes at startup if there is any overlap."
            )

    # 4. knowledge_retrieval must be in connectors.internal (not connectors.read).
    # Tied to tools phase (when connectors.internal is populated) but only
    # actually fires once a subagent or global_tools references it.
    all_tool_names = set(global_tools)
    for sa in workflow.get("subagents") or []:
        if isinstance(sa, dict):
            all_tool_names.update(sa.get("tools") or [])
    if applicable_after("tools") and "knowledge_retrieval" in all_tool_names and "knowledge_retrieval" not in internal_connectors:
        read_names = {c["name"] for c in (connectors.get("read") or []) if isinstance(c, dict)}
        if "knowledge_retrieval" in read_names:
            errors.append(
                "agent_core: 'knowledge_retrieval' is in connectors.read but must be in "
                "connectors.internal (it routes to Knowledge Engine, not Action Gateway). "
                "Move the connector to connectors.internal and add 'route: knowledge_engine'."
            )
        else:
            errors.append(
                "agent_core: 'knowledge_retrieval' is referenced in tools but not declared "
                "in connectors.internal. Add it under connectors.internal with route: knowledge_engine."
            )

    # 5. intent_filters keys must be in NLU intents.
    # Tied to the knowledge phase (intent_filters is configured there).
    nlu_intents: set[str] = set(
        (ac.get("preprocessing") or {}).get("nlu_processor", {}).get("intents") or []
    )
    intent_filters: dict = (
        (ke.get("knowledge") or {})
        .get("blocks", {})
        .get("static_knowledge_base", {})
        .get("intent_filters") or {}
    )
    if applicable_after("knowledge"):
        for intent_key in intent_filters:
            if intent_key not in nlu_intents:
                errors.append(
                    f"knowledge_engine.intent_filters key '{intent_key}' is not declared in "
                    f"agent_core.preprocessing.nlu_processor.intents. Queries for this intent "
                    f"will bypass the filter. Add '{intent_key}' to the NLU intents list."
                )

    # 6. Voice selected → reach_layer.channels.voice fully configured.
    # Tied to the reach phase — the voice channel is configured there.
    if applicable_after("reach") and "voice" in selected:
        voice_cfg = ((rl.get("reach_layer") or {}).get("channels") or {}).get("voice")
        if not voice_cfg or not isinstance(voice_cfg, dict):
            errors.append(
                "reach_layer.channels.voice is not configured but voice is in selected_channels. "
                "Set reach_layer.channels.voice with raya.voice_id, raya.stt_language, and raya.tts_language."
            )
        else:
            raya = voice_cfg.get("raya") or {}
            for field in ("voice_id", "stt_language", "tts_language"):
                if not raya.get(field):
                    errors.append(
                        f"reach_layer.channels.voice.raya.{field} is empty but voice is in selected_channels."
                    )
            if not voice_cfg.get("terminal_word"):
                errors.append(
                    "reach_layer.channels.voice.terminal_word is not set but voice is in selected_channels. "
                    "The voice session never ends without a terminal word (e.g. 'goodbye'). "
                    "Set reach_layer.channels.voice.terminal_word."
                )

    # 7. Each selected channel must have an agent_core.channels.<x> entry.
    # Tied to the language phase — agent_core.channels.<name> is configured there.
    ac_channels = ac.get("channels") or {}
    if applicable_after("language"):
        for ch in selected:
            if ch not in ac_channels:
                errors.append(
                    f"agent_core.channels.{ch} is missing but '{ch}' is in selected_channels. "
                    f"Agent Core raises ValueError: Unsupported channel at startup. "
                    f"Add a channels.{ch} block with system_prompt_suffix and turn_assembler settings."
                )

    # 8. Each selected channel must have a non-null reach_layer.channels.<x> entry.
    # Tied to the reach phase — reach_layer.channels.<name> is configured there.
    rl_channels = (rl.get("reach_layer") or {}).get("channels") or {}
    if applicable_after("reach"):
        for ch in selected:
            if rl_channels.get(ch) is None:
                errors.append(
                    f"reach_layer.channels.{ch} is null/missing but '{ch}' is in selected_channels. "
                    f"The reach layer service will fail to start. Add a reach_layer.channels.{ch} block."
                )

    # 9. Every non-terminal subagent must have a non-empty opening_phrase.
    # Tied to workflow phase.
    if applicable_after("workflow"):
        for sa in workflow.get("subagents") or []:
            if not isinstance(sa, dict):
                continue
            sa_id = sa.get("id", "?")
            if not sa.get("is_terminal") and not (sa.get("opening_phrase") or "").strip():
                errors.append(
                    f"agent_core.agent_workflow.subagents[{sa_id}].opening_phrase is empty. "
                    f"Every non-terminal subagent must have an opening_phrase — it is emitted "
                    f"on the first turn the session enters this subagent."
                )

    # 10. default_fallback_subagent_id must match a declared subagent id.
    if applicable_after("workflow"):
        fallback_id = (workflow.get("default_fallback_subagent_id") or "").strip()
        if fallback_id and fallback_id not in declared_subagent_ids:
            errors.append(
                f"agent_core.agent_workflow.default_fallback_subagent_id: '{fallback_id}' is not "
                f"declared in subagents (declared: {sorted(declared_subagent_ids)}). "
                f"Agent Core will raise KeyError when the fallback is triggered."
            )

    # 11. Every routing[*].next_subagent_id must match a declared subagent id.
    if applicable_after("workflow"):
        for rule in (workflow.get("global_routing") or []):
            if not isinstance(rule, dict):
                continue
            next_id = (rule.get("next_subagent_id") or "").strip()
            if next_id and next_id not in declared_subagent_ids:
                errors.append(
                    f"agent_core.agent_workflow.global_routing: next_subagent_id '{next_id}' "
                    f"is not declared in subagents (declared: {sorted(declared_subagent_ids)})."
                )
        for sa in (workflow.get("subagents") or []):
            if not isinstance(sa, dict):
                continue
            sa_id = sa.get("id", "?")
            for rule in (sa.get("routing") or []):
                if not isinstance(rule, dict):
                    continue
                next_id = (rule.get("next_subagent_id") or "").strip()
                if next_id and next_id not in declared_subagent_ids:
                    errors.append(
                        f"agent_core.agent_workflow.subagents[{sa_id}].routing: "
                        f"next_subagent_id '{next_id}' is not declared in subagents "
                        f"(declared: {sorted(declared_subagent_ids)})."
                    )

    # 12. workflow.workflow_id and agent_system_prompt must be non-empty (only after workflow exists).
    if applicable_after("workflow") and workflow:
        for field in ("workflow_id", "agent_system_prompt"):
            if not (workflow.get(field) or "").strip():
                errors.append(
                    f"agent_core.agent_workflow.{field} is empty. "
                    f"This is a required field — Agent Core fails Pydantic validation at startup."
                )

    # 13. trust_layer.dignity_check.questions must be non-empty when enabled.
    # Tied to the trust phase.
    if applicable_after("trust"):
        dignity = tl.get("dignity_check") or {}
        if dignity.get("enabled") and not dignity.get("questions"):
            errors.append(
                "trust_layer.dignity_check.enabled is true but questions is empty. "
                "The dignity check will always pass with no questions — add the 5 canonical "
                "questions: ['Does this blame the user?', 'Does it over-promise?', "
                "'Does it push urgency?', 'Does it reduce their agency?', "
                "'Does it sound like a script instead of a human call?']"
            )

    # 14. Connector input_schema property names MUST match the action_gateway
    # tool's agent-source param names. The REST adapter passes the LLM's
    # parameters verbatim into the HTTP request — if the connector exposes
    # a renamed key, the LLM's call hits the API with the wrong param and
    # silently fails or 4xxs.
    # Tied to the tools phase — connectors and action_gateway tools are
    # both populated there.
    if not applicable_after("tools"):
        return errors
    ag_tools_by_id: dict[str, dict] = {
        t["id"]: t
        for t in (blocks.get("action_gateway") or {}).get("tools") or []
        if isinstance(t, dict) and t.get("id") and t.get("type") == "rest_api"
    }
    for category in ("read", "write", "identity"):
        for c in connectors.get(category) or []:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not name or name not in ag_tools_by_id:
                # Connector with no matching action_gateway tool — out of scope
                # for this check (might be an internal route handled elsewhere).
                continue
            tool = ag_tools_by_id[name]
            connector_props: set[str] = set(
                ((c.get("input_schema") or {}).get("properties") or {}).keys()
            )
            tool_agent_params: set[str] = set()
            for endpoint in tool.get("endpoints") or []:
                for p in endpoint.get("params") or []:
                    if isinstance(p, dict) and p.get("source") == "agent" and p.get("name"):
                        tool_agent_params.add(p["name"])
            extra_in_connector = connector_props - tool_agent_params
            missing_from_connector = {
                p for p in tool_agent_params
                if p not in connector_props
                and any(
                    isinstance(ep.get("params"), list)
                    and any(
                        pp.get("name") == p and pp.get("required")
                        for pp in ep.get("params") or []
                        if isinstance(pp, dict)
                    )
                    for ep in tool.get("endpoints") or []
                )
            }
            if extra_in_connector:
                errors.append(
                    f"agent_core.connectors.{category}[name={name!r}].input_schema.properties "
                    f"has keys {sorted(extra_in_connector)} that are NOT declared as "
                    f"agent-source params in action_gateway.tools[id={name!r}]. The REST "
                    f"adapter forwards the LLM's params verbatim to the HTTP API, so a "
                    f"renamed connector key (e.g. `city_name` instead of the tool's "
                    f"`name`) will be sent as `?city_name=...` and the API will not "
                    f"recognise it. Either rename the connector key to match the tool, "
                    f"or rename the tool's agent-source param to match the connector."
                )
            if missing_from_connector:
                errors.append(
                    f"agent_core.connectors.{category}[name={name!r}].input_schema.properties "
                    f"is missing required tool params {sorted(missing_from_connector)} "
                    f"declared with source=agent in action_gateway.tools[id={name!r}]. "
                    f"The LLM cannot supply these via the connector."
                )

    # 15. Every intent referenced by the workflow MUST already be declared in
    # nlu_processor.intents. Without this check, the renderer silently unions
    # subagent valid_intents into the NLU intents list — which means new
    # intents enter the config without the user ever approving them.
    # Tied to the workflow phase.
    if applicable_after("workflow") and workflow:
        workflow_intents: set[str] = set(global_intents) | all_subagent_intents
        workflow_intents.discard("other")
        workflow_intents.discard("*")
        missing_from_nlu = workflow_intents - nlu_intents
        if missing_from_nlu:
            errors.append(
                f"agent_core.agent_workflow references intents {sorted(missing_from_nlu)} "
                f"that are NOT declared in agent_core.preprocessing.nlu_processor.intents. "
                f"NLU intents are signed off by the user in the language phase; introducing "
                f"new ones in the workflow phase is silent expansion. If you genuinely need "
                f"a new intent, ask the user first, then add it to "
                f"preprocessing.nlu_processor.intents AND the subagent's valid_intents in "
                f"the same response. Otherwise, rename the subagent intent to match an "
                f"existing NLU intent."
            )

    return errors
