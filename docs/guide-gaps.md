# Agent Configuration Guide — Gaps & DPG-Specific Addenda

This document records fields, behaviours, and mechanisms that the dev-kit
configures but that the Agent Configuration Guide (v3.0, April 2026) does
not cover. Share with the guide authors for potential inclusion in a future
revision.

## Fields not in the guide

### agent_core block

- `preprocessing.nlu_processor.signal_intents` — map of intent → signal type
  for longitudinal writes to the Memory Layer context graph. DPG-specific
  observability feature.
- `preprocessing.nlu_processor.user_state_confidence_threshold` — sticky
  fallback threshold for the user_state classifier (GH-139). Guide describes
  the state model but not how confidence-thresholded classification handles
  ambiguous turns.
- `entity_to_profile_field` — maps extracted NLU entities to persistent
  graph profile fields. Required for profile building.
- `channels.<name>.turn_assembler` — TurnAssembler runs inside Agent Core
  but is per-channel. Guide treats assembly as a channel/adapter concern.
- `channels.<name>.system_prompt_suffix` — per-channel tuning (GH-97). Guide
  discusses voice vs chat differences as prompt-authoring guidance, not as
  a runtime channel-aware suffix mechanism.

### memory_layer block

- `state.context_graph` node types and edge types — Memgraph-backed typed
  attribute graph per session. Guide describes contact memory as a flat
  record; DPG adds a typed graph dimension.
- `state.persistent.merge_on_session_end` — explicit declaration of which
  session fields promote to persistent scope at session end. Guide assumes
  memory is written as the session progresses.
- `reengagement.triggers` — outbound re-engagement via WhatsApp / SMS /
  callback. Guide does not cover outbound behaviour.

### trust_layer block

- `/assemble_constraints` async endpoint contract — the guide describes the
  dignity check as a pre-response self-check but does not specify how it
  plumbs through a distinct Trust Layer service.
- DPDP consent rules — Indian data-protection specifics not in the guide.

### observability_layer block

- OTel span attribute conventions (e.g. `user_state.current`,
  `session.turn_count`, `session_id`) — DPG-specific instrumentation.
- `turn_event` schema — async emit contract, per-turn event shape.

### reach_layer block

- Outbound campaigns and scheduled triggers.
- TurnAssembler adapter policy (semantic_gate, silence_trigger,
  max_wait_ceiling) for turn completion detection.

## Mechanisms not in the guide

- **Subagent state machine.** The guide treats the full agent prompt as a
  single document; DPG decomposes into subagents with typed routing. This
  affects how opening logic, tool scoping, and per-subagent prompts are
  authored.
- **Opening logic via `subagents[].opening_phrase`.** The guide's 5
  contact-memory branches map to our subagent graph rather than a single
  prompt's conditional; we enforce per-subagent opening phrases emitted on
  turn 1 only.
- **User-state model with NLU-based classification (GH-139).** Guide
  describes state concept; DPG adds runtime classification via the NLU
  call, sticky fallback, Memory-Layer persistence, and observability
  transition events.
- **Session-end signalling via `end_session` internal tool (GH-137).** The
  guide mentions a fixed terminal word for voice but does not prescribe how
  the LLM signals session end. DPG uses an orchestrator-routed internal tool
  to separate "when to end" (LLM decides) from "how to end" (voice adapter
  appends terminal word + closes websocket; chat closes naturally).
- **Channel-aware prompting with consolidated `channels:` top-level
  block.** GH-97 + GH-137.

## Fields used in the guide but not in DPG

(To be filled as we find them during implementation.)

---

**Maintainer:** keep this file updated as new gaps are discovered. Filed
items here are candidates for either (a) guide v4 inclusion, (b) dev-kit
documentation to compensate, or (c) alignment work to make DPG match the
guide where feasible.
