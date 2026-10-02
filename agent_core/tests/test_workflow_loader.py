"""
agent_core/tests/test_workflow_loader.py

Unit tests for AgentWorkflowLoader (agent_core/src/workflow_loader.py).
No external dependencies — ToolRegistry is mocked via MagicMock.

Coverage:
- Normal: valid minimal config loads successfully
- Normal: all 5 validation rules pass on a well-formed config
- Normal: no NLU intents are read; no per-subagent intent set
- Normal: tool_defs pre-computed via registry.get_definitions_for
- Normal: global_routing parsed correctly
- Normal: routing condition (single) parsed correctly
- Normal: routing conditions (multi) parsed correctly
- Normal: session_writes on routing rules parsed correctly
- Normal: special_handler values accepted (hitl, whatsapp_handoff)
- Normal: terminal subagent with no routing passes validation
- Edge: subagent with no tools is valid
- Edge: falsy routing condition value (0, False) accepted
- Edge: output_format None is valid
- Failure: config=None raises ValueError
- Failure: tool_registry=None raises ValueError
- Failure: missing agent_workflow key raises ConfigurationError
- Failure: missing workflow_id raises ConfigurationError
- Failure: missing version raises ConfigurationError
- Failure: empty subagents list raises ConfigurationError
- Failure: duplicate subagent id raises ConfigurationError
- Rule 1: no start subagent raises ConfigurationError
- Rule 1: multiple start subagents raises ConfigurationError
- Rule 2: unknown next_subagent_id in subagent routing raises ConfigurationError
- Rule 2: unknown next_subagent_id in global_routing raises ConfigurationError
- Rule 3: unregistered tool name raises ConfigurationError
- Rule 4: terminal subagent with routing rules raises ConfigurationError
- Rule 5: non-terminal subagent with no routing raises ConfigurationError
- Failure: invalid routing condition operator raises ConfigurationError
- Failure: routing condition missing field raises ConfigurationError
- Failure: routing condition missing operator raises ConfigurationError
- Failure: routing condition missing value key raises ConfigurationError
- Failure: routing rule missing intent raises ConfigurationError
- Failure: routing rule missing next_subagent_id raises ConfigurationError
- Failure: invalid special_handler value raises ConfigurationError
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.workflow_loader import AgentWorkflowLoader, AgentWorkflow
from src.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Helpers — minimal valid config builders
# ---------------------------------------------------------------------------

def _make_tool_registry(tool_names: list[str] | None = None) -> MagicMock:
    """Return a mock ToolRegistry that reports the given tool names as registered."""
    registry = MagicMock()
    registry.get_tool_names.return_value = set(tool_names or [])
    registry.get_definitions_for.side_effect = lambda names: [
        {"name": n, "description": "", "input_schema": {}} for n in names
    ]
    return registry


def _minimal_config(
    *,
    tools: list[str] | None = None,
    routing: list[dict] | None = None,
    extra_subagents: list[dict] | None = None,
    global_routing: list[dict] | None = None,
) -> dict:
    """
    Build a minimal valid config with one start subagent and one terminal subagent.

    The start subagent routes to the terminal subagent via a catch-all rule.
    """
    terminal = {
        "id": "end",
        "name": "End",
        "description": "Terminal node",
        "is_start": False,
        "is_terminal": True,
        "tools": [],
        "system_prompt": "You are done.",
        "opening_phrase": "Goodbye.",
        "routing": [],
    }

    start = {
        "id": "start",
        "name": "Start",
        "description": "Entry node",
        "is_start": True,
        "is_terminal": False,
        "tools": tools or [],
        "system_prompt": "You are a helpful agent.",
        "opening_phrase": "Hello.",
        "routing": routing if routing is not None else [
            {"intent": "*", "next_subagent_id": "end"}
        ],
    }

    subagents = [start, terminal] + (extra_subagents or [])

    return {
        "agent_workflow": {
            "workflow_id": "test_workflow",
            "version": "1.0.0",
            "agent_system_prompt": "Top-level system prompt.",
            "global_routing": global_routing or [],
            "default_fallback_subagent_id": "start",
            "subagents": subagents,
        },
    }


@pytest.fixture
def loader() -> AgentWorkflowLoader:
    return AgentWorkflowLoader()


@pytest.fixture
def registry() -> MagicMock:
    return _make_tool_registry()


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


def test_load_returns_agent_workflow(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert isinstance(workflow, AgentWorkflow)


def test_workflow_id_and_version_populated(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert workflow.workflow_id == "test_workflow"
    assert workflow.version == "1.0.0"


def test_start_subagent_id_identified(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert workflow.start_subagent_id == "start"


def test_subagents_keyed_by_id(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert "start" in workflow.subagents
    assert "end" in workflow.subagents






def test_tool_defs_pre_computed_via_registry(loader):
    registry = _make_tool_registry(["my_tool"])
    config = _minimal_config(
        tools=["my_tool"],
    )
    workflow = loader.load(config, registry)
    defs = workflow.tool_defs["start"]
    assert len(defs) == 1
    assert defs[0]["name"] == "my_tool"
    registry.get_definitions_for.assert_called()


def test_tool_defs_empty_for_subagent_with_no_tools(loader, registry):
    config = _minimal_config(tools=[])
    workflow = loader.load(config, registry)
    assert workflow.tool_defs["start"] == []


def test_global_routing_parsed(loader, registry):
    config = _minimal_config(
        global_routing=[
            {"intent": "farewell", "next_subagent_id": "end"}
        ],
    )
    workflow = loader.load(config, registry)
    assert len(workflow.global_routing) == 1
    assert workflow.global_routing[0].intent == "farewell"
    assert workflow.global_routing[0].next_subagent_id == "end"


def test_routing_rule_with_single_condition_parsed(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "loop_count", "operator": "gt", "value": 3},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    workflow = loader.load(config, registry)
    rule = workflow.subagents["start"].routing[0]
    assert rule.condition is not None
    assert rule.condition.field == "loop_count"
    assert rule.condition.operator == "gt"
    assert rule.condition.value == 3


def test_routing_rule_with_multi_conditions_parsed(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "conditions": [
                    {"field": "loop_count", "operator": "gt", "value": 1},
                    {"field": "mental_state", "operator": "eq", "value": "fog"},
                ],
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    workflow = loader.load(config, registry)
    rule = workflow.subagents["start"].routing[0]
    assert len(rule.conditions) == 2
    assert rule.conditions[0].field == "loop_count"
    assert rule.conditions[1].field == "mental_state"


def test_session_writes_on_routing_rule_parsed(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "session_writes": {"user_storage_mode": "anonymous"},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    workflow = loader.load(config, registry)
    rule = workflow.subagents["start"].routing[0]
    assert rule.session_writes == {"user_storage_mode": "anonymous"}


def test_special_handler_hitl_accepted(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"].append({
        "id": "escalation",
        "name": "Escalation",
        "description": "HITL escalation",
        "is_start": False,
        "is_terminal": True,
        "special_handler": "hitl",
        "tools": [],
        "system_prompt": "",
        "opening_phrase": "Connecting you to a counsellor.",
        "routing": [],
    })
    workflow = loader.load(config, registry)
    assert workflow.subagents["escalation"].special_handler == "hitl"


def test_special_handler_whatsapp_handoff_accepted(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"].append({
        "id": "handoff",
        "name": "Handoff",
        "description": "WhatsApp handoff",
        "is_start": False,
        "is_terminal": True,
        "special_handler": "whatsapp_handoff",
        "tools": [],
        "system_prompt": "",
        "opening_phrase": "Continuing on WhatsApp.",
        "routing": [],
    })
    workflow = loader.load(config, registry)
    assert workflow.subagents["handoff"].special_handler == "whatsapp_handoff"


def test_terminal_subagent_no_routing_passes(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert workflow.subagents["end"].is_terminal is True
    assert workflow.subagents["end"].routing == []


def test_output_format_none_is_valid(loader, registry):
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert workflow.subagents["start"].output_format is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------






def test_routing_condition_value_zero_is_accepted(loader, registry):
    """Falsy value 0 must not be treated as missing."""
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "loop_count", "operator": "eq", "value": 0},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    workflow = loader.load(config, registry)
    assert workflow.subagents["start"].routing[0].condition.value == 0


def test_routing_condition_value_false_is_accepted(loader, registry):
    """Falsy value False must not be treated as missing."""
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "opted_in", "operator": "eq", "value": False},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    workflow = loader.load(config, registry)
    assert workflow.subagents["start"].routing[0].condition.value is False


# ---------------------------------------------------------------------------
# Failure: None / missing top-level keys
# ---------------------------------------------------------------------------


def test_none_config_raises_value_error(loader, registry):
    with pytest.raises(ValueError, match="config must not be None"):
        loader.load(None, registry)


def test_none_tool_registry_raises_value_error(loader):
    config = _minimal_config()
    with pytest.raises(ValueError, match="tool_registry must not be None"):
        loader.load(config, None)


def test_missing_agent_workflow_key_raises(loader, registry):
    config = _minimal_config()
    del config["agent_workflow"]
    with pytest.raises(ConfigurationError, match="agent_workflow"):
        loader.load(config, registry)


def test_missing_workflow_id_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["workflow_id"] = ""
    with pytest.raises(ConfigurationError, match="workflow_id"):
        loader.load(config, registry)


def test_missing_version_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["version"] = ""
    with pytest.raises(ConfigurationError, match="version"):
        loader.load(config, registry)


def test_empty_subagents_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"] = []
    with pytest.raises(ConfigurationError, match="subagents"):
        loader.load(config, registry)


def test_duplicate_subagent_id_raises(loader, registry):
    config = _minimal_config()
    # Append a copy of the start subagent with the same id
    config["agent_workflow"]["subagents"].append(
        config["agent_workflow"]["subagents"][0].copy()
    )
    with pytest.raises(ConfigurationError, match="Duplicate subagent id"):
        loader.load(config, registry)










# ---------------------------------------------------------------------------
# Validation rule 1: exactly one start subagent
# ---------------------------------------------------------------------------


def test_rule1_no_start_subagent_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"][0]["is_start"] = False
    with pytest.raises(ConfigurationError, match="rule 1"):
        loader.load(config, registry)


def test_rule1_multiple_start_subagents_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"][1]["is_start"] = True
    with pytest.raises(ConfigurationError, match="rule 1"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Validation rule 2: routing references
# ---------------------------------------------------------------------------


def test_rule2_unknown_next_subagent_in_routing_raises(loader, registry):
    config = _minimal_config(
        routing=[{"intent": "*", "next_subagent_id": "nonexistent"}]
    )
    with pytest.raises(ConfigurationError, match="rule 2"):
        loader.load(config, registry)


def test_rule2_unknown_next_subagent_in_global_routing_raises(loader, registry):
    config = _minimal_config(
        global_routing=[{"intent": "farewell", "next_subagent_id": "ghost"}],
    )
    with pytest.raises(ConfigurationError, match="rule 2"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Validation rule 3: tool names exist in registry
# ---------------------------------------------------------------------------


def test_rule3_unregistered_tool_raises(loader):
    registry = _make_tool_registry(["known_tool"])
    config = _minimal_config(
        tools=["unknown_tool"],
    )
    with pytest.raises(ConfigurationError, match="rule 3"):
        loader.load(config, registry)


def test_rule3_registered_tool_passes(loader):
    registry = _make_tool_registry(["my_tool"])
    config = _minimal_config(
        tools=["my_tool"],
    )
    workflow = loader.load(config, registry)
    assert "my_tool" in workflow.subagents["start"].tools


# ---------------------------------------------------------------------------
# Validation rule 4: terminal subagents must have no routing
# ---------------------------------------------------------------------------


def test_rule4_terminal_with_routing_raises(loader, registry):
    config = _minimal_config()
    # Add a routing rule to the terminal subagent
    config["agent_workflow"]["subagents"][1]["routing"] = [
        {"intent": "*", "next_subagent_id": "start"}
    ]
    with pytest.raises(ConfigurationError, match="rule 4"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Validation rule 5: non-terminal subagents must have routing
# ---------------------------------------------------------------------------


def test_rule5_nonterminal_with_no_routing_raises(loader, registry):
    config = _minimal_config(routing=[])
    with pytest.raises(ConfigurationError, match="rule 5"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Failure: routing condition and rule parse errors
# ---------------------------------------------------------------------------


def test_invalid_routing_condition_operator_raises(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "loop_count", "operator": "INVALID_OP", "value": 1},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="operator.*invalid|invalid.*operator"):
        loader.load(config, registry)


def test_routing_condition_missing_field_raises(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"operator": "eq", "value": 1},  # no 'field'
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="'field'"):
        loader.load(config, registry)


def test_routing_condition_missing_operator_raises(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "loop_count", "value": 1},  # no 'operator'
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="'operator'"):
        loader.load(config, registry)


def test_routing_condition_missing_value_key_raises(loader, registry):
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "condition": {"field": "loop_count", "operator": "eq"},  # no 'value'
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="'value'"):
        loader.load(config, registry)


def test_routing_rule_missing_intent_raises(loader, registry):
    config = _minimal_config(
        routing=[{"next_subagent_id": "end"}]  # no 'intent'
    )
    with pytest.raises(ConfigurationError, match="'intent'"):
        loader.load(config, registry)


def test_routing_rule_missing_next_subagent_id_raises(loader, registry):
    config = _minimal_config(
        routing=[{"intent": "greeting"}]  # no 'next_subagent_id'
    )
    with pytest.raises(ConfigurationError, match="next_subagent_id"):
        loader.load(config, registry)


def test_invalid_special_handler_raises(loader, registry):
    config = _minimal_config()
    config["agent_workflow"]["subagents"].append({
        "id": "bad_handler",
        "name": "Bad",
        "description": "",
        "is_start": False,
        "is_terminal": True,
        "special_handler": "not_a_real_handler",
        "tools": [],
        "system_prompt": "",
        "opening_phrase": "x",
        "routing": [],
    })
    with pytest.raises(ConfigurationError, match="special_handler"):
        loader.load(config, registry)


def test_session_writes_dict_value_raises_config_error(loader, registry):
    """session_writes value that is a dict must raise ConfigurationError at startup."""
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "session_writes": {"metadata": {"nested": "value"}},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="session_writes"):
        loader.load(config, registry)


def test_session_writes_list_value_raises_config_error(loader, registry):
    """session_writes value that is a list must raise ConfigurationError at startup."""
    config = _minimal_config(
        routing=[
            {
                "intent": "greeting",
                "next_subagent_id": "end",
                "session_writes": {"tags": ["a", "b"]},
            },
            {"intent": "*", "next_subagent_id": "end"},
        ]
    )
    with pytest.raises(ConfigurationError, match="session_writes"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# opening_phrase field tests (GH-137)
# ---------------------------------------------------------------------------


def test_subagent_opening_phrase_default_empty():
    from src.workflow_loader import SubAgent
    sa = SubAgent(
        id="greeting", name="Greeting", description="d",
        is_start=True, is_terminal=False, special_handler=None, tools=[], system_prompt="",
        output_format=None, routing=[],
    )
    assert sa.opening_phrase == ""


def test_subagent_opening_phrase_accepts_string():
    from src.workflow_loader import SubAgent
    sa = SubAgent(
        id="greeting", name="Greeting", description="d",
        is_start=True, is_terminal=False, special_handler=None, tools=[], system_prompt="",
        output_format=None, routing=[],
        opening_phrase="नमस्ते।",
    )
    assert sa.opening_phrase == "नमस्ते।"


def test_loader_extracts_opening_phrase_from_yaml():
    from src.workflow_loader import AgentWorkflowLoader
    config = {
        "agent_workflow": {
            "workflow_id": "test",
            "version": "1.0.0",
            "agent_system_prompt": "You are helpful.",
            "global_routing": [],
            "default_fallback_subagent_id": "greeting",
            "subagents": [
                {
                    "id": "greeting",
                    "name": "Greeting",
                    "description": "Opener",
                    "is_start": True,
                    "is_terminal": False,
                    "tools": [],
                    "system_prompt": "Greet.",
                    "opening_phrase": "नमस्ते।",
                    "routing": [
                        {"intent": "*", "next_subagent_id": "end"}
                    ],
                },
                {
                    "id": "end",
                    "name": "End",
                    "description": "Terminal",
                    "is_start": False,
                    "is_terminal": True,
                    "tools": [],
                    "system_prompt": "Done.",
                    "opening_phrase": "Goodbye.",
                    "routing": [],
                }
            ],
        }
    }
    loader = AgentWorkflowLoader()
    registry = _make_tool_registry()
    workflow = loader.load(config, registry)
    assert workflow.subagents["greeting"].opening_phrase == "नमस्ते।"


def test_loader_missing_opening_phrase_raises():
    from src.workflow_loader import AgentWorkflowLoader
    config = {
        "agent_workflow": {
            "workflow_id": "test",
            "version": "1.0.0",
            "agent_system_prompt": "You are helpful.",
            "global_routing": [],
            "default_fallback_subagent_id": "greeting",
            "subagents": [
                {
                    "id": "greeting",
                    "name": "Greeting",
                    "description": "Opener",
                    "is_start": True,
                    "is_terminal": False,
                    "tools": [],
                    "system_prompt": "Greet.",
                    "routing": [
                        {"intent": "*", "next_subagent_id": "end"}
                    ],
                },
                {
                    "id": "end",
                    "name": "End",
                    "description": "Terminal",
                    "is_start": False,
                    "is_terminal": True,
                    "tools": [],
                    "system_prompt": "Done.",
                    "routing": [],
                }
            ],
        }
    }
    loader = AgentWorkflowLoader()
    registry = _make_tool_registry()
    with pytest.raises(ConfigurationError, match="opening_phrase"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Task 2 — global_tool_defs + resolve_tools_for
# ---------------------------------------------------------------------------


def test_global_tools_resolved_to_definitions(loader):
    registry = _make_tool_registry(["get_profile", "onest_market_lookup"])
    config = _minimal_config(tools=[])
    config["agent_workflow"]["global_tools"] = ["get_profile", "onest_market_lookup"]
    workflow = loader.load(config, registry)
    assert [d["name"] for d in workflow.global_tool_defs] == [
        "get_profile",
        "onest_market_lookup",
    ]


def test_resolve_tools_for_prefers_global_tool_defs(loader):
    registry = _make_tool_registry(["get_profile", "local_only"])
    config = _minimal_config(tools=["local_only"])
    config["agent_workflow"]["global_tools"] = ["get_profile"]
    workflow = loader.load(config, registry)
    names = [d["name"] for d in workflow.resolve_tools_for("start")]
    assert names == ["get_profile"]


def test_resolve_tools_for_falls_back_to_subagent_tools_when_global_empty(loader):
    registry = _make_tool_registry(["local_only"])
    config = _minimal_config(tools=["local_only"])
    workflow = loader.load(config, registry)
    names = [d["name"] for d in workflow.resolve_tools_for("start")]
    assert names == ["local_only"]


def test_resolve_tools_for_unknown_subagent_returns_empty_when_global_empty(loader):
    registry = _make_tool_registry(["local_only"])
    config = _minimal_config(tools=["local_only"])
    workflow = loader.load(config, registry)
    assert workflow.resolve_tools_for("nope") == []


def test_global_tools_validated_against_registry(loader):
    """Unregistered global tool name must fail validation rule 3."""
    registry = _make_tool_registry(["get_profile"])  # no 'ghost_tool'
    config = _minimal_config()
    config["agent_workflow"]["global_tools"] = ["ghost_tool"]
    with pytest.raises(ConfigurationError, match="ghost_tool"):
        loader.load(config, registry)


# ---------------------------------------------------------------------------
# Pending questions
# ---------------------------------------------------------------------------

from src.workflow_loader import OptionsFrom, PendingQuestion  # noqa: E402


def _with_pending(cfg: dict) -> dict:
    start = cfg["agent_workflow"]["subagents"][0]
    start["pending"] = [
        {"id": "consent", "expects": "हाँ/नहीं",
         "when": [{"field": "consent_response", "operator": "in", "value": [None, ""]}]},
        {"id": "select_job", "options_from": {"tool": "fetch_jobs", "fields": ["role", "company"],
                                              "id_field": "item_id"},
         "resolves_to": "selected_job_item_id"},
    ]
    return cfg


def test_pending_questions_are_parsed_in_order():
    wf = AgentWorkflowLoader().load(_with_pending(_minimal_config()), _make_tool_registry())
    start = wf.subagents[wf.start_subagent_id]
    assert [p.id for p in start.pending] == ["consent", "select_job"]
    assert start.pending[0].when[0].operator == "in"
    assert start.pending[1].options_from == OptionsFrom("fetch_jobs", ("role", "company"), "item_id")
    assert start.pending[1].resolves_to == "selected_job_item_id"


def test_contains_operator_accepted_in_routing_and_pending():
    cfg = _minimal_config(routing=[
        {"intent": "greeting", "next_subagent_id": "end",
         "conditions": [{"field": "current_question", "operator": "contains", "value": "x"}]},
        {"intent": "*", "next_subagent_id": "end"},
    ])
    cfg["agent_workflow"]["subagents"][0]["pending"] = [
        {"id": "submit_confirm", "expects": "हाँ/ना",
         "when": [{"field": "current_question", "operator": "contains", "value": ["a", "b"]}]},
    ]
    wf = AgentWorkflowLoader().load(cfg, _make_tool_registry())
    start = wf.subagents[wf.start_subagent_id]
    assert start.routing[0].conditions[0].operator == "contains"
    assert start.pending[0].when[0].operator == "contains"
    assert start.pending[0].when[0].value == ["a", "b"]


def test_pending_defaults_to_empty():
    wf = AgentWorkflowLoader().load(_minimal_config(), _make_tool_registry())
    assert all(s.pending == [] for s in wf.subagents.values())




def test_loads_without_nlu_intents_and_has_no_intent_set(loader, registry):
    """The loader no longer reads NLU intents; the workflow carries no intent set."""
    config = _minimal_config()
    workflow = loader.load(config, registry)
    assert not hasattr(workflow, "nlu_intent_set")
    assert not hasattr(workflow, "global_intents")
    assert not hasattr(workflow.subagents["start"], "valid_intents")
