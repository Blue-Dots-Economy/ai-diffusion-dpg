"""Phase prompt builder: knowledge.

Configures the Knowledge Engine RAG knowledge base — collection name,
doc_types, intent_filters, and embedding provider. Part of the dev-kit
deterministic wizard's phase-prompt system.

See design §6 of
docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dev_kit.agent.phase_prompts._helpers import (
    _phase_focus_header,
    _closing_block,
    _common_rules,
    _path_of,
    _render_fields,
    _rule_of,
)

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
        "The user already confirmed they need a knowledge base — this "
        "configuration is required."
        if has_kb
        else "The user did not flag a knowledge base requirement during "
        "intake. Briefly confirm with the user before configuring anything; "
        "if no KB is needed, simply say so and stop — the system will move "
        "on."
    )

    return f"""{_phase_focus_header("knowledge", pending_fields)}# Phase: Knowledge base

You are configuring the agent's knowledge base. {kb_gate}

The KB collection name defaults to `{default_collection}`; the user can
override it. `doc_types` are domain-specific labels used to filter retrieval.
`intent_filters` map NLU intents to `doc_types` — keys MUST match the intents
declared in the previous step (visible in the references section below).

{_common_rules()}

**Do NOT write `knowledge_engine.observability.domain`** — it is a
derived field that the wizard computes automatically from the project
slug. Any `update_config` call to that path is rejected as a non-chat
field.

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

**CRITICAL — knowledge_retrieval connector:**

The `knowledge_retrieval` connector entry under
`agent_core.connectors.internal` is already created (predetermined,
populated by the router skeleton when the user wants a knowledge
base). You do NOT need to add or replace the connector entry itself. You DO need to fill in
its chat sub-fields, all of which are in FIELD_RULES individually —
use the path form:

```
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].description",
              value="<one-line description of what the KB contains>")
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.call_when",
              value="<plain-language trigger condition>")
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.required_before_calling",
              value=["query"])
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.must_not_substitute",
              value="<what the LLM must NOT substitute>")
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_empty",
              value="<exact line on empty results>")
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.on_failure",
              value="<exact line on failure>")
update_config(path="agent_core.connectors.internal[name=knowledge_retrieval].invocation_rules.bridge_line",
              value="<short line said right before the call>")
```

Do NOT use the block/section/values form here — `connectors.internal` is
a list, and FIELD_RULES has no entries for `agent_core.connectors.internal.<key>`;
the path-with-filter syntax above is the only valid shape.

**CRITICAL — NLU intents and intent_filters must stay in sync:**
Every key in `intent_filters` MUST appear in
`agent_core.preprocessing.nlu_processor.intents`. When you add
`intent_filters`, pair the write with a matching NLU intents update in the
SAME message. Read the current NLU intents from the references section
below before constructing the update.

**Conversation style:**
1. Ask: "What topics or information do your documents cover?" (content,
   not quantity, size, or format).
2. From the answer, create `doc_type` labels (short snake_case) and
   `intent_filters`. Present together with the full KB config for
   confirmation.
3. **Azure Blob Storage question.** After the KB config is confirmed,
   ask exactly ONE follow-up question on its own turn — plain phrasing,
   no preambles like "let me ask the mandatory follow-up":

   > "Will your KB documents be stored in Azure Blob Storage, or only
   > uploaded locally?"

   The phase will NOT advance until you record the user's answer via
   `update_intake(field="uses_azure_blob", ...)`. Skipping this
   question or assuming the default will leave the wizard stuck on
   the knowledge phase indefinitely — the router explicitly gates
   knowledge-phase completion on this decision because the deploy
   form needs to know whether to surface Azure credential inputs.

   When the user answers:
   - If **yes (Azure)**: call
     `update_intake(field="uses_azure_blob", value=true)` and tell
     the user "Noted. In the Deploy step you'll be asked for the
     three Azure values — account name, account key, and container
     name. Keep them ready." NEVER ask for the credentials in chat;
     they're collected securely on the deploy form.
   - If **no (local only)**: call
     `update_intake(field="uses_azure_blob", value=false)` and tell
     the user "Fine — at the Ingest Documents step (post-deploy)
     you'll upload files directly. No cloud credentials needed."

   In BOTH cases the `update_intake` call is required — the value
   alone (True or False) does not signal "user has answered"; the call
   itself is what releases the phase.

Document ingestion itself happens AFTER deployment via the Ingest
Documents step — do NOT ask the user to upload documents in this chat
and do NOT ask for any Azure credential value at chat time.

## Fields to capture this phase

{fields_section}

## Pydantic schemas (use ONLY these field names)

```python
{schemas_section}
```

## Already-set values you can reference

{refs_section}

{_closing_block()}
"""
