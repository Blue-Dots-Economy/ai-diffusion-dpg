"""Cross-block invariant checks for the DPG configuration.

Per-block Pydantic schemas can only see one block's data. Many real
runtime constraints span two or more blocks (e.g. ``intent_filters``
keys must name a routing intent the ``agent_core`` NLU can derive). Those rules
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


# Phase ordering, mirrored from phases_config.PHASES (formerly
# dev_kit.agent.accumulator.PHASES, now deleted) so the validator stays
# self-contained (no circular import). Index in this list determines whether
# a check is "applicable yet" — checks tied to a phase only fire when the LLM
# is leaving that phase or a later one. Keep this in sync if PHASES changes.
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


def _validate_recording(reach_layer_block: dict) -> list[str]:
    """Recording-specific cross-block rules.

    - recording.caller_id_hash_salt must be set when source != disabled
    - recording.store.s3.bucket must be set when store.backend == 's3'

    Args:
        reach_layer_block: The reach_layer domain config dict (top-level key
            ``reach_layer`` wrapping ``channels.voice.recording``).

    Returns:
        List of human-readable error strings, empty when all rules pass.
    """
    errors: list[str] = []
    rec = (reach_layer_block.get("reach_layer", {})
                            .get("channels", {})
                            .get("voice", {})
                            .get("recording", {}))
    if rec.get("source", "disabled") == "disabled":
        return errors
    if not rec.get("caller_id_hash_salt"):
        errors.append(
            "reach_layer.channels.voice.recording.caller_id_hash_salt must be set "
            "when recording.source is enabled"
        )
    store = rec.get("store", {})
    if store.get("backend") == "s3" and not (store.get("s3") or {}).get("bucket"):
        errors.append(
            "reach_layer.channels.voice.recording.store.s3.bucket must be set "
            "when store.backend == 's3'"
        )
    return errors


def _as_int(value: object, default: int, label: str, errors: list[str]) -> Optional[int]:
    """Coerce a config value to int, recording an error instead of raising.

    Args:
        value: Raw config value (``None``/falsy falls back to ``default``).
        default: Value used when ``value`` is missing or falsy.
        label: Config path used in the error message.
        errors: Error list appended to when ``value`` is not numeric.

    Returns:
        The integer, or ``None`` if ``value`` was not numeric.
    """
    if not value:
        return default
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        errors.append(f"{label}: '{value}' is not a number")
        return None


def _tool_result_agent_rules(ac: dict) -> list[str]:
    """Agent_core-only tool-result rules (mirror runtime ``MergedConfig._check_tool_result_rules``).

    Args:
        ac: The agent_core block.

    Returns:
        Error strings for user-scope TTLs above ``tool_results.max_user_ttl_seconds``,
        a ``memory_tool.name`` colliding with a connector, and ``grounded_in``
        entries that are not connector names.
    """
    errors: list[str] = []
    connectors = ac.get("connectors") or {}
    names = {c["name"] for g in ("read", "write", "identity", "internal")
             for c in (connectors.get(g) or []) if isinstance(c, dict) and c.get("name")}
    cap = _as_int((ac.get("tool_results") or {}).get("max_user_ttl_seconds"), 86400,
                  "tool_results.max_user_ttl_seconds", errors)
    for c in connectors.get("read") or []:
        cache = c.get("cache") if isinstance(c, dict) else None
        if not isinstance(cache, dict) or cache.get("scope") != "user" or cap is None:
            continue
        ttl = _as_int(cache.get("ttl_seconds"), 0, f"connectors.{c.get('name', '?')}.cache.ttl_seconds", errors)
        if ttl is not None and ttl > cap:
            errors.append(f"connector '{c.get('name', '?')}': user-scope ttl_seconds {ttl} "
                          f"exceeds tool_results.max_user_ttl_seconds {cap}")
    mt = ac.get("memory_tool")
    if isinstance(mt, dict):
        mt_name = mt.get("name") or "remember"
        if mt_name in names:
            errors.append(f"memory_tool.name '{mt_name}' collides with a connector")
        for fname, f in (mt.get("fields") or {}).items():
            for src in (f or {}).get("grounded_in") or []:
                if src not in names:
                    errors.append(f"memory_tool.fields.{fname}.grounded_in: unknown connector '{src}'")
    return errors


def _tool_result_memory_rules(ac: dict, ml: dict) -> list[str]:
    """Cross-block rules for tool-result caching and the memory tool (agent_core ↔ memory_layer).

    Args:
        ac: The agent_core block.
        ml: The memory_layer block.

    Returns:
        Error strings for session TTLs beyond the session lifetime, undeclared
        ``vary_on`` fields, and ``memory_tool`` fields missing from the session
        schema or ``UserProfile.declared_fields``.
    """
    errors: list[str] = []
    session = ((ml.get("state") or {}).get("session") or {})
    schema = session.get("schema") or {}
    minutes = _as_int(session.get("ttl_minutes"), 60, "memory_layer.state.session.ttl_minutes", errors)
    session_ttl = (minutes if minutes is not None else 60) * 60
    declared = (((((ml.get("state") or {}).get("persistent") or {}).get("graph") or {})
                 .get("subnodes") or {}).get("UserProfile") or {}).get("declared_fields") or []
    for group in (ac.get("connectors") or {}).values():
        for c in group or []:
            cache = (c or {}).get("cache") if isinstance(c, dict) else None
            if not isinstance(cache, dict):
                continue
            name = c.get("name", "?")
            ttl = _as_int(cache.get("ttl_seconds"), 0, f"connectors.{name}.cache.ttl_seconds", errors)
            if cache.get("scope") == "session" and ttl is not None and ttl > session_ttl:
                errors.append(f"connectors.{name}.cache.ttl_seconds exceeds the session lifetime "
                              f"({session_ttl}s from memory_layer.state.session.ttl_minutes)")
            for f in cache.get("vary_on") or []:
                if f not in schema:
                    errors.append(f"connectors.{name}.cache.vary_on: '{f}' is not a declared session field")
    for fname, f in (((ac.get("memory_tool") or {}).get("fields")) or {}).items():
        scope = (f or {}).get("scope")
        if scope == "session" and fname not in schema:
            errors.append(f"memory_tool.fields.{fname}: not declared in memory_layer session schema")
        if scope == "persistent" and fname not in declared:
            errors.append(f"memory_tool.fields.{fname}: not in UserProfile.declared_fields")
    return errors


def _session_bootstrap_rules(ac: dict, ml: dict) -> list[str]:
    """Cross-block rules for session bootstrap and prompt_session_fields (agent_core ↔ memory_layer)."""
    errors: list[str] = []
    schema = (((ml.get("state") or {}).get("session") or {}).get("schema")) or {}
    for f in ((ac.get("agent") or {}).get("prompt_session_fields")) or []:
        if f not in schema:
            errors.append(f"agent.prompt_session_fields: '{f}' is not a declared session field")
    read = {c.get("name") for c in ((ac.get("connectors") or {}).get("read") or []) if isinstance(c, dict)}
    for i, s in enumerate(((ac.get("session_bootstrap") or {}).get("steps")) or []):
        tool = (s or {}).get("tool") if isinstance(s, dict) else None
        if tool not in read:
            errors.append(f"session_bootstrap.steps[{i}]: '{tool}' is not a read connector")
    return errors


def _tool_result_session_mapping_rules(ac: dict, ag: dict) -> list[str]:
    """Reject user-scope caching of a connector whose tool declares ``session_mapping``.

    A cache hit returns stored data only: it carries no ``session_values``, so
    the tool's ``response.session_mapping`` runs only when the result is first
    fetched live. With session scope that is within the same session, where the
    mapped values are already in session state; with user scope a later session
    would get the hit but never the mapped values.

    Args:
        ac: The agent_core block.
        ag: The action_gateway block.

    Returns:
        One error string per user-scope cached connector whose matching
        action_gateway tool (by ``id`` or ``name``) declares
        ``response.session_mapping``.
    """
    errors: list[str] = []
    mapped: set[str] = set()
    for t in ag.get("tools") or []:
        if not isinstance(t, dict) or not ((t.get("response") or {}).get("session_mapping")):
            continue
        mapped.update(str(k) for k in (t.get("id"), t.get("name")) if k)
    for group in (ac.get("connectors") or {}).values():
        for c in group or []:
            if not isinstance(c, dict):
                continue
            cache = c.get("cache")
            name = c.get("name")
            if isinstance(cache, dict) and cache.get("scope") == "user" and name in mapped:
                errors.append(
                    f"connectors.{name}.cache.scope is 'user' but action_gateway tool '{name}' "
                    f"declares response.session_mapping. A cache hit does not replay "
                    f"session_mapping, so a later session would miss the mapped values. "
                    f"Use scope: session for this connector."
                )
    return errors


def _dialogue_act_session_mapping_rules(ac: dict, ag: dict) -> list[str]:
    """Reject dialogue-act NLU state keys that a connector session_mapping also writes.

    NLU slots (mapped through ``entity_to_profile_field``) and pending
    ``resolves_to`` keys are written by Agent Core's SlotWriter; a
    ``response.session_mapping`` target of the same name would make two
    writers race on one field (NLU dialogue-acts spec §7.3).

    Args:
        ac: The agent_core block.
        ag: The action_gateway block.

    Returns:
        One error per colliding key.
    """
    nlu = ((ac.get("preprocessing") or {}).get("nlu_processor")) or {}
    emap = ac.get("entity_to_profile_field") or {}
    keys = {emap.get(n, n) for n in (nlu.get("slots") or {})}
    for s in ((ac.get("agent_workflow") or {}).get("subagents")) or []:
        for p in (s or {}).get("pending") or []:
            if (p or {}).get("resolves_to"):
                keys.add(p["resolves_to"])
    errors: list[str] = []
    for t in ag.get("tools") or []:
        for m in ((t or {}).get("response") or {}).get("session_mapping") or []:
            target = (m or {}).get("target")
            if target in keys:
                errors.append(
                    f"action_gateway tool '{t.get('id') or t.get('name')}' session_mapping target "
                    f"'{target}' is also a dialogue_act NLU state key; rename one of them.")
    return errors


_FRAMEWORK_HANDLED_INTENTS: frozenset[str] = frozenset({"language_switch_request", "human_request"})
"""Mirrors runtime ``_FRAMEWORK_HANDLED_INTENTS``: derived but never routed."""

_DEFAULT_OFF_TRACK_INTENT = "off_track"
_ANY_INPUT_INTENT = "any_input"


def _nlu_block(ac: dict) -> dict:
    nlu = (ac.get("preprocessing") or {}).get("nlu_processor") or {}
    return nlu if isinstance(nlu, dict) else {}


def _off_track_intent(nlu: dict) -> str:
    off_track = nlu.get("off_track") or {}
    if isinstance(off_track, dict) and off_track.get("intent"):
        return str(off_track["intent"])
    return _DEFAULT_OFF_TRACK_INTENT


def _act_intent_rows(nlu: dict) -> list[dict]:
    return [r for r in (nlu.get("act_intents") or []) if isinstance(r, dict)]


def _dialogue_act_routing_rules(ac: dict) -> list[str]:
    """Mirror runtime ``MergedConfig._check_dialogue_act_rules`` routing checks.

    Every ``act_intents`` intent must be used by a subagent routing rule or
    by ``agent_workflow.global_routing`` (``language_switch_request`` and
    ``human_request`` are framework-handled), and the off-track intent must be routed whenever
    the workflow has subagents. Messages match the runtime, prefixed with
    ``agent_core.``.

    Args:
        ac: The ``agent_core`` block dict.

    Returns:
        One error string per violation; empty when consistent.
    """
    nlu = _nlu_block(ac)
    workflow = ac.get("agent_workflow") or {}
    subagents = [s for s in (workflow.get("subagents") or []) if isinstance(s, dict)]
    routed: set[str] = {
        r.get("intent")
        for s in subagents
        for r in (s.get("routing") or [])
        if isinstance(r, dict)
    } | {r.get("intent") for r in (workflow.get("global_routing") or []) if isinstance(r, dict)}

    errors: list[str] = []
    for i, row in enumerate(_act_intent_rows(nlu)):
        intent = row.get("intent")
        if intent and intent not in routed and intent not in _FRAMEWORK_HANDLED_INTENTS:
            errors.append(
                f"agent_core.preprocessing.nlu_processor.act_intents[{i}]: intent '{intent}' "
                f"is not used by any routing rule"
            )
    off_track = _off_track_intent(nlu)
    if subagents and off_track not in routed:
        errors.append(
            f"agent_core.preprocessing.nlu_processor.off_track.intent '{off_track}' "
            f"is not used by any routing rule"
        )
    return errors


def _intent_filter_rules(ac: dict, ke: dict) -> list[str]:
    """Check KE ``intent_filters`` keys against the routing intent set the NLU can derive.

    The routing intent on a turn is an ``act_intents`` intent, ``any_input``,
    the off-track intent, ``language_switch_request`` or ``human_request``; a filter keyed on
    anything else never matches. Self-guards until ``act_intents`` is
    authored (it is hand-written in ``agent_core.yaml`` for now, spec §16).

    Args:
        ac: The ``agent_core`` block dict.
        ke: The ``knowledge_engine`` block dict.

    Returns:
        One error string per unknown key; empty when consistent.
    """
    nlu = _nlu_block(ac)
    rows = _act_intent_rows(nlu)
    if not rows:
        return []
    intent_filters = (
        ((ke.get("knowledge") or {}).get("blocks") or {})
        .get("static_knowledge_base", {})
        .get("intent_filters") or {}
    )
    derivable = (
        {r["intent"] for r in rows if r.get("intent")}
        | {_ANY_INPUT_INTENT, _off_track_intent(nlu)}
        | _FRAMEWORK_HANDLED_INTENTS
    )
    return [
        f"knowledge_engine.intent_filters key '{key}' is not an intent the NLU can derive "
        f"(an agent_core.preprocessing.nlu_processor.act_intents row intent, '{_ANY_INPUT_INTENT}', "
        f"the off-track intent, 'language_switch_request' or 'human_request'). Queries for this key never "
        f"match; rename it or remove it. Known: {sorted(derivable)}"
        for key in intent_filters
        if key not in derivable
    ]


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

    declared_subagent_ids: set[str] = {
        sa["id"]
        for sa in (workflow.get("subagents") or [])
        if isinstance(sa, dict) and sa.get("id")
    }

    # 1. Tool names in global_tools exist in connectors (skip MCP-namespaced).
    # Tied to the workflow phase — global_tools is populated there.
    if applicable_after("workflow"):
        for tool in global_tools:
            if tool not in declared_connectors and "__" not in tool:
                errors.append(
                    f"agent_core.agent_workflow.global_tools: '{tool}' is not declared "
                    f"in any connectors.* list. Declared connectors: {sorted(declared_connectors)}"
                )

    # 2. Per-subagent tool names must be declared.
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

    # 3. Every act_intents row intent and the off-track intent must be routed
    # (mirrors the runtime _check_dialogue_act_rules). Tied to the workflow phase.
    if applicable_after("workflow"):
        errors.extend(_dialogue_act_routing_rules(ac))

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

    # 5. intent_filters keys must name a routing intent the NLU can derive.
    # Tied to the knowledge phase (intent_filters is configured there).
    if applicable_after("knowledge"):
        errors.extend(_intent_filter_rules(ac, ke))

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

    # 13b. Tool-result cache / memory tool vs memory_layer session + profile.
    if applicable_after("tools"):
        errors.extend(_tool_result_memory_rules(ac, blocks.get("memory_layer") or {}))
        errors.extend(_tool_result_agent_rules(ac))
        errors.extend(_session_bootstrap_rules(ac, blocks.get("memory_layer") or {}))
        # 13c. User-scope cache is unsafe where the tool declares session_mapping.
        errors.extend(_tool_result_session_mapping_rules(ac, blocks.get("action_gateway") or {}))
        # 13d. dialogue_act state keys must not collide with session_mapping targets.
        errors.extend(_dialogue_act_session_mapping_rules(ac, blocks.get("action_gateway") or {}))

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

    # 16. Recording cross-block rules (tied to the reach phase).
    if applicable_after("reach"):
        errors.extend(_validate_recording(rl))

    return errors
