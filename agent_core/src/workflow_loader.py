"""
agent_core/workflow_loader.py

Parses and validates the ``agent_workflow`` configuration block at startup.

Belongs to the Agent Core DPG block. Produces an ``AgentWorkflow`` object that
is the single authoritative representation of the multi-subagent workflow for
the lifetime of the process. All subagent routing, tool scoping, and NLU
intent scoping decisions are derived from this object at turn time.

No I/O occurs after load — all pre-computation (intent sets, tool definition
slices) is performed once here so hot-path code does only O(1) dict lookups.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.exceptions import ConfigurationError
from src.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# The framework-built-in tool name that has no Action Gateway definition and
# must be excluded from registry validation and tool_defs pre-computation.
# No longer needs hardcoded built-in tools as they are now handled via ToolRegistry

# Valid operators for RoutingCondition
_VALID_OPERATORS = {"eq", "not_eq", "in", "lt", "gt"}

# Valid special_handler values (None is also valid)
_VALID_SPECIAL_HANDLERS = {"hitl", "whatsapp_handoff"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RoutingCondition:
    """
    A single predicate evaluated against a session field at routing time.

    Attributes:
        field:    Session field name to evaluate.
        operator: Comparison operator — one of "eq", "not_eq", "in", "lt", "gt".
        value:    Scalar value or list to compare the field value against.
    """

    field: str
    operator: str
    value: Any


@dataclass
class RoutingRule:
    """
    A single routing decision that maps an intent (or catch-all) to the next subagent.

    Attributes:
        intent:           Intent name to match, or ``"*"`` to match any intent.
        next_subagent_id: ID of the destination subagent.
        condition:        Optional single condition that must be true for this rule to fire.
        conditions:       Optional list of conditions — ALL must be true for this rule to fire.
        session_writes:   Optional dict of session field/value pairs written to session when
                          this rule fires. Allows domain.yaml routing rules to update session
                          state without any domain-specific logic in orchestrator code.
    """

    intent: str
    next_subagent_id: str
    condition: RoutingCondition | None = None
    conditions: list[RoutingCondition] = field(default_factory=list)
    session_writes: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubAgent:
    """
    Configuration for a single subagent node in the workflow graph.

    Attributes:
        id:               Unique identifier for this subagent within the workflow.
        name:             Human-readable display name.
        description:      Short description of this subagent's role.
        is_start:         True if this subagent is the entry point for new sessions.
        is_terminal:      True if this subagent ends the conversation (no outbound routing).
        special_handler:  Optional framework handler — "hitl", "whatsapp_handoff", or None.
        valid_intents:    Intents this subagent is responsible for handling.
        tools:            Tool names available to this subagent.
        system_prompt:    System prompt injected for LLM calls in this subagent.
        output_format:    JSON schema for structured output validation, or None.
        routing:          Routing rules emitted from this subagent.
        opening_phrase:   Optional opening phrase spoken/displayed when entering this subagent.
    """

    id: str
    name: str
    description: str
    is_start: bool
    is_terminal: bool
    special_handler: str | None
    valid_intents: list[str]
    tools: list[str]
    system_prompt: str
    output_format: dict | None
    routing: list[RoutingRule]
    opening_phrase: str = ""


@dataclass
class AgentWorkflow:
    """
    Immutable, fully parsed and pre-computed representation of the multi-subagent
    workflow graph for a single deployment.

    Pre-computed fields (``nlu_intent_set``, ``tool_defs``, ``global_tool_defs``)
    are populated by :class:`AgentWorkflowLoader` at startup.

    Attributes:
        workflow_id:                Unique identifier for this workflow.
        version:                    SemVer string for this workflow.
        agent_system_prompt:        Top-level system prompt shared across subagents.
        global_intents:             Intents handled globally before subagent routing.
        global_routing:             Routing rules applied globally after intent classification.
        default_fallback_subagent_id: Subagent to route to when no routing rule matches.
        subagents:                  All subagents keyed by their id for O(1) lookup.
        start_subagent_id:          ID of the subagent with ``is_start=True``.
        nlu_intent_set:             Per-subagent scoped intent list (subagent + global intents).
        tool_defs:                  Per-subagent tool definition slices (excludes
                                    built-in ``knowledge_retrieval``).
        global_tool_defs:           Shared tool-def list applied to every subagent when
                                    non-empty. Empty means fall back to per-subagent
                                    ``tool_defs``. Validated against the registry.
    """

    workflow_id: str
    version: str
    agent_system_prompt: str
    global_intents: list[str]
    global_routing: list[RoutingRule]
    default_fallback_subagent_id: str
    subagents: dict[str, SubAgent]
    start_subagent_id: str
    nlu_intent_set: dict[str, list[str]] = field(default_factory=dict)
    tool_defs: dict[str, list[dict]] = field(default_factory=dict)
    global_tool_defs: list[dict] = field(default_factory=list)

    def resolve_tools_for(self, subagent_id: str) -> list[dict]:
        """Return the tool definitions to inject into the LLM call for a subagent.

        When ``global_tool_defs`` is non-empty, it takes precedence and every
        subagent sees the same tool set (KKB behaviour). Otherwise the per-subagent
        ``tool_defs`` slice is returned, or an empty list if the subagent is unknown.

        Args:
            subagent_id: Subagent id whose tool set is being assembled.

        Returns:
            List of Anthropic-shaped tool definition dicts.
        """
        if self.global_tool_defs:
            return self.global_tool_defs
        return self.tool_defs.get(subagent_id, [])


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class AgentWorkflowLoader:
    """
    Parses and validates the ``agent_workflow`` block from the domain config dict.

    Instantiate once at startup and call ``load()`` with the full config. The
    returned ``AgentWorkflow`` is immutable for the lifetime of the process.
    """

    def load(self, config: dict, tool_registry: ToolRegistry) -> AgentWorkflow:
        """
        Parse the ``agent_workflow`` block from config and return a validated workflow.

        Performs all 7 structural validation checks and pre-computes per-subagent
        intent sets and tool definition slices. Never returns a partially-valid
        workflow — raises ``ConfigurationError`` at the first detected violation.

        Args:
            config:        Full domain configuration dict. Must contain
                           ``config["agent_workflow"]`` and
                           ``config["preprocessing"]["nlu_processor"]["intents"]``.
            tool_registry: Initialised ToolRegistry used to validate tool names and
                           fetch tool definitions.

        Returns:
            A fully validated ``AgentWorkflow`` instance with pre-computed fields.

        Raises:
            ValueError:          If ``config`` or ``tool_registry`` is None.
            ConfigurationError:  If any validation rule fails or a required config
                                 key is missing.
        """
        if config is None:
            raise ValueError("config must not be None")
        if tool_registry is None:
            raise ValueError("tool_registry must not be None")

        workflow_cfg = config.get("agent_workflow")
        if not workflow_cfg:
            raise ConfigurationError(
                "config is missing required key 'agent_workflow'"
            )

        # ------------------------------------------------------------------
        # Parse top-level scalar fields
        # ------------------------------------------------------------------
        workflow_id = workflow_cfg.get("workflow_id", "")
        if not workflow_id:
            raise ConfigurationError(
                "agent_workflow.workflow_id is required and must not be empty"
            )

        version = workflow_cfg.get("version", "")
        if not version:
            raise ConfigurationError(
                "agent_workflow.version is required and must not be empty"
            )

        agent_system_prompt = workflow_cfg.get("agent_system_prompt", "")
        global_intents: list[str] = workflow_cfg.get("global_intents") or []
        default_fallback_subagent_id = workflow_cfg.get("default_fallback_subagent_id", "")

        global_tools_raw: list[str] = workflow_cfg.get("global_tools") or []
        if not isinstance(global_tools_raw, list) or not all(isinstance(t, str) for t in global_tools_raw):
            raise ConfigurationError(
                "agent_workflow.global_tools must be a list of tool name strings"
            )

        # ------------------------------------------------------------------
        # Parse global routing rules
        # ------------------------------------------------------------------
        global_routing_raw: list[dict] = workflow_cfg.get("global_routing") or []
        global_routing: list[RoutingRule] = [
            self._parse_routing_rule(r, context="global_routing")
            for r in global_routing_raw
        ]

        # ------------------------------------------------------------------
        # Parse subagents
        # ------------------------------------------------------------------
        subagents_raw: list[dict] = workflow_cfg.get("subagents") or []
        if not subagents_raw:
            raise ConfigurationError(
                "agent_workflow.subagents is required and must contain at least one subagent"
            )

        subagents: dict[str, SubAgent] = {}
        for raw in subagents_raw:
            subagent = self._parse_subagent(raw)
            if subagent.id in subagents:
                raise ConfigurationError(
                    f"Duplicate subagent id '{subagent.id}' in agent_workflow.subagents"
                )
            subagents[subagent.id] = subagent

        # ------------------------------------------------------------------
        # Collect NLU intents from preprocessing config
        # ------------------------------------------------------------------
        all_nlu_intents: set[str] = self._load_nlu_intents(config)

        # ------------------------------------------------------------------
        # Run all 7 validation rules
        # ------------------------------------------------------------------
        start_subagent_id = self._validate_exactly_one_start(subagents)
        self._validate_routing_references(subagents, global_routing)
        internal_tool_names = {
            c.get("name") for c in
            config.get("connectors", {}).get("internal", [])
            if c.get("name")
        }
        self._validate_tool_names(subagents, tool_registry, internal_tool_names)
        self._validate_global_tool_names(global_tools_raw, tool_registry)
        self._validate_subagent_intents(subagents, all_nlu_intents)
        self._validate_global_intents_not_in_subagents(subagents, global_intents)
        self._validate_terminal_routing(subagents)
        self._validate_nonterminal_routing(subagents)

        # ------------------------------------------------------------------
        # Pre-compute per-subagent intent sets and tool definitions
        # ------------------------------------------------------------------
        nlu_intent_set: dict[str, list[str]] = self._build_nlu_intent_set(
            subagents, global_intents
        )
        tool_defs: dict[str, list[dict]] = self._build_tool_defs(
            subagents, tool_registry
        )
        global_tool_defs: list[dict] = self._build_global_tool_defs(
            global_tools_raw, tool_registry
        )

        # ------------------------------------------------------------------
        # Assemble and return
        # ------------------------------------------------------------------
        workflow = AgentWorkflow(
            workflow_id=workflow_id,
            version=version,
            agent_system_prompt=agent_system_prompt,
            global_intents=global_intents,
            global_routing=global_routing,
            default_fallback_subagent_id=default_fallback_subagent_id,
            subagents=subagents,
            start_subagent_id=start_subagent_id,
            nlu_intent_set=nlu_intent_set,
            tool_defs=tool_defs,
            global_tool_defs=global_tool_defs,
        )

        logger.info(
            "workflow_loader.load",
            extra={
                "operation": "workflow_loader.load",
                "status": "success",
                "workflow_id": workflow.workflow_id,
                "subagent_count": len(workflow.subagents),
            },
        )

        return workflow

    # ------------------------------------------------------------------
    # Private parse helpers
    # ------------------------------------------------------------------

    def _parse_routing_condition(
        self, raw: dict, context: str
    ) -> RoutingCondition:
        """
        Parse a single routing condition dict into a ``RoutingCondition``.

        Args:
            raw:     Raw dict from config with keys ``field``, ``operator``, ``value``.
            context: Human-readable path string for error messages.

        Returns:
            Parsed ``RoutingCondition``.

        Raises:
            ConfigurationError: If any required key is missing or operator is invalid.
        """
        cond_field = raw.get("field")
        if not cond_field:
            raise ConfigurationError(
                f"{context}: routing condition is missing required 'field' key"
            )

        operator = raw.get("operator")
        if not operator:
            raise ConfigurationError(
                f"{context}: routing condition is missing required 'operator' key"
            )
        if operator not in _VALID_OPERATORS:
            raise ConfigurationError(
                f"{context}: routing condition operator '{operator}' is invalid. "
                f"Must be one of: {sorted(_VALID_OPERATORS)}"
            )

        # value is intentionally allowed to be falsy (e.g. 0, False, "")
        if "value" not in raw:
            raise ConfigurationError(
                f"{context}: routing condition is missing required 'value' key"
            )

        return RoutingCondition(
            field=cond_field,
            operator=operator,
            value=raw["value"],
        )

    def _parse_routing_rule(self, raw: dict, context: str) -> RoutingRule:
        """
        Parse a single routing rule dict into a ``RoutingRule``.

        Args:
            raw:     Raw dict from config.
            context: Human-readable path for error messages.

        Returns:
            Parsed ``RoutingRule``.

        Raises:
            ConfigurationError: If required keys are missing.
        """
        intent = raw.get("intent")
        if intent is None:
            raise ConfigurationError(
                f"{context}: routing rule is missing required 'intent' key"
            )

        next_subagent_id = raw.get("next_subagent_id")
        if not next_subagent_id:
            raise ConfigurationError(
                f"{context}: routing rule for intent '{intent}' is missing "
                f"required 'next_subagent_id' key"
            )

        # Parse optional single condition
        condition: RoutingCondition | None = None
        raw_condition = raw.get("condition")
        if raw_condition:
            condition = self._parse_routing_condition(
                raw_condition,
                context=f"{context}[intent={intent}].condition",
            )

        # Parse optional multi-condition list
        conditions: list[RoutingCondition] = []
        raw_conditions = raw.get("conditions") or []
        for i, raw_cond in enumerate(raw_conditions):
            conditions.append(
                self._parse_routing_condition(
                    raw_cond,
                    context=f"{context}[intent={intent}].conditions[{i}]",
                )
            )

        session_writes: dict = raw.get("session_writes") or {}
        for field_name, field_val in session_writes.items():
            if isinstance(field_val, (dict, list)):
                raise ConfigurationError(
                    f"{context}: session_writes value for '{field_name}' must be a scalar "
                    f"(str, int, float, or bool), not {type(field_val).__name__}"
                )

        return RoutingRule(
            intent=str(intent),
            next_subagent_id=next_subagent_id,
            condition=condition,
            conditions=conditions,
            session_writes=session_writes,
        )

    def _parse_subagent(self, raw: dict) -> SubAgent:
        """
        Parse a single subagent dict into a ``SubAgent``.

        Args:
            raw: Raw dict from config.

        Returns:
            Parsed ``SubAgent``.

        Raises:
            ConfigurationError: If required fields are missing or special_handler
                                 has an unrecognised value.
        """
        subagent_id = raw.get("id")
        if not subagent_id:
            raise ConfigurationError(
                "agent_workflow.subagents: entry is missing required 'id' field"
            )

        name = raw.get("name", "")
        description = raw.get("description", "")
        is_start: bool = bool(raw.get("is_start", False))
        is_terminal: bool = bool(raw.get("is_terminal", False))

        special_handler_raw = raw.get("special_handler")
        if special_handler_raw is not None and special_handler_raw not in _VALID_SPECIAL_HANDLERS:
            raise ConfigurationError(
                f"subagent '{subagent_id}': special_handler '{special_handler_raw}' "
                f"is not recognised. Must be one of: {sorted(_VALID_SPECIAL_HANDLERS)} or null"
            )
        special_handler: str | None = special_handler_raw or None

        valid_intents: list[str] = raw.get("valid_intents") or []
        tools: list[str] = raw.get("tools") or []
        system_prompt: str = raw.get("system_prompt", "")
        output_format: dict | None = raw.get("output_format") or None
        opening_phrase: str = str(raw.get("opening_phrase", "") or "").strip()
        if not opening_phrase:
            raise ConfigurationError(
                f"subagent '{subagent_id}': 'opening_phrase' is required and must be "
                f"non-empty. Every subagent must have a phrase to emit on entry so "
                f"adopted-state callbacks always greet the caller."
            )

        routing_raw: list[dict] = raw.get("routing") or []
        routing: list[RoutingRule] = [
            self._parse_routing_rule(r, context=f"subagent '{subagent_id}'.routing")
            for r in routing_raw
        ]

        return SubAgent(
            id=subagent_id,
            name=name,
            description=description,
            is_start=is_start,
            is_terminal=is_terminal,
            special_handler=special_handler,
            valid_intents=valid_intents,
            tools=tools,
            system_prompt=system_prompt,
            output_format=output_format,
            routing=routing,
            opening_phrase=opening_phrase,
        )

    # ------------------------------------------------------------------
    # Private validation helpers (rules 1–7)
    # ------------------------------------------------------------------

    def _validate_exactly_one_start(self, subagents: dict[str, SubAgent]) -> str:
        """
        Validate that exactly one subagent has ``is_start=True`` (rule 1).

        Args:
            subagents: All subagents keyed by id.

        Returns:
            The id of the start subagent.

        Raises:
            ConfigurationError: If zero or more than one subagent has ``is_start=True``.
        """
        start_ids = [sa_id for sa_id, sa in subagents.items() if sa.is_start]
        if len(start_ids) == 0:
            raise ConfigurationError(
                "agent_workflow validation failed (rule 1): no subagent has 'is_start: true'. "
                "Exactly one subagent must be the start node."
            )
        if len(start_ids) > 1:
            raise ConfigurationError(
                f"agent_workflow validation failed (rule 1): multiple subagents have "
                f"'is_start: true': {start_ids}. Exactly one must be the start node."
            )
        return start_ids[0]

    def _validate_routing_references(
        self,
        subagents: dict[str, SubAgent],
        global_routing: list[RoutingRule],
    ) -> None:
        """
        Validate that every ``next_subagent_id`` resolves to a known subagent (rule 2).

        Checks both subagent-level and global routing rules.

        Args:
            subagents:      All subagents keyed by id.
            global_routing: Global routing rules to check.

        Raises:
            ConfigurationError: If any ``next_subagent_id`` is not in ``subagents``.
        """
        known_ids = set(subagents.keys())

        for rule in global_routing:
            if rule.next_subagent_id not in known_ids:
                raise ConfigurationError(
                    f"agent_workflow validation failed (rule 2): global_routing rule "
                    f"for intent '{rule.intent}' references unknown subagent id "
                    f"'{rule.next_subagent_id}'"
                )

        for sa_id, subagent in subagents.items():
            for rule in subagent.routing:
                if rule.next_subagent_id not in known_ids:
                    raise ConfigurationError(
                        f"agent_workflow validation failed (rule 2): subagent '{sa_id}' "
                        f"routing rule for intent '{rule.intent}' references unknown "
                        f"subagent id '{rule.next_subagent_id}'"
                    )

    def _validate_tool_names(
        self,
        subagents: dict[str, SubAgent],
        tool_registry: ToolRegistry,
        internal_tool_names: set[str] | None = None,
    ) -> None:
        """
        Validate that every tool in subagent.tools exists in the registry (rule 3).

        Internal connector tools (e.g. ``knowledge_retrieval``) are exempt — they
        are routed by Agent Core directly and do not appear in the Action Gateway
        ToolRegistry.

        Args:
            subagents:            All subagents keyed by id.
            tool_registry:        Initialised registry to compare against.
            internal_tool_names:  Tool names from connectors.internal (exempt from check).

        Raises:
            ConfigurationError: If any tool name is not in the registry or internal list.
        """
        registered_tools: set[str] = tool_registry.get_tool_names()
        exempt: set[str] = internal_tool_names or set()
        all_valid = registered_tools | exempt

        for sa_id, subagent in subagents.items():
            for tool_name in subagent.tools:
                if tool_name not in all_valid:
                    raise ConfigurationError(
                        f"agent_workflow validation failed (rule 3): subagent '{sa_id}' "
                        f"lists tool '{tool_name}' which is not registered in the "
                        f"ToolRegistry. Registered tools: {sorted(registered_tools)}"
                    )

    def _validate_subagent_intents(
        self,
        subagents: dict[str, SubAgent],
        all_nlu_intents: set[str],
    ) -> None:
        """
        Validate that every intent in subagent.valid_intents exists in the NLU config (rule 4).

        Args:
            subagents:        All subagents keyed by id.
            all_nlu_intents:  Set of all declared NLU intents from preprocessing config.

        Raises:
            ConfigurationError: If any subagent intent is not in the NLU intent set.
        """
        for sa_id, subagent in subagents.items():
            for intent in subagent.valid_intents:
                if intent == "other":
                    # "other" is a router catch-all — not an NLU classifier label.
                    continue
                if intent not in all_nlu_intents:
                    raise ConfigurationError(
                        f"agent_workflow validation failed (rule 4): subagent '{sa_id}' "
                        f"declares intent '{intent}' which is not present in "
                        f"config['preprocessing']['nlu_processor']['intents']"
                    )

    def _validate_global_intents_not_in_subagents(
        self,
        subagents: dict[str, SubAgent],
        global_intents: list[str],
    ) -> None:
        """
        Validate that no global intent appears in any subagent's valid_intents (rule 5).

        Args:
            subagents:       All subagents keyed by id.
            global_intents:  List of global intent names from the workflow config.

        Raises:
            ConfigurationError: If any global intent is also claimed by a subagent.
        """
        # "other" is a router catch-all — it may appear in both subagent valid_intents
        # and global_intents without conflict.
        global_intent_set = set(global_intents) - {"other"}
        for sa_id, subagent in subagents.items():
            overlap = global_intent_set & (set(subagent.valid_intents) - {"other"})
            if overlap:
                raise ConfigurationError(
                    f"agent_workflow validation failed (rule 5): subagent '{sa_id}' "
                    f"declares intents that are also in global_intents: {sorted(overlap)}. "
                    f"Global intents must not appear in any subagent's valid_intents."
                )

    def _validate_terminal_routing(self, subagents: dict[str, SubAgent]) -> None:
        """
        Validate that terminal subagents have an empty routing list (rule 6).

        Args:
            subagents: All subagents keyed by id.

        Raises:
            ConfigurationError: If a terminal subagent has routing rules.
        """
        for sa_id, subagent in subagents.items():
            if subagent.is_terminal and subagent.routing:
                raise ConfigurationError(
                    f"agent_workflow validation failed (rule 6): subagent '{sa_id}' is "
                    f"marked is_terminal=true but has {len(subagent.routing)} routing "
                    f"rule(s). Terminal subagents must have an empty routing list."
                )

    def _validate_nonterminal_routing(self, subagents: dict[str, SubAgent]) -> None:
        """
        Validate that non-terminal subagents have at least one routing rule (rule 7).

        A subagent satisfies this rule if it has at least one routing rule, or if
        it has a catch-all ``"*"`` rule among its routing rules.

        Args:
            subagents: All subagents keyed by id.

        Raises:
            ConfigurationError: If a non-terminal subagent has no routing rules.
        """
        for sa_id, subagent in subagents.items():
            if subagent.is_terminal:
                continue
            has_routing = bool(subagent.routing)
            has_catchall = any(r.intent == "*" for r in subagent.routing)
            if not has_routing and not has_catchall:
                raise ConfigurationError(
                    f"agent_workflow validation failed (rule 7): subagent '{sa_id}' is "
                    f"non-terminal but has no routing rules. Non-terminal subagents must "
                    f"have at least one routing rule or a catch-all '*' rule."
                )

    # ------------------------------------------------------------------
    # Private pre-computation helpers
    # ------------------------------------------------------------------

    def _load_nlu_intents(self, config: dict) -> set[str]:
        """
        Extract the full NLU intent set from the preprocessing config.

        Args:
            config: Full domain configuration dict.

        Returns:
            Set of all declared intent name strings.

        Raises:
            ConfigurationError: If the required path into config is missing or empty.
        """
        preprocessing = config.get("preprocessing")
        if not preprocessing:
            raise ConfigurationError(
                "config is missing required key 'preprocessing'"
            )
        nlu_processor = preprocessing.get("nlu_processor")
        if not nlu_processor:
            raise ConfigurationError(
                "config['preprocessing'] is missing required key 'nlu_processor'"
            )
        intents = nlu_processor.get("intents")
        if not intents:
            raise ConfigurationError(
                "config['preprocessing']['nlu_processor']['intents'] is required "
                "and must not be empty"
            )
        if not isinstance(intents, list):
            raise ConfigurationError(
                f"config['preprocessing']['nlu_processor']['intents'] must be a list, "
                f"got {type(intents)}"
            )
        return set(intents)

    def _build_nlu_intent_set(
        self,
        subagents: dict[str, SubAgent],
        global_intents: list[str],
    ) -> dict[str, list[str]]:
        """
        Pre-compute per-subagent scoped intent lists for NLU at turn time.

        Each subagent's scoped set is ``subagent.valid_intents + global_intents``.

        Args:
            subagents:       All subagents keyed by id.
            global_intents:  Global intents shared across all subagents.

        Returns:
            Dict mapping subagent_id to its scoped intent list.
        """
        result: dict[str, list[str]] = {}
        for sa_id, subagent in subagents.items():
            result[sa_id] = list(subagent.valid_intents) + list(global_intents)
        return result

    def _build_tool_defs(
        self,
        subagents: dict[str, SubAgent],
        tool_registry: ToolRegistry,
    ) -> dict[str, list[dict]]:
        """
        Pre-compute per-subagent tool definition slices for injection into LLM calls.

        Args:
            subagents:      All subagents keyed by id.
            tool_registry:  Initialised registry to fetch definitions from.

        Returns:
            Dict mapping subagent_id to its list of tool definition dicts.
        """
        result: dict[str, list[dict]] = {}
        for sa_id, subagent in subagents.items():
            # Use the registry to get definitions for ALL tools listed on the subagent.
            # This now includes internal tools like knowledge_retrieval.
            result[sa_id] = tool_registry.get_definitions_for(subagent.tools or [])
        return result

    def _validate_global_tool_names(
        self,
        global_tools: list[str],
        tool_registry: ToolRegistry,
    ) -> None:
        """Fail fast if any name in agent_workflow.global_tools is not registered.

        Args:
            global_tools:   Names declared under ``agent_workflow.global_tools``.
            tool_registry:  Registry whose :meth:`get_tool_names` lists all known tools.

        Raises:
            ConfigurationError: If any name is not registered.
        """
        if not global_tools:
            return
        known = tool_registry.get_tool_names()
        unknown = [t for t in global_tools if t not in known]
        if unknown:
            raise ConfigurationError(
                "agent_workflow.global_tools references unregistered tools: "
                f"{sorted(unknown)}. Registered tools: {sorted(known)}"
            )

    def _build_global_tool_defs(
        self,
        global_tools: list[str],
        tool_registry: ToolRegistry,
    ) -> list[dict]:
        """Resolve ``global_tools`` names to Anthropic-shaped definitions.

        Args:
            global_tools:   Validated list of tool names.
            tool_registry:  Registry that produces definition dicts.

        Returns:
            List of tool definitions — empty list when ``global_tools`` is empty.
        """
        if not global_tools:
            return []
        return tool_registry.get_definitions_for(global_tools)
