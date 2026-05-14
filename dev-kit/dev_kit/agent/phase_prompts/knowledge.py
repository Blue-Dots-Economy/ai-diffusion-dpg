"""Phase prompt builder: knowledge.

Configures the Knowledge Engine RAG knowledge base — collection name,
doc_types, intent_filters, and embedding provider.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import _path_of, _rule_of, _render_fields

if TYPE_CHECKING:
    from dev_kit.agent.field_rules import FieldRule
    from dev_kit.agent.intake_state import IntakeState


def build(
    pending_fields: list["FieldRule"],
    pydantic_schemas: str,
    cross_phase_refs: str,
    intake_state: "IntakeState",
) -> str:
    """Build the knowledge phase system prompt addition.

    Args:
        pending_fields: List of FieldRule objects (or ``(path, rule)`` tuples)
            still pending in the knowledge phase after applies_if filtering.
        pydantic_schemas: Pre-rendered Pydantic class source code for schemas
            backing the pending fields. Injected verbatim.
        cross_phase_refs: Pre-rendered string of already-set values from prior
            phases for the LLM to reference. Crucially includes the NLU
            intents declared in the language phase (for intent_filters sync).
        intake_state: Current IntakeState. Used to gate this phase and to
            suggest the default collection_name.

    Returns:
        A non-empty string to append to the base system prompt for the
        knowledge phase.
    """
    fields_section = _render_fields(pending_fields)
    schemas_section = pydantic_schemas if pydantic_schemas.strip() else "_N/A_"
    refs_section = cross_phase_refs if cross_phase_refs.strip() else "_No prior-phase refs to display._"

    project_name = getattr(intake_state, "project_name", "")
    has_kb = getattr(intake_state, "has_kb", False)
    default_collection = f"{project_name}_kb" if project_name else "_kb"

    kb_gate = (
        "The user confirmed `has_kb=true` during the tier intake chat — this phase is REQUIRED."
        if has_kb
        else "The user did NOT confirm a knowledge base during tier intake (`has_kb=false`). "
        "Confirm whether this phase is needed before proceeding. If no KB is required, "
        "note it and advance."
    )

    return f"""# Phase: Knowledge base

You are now configuring the agent's knowledge base. {kb_gate}

The KB collection name defaults to `{default_collection}`; the user can
override it. `doc_types` are domain-specific labels used to filter retrieval.
`intent_filters` map NLU intents to `doc_types` — keys MUST match the intents
declared in the language phase (visible in cross-phase refs below).

**STEP 0 — Before asking the user anything:**
Auto-call:
`update_config(block=knowledge_engine, section=observability,
values={{domain: '{project_name}'}})`
Use `section=observability` NOT `section=observability.domain`.
Do NOT ask the user.

**Configuration path:**
`update_config(block=knowledge_engine,
section=knowledge.blocks.static_knowledge_base, values={{...}})`

Valid keys: `collection_name`, `top_k`, `similarity_threshold`,
`default_doc_type`, `embedding_provider`, `intent_filters` (dict).

NEVER write:
- `vector_store` — this key does not exist.
- `sources` — documents are uploaded post-deploy, not configured here.
- `conversation`, `persona`, `language_instruction` — these do not exist in
  knowledge_engine.
- Flat keys directly under `knowledge:` (e.g. `knowledge.collection_name`).

Valid `embedding_provider` values: `chroma_default` (works for most
deployments; only change if the user has a specific reason).

**CRITICAL — knowledge_retrieval connector placement:**
When the agent has a KB, create the `knowledge_retrieval` connector under
`connectors.internal` (NOT `connectors.read`):
`update_config(block=agent_core, section=connectors.internal,
values=[{{name: 'knowledge_retrieval', route: 'knowledge_engine',
description: '<describe what the KB contains>',
input_schema: {{type: 'object', properties: {{query: {{type: 'string',
description: 'Search query'}}}}, required: ['query']}},
invocation_rules: {{call_when: '...', required_before_calling: ['query'],
must_not_substitute: '...', on_empty: '...', on_failure: '...',
bridge_line: '...'}}}}]}}`

**CRITICAL — NLU intents and intent_filters must stay in sync:**
Every key in `intent_filters` MUST appear in
`agent_core.preprocessing.nlu_processor.intents`. When you add
`intent_filters`, pair the write with a matching NLU intents update in the
SAME message. Read current NLU intents from cross-phase refs before
constructing the update.

**Conversation style:**
1. Ask: "What topics or information do your documents cover?" (content, not
   quantity, size, or format).
2. From the answer, create `doc_type` labels (short snake_case) and
   `intent_filters`. Present together with the full KB config for
   confirmation.
3. Ask: "Do you have Azure Blob Storage for your KB documents?" If yes, note
   that Azure account details are collected securely at deploy time (NEVER
   ask for them in chat).

Document ingestion happens AFTER deployment via the Ingest Documents step —
do NOT ask the user to upload documents in this chat.

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

When `collection_name`, `intent_filters`, and `default_doc_type` are set,
the router advances to the memory phase automatically. Do NOT call set_phase.
"""
