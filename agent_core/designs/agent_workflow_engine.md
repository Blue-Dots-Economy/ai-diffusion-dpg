# AgentWorkflow Engine — Agent Core Design

Version 1.0 · March 2026

---

## Overview

The Agent Core becomes workflow-aware. Any usecase deployer defines their conversation as an `AgentWorkflow` — a collection of `SubAgent` units, each owning its own instructions, tools, and intents. The engine loads this workflow at startup and activates the right subagent turn by turn — determining where the user is, what intents are valid, and where to transition next — entirely driven by config.

This replaces the flat `WorkflowStep` enum and implicit LLM-driven routing with an explicit, declarative subagent model.

---

## Core Concepts

| Concept | Description |
|---|---|
| `AgentWorkflow` | The full usecase workflow — a collection of subagents and global routing rules |
| `SubAgent` | A single state in the conversation. Owns its own intents, routing, tools, and LLM system prompt |
| `current_subagent_id` | Stored in session state. The only piece of graph state the Memory Layer holds |
| Intent-scoped NLU | NLU classifies only within the valid intents of the current subagent + global intents |
| Routing | Deterministic: intent + optional session field conditions → next_subagent_id |

---

## SubAgent Schema

Each subagent is a self-contained unit. Everything the Agent Core, NLU, and LLM need for a given conversation state lives inside the subagent.

```yaml
subagents:
  - id: <string>                        # Unique identifier. Used as current_subagent_id in session.
    name: <string>                      # Human-readable label.
    description: <string>               # What this subagent represents in the conversation.

    is_start: <bool>                    # Exactly one subagent per workflow must be true.
    is_terminal: <bool>                 # No routing rules required. Session ends here.
    special_handler: <string|null>      # Optional. Bypasses LLM entirely.
                                        # Values: "hitl" | "whatsapp_handoff" | null

    valid_intents: [<intent_name>]      # NLU classifies ONLY within this set + global_intents.
                                        # Scopes intent classification to what is meaningful here.

    tools: [<tool_name>]                # Action Gateway tools available at this subagent.
                                        # Only these tool definitions are passed to the LLM.
                                        # Empty list = no tools exposed.

    system_prompt: |                    # Full LLM instruction block for this subagent.
      <text>                            # Defines: role at this stage, what to ask/say,
                                        # rules to follow, what NOT to do, tone, format.
                                        # This is the primary driver of LLM behaviour here.
                                        # Merged with agent_system_prompt at runtime.

    output_format: <json_schema|null>   # Optional. When set, the LLM response is constrained
                                        # to this JSON schema (passed as structured output spec).
                                        # Null = free-form text response (default for most subagents).
                                        # Use for subagents that must return machine-readable output
                                        # (e.g. data extraction, eligibility check results).

    routing:                            # Ordered list of routing rules. First match wins.
      - intent: <intent_name>
        next_subagent_id: <subagent_id>

      - intent: <intent_name>           # Conditional routing: evaluated against session fields.
        condition:
          field: <session_field_name>
          operator: <eq|in|lt|gt|not_eq>
          value: <value_or_list>
        next_subagent_id: <subagent_id>

      - intent: <intent_name>           # Multiple conditions (all must be true).
        conditions:
          - field: <field>
            operator: <op>
            value: <value>
          - field: <field>
            operator: <op>
            value: <value>
        next_subagent_id: <subagent_id>

      - intent: "*"                     # Catch-all for this subagent. Stays on same subagent.
        next_subagent_id: <subagent_id>
```

---

## AgentWorkflow Schema

The top-level config block that wraps all subagents and defines workflow-wide rules.

```yaml
agent_workflow:
  workflow_id: <string>                    # Usecase identifier (e.g. "kkb_iti_graduate")
  version: <string>                     # Semver. Logged with every turn for auditability.

  agent_system_prompt: |               # Defines the entire use case at the agent level.
    <text>                             # Covers: what this agent is, its purpose, who it serves,
                                       # what it must never do, and any hard rules that apply
                                       # unconditionally across every subagent and every turn.
                                       # This is the foundational instruction layer.
                                       # Channel tone and language style are injected at runtime
                                       # by the Reach Layer / Language Normalisation — not here.

  global_intents: [<intent_name>]       # Intents valid at ANY subagent regardless of current subagent.
                                        # Examples: counsellor_request, termination_intent,
                                        # whatsapp_handoff_request.
                                        # These are ALWAYS added to NLU's classification set.

  global_routing:                       # Routing rules for global_intents. Applied after
                                        # subagent-level routing fails to match.
    - intent: <intent_name>
      next_subagent_id: <subagent_id>
    - intent: <intent_name>
      condition:
        field: <field>
        operator: <op>
        value: <value>
      next_subagent_id: <subagent_id>

  default_fallback_subagent_id: <subagent_id>      # Where to go on unknown or unmatched intent.
                                        # Typically a re-prompt or clarification subagent.

  subagents:
    - <SubAgent>
    - <SubAgent>
    ...
```

---

## How the Orchestrator Uses the Workflow

### Startup

1. Load `agent_workflow` block from domain config YAML.
2. Parse into `AgentWorkflow` object. Validate: exactly one `is_start` subagent, all `next_subagent` references resolve, no orphaned subagents.
3. Pre-compute NLU intent set per subagent: merge subagent.valid_intents + workflow.global_intents for every
   subagent and store as a dict keyed by subagent_id. This is static after startup — computed once,
   looked up by subagent_id on every turn instead of recomputing the merge each time.
   Example: { "market_truth": ["interested_engaged", "pay_disappointment", ..., "counsellor_request"] }

4. Pre-compute tool definition set per subagent: for each subagent, fetch the full tool definitions from
   Action Gateway for only the tools listed in subagent.tools. Store as a dict keyed by subagent_id.
   At turn time, the active tool list is a single dict lookup — no registry filtering per request.
   Example: { "commitment": [<onest_apply_def>, <counsellor_schedule_def>], "greeting": [] }

   Both structures are stored as class-level attributes on AgentCore (not per-session).
   They are safe for concurrent request access because they are read-only after startup.

### Per-Turn Execution (modifications to existing 12-step sequence)

```
Step 1:  Read session state (Memory Layer)
         → extract current_subagent_id from session
         → if no current_subagent_id: use workflow.start_subagent

Step 2:  Resolve current_subagent = workflow.subagents[current_subagent_id]
         → if current_subagent.special_handler is set: execute handler, skip Steps 3–9

Step 3:  Safety check on input (Trust Layer) — unchanged

Step 4:  Language Normalisation — unchanged

Step 5:  NLU Processor
         → pass current_subagent.valid_intents + workflow.global_intents as the allowed intent set
         → NLU classifies intent AND extracts entities in a single call
         → Workflow Gate writes extracted entities to session state synchronously
           (routing in Step 6 sees current-turn values, not just last-turn state)

Step 6:  Determine next_subagent_id via routing resolution (see Routing Algorithm below)
         → entry guards expressed as routing conditions, not subagent-level fields
           (e.g. "don't enter evaluation unless profile_complete == true" is a
           condition on the routing rule that points to evaluation, not a
           required_inputs declaration on evaluation itself)

Step 7:  ManagerAgent assembles prompt (internal — no external call)
         → system prompt = agent_system_prompt
                         + channel/language context (from Reach Layer / Language Normalisation)
                         + current_subagent.system_prompt
         → messages built from current_question and session context

Step 8:  LLM call #1 with scoped tools (current_subagent.tools only)
         → tool set may include knowledge_retrieval (KE) if listed in subagent.tools
         → if current_subagent.output_format is set: passed as structured output schema to LLM

Step 9:  Tool-use loop (Manager Agent + Action Gateway)
         # OPEN: Decide whether the orchestrator drives the full tool-use loop (Step 9 as written),
         # or the LLM handles tool calling autonomously
         # Current design: orchestrator owns the loop — appends tool_result and re-calls LLM until
         # a non-tool_use response is returned.
         → if LLM calls knowledge_retrieval: orchestrator calls Knowledge Engine,
           appends retrieved chunks as tool_result, LLM continues
         → if LLM calls any other tool: routed to Action Gateway
         → loop continues until LLM returns a non-tool_use response

Step 10: Safety check on output (Trust Layer) — unchanged

Step 11: Return TurnResult

Step 12: [async] Write session state including next_subagent_id → Memory Layer
Step 13: [async] Emit turn event → Observability Layer
```

---

## Routing Algorithm

```
function resolve_next_subagent(current_subagent, nlu_result, session_state):

  # 1. Try subagent-level routing rules (ordered, first match wins)
  for rule in current_subagent.routing:
    if rule.intent == nlu_result.intent OR rule.intent == "*":
      if rule has no condition:
        return rule.next_subagent_id
      if evaluate_condition(rule.condition, session_state):
        return rule.next_subagent_id

  # 2. Try global routing rules
  for rule in workflow.global_routing:
    if rule.intent == nlu_result.intent:
      if rule has no condition:
        return rule.next_subagent_id
      if evaluate_condition(rule.condition, session_state):
        return rule.next_subagent_id

  # 3. Fallback
  return workflow.default_fallback_subagent_id

function evaluate_condition(condition, session_state):
  value = session_state.get(condition.field)
  match condition.operator:
    "eq"     → return value == condition.value
    "not_eq" → return value != condition.value
    "in"     → return value in condition.value   # condition.value is a list
    "lt"     → return value < condition.value
    "gt"     → return value > condition.value
```

---

## Session State Changes

`current_subagent_id` is the single new field the Memory Layer must persist. Everything else (profile fields, confirmed entities, session history) remains as-is.

### Consent-Declined Profile Handling

If a user declines consent, the conversation still proceeds through profile-building subagents — those profile fields are needed to serve the user during the session. However, when the session ends (terminal subagent reached or `flush_session` called), the orchestrator checks `consent_status`:

```
on session end:
  if session.consent_status == "declined":
    Memory Layer: delete all profile fields collected this session
    Memory Layer: do NOT write to User Profile Store
    Only session-scoped (in-flight) data is flushed — nothing persists
```

This is enforced by the orchestrator at `flush_session` time, not by individual subagents. SubAgents do not need to be aware of consent status during collection — they collect normally. The deletion happens once, at the end.

```
session state (key additions):
  current_subagent_id: <subagent_id>         # Updated at end of every turn
  subagent_entry_count: dict             # { subagent_id: int } — how many turns spent in each subagent
                                     # Used for HITL loop detection, drop-off triggers
```

`subagent_entry_count` is incremented by the orchestrator each time a subagent is entered. This replaces the current `loop_count` mechanism and makes it subagent-specific rather than session-global.

---

## NLU Scoping

The NLU processor receives the scoped intent set instead of the full global intent list:

```python
# Before (flat list)
nlu_intents = config.preprocessing.nlu.intents  # all intents, always

# After (scoped)
nlu_intents = current_subagent.valid_intents + workflow.global_intents
```

This improves classification accuracy: NLU is not asked to distinguish between intents that are not meaningful at the current stage.

---

## System Prompt Assembly

Prompt assembly is done by **ManagerAgent internally**.

The LLM receives a system prompt assembled as:

```
[agent_system_prompt]               ← from agent_workflow — use case purpose, hard rules

[channel/language context]          ← injected at runtime by Reach Layer / Language Normalisation
                                      (e.g. channel type, detected language, response format hints)

[current_subagent.system_prompt]        ← from the active subagent — what to do on this specific turn
```

`agent_system_prompt` defines the agent's mission and non-negotiable constraints. Channel and language context comes from Reach Layer and Language Normalisation — it is not hardcoded in the workflow config. The subagent system prompt governs behaviour for this specific conversation state. SubAgent instructions take precedence where they conflict with the use-case prompt.

RAG chunks from Knowledge Engine are returned as `tool_result` in the messages array — the LLM calls `knowledge_retrieval` as a tool when it needs them. KE is never called on a fixed schedule; the LLM decides when retrieval is needed based on the user's query.

---

## Tool Scoping

Only tools listed in `current_subagent.tools` are passed to the LLM. This prevents the LLM from calling actions not appropriate for the current stage (e.g. `onest_apply` should never be available during profile building).

`knowledge_retrieval` is one of the available tool names. SubAgents that may need RAG include it in their `tools` list; subagents that never need it (e.g. greeting, awaiting_consent) do not. The LLM calls it like any other tool — the orchestrator routes it to Knowledge Engine and returns the result as `tool_result`.

```python
# Orchestrator builds scoped tool set per turn
active_tools = [
    tool_registry.get_definition(name)
    for name in current_subagent.tools
]
# Passed to ManagerAgent — includes knowledge_retrieval if listed on this subagent
```

---

## Special Handlers

SubAgents with `special_handler` bypass LLM inference entirely.

| Handler | Behaviour |
|---|---|
| `hitl` | Orchestrator returns the fixed `hitl_response` message from config. No LLM call. Triggers counsellor scheduling via Action Gateway. |
| `whatsapp_handoff` | Orchestrator triggers WhatsApp message delivery with session summary. No LLM call. |

These handlers are checked by the orchestrator before Step 7 (prompt assembly). If `current_subagent.special_handler` is set, skip Steps 7–9 entirely.

---

## domain.yaml Refactor Impact

This design replaces the following existing sections in domain.yaml:

| Removed | Replaced by |
|---|---|
| `conversation.workflow.steps[]` | `agent_workflow.subagents[].id` |
| `conversation.workflow.transitions{}` | `agent_workflow.subagents[].routing[]` |
| `conversation.prompt_blocks.node_instructions{}` | `agent_workflow.subagents[].system_prompt` |
| `conversation.prompt_blocks.persona` | `agent_workflow.agent_system_prompt` |

Sections that remain unchanged: `agent`, `preprocessing`, `connectors`, `trust`, `knowledge`, `hitl`, `messages`.

---

## Validation Rules (enforced at startup)

1. Exactly one subagent has `is_start: true`.
2. Every `next_subagent_id` reference in routing rules resolves to a subagent id in the workflow.
3. Every tool name in `subagent.tools` must exist in the Action Gateway tool registry.
4. Every intent in `subagent.valid_intents` must exist in `preprocessing.nlu.intents`.
5. Global intents must NOT appear in any subagent's `valid_intents` (they're added automatically).
6. Terminal subagents have no routing rules.
7. At least one routing rule per non-terminal subagent (or a catch-all `"*"` rule).
